"""Summarize C2 from untouched receipts and bind the three arms to one trace."""
import argparse
import hashlib
import json
from pathlib import Path

ARMS = {'brewfs-flat': ('brewfs', 'flat-v1'),
        'brewfs-workspace': ('brewfs', 'workspace-v1'),
        'juicefs': ('juicefs', None)}
WORK = {'operations': 1_747_109, 'actual_read_bytes': 281_774_624,
        'actual_write_bytes': 194_706_661}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(run, overrides):
    restoration = json.loads((run.parent / 'c2-mode-restoration.json').read_text())
    if restoration['passed'] is not True or restoration['entries'] != 714:
        raise ValueError('Expected the verified initial tree')
    trace = restoration['events_sha256']
    rows, pins = [], []
    for name, (product, engine) in ARMS.items():
        source_run = overrides.get(name, run)
        path = source_run / f'c2-{name}.json'
        trial = json.loads(path.read_text())
        if (trial['trace_sha256'] != trace or trial['trace_callbacks'] != WORK['operations']
                or trial['trace_kind'] != 'full-fuse-input' or trial['workload'] != 'c2'
                or engine and trial['engine'] not in (engine, engine.replace('-v1',''))):
            raise ValueError(f'Wrong input or engine: {name}')
        arm, replay = trial['arms'][product], trial.get('replay', {})
        replay_ok = bool(replay and all(replay.get(k) == v for k, v in WORK.items())
                         and replay.get('completed') is True and replay.get('valid') is True
                         and replay.get('remaining_handles') == 0
                         and replay.get('think_time', {}).get('enabled') is False
                         and replay.get('mode') == 'strict-portable-terminal-probe')
        hot = arm.get('run', {})
        hot_ok = (hot.get('passed') is True and hot.get('replayed') is True
                  and hot.get('byte_validation') is True and hot.get('final_byte_validation') is True
                  and hot.get('initial_tree_equal') is True and hot.get('final_tree_equal') is True
                  and hot.get('hardlink_topology_equal') is True)
        lower_ok = name != 'brewfs-workspace' or (arm.get('lower_objects', {}).get('unchanged') is True
                                                and arm.get('lower_objects', {}).get('count', 0) > 0)
        repeated = hot.get('captured_repeated_read_ratio')
        if repeated is not None and (type(repeated) not in (int, float) or not 0 <= repeated <= 1):
            raise ValueError(f'Invalid captured repeated-read ratio: {name}')
        cold = arm.get('verify_touched', {})
        cold_ok = (cold.get('passed') is True and cold.get('replayed') is False
                   and cold.get('final_tree_equal') is True and cold.get('hardlink_topology_equal') is True
                   and bool(cold.get('databases')) and all(x['quick_check'] == 'ok' for x in cold['databases'])
                   and arm.get('reopen_unmount', {}).get('mount_absent') is True)
        if trial['passed'] and not (replay_ok and hot_ok and cold_ok and lower_ok and trial['cleanup']['passed']):
            raise ValueError(f'Passed receipt lacks full replay, tree, lower-isolation, cold or cleanup proof: {name}')
        backend = trial['shared_backend']
        pins.append((trial['binary_sha256']['replay'], trial['rustfs_image'], backend['s3'], backend['redis'],
                     trial['binary_sha256']['brewfs'], trial['binary_sha256']['producer'], trial['binary_sha256']['juicefs']))
        rows.append(dict(arm=name, passed=trial['passed'], error=arm.get('error') or trial.get('error'),
                         replay_seconds=replay.get('active_replay_seconds'),
                         **{k: replay.get(k) for k in WORK}, cold_verified=cold_ok,
                         replay_verified=replay_ok, initial_and_final_verified=hot_ok,
                         immutable_lower_verified=lower_ok if name == 'brewfs-workspace' else None,
                         captured_repeated_read_ratio=repeated,
                         cold_databases=cold.get('databases', []),
                         terminal_probe_errno_differences=replay.get('probe_errno_differences'),
                         receipt=f'{source_run.name}/{path.name}', receipt_sha256=sha(path)))
    if len(set(pins)) != 1:
        raise ValueError('Replay binary or backend changed between arms')
    return dict(run=run.name, trace='pytest-full-02', trace_events_sha256=trace,
                initial_tree_sha256=restoration['initial_tree_sha256'],
                replay_binary_sha256=pins[0][0], rustfs_image=pins[0][1], formal_sample=False,
                brewfs_binary_sha256=pins[0][4], producer_binary_sha256=pins[0][5], juicefs_binary_sha256=pins[0][6],
                timer='Rust active replay wall time; excludes setup, terminal-state verification and cold reopen',
                read_write_scope='Successful POSIX bytes reported by the replay; excludes preparation and verification',
                repeated_read_scope='Captured workload repeated bytes; not a backend cache-hit measurement',
                arms=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--arm-run', action='append', default=[], metavar='ARM=RUN_DIR')
    args = parser.parse_args()
    overrides = {}
    for raw in args.arm_run:
        name, sep, folder = raw.partition('=')
        if not sep or name not in ARMS or name in overrides or not folder:
            parser.error(f'Invalid arm override: {raw}')
        source = Path(folder)
        if source.resolve().parent != args.run.resolve().parent:
            parser.error('All arm runs must belong to the same campaign receipts directory')
        overrides[name] = source
    value = summarize(args.run, overrides)
    complete = all(arm['passed'] for arm in value['arms'])
    path = args.run / ('c2-comparison.json' if complete else
                       'c2-retry-incomplete.json' if overrides else 'c2-incomplete.json')
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, indent=2)
        out.write('\n')
    print(path)
    return 0 if complete else 2


if __name__ == '__main__':
    raise SystemExit(main())
