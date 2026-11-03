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

PROTOCOL='SCALE-20261029-v2'
WRITEBACK_PROTOCOL='WRITEBACK-20261029-v1'
WRITE_POLICIES=['upload_before_commit','commit_before_upload']
N=[1,8,16,32,64,128]
FS=['native-overlay','juicefs']
LAYOUTS=['default','enabled-64KiB','enabled-256KiB','enabled-1MiB','enabled-4MiB']
DEPLOYMENT_CAMPAIGN='ecs-20261101'
BACKEND_POLICY={'repository':'https://github.com/Ivanbeethoven/rustfs',
                'branch':'feat/dynamic-block-size-rebased','revision_policy':'branch-latest',
                'layout_profile':'enabled','brewfs_hint':'auto'}


def ec22_capacity_floor(logical_bytes,physical_overhead_bytes=0):
    """Raw bytes with 25% of total capacity free; overhead is already physical."""
    if any(type(v) is not int or v<0 for v in (logical_bytes,physical_overhead_bytes)):
        raise ValueError('capacity inputs must be nonnegative integer bytes')
    return ((2*logical_bytes+physical_overhead_bytes)*4+2)//3


def hardware_reference(isolation):
    # Planning targets, not a claim that allocated machines passed qualification.
    executor={'vcpus':32,'memory_gib':128,'input_disk_gib':1024,'scratch_disk_gib':1024}
    if isolation=='vm':executor={'vcpus':4,'memory_gib':16,'input_disk_gib':256,'scratch_disk_gib':128}
    return {
        'executor_node_reference':executor,
        'backend_reference':{'vcpus':32,'memory_gib':128,'independent_data_disks':4,'base_disk_gib':1024},
        'metadata_reference':{'vcpus':16,'memory_gib':64,'data_disk_gib':1024},
        'coordinator_reference':{'vcpus':8,'memory_gib':32,'result_disk_gib':2048},
        'hardware_verified':False,'minimum_free_fraction':0.25,'system_disk_gib_per_node':100,
        'shared_nodes_private_network_target_gbps':25,
        'hardware_contract':'experiments/retest-2026-10-29/HARDWARE.zh.md',
        'required_hardware_evidence':['actual-role-cpu-memory-cgroups-and-host-placement',
            'per-volume-physical-bytes-and-inodes-including-temporary-retention',
            'measured-input-io-parser-memory-and-observer-headroom',
            'measured-private-network-and-provider-disk-instance-limits']}


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


def writeback_groups():
    """Bounded paired study, not a multiplier over the baseline matrix."""
    def g(owner,name,methods,axes,kind):
        return dict(cluster=owner,name=name,methods=methods,axes=axes,kind=kind,extension_only=False)
    return [
        g('C1','writeback-trace',['native','native-overlay','juicefs'],
          {'trace_case':['django','ripgrep-20261026','archive-io-20261026'],
           'agents':[1,16,64],'tasks_per_agent':[1]},'trace'),
        g('C1','writeback-queue',FS,
          {'agents':[16,64],'total_tasks':[256],
           'queue_recipe':['alternate-ripgrep-archive-128-each']},'trace'),
        g('C4','writeback-handoff',
          ['native-overlay-stay','native-overlay-move','juicefs-stay','juicefs-move'],
          {'trace_case':['archive-io-20261026'],
           'handoff':[{'agents':1,'moved_agents':1},{'agents':64,'moved_agents':8},
                      {'agents':64,'moved_agents':64}]},'handoff'),
        g('C4','writeback-copy-readiness',['brewfs-fork','juicefs-clone','native-full-copy'],
          {'dataset':['mixed-400GiB'],'children':[8],'creation':['burst']},'fork'),
    ]


def topology(cluster,agents,isolation):
    refs=hardware_reference(isolation)
    if cluster=='C3':
        refs.pop('executor_node_reference');refs.pop('metadata_reference')
        refs['requester_reference']={'vcpus':32,'memory_gib':128,'scratch_disk_gib':1024}
        return {'load_unit':'HTTP request, not Agent','requester_nodes':2,'backend_nodes':1,'coordinator_nodes':1,
                'node_count':4,'physical_host_count':None,'agent_environments':0,**refs}
    spare=agents if cluster=='C4' else 0
    # Spare placement is reserved even for the matched stay arm.
    executors=math.ceil(agents/8)+math.ceil(spare/8) if isolation=='docker' else agents+spare
    return {'active_agents':agents,'standby_environments':spare,'agent_environments':agents+spare,
            'isolation':isolation,'agents_per_executor_node':8 if isolation=='docker' else 1,
            'executor_nodes':executors,'backend_nodes':1,'metadata_nodes':1,'coordinator_nodes':1,
            'node_count':executors+3,'physical_host_count':None,
            'per_agent_cgroup':{'vcpus':2,'memory_gib':8},
            **refs,
            'note':'node_count counts allocated Linux nodes/VMs, not proven physical hosts; C4 fork capacity is checked separately'}


def reference_compute(topologies):
    total={'vcpus':0,'memory_gib':0}
    for t in topologies.values():
        for role,ref in [('executor','executor_node'),('requester','requester'),('backend','backend'),
                         ('metadata','metadata'),('coordinator','coordinator')]:
            for resource in total:
                total[resource]+=t.get(role+'_nodes',0)*t.get(ref+'_reference',{}).get(resource,0)
    return total


def placement(agents,isolation,migration=False):
    rows=[]
    for pool in ['source']+(['target'] if migration else []):
        for i in range(agents):
            rows.append({'logical_agent_id':f'a{i+1:03}','environment_id':f'{pool}-a{i+1:03}',
                         'executor_node':f'{pool}-{i//8+1:03}' if isolation=='docker' else f'{pool}-vm-{i+1:03}',
                         'namespace':f'agent-{i+1:03}','pool':pool,'state':'planned',
                         'actual_host_id':None,'physical_host_id':None,'mount_namespace_verified':False})
    return rows


def build(profile='main',isolation='docker',provider='provided',cluster=None,seed=42,include_llm_simulation=False,study='baseline',campaign='legacy-20261029'):
    if campaign not in ('legacy-20261029',DEPLOYMENT_CAMPAIGN):raise ValueError('unknown campaign')
    modern=campaign==DEPLOYMENT_CAMPAIGN
    if profile not in ('main','expanded','pilot','c1-8','pilot-8') or isolation not in ('docker','vm') or provider not in ('provided','aliyun','ssh'):
        raise ValueError('invalid profile/isolation/provider')
    if provider=='ssh' and not modern:raise ValueError('SSH matrices use ecs-20261101 methods')
    small=profile in ('c1-8','pilot-8')
    pilot=profile in ('pilot','pilot-8')
    if small and (not modern or cluster!='C1' or study!='baseline' or isolation!='docker'):
        raise ValueError('eight-Agent profile requires ecs-20261101 methods, C1 baseline and container isolation')
    if profile=='pilot' and (not modern or cluster!='C1' or study!='baseline'):
        raise ValueError('pilot requires ecs-20261101, C1, baseline')
    if modern and include_llm_simulation:
        raise ValueError('new campaign defaults pacing off; prepare the separate paired pacing contract before generating it')
    if cluster is not None and cluster not in ('C1','C2','C3','C4'):raise ValueError('unknown cluster')
    if study not in ('baseline','writeback'):raise ValueError('unknown study')
    writeback=study=='writeback'
    if writeback and (profile!='main' or cluster in ('C2','C3')):
        raise ValueError('writeback study is limited to main profile C1/C4; no automatic expanded matrix')
    selected_groups=writeback_groups() if writeback else deployment_groups(profile) if modern else groups()
    protocol=WRITEBACK_PROTOCOL if writeback else PROTOCOL
    if modern:protocol='C1-C4-20261101-dynamic-on-v2-'+study
    cells=[]
    common=['frozen-code-input-and-placement','all-ready-before-one-barrier','private-agent-workspace-home-xdg',
            'shared-backend-runtime-verified','actual-cgroups-and-cache-accounting','all-agent-result-retention',
            'role-hardware-and-physical-capacity-verified','load-input-and-observer-headroom-verified']
    for g in selected_groups:
        if cluster and g['cluster']!=cluster:continue
        for values in itertools.product(*g['axes'].values()):
            params=dict(zip(g['axes'],values)); agents=params.get('agents',params.get('handoff',{}).get('agents',0))
            extension=(g['extension_only'] or agents>64 or params.get('children',0)>64
                       or params.get('requests_in_flight',0)>64 or params.get('files',0)>8388608)
            if extension and profile=='main':continue
            can_simulate=g['kind']=='trace' and agents>1
            if writeback:
                can_simulate=(g['name']=='writeback-trace' and agents==64
                              and params['trace_case']=='archive-io-20261026')
            modes=[False,True] if include_llm_simulation and can_simulate else [False]
            for simulate in modes:
                condition=dict(params,simulate_llm=simulate)
                identity=[protocol,provider,isolation,g['name'],condition]
                if modern:identity.append(profile)  # expanded C2/C4 storage budgets differ
                key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:16]
                for block in ((1,) if pilot else (1,2,3)):
                    methods=FS if writeback and simulate else g['methods']
                    arms=list(itertools.product(methods,WRITE_POLICIES if writeback else [None]))
                    random.Random(f'{seed}:{key}:{block}').shuffle(arms)
                    for order,(method,policy) in enumerate(arms,1):
                        gates=list(common)
                        if g['kind']=='trace':gates+=['complete-trace-bytes-and-final-state','frozen-balanced-task-assignment']
                        if simulate:gates+=['reviewed-tool-turn-boundaries','explicit-fixed-total-think-ms-per-task','same-plan-between-baselines']
                        if g['kind']=='live':gates+=['frozen-real-benchmark-and-model','LLM-quota-and-task-budget','native-session-and-independent-scorer']
                        if g['kind']=='readonly':gates+=['distinct-real-corpus-inventory','frozen-ML-task-readset','latency-histogram']
                        if g['kind']=='object':gates+=['actual-four-flags-and-B-q','external-layout','parallel-request-runner-and-layered-counters']
                        if g['kind']=='fork':gates+=['all-baseline-physical-capacity','native-fork-clone-isolation','burst-runner']
                        if g['kind']=='handoff':gates+=['same-checkpoint-stay-move','native-session-restore','all-source-writers-released','target-on-different-host']
                        if writeback:
                            gates+=['same-build-and-volume-format-between-write-policies',
                                    'effective-writeback-mode-and-staging-budget-verified',
                                    'local-staging-failure-and-recovery-qualified',
                                    'scoped-remote-drain-and-independent-read',
                                    'application-drain-and-remote-confirmation-timestamps']
                            if g['kind']=='handoff':gates+=['frozen-post-write-tool-checkpoint','resumable-trace-checkpoint-state','neighbor-writers-continue-outside-drain-scope']
                            if g['kind']=='fork':gates+=['source-remotely-ready-before-fork','same-remote-ready-boundary-including-full-copy']
                            if g['name']=='writeback-queue':gates+=['frozen-256-task-assignment','per-task-staging-cleanup-only-after-remote-ready']
                        suffix=f'-{policy}' if writeback else ''
                        cell={'id':f'{key}-b{block}-{method}{suffix}','paired_block':f'{key}-b{block}',
                              'cluster_id':g['cluster'],'owner_agent':g['cluster'].lower()+'-owner','group':g['name'],
                              'load_kind':g['kind'],'method':method,'order_in_block':order,'params':deepcopy(condition),
                              'tier':'extension' if extension else 'primary','requires':gates,
                              'status':'not_run','execution_enabled':False,
                              'runner_status':'runtime adapters implemented; real service and telemetry qualification required before release'}
                        if writeback:
                            cell['params']['write_policy']=policy
                            cell['writeback_qualification_verified']=False
                            if method.startswith('juicefs'):
                                cell['required_effective_config']={'writeback':policy=='commit_before_upload'}
                                if policy=='commit_before_upload':cell['required_effective_config']['upload_delay_seconds']=0
                            else:cell['required_effective_config']={'cache.writeback_mode':policy}
                            if g['kind']=='handoff':cell['checkpoint_manifest_sha256']=None
                        if simulate:cell['think_budget_ms_per_task']=None
                        if g['kind']=='fork':
                            source_bytes=1048576*4096 if params['dataset']=='1M-4KiB' else 400*1024**3
                            logical=source_bytes*(params['children']+1)
                            cell['capacity_floor']={'full_copy_source_plus_children_bytes':logical,
                                'raw_data_only_with_25pct_free_bytes':ec22_capacity_floor(logical),
                                'minimum_free_fraction':0.25,
                                'fits_4TiB_backend_data_only':ec22_capacity_floor(logical)<=4*1024**4,
                                'complete_budget_verified':False,
                                'applies_to_all_paired_arms':True,'measured_metadata_and_temporary_bytes_additional':True}
                        cells.append(cell)
    clusters=sorted({c['cluster_id'] for c in cells}); n=8 if small else 16 if pilot else 64 if profile=='main' else 128
    topologies={c:topology(c,n,isolation) for c in clusters}
    result={'protocol':protocol,'profile':profile,'provider':provider,'isolation':isolation,'seed':seed,
            'campaign':campaign,
            'study':study,
            'execution_enabled':False,'provisioning_enabled':False,'agent_count_is_not_physical_host_count':True,
            'include_llm_simulation':include_llm_simulation,'simulation_default':False,
            'paired_blocks':1 if pilot else 3,'total_cells':len(cells),'clusters':topologies,
            'parallel_node_count':sum(t['node_count'] for t in topologies.values()),
            'parallel_reference_compute':reference_compute(topologies),
            'placement_templates':{c:placement(n,isolation,c=='C4') for c in clusters if c!='C3'},
            'group_counts':{g['name']:sum(c['group']==g['name'] for c in cells) for g in selected_groups if any(c['group']==g['name'] for c in cells)},
            'cells':cells,'note':'One filesystem at a time per shared service pool; extra parallel baseline pools must be allocated separately. No automatic dispatcher.'}
    if modern:attach_deployment(result,n)
    if provider=='ssh':
        # Keep the experiment axes, not a cloud allocation or assumed host count.
        result['clusters']={c:{k:v for k,v in t.items() if k in ('active_agents','standby_environments','per_agent_cgroup','metadata_backend')}
                            for c,t in result['clusters'].items()}
        result['resource_requirements']=[]
        result['parallel_node_count']=None
        result['parallel_reference_compute']=None
        result['provider_contract']={'transport':'ssh','allocation':'existing environments',
                                    'allow_host_deletion':False,'allow_device_format':False}
        for rows in result['placement_templates'].values():
            for row in rows:row['executor_node']=None
        for step in result['steps']:
            if step['id'] in ('02-allocation','03-host'):
                step['entrypoint']='bench/runtime/fleet.py'
                step['state']='fill SSH slots; probe endpoints; qualify shared-host capacity'
            if step['id']=='05-backend':
                step['entrypoint']='experiments/retest-2026-10-29/runbook/DEPLOYMENT.zh.md'
                step['state']='qualify supplied service endpoints, versions, layout and native metrics'
        result['note']='SSH workload matrix; fill a v3 fleet and explicit slots for the available environments.'
    return result


def deployment_groups(profile):
    """Use the existing C2-C4 design; specialize only the reviewed C1 matrix."""
    def group(name,methods,axes):
        return dict(cluster='C1',name=name,methods=methods,axes=axes,kind='trace',extension_only=False)
    if profile in ('pilot','pilot-8'):
        return [group('pilot',FS,{'trace_case':['pytest-full-02'],'agents':[1,8] if profile=='pilot-8' else [1,8,16],'tasks_per_agent':[1]})]
    traces=['django','ripgrep-20261026','archive-io-20261026']
    if profile=='c1-8':
        return [
            group('trace-weak',FS,{'trace_case':traces,'agents':[1,4,8],'tasks_per_agent':[1]}),
            group('trace-coverage',FS,{'trace_case':['sklearn','sympy','pytest-full-02','pytest-20261026'],'agents':[1],'tasks_per_agent':[1]}),
            group('trace-fixed-queue',FS,{'agents':[8],'total_tasks':[256],'queue_recipe':['alternate-ripgrep-archive-128-each']}),
            group('overlay-scale-ablation',['native','native-overlay'],{'trace_case':traces,'agents':[1,8],'tasks_per_agent':[1]})]
    return [
        group('trace-weak',FS,{'trace_case':traces,'agents':N,'tasks_per_agent':[1]}),
        group('trace-coverage',FS,{'trace_case':['sklearn','sympy','pytest-full-02','pytest-20261026'],'agents':[1],'tasks_per_agent':[1]}),
        group('trace-fixed-queue',FS,{'agents':[16,32,64,128],'total_tasks':[256],'queue_recipe':['alternate-ripgrep-archive-128-each']}),
        group('overlay-scale-ablation',['native','native-overlay'],{'trace_case':traces,'agents':[1,32,64],'tasks_per_agent':[1]}),
        *[g for g in groups() if g['cluster']!='C1']]


def deployment_roles(cluster,n,isolation,provider,cells):
    """Explicit Linux node targets. These are neither cloud orders nor readiness."""
    def role(name,count,cpu,memory,sku,disks,processes=None):
        return {'role':name,'count':count,'vcpus':cpu,'memory_gib':memory,
                'candidate_ecs_type':sku if provider=='aliyun' else None,
                'data_disks_gib':disks,'system_disk_gib':100,'processes':processes or [],
                'hardware_verified':False,'capacity_verified':False}
    r=[]
    if cluster=='C3':
        r.append(role('requester',2,32,128,'ecs.g8ine.8xlarge',[1024]))
    else:
        count=math.ceil(n/8) if isolation=='docker' else n
        cpu,mem,sku,disks=(32,128,'ecs.g8ine.8xlarge',[1024,1024]) if isolation=='docker' else (4,16,'ecs.g8i.xlarge',[256,128])
        r.append(role('source',count,cpu,mem,sku,disks))
        if cluster=='C4':r.append(role('target',count,cpu,mem,sku,disks))
    backend_disk=1024
    if cluster=='C2':backend_disk=8192 if n>64 else 2048
    if cluster=='C4':
        largest=max([c.get('capacity_floor',{}).get('raw_data_only_with_25pct_free_bytes',0) for c in cells]+[0])
        # Headroom targets above the data-only floor; measured overhead still gates readiness.
        backend_disk=40960 if largest>80*1024**4 else 20480 if largest>12*1024**4 else 3072 if largest>4*1024**4 else 1024
    r.append(role('rustfs',1,64,256,'ecs.g8i.16xlarge',[backend_disk]*4,['rustfs']))
    r[-1]['service_cgroup']={'vcpus':56,'memory_gib':224,'swap':False}
    if cluster=='C2':r.append(role('tikv-pd',3,32,128,'ecs.g8ine.8xlarge',[128,1024],['pd','tikv']))
    elif cluster!='C3':r.append(role('redis',1,32,128,'ecs.g8ine.8xlarge',[1024],['redis']))
    r.append(role('coordinator',1,8,32,'ecs.g8i.2xlarge',[2048]))
    return r


def attach_deployment(result,n):
    provider=result['provider'];isolation=result['isolation'];topologies={};nodes=[]
    for cluster in result['clusters']:
        cells=[c for c in result['cells'] if c['cluster_id']==cluster]
        roles=deployment_roles(cluster,n,isolation,provider,cells)
        if result['profile'] in ('c1-8','pilot-8'):
            source=next(r for r in roles if r['role']=='source')
            source['count']=2
            source['max_agents']=4
        topologies[cluster]={'roles':roles,'node_count':sum(r['count'] for r in roles),
            'hardware_verified':False,'physical_host_count':None,'minimum_free_fraction':.25,
            'active_agents':0 if cluster=='C3' else n,'standby_environments':n if cluster=='C4' else 0,
            'per_agent_cgroup':None if cluster=='C3' else {'vcpus':2,'memory_gib':8,'swap':False},
            'metadata_backend':'tikv' if cluster=='C2' else None if cluster=='C3' else 'redis'}
        for role in roles:
            for index in range(1,role['count']+1):
                nodes.append({'id':f'{cluster.lower()}-{role["role"]}-{index:03}',
                    'cluster_id':cluster,'role':role['role'],'requirements':deepcopy(role),
                    'ssh_alias':None,'private_ip':None,'host_key_verified':False,
                    'resource_id':None,'physical_host_id':None,'hardware_class':None,
                    'work_root':None,'data_mounts':[],
                    'min_free_bytes_per_disk':None,'min_free_inodes_per_disk':None,
                    'owned_by_campaign':None,'allocation_verified':False})
    result['clusters']=topologies
    result['parallel_node_count']=len(nodes)
    result['parallel_reference_compute']={k:sum(node['requirements'][k] for node in nodes) for k in ('vcpus','memory_gib')}
    result['resource_requirements']=nodes
    result['phase']='prepare';result['requires_kubernetes']=False
    result['backend_policy']=deepcopy(BACKEND_POLICY)
    result['provider_contract']={'service_pool_per_cluster':True,'automatic_replacement':False,
        'business_network':'private','public_entry_count_target':1 if provider=='aliyun' else 0,
        'region':None,'zone':None,'cost_ceiling':None,
        'allow_host_deletion':False,'allow_device_format':False,
        'instructions':'explicit allocation scope required; never provision or clean up from this plan'}
    for cluster,rows in result['placement_templates'].items():
        for row in rows:
            index=int(row['logical_agent_id'][1:])-1
            capacity=4 if result['profile'] in ('c1-8','pilot-8') else 8
            row['executor_node']=f'{cluster.lower()}-{row["pool"]}-{index//capacity+1 if isolation=="docker" else index+1:03}'
    result['steps']=[
        {'id':'01-plan','entrypoint':'bench/scale_plan.py','state':'implemented-plan-only'},
        {'id':'02-allocation','entrypoint':'bench/experiment_inventory.py','state':'implemented-template-and-static-check-only'},
        {'id':'03-host','entrypoint':'bench/host_preflight.py','state':'implemented-read-only-host-check'},
        {'id':'04-pins','entrypoint':'infra/rustfs/resolve_backend.py','state':'update-specified-branch-then-verify-latest-built-image'},
        {'id':'05-backend','entrypoint':'infra/rustfs/rustfs_node.py','state':'single-node-step-requires-reviewed-config'},
        {'id':'06-input','entrypoint':'experiments/retest-2026-10-29/runbook/DATA.zh.md','state':'download-and-full-validation-before-timing'},
        {'id':'07-mount','entrypoint':'bench/runtime/prepare.py','state':'native-mount-supervisor-implemented-real-service-qualification-required'},
        {'id':'08-observe','entrypoint':'bench/runtime/observe.py','state':'raw-sampling-and-audit-implemented-native-metrics-and-overhead-qualification-required'},
        {'id':'08-cohort','entrypoint':'bench/runtime/cohort.py','state':'one-reviewed-cohort-no-auto-dispatch'},
        {'id':'08-handoff','entrypoint':'bench/runtime/handoff.py','state':'one-reviewed-checkpoint-native-two-node-qualification-required'},
        {'id':'09-retention','entrypoint':'bench/experiment_step.py','state':'seal-and-verify-before-manual-cleanup'}]
    for cell in result['cells']:
        cell['study']=result['study']
        cell['backend_policy']=deepcopy(BACKEND_POLICY)
        cell['requires']+=['latest-specified-rustfs-branch-at-batch-start','actual-hint-status-and-persisted-B-q']
        if cell['cluster_id']=='C3':
            # The no-hint arm keeps the same enabled server as the fixed-hint arms.
            cell['backend_policy']['brewfs_hint']=None
            cell['backend_policy']['request_hint_bytes']={'default':None,'enabled-64KiB':65536,
                'enabled-256KiB':262144,'enabled-1MiB':1048576,'enabled-4MiB':4194304}[cell['method']]
        elif cell['method'].startswith('juicefs') or cell['method']=='native-full-copy':
            cell['backend_policy']['brewfs_hint']=None
        else:
            cell.setdefault('required_effective_config',{})['data.s3.rustfs_ec_block_size_hint']='auto'
            cell['requires']+=['brewfs-rustfs-hint-feature-compiled','auto-hint-on-new-data-and-lower-producers']
        cell['requires']+=['provider-allocation-and-role-capacity-qualified','native-cli-and-distributed-adapter-qualified']
        cell['requires']+=['layered-native-metrics-contract','paired-observer-overhead-qualified','complete-raw-telemetry-and-phase-coverage','drain-readback-and-retention-sealed']
        if cell['cluster_id']=='C3':
            cell['requires']=[r for r in cell['requires'] if r not in ('private-agent-workspace-home-xdg','all-agent-result-retention')]
            cell['requires']+=['requester-result-retention','global-not-per-requester-concurrency-limit']
        if cell['cluster_id']=='C1':cell['requires']+=['redis-metadata-no-sqlite-fallback','nonempty-native-lower-and-real-fuse-qualified']
        if cell['cluster_id']=='C2':cell['requires']+=['three-pd-three-tikv-runtime-qualified']
        if cell['cluster_id']=='C4':cell['requires']+=['retry-original-identity-no-head-rollback','no-delete-before-drain-unmount-and-lease-release']
    result['note']='Reviewable two-provider preparation; no cloud API, SSH, replay or cleanup. New cohort IDs must not be mixed with legacy results.'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',choices=['legacy-20261029',DEPLOYMENT_CAMPAIGN],default='legacy-20261029')
    p.add_argument('--profile',choices=['pilot-8','c1-8','pilot','main','expanded'],default='main')
    p.add_argument('--isolation',choices=['docker','vm'])
    p.add_argument('--provider',choices=['provided','aliyun','ssh'])
    p.add_argument('--suite-config',help='unified-c1-c4-v1 JSON; existing campaigns retain their original meaning')
    p.add_argument('--cluster',choices=['C1','C2','C3','C4']);p.add_argument('--seed',type=int,default=42)
    p.add_argument('--study',choices=['baseline','writeback'],default='baseline',help='writeback plans a separate bounded C1/C4 policy ablation; never enables a mount or executes it')
    p.add_argument('--include-llm-simulation',action='store_true',help='also plan paired paced multi-trace conditions; fixed budget/boundary qualification still required')
    p.add_argument('--output',required=True);a=p.parse_args()
    if a.suite_config:
        import sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from bench.suite_plan import build as suite_build
        result=suite_build(json.loads(Path(a.suite_config).read_text(encoding='utf-8')))
        write_new(a.output,result)
        print(json.dumps(result['counts']))
        raise SystemExit(0)
    if not a.isolation or not a.provider:p.error('legacy plans require --isolation and --provider')
    result=build(a.profile,a.isolation,a.provider,a.cluster,a.seed,a.include_llm_simulation,a.study,a.campaign)
    write_new(a.output,result)
    print(json.dumps({'cells':result['total_cells'],'nodes':result['parallel_node_count'],'execution_enabled':False}))
