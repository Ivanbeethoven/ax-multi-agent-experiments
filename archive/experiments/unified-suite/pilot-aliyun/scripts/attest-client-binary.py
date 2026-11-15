#!/usr/bin/env python3
"""Read the qualified client's actual binary and original mount command; no mount or workload."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import runpy
import shlex
import subprocess
from datetime import datetime,timezone


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(path):return dict(path=str(Path(path).resolve(strict=True)),sha256=digest(path))
def load(path):return json.loads(Path(path).read_bytes())
def write(path,value):
    with Path(path).open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def original_route(filesystem,transfers,key=None):
    keys=[name for name in ('native_checks','complete_route','complete_base') if name in filesystem]
    if key is None:
        if len(keys)!=1:raise ValueError('Select one actual qualified route')
        key=keys[0]
    if key not in keys:raise ValueError('Requested qualified route is absent')
    route=filesystem[key]
    if digest(route['path'])!=route['sha256']:raise ValueError('Local qualified route changed')
    rows=[item for item in transfers if item.get('path')==route['path'] and item.get('sha256')==route['sha256']]
    if len(rows)!=1:raise ValueError('Unique original route transfer required')
    source=PurePosixPath(rows[0]['source'])
    if not source.is_absolute() or '..' in source.parts:raise ValueError('Absolute original route required')
    return route,str(source)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qualification','ssh-config','release','admitter','build-manifest'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--client-alias',required=True)
    p.add_argument('--route-key',choices=['native_checks','complete_route','complete_base'])
    p.add_argument('--mount-spec',required=True,help='Original remote mount specification containing command ref, or original command JSON')
    a=p.parse_args();folder=a.qualification.resolve(strict=True);root=folder.parent
    asset_path=root/'assets/juicefs/asset.json';asset=load(asset_path)
    if asset.get('product')!='juicefs':raise ValueError('This external client attestation is for JuiceFS')
    binary=asset['filesystem_binary'];route,route_source=original_route(load(folder/'filesystem.json'),load(root/'transfers.json'),a.route_key)
    source_root=PurePosixPath(route_source).parent;mount_source=PurePosixPath(a.mount_spec)
    if not mount_source.is_absolute() or '..' in mount_source.parts or not mount_source.is_relative_to(source_root):
        raise ValueError('Original mount specification must belong to the qualified route')
    out=folder/'client-binary';wrapper=folder/'client-binary.json'
    if wrapper.exists():raise ValueError('Never replace an existing attestation')
    out.mkdir(exist_ok=False)
    transfers=[]
    def remote(argv):
        return subprocess.check_output(['ssh','-F',str(a.ssh_config),a.client_alias,shlex.join(list(map(str,argv)))])
    def get(source,name,expected=None):
        wanted=remote(['sha256sum',source]).decode().split()[0]
        if expected is not None and expected!=wanted:raise ValueError('Original qualified evidence changed: '+source)
        data=remote(['cat',source]);path=out/name
        if hashlib.sha256(data).hexdigest()!=wanted:raise ValueError('Transferred bytes changed')
        with path.open('xb') as stream:stream.write(data)
        transfers.append(dict(source=source,**ref(path)));return path
    try:
        original=get(route_source,'original-route.json',route['sha256'])
        spec_path=get(str(mount_source),'original-mount-spec.json');spec=load(spec_path)
        if isinstance(spec,dict) and 'command' in spec:
            command=spec['command'];command_source=command['path']
            command_path=get(command_source,'original-command.json',command['sha256'])
        else:command_source=str(mount_source);command_path=spec_path
        remote_command=PurePosixPath(command_source)
        if not remote_command.is_absolute() or '..' in remote_command.parts or not remote_command.is_relative_to(source_root):
            raise ValueError('Original mount command must belong to the qualified route')
        installed_hash=remote(['sha256sum',binary['path']])
        with (out/'binary-sha256.stdout').open('xb') as stream:stream.write(installed_hash)
        if installed_hash.decode().split()[0]!=binary['sha256']:raise ValueError('Actual installed binary differs from prepared asset')
        version=remote([binary['path'],'--version'])
        with (out/'version.stdout').open('xb') as stream:stream.write(version)
        proof=dict(schema='pilot-external-client-binary-v1',passed=True,product='juicefs',binary=binary,
            version_output=version.decode(),release=ref(a.release),qualified_route=route,
            mount_command=ref(command_path),mount_command_source=command_source,
            collected_at=datetime.now(timezone.utc).isoformat(),source_tool=ref(Path(__file__)),
            original_mount_spec=ref(spec_path),qualified_route_copy=ref(original),
            binary_sha256_stdout=ref(out/'binary-sha256.stdout'),version_stdout=ref(out/'version.stdout'))
        write(out/'transfers.json',transfers)
        # The frozen admission helper performs the same checks used before claim.
        write(wrapper,proof)
        helper=runpy.run_path(str(a.admitter))['asset_binary_proof']
        accepted=helper(asset,'juicefs',load(a.build_manifest),folder)
        result=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),wrapper=ref(wrapper),
            admitted_by=ref(a.admitter),asset=ref(asset_path),verification=accepted,
            transfers=ref(out/'transfers.json'),scope='Read-only binary and original command attestation; no claim, mount, or workload')
        write(out/'result.json',result);print(json.dumps(result))
    except Exception as error:
        write(out/'failed.json',dict(passed=False,error=type(error).__name__+': '+str(error),automatic_retry=False))
        raise


if __name__=='__main__':main()
