#!/usr/bin/env python3
"""Package this batch's compiled RustFS; publish only to a loopback registry.

The registry data and image archives can later be copied to the one private
coordinator. This does not create cloud resources or start a storage service.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

BASE = 'debian@sha256:115ea8be71229531ceb61a08a1da3bb4342d1463e0f655012d70298c0d58cf8b'
URL = 'https://github.com/Ivanbeethoven/rustfs.git'
BRANCH = 'feat/dynamic-block-size-rebased'


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--registry-name', required=True)
    p.add_argument('--registry-port', type=int, default=15000)
    a = p.parse_args()
    if not re.fullmatch(r'ax-pilot-[a-z0-9-]+', a.registry_name):
        p.error('dedicated ax-pilot registry name required')
    if not 1024 <= a.registry_port <= 65535:
        p.error('unprivileged explicit port required')
    source = a.build_root/'src/rustfs'
    binary = a.build_root/'bin/rustfs'
    def capture(argv):
        return subprocess.check_output(argv, text=True).strip()
    commit = capture(['git','-C',str(source),'rev-parse','HEAD'])
    if capture(['git','-C',str(source),'status','--porcelain']):
        raise ValueError('preserve dirty source; do not package it')
    if capture(['git','-C',str(source),'branch','--show-current']) != BRANCH:
        raise ValueError('wrong RustFS branch')
    if capture(['git','ls-remote',URL,'refs/heads/'+BRANCH]).split()[0] != commit:
        raise ValueError('RustFS advanced; rebuild its latest binary first')
    if digest(binary) != digest(a.build_root/'targets/rustfs/release/rustfs'):
        raise ValueError('distributed RustFS is not the compiled output')
    attempt=Path((a.build_root/'receipts/rustfs.last-attempt').read_text().strip())
    proof_path=attempt/'attestation-finished.json'
    proof=json.loads(proof_path.read_text())
    if proof.get('passed') is not True or proof['source']['source_commit']!=commit or \
            proof['outputs']!=[dict(file='rustfs',bytes=binary.stat().st_size,sha256=digest(binary))]:
        raise ValueError('RustFS build/source attestation does not match the binary')
    # A stopped container with this name is also preserved for inspection.
    if subprocess.run(['docker','container','inspect',a.registry_name],capture_output=True).returncode == 0:
        raise ValueError('registry name already exists; inspect, do not replace')
    a.out.mkdir(parents=True, exist_ok=False)
    def run(argv, name, **kw):
        with (a.out/(name+'.log')).open('xb') as log:
            subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=True, **kw)
    context = a.out/'context'; context.mkdir()
    shutil.copyfile(binary, context/'rustfs')
    shutil.copyfile(source/'entrypoint.sh', context/'entrypoint.sh')
    shutil.copyfile('/etc/ssl/certs/ca-certificates.crt', context/'ca-certificates.crt')
    (context/'Dockerfile').write_text(f'''FROM {BASE}
LABEL org.opencontainers.image.revision="{commit}"
LABEL org.opencontainers.image.source="{URL}"
LABEL org.opencontainers.image.version="branch-{commit}"
COPY --chmod=0755 rustfs /usr/bin/rustfs
COPY --chmod=0755 entrypoint.sh /usr/bin/rustfs-entrypoint.sh
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt RUSTFS_UID=10001 RUSTFS_GID=10001
RUN groupadd -g 10001 rustfs && useradd -u 10001 -g 10001 -M -s /usr/sbin/nologin rustfs && mkdir -p /data /var/log/rustfs && chown 10001:10001 /data /var/log/rustfs
USER 10001:10001
ENTRYPOINT ["/usr/bin/rustfs-entrypoint.sh"]
''')
    reference = f'localhost:{a.registry_port}/ax-pilot/rustfs:{commit}'
    run(['docker','build','--pull=false','--network=none','--tag',reference,str(context)], 'image-build')
    run(['docker','run','--rm','--network','none','--entrypoint','/usr/bin/rustfs',reference,'--version'], 'image-version')
    runtime_hash = capture(['docker','run','--rm','--network','none','--entrypoint','sha256sum',reference,'/usr/bin/rustfs']).split()[0]
    if runtime_hash != digest(binary):
        raise ValueError('image binary differs from compiled binary')
    # Resolve and retain the registry image's digest once, not a mutable tag in deployment.
    run(['docker','pull','registry:2'], 'registry-pull')
    registry = json.loads(capture(['docker','image','inspect','registry:2']))[0]
    registry_image = next(d for d in registry['RepoDigests'] if '@sha256:' in d)
    registry_data = a.out/'registry-data'; registry_data.mkdir()
    run(['docker','run','-d','--name',a.registry_name,'--label','ax.pilot.owner='+a.registry_name,
         '-p',f'127.0.0.1:{a.registry_port}:5000','-v',f'{registry_data}:/var/lib/registry',registry_image], 'registry-start')
    # Bounded readiness checks do not restart or replace a failed registry.
    import time
    from urllib.request import urlopen
    for _ in range(30):
        try:
            with urlopen(f'http://127.0.0.1:{a.registry_port}/v2/', timeout=2) as response:
                if response.status == 200: break
        except OSError:
            time.sleep(.25)
    else:
        raise RuntimeError('loopback registry did not become ready; retain container/logs')
    run(['docker','push',reference], 'image-push')
    run(['docker','pull',reference], 'image-pull')
    image = json.loads(capture(['docker','image','inspect',reference]))[0]
    if image['Config']['Labels']['org.opencontainers.image.revision'] != commit:
        raise ValueError('OCI revision mismatch')
    immutable = next(d for d in image['RepoDigests'] if d.startswith(f'localhost:{a.registry_port}/ax-pilot/rustfs@sha256:'))
    run(['docker','stop',a.registry_name], 'registry-stop')
    # Never remove another container or the registry data; keep restartable provenance.
    run(['docker','image','save','--output',str(a.out/'registry-image.tar'),registry_image], 'registry-export')
    run(['docker','image','save','--output',str(a.out/'rustfs-image.tar'),reference], 'rustfs-export')
    run(['tar','-C',str(registry_data),'-cf',str(a.out/'registry-data.tar'),'.'], 'registry-data-export')
    receipt = dict(resolved_at=datetime.now(timezone.utc).isoformat(), repository=URL, branch=BRANCH,
        source_commit=commit, base_image=BASE, binary_sha256=digest(binary), image_reference=immutable,
        build_attestation_sha256=digest(proof_path),
        image_digest=immutable.split('@')[1], image_id=image['Id'], registry_image=registry_image,
        registry_container=a.registry_name, registry_running=False,
        context={f.name:digest(f) for f in context.iterdir()},
        archives={f.name:dict(bytes=f.stat().st_size,sha256=digest(f)) for f in a.out.glob('*.tar')},
        cloud_instances_started=False,
        note='Runtime version and exact binary checked; S3/layout/mount qualification remains separate.')
    (a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
