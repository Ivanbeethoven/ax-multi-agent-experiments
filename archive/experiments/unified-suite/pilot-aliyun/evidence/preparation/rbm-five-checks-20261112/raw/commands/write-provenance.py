import hashlib,json,sys
from pathlib import Path
job=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/home/dev/ax-five-fixes-20261112')
def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
assert (job/'build.exit').read_text().strip()=='0'
value=dict(schema='brewfs-unified-candidate-v1',build_exit_code=0,
    source_commit='ca07159f3da4b670e4aeb878faef928e69bc1634',
    candidate_diff_sha256=sha(job/'candidate.patch'),
    cargo_default_features=True,cargo_features=['workspace-overlay','observability','rustfs-ec-block-size-hint'],
    cargo_profile='release; package.brewfs.opt-level=0, debug=0; derived binaries objcopy --strip-debug',
    capabilities=['packed-metadata-off','eager-workspace'],
    binary_sha256={name:sha(job/'bin'/binary) for name,binary in [('brewfs','brewfs'),('producer','packed_v3_snapshot_fixture')]},
    formal_performance_qualified=False,full_rust_ci_passed=False)
(job/'build-provenance.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value))
