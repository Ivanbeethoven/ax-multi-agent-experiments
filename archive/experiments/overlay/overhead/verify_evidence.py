#!/usr/bin/env python3
"""Verify archive hashes and frozen measurements without starting services."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New directory; existing outputs are refused')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    evidence = here / 'evidence'
    args.out.mkdir(parents=True, exist_ok=False)
    receipts = [read(evidence / 'archive-receipt.json'), read(evidence / 'remount-regression-receipt.json')]
    receipts += [dict(archive=item['name'], **{k: v for k, v in item.items() if k != 'name'})
                 for item in read(evidence / 'cold-archive-receipt.json')['archives']]
    extracted = {}
    for item in receipts:
        archive = evidence / item['archive']
        assert archive.stat().st_size == item['bytes'] and digest(archive) == item['sha256'], archive
        destination = args.out / archive.name.removesuffix('.tar.gz')
        destination.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(destination, filter='data')
        extracted[archive.name] = destination
    audits = []
    for script, root, summary in [
        ('summarize.py', extracted['a355-results.tar.gz'] / 'run-02', 'summary-02.json'),
        ('summarize_cold.py', extracted['cold-native-03-results.tar.gz'] / 'cold-native-03', 'summary-cold-native-03.json'),
        ('summarize_cold.py', extracted['cold-native-04-results.tar.gz'] / 'cold-native-04', 'summary-cold-native-04.json'),
    ]:
        spec = importlib.util.spec_from_file_location('audit', here / script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # JSON stores permutation tuples as arrays; compare the serialized form.
        assert json.loads(json.dumps(module.summarize(root))) == read(evidence / summary), summary
        audits.append(dict(run=root.name, manifest_files=len((root / 'SHA256SUMS').read_text().splitlines())))
    regression = extracted['remount-regression-results.tar.gz'] / 'remount-regression-01'
    for line in (regression / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = (regression / name).resolve()
        assert path.is_relative_to(regression.resolve()) and digest(path) == expected, name
    run = read(regression / 'run.json')
    assert run['passed'] and len(run['cells']) == 6 and run['cleanup']['mount_absent']
    assert all(run['cleanup'][name] for name in run['containers'])
    for cell in run['cells']:
        assert read(regression / cell['label'] / 'result.json')['remote_remount_verified']
    failed = read(extracted['cold-failures-and-calibration.tar.gz'] / 'cold-native-01/run.json')
    assert not failed['passed'] and digest(evidence / 'run_local-cold-01.py') == failed['harness_sha256']
    result = dict(passed=True, archives=len(receipts), audited_runs=audits, remount_regression_cells=6,
                  excluded_timings=['cold-native-01: script failure', 'cold-native-02: proxy latency',
                                    'cold-native-03: counter validation through proxy'])
    (args.out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
