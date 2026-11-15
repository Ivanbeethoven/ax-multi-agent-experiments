"""Shared parent preparation and workspace operations for the C4 campaign."""
import json
from pathlib import Path
import time

import cluster_lib as cl

TAGS={'brewfs-native-overlay':'brew','juicefs':'jfs','juicefs-overlay':'jov'}
REDIS_URL=f"redis://:{cl.SECRETS['redis_password']}@{cl.REDIS_HOST}:{cl.REDIS_PORT}/0"


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n')


def command(argv,out,host='A366',secrets=False,timeout=7200,accepted=(0,)):
    result=cl.run_on_host(host,argv,with_secrets=secrets,timeout=timeout,check=False)
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    for suffix,content in [('.stdout',result.stdout),('.stderr',result.stderr)]:
        for value in cl.SECRETS.values():
            if isinstance(value,str) and value:content=content.replace(value.encode(),b'<redacted>')
        out.with_suffix(suffix).write_bytes(content)
    if result.returncode not in accepted:raise RuntimeError(f'{out.name} failed on {host}: {result.returncode}')
    return result


def admin(parent,arguments,out):
    result=command([str(cl.BIN_DIR/'ax-brewfs-admin'),'--meta-url',REDIS_URL,
                    '--workspace-namespace',parent['namespace'],*arguments],out,secrets=True)
    return cl.extract_json_from_cli(result.stdout)


def spec(parent,workspace,endpoint=None):
    endpoint=endpoint or cl.RUSTFS_ENDPOINTS[0]
    if parent['system']=='brewfs-native-overlay':
        return {'workspace_uuid':workspace,'workspace_namespace':parent['namespace'],
                's3_bucket':parent['bucket'],'s3_endpoint':endpoint}
    if parent['system']=='juicefs':
        return {'redis_db':parent['redis_db'],'subdir':workspace,
                's3_bucket_url':endpoint+'/'+parent['bucket']}
    return {'volume':parent['volume'],'branch':workspace,
            's3_bucket_url':endpoint+'/'+parent['bucket']}


def request(parent,workspace,identifier,host,slot,phase='measure',row_cache_mib=64):
    engine=parent['system']
    return {'id':identifier,'phase':phase,'engine':engine,'host':cl.HOST_BY_NAME[host]['ssh'],
            'slot':slot,'workspace':workspace,'parent_workspace':parent.get('base','none'),
            'purpose':'C4','binary':str(cl.BIN_DIR/('brewfs' if engine.startswith('brewfs') else engine)),
            'cache_state':'cross-host-cold' if phase=='verify' else 'cold',
            'windows':[phase],'root':str(cl.RUN_ROOT),
            'overrides':{'row_cache_mib':row_cache_mib} if engine.startswith('brewfs') else {}}


def mount_one(parent,workspace,identifier,out,host='A366',slot=1,phase='initial',row_cache_mib=64):
    config=Path(out)/(identifier+'-mounts.json')
    cl.generate_mounts_json([request(parent,workspace,identifier,host,slot,phase,row_cache_mib)],config)
    records=cl.start_mounts(config,{identifier:spec(parent,workspace,cl.endpoint_for_slot(slot))},Path(out),register_collector=False)
    return records


def prepare_parent(system,files,file_bytes,seal=True,tag_suffix=""):
    tag=f'{TAGS[system]}-{files}-{file_bytes}{tag_suffix}'
    out=cl.RUN_ROOT/'prepared/fork'/tag
    state=out/'parent.json'
    if state.exists():
        parent=json.loads(state.read_text())
        if parent.get('prepared'):return parent
    out.mkdir(parents=True,exist_ok=True)
    parent={'system':system,'files':files,'file_bytes':file_bytes,'bucket':f'{cl.RUN_PREFIX}-c4-'+tag,
            'namespace':f'{cl.RUN_NAMESPACE}_c4_'+tag.replace('-','_'),'volume':f'{cl.RUN_PREFIX}-c4-'+tag,'tag':tag}
    started=time.monotonic()
    cl.delete_bucket_and_objects(parent['bucket']);cl.ensure_bucket(parent['bucket'])
    if system=='brewfs-native-overlay':
        cl.redis_delete_pattern_db0('*'+parent['namespace']+'*')
        parent['base']=admin(parent,['init-shared-volume'],out/'init')['workspace_id']
        workspace=parent['base']
    elif system=='juicefs':
        parent['redis_db']={10000:8,100000:9,8:10}.get(files,15)
        cl.redis_flush_db(parent['redis_db'])
        parent['meta_url']=f"redis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/{parent['redis_db']}"
        parent['base']='/base';workspace='/'
    else:
        cl.redis_delete_pattern_db0('{jfs-fork:'+parent['volume']+'}:*')
        parent['meta_url']=f"forkredis://{cl.REDIS_HOST}:{cl.REDIS_PORT}/0?volume={parent['volume']}&branch=main"
        parent['base']='main';workspace='main'
    if system!='brewfs-native-overlay':
        command([str(cl.BIN_DIR/system),'--no-agent','format','--storage=s3',
                 '--bucket='+cl.RUSTFS_ENDPOINTS[0]+'/'+parent['bucket'],'--block-size=4096',
                 '--compress=none','--trash-days=0',parent['meta_url'],parent['volume']],out/'format',secrets=True)
    mounted=mount_one(parent,workspace,'c4-prepare-'+tag,out)
    try:
        path=Path(mounted[0]['mount_directory'])
        if system=='juicefs':
            (path/'children').mkdir(exist_ok=True);path=path/'base';path.mkdir(exist_ok=True)
        result=command(['python3','-m','bench.cluster.c4_workload','prepare','--root',str(path),
                        '--files',str(files),'--file-bytes',str(file_bytes),'--workers','16'],out/'populate')
        parent['population']=json.loads(result.stdout)
        parent['upload']=cl.wait_uploads(mounted,timeout=3600)
    finally:cl.stop_mounts(mounted,out,clear_collector=False,remove_cache=True)
    if seal and system=='brewfs-native-overlay':
        parent['revision']=admin(parent,['seal-native-base','--workspace',parent['base']],out/'seal')['revision']
    elif seal and system=='juicefs-overlay':
        command([str(cl.BIN_DIR/system),'--no-agent','clone','--seal',parent['meta_url']],out/'seal',secrets=True)
    parent['prepare_seconds']=time.monotonic()-started
    parent['prepared']=True
    save(state,parent)
    return parent


def fork(parent,count,tag,out,mode='shared'):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    system=parent['system'];mounts=[]
    if system=='juicefs':mounts=mount_one(parent,'/',tag+'-clone-admin',out,slot=50)
    started=time.monotonic()
    try:
        if system=='brewfs-native-overlay':
            reply=admin(parent,['fork-shared-bucket','--revision',parent['revision'],
                                '--count',str(count),'--mode',mode],out/'fork')
            children=reply['workspaces']
        else:
            children=[]
            for index in range(count):
                name=f'{tag}-{index:03d}'
                argv=[str(cl.BIN_DIR/system),'--no-agent','clone']
                if system=='juicefs':
                    root=Path(mounts[0]['mount_directory']);child='/children/'+name
                    argv.extend(['-p',str(root/'base'),str(root/child.lstrip('/'))])
                else:
                    child=name;argv.extend(['--lazy',parent['meta_url'],child])
                command(argv,out/f'fork-{index:03d}',secrets=True)
                children.append(child)
        seconds=time.monotonic()-started
    finally:
        if mounts:cl.stop_mounts(mounts,out,clear_collector=False,remove_cache=True)
    save(out/'fork.json',{'workspaces':children,'seconds':seconds,'mode':mode,'count':count,
                         'native':reply if system=='brewfs-native-overlay' else None})
    return children,seconds


def placements(count,same_host=False):
    if same_host:return [('A366',index+3) for index in range(count)]
    weights=[16,11,6,13,13,5]
    counts=[count*weight//64 for weight in weights]
    order=sorted(range(6),key=lambda index:count*weights[index]%64,reverse=True)
    for index in order[:count-sum(counts)]:counts[index]+=1
    return [(host,slot+(3 if host=='A366' else 1))
            for host,amount in zip(cl.ALL_HOST_NAMES,counts) for slot in range(amount)]
