"""Generate reviewable native mount specs and per-Agent isolation commands.

No service, mount, container or cloud API is started by this module.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .common import digest, load, pinned, private_dir, write_new

AGENT_BUDGET = {'cpus':2.0,'memory_bytes':8*1024**3,'swap_bytes':0}


def anchor(filesystem, config):
    if filesystem == 'brewfs':
        stable = {'meta':config['meta'], 'data':config['data'],
                  'volume_format':config['volume_format'],
                  'workspace':config.get('workspace'), 'namespace':config.get('workspace_namespace'),
                  'packed_manifest_key':config.get('packed_manifest_key')}
        if config.get('entity_native_manifest_key'):stable['entity_native_manifest_key']=config['entity_native_manifest_key']
        if config.get('entity_volume_id'):stable['entity_volume_id']=config['entity_volume_id']
    else: stable = {'metadata_url':config['metadata_url']}
    # Opaque metadata/config identity keeps credentials out of mount receipts.
    return {'filesystem':filesystem,'workspace':config.get('workspace'),
            'namespace':config.get('workspace_namespace'),
            'storage_identity_sha256':hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()}


def native_argv(filesystem, binary, config_path, config, point, cluster, metrics_address=None, dynamic_block='auto', metadata_backend=None):
    unified = metadata_backend is not None
    if unified and metadata_backend not in ('redis','tikv'): raise ValueError('explicit Redis or TiKV backend required')
    if not unified and cluster not in ('C1','C2','C4'): raise ValueError('C3 has no filesystem mount in the legacy protocol')
    if filesystem == 'brewfs':
        fmt = config['volume_format']; backend = config['meta']['backend']
        if backend != (metadata_backend if unified else 'tikv' if cluster == 'C2' else 'redis'): raise ValueError('wrong baseline metadata backend')
        if not unified and fmt.startswith('packed-') and cluster != 'C2': raise ValueError('Pack belongs only to legacy C2')
        if fmt not in ('flat','workspace','native-workspace','packed-snapshot') and not (unified and fmt=='entity-workspace'):
            raise ValueError('unqualified volume format')
        if fmt in ('workspace','native-workspace','entity-workspace') and (not config.get('workspace') or not config.get('workspace_namespace')):
            raise ValueError('workspace and namespace identities required')
        if config['data']['backend'] != 's3': raise ValueError('shared object backend required')
        if 'mount_point' in config and config['mount_point'] != point: raise ValueError('mount path mismatch')
        cache = config['cache']
        if cache.get('writeback_mode') != 'upload_before_commit':
            raise ValueError('baseline mount adapter requires explicit upload_before_commit; async is a separate qualification')
        if fmt == 'native-workspace' and 'native_base' not in config: raise ValueError('native P1 config missing')
        if dynamic_block not in ('auto','off','65536','262144','1048576','4194304'):
            raise ValueError('dynamic block policy must be auto, off or a supported byte size')
        configured = config['data']['s3'].get('rustfs_ec_block_size_hint')
        if configured is not None and str(configured) != dynamic_block:
            raise ValueError('config hint differs from the requested dynamic block policy')
        if not isinstance(config.get('fuse',{}).get('cache_ttl_ms'),int) or not isinstance(config['fuse'].get('keep_cache'),bool):
            raise ValueError('freeze cache TTL and keep_cache explicitly')
        argv=[binary,'mount','--config',config_path,point]
        if fmt == 'entity-workspace':
            import uuid
            if not uuid.UUID(config['entity_volume_id']).int or not uuid.UUID(config['workspace']).int:raise ValueError('non-nil entity identities required')
            packed=config.get('packed_manifest_key'); native=config.get('entity_native_manifest_key')
            if bool(packed)==bool(native) or config['fuse']['keep_cache']:
                raise ValueError('fresh entity requires exactly one frozen packed/native lower and direct IO')
            budget = config.get('entity_read_cache_bytes')
            if type(budget) is not int or budget < 0: raise ValueError('explicit entity cache byte budget required')
            if native and budget:raise ValueError('current entity payload cache supports packed lower only')
            argv += ['--volume-format',fmt,'--experimental-entity-mount',
                     '--entity-volume-id',config['entity_volume_id'],'--workspace',config['workspace'],
                     '--workspace-namespace',config['workspace_namespace'],
                     '--packed-manifest-key' if packed else '--entity-native-manifest-key',packed or native,
                     '--entity-read-cache-bytes',str(budget)]
        if dynamic_block != 'off': argv+=['--s3-rustfs-ec-block-size-hint',dynamic_block]
        if metrics_address:argv+=['--metrics','--metrics-addr',metrics_address]
        return argv
    if filesystem != 'juicefs' or (not unified and cluster == 'C2'): raise ValueError('unsupported baseline')
    url = urlsplit(config['metadata_url'])
    if url.scheme != (metadata_backend if unified else 'redis') or url.username or url.password or url.query or url.fragment:
        raise ValueError('matching plain metadata URL; credentials via native supported environment')
    if config.get('writeback') is not False: raise ValueError('baseline JuiceFS writeback must be explicitly false')
    for key in (('cache_mib','buffer_mib','attr_cache_seconds','entry_cache_seconds') if unified else ()):
        if type(config.get(key)) is not int or config[key] < (1 if key=='buffer_mib' else 0):
            raise ValueError('explicit nonnegative JuiceFS cache settings required: '+key)
    argv=[binary,'mount',config['metadata_url'],point,'--cache-dir',config['cache_dir'],
            '--cache-size',str(config['cache_mib']),'--buffer-size',str(config['buffer_mib']),
            '--enable-xattr','--enable-cap','--attr-cache',str(config['attr_cache_seconds']),
            '--entry-cache',str(config['entry_cache_seconds']),'--dir-entry-cache',str(config['entry_cache_seconds'])]
    for key,flag in (('open_cache_seconds','open-cache'),('prefetch','prefetch'),
                     ('max_readahead_mib','max-readahead'),('backup_meta','backup-meta'),
                     ('verify_cache_checksum','verify-cache-checksum')):
        if key in config:argv+=['--'+flag,str(config[key])]
    for key in ('cache_large_write','no_usage_report'):
        if key in config:
            if type(config[key]) is not bool:raise ValueError('explicit boolean required: '+key)
            argv+=['--'+key.replace('_','-')+'='+str(config[key]).lower()]
    if metrics_address:argv+=['--metrics',metrics_address]
    return argv


def mount(config_ref, binary_ref, filesystem, cluster, point, cwd, output, environment, fstypes, metrics_address,purpose='experiment',dynamic_block='auto',metadata_backend=None,suite_binding=None):
    config_path = str(pinned(config_ref)); binary = str(pinned(binary_ref)); config = load(config_path)
    if metadata_backend is not None:
        if not suite_binding:raise ValueError('unified mount requires --suite-plan and --suite-cell')
        from ..suite_mount import validate_binding
        validate_binding(suite_binding,config,filesystem,cluster,metadata_backend,dynamic_block)
    elif suite_binding:raise ValueError('suite binding requires explicit metadata backend')
    if purpose=='observer-calibration-off':metrics_address=None
    elif not re.fullmatch(r'127\.0\.0\.1:[0-9]{4,5}',metrics_address or ''):raise ValueError('dedicated loopback metrics port required')
    argv = native_argv(filesystem,binary,config_path,config,point,cluster,metrics_address,dynamic_block,metadata_backend)
    output = private_dir(output)
    step = {'schema':'experiment-step-v1','reviewed':False,'cell_id':output.name,'stage':'mount',
            'argv':argv,'cwd':cwd,'executable_sha256':binary_ref['sha256'],
            'inputs':[config_ref]+([suite_binding['plan']] if suite_binding else []),'inherit_environment':environment}
    write_new(output/'command.json',step)
    value = {'schema':'experiment-mount-v1','reviewed':False,'cluster':cluster,'filesystem':filesystem,
             'config':config_ref,
             'metrics_address':metrics_address,
             'dynamic_block':dynamic_block if filesystem=='brewfs' else None,
             'purpose':purpose,
             'metadata_backend':metadata_backend,
             'mount_point':point,'anchor':anchor(filesystem,config), 'allowed_fstypes':fstypes,
             'budget':AGENT_BUDGET,'command':{'path':str(output/'command.json'),'sha256':digest(output/'command.json')},
             'ready_timeout_seconds':60}
    if suite_binding:value['suite_binding']=suite_binding
    write_new(output/'mount.json',value)
    return value


def container_argv(s):
    """Create only. Pinned image must already be distributed; never pull latest."""
    if not re.fullmatch(r'ax-[a-z0-9-]+',s['name']): raise ValueError('dedicated ax container name')
    if not re.fullmatch(r'.+@sha256:[a-f0-9]{64}',s['image']): raise ValueError('image digest required')
    args=[s['docker'],'create','--pull=never','--name',s['name'],
          '--label','ax.campaign='+s['campaign_id'],'--label','ax.agent='+s['agent_id'],
          '--cpus','2','--memory','8589934592','--memory-swap','8589934592','--pids-limit','4096',
          '--network','host','--device','/dev/fuse','--cap-add','SYS_ADMIN','--security-opt','apparmor=unconfined']
    destinations=set()
    for binding in s['binds']:
        source=Path(binding['source']); target=Path(binding['target'])
        if not source.is_absolute() or not source.exists() or source.is_symlink() or str(source) == '/' or ',' in str(source):
            raise ValueError('existing dedicated bind source required')
        if not target.is_absolute() or str(target) == '/' or ',' in str(target) or str(target) in destinations:
            raise ValueError('distinct non-root container target required')
        destinations.add(str(target))
        args+=['--mount',f'type=bind,source={source},target={target}'+(',readonly' if binding['readonly'] else '')]
    if s.get('environment_file'): args+=['--env-file',str(pinned(s['environment_file']))]
    args += [s['image'],'/bin/sleep','infinity']
    return args


def trace_worker(request, output):
    """Prepare one full-trace job on its executor; all generated specs need review."""
    from ..trace_checks import inputs
    from ..experiment_io import linux_path
    s = load(request)
    if type(s.get('cache_parent_fds',False)) is not bool:raise ValueError('cache_parent_fds must be an explicit boolean')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*',s['worker_id']): raise ValueError('worker ID required')
    case = Path(s['case']).resolve(strict=True)
    manifest = pinned(s['manifest'])
    inputs(case, manifest, s['manifest']['sha256'])
    metrics = load(case/'metrics.json')
    for key in ('callbacks','actual_read_bytes','actual_write_bytes'):
        if type(metrics.get(key)) is not int or metrics[key] < 0: raise ValueError('complete work counts required')
    if metrics['callbacks'] == 0: raise ValueError('empty trace')
    binary = str(pinned(s['binary']))
    root = linux_path(s['root']); cwd = linux_path(s['cwd'])
    out_path = linux_path(str(output))
    if root == out_path or root in out_path.parents: raise ValueError('results must be outside measured tree')
    mount_spec = load(pinned(s['mount']['spec']))
    point = linux_path(mount_spec['mount_point'])
    if out_path == point or point in out_path.parents: raise ValueError('results must be outside measured mount')
    if root != point and point not in root.parents: raise ValueError('trace root must use supervised mount')
    if case == Path(point) or Path(point) in case.parents: raise ValueError('inputs must be outside measured mount')
    purpose = s.get('purpose','experiment')
    if purpose not in ('experiment','observer-calibration-on','observer-calibration-off'): raise ValueError('invalid purpose')
    output = private_dir(output)
    refs = [{'path': str(case/p), 'sha256': sha} for p,sha in load(manifest).items()]
    step = {'schema':'experiment-step-v1','reviewed':False,'cell_id':s['worker_id'],'stage':'trace-replay',
            'argv':[binary,str(case/'capture/events.jsonl'),str(case/'capture/blobs'),str(root),
                    str(output/'replay.json'),'--performance'],
            'cwd':str(cwd),'executable_sha256':s['binary']['sha256'],'inputs':[s['manifest'],*refs],
            'inherit_environment':[]}
    if s.get('cache_parent_fds'):
        step['argv'].append('--cache-parent-fds')
    write_new(output/'step.json', step)
    value = {'schema':'experiment-worker-v1','reviewed':False,'worker_id':s['worker_id'],
             'purpose':purpose,'kind':'trace','budget':AGENT_BUDGET,'requires_mount':True,'mount':s['mount'],
             'jobs':[{'id':'trace-0001','workload_id':s['workload_id'],
                      'step':{'path':str(output/'step.json'),'sha256':digest(output/'step.json')},
                      'result':{'path':str(output/'replay.json'),'equals':{
                          'completed':True,'mode':'performance','valid':False,'operations':metrics['callbacks'],
                          'actual_read_bytes':metrics['actual_read_bytes'],
                          'actual_write_bytes':metrics['actual_write_bytes'],'remaining_handles':0}}}]}
    if s.get('cache_parent_fds'):value['jobs'][0]['result']['equals']['path_resolution']='parent-fd-cache'
    write_new(output/'worker.json', value)
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    m=sub.add_parser('mount')
    for key in ('config','config-sha256','binary','binary-sha256','filesystem','cluster','mount-point','cwd','output'):
        m.add_argument('--'+key,required=True)
    m.add_argument('--metrics-address');m.add_argument('--purpose',choices=['experiment','observer-calibration-off'],default='experiment')
    m.add_argument('--metadata-backend',choices=['redis','tikv'],help='unified suite: decouple filesystem/backend from legacy C1-C4 cluster IDs')
    m.add_argument('--suite-plan');m.add_argument('--suite-cell')
    m.add_argument('--dynamic-block',choices=['auto','off','65536','262144','1048576','4194304'],default='auto',
                   help='BrewFS object hint policy; off is an explicit client ablation')
    m.add_argument('--inherit-environment',nargs='*',default=[]);m.add_argument('--fstypes',nargs='+',required=True)
    c=sub.add_parser('container');c.add_argument('--spec',required=True);c.add_argument('--output',required=True)
    t=sub.add_parser('trace-worker');t.add_argument('--request',required=True);t.add_argument('--output',required=True)
    a=p.parse_args()
    if a.action=='mount':
        if bool(a.suite_plan)!=bool(a.suite_cell):p.error('--suite-plan and --suite-cell must be used together')
        binding=({'plan':{'path':str(Path(a.suite_plan).resolve()),'sha256':digest(a.suite_plan)},
                  'cell_id':a.suite_cell} if a.suite_plan else None)
        mount({'path':a.config,'sha256':a.config_sha256},{'path':a.binary,'sha256':a.binary_sha256},
              a.filesystem,a.cluster,a.mount_point,a.cwd,a.output,a.inherit_environment,a.fstypes,a.metrics_address,a.purpose,a.dynamic_block,a.metadata_backend,binding)
    elif a.action=='trace-worker':trace_worker(a.request,a.output)
    else:
        s=load(a.spec)
        write_new(a.output,{'schema':'experiment-step-v1','reviewed':False,'cell_id':s['agent_id'],'stage':'container-create',
                           'argv':container_argv(s),'cwd':s['cwd'],'executable_sha256':digest(s['docker']),
                           'inputs':[{'path':str(Path(a.spec).resolve()),'sha256':digest(a.spec)}]
                                    +([s['environment_file']] if s.get('environment_file') else [])})
