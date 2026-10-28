#!/usr/bin/env python3
import json,base64,subprocess,time,sys,hashlib
from pathlib import Path
from parallel_controller import *
R='a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed'
s=json.loads(STATE.read_text());b=s['workers']['node-b'];source=json.loads((ORIGINAL/'state.local.json').read_text());bucket=s['bucket']
assert (ROOT/'PAUSED-FOR-CACHE-PLAN').is_file() and b['role']=='build_only' and len([w for w in s['workers'].values() if not w['source_node']])==1
# Establish the independent SSH backup before any build is started.
hostkey=invoke(b['instance_id'],'cat /etc/ssh/ssh_host_ed25519_key.pub','ax-build-ssh-hostkey').strip();assert hostkey.startswith('ssh-ed25519 ')
known=ROOT/'known_hosts.local'
line=b['public_ip']+' '+hostkey+'\n'
if not known.exists() or line not in known.read_text():
 with known.open('a') as f:f.write(line)
p=subprocess.run(['ssh','-i',s['ssh_key_path'],'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'root@'+b['public_ip'],'true'],capture_output=True,text=True)
b['backup_ssh_verified']=p.returncode==0;save(STATE,s)
if p.returncode:print('SSH backup unverified; CloudAssistant+OSS remain active; no broad ingress added')
# The wrapper goes first and atomically: active BrewFS cargo is already executing the real binary.
wrapper=base64.b64encode((ROOT/'rustfs_cargo_handoff.py').read_text().encode()).decode()
script="""set -eu
python3 - <<'PY'
import pathlib,base64,subprocess,hashlib
p=pathlib.Path('/data/build/gitwrap/cargo');new=p.with_suffix('.new')
assert pathlib.Path('/data/results/rebuild-current-stage.txt').read_text().strip()=='brewfs'
body=base64.b64decode('WRAPPER');new.write_bytes(body);new.chmod(0o755)
if p.exists():assert p.read_bytes()==body,'different cargo wrapper already installed'
new.replace(p)
assert hashlib.sha256(p.read_bytes()).hexdigest()=='WRAPPERSHA'
print('strict external RustFS handoff installed; BrewFS unchanged')
PY
""".replace('WRAPPERSHA',hashlib.sha256((ROOT/'rustfs_cargo_handoff.py').read_text().encode()).hexdigest()).replace('WRAPPER',wrapper)
print(invoke(s['workers']['node-a']['instance_id'],script,'ax-rustfs-handoff-install').strip());s['external_rustfs_handoff']={'script_sha256':sha(ROOT/'rustfs_cargo_handoff.py'),'builder_instance':b['instance_id'],'source':R,'installed':True};save(STATE,s)
config={'source_instance':s['workers']['node-a']['instance_id'],'cache_put':signed(bucket,'parallel-build/rustfs-public-cache.tar.gz','PUT','application/gzip'),'manifest_put':signed(bucket,'parallel-build/cache-manifest.json','PUT','application/json')}
export=base64.b64encode((ROOT/'rustfs_cache_export.py').read_bytes()).decode();config64=base64.b64encode(json.dumps(config).encode()).decode()
script='set -eu\numask 077\nprintf %s '+export+' | base64 -d > /root/ax-rustfs-cache-export.py\nprintf %s '+config64+' | base64 -d > /root/ax-rustfs-export.private.json\nif ! systemctl show ax-rustfs-cache-export -p ActiveState --value 2>/dev/null | grep -qx active; then systemctl reset-failed ax-rustfs-cache-export 2>/dev/null || true; systemd-run --unit=ax-rustfs-cache-export --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes --property=Nice=15 --property=IOSchedulingClass=idle --property=StandardOutput=append:/var/log/ax-main/rustfs-cache-export.log --property=StandardError=append:/var/log/ax-main/rustfs-cache-export.log /usr/bin/python3 /root/ax-rustfs-cache-export.py; fi\n'
print(invoke(s['workers']['node-a']['instance_id'],script,'ax-rustfs-cache-export-start').strip());s['rustfs_cache_export_started']=True;save(STATE,s)
if '--source-only' in sys.argv:
 print('source-only handoff/export started; node-b initialization delegated');sys.exit(0)
config={'instance':b['instance_id'],'data_disk_id':b['data_disk_id'],'cache_get':signed(bucket,'parallel-build/rustfs-public-cache.tar.gz'),'manifest_get':signed(bucket,'parallel-build/cache-manifest.json'),'status_put':signed(bucket,'parallel-build/node-b-status.json','PUT','application/json'),'binary_put':signed(bucket,'parallel-build/rustfs-binary','PUT','application/octet-stream'),'provenance_put':signed(bucket,'parallel-build/rustfs-provenance.json','PUT','application/json'),'downloads':{name:{'url':signed(bucket,name),'sha256':source['uploaded_files'][name]['sha256']} for name in ('rust-toolchain-1.99.tar.gz','rustfs.bundle')}}
body=base64.b64encode((ROOT/'rustfs_builder.py').read_bytes()).decode();conf=base64.b64encode(json.dumps(config).encode()).decode()
shell='set -eu\ncloud-init status --wait >/var/log/ax-parallel/cloud-init-wait.log 2>&1 || true\ncommand -v mkfs.xfs >/dev/null\nexec /usr/bin/python3 /root/ax-rustfs-builder.py\n'
shell64=base64.b64encode(shell.encode()).decode()
script='set -eu\numask 077\nprintf %s '+body+' | base64 -d > /root/ax-rustfs-builder.py\nprintf %s '+conf+' | base64 -d > /root/ax-rustfs-build.private.json\nprintf %s '+shell64+' | base64 -d > /root/ax-rustfs-build.sh\nif ! systemctl show ax-rustfs-build-only -p ActiveState --value 2>/dev/null | grep -qx active; then systemd-run --unit=ax-rustfs-build-only --property=RuntimeMaxSec=infinity --property=TimeoutStartSec=infinity --property=RemainAfterExit=yes --property=CPUQuota=600% --property=MemoryHigh=20G --property=MemoryMax=26G --property=MemorySwapMax=8G --property=Nice=10 --property=StandardOutput=append:/var/log/ax-parallel/rustfs-build-only.log --property=StandardError=append:/var/log/ax-parallel/rustfs-build-only.log /bin/bash /root/ax-rustfs-build.sh; fi\n'
print(invoke(b['instance_id'],script,'ax-rustfs-build-only-start').strip());b.update(phase='build_bootstrap_running',build_service='ax-rustfs-build-only',build_script_sha256=sha(ROOT/'rustfs_builder.py'),build_started_epoch=time.time());save(STATE,s);print(json.dumps({'node_b':b['instance_id'],'bootstrap_started':True,'matrix_paused':True,'node_c_created':False}))
