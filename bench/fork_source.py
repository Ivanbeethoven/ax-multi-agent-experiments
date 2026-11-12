"""Qualify the exact source of a C4 native fork/clone before timing any child.

Run on the executor, in the mount's namespace, under the campaign's exclusive
source ownership. Qualification reads every byte; preflight does not. Neither
operation starts a mount, runs a native command, or admits a performance cell.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import uuid

from scripts.full_trace_common import tree

from .runtime import node
from .runtime.common import alive, load, mounts, pinned, spec, write_new
from .runtime.handoff import target_identity
from .runtime.prepare import anchor


REQUEST = 'suite-fork-source-request-v3'
QUALIFIED = 'suite-fork-source-qualified-v3'
SHA = re.compile(r'[0-9a-f]{64}')
REVISION = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}:[1-9][0-9]*:[0-9a-f]{64}')


def _canonical(value):
    path = Path(value)
    if not path.is_absolute() or str(path) != value or path.resolve() != path:
        raise ValueError('canonical absolute source path required')
    return path


def _external(ref, point):
    path = _canonical(ref['path'])
    if path.is_relative_to(point):
        raise ValueError('source evidence must be outside the tested mount')
    return pinned(ref)


def _manifest(ref, point):
    value = load(_external(ref, point))
    if not isinstance(value, dict) or not value:
        raise ValueError('nonempty canonical tree manifest required')
    files = payload = 0
    for name, entry in value.items():
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or path.as_posix() != name
                or '..' in path.parts or '\0' in name or not isinstance(entry, dict)):
            raise ValueError('invalid manifest path or entry')
        mode = entry.get('mode')
        if type(mode) is not int or not 0 <= mode <= 0o7777:
            raise ValueError('manifest must pin POSIX modes')
        if entry.get('type') == 'directory' and set(entry) == {'type', 'mode'}:
            continue
        size, sha = entry.get('bytes'), entry.get('sha256')
        if (entry.get('type') != 'file' or set(entry) != {'type', 'mode', 'bytes', 'sha256'}
                or type(size) is not int or size < 0 or not isinstance(sha, str) or not SHA.fullmatch(sha)):
            raise ValueError('manifest must pin file sizes and hashes; only files and directories supported')
        files += 1
        payload += size
    if not payload:
        raise ValueError('manifest must contain nonempty payload')
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return value, dict(tree_sha256=hashlib.sha256(canonical).hexdigest(), files=files, bytes=payload)


def _ready(ref):
    path = _canonical(ref['path'])
    if path.name != 'ready.json':
        raise ValueError('mount supervisor ready.json required')
    ready = load(pinned(ref))
    identity = target_identity(ready)
    point = _canonical(identity['mount']['point'])
    _external(ref, point)
    return path.parent, ready, identity, point


def _live(directory, identity):
    mount, process = identity['mount'], identity['process']
    if (target_identity(node.control(directory, 'status')) != identity
            or mounts(mount['point']) != [mount] or not alive(process)):
        raise ValueError('source FUSE mount or foreground process changed')


def _closed(ready_ref, stopped_ref, replacement=None):
    directory, _, original, point = _ready(ready_ref)
    stopped_path = _external(stopped_ref, point)
    if stopped_path != directory/'stopped.json':
        raise ValueError('stop receipt must come from the same mount supervisor')
    wanted = dict(passed=True, exit_code=0, mount_absent=True, anchor=original['anchor'],
                  original_mount=original['mount'], active_workers=[])
    stopped = load(stopped_path)
    now = mounts(point)
    # Linux may recycle a mount ID immediately after unmount. When that
    # happens, a different live supervisor process identifies the new mount.
    reused = (replacement is not None and now==[replacement['mount']] and
              replacement['process']!=original['process'] and
              replacement['process']['boot_id']==original['process']['boot_id'] and alive(replacement['process']))
    if (stopped != wanted or type(stopped['exit_code']) is not int
            or any(type(stopped[k]) is not bool for k in ('passed', 'mount_absent'))
            or alive(original['process']) or (original['mount'] in now and not reused)):
        raise ValueError('original FUSE process and mount must have stopped normally')
    return original


def _mount(request, ready, identity, point, filesystem):
    mount_ref = request['mount_spec']
    _external(mount_ref, point)
    mount = spec(mount_ref, 'experiment-mount-v1')
    _external(mount['command'], point)
    command = spec(mount['command'], 'experiment-step-v1')
    config_ref = mount['config']
    config_path = _external(config_ref, point)
    config = load(config_path)
    binary = _external(request['native']['binary'], point)
    if filesystem == 'brewfs':
        prefix = [str(binary), 'mount', '--config', str(config_path), str(point)]
    else:
        prefix = [str(binary), 'mount', config['metadata_url'], str(point)]
    if (mount['filesystem'] != filesystem or mount['mount_point'] != str(point) or ready['spec'] != mount_ref
            or mount['anchor'] != identity['anchor'] or anchor(filesystem, config) != identity['anchor']
            or identity['mount']['filesystem'] not in mount['allowed_fstypes']
            or command['argv'][:len(prefix)] != prefix or config_ref not in command['inputs']
            or command['executable_sha256'] != request['native']['binary']['sha256']):
        raise ValueError('mount, binary or backend differs from the pinned source')
    return config, binary


def _carrier(request, point, config, binary):
    native, evidence = request['native'], request['carrier']
    if set(native) != {'binary', 'redis_url', 'namespace', 'revision', 'data_config'}:
        raise ValueError('exact BrewFS fork configuration required')
    data = _external(native['data_config'], point)
    publication = load(_external(evidence['publication'], point))
    revision = native['revision']
    sealed = publication.get('native_sealed_source')
    if (binary.name != 'brewfs' or not REVISION.fullmatch(revision) or publication.get('fork_source') != revision
            or not isinstance(sealed, str) or not REVISION.fullmatch(sealed) or sealed.split(':')[0] == revision.split(':')[0]):
        raise ValueError('exact published native Packed revision required')
    manifest_sha, manifest_key = publication.get('manifest_sha256'), publication.get('manifest_key')
    if (not isinstance(manifest_sha, str) or not SHA.fullmatch(manifest_sha)
            or not isinstance(manifest_key, str) or manifest_key.split('/')[-1] != manifest_sha):
        raise ValueError('published Packed manifest key must pin the object hash')

    _external(evidence['step'], point)
    probe = spec(evidence['step'], 'experiment-step-v1')
    stdout = _external(evidence['stdout'], point)
    finished_path = _external(evidence['finished'], point)
    finished = load(finished_path)
    argv = [str(binary), 'workspace', '--meta-backend', 'redis', '--meta-url', native['redis_url'],
            '--workspace-namespace', native['namespace'], 'fork', revision, '--require-packed-carrier',
            '--count', '1', '--data-config', str(data)]
    if (stdout.name != 'stdout.log' or finished_path != stdout.parent/'finished.json'
            or load(stdout.parent/'spec.json') != probe or probe['argv'] != argv
            or probe['executable_sha256'] != native['binary']['sha256']
            or any(ref not in probe['inputs'] for ref in (evidence['publication'], request['input_tree'], native['data_config']))
            or finished.get('spec_sha256') != evidence['step']['sha256']
            or finished.get('cell_id') != probe['cell_id'] or finished.get('stage') != probe['stage']
            or finished.get('passed') is not True or finished.get('completed') is not True
            or finished.get('automatic_retry') is not False or type(finished.get('exit_code')) is not int
            or finished['exit_code'] != 0):
        raise ValueError('successful exact native Packed carrier probe required')
    children = load(stdout)
    if not isinstance(children, list) or len(children) != 1 or not isinstance(children[0], dict):
        raise ValueError('one independently forked qualification child required')
    child = children[0]
    layer, version, root = revision.split(':')
    basis = dict(layer_id=layer, sealed_version=int(version), root_hash=list(bytes.fromhex(root)))
    if (child.get('fork_base') != basis or str(uuid.UUID(child['workspace_id'])) != child['workspace_id']
            or not uuid.UUID(child['workspace_id']).int or not child.get('upper_bucket')
            or config.get('volume_format') != 'workspace-v1' or config.get('workspace') != child['workspace_id']
            or config.get('workspace_namespace') != native['namespace'] or config['meta']['backend'] != 'redis'
            or config['meta']['redis']['url'] != native['redis_url'] or config['data'] != load(data)['data']):
        raise ValueError('mounted BrewFS source readback is not the native Packed probe child')
    return dict(revision=revision, manifest_key=manifest_key, manifest_sha256=manifest_sha,
                qualification_workspace=child['workspace_id'])


def _inode(path):
    s = path.stat()
    if not path.is_dir():
        raise ValueError('source must be a directory')
    return dict(device=s.st_dev, inode=s.st_ino, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)


def _binding(request_ref, *, active, replacement=None):
    request = spec(request_ref, REQUEST)
    directory, ready, identity, point = _ready(request['ready'])
    _external(request_ref, point)
    if active:
        _live(directory, identity)
    manifest, summary = _manifest(request['input_tree'], point)
    method, native = request['method'], request['native']
    if method == 'brewfs-fork':
        config, binary = _mount(request, ready, identity, point, 'brewfs')
        backend = _carrier(request, point, config, binary)
        source = point
    elif method == 'juicefs-clone':
        _, binary = _mount(request, ready, identity, point, 'juicefs')
        if (binary.name != 'juicefs' or set(native) != {'binary', 'mount', 'source', 'destination_parent', 'threads'}
                or native['mount'] != str(point) or identity['mount']['filesystem'] != 'fuse.juicefs'
                or type(native['threads']) is not int or native['threads'] != 4):
            raise ValueError('exact JuiceFS clone configuration required')
        source, parent = (_canonical(native[k]) for k in ('source', 'destination_parent'))
        if (point in (source, parent) or not source.is_relative_to(point) or not parent.is_relative_to(point)
                or parent.is_relative_to(source) or not parent.is_dir()
                or source.stat().st_dev != point.stat().st_dev or parent.stat().st_dev != point.stat().st_dev):
            raise ValueError('source and destination must be separate paths on the exact JuiceFS mount')
        writer = request['writer']
        _external(writer['ready'], point)
        _external(writer['stopped'], point)
        closed = _closed(writer['ready'], writer['stopped'], identity if active else replacement)
        if (closed['anchor'] != identity['anchor'] or closed['process'] == identity['process']
                or closed['process']['boot_id'] != identity['process']['boot_id']):
            raise ValueError('closed preparation writer must belong to the same JuiceFS backend and boot')
        backend = dict(closed_preparation_writer=closed)
    else:
        raise ValueError('only BrewFS native Packed fork and JuiceFS native clone are supported')
    binding = dict(schema=QUALIFIED, passed=True, request=request_ref, method=method, cell_id=request['cell_id'],
                   input_tree=request['input_tree'], source=str(source), source_mount=identity,
                   backend=backend, **summary)
    return request, directory, point, source, manifest, binding


def qualify(request_ref, output):
    """Compare a supervised readback against the independently frozen source manifest."""
    request, directory, point, source, manifest, result = _binding(request_ref, active=True)
    output = _canonical(str(output))
    if output.is_relative_to(point):
        raise ValueError('source qualification output must be outside the tested mount')
    initial = _inode(source)
    if tree(source) != manifest:
        raise ValueError('native source tree differs from the reviewed input bytes or modes')
    _live(directory, result['source_mount'])
    if _inode(source) != initial:
        raise ValueError('source directory changed during readback')
    _external(request['input_tree'], point)
    result['source_inode'] = initial
    write_new(output, result)
    return result


def preflight(qualification_ref, *, cell_id, method, native, input_tree, probe_stopped, measurement=None):
    """Bind a native burst before its start gate, without inheriting readback's mount cache.

    JuiceFS remounts at the same path with a fresh private cache, independently
    pinned by measurement={mount_spec, ready}. The caller keeps the campaign's
    exclusive source ownership through post-burst verification.
    """
    receipt = load(pinned(qualification_ref))
    if receipt.get('schema') != QUALIFIED or receipt.get('passed') is not True:
        raise ValueError('successful v3 source qualification required')
    replacement = None
    if method=='juicefs-clone' and isinstance(measurement,dict) and set(measurement)=={'mount_spec','ready'}:
        replacement = _ready(measurement['ready'])[2]
    request, _, point, source, _, expected = _binding(receipt['request'], active=False,replacement=replacement)
    _external(qualification_ref, point)
    if (cell_id, method, native, input_tree) != tuple(request[k] for k in ('cell_id', 'method', 'native', 'input_tree')):
        raise ValueError('qualification does not bind this exact native burst source')
    if probe_stopped is None or _closed(request['ready'], probe_stopped,replacement) != expected['source_mount']:
        raise ValueError('normally closed qualification mount required before the timed burst')
    original_inode = receipt['source_inode']
    if (set(original_inode) != {'device', 'inode', 'mtime_ns', 'ctime_ns'}
            or any(type(value) is not int for value in original_inode.values())):
        raise ValueError('qualified source inode missing or invalid')
    expected['source_inode'] = original_inode
    if receipt != expected:
        raise ValueError('qualification readback binding differs')
    if method == 'brewfs-fork':
        if measurement is not None:
            raise ValueError('BrewFS native fork uses the retained revision, not a source mount')
        # Retention itself is revalidated by --require-packed-carrier on every native fork.
        return receipt
    if not isinstance(measurement, dict) or set(measurement) != {'mount_spec', 'ready'}:
        raise ValueError('fresh JuiceFS measurement mount spec and ready receipt required')
    _external(measurement['ready'], point)
    directory, ready, current, current_point = _ready(measurement['ready'])
    original = expected['source_mount']
    if (current_point != point or current['anchor'] != original['anchor']
            or current['mount']['filesystem'] != 'fuse.juicefs'
            or current['process'] == original['process']
            or current['process']['boot_id'] != original['process']['boot_id']):
        raise ValueError('distinct live JuiceFS mount on the qualified backend and path required')
    _live(directory, current)
    measured_config, _ = _mount({**request, **measurement}, ready, current, point, 'juicefs')
    qualified_mount = spec(request['mount_spec'], 'experiment-mount-v1')
    qualified_config = load(_external(qualified_mount['config'], point))
    old_cache, current_cache = (config.pop('cache_dir') for config in (qualified_config, measured_config))
    if qualified_config != measured_config or (old_cache == current_cache and current_cache != 'memory'):
        raise ValueError('measurement must use matching JuiceFS settings and a fresh private cache')
    current_inode = _inode(source)
    if any(current_inode[k] != original_inode[k] for k in ('inode', 'mtime_ns', 'ctime_ns')):
        raise ValueError('qualified JuiceFS source changed')
    return {**receipt, 'measurement_mount': current}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    qualify(dict(path=args.request, sha256=args.sha256), args.out)
