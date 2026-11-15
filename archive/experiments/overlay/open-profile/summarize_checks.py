"""Audit correctness, paired fio, and diagnostic stage times separately."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-fixes-a355-20261114'))
from collect_checks import collect


def summarize_fio(directory, runtime_seconds):
    run = json.loads((directory / 'run.json').read_text())
    assert run['passed'] and len(run['cells']) == 18
    assert run['cleanup']['mount_absent'] and 'mount_error' not in run['cleanup']
    assert all(run['cleanup'][name] for name in run['containers'])
    rows = []
    for cell in run['cells']:
        folder = directory / cell['label']
        detail = json.loads((folder / 'result.json').read_text())
        if cell['arm'] == 'packed-direct':
            assert not (folder / 'fio.json').exists()
            continue
        receipt = json.loads((folder / 'fio-receipt.json').read_text())
        prepared = json.loads((folder / 'fio-prepare.json').read_text())
        job = json.loads((folder / 'fio.json').read_text())['jobs'][0]
        assert receipt['exit_code'] == job['error'] == 0
        assert f'--runtime={runtime_seconds}' in receipt['command']
        assert prepared['bytes'] == 16 << 20 and prepared['fsync'] and prepared['full_readback']
        row = {k: cell[k] for k in ('label', 'pair', 'scenario', 'arm')}
        row.update(binary_sha256=detail['binary_sha256'],
                   wall_with_final_fsync_ns=receipt['wall_with_final_fsync_ns'])
        for direction in ('read', 'write'):
            io = job[direction]
            assert io['io_bytes'] > 0
            row[direction] = dict(bytes=io['io_bytes'], operations=io['total_ios'], bw_bytes=io['bw_bytes'],
                mean_clat_ns=io['clat_ns']['mean'], p99_clat_ns=io['clat_ns']['percentile']['99.000000'])
        rows.append(row)
    assert len(rows) == 12
    medians = []
    for scenario in sorted({r['scenario'] for r in rows}):
        for arm in ('production', 'candidate'):
            selected = [r for r in rows if r['scenario'] == scenario and r['arm'] == arm]
            assert len(selected) == 3
            medians.append(dict(scenario=scenario, arm=arm,
                read_bw_bytes=statistics.median(r['read']['bw_bytes'] for r in selected),
                write_bw_bytes=statistics.median(r['write']['bw_bytes'] for r in selected),
                latency_ns={d: {k: statistics.median(r[d][k] for r in selected)
                                for k in ('mean_clat_ns', 'p99_clat_ns')}
                            for d in ('read', 'write')},
                wall_with_final_fsync_ns=statistics.median(r['wall_with_final_fsync_ns'] for r in selected)))
    return dict(passed=True, runtime_seconds=runtime_seconds, cells=rows, medians=medians)


def summarize(root):
    result = collect(root, kinds=('replace', 'mixed'))
    result['fio_paired'] = summarize_fio(root / 'fio-paired', 5)
    result['fio_tail30'] = summarize_fio(root / 'fio-tail30', 30)

    directory = root / 'profile-stages'
    run = json.loads((directory / 'run.json').read_text())
    assert run['passed'] and run['diagnostic_only']
    groups = defaultdict(list)
    for cell in run['cells']:
        detail = json.loads((directory / cell['label'] / 'result.json').read_text())
        profile = defaultdict(float)
        for sample in detail['samples']:
            assert sample['diagnostic_only']
            for k, v in sample['server_profile_delta'].items():
                profile[k] += v
        groups[(cell['scenario'], cell['arm'])].append(dict(label=cell['label'],
            diagnostic_batch_ms_including_collection=detail['elapsed_ns'] / 1e6, stages=dict(profile),
            redis_commands={k: v['calls'] for k, v in detail['redis_delta'].items() if k != 'info'}))
    result['stage_profiles'] = [dict(scenario=k[0], arm=k[1], diagnostic_only=True, trials=v)
                               for k, v in groups.items()]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2))
