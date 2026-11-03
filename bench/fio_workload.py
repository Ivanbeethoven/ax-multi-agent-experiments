"""One fixed-work fio phase on regular files. Keeps raw JSON and real exit status."""
import argparse
import json
from pathlib import Path
import subprocess
import time

try:
    from .experiment_io import digest, load, write_new, linux_path
except ImportError:
    from experiment_io import digest, load, write_new, linux_path


def arguments(request, phase, output):
    p = request['params']; root = linux_path(request['root'])
    if phase not in ('prepare','measure','verify') or p['rw'] not in ('read','write','randread','randwrite','randrw'):
        raise ValueError('known fio phase and read/write mode required')
    for key in ('file_bytes_per_job','io_bytes_per_job','block_bytes','numjobs'):
        if type(p[key]) is not int or p[key] < 1: raise ValueError('positive fixed work required: '+key)
    if p['io_bytes_per_job'] % p['block_bytes'] or p['file_bytes_per_job'] % p['block_bytes']:
        raise ValueError('whole blocks required')
    if p['ioengine'] != 'psync' or p['iodepth'] != 1 or p['time_based'] is not False:
        raise ValueError('this main protocol uses fixed-work psync depth 1; separate async/time-based studies')
    if p['direct'] is not False or p['fsync_every_write'] is not True:
        raise ValueError('buffered application calls with explicit fsync; actual FUSE/page-cache mode qualified separately')
    if p.get('rwmixread') != 75: raise ValueError('freeze the main mixed-work ratio at 75/25')
    args = [request['binary']['path'],'--output-format=json+','--output='+str(output),
            '--ioengine=psync','--iodepth=1','--direct=0','--thread=1','--fallocate=none',
            '--randrepeat=1','--refill_buffers=1','--verify=crc32c',
            '--verify_fatal=1','--verify_state_save=0','--end_fsync=1','--fsync=1','--group_reporting=0',
            '--unlink=0','--allow_file_create='+('1' if phase=='prepare' else '0')]
    for index in range(p['numjobs']):
        mode = 'write' if phase=='prepare' else 'read' if phase=='verify' else p['rw']
        args += ['--name=job-'+str(index),'--filename='+str(root/f'fio-{index:04}.dat'),
                 '--rw='+mode,'--bs='+str(p['block_bytes']),
                 '--size='+str(p['file_bytes_per_job']),
                 '--io_size='+str(p['io_bytes_per_job'] if phase=='measure' else p['file_bytes_per_job']),
                 '--randseed='+str(request['seed']+index),'--rwmixread=75',
                 '--do_verify='+('1' if phase=='verify' else '0')]
        if phase=='verify': args += ['--verify_only=1']
    return args


def summarize(raw, expected_jobs, expected_bytes, exit_code, wall):
    jobs = raw.get('jobs', [])
    if len(jobs)!=expected_jobs or any(type(j.get('error')) is not int or j['error']!=0 for j in jobs):
        raise ValueError('fio job/error coverage mismatch')
    byte_count = sum(int(j.get(k,{}).get('io_bytes',0)) for j in jobs for k in ('read','write'))
    if byte_count != expected_bytes: raise ValueError('fio completed a different amount of work')
    return {'completed':True,'passed':exit_code==0,'jobs':len(jobs),'application_bytes':byte_count,
            'wrapper_wall_seconds':wall, 'native_jobs':jobs,
            'scope':'fio application work and native JSON; remote drain, cold readback and real mount exit are separate'}


def run(request, phase, output):
    root = Path(request['root']); out = Path(output)
    if not root.is_dir() or root.is_symlink() or root.resolve()!=root or not out.is_absolute() or out.resolve().is_relative_to(root):
        raise ValueError('existing regular dataset directory and external absolute output required')
    binary = Path(request['binary']['path'])
    if not binary.is_absolute() or digest(binary)!=request['binary']['sha256']:
        raise ValueError('pin the actual fio binary, not the unrelated Python fio package')
    for i in range(request['params']['numjobs']):
        target=root/f'fio-{i:04}.dat'
        if target.is_symlink() or (phase=='prepare' and target.exists()) or (phase!='prepare' and not target.is_file()):
            raise ValueError('fresh preparation / existing regular input files required')
    args = arguments(request,phase,out/'fio.json')
    out.mkdir(parents=True,exist_ok=False)
    write_new(out/'started.json',{'phase':phase,'argv':args,'automatic_retry':False})
    start=time.monotonic()
    with (out/'stderr.log').open('xb') as err, (out/'stdout.log').open('xb') as log:
        child=subprocess.Popen(args,cwd=out,stdout=log,stderr=err)
        write_new(out/'process.json',{'pid':child.pid})
        code=child.wait()  # no stage kill deadline
    wall=time.monotonic()-start
    write_new(out/'exit.json',{'exit_code':code,'wall_seconds':wall})
    expected=request['params']['numjobs']*request['params']['io_bytes_per_job' if phase=='measure' else 'file_bytes_per_job']
    result=summarize(load(out/'fio.json'),request['params']['numjobs'],expected,code,wall)
    result.update(phase=phase,raw_sha256=digest(out/'fio.json'))
    write_new(out/'result.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',required=True);p.add_argument('--phase',choices=['prepare','measure','verify'],required=True)
    p.add_argument('--output',required=True);a=p.parse_args()
    raise SystemExit(0 if run(load(a.request),a.phase,a.output)['passed'] else 1)
