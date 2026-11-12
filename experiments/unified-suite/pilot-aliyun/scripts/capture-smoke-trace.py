#!/usr/bin/env python3
"""Capture a small real FUSE workload: shared files, links and SQLite state. No model."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO/'scripts'))
from full_trace_common import tree
from analyze_full_fuse_trace import analyze


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(mode=0o700)
    backing, mount = out/'backing', out/'mount'
    backing.mkdir(); mount.mkdir()
    (backing/'seed').write_bytes(bytes(range(256))*16)
    os.link(backing/'seed', backing/'seed-alias')
    assert (backing/'seed').stat().st_ino == (backing/'seed-alias').stat().st_ino
    subprocess.run(['cp','-a','--',str(backing),str(out/'initial')], check=True)
    save(out/'initial-tree.json', tree(backing))
    with (out/'recorder.log').open('w') as log:
        recorder = subprocess.Popen([sys.executable,str(REPO/'scripts/full_fuse_record.py'),
            '--root',str(backing),'--mount',str(mount),'--output',str(out/'capture')], stdout=log,stderr=log)
        try:
            for _ in range(200):
                if os.path.ismount(mount): break
                if recorder.poll() is not None: raise RuntimeError('Recorder exited; see recorder.log')
                time.sleep(.05)
            else: raise TimeoutError('FUSE recorder not ready')
            payload = '''import os, sqlite3, sys
from pathlib import Path
p=Path(sys.argv[1]); assert (p/'seed').read_bytes()==bytes(range(256))*16
(p/'state').mkdir(); c=sqlite3.connect(p/'state/opencode.db')
c.execute('pragma journal_mode=wal')
for name in ('session','message','part'):
    c.execute('create table '+name+'(id text primary key, data text)')
    c.execute('insert into '+name+' values (?,?)',(name,'smoke state'))
c.commit(); assert c.execute('select count(*) from session').fetchone()[0]==1; c.close()
with (p/'seed').open('r+b',buffering=0) as f:
    f.seek(17); f.write(b'changed'); os.fsync(f.fileno()); f.seek(17); assert f.read(7)==b'changed'
assert (p/'seed-alias').read_bytes()[17:24]==b'changed'
(p/'seed-alias').rename(p/'renamed'); os.symlink('seed',p/'link'); os.chmod(p/'seed',0o640)
assert (p/'link').read_bytes()==(p/'renamed').read_bytes()
'''
            subprocess.run([sys.executable,'-c',payload,str(mount)],check=True,timeout=30)
        except Exception as error:
            save(out/'capture-error.json',{'passed':False,'error':str(error)})
            raise
        finally:
            if os.path.ismount(mount):
                subprocess.run(['fusermount3','-u',str(mount)],check=True,timeout=10)
            try: recorder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                recorder.terminate(); recorder.wait(timeout=5)
        if recorder.returncode: raise RuntimeError('Recorder did not close cleanly')
    spec=importlib.util.spec_from_file_location('qualification',Path(__file__).with_name('qualify-trace.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    metrics=analyze(out/'capture/events.jsonl')
    if metrics['operations_by_type'].get('init')!=1 or metrics['operations_by_type'].get('destroy')!=1:
        raise ValueError('Incomplete capture')
    save(out/'final-tree.json',tree(backing))
    save(out/'hardlink-check.json',{'groups':module.hardlinks(backing)})
    save(out/'metrics.json',metrics)
    save(out/'source.json',{'kind':'recorded-no-model-smoke','agent_task':False,
        'scope':'Real FUSE callbacks from files, hardlinks, symlinks and a small SQLite database; no OpenCode session'})
    print(json.dumps({'case':str(out),'callbacks':metrics['callbacks']}))


if __name__=='__main__': main()
