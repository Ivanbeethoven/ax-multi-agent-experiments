#!/usr/bin/env python3
"""Upload a verified cloud evidence file to a new public OSS object over the VPC.

Run locally. The access key secret stays local; the cloud receives a short-lived signed
PUT, on stdin, and never writes it to disk. No bulk evidence crosses the laptop.
"""
import argparse
import datetime
import hashlib
import hmac
import json
from pathlib import Path
import shlex
import subprocess
import urllib.parse
import urllib.request


REMOTE = r'''
import base64,hashlib,http.client,json,os,pathlib,re,sys,tarfile,urllib.parse
s=json.load(sys.stdin)
try:
 root=pathlib.Path('/srv/ax-pilot-control').resolve(strict=True)
 path=pathlib.Path(s['source'])
 if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
  raise ValueError('Source must be an owned regular coordinator evidence file')
 blocked={'private','.ssh','.aliyun','credentials','config.toml','rustfs.env'}
 if any(v.lower() in blocked or v.lower().endswith('.env') for v in path.parts):
  raise ValueError('Private source path rejected')
 initial=path.stat()
 identity=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
 secret=re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|LTAI[A-Za-z0-9]{12,}|(?:"access_key_secret"|"aws_secret_access_key")\s*:\s*"[^"\s]{8,}"',re.I)
 def scan(stream):
  tail=b''
  while True:
   data=stream.read(1024*1024)
   if not data:break
   if secret.search(tail+data):raise ValueError('Possible credential found; public upload rejected')
   tail=(tail+data)[-256:]
 def archive(stream,depth=0):
  if depth>3:raise ValueError('Archive nesting exceeds the audit limit')
  with tarfile.open(fileobj=stream,mode='r|*') as tar:
   for member in tar:
    name=pathlib.PurePosixPath(member.name)
    if name.is_absolute() or '..' in name.parts or member.issym() or member.islnk():
     raise ValueError('Unsafe evidence archive member')
    if any(v.lower() in blocked or v.lower().endswith('.env') for v in name.parts):
     raise ValueError('Private evidence archive member rejected')
    if member.isdir():continue
    if not member.isfile():raise ValueError('Special evidence archive member rejected')
    with tar.extractfile(member) as incoming:
     if member.name.endswith(('.tar.gz','.tgz','.tar')):archive(incoming,depth+1)
     else:scan(incoming)
 if s['action']=='prepare':
  with path.open('rb') as f:
   if path.name.endswith(('.tar.gz','.tgz','.tar')):archive(f)
   else:scan(f)
  sha=hashlib.sha256();md5=hashlib.md5()
  with path.open('rb') as f:
   while data:=f.read(4*1024*1024):sha.update(data);md5.update(data)
  if identity(path.stat())!=identity(initial) or sha.hexdigest()!=s['sha256']:
   raise ValueError('Source changed or expected SHA differs')
  print(json.dumps(dict(passed=True,source=str(path),sha256=sha.hexdigest(),bytes=initial.st_size,
   content_md5=base64.b64encode(md5.digest()).decode(),identity=identity(initial),credential_scan='passed')))
 elif s['action']=='upload':
  if list(identity(initial))!=s['prepared']['identity']:raise ValueError('Prepared source identity changed')
  parsed=urllib.parse.urlsplit(s['signed_url'])
  if (parsed.scheme!='https' or parsed.netloc!=s['host'] or parsed.path!=s['object_path']
      or not parsed.netloc.endswith('.oss-cn-hangzhou-internal.aliyuncs.com')):
   raise ValueError('Signed endpoint differs')
  connection=http.client.HTTPSConnection(parsed.netloc,timeout=180)
  connection.putrequest('PUT',parsed.path+'?'+parsed.query,skip_host=True,skip_accept_encoding=True)
  for key,value in s['headers'].items():connection.putheader(key,value)
  connection.endheaders()
  sha=hashlib.sha256();sent=0
  with path.open('rb') as f:
   while data:=f.read(4*1024*1024):connection.send(data);sha.update(data);sent+=len(data)
  response=connection.getresponse();status=response.status
  request_id=response.getheader('x-oss-request-id');etag=response.getheader('etag');response.read();connection.close()
  if status!=200:raise ValueError('OSS PUT rejected with HTTP '+str(status))
  if identity(path.stat())!=identity(initial) or sha.hexdigest()!=s['prepared']['sha256'] or sent!=initial.st_size:
   raise ValueError('Uploaded source changed')
  connection=http.client.HTTPSConnection(parsed.netloc,timeout=180)
  connection.request('GET',s['object_path']);response=connection.getresponse()
  if response.status!=200:raise ValueError('Public internal readback rejected')
  received=0;readback=hashlib.sha256()
  while data:=response.read(4*1024*1024):readback.update(data);received+=len(data)
  connection.close()
  if received!=sent or readback.hexdigest()!=sha.hexdigest():raise ValueError('Complete OSS readback differs')
  print(json.dumps(dict(passed=True,bytes=sent,sha256=sha.hexdigest(),readback_sha256=readback.hexdigest(),
   full_internal_readback=True,put_request_id=request_id,etag=etag,source=str(path),overwrite=False)))
 else:raise ValueError('Unknown operation')
except Exception as error:
 # Never echo URL, request, exception internals, service body, or signing data.
 print(json.dumps(dict(passed=False,error=str(error) if isinstance(error,ValueError) else type(error).__name__)))
 sys.exit(1)
'''


def signed_put(profile, bucket, key, headers, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    date = now.strftime('%Y%m%d')
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    scope = date+'/cn-hangzhou/oss/aliyun_v4_request'
    host = bucket+'.oss-cn-hangzhou-internal.aliyuncs.com'
    headers = {k.lower(): str(v).strip() for k, v in headers.items()}
    headers['host'] = host
    extra = 'content-length;host'
    query = {'x-oss-signature-version':'OSS4-HMAC-SHA256',
             'x-oss-credential':profile['access_key_id']+'/'+scope,
             'x-oss-date':stamp,'x-oss-expires':'900','x-oss-additional-headers':extra}
    if profile.get('sts_token'):
        query['x-oss-security-token'] = profile['sts_token']
    quote = lambda value: urllib.parse.quote(str(value), safe='-_.~')
    encoded = '&'.join(quote(k)+'='+quote(v) for k,v in sorted(query.items()))
    canonical_headers = ''.join(k+':'+headers[k]+'\n' for k in sorted(headers))
    canonical = '\n'.join(['PUT',urllib.parse.quote('/'+bucket+'/'+key,safe='/-_.~'),
                           encoded,canonical_headers,extra,'UNSIGNED-PAYLOAD'])
    string = '\n'.join(['OSS4-HMAC-SHA256',stamp,scope,hashlib.sha256(canonical.encode()).hexdigest()])
    signing = ('aliyun_v4'+profile['access_key_secret']).encode()
    for value in (date,'cn-hangzhou','oss','aliyun_v4_request'):
        signing = hmac.new(signing,value.encode(),hashlib.sha256).digest()
    signature = hmac.new(signing,string.encode(),hashlib.sha256).hexdigest()
    path = urllib.parse.quote('/'+key,safe='/-_.~')
    return 'https://'+host+path+'?'+encoded+'&x-oss-signature='+signature, headers


def cloud(args, payload):
    process = subprocess.run([args.ssh,'-F',args.ssh_config,args.alias,
        'nice -n 15 ionice -c 3 python3 -c '+shlex.quote(REMOTE)],input=json.dumps(payload).encode(),capture_output=True)
    try:
        result = json.loads(process.stdout)
    except (ValueError, UnicodeError):
        raise RuntimeError('Cloud transfer failed; no raw transport response logged') from None
    if process.returncode or result.get('passed') is not True:
        raise RuntimeError(result.get('error','Cloud transfer failed'))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--object',required=True,help='New key under pilot-results/20261111/')
    p.add_argument('--bucket',default='brewfs-agent-traces-20261023-4ccdae73')
    p.add_argument('--ssh',default='ssh')
    p.add_argument('--ssh-config',required=True)
    p.add_argument('--alias',default='ax-pilot-jump')
    p.add_argument('--credentials',type=Path,default=Path.home()/'.aliyun/config.json')
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    if a.out.exists():raise ValueError('Receipt must be new')
    if (not a.object.startswith('pilot-results/20261111/') or '..' in a.object.split('/') or
        len(a.sha256)!=64 or any(c not in '0123456789abcdef' for c in a.sha256)):
        raise ValueError('Explicit new experiment prefix and expected SHA required')
    prepared = cloud(a,dict(action='prepare',source=a.source,sha256=a.sha256))
    config = json.loads(a.credentials.read_text(encoding='utf-8-sig'))
    profile = next(v for v in config['profiles'] if v['name']==config['current'])
    if profile['mode'] not in ('AK','StsToken'):raise ValueError('Unsupported local credential mode')
    headers = {'content-length':str(prepared['bytes']),'content-type':'application/octet-stream',
        'content-md5':prepared['content_md5'],'x-oss-meta-sha256':a.sha256,
        'x-oss-forbid-overwrite':'true','x-oss-object-acl':'public-read'}
    signed, headers = signed_put(profile,a.bucket,a.object,headers)
    parsed = urllib.parse.urlsplit(signed)
    result = cloud(a,dict(action='upload',source=a.source,prepared=prepared,signed_url=signed,
        host=parsed.netloc,object_path=parsed.path,headers=headers))
    public = 'https://'+a.bucket+'.oss-cn-hangzhou.aliyuncs.com'+parsed.path
    with urllib.request.urlopen(urllib.request.Request(public,method='HEAD'),timeout=30) as response:
        if response.status!=200 or int(response.headers['Content-Length'])!=prepared['bytes']:
            raise ValueError('Anonymous public HEAD differs')
    receipt = dict(schema='pilot-cloud-oss-archive-v1',at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        public_url=public,bucket=a.bucket,key=a.object,prepared=prepared,upload=result,
        anonymous_public_head=True,access_key_secret_sent_to_cloud=False,
        access_key_identifier_in_signed_url=True,signed_url_persisted=False,
        bytes_downloaded_to_local=0,tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps(dict(passed=True,public_url=public,bytes=result['bytes'],receipt=str(a.out))))


if __name__=='__main__':
    main()
