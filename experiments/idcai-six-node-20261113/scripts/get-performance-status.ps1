param([Parameter(Mandatory)][ValidatePattern('^ax-four-[0-9a-f]{12}$')][string]$Run,
      [ValidateSet('c1','c2','c3','c4')][string]$Case)

$ssh = Join-Path $env:WINDIR 'System32/OpenSSH/ssh.exe'
$command = @'
import json, sys
from pathlib import Path

run, case = sys.argv[1:]
root = Path('/srv/ax-idcai-20261113/campaign-100k-001') / run
if not root.is_dir():
    raise SystemExit('Unknown run: ' + run)
rows = []
for trial in sorted(root.glob((case or 'c[1-4]') + '-*-qualification')):
    row = {'trial': trial.name}
    receipt = trial / 'receipt.json'
    if not receipt.is_file():
        receipt = trial / 'trial.json'
    if receipt.is_file():
        data = json.loads(receipt.read_text())
        error = data.get('error') or next((arm['error'] for arm in data.get('arms', {}).values()
                                           if arm.get('error')), None)
        row.update(passed=data.get('passed'), finished=True, error=error)
    else:
        row.update(finished=False, phase='starting')
    events = trial / 'stages.jsonl'
    if events.is_file():
        for line in reversed(events.read_text().splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            row['last_event'] = {key: event.get(key) for key in ('name', 'event', 'status')}
            if not row['finished']:
                row['phase'] = event.get('name')
            break
    for replay in trial.glob('*/run/replay.json'):
        try:
            data = json.loads(replay.read_text())
        except json.JSONDecodeError:
            continue
        row['replay'] = {key: data.get(key) for key in (
            'completed', 'valid', 'operations', 'actual_read_bytes', 'actual_write_bytes',
            'active_replay_seconds', 'remaining_handles')}
    for stage, label in [('run', 'verified'), ('verify-touched', 'cold_verified')]:
        for proof in trial.glob(f'*/{stage}/verification.json'):
            try:
                data = json.loads(proof.read_text())
            except json.JSONDecodeError:
                continue
            row[label] = bool(data.get('passed'))
    if not row['finished'] and 'replay' not in row:
        for process in trial.glob('*/run/process.json'):
            try:
                info = json.loads(process.read_text())
                if info['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
                    continue
                proc = Path('/proc') / str(info['pid'])
                for fd in (proc / 'fd').iterdir():
                    if not str(fd.readlink()).endswith('/capture/events.jsonl'):
                        continue
                    size = fd.stat().st_size
                    position = next(int(line.split()[1]) for line in
                                    (proc / 'fdinfo' / fd.name).read_text().splitlines()
                                    if line.startswith('pos:'))
                    row['phase'] = 'replaying'
                    row['trace_read'] = {'bytes': position, 'total_bytes': size,
                                         'file_percent': round(100 * position / size, 2) if size else None,
                                         'scope': 'JSON input bytes consumed; not operation count or completion'}
                    break
            except (OSError, KeyError, ValueError, StopIteration):
                pass  # The child can close descriptors between checks.
    for code in trial.glob('*/run/exit.json'):
        try:
            data = json.loads(code.read_text())
        except json.JSONDecodeError:
            continue
        row['replay_process_exit'] = data.get('exit_code')
        row['replay_process_seconds'] = data.get('wall_seconds')
    if not row['finished'] and row.get('replay', {}).get('completed'):
        if not row.get('verified'):
            row['phase'] = 'replay-complete-awaiting-final-state-proof'
        elif not row.get('cold_verified'):
            row['phase'] = 'awaiting-cold-reopen-proof'
        else:
            row['phase'] = 'awaiting-cleanup-receipt'
    rows.append(row)
print(json.dumps({'run': run, 'case': case or None, 'trials': rows}, indent=2))
'@
$command | & $ssh -T -o BatchMode=yes -o ConnectTimeout=8 idcai-jump "python3 - '$Run' '$Case'"
if ($LASTEXITCODE -ne 0) { throw 'Unable to read trial status from the coordinator.' }
