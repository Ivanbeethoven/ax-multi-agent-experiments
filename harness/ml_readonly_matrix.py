#!/usr/bin/env python3
"""Readonly ML corpus qualification, paired packed / native-TiKV metadata.

Backends must already be owned and running. Never purchases cloud resources.
This version qualifies the complete path locally; formal performance acceptance
requires the separately audited cloud resource/cache and physical-disk gates.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from aliyun_suite import check_binary, digest, load, no_credentials_in_url, runtime_pin, save

ARMS=('packed','native-tikv')


def mount_config(spec, arm, directory, key, bucket, namespace):
    if arm not in ARMS:
        raise ValueError('only packed and native-tikv are allowed')
    config={'mount_point':str(directory/'mount'),'volume_format':'packed-metadata-v3',
            'packed_manifest_key':key,
            'data':{'backend':'s3','s3':{'bucket':bucket,'endpoint':spec['endpoint'],
                    'region':'us-east-1','force_path_style':True,'disable_payload_checksum':True}},
            'layout':{'chunk_size':67108864,'block_size':4194304},
            'fuse':{'workers':16,'max_background':512,'privileged':False},
            'cache':{'cache_root':str(directory/'cache'),'read_memory_bytes':0,'read_ssd_bytes':0,
                     'prefetch_enabled':False,'range_background_prefetch':False,
                     'compression':'none','verify_cache_checksum':'full'}}
    if arm=='native-tikv':
        config['meta']={'backend':'tikv','tikv':{'pd_endpoints':[spec['tikv_pd']],'namespace':namespace}}
    return config


def environment(spec, arm, importing=False):
    # Reject hidden inherited controls, then set the same explicit controls.
    env={k:v for k,v in os.environ.items() if not k.startswith('BREWFS_')}
    env.update(BREWFS_PACKED_METADATA_ARM=arm,BREWFS_PACKED_NATIVE_IMPORT='1' if importing else '0',
        BREWFS_PACKED_METADATA_CACHE_BYTES=str(spec['metadata_cache_bytes']),
        BREWFS_PACKED_METADATA_PREFETCH='off',BREWFS_PACKED_DECODED_FRAME_CACHE_BYTES='0',
        BREWFS_PACKED_FRAME_WINDOW_CACHE_BYTES='0',BREWFS_PACKED_FRAME_WINDOW_PREFETCH='false',
        BREWFS_PACKED_V3_DECODE_WORKSPACE_BUDGET_BYTES=str(spec.get('decode_workspace_bytes',8388608)),
        AWS_EC2_METADATA_DISABLED='true',AWS_DEFAULT_REGION='us-east-1',
        HTTP_PROXY='',HTTPS_PROXY='',ALL_PROXY='',http_proxy='',https_proxy='',all_proxy='',
        NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost',RUST_LOG='warn,brewfs=info')
    # Kernel defaults are kept. TTL0 is a separately labeled future diagnostic.
    return env


def redact_logs(directory):
    for path in directory.rglob('*.log'):
        content=path.read_text(errors='replace')
        for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'):
            if os.environ.get(key):content=content.replace(os.environ[key],'[redacted]')
        path.write_text(content)


def command(argv, log, env=None):
    started=time.monotonic()
    with log.open('x') as stream:
        subprocess.run(argv,stdout=stream,stderr=subprocess.STDOUT,env=env,check=True)
    return time.monotonic()-started


@contextmanager
def mounted(binary, config, directory, env):
    mp=directory/'mount';mp.mkdir()
    proc=None
    try:
        with (directory/'mount.log').open('x') as log:
            proc=subprocess.Popen([binary,'mount','--config',str(config),str(mp)],
                                  stdout=log,stderr=subprocess.STDOUT,env=env)
            for _ in range(1800):
                if os.path.ismount(mp):break
                if proc.poll() is not None:raise RuntimeError('mount exited before readiness')
                time.sleep(.1)
            else:raise RuntimeError('mount readiness failed')
            yield mp
            subprocess.run(['fusermount3','-u',str(mp)],check=True)
            if proc.wait()!=0:raise RuntimeError('daemon failed during normal unmount')
    finally:
        if os.path.ismount(mp):subprocess.run(['fusermount3','-u',str(mp)],check=True)
        if proc is not None and proc.poll() is None:
            proc.terminate();proc.wait()
        save(directory/'cleanup.json',{'mount_absent':not os.path.ismount(mp),
                                      'daemon_exited':proc is None or proc.poll() is not None})


def run(spec,out):
    if spec['scope']!='local-qualification':
        raise ValueError('formal performance gate is not implemented by this qualification runner')
    if subprocess.run(['pgrep','-x','rustc'],stdout=subprocess.DEVNULL).returncode==0:
        raise ValueError('finish compilation before the paired run')
    no_credentials_in_url(spec['endpoint'])
    source=Path(spec['source']).resolve(strict=True);manifest=Path(spec['manifest']).resolve(strict=True)
    inventory=load(spec['inventory_receipt'])
    if not inventory.get('passed') or not inventory.get('exact_regular_file_inventory'):
        raise ValueError('unqualified corpus inventory')
    if inventory['manifest_sha256']!=digest(manifest) or inventory['files']!=spec['files']:
        raise ValueError('corpus identity/count changed')
    if not 1<=spec['repeats']<=3 or not set(spec['workers']).issubset({1,16,64}) or not spec['workers']:
        raise ValueError('unsupported paired matrix')
    pin=load(spec['backend_pin'])
    actual=runtime_pin(pin,spec['rustfs_container'])
    for name in ('brewfs','fixture'):
        if spec[name]['source_sha']!=pin['brewfs_client_sha']:
            raise ValueError('producer/client source mismatch')
    binaries={k:check_binary(spec[k]) for k in ('brewfs','fixture','scanner')}
    out.mkdir(parents=True,exist_ok=False)
    report={'scope':'local path qualification; not accepted cloud performance',
            'status':'running','runtime':actual,'source_manifest_sha256':digest(manifest),
            'source_files':spec['files'],'source_payload_bytes':inventory['bytes'],
            'cells':[],'source_consistency':'best-effort-detected; not atomic snapshot',
            'metadata_cache_bytes':spec['metadata_cache_bytes'],'kernel_cache':'product-defaults-unobserved',
            'decode_workspace_bytes':spec.get('decode_workspace_bytes',8388608),
            'page_cache':'shared host cache not evicted; warm epochs explicitly labeled',
            'backend_retained':True,'live_agent_end_to_end':False}
    save(out/'result.json',report);save(out/'spec.json',spec)
    bucket='ax-ml-'+secrets.token_hex(12);namespace='ax-ml-'+secrets.token_hex(12)
    report.update(bucket=bucket,tikv_namespace=namespace)
    try:
        # Check actual bytes on the source before publication; outside arm timing.
        command([binaries['scanner'],str(source),str(manifest),'verify','16',str(spec['files']),
                 str(out/'source-content.json')],out/'source-verify.log')
        import boto3
        from botocore.config import Config
        client=boto3.client('s3',endpoint_url=spec['endpoint'],region_name='us-east-1',
              config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                            request_checksum_calculation='when_required',response_checksum_validation='when_required'))
        client.create_bucket(Bucket=bucket);client.close()
        keyfile=out/'manifest-key.txt'
        report['publish_seconds']=command([binaries['fixture'],'--source-directory',str(source),
            '--source-consistency','best-effort-detected','--source-hardlink-policy','reject-external',
            '--bucket',bucket,'--endpoint',spec['endpoint'],'--region','us-east-1','--force-path-style','true',
            '--prefix','source','--manifest-output',str(keyfile),'--output-dir',str(out/'producer'),
            '--wire-version','5','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
            '--frame-policy','size-only','--access-profile','sequential-small-file'],out/'publish.log',environment(spec,'packed'))
        key=keyfile.read_text().strip();report['packed_manifest_key']=key
        # Import once, charge it as preparation, and never reimport during reads.
        imp=out/'native-import';imp.mkdir();(imp/'mount').mkdir()
        config=imp/'mount.json';save(config,mount_config(spec,'native-tikv',imp,key,bucket,namespace))
        report['native_import_seconds']=command([binaries['brewfs'],'mount','--config',str(config),str(imp/'mount')],
                                              imp/'import.log',environment(spec,'native-tikv',True))
        save(out/'result.json',report)
        for repeat in range(1,spec['repeats']+1):
            order=ARMS if repeat%2 else ARMS[::-1]
            for arm in order:
                directory=out/f'r{repeat}-{arm}';directory.mkdir()
                config=directory/'mount.json';save(config,mount_config(spec,arm,directory,key,bucket,namespace))
                cell={'repeat':repeat,'arm':arm,'status':'running','passes':[]};report['cells'].append(cell)
                save(out/'result.json',report)
                with mounted(binaries['brewfs'],config,directory,environment(spec,arm)) as mp:
                    # Full content check warms every file. All subsequent numbers
                    # are labeled warm; do not call these cold-start measurements.
                    command([binaries['scanner'],str(mp),str(manifest),'verify','16',str(spec['files']),
                             str(directory/'content.json')],directory/'content.log')
                    for workers in spec['workers']:
                        for mode in ('stat','read'):
                            for epoch in (1,2,3):
                                label=f'{mode}-{workers}-epoch{epoch}';result=directory/(label+'.json')
                                command([binaries['scanner'],str(mp),str(manifest),mode,str(workers),str(spec['files']),str(result)],
                                        directory/(label+'.log'))
                                value=load(result)
                                if not value['completed'] or value['manifest_sha256']!=report['source_manifest_sha256']:
                                    raise ValueError('incomplete/mismatched corpus access')
                                cell['passes'].append({'mode':mode,'workers':workers,'epoch':epoch,'warm':True,
                                                      'raw':str(result.relative_to(out)),**value})
                                save(out/'result.json',report)
                cell['status']='ok';save(out/'result.json',report)
        report['status']='qualified'
    except Exception as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        redact_logs(out);save(out/'result.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--spec',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=run(load(a.spec),a.output.resolve());print(json.dumps({'status':result['status'],'cells':len(result['cells'])}))
