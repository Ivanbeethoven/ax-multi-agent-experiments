"""C1-C4 plans and one-step reservations. Never provisions or runs a whole campaign."""
import argparse
import json
from pathlib import Path

from .experiment_io import load, write_new
from .suite_catalog import template
from .suite_plan import build
from . import suite_state as state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action',required=True)
    t = sub.add_parser('template'); t.add_argument('--profile',choices=['pilot','main'],default='pilot')
    t.add_argument('--legacy-catalog', action='store_true',
                   help='Explicitly generate the historical unified-c1-c4-v1 catalog, not the current design')
    t.add_argument('--study',choices=['systems','ablations'],default='systems'); t.add_argument('--output',required=True)
    t = sub.add_parser('plan'); t.add_argument('--config',required=True); t.add_argument('--output',required=True)
    t = sub.add_parser('init'); t.add_argument('--plan',required=True); t.add_argument('--db',required=True)
    for name in ('status','qualify','asset','claim','run-stage','finish'):
        t = sub.add_parser(name); t.add_argument('--db',required=True)
        if name in ('qualify','asset','finish'): t.add_argument('--receipt',required=True)
        if name == 'claim':
            t.add_argument('--pool',required=True); t.add_argument('--owner',required=True); t.add_argument('--output',required=True)
        if name in ('run-stage','finish'): t.add_argument('--lease',required=True)
        if name == 'run-stage':
            t.add_argument('--stage',required=True); t.add_argument('--spec',required=True)
            t.add_argument('--sha256',required=True); t.add_argument('--output',required=True)
    a = p.parse_args()
    if a.action == 'template':
        if not a.legacy_catalog:
            p.error('Template uses historical entity/native-v2 routes. For new experiments read '
                    'experiments/unified-suite/current/README.zh.md; the new adapter is pending. '
                    'Use --legacy-catalog only for an intentional historical template.')
        write_new(a.output,template(a.profile,a.study))
    elif a.action == 'plan':
        result=build(load(a.config)); write_new(a.output,result); print(json.dumps(result['counts']))
    elif a.action == 'init': state.initialize(a.db,load(a.plan))
    elif a.action == 'status': print(json.dumps(state.status(a.db),indent=2))
    elif a.action == 'qualify': state.qualify(a.db,state.reference(a.receipt))
    elif a.action == 'asset': state.register_asset(a.db,state.reference(a.receipt))
    elif a.action == 'claim': write_new(a.output,state.claim(a.db,a.pool,a.owner))
    elif a.action == 'finish': state.finish(a.db,load(a.lease),state.reference(a.receipt))
    else: return state.run_stage(a.db,load(a.lease),a.stage,{'path':a.spec,'sha256':a.sha256},a.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
