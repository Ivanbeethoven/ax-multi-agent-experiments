#!/usr/bin/env python3
"""Prepare one observer from the actual live services; does not start sampling."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,identity,write_new,pinned
from bench.runtime.observe import validate,collect_target
from bench.runtime.pilot_pools import alias


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role',choices=['client','target','metadata','objects'],required=True)
    p.add_argument('--product',choices=['brewfs','juicefs'],required=True)
    p.add_argument('--pool',default='pool-a')
    p.add_argument('--hostname',help='Expected actual hostname from the frozen runtime fleet')
    p.add_argument('--entity',action='store_true',help='Entity cache metrics; debt is checked through the original namespace')
    p.add_argument('--metric-contract-sha256',required=True)
    p.add_argument('--mount-directory',type=Path)
    p.add_argument('--extra-mount-directory',type=Path,action='append',default=[],
                   help='Additional original supervisors, for preparation and cold verification')
    p.add_argument('--collector-name',default='ax-pilot-cloud-02-collector')
    p.add_argument('--rustfs-compose',type=Path,help='Exact live deployment used by the metrics contract')
    p.add_argument('--rustfs-compose-sha256')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if bool(a.rustfs_compose)!=bool(a.rustfs_compose_sha256):p.error('RustFS compose path and SHA256 must be provided together')
    if a.role=='objects' and a.pool!='pool-a' and not a.rustfs_compose:
        raise ValueError('New pool requires its exact pinned RustFS compose')
    if a.entity and a.product!='brewfs':raise ValueError('Entity is a BrewFS engine')
    if a.pool!='pool-a' and not a.hostname:raise ValueError('New pool observer requires its actual frozen hostname')
    observer_id=alias(a.pool,a.role)
    if socket.gethostname()!=(a.hostname or 'ax-pilot-20261111-'+a.role):raise ValueError('Wrong observer host')
    a.out.mkdir(parents=True,exist_ok=False)
    root={'client':'/srv/ax-client','target':'/srv/ax-target','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}[a.role]
    s=dict(schema='experiment-observer-v1',reviewed=True,interval_seconds=5,metric_contract_sha256=a.metric_contract_sha256,
        data_paths=[root],cgroups=[],processes=[],targets=[],role=a.role,pool=a.pool,observer_id=observer_id,
        review=dict(reviewer='pilot-operator',note='Native endpoints probed on this host; long-lived service identity and cgroups read from the running processes. Mount lifecycle is bound separately.'))
    def group(pid,tag):
        relative=next(x[3:] for x in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if x.startswith('0::'))
        return dict(id=tag,path='/sys/fs/cgroup/'+relative.lstrip('/'))
    def http(tag,url,required,counters):
        return dict(id=tag,kind='http',url=url,format='prometheus',timeout_seconds=2,required_names=required,counter_names=counters)
    if a.role in ('client','target'):
        s['cgroups']=[dict(id='agent',path='/sys/fs/cgroup/ax-slot-1')]
        # Client PIDs end before the observer. ready/stopped receipts and the
        # Agent cgroup track them; PID 1 pins the host boot throughout sampling.
        s['processes']=[identity(1)]
        required=(['brewfs_fuse_op_total','brewfs_fuse_op_latency_seconds_count','brewfs_object_get_total',
            'brewfs_object_get_bytes_total','brewfs_object_put_total','brewfs_object_put_bytes_total',
            'brewfs_cache_hits_total','brewfs_cache_misses_total','brewfs_writeback_pending_upload_bytes'] if a.product=='brewfs' else
            ['juicefs_fuse_ops_total','juicefs_fuse_ops_durations_histogram_seconds_count','juicefs_blockcache_hits',
             'juicefs_blockcache_miss','juicefs_blockcache_hit_bytes','juicefs_blockcache_miss_bytes',
             'juicefs_object_request_data_bytes','juicefs_staging_blocks','juicefs_staging_block_bytes'])
        counters=[n for n in required if n not in ('brewfs_writeback_pending_upload_bytes','juicefs_staging_blocks','juicefs_staging_block_bytes')]
        # Labeled request series can appear only after their first operation.
        # Do not warm the data just to manufacture discovery counters. Keep raw
        # series and check the workload-specific ones after actual work.
        always=(['brewfs_fuse_op_total','brewfs_mount_info','brewfs_writeback_pending_upload_bytes'] if a.product=='brewfs' else
                ['juicefs_uptime','juicefs_fuse_ops_total','juicefs_blockcache_hits','juicefs_blockcache_miss','juicefs_staging_blocks','juicefs_staging_block_bytes'])
        if a.entity:
            always=['brewfs_fuse_op_total','brewfs_mount_info','brewfs_cache_hits_total','brewfs_cache_misses_total',
                    'brewfs_entity_read_cache_capacity_bytes','brewfs_entity_read_cache_retained_bytes','brewfs_entity_read_cache_evictions_total']
            required=[n for n in required if n!='brewfs_writeback_pending_upload_bytes']+always[4:]
            counters=[n for n in required if n not in ['brewfs_entity_read_cache_capacity_bytes','brewfs_entity_read_cache_retained_bytes']]
        target=http('filesystem','http://127.0.0.1:'+('8910' if a.product=='brewfs' else '9567')+'/metrics',always,counters)
        target['workload_series']=required
        if a.mount_directory is None:raise ValueError('Bind the original supervised mount directory')
        target['mount_directory']=str(a.mount_directory)
        s['targets']=[target]
        for index, directory in enumerate(a.extra_mount_directory):
            extra=dict(target,id='filesystem-extra-'+str(index+1),mount_directory=str(directory))
            s['targets'].append(extra)
        s['process_scope']='PID 1 pins boot; actual mount process identity comes from ready.json/stopped.json and cgroup samples, not PID 1 statistics.'
    elif a.role=='metadata':
        pid=int(subprocess.check_output(['systemctl','show','ax-pilot-redis','--property=MainPID','--value'],text=True))
        s['processes']=[identity(pid)];s['cgroups']=[group(pid,'redis-process')]
        executable=Path('/usr/bin/redis-cli').resolve()
        step=dict(schema='experiment-step-v1',reviewed=True,cell_id='redis-observer',stage='INFO',argv=[str(executable),'INFO','ALL'],
            cwd=str(REPO),executable_sha256=digest(executable),inputs=[dict(path=str(executable),sha256=digest(executable))],inherit_environment=[])
        write_new(a.out/'redis-info-step.json',step)
        s['targets']=[dict(id='redis',kind='command',step=dict(path=str(a.out/'redis-info-step.json'),sha256=digest(a.out/'redis-info-step.json')),
            format='redis-info',timeout_seconds=2,required_names=['total_commands_processed','total_net_input_bytes','total_net_output_bytes',
            'used_memory','used_cpu_sys','used_cpu_user','aof_enabled','aof_last_write_status'],
            counter_names=['total_commands_processed','total_net_input_bytes','total_net_output_bytes','used_cpu_sys','used_cpu_user'])]
    else:
        compose=(pinned(dict(path=str(a.rustfs_compose),sha256=a.rustfs_compose_sha256)) if a.rustfs_compose
                 else Path('/srv/ax-objects-0/preparation/rustfs-01/deployment/rustfs1/compose.json'))
        cid=subprocess.check_output(['docker','compose','-f',str(compose),'ps','-q','rustfs'],text=True).strip()
        rows=[json.loads(subprocess.check_output(['docker','inspect',name]))[0] for name in (cid,a.collector_name)]
        if not all(r['State']['Running'] for r in rows):raise ValueError('Expected original live services')
        s['processes']=[identity(r['State']['Pid']) for r in rows]
        s['cgroups']=[group(r['State']['Pid'],tag+'-process') for r,tag in zip(rows,['rustfs','collector'])]
        s['data_paths']=['/srv/ax-objects-'+str(i) for i in range(4)]
        required=['rustfs_s3_http_requests_total','rustfs_http_server_request_body_bytes_total',
            'rustfs_http_server_response_body_bytes_total','rustfs_http_server_request_duration_seconds_count',
            'rustfs_storage_snapshot_last_success_timestamp_seconds']
        s['targets']=[http('rustfs','http://127.0.0.1:18889/metrics',required,required[:-1]),
            http('collector','http://127.0.0.1:8888/metrics',
            ['otelcol_receiver_accepted_metric_points','otelcol_receiver_failed_metric_points','otelcol_receiver_refused_metric_points'],
            ['otelcol_receiver_accepted_metric_points','otelcol_receiver_failed_metric_points','otelcol_receiver_refused_metric_points'])]
        write_new(a.out/'service-identities.json',[dict(name=r['Name'],container_id=r['Id'],image_id=r['Image'],pid=r['State']['Pid'],restart_count=r['RestartCount']) for r in rows])
    for t in s['targets']:
        # A not-yet-mounted client is checked when ready. Services are checked
        # now so absent series cannot be mistaken for an inactive mount.
        if a.role not in ('client','target'):
            raw,_=collect_target(t);(a.out/(t['id']+'-probe.txt')).write_text(raw)
    validate(s)
    write_new(a.out/'observer.json',s)
    print(json.dumps(dict(path=str(a.out/'observer.json'),sha256=digest(a.out/'observer.json'))))


if __name__=='__main__':
    main()
