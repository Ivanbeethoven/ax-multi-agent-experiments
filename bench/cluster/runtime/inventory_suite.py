"""Record host configuration, executable versions and input file locations."""
import json
from pathlib import Path

import cluster_lib as cl
import rustfs_layout_tools as layout


def capture():
    source = '''
import json,os,subprocess
from pathlib import Path
def command(arguments):
 p=subprocess.run(arguments,capture_output=True,text=True)
 return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
root=Path('/srv/ax-cluster/direct/bin')
result={'hostname':os.uname().nodename,'kernel':os.uname().release,
 'cpu':command(['lscpu','-J']),'memory':Path('/proc/meminfo').read_text(),
 'disks':command(['lsblk','-J','-b','-o','NAME,SIZE,TYPE,MOUNTPOINTS']),
 'addresses':command(['ip','-j','address']),'routes':command(['ip','-j','route']),
 'binaries':{}}
for name in ['brewfs','juicefs','juicefs-overlay','full-fuse-replay','c1-io-helper','c3-loader','ax-brewfs-admin']:
 p=root/name
 if p.exists():
  result['binaries'][name]={'path':str(p.resolve()),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
  if name in ['brewfs','juicefs','juicefs-overlay']:
   result['binaries'][name]['version']=command([str(p),'--version'])
print(json.dumps(result))
'''
    records = {}
    for host in cl.ALL_HOST_NAMES:
        result = cl.run_on_host(host, ['python3', '-'], input_bytes=source.encode())
        records[host] = json.loads(result.stdout)
    output = {'recorded_at': cl.utc_iso(), 'hosts': records, 'rustfs': layout.discover_rustfs_nodes()}
    (cl.RUN_ROOT / 'inventory.json').write_text(json.dumps(output, indent=2) + '\n')
    inputs = {}
    for case in sorted((cl.INPUT_DIR / 'traces').iterdir()):
        if (case / 'capture/events.jsonl').exists():
            inputs[case.name] = {name: str(case / name) for name in
                ('initial', 'capture/events.jsonl', 'capture/blobs', 'boundaries.json', 'final-tree.json', 'replayed')}
            inputs[case.name]['events_bytes'] = (case / 'capture/events.jsonl').stat().st_size
    (cl.RUN_ROOT / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    return output


if __name__ == '__main__':
    capture()
