#!/usr/bin/env python3
"""Check an already collected task without model calls or editing captured databases."""
import argparse
import json
from pathlib import Path
import sys

RUNTIME = Path(__file__).resolve().parents[1] / 'assets/runtime'
sys.path.insert(0, str(RUNTIME / 'scripts'))
from summarize_local_full_traces import summarize
sys.path.insert(0, str(RUNTIME / 'scenarios/k8s-containers/scripts'))
from replay_case import tree


def hardlinks(root):
    by_inode = {}
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            by_inode.setdefault((stat.st_dev, stat.st_ino), []).append(path.relative_to(root).as_posix())
    return sorted(sorted(paths) for paths in by_inode.values() if len(paths) > 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    expected = json.loads((source / 'final-tree.json').read_text())
    if tree(source / 'backing') != expected or tree(source / 'replayed') != expected:
        raise ValueError('Current final tree differs from the recorded oracle')
    result = summarize(source)
    equal = hardlinks(source / 'backing') == hardlinks(source / 'replayed')
    if not equal:
        raise ValueError('Captured/replayed hardlink topology differs')
    result['hardlink_topology_equal'] = equal
    (source / 'skill-check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
