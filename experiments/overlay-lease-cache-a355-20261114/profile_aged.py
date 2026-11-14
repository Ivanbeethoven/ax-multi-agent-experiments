"""Separate diagnostic: wait 61 s after warmup, then retain stage/Redis evidence.

Same final binary: production label selects TTL, candidate selects lease.
Only the 32-file writable arms wait; all three pairs exchange order. Server
profiling and HTTP sampling make these diagnostic, not acceptance wall times.
"""
import json
from pathlib import Path
import os
import socket
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-profile-a355-20261114'))
from profile_stages import parse


def main():
    binary = Path(sys.argv[sys.argv.index('--binary') + 1]).resolve()
    candidate = Path(sys.argv[sys.argv.index('--candidate-binary') + 1]).resolve()
    assert binary == candidate, 'policy ablation must use one binary'
    os.environ['BREWFS_HOT_PATH_PROFILE'] = '1'
    latest = {}
    original_round = run_detail.measured_round
    def configure(module):
        popen, save = module.subprocess.Popen, module.save
        def observed_popen(args, *pos, **kw):
            if isinstance(args, list) and len(args) > 1 and args[1] == 'mount':
                config = Path(str(args[args.index('--config') + 1]))
                policy = 'ttl' if '-production-' in config.parent.name else 'lease'
                kw['env']['BREWFS_WORKSPACE_ROW_CACHE_POLICY'] = policy
                latest['policy'] = policy
                with socket.socket() as reserve:
                    reserve.bind(('127.0.0.1', 0))
                    port = reserve.getsockname()[1]
                latest['metrics'] = f'http://127.0.0.1:{port}/metrics'
                args = [args[0], '--metrics', '--metrics-addr', f'127.0.0.1:{port}', *args[1:]]
                latest['command'] = list(map(str, args))
            return popen(args, *pos, **kw)
        def saved(path, value):
            if path.name == 'warm.json' and path.parent.name.startswith('small-files-32-') and 'packed-direct' not in path.parent.name:
                started = time.monotonic_ns()
                time.sleep(61)
                save(path.with_name('age-proof.json'), dict(
                    seconds=(time.monotonic_ns() - started) / 1e9,
                    phase='after warmup, before counters and first timed read',
                    mount_command=latest['command'], row_cache_policy=latest['policy'], diagnostic_only=True))
            if path.name == 'run.json':
                value['diagnostic_only'] = True
                value['age_seconds'] = 61
                value['policy_ablation'] = {'production': 'ttl', 'candidate': 'lease', 'same_binary': True}
            save(path, value)
        module.subprocess.Popen, module.save = observed_popen, saved
    def metrics():
        with urllib.request.urlopen(latest['metrics'], timeout=10) as response:
            return parse(response.read().decode())
    def measured(root, expected):
        before = metrics()
        result = original_round(root, expected)
        after = metrics()
        result['server_profile_delta'] = {k: after[k] - before.get(k, 0) for k in after if k != 'brewfs_hot_path_profile_enabled'}
        result['diagnostic_only'] = True
        result['server_profile_command'] = latest['command']
        return result
    run_detail.measured_round = measured
    run_detail.main(configure_runner=configure)


if __name__ == '__main__':
    main()
