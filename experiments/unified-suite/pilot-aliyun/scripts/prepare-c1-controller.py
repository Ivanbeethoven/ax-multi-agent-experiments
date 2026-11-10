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


def ref(path):return dict(path=str(path),sha256=digest(path))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    roots={'client':'/srv/ax-client','metadata':'/srv/ax-metadata','objects':'/srv/ax-objects-0'}
    config=a.control/'ssh.conf'
    receipts=[]
    def get(role,relative,local):
        source=roots[role]+'/'+relative;target=a.out/local;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():raise ValueError('Never replace collected evidence')
        wanted=subprocess.check_output(['ssh','-F',str(config),'pool-a-'+role,shlex.join(['sha256sum',source])],text=True).split()[0]
        subprocess.run(['scp','-F',str(config),'pool-a-'+role+':'+source,str(target)],check=True)
        if digest(target)!=wanted:raise ValueError('Evidence transfer changed')
        receipts.append(dict(role=role,source=source,**ref(target)))
        return target
    for role in roots:
        for file in ('result.json','meminfo.txt','cpuinfo.txt','kernel.txt','previous-timers.json'):
            get(role,'preparation/maintenance-freeze-01/'+file,'hosts/'+role+'/'+file)
    for arm in ('native','juicefs'):
        for name in ('asset.json','base-seq-prepare/result.json','base-seq-verify/result.json',
                     'base-random-prepare/result.json','base-random-verify/result.json',
                     'cold-base-verify-1048576/result.json','cold-base-verify-4096/result.json',
                     'prep-exit-01.json','prep-exit-02.json','base-debt/result.json','native-endpoint.prom'):
            get('client','a/c1-'+arm+'/'+name,'assets/'+arm+'/'+name)
    for route in ('native-flat-02','native-workspace-01','juicefs-regular-01'):
        get('client','qualification/'+route+'/result.json','filesystem/'+route+'.json')
    get('objects','qualification/rustfs-layout-03/result.json','rustfs-layout.json')
    get('objects','preparation/rustfs-01/result.json','rustfs-source.json')
    get('objects','preparation/collector-02/started.json','collector.json')
    write_new(a.out/'transfers.json',receipts)
    fleet=load(a.control/'fleet.json');probes=[load(p) for p in sorted((a.control/'probes').glob('*/probe.json'))]
    checks={}
    for role in roots:
        maintenance=load(a.out/'hosts'/role/'result.json')
        mem=int(re.search(r'^MemTotal:\s+(\d+)',(a.out/'hosts'/role/'meminfo.txt').read_text(),re.M)[1])*1024
        probe=next(r for r in probes if r['node_id']=='pool-a-'+role and r['host_observer'])
        checks[role]=dict(passed=maintenance['passed'] and probe['passed'] and not maintenance['reboot_required'] and
            maintenance['boot_id']==probe['boot_id'] and probe['affinity_cpu_count']>=(8 if role=='objects' else 4) and
            mem>=(30 if role=='objects' else 15)*1024**3,memory_total_bytes=mem,affinity_cpu_count=probe['affinity_cpu_count'],
            maintenance=ref(a.out/'hosts'/role/'result.json'),probe=ref(a.control/'probes'/('pool-a-'+role)/'probe.json'))
    slot=next(r for r in probes if r['slot_id'] is not None)
    observed=slot['cgroups']['observed'][0]
    slot_ok=observed['cpu.max']=='200000 100000' and observed['memory.max']=='8589934592' and observed['memory.swap.max']=='0'
    hardware=dict(passed=all(r['passed'] for r in checks.values()) and slot_ok,inventory_sha256=digest(a.control/'fleet.json'),
        checks=checks,slot_limits=observed,scope='Actual ECS capacities, boot identities, data paths and recorded maintenance freeze; no physical-host exclusivity claim')
    write_new(a.out/'hardware.json',hardware)
    if not hardware['passed']:raise ValueError('Hardware preparation failed')
    for arm,product in [('native','brewfs'),('juicefs','juicefs')]:
        folder=a.out/arm;folder.mkdir()
        asset=a.out/'assets'/arm
        paths=[asset/x for x in ('base-seq-prepare/result.json','base-seq-verify/result.json','base-random-prepare/result.json',
            'base-random-verify/result.json','cold-base-verify-1048576/result.json','cold-base-verify-4096/result.json')]
        passed=all(load(f)['passed'] and load(f)['completed'] and load(f)['application_bytes']==67108864 for f in paths)
        passed=passed and all(load(asset/n)['passed'] and load(asset/n)['exit_code']==0 and load(asset/n)['mount_absent'] for n in ('prep-exit-01.json','prep-exit-02.json'))
        input_check=dict(passed=passed,crc_fixture_checks=[ref(p) for p in paths],cold_readback=True,
            asset_identity=ref(asset/'asset.json'),scope='Two CRC block geometries, full prepare/readback and fresh-cache reopening on the original backend')
        write_new(folder/'input.json',input_check)
        if not passed:raise ValueError('Input qualification failed')
        route='native-flat-02' if arm=='native' else 'juicefs-regular-01'
        fs=load(a.out/'filesystem'/(route+'.json'))
        filesystem=dict(passed=fs['passed'] and fs['normally_stopped'] and load(a.out/'rustfs-layout.json')['passed'],
            native_checks=ref(a.out/'filesystem'/(route+'.json')),rustfs_layout=ref(a.out/'rustfs-layout.json'),
            actual_fixture=input_check,scope='Cloud native write/normal stop/reopen plus exact suite-profile CRC fixture checks')
        write_new(folder/'filesystem.json',filesystem)
        if not filesystem['passed']:raise ValueError('Filesystem qualification failed')
        always=(['brewfs_fuse_op_total','brewfs_mount_info','brewfs_writeback_pending_upload_bytes'] if product=='brewfs' else
            ['juicefs_uptime','juicefs_fuse_ops_total','juicefs_blockcache_hits','juicefs_blockcache_miss','juicefs_staging_blocks','juicefs_staging_block_bytes'])
        bindings=[dict(observer_id='pool-a-client',target_id='filesystem',required_names=always,layer='filesystem client',
            unit='native per-series units',meaning='FUSE operations, client cache and pending data; retain native TYPE/HELP and histogram buckets, not host disk bytes'),
            dict(observer_id='pool-a-metadata',target_id='redis',required_names=['total_commands_processed','total_net_input_bytes','total_net_output_bytes',
                'used_memory','used_cpu_sys','used_cpu_user','aof_enabled','aof_last_write_status'],layer='metadata service',unit='commands, bytes, CPU seconds',
                meaning='Redis INFO cumulative service work; includes shared service/observer activity, not per-workspace attribution'),
            dict(observer_id='pool-a-objects',target_id='rustfs',required_names=['rustfs_s3_http_requests_total','rustfs_http_server_request_body_bytes_total',
                'rustfs_http_server_response_body_bytes_total','rustfs_http_server_request_duration_seconds_count','rustfs_storage_snapshot_last_success_timestamp_seconds'],
                layer='S3 HTTP service',unit='requests, HTTP body bytes, seconds',meaning='HTTP requests and encoded network body bytes. Physical disk bytes are separate /proc/diskstats sector counters; source point timestamps must remain fresh.'),
            dict(observer_id='pool-a-objects',target_id='collector',required_names=['otelcol_receiver_accepted_metric_points','otelcol_receiver_failed_metric_points',
                'otelcol_receiver_refused_metric_points'],layer='telemetry receiver',unit='metric points',meaning='Accepted points must advance; failed/refused remain zero; service PID and cgroup track collector costs')]
        contract=dict(schema='experiment-metric-contract-v1',reviewed=True,product=product,bindings=bindings,
            source_pins=[ref(a.out/'rustfs-source.json'),ref(a.out/'collector.json'),ref(a.control/'provenance/build-manifest.json'),ref(asset/'native-endpoint.prom')],
            interval_seconds=5,max_gap_seconds=30,
            calibration_scope='Client native metrics and RustFS OTLP sender stay enabled. Toggle all external observers/scrapes and the OTLP collector. Native built-in instrumentation is part of each filesystem binary, not removed.',
            notes=['No missing counter is filled with zero. Workload-specific labeled counters checked after actual work.',
                'Client-cold is a fresh mount/cache; server/page caches and background maintenance are observed, not claimed cold.',
                'At N=1 task p99 is one task duration; native fio operation latency percentiles are reported separately.'])
        write_new(folder/'metrics.json',contract)
        binary='/usr/bin/fio'
        sha=subprocess.check_output(['ssh','-F',str(config),'pool-a-client','sha256sum '+binary],text=True).split()[0]
        workload=dict(schema='experiment-workload-contract-v1',reviewed=True,binary_pins=[dict(path=binary,sha256=sha)],
            input_pins=[ref(folder/'input.json'),ref(a.control/'suite/plan.json')],
            params=next(c['params'] for c in load(a.control/'suite/plan.json')['cells'] if c['case']=='fio-randread'),
            seed=load(a.control/'suite/plan.json')['config']['seed'],scope='C1 randread calibration, 64 MiB fixed work, one 2 CPU / 8 GiB worker; other workload classes need their own calibration evidence')
        write_new(folder/'workload.json',workload)
    print(json.dumps(dict(passed=True,out=str(a.out),products=['brewfs','juicefs'])))


if __name__=='__main__':
    main()
