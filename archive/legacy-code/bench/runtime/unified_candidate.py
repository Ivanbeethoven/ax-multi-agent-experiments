"""Pin a patched functional candidate; never use this as performance admission."""
import hashlib
import json
import re

SCHEMA = 'brewfs-unified-candidate-v1'
FEATURES = {'workspace-overlay', 'observability', 'rustfs-ec-block-size-hint'}


def verify(binary, producer, provenance, revision, capability):
    build = json.loads(provenance.read_text())
    if (build.get('schema') != SCHEMA or build.get('build_exit_code') != 0
            or build.get('source_commit') != revision
            or not re.fullmatch('[0-9a-f]{40}', revision)
            or not re.fullmatch('[0-9a-f]{64}', build.get('candidate_diff_sha256', ''))
            or not FEATURES.issubset(build.get('cargo_features', []))
            or build.get('cargo_default_features') is not True
            or capability not in build.get('capabilities', [])):
        raise ValueError('Candidate source, build, features or capability differs')
    pins = {}
    for name, path in (('brewfs', binary), ('producer', producer)):
        if path is None:
            continue
        with path.open('rb') as stream:
            pins[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        if pins[name] != build.get('binary_sha256', {}).get(name):
            raise ValueError('Candidate '+name+' SHA differs from its build receipt')
    return pins
