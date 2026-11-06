"""Direct fio matrix for quick pilots, not a formal suite campaign.

This helper exists for operator smoke/pilot runs that need one file per fio
job and optional asynchronous writes. It deliberately does not create a
suite plan, state DB, qualification receipts or observer evidence; use
``bench.suite`` for measured protocol cells.

Default behaviour is asynchronous (``fsync=0``). Pass ``--fsync 1`` when a
synchronous-write pilot is explicitly wanted; results from the two modes are
not comparable and must be labelled.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CASES = {
    '1m': [('write', 4 * 2**30), ('randwrite', 1 * 2**30), ('randrw', 1 * 2**30),
           ('read', 4 * 2**30), ('randread', 1 * 2**30)],
    '4m': [('write', 4 * 2**30), ('randwrite', 4 * 2**30), ('randrw', 4 * 2**30),
           ('read', 4 * 2**30), ('randread', 4 * 2**30)],
    '64m': [('write', 4 * 2**30), ('read', 4 * 2**30)],
}


def fio_command(fio, mount, out, label, block_size, mode, io_bytes,
                numjobs, size_bytes, fsync):
    directory = Path(mount) / f'pilot-{block_size}'
    return [
        fio,
        f'--name={label}-{block_size}-{mode}',
        f'--directory={directory}',
        '--filename_format=fio.$jobnum',
        f'--size={size_bytes}',
        f'--bs={block_size}',
        f'--rw={mode}',
        '--rwmixread=75',
        f'--io_size={io_bytes}',
        '--ioengine=psync',
        '--iodepth=1',
        f'--numjobs={numjobs}',
        '--direct=0',
        '--invalidate=1',
        '--group_reporting=1',
        '--output-format=json',
        f'--output={out}',
        f'--fsync={fsync}',
    ]


def drop_caches():
    try:
        os.sync()
        Path('/proc/sys/vm/drop_caches').write_text('3\n')
        return True
    except OSError:
        return False


def run(args):
    mount = Path(args.mount)
    if not mount.is_dir() or not os.path.ismount(mount):
        raise ValueError(f'mount point is not a mounted directory: {mount}')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fio = shutil.which(args.fio) or args.fio
    summary = []
    for block_size in args.block_sizes:
        if block_size not in CASES:
            raise ValueError(f'unsupported block size: {block_size}')
        directory = mount / f'pilot-{block_size}'
        directory.mkdir(parents=True, exist_ok=True)
        for mode, io_bytes in CASES[block_size]:
            name = f'{args.label}-{block_size}-{mode}'
            raw = out / f'{name}.json'
            cmd = fio_command(fio, mount, raw, args.label, block_size, mode,
                              io_bytes, args.numjobs, args.size_bytes, args.fsync)
            if args.dry_run:
                print(' '.join(cmd))
                continue
            if mode in ('read', 'randread'):
                cold = drop_caches()
            else:
                cold = None
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f'{name} failed: {result.stderr[-500:]}')
            os.sync()
            job = json.loads(raw.read_text())['jobs'][0]
            read = job.get('read', {})
            write = job.get('write', {})
            row = {
                'case': name, 'block_size': block_size, 'mode': mode,
                'async': args.fsync == 0, 'cold_cache': cold,
                'read_iops': read.get('iops', 0),
                'write_iops': write.get('iops', 0),
                'read_mib_s': read.get('bw_bytes', 0) / 2**20,
                'write_mib_s': write.get('bw_bytes', 0) / 2**20,
                'read_lat_ms': read.get('lat_ns', {}).get('mean', 0) / 1e6,
                'write_lat_ms': write.get('lat_ns', {}).get('mean', 0) / 1e6,
                'raw': str(raw),
            }
            summary.append(row)
            print(json.dumps(row), flush=True)
        if not args.keep_data and not args.dry_run:
            shutil.rmtree(directory, ignore_errors=True)
    if not args.dry_run:
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mount', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--fio', default='/usr/bin/fio')
    parser.add_argument('--block-sizes', default='1m,4m,64m',
                        help='comma-separated subset of 1m,4m,64m')
    parser.add_argument('--numjobs', type=int, default=8)
    parser.add_argument('--size-bytes', type=int, default=4 * 2**30)
    parser.add_argument('--fsync', type=int, choices=(0, 1), default=0,
                        help='0 = asynchronous pilot (default), 1 = synchronous')
    parser.add_argument('--keep-data', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    args.block_sizes = [x.strip().lower() for x in args.block_sizes.split(',') if x.strip()]
    if args.numjobs < 1 or args.size_bytes < 1:
        parser.error('positive numjobs/size required')
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
