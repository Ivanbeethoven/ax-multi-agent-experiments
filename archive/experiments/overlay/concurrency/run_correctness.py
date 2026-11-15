#!/usr/bin/env python3
"""Real FUSE replacement and mixed-I/O checks; reuse the frozen mount harness."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail


def replacement(root, expected, output, phases=100):
    target, temporary = root / '.ax-replace', root / '.ax-replace-next'
    bodies = [hashlib.sha256(str(i).encode()).digest() * 128 for i in range(phases + 1)]
    assert not target.exists() and not temporary.exists()
    with target.open('xb') as stream:
        stream.write(bodies[0]); stream.flush(); os.fsync(stream.fileno())
    barrier = threading.Barrier(2, timeout=30)
    rows, completed, errors = [], [], []

    def writer():
        try:
            for phase in range(1, phases + 1):
                barrier.wait()
                with temporary.open('xb') as stream:
                    stream.write(bodies[phase]); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, target)
                completed.append(phase)
                barrier.wait()
        except BaseException:
            barrier.abort()
            raise

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(writer)
            try:
                for phase in range(1, phases + 1):
                    barrier.wait()
                    for _ in range(16):
                        started = time.monotonic_ns()
                        with target.open('rb', buffering=0) as stream:
                            body = stream.read()
                        # Previous phase completed before this barrier. Only
                        # its value or the current replacement is permissible.
                        assert body in (bodies[phase - 1], bodies[phase]), 'stale/torn/future value'
                        rows.append(dict(phase=phase, elapsed_ns=time.monotonic_ns() - started,
                                         sha256=hashlib.sha256(body).hexdigest()))
                    barrier.wait()
                work.result()
            finally:
                barrier.abort()
        assert target.read_bytes() == bodies[-1]
        assert not temporary.exists()
        expected[target.name] = bodies[-1]  # Existing harness verifies after remount.
    except BaseException as error:
        errors.append(repr(error))
        raise
    finally:
        output.write_text(json.dumps(dict(passed=not errors, errors=errors,
            planned_writes=phases, completed_writes=completed, reads=rows), indent=2))


def mixed(root, expected, output, seconds=8, blocks=16):
    """Validate each random read, final fsync, close/reopen and later remount."""
    # Keep this retained file within one FUSE READ: the shared harness asserts
    # one callback per measured file. The separate fio guard still uses 16 MiB.
    target = root / '.ax-mixed'
    assert not target.exists()
    body = bytearray(bytes(range(256)) * (blocks * 16))
    with target.open('xb') as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    rows, errors, writes, reads = [], [], 0, 0
    rng = random.Random(241113)
    started = time.monotonic_ns()
    fd = os.open(target, os.O_RDWR)
    try:
        while time.monotonic_ns() - started < seconds * 1e9 or len(rows) < 256:
            offset = rng.randrange(blocks) * 4096
            writing = rng.randrange(100) >= 70
            begin = time.monotonic_ns()
            if writing:
                value = hashlib.sha256(str(len(rows)).encode()).digest() * 128
                assert os.pwrite(fd, value, offset) == len(value)
                body[offset:offset + 4096] = value
                writes += 1
            else:
                got = os.pread(fd, 4096, offset)
                assert got == body[offset:offset + 4096], (len(rows), offset)
                reads += 1
            rows.append(dict(op='pwrite' if writing else 'pread', offset=offset,
                             elapsed_ns=time.monotonic_ns() - begin))
        begin = time.monotonic_ns()
        os.fsync(fd)
        fsync_ns = time.monotonic_ns() - begin
        os.close(fd); fd = None
        assert target.read_bytes() == body
        expected[target.name] = bytes(body)
    except BaseException as error:
        errors.append(repr(error))
        raise
    finally:
        if fd is not None:
            os.close(fd)
        output.write_text(json.dumps(dict(passed=not errors, errors=errors, seed=241113,
            bytes=len(body), reads=reads, writes=writes, operations=rows,
            wall_ns=time.monotonic_ns() - started, final_fsync_ns=locals().get('fsync_ns'),
            final_sha256=hashlib.sha256(body).hexdigest()), indent=2))


def main():
    position = sys.argv.index('--check')
    kind = sys.argv[position + 1]
    del sys.argv[position:position + 2]
    assert kind in ('replace', 'mixed')
    assert sys.argv[sys.argv.index('--comparison') + 1] == 'empty-cache'
    output = Path(sys.argv[sys.argv.index('--out') + 1])
    original, seen = run_detail.measured_round, set()

    def checked(root, expected):
        if root not in seen:
            seen.add(root)
            function = replacement if kind == 'replace' else mixed
            function(root, expected, output / root.name / (kind + '-correctness.json'))
        return original(root, expected)

    run_detail.measured_round = checked
    run_detail.main()


if __name__ == '__main__':
    main()
