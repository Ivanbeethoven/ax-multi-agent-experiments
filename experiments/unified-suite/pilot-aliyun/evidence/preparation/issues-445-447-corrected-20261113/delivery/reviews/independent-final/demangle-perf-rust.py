#!/usr/bin/env python3
"""Offline, lossless symbol-label transformation of archived perf text.

Does not execute perf, read DWARF stacks, change binaries, or attribute CPU.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('input', type=Path)
parser.add_argument('output_directory', type=Path)
args = parser.parse_args()
raw = args.input.read_bytes()
text = raw.decode('utf-8', errors='strict')
# Perf labels retain an optional optimization clone suffix after the mangled name.
# Match a complete label so that replacement cannot change a larger identifier.
pattern = re.compile(r'(?<![A-Za-z0-9_.$])_R[A-Za-z0-9_.$]+(?![A-Za-z0-9_.$])')
symbols = sorted(set(pattern.findall(text)))
if not symbols:
    raise SystemExit('No Rust v0 labels found; leave input untouched and inspect format.')
tool = Path(shutil.which('c++filt') or '/nonexistent')
version = subprocess.run([str(tool), '--version'], check=True, text=True,
                         capture_output=True, timeout=10).stdout
command = [str(tool), '--format=rust', '--no-strip-underscore']
result = subprocess.run(command, input='\n'.join(symbols) + '\n', text=True,
                        capture_output=True, check=True, timeout=60)
resolved = result.stdout.splitlines()
assert len(resolved) == len(symbols), (len(resolved), len(symbols))
mapping = dict(zip(symbols, resolved))
converted = pattern.sub(lambda m: mapping[m.group()], text)
# Non-symbol text (timestamps, addresses, PID/TID, periods, order, blank lines)
# must be byte-for-byte identical after projecting labels away.
masked_input = pattern.sub('<RUST_V0_LABEL>', text)
projected = []
last = 0
for m in pattern.finditer(text):
    projected.extend([text[last:m.start()], '<RUST_V0_LABEL>'])
    last = m.end()
projected.append(text[last:])
assert ''.join(projected) == masked_input
assert converted.count('\n') == text.count('\n')

out = args.output_directory
out.mkdir(parents=True, exist_ok=False)
(out / 'script.mangled.txt').write_bytes(raw)
(out / 'script.demangled.txt').write_text(converted, encoding='utf-8', newline='')
(out / 'symbol-map.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n')
(out / 'tool-version.txt').write_text(version)
(out / 'tool-stderr.txt').write_text(result.stderr)
changed = {k: v for k, v in mapping.items() if k != v}
unresolved = sorted(k for k, v in mapping.items() if k == v or re.match(r'^_R[A-Za-z0-9_]', v))
receipt = {
    'schema': 'offline-rust-v0-perf-labels-v1',
    'input_path': str(args.input.resolve()),
    'input_sha256': hashlib.sha256(raw).hexdigest(),
    'tool': str(tool.resolve()),
    'tool_sha256': hashlib.sha256(tool.read_bytes()).hexdigest(),
    'tool_version': version,
    'command': command,
    'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'input_bytes': len(raw),
    'rust_v0_unique_labels': len(symbols),
    'rust_v0_label_occurrences': len(pattern.findall(text)),
    'changed_unique_labels': len(changed),
    'unresolved_unique_labels': unresolved,
    'brewfs_labels_after': sum(bool(re.search(r'brewfs(?:\[[^\]]+\])?::', x)) for x in mapping.values()),
    'unknown_mentions_before': text.count('[unknown]'),
    'unknown_mentions_after': converted.count('[unknown]'),
    'newline_count_unchanged': True,
    'transformation': 'Only full Rust-v0 label substrings are replaced; sample order, addresses, periods, timestamps, PID/TID and existing callchains unchanged.',
    'limits': 'No inline expansion, unwinding, missing stack recovery, CPU attribution, or performance acceptance. An absent demangled function does not prove it did not execute.',
}
receipt['outputs'] = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(out.iterdir()) if p.is_file()}
(out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'receipt': str(out / 'receipt.json'), 'resolved': len(changed), 'unresolved': len(unresolved)}))
