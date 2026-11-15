#!/usr/bin/env python3
"""Verify the downloaded A355 archive and recompute both summaries offline."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

from summarize import summarize

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True, help='New extraction directory')
args = parser.parse_args()
evidence = Path(__file__).resolve().parent / 'evidence'
receipt = json.loads((evidence / 'archive-receipt.json').read_text())
archive = evidence / receipt['archive']
assert archive.stat().st_size == receipt['bytes']
with archive.open('rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == receipt['sha256']
args.out.mkdir(parents=True, exist_ok=False)
with tarfile.open(archive) as stream:
    stream.extractall(args.out, filter='data')
audits = []
for name, summary in [('validation-02', 'validation-summary-02.json'), ('measurement-01', 'measurement-summary-01.json')]:
    actual = summarize(args.out / name)
    assert actual == json.loads((evidence / summary).read_text())
    assert actual == json.loads((args.out / summary).read_text())
    audits.append(dict(run=name, cells=actual['cells'], files=len((args.out / name / 'SHA256SUMS').read_text().splitlines())))
result = dict(passed=True, archive_sha256=receipt['sha256'], audits=audits)
(args.out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n', newline='\n')
print(json.dumps(result))
