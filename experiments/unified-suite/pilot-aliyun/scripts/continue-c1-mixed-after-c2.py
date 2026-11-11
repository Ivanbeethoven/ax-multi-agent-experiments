#!/usr/bin/env python3
"""Prepare or run the remaining JuiceFS mixed fio cell after a closed C2."""
import argparse
import json
from pathlib import Path
import re
import runpy
import sqlite3
import subprocess
import sys
from types import SimpleNamespace


def ref(path):
    path = Path(path)
    return dict(path=str(path.resolve(strict=True)), sha256=digest(path))


def read_only(control):
    return sqlite3.connect((control/'state.sqlite').as_uri()+'?mode=ro', uri=True)


def plan_inputs(watch_pin, code):
    watch = load(pinned(watch_pin))
    if (watch.get('schema') != 'c4-to-c2-coordinator-watch-v1' or
            watch.get('reviewed') is not True or watch['pool'] != 'pool-b' or
            Path(watch['runtime_repo']) != code):
        raise ValueError('Expected the reviewed pool B C2 request and its runtime')
    recipe = load(pinned(watch['recipe']))
    control = Path(recipe['plan']['path']).parents[1]
    plan = load(pinned(recipe['plan']))
    c2 = next(c for c in plan['cells'] if c['id'] == watch['c2_cell'])
    mixed, = [c for c in plan['cells'] if c['case'] == 'fio-randrw' and c['arm_id'] == 'juicefs']
    if (recipe['cell_id'] != c2['id'] or c2['workload'] != 'trace' or c2['arm_id'] != 'juicefs' or
            c2['pool'] != mixed['pool'] or mixed['pool'] != 'pool-b' or
            set(c2['resource_ids']) != set(mixed['resource_ids'])):
        raise ValueError('C2 and mixed fio must use the same frozen physical pool')
    runtime = pilot_context.select(control, recipe['runtime_control'])
    argv = recipe['commands']['draft_six_and_formal']
    arg = lambda key: argv[argv.index('--'+key)+1]
    cross_pool_proof(arg('cross-pool-qualification'), plan, runtime/'fleet.json')
    tag = arg('attempt-suffix')
    if not re.fullmatch('[a-z0-9]{1,6}', tag):
        raise ValueError('Expected the exact C2 calibration suffix')
    prefix = f'cal-pool-b-j-tr-{tag}-on3-collector'
    collector = dict(collector_name='ax-pilot-'+prefix, collector_out='/srv/ax-objects-0/q/'+prefix)
    return watch, recipe, control, plan, c2, mixed, runtime, arg, collector


def require_pending(control, cell):
    with read_only(control) as db:
        row = db.execute('SELECT status FROM cells WHERE id=?', (cell['id'],)).fetchone()
        started = db.execute("SELECT 1 FROM stages WHERE cell_id=? AND status!='pending'", (cell['id'],)).fetchone()
    if row != ('pending',) or started or (control/'cells'/cell['id']).exists():
        raise ValueError('Mixed fio cell has already started; no new attempt')


def prepare(a, watch_pin):
    watch, recipe, control, plan, c2, cell, runtime, arg, collector = plan_inputs(watch_pin, a.runtime_repo)
    require_pending(control, cell)
    original = a.source
    stage = a.out
    if (not original.is_relative_to(control/'next') or not stage.is_relative_to(control/'drafts') or
            original.is_symlink() or stage.exists()):
        raise ValueError('Original coordinator qualification and a fresh draft directory required')
    hardware = Path(watch['recipe']['path']).parents[1]/'shared/hardware.json'
    old_hw = load(original/'hardware.json')
    new_hw = load(hardware)
    if (new_hw['original_observation']['sha256'] != digest(original/'hardware.json') or
            new_hw['inventory_sha256'] != digest(runtime/'fleet.json') or
            any(new_hw[k] != old_hw[k] for k in ('checks', 'slot_limits', 'pool'))):
        raise ValueError('Runtime binding must preserve the actual B hardware observations')
    q = original/'juicefs'
    if (load(q/'workload.json')['params'] != cell['params'] or
            any(load(q/f'{name}.json')['passed'] is not True for name in ('input', 'filesystem'))):
        raise ValueError('Expected the already verified, exact mixed fio base')
    asset = original/'assets/juicefs'
    paths = [*sorted(q.glob('*.json')), *sorted(asset.rglob('*.json'))]
    if any(not p.is_file() or p.is_symlink() for p in paths):
        raise ValueError('Original qualification must contain ordinary JSON evidence')
    sources = {p.relative_to(original).as_posix(): ref(p) for p in paths}
    sources['hardware.json'] = ref(hardware)
    stage.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        target = stage/'qualification'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(pinned(source).read_bytes())
    latest = stage/'latest.json'
    request = stage/'request.json'
    args = ['--control',str(control),'--cell',cell['id'],'--qualification',str(stage/'qualification'),
            '--collector-name',collector['collector_name'],'--collector-out',collector['collector_out'],
            '--latest-check',str(latest),'--attempt-suffix','mxb01','--out',str(request)]
    for key in ('node-tool','node-tool-sha256','observer-tool-name','observer-tool-sha256','calibrator',
                'checker','admitter','runner','cross-pool-qualification','runtime-control','runtime-control-sha256'):
        args += ['--'+key,arg(key)]
    scripts = a.runtime_repo/'experiments/unified-suite/pilot-aliyun/scripts'
    write_new(stage/'draft.json', dict(schema='c1-mixed-after-c2-v1', execution_enabled=False,
        workers_contacted=False, cell_id=cell['id'], predecessor=c2['id'], watch=watch_pin, collector=collector,
        qualification={name: ref(stage/'qualification'/name) for name in sources}, original_sources=sources,
        draft_tool=ref(scripts/'draft-one-c1.py'), runner=ref(scripts/'run-one-c1.py'),
        latest_tool=ref(scripts/'verify-latest.py'), args=args, request=str(request), latest=str(latest)))
    print(json.dumps(ref(stage/'draft.json')))


def execute(a, draft_pin, completed_pin):
    s = load(pinned(draft_pin))
    if s.get('schema') != 'c1-mixed-after-c2-v1' or s['execution_enabled'] is not False:
        raise ValueError('Expected the exact coordinator-only mixed fio draft')
    watch, recipe, control, plan, c2, cell, runtime, _, collector = plan_inputs(s['watch'], a.runtime_repo)
    if s['cell_id'] != cell['id'] or s['predecessor'] != c2['id'] or s['collector'] != collector:
        raise ValueError('Draft does not match the actual frozen plan')
    if Path(completed_pin['path']) != Path(watch['runner_output'])/'result.json':
        raise ValueError('Expected the original C2 continuation result')
    completed = load(pinned(completed_pin))
    started = load(Path(completed_pin['path']).with_name('started.json'))
    measured = load(pinned(completed['run']))
    pinned(completed['request'])
    closing = load(pinned(completed['closing']))
    if (completed.get('passed') is not True or started['recipe'] != watch['recipe'] or
            completed['cell_id'] != c2['id'] or completed['pool'] != 'pool-b' or
            any(completed[k] != v for k, v in collector.items()) or closing['outcome'] != 'complete' or
            closing['cell_id'] != c2['id'] or measured.get('passed') is not True or
            measured['closing'] != completed['closing'] or measured['cell_id'] != c2['id'] or
            measured['final_collector_name'] != collector['collector_name']):
        raise ValueError('C2 result, final collector and formal closing must all match')
    for group in ('qualification', 'original_sources'):
        for pin in s[group].values():
            pinned(pin)
    status = subprocess.check_output(['systemctl','show',watch['runner_unit'],'-p','MainPID','-p','ActiveState'], text=True)
    if dict(row.split('=', 1) for row in status.splitlines()) != dict(MainPID='0', ActiveState='inactive'):
        raise ValueError('C2 coordinator unit is still live or not normally retired')
    with resource_guard(control,cell,plan):
        require_pending(control, cell)
        for db in control.parent.glob('*/state.sqlite'):
            if active_conflicts(db.parent,cell,plan):
                raise ValueError('B still owns a physical lease in '+str(db))
        with read_only(control) as db:
            row = db.execute('SELECT status,result FROM cells WHERE id=?', (c2['id'],)).fetchone()
        if not row or row[0] != 'complete' or json.loads(row[1]) != completed['closing']:
            raise ValueError('C2 SQLite state does not bind to the formal close')
        manifest = control/'provenance/build-manifest.json'
        if digest(manifest) != plan['config']['build_manifest_sha256']:
            raise ValueError('Frozen build manifest changed')
        subprocess.run([sys.executable,str(pinned(s['latest_tool'])),'--manifest',str(manifest),
                        '--out',s['latest']],check=True)
        running = subprocess.check_output(['ssh','-F',str(runtime/'ssh.conf'),'pool-b-objects',
             'docker','inspect','--format','{{.State.Running}}',collector['collector_name']], text=True)
        if running.strip() != 'true':
            raise ValueError('Original live C2 collector not running')
        subprocess.run([sys.executable,str(pinned(s['draft_tool'])),*s['args']],check=True)
        request = Path(s['request'])
        r = load(request)
        if r.get('reviewed') is not True or r['cell_id'] != cell['id'] or r['runtime_control'] != recipe['runtime_control']:
            raise ValueError('Generated request differs from the intended B mixed fio cell')
        write_new(request.parent/'activation.json',dict(draft=draft_pin,c2=completed_pin,request=ref(request),
            scope='Run one mixed fio condition using its own calibration; no automatic retry.'))
        runpy.run_path(str(pinned(s['runner'])))['execute'](
            SimpleNamespace(request=request,sha256=digest(request)),r,control,plan,runtime,cell)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-repo', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    draft = sub.add_parser('draft')
    for key in ('watch','source','out'):
        draft.add_argument('--'+key,type=Path,required=True)
    draft.add_argument('--watch-sha256',required=True)
    run = sub.add_parser('run')
    for key in ('draft','completed'):
        run.add_argument('--'+key,type=Path,required=True)
        run.add_argument('--'+key+'-sha256',required=True)
    a = parser.parse_args()
    sys.path.insert(0,str(a.runtime_repo.resolve(strict=True)))
    from bench.runtime import pilot_context
    from bench.runtime.common import digest,load,pinned,write_new
    from bench.runtime.pilot_pools import active_conflicts,cross_pool_proof,resource_guard
    if a.action == 'draft':
        prepare(a, dict(path=str(a.watch),sha256=a.watch_sha256))
    else:
        execute(a,dict(path=str(a.draft),sha256=a.draft_sha256),
                dict(path=str(a.completed),sha256=a.completed_sha256))
