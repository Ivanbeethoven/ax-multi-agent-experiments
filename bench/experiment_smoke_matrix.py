"""Serial minimal-mode matrix; forwards build/tool flags to experiment_smoke."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from bench.experiment_modes import MODES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--case-overrides', type=Path, help='JSON map of case name to additional CLI arguments')
    parser.add_argument('--only', action='append', help='Run only this case; repeat for multiple cases')
    args, common = parser.parse_known_args()
    if any(flag in common for flag in ('--experiment','--mode','--placement')):
        parser.error('matrix selects experiment, mode and placement itself')
    output = args.out.resolve()
    output.mkdir(mode=0o700)
    overrides = json.loads(args.case_overrides.read_text()) if args.case_overrides else {}
    known = {'-'.join(x for x in (experiment,mode,placement) if x)
             for experiment,modes in MODES.items() for mode in modes
             for placement in (('stay','move') if experiment=='C4-handoff' else (None,))}
    if not isinstance(overrides,dict) or set(overrides)-known:
        parser.error('Unknown case override')
    if args.only and set(args.only)-known:
        parser.error('Unknown selected case')
    for values in overrides.values():
        if (not isinstance(values,list) or not all(isinstance(v,str) for v in values)
                or any(v.split('=')[0] in ('--out','--experiment','--mode','--placement') for v in values)):
            parser.error('Overrides must be tool/fixture arguments, not case or output selectors')
    rows = []
    for experiment, modes in MODES.items():
        for mode in modes:
            placements = ('stay','move') if experiment == 'C4-handoff' else (None,)
            for placement in placements:
                name = '-'.join(x for x in (experiment,mode,placement) if x)
                if args.only and name not in args.only:
                    continue
                cmd = [sys.executable,'-m','bench.experiment_smoke',*common,*overrides.get(name,[]),
                       '--experiment',experiment,'--mode',mode,'--out',str(output/name)]
                if placement:
                    cmd += ['--placement',placement]
                with (output/(name+'.log')).open('w') as log:
                    process = subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
                try:
                    receipt = json.loads((output/name/'receipt.json').read_text())
                except (OSError, ValueError) as error:
                    receipt = {'status':'failed','reason':str(error)}
                rows.append({'name':name,'status':receipt['status'],
                             'elapsed_ns':receipt.get('elapsed_ns'),
                             'reason':receipt.get('reason'),
                             'exit_code':process.returncode,
                             'receipt':name+'/receipt.json'})
                (output/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
                print(name+': '+receipt['status'],flush=True)
    return 0 if all(row['status']=='passed' for row in rows) else 2


if __name__ == '__main__':
    raise SystemExit(main())
