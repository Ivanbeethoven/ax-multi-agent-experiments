"""Prepare and validate the four-node official RustFS 1.0.5 deployment."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import zipfile
import zlib

VERSION='1.0.5'
RELEASE=Path('/srv/ax-cluster/releases/rustfs-1.0.5')
ARCHIVE='rustfs-linux-x86_64-gnu-v1.0.5.zip'
ARCHIVE_BYTES=122476523
URL='https://dl.rustfs.com/artifacts/rustfs/release/'+ARCHIVE
BASE=Path('/srv/ax-cluster/official-1.0.5')
OBJECTS=['A672','A601','A635','A368']
UNIT='ax-rustfs-official-105.service'
OTEL='ax-rustfs-official-105-otel'
PORT=19000


def download():
    RELEASE.mkdir(parents=True,exist_ok=True)
    archive=RELEASE/ARCHIVE
    if not archive.exists() or archive.stat().st_size!=ARCHIVE_BYTES:
        subprocess.run(['curl','--fail','--location','--retry','2','--max-time','600',
                        '--limit-rate','12M','--output',str(archive),URL],check=True)
    extract()


def extract():
    archive=RELEASE/ARCHIVE
    if archive.stat().st_size!=ARCHIVE_BYTES:raise RuntimeError('Release package download is incomplete')
    with zipfile.ZipFile(archive) as directory:
        items=[item for item in directory.infolist() if Path(item.filename).name=='rustfs' and not item.is_dir()]
    if len(items)!=1:raise RuntimeError('Release package must contain one rustfs executable')
    item=items[0];output=RELEASE/'rustfs';temporary=RELEASE/'rustfs.part'
    with archive.open('rb') as source,temporary.open('wb') as target:
        source.seek(item.header_offset);header=source.read(30)
        if header[:4]!=b'PK\x03\x04':raise RuntimeError('Invalid ZIP local header')
        flags,method=struct.unpack_from('<HH',header,6)
        name_length,extra_length=struct.unpack_from('<HH',header,26)
        if flags & 1 or method not in (0,8):raise RuntimeError('Unsupported release package encoding')
        source.seek(name_length+extra_length,1)
        decoder=zlib.decompressobj(-15) if method==8 else None
        remaining=item.compress_size;written=0
        while remaining:
            chunk=source.read(min(1024*1024,remaining))
            if not chunk:raise RuntimeError('Release package ended unexpectedly')
            remaining-=len(chunk);data=decoder.decompress(chunk) if decoder else chunk
            target.write(data);written+=len(data)
        if decoder:
            data=decoder.flush();target.write(data);written+=len(data)
            if not decoder.eof:raise RuntimeError('Deflate stream did not finish')
        if written!=item.file_size:raise RuntimeError('Executable length differs from package directory')
    with temporary.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':raise RuntimeError('Release executable is not ELF')
    temporary.chmod(0o755);temporary.replace(output)
    result=subprocess.run([str(output),'--version'],capture_output=True,text=True,check=True)
    if VERSION not in result.stdout:raise RuntimeError('Executable version differs from selected release')
    record={'version':VERSION,'package_url':URL,'package_bytes':ARCHIVE_BYTES,
            'binary_bytes':output.stat().st_size,'version_output':result.stdout.strip()}
    (RELEASE/'release.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record),flush=True)


def coordinator():
    sys.path.insert(0,'/srv/ax-cluster/direct/scripts')
    import cluster_lib as cl
    return cl


def configure():
    cl=coordinator()
    from deploy_direct import remote_python,restrict_service
    source=cl.run_on_host('A355',['cat',str(RELEASE/'release.json')],log_cmd=False)
    metadata=json.loads(source.stdout)
    binary=cl.run_on_host('A355',['cat',str(RELEASE/'rustfs')],log_cmd=False,timeout=300).stdout
    if len(binary)!=metadata['binary_bytes']:raise RuntimeError('Release transfer length differs')
    local=RELEASE/'rustfs';local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(binary);local.chmod(0o755)
    output=cl.RUN_ROOT/'deployment/rustfs-1.0.5';output.mkdir(parents=True,exist_ok=True)
    endpoints=[f"http://{cl.HOST_BY_NAME[host]['ip']}:{PORT}" for host in OBJECTS]
    volumes=' '.join(endpoint+str(BASE/'data') for endpoint in endpoints)
    nodes=[]
    for host,endpoint in zip(OBJECTS,endpoints):
        cl.sync_file_to_host(host,local,str(local))
        env={'RUSTFS_ADDRESS':endpoint.removeprefix('http://'),
             'RUSTFS_CONSOLE_ADDRESS':'127.0.0.1:19001','RUSTFS_CONSOLE_ENABLE':'false',
             'RUSTFS_OBS_LOGGER_LEVEL':'warn','RUSTFS_OBS_LOG_DIRECTORY':str(BASE/'logs'),
             'RUSTFS_OBS_METRIC_ENDPOINT':'http://127.0.0.1:14318/v1/metrics',
             'RUSTFS_OBS_METRICS_EXPORT_ENABLED':'true','RUSTFS_OBS_METER_INTERVAL':'1',
             'RUSTFS_OBS_TRACES_EXPORT_ENABLED':'false','RUSTFS_OBS_LOGS_EXPORT_ENABLED':'false',
             'RUSTFS_OBS_PROFILING_EXPORT_ENABLED':'false','RUSTFS_OBS_USE_STDOUT':'false',
             'OTEL_RESOURCE_ATTRIBUTES':'rustfs.cluster.id=ax-cluster-official-105'}
        memory=24 if host=='A368' else 32
        unit='\n'.join(['[Unit]','Description=Official RustFS 1.0.5 experiment node',
                       'After=network-online.target','[Service]','Type=simple','User=axrustfs','Group=axrustfs',
                       'EnvironmentFile=/srv/ax-cluster/private/rustfs.env',
                       *['Environment="'+key+'='+value+'"' for key,value in env.items()],
                       'ExecStart='+str(local)+' server '+volumes,'LimitNOFILE=1048576',
                       'CPUQuota=1600%','MemoryMax='+str(memory)+'G','MemorySwapMax=0',
                       'TimeoutStopSec=90','Restart=no',
                       'StandardOutput=append:'+str(BASE/'logs/service.log'),
                       'StandardError=append:'+str(BASE/'logs/service.log'),''])
        spec={'unit':unit,'binary':str(local),'base':str(BASE),'otel':OTEL}
        reply=remote_python(host,r'''
import json,os,pathlib,pwd,subprocess,sys
spec=json.loads(sys.argv[1]);base=pathlib.Path(spec['base'])
try:account=pwd.getpwnam('axrustfs')
except KeyError:
 subprocess.run(['useradd','--system','--user-group','--home-dir',str(base),'--no-create-home',
                 '--shell','/usr/sbin/nologin','axrustfs'],check=True)
 account=pwd.getpwnam('axrustfs')
for name in ['data','logs']:
 p=base/name;p.mkdir(parents=True,exist_ok=True);os.chown(p,account.pw_uid,account.pw_gid)
pathlib.Path(spec['binary']).chmod(0o755)
config=pathlib.Path('/srv/ax-cluster/rustfs/otelcol.yaml').read_text().replace('127.0.0.1:4318','127.0.0.1:14318').replace('127.0.0.1:9464','127.0.0.1:19464')
config=config.replace('service:\n','service:\n  telemetry:\n    metrics:\n      readers:\n        - pull:\n            exporter:\n              prometheus:\n                host: 127.0.0.1\n                port: 18888\n',1)
(base/'otelcol.yaml').write_text(config)
image=json.loads(subprocess.check_output(['docker','inspect','ax-cluster-otelcol-run001']))[0]['Config']['Image']
if subprocess.run(['docker','inspect',spec['otel']],capture_output=True).returncode:
 subprocess.run(['docker','create','--name',spec['otel'],'--network=host','--memory=512m','--cpus=1',
  '--mount','type=bind,src='+str(base/'otelcol.yaml')+',dst=/etc/otelcol-contrib/config.yaml,readonly',
  image,'--config=/etc/otelcol-contrib/config.yaml'],check=True,capture_output=True)
pathlib.Path('/etc/systemd/system/ax-rustfs-official-105.service').write_text(spec['unit'])
subprocess.run(['systemctl','daemon-reload'],check=True)
p=subprocess.run([spec['binary'],'--version'],capture_output=True,text=True,check=True)
print(json.dumps({'binary_bytes':pathlib.Path(spec['binary']).stat().st_size,'version_output':p.stdout.strip(),'otel_image':image}))
''',spec)
        node=json.loads(reply.stdout)
        if node['binary_bytes']!=metadata['binary_bytes'] or VERSION not in node['version_output']:
            raise RuntimeError('Release installation differs on '+host)
        restrict_service(host,PORT)
        node.update({'host':host,'endpoint':endpoint,'metrics':'127.0.0.1:19464','memory_gib':memory,'cpus':16})
        nodes.append(node);(output/(host+'.service')).write_text(unit)
        print(json.dumps(node),flush=True)
    metadata.update({'nodes':nodes,'data_directory':str(BASE/'data'),'state':'prepared','release_url':'https://github.com/rustfs/rustfs/releases/tag/1.0.5'})
    (output/'deployment.json').write_text(json.dumps(metadata,indent=2)+'\n')


def validate():
    cl=coordinator()
    output=cl.RUN_ROOT/'deployment/rustfs-1.0.5'
    metadata=json.loads((output/'deployment.json').read_text())
    dyn='ax-cluster-direct-run001-rustfs-1'
    with cl.MeasurementLock():
        try:
            for host in OBJECTS:cl.run_on_host(host,['docker','stop','--time','60',dyn])
            for host in OBJECTS:
                cl.run_on_host(host,['docker','start',OTEL])
                cl.run_on_host(host,['systemctl','start',UNIT])
            deadline=time.monotonic()+120
            clients=[cl.make_s3_client(node['endpoint']) for node in metadata['nodes']]
            while True:
                try:
                    for client in clients:client.list_buckets()
                    break
                except Exception:
                    for host in OBJECTS:
                        state=cl.run_on_host(host,['systemctl','show',UNIT,'--property=ActiveState','--value'],log_cmd=False).stdout.strip()
                        if state==b'failed':raise RuntimeError('Official RustFS service failed on '+host)
                    if time.monotonic()>deadline:raise
                    time.sleep(2)
            bucket='ax-official-105-functional'
            buckets={item['Name'] for item in clients[0].list_buckets().get('Buckets',[])}
            if bucket not in buckets:clients[0].create_bucket(Bucket=bucket)
            payload=bytes(range(256))*16384
            clients[0].put_object(Bucket=bucket,Key='content',Body=payload)
            records=[]
            for node,client in zip(metadata['nodes'],clients):
                read=client.get_object(Bucket=bucket,Key='content')['Body'].read()
                if read!=payload:raise RuntimeError('Full object bytes differ on '+node['host'])
                for offset,length in [(0,4096),(123,65536),(2097152,1048576)]:
                    body=client.get_object(Bucket=bucket,Key='content',Range=f'bytes={offset}-{offset+length-1}')['Body'].read()
                    if body!=payload[offset:offset+length]:raise RuntimeError('Range bytes differ on '+node['host'])
                records.append({'host':node['host'],'full_read_bytes':len(read),'range_reads':3,'passed':True})
            time.sleep(3)
            for host in OBJECTS:
                prom=cl.run_on_host(host,['curl','--fail','--silent','http://127.0.0.1:19464/metrics'],log_cmd=False).stdout
                (output/(host+'-metrics.txt')).write_bytes(prom)
                if b'rustfs_' not in prom:raise RuntimeError('Official RustFS metrics are absent on '+host)
        finally:
            for host in OBJECTS:
                cl.run_on_host(host,['systemctl','stop',UNIT],check=False)
                cl.run_on_host(host,['docker','stop','--time','20',OTEL],check=False)
            for host in OBJECTS:
                cl.run_on_host(host,['docker','start',dyn])
                cl.run_on_host(host,['docker','restart','ax-cluster-otelcol-run001'])
            deadline=time.monotonic()+120
            while True:
                try:
                    for endpoint in cl.RUSTFS_ENDPOINTS:cl.make_s3_client(endpoint).list_buckets()
                    break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(2)
            time.sleep(3)
        metadata.update({'state':'validated-stopped','functional':records})
        (output/'deployment.json').write_text(json.dumps(metadata,indent=2)+'\n')
        print(json.dumps({'version':VERSION,'four_node_validation':'passed'}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['download','extract','configure','validate'])
    options=parser.parse_args()
    {'download':download,'extract':extract,'configure':configure,'validate':validate}[options.action]()
