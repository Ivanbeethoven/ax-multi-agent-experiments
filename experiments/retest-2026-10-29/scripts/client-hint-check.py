"""Actual BrewFS FUSE -> S3 hint -> persisted EC -> remount readback qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

def check(client, directory, environment, binary, backend_name, data_root):
    # Isolate this qualification from a shell's previous mount/cache controls.
    environment={k:v for k,v in environment.items() if not k.startswith('BREWFS_')}
    environment.pop('AWS_SESSION_TOKEN',None)
    environment.update(AWS_EC2_METADATA_DISABLED='true',AWS_DEFAULT_REGION='us-east-1',
                       HTTP_PROXY='',HTTPS_PROXY='',ALL_PROXY='',http_proxy='',https_proxy='',all_proxy='',
                       NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
    module=importlib.util.spec_from_file_location('inspect_layout',Path(__file__).with_name('inspect-layout.py'))
    layout=importlib.util.module_from_spec(module);module.loader.exec_module(layout)
    bucket='ax-client-hint-'+secrets.token_hex(6)
    client.create_bucket(Bucket=bucket)
    directory.mkdir();mp=directory/'mount';mp.mkdir()
    redis_name='ax-client-hint-redis-'+secrets.token_hex(6)
    redis_id=subprocess.check_output(['docker','run','-d','--name',redis_name,'--network','host',
                 '--memory','512m','redis:7.2-alpine','redis-server','--bind','127.0.0.1','--port','19931',
                 '--save','','--appendonly','no'],text=True).strip()
    with binary.open('rb') as source:
        binary_sha=hashlib.file_digest(source,'sha256').hexdigest()
    report={'binary_sha256':binary_sha,
            'client_process_uid':os.geteuid(),'mount_roots':[],
            'requested_hint_bytes':65536,'client_block_bytes':4194304,'objects':[],
            'redis_container_image':json.loads(subprocess.check_output(['docker','inspect',redis_id],text=True))[0]['Image'],
            'passed':False}
    proc=None;log=None
    try:
        for _ in range(100):
            if subprocess.run(['docker','exec',redis_name,'redis-cli','-p','19931','ping'],
                              capture_output=True,text=True).stdout.strip()=='PONG':break
            time.sleep(.1)
        else:raise RuntimeError('owned Redis did not become ready')
        def unmount():
            nonlocal proc,log
            if os.path.ismount(mp):subprocess.run(['fusermount3','-u',str(mp)],check=True)
            if proc is not None:
                if proc.poll() is None and not os.path.ismount(mp):
                    try:proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:proc.terminate()
                assert proc.wait(timeout=30)==0,'BrewFS exited abnormally'
                proc=None
            if log:log.close();log=None
        payloads={name:hashlib.shake_256(('BrewFS actual hint path '+name).encode()).digest(size)
                  for name,size in [('payload',5*1024*1024+123),('inline-small',4096),('external-small',131072)]}
        for attempt in (1,2):
            config=directory/f'mount-{attempt}.json'
            config.write_text(json.dumps({'cache':{'cache_root':str(directory/f'cache-{attempt}'),
                      'read_memory_bytes':0,'write_memory_bytes':268435456,'read_ssd_bytes':0,
                      'write_ssd_bytes':1073741824,'memory_budget_bytes':536870912,
                      'compression':'none','verify_cache_checksum':'full','writeback_mode':'upload_before_commit'}}))
            argv=[str(binary),'mount','--config',str(config),'--data-backend','s3','--s3-bucket',bucket,
                  '--s3-endpoint','http://127.0.0.1:19930','--s3-region','us-east-1','--s3-force-path-style','true',
                  '--meta-backend','redis','--meta-url','redis://127.0.0.1:19931/0',
                  '--block-size','4194304','--s3-rustfs-ec-block-size-hint','65536',str(mp)]
            log=(directory/f'client-{attempt}.log').open('x')
            proc=subprocess.Popen(argv,env=environment,stdout=log,stderr=subprocess.STDOUT)
            for _ in range(600):
                if os.path.ismount(mp):break
                if proc.poll() is not None:raise RuntimeError('BrewFS mount failed; inspect retained log')
                time.sleep(.1)
            else:raise RuntimeError('BrewFS mount not ready')
            attrs=mp.stat()
            report['mount_roots'].append({'attempt':attempt,'uid':attrs.st_uid,'gid':attrs.st_gid,'mode':oct(attrs.st_mode & 0o7777)})
            if attempt==1:
                for filename,payload in payloads.items():
                    with (mp/filename).open('xb') as stream:
                        stream.write(payload);stream.flush();os.fsync(stream.fileno())
                    assert (mp/filename).read_bytes()==payload
            else:
                for filename,payload in payloads.items():
                    assert (mp/filename).read_bytes()==payload,'BrewFS remount content mismatch'
                payload=payloads['payload']
                for offset in (65520,262130,1048560,4194290):
                    with (mp/'payload').open('rb') as stream:
                        stream.seek(offset);assert stream.read(4096)==payload[offset:offset+4096]
            unmount()
            if attempt==2:
                report['full_content_and_ranges_after_backend_restart_and_client_remount']=True
                (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
            if attempt==1:
                subprocess.run(['docker','restart',backend_name],check=True,stdout=subprocess.DEVNULL)
                for _ in range(60):
                    try:client.head_bucket(Bucket=bucket);break
                    except Exception:time.sleep(1)
                else:raise RuntimeError('backend restart readiness failed')
        objects=client.list_objects_v2(Bucket=bucket)
        assert not objects.get('IsTruncated') and objects.get('Contents'),'missing client-created objects'
        for item in objects['Contents']:
            head=client.head_object(Bucket=bucket,Key=item['Key'])
            headers=head['ResponseMetadata']['HTTPHeaders']
            # RustFS need not echo its consumed layout hint as HEAD user metadata.
            # Prove the real request from each shard's persisted MetaUsr instead.
            assert headers.get('x-rustfs-effective-ec-block-size')=='65536','actual client object has wrong block'
            shards=[]
            for disk in range(1,5):
                path=data_root/f'disk{disk}'/bucket/item['Key']/'xl.meta'
                decoded=layout.decode(path);obj=decoded['versions'][0]['V2Obj']
                assert obj['MetaUsr'].get('rustfs-ec-block-size-hint')=='65536','persisted client request hint missing'
                assert obj['EcBSize']==65536 and (obj['EcM'],obj['EcN'])==(2,2)
                q=obj['MetaSys'].get('x-rustfs-internal-ec-read-quantum')
                inline='x-rustfs-internal-inline-data' in obj['MetaSys'] or 'x-minio-internal-inline-data' in obj['MetaSys']
                assert q==(None if inline else b'16384'),'actual client object has wrong read quantum'
                assert inline==(item['Size']<=65536),'actual client object has unexpected inline placement'
                shards.append({'block_bytes':obj['EcBSize'],'read_quantum_bytes':int(q) if q else None,
                               'inline_marker':inline,'inline_payload_bytes':sum(decoded['inline_shard_lengths'].values()),
                               'persisted_request_hint_bytes':65536,'metadata_crc_verified':decoded['metadata_crc_verified'],
                               'xl_meta_sha256':decoded['xl_meta_sha256']})
            report['objects'].append({'key':item['Key'],'bytes':item['Size'],
                                      'head_effective_block_bytes':int(headers['x-rustfs-effective-ec-block-size']),
                                      'head_echoed_hint':head.get('Metadata',{}).get('rustfs-ec-block-size-hint'),
                                      'shards':shards})
        assert any(item['bytes']>=1048576 for item in report['objects']),'no large client data object verified'
        # The pinned client persists a four-byte format header, even with codec none.
        assert sorted(item['bytes'] for item in report['objects'])==sorted([4096+4,131072+4,4194304+4,1048699+4])
        report['client_persisted_block_header_bytes']=4
        assert any(item['bytes']==4100 and all(s['inline_marker'] for s in item['shards'])
                   for item in report['objects']),'small client object did not exercise inline placement'
        assert any(item['bytes']==131076 and all(not s['inline_marker'] for s in item['shards'])
                   for item in report['objects']),'small client object did not exercise external placement'
        report.update(passed=True,full_content_and_ranges_after_backend_restart_and_client_remount=True,
                      file_sha256={name:hashlib.sha256(value).hexdigest() for name,value in payloads.items()})
    finally:
        if os.path.ismount(mp):subprocess.run(['fusermount3','-u',str(mp)],check=True)
        if proc is not None and proc.poll() is None:proc.terminate();proc.wait(timeout=30)
        if log:log.close()
        for log_path in directory.glob('client-*.log'):
            content=log_path.read_text(errors='replace')
            for name in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'):
                if environment.get(name):content=content.replace(environment[name],'[redacted]')
            log_path.write_text(content)
        subprocess.run(['docker','stop',redis_name],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','rm',redis_name],check=True,stdout=subprocess.DEVNULL)
        report['owned_redis_removed']=True
        (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
