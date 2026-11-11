#!/usr/bin/env python3
"""Bind a new client build to unchanged, previously verified service assets.

Permits environment setup only. It preserves workspace failures and does not
qualify any cloud performance cell. New proofs must be public copies of actual
receipts; credentials and service data are never read.
"""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path


def sha(path):
    import hashlib
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('previous-gate','build-root','binary-bundle','latest','checks-root','out'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();old=json.loads(a.previous_gate.read_text());root=a.build_root
    loader=importlib.util.spec_from_file_location('pilot_cloud',Path(__file__).with_name('cloud.py'))
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
    checker=module.Cloud('unused',a.out.parent)
    checker.verify_local_receipts(old)
    manifest=root/'receipts/build-manifest.json';build=json.loads(manifest.read_text())
    latest=json.loads(a.latest.read_text());bundle=json.loads(a.binary_bundle.read_text())
    if not old['ready_for_cloud'] or not latest['passed'] or latest['build_manifest_sha256']!=sha(manifest):
        raise ValueError('Previous verified setup and matching current branch check required')
    previous=json.loads(Path(old['build_manifest_path']).read_text())
    old_rust=next(x for x in previous['sources'] if x['component']=='rustfs')
    new_rust=next(x for x in build['sources'] if x['component']=='rustfs')
    if old_rust!=new_rust:raise ValueError('Service build changed; cannot reuse old image qualification')
    old_rust_binary=next(x for x in previous['files'] if x['file']=='rustfs')
    new_rust_binary=next(x for x in build['files'] if x['file']=='rustfs')
    if old_rust_binary!=new_rust_binary:raise ValueError('RustFS binary changed')
    if bundle['passed'] is not True or bundle['source_commit']!=next(x['source_commit'] for x in build['sources'] if x['component']=='brewfs'):
        raise ValueError('Current client bundle differs')
    proofs={name:old['proofs'][name] for name in ('image','rustfs_layout','collector','complete_trace')}
    def proof(name,path,equals):
        value=json.loads(path.read_text())
        if any(type(value.get(k)) is not type(v) or value[k]!=v for k,v in equals.items()):
            raise ValueError('Current local proof differs: '+name)
        proofs[name]=dict(path=str(path),sha256=sha(path),equals=equals)
    proof('current_flat',a.checks_root/'native-ca07159-guards/flat-fresh-database-guarded.json',
          dict(passed=True,reopened_writes_persisted=True,metadata_database_empty_verified=True))
    proof('current_packed',a.checks_root/'c3-packed-ca07159-100k/result.json',
          dict(passed=True,files=100000,full_base_verified=True,immutable_source_unchanged=True,mount_present=False,process_alive=False))
    proof('current_workspace_failure',a.checks_root/'native-ca07159-guards/workspace-guarded-failure.json',
          dict(passed=False,clean_reopened_tree_equal_before_write=True,mount_present=False,process_alive=False))
    proof('current_branch',a.latest,dict(passed=True,build_manifest_sha256=sha(manifest)))
    binaries=[dict(path=str(root/'bin'/x['file']),sha256=x['sha256']) for x in build['files']]
    binaries += [x for x in old['binaries'] if Path(x['path']).name in ('juicefs','opencode')]
    result=dict(ready_for_cloud=True,at=datetime.now(timezone.utc).isoformat(),
        binaries_verified=True,rustfs_image_verified=True,local_routes_checked=True,dependencies_prepared=True,
        build_manifest_path=str(manifest),build_manifest_sha256=sha(manifest),proofs=proofs,binaries=binaries,
        deployment_bundle=dict(receipt=str(a.binary_bundle),receipt_sha256=sha(a.binary_bundle),
            archive=bundle['archive'],sha256=bundle['sha256']),
        unchanged_service_deployment_bundle=old['deployment_bundle'],
        previous_gate=dict(path=str(a.previous_gate),sha256=sha(a.previous_gate)),
        scope='Second private pool environment setup using unchanged RustFS/services/dependencies and a new client overlay; no cloud performance qualification. Workspace reopen-write remains failed.')
    checker.verify_local_receipts(result)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(ready_for_cloud=True,scope=result['scope'],proofs=list(proofs))))


if __name__=='__main__':main()
