"""Audit final correctness, 30 s fio and the 61 s idle-cache diagnostic."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-profile-a355-20261114'))
from summarize_checks import summarize_fio
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-fixes-a355-20261114'))
from collect_checks import collect


def summarize(root):
    result = collect(root, kinds=('replace', 'mixed'))
    result['fio_paired'] = summarize_fio(root / 'fio-paired', 30)
    aged = root / 'aged'
    run = json.loads((aged / 'run.json').read_text())
    assert run['passed'] and run['diagnostic_only']
    assert run['policy_ablation']['same_binary'] and run['binary_sha256'] == run['candidate_binary_sha256']
    assert run['cleanup']['mount_absent'] and all(run['cleanup'][name] for name in run['containers'])
    probes = []
    for path in sorted(aged.glob('*/age-proof.json')):
        age = json.loads(path.read_text())
        assert age['seconds'] >= 61
        cell = json.loads(path.with_name('result.json').read_text())
        policy = 'ttl' if cell['arm'] == 'production' else 'lease'
        assert age['row_cache_policy'] == policy
        assert json.loads(path.with_name('runtime-env.json').read_text())['BREWFS_WORKSPACE_ROW_CACHE_POLICY'] == policy
        assert not any(cell['first_objects'].values())
        assert not any(cell['steady_objects'].values())
        probes.append(dict(label=cell['label'], arm=cell['arm'], policy=policy, pair=cell['pair'],
            diagnostic_only=True, age=age,
            first_ms=cell['first_round']['round_elapsed_ns'] / 1e6,
            first_breakdown_ns=cell['first_round']['breakdown_ns'],
            first_stages=cell['first_round']['server_profile_delta'],
            first_redis=cell['first_redis_delta'],
            first_stats=cell['first_stats_delta'], first_objects=cell['first_objects']))
    assert len(probes) == 6
    result['aged_probes'] = probes
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    print(json.dumps(summarize(p.parse_args().directory), indent=2))
