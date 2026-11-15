#!/usr/bin/env python3
"""Recover the exact interrupted local probe; preserve its data and logs."""
import json
from pathlib import Path
import subprocess
root=Path('/home/hxy/ax-retest-20261029/dynamic-probe-01')
name='ax-layout-765105713c45'
identity=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
assert identity['State']['Running'] is False
assert identity['Config']['Image']=='ax-retest/rustfs:a87e8f94'
envfile=root/'disabled/server.env'
secret_values=[line.split('=',1)[1] for line in envfile.read_text().splitlines()
               if line.startswith(('RUSTFS_ACCESS_KEY=','RUSTFS_SECRET_KEY='))]
log=subprocess.run(['docker','logs',name],capture_output=True,text=True)
safe=log.stdout+log.stderr
for value in secret_values:safe=safe.replace(value,'[redacted]')
(root/'disabled/server.log').write_text(safe)
report=json.loads((root/'result.json').read_text())
report.update(complete=False, interrupted_by='WSL service connection timeout; runtime never qualified',
              container_exit_code=identity['State']['ExitCode'])
subprocess.run(['docker','rm',name],check=True,stdout=subprocess.DEVNULL)
report['owned_container_removed']=True
envfile.unlink()
(root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
print(safe[-5000:])
