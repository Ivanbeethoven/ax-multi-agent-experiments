"""Select independent functional-check blocks and preview their exact commands."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from bench.experiment_modes import MODES
from bench.experiment_blocks import BLOCKS, selected_cases
from bench.experiment_smoke import argument_parser


RESERVED = {'--out', '--experiment', '--mode', '--placement'}


def argument_list(value):
    return (isinstance(value, list) and all(isinstance(v, str) for v in value)
            and not any(v.split('=')[0] in RESERVED for v in value))


def check_command(command, parser):
    """Check arguments only. Binary hashes, mounts and runtime qualification stay in the child."""
    args = argument_parser().parse_args(command[3:])
    if args.experiment == 'C2-replay' and not (args.trace and args.replay):
        parser.error('C2-replay requires --trace and --replay before creating output')
    if args.experiment == 'C2-runtime' and not args.brewfs_provenance:
        parser.error('C2-runtime requires --brewfs-provenance')
    if args.mode in ('packed-off', 'all-off') and not args.brewfs_provenance:
        parser.error('metadata controls require --brewfs-provenance')
    if args.experiment == 'C4-fork' and args.mode == 'brewfs-full' and not args.brewfs_provenance:
        parser.error('C4 BrewFS fork requires --brewfs-provenance')
    if args.experiment == 'C4-handoff':
        if not args.checkpoint:
            parser.error('C4-handoff requires --checkpoint')
        if args.placement == 'move' and not all((args.ssh_config, args.target_host,
                                                args.target_repo, args.target_tools)):
            parser.error('C4 move requires --ssh-config, --target-host, --target-repo and --target-tools')


def read_json(path, parser):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        parser.error(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--common-args', type=Path, help='JSON array of shared experiment_smoke arguments')
    parser.add_argument('--case-overrides', type=Path, help='JSON map of case name to additional CLI arguments')
    parser.add_argument('--scratch-parent', type=Path, help='Existing local data directory for owned per-case TMPDIR')
    parser.add_argument('--scratch-peak-bytes', type=int, default=0, help='Additional temporary peak bytes reserved before each case')
    parser.add_argument('--scratch-peak-inodes', type=int, default=0, help='Additional temporary peak inodes reserved before each case')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--block', choices=BLOCKS, action='append', help='Select an independent functional-check group')
    selection.add_argument('--only', action='append', help='Run only this case; repeat for multiple cases')
    parser.add_argument('--list-blocks', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without creating directories or processes')
    args, extra = parser.parse_known_args()
    if args.list_blocks:
        if args.out or args.common_args or args.case_overrides or args.block or args.only or args.dry_run or args.scratch_parent or args.scratch_peak_bytes or args.scratch_peak_inodes or extra:
            parser.error('--list-blocks must be used alone')
        print(json.dumps({'formal_sample': False, 'blocks': BLOCKS}, ensure_ascii=False, indent=2))
        return 0
    if not args.out:
        parser.error('--out is required')
    if min(args.scratch_peak_bytes,args.scratch_peak_inodes) < 0 or ((args.scratch_peak_bytes or args.scratch_peak_inodes) and not args.scratch_parent):
        parser.error('nonnegative scratch estimates require --scratch-parent')
    common = read_json(args.common_args, parser) if args.common_args else []
    if not argument_list(common) or not argument_list(extra):
        parser.error('shared arguments cannot set out, experiment, mode or placement')
    common = common + extra
    overrides = read_json(args.case_overrides, parser) if args.case_overrides else {}
    identities = {'-'.join(x for x in (experiment, mode, placement) if x): (experiment, mode, placement)
                  for experiment, modes in MODES.items() for mode in modes
                  for placement in (('stay', 'move') if experiment == 'C4-handoff' else (None,))}
    if not isinstance(overrides, dict) or set(overrides) - identities.keys():
        parser.error('Unknown case override')
    if any(not argument_list(values) for values in overrides.values()):
        parser.error('Overrides must be tool/fixture arguments, not case or output selectors')
    if args.only and set(args.only) - identities.keys():
        parser.error('Unknown selected case')
    names = selected_cases(args.block) if args.block else list(dict.fromkeys(args.only or identities))
    output = args.out.resolve()
    if output.exists():
        parser.error('output directory already exists; inspect it and use a new attempt')
    commands = []
    for name in names:
        experiment, mode, placement = identities[name]
        cmd = [sys.executable, '-m', 'bench.experiment_smoke', *common, *overrides.get(name, []),
               '--experiment', experiment, '--mode', mode, '--out', str(output / name)]
        if placement:
            cmd += ['--placement', placement]
        check_command(cmd, parser)
        commands.append({'name': name, 'argv': cmd})
    plan = {'schema': 'ax-functional-block-plan-v1', 'formal_sample': False,
            'blocks': args.block or [], 'commands': commands, 'sequential': True,
            'stop_on_nonpassing_case': True, 'runtime_qualification_verified': False,
            'resource_lock': 'external lock on the complete source/backend/target pool required'}
    plan['scratch'] = {'parent':str(args.scratch_parent) if args.scratch_parent else None,
                       'peak_bytes':args.scratch_peak_bytes,'peak_inodes':args.scratch_peak_inodes,
                       'reserve_fraction':0.2,'policy':'remove only passed, closed, owned scratch; retain failures'}
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    output.mkdir(mode=0o700)
    (output / 'execution-plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
    rows = []
    for item in commands:
        name, cmd = item['name'], item['argv']
        code = None
        reported = None
        managed = None
        cleanup = None
        try:
            environment = None
            if args.scratch_parent:
                from bench.run_cleanup import create
                managed = output/(name+'.scratch')
                scratch = create(managed,args.scratch_parent,output/name/'receipt.json',
                                 args.scratch_peak_bytes,args.scratch_peak_inodes)
                environment = dict(os.environ,TMPDIR=str(scratch),TMP=str(scratch),TEMP=str(scratch))
            with (output / (name + '.log')).open('w', encoding='utf-8') as log:
                process = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=environment)
            code = process.returncode
            receipt = json.loads((output / name / 'receipt.json').read_text(encoding='utf-8'))
            if not isinstance(receipt, dict) or receipt.get('status') not in ('passed', 'failed', 'blocked'):
                raise ValueError('receipt must contain a known status')
            reported = receipt['status']
            status = reported
            reason = receipt.get('reason')
            if status == 'passed' and code != 0:
                status, reason = 'failed', 'child returned nonzero despite a passed receipt'
        except (OSError, ValueError) as error:
            receipt, status, reason = {}, 'failed', str(error)
        if managed is not None and (managed/'owner.json').exists():
            from bench.run_cleanup import finish
            try:
                cleanup = finish(managed,code)
            except (OSError,ValueError) as error:
                cleanup = {'passed':False,'retained':True,'reasons':[str(error)]}
            if status == 'passed' and not cleanup['passed']:
                status, reason = 'failed', 'scratch retained; inspect cleanup proof before the next case'
        rows.append({'name': name, 'status': status, 'reported_status': reported,
                     'elapsed_ns': receipt.get('elapsed_ns'), 'reason': reason,
                     'exit_code': code, 'receipt': name + '/receipt.json','scratch_cleanup':cleanup})
        (output / 'summary.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
        print(name + ': ' + status, flush=True)
        if status != 'passed' or code != 0:
            # No next case on possibly live mounts/services. Other independent pools may proceed.
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
