"""Untimed, opt-in CPU profile of warmed path-open versus openat on A355."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail


def main():
    output = Path(sys.argv[sys.argv.index('--out') + 1])
    binary = Path(sys.argv[sys.argv.index('--binary') + 1]).resolve()
    original, done, latest = run_detail.measured_round, set(), {}

    def configure(module):
        commandstats = module.commandstats
        def counted(port):
            latest['port'] = port
            return commandstats(port)
        module.commandstats = counted
        latest['commandstats'] = commandstats

    def measured(root, expected):
        sample = original(root, expected)
        if 'small-files-32-pair1-production' not in root.name or root in done:
            return sample
        done.add(root)
        processes = [int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit()
                     and (p / 'exe').exists() and (p / 'exe').resolve() == binary]
        assert len(processes) == 1, processes
        cell = output / root.name
        descriptor = os.open(root / 'sub', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for mode in ('path', 'openat'):
                before = (root / '.stats').read_text()
                redis_before = latest['commandstats'](latest['port'])
                path = cell / ('cpu-' + mode + '.data')
                command = ['perf', 'record', '-o', str(path), '-F', '199', '-g',
                           '--call-graph', 'dwarf,16384', '-p', str(processes[0])]
                with (cell / ('cpu-' + mode + '.log')).open('wb') as log:
                    profiler = subprocess.Popen(command, stdout=log, stderr=log)
                    try:
                        time.sleep(.4)
                        assert profiler.poll() is None
                        durations = []
                        for _ in range(20000):
                            start = time.monotonic_ns()
                            fd = (os.open(root / 'sub/b.txt', os.O_RDONLY) if mode == 'path'
                                  else os.open('b.txt', os.O_RDONLY, dir_fd=descriptor))
                            opened = time.monotonic_ns()
                            os.close(fd)
                            durations.append([opened - start, time.monotonic_ns() - opened])
                    finally:
                        profiler.send_signal(2)
                        profiler.wait(timeout=15)
                assert path.stat().st_size > 0
                redis_after = latest['commandstats'](latest['port'])
                (cell / ('cpu-' + mode + '-before.stats')).write_text(before)
                (cell / ('cpu-' + mode + '-after.stats')).write_text((root / '.stats').read_text())
                (cell / ('cpu-' + mode + '.json')).write_text(json.dumps(dict(
                    command=command, durations_ns=durations, iterations=len(durations),
                    columns=['open', 'close'], redis_before=redis_before,
                    redis_after=redis_after, diagnostic_only=True), indent=2))
                with (cell / ('cpu-' + mode + '-report.txt')).open('w') as report:
                    subprocess.run(['perf', 'report', '-i', str(path), '--stdio', '--no-children',
                                    '--percent-limit', '0.5'], stdout=report, check=True)
        finally:
            os.close(descriptor)
        return sample

    run_detail.measured_round = measured
    run_detail.main(configure_runner=configure)


if __name__ == '__main__':
    main()
