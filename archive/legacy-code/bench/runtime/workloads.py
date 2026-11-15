"""Draft one fio/smallfiles barrier worker using the existing node/cohort runtime."""
import argparse
from pathlib import Path

from .common import digest, load, pinned, private_dir, write_new
from .prepare import AGENT_BUDGET
from ..experiment_io import linux_path


def counts(p):
    if p.get('file_bytes')!=4096 or p.get('directories')!=1024:
        raise ValueError('fixed 4 KiB payload and 1024-directory mapping required')
    for key in ('files','hot_files','workers','read_ops','cow_files','write_ops','mixed_ops'):
        if type(p[key]) is not int or p[key]<=0:raise ValueError('positive fixed work required')
    if p['hot_files']>p['files'] or p['cow_files']>p['hot_files']:
        raise ValueError('hot/COW set exceeds dataset')
    if any(p[k]%p['workers'] for k in ('hot_files','read_ops','cow_files','write_ops','mixed_ops')) or (p['mixed_ops']//p['workers'])%4:
        raise ValueError('balanced workers and exact 3:1 mixed operations required')
    if p.get('prewarm_passes')!=2 or p.get('mixed_read_fraction')!=.75 or p.get('fsync_every_write') is not True:
        raise ValueError('freeze two prewarm passes, 3:1 mix and fsync')
    reads=2*p['read_ops']+2*p['hot_files']+4*p['cow_files']+3*p['mixed_ops']//4
    writes=p['cow_files']+p['write_ops']+p['mixed_ops']//4
    return {'completed':True,'passed':True,'files':p['files'],'verified_reads':reads,
            'writes':writes,'actual_read_bytes':reads*4096,'actual_write_bytes':writes*4096}


def worker(request, output):
    s=load(request); kind=s['kind']
    if kind not in ('fio','smallfiles'):raise ValueError('fio or smallfiles worker required')
    binary=str(pinned(s['binary'])); root=linux_path(s['root']); output=linux_path(str(output))
    mount=load(pinned(s['mount']['spec'])); point=linux_path(mount['mount_point'])
    if root!=point and point not in root.parents:raise ValueError('workload must use supervised mount')
    if output==point or point in output.parents:raise ValueError('evidence must be outside mount')
    if not s.get('worker_id') or not s.get('workload_id'):raise ValueError('worker and workload identity required')
    params=s['params']; request_ref={'path':str(Path(request).resolve()),'sha256':digest(request)}
    if mount.get('suite_binding'):
        from ..suite_mount import cell_of
        plan=load(pinned(mount['suite_binding']['plan']))
        cell=cell_of(plan,mount['suite_binding']['cell_id'])
        if cell['workload']!=kind or cell['params']!=params or plan['config']['seed']!=s['seed']:
            raise ValueError('worker workload/params/seed differ from mounted suite cell')
    inputs=[request_ref,s['binary']]; result_path=output/'workload'/'result.json'
    if kind=='smallfiles':
        if type(s.get('workspace')) is not int or s['workspace']<=0:raise ValueError('distinct positive child tag required')
        expected=counts(params)
        argv=[binary,'run','--root',str(root),'--output',str(output/'workload')]
        for option,key in (('files','files'),('hot-files','hot_files'),('workers','workers'),('read-ops','read_ops'),
                           ('cow-files','cow_files'),('write-ops','write_ops'),('mixed-ops','mixed_ops')):
            argv+=['--'+option,str(params[key])]
        argv+=['--seed',str(s['seed']),'--workspace',str(s['workspace'])]
        executable=s['binary']
    else:
        from ..fio_workload import arguments
        arguments(s,'measure',output/'workload'/'fio.json')
        helper=pinned(s['helper']); python=pinned(s['python'])
        if helper.name!='fio_workload.py':raise ValueError('pin fio_workload.py helper')
        inputs.extend([s['helper'],s['io_helper']])
        if pinned(s['io_helper'])!=helper.parent/'experiment_io.py':raise ValueError('pin the actual adjacent IO helper')
        executable=s['python']
        argv=[str(python),str(helper),'--request',str(Path(request).resolve()),'--phase','measure','--output',str(output/'workload')]
        expected={'completed':True,'passed':True,'jobs':params['numjobs'],
                  'application_bytes':params['numjobs']*params['io_bytes_per_job'],'phase':'measure'}
    purpose=s.get('purpose','experiment')
    if purpose not in ('experiment','observer-calibration-on','observer-calibration-off'):raise ValueError('invalid purpose')
    private_dir(output)
    step={'schema':'experiment-step-v1','reviewed':False,'cell_id':s['worker_id'],'stage':'measure',
          'argv':argv,'cwd':str(linux_path(s['cwd'])),'executable_sha256':executable['sha256'],
          'inputs':inputs,'inherit_environment':[]}
    write_new(output/'step.json',step)
    result={'schema':'experiment-worker-v1','reviewed':False,'worker_id':s['worker_id'],
            'purpose':purpose,'kind':kind,'budget':AGENT_BUDGET,'requires_mount':True,'mount':s['mount'],
            'jobs':[{'id':kind+'-0001','workload_id':s['workload_id'],
                     'step':{'path':str(output/'step.json'),'sha256':digest(output/'step.json')},
                     'result':{'path':str(result_path),'equals':expected}}]}
    write_new(output/'worker.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();worker(a.request,a.output)
