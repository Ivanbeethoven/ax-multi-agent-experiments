#!/usr/bin/env python3
"""Link an experiment-only Rust helper to the exact completed BrewFS library."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

FEATURES = {'fuse-io-uring-runtime','workspace-overlay','native-packed-base',
            'frozen-base-metadata','rustfs-ec-block-size-hint','observability'}
HELPER_DEPS = {'anyhow','futures_util','serde_json','sha2','uuid','tokio','hex','async_trait','bytes'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_artifacts(release, document):
    result = {}
    for _, name, _, fingerprint in document['deps']:
        # Only the helpers' direct crates need --extern. Cargo also lists build
        # scripts and renamed crates; transitive crates resolve via -L dependency.
        if name not in HELPER_DEPS:
            continue
        matches = []
        for path in (release/'.fingerprint').glob('*/lib-'+name):
            raw = path.read_text().strip()
            if len(raw) == 16 and int.from_bytes(bytes.fromhex(raw), 'little') == fingerprint:
                suffix = path.parent.name.rsplit('-',1)[1]
                candidates = [release/'deps'/('lib'+name+'-'+suffix+ext) for ext in ('.rlib','.so')]
                matches.extend(p for p in candidates if p.is_file())
        if len(matches) != 1:
            raise ValueError(f'Exact dependency artifact unresolved for {name}: {matches}')
        result[name] = matches[0]
    if set(result) != HELPER_DEPS:
        raise ValueError('Final library lacks a required helper dependency')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--test', action='store_true', help='Build this helper/module test harness, not a deployable reader')
    args = parser.parse_args()
    if not re.fullmatch('[a-z][a-z0-9_]+', args.name):
        raise ValueError('Rust crate name required')
    release = args.build_root/'targets/brewfs/release'
    found = []
    for file in (release/'.fingerprint').glob('brewfs-*/lib-brewfs.json'):
        document = json.loads(file.read_text())
        if set(json.loads(document['features'])) == FEATURES and file.with_name('lib-brewfs').exists():
            found.append((file, document))
    if len(found) != 1:
        raise ValueError('Wait for the completed, exact observability-enabled BrewFS build')
    fingerprint, document = found[0]
    library = release/'deps/libbrewfs.rlib'
    if not library.exists():
        raise FileNotFoundError(library)
    attempt = Path((args.build_root/'receipts/brewfs.last-attempt').read_text().strip())
    attestation = attempt/'attestation-finished.json'
    finished = json.loads(attestation.read_text())
    source_checkout = args.build_root/'src/brewfs'
    head = subprocess.check_output(['git','-C',str(source_checkout),'rev-parse','HEAD'],text=True).strip()
    if finished.get('passed') is not True or finished['source']['source_commit'] != head or \
            finished['brewfs_library_sha256'] != digest(library):
        raise ValueError('Helper library must match the finished current BrewFS build')
    if subprocess.check_output(['git','-C',str(source_checkout),'status','--porcelain'],text=True).strip():
        raise ValueError('BrewFS source changed after building')
    dependencies = dependency_artifacts(release, document)
    env = dict(os.environ, RUSTUP_TOOLCHAIN='stable')
    rustc = Path.home()/'.cargo/bin/rustc'
    args.out.mkdir(parents=True, exist_ok=False)
    source = args.out/(args.name+'.rs')
    shutil.copyfile(args.source, source)
    local_modules = []
    if args.source.name == 'verify-entity-remote.rs':
        module = args.source.with_name('verify-object-cache.rs')
        destination = args.out/module.name
        shutil.copyfile(module,destination)
        local_modules.append(dict(file=destination.name,sha256=digest(destination)))
    output = args.out/args.name
    command = [str(rustc),'--edition=2024','--crate-name',args.name,str(source),
               '-C','opt-level=1','-C','debuginfo=0','-L','dependency='+str(release/'deps'),
               '--extern','brewfs='+str(library),'-o',str(output)]
    if args.test:
        command.append('--test')
    for name, path in sorted(dependencies.items()):
        command += ['--extern',name+'='+str(path)]
    native = set()
    for file in (release/'build').glob('*/output'):
        for line in file.read_text().splitlines():
            if line.startswith(('cargo:rustc-link-search=native=', 'cargo::rustc-link-search=native=')):
                native.add(line.split('native=',1)[1])
    for path in sorted(native):
        command += ['-L','native='+path]
    receipt = dict(at=datetime.now(timezone.utc).isoformat(), argv=command,
                   source_sha256=digest(source), brewfs_library_sha256=digest(library),
                   brewfs_source_commit=head, build_attestation_sha256=digest(attestation),
                   local_modules=local_modules,
                   fingerprint_sha256=digest(fingerprint), features=sorted(FEATURES),
                   dependencies={name:dict(path=str(p),sha256=digest(p)) for name,p in dependencies.items()},
                   rustc=subprocess.check_output([str(rustc),'-Vv'],env=env,text=True))
    (args.out/'started.json').write_text(json.dumps(receipt,indent=2)+'\n')
    with (args.out/'build.log').open('xb') as log:
        code = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    receipt.update(exit_code=code)
    if code == 0:
        if digest(library) != receipt['brewfs_library_sha256']:
            raise ValueError('BrewFS library changed during helper linking')
        receipt['binary_sha256'] = digest(output)
    (args.out/'result.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if code:
        raise SystemExit(code)
    print(json.dumps(dict(binary=str(output),sha256=receipt['binary_sha256'])))


if __name__ == '__main__':
    main()
