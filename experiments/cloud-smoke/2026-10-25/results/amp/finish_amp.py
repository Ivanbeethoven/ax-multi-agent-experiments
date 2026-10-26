#!/usr/bin/env python3
"""Record final state and unmount only the three experiment mountpoints."""
import hashlib, json, pathlib, subprocess, time
import run_amp as amp
def command(argv):
    p=subprocess.run(argv,capture_output=True,text=True,timeout=60)
    return {'argv':argv,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def main():
    amp.snapshot(amp.OUT/'final-before-unmount.json')
    result={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'formats':{}}
    for db in (1,2):
        r=command(['redis-cli','-p','6380','-n',str(db),'get','setting'])
        fmt=json.loads(r['stdout'])
        result['formats'][str(db)]={k:('<redacted>' if any(s in k.lower() for s in ('secret','accesskey','token')) else v)
                                   for k,v in fmt.items()}
    result['unmounts']=[]
    for name in ('default','writeback','small'):
        path=amp.BASE/name
        assert str(path.resolve()).startswith('/data/amp/')
        r=command(['juicefs','umount',str(path)])
        result['unmounts'].append(r)
        if r['returncode']:
            amp.save(amp.OUT/'finish.json',result)
            raise RuntimeError(r)
    result['original_mount']=command(['findmnt','/mnt/jfs'])
    result['brewfs_mount_presence_only']=command(['findmnt','/mnt/brewfs'])
    result['leftover_experiment_processes']=command(['pgrep','-af','/data/amp/'])
    amp.save(amp.OUT/'finish.json',result)
    # Redact setup output as well as argv; metrics and request logs are unchanged.
    log=amp.OUT/'setup-commands.jsonl'
    if log.exists():
        log.write_text(log.read_text().replace('rustfsadmin','<redacted>'))
    preflight=pathlib.Path('/data/amp-preflight.py')
    if preflight.exists():
        preflight.rename(amp.BASE/'preflight.py')
    print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':
    main()
