#!/usr/bin/env python3
"""Prepare/run an explicit new campaign using unchanged historical base/calibration."""
import argparse
import base64
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO))
from bench.runtime.common import digest,load,pinned,write_new
from bench.runtime import pilot_context
from bench.runtime.pilot_pools import pool_of,alias,fleet_node,resource_guard,active_conflicts
from bench import suite_plan,suite_state

def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))


def rebound_plan(old,cell_id,campaign):
    cell=next(c for c in old['cells'] if c['id']==cell_id);config=deepcopy(old['config'])
    config['campaign_id']=campaign;config['cases']=[row for row in config['cases'] if row['name']==cell['case']]
    new=suite_plan.build(config);current=next(c for c in new['cells'] if c['id']==cell_id)
    if current!=cell:raise ValueError('Full cell changed; original calibration cannot be reused')
    return new,current


def asset_view(asset,prepared_asset,execution_plan,directory,rebind):
    value=deepcopy(asset)
    value.update(prepared_plan=asset['plan'],prepared_asset=prepared_asset,plan=execution_plan,
        directory=directory,mount_point=directory+'/mnt',execution_rebind=rebind)
    if any(value[key]!=asset[key] for key in ('config','bucket','database','seed','filesystem_binary')):raise ValueError('Base/binary/configuration changed')
    return value


def command(out,name,argv,input=None):
    folder=out/name;folder.mkdir(parents=True,exist_ok=False);write_new(folder/'intent.json',dict(argv=list(map(str,argv)),automatic_retry=False))
    with (folder/'stdout').open('xb') as stdout,(folder/'stderr').open('xb') as stderr:
        code=subprocess.run(list(map(str,argv)),input=input,stdout=stdout,stderr=stderr).returncode
    write_new(folder/'exit.json',dict(exit_code=code))
    if code:raise RuntimeError('Inspect '+str(folder))
    return (folder/'stdout').read_text()


COLLECTOR_FIELDS=('image_reference','runtime_image','network','config_sha256','prometheus_port',
    'otlp_http_port','source_timestamps','container_memory_bytes','container_cpus','limiter_mib')


def collector_identity(original,current):
    if original['name']==current['name'] or original['container_id']==current['container_id']:
        raise ValueError('A replacement must identify a different named container')
    if any(original[k]!=current[k] for k in (*COLLECTOR_FIELDS,'image_id','args','runtime_limits')):
        raise ValueError('Current collector image, config or resource settings differ from the calibrated collector')
    if not current['running'] or current['runtime_limits'] != dict(network='host',memory=512*1024**2,
            nano_cpus=10**9,read_only=True):
        raise ValueError('Matching collector is not running with the calibrated resource settings')
    return dict(passed=True,original=original,current=current,configuration_unchanged=True)


def collector_probe(out,control,pool,original,replacement):
    old=[original['final_collector_name'],original['final_collector_out']]
    new=[replacement['name'],replacement['out']]
    for name,directory in (old,new):
        if not re.fullmatch(r'ax-pilot-[a-z0-9-]+',name) or not re.fullmatch(r'/srv/ax-objects-0/q/[a-z0-9-]+',directory):
            raise ValueError('Expected named pilot collector with a dedicated original receipt')
    code=r'''import pathlib,subprocess,json,hashlib,sys
def get(name,directory):
    directory=pathlib.Path(directory)
    path=directory/'started.json';b=path.read_bytes();receipt=json.loads(b)
    conf=directory/'collector.json'
    row=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    host=row['HostConfig'];mounts=row['Mounts'];labels=row['Config']['Labels']
    if (row['Id']!=receipt['container_id'] or labels.get('ax.pilot.owner')!=name
        or hashlib.sha256(conf.read_bytes()).hexdigest()!=receipt['config_sha256']
        or len(mounts)!=1 or mounts[0]['Source']!=str(conf)
        or mounts[0]['Destination']!='/etc/otelcol-contrib/pilot.json' or mounts[0]['RW']):
        raise ValueError('Collector runtime or immutable receipt differs')
    limits=dict(network=host['NetworkMode'],memory=host['Memory'],nano_cpus=host['NanoCpus'],read_only=host['ReadonlyRootfs'])
    return dict({k:receipt[k] for k in FIELDS},name=name,container_id=row['Id'],image_id=row['Image'],
        args=row['Args'],running=row['State']['Running'],runtime_limits=limits,
        start_receipt=dict(path=str(path),sha256=hashlib.sha256(b).hexdigest()))
print(json.dumps([get(*json.loads(value)) for value in sys.argv[1:]]))
'''.replace('FIELDS',repr(COLLECTOR_FIELDS))
    rows=json.loads(command(out,'collector-equivalence',['ssh','-F',control/'ssh.conf',alias(pool,'objects'),
        shlex.join(['python3','-c',code,json.dumps(old),json.dumps(new)])]))
    proof=collector_identity(*rows)
    write_new(out/'collector-equivalence.json',proof)
    return proof['current']['name']


def prepare(request):
    s=load(request)
    if s.get('schema')!='flat-attempt-rebind-v1' or s.get('reviewed') is not True:raise ValueError('Explicit reviewed new campaign required')
    original=load(pinned(s['original_request']));source=Path(original['control']);old=load(source/'suite/plan.json')
    plan,cell=rebound_plan(old,original['cell_id'],s['campaign']);pool=pool_of(cell);runtime=pilot_context.select(source,original.get('runtime_control'))
    close=load(pinned(s['failed_attempt_close']))
    if (close.get('outcome')!='failed' or close.get('performance_started') is not False or
            close.get('cell_id')!=cell['id'] or close.get('plan_sha256')!=old['plan_sha256']):
        raise ValueError('Retained original plan-bound before-work failure required')
    with suite_state.transaction(source/'state.sqlite') as db:
        row=db.execute('SELECT status,result FROM cells WHERE id=?',(cell['id'],)).fetchone()
        if row[0]!='failed' or json.loads(row[1])!=s['failed_attempt_close']:
            raise ValueError('Old failed attempt must be closed without reset with this exact proof')
    out=Path(s['control']);q=out/'qualification';oldq=Path(original['qualification']);arm=cell['arm_id']
    with resource_guard(source,cell,old):
        if active_conflicts(source,cell,old):raise ValueError('Original physical pool is reserved')
        out.mkdir(parents=True,exist_ok=False);(out/'suite').mkdir();write_new(out/'suite/plan.json',plan);write_new(out/'rebind-request.json',s)
        (out/'provenance').mkdir()
        for name in ('build-manifest.json','input-manifest.json','inventory.json'):shutil.copyfile(source/'provenance'/name,out/'provenance'/name)
        for name in ('fleet.json','allocation.json','ssh.conf'):shutil.copyfile(runtime/name,out/name)
        (out/'probes').symlink_to(runtime/'probes',target_is_directory=True)
        (out/'runner-locks').symlink_to(source/'runner-locks',target_is_directory=True)
        # These are inherited actual probes at their original times, not new probes.
        write_new(out/'allocation-inheritance.json',dict(passed=True,prepared_runtime=original.get('runtime_control'),
            original_fleet=ref(runtime/'fleet.json'),actual_fleet=ref(out/'fleet.json'),original_allocation=ref(runtime/'allocation.json'),
            shared_physical_locks=str((source/'runner-locks').resolve()),scope='Same physical hosts/disks/slots and unchanged peer bytes. Original probe times and identities retained; no state database copied.'))
        q.mkdir()
        for path in oldq.rglob('*'):
            relative=path.relative_to(oldq)
            if path.is_dir():continue
            if relative.as_posix() in (arm+'/binding.json','assets/'+arm+'/asset.json'):continue
            if path.is_symlink():raise ValueError('Unexpected qualification symlink')
            target=q/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        proof=dict(schema='flat-condition-rebind-v1',passed=True,prepared_plan=ref(source/'suite/plan.json'),execution_plan=ref(out/'suite/plan.json'),
            prepared_request=s['original_request'],failed_attempt=s['failed_attempt_close'],prepared_asset=ref(oldq/'assets'/arm/'asset.json'),
            original_cell=next(c for c in old['cells'] if c['id']==cell['id']),execution_cell=cell,full_cell_equal=True,
            original_metric_contract=ref(oldq/arm/'metrics.json'),original_workload_contract=ref(oldq/arm/'workload.json'),
            unchanged_metric_contract=ref(q/arm/'metrics.json'),unchanged_workload_contract=ref(q/arm/'workload.json'),
            original_calibration=s['overhead'],source_pins=[s['original_request'],s['failed_attempt_close'],s['overhead'],ref(__file__)],
            scope='New declared attempt; same complete cell/binaries/configuration/input/work and sampler. Original preparation and calibration remain historical facts, with their original plan and times. No producer, import or calibration rerun.')
        for key in ('metric','workload'):
            if proof['original_'+key+'_contract']['sha256']!=proof['unchanged_'+key+'_contract']['sha256']:raise ValueError('Calibration contract changed')
        write_new(out/'condition-rebind.json',proof)
        binding=load(oldq/arm/'binding.json');oldasset=load(oldq/'assets'/arm/'asset.json');native_plan=Path(s['remote_plan']);native_asset=Path(s['remote_asset'])
        if not str(native_asset).startswith('/srv/ax-client/a/') or not str(native_plan).startswith('/srv/ax-client/plans/'):raise ValueError('Dedicated new client plan and asset-view paths required')
        payload=dict(old_asset=binding['asset']+'/asset.json',old_asset_sha=digest(oldq/'assets'/arm/'asset.json'),old_plan_sha=digest(source/'suite/plan.json'),
            new_plan=str(native_plan),plan_bytes=base64.b64encode((out/'suite/plan.json').read_bytes()).decode(),asset=str(native_asset),proof=proof)
        code=r'''import pathlib,json,hashlib,base64
s=PAYLOAD;p=pathlib.Path(s['old_asset']);raw=p.read_bytes();old=json.loads(raw)
if hashlib.sha256(raw).hexdigest()!=s['old_asset_sha'] or old['plan']['sha256']!=s['old_plan_sha']:raise ValueError('Original prepared base changed')
newplan=pathlib.Path(s['new_plan']);newplan.parent.mkdir(parents=True,exist_ok=False);newplan.write_bytes(base64.b64decode(s['plan_bytes']))
directory=pathlib.Path(s['asset']);directory.mkdir(parents=True,exist_ok=False);(directory/'mnt').mkdir()
def ref(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
proof=directory/'execution-rebind.json';proof.write_text(json.dumps(s['proof'],indent=2)+'\n')
value=dict(old,prepared_plan=old['plan'],prepared_asset=ref(p),plan=ref(newplan),directory=str(directory),mount_point=str(directory/'mnt'),execution_rebind=ref(proof))
asset=directory/'asset.json';asset.write_text(json.dumps(value,indent=2)+'\n')
if p.read_bytes()!=raw:raise ValueError('Original asset changed')
print(json.dumps(dict(asset=ref(asset),value=value,original_unchanged=True)))
'''.replace('PAYLOAD',repr(payload))
        response=json.loads(command(out,'asset-view',['ssh','-F',runtime/'ssh.conf',alias(pool,'client'),'python3 -'],code.encode()))
        wanted=asset_view(oldasset,response['value']['prepared_asset'],response['value']['plan'],str(native_asset),response['value']['execution_rebind'])
        if response['value']!=wanted or response.get('original_unchanged') is not True:raise ValueError('New view changed old base facts')
        assetpath=q/'assets'/arm/'asset.json';assetpath.parent.mkdir(parents=True,exist_ok=True);write_new(assetpath,response['value'])
        if digest(assetpath)!=response['asset']['sha256']:raise ValueError('Collected execution asset bytes differ')
        binding.update(asset=str(native_asset),asset_manifest=ref(assetpath),prepared_binding=ref(oldq/arm/'binding.json'),execution_rebind=ref(out/'condition-rebind.json'))
        write_new(q/arm/'binding.json',binding)
        previous=load(pinned(original['cross_pool_qualification']))
        if previous.get('passed') is not True or previous['plan_sha256']!=old['plan_sha256'] or previous['inventory_sha256']!=digest(runtime/'fleet.json'):raise ValueError('Original actual cross-pool proof differs')
        cross=deepcopy(previous);cross.update(plan_sha256=plan['plan_sha256'],prepared_proof=original['cross_pool_qualification'],execution_rebind=ref(out/'condition-rebind.json'))
        cross['source_pins']+=[original['cross_pool_qualification'],ref(out/'condition-rebind.json')]
        write_new(out/'cross-pool-inheritance.json',cross)
        suite_state.initialize(out/'state.sqlite',plan)
        write_new(out/'prepared.json',dict(passed=True,cell_id=cell['id'],execution_plan=ref(out/'suite/plan.json'),condition_rebind=ref(out/'condition-rebind.json'),
            execution_asset=ref(assetpath),original_request=s['original_request'],overhead=s['overhead'],run=s['run'],source_tool=ref(__file__),
            scope='Explicit new campaign, fresh empty state. Original failed state unchanged; no accepted samples transferred.'))
        print(json.dumps(dict(prepared=True,control=str(out),cell_id=cell['id'],receipt=ref(out/'prepared.json'))))


def formal(request,sha):
    s=load(pinned(dict(path=str(request),sha256=sha)))
    if s.get('schema')!='rebound-flat-formal-v1' or s.get('reviewed') is not True:raise ValueError('Reviewed explicit new formal attempt required')
    prepared=load(pinned(s['prepared']));control=Path(s['control']);r=load(control/'rebind-request.json');old=load(pinned(prepared['original_request']))
    plan=load(control/'suite/plan.json');cell=next(c for c in plan['cells'] if c['id']==prepared['cell_id']);pool=pool_of(cell);q=control/'qualification';arm=cell['arm_id']
    if prepared.get('passed') is not True or prepared['execution_plan']!=ref(control/'suite/plan.json'):raise ValueError('Exact new campaign preparation required')
    latest_path=pinned(s['latest']);latest=load(latest_path);age=(datetime.now(timezone.utc)-datetime.fromisoformat(latest['at'])).total_seconds()
    if latest.get('passed') is not True or latest['build_manifest_sha256']!=plan['config']['build_manifest_sha256'] or not 0<=age<=900:raise ValueError('Fresh actual latest-branch check required')
    run=pinned(prepared['run']);admit=pinned(old['tools']['admit']);overhead=pinned(prepared['overhead'])
    if load(overhead).get('passed') is not True:raise ValueError('Original six calibrations must have passed the corrected checker')
    out=Path(s['out']);cellout=control/'cells'/cell['id']
    with resource_guard(control,cell,plan):
        if active_conflicts(control,cell,plan):raise ValueError('Physical pool reserved')
        with suite_state.transaction(control/'state.sqlite') as db:
            if db.execute('SELECT status FROM cells WHERE id=?',(cell['id'],)).fetchone()[0]!='pending' or db.execute('SELECT 1 FROM stages WHERE cell_id=?',(cell['id'],)).fetchone():raise ValueError('Only new unstarted formal attempt supported')
        out.mkdir(parents=True,exist_ok=False);write_new(out/'started.json',dict(at=datetime.now(timezone.utc).isoformat(),request=ref(request),prepared=s['prepared'],latest=s['latest'],automatic_retry=False))
        try:
            for role in ('client','metadata','objects'):
                node=fleet_node(load(control/'fleet.json'),pool,role)
                code="import pathlib,hashlib,json;p=pathlib.Path("+repr(node['repo']+'/experiments/unified-suite/pilot-aliyun/scripts/'+old['observer_tool']['name'])+");v={'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'observer_sha256':hashlib.sha256(p.read_bytes()).hexdigest()};"
                if role=='client':code+="v['slot']=pathlib.Path('/sys/fs/cgroup/ax-slot-1/cgroup.procs').read_text();v['node_sha256']=hashlib.sha256(pathlib.Path("+repr(old['node_tool']['path'])+").read_bytes()).hexdigest();"
                code+='print(json.dumps(v))'
                value=json.loads(command(out,'preflight-'+role,['ssh','-F',control/'ssh.conf',alias(pool,role),shlex.join(['python3','-c',code])]))
                if value['boot_id']!=load(control/'probes'/alias(pool,role)/'probe.json')['boot_id'] or value['observer_sha256']!=old['observer_tool']['sha256']:raise ValueError('Inherited actual hardware or observer changed')
                if role=='client' and (value['slot'].strip() or value['node_sha256']!=old['node_tool']['sha256']):raise ValueError('Slot busy or original worker helper changed')
            collector=old['final_collector_name']
            if s.get('replacement_collector'):
                collector=collector_probe(out,control,pool,old,s['replacement_collector'])
            argv=['python3',admit,'--control',control,'--qualification',q,'--overhead',overhead,'--cell',cell['id'],
                '--node-tool',old['node_tool']['path'],'--node-tool-sha256',old['node_tool']['sha256'],'--observer-tool-name',old['observer_tool']['name'],
                '--observer-tool-sha256',old['observer_tool']['sha256'],'--collector-name',collector,'--latest-check',latest_path,
                '--cross-pool-qualification',control/'cross-pool-inheritance.json','--owner',old['owner'],'--out',cellout]
            command(out,'admit',argv);command(out,'formal',['python3',run,'--context',cellout/'context.json'])
            write_new(out/'result.json',dict(passed=True,cell_id=cell['id'],closing=ref(control/'cells'/(cell['id']+'.close.json')),prepared=s['prepared'],automatic_retry=False))
        except Exception as error:
            write_new(out/'failed.json',dict(passed=False,error=str(error),automatic_retry=False));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for action in ('prepare','formal'):
        child=sub.add_parser(action);child.add_argument('--request',type=Path,required=True)
        if action=='formal':child.add_argument('--sha256',required=True)
    a=p.parse_args();prepare(a.request) if a.action=='prepare' else formal(a.request,a.sha256)
