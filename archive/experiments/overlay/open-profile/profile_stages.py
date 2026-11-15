"""Diagnostic run only: capture opt-in server stage times around each round.

Uses the matched Redis/RustFS harness unchanged. HTTP snapshots are outside
the caller timer, but profiling adds server work: do not use as acceptance BW.
Nested stage totals overlap, and server handler time excludes FUSE queueing.
"""
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail


def parse(raw):
    result = {}
    for line in raw.splitlines():
        if not line or line.startswith('#'):
            continue
        name, value = line.rsplit(' ', 1)
        if (name.startswith('brewfs_hot_path_') or
                name.startswith('brewfs_fuse_op_latency_seconds_sum{') or
                name.startswith('brewfs_fuse_op_total{') or
                name.startswith('brewfs_metadata_op_latency_seconds_sum{')):
            result[name] = float(value)
    return result


def main():
    os.environ['BREWFS_HOT_PATH_PROFILE'] = '1'
    latest = {}
    probed = set()
    output = Path(sys.argv[sys.argv.index('--out') + 1])
    original_round = run_detail.measured_round

    def configure(module):
        popen, save = module.subprocess.Popen, module.save
        commandstats = module.commandstats
        latest['module'] = module

        def counted(port):
            latest['redis_port'] = port
            return commandstats(port)

        def observed_popen(args, *positional, **kwargs):
            if isinstance(args, list) and len(args) > 1 and args[1] == 'mount':
                with socket.socket() as reserve:
                    reserve.bind(('127.0.0.1', 0))
                    port = reserve.getsockname()[1]
                latest['metrics'] = f'http://127.0.0.1:{port}/metrics'
                args = [args[0], '--metrics', '--metrics-addr', f'127.0.0.1:{port}', *args[1:]]
                latest['mount_command'] = list(map(str, args))
            return popen(args, *positional, **kwargs)

        def saved(path, value):
            if path.name == 'run.json':
                value['diagnostic_only'] = True
                value['stage_profile'] = 'BREWFS_HOT_PATH_PROFILE=1; metrics sampled outside caller timer'
            save(path, value)

        module.subprocess.Popen = observed_popen
        module.save = saved
        module.commandstats = counted

    def metrics():
        with urllib.request.urlopen(latest['metrics'], timeout=10) as response:
            return response.read().decode()

    def measured(root, expected):
        before = parse(metrics())
        assert before['brewfs_hot_path_profile_enabled'] == 1
        sample = original_round(root, expected)
        after = parse(metrics())
        sample['server_profile_delta'] = {k: after[k] - before.get(k, 0) for k in after
                                          if k != 'brewfs_hot_path_profile_enabled'}
        sample['server_profile_command'] = latest['mount_command']
        sample['diagnostic_only'] = True
        if root not in probed and 'small-files-32-pair1-production' in root.name:
            probed.add(root)
            # Keep the immutable payload hot while deliberately allowing the
            # unchanged 60 s metadata TTL to expire. This is not a perf pair.
            time.sleep(61)
            module = latest['module']
            raw_before = (root / '.stats').read_text()
            redis_before = module.commandstats(latest['redis_port'])
            stages_before = parse(metrics())
            probe = original_round(root, expected)
            stages_after = parse(metrics())
            redis_after = module.commandstats(latest['redis_port'])
            raw_after = (root / '.stats').read_text()
            objects = module.delta(module.client_objects(raw_after, 'production'),
                                   module.client_objects(raw_before, 'production'))
            assert not any(objects.values()), 'payload must remain hot during TTL probe'
            record = dict(diagnostic_only=True, metadata_idle_seconds=61,
                phase='preparation mount, after first full read',
                mount_command=latest['mount_command'],
                payload_hot=objects, sample=probe,
                stages={k: stages_after[k] - stages_before.get(k, 0) for k in stages_after
                        if k != 'brewfs_hot_path_profile_enabled'},
                redis_delta=module.redis_delta(redis_after, redis_before))
            (output / root.name / 'metadata-expired-probe.json').write_text(json.dumps(record, indent=2))
            (output / root.name / 'metadata-expired-before.stats').write_text(raw_before)
            (output / root.name / 'metadata-expired-after.stats').write_text(raw_after)
        return sample

    run_detail.measured_round = measured
    run_detail.main(configure_runner=configure)


if __name__ == '__main__':
    main()
