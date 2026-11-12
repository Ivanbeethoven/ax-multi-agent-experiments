#!/usr/bin/env python3
"""One clean JuiceFS state-reopen qualification on an existing executor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


NETWORK_WRAPPER = '''
import json, os, pathlib, sys
before, receipt, binary, session = sys.argv[1:]
after = os.readlink('/proc/self/ns/net')
interfaces = sorted(line.split(':', 1)[0].strip()
                    for line in pathlib.Path('/proc/self/net/dev').read_text().splitlines()[2:])
if before == after or interfaces != ['lo']:
    raise ValueError('Native export requires an isolated, loopback-only network namespace')
with open(receipt, 'x') as stream:
    json.dump(dict(parent=before, child=after, interfaces=interfaces, external_network=False), stream)
    stream.write('\\n')
os.execv(binary, [binary, 'export', session])
'''


def wait_export_group(pgid, members, *, timeout=5.0, poll=0.05,
                      monotonic=time.monotonic, sleep=time.sleep):
    """Wait only for normal exit of the original process group."""
    started = monotonic()
    first = current = sorted(members(pgid))
    while current:
        remaining = timeout - (monotonic()-started)
        if remaining <= 0:
            break
        sleep(min(poll,remaining))
        current = sorted(members(pgid))
    return dict(group_members_after_parent_exit=first, group_members_after=current,
                group_closure_wait_seconds=monotonic()-started,
                group_closure_timeout_seconds=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('request', 'runtime-repo', 'out'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    a = parser.parse_args()
    root = a.runtime_repo.resolve(strict=True)
    sys.path[:0] = [str(root), str(root/'bench')]
    from bench.runtime import node
    from bench.runtime.common import alive, cgroup_limits, digest, identity, load, mounts, pinned, write_new
    from bench.runtime.observe import prometheus
    from bench.runtime.prepare import AGENT_BUDGET
    from bench.session_check import check_after
    from bench.suite_mount import cell_of
    from bench.suite_plan import check_plan, fingerprint
    scripts = root/'experiments/unified-suite/pilot-aliyun/scripts'

    def ref(path):
        return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))

    s = load(pinned(dict(path=str(a.request), sha256=a.sha256)))
    role = s['role']
    if s.get('schema') != 'pilot-c4-state-reopen-node-v1' or s.get('reviewed') is not True or role not in ('source', 'target'):
        raise ValueError('Explicit one-host qualification request required')
    slot = Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs')
    actual = dict(hostname=socket.gethostname(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    if actual != s['identity'] or slot.read_text().strip():
        raise ValueError('Expected actual idle executor required')
    if role == 'target' and s['identity'] == s['source_identity']:
        raise ValueError('A different actual executor is required')
    plan = check_plan(load(pinned(s['plan'])))
    cell = cell_of(plan, s['cell_id'])
    if cell['workload'] != 'handoff' or cell['arm_id'] != 'juicefs-move' or cell['params']['trace'] != 'opencode-checkpoint-20261111':
        raise ValueError('Only the explicit JuiceFS move qualification is supported')
    checkpoint = load(pinned(s['checkpoint']))
    binary = pinned(s['opencode'])
    jfs = pinned(s['juicefs'])
    if (checkpoint['contains_provider_master_key'] is not False or checkpoint['source_process_exited'] is not True or
            checkpoint['sqlite_quick_check'] != 'ok' or s['opencode']['sha256'] != checkpoint['opencode_binary_sha256']):
        raise ValueError('Closed native checkpoint and original exact OpenCode binary required')
    point = Path('/srv/ax-pilot-session')
    workspace = point/'workspace'
    project = workspace/'repo'
    if (checkpoint['canonical_workspace'] != str(workspace) or checkpoint['canonical_project'] != str(project) or
            mounts(point) or point.is_symlink() or
            (point.exists() and (not point.is_dir() or any(point.iterdir())))):
        raise ValueError('Do not hide data: original canonical directory must be absent or empty')
    if not a.out.is_absolute() or a.out.is_relative_to(point):
        raise ValueError('Fresh absolute evidence outside the workspace required')
    a.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    prep = runpy.run_path(str(pinned(s['prepare_session_input'])))
    tree, sync = prep['tree'], prep['sync_tree']
    mount_run = Path(s['mount_run'])
    if not mount_run.is_absolute() or mount_run.is_relative_to(point) or mount_run.exists():
        raise ValueError('New independent supervisor directory required')
    slot.write_text(str(os.getpid()))
    budget = cgroup_limits(AGENT_BUDGET)

    def helper(label, argv):
        path = a.out/label
        path.mkdir()
        write_new(path/'command.json', dict(argv=list(map(str,argv)), automatic_retry=False))
        with (path/'stdout').open('xb') as stdout, (path/'stderr').open('xb') as stderr:
            done = subprocess.run(list(map(str,argv)), stdout=stdout, stderr=stderr)
        write_new(path/'exit.json', dict(exit_code=done.returncode))
        done.check_returncode()

    def native_export():
        config = workspace/'config/opencode.json'
        settings = load(config)
        dummy = settings['provider']['experiment']['options']
        if dummy['apiKey'] != 'local-session-no-master-key' or not dummy['baseURL'].startswith('http://127.0.0.1:'):
            raise ValueError('Never use live provider configuration in the storage qualification')
        if config.is_symlink() or project.is_symlink() or project.resolve(strict=True) != project:
            raise ValueError('Original canonical project/configuration required')
        out = a.out/'native-export'
        out.mkdir()
        home = out/'empty-home'
        home.mkdir(mode=0o700)
        env = dict(PATH='/usr/local/bin:/usr/bin:/bin', HOME=str(home), USERPROFILE=str(home),
            OPENCODE_CONFIG=str(config), NO_COLOR='1', OPENCODE_DISABLE_AUTOUPDATE='1',
            OPENCODE_DISABLE_SHARE='1', OPENCODE_DISABLE_LSP_DOWNLOAD='1',
            OPENCODE_DISABLE_DEFAULT_PLUGINS='true', OPENCODE_DISABLE_EXTERNAL_SKILLS='true',
            OPENCODE_DISABLE_CLAUDE_CODE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
        for name in ('data', 'state', 'cache', 'config'):
            path = workspace/'source'/name
            if not path.is_dir() or path.resolve() != path:
                raise ValueError('Original workspace-local XDG directory required: '+name)
            env['XDG_'+name.upper()+'_HOME'] = str(path)
        net = os.readlink('/proc/self/ns/net')
        argv = ['unshare', '--net', '--', sys.executable, '-c', NETWORK_WRAPPER, net,
                str(out/'network.json'), str(binary), checkpoint['session_id']]
        if any(home.iterdir()):
            raise ValueError('Fresh empty HOME required')
        write_new(out/'started.json', dict(action='export', workspace=str(workspace), project=str(project),
            xdg={k:v for k,v in env.items() if k.startswith('XDG_')}, home=str(home), home_empty=True,
            binary=s['opencode'], session_id=checkpoint['session_id'], automatic_retry=False))
        with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
            process = subprocess.Popen(argv, cwd=project, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
            original = identity(process.pid)
            code = process.wait()
        closure = wait_export_group(process.pid, node.process_group_members)
        write_new(out/'exit.json', dict(exit_code=code, original_process=original, **closure))
        if not original or code or closure['group_members_after']:
            raise ValueError('Native export or its process closure failed')
        raw = load(out/'stdout')
        validation = check_after(checkpoint['session_prefix'], raw, False)
        canonical = (json.dumps(raw, ensure_ascii=False)+'\n').encode()
        if hashlib.sha256(canonical).hexdigest() != checkpoint['session_export_sha256']:
            raise ValueError('Original native OpenCode session export bytes differ')
        with (out/'export.json').open('xb') as stream:
            stream.write(canonical)
        return dict(export=ref(out/'export.json'), network=ref(out/'network.json'),
                    validation=validation, process=original)

    def debt():
        out = a.out/'debt'
        out.mkdir()
        client = build_opener(ProxyHandler({}))
        wanted = {'juicefs_staging_block_bytes', 'juicefs_staging_blocks', 'juicefs_staging_writing_blocks'}
        samples = []
        for i in range(2):
            with client.open('http://127.0.0.1:9567/metrics', timeout=5) as response:
                raw = response.read().decode()
            (out/f'{i}.prom').write_text(raw)
            selected = {k:v for k,v in prometheus(raw).items() if k.split('{',1)[0] in wanted}
            if {k.split('{',1)[0] for k in selected} != wanted or any(v != 0 for v in selected.values()):
                raise ValueError('Require all native pending upload gauges to be zero')
            path = out/f'{i}.json'
            write_new(path, dict(passed=True, monotonic_ns=time.monotonic_ns(), pending_upload_bytes=0,
                pending_upload_operations=0, native_gauges=selected, prometheus=ref(out/f'{i}.prom')))
            samples.append(ref(path))
            if i == 0:
                time.sleep(1.05)
        return samples

    state = None
    try:
        if role == 'source':
            original_workspace = Path(s['checkpoint']['path']).parent/'workspace'
            expected = load(pinned(s['source_tree']))
            if tree(original_workspace) != expected:
                raise ValueError('Original complete checkpoint differs')
            if any(k.endswith(('.db-wal','.db-journal')) and v.get('bytes',0) for k,v in expected.items()):
                raise ValueError('Checkpoint has nonempty SQLite journal')
            asset = Path(s['asset'])
            tool = pinned(s['pilot_node'])
            helper('initialize-source', [sys.executable, tool, 'asset-init', '--asset', asset,
                   '--out', a.out/'asset-init-proof', '--product', 'juicefs', '--database', s['database'],
                   '--plan', s['plan']['path'], '--cell', cell['id'], '--inventory', pinned(s['inventory']),
                   '--filesystem-binary', jfs, '--filesystem-binary-sha256', s['juicefs']['sha256'],
                   '--mount-point', point])
            helper('draft-source', [sys.executable, tool, 'draft-mount', '--asset', asset, '--out', a.out/'draft',
                   '--cell', cell['id']])
            mount = ref(a.out/'draft/mount.reviewed.json')
            asset_pin = ref(asset/'asset.json')
        else:
            previous = load(pinned(s['source_result']))
            fence = load(pinned(s['fence']))
            if (previous['passed'] is not True or previous['role'] != 'source' or
                    previous['identity'] != s['source_identity'] or
                    fence.get('old_grant_rejected') is not True or fence['source_sha256'] != s['source_result']['sha256']):
                raise ValueError('Actual source normal closure and stale grant denial required')
            draft = runpy.run_path(str(pinned(s['draft_handoff_target'])))['draft']
            result = draft(dict(reviewed=True, plan=s['plan'], cell_id=cell['id'], source_asset=s['source_asset'],
                source_mount=s['source_mount'], source_command=s['source_command'], binary=s['juicefs'],
                purpose='experiment'), a.out/'draft')
            if result['anchor'] != previous['anchor'] or result['workspace_copy_bytes'] != 0:
                raise ValueError('Destination must reopen original backend identity without copying state')
            mount = result['mount']
            asset_pin = s['source_asset']
        mount_begin = time.monotonic_ns()
        ready = node.start(mount, mount_run)
        mount_ready = time.monotonic_ns()
        state = mount_run
        if role == 'source':
            shutil.copytree(original_workspace, workspace, symlinks=True)
            if tree(workspace) != expected:
                raise ValueError('One initial source import differs from frozen input')
            sync(workspace)
        else:
            if fingerprint(tree(workspace)) != previous['closed_workspace_tree_sha256']:
                raise ValueError('Original complete workspace/state differs on cold target before app opens')
        export = native_export()
        export_ready = time.monotonic_ns()
        synced = sync(workspace)
        closed_tree = fingerprint(tree(workspace))
        samples = debt()
        stop = node.control(state, 'stop')
        state = None
        proc = load(mount_run/'process.json')
        deadline = time.monotonic()+5
        while time.monotonic() < deadline and alive(proc['supervisor']):
            time.sleep(.02)
        if (stop['anchor'] != ready['anchor'] or stop['original_mount'] != ready['mount'] or
                stop['exit_code'] != 0 or not stop['mount_absent'] or mounts(point) or stop['active_workers'] or
                any(alive(p) for p in (proc['supervisor'], proc['process'], export['process']))):
            raise ValueError('Original mount/application ownership remains live')
        result = dict(passed=True, qualification_only=True, role=role, identity=actual, anchor=ready['anchor'],
            workspace=str(workspace), project=str(project), budget=budget, asset=asset_pin, mount=mount,
            command=load(pinned(mount))['command'], export=export, debt_samples=samples,
            closed_workspace_tree_sha256=closed_tree, sync=synced, stop=ref(mount_run/'stopped.json'),
            original_processes=[proc['supervisor'], proc['process'], export['process']],
            original_processes_exited=True, mount_absent=True,
            native_json_imports=0, native_model_calls=0, real_resume=False,
            local_mount_ready_seconds=(mount_ready-mount_begin)/1e9,
            local_mount_ready_to_native_export_seconds=(export_ready-mount_ready)/1e9,
            target_workspace_copy_bytes=0 if role=='target' else None)
        write_new(a.out/'result.json', result)
        print(json.dumps(result))
    except Exception as error:
        write_new(a.out/'failed.json', dict(passed=False, error=str(error), automatic_retry=False,
            active_mount_directory=str(state) if state else None))
        if state is not None:
            stop = node.control(state, 'stop')
            write_new(a.out/'failure-normal-stop.json', stop)
        raise


if __name__ == '__main__':
    main()
