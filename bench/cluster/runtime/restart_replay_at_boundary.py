"""Reload worker placement between two completed/untouched replay conditions."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    import cluster_lib as cl
    from run_suite import save

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after', required=True)
    parser.add_argument('--next', required=True)
    parser.add_argument('--expected-run', required=True)
    args = parser.parse_args()
    if cl.RUN_ID != args.expected_run:
        parser.error('Unexpected run directory')
    root = cl.RUN_ROOT
    unit = 'ax-' + cl.RUN_ID + '.service'
    jobs = json.loads((root / 'plan.json').read_text())['jobs']
    ids = [j['id'] for j in jobs if j['group'] == '04-agent-replay']
    if args.after not in ids or ids[ids.index(args.after)+1:][:1] != [args.next]:
        parser.error('Expected consecutive replay conditions')
    ready = root / next(j['output'] for j in jobs if j['id'] == args.after) / 'result.json'
    next_dir = root / next(j['output'] for j in jobs if j['id'] == args.next)
    path = root / 'continuations' / 'replay-worker-reload.json'

    def pid():
        return int(subprocess.check_output(['systemctl', 'show', unit, '-p', 'MainPID', '--value']))

    def states():
        return json.loads((root / 'status.json').read_text())

    def between(s):
        return (s['current'] == args.after and s['jobs'][args.after]['state'] == 'passed'
                and s['jobs'][args.next]['state'] == 'planned'
                and not any(next_dir.glob('attempt-*')))

    original = pid()
    if original <= 1:
        raise RuntimeError('Expected a running replay coordinator')
    fd = os.pidfd_open(original)
    cmd = Path(f'/proc/{original}/cmdline').read_bytes().split(b'\0')
    if cmd[1:4] != [str(cl.SCRIPTS_DIR / 'run_suite.py').encode(), b'--group', b'04-agent-replay']:
        raise RuntimeError('Coordinator command does not match the replay group')
    save(path, dict(state='waiting', after=args.after, following=args.next, pid=original))
    try:
        last_check = -10.0
        while not ready.exists():
            if time.monotonic() - last_check >= 5:
                if pid() != original or states()['jobs'][args.after]['state'] == 'failed':
                    raise RuntimeError('Coordinator changed or the preceding condition failed')
                last_check = time.monotonic()
            time.sleep(.05)
        while not between(states()):
            if pid() != original or states()['jobs'][args.next]['state'] != 'planned':
                raise RuntimeError('Missed the handoff; leaving the running condition alone')
            time.sleep(.01)
        # Freeze just the coordinator, then confirm that it did not start a new
        # attempt between our status read and the signal. Workers are untouched.
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        try:
            for _ in range(100):
                if '\nState:\tT' in Path(f'/proc/{original}/status').read_text():
                    break
                time.sleep(.01)
            else:
                raise RuntimeError('Coordinator did not enter stopped state')
            if not between(states()) or pid() != original:
                raise RuntimeError('Condition advanced; resuming original coordinator')
            subprocess.run(['systemctl', 'restart', unit], check=True, timeout=120)
        finally:
            try:
                signal.pidfd_send_signal(fd, signal.SIGCONT)
            except ProcessLookupError:
                pass
        replacement = pid()
        if replacement <= 1 or replacement == original:
            raise RuntimeError('Expected a new replay coordinator')
        save(path, dict(state='reloaded', after=args.after, following=args.next,
                        old_pid=original, new_pid=replacement, time=cl.utc_iso()))
    except Exception as error:
        save(path, dict(state='not-reloaded', after=args.after, following=args.next,
                        detail=str(error), time=cl.utc_iso()))
        raise
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
