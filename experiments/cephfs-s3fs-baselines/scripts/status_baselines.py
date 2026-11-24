#!/usr/bin/env python3
"""Single-shot status query for CephFS/s3fs baseline runs and cluster readiness."""

import argparse
import json
from pathlib import Path
import subprocess

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PKG_DIR = SCRIPT_DIR.parent


REMOTE_QUERY = r"""
import json, pathlib, subprocess

rt = json.loads(pathlib.Path('/srv/ax-cluster/baselines/configs/baseline-runtime.json').read_text()) if pathlib.Path('/srv/ax-cluster/baselines/configs/baseline-runtime.json').is_file() else __RT_FALLBACK__
run_root = pathlib.Path(rt['remote_runs_root']) / rt['run_id']
mode = __MODE__
status_name = {'tiny': 'tiny-status.json', 'check': 'check-status.json', 'formal': 'status.json'}[mode]
status_file = run_root / status_name
status = json.loads(status_file.read_text()) if status_file.is_file() else {'current': None, 'jobs': {}}

unit_map = {
    'tiny': rt.get('runner_tiny_unit', rt['runner_unit'] + '-tiny'),
    'check': rt['runner_check_unit'],
    'formal': rt['runner_unit'],
}
unit = unit_map[mode]
unit_state = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True).stdout.strip() or 'inactive'

main_units = {}
for mu in rt.get('guard_main_units', ['ax-run-002']):
    main_units[mu] = subprocess.run(['systemctl', 'is-active', mu], capture_output=True, text=True).stdout.strip() or 'inactive'

main_active = []
for p_str in rt.get('guard_active_main_runs', []):
    p = pathlib.Path(p_str)
    if p.is_file():
        d = json.loads(p.read_text())
        if d.get('current'):
            main_active.append({'file': p_str, 'current': d['current'], 'completed': d.get('completed_runs')})

try:
    ceph_p = subprocess.run(['/usr/bin/ceph', '--connect-timeout', '2', '-s', '-f', 'json'], capture_output=True, text=True, timeout=4)
    ceph_state = (json.loads(ceph_p.stdout).get('health') or {}).get('status', 'running') if (ceph_p.returncode == 0 and ceph_p.stdout.strip()) else 'stopped'
except Exception:
    ceph_state = 'stopped'

counts = {}
for j in (status.get('jobs') or {}).values():
    st = j.get('state', 'unknown')
    counts[st] = counts.get(st, 0) + 1

print(json.dumps({
    'run_id': rt['run_id'],
    'mode': mode,
    'runner_unit': unit,
    'runner_unit_state': unit_state,
    'current_job': status.get('current'),
    'job_state_counts': counts,
    'ceph_cluster_state': ceph_state,
    'main_suite_units': main_units,
    'main_suite_active_jobs': main_active,
}, indent=2))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--check", action="store_true", help="Query check-status.json instead of formal status.json")
    mode_grp.add_argument("--tiny", action="store_true", help="Query tiny-status.json for ultra-small verification")
    args = parser.parse_args()

    mode = "tiny" if args.tiny else ("check" if args.check else "formal")
    rt_cfg = json.loads((BASELINE_PKG_DIR / "configs" / "baseline-runtime.json").read_text(encoding="utf-8"))
    py_code = REMOTE_QUERY.replace("__RT_FALLBACK__", repr(rt_cfg)).replace("__MODE__", repr(mode))

    p = subprocess.run(
        ["ssh", "cluster-node-1", "python3 -"],
        input=py_code,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if p.returncode != 0:
        raise SystemExit(f"Remote status query failed (rc={p.returncode}):\n{p.stderr}")
    print(p.stdout.strip())


if __name__ == "__main__":
    main()
