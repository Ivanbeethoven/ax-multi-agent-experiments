import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
BINARY = Path(os.environ['AX_TRACE_BINARY']).resolve()

def run(argv, **kw):
    return subprocess.run([str(x) for x in argv], check=True, capture_output=True, text=True, **kw).stdout

def tree(root):
    entries = {}
    for p in sorted(root.rglob('*')):
        s = p.lstat()
        key = p.relative_to(root).as_posix()
        if p.is_symlink(): value = {'type':'link','target':os.readlink(p)}
        elif p.is_dir(): value = {'type':'directory'}
        elif p.is_file(): value = {'type':'file','bytes':s.st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        else: raise RuntimeError('Unsupported final inode: '+key)
        value['mode'] = s.st_mode & 0o7777
        entries[key] = value
    return entries

def save(path, value):
    path.write_text(json.dumps(value,indent=2),encoding='utf-8')

class Mount:
    def __init__(self, folder):
        self.folder = folder
        self.root = folder/'backing'
        self.mount = folder/'mount'
        self.root.mkdir(exist_ok=True); self.mount.mkdir()
        helper=Path(os.environ['AX_TRACE_FUSE_HELPERS']); helper.mkdir(parents=True,exist_ok=True)
        if not (helper/'fusermount').exists(): (helper/'fusermount').symlink_to('/usr/bin/fusermount3')
        self.proc = subprocess.Popen([sys.executable,str(SCRIPTS/'full_fuse_record.py'),'--root',str(self.root),
            '--mount',str(self.mount),'--output',str(folder/'capture')],env={**os.environ,'PATH':str(helper)+':'+os.environ['PATH']},
            stdout=(folder/'daemon.stdout').open('wb'),stderr=(folder/'daemon.stderr').open('wb'))
        for _ in range(200):
            if os.path.ismount(self.mount): return
            if self.proc.poll() is not None: raise RuntimeError('Recorder failed: '+(folder/'daemon.stderr').read_text())
            time.sleep(.05)
        raise RuntimeError('Recorder mount timeout')
    def stop(self):
        run(['fusermount3','-u',self.mount])
        self.proc.wait(timeout=60)
        if self.proc.returncode: raise RuntimeError('Recorder exited with error')

def replay_check(folder):
    target=folder/'replayed'
    shutil.copytree(folder/'initial',target,symlinks=True)
    run([BINARY,folder/'capture/events.jsonl',folder/'capture/blobs',target,folder/'replay.json'])
    expected, actual=tree(folder/'backing'),tree(target)
    save(folder/'final-tree.json',expected)
    if expected!=actual:
        save(folder/'tree-differences.json',{k:{'captured':expected.get(k),'replayed':actual.get(k)} for k in expected.keys()|actual.keys() if expected.get(k)!=actual.get(k)})
        raise RuntimeError('Final tree mismatch')
    result=json.loads((folder/'replay.json').read_text())
    result['final_tree_equal']=True
    result['final_tree_sha256']=hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()
    save(folder/'verification.json',result)
    return result

def preflight(folder):
    folder.mkdir(parents=True,exist_ok=False)
    (folder/'backing').mkdir(); shutil.copytree(folder/'backing',folder/'initial')
    m=Mount(folder)
    code='''import os, sqlite3, mmap, subprocess, sys
from pathlib import Path
p=Path(sys.argv[1]); (p/'state').mkdir()
c=sqlite3.connect(p/'state/session.db'); c.execute('pragma journal_mode=wal'); c.execute('create table t(x)'); c.execute('insert into t values (?)',('agent-state',)); c.commit(); assert c.execute('select x from t').fetchone()[0]=='agent-state'; c.close()
f=open(p/'mapped','w+b'); f.write(b'x'*8192); f.flush(); mm=mmap.mmap(f.fileno(),8192); mm[5:9]=b'edit'; mm.flush(); mm.close(); f.close()
subprocess.run([sys.executable,'-c',"from pathlib import Path; import sys; p=Path(sys.argv[1]); assert p.read_bytes()[5:9]==b'edit'; p.rename(p.with_name('renamed'))",str(p/'mapped')],check=True)
os.symlink('renamed',p/'link'); assert (p/'link').read_bytes()[5:9]==b'edit'
os.link(p/'renamed',p/'hardlink'); os.chmod(p/'renamed',0o640)
assert len(list(p.iterdir()))==4
'''
    try: run([sys.executable,'-c',code,m.mount])
    finally: m.stop()
    result=replay_check(folder)
    save(folder/'gate.json',{'passed':True,'unix':time.time(),'checks':['child process','SQLite WAL','mmap read/write','directory listing','rename','symlink','hardlink','chmod','Rust replay','final tree']})
    return result

if __name__=='__main__': print(json.dumps(preflight(Path(sys.argv[1]).resolve())))
