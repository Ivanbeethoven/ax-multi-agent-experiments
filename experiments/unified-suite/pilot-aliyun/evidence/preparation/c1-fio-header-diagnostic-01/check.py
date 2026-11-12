from hashlib import sha256
from pathlib import Path
import json
import subprocess
import tempfile

fio = '/usr/bin/fio'
block = 4096
size = 4 * 1024**2
with tempfile.TemporaryDirectory(prefix='ax-c1-crc-header-') as root:
    root = Path(root)
    shared = ['--output-format=json','--ioengine=psync','--iodepth=1','--direct=0','--thread=1',
              '--fallocate=none','--randrepeat=1','--refill_buffers=1','--verify=crc32c','--verify_fatal=1',
              '--verify_state_save=0','--end_fsync=1','--fsync=1','--group_reporting=0','--unlink=0',
              '--invalidate=1','--name=job-0','--bs=4096',f'--size={size}',f'--io_size={size}','--randseed=9173',
              '--do_verify=0']
    src = root/'src'
    subprocess.run([fio,*shared,'--rw=write',f'--filename={src}'],check=True,capture_output=True)
    source = src.read_bytes()
    rows, payloads = [], []
    for side in ('first','second'):
        path = root/side
        path.write_bytes(source)
        raw = subprocess.run([fio,*shared,'--rw=randrw','--rwmixread=75','--allow_file_create=0',
                              f'--filename={path}'],check=True,capture_output=True)
        data = json.loads(raw.stdout)
        job = data['jobs'][0]
        assert data['fio version']=='fio-3.36' and job['error']==0
        rows.append({k:job[k]['total_ios'] for k in ('read','write')})
        payloads.append(path.read_bytes())
    assert rows[0]==rows[1]
    allowed=set(range(24,32)) | set(range(36,40))  # x86-64 fio 3.36 CRC verify header.
    differences=[i for i,(a,b) in enumerate(zip(*payloads)) if a!=b]
    assert len(payloads[0])==len(payloads[1])==size
    assert differences and all(i%block in allowed for i in differences)
    print(json.dumps(dict(fio='3.36',same_start_sha256=sha256(source).hexdigest(),
        timed_file_bytes=size,operations=rows,different_bytes=len(differences),
        differing_4k_offsets=sorted({i%block for i in differences}),all_differences_within_header_time_and_crc=True)))
