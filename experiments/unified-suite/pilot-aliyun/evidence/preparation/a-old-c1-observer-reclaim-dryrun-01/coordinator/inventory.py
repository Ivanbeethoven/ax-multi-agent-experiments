"""Read only bounded metadata for four archived original Pool A C1 sources."""
import hashlib
import json
from pathlib import Path
import tarfile

P = Path('/srv/ax-pilot-control')
C = P/'c123-control-01'
cell = '7084af367e0bcfad-r1-native'
archives = [C/'cells'/cell/'archives/objects.tar.gz']
archives += [C/'retained-calibrations/native-randwrite-02'/tag/'objects.tar.gz'
             for tag in ('cal-n-rw-on1', 'cal-n-rw-on2', 'cal-n-rw-on3')]
rows = []
for path in archives:
    with tarfile.open(path, 'r|gz') as tar:
        first = tar.next()
        assert first.name == '.suite-manifest.json' and first.isfile() and first.size <= 16*1024**2
        m = json.load(tar.extractfile(first))
    assert m['cell_id'] == cell and m['deferred'] == []
    tags = {name.split('/')[1] for name in m['files'] if name.startswith('o/')}
    assert len(tags) == 1
    tag = tags.pop()
    files = {name: row for name, row in m['files'].items() if name.startswith('o/'+tag+'/')}
    check = path.with_name('objects-check.json' if tag.startswith('c1-') else 'objects-archive-check.json')
    checked = json.loads(check.read_bytes())
    rows.append(dict(tag=tag, archive=str(path), archive_bytes=path.stat().st_size,
                     role_check=str(check), role_sha256=checked['sha256'],
                     observer_members=len(files), observer_logical_bytes=sum(v['bytes'] for v in files.values())))
plan = json.loads((P/'parallel-control-01/suite/plan.json').read_bytes())
old = json.loads((C/'suite/plan.json').read_bytes())
controls = []
for path in sorted(P.glob('*/state.sqlite')):
    controls.append(dict(path=str(path.parent), resolved=str(path.parent.resolve()), database_resolved=str(path.resolve())))
print(json.dumps(dict(candidates=rows,
    canonical_locks=[dict(id=c['id'], pool=c['pool'], suite=c['suite'], case=c['case']) for c in plan['cells']
                     if c['pool']=='pool-a' and c['suite']=='C4' and c['case']=='fork-1'],
    original_cell=[dict(id=c['id'], pool=c['pool'], suite=c['suite']) for c in old['cells'] if c['id']==cell],
    controls=controls), indent=2))
