#!/usr/bin/env python3
"""Small mixed-I/O regression guard, separate from read-cost measurements.

Use empty-cache, or overlay-total to alternate baseline/candidate binaries.
fio runs once per writable cell, before warmup/measurement; all original
content/remount checks remain. The readonly lower arm never runs write fio.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import run_detail


def main():
    runtime = 5
    if '--fio-runtime' in sys.argv:
        index = sys.argv.index('--fio-runtime')
        runtime = int(sys.argv[index + 1])
        del sys.argv[index:index + 2]
    assert 1 <= runtime <= 60
    prepared = '--prepared-file' in sys.argv
    if prepared:
        sys.argv.remove('--prepared-file')
    assert '--comparison' in sys.argv
    comparison = sys.argv[sys.argv.index('--comparison') + 1]
    assert comparison in ('empty-cache', 'overlay-total')
    if comparison == 'overlay-total':
        assert prepared and '--candidate-binary' in sys.argv
    output = Path(sys.argv[sys.argv.index('--out') + 1])
    original = run_detail.measured_round
    seen = set()

    def round_with_guard(root, expected):
        result = original(root, expected)
        if 'packed-direct' in root.name:
            return result
        if root in seen:
            return result
        seen.add(root)
        target = root / '.ax-randrw-guard.bin'
        assert not target.exists()
        cell = output / root.name
        if prepared:
            prepare_started = time.monotonic_ns()
            block = bytes(range(256)) * 4096
            with target.open('xb') as stream:
                for _ in range(16):
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            with target.open('rb') as stream:
                for _ in range(16):
                    assert stream.read(len(block)) == block
                assert stream.read(1) == b''
            (cell / 'fio-prepare.json').write_text(json.dumps(dict(
                bytes=16 << 20, fsync=True, full_readback=True,
                elapsed_ns=time.monotonic_ns() - prepare_started), indent=2))
        command = ['/usr/bin/fio', '--name=overlay-randrw', '--filename=' + str(target),
                   '--ioengine=sync', '--direct=1', '--rw=randrw', '--rwmixread=70',
                   '--bs=4k', '--size=16m', f'--runtime={runtime}', '--time_based=1',
                   '--iodepth=1', '--numjobs=1', '--fallocate=none', '--end_fsync=1',
                   '--randrepeat=1', '--randseed=241113', '--output-format=json']
        if prepared:
            command += ['--allow_file_create=0']
        started = time.monotonic_ns()
        try:
            done = subprocess.run(command, capture_output=True, timeout=120)
            (cell / 'fio.json').write_bytes(done.stdout)
            (cell / 'fio.stderr').write_bytes(done.stderr)
            receipt = dict(command=command, exit_code=done.returncode,
                           runtime_seconds=runtime,
                           wall_with_final_fsync_ns=time.monotonic_ns() - started)
            (cell / 'fio-receipt.json').write_text(json.dumps(receipt, indent=2))
            assert done.returncode == 0, done.stderr.decode(errors='replace')
            jobs = json.loads(done.stdout)['jobs']
            assert len(jobs) == 1 and jobs[0]['error'] == 0
            assert jobs[0]['read']['io_bytes'] > 0 and jobs[0]['write']['io_bytes'] > 0
        finally:
            if target.exists():
                target.unlink()
        original(root, expected)
        return result

    # Include the wrapper itself in the archived provenance through save hook.
    run_detail.measured_round = round_with_guard
    run_detail.main()


if __name__ == '__main__':
    main()
