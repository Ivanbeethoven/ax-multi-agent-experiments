import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

root=Path('/srv/ax-pilot-control')
base=root/'preparation/a-c4-on1-publication-01'
recover=root/'preparation/a-c4-on1-historical-archive-01/recovered-trial'
sys.path.insert(0,str(root/'code-19'))
from bench.archive_check import verify

out=base/'independent'
out.mkdir(mode=0o700)
host='brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou-internal.aliyuncs.com'
prefix='pilot-results/20261111/c4-a-flat-on1-historical-01'
finished=json.loads((recover/'finished.json').read_bytes())
assert finished['passed'] and finished['purpose']=='observer-calibration-on'
sha=finished['archives']['sha256']
index=Path(finished['archives']['path']).read_bytes()
assert hashlib.sha256(index).hexdigest()==sha
original={Path(a['path']).name:a for a in json.loads(index)}
assert set(original)=={'client.tar.gz','metadata.tar.gz','objects.tar.gz'}
counts={'client':85741,'metadata':86057,'objects':18}
checks={}
for kind,count in counts.items():
    name=kind+'.tar.gz'
    original_check=json.loads((recover/'archives'/(kind+'-check.json')).read_bytes())
    pin=original[name]
    assert original_check['sha256']==pin['sha256'] and re.fullmatch('[a-f0-9]{64}',pin['sha256'])
    assert original_check['passed'] is True and original_check['file_members']==count
    url=f'https://{host}/{prefix}/{name}'
    path=out/name
    checksum=hashlib.sha256()
    n=0
    with urllib.request.urlopen(url,timeout=180) as response,path.open('xb') as disk:
        assert response.status==200 and int(response.headers['Content-Length'])==original_check['bytes']
        while chunk:=response.read(4*1024*1024):
            disk.write(chunk)
            checksum.update(chunk)
            n+=len(chunk)
        disk.flush()
        os.fsync(disk.fileno())
    assert n==original_check['bytes'] and checksum.hexdigest()==pin['sha256']
    checked=verify(path,pin['sha256'])
    assert all(checked[k]==original_check[k] for k in ('schema','passed','bytes','sha256','file_members','cell_id','kind','deferred'))
    checks[kind]={'downloaded_path':str(path),'verified':checked,'unsigned_internal_get':True}
    print(json.dumps(dict(role=kind, bytes=n, sha256=pin['sha256'], file_members=count)),flush=True)
with (base/'independent-readback.json').open('x') as f:
    json.dump(dict(schema='c4-historical-on1-oss-readback-v1',passed=True,
        at=datetime.now(timezone.utc).isoformat(),recovered_finished_sha256=hashlib.sha256((recover/'finished.json').read_bytes()).hexdigest(),
        archives_manifest_sha256=sha,objects=checks,anonymous_internal_get=True,
        qualification='historical-observer-calibration-on-only',formal_acceptance=False),f,indent=2)
    f.write('\n')
