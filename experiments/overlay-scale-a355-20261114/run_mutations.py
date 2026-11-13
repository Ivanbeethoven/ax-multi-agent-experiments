"""Concurrent replace/read plus the existing overwrite/delete/fsync/remount checks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import stat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail


def concurrent_replace(root, expected, output):
    target = root / '.ax-concurrent.bin'
    temporary = root / '.ax-concurrent-next.bin'
    assert not target.exists() and not temporary.exists()
    bodies = [bytes([i]) * 4096 for i in range(13)]
    with target.open('xb') as stream:
        stream.write(bodies[0]); stream.flush(); os.fsync(stream.fileno())
    barrier = threading.Barrier(2, timeout=30)
    rows, errors, completed = [], [], []

    def writer():
        try:
            for phase, body in enumerate(bodies[1:], 1):
                barrier.wait()
                with temporary.open('xb') as stream:
                    stream.write(body); stream.flush(); os.fsync(stream.fileno())
                os.rename(temporary, target)
                completed.append(phase)
                barrier.wait()
        except BaseException:
            barrier.abort()
            raise

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(writer)
            try:
                for phase in range(1, 13):
                    barrier.wait()
                    for _ in range(8):
                        with target.open('rb', buffering=0) as stream:
                            body = stream.read()
                        rows.append(dict(phase=phase, bytes=len(body), sha256=hashlib.sha256(body).hexdigest()))
                        assert body in bodies[:phase + 1], 'partial or future generation read'
                    barrier.wait()
                work.result()
            finally:
                barrier.abort()
        assert target.read_bytes() == bodies[-1]
        assert not temporary.exists()
        expected[target.name] = bodies[-1]
    except BaseException as error:
        errors.append(type(error).__name__ + ': ' + str(error))
        raise
    finally:
        output.write_text(json.dumps(dict(passed=not errors, errors=errors,
            planned_writes=12, completed_writes=completed, reads=rows,
            expected_final_sha256=hashlib.sha256(bodies[-1]).hexdigest()), indent=2))


def concurrent_chmod(root, expected, output):
    """Exercise metadata invalidation while another thread opens the same inode."""
    target = root / '.ax-concurrent.bin'
    body = bytes(range(256)) * 16
    assert not target.exists()
    with target.open('xb') as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    target.chmod(0o600)
    barrier = threading.Barrier(2, timeout=30)
    rows, errors, completed = [], [], []

    def writer():
        try:
            for phase in range(1, 13):
                barrier.wait()
                target.chmod(0o640 if phase % 2 else 0o600)
                completed.append(phase)
                barrier.wait()
        except BaseException:
            barrier.abort()
            raise

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(writer)
            try:
                for phase in range(1, 13):
                    barrier.wait()
                    for _ in range(8):
                        with target.open('rb', buffering=0) as stream:
                            mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
                            got = stream.read()
                        assert mode in (0o600, 0o640) and got == body
                        rows.append(dict(phase=phase, mode=mode, bytes=len(got),
                                         sha256=hashlib.sha256(got).hexdigest()))
                    barrier.wait()
                    assert stat.S_IMODE(target.stat().st_mode) == (0o640 if phase % 2 else 0o600)
                work.result()
            finally:
                barrier.abort()
        expected[target.name] = body
    except BaseException as error:
        errors.append(type(error).__name__ + ': ' + str(error))
        raise
    finally:
        output.write_text(json.dumps(dict(passed=not errors, errors=errors,
            planned_chmods=12, completed_chmods=completed, reads=rows, expected_final_mode=0o600,
            expected_final_sha256=hashlib.sha256(body).hexdigest()), indent=2))


def main():
    kind = 'replace'
    if '--concurrent-kind' in sys.argv:
        position = sys.argv.index('--concurrent-kind')
        kind = sys.argv[position + 1]
        del sys.argv[position:position + 2]
    assert kind in ('replace', 'chmod')
    assert sys.argv[sys.argv.index('--comparison') + 1] == 'empty-cache'
    output = Path(sys.argv[sys.argv.index('--out') + 1])
    original, seen = run_detail.measured_round, set()

    def measured(root, expected):
        if root not in seen:
            seen.add(root)
            function = concurrent_replace if kind == 'replace' else concurrent_chmod
            function(root, expected, output / root.name / ('concurrent-read-' + kind + '.json'))
        return original(root, expected)

    run_detail.measured_round = measured
    run_detail.main()


if __name__ == '__main__':
    main()
