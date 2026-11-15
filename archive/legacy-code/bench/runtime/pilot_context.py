"""Select a separately probed runtime while retaining one original state database.

Only runtime repository paths may differ. Hosts, slots, disks and old evidence
stay fixed; no SQLite database is copied or created in the runtime directory.
"""
from pathlib import Path
from .common import digest, load, pinned


def ref(path):
    return dict(path=str(path),sha256=digest(path))


def validate(control, runtime):
    control=Path(control).resolve(strict=True);runtime=Path(runtime).resolve(strict=True)
    if control==runtime or (runtime/'state.sqlite').exists():
        raise ValueError('Separate runtime evidence directory without another state database required')
    old=load(control/'fleet.json');new=load(runtime/'fleet.json')
    def placement(value):
        value={**value,'nodes':[{k:v for k,v in n.items() if k!='repo'} for n in value['nodes']]}
        return value
    if placement(old)!=placement(new):
        raise ValueError('Runtime refresh cannot change hosts, disk paths, slots or resource budgets')
    allocation=load(runtime/'allocation.json')
    if not allocation['passed'] or allocation['inventory_sha256']!=digest(runtime/'fleet.json'):
        raise ValueError('New runtime needs its own successful actual fleet probes')
    proofs=[]
    for node in new['nodes']:
        name=node['id'];before=load(control/'probes'/name/'probe.json')
        after=load(runtime/'probes'/name/'probe.json')
        if not after['passed'] or after['boot_id']!=before['boot_id']:
            raise ValueError('Runtime refresh cannot silently replace or reboot a host')
        proofs += [ref(runtime/'probes'/name/'probe.json'),ref(runtime/'probes'/name/'peer.json')]
        if node['role']=='executor':
            proofs += [ref(runtime/'probes'/(name+'-slot')/'probe.json'),
                       ref(runtime/'probes'/(name+'-slot')/'peer.json')]
    return dict(control=str(control),runtime=str(runtime),original_fleet=ref(control/'fleet.json'),
        fleet=ref(runtime/'fleet.json'),allocation=ref(runtime/'allocation.json'),
        ssh=ref(runtime/'ssh.conf'),proofs=proofs,scope='Runtime paths only; one original SQLite state, same hosts/disks/slots/boot IDs')


def select(control, reference=None):
    if reference is None:return Path(control)
    binding=load(pinned(reference));runtime=Path(binding['runtime'])
    for pin in [binding['original_fleet'],binding['fleet'],binding['allocation'],binding['ssh'],*binding['proofs']]:
        pinned(pin)
    if binding!=validate(control,runtime):
        raise ValueError('Runtime binding no longer matches actual frozen files')
    return runtime


def arguments(parser):
    parser.add_argument('--runtime-control',type=Path,help='Pinned runtime binding; state stays in --control')
    parser.add_argument('--runtime-control-sha256')


def reference(args):
    if bool(args.runtime_control)!=bool(args.runtime_control_sha256):
        raise ValueError('Runtime binding path and SHA256 must be supplied together')
    return dict(path=str(args.runtime_control),sha256=args.runtime_control_sha256) if args.runtime_control else None
