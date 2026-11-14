#!/usr/bin/env python3
"""Audit completed correctness/fio runs, including remount and owner cleanup."""
import argparse
import json
from pathlib import Path
import re


def collect(root):
    result = {}
    for kind in ('replace', 'mixed', 'fio'):
        directory = root / kind
        run = json.loads((directory / 'run.json').read_text())
        assert run['passed'] and len(run['cells']) == 6
        assert run['cleanup']['mount_absent'] and 'mount_error' not in run['cleanup']
        assert all(run['cleanup'][name] for name in run['containers'])
        rows = []
        for cell in run['cells']:
            folder = directory / cell['label']
            detail = json.loads((folder / 'result.json').read_text())
            assert detail['remote_remount_verified']
            row = dict(label=cell['label'], remount_verified=True)
            if kind in ('replace', 'mixed'):
                check = json.loads((folder / (kind + '-correctness.json')).read_text())
                assert check['passed'] and not check['errors']
                if kind == 'replace':
                    assert check['completed_writes'] == list(range(1, check['planned_writes'] + 1))
                    assert len(check['reads']) == check['planned_writes'] * 16
                    row.update(writes=len(check['completed_writes']), reads=len(check['reads']))
                else:
                    assert check['reads'] > 0 and check['writes'] > 0 and check['final_fsync_ns'] is not None
                    row.update(bytes=check['bytes'], reads=check['reads'], writes=check['writes'],
                               wall_ns=check['wall_ns'], final_fsync_ns=check['final_fsync_ns'])
                log = re.sub(r'\x1b\[[0-9;]*m', '', (folder / 'mount.log').read_text())
                row['stale_open_revalidations'] = sum('fuse.open failed' in line and 'Errno(116)' in line for line in log.splitlines())
                row['publication_waits'] = log.count('packed read waiting for local metadata publication')
            else:
                receipt = json.loads((folder / 'fio-receipt.json').read_text())
                job = json.loads((folder / 'fio.json').read_text())['jobs'][0]
                assert receipt['exit_code'] == 0 and job['error'] == 0
                row['wall_with_final_fsync_ns'] = receipt['wall_with_final_fsync_ns']
                for direction in ('read', 'write'):
                    io = job[direction]
                    row[direction] = dict(bytes=io['io_bytes'], bw_bytes=io['bw_bytes'],
                        mean_clat_ns=io['clat_ns']['mean'], p99_clat_ns=io['clat_ns']['percentile']['99.000000'])
            rows.append(row)
        result[kind] = dict(passed=True, cells=rows, binary_sha256=run['binary_sha256'],
                            producer_sha256=run['producer_sha256'], cleanup=run['cleanup'])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(collect(parser.parse_args().directory), indent=2))
