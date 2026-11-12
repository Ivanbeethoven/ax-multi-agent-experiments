#!/usr/bin/env python3
"""Prepare and run exactly the next Pool A C4 flat-copy arm after its original C3 closure."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys


PREFIX = 'experiments/unified-suite/pilot-aliyun/scripts'
C3 = '16f4ac389d2a2da6-r1-native'
C4 = 'c5e97774bcffa69a-r1-native'
ORDER = ('off', 'on', 'on', 'off', 'off', 'on')
TOOLS = dict(baseline='run-c4-fork-baseline.py', capacity='capacity-check.py',
             prepare='prepare-c4-controller.py', calibrate='calibrate-c4.py',
             check='check-c4-calibration.py', admit='admit-c4.py', run='run-c4.py')
RUNTIME_INPUTS = ('bench/fs_step.py', PREFIX+'/fork-oracle.py', PREFIX+'/pilot_io.py')


def module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def command(out, name, argv):
    directory = out/name
    directory.mkdir()
    argv = list(map(str, argv))
    write_new(directory/'intent.json', dict(argv=argv, automatic_retry=False))
    with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
        result = subprocess.run(argv, stdout=stdout, stderr=stderr)
    write_new(directory/'exit.json', dict(exit_code=result.returncode))
    if result.returncode:
        raise RuntimeError('Preserve original failure: '+str(directory))


def ref(path):
    return dict(path=str(path), sha256=digest(path))


def shape(s, repo):
    draft = load(pinned(s['draft']))
    canonical, control = Path(draft['canonical_control']), Path(draft['control'])
    if (draft['schema'] != 'c4-native-flat-plan-draft-v1' or draft['reviewed'] or draft['executable'] or
            draft['cell_id'] != C4 or control.parent != canonical.parent or
            not re.fullmatch(r'code-[0-9]{2}', repo.name) or repo.parent != canonical.parent or
            not re.fullmatch(r'[0-9]{2}', s['attempt_suffix'])):
        raise ValueError('Exact native C4 draft and unused versioned code path required')
    pinned(s['archive'])
    manifest = load(pinned(s['manifest']))
    for key, sha in manifest.items():
        path = (repo/key).resolve(strict=True)
        if not path.is_relative_to(repo):
            raise ValueError('Code member outside frozen runtime')
        pinned(dict(path=str(path), sha256=sha))
    for name in ('measured_leaf_reference', 'source_c2', 'hardware', 'cross_pool', 'rustfs'):
        pinned(s[name])
    if s['source_c2'] != draft['required_handoff']['c2_staging']:
        raise ValueError('Different A C2 staging proof')
    def measured(path):
        tree = ast.parse(path.read_text())
        return ast.dump(next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'measure'))
    if measured(pinned(s['measured_leaf_reference'])) != measured(repo/PREFIX/'c4-fork-node.py'):
        raise ValueError('Timed leaf changed relative to the original C4 experiment')
    tools = set(TOOLS.values()) | {'c4-fork-node.py', 'pilot-node.py', 'make-observer.py', 'deploy-c4-code.py',
                                 'setup-c4-control.py', 'install-code.py', 'run-c4-batch.py',
                                 'continue-flat-c3-after-base.py'}
    if any(PREFIX+'/'+n not in manifest for n in tools) or set(RUNTIME_INPUTS)-set(manifest):
        raise ValueError('C4 runtime files missing from frozen manifest')
    plan = check_plan(load(pinned(draft['plan'])))
    cell = next(c for c in plan['cells'] if c['id'] == C4)
    if cell['pool'] != 'pool-a' or cell['resource_ids'] != draft['physical_resource_ids']:
        raise ValueError('Original A hardware changed')
    controls = [canonical, control, *map(Path, s['other_lease_controls'])]
    if set(controls) != {p.parent for p in canonical.parent.glob('*/state.sqlite')}:
        raise ValueError('Recheck the explicit current phase state inventory')
    return draft, canonical, control, manifest, plan, cell, controls


def handoff(s, draft, canonical, plan, cell, controls):
    release = s['c3']
    if release['retention_decision'] not in ('verified-before-c4', 'preserve-local-through-c4'):
        raise ValueError('Record the C3 retention decision before launching C4')
    if release['retention_decision'] == 'verified-before-c4' and load(pinned(release['retention_verification'])).get('passed') is not True:
        raise ValueError('Original C3 retention verification did not pass')
    manager = subprocess.check_output(['systemctl', 'show', release['unit'], '-p',
        'InvocationID,ActiveState,SubState,ExecMainStatus,MainPID,Result'], text=True)
    state = dict(line.split('=', 1) for line in manager.splitlines())
    if (state['InvocationID'] != release['invocation'] or state['SubState'] != 'exited' or
            state['MainPID'] != '0' or state['Result'] != 'success' or state['ExecMainStatus'] != '0'):
        raise ValueError('Original A C3 service invocation has not closed successfully')
    result = load(pinned(release['result']))
    run = load(pinned(release['run_request']))
    close_ref = release['closing']
    close = load(pinned(close_ref))
    original = canonical/'one-cell-runs'/C3
    if (Path(release['result']['path']) != original/'result.json' or
            load(original/'started.json')['request'] != release['run_request'] or
            result['passed'] is not True or result['cell_id'] != C3 or result['closing'] != close_ref or
            close_ref['path'] != draft['required_handoff']['closure_path'] or
            close['cell_id'] != C3 or close['schema'] != 'suite-cell-close-v1' or close['outcome'] != 'complete' or
            close['plan_sha256'] != load(canonical/'suite/plan.json')['plan_sha256'] or
            run['cell_id'] != C3 or run['final_collector_name'] != result['final_collector_name']):
        raise ValueError('C3 handoff does not match the original completed run')
    for field, expected in [('processes', {'all_exited': True}), ('mounts', {'all_unmounted': True}),
                            ('debt', {'pending_upload_bytes': 0, 'pending_upload_operations': 0})]:
        proof = load(pinned(close['closure'][field]['evidence']))
        if close['closure'][field]['equals'] != expected or proof['cell_id'] != C3 or proof['passed'] is not True or any(
                proof[k] != v for k, v in expected.items()):
            raise ValueError('Incomplete original C3 process/mount/debt closure')
    pinned(close['sample']); pinned(close['retention']['manifest'])
    with sqlite3.connect((canonical/'state.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        if db.execute('SELECT status FROM cells WHERE id=?', (C3,)).fetchone() != ('complete',):
            raise ValueError('Original C3 state did not finish')
    if any(active_conflicts(p, cell, plan) for p in controls):
        raise ValueError('A or an overlapping physical resource is leased')
    collector = dict(collector_name=run['final_collector_name'], collector_out=run['final_collector_out'])
    if not re.fullmatch('ax-pilot-[a-z0-9-]+', collector['collector_name']) or not re.fullmatch(
            '/srv/ax-objects-0/q/[a-z0-9-]+', collector['collector_out']):
        raise ValueError('Expected the original owned A collector')
    return collector, state


def preflight(ssh, database, collector, rustfs):
    def remote(role, argv):
        return subprocess.check_output(['ssh', '-F', str(ssh), alias('pool-a', role), shlex.join(argv)], text=True).strip()
    dbsize = remote('metadata', ['redis-cli', '-h', '127.0.0.1', '-n', str(database), 'DBSIZE'])
    if dbsize != '0':
        raise ValueError('Declared isolated A Redis DB is not empty')
    def container(target):
        return json.loads(remote('objects', ['docker', 'inspect', '--format',
            '{"id":{{json .Id}},"running":{{json .State.Running}},"pid":{{json .State.Pid}},'
            '"image":{{json .Image}},"owner":{{json (index .Config.Labels "ax.pilot.owner")}}}', target]))
    c = container(collector['collector_name'])
    receipt = json.loads(remote('objects', ['cat', collector['collector_out']+'/started.json']))
    if (c['running'] is not True or c['id'] != receipt['container_id'] or c['owner'] != collector['collector_name'] or
            receipt['source_timestamps'] is not True or receipt['network'] != 'host'):
        raise ValueError('Current collector differs from original on3 receipt')
    service = container(rustfs['container_id'])
    if (service['running'] is not True or service['id'] != rustfs['container_id'] or
            service['pid'] != rustfs['pid'] or service['image'] != rustfs['image_id']):
        raise ValueError('Original A RustFS identity changed')
    remote('client', ['sh', '-c', 'test -z "$(cat /sys/fs/cgroup/ax-slot-1/cgroup.procs)"'])
    return dict(passed=True, database=database, dbsize=0, collector=c, rustfs=service, slot_empty=True,
                workers_started=False)


def execute(s, req, repo):
    draft, canonical, control, manifest, plan, cell, controls = shape(s, repo)
    if cell['id'] == C4:
        raise ValueError('Historical C4 BrewFS Flat full-copy dispatch is retired; existing evidence remains readable')
    suffix = s['attempt_suffix']
    out = Path(s['out'])
    if not out.is_absolute() or not out.is_relative_to(canonical.parent/'preparation') or out.exists():
        raise ValueError('New coordinator continuation output required')
    if type(s['database']) is not int or not 20 <= s['database'] < 256:
        raise ValueError('Explicit candidate isolated database required')
    with resource_guard(canonical, cell, plan):
        collector, manager = handoff(s, draft, canonical, plan, cell, controls)
    build = pinned(draft['provenance']['build-manifest.json'])
    latest = module(repo/PREFIX/'continue-flat-c3-after-base.py')
    latest.latest_receipt(s['latest'], build)
    rustfs = load(pinned(s['rustfs']))
    if rustfs['passed'] is not True or rustfs['source_commit'] != next(v['source_commit'] for v in load(build)['sources']
                                                                     if v['component'] == 'rustfs'):
        raise ValueError('Original A RustFS is not the frozen build')
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'started.json', dict(request=req, manager=manager, collector=collector, automatic_retry=False))
    def tool(name, client=False):
        name = PREFIX+'/'+name
        return dict(path=str((Path('/srv/ax-client')/repo.name if client else repo)/name), sha256=manifest[name])
    def invoke(name, script, body, extra=()):
        request = out/(name+'.request.json')
        write_new(request, dict(body, reviewed=True))
        command(out, name, [sys.executable, pinned(tool(script)), *extra, '--request', request, '--sha256', digest(request)])
        return ref(request)
    other = [str(p) for p in controls if p != canonical]
    base = dict(control=str(control), canonical_control=str(canonical), cell_id=C4)
    barriers = [s['c3']['closing'], s['source_c2']]
    ssh = pinned(ref(control/'ssh.conf'))
    runtime_dir = control/('runtime-a-'+suffix)
    hardware_dir = control/('hardware-a-'+suffix)
    deploy_out = out/'deploy-proof'
    client_input_dir = 'c4-native-flat-'+suffix
    try:
        with resource_guard(canonical, cell, plan):
            handoff(s, draft, canonical, plan, cell, controls)
            write_new(out/'preflight.json', preflight(ssh, s['database'], collector, rustfs))
        invoke('deploy', 'deploy-c4-code.py', dict(base, schema='c4-code-deployment-v1',
            other_lease_controls=other, code_directory=repo.name, archive=s['archive'], manifest=s['manifest'],
            installer=tool('install-code.py'), ssh_config=ref(ssh), barriers=barriers,
            attempt=client_input_dir, out=str(deploy_out), client_input_directory=client_input_dir+'-inputs',
            client_inputs={'plan.json': draft['plan'], 'inventory.json': draft['provenance']['inventory.json']}))
        invoke('runtime', 'setup-c4-control.py', dict(base, schema='c4-setup-request-v1',
            other_lease_controls=[p for p in other if p != str(control)], code_directory=repo.name,
            out=str(runtime_dir), phase_barriers=barriers, deployment=ref(deploy_out/'result.json')), ('runtime',))
        runtime = ref(runtime_dir/'binding.json')
        invoke('hardware', 'setup-c4-control.py', dict(base, schema='c4-setup-request-v1', runtime_control=runtime,
            original_cross_pool=s['cross_pool'], original_hardware=s['hardware'], out=str(hardware_dir)), ('hardware',))
        latest.latest_receipt(s['latest'], build)
        prepared = draft['preparation']
        source = draft['source']
        remote_inputs = Path('/srv/ax-client/preparation')/(client_input_dir+'-inputs')
        prep_request = out/'base.request.json'
        node = tool('pilot-node.py', True)
        write_new(prep_request, dict(schema='c4-fork-baseline-request-v1', reviewed=True,
            source_tool_sha256=tool(TOOLS['baseline'])['sha256'], control=str(canonical),
            canonical_plan=ref(canonical/'suite/plan.json'), fork_plan=draft['plan'],
            inventory=draft['provenance']['inventory.json'], build_manifest=draft['provenance']['build-manifest.json'],
            ssh_config=ref(ssh), latest_check=s['latest'], pool='pool-a', product='brewfs', cell_id=C4,
            other_lease_controls=other, out=prepared['controller_out'], worker=dict(mode='prepare',
                tool=tool(TOOLS['baseline'], True), node_tool=node,
                plan=dict(path=str(remote_inputs/'plan.json'), sha256=draft['plan']['sha256']),
                inventory=dict(path=str(remote_inputs/'inventory.json'), sha256=draft['provenance']['inventory.json']['sha256']),
                runtime_pins={name:manifest[name] for name in RUNTIME_INPUTS}, filesystem_binary=draft['binary'],
                source_tree=source['tree'], fixture=source['fixture'], input_closure=source['input_closure'],
                source=source['path'], asset=prepared['asset'], out=prepared['client_out'],
                database=s['database'], attempt=prepared['attempt'])))
        trials = []
        current = collector
        if tuple(t['mode'] for t in draft['trials']) != ORDER:
            raise ValueError('Original six counterbalanced calibration trials required')
        for i, trial in enumerate(draft['trials']):
            args = {k:trial[k] for k in ('mode', 'attempt', 'out')}
            args.update(current)
            if i in (1, 5):
                name = 'ax-pilot-'+trial['attempt']+'-collector'
                current = dict(collector_name=name, collector_out='/srv/ax-objects-0/q/'+name)
                args.update(new_collector_name=name, new_collector_out=current['collector_out'])
            trials.append(args)
        batch_out = control/('batch-a-flat-'+suffix)
        batch = invoke('batch', 'run-c4-batch.py', dict(base, schema='c4-fork-batch-v1', initialize_state=False,
            preparation_request=ref(prep_request), asset=prepared['asset'], qualification=str(control/('qualification/pool-a-fork-'+suffix)),
            runtime_control=runtime, hardware=ref(hardware_dir/'hardware.json'),
            cross_pool_qualification=ref(hardware_dir/'cross-pool.json'),
            shared_qualification=str(Path(draft['metrics_contract']['path']).parents[1]),
            tools={k:tool(v) for k,v in TOOLS.items()}, node_tool=node, fork_tool=tool('c4-fork-node.py', True),
            observer_tool_name='make-observer.py', observer_tool_sha256=tool('make-observer.py')['sha256'],
            objects_container=rustfs['container_id'], out=str(batch_out), formal_attempt=draft['formal']['attempt'],
            calibration_verification='touched-cold-v1', trials=trials, **collector))
        write_new(out/'result.json', dict(passed=True, batch=batch, result=ref(batch_out/'result.json'), automatic_retry=False))
    except Exception as error:
        write_new(out/'failed.json', dict(passed=False, error=str(error), automatic_retry=False))
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('stage-check', 'run'))
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--runtime-repo', type=Path, required=True)
    a = p.parse_args()
    repo = a.runtime_repo.resolve(strict=True)
    sys.path.insert(0, str(repo))
    global digest, load, pinned, write_new, active_conflicts, alias, resource_guard, check_plan
    from bench.runtime.common import digest, load, pinned, write_new
    from bench.runtime.pilot_pools import active_conflicts, alias, resource_guard
    from bench.suite_plan import check_plan
    req = dict(path=str(a.request.resolve(strict=True)), sha256=a.sha256)
    s = load(pinned(req))
    if s['schema'] != 'c4-native-flat-continuation-v1':
        raise ValueError('Expected explicit A C4 continuation')
    if a.action == 'stage-check':
        if s['reviewed'] is not False or 'c3' in s or 'latest' in s:
            raise ValueError('Stage-check accepts only the unsealed coordinator request')
        shape(s, repo)
        print(json.dumps(dict(passed=True, request=req, runtime=str(repo), source_tool=ref(Path(__file__)),
                              measured_leaf_unchanged=True, workers_contacted=False, executable=False)))
        return
    if s['reviewed'] is not True:
        raise ValueError('Sealed explicit original C3 handoff and branch receipt required')
    execute(s, req, repo)


if __name__ == '__main__':
    main()
