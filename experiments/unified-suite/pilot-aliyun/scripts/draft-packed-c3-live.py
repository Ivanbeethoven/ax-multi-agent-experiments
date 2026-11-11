#!/usr/bin/env python3
"""Bind a fresh Packed calibration draft to a retained full cold proof."""

import argparse
from pathlib import Path
import runpy
import shlex
import subprocess
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.runtime.common import digest, load, pinned, write_new


def ref(path):
    return dict(path=str(Path(path).resolve(strict=True)), sha256=digest(path))


def draft(source, formal_path, checker, node_path, node_sha, out):
    source_request = load(source)
    if source_request.get('schema') != 'packed-c3-pairs-v1' or source_request.get('reviewed') is not True:
        raise ValueError('An original reviewed six-trial plan is required')
    controller = runpy.run_path(str(Path(__file__).with_name('packed-c3-controller.py')))
    controller['pair_schedule'](source_request['contexts'])
    formal, _ = controller['cell'](formal_path)
    if formal.purpose != 'experiment':
        raise ValueError('The separate formal context is required')
    full_pin = ref(formal.qualification / 'filesystem.json')
    full = load(pinned(full_pin))
    files = formal.cell['params']['files']
    if full.get('passed') is not True or full.get('cell_id') != formal.id or full.get('files') != files:
        raise ValueError('Retained full cold qualification must match the cell')
    for name in ('cold_child', 'cold_parent'):
        proof = load(pinned(full['proofs'][name]))
        flags = ('passed', 'expected_tree_equal', 'expected_hardlinks_equal',
                 'namespace_unchanged', 'authority_unchanged', 'volume_rows_unchanged')
        if not all(proof.get(flag) is True for flag in flags) or proof['files'] != files or proof['logical_file_bytes'] != files * 4096:
            raise ValueError('Incomplete cold proof: ' + name)
    node = dict(path=str(node_path), sha256=node_sha)
    if not node_path.is_absolute() or len(node_sha) != 64 or any(c not in '0123456789abcdef' for c in node_sha):
        raise ValueError('Actual remote calibration helper path and SHA required')
    argv = ['ssh', '-F', str(formal.runtime / 'ssh.conf'), formal.pool + '-client',
            shlex.join(['sha256sum', node['path']])]
    observed = subprocess.check_output(argv, text=True).split()[0]
    if observed != node_sha:
        raise ValueError('Actual remote calibration helper changed')
    contexts = []
    for source_context in source_request['contexts']:
        p = pinned(source_context)
        original = load(p)
        for key in ('control', 'cell_id', 'qualification', 'runtime_control', 'entity_tool', 'node_tool', 'observer_tools'):
            if original.get(key) != formal.s.get(key):
                raise ValueError('Calibration differs from formal binding: ' + key)
        if Path(original['out']).exists():
            raise ValueError('A calibration path has already been used: ' + original['out'])
        original.update(base_runner=formal.s['base_runner'], calibration_node=node,
                        calibration_verification='live-touched-v1', full_cold_qualification=full_pin)
        contexts.append((p.name, original))
    out.mkdir(parents=True, exist_ok=False)
    (out / 'contexts').mkdir()
    for name, value in contexts:
        write_new(out / 'contexts' / name, value)
    request = dict(schema=source_request['schema'], reviewed=False,
                   contexts=[ref(out / 'contexts' / name) for name, _ in contexts], checker=ref(checker))
    write_new(out / 'pairs-request.draft.json', request)
    write_new(out / 'result.json', dict(passed=True, original_request=ref(source), formal_context=ref(formal_path),
                                        full_cold_qualification=full_pin, remote_helper=node,
                                        helper_pin_command=argv, draft=ref(out / 'pairs-request.draft.json'),
                                        measured_workload_started=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'formal-context', 'checker', 'calibration-node', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--calibration-node-sha256', required=True)
    a = p.parse_args()
    draft(a.source, a.formal_context, a.checker, a.calibration_node, a.calibration_node_sha256, a.out)
