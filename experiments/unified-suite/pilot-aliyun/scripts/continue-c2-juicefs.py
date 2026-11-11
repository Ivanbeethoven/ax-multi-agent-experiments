#!/usr/bin/env python3
"""Run the copied C2 trace after an explicit C4 closure; retain one physical lock."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import shlex
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


def require_clean_predecessor(handoff, recipe, closure, expected_collector):
    if handoff.get('pool') != recipe['pool'] or handoff.get('safe_for_handoff') is not True:
        raise ValueError('Explicit clean C4 handoff required')
    if tuple(handoff.get(k) for k in ('collector_name', 'collector_out')) != expected_collector:
        raise ValueError('Actual C4 collector differs from this immutable C2 recipe')
    if not closure or closure.get('passed') is not True or closure.get('pool') != recipe['pool']:
        raise ValueError('Actual pool-specific C4 closure receipt required')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('recipe', 'handoff', 'runtime-repo', 'out'):
        p.add_argument('--' + name, required=True, type=Path)
    for name in ('recipe-sha256', 'handoff-sha256'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    sys.path.insert(0, str(a.runtime_repo.resolve(strict=True)))
    from bench.runtime.common import digest, load, pinned, write_new
    from bench.runtime import pilot_context
    from bench.runtime.pilot_pools import active_conflicts, alias, resource_guard

    def ref(path):
        return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))

    recipe_pin = dict(path=str(a.recipe), sha256=a.recipe_sha256)
    handoff_pin = dict(path=str(a.handoff), sha256=a.handoff_sha256)
    s = load(pinned(recipe_pin))
    handoff = load(pinned(handoff_pin))
    closure = load(pinned(handoff['closing']))
    if s['schema'] != 'c2-juicefs-coordinator-draft-v1' or s['execution_enabled'] or s['reviewed']:
        raise ValueError('Pinned coordinator recipe required')
    control = Path(s['plan']['path']).parents[1]
    plan = load(pinned(s['plan']))
    cell = next(c for c in plan['cells'] if c['id'] == s['cell_id'])
    pool = cell['pool']
    if (cell['workload'], cell['arm_id'], pool) != ('trace', 'juicefs', s['pool']):
        raise ValueError('One explicit flat JuiceFS C2 cell required')
    for pin in [*s['tools'].values(), *s['source_pins']]:
        pinned(pin)
    rt = pilot_context.select(control, s['runtime_control'])
    commands = s['commands']
    draft = commands['draft_six_and_formal']
    mount = commands['client_qualification']
    arg = lambda argv, flag: argv[argv.index(flag) + 1]
    collector = (arg(draft, '--collector-name'), arg(draft, '--collector-out'))
    require_clean_predecessor(handoff, s, closure, collector)
    latest_tool = a.runtime_repo/'experiments/unified-suite/pilot-aliyun/scripts/verify-latest.py'
    latest_output = Path(arg(draft, '--latest-check'))
    build = control/'provenance/build-manifest.json'
    if digest(build) != plan['config']['build_manifest_sha256']:
        raise ValueError('Actual build manifest differs from original plan')
    with resource_guard(control, cell, plan):
        for phase in control.parent.glob('*/state.sqlite'):
            if active_conflicts(phase.parent, cell, plan):
                raise ValueError('Existing physical pool lease in ' + str(phase))
        with sqlite3.connect('file:' + str(control/'state.sqlite') + '?mode=ro', uri=True) as db:
            wanted = db.execute('SELECT status FROM cells WHERE id=?', (cell['id'],)).fetchone()
            earlier = [x['id'] for x in plan['cells'][:plan['cells'].index(cell)] if x['pool'] == pool and
                db.execute('''SELECT 1 FROM cells c JOIN qualifications q ON q.cell_id=c.id
                              JOIN assets a ON a.id=? WHERE c.id=? AND c.status='pending' ''',
                           (x['asset_id'], x['id'])).fetchone()]
            if wanted != ('pending',) or earlier:
                raise ValueError('Cell is no longer unstarted/first eligible: ' + str(earlier))
        a.out.mkdir(parents=True, exist_ok=False)
        write_new(a.out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(),
            recipe=recipe_pin, handoff=handoff_pin, control=str(control),
            cell_id=cell['id'], pool=pool, automatic_retry=False, next_cell=False))

        def ssh(role, argv):
            return ['ssh', '-F', rt/'ssh.conf', alias(pool, role), shlex.join(list(map(str, argv)))]

        def run(name, argv, stdin=None):
            folder = a.out/name
            folder.mkdir()
            argv = list(map(str, argv))
            write_new(folder/'intent.json', dict(argv=argv, automatic_retry=False))
            with (folder/'stdout').open('xb') as stdout, (folder/'stderr').open('xb') as stderr:
                result = subprocess.run(argv, input=stdin, stdout=stdout, stderr=stderr)
            write_new(folder/'exit.json', dict(exit_code=result.returncode))
            if result.returncode:
                raise RuntimeError('Inspect original failed C2 step ' + str(folder))
            return (folder/'stdout').read_bytes()

        try:
            install = s['client_helper_install']
            helper_path = install['destination']
            # All are small files except the exact replay binary. The complete
            # input inventory was independently read on B after cloud-only copy.
            client_pins = [s['remote_plan'], s['remote_inventory'], s['filesystem_binary'],
                *s['remote_tools'].values(),
                dict(path=s['source']['manifest'], sha256=s['source']['manifest_sha256']),
                dict(path=s['source']['binary'], sha256=s['source']['binary_sha256'])]
            missing = [arg(mount, '--asset'), arg(mount, '--out'),
                str(Path(arg(mount, '--out')).with_name(Path(arg(mount, '--out')).name+'-replay-preflight')),
                str(Path(helper_path).parent)]
            c = ('import hashlib,json,pathlib\n'
                 'P=pathlib.Path\n'
                 'assert not P("/sys/fs/cgroup/ax-slot-1/cgroup.procs").read_text().strip()\n'
                 'assert all(not P(x).exists() for x in ' + repr(missing) + ')\n'
                 'for pin in ' + repr(client_pins) + ':\n'
                 ' with P(pin["path"]).open("rb") as f:\n'
                 '  assert hashlib.file_digest(f,"sha256").hexdigest() == pin["sha256"], pin["path"]\n'
                 'print(json.dumps(dict(passed=True, paths_absent=' + repr(missing) +
                 ', worker_pins=' + repr(client_pins) + ')))\n')
            run('client-pins-and-empty-slot', ssh('client', ['python3', '-']), c.encode())
            out = run('redis-db-empty', ssh('metadata', ['redis-cli', '--raw', '-n',
                      str(s['candidate_database']), 'DBSIZE']))
            if out.strip() != b'0':
                raise ValueError('Candidate database is not empty; no reset')
            out = run('collector-running', ssh('objects', ['docker', 'inspect', '--format',
                '{{.State.Running}}', collector[0]]))
            if out.strip() != b'true':
                raise ValueError('Inherited collector is not running')
            latest = ['python3', latest_tool, '--manifest', build]
            run('latest-before-qualification', latest + ['--out', a.out/'latest-before-qualification.json'])
            before_path = Path(arg(commands['capacity_before'], '--out'))
            before_path.parent.mkdir(parents=True, exist_ok=False)
            run('capacity-before', commands['capacity_before'])
            before = load(before_path)
            for role in ('client', 'metadata', 'objects'):
                original = load(rt/'probes'/alias(pool, role)/'probe.json')
                if before['nodes'][role]['boot_id'] != original['boot_id']:
                    raise ValueError('Actual host boot identity changed after the runtime freeze: ' + role)
            install_code = (
                'from pathlib import Path;import sys,hashlib;'
                'data=sys.stdin.buffer.read();'
                'assert hashlib.sha256(data).hexdigest()=='+repr(install['source']['sha256'])+';'
                'p=Path('+repr(helper_path)+');p.parent.mkdir(parents=True,exist_ok=False);'
                'p.open("xb").write(data)')
            run('install-client-helper', ssh('client', ['python3', '-c', install_code]),
                pinned(install['source']).read_bytes())
            run('strict-and-cold-qualification', ssh('client', mount))
            run('collect-qualification', commands['collect_qualification'])
            run('attest-actual-juicefs', commands['attest_client'])
            run('capacity-after', commands['capacity_after'])
            run('capacity-budget', commands['capacity_budget'])
            run('latest-before-calibrations-and-formal', latest + ['--out', latest_output])
            run('write-reviewed-request', draft)
            request = Path(arg(draft, '--out'))
            req = load(request)
            if (req.get('reviewed') is not True or req['cell_id'] != cell['id'] or req['pool'] != pool or
                    req['runtime_control'] != s['runtime_control']):
                raise ValueError('Drafted request differs from the reviewed route')
            execute = runpy.run_path(str(pinned(s['tools']['run-one-c1.py'])))['execute']
            # The caller holds the same canonical guard, so do not re-enter main().
            execute(SimpleNamespace(request=request, sha256=digest(request)), req, control, plan, rt, cell)
            final = load(Path(req['out'])/'result.json')
            write_new(a.out/'result.json', dict(passed=True, cell_id=cell['id'], pool=pool,
                closing=final['closing'], request=ref(request), run=ref(Path(req['out'])/'result.json'),
                collector_name=req['final_collector_name'], collector_out=req['final_collector_out'],
                automatic_retry=False, next_cell=False))
        except Exception as error:
            write_new(a.out/'failed.json', dict(passed=False, cell_id=cell['id'], pool=pool,
                error=str(error), automatic_retry=False, handoff_permitted=False,
                note='Inspect original slot, FUSE and observer processes before any new pool use.'))
            raise


if __name__ == '__main__':
    main()
