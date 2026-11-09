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


def validate_params(p):
    if p.get('rw') not in ('read','write','randread','randwrite','randrw'):
        raise ValueError('known fio read/write mode required')
    for key in ('file_bytes_per_job','io_bytes_per_job','block_bytes','numjobs'):
        if type(p[key]) is not int or p[key] < 1: raise ValueError('positive fixed work required: '+key)
    if p['io_bytes_per_job'] % p['block_bytes'] or p['file_bytes_per_job'] % p['block_bytes']:
        raise ValueError('whole blocks required')
    if p['io_bytes_per_job']>p['file_bytes_per_job'] or p['block_bytes']<512:
        raise ValueError('one pass within the working set and CRC-sized blocks required')
    if p['ioengine'] != 'psync' or type(p['iodepth']) is not int or p['iodepth'] != 1 or p['time_based'] is not False:
        raise ValueError('this main protocol uses fixed-work psync depth 1; separate async/time-based studies')
    if p['direct'] is not False or p['fsync_every_write'] is not True:
        raise ValueError('buffered application calls with explicit fsync; actual FUSE/page-cache mode qualified separately')
    if type(p.get('rwmixread')) is not int or p['rwmixread'] != 75: raise ValueError('freeze the main mixed-work ratio at 75/25')
    if p.get('cache_state')!='client-cold':raise ValueError('fio uses declared client-cold cells; hot workload is C3')


def arguments(request, phase, output):
    p = request['params']; root = linux_path(request['root'])
    validate_params(p)
    if phase not in ('prepare','measure','verify'):raise ValueError('known fio phase required')
    if type(request.get('seed')) is not int or not 0<=request['seed']<=2**64-p['numjobs']:
        raise ValueError('uint64 seed including per-job offsets required')
    args = [request['binary']['path'],'--output-format=json+','--output='+str(output),
            '--ioengine=psync','--iodepth=1','--direct=0','--thread=1','--fallocate=none',
            '--randrepeat=1','--refill_buffers=1','--verify=crc32c',
            '--verify_fatal=1','--verify_state_save=0','--end_fsync=1',
            # Fixture preparation is not a performance phase; avoid one fsync per
            # write while materializing a 4 KiB 32 GiB base. Measurement keeps the
            # protocol's every-write fsync.
            '--fsync='+('0' if phase=='prepare' else '1'),'--group_reporting=0',
            '--unlink=0','--invalidate=1','--allow_file_create='+('1' if phase=='prepare' else '0')]
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


def validate_raw(raw, params, seed, phase):
    """Check fio's emitted options and per-job work, not just total bytes."""
    validate_params(params)
    jobs=raw.get('jobs',[])
    if len(jobs)!=params['numjobs'] or {j.get('jobname') for j in jobs}!={f'job-{i}' for i in range(params['numjobs'])}:
        raise ValueError('fio job identity differs')
    mode='write' if phase=='prepare' else 'read' if phase=='verify' else params['rw']
    size=params['io_bytes_per_job'] if phase=='measure' else params['file_bytes_per_job']
    expected={'ioengine':'psync','iodepth':'1','direct':'0','thread':'1','fallocate':'none',
              'randrepeat':'1','refill_buffers':'1','verify':'crc32c','verify_fatal':'1',
              'verify_state_save':'0','end_fsync':'1','fsync':'0' if phase=='prepare' else '1','group_reporting':'0','unlink':'0',
              'invalidate':'1','allow_file_create':'1' if phase=='prepare' else '0',
              'rw':mode,'bs':str(params['block_bytes']),'size':str(params['file_bytes_per_job']),
              'io_size':str(size),'rwmixread':'75','do_verify':'1' if phase=='verify' else '0'}
    if phase=='verify':expected['verify_only']='1'
    filenames=set()
    for job in jobs:
        index=int(job['jobname'].removeprefix('job-'))
        options=dict(raw.get('global options',{}),**job.get('job options',{}))
        want=dict(expected,randseed=str(seed+index),name=job['jobname'])
        if set(options)-set(want)-{'filename','output','output-format','time_based','verify_only'}:
            raise ValueError('undeclared native fio option')
        if any(str(options.get(k))!=v for k,v in want.items()):
            raise ValueError('fio native options differ from frozen workload')
        if options.get('time_based','0')!='0' or (phase!='verify' and options.get('verify_only','0')!='0'):
            raise ValueError('fio time-based/verify-only work is not the measure phase')
        filename=options.get('filename','')
        if not Path(filename).is_absolute() or Path(filename).name!=f'fio-{index:04}.dat':
            raise ValueError('fio filename differs from job identity')
        filenames.add(filename)
        rb,wb=(int(job.get(k,{}).get('io_bytes',0)) for k in ('read','write'))
        if rb+wb!=size or min(rb,wb)<0 or rb%params['block_bytes'] or wb%params['block_bytes']:
            raise ValueError('fio per-job bytes differ')
        if (mode in ('read','randread') and wb) or (mode in ('write','randwrite') and rb):
            raise ValueError('fio read/write direction differs')
    if len(filenames)!=len(jobs):raise ValueError('distinct per-job fio files required')


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
    args = arguments(request,phase,out/'fio.json')
    if not root.is_dir() or root.is_symlink() or root.resolve()!=root or not out.is_absolute() or out.resolve().is_relative_to(root):
        raise ValueError('existing regular dataset directory and external absolute output required')
    binary = Path(request['binary']['path'])
    if not binary.is_absolute() or digest(binary)!=request['binary']['sha256']:
        raise ValueError('pin the actual fio binary, not the unrelated Python fio package')
    for i in range(request['params']['numjobs']):
        target=root/f'fio-{i:04}.dat'
        if target.is_symlink() or (phase=='prepare' and target.exists()) or (phase!='prepare' and not target.is_file()):
            raise ValueError('fresh preparation / existing regular input files required')
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
    raw=load(out/'fio.json')
    result=summarize(raw,request['params']['numjobs'],expected,code,wall)
    validate_raw(raw,request['params'],request['seed'],phase)
    result.update(phase=phase,raw_sha256=digest(out/'fio.json'),params=request['params'],seed=request['seed'])
    write_new(out/'result.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request',required=True);p.add_argument('--phase',choices=['prepare','measure','verify'],required=True)
    p.add_argument('--output',required=True);a=p.parse_args()
    raise SystemExit(0 if run(load(a.request),a.phase,a.output)['passed'] else 1)
