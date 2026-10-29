#!/usr/bin/env python3
"""Plan 64/128-agent campaigns and placement budgets; no provision or execution."""
import argparse
from copy import deepcopy
import hashlib
import itertools
import json
import math
import random
from experiment_io import write_new

PROTOCOL='SCALE-20261029-v1'
N=[1,8,16,32,64,128]
FS=['native-overlay','juicefs']
LAYOUTS=['default','enabled-64KiB','enabled-256KiB','enabled-1MiB','enabled-4MiB']


def groups():
    def g(owner,name,methods,axes,kind,extra=False):
        return dict(cluster=owner,name=name,methods=methods,axes=axes,kind=kind,extension_only=extra)
    readonly={'workload':['discovery-stat','shuffled-read'],'cache':['client-cold','same-readset-warm']}
    reads={'request':['full-GET','random-4KiB-Range'],'cache':['verified-cold','warm']}
    moves=[{'agents':n,'moved_agents':k} for n,ks in [(1,[1]),(16,[1,8,16]),(64,[8,32,64]),(128,[16,64,128])] for k in ks]
    return [
        g('C1','trace-weak',FS,{'agents':N,'tasks_per_agent':[1]},'trace'),
        g('C1','trace-fixed-queue',FS,{'agents':N,'total_tasks':[256]},'trace'),
        g('C1','overlay-scale-ablation',['native','native-overlay'],{'agents':[1,32,64],'tasks_per_agent':[1]},'trace'),
        g('C1','live-agent',FS,{'agents':[1,8,32,64]},'live'),
        g('C2','readonly-scale',['packed','native-tikv-flat-v1'],dict(agents=N,files=[8388608],**readonly),'readonly'),
        g('C2','readonly-corpus-growth',['packed','native-tikv-flat-v1'],dict(agents=[64],files=[1048576,33554432],**readonly),'readonly'),
        g('C3','layout-load',LAYOUTS,dict(requests_in_flight=[1,16,64,128],object_bytes=[8388731],pool_bytes=[128*1024**3],**reads),'object'),
        g('C3','layout-large-objects',LAYOUTS,dict(requests_in_flight=[64],object_bytes=[64*1024**2+123,256*1024**2+123],pool_bytes=[128*1024**3],**reads),'object',True),
        g('C3','layout-large-pool',LAYOUTS,dict(requests_in_flight=[64],object_bytes=[8388731],pool_bytes=[1024**4],**reads),'object',True),
        g('C4','fork-fanout',['brewfs-fork','juicefs-clone','native-full-copy'],
          {'dataset':['1M-4KiB','mixed-400GiB'],'children':[1,8,64,128],'creation':['serial','burst']},'fork'),
        g('C4','fleet-handoff',['native-overlay-stay','native-overlay-move','juicefs-stay','juicefs-move'],
          {'handoff':moves},'handoff'),
    ]


def topology(cluster,agents,isolation):
    if cluster=='C3':
        return {'load_unit':'HTTP request, not Agent','requester_nodes':2,'backend_nodes':1,'coordinator_nodes':1,
                'node_count':4,'physical_host_count':None,'agent_environments':0}
    spare=agents if cluster=='C4' else 0
    # Spare placement is reserved even for the matched stay arm.
    executors=math.ceil(agents/8)+math.ceil(spare/8) if isolation=='docker' else agents+spare
    return {'active_agents':agents,'standby_environments':spare,'agent_environments':agents+spare,
            'isolation':isolation,'agents_per_executor_node':8 if isolation=='docker' else 1,
            'executor_nodes':executors,'backend_nodes':1,'metadata_nodes':1,'coordinator_nodes':1,
            'node_count':executors+3,'physical_host_count':None,
            'executor_node_reference':{'vcpus':32,'memory_gib':128} if isolation=='docker' else {'vcpus':4,'memory_gib':16},
            'per_agent_cgroup':{'vcpus':2,'memory_gib':8},
            'backend_reference':{'vcpus':32,'memory_gib':128,'independent_data_disks':4,'base_disk_gib':1024},
            'metadata_reference':{'vcpus':16,'memory_gib':64},
            'coordinator_reference':{'vcpus':8,'memory_gib':32},
            'note':'node_count counts allocated Linux nodes/VMs, not proven physical hosts; C4 fork capacity is checked separately'}


def placement(agents,isolation,migration=False):
    rows=[]
    for pool in ['source']+(['target'] if migration else []):
        for i in range(agents):
            rows.append({'logical_agent_id':f'a{i+1:03}','environment_id':f'{pool}-a{i+1:03}',
                         'executor_node':f'{pool}-{i//8+1:03}' if isolation=='docker' else f'{pool}-vm-{i+1:03}',
                         'namespace':f'agent-{i+1:03}','pool':pool,'state':'planned',
                         'actual_host_id':None,'physical_host_id':None,'mount_namespace_verified':False})
    return rows


def build(profile='main',isolation='docker',provider='provided',cluster=None,seed=42,include_llm_simulation=False):
    if profile not in ('main','expanded') or isolation not in ('docker','vm') or provider not in ('provided','aliyun'):
        raise ValueError('invalid profile/isolation/provider')
    if cluster is not None and cluster not in ('C1','C2','C3','C4'):raise ValueError('unknown cluster')
    cells=[]
    common=['frozen-code-input-and-placement','all-ready-before-one-barrier','private-agent-workspace-home-xdg',
            'shared-backend-runtime-verified','actual-cgroups-and-cache-accounting','all-agent-result-retention']
    for g in groups():
        if cluster and g['cluster']!=cluster:continue
        for values in itertools.product(*g['axes'].values()):
            params=dict(zip(g['axes'],values)); agents=params.get('agents',params.get('handoff',{}).get('agents',0))
            extension=(g['extension_only'] or agents>64 or params.get('children',0)>64
                       or params.get('requests_in_flight',0)>64 or params.get('files',0)>8388608)
            if extension and profile=='main':continue
            modes=[False,True] if include_llm_simulation and g['kind']=='trace' and agents>1 else [False]
            for simulate in modes:
                condition=dict(params,simulate_llm=simulate)
                key=hashlib.sha256(json.dumps([PROTOCOL,provider,isolation,g['name'],condition],sort_keys=True).encode()).hexdigest()[:16]
                for block in (1,2,3):
                    methods=list(g['methods']);random.Random(f'{seed}:{key}:{block}').shuffle(methods)
                    for order,method in enumerate(methods,1):
                        gates=list(common)
                        if g['kind']=='trace':gates+=['complete-trace-bytes-and-final-state','frozen-balanced-task-assignment']
                        if simulate:gates+=['reviewed-tool-turn-boundaries','explicit-fixed-total-think-ms-per-task','same-plan-between-baselines']
                        if g['kind']=='live':gates+=['frozen-real-benchmark-and-model','LLM-quota-and-task-budget','native-session-and-independent-scorer']
                        if g['kind']=='readonly':gates+=['distinct-real-corpus-inventory','frozen-ML-task-readset','latency-histogram']
                        if g['kind']=='object':gates+=['actual-four-flags-and-B-q','external-layout','parallel-request-runner-and-layered-counters']
                        if g['kind']=='fork':gates+=['all-baseline-physical-capacity','native-fork-clone-isolation','burst-runner']
                        if g['kind']=='handoff':gates+=['same-checkpoint-stay-move','native-session-restore','all-source-writers-released','target-on-different-host']
                        cell={'id':f'{key}-b{block}-{method}','paired_block':f'{key}-b{block}',
                              'cluster_id':g['cluster'],'owner_agent':g['cluster'].lower()+'-owner','group':g['name'],
                              'load_kind':g['kind'],'method':method,'order_in_block':order,'params':deepcopy(condition),
                              'tier':'extension' if extension else 'primary','requires':gates,
                              'status':'not_run','execution_enabled':False,
                              'runner_status':'planning only; distributed barrier, telemetry and real service qualification pending'}
                        if simulate:cell['think_budget_ms_per_task']=None
                        if g['kind']=='fork':
                            source_bytes=1048576*4096 if params['dataset']=='1M-4KiB' else 400*1024**3
                            logical=source_bytes*(params['children']+1)
                            cell['capacity_floor']={'full_copy_source_plus_children_bytes':logical,
                                'EC2_plus2_with_25pct_headroom_bytes':math.ceil(logical*2.5),
                                'applies_to_all_paired_arms':True,'measured_metadata_and_temporary_bytes_additional':True}
                        cells.append(cell)
    clusters=sorted({c['cluster_id'] for c in cells}); n=64 if profile=='main' else 128
    topologies={c:topology(c,n,isolation) for c in clusters}
    return {'protocol':PROTOCOL,'profile':profile,'provider':provider,'isolation':isolation,'seed':seed,
            'execution_enabled':False,'provisioning_enabled':False,'agent_count_is_not_physical_host_count':True,
            'include_llm_simulation':include_llm_simulation,'simulation_default':False,
            'paired_blocks':3,'total_cells':len(cells),'clusters':topologies,
            'parallel_node_count':sum(t['node_count'] for t in topologies.values()),
            'placement_templates':{c:placement(n,isolation,c=='C4') for c in clusters if c!='C3'},
            'group_counts':{g['name']:sum(c['group']==g['name'] for c in cells) for g in groups() if any(c['group']==g['name'] for c in cells)},
            'cells':cells,'note':'One filesystem at a time per shared service pool; extra parallel baseline pools must be allocated separately. No automatic dispatcher.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',choices=['main','expanded'],default='main')
    p.add_argument('--isolation',choices=['docker','vm'],required=True)
    p.add_argument('--provider',choices=['provided','aliyun'],required=True)
    p.add_argument('--cluster',choices=['C1','C2','C3','C4']);p.add_argument('--seed',type=int,default=42)
    p.add_argument('--include-llm-simulation',action='store_true',help='also plan paired paced multi-trace conditions; fixed budget/boundary qualification still required')
    p.add_argument('--output',required=True);a=p.parse_args()
    result=build(a.profile,a.isolation,a.provider,a.cluster,a.seed,a.include_llm_simulation)
    write_new(a.output,result)
    print(json.dumps({'cells':result['total_cells'],'nodes':result['parallel_node_count'],'execution_enabled':False}))
