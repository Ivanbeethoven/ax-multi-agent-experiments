#!/usr/bin/env python3
"""Pack or restore the fixed local-relay OpenCode input; no model invocation."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import tarfile

SOURCE=Path(__file__).with_name('prepare-session-input.py')
spec=importlib.util.spec_from_file_location('session_input_bytes',SOURCE)
shared=importlib.util.module_from_spec(spec);spec.loader.exec_module(shared)


def digest(p):
    with p.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def validate_source(root):
    checkpoint=json.loads((root/'checkpoint.json').read_text())
    if (checkpoint['source_process_exited'] is not True or checkpoint['sqlite_quick_check']!='ok' or
            checkpoint['contains_provider_master_key'] is not False):
        raise ValueError('Original closed and verified input checkpoint required')
    workspace=root/'workspace';tree=shared.tree(workspace)
    if digest(workspace/'session-export.json')!=checkpoint['session_export_sha256']:
        raise ValueError('Original export changed')
    for name,row in tree.items():
        if name.endswith(('.db-wal','.db-journal')) and row.get('bytes',0):
            raise ValueError('Unclosed SQLite journal in source checkpoint')
    provider=json.loads((workspace/'config/opencode.json').read_text())['provider']['experiment']['options']
    if provider['apiKey']!='local-session-no-master-key' or not provider['baseURL'].startswith('http://127.0.0.1:'):
        raise ValueError('Package only the frozen local-relay dummy configuration, never active provider credentials')
    return checkpoint,tree


def verify_archive(archive,receipt):
    if digest(archive)!=receipt['archive']['sha256']:raise ValueError('Checkpoint archive changed')
    expected={k:v for k,v in receipt['files'].items()};seen=set()
    links={k for k,v in expected.items() if v['type']=='symlink'}
    with tarfile.open(archive,'r:gz') as source:
        for member in source:
            name=member.name;p=PurePosixPath(name)
            if (name in seen or p.is_absolute() or '..' in p.parts or str(p)!=name or name not in expected or
                    any(str(parent) in links for parent in p.parents)):
                raise ValueError('Unexpected, duplicate or unsafe checkpoint member')
            seen.add(name);row=expected[name]
            if member.mode!=row['mode'] or member.mode&0o7000:
                raise ValueError('Checkpoint archive permissions changed')
            if row['type']=='directory':
                if not member.isdir():raise ValueError('Expected directory')
            elif row['type']=='symlink':
                if not member.issym() or member.linkname!=row['target']:raise ValueError('Expected internal symlink')
            else:
                if not member.isfile() or member.size!=row['bytes']:raise ValueError('Expected complete regular file')
                with source.extractfile(member) as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=row['sha256']:
                        raise ValueError('Checkpoint member bytes changed')
    if seen!=set(expected):raise ValueError('Checkpoint archive is incomplete')


def pack(root,out):
    checkpoint,tree=validate_source(root)
    if out.resolve().is_relative_to(root.resolve()):raise ValueError('Archive belongs outside source checkpoint')
    out.mkdir(parents=True,exist_ok=False)
    files={'checkpoint.json':dict(type='file',mode=(root/'checkpoint.json').stat().st_mode&0o777,
        bytes=(root/'checkpoint.json').stat().st_size,sha256=digest(root/'checkpoint.json')),
        'workspace':dict(type='directory',mode=(root/'workspace').stat().st_mode&0o777)}
    files.update({'workspace/'+name:row for name,row in tree.items()})
    archive=out/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',dereference=False) as target:
        for name in sorted(files):target.add(root/name,arcname=name,recursive=False)
    receipt=dict(passed=True,at=datetime.now(timezone.utc).isoformat(),files=files,
        archive=dict(bytes=archive.stat().st_size,sha256=digest(archive)),
        checkpoint_sha256=digest(root/'checkpoint.json'),session_id=checkpoint['session_id'],
        opencode_sha256=checkpoint['opencode_binary_sha256'],tree_reader_sha256=digest(SOURCE),
        contains_provider_master_key=False,contains_active_relay_key=False,
        scope='Frozen input with local dummy relay configuration; no model call or migration/performance result')
    verify_archive(archive,receipt)
    write(out/'source-tree.json',tree);write(out/'receipt.json',receipt)
    return {k:v for k,v in receipt.items() if k!='files'}


def install(archive,receipt_file,receipt_sha,out):
    if digest(receipt_file)!=receipt_sha:raise ValueError('Checkpoint receipt changed')
    receipt=json.loads(receipt_file.read_text())
    if digest(SOURCE)!=receipt['tree_reader_sha256']:raise ValueError('Checkpoint tree reader changed')
    verify_archive(archive,receipt)
    out.mkdir(parents=True,exist_ok=False,mode=0o700)
    with tarfile.open(archive,'r:gz') as source:source.extractall(out,filter='data')
    # Python's data filter intentionally ignores directory modes. Apply only
    # the reviewed ordinary permission bits after safe extraction.
    for name,row in receipt['files'].items():
        if row['type']!='symlink':(out/name).chmod(row['mode'])
    checkpoint,tree=validate_source(out)
    if digest(out/'checkpoint.json')!=receipt['checkpoint_sha256']:
        raise ValueError('Installed checkpoint differs')
    expected={name.removeprefix('workspace/'):row for name,row in receipt['files'].items() if name.startswith('workspace/')}
    if tree!=expected:raise ValueError('Installed complete workspace differs')
    write(out/'source-tree.json',tree)
    result=dict(passed=True,archive_sha256=digest(archive),receipt_sha256=receipt_sha,
        checkpoint_sha256=digest(out/'checkpoint.json'),files=len(tree),session_id=checkpoint['session_id'],
        scope='Complete input restoration only; no mount, model, import or resume')
    write(out/'install.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('pack');s.add_argument('--checkpoint',type=Path,required=True);s.add_argument('--out',type=Path,required=True)
    s=sub.add_parser('install')
    for name in ('archive','receipt','out'):s.add_argument('--'+name,type=Path,required=True)
    s.add_argument('--receipt-sha256',required=True)
    a=p.parse_args()
    value=pack(a.checkpoint,a.out) if a.action=='pack' else install(a.archive,a.receipt,a.receipt_sha256,a.out)
    print(json.dumps(value))
