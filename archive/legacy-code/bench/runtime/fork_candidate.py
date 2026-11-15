"""Check the source and artifact pins for the qualified C4 candidate."""
import hashlib
import json

SOURCE = 'ca07159f3da4b670e4aeb878faef928e69bc1634'
TREE = '1ee7c97a427156d9c2cfe056f857e2e01efeb714'
PATCH = '4464b9aaeb287d31c0dd6b8f2fcfdef02193e16d0e85e193b913f86acc1177a2'


def verify_candidate(binary, provenance, revision):
    build = json.loads(provenance.read_text())
    if not isinstance(build, dict):
        raise ValueError('C4 build receipt must be an object')
    if revision != SOURCE or build.get('source_commit') != SOURCE or build.get('source_tree') != TREE:
        raise ValueError('C4 source differs from the qualified candidate')
    patch = build.get('candidate_patch_sha256', build.get('patch_sha256'))
    if patch != PATCH or any(build.get(k, patch) != patch for k in ('candidate_patch_sha256', 'patch_sha256')):
        raise ValueError('C4 publication/root-nlink patch differs')
    if not (build.get('passed') is True or build.get('build_exit_code') == 0):
        raise ValueError('C4 build has no successful receipt')
    if ('passed' in build and build['passed'] is not True) or ('build_exit_code' in build and build['build_exit_code'] != 0):
        raise ValueError('C4 build receipt reports failure')
    with binary.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if build.get('artifact_sha256') != digest:
        raise ValueError('C4 executable SHA differs from its build receipt')
    return dict(source_commit=SOURCE, source_tree=TREE, candidate_patch_sha256=PATCH,
                artifact_sha256=digest, provenance_sha256=hashlib.sha256(provenance.read_bytes()).hexdigest(),
                scope='patched correctness candidate; not an upstream or performance build')
