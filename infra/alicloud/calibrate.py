#!/usr/bin/env python3
"""Calibrate dedicated mounted data disks, singly and concurrently; never format."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import uuid


def call(cmd, output):
    with output.open('x') as f:
        subprocess.run(cmd, stdout=f, check=True)
    data=json.loads(output.read_text())
    if any(job.get('error',0) for job in data['jobs']): raise RuntimeError('fio job error')
    return data


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--disk',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--size-mib',type=int,default=4096)
    p.add_argument('--runtime-seconds',type=int,default=20)
    a=p.parse_args()
    if a.size_mib < 16 or a.runtime_seconds < 1: p.error('size >=16 MiB, duration >=1 second')
    a.output.mkdir(parents=True,exist_ok=False)
    devices=set(); evidence=[]; owned=[]
    for disk in a.disk:
        data=json.loads(subprocess.check_output(['findmnt','--json','--mountpoint',str(disk),'-o','SOURCE,FSTYPE,MAJ:MIN,OPTIONS'],text=True))['filesystems'][0]
        if data['fstype'] not in ('ext4','xfs') or data['maj:min'] in devices or 'rw' not in data['options'].split(','):
            raise ValueError('independent writable ext4/XFS data filesystems required')
        root=json.loads(subprocess.check_output(['findmnt','--json','--mountpoint','/','-o','MAJ:MIN'],text=True))['filesystems'][0]
        if root['maj:min']==data['maj:min']: raise ValueError('system filesystem is not a calibration target')
        devices.add(data['maj:min']); evidence.append(data)
    (a.output/'devices.json').write_text(json.dumps(evidence,indent=2))
    token=uuid.uuid4().hex
    try:
        for i,disk in enumerate(a.disk):
            directory=disk / ('ax-calibration-'+token); directory.mkdir(); owned.append(directory)
            target=directory/'payload'
            # Fill every byte and verify outside timed read phase; no sparse fabricated read target.
            call(['fio','--name=prefill','--filename='+str(target),'--size='+str(a.size_mib)+'M',
                  '--rw=write','--bs=1M','--direct=1','--ioengine=libaio','--iodepth=32',
                  '--verify=crc32c','--do_verify=1','--verify_fatal=1','--refill_buffers=1','--end_fsync=1',
                  '--output-format=json'],a.output/f'prefill-{i}.json')
        def run(item):
            i,mode,bs,label=item
            return call(['fio','--name='+label,'--filename='+str(owned[i]/'payload'),
                         '--size='+str(a.size_mib)+'M','--rw='+mode,'--bs='+bs,'--direct=1',
                         '--ioengine=libaio','--iodepth=32','--time_based=1','--runtime='+str(a.runtime_seconds),
                         '--output-format=json'],a.output/f'{label}-{mode}-{i}.json')
        for mode,bs in [('read','1M'),('randread','4k'),('write','1M')]:
            for i in range(len(owned)): run((i,mode,bs,'single'))
            with ThreadPoolExecutor(max_workers=len(owned)) as pool:
                list(pool.map(run,[(i,mode,bs,'aggregate') for i in range(len(owned))]))
    finally:
        # Remove only the exact file/directory created above; never recurse or delete a disk.
        for directory in owned:
            (directory/'payload').unlink(missing_ok=True); directory.rmdir()
    (a.output/'scope.json').write_text(json.dumps({'time_based_bandwidth_diagnostic':True,
        'lifecycle_cost':False,'instance_iops_limit':'must record from instance inventory',
        'invalidity_threshold':None},indent=2))


if __name__=='__main__': main()
