#!/usr/bin/env python3
"""Alternate two optimized builds; preserve each independent three-pair run."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for side in ('before', 'after'):
        parser.add_argument(f'--{side}-build', type=Path, required=True)
        parser.add_argument(f'--{side}-source', required=True)
        parser.add_argument(f'--{side}-patch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0
    args.out.mkdir(parents=True, exist_ok=False)
    harness = Path(__file__).resolve().parent.parent / 'hot-lower-20261112'
    env = os.environ.copy()
    env.pop('BREWFS_METADATA_PROFILE', None)
    for key in ('BREWFS_METADATA_MAX_BYTES', 'BREWFS_METADATA_MAX_OPERATIONS'):
        env.pop(key, None)
    runs = []
    for pair in range(1, 4):
        intervals = (10, 100) if pair % 2 else (100, 10)
        for interval in intervals:
            env['BREWFS_METADATA_FLUSH_MS'] = str(interval)
            for side in (('before', 'after') if pair % 2 else ('after', 'before')):
                build = getattr(args, f'{side}_build').resolve()
                out = args.out / f'pair-{pair}-{interval}ms-{side}'
                command = [sys.executable, str(harness / 'run_local.py'),
                           '--binary', str(build / 'brewfs'),
                           '--producer', str(build / 'packed_v3_snapshot_fixture'),
                           '--source-commit', getattr(args, f'{side}_source'),
                           '--source-patch', str(getattr(args, f'{side}_patch').resolve()),
                           '--pairs', '3', '--passes', '4', '--metadata-edits', '100',
                           '--cache-state', 'on', '--metadata-sync', 'local-buffered',
                           '--out', str(out.resolve())]
                with (args.out / f'{out.name}.log').open('xb') as log:
                    subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
                verified = subprocess.check_output([sys.executable, str(harness / 'verify_results.py'), str(out)])
                (args.out / f'{out.name}-verification.json').write_bytes(verified)
                runs.append(dict(pair=pair, interval_ms=interval, variant=side, directory=out.name,
                                 command=command))
                (args.out / 'runs.json').write_text(json.dumps(runs, indent=2) + '\n')
    assert len(runs) == 12
    (args.out / 'status.txt').write_text('passed\n')


if __name__ == '__main__':
    main()
