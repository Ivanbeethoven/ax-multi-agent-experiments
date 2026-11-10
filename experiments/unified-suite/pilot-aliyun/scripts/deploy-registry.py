#!/usr/bin/env python3
"""Restore the verified RustFS registry on the campaign's private coordinator."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tarfile
from urllib.request import ProxyHandler, Request, build_opener
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--verify-existing', action='store_true', help='Finish verification after a preserved startup failure; never restart')
    a = p.parse_args()
    if socket.gethostname() != 'ax-c1-jump' or not Path('/srv/ax-pilot-control').is_mount():
        raise ValueError('Dedicated coordinator disk required')
    if not a.verify_existing:
        a.out.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((a.assets/'provenance/rustfs-image.json').read_text())
    for name in ('registry-image.tar', 'registry-data.tar'):
        with (a.assets/'images'/name).open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest() != receipt['archives'][name]['sha256']:
                raise ValueError('Registry archive differs')
    name = 'ax-pilot-20261111-registry'
    if not a.verify_existing and subprocess.run(['docker','inspect',name], capture_output=True).returncode == 0:
        raise ValueError('Preserve existing container')
    data = a.out/'data'
    if not a.verify_existing:
        data.mkdir()
        with tarfile.open(a.assets/'images/registry-data.tar') as t:
            for m in t.getmembers():
                target = (data/m.name).resolve()
                if not target.is_relative_to(data.resolve()) or not (m.isdir() or m.isfile()):
                    raise ValueError('Unexpected registry archive member')
            t.extractall(data, filter='data')
        with (a.out/'load.log').open('xb') as log:
            subprocess.run(['docker','load','-i',str(a.assets/'images/registry-image.tar')],check=True,stdout=log,stderr=subprocess.STDOUT)
    # docker save can retain the digest as a repository tag rather than a
    # RepoDigest. Resolve it to its actual config hash before starting.
    with tarfile.open(a.assets/'images/registry-image.tar') as t:
        manifest = json.load(t.extractfile('manifest.json'))
        config = t.extractfile(manifest[0]['Config']).read()
    config_id = 'sha256:'+hashlib.sha256(config).hexdigest()
    # Docker's containerd store names a loaded image by its OCI manifest;
    # the classic image store names it by its config. Check both explicitly.
    candidates = [receipt['registry_image'].split('@')[1], config_id]
    image = next((candidate for candidate in candidates if subprocess.run(
        ['docker','image','inspect',candidate],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0),None)
    if image is None:
        raise ValueError('Neither the verified OCI manifest nor its config is loaded')
    argv=['docker','run','--pull','never','-d','--name',name,'--label','ax.pilot.owner='+name,
          '--restart','no','--memory','512m','--cpus','0.5','-p','10.20.0.10:15000:5000',
          '-v',str(data)+':/var/lib/registry',image]
    if a.verify_existing:
        row=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
        if row['Config']['Labels'].get('ax.pilot.owner')!=name or not row['State']['Running'] or \
                not any(m['Source']==str(data) and m['Destination']=='/var/lib/registry' for m in row['Mounts']):
            raise ValueError('Expected original live registry and its data directory')
        cid=row['Id']
    else:
        (a.out/'intent.json').write_text(json.dumps(argv)+'\n')
        cid=subprocess.check_output(argv,text=True).strip()
    digest=receipt['image_reference'].split('@')[1]
    url='http://10.20.0.10:15000/v2/ax-pilot/rustfs/manifests/'+digest
    opener=build_opener(ProxyHandler({}))
    for i in range(30):
        try:
            request=Request(url,headers={'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
            with opener.open(request,timeout=3) as r:
                raw=r.read(); headers=dict(r.headers)
            break
        except OSError:
            if i==29: raise
            time.sleep(1)
    if 'sha256:'+hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Restored OCI manifest differs')
    (a.out/'rustfs-manifest.json').write_bytes(raw)
    (a.out/'result.json').write_text(json.dumps(dict(passed=True,at=datetime.now(timezone.utc).isoformat(),
        container_id=cid,registry_image_id=image,image_reference='10.20.0.10:15000/ax-pilot/rustfs@'+digest,
        source_commit=receipt['source_commit'],private_listener='10.20.0.10:15000'),indent=2)+'\n')
    print((a.out/'result.json').read_text())


if __name__=='__main__':
    main()
