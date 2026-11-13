"""Prepare, but never execute, the complete original matrix in reversed arm order."""
import ast
import difflib
import hashlib
import json
from pathlib import Path

SOURCE=Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-05/harness/run-corrected-standard-guard-pair-512.py')
OUT=Path('/home/dev/ax-full-reverse-603')
EXPECTED='d2ca7f25aa30bfc3011344d36bfa02b3f7abbbd030b3b95c920a39c320ddd05a'
changes=[
    ("OUT = ROOT/'matrices/regression-corrected512-01'", "OUT = ROOT/'matrices/regression-corrected512-reverse603'"),
    ("for arm in ('baseline','candidate'):", "for arm in ('candidate','baseline'):"),
    ("identity = f'ax447-corrected-{arm}-regression-601'", "identity = f'ax447-corrected-{arm}-regression-603'"),
    ("arm,'regression','601'", "arm,'regression','603'"),
    ("'case':'regression','trial':601", "'case':'regression','trial':603"),
]

def sha(data):return hashlib.sha256(data).hexdigest()

OUT.mkdir(exist_ok=True)
old=SOURCE.read_text();assert sha(SOURCE.read_bytes())==EXPECTED
new=old
for before,after in changes:
    assert new.count(before)==1,before
    new=new.replace(before,after)
ast.parse(new)
# Mechanical inverse must recover every original byte, including all six-scene gates.
inverse=new
for before,after in reversed(changes):
    assert inverse.count(after)==1,after
    inverse=inverse.replace(after,before)
assert inverse==old
for invariant in [
    "for mode in ('seqwrite','randwrite','randrw') for direct in (0,1)",
    'len(capacity_rows) == 12',
    "assert len(json.loads((artifact/'precise-phase-summary.json').read_text())['tools']) == 6",
    'reserve = 256 * 1024**3',
    'current.f_files//5',
    'process.wait(timeout=2)',
    'fcntl.flock(lock,fcntl.LOCK_EX)',
    "ROOT/'run-compose-case.py'",
    "checkpoint['passed'] is True",
]:
    assert invariant in old and invariant in new,invariant
name='run-standard-full-reverse-603.py'
(OUT/name).write_text(new)
(OUT/(name+'.patch')).write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(SOURCE),tofile=name)))
receipt={'schema':'ax.full-matrix-reverse-plan.v1','executed':False,
    'source':str(SOURCE),'source_sha256':EXPECTED,'script_sha256':sha(new.encode()),
    'only_changed_fields':['arm order','trial number','unique output directory'],
    'inverse_derivation_byte_identical':True,'syntax_passed':True,'invariant_checks':9,
    'arm_order':['candidate','baseline'],'trial':603,'case':'regression',
    'scenario_order':[f'fio-{mode}-direct{direct}' for mode in ('seqwrite','randwrite','randrw') for direct in (0,1)],
    'matrix':'0 1','profile':'512m/20s, inherited unchanged from original case',
    'output':'/home/dev/ax-perf-issues-corrected-validation/compose/matrices/regression-corrected512-reverse603',
    'case_script_sha256':'a6621e4987ca488487ea45e455d7fd473ba3620ea518475c898e227941584085',
    'full_comparator_sha256':'14fd7c9745ece9b489bbab9065e1fee146a8f8d0501eb7542dbaf4002bcbe698',
    'original601_comparison_sha256':'b9a81cc1bbe3f2144e3bbb865a22c75c681adb3de303301448d7c608b6ecdb79',
    'prior_preparation_602':'prepared only; not deployed or executed; superseded by full603 at root instruction',
    'limits':'This is a preparation receipt, not proof that workload/qualification/performance passed. Original601 remains immutable; full603 is an independent matched pair.'}
(OUT/'plan-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
