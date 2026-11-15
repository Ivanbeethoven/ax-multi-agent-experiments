CODE='/srv/ax-client/code-12'
ROOT='/srv/ax-client'
TAG='c3-d909e5330618-16f4ac389d2a2da6-r1-juicefs'
ROLE='client'
import json,pathlib,sys
sys.path.insert(0,CODE)
from bench.runtime.common import alive,digest,mounts
p=pathlib.Path(ROOT)/'q'/(TAG+'-closure')/'result.json';v=json.loads(p.read_text())
identities=v['processes'];owned=[m['point'] for m in v['mount_points']] if 'mount_points' in v else v['owned_mounts']
if not identities or (ROLE=='client' and not owned):raise ValueError('Actual original process and client mount identities required')
current=dict(original_identities=identities,any_original_process_alive=any(alive(i) for i in identities),original_owned_mounts=owned,current_owned_mounts=[m for point in owned for m in mounts(point)],source=dict(path=str(p),sha256=digest(p)))
if ROLE=='client':current['slot_procs']=(pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip())
current['passed']=not current['any_original_process_alive'] and not current['current_owned_mounts'] and not current.get('slot_procs')
print(json.dumps(current))
