#!/usr/bin/env python3
"""Collect actual C1 preparation evidence and write its two metrics contracts."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,write_new
from bench.runtime import pilot_context as pilot_runtime
from bench.runtime.pilot_pools import alias,ROOTS


def ref(path):return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--pool',default='pool-a')
    p.add_argument('--product',choices=['brewfs','juicefs'])
    p.add_argument('--asset',help='Actual absolute client asset directory; select one product')
    p.add_argument('--maintenance-directory',default='preparation/maintenance-freeze-01',
                   help='Exact original receipt directory; absolute or relative to each role data root')
    p.add_argument('--route-proof',help='Actual absolute result.json for this product and batch')
    p.add_argument('--rustfs-layout-proof',default='/srv/ax-objects-0/qualification/rustfs-layout-03/result.json')
    p.add_argument('--rustfs-source-proof',default='/srv/ax-objects-0/preparation/rustfs-01/result.json')
    p.add_argument('--collector-proof',default='/srv/ax-objects-0/preparation/collector-02/started.json')
    p.add_argument('--rustfs-compose',help='Actual original objects compose.json; required for another pool')
    pilot_runtime.arguments(p)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    maintenance_directory=Path(a.maintenance_directory)
    if '..' in maintenance_directory.parts:
        raise ValueError('Explicit maintenance directory without parent traversal required')
    runtime=pilot_runtime.select(a.control,pilot_runtime.reference(a))
    roots={r:ROOTS[r] for r in ('client','metadata','objects')}
    config=runtime/'ssh.conf';plan=load(a.control/'suite/plan.json')
    products={c['arm']['product'] for c in plan['cells'] if c['pool']==a.pool}
    if a.product:
        if a.product not in products:raise ValueError('Requested product is not assigned to this actual pool')
        products={a.product}
    arms=[(arm,product) for arm,product in [('native','brewfs'),('juicefs','juicefs')] if product in products]
    if not arms:raise ValueError('No flat route assigned to this pool')
    if a.asset and (len(arms)!=1 or not Path(a.asset).is_absolute()):raise ValueError('Explicit absolute asset requires one product')
    if a.route_proof and (len(arms)!=1 or not Path(a.route_proof).is_absolute()):raise ValueError('Explicit route proof requires one product and an absolute path')
    if len(plan['config']['clusters'])>1 and not a.asset:raise ValueError('New multi-pool qualification requires an explicit prepared asset')
    if a.pool!='pool-a' and not a.rustfs_compose:raise ValueError('New pool requires its explicit original RustFS compose path')
    rustfs_compose=a.rustfs_compose or '/srv/ax-objects-0/preparation/rustfs-01/deployment/rustfs1/compose.json'
    if not Path(rustfs_compose).is_absolute():raise ValueError('Absolute original compose path required')
    receipts=[]
    def get(role,relative,local):
        source=relative if Path(relative).is_absolute() else roots[role]+'/'+relative
        target=a.out/local;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Never replace collected evidence')
        wanted=subprocess.check_output(['ssh','-F',str(config),alias(a.pool,role),shlex.join(['sha256sum',source])],text=True).split()[0]
        subprocess.run(['scp','-F',str(config),alias(a.pool,role)+':'+source,str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Evidence transfer changed')
        receipts.append(dict(role=role,source=source,**ref(target)))
        return target
    for role in roots:
        for file in ('result.json','meminfo.txt','cpuinfo.txt','kernel.txt','previous-timers.json'):
            get(role,str(maintenance_directory/file),'hosts/'+role+'/'+file)
    for arm,product in arms:
        source_asset=a.asset or roots['client']+'/a/c1-'+arm
        for name in ('asset.json','base-seq-prepare/result.json','base-seq-verify/result.json',
                     'base-random-prepare/result.json','base-random-verify/result.json',
                     'cold-base-verify-1048576/result.json','cold-base-verify-4096/result.json',
                     'prep-exit-01.json','prep-exit-02.json','base-debt/result.json','native-endpoint.prom'):
            get('client',str(Path(source_asset).relative_to(roots['client']))+'/'+name,'assets/'+arm+'/'+name)
    route_files={}
    for arm,product in arms:
        route='native-flat-02' if arm=='native' else 'juicefs-regular-01'
        source=a.route_proof or roots['client']+'/qualification/'+route+'/result.json'
        local='filesystem/'+(arm if a.route_proof else route)+'.json'
        route_files[arm]=get('client',str(Path(source).relative_to(roots['client'])),local)
    if len(products)==2:get('client','qualification/native-workspace-01/result.json','filesystem/native-workspace-01.json')
    for source,target in [(a.rustfs_layout_proof,'rustfs-layout.json'),(a.rustfs_source_proof,'rustfs-source.json'),(a.collector_proof,'collector.json')]:
        get('objects',str(Path(source).relative_to(roots['objects'])),target)
    get('objects',str(Path(rustfs_compose).relative_to(roots['objects'])),'rustfs-compose.json')
    write_new(a.out/'transfers.json',receipts)
    fleet=load(runtime/'fleet.json');probes=[load(p) for p in sorted((runtime/'probes').glob('*/probe.json'))]
    checks={}
    for role in roots:
        maintenance=load(a.out/'hosts'/role/'result.json')
        mem=int(re.search(r'^MemTotal:\s+(\d+)',(a.out/'hosts'/role/'meminfo.txt').read_text(),re.M)[1])*1024
        probe=next(r for r in probes if r['node_id']==alias(a.pool,role) and r['host_observer'])
        checks[role]=dict(passed=maintenance['passed'] and probe['passed'] and not maintenance['reboot_required'] and
            maintenance['boot_id']==probe['boot_id'] and probe['affinity_cpu_count']>=(8 if role=='objects' else 4) and
            mem>=(30 if role=='objects' else 15)*1024**3,memory_total_bytes=mem,affinity_cpu_count=probe['affinity_cpu_count'],
            maintenance=ref(a.out/'hosts'/role/'result.json'),probe=ref(runtime/'probes'/alias(a.pool,role)/'probe.json'))
    slot=next(r for r in probes if r['node_id']==alias(a.pool,'client') and r['slot_id'] is not None)
    observed=slot['cgroups']['observed'][0]
    slot_ok=observed['cpu.max']=='200000 100000' and observed['memory.max']=='8589934592' and observed['memory.swap.max']=='0'
    hardware=dict(passed=all(r['passed'] for r in checks.values()) and slot_ok,inventory_sha256=digest(runtime/'fleet.json'),pool=a.pool,
        checks=checks,slot_limits=observed,scope='Actual ECS capacities, boot identities, data paths and recorded maintenance freeze; no physical-host exclusivity claim')
    write_new(a.out/'hardware.json',hardware)
    if not hardware['passed']:raise ValueError('Hardware preparation failed')
    for arm,product in arms:
        folder=a.out/arm;folder.mkdir()
        asset=a.out/'assets'/arm
        actual=load(asset/'asset.json')
        if actual['plan']['sha256']!=digest(a.control/'suite/plan.json') or actual.get('pool','pool-a')!=a.pool:
            raise ValueError('C1 asset belongs to another frozen plan or physical pool')
        write_new(folder/'binding.json',dict(asset=a.asset or roots['client']+'/a/c1-'+arm,
            asset_manifest=ref(asset/'asset.json'),pool=a.pool))
        paths=[asset/x for x in ('base-seq-prepare/result.json','base-seq-verify/result.json','base-random-prepare/result.json',
            'base-random-verify/result.json','cold-base-verify-1048576/result.json','cold-base-verify-4096/result.json')]
        passed=all(load(f)['passed'] and load(f)['completed'] and load(f)['application_bytes']==67108864 for f in paths)
        passed=passed and all(load(asset/n)['passed'] and load(asset/n)['exit_code']==0 and load(asset/n)['mount_absent'] for n in ('prep-exit-01.json','prep-exit-02.json'))
        input_check=dict(passed=passed,crc_fixture_checks=[ref(p) for p in paths],cold_readback=True,
            asset_identity=ref(asset/'asset.json'),scope='Two CRC block geometries, full prepare/readback and fresh-cache reopening on the original backend')
        write_new(folder/'input.json',input_check)
        if not passed:raise ValueError('Input qualification failed')
        route_file=route_files[arm];fs=load(route_file)
        filesystem=dict(passed=fs['passed'] and fs['normally_stopped'] and load(a.out/'rustfs-layout.json')['passed'],
            native_checks=ref(route_file),rustfs_layout=ref(a.out/'rustfs-layout.json'),
            actual_fixture=input_check,scope='Cloud native write/normal stop/reopen plus exact suite-profile CRC fixture checks')
        write_new(folder/'filesystem.json',filesystem)
        if not filesystem['passed']:raise ValueError('Filesystem qualification failed')
        always=(['brewfs_fuse_op_total','brewfs_mount_info','brewfs_writeback_pending_upload_bytes'] if product=='brewfs' else
            ['juicefs_uptime','juicefs_fuse_ops_total','juicefs_blockcache_hits','juicefs_blockcache_miss','juicefs_staging_blocks','juicefs_staging_block_bytes'])
        bindings=[dict(observer_id=alias(a.pool,'client'),target_id='filesystem',required_names=always,layer='filesystem client',
            unit='native per-series units',meaning='FUSE operations, client cache and pending data; retain native TYPE/HELP and histogram buckets, not host disk bytes'),
            dict(observer_id=alias(a.pool,'metadata'),target_id='redis',required_names=['total_commands_processed','total_net_input_bytes','total_net_output_bytes',
                'used_memory','used_cpu_sys','used_cpu_user','aof_enabled','aof_last_write_status'],layer='metadata service',unit='commands, bytes, CPU seconds',
                meaning='Redis INFO cumulative service work; includes shared service/observer activity, not per-workspace attribution'),
            dict(observer_id=alias(a.pool,'objects'),target_id='rustfs',required_names=['rustfs_s3_http_requests_total','rustfs_http_server_request_body_bytes_total',
                'rustfs_http_server_response_body_bytes_total','rustfs_http_server_request_duration_seconds_count','rustfs_storage_snapshot_last_success_timestamp_seconds'],
                layer='S3 HTTP service',unit='requests, HTTP body bytes, seconds',meaning='HTTP requests and encoded network body bytes. Physical disk bytes are separate /proc/diskstats sector counters; source point timestamps must remain fresh.'),
            dict(observer_id=alias(a.pool,'objects'),target_id='collector',required_names=['otelcol_receiver_accepted_metric_points','otelcol_receiver_failed_metric_points',
                'otelcol_receiver_refused_metric_points'],layer='telemetry receiver',unit='metric points',meaning='Accepted points must advance; failed/refused remain zero; service PID and cgroup track collector costs')]
        contract=dict(schema='experiment-metric-contract-v1',reviewed=True,product=product,bindings=bindings,
            observer_settings=dict(rustfs_compose=dict(path=rustfs_compose,sha256=digest(a.out/'rustfs-compose.json'))),
            source_pins=[ref(a.out/'rustfs-source.json'),ref(a.out/'collector.json'),ref(a.control/'provenance/build-manifest.json'),ref(asset/'native-endpoint.prom'),ref(Path(__file__))],
            interval_seconds=5,max_gap_seconds=30,
            calibration_scope='Client native metrics and RustFS OTLP sender stay enabled. Toggle all external observers/scrapes and the OTLP collector. Native built-in instrumentation is part of each filesystem binary, not removed.',
            notes=['No missing counter is filled with zero. Workload-specific labeled counters checked after actual work.',
                'Client-cold is a fresh mount/cache; server/page caches and background maintenance are observed, not claimed cold.',
                'At N=1 task p99 is one task duration; native fio operation latency percentiles are reported separately.'])
        write_new(folder/'metrics.json',contract)
        binary='/usr/bin/fio'
        sha=subprocess.check_output(['ssh','-F',str(config),alias(a.pool,'client'),'sha256sum '+binary],text=True).split()[0]
        workload=dict(schema='experiment-workload-contract-v1',reviewed=True,binary_pins=[dict(path=binary,sha256=sha)],
            input_pins=[ref(folder/'input.json'),ref(a.control/'suite/plan.json')],
            params=next(c['params'] for c in load(a.control/'suite/plan.json')['cells'] if c['case']=='fio-randread'),
            seed=load(a.control/'suite/plan.json')['config']['seed'],scope='C1 randread calibration, 64 MiB fixed work, one 2 CPU / 8 GiB worker; other workload classes need their own calibration evidence')
        write_new(folder/'workload.json',workload)
    print(json.dumps(dict(passed=True,out=str(a.out),pool=a.pool,products=sorted(products))))


if __name__=='__main__':
    main()
