"""Bind a reviewed C4 release build receipt to the installed executable."""
import re
from pathlib import Path, PurePosixPath

from .common import load, pinned

REVIEW = 'c4-native-100k-release-review-v1'
BUILD = 'c4-rbm-normal-release-build-v1'
SOURCE = 'ca07159f3da4b670e4aeb878faef928e69bc1634'
TREE = '1ee7c97a427156d9c2cfe056f857e2e01efeb714'
PATCH = '4464b9aaeb287d31c0dd6b8f2fcfdef02193e16d0e85e193b913f86acc1177a2'
OPT0 = '3ecde1a2a4a55d450b7680661c655fd8d08491afc6aebacef84b58cea76bd6ee'
PROFILE = {'cargo': 'release', 'opt_level': 3, 'debug': 0, 'incremental': 0, 'cargo_jobs': 3}
LOGS = {'prepare.log', 'build.log', 'build.exit', 'artifact.sha256', 'source-versions.json',
        'build.sh', 'fork-help.txt', 'publish-help.txt'}


def _pattern(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _canonical(ref):
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
            or not isinstance(ref['path'], str) or not _pattern(ref['sha256'], r'[0-9a-f]{64}')):
        raise ValueError('Exact path and SHA-256 reference required')
    path = Path(ref['path'])
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('Canonical absolute release reference required')


def verify(review_ref, binary_ref):
    """Read a human-reviewed descriptor, collector receipt and actual local bytes."""
    for ref in (review_ref, binary_ref):
        _canonical(ref)
    review = load(pinned(review_ref))
    if not isinstance(review, dict) or review.get('schema') != REVIEW or review.get('reviewed') is not True:
        raise ValueError('Human-reviewed release descriptor required')
    if set(review) != {'schema', 'reviewed', 'build_receipt', 'binary'} or review['binary'] != binary_ref:
        raise ValueError('Release review must pin this installed executable and its build receipt')
    receipt_ref = review['build_receipt']
    _canonical(receipt_ref)
    receipt = load(pinned(receipt_ref))
    if (not isinstance(receipt, dict) or receipt.get('schema') != BUILD
            or receipt.get('outcome') != 'verified-release-artifact'):
        raise ValueError('Successful rbm release build receipt required')
    if (receipt.get('source_commit'), receipt.get('source_tree'), receipt.get('patch_sha256'),
            receipt.get('prior_opt0_sha256')) != (SOURCE, TREE, PATCH, OPT0):
        raise ValueError('Release source, tree, patch or prior diagnostic pin differs')
    profile = receipt.get('profile')
    if profile != PROFILE or any(type(profile[key]) is not int for key in PROFILE if key != 'cargo'):
        raise ValueError('Normal optimized release profile required')
    invocation = receipt.get('invocation_id')
    terminal = receipt.get('terminal_unit')
    if (not _pattern(receipt.get('unit'), r'ax-brewfs-c4-native-fork-[a-z0-9-]+-release-[0-9]+\.service')
            or not _pattern(invocation, r'[0-9a-f]{32}') or not isinstance(terminal, list)
            or any(not isinstance(line, str) or '=' not in line for line in terminal)):
        raise ValueError('Exact rbm release unit and invocation required')
    status = dict(line.split('=', 1) for line in terminal)
    expected = {'LoadState': 'loaded', 'InvocationID': invocation, 'ActiveState': 'active',
                'SubState': 'exited', 'Result': 'success', 'ExecMainCode': '1',
                'ExecMainStatus': '0', 'MainPID': '0'}
    if len(status) != len(terminal) or any(status.get(key) != value for key, value in expected.items()):
        raise ValueError('Release unit must have exited successfully with the same invocation')
    logs = receipt.get('logs_sha256')
    if not isinstance(logs, dict) or set(logs) != LOGS or any(
            not _pattern(sha, r'[0-9a-f]{64}') for sha in logs.values()):
        raise ValueError('Release collector must pin its source, build and CLI logs')
    artifact = receipt.get('artifact')
    if not isinstance(artifact, dict) or set(artifact) != {'remote_path', 'sha256', 'bytes'}:
        raise ValueError('Exact release artifact record required')
    remote = artifact['remote_path']
    if (not isinstance(remote, str) or not PurePosixPath(remote).is_absolute()
            or PurePosixPath(remote).name != 'brewfs-linux-x86_64'):
        raise ValueError('Expected remote release executable path')
    binary = pinned(binary_ref)
    if (binary.name != 'brewfs' or artifact['sha256'] in (None, OPT0)
            or artifact['sha256'] != binary_ref['sha256'] or type(artifact['bytes']) is not int
            or artifact['bytes'] <= 0 or artifact['bytes'] != binary.stat().st_size):
        raise ValueError('Installed executable SHA-256 or size differs from release artifact')
    return {'review': review_ref, 'build_receipt': receipt_ref}
