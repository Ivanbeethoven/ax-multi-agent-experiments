#!/usr/bin/env python3
"""Run one reviewed model-free qualification only after both actual pool handoffs."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time


def require_grant(presented, current, side):
    if presented != current or current['side'] != side:
        raise PermissionError('Old/other executor grant rejected before transport')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'runtime-repo', 'out'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--sha256', required=True)
    a = p.parse_args()
    sys.path[:0] = [str(a.runtime_repo), str(a.runtime_repo/'bench')]
    from bench.runtime.common import digest, load, pinned, write_new
    from bench.runtime.pilot_pools import active_conflicts, resource_guard
    from bench.suite_plan import check_plan

    def ref(path):
        return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))

    request = load(pinned(dict(path=str(a.request), sha256=a.sha256)))
    s = load(pinned(request['draft']))
    if (request.get('schema') != 'pilot-c4-state-reopen-request-v1' or request.get('reviewed') is not True or
            s.get('schema') != 'pilot-c4-state-reopen-draft-v1' or s['model_calls_allowed'] != 0):
        raise ValueError('Explicit reviewed execution, storage qualification only')
    control = Path(s['canonical_control'])
    original = check_plan(load(pinned(s['original_plan'])))
    plan = check_plan(load(pinned(s['plan'])))
    ids = {side:s[side+'_pool'] for side in ('source','target')}
    pools = sorted(ids.values())
    cells = {pool:next(c for c in original['cells'] if c['pool']==pool) for pool in pools}
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
        write_new(a.out/'started.json', dict(request=ref(a.request), handoffs=request['handoffs'],
            canonical_control=str(control), qualification_only=True, model_calls=0, no_automatic_retry=True))

        def run(label, argv, data=None):
            directory = a.out/label
            directory.mkdir()
            argv = list(map(str,argv))
            write_new(directory/'intent.json', dict(argv=argv))
            with (directory/'stdout').open('xb') as stdout, (directory/'stderr').open('xb') as stderr:
                status = subprocess.run(argv, input=data, stdout=stdout, stderr=stderr).returncode
            write_new(directory/'exit.json', dict(exit_code=status))
            if status:
                raise ValueError('Inspect original failed step '+str(directory))
            return (directory/'stdout').read_bytes()

        def ssh(side, label, argv, data=None):
            return run(label, ['ssh','-F',ssh_config,s[side+'_ssh'],shlex.join(list(map(str,argv)))], data)

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
            frozen = load(control/'probes'/(ids[side]+'-client')/'probe.json')
            code = ('from pathlib import Path; import json,socket; '
                    'print(json.dumps(dict(hostname=socket.gethostname(),'
                    'boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),'
                    'slot=Path("/sys/fs/cgroup/ax-slot-1/cgroup.procs").read_text().strip())))')
            actual = json.loads(ssh(side,'check-'+side,['python3','-c',code]))
            want = dict(hostname=s[side+'_host']['hostname'],boot_id=frozen['boot_id'])
            if actual != dict(want,slot=''):
                raise ValueError('Original actual empty executor slot required: '+side)
            return want

        try:
            for side in ('source','target'):
                identity = ensure_slot(side)
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
            write_new(a.out/'result.json',dict(passed=True, qualification_only=True, formal_sample=False,
                source=ref(source_result), target=ref(a.out/'target-result.json'),
                old_grant=ref(a.out/'old-grant-fenced.json'), same_workspace=True, different_executor=True,
                native_model_calls=0, native_session_json_transferred_bytes=0, target_workspace_copy_bytes=0,
                source_close_to_target_complete_ack_seconds=(time.monotonic_ns()-state_ready_begin)/1e9,
                scope='Coordinator interval includes metadata transfer, target mount, export, fsync, drain and close; no formal resume latency.'))
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
