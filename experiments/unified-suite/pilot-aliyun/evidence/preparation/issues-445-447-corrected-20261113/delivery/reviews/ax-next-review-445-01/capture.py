import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

os.environ['GIT_OPTIONAL_LOCKS'] = '0'
OUT = Path('/home/dev/ax-next-review-445-01')
TARGETS = {
    'issues': (Path('/home/dev/ax-issue447-corrected-candidate'), 'c8f5f16aefe811540782b952481982179a1bfd6f'),
    'packed': (Path('/home/dev/ax-packed-prefetch-test-drain'), '53c725f36db5bcd060ec2483a0abecb943bed4ea'),
}
FILES = {
    'issues': {
        'src/workspace_overlay/packed_v3/wire005/read_cache.rs': [(1, 266), (274, 440)],
        'src/workspace_overlay/packed_v3/wire005/budget.rs': [(202, 244), (304, 355)],
        'src/workspace_overlay/packed_v3/wire005/index.rs': [(1455, 1495)],
        'src/workspace_overlay/meta_layer/row_cache.rs': [(1, 690)],
        'src/workspace_overlay/meta_layer/row_cache_cancellation_tests.rs': [(1, 100)],
        'src/workspace_overlay/model.rs': [(252, 278), (329, 357)],
        'src/workspace_overlay/catalog.rs': [(245, 260)],
        'src/vfs/io/writer.rs': [(4592, 4601), (4677, 4718), (4926, 4973), (5012, 5069)],
        'src/vfs/io/writer/commit_cleanup_tests.rs': [(327, 557)],
    },
    'packed': {
        'src/cli.rs': [(2296, 2341)],
        'src/workspace/packed/readonly/pack_cache.rs': [(1, 496)],
        'src/workspace/packed/readonly/transport.rs': [(1, 255)],
        'src/workspace/packed/readonly.rs': [(896, 930)],
    },
}

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

def sha(data):
    return hashlib.sha256(data).hexdigest()

OUT.mkdir(exist_ok=True)
receipt = {'schema': 'ax.next-source-review.v1', 'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'method': 'read-only source review; no new Rust build or runtime benchmark', 'new_confirmed_findings': [], 'targets': {}, 'prior_behavioral_evidence': []}
excerpts = []
for label, (root, expected) in TARGETS.items():
    head = git(root, 'rev-parse', 'HEAD')
    status = git(root, 'status', '--porcelain')
    assert head == expected and status == '', (label, head, status)
    target = {'path': str(root), 'head': head, 'tree': git(root, 'rev-parse', 'HEAD^{tree}'), 'clean': True, 'files': {}}
    for rel, spans in FILES[label].items():
        data = (root / rel).read_bytes()
        committed = subprocess.check_output(['git', '-C', str(root), 'show', f'HEAD:{rel}'])
        assert data == committed, rel
        target['files'][rel] = {'sha256': sha(data), 'bytes': len(data), 'excerpts': spans}
        lines = data.decode().splitlines()
        for lo, hi in spans:
            excerpts.append(f'\n{head} {rel}:{lo}-{hi}\n' + '\n'.join(f'{n}: {lines[n-1]}' for n in range(lo, min(hi, len(lines)) + 1)))
    receipt['targets'][label] = target

packed = TARGETS['packed'][0]
changed = git(packed, 'diff', '--name-only', '7093f141d8081e6b96cd0a80814876aed5a3b577', TARGETS['packed'][1]).splitlines()
assert changed == ['src/chunk/store/read_amplification_tests.rs'], changed
receipt['packed_vs_measured_changed_paths'] = changed
freeze = Path('/home/dev/ax-writeback-commit-cleanup-freeze.git')
delta = git(freeze, 'diff', '--name-only', TARGETS['issues'][1], 'e2c964c55fafdcced34af58e840ebddda1cf9ea4').splitlines()
assert len(delta) == 1 and delta[0].endswith('cached_write_watermarks_bench.rs'), delta
receipt['publication_vs_runtime_changed_paths'] = delta
for name, digest in [
    ('/home/dev/ax-writeback-commit-cleanup-validation/ci-final.json', '2102568589d8a2608648c6342fb40f936f286e5c1eb5a78aa8a384ddc169755a'),
    ('/home/dev/ax-known-tail-trace-01/receipt.json', '8d0be53e535dedbe6802a869f33768df2f09db70d541dfbb6c7fa9cfa5435cb0'),
]:
    data = Path(name).read_bytes()
    assert sha(data) == digest, name
    receipt['prior_behavioral_evidence'].append({'path': name, 'sha256': digest, 'rerun_in_this_review': False})
for label, (root, expected) in TARGETS.items():
    assert git(root, 'rev-parse', 'HEAD') == expected and git(root, 'status', '--porcelain') == ''
with (OUT/'source-excerpts.txt').open('x') as f:
    f.write('\n'.join(excerpts) + '\n')
with (OUT/'source-receipt.json').open('x') as f:
    json.dump(receipt, f, indent=2)
    f.write('\n')
manifest = {p.name: {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size} for p in sorted(OUT.iterdir()) if p.is_file()}
with (OUT/'artifact-sha256.json').open('x') as f:
    json.dump(manifest, f, indent=2)
    f.write('\n')
print(json.dumps({'result': 'source identities and evidence references verified', 'findings': 0, 'output': str(OUT), 'source_receipt_sha256': sha((OUT/'source-receipt.json').read_bytes()), 'artifact_manifest_sha256': sha((OUT/'artifact-sha256.json').read_bytes())}))
