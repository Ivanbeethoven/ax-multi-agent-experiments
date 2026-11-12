#!/usr/bin/env python3
"""Pack a closed C4 role archive without copying its observer tree.

The reviewed request describes the same role, root, tag, cell and ordered
directory allowlist as run-c4's archive stage. Run after that stage's existing
per-role closure proof; this tool does not stop or start any process.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from types import ModuleType


ROOTS = dict(client='/srv/ax-client', metadata='/srv/ax-metadata', objects='/srv/ax-objects-0')
CODE_FILES = {
    'bench/__init__.py', 'bench/archive_check.py', 'bench/experiment_io.py', 'bench/experiment_step.py',
    'bench/runtime/__init__.py', 'bench/runtime/common.py', 'bench/runtime/observe.py',
}
MARKS = ['measure-start', 'measure-end', 'drain-end', 'verify-end']
ID_KEYS = ('pid', 'boot_id', 'start_ticks')
SCRATCH_ROOT = '/srv/ax-c4-observer-scratch'
LEGACY_FINISHED = 'legacy-finished.json'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(path):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p, 'Noncanonical or linked evidence path: '+str(p))
    return p


def verify_scratch(storage):
    """Recheck the immutable setup receipt and actual live observer-only mount."""
    require(storage['root']==SCRATCH_ROOT, 'Only the dedicated C4 observer root is allowed')
    root = canonical(storage['root'])
    value = load(storage['worker_receipt'], root/'storage.json')
    keys = ('root','disk_id','serial','uuid','device','boot_id','ownership_sha256')
    require(value['schema']=='c4-observer-scratch-storage-v1' and value['passed'] is True and
            value['applied'] is True and all(value[k]==storage[k] for k in keys) and
            root.stat().st_dev==storage['device'] and os.path.ismount(root) and
            Path('/proc/sys/kernel/random/boot_id').read_text().strip()==storage['boot_id'],
            'Original scratch receipt, boot or mount device changed')
    fs = json.loads(subprocess.check_output(['findmnt','--json','--mountpoint',str(root),
        '--output','SOURCE,TARGET,FSTYPE,UUID,OPTIONS'],text=True))['filesystems']
    require(len(fs)==1 and fs[0]['target']==str(root) and fs[0]['fstype']=='ext4' and
            fs[0]['uuid']==storage['uuid'] and 'rw' in fs[0]['options'].split(',') and
            'noatime' in fs[0]['options'].split(','), 'Scratch filesystem identity/options changed')
    block = json.loads(subprocess.check_output(['lsblk','--json','--bytes','--output','TYPE,SERIAL,SIZE',fs[0]['source']],
                                              text=True))['blockdevices']
    require(len(block)==1 and block[0]['type']=='disk' and not block[0].get('children') and
            block[0]['serial'].strip()==storage['serial'] and block[0]['size']==100*1024**3,
            'Scratch is no longer the exact owned 100GiB whole disk')
    for name in ('o','q','archives','evidence'):
        require(canonical(root/name).is_dir() and (root/name).stat().st_dev==storage['device'],
                'Scratch subtree escaped its device')
    return root


def pin(ref, expected=None):
    p = canonical(ref['path'])
    require((expected is None or p == expected) and p.is_file(), 'Misplaced pinned evidence: '+str(p))
    with p.open('rb') as data:
        require(hashlib.file_digest(data, 'sha256').hexdigest() == ref['sha256'], 'Changed pinned evidence: '+str(p))
    return p


def load(ref, expected=None):
    p = canonical(ref['path'])
    require((expected is None or p == expected) and p.is_file(), 'Misplaced pinned evidence: '+str(p))
    raw = p.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Changed pinned evidence: '+str(p))
    return json.loads(raw)


def tools(spec):
    code = canonical(spec['code'])
    paths = [pin(r) for r in spec['code_pins']]
    require(len(paths) == len(set(paths)) and all(p.is_relative_to(code) for p in paths) and
            CODE_FILES <= {p.relative_to(code).as_posix() for p in paths}, 'Original CODE pins incomplete')
    sys.path.insert(0, str(code))
    modules = [importlib.import_module(n) for n in ('bench.archive_check', 'bench.runtime.common',
                                                    'bench.runtime.observe')]
    for name, module in zip(('bench/archive_check.py', 'bench/runtime/common.py', 'bench/runtime/observe.py'), modules):
        require(canonical(module.__file__) == code/name, 'Unexpected module loaded outside original CODE')
    return modules


def format_module(body, name, package):
    """Load the same pinned source bytes at the coordinator and worker."""
    require(set(body)=={'source','sha256'} and
            hashlib.sha256(body['source'].encode('utf-8')).hexdigest()==body['sha256'],
            'Pinned C4 observer format source changed')
    module = ModuleType(name)
    module.__package__ = package
    exec(compile(body['source'], '<pinned '+name+'>', 'exec'), module.__dict__)
    return module


def prepare_observer(root, obs, role, spec, legacy, ready, finished):
    """Run the unchanged legacy audit or the explicitly pinned NDJSON audit."""
    directory = root/obs
    bundle = spec.get('observer_format')
    if not bundle:
        return legacy.audit(directory, 30, MARKS), spec['observer_finished']['sha256']
    audit = format_module(bundle['audit_module'], 'bench.runtime.observe_ndjson', 'bench.runtime').audit
    if role=='objects' and bundle['producer']=='ndjson':
        digest = bundle['audit_module']['sha256']
        require('stream' in finished and ready.get('observer_format_sha256')==digest and
                finished.get('observer_format_sha256')==digest and
                not any(os.path.lexists(directory/name) for name in
                        (LEGACY_FINISHED,'fold-receipt.ndjson','fold-receipt.json')),
                'Expected the directly observed C4 stream with the reviewed writer SHA')
    if role=='objects' and bundle['producer']=='legacy-fold':
        original = legacy.audit(directory, 30, MARKS)
        require(original['passed'] is True and original['finished_sha256']==spec['observer_finished']['sha256'],
                'Original closed observer did not audit before folding')
        for name in ('samples.ndjson','fold-receipt.ndjson','fold-receipt.json','.fold-staging',
                     '.finished.json.tmp', LEGACY_FINISHED):
            require(not os.path.lexists(directory/name), 'Existing fold output or partial fold; retain, do not retry')
        # Preserve the original receipt as evidence. The new finished.json
        # names the folded stream, but must not replace the original proof.
        with (directory/LEGACY_FINISHED).open('xb') as sink:
            sink.write(pin(spec['observer_finished'], directory/'finished.json').read_bytes())
            sink.flush()
            os.fsync(sink.fileno())
        fold = format_module(bundle['fold_tool'], 'fold_c4_observer', '').fold
        space = os.statvfs(directory)
        reserve = (space.f_blocks*space.f_frsize+4)//5
        receipt = fold(directory, max_output_bytes=bundle['fold_peak_bytes'], reserve_bytes=reserve)
        audited = audit(directory, 30, MARKS)
        require(receipt['passed'] is True and receipt['stream']['sha256']==audited['stream_sha256'] and
                receipt['samples']==audited['samples']==finished['samples'] and
                _digest(directory/LEGACY_FINISHED)==original['finished_sha256'],
                'Folded stream or preserved original closure differs')
        return audited, audited['finished_sha256']
    return audit(directory, 30, MARKS), spec['observer_finished']['sha256']


def list_members(root, directory, check):
    base = canonical(root/directory)
    require(base.is_dir(), 'Original evidence directory required: '+directory)
    members = []

    def unreadable(error):
        raise error

    for current, folders, files in os.walk(base, followlinks=False, onerror=unreadable):
        # The previous copy loop skipped every member under a component named cache.
        folders[:] = [n for n in folders if n != 'cache']
        for name in folders + files:
            if name == 'cache':
                continue
            p = Path(current)/name
            mode = p.lstat().st_mode
            require(not stat.S_ISLNK(mode), 'Evidence symlink: '+str(p))
            if stat.S_ISREG(mode):
                relative = p.relative_to(root)
                check.safe(relative.as_posix())
                require(not re.search(r'(?i)(credential|secret|\.env$|id_(rsa|ed25519)|\.pem$)', str(relative)),
                        'Credential-like evidence excluded')
                members.append(relative)
    return sorted(members)


def stamp(s):
    require(stat.S_ISREG(s.st_mode), 'Evidence stopped being a regular file')
    return s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns


@contextmanager
def stable_open(path, expected):
    require(canonical(path) == path, 'Evidence path changed')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stamp(os.fstat(stream.fileno())) == expected, 'Evidence changed before reading: '+str(path))
        yield stream
        require(stamp(os.fstat(stream.fileno())) == expected and stamp(path.lstat()) == expected,
                'Evidence changed while reading: '+str(path))


def _digest(path):
    with path.open('rb') as data:
        return hashlib.file_digest(data, 'sha256').hexdigest()


def pack_mapped(sources, states, out, cell, role, check):
    entries = {}
    for name, path in sources.items():
        with stable_open(path, states[name]) as data:
            entries[name] = dict(bytes=states[name][3], sha256=hashlib.file_digest(data, 'sha256').hexdigest())
    manifest = dict(schema='suite-archive-manifest-v1', cell_id=cell, kind=role, files=entries, deferred=[])
    raw = (json.dumps(manifest, sort_keys=True)+'\n').encode()
    require(len(raw)<=16*1024**2, 'Observer archive manifest would exceed the frozen 16 MiB bound')
    with out.open('xb') as sink:
        os.fchmod(sink.fileno(), 0o600)
        with tarfile.open(fileobj=sink, mode='w:gz') as archive:
            header = tarfile.TarInfo(check.MANIFEST)
            header.size, header.mode = len(raw), 0o600
            archive.addfile(header, io.BytesIO(raw))
            for name, path in sources.items():
                with stable_open(path, states[name]) as data:
                    # Each old staged member was a regular file, even if two source
                    # names happened to share an inode. Never emit tar hardlinks.
                    header = tarfile.TarInfo(name)
                    header.size = entries[name]['bytes']
                    header.mode = stat.S_IMODE(states[name][2])
                    header.mtime = states[name][4]//1_000_000_000
                    archive.addfile(header, data)
        sink.flush()
        os.fsync(sink.fileno())
    return check.verify(out, _digest(out))


def archive(spec):
    role, tag, cell = (spec[k] for k in ('role', 'tag', 'cell_id'))
    storage = spec.get('observer_scratch')
    if storage:
        require(role=='objects' and spec['root']==storage['root'], 'Scratch only routes the object observer')
        verify_scratch(storage)
    require(spec['schema']=='c4-closed-observer-archive-v1' and spec['reviewed'] is True and
            role in ROOTS and spec['root']==(SCRATCH_ROOT if storage else ROOTS[role]) and spec['has_observer'] is True and
            re.fullmatch(r'[a-z0-9-]{3,48}', tag) and re.fullmatch(r'[a-z0-9-]{3,80}', cell),
            'Reviewed exact original observed C4 archive specification required')
    root = canonical(spec['root'])
    obs = 'o/'+tag
    directories = [obs, 'q/'+tag+'-observer']
    if role == 'client':
        directories += ['q/'+tag, 'm/'+tag, 'm/'+tag+'-v', 'w/'+tag, 'j/'+tag]
    directories.append('q/'+tag+'-closure')
    require(spec['directories'] == directories, 'Original C4 archive directory allowlist required')
    check, common, observe = tools(spec)
    ready = load(spec['observer_ready'], root/obs/'ready.json')
    end = load(spec['observer_finished'], root/obs/'finished.json')
    closed = load(spec['role_closure'], root/directories[-1]/'result.json')
    source = ready['spec']
    pin(source, root/directories[1]/'observer.json')
    process = ready['process']
    require(ready['passed'] is True and end['passed'] is True and end['spec_sha256']==source['sha256'] and
            end['metric_contract_sha256']==ready['metric_contract_sha256'] and
            type(process['pid']) is int and process['pid']>0 and type(process['start_ticks']) is int and
            process['start_ticks']>0 and process['boot_id']==common.identity(os.getpid())['boot_id'] and
            not common.alive(process), 'Original ready observer has not closed on this boot')
    require(closed['passed'] is True and closed['all_exited'] is True and closed['all_unmounted'] is True and
            closed['cell_id']==cell and sum(all(p.get(k)==process[k] for k in ID_KEYS) and
                                           p.get('alive', False) is False for p in closed['processes'])==1,
            'Original role closure does not bind this observer')

    staging, outdir = root/'evidence'/tag, root/'archives'
    require(staging.resolve() == staging and outdir.resolve() == outdir,
            'Archive output path escapes original worker root')
    output = outdir/(tag+'-'+role+'.tar.gz')
    require(not os.path.lexists(staging) and not os.path.lexists(output),
            'Archive output already exists; do not repack this attempt')
    # Refuse unsafe members before any in-place fold. For the legacy-only path
    # keep the earlier snapshot to detect any concurrent source mutation.
    paths = [list_members(root, d, check) for d in directories]
    frozen = {p:stamp((root/p).lstat()) for p in paths[0]}
    audited, finished_sha256 = prepare_observer(root, obs, role, spec, observe, ready, end)
    require(audited['passed'] is True and audited['finished_sha256']==finished_sha256,
            'Closed observer audit does not match pinned finished receipt')
    if spec.get('observer_format') and role=='objects' and 'stream' not in end:
        paths[0] = list_members(root, obs, check)
        frozen = {p:stamp((root/p).lstat()) for p in paths[0]}
    staging.mkdir(parents=True)
    canonical(staging)
    outdir.mkdir(exist_ok=True)
    canonical(outdir)
    sources, states = {}, {}
    for i, members in enumerate(paths):
        for p in members:
            name = p.as_posix()
            require(name not in sources and name!=check.MANIFEST, 'Duplicate or reserved evidence member')
            if i == 0:
                target, state = root/p, frozen[p]
            else:
                target = staging/p
                target.parent.mkdir(parents=True, exist_ok=True)
                # Preserve the old snapshot for all non-observer inputs.
                shutil.copyfile(canonical(root/p), target)
                state = stamp(target.lstat())
            sources[name], states[name] = target, state
    require(sources, 'No evidence members')
    archive = pack_mapped(sources, states, output, cell, role, check)
    require(paths[0] == list_members(root, obs, check) and
            all(stamp((root/p).lstat()) == state for p,state in frozen.items()) and not common.alive(process),
            'Closed observer membership or source changed during archive')
    return dict(closure=closed, archive=archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(archive(load(dict(path=args.request, sha256=args.sha256)))))


if __name__ == '__main__':
    main()
