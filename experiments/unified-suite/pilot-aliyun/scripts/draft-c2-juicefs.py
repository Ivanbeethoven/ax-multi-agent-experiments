#!/usr/bin/env python3
"""Write a coordinator-only C2 recipe; never contacts a worker or claims a cell."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context
from bench.runtime.pilot_pools import fleet_node,cross_pool_proof,alias

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def trace_source(copy,cell):
    required=('manifest_sha256','binary_sha256','case','manifest','binary','verified_bytes','manifest_files','initial_entries')
    if copy.get('passed') is not True or copy.get('destination')!=alias(cell['pool'],'client') or Path(copy.get('case','')).name!=cell['params']['trace']:
        raise ValueError('Actual completed copy for this exact destination and trace required')
    if any(k not in copy for k in required) or any(type(copy[k]) is not int or copy[k]<=0 for k in ('verified_bytes','manifest_files','initial_entries')):
        raise ValueError('Complete actual copied input inventory required')
    if any(not Path(copy[k]).is_absolute() for k in ('case','manifest','binary')):raise ValueError('Absolute actual source paths required')
    return {k:copy[k] for k in required}


def draft(a):
    plan=load(a.control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['workload']=='trace' and c['arm_id']=='juicefs')
    runtime_ref=pilot_context.reference(a);runtime=pilot_context.select(a.control,runtime_ref);fleet=load(runtime/'fleet.json');node=fleet_node(fleet,cell['pool'],'client')
    if cell['pool']!='pool-b':raise ValueError('This explicit continuation is for pool-b')
    with sqlite3.connect('file:'+str(a.control/'state.sqlite')+'?mode=ro',uri=True) as db:
        if db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()[0]!='pending' or db.execute('SELECT 1 FROM stages WHERE cell_id=?',(cell['id'],)).fetchone():raise ValueError('Original C2 cell must be unstarted; no retry')
    source=trace_source(load(a.copy_proof),cell);oldasset=load(a.prior_asset)
    if oldasset.get('pool')!=cell['pool'] or oldasset.get('product')!='juicefs' or oldasset['plan']['sha256']!=digest(a.control/'suite/plan.json'):raise ValueError('Actual same-plan pool-B asset binding required')
    remote_plan=oldasset['plan'];inventory=oldasset['endpoint_binding']['inventory'];binary=oldasset['filesystem_binary']
    hardware=load(a.shared_qualification/'hardware.json')
    if hardware.get('passed') is not True or hardware.get('pool')!=cell['pool'] or hardware['inventory_sha256']!=digest(runtime/'fleet.json'):raise ValueError('Actual runtime/shared qualification differs')
    cross_pool_proof(a.cross_pool_qualification,plan,runtime/'fleet.json')
    if not 20<=a.database<256 or a.database==oldasset['database']:raise ValueError('Dedicated candidate database required')
    code=a.code/'experiments/unified-suite/pilot-aliyun/scripts';worker=Path(node['repo'])/'experiments/unified-suite/pilot-aliyun/scripts'
    names=['prepare-c2.py','prepare-c2-controller.py','attest-client-binary.py','draft-one-c1.py','run-one-c1.py','calibrate-c1.py','check-calibration.py','admit-c1.py','run-c1.py','pilot-node.py','make-observer.py','qualify-trace.py','capacity-check.py']
    tools={name:ref(code/name) for name in names}
    # Frozen controller and peer worker sources are identical for these three helpers;
    # installation and hashes must still be checked on the actual client after handoff.
    remote_tools={name:dict(path=str(worker/name),sha256=tools[name]['sha256']) for name in ('pilot-node.py','make-observer.py','qualify-trace.py')}
    shared_rustfs=load(a.shared_qualification/'rustfs-source.json');container=shared_rustfs['container_id']
    q=a.control/'qualification/pool-b-c2-01';run_dir=a.control/'one-cell-runs'/(cell['id']+'-c201');request=a.control/'requests/pool-b-juicefs-c2-01.json'
    capacity=a.control/'capacity-b-c2-01';latest=a.control/'provenance/latest-before-b-c2-01.json'
    remote_prepare='/srv/ax-client/preparation/c2-juicefs-01/prepare-c2.py'
    commands={
        'capacity_before':['python3',str(code/'capacity-check.py'),'snapshot','--control',str(a.control),'--pool',cell['pool'],'--objects-container',container,'--out',str(capacity/'before.json')],
        'client_qualification':['python3',remote_prepare,'--product','juicefs','--asset',str(a.asset),'--database',str(a.database),'--plan',remote_plan['path'],
            '--case',source['case'],'--replay-binary',source['binary'],'--node-tool',remote_tools['pilot-node.py']['path'],'--node-tool-sha256',remote_tools['pilot-node.py']['sha256'],
            '--qualifier',remote_tools['qualify-trace.py']['path'],'--qualifier-sha256',remote_tools['qualify-trace.py']['sha256'],'--attempt','bc201',
            '--filesystem-binary',binary['path'],'--filesystem-binary-sha256',binary['sha256'],'--inventory',inventory['path'],
            '--portable-terminal-probe','--cache-parent-fds','--out',str(a.route)],
        'collect_qualification':['python3',str(code/'prepare-c2-controller.py'),'--control',str(a.control),'--shared-qualification',str(a.shared_qualification),'--product','juicefs',
            '--route',str(a.route),'--asset',str(a.asset),'--case',source['case'],'--manifest',source['manifest'],'--binary',source['binary'],
            '--verifier',remote_tools['qualify-trace.py']['path'],'--cache-parent-fds','--out',str(q)],
        'attest_client':['python3',str(code/'attest-client-binary.py'),'--qualification',str(q/'juicefs'),'--ssh-config',str(runtime/'ssh.conf'),
            '--client-alias',alias(cell['pool'],'client'),'--release',str(a.control/'provenance/juicefs-release.json'),
            '--admitter',str(code/'admit-c1.py'),'--build-manifest',str(a.control/'provenance/build-manifest.json'),'--route-key','complete_route','--mount-spec',str(a.route/'mount/mount.reviewed.json')],
        'capacity_after':['python3',str(code/'capacity-check.py'),'snapshot','--control',str(a.control),'--pool',cell['pool'],'--before',str(capacity/'before.json'),'--objects-container',container,'--out',str(capacity/'after.json')],
        'capacity_budget':['python3',str(code/'capacity-check.py'),'budget','--before',str(capacity/'before.json'),'--after',str(capacity/'after.json'),'--extra-full-copies','7','--reserve','0.2','--out',str(capacity/'budget.json')],
        'draft_six_and_formal':['python3',str(code/'draft-one-c1.py'),'--control',str(a.control),'--qualification',str(q),'--cell',cell['id'],
            '--calibrator',str(code/'calibrate-c1.py'),'--checker',str(code/'check-calibration.py'),'--admitter',str(code/'admit-c1.py'),'--runner',str(code/'run-c1.py'),
            '--node-tool',remote_tools['pilot-node.py']['path'],'--node-tool-sha256',remote_tools['pilot-node.py']['sha256'],'--observer-tool-name','make-observer.py','--observer-tool-sha256',remote_tools['make-observer.py']['sha256'],
            '--collector-name',a.incoming_collector_name,'--collector-out',a.incoming_collector_out,'--attempt-suffix','c201','--run-directory',str(run_dir),
            '--latest-check',str(latest),'--cross-pool-qualification',str(a.cross_pool_qualification),'--out',str(request)]}
    if runtime_ref:
        for key in ('capacity_before','capacity_after','collect_qualification','draft_six_and_formal'):
            commands[key]+=['--runtime-control',runtime_ref['path'],'--runtime-control-sha256',runtime_ref['sha256']]
    value=dict(schema='c2-juicefs-coordinator-draft-v1',execution_enabled=False,reviewed=False,at=datetime.now(timezone.utc).isoformat(),cell_id=cell['id'],pool=cell['pool'],candidate_database=a.database,
        plan=ref(a.control/'suite/plan.json'),runtime_control=runtime_ref,source=source,tools=tools,remote_tools=remote_tools,
        commands=commands,client_helper_install=dict(source=tools['prepare-c2.py'],destination=remote_prepare,overwrite=False),remote_plan=remote_plan,remote_inventory=inventory,filesystem_binary=binary,
        blocked_until=['C4 owner hands off B after normal closure and retention; verify its actual final collector matches this recipe.',
            'Check all actual campaign databases for B leases and hold the common physical runner locks throughout preparation and six trials/formal.',
            'Verify actual current B hostname/boot/worker hashes, old remote plan/inventory hashes, empty slot, candidate DBSIZE=0 and absent new asset/route/output paths.',
            'Capture before capacity before any C2 backend write; execute complete strict replay and original cold readback, then collect/attest/after snapshot. Budget seven additional full-copy costs and 20% reserve.',
            'Record a fresh actual branch-tip receipt and require unchanged frozen builds. Generate the real reviewed six-trial request only after all qualifications and capacity pass.'],
        source_pins=[ref(__file__),ref(a.copy_proof),ref(a.prior_asset),ref(a.shared_qualification/'hardware.json'),ref(a.shared_qualification/'juicefs/metrics.json'),ref(a.cross_pool_qualification),ref(a.shared_qualification/'rustfs-source.json')],
        scope='Only a future recipe; no worker contacted, service changed, database selected in Redis, qualification asserted, calibration reused, or cell claimed. Full fixed trace includes repository and HOME/XDG state; no model calls.')
    a.out.mkdir(parents=True,exist_ok=False);write_new(a.out/'draft.json',value);print(json.dumps(dict(draft=ref(a.out/'draft.json'),execution_enabled=False,cell_id=cell['id'])))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','code','copy-proof','prior-asset','shared-qualification','cross-pool-qualification','asset','route','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--database',type=int,required=True);p.add_argument('--incoming-collector-name',required=True);p.add_argument('--incoming-collector-out',required=True);pilot_context.arguments(p)
    draft(p.parse_args())
