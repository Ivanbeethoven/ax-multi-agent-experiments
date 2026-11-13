"""Check current entry points, Python syntax and local documentation links; no experiments."""
import ast
import json
from pathlib import Path
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LEGACY_EXPERIMENT = ROOT / 'experiments/retest-2026-10-29'
EXPERIMENT = ROOT / 'experiments/unified-suite'


def check():
    docs = [ROOT/'README.md', ROOT/'AGENTS.md', ROOT/'archive/README.md', ROOT/'tests/README.md', ROOT/'experiments/README.md']
    for directory in ('bench', 'infra', 'paper-design'):
        docs.extend((ROOT/directory).rglob('*.md'))
    docs.extend((ROOT/'scripts').glob('*.md'))
    docs.extend((ROOT/'experiments/idcai-six-node-20261113').glob('*.md'))
    docs.extend(LEGACY_EXPERIMENT.glob('*.md'))
    historical_docs = list(EXPERIMENT.rglob('*.md'))
    frozen_docs = [doc for doc in historical_docs if 'evidence' in doc.relative_to(EXPERIMENT).parts]
    docs.extend(doc for doc in historical_docs if doc not in frozen_docs)
    docs.extend((LEGACY_EXPERIMENT/'runbook').rglob('*.md'))
    docs.extend((LEGACY_EXPERIMENT/'scripts').glob('*.md'))
    docs.extend((ROOT/'tools').glob('*/README.md'))
    docs.append(ROOT/'tools/full-fuse-replay/THINK-TIME.zh.md')
    errors = []
    for doc in sorted(set(docs)):
        if not doc.is_file():
            errors.append(f'missing entry point: {doc.relative_to(ROOT)}')
            continue
        for match in re.finditer(r'\]\(([^\s)]+)\)', doc.read_text(encoding='utf-8')):
            target = match[1]
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:|^#|^/',target):
                continue
            target = unquote(target.split('#',1)[0])
            if target and not (doc.parent/target).exists():
                errors.append(f'{doc.relative_to(ROOT)}: broken link: {target}')
    sources = []
    for directory in ('bench','infra','tests'):
        sources.extend((ROOT/directory).rglob('*.py'))
    sources.extend((ROOT/'scripts').glob('*.py'))
    sources.extend((LEGACY_EXPERIMENT/'scripts').glob('*.py'))
    sources.extend((EXPERIMENT/'pilot-aliyun/scripts').glob('*.py'))
    sources.extend((ROOT/'experiments/idcai-six-node-20261113/scripts').glob('*.py'))
    for source in sources:
        try: ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
        except SyntaxError as exc: errors.append(str(exc))
    for legacy in ('harness','infra/alicloud/parallel','configs/adapters','paper-design/tools'):
        directory=ROOT/legacy
        if any(directory.rglob('*.py')) or any(directory.glob('*.json')):
            errors.append(f'legacy implementation returned to active tree: {legacy}')
    if errors: raise ValueError('\n'.join(errors))
    return {'passed':True,'documents_checked':len(set(docs)),'python_sources_parsed':len(sources),
            'frozen_evidence_documents_not_link_checked':len(frozen_docs)}


if __name__ == '__main__':
    print(json.dumps(check()))
