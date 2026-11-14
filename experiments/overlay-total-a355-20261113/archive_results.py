#!/usr/bin/env python3
"""Archive this completed A355 run, checking cleanup before retaining evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
args = parser.parse_args()
r = args.root.resolve()
assert (r / 'validation-02.exit').read_text().strip() == '0'
assert (r / 'measurement.exit').read_text().strip() == '0'
for name in ('validation-01', 'validation-02', 'measurement-01'):
    run = json.loads((r / name / 'run.json').read_text())
    assert run['cleanup']['mount_absent'] and all(run['cleanup'][c] for c in run['containers'])
    for container in run['containers']:
        assert subprocess.run(['docker', 'inspect', container], capture_output=True).returncode != 0
    assert run['temporary_directory'] not in Path('/proc/self/mountinfo').read_text()
for name, summary in [('validation-02', 'validation-summary-02.json'), ('measurement-01', 'measurement-summary-01.json')]:
    computed = json.loads(subprocess.check_output(['python3', str(r / 'summarize.py'), str(r / name)], text=True))
    assert computed == json.loads((r / summary).read_text())
    assert computed['cells'] == (24 if name == 'validation-02' else 48)
files = ['validation-01', 'validation-02', 'measurement-01', 'validation-summary-02.json',
         'measurement-summary-01.json', 'scope-check.json', 'harness-tests.log', 'run_local.py',
         'summarize.py', 'test_total.py', 'test_meter.py', 'test_cold_counters.py', 'check-scope.py',
         'retention-parity.patch', 'build.sh', 'build.log', 'build.exit', 'checks.log', 'checks.exit',
         'release.log', 'release.exit', 'formatted-cli.sha256', 'ownership.json', 'host-before.txt',
         'validation.log', 'validation.exit', 'validation-02.log', 'validation-02.exit',
         'measurement.log', 'measurement.exit', 'run-validation.sh', 'run-validation-02.sh',
         'run-measurement.sh', 'run-measurement-02.sh', 'run-after-build.py', 'supervisor.log',
         'run-pairing-02.sh', 'pairing-02-launch.log', 'artifacts/SHA256SUMS', 'artifacts/source.patch']
assert all((r / f).is_file() or (r / f).is_dir() for f in files)
archive = r / 'overlay-total-results.tar.gz'
assert not archive.exists()
with tarfile.open(archive, 'w:gz') as out:
    for name in files:
        out.add(r / name, arcname=name)
with archive.open('rb') as stream:
    receipt = dict(archive=archive.name, bytes=archive.stat().st_size,
                   sha256=hashlib.file_digest(stream, 'sha256').hexdigest(), cleanup_verified=True,
                   measured_cells=48, counter_validation_cells=24)
(r / 'archive-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
