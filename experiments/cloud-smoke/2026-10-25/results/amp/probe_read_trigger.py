#!/usr/bin/env python3
"""Separate small-write upload from read-triggered slice compaction.

Every read-enabled cycle: four disjoint 4 KiB pwrite+fsync, snapshot, one
4 KiB pread, wait for metadata slice count to become one. F_write_400 omits
the read and that wait. No explicit compact command is issued.
"""
import argparse, hashlib, json, os, pathlib, time
import run_amp as amp
SPECS = {
    'F_read_trigger': ('default',4*amp.MIB,1),
    'F_read_100': ('default',4*amp.MIB,100),
    'D_read_100': ('writeback',4*amp.MIB,100),
    'E_read_100': ('small',4*amp.MIB,100),
    'F_span_64K': ('default',65536,1),
    'F_write_400': ('default',4*amp.MIB,100),
}
def run(name):
    client,size,cycles = SPECS[name]
    do_read = name != 'F_write_400'
    out = amp.OUT / name
    out.mkdir(exist_ok=False)
    root = amp.BASE/client/('amp-20261025-'+name)
    root.mkdir(exist_ok=False)
    path = root/'data.bin'
    original = hashlib.shake_256(b'amp-fixture-20261025').digest(size)
    expected = bytearray(original)
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o644)
    db=2 if client=='small' else 1
    inode=os.fstat(fd).st_ino
    rows=[]
    try:
        assert os.pwrite(fd,original,0)==size
        os.fsync(fd)
        amp.quiet(out,'fixture')
        before=amp.snapshot(out/'before.json')
        for cycle in range(cycles):
            s0=amp.snapshot(out/'cycle-first-before.json' if cycle==0 else None)
            for i in range(4):
                off=4096 if i==0 else i*(size//4)
                payload=hashlib.shake_256(f'probe-{cycle}-{i}'.encode()).digest(4096)
                assert os.pwrite(fd,payload,off)==4096
                os.fsync(fd)
                expected[off:off+4096]=payload
            sw=amp.snapshot(out/'cycle-first-after-writes.json' if cycle==0 else None)
            mw=amp.redis_llen(db,inode)
            if do_read:
                assert os.pread(fd,4096,0)==original[:4096]
                started=time.monotonic()
                while True:
                    meta=amp.redis_llen(db,inode)
                    if meta['llen']==1:
                        break
                    if time.monotonic()-started>30:
                        raise RuntimeError('read did not compact metadata within 30 seconds')
                    time.sleep(.02)
            else:
                meta=amp.redis_llen(db,inode)
            sr=amp.snapshot(out/'cycle-first-after-read.json' if cycle==0 else None)
            rows.append({'cycle':cycle,'after_writes_metadata':mw,'after_read_metadata':meta,
                         'write_phase':amp.diff(s0,sw),'read_phase':amp.diff(sw,sr)})
            if cycle%25==0:
                print(json.dumps({'case':name,'cycle':cycle,'write_phase':rows[-1]['write_phase'][client],
                                  'read_phase':rows[-1]['read_phase'][client]}),flush=True)
        os.fsync(fd)
        os.close(fd)
        fd=None
        amp.snapshot(out/'immediate.json')
        amp.quiet(out,'after')
        after=amp.snapshot(out/'after.json')
        delta=amp.diff(before,after)
        logical=cycles*4*4096
        result={'case':name,'mount':client,'path':str(path),'inode':inode,'file_size':size,
                'cycles':cycles,'writes':cycles*4,'logical_write_bytes':logical,'read_enabled':do_read,
                'logical_read_bytes':cycles*4096 if do_read else 0,'deltas':delta,
                'PUT_amplification':delta[client]['PUT_bytes']/logical,
                'GET_per_logical_write':delta[client]['GET_bytes']/logical,
                'cycle_phases':rows}
        actual=path.read_bytes()
        assert actual==expected
        result['validation']={'ok':True,'size':len(actual),'sha256':hashlib.sha256(actual).hexdigest()}
        amp.quiet(out,'validation')
        amp.snapshot(out/'after-validation.json')
        amp.save(out/'result.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('cycle_phases','deltas')}
                         | {'primary':delta[client]}),flush=True)
    finally:
        if fd is not None:
            os.close(fd)
if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('cases',nargs='*',default=list(SPECS))
    a=p.parse_args()
    for name in a.cases:
        try:
            run(name)
        except Exception as exc:
            amp.save(amp.OUT/(name+'-error.json'),{'error':repr(exc)})
            raise
