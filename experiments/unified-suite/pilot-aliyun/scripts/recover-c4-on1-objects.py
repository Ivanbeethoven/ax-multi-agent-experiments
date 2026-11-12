#!/usr/bin/env python3
"""Repack the already-closed historical A/flat On1 objects evidence; never run a trial."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from bench import archive_check
from bench.runtime.common import write_new


CELL = 'c5e97774bcffa69a-r1-native'
TAG = 'c4-a-flat-on1'
ARCHIVE = f'archives/{TAG}-objects.tar.gz'
OBS = f'o/{TAG}'
Q = f'q/{TAG}-observer'
CLOSE = f'q/{TAG}-closure/result.json'
Q_FILES = ('observer.json', 'service-identities.json', 'rustfs-probe.txt', 'collector-probe.txt')
MARKS = tuple(f'mark-{i:04d}.json' for i in range(1, 7))
LEGACY_FILES = (f'{OBS}/ready.json', f'{OBS}/collector.log', *(f'{OBS}/{m}' for m in MARKS),
                *(f'{Q}/{n}' for n in Q_FILES), CLOSE)
MAX_REJECTED_ARCHIVE_BYTES = 1_000_000_000
MIN_MANIFEST_BYTES = 16*1024**2
MAX_MANIFEST_BYTES = 24*1024**2
FINISHED_SHA = '10056ccbbaa07f96bc1d5846b204f08614855a1e23a75c0ba7c39e37f76cd164'
FOLDED_SHA = '77c057ec66ab083f46aa8462febe9058a123d36676c83b6b552f38879c0a4725'
FOLD_RECEIPT_SHA = '1e9fea4d5a4931814942f925bd5373c194bb86b02fdd1baa571cbea7f2cada2a'
STREAM = dict(name='samples.ndjson', bytes=23_497_612_644,
              sha256='d46e937826d88931bb2238ad22f6f3e87677b546e6ed5b8df5d08dc5a7d8d5a0')
SAMPLES = 2954
SOURCE_MEMBERS = 132930


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path and
            stat.S_ISREG(path.lstat().st_mode), 'Expected exact regular file: '+str(path))
    return path


def digest(path):
    with file(path).open('rb') as src:
        return hashlib.file_digest(src, 'sha256').hexdigest()


def ref(path, sha=None):
    return dict(path=str(path), sha256=sha or digest(path))


def unique(pairs):
    obj = dict(pairs)
    require(len(obj) == len(pairs), 'Duplicate historical manifest key')
    return obj


def manifest(path):
    """Read only the first bounded legacy manifest; never relax the standard verifier."""
    require(0 < file(path).stat().st_size <= MAX_REJECTED_ARCHIVE_BYTES, 'Rejected original archive outside known size range')
    with tarfile.open(path, 'r|gz') as ar:
        item = ar.next()
        require(item and item.name == archive_check.MANIFEST and item.isfile() and
                MIN_MANIFEST_BYTES < item.size <= MAX_MANIFEST_BYTES, 'Unexpected historical rejected manifest')
        raw = ar.extractfile(item).read(item.size+1)
    require(len(raw) == item.size, 'Incomplete historical rejected manifest')
    info = json.loads(raw, object_pairs_hook=unique)
    require(info.get('schema') == 'suite-archive-manifest-v1' and info.get('cell_id') == CELL and
            info.get('kind') == 'objects' and info.get('deferred') == [],
            'Rejected manifest is not from historical A/flat On1')
    return info['files'], dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def entry(path):
    return dict(bytes=file(path).stat().st_size, sha256=digest(path))


def build(root, old_finished, fold_receipt, out):
    root, old_finished, fold_receipt, out = map(Path, (root, old_finished, fold_receipt, out))
    require(root.is_absolute() and root.is_dir() and root.resolve(strict=True) == root,
            'Exact evidence root required')
    require(out.is_absolute() and out.parent.resolve(strict=True) == out.parent and
            not out.is_relative_to(root) and not os.path.lexists(out),
            'New absolute output directory outside source tree required')
    original_archive = root/ARCHIVE
    members, legacy_manifest = manifest(original_archive)
    paths = {name:root/name for name in LEGACY_FILES}
    entries = {name:entry(path) for name,path in paths.items()}
    require(all(members.get(name) == value for name,value in entries.items()),
            'Historical archive does not bind the retained original members')
    original = entry(old_finished)
    require(original['sha256'] == FINISHED_SHA and members.get(f'{OBS}/finished.json') == original,
            'Retained original finished receipt does not match historical archive')
    paths[f'{OBS}/legacy-finished.json'] = file(old_finished)
    entries[f'{OBS}/legacy-finished.json'] = original
    for name in ('finished.json', 'fold-receipt.json', 'fold-receipt.ndjson'):
        member = f'{OBS}/{name}'
        paths[member] = root/member
        entries[member] = entry(paths[member])
    folded = paths[f'{OBS}/finished.json']
    inner_fold = paths[f'{OBS}/fold-receipt.json']
    require(entries[f'{OBS}/finished.json']['sha256'] == FOLDED_SHA and
            digest(fold_receipt) == FOLD_RECEIPT_SHA and
            file(fold_receipt).read_bytes() == file(inner_fold).read_bytes(),
            'Folded proof or separate fold receipt changed')
    result = json.loads(fold_receipt.read_bytes())
    end = json.loads(folded.read_bytes())
    ready = json.loads(paths[f'{OBS}/ready.json'].read_bytes())
    closure = json.loads(paths[CLOSE].read_bytes())
    ident = ready['process']
    require(ready.get('passed') is True and result.get('passed') is True and end.get('passed') is True and
            result['samples'] == end['samples'] == SAMPLES and result['source_members'] == SOURCE_MEMBERS and
            result['stream'] == end['stream'] == STREAM and
            entries[f'{Q}/observer.json']['sha256'] == ready['spec']['sha256'] == end['spec_sha256'] and
            ready['metric_contract_sha256'] == end['metric_contract_sha256'],
            'Fold, observer specification or metric identity differs')
    require(closure['cell_id'] == CELL and all(closure.get(k) is True for k in ('passed','all_exited','all_unmounted'))
            and sum(all(p.get(k) == ident[k] for k in ('pid','boot_id','start_ticks')) and p.get('alive',False) is False
                    for p in closure['processes']) == 1, 'Original closure does not bind the historical observer')
    require(not list((root/OBS).glob('sample-*')), 'Original sample tree still present; refusing to re-fold')
    stream_name = f"{OBS}/{STREAM['name']}"
    paths[stream_name] = file(root/stream_name)
    entries[stream_name] = {k:STREAM[k] for k in ('bytes','sha256')}
    require(paths[stream_name].stat().st_size == STREAM['bytes'], 'Folded stream size differs')

    original_archive_pin = ref(original_archive)
    out.mkdir(mode=0o700)
    archive = out/'objects.tar.gz'
    payload = (json.dumps(dict(schema='suite-archive-manifest-v1', cell_id=CELL, kind='objects',
                              files=entries, deferred=[]), sort_keys=True)+'\n').encode()
    with archive.open('xb') as sink:
        os.fchmod(sink.fileno(), 0o600)
        with tarfile.open(fileobj=sink, mode='w:gz', compresslevel=1) as packed:
            head = tarfile.TarInfo(archive_check.MANIFEST)
            head.size, head.mode = len(payload), 0o600
            packed.addfile(head, io.BytesIO(payload))
            for name,path in sorted(paths.items()):
                archive_check.safe(name)
                with file(path).open('rb') as source:
                    head = tarfile.TarInfo(name)
                    head.size, head.mode = entries[name]['bytes'], 0o600
                    packed.addfile(head, source)
        sink.flush()
        os.fsync(sink.fileno())
    archive_pin = ref(archive)
    checked = archive_check.verify(archive, archive_pin['sha256'])
    check = out/'objects-check.json'
    write_new(check, checked)
    recovery = dict(schema='c4-on1-recovered-objects-v1', cell_id=CELL, tag=TAG, archive=archive_pin, check=ref(check),
        original_archive=original_archive_pin | dict(bytes=original_archive.stat().st_size), legacy_manifest=legacy_manifest, original_finished=ref(old_finished, FINISHED_SHA),
        folded_finished=ref(folded, FOLDED_SHA), stream=STREAM,
        retained_legacy_members={name:entries[name] for name in LEGACY_FILES},
        new_measurement=False, formal_acceptance=False)
    write_new(out/'objects-pack.json', recovery)
    return recovery


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('root','original-finished','fold-receipt','out'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(build(args.root, args.original_finished, args.fold_receipt, args.out), indent=2))
