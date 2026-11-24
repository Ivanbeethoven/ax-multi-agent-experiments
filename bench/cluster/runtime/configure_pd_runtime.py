"""Apply the run's PD CPU and Go runtime settings while measurements are stopped."""
import json
import subprocess

import cluster_lib as cl
from run_suite import save


def main():
    active = subprocess.run(['systemctl', 'is-active', 'ax-' + cl.RUN_ID + '.service'], capture_output=True, text=True)
    if active.stdout.strip() == 'active':
        raise RuntimeError('Stop measurements before configuring PD')
    unit = 'ax-' + cl.RUN_ID + '-pd.service'
    source = '''
from pathlib import Path
import json,subprocess,time,urllib.request
unit=UNIT
def state():
 return subprocess.run(['systemctl','show',unit,'-p','MainPID','-p','CPUQuotaPerSecUSec','-p','MemoryMax','-p','ActiveState'],capture_output=True,text=True,check=True).stdout
before=state()
folder=Path('/etc/systemd/system')/(unit+'.d');folder.mkdir(exist_ok=True)
(folder/'runtime.conf').write_text('[Service]\\nCPUQuota=800%\\nEnvironment=GOMAXPROCS=8\\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart',unit],check=True)
for attempt in range(60):
 try:
  with urllib.request.urlopen('http://175.168.3.55:2379/health',timeout=2) as response:
   healthy=json.loads(response.read())
  rows=healthy if isinstance(healthy,list) else [healthy]
  if rows and all(isinstance(row,dict) and str(row.get('health')).lower()=='true' for row in rows):break
 except (OSError,ValueError):pass
 time.sleep(1)
else:raise RuntimeError('PD did not become healthy')
pid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()
environment=(Path('/proc')/pid/'environ').read_bytes().split(b'\\0')
if b'GOMAXPROCS=8' not in environment:raise RuntimeError('PD Go runtime setting is absent')
print(json.dumps({'unit':unit,'before':before,'after':state(),'GOMAXPROCS':8,'healthy':True}))
'''.replace('unit=UNIT', 'unit=' + repr(unit))
    result = cl.run_on_host('node-meta', ['python3', '-'], input_bytes=source.encode(), timeout=120)
    record = json.loads(result.stdout)
    record['configured_at'] = cl.utc_iso()
    save(cl.RUN_ROOT / 'versions/pd-runtime.json', record)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
