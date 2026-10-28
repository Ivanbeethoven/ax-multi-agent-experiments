import json,base64,time,sys
from parallel_controller import *
s=json.loads(STATE.read_text());a=s['workers']['node-a']['instance_id'];b=s['workers']['node-b']['instance_id'];attempt='node-b-rustfs-'+str(int(time.time()));s['workers']['node-b']['build_attempt_id']=attempt;save(STATE,s)
body=base64.b64encode((ROOT/'rustfs_builder.py').read_bytes()).decode();payload=base64.b64encode(json.dumps({'build_attempt_id':attempt,'public_cargo_sources':json.loads((ROOT/'public-cargo-sources.json').read_text())}).encode()).decode()
script="""set -eu
umask 077
python3 - <<'PY'
import pathlib,json,base64,shutil,subprocess
assert subprocess.check_output(['systemctl','show','ax-rustfs-build-only','-p','SubState','--value'],text=True).strip()=='failed'
out=pathlib.Path('/data/rustfs-external-build');old=json.loads((out/'status.json').read_text()).get('build_attempt_id','legacy-attempt-before-token');d=out/'attempts'/old;d.mkdir(parents=True,exist_ok=True)
for name in ('status.json','provenance.json','build.log'):
 if (out/name).is_file():shutil.copy2(out/name,d/name)
p=pathlib.Path('/root/ax-rustfs-build.private.json');c=json.loads(p.read_text());c.update(json.loads(base64.b64decode('PAYLOAD')));p.write_text(json.dumps(c));p.chmod(0o600)
pathlib.Path('/root/ax-rustfs-builder.py').write_bytes(base64.b64decode('BODY'))
PY
systemctl reset-failed ax-rustfs-build-only
systemd-run --unit=ax-rustfs-build-only --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes --property=CPUQuota=600% --property=MemoryHigh=20G --property=MemoryMax=26G --property=MemorySwapMax=8G --property=Nice=10 --property=StandardOutput=append:/var/log/ax-parallel/rustfs-build-only.log --property=StandardError=append:/var/log/ax-parallel/rustfs-build-only.log /bin/bash /root/ax-rustfs-build.sh
""".replace('BODY',body).replace('PAYLOAD',payload)
print(invoke(b,script,'ax-rustfs-retry-preserve-attempt').strip())
c={'source_instance':a,'builder_instance':b,'build_attempt_id':attempt,'provenance_get':signed(s['bucket'],'parallel-build/rustfs-provenance.json'),'binary_get':signed(s['bucket'],'parallel-build/rustfs-binary'),'builder_status_get':signed(s['bucket'],'parallel-build/node-b-status.json')};body=base64.b64encode((ROOT/'rustfs_import_external.py').read_bytes()).decode();conf=base64.b64encode(json.dumps(c).encode()).decode();wrapper=base64.b64encode((ROOT/'rustfs_cargo_handoff.py').read_text().encode()).decode();pointer=base64.b64encode(json.dumps({'build_attempt_id':attempt,'builder_instance':b}).encode()).decode()
script='set -eu\numask 077\nprintf %s '+body+' | base64 -d > /root/ax-rustfs-import-external.py\nprintf %s '+conf+' | base64 -d > /root/ax-rustfs-import.private.json\nprintf %s '+pointer+' | base64 -d > /data/results/rustfs-external-attempt.json\nprintf %s '+wrapper+' | base64 -d > /data/build/gitwrap/cargo.new\nchmod 755 /data/build/gitwrap/cargo.new\nmv -f /data/build/gitwrap/cargo.new /data/build/gitwrap/cargo\nsystemctl reset-failed ax-rustfs-external-import\nsystemd-run --unit=ax-rustfs-external-import --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes --property=StandardOutput=append:/var/log/ax-main/rustfs-external-import.log --property=StandardError=append:/var/log/ax-main/rustfs-external-import.log /usr/bin/python3 /root/ax-rustfs-import-external.py\n'
print(invoke(a,script,'ax-rustfs-exact-attempt-handoff').strip());s['external_rustfs_handoff'].update(script_sha256=sha(ROOT/'rustfs_cargo_handoff.py'),build_attempt_id=attempt);s['workers']['node-b'].update(phase='build_retry_running',build_script_sha256=sha(ROOT/'rustfs_builder.py'));save(STATE,s);print('attempt '+attempt+' started; failures preserved; main matrix paused')
