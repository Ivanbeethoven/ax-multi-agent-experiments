#!/usr/bin/env python3
"""Re-read six archived arms using the frozen fixed-work assessor."""
import hashlib
import importlib.util
import json
import statistics
from pathlib import Path

ROOT = Path('/home/dev/ax-perf-issues-validation/corrected-runtime-review-11')
OUT = Path('/home/dev/ax-randwrite-drain-diagnosis-01/final-scope-review')
OUT.mkdir(exist_ok=True)
manifest_bytes = (ROOT / 'manifest.json').read_bytes()
manifest = json.loads(manifest_bytes)
for item in manifest['files']:
    data = (ROOT / item['path']).read_bytes()
    assert len(data) == item['bytes']
    assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
prefix = ROOT / 'home/dev/ax-perf-issues-corrected-validation/compose'
harness = prefix / 'fixed-work-610'
module = importlib.util.spec_from_file_location('frozen_fixed11', harness / 'assess_fixed_work.py')
assessor = importlib.util.module_from_spec(module)
module.loader.exec_module(assessor)
frozen_manifest = json.loads((harness / 'manifest.json').read_text())
for filename, item in frozen_manifest.items():
    assert hashlib.sha256((harness / filename).read_bytes()).hexdigest() == item['sha256']
receipt = json.loads((prefix / 'matrices/fixed-work-randwrite1-610/receipt.json').read_text())
assert receipt['passed'] and receipt['historical_601_603_replaced'] is False
assert receipt['manifest_sha256'] == hashlib.sha256((harness/'manifest.json').read_bytes()).hexdigest()
assert [(p['trial'],p['order']) for p in receipt['pairs']] == [(610,['baseline','candidate']),(611,['candidate','baseline']),(612,['baseline','candidate'])]
pairs=[]
for pair in receipt['pairs']:
    values={}
    for arm in pair['order']:
        recorded=pair['arms'][arm]
        artifact=ROOT/recorded['artifact'].lstrip('/')
        fresh=assessor.assess(artifact)
        for key in fresh:
            if key != 'artifact':
                assert fresh[key] == recorded[key], (pair['trial'],arm,key)
        project=f"ax447-corrected-{arm}-fixed-work-randwrite1-{pair['trial']}"
        p=prefix/'trials'/project/'receipt.json'
        trial=json.loads(p.read_text())
        assert trial['qualified'] and trial['status']==0
        assert hashlib.sha256(p.read_bytes()).hexdigest()==recorded['trial_receipt_sha256']
        assert not (p.parent/'remaining-owned-containers.json').read_text().strip()
        values[arm]={key:fresh[key] for key in ['write_bytes','write_ios','active_s','active_plus_strict_drain_s','cpu_per_gib_s','active_mib_s','active_plus_drain_mib_s']}
        values[arm]['full_cpu_s']=fresh['cpu']['tool_and_drain_cpu']['total_s']
        values[arm]['active_cpu_s']=fresh['cpu']['tool_cpu']['total_s'] if 'tool_cpu' in fresh['cpu'] else None
        values[arm]['drain_wall_s']=fresh['active_plus_strict_drain_s']-fresh['active_s']
    ratios={key:values['candidate'][key]/values['baseline'][key] for key in pair['candidate_over_baseline']}
    assert ratios==pair['candidate_over_baseline']
    pairs.append({'trial':pair['trial'],'order':pair['order'],'arms':values,'candidate_over_baseline':ratios})
medians={arm:{key:statistics.median(p['arms'][arm][key] for p in pairs) for key in ['active_s','active_plus_strict_drain_s','full_cpu_s','cpu_per_gib_s','drain_wall_s']} for arm in ['baseline','candidate']}
ratio_of_medians={k:medians['candidate'][k]/medians['baseline'][k] for k in medians['baseline']}
proof={'kind':'fixed11-six-arm-frozen-assessor-readback','passed':True,'archive_payload_files_checked':len(manifest['files']),
       'archive_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
       'frozen_harness_manifest_sha256':receipt['manifest_sha256'],
       'pairs':pairs,'absolute_medians':medians,'candidate_over_baseline_ratio_of_medians':ratio_of_medians,
       'scope':'Fresh namespace, identical 4 GiB logical write count; whole tool plus strict writeback drain. Later compaction lifecycle is not included.',
       'historical_601_603_replaced':False,'fixed_guard_alone_proves_performance_gain':False,
       'qualification_note':'This proof validates archived facts, not an automatic universal performance claim.'}
(OUT/'fixed11-readback.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({'proof_sha256':hashlib.sha256((OUT/'fixed11-readback.json').read_bytes()).hexdigest(),
                  'checked':len(manifest['files']),'pairs':pairs,'absolute_medians':medians,'ratio_of_medians':ratio_of_medians},indent=2))
