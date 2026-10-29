#!/usr/bin/env python3
"""P2 local qualification: real flat-v1/TiKV writes versus packed publication.

Both arms use identical source bytes. They deliberately use distinct S3 objects;
P1 is the separate metadata-only comparison. No cloud/performance acceptance.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import time

from aliyun_suite import check_binary, digest, load, runtime_pin, save
from ml_readonly_matrix import command, environment, mount_config, mounted, redact_logs
from ml_import import import_files

AX=Path(__file__).resolve().parents[1]


def native_config(spec, directory, bucket, namespace):
    config=mount_config(spec,'native-tikv',directory,'unused',bucket,namespace)
    config['volume_format']='flat-v1'
    del config['packed_manifest_key']
    config['cache'].update(writeback_mode='upload_before_commit',write_memory_bytes=268435456,
                           write_ssd_bytes=1073741824,memory_budget_bytes=536870912)
    return config


def native_environment(spec):
    return {k:v for k,v in environment(spec,'native-tikv').items() if not k.startswith('BREWFS_PACKED_')}


def object_inventory(client, bucket, data_root, output):
    """Stream actual S3 sizes and all four persisted shard placements, outside timing."""
    module=importlib.util.spec_from_file_location('p2_layout',AX/'experiments/retest-2026-10-29/scripts/inspect-layout.py')
    layout=importlib.util.module_from_spec(module);module.loader.exec_module(layout)
    result={'objects':0,'logical_bytes':0,'inline_objects':0,'external_objects':0,
            'xl_meta_bytes_all_shards':0,'part_bytes_all_shards':0,'size_histogram':{},
            'all_four_metadata_crc_verified':True,'scope':'serialized file lengths, not allocated disk/device I/O'}
    bounds=(4096,32768,65536,262144,1048576,4194304)
    with output.open('x') as stream:
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket):
            for item in page.get('Contents',[]):
                key=item['Key']
                if PurePosixPath(key).is_absolute() or any(p in ('','..','.') for p in key.split('/')):
                    raise ValueError('unsafe backend key')
                row={'key':key,'bytes':item['Size'],'shards':[]}
                indices=set();inline=set()
                for number in range(1,5):
                    path=data_root/f'disk{number}'/bucket/key/'xl.meta'
                    decoded=layout.decode(path)
                    if len(decoded['versions'])!=1:raise ValueError('unexpected versioned P2 object')
                    obj=decoded['versions'][0]['V2Obj'];meta=obj['MetaSys']
                    if (obj['EcM'],obj['EcN'])!=(2,2):raise ValueError('P2 EC geometry changed')
                    marker=any(k in meta for k in ('x-rustfs-internal-inline-data','x-minio-internal-inline-data'))
                    payload=sum(decoded['inline_shard_lengths'].values())
                    parts=list(path.parent.glob('*/part.*'))
                    if marker and (parts or not payload):raise ValueError('invalid inline placement')
                    if not marker and (payload or not parts):raise ValueError('invalid external placement')
                    indices.add(obj['EcIndex']);inline.add(marker)
                    row['shards'].append({'disk':number,'inline':marker,'inline_payload_bytes':payload,
                        'block_bytes':obj['EcBSize'],'xl_meta_bytes':decoded['xl_meta_bytes'],
                        'xl_meta_sha256':decoded['xl_meta_sha256'],'part_files':len(parts),
                        'part_bytes':sum(p.stat().st_size for p in parts)})
                if indices!={1,2,3,4} or len(inline)!=1:raise ValueError('inconsistent shard placement')
                result['objects']+=1;result['logical_bytes']+=item['Size']
                result['inline_objects' if inline.pop() else 'external_objects']+=1
                result['xl_meta_bytes_all_shards']+=sum(x['xl_meta_bytes'] for x in row['shards'])
                result['part_bytes_all_shards']+=sum(x['part_bytes'] for x in row['shards'])
                band=next(('le_'+str(b) for b in bounds if item['Size']<=b),'gt_4194304')
                result['size_histogram'][band]=result['size_histogram'].get(band,0)+1
                stream.write(json.dumps(row)+'\n')
    if not result['objects']:raise ValueError('no persisted data objects')
    result.update(passed=True,object_inventory_sha256=digest(output))
    return result


def run(spec,out):
    if spec['scope']!='local-qualification':raise ValueError('P2 has no formal performance acceptance gate')
    if subprocess.run(['pgrep','-x','rustc'],stdout=subprocess.DEVNULL).returncode==0:
        raise ValueError('compilation must finish first')
    source=Path(spec['source']).resolve(strict=True);manifest=Path(spec['manifest']).resolve(strict=True)
    inventory=load(spec['inventory_receipt']);pin=load(spec['backend_pin'])
    if not inventory.get('passed') or not inventory.get('exact_regular_file_inventory'):
        raise ValueError('unqualified source inventory')
    if inventory['manifest_sha256']!=digest(manifest) or inventory['files']!=spec['files']:
        raise ValueError('source identity changed')
    if not 1<=spec['repeats']<=3 or not spec['workers'] or not set(spec['workers']).issubset({1,16,64}):
        raise ValueError('unsupported P2 matrix')
    runtime=runtime_pin(pin,spec['rustfs_container'])
    for name in ('brewfs','fixture'):
        if spec[name]['source_sha']!=pin['brewfs_client_sha']:raise ValueError('client/producer source mismatch')
    binaries={k:check_binary(spec[k]) for k in ('brewfs','fixture','scanner')}
    out.mkdir(parents=True,exist_ok=False)
    report={'status':'running','group':'P2','scope':'local end-to-end path qualification; not accepted performance',
            'runtime':runtime,'source_manifest_sha256':digest(manifest),'source_files':spec['files'],
            'source_bytes':inventory['bytes'],'live_agent_end_to_end':False,'arms':{},'cells':[],
            'packed_decode_workspace_bytes':spec.get('decode_workspace_bytes',8388608),
            'cache_resource_parity_qualified':False,'kernel_cache':'product defaults, not instrumented'}
    save(out/'result.json',report);save(out/'spec.json',spec)
    import boto3
    from botocore.config import Config
    client=boto3.client('s3',endpoint_url=spec['endpoint'],region_name='us-east-1',
        config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
            request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    try:
        command([binaries['scanner'],str(source),str(manifest),'verify','16',str(spec['files']),
                 str(out/'source-content.json')],out/'source-verify.log')
        for arm in ('packed','native-tikv'):
            directory=out/(arm+'-prepare');directory.mkdir()
            bucket='ax-p2-'+secrets.token_hex(12);namespace='ax-p2-'+secrets.token_hex(12)
            client.create_bucket(Bucket=bucket)
            row={'bucket':bucket,'namespace':namespace};report['arms'][arm]=row;save(out/'result.json',report)
            if arm=='packed':
                keyfile=directory/'manifest-key.txt'
                row['publish_seconds']=command([binaries['fixture'],'--source-directory',str(source),
                    '--source-consistency','best-effort-detected','--source-hardlink-policy','reject-external',
                    '--bucket',bucket,'--endpoint',spec['endpoint'],'--region','us-east-1','--force-path-style','true',
                    '--prefix','source','--manifest-output',str(keyfile),'--output-dir',str(directory/'producer'),
                    '--wire-version','5','--metadata-codec','raw','--data-codec','raw','--inline-data','off',
                    '--frame-policy','size-only','--access-profile','sequential-small-file'],
                    directory/'publish.log',environment(spec,'packed'))
                row['manifest_key']=keyfile.read_text().strip()
            else:
                config=directory/'mount.json';save(config,native_config(spec,directory,bucket,namespace))
                start=time.monotonic()
                with mounted(binaries['brewfs'],config,directory,native_environment(spec)) as mp:
                    row['file_import']=import_files(source,manifest,mp,spec['files'],inventory['bytes'])
                row['prepare_including_mount_unmount_seconds']=time.monotonic()-start
            row['object_layout']=object_inventory(client,bucket,Path(spec['rustfs_data_root']),directory/'objects.jsonl')
            save(out/'result.json',report)
        for repeat in range(1,spec['repeats']+1):
            for arm in (('packed','native-tikv') if repeat%2 else ('native-tikv','packed')):
                row=report['arms'][arm];directory=out/f'r{repeat}-{arm}';directory.mkdir()
                config=directory/'mount.json'
                save(config,mount_config(spec,'packed',directory,row['manifest_key'],row['bucket'],row['namespace'])
                     if arm=='packed' else native_config(spec,directory,row['bucket'],row['namespace']))
                cell={'arm':arm,'repeat':repeat,'status':'running','passes':[]};report['cells'].append(cell)
                with mounted(binaries['brewfs'],config,directory,environment(spec,'packed') if arm=='packed' else native_environment(spec)) as mp:
                    command([binaries['scanner'],str(mp),str(manifest),'verify','16',str(spec['files']),
                             str(directory/'content.json')],directory/'content.log')
                    cell['all_content_verified_after_remount']=True
                    for workers in spec['workers']:
                        for mode in ('stat','read'):
                            for epoch in (1,2,3):
                                label=f'{mode}-{workers}-epoch{epoch}';result=directory/(label+'.json')
                                command([binaries['scanner'],str(mp),str(manifest),mode,str(workers),str(spec['files']),str(result)],
                                        directory/(label+'.log'))
                                value=load(result)
                                if not value['completed'] or value['manifest_sha256']!=report['source_manifest_sha256']:
                                    raise ValueError('incomplete P2 access')
                                cell['passes'].append({'mode':mode,'workers':workers,'epoch':epoch,'warm':True,
                                                      'raw':str(result.relative_to(out)),**value})
                                save(out/'result.json',report)
                cell['status']='ok';save(out/'result.json',report)
        report['status']='qualified'
    except Exception as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        client.close();redact_logs(out);save(out/'result.json',report)
    return report
