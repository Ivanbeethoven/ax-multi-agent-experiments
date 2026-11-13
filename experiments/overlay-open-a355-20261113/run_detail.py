#!/usr/bin/env python3
"""Use the existing matched Overlay runner, splitting every filesystem call.

All timers run in the caller. Server CPU, queue time and permission decoding
require separate profiling; a slow open alone cannot distinguish them.
"""
import importlib.util
import hashlib
import ctypes
import os
from pathlib import Path
import sys
import time


def assert_zero_ttl(raw):
    values = dict(line.split() for line in raw.splitlines()
                  if line.startswith(('brewfs_fuse_attr_valid_ns ', 'brewfs_fuse_entry_valid_ns ')))
    assert values == {'brewfs_fuse_attr_valid_ns': '0', 'brewfs_fuse_entry_valid_ns': '0'}, values


def directory_page_probe(root, expected, buffer_bytes=4096):
    """Untimed getdents64 pagination/content proof; count user syscalls explicitly."""
    libc = ctypes.CDLL(None, use_errno=True)
    getdents = libc.getdents64
    getdents.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    getdents.restype = ctypes.c_ssize_t
    buffer = ctypes.create_string_buffer(buffer_bytes)
    fd = os.open(root / 'sub', os.O_RDONLY | os.O_DIRECTORY)
    names, calls, sizes = [], 0, []
    try:
        while True:
            size = getdents(fd, buffer, buffer_bytes)
            calls += 1
            if size < 0:
                raise OSError(ctypes.get_errno(), 'getdents64')
            if size == 0:
                break
            sizes.append(size)
            raw, offset = buffer.raw[:size], 0
            while offset < size:
                record_len = int.from_bytes(raw[offset + 16:offset + 18], sys.byteorder)
                assert record_len >= 20 and offset + record_len <= size
                name = raw[offset + 19:offset + record_len].split(b'\0', 1)[0]
                if name not in (b'.', b'..'):
                    names.append(os.fsdecode(name))
                offset += record_len
    finally:
        os.close(fd)
    want = sorted(Path(p).name for p in expected if p.startswith('sub/'))
    assert sorted(names) == want
    return dict(entries=len(names), user_getdents64_calls=calls, buffer_bytes=buffer_bytes,
                page_bytes=sizes, content_verified=True, timed=False)


def measured_round(root, expected):
    started = time.monotonic_ns()
    parts = {}
    start = time.monotonic_ns()
    directory = os.open(root / 'sub', os.O_RDONLY | os.O_DIRECTORY)
    parts['directory_open'] = time.monotonic_ns() - start
    try:
        start = time.monotonic_ns()
        observed = sorted(os.listdir(directory))
        parts['directory_list'] = time.monotonic_ns() - start
        start = time.monotonic_ns()
        st = os.stat('b.txt', dir_fd=directory, follow_symlinks=False)
        parts['directory_relative_stat'] = time.monotonic_ns() - start
    finally:
        start = time.monotonic_ns()
        os.close(directory)
        parts['directory_close'] = time.monotonic_ns() - start
    assert observed == sorted(Path(p).name for p in expected if p.startswith('sub/'))
    assert st.st_size == len(expected['sub/b.txt'])
    reads = []
    for name, content in expected.items():
        path = root / name
        start = time.monotonic_ns()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = time.monotonic_ns() - start
        try:
            start = time.monotonic_ns()
            st = os.fstat(fd)
            stated = time.monotonic_ns() - start
            assert st.st_size == len(content)
            start = time.monotonic_ns()
            got = os.pread(fd, len(content), 0)
            elapsed = time.monotonic_ns() - start
            assert got == content, name
        finally:
            start = time.monotonic_ns()
            os.close(fd)
            closed = time.monotonic_ns() - start
        reads.append(dict(path=name, elapsed_ns=elapsed, bytes=len(got),
                          open_ns=opened, fstat_ns=stated, close_ns=closed))
    for key in ('open', 'fstat', 'close'):
        parts['file_' + key] = sum(row[key + '_ns'] for row in reads)
    parts['file_pread'] = sum(row['elapsed_ns'] for row in reads)
    total = time.monotonic_ns() - started
    parts['caller_other'] = total - sum(parts.values())
    assert parts['caller_other'] >= 0
    return dict(open_and_list_ns=parts['directory_open'] + parts['directory_list'],
                lookup_and_stat_ns=parts['directory_relative_stat'], pread=reads,
                round_elapsed_ns=total, breakdown_ns=parts)


def main(configure_runner=None, extra_sources=()):
    # Readonly Packed uses the legacy environment TTL; writable Workspace uses
    # per-mount configuration. Both must actually return zero, not just have
    # matching JSON. Explicit opt-out only reproduces the historical mismatch.
    legacy = '--legacy-ttl-diagnostic' in sys.argv
    if legacy:
        sys.argv.remove('--legacy-ttl-diagnostic')
    else:
        os.environ['BREWFS_CACHE_TTL_MS'] = '0'
    # Keep all original data, cache, counter, ordering and teardown checks.
    path = Path(__file__).resolve().parents[1] / 'hot-lower-20261112/run_local.py'
    spec = importlib.util.spec_from_file_location('hot_lower_runner', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if configure_runner is not None:
        configure_runner(runner)
    last_round = {}

    def tracked_round(root, expected):
        last_round.update(root=root, expected=expected)
        return measured_round(root, expected)

    runner.measured_round = tracked_round
    original_save = runner.save

    def save_with_provenance(output, value):
        if output.name == 'warm.json' and len(last_round.get('expected', {})) >= 10000:
            assert last_round['root'].name == output.parent.name
            proof = directory_page_probe(last_round['root'], last_round['expected'])
            assert proof['user_getdents64_calls'] > 2
            original_save(output.with_name('directory-pages.json'), proof)
        if output.name == 'result.json' and not legacy:
            for phase in ('before', 'first', 'after'):
                stats = output.with_name(phase + '.stats')
                if stats.is_file():
                    assert_zero_ttl(stats.read_text())
            value['effective_kernel_ttl_checked_zero'] = True
        if output.name == 'run.json':
            sources = {}
            for source in dict.fromkeys((Path(__file__).resolve(), path, Path(sys.argv[0]).resolve(), *extra_sources)):
                content = source.read_bytes()
                target = output.parent / ('frozen-' + source.name)
                with target.open('xb') as stream:
                    stream.write(content)
                sources[target.name] = hashlib.sha256(content).hexdigest()
            value['detail_runner'] = dict(schema='syscall-decomposition-v1',
                                          argv=sys.argv, sources_sha256=sources,
                                          effective_kernel_ttl='legacy-mismatch-diagnostic' if legacy else 'zero-checked')
        original_save(output, value)

    runner.save = save_with_provenance
    runner.main()


if __name__ == '__main__':
    main()
