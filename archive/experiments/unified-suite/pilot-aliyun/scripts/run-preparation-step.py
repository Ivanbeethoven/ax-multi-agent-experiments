#!/usr/bin/env python3
"""Run one preparation command once and retain its actual output and exit.

Pass paths to private environment files, never secret values in argv. This is
not a benchmark runner and does not retry or start the next preparation step.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time


def run_step(steps_root, name, argv, cwd=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,70}', name) or not argv:
        raise ValueError('Explicit step name and argv required')
    if not steps_root.is_absolute() or (cwd and (not cwd.is_absolute() or not cwd.is_dir())):
        raise ValueError('Absolute output root and existing absolute working directory required')
    directory = steps_root / name
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)

    def write(name, value):
        with (directory / name).open('x') as stream:
            os.chmod(stream.name, 0o600)
            json.dump(value, stream, indent=2); stream.write('\n')

    write('command.json', dict(argv=argv, cwd=str(cwd) if cwd else None, automatic_retry=False))
    boot = Path('/proc/sys/kernel/random/boot_id')
    begin = time.monotonic()
    with (directory / 'stdout').open('xb') as stdout, (directory / 'stderr').open('xb') as stderr:
        os.chmod(stdout.name, 0o600); os.chmod(stderr.name, 0o600)
        try:
            child = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr)
        except OSError as error:
            result = dict(started=False, exit_code=None, launch_error=str(error),
                          wall_seconds=time.monotonic()-begin, at=datetime.now(timezone.utc).isoformat())
            write('exit.json', result)
            return result
        write('started.json', dict(pid=child.pid, at=datetime.now(timezone.utc).isoformat(),
                                  boot_id=boot.read_text().strip() if boot.exists() else None))
        code = child.wait()
    result = dict(started=True, exit_code=code, wall_seconds=time.monotonic()-begin,
                  at=datetime.now(timezone.utc).isoformat())
    write('exit.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps-root', type=Path, default=Path(__file__).resolve().parent/'steps')
    parser.add_argument('--name', required=True)
    parser.add_argument('--cwd', type=Path)
    parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.argv and args.argv[0] == '--': args.argv = args.argv[1:]
    result = run_step(args.steps_root, args.name, args.argv, args.cwd)
    print(json.dumps(dict(name=args.name, **result)))
    raise SystemExit(result['exit_code'] if result['started'] else 1)


if __name__ == '__main__': main()
