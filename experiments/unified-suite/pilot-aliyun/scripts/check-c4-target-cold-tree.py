#!/usr/bin/env python3
"""Check the entire workspace on the freshly mounted target before OpenCode opens it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime import handoff, node
from bench.runtime.common import load, mounts, pinned, spec, write_new

source = importlib.util.spec_from_file_location(
    'session_input', Path(__file__).with_name('prepare-session-input.py'))
session_input = importlib.util.module_from_spec(source)
source.loader.exec_module(session_input)


def fingerprint(tree):
    # Pin the JSON file itself separately. This digest covers the observed tree
    # regardless of whitespace or ordering in the reviewed manifest.
    return hashlib.sha256(json.dumps(tree, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode('ascii')).hexdigest()


def absolute(path):
    p = Path(path)
    if not p.is_absolute() or p == p.parent or p.resolve() != p:
        raise ValueError('Canonical absolute path required')
    return p


def outside(path, point):
    p = absolute(path)
    if p.is_relative_to(point):
        raise ValueError('Proof and manifest belong outside the tested mount')
    return p


def check(ref, output):
    s = spec(ref, 'c4-target-cold-tree-request-v1')
    if set(s) - {'schema','reviewed','review','mount_spec','mount_directory','workspace',
                 'expected_tree','expected_tree_sha256'}:
        raise ValueError('Unknown cold-tree request field')
    mount_spec = spec(s['mount_spec'], 'experiment-mount-v1')
    point = absolute(mount_spec['mount_point'])
    workspace = absolute(s['workspace'])
    if workspace != point/'workspace' or not workspace.is_dir():
        raise ValueError('The complete canonical target workspace is required')
    mount_directory = outside(s['mount_directory'], point)
    output = outside(output, point)
    if output.exists():
        raise ValueError('Stale target result')
    expected_path = outside(s['expected_tree']['path'], point)
    expected = load(pinned(s['expected_tree']))
    if not isinstance(expected, dict) or not expected or fingerprint(expected) != s['expected_tree_sha256']:
        raise ValueError('Independently pinned expected tree is missing or changed')
    ready = load(mount_directory/'ready.json')
    if ready['spec'] != s['mount_spec'] or ready['anchor'] != mount_spec['anchor']:
        raise ValueError('Wrong reviewed target mount')

    def current():
        status = node.control(mount_directory, 'status')
        if (handoff.target_identity(status) != handoff.target_identity(ready)
                or mounts(point) != [status['mount']]):
            raise ValueError('Target FUSE mount or its process changed')
        return status

    before = current()
    observed = session_input.tree(workspace)
    after = current()
    if handoff.target_identity(before) != handoff.target_identity(after):
        raise ValueError('Target was replaced during tree read')
    sha = fingerprint(observed)
    if observed != expected or sha != s['expected_tree_sha256']:
        raise ValueError('Full target tree differs from the expected source tree')
    files = [r for r in observed.values() if r['type'] == 'file']
    if not files or not (total := sum(r['bytes'] for r in files)):
        raise ValueError('Nonempty file content must actually be checked')
    if any(p.endswith(('.db-wal','.db-journal')) and r.get('bytes',0)
           for p,r in observed.items()):
        raise ValueError('An unclosed SQLite journal is present on target')
    result = dict(schema='c4-target-cold-tree-v1',passed=True,full_tree_read=True,read_only=True,
                  native_app_opened=False,workspace_copy_bytes=0,workspace=str(workspace),
                  anchor=after['anchor'],mount_spec=s['mount_spec'],mount_directory=str(mount_directory),
                  mount=after['mount'],process=after['process'],observed_tree_sha256=sha,
                  files_hashed=len(files),bytes_hashed=total,expected_tree=s['expected_tree'])
    write_new(output,result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='action', required=True)
    f = commands.add_parser('fingerprint', help='Compute the stable digest of the expected manifest')
    f.add_argument('manifest')
    r = commands.add_parser('check')
    for name in ('request','sha256','out'):
        r.add_argument('--'+name,required=True)
    a = p.parse_args()
    if a.action == 'fingerprint':
        print(fingerprint(load(a.manifest)))
    else:
        print(json.dumps(check(dict(path=a.request,sha256=a.sha256),a.out)))
