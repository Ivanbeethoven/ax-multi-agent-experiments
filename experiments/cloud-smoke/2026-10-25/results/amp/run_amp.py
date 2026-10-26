#!/usr/bin/env python3
"""Serial, single-process microbenchmarks. Snapshots include all JuiceFS clients.

No experiment reads its data during the measured write-only interval. Validation
has a separate snapshot, so validation-triggered compaction cannot enter results.
"""
import argparse, base64, decimal, hashlib, json, os, pathlib, random, re, socket
import subprocess, time, urllib.request
BASE = pathlib.Path('/data/amp')
OUT = BASE / 'artifacts'
MIB = 1 << 20
SIZE = 4 * MIB
PATCH = 4096
PORTS = {'original':9567, 'default':9571, 'writeback':9572, 'small':9573}
CASES = {
    'A_final': ('default','random',1000,'final'),
    'B_full': ('default','full',1,'final'),
    'C_sparse_final': ('default','sparse_same',100,'final'),
    'A_each': ('default','random',1000,'each'),
    'C_sparse_each': ('default','sparse_same',100,'each'),
    'C_sparse_distinct': ('default','sparse_distinct',100,'final'),
    'D_final': ('writeback','random',1000,'final'),
    'D_each': ('writeback','random',1000,'each'),
    'E_final': ('small','random',1000,'final'),
    'E_each': ('small','random',1000,'each'),
}
def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')
def parse(raw):
    data = {}
    for line in raw.splitlines():
        if not line or line.startswith('#'):
            continue
        key, value = line.rsplit(' ', 1)
        try:
            value = decimal.Decimal(value)
            data[key] = int(value) if value.is_finite() and value == int(value) else float(value)
        except (ValueError, decimal.InvalidOperation):
            pass
    return data
def get(data, name, method=None):
    return sum(v for k,v in data.items() if (k == name or k.startswith(name+'{'))
               and (method is None or ('method="'+method+'"') in k))
def selected(data):
    r = {}
    for method in ('PUT','GET','DELETE'):
        r[method+'_bytes'] = get(data, 'juicefs_object_request_data_bytes', method)
        r[method+'_count'] = get(data, 'juicefs_object_request_durations_histogram_seconds_count', method)
    for name in ('juicefs_compact_size_histogram_bytes_sum', 'juicefs_compact_size_histogram_bytes_count',
                 'juicefs_object_request_errors', 'juicefs_fuse_written_size_bytes_sum',
                 'juicefs_fuse_written_size_bytes_count', 'juicefs_fuse_read_size_bytes_sum',
                 'juicefs_fuse_read_size_bytes_count', 'juicefs_staging_blocks',
                 'juicefs_staging_block_bytes', 'juicefs_staging_writing_blocks', 'juicefs_staging_block_errors'):
        r[name] = get(data, name)
    return r
def snapshot(path=None):
    s = {'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()), 'unix':time.time(), 'clients':{}}
    for name, port in PORTS.items():
        raw = urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=5).read().decode()
        p = parse(raw)
        s['clients'][name] = {'raw':raw, 'parsed':p, 'selected':selected(p)}
    if path:
        save(path, s)
    return s
def diff(a,b):
    return {name:{k:b['clients'][name]['selected'][k]-v for k,v in x['selected'].items()}
            for name,x in a['clients'].items()}
def quiet(directory, label, minimum=3, maximum=60):
    started = time.monotonic()
    prev = snapshot()
    history = []
    stable_since = time.monotonic()
    while time.monotonic()-started < maximum:
        time.sleep(.5)
        cur = snapshot()
        same = all(cur['clients'][c]['selected'] == prev['clients'][c]['selected'] for c in PORTS)
        history.append({'utc':cur['utc'],'selected':{c:cur['clients'][c]['selected'] for c in PORTS}})
        if not same:
            stable_since = time.monotonic()
        if time.monotonic()-stable_since >= minimum:
            save(directory/(label+'-settle.json'), {'stable':True,'seconds':time.monotonic()-started,'history':history})
            return cur
        prev = cur
    save(directory/(label+'-settle.json'), {'stable':False,'seconds':time.monotonic()-started,'history':history})
    raise RuntimeError('counters did not settle')
def redis_llen(db, inode):
    argv = ['redis-cli','-p','6380','-n',str(db),'llen',f'c{inode}_0']
    p = subprocess.run(argv,capture_output=True,text=True,timeout=5)
    return {'key':f'c{inode}_0','db':db,'llen':int(p.stdout.strip())}
def run(case):
    mount, pattern, count, sync = CASES[case]
    directory = OUT / case
    directory.mkdir(exist_ok=False)
    root = BASE / mount / ('amp-20261025-'+case)
    root.mkdir(exist_ok=False)
    path = root / 'data.bin'
    original = hashlib.shake_256(b'amp-fixture-20261025').digest(SIZE)
    expected = bytearray(original if not pattern.startswith('sparse') else b'')
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o644)
    try:
        if not pattern.startswith('sparse'):
            assert os.pwrite(fd, original, 0) == SIZE
            os.fsync(fd)
        quiet(directory,'fixture')
        before = snapshot(directory/'before.json')
        inode = os.fstat(fd).st_ino
        db = 2 if mount == 'small' else 1
        metadata_before = redis_llen(db,inode)
        rng = random.Random(20261025)
        offsets = []
        logical = 0
        started = time.monotonic()
        for i in range(count):
            if pattern == 'full':
                off = 0
                payload = hashlib.shake_256(b'amp-full-20261025').digest(SIZE)
            else:
                off = (rng.randrange(SIZE//PATCH)*PATCH if pattern == 'random' else
                       3*MIB + (i*PATCH if pattern == 'sparse_distinct' else 0))
                payload = hashlib.shake_256(('amp-patch-'+str(i)).encode()).digest(PATCH)
            if len(expected) < off+len(payload):
                expected.extend(b'\0'*(off+len(payload)-len(expected)))
            expected[off:off+len(payload)] = payload
            assert os.pwrite(fd,payload,off) == len(payload)
            offsets.append(off)
            logical += len(payload)
            if sync == 'each':
                os.fsync(fd)
        os.fsync(fd)
        os.close(fd)
        fd = None
        wall = time.monotonic()-started
        immediate = snapshot(directory/'immediate.json')
        quiet(directory,'after')
        after = snapshot(directory/'after.json')
        metadata_after = redis_llen(db,inode)
        delta = diff(before,after)
        primary = delta[mount]
        result = {'case':case, 'mount':mount,'path':str(path),'inode':inode,
                  'pattern':pattern,'fsync':sync,'writes':count,'logical_write_bytes':logical,
                  'unique_patched_slots':len(set(offsets)),'offsets':offsets,
                  'fsync_and_close_seconds':wall,'deltas':delta,
                  'immediate_deltas':diff(before,immediate),
                  'PUT_amplification':primary['PUT_bytes']/logical,
                  'GET_per_logical_write':primary['GET_bytes']/logical,
                  'metadata_before':metadata_before,'metadata_after':metadata_after}
        save(directory/'result.json',result)
        actual = path.read_bytes()
        assert actual == expected, 'readback mismatch'
        result['validation'] = {'ok':True,'size':len(actual),'sha256':hashlib.sha256(actual).hexdigest()}
        quiet(directory,'validation')
        snapshot(directory/'after-validation.json')
        save(directory/'result.json',result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('offsets','deltas','immediate_deltas')}
                         | {'primary':primary}),flush=True)
        return result
    finally:
        if fd is not None:
            os.close(fd)
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('cases',nargs='*',default=list(CASES))
    args=parser.parse_args()
    for case in args.cases:
        try:
            run(case)
        except Exception as exc:
            save(OUT/(case+'-error.json'),{'error':repr(exc)})
            raise
if __name__=='__main__':
    main()
