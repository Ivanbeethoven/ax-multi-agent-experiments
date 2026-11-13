from pathlib import Path
import collections
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time

R = Path('/home/dev/ax-perf-issues-validation')
PRIVATE = R / 'private-profiles/profiles605606-raw-private-01'
OUT = R / 'private-profiles/offline-decode-605606-01'
PINS = {'baseline':'0b0a2743aa968f497782d823cb068cd421eb840f82d5739a363d3bc3623c8679',
        'candidate':'905c4e4ca35162ae6a1ab2de03f4fa18194ef64d9afcbe20cff9bbefd570ce90'}
def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):
    p.write_text(json.dumps(x,indent=2)+'\n')

with Path('/home/dev/.ax-rbm-heavy.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads((PRIVATE/'manifest.json').read_text())
    for e in manifest['files']:
        p=PRIVATE/e['path'];assert p.stat().st_size==e['bytes'] and digest(p)==e['sha256'],p
    OUT.mkdir(mode=0o700,exist_ok=False)
    env={**os.environ,'DEBUGINFOD_URLS':''}
    perf=Path('/usr/bin/perf')
    tool={'path':str(perf),'sha256':digest(perf),'version':subprocess.check_output([str(perf),'--version'],text=True)}
    save(OUT/'tool.json',tool)
    for arm,trial in [('baseline','605'),('candidate','606')]:
        original=next(PRIVATE.rglob('profile'+trial))
        binary=R/'corrected-release-artifacts-01/artifacts'/arm/'brewfs'
        assert digest(binary)==PINS[arm]
        dest=OUT/arm;dest.mkdir(mode=0o700)
        sym=dest/'symfs';sym.mkdir(mode=0o700)
        libs=json.loads((original/'symbol-files.json').read_text())
        for lib in libs:
            sub=lib['mapped_path'].lstrip('/')
            src=binary if sub=='usr/local/bin/brewfs' else original/'symfs'/sub
            assert digest(src)==lib['sha256'] and src.stat().st_size==lib['bytes'],sub
            link=sym/sub;link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(src)
        raw=original/'perf.data'
        identity=json.loads((original/'raw-identity.json').read_text())
        assert digest(raw)==identity['sha256'] and raw.stat().st_size==identity['bytes']
        cmd=[str(perf),'script','-i',str(raw),'--symfs',str(sym),'--no-inline','--ns',
             '-F','comm,pid,tid,cpu,time,event,ip,sym,dso,period']
        start=time.monotonic()
        with (dest/'script.mangled.txt').open('w') as stdout,(dest/'script.stderr').open('w') as stderr:
            result=subprocess.run(cmd,stdout=stdout,stderr=stderr,env=env,timeout=90)
        save(dest/'decode-receipt.json',{'command':cmd,'status':result.returncode,'elapsed_s':time.monotonic()-start,
             'raw_sha256':digest(raw),'binary_path':str(binary),'binary_sha256':PINS[arm],
             'script_sha256':digest(dest/'script.mangled.txt'),'scope':'Offline original samples; no inline expansion; no performance measurement'})
        assert result.returncode==0
        for name,args in [('event-list.txt',['evlist','-v']),('header.txt',['report','--header-only','--stdio'])]:
            with (dest/name).open('w') as out,(dest/(name+'.stderr')).open('w') as err:
                result=subprocess.run([str(perf),*args,'-i',str(raw)],stdout=out,stderr=err,env=env,timeout=30)
            assert result.returncode==0
        counts=collections.Counter();lost=[]
        with (dest/'raw-decode.stderr').open('w') as err:
            p=subprocess.Popen([str(perf),'script','-D','-i',str(raw)],stdout=subprocess.PIPE,stderr=err,text=True,env=env)
            for line in p.stdout:
                counts.update(re.findall(r'PERF_RECORD_[A-Z_]+',line))
                if 'PERF_RECORD_LOST' in line or 'PERF_RECORD_THROTTLE' in line:lost.append(line.rstrip())
            assert p.wait(timeout=30)==0
        save(dest/'event-quality.json',{'record_counts':counts,'lost_or_throttled_lines':lost,
              'scope':'Record metadata only; raw stack memory not emitted. Zero loss does not prove full callchains.'})
        public=R/'independent-review-7b844'/('profile'+trial+'-noinline-demangled-01')
        subprocess.run(['python3','-B',str(R/'independent-review-7b844/demangle-perf-rust.py'),str(dest/'script.mangled.txt'),str(public)],check=True,timeout=90)
        print(arm,'decoded',dest,'elapsed',round(time.monotonic()-start,2),flush=True)
