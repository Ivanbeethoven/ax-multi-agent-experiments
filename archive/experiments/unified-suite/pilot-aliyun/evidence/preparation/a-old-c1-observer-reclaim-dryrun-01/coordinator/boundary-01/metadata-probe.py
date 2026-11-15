ROOT='/srv/ax-metadata'
REPO='/srv/ax-metadata/code-19'
ROLE='metadata'
ASSET='/srv/ax-client/a/c4-fork-flat-base-03'
ATTEMPTS=['c4-a-flat-off1', 'c4-a-flat-on1', 'c4-a-flat-on2', 'c4-a-flat-off2', 'c4-a-flat-off3', 'c4-a-flat-on3', 'c4-a-flat-formal-01']
import pathlib,sys,json
root=pathlib.Path(ROOT);sys.path.insert(0,REPO)
from bench.runtime.common import alive,load,mounts
identities=[];unknown=[]
for tag in ATTEMPTS:
 observer=root/'o'/tag
 if observer.exists():
  if (observer/'ready.json').is_file():identities.append(load(observer/'ready.json')['process'])
  else:unknown.append(str(observer))
 if ROLE=='client':
  for suffix in ('','-v'):
   state=root/'m'/(tag+suffix)
   if state.exists():
    if (state/'process.json').is_file():
     p=load(state/'process.json');identities.extend([p['process'],p['supervisor']])
    else:unknown.append(str(state))
  worker=root/'j'/tag
  if worker.exists():
   if (worker/'ready.json').is_file():identities.append(load(worker/'ready.json')['process'])
   else:unknown.append(str(worker))
   for path in worker.glob('job-*/process.json'):identities.append(load(path)['process'])
live=[p for p in identities if p is None or alive(p)]
slot=pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text().strip() if ROLE=='client' else ''
mounted=mounts(str(pathlib.Path(ASSET)/'mnt')) if ROLE=='client' else []
safe=not (live or unknown or slot or mounted)
print(json.dumps(dict(safe_for_release=safe,original_processes=identities,live=live,unknown_startup_directories=unknown,slot=slot,mounts=mounted)))
raise SystemExit(0 if safe else 2)
