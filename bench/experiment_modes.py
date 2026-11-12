"""Resolve C1-C4 experiment flags without dispatching workloads."""
import argparse
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'experiments/unified-suite/current/design.json'
MODES = {
    'C1-io': ('brewfs-basic', 'juicefs'),
    'C1-layout': ('brewfs-basic', 'dynamic-off', 'hint-64k', 'hint-256k',
                  'hint-1m', 'hint-4m', 'block-only'),
    'C2-replay': ('brewfs-basic', 'brewfs-full', 'juicefs', 'packed-off', 'dynamic-off', 'all-off'),
    'C2-runtime': ('brewfs-full', 'overlay-off'),
    'C3-smallfiles': ('brewfs-full', 'juicefs', 'packed-off'),
    'C4-fork': ('brewfs-full', 'juicefs'),
    'C4-handoff': ('brewfs-full', 'juicefs'),
}
FIXED_HINTS = {'hint-64k': 65536, 'hint-256k': 262144, 'hint-1m': 1048576, 'hint-4m': 4194304}
SWITCHES = ('overlay', 'packed_metadata', 'dynamic_block')


def resolve(design, experiment, mode, placement=None):
    if mode not in MODES[experiment]:
        raise ValueError(f'{experiment} does not support {mode}')
    if (experiment == 'C4-handoff') != (placement is not None):
        raise ValueError('--placement stay|move is required only for C4-handoff')
    c1 = experiment == 'C1-layout'
    if mode in design['arms']:
        arm = design['arms'][mode]
    elif c1:
        arm = design['arms']['brewfs-basic']
    else:
        ablation = next(item for item in design['ablations'] if item['id'] == mode)
        arm = {'engine': ablation['engine'], **ablation['switches']}
    engine = arm['engine']
    settings = {k: arm.get(k) for k in SWITCHES}
    layout = 'enabled'
    hint = None if engine == 'juicefs' else 'auto'
    if mode == 'dynamic-off' or mode == 'all-off':
        settings['dynamic_block'] = False
        layout, hint = 'disabled', 'off'
    elif mode == 'block-only':
        layout = 'block-only'
    elif mode in FIXED_HINTS:
        hint = str(FIXED_HINTS[mode])
    blockers = []
    if mode in ('overlay-off', 'packed-off', 'all-off'):
        blockers.append('same-engine-runtime-adapter-required')
    if experiment == 'C2-runtime':
        blockers.append('same-mount-lower-warmth-qualification-required')
    if experiment == 'C4-fork' and engine != 'juicefs':
        blockers.append('populated-packed-lower-fork-failed-rbm')
    if experiment == 'C4-handoff':
        blockers.append('same-workspace-handoff-qualification-required')
    if experiment == 'C2-replay' or experiment == 'C3-smallfiles':
        blockers.append('current-suite-workload-adapter-qualification-required')
    if c1:
        blockers.append('persisted-layout-qualification-required')
    brewfs = design['versions']['brewfs']
    return {
        'experiment': experiment, 'mode': mode, 'placement': placement, 'engine': engine,
        'mechanisms': settings, 'rustfs_layout_profile': layout,
        'brewfs_hint': hint, 'brewfs_cargo_default_features': brewfs['default_features'],
        'brewfs_cargo_features': brewfs['cargo_features'],
        'native_action': ('clone' if engine == 'juicefs' else 'fork') if experiment == 'C4-fork' else None,
        'runtime_qualification_blockers': blockers,
        'dispatch_enabled': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', choices=MODES, required=True)
    parser.add_argument('--mode', required=True)
    parser.add_argument('--placement', choices=('stay', 'move'))
    args = parser.parse_args()
    try:
        result = resolve(json.loads(CONFIG.read_text()), args.experiment, args.mode, args.placement)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
