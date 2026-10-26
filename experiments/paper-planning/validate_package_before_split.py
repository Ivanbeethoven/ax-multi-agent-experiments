#!/usr/bin/env python3
"""Validate this planning package. Does not run or validate BrewFS experiments."""
from __future__ import annotations
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []
required = [
    'main.tex', 'references.bib',
    'usenix2019_v3.sty', 'templates/usenix-official/usenix2019_v3.sty',
    'planning/01-research-outline.zh.md', 'planning/02-experiment-plan.zh.md',
    'experiments/matrix.csv', 'experiments/run-manifest.example.yaml',
    'experiments/observations.schema.json', 'experiments/implementation-backlog.csv',
    'evidence/implementation_inventory.csv', 'references/source_registry.csv',
    'skills/osdi-systems-paper/SKILL.md',
    'experiments/ax-multi-agent/paper-design/design.json',
    'experiments/ax-multi-agent/paper-design/trace-archives.json',
    'experiments/ax-multi-agent/paper-design/fixtures.json',
    'experiments/ax-multi-agent/paper-design/live-tasks.json'
]
for name in required:
    if not (ROOT / name).is_file():
        errors.append(f'Missing file: {name}')
if errors:
    print(json.dumps({'passed': False, 'errors': errors}, indent=2)); sys.exit(1)

bib = (ROOT / 'references.bib').read_text()
keys = re.findall(r'@\w+\s*\{\s*([^,\s]+)', bib)
if len(keys) != len(set(keys)):
    errors.append('Duplicate BibTeX keys')
tex_files = list((ROOT / 'sections').glob('*.tex')) + [ROOT / 'main.tex']
tex = '\n'.join(p.read_text() for p in tex_files)
cited = set()
for group in re.findall(r'\\cite(?:\[[^\]]*\])?\{([^}]+)\}', tex):
    cited.update(k.strip() for k in group.split(','))
missing = sorted(cited - set(keys))
if missing: errors.append(f'Undefined bibliography keys: {missing}')

with (ROOT / 'experiments/matrix.csv').open(newline='') as f:
    matrix = list(csv.DictReader(f))
ids = [row['experiment_id'] for row in matrix]
design_dir = ROOT / 'experiments/ax-multi-agent/paper-design'
design = json.loads((design_dir / 'design.json').read_text(encoding='utf-8'))
expected_ids = {e['id'] for e in design['experiments']}
expected_rqs = {q['id'] for q in design['research_questions']}
if expected_rqs != {f'RQ{i}' for i in range(1, 6)}:
    errors.append('Expected five mechanism-first research questions')
if design['status'] != 'planned' or design['execution_enabled'] is not False:
    errors.append('Design must remain planned and execution disabled')
if set(ids) != expected_ids or len(ids) != len(expected_ids):
    errors.append('Matrix must contain every design experiment exactly once')
by_id = {e['id']: e for e in design['experiments']}
rqs = set()
for row in matrix:
    rqs.update(row['rq'].split('|'))
    if row['status'] != 'planned': errors.append(f"Unexpected status: {row['experiment_id']}")
    for field in ['intervention', 'primary_metrics', 'implementation_gate', 'weakening_outcome']:
        if not row[field].strip(): errors.append(f"Empty {field}: {row['experiment_id']}")
    entry = by_id.get(row['experiment_id'])
    if entry and set(row['rq'].split('|')) != set(entry['rqs']):
        errors.append(f"RQ mismatch: {row['experiment_id']}")
if rqs != expected_rqs: errors.append('RQ coverage differs from current design')
paper_rqs = set(re.findall(r'\\researchq\{(\d+)\}', tex))
if paper_rqs != {str(i) for i in range(1, 6)}:
    errors.append('Paper and plan must use the same five RQs')
archives = json.loads((design_dir / 'trace-archives.json').read_text(encoding='utf-8'))['archives']
if len(archives) != 7 or {a['case'] for a in archives} != set(design['inputs']['case_order']):
    errors.append('Trace catalog must contain the seven frozen cases')
for a in archives:
    if not re.fullmatch(r'[0-9a-f]{64}', a['sha256']) or a['bytes'] <= 0:
        errors.append(f"Invalid archive identity: {a['case']}")
    if not a['https_url'].startswith('https://'):
        errors.append(f"Missing HTTPS download: {a['case']}")
replay = by_id['replay']['parameters']
scale = by_id['scale']['parameters']
if replay['assignments'] != len(archives) * replay['copies_per_case']:
    errors.append('Replay assignment count mismatch')
if replay['base_replays'] != replay['assignments'] * replay['base_batches']:
    errors.append('Replay campaign count mismatch')
if scale['all_scale_replays'] != sum(scale['primary_workers'] + scale['extension_workers']) * len(archives) * scale['external_system_count'] * scale['initial_independent_paired_batches']:
    errors.append('Scale campaign count mismatch')

schema = json.loads((ROOT / 'experiments/observations.schema.json').read_text())
if schema['properties']['status'].get('const') != 'executed': errors.append('Bad observation status contract')
if set(schema['properties']['experiment_id'].get('enum', [])) != expected_ids:
    errors.append('Observation schema experiment IDs differ from current design')
manifest = (ROOT / 'experiments/run-manifest.example.yaml').read_text()
if 'status: planned' not in manifest or 'measurements: null' not in manifest:
    errors.append('Manifest template must remain visibly unexecuted')

styles = [ROOT / 'usenix2019_v3.sty', ROOT / 'templates/usenix-official/usenix2019_v3.sty']
hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in styles]
expected_style = '4ed9aa9d0c5daec54ce3b4188a4bf6d3bef3462c79ff65551d567511d3c45ff6'
if hashes != [expected_style, expected_style]: errors.append('Official style hash mismatch')

log_status = {}
for stem in ['main']:
    p = ROOT / (stem + '.log')
    if not p.exists():
        log_status[stem] = 'not available; build with make'
        continue
    log = p.read_text(errors='replace')
    problems = [s for s in ['Citation', 'Reference', 'Overfull', 'Missing character']
                if ((s in ['Citation','Reference'] and re.search(s + r'.*undefined', log))
                    or (s in ['Overfull','Missing character'] and s in log))]
    if problems: errors.append(f'{stem} LaTeX issues: {problems}')
    log_status[stem] = 'checked' if not problems else problems

report = {
    'passed': not errors, 'scope': 'planning/package validation only; no runtime experiments',
    'bibliography_entries': len(keys), 'cited_entries': len(cited),
    'experiments': len(matrix), 'research_questions': sorted(x for x in rqs if x.startswith('RQ')),
    'official_style_sha256': hashes[0], 'latex_logs': log_status, 'errors': errors
}
print(json.dumps(report, indent=2))
sys.exit(0 if not errors else 1)
