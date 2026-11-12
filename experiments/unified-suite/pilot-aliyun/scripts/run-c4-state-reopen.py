#!/usr/bin/env python3
"""Run one authorized model-free qualification only after both actual pool handoffs."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid


CURRENT_BOOTS = 'c4-state-reopen-current-boots-v1'
REQUEST_V1 = 'pilot-c4-state-reopen-request-v1'
REQUEST_V2 = 'pilot-c4-state-reopen-request-v2'
AUTHORIZATION = 'pilot-c4-state-reopen-launch-authorization-v1'
EXPORT_WORKER_SHA = 'ae48f43d2cfe54d9b29e021f5e9e3f767bdac7271644120b1d61c370555c5a37'
EXPORT_WORKER_BOUNDED_CLOSURE_SHA = 'bc340e15a606219c4ebd62dbed5daa29e6d71a9b7545a841a8c767b177cac7be'
V2_FIELDS = {'schema','attempt','draft','handoffs','latest','worker','verified_checkpoint',
             'source_tree','peers','current_boots'}
SIDES = ('source', 'target')
SLOT = {'cpu.max':'200000 100000', 'memory.max':'8589934592', 'memory.swap.max':'0', 'cgroup.procs':''}
CLIENT_PROBE = r'''
import json, os, socket, subprocess
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

root = Path('/srv/ax-client')
if root.resolve() != root or not os.path.ismount(root):
    raise ValueError('Original client data disk must be mounted')
status = root.stat()
major_minor = f'{os.major(status.st_dev)}:{os.minor(status.st_dev)}'
devices = json.loads(subprocess.check_output(
    ['lsblk','--json','--paths','--output','PATH,TYPE,SERIAL,MAJ:MIN,MOUNTPOINTS'], text=True, timeout=5))
matches = [row for row in devices['blockdevices'] if row['type']=='disk'
           and row['maj:min']==major_minor and str(root) in row.get('mountpoints', [])]
if len(matches)!=1 or not matches[0].get('serial'):
    raise ValueError('Original client data mount must bind one identifiable disk')
serial = matches[0]['serial'].strip()
disk_id = serial if serial.startswith('d-') else 'd-'+serial

opener = build_opener(ProxyHandler({}))
with opener.open(Request('http://100.100.100.200/latest/api/token', method='PUT',
    headers={'X-aliyun-ecs-metadata-token-ttl-seconds':'60'}), timeout=3) as response:
    token = response.read().decode()
with opener.open(Request('http://100.100.100.200/latest/meta-data/instance-id',
    headers={'X-aliyun-ecs-metadata-token':token}), timeout=3) as response:
    instance_id = response.read().decode().strip()

slot = Path('/sys/fs/cgroup/ax-slot-1')
print(json.dumps(dict(hostname=socket.gethostname(), instance_id=instance_id,
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    data_disk_ids=[disk_id], work_root=str(root), device=major_minor,
    slot={name:(slot/name).read_text().strip() for name in
          ('cpu.max','memory.max','memory.swap.max','cgroup.procs')})))
'''


def require_grant(presented, current, side):
    if presented != current or current['side'] != side:
        raise PermissionError('Old/other executor grant rejected before transport')


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_pin(pin):
    """Read launch authority before importing runtime code or starting SSH."""
    path = Path(pin['path'])
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Absolute pinned launch file required')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin['sha256']:
        raise ValueError('Pinned launch file changed')
    return json.loads(raw.decode('utf-8-sig'))


def launch_identity(request, route, request_sha, runner_sha, runtime_repo, output):
    boot = request.get('current_boots')
    attempt = request.get('attempt')
    worker = request.get('worker')
    if (request.get('schema') != REQUEST_V2 or set(request) != V2_FIELDS
            or not isinstance(attempt, str) or not re.fullmatch('[a-z0-9-]{3,24}', attempt)
            or not isinstance(boot, dict) or set(boot) != {'path','sha256'}
            or not isinstance(boot['path'], str) or not Path(boot['path']).is_absolute()
            or not isinstance(boot['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', boot['sha256'])):
        raise ValueError('Exact v2 request with current boot proof and unique attempt required')
    if (not isinstance(worker, dict) or set(worker) != {'path','sha256'}
            or not isinstance(worker['path'], str) or not Path(worker['path']).is_absolute()
            or worker['sha256'] not in (EXPORT_WORKER_SHA, EXPORT_WORKER_BOUNDED_CLOSURE_SHA)):
        raise ValueError('V2 requires the pinned no-model OpenCode export worker')
    if (route.get('schema') != 'pilot-c4-state-reopen-draft-v1'
            or route.get('source_pool') != 'pool-a' or route.get('target_pool') != 'pool-b'
            or route.get('qualification_only') is not True or route.get('reviewed') is not False
            or route.get('execution_enabled') is not False or type(route.get('model_calls_allowed')) is not int
            or route['model_calls_allowed'] != 0):
        raise ValueError('Launch authorization covers only the A-to-B no-model qualification draft')
    if (not runtime_repo.is_absolute() or runtime_repo.resolve() != runtime_repo or not runtime_repo.is_dir()
            or not output.is_absolute() or output.resolve() != output or not output.parent.is_dir()
            or output.exists() or output.is_symlink()):
        raise ValueError('Canonical runtime directory and fresh absolute output required')
    return dict(schema=AUTHORIZATION, source='conversation-user-instruction',
                user_instruction='设定目标 完成所有的c1~c4的小规模实验吧', user_followup='继续完成实验',
                scope='A-to-B JuiceFS shared workspace OpenCode export qualification',
                source_pool='pool-a', target_pool='pool-b', formal_sample=False, model_calls_allowed=0,
                attempt=attempt, request_sha256=request_sha, runner_sha256=runner_sha,
                runtime_repo=str(runtime_repo), output=str(output))


def verify_launch(request, route, authorization, request_sha, runner_sha, runtime_repo, output):
    if request.get('schema') == REQUEST_V1:
        if (authorization is not None or request.get('reviewed') is not True
                or route.get('schema') != 'pilot-c4-state-reopen-draft-v1' or route['model_calls_allowed'] != 0):
            raise ValueError('Explicit reviewed execution, storage qualification only')
        return
    expected = launch_identity(request, route, request_sha, runner_sha, runtime_repo, output)
    if (authorization != expected or authorization['formal_sample'] is not False
            or type(authorization['model_calls_allowed']) is not int):
        raise ValueError('Recorded user authorization must bind this exact diagnostic launch')


def record_launch_authorization(argv):
    p = argparse.ArgumentParser(description='Record existing user authorization without contacting workers')
    for name in ('request','runtime-repo','out','authorization-out'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--sha256',required=True)
    a = p.parse_args(argv)
    request = read_pin(dict(path=str(a.request),sha256=a.sha256))
    route = read_pin(request['draft'])
    value = launch_identity(request,route,a.sha256,sha256(__file__),a.runtime_repo,a.out)
    path = a.authorization_out
    if (not path.is_absolute() or path.resolve() != path or not path.parent.is_dir()
            or path.exists() or path.is_symlink() or path == a.out):
        raise ValueError('New canonical absolute authorization file required')
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(sha256(path),path)


def checked_client(row, frozen):
    if (not isinstance(row, dict) or set(row) != {
            'hostname','instance_id','boot_id','data_disk_ids','work_root','device','slot'}
            or any(row[key] != frozen[key] for key in ('hostname','instance_id','data_disk_ids'))
            or len(row['data_disk_ids']) != 1 or row['work_root'] != '/srv/ax-client'
            or not isinstance(row['device'], str) or not re.fullmatch(r'[1-9][0-9]*:[0-9]+', row['device'])
            or row['slot'] != SLOT):
        raise ValueError('Frozen client host/instance, mounted data disk and original empty 2-CPU/8-GiB slot required')
    boot = row['boot_id']
    if not isinstance(boot, str) or not uuid.UUID(boot).int or str(uuid.UUID(boot)) != boot:
        raise ValueError('Live canonical client boot identity required')
    return row


def check_current_boots(value, draft_ref, route, frozen, *, now=None):
    if (not isinstance(value, dict) or set(value) != {
            'schema','read_only','at','draft','original_inventory','clients'}
            or value['schema'] != CURRENT_BOOTS or value['read_only'] is not True
            or value['draft'] != draft_ref or value['original_inventory'] != route['original_inventory']
            or not isinstance(value['clients'], dict) or set(value['clients']) != set(SIDES)):
        raise ValueError('Current boot evidence must bind the reviewed draft and frozen inventory')
    recorded = datetime.fromisoformat(value['at'])
    now = now or datetime.now(timezone.utc)
    if recorded.tzinfo is None or not 0 <= (now-recorded).total_seconds() <= 900:
        raise ValueError('Current client boot evidence is older than 900 seconds or in the future')
    rows = {side:checked_client(value['clients'][side],frozen[side]) for side in SIDES}
    for key in ('instance_id','boot_id'):
        if rows['source'][key]==rows['target'][key]:
            raise ValueError('Current boot evidence must identify two distinct executors')
    return rows


def frozen_clients(route, inventory, original):
    from bench.runtime.pilot_pools import pool_inventory, resource_ids
    if {route[side+'_pool'] for side in SIDES} != {'pool-a','pool-b'}:
        raise ValueError('Two frozen A/B client roles required')
    pools = {side:pool_inventory(inventory,route[side+'_pool']) for side in SIDES}
    clusters = {value['id']:value for value in original['config']['clusters']}
    for pool in pools.values():
        cluster = clusters[pool['pool_id']]
        if (cluster['inventory_sha256']!=route['original_inventory']['sha256']
                or set(cluster['resource_ids'])!=set(resource_ids(pool))):
            raise ValueError('Original inventory differs from the canonical resource locks')
    rows = {side:pool['roles']['client'] for side,pool in pools.items()}
    if any(route[side+'_host']!=rows[side] or route[side+'_ssh']!=route[side+'_pool']+'-client' for side in SIDES):
        raise ValueError('Draft SSH roles or clients differ from the frozen inventory')
    return rows


def check_authorized_storage(original_inventory, diagnostic_inventory):
    from bench.runtime.pilot_pools import pool_inventory
    source = pool_inventory(original_inventory,'pool-a')['roles']
    target = pool_inventory(original_inventory,'pool-b')['roles']['client']
    if diagnostic_inventory != {'pools':[dict(pool_id='pool-c4-state',roles={**source,'target':target})]}:
        raise ValueError('V2 requires A storage and the two original A/B clients')


def current_identity(observed, expected, frozen):
    checked_client(observed,frozen)
    if observed != expected:
        raise ValueError('Current client boot, device or empty slot changed after review')
    return {key:observed[key] for key in ('hostname','boot_id')}


def probe_current_boots(argv):
    p = argparse.ArgumentParser(description='Read both live client identities without launching any mounts')
    for name in ('draft','draft-sha256','runtime-repo','out'):
        p.add_argument('--'+name, required=True)
    a = p.parse_args(argv)
    sys.path.insert(0,a.runtime_repo)
    from bench.runtime.common import load, pinned, write_new
    from bench.suite_plan import check_plan
    draft_ref = dict(path=a.draft,sha256=a.draft_sha256)
    route = load(pinned(draft_ref))
    if route.get('schema')!='pilot-c4-state-reopen-draft-v1' or route.get('model_calls_allowed')!=0:
        raise ValueError('Exact no-model C4 draft required')
    original = check_plan(load(pinned(route['original_plan'])))
    frozen = frozen_clients(route,load(pinned(route['original_inventory'])),original)
    config = pinned(route['ssh'])
    output = Path(a.out)
    if (not output.is_absolute() or output.resolve()!=output or not output.parent.is_dir()
            or output.exists() or output.is_symlink()):
        raise ValueError('New canonical absolute current-boot output required')
    began = datetime.now(timezone.utc)
    rows = {}
    for side in SIDES:
        result = subprocess.check_output(['ssh','-F',str(config),'-o','BatchMode=yes','-o','ConnectTimeout=6',
            route[side+'_ssh'],'python3 -'],input=CLIENT_PROBE.encode(),timeout=22)
        rows[side] = checked_client(json.loads(result),frozen[side])
    value = dict(schema=CURRENT_BOOTS,read_only=True,at=began.isoformat(),draft=draft_ref,
                 original_inventory=route['original_inventory'],clients=rows)
    check_current_boots(value,draft_ref,route,frozen)
    write_new(output,value)


def main():
    if sys.argv[1:2] == ['probe-current-boots']:
        return probe_current_boots(sys.argv[2:])
    if sys.argv[1:2] == ['record-launch-authorization']:
        return record_launch_authorization(sys.argv[2:])
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'runtime-repo', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--authorization', type=Path)
    p.add_argument('--authorization-sha256')
    a = p.parse_args()
    if (a.authorization is None) != (a.authorization_sha256 is None):
        p.error('Supply both launch authorization path and SHA-256')
    request_ref = dict(path=str(a.request),sha256=a.sha256)
    request = read_pin(request_ref)
    s = read_pin(request['draft'])
    authorization_ref = (dict(path=str(a.authorization),sha256=a.authorization_sha256)
                         if a.authorization is not None else None)
    if request.get('schema') == REQUEST_V2 and authorization_ref is None:
        raise ValueError('Exact v2 launch authorization reference required')
    authorization = read_pin(authorization_ref) if authorization_ref is not None else None
    verify_launch(request,s,authorization,a.sha256,sha256(__file__),a.runtime_repo,a.out)
    sys.path[:0] = [str(a.runtime_repo), str(a.runtime_repo/'bench')]
    from bench.runtime.common import digest, load, pinned, write_new
    from bench.runtime.pilot_pools import active_conflicts, resource_guard
    from bench.suite_plan import check_plan

    def ref(path):
        return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))

    control = Path(s['canonical_control'])
    original = check_plan(load(pinned(s['original_plan'])))
    plan = check_plan(load(pinned(s['plan'])))
    ids = {side:s[side+'_pool'] for side in ('source','target')}
    pools = sorted(ids.values())
    cells = {pool:next(c for c in original['cells'] if c['pool']==pool) for pool in pools}
    boot_pin = request.get('current_boots')
    live_boots, frozen = None, None
    if 'current_boots' in request:
        original_inventory = load(pinned(s['original_inventory']))
        frozen = frozen_clients(s,original_inventory,original)
        live_boots = check_current_boots(load(pinned(boot_pin)),request['draft'],s,frozen)
        if authorization_ref is not None:
            check_authorized_storage(original_inventory,load(pinned(s['inventory'])))
    if set(request['handoffs']) != set(pools):
        raise ValueError('Both existing pools require actual explicit handoffs')
    for pool in pools:
        proof = load(pinned(request['handoffs'][pool]))
        if proof.get('pool') != pool or proof.get('safe_for_handoff') is not True:
            raise ValueError('Pool not explicitly handed off: '+pool)
    latest = load(pinned(request['latest']))
    if (latest.get('passed') is not True or latest['build_manifest_sha256'] != s['build_manifest']['sha256'] or
            len(latest.get('sources',[])) != 2 or any(x.get('matches') is not True for x in latest['sources']) or
            not 0 <= (datetime.now(timezone.utc)-datetime.fromisoformat(latest['at'])).total_seconds() <= 600):
        raise ValueError('Recheck both actual branch tips before launch')
    attempt = request['attempt']
    if not re.fullmatch('[a-z0-9-]{3,24}', attempt):
        raise ValueError('Unique short attempt required')
    ssh_config = pinned(s['ssh'])
    release = load(pinned(s['opencode_release']))
    archive, receipt = [pinned(s[k]) for k in ('checkpoint_archive','checkpoint_receipt')]
    checkpoint = pinned(request['verified_checkpoint'])
    if digest(checkpoint) != load(receipt)['checkpoint_sha256']:
        raise ValueError('Use the original verified checkpoint metadata')
    worker = pinned(request['worker'])
    scripts = a.runtime_repo/'experiments/unified-suite/pilot-aliyun/scripts'
    helpers = {name:ref(scripts/(name+'.py')) for name in ('pilot-node','prepare-session-input',
                'package-session-checkpoint','draft-handoff-target')}
    root = Path('/srv/ax-client/preparation')/attempt
    context = {}
    entered = set()
    with ExitStack() as locks:
        for pool in pools:
            locks.enter_context(resource_guard(control, cells[pool], original))
        for state in sorted(control.parent.glob('*/state.sqlite')):
            for pool in pools:
                if active_conflicts(state.parent, cells[pool], original):
                    raise ValueError('Active original lease on '+pool+' in '+str(state))
        a.out.mkdir(parents=True, exist_ok=False)
        started = dict(request=request_ref if authorization_ref is not None else ref(a.request),
                       handoffs=request['handoffs'], canonical_control=str(control),
                       qualification_only=True, model_calls=0, no_automatic_retry=True)
        if live_boots is not None:
            started['current_boots'] = boot_pin
        if authorization_ref is not None:
            started['launch_authorization'] = authorization_ref
        write_new(a.out/'started.json', started)

        def run(label, argv, data=None, *, timeout=None):
            directory = a.out/label
            directory.mkdir()
            argv = list(map(str,argv))
            write_new(directory/'intent.json', dict(argv=argv))
            with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
                status = subprocess.run(argv, input=data, stdout=stdout, stderr=stderr, timeout=timeout).returncode
            write_new(directory/'exit.json', dict(exit_code=status))
            if status:
                raise ValueError('Inspect original failed step '+str(directory))
            return (directory/'stdout').read_bytes()

        def ssh(side, label, argv, data=None, *, timeout=None):
            options = ['-o','BatchMode=yes','-o','ConnectTimeout=6'] if timeout else []
            return run(label, ['ssh','-F',ssh_config,*options,s[side+'_ssh'],shlex.join(list(map(str,argv)))],
                       data,timeout=timeout)

        def put(side, label, path, target):
            run(label, ['scp','-F',ssh_config,path,s[side+'_ssh']+':'+str(target)])
            return dict(path=str(target), sha256=digest(path))

        def get(side, label, pin):
            target = a.out/(label+'.json')
            run(label, ['scp','-F',ssh_config,s[side+'_ssh']+':'+pin['path'],target])
            if digest(target) != pin['sha256']:
                raise ValueError('Original source evidence changed: '+pin['path'])
            return target

        def ensure_slot(side):
            if live_boots is not None:
                # Recheck the exact live disk and boot while owning the existing
                # guard, before transferring a checkpoint or mounting anything.
                check_current_boots(load(pinned(boot_pin)),request['draft'],s,frozen)
                observed = json.loads(ssh(side,'check-'+side,['python3','-'],CLIENT_PROBE.encode(),timeout=22))
                return current_identity(observed,live_boots[side],frozen[side])
            original_probe = load(control/'probes'/(ids[side]+'-client')/'probe.json')
            code = ('from pathlib import Path; import json,socket; '
                    'print(json.dumps(dict(hostname=socket.gethostname(),'
                    'boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),'
                    'slot=Path("/sys/fs/cgroup/ax-slot-1/cgroup.procs").read_text().strip())))')
            actual = json.loads(ssh(side,'check-'+side,['python3','-c',code]))
            want = dict(hostname=s[side+'_host']['hostname'],boot_id=original_probe['boot_id'])
            if actual != dict(want,slot=''):
                raise ValueError('Original actual empty executor slot required: '+side)
            return want

        try:
            current_identities = ({side:ensure_slot(side) for side in SIDES}
                                  if live_boots is not None else None)
            for side in ('source','target'):
                identity = ensure_slot(side) if current_identities is None else current_identities[side]
                remote = request['peers'][side]
                path = Path(remote['repo'])
                if not path.is_absolute():
                    raise ValueError('Absolute frozen peer repo required')
                tools = path/'experiments/unified-suite/pilot-aliyun/scripts'
                peer_tools = {name:dict(path=str(tools/(name+'.py')),sha256=pin['sha256']) for name,pin in helpers.items()}
                if remote['opencode']['sha256'] != release['opencode_sha256']:
                    raise ValueError('Exact original application binary required')
                pins = [*peer_tools.values(),remote['opencode'],remote['juicefs']]
                code = ('import hashlib,pathlib\nfor pin in '+repr(pins)+':\n'
                        ' with pathlib.Path(pin["path"]).open("rb") as f:\n'
                        '  if hashlib.file_digest(f,"sha256").hexdigest()!=pin["sha256"]: raise ValueError(pin["path"])\n')
                ssh(side,'verify-'+side+'-pins',['python3','-'],code.encode())
                ssh(side,'create-'+side+'-tools',['mkdir',root])
                worker_pin = put(side,'worker-'+side,worker,root/'node.py')
                plan_pin = put(side,'plan-'+side,pinned(s['plan']),root/'plan.json')
                context[side] = dict(schema='pilot-c4-state-reopen-node-v1', reviewed=True, role=side,
                    identity=identity, plan=plan_pin, cell_id=s['cell_id'], juicefs=remote['juicefs'],
                    opencode=remote['opencode'], prepare_session_input=peer_tools['prepare-session-input'],
                    mount_run=str(Path('/srv/ax-client/m')/(attempt+'-'+side)))
                remote.update(worker=worker_pin, tool_pins=peer_tools)
            if context['source']['identity']==context['target']['identity'] or request['peers']['source']['juicefs']['sha256'] != request['peers']['target']['juicefs']['sha256']:
                raise ValueError('Two actual executors and the same JuiceFS bytes required')

            src = context['source']
            src.update(database=s['candidate_database'],asset=str(Path('/srv/ax-client/a')/attempt),
                inventory=put('source','inventory-source',pinned(s['inventory']),root/'inventory.json'),
                pilot_node=request['peers']['source']['tool_pins']['pilot-node'])
            arc = put('source','source-only-checkpoint-archive',archive,root/'checkpoint.tar.gz')
            rec = put('source','source-only-checkpoint-package-receipt',receipt,root/'receipt.json')
            packager = request['peers']['source']['tool_pins']['package-session-checkpoint']
            restored = json.loads(ssh('source','restore-source-only-input',['python3',packager['path'],'install',
                '--archive',arc['path'],'--receipt',rec['path'],'--receipt-sha256',rec['sha256'],'--out',root/'input']))
            if restored.get('passed') is not True or restored['checkpoint_sha256'] != digest(checkpoint):
                raise ValueError('Source-only complete checkpoint did not restore')
            src.update(checkpoint=dict(path=str(root/'input/checkpoint.json'),sha256=digest(checkpoint)),
                source_tree=dict(path=str(root/'input/source-tree.json'),sha256=digest(pinned(request['source_tree']))))

            current = old = dict(side='source',generation=1,attempt=attempt)
            write_new(a.out/'authority-01.json',current)

            def phase(side, grant):
                require_grant(grant,current,side)
                path = a.out/(side+'-request.json')
                write_new(path,context[side])
                pin = put(side,'request-'+side,path,root/'request.json')
                peer = request['peers'][side]
                entered.add(side)
                raw = ssh(side,'phase-'+side,['python3',peer['worker']['path'],'--runtime-repo',peer['repo'],
                        '--request',pin['path'],'--sha256',pin['sha256'],'--out',root/'evidence'])
                result = json.loads(raw)
                if (result.get('passed') is not True or result['role']!=side or
                        result['identity']!=context[side]['identity'] or result['native_model_calls']!=0 or
                        not result['mount_absent'] or not result['original_processes_exited']):
                    raise ValueError('Original node not normally closed')
                return result

            source = phase('source', old)
            state_ready_begin = time.monotonic_ns()
            slot = ssh('source','source-empty-after-close',['cat','/sys/fs/cgroup/ax-slot-1/cgroup.procs'])
            if slot.strip():
                raise ValueError('Source original slot is not empty after closure')
            source_result = a.out/'source-result.json'
            write_new(source_result, source)
            current = dict(side='target',generation=2,attempt=attempt)
            write_new(a.out/'authority-02.json',current)
            try:
                phase('source', old)
            except PermissionError:
                write_new(a.out/'old-grant-fenced.json',dict(old_grant_rejected=True,old=old,current=current,
                    source_sha256=digest(source_result),before_transport=True,
                    scope='Harness admission; source process/mount already closed. No JuiceFS native fencing.'))
            else:
                raise ValueError('Old writer grant not rejected')

            target = context['target']
            target.update(source_identity=source['identity'],
                checkpoint=put('target','target-checkpoint-metadata-only',checkpoint,root/'checkpoint.json'),
                draft_handoff_target=request['peers']['target']['tool_pins']['draft-handoff-target'])
            for name in ('asset','mount','command'):
                meta = get('source','source-'+name,source[name])
                target['source_'+name] = put('target','target-source-'+name,meta,root/('source-'+name+'.json'))
            target['source_result'] = put('target','target-source-closure',source_result,root/'source-result.json')
            target['fence'] = put('target','target-old-grant-fence',a.out/'old-grant-fenced.json',root/'fence.json')
            final = phase('target',current)
            if (final['anchor']!=source['anchor'] or final['workspace']!=source['workspace'] or
                    final['export']['export']['sha256']!=source['export']['export']['sha256'] or final['target_workspace_copy_bytes']!=0):
                raise ValueError('Target did not reopen exact original state')
            write_new(a.out/'target-result.json',final)
            result = dict(passed=True, qualification_only=True, formal_sample=False,
                source=ref(source_result), target=ref(a.out/'target-result.json'),
                old_grant=ref(a.out/'old-grant-fenced.json'), same_workspace=True, different_executor=True,
                native_model_calls=0, native_session_json_transferred_bytes=0, target_workspace_copy_bytes=0,
                source_close_to_target_complete_ack_seconds=(time.monotonic_ns()-state_ready_begin)/1e9,
                scope='Coordinator interval includes metadata transfer, target mount, export, fsync, drain and close; no formal resume latency.')
            if authorization_ref is not None:
                result['launch_authorization'] = authorization_ref
            write_new(a.out/'result.json',result)
        except Exception as error:
            write_new(a.out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False,
                handoff_permitted=False,note='Inspect original cgroup, mount supervisor and native debt before pool reuse.'))
            # A broken SSH transport is not a normal stop. Keep the actual pool
            # locks until read-only probes show the test cannot still be active.
            probe = '''
import json, pathlib, sys
from bench.runtime.common import alive, mounts
run, point = sys.argv[1:]
p = pathlib.Path(run)
proof = p/'process.json'
identities = json.loads(proof.read_text()) if proof.exists() else {}
live = [v for k,v in identities.items() if k in ('supervisor','process') and alive(v)]
slot = pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip()
mounted = mounts(point)
safe = not (live or slot or mounted)
print(json.dumps(dict(safe=safe,live=live,slot=slot,mounts=mounted)))
'''
            check = 0
            while entered:
                check += 1
                safe = set()
                for side in sorted(entered):
                    peer = request['peers'][side]
                    try:
                        prefix = 'import sys;sys.path.insert(0,'+repr(peer['repo'])+')\n'
                        result = json.loads(ssh(side, 'failure-probe-'+side+'-'+str(check),
                            ['python3','-c',prefix+probe,context[side]['mount_run'],s['mount_point']]))
                        if result.get('safe') is True:
                            safe.add(side)
                    except (ValueError, subprocess.SubprocessError, OSError):
                        pass
                entered -= safe
                if entered:
                    time.sleep(5)
            raise


if __name__ == '__main__':
    main()
