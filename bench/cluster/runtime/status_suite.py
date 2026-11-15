"""Print one compact progress snapshot without starting a monitoring loop."""
import json
from pathlib import Path
import subprocess

from runtime_context import RUN_ROOT, RUN_ID


def main():
    plan = json.loads((RUN_ROOT / 'plan.json').read_text())
    status = json.loads((RUN_ROOT / 'status.json').read_text())
    groups = {}
    for group, total in plan['counts'].items():
        selected = [status['jobs'].get(job['id'], {}) for job in plan['jobs'] if job['group'] == group]
        groups[group] = {'passed': sum(v.get('state') == 'passed' for v in selected), 'total': total,
                         'failed': sum(v.get('state') == 'failed' for v in selected)}
    current = status['jobs'].get(status.get('current'), {})
    measurement = Path(current['output']) / 'measurement.json' if current.get('output') else None
    phase = json.loads(measurement.read_text()).get('phase') if measurement and measurement.exists() and current.get('state') == 'running' else current.get('state')
    active = subprocess.run(['systemctl', 'is-active', 'ax-' + RUN_ID], capture_output=True, text=True)
    print(json.dumps({'run': RUN_ID, 'service': active.stdout.strip(), 'current': status.get('current'),
        'phase': phase, 'groups': groups, 'failed': {key: value for key, value in status['jobs'].items()
                                   if value.get('state') == 'failed'}, 'directory': str(RUN_ROOT)}, indent=2))


if __name__ == '__main__':
    main()
