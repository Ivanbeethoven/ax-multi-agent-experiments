"""Prepare existing SSH endpoints and invoke one runtime action at a time."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from .common import digest, load, pinned, private_dir, write_new
from . import transport
from .. import ssh_inventory


def reference(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': digest(path)}


def runtime_files(repo):
    repo = Path(repo).resolve(strict=True)
    files = list((repo/'bench/runtime').glob('*.py'))
    files += [repo/'bench'/n for n in transport.SUPPORT_FILES]
    return {p.relative_to(repo).as_posix(): digest(p) for p in sorted(files)}


def probe(inventory, node_id, slot_id, ssh, output, local_repo, host_observer=False):
    """Inspect one trusted endpoint. Inventory may still be an unfilled draft."""
    value = load(inventory)
    if value.get('schema') != 'experiment-inventory-v3': raise ValueError('SSH fleet required')
    n = next(n for n in value['nodes'] if n['id'] == node_id)
    for key in ('repo','python','work_root'): ssh_inventory.linux_path(n[key])
    if host_observer and (n['kind'] != 'host' or slot_id):
        raise ValueError('host observer probe requires a host node without an Agent slot')
    if n['role'] == 'executor' and slot_id is None and not host_observer: raise ValueError('select an executor slot')
    slot = next(s for s in n['slots'] if s['id'] == slot_id) if slot_id else {}
    peer = {'transport': 'ssh', 'alias': n['ssh_alias'], 'repo': n['repo'], 'python': n['python'],
            'ssh': ssh, 'container': slot.get('container'), 'cgroup': slot.get('cgroup')}
    if peer['cgroup'] and not str(ssh_inventory.linux_path(peer['cgroup'])).startswith('/sys/fs/cgroup/ax-'):
        raise ValueError('dedicated ax cgroup required')
    request = {k: n[k] for k in ('repo','work_root','data_paths')}
    executor = n['role'] == 'executor' and not host_observer
    request.update(runtime_files=runtime_files(local_repo), executor=executor,
                   requires_fuse=executor and n['cluster_id']!='C3', cgroup=peer['cgroup'])
    source = (Path(__file__).with_name('preflight.py')).read_text(encoding='utf-8')
    script = source + '\nimport json\nprint(json.dumps(inspect(json.loads(' + repr(json.dumps(request)) + '))))\n'
    remote = [peer['python'], '-c', "import sys; exec(compile(sys.stdin.buffer.read(), '<ax-preflight>', 'exec'))"]
    argv = transport.ssh_command(peer, remote)
    output = private_dir(output)
    write_new(output/'request.json', {'node_id': node_id, 'slot_id': slot_id, 'host_observer': host_observer, 'inventory_sha256': digest(inventory),
                                    'probe_source_sha256': digest(Path(__file__).with_name('preflight.py')), 'request': request})
    try:
        result = subprocess.run(argv, input=script, text=True, encoding='utf-8', capture_output=True, timeout=90)
        (output/'ssh.stderr.log').write_text(result.stderr, encoding='utf-8')
        (output/'response.json').write_text(result.stdout, encoding='utf-8')
        if result.returncode: raise ValueError('SSH probe failed; inspect retained stderr')
        receipt = json.loads(result.stdout)
        receipt.update(node_id=node_id, slot_id=slot_id, host_observer=host_observer, inventory_sha256=digest(inventory))
        write_new(output/'probe.json', receipt)
        if receipt['checks']['runtime_matches']:
            peer['pins'] = receipt['pins']
            write_new(output/'peer.json', peer)
        return receipt
    except Exception as error:
        write_new(output/'failed.json', {'passed': False, 'error_type': type(error).__name__, 'read_only': True})
        raise


def call(peer_ref, action, state, output, spec_ref=None, label=None):
    if action not in ('start','status','stop','observe-start','observe-status','observe-mark','observe-stop'):
        raise ValueError('one supported lifecycle action required')
    peer = load(pinned(peer_ref))
    ssh_inventory.linux_path(state)
    args = [action, '--output', state]
    if action in ('start','observe-start'):
        if not spec_ref or not spec_ref.get('sha256'): raise ValueError('remote spec and SHA required')
        ssh_inventory.linux_path(spec_ref['path'])
        args += ['--spec', spec_ref['path'], '--sha256', spec_ref['sha256']]
    if action == 'observe-mark':
        if not label: raise ValueError('stage label required')
        args += ['--label', label]
    return transport.one(peer, args, private_dir(output))


def cohort_draft(request, output):
    """Expand local peer references into one reviewable cohort, without running it."""
    value = load(request)
    inventory = load(pinned(value['inventory']))
    ssh_inventory.validate(inventory)
    workers = []
    for worker in value['workers']:
        worker = dict(worker)
        worker['peer'] = load(pinned(worker.pop('peer_ref')))
        workers.append(worker)
    ssh_inventory.validate_workers(inventory, workers)
    value.update(schema='experiment-cohort-v1', reviewed=False, provider='ssh', agents=len(workers), workers=workers)
    for observer in value.get('observers', []):
        observer['peer'] = load(pinned(observer.pop('peer_ref')))
    write_new(output, value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    t = sub.add_parser('template')
    t.add_argument('--executors', nargs='+', required=True); t.add_argument('--services', nargs='*', default=[])
    t.add_argument('--hosts', nargs='*', default=[]); t.add_argument('--cluster', default='C1', help='resource pool ID; new suites use pool-a/pool-b independently of C1-C4 workloads')
    t.add_argument('--output', required=True)
    c = sub.add_parser('check'); c.add_argument('--inventory', required=True); c.add_argument('--output', required=True)
    d = sub.add_parser('cohort'); d.add_argument('--request', required=True); d.add_argument('--output', required=True)
    q = sub.add_parser('probe')
    for key in ('inventory','node','ssh-config','known-hosts','output'): q.add_argument('--'+key, required=True)
    q.add_argument('--slot'); q.add_argument('--ssh', default=shutil.which('ssh'))
    q.add_argument('--host-observer', action='store_true', help='probe the host itself, outside its executor slots')
    q.add_argument('--local-repo', default=str(Path(__file__).resolve().parents[2]))
    a = sub.add_parser('call')
    for key in ('peer','sha256','operation','state','output'): a.add_argument('--'+key, required=True)
    a.add_argument('--spec'); a.add_argument('--spec-sha256'); a.add_argument('--label')
    args = p.parse_args()
    if args.action == 'template':
        write_new(args.output, ssh_inventory.template(args.executors,args.services,args.hosts,args.cluster))
    elif args.action == 'check':
        receipt = ssh_inventory.validate(load(args.inventory)); receipt['inventory_sha256'] = digest(args.inventory)
        write_new(args.output, receipt); print(json.dumps(receipt))
    elif args.action == 'cohort':
        cohort_draft(args.request,args.output)
    elif args.action == 'probe':
        ssh = {'executable': reference(args.ssh), 'config': reference(args.ssh_config), 'known_hosts': reference(args.known_hosts)}
        result = probe(args.inventory,args.node,args.slot,ssh,args.output,args.local_repo,args.host_observer)
        print(json.dumps({'passed': result['passed'], 'checks': result['checks'], 'output': args.output}))
        return 0 if result['passed'] else 1
    else:
        result = call({'path': args.peer, 'sha256': args.sha256},args.operation,args.state,args.output,
                      {'path':args.spec,'sha256':args.spec_sha256} if args.spec else None,args.label)
        print(json.dumps(result))
    return 0


if __name__ == '__main__': raise SystemExit(main())
