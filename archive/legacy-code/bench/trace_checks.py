#!/usr/bin/env python3
"""Read-only trace checks. Each invocation writes one receipt and stops."""
import argparse
import json
from pathlib import Path
import sys

from experiment_io import digest, load

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from full_trace_common import tree


def inputs(case, manifest, expected_sha256):
    case = case.resolve(strict=True)
    if digest(manifest) != expected_sha256:
        raise ValueError('input manifest digest mismatch')
    inventory = load(manifest)
    required = {'capture/events.jsonl','metrics.json','initial-tree.json','final-tree.json'}
    if not required.issubset(inventory):
        raise ValueError('input manifest must include events, metrics, initial and final tree')
    for relative, expected in inventory.items():
        path = (case/relative).resolve(strict=True)
        if not path.is_relative_to(case) or digest(path) != expected:
            raise ValueError('input digest or path mismatch: '+relative)
    for folder in (case/'capture/blobs', case/'initial'):
        if not folder.is_dir():
            raise ValueError('required input directory missing')
        for path in folder.rglob('*'):
            if path.is_file() and not path.is_symlink() and path.relative_to(case).as_posix() not in inventory:
                raise ValueError('unlisted input payload')
    return {'passed':True, 'check':'inputs', 'manifest_sha256':expected_sha256,
            'scope':'inventory hashes only; trusted trace provenance and target isolation checked by operator'}


def replay(result, metrics):
    value, expected = load(result), load(metrics)
    if not value.get('completed') or value.get('remaining_handles') != 0:
        raise ValueError('incomplete replay or live handles')
    for actual, source in [('operations','callbacks'),('actual_read_bytes','actual_read_bytes'),
                           ('actual_write_bytes','actual_write_bytes')]:
        if source not in expected or value.get(actual) != expected[source]:
            raise ValueError('replay count mismatch: '+actual)
    return {'passed':True,'check':'replay-counts','strict_correctness':False,
            'result_sha256':digest(result),'metrics_sha256':digest(metrics),
            'scope':'completion and work counts; content and remount checks are separate'}


def content(root, expected):
    if not root.is_dir():
        raise ValueError('target tree missing')
    if tree(root) != load(expected):
        raise ValueError('file tree differs from expected paths/content/modes')
    return {'passed':True,'check':'tree','expected_sha256':digest(expected),
            'scope':'paths, types, lengths, file SHA256, link targets and modes; not all POSIX state'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    inp=sub.add_parser('inputs')
    inp.add_argument('--case',type=Path,required=True)
    inp.add_argument('--manifest',type=Path,required=True)
    inp.add_argument('--sha256',required=True)
    rep=sub.add_parser('replay')
    rep.add_argument('--result',type=Path,required=True)
    rep.add_argument('--metrics',type=Path,required=True)
    check=sub.add_parser('tree')
    check.add_argument('--root',type=Path,required=True)
    check.add_argument('--expected',type=Path,required=True)
    for cmd in (inp,rep,check):
        cmd.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    with a.output.open('x',encoding='utf-8') as stream:
        try:
            result=(inputs(a.case,a.manifest,a.sha256) if a.action=='inputs' else
                    replay(a.result,a.metrics) if a.action=='replay' else content(a.root,a.expected))
        except Exception as error:
            json.dump({'passed':False,'check':a.action,'error':str(error)},stream,indent=2)
            raise
        json.dump(result,stream,indent=2)
    print(json.dumps(result))


if __name__=='__main__':
    main()
