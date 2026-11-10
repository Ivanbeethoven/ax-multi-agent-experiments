#!/usr/bin/env python3
"""Strict local input qualification; keeps raw replay and checks tree/links/DB copies."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO/'scripts'))
from full_trace_common import tree


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def hardlinks(root):
    groups = {}
    for path in root.rglob('*'):
        st = path.lstat()
        if path.is_file() and not path.is_symlink():
            groups.setdefault((st.st_dev, st.st_ino), []).append(path.relative_to(root).as_posix())
    return sorted(sorted(names) for names in groups.values() if len(names) > 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--prepared-root', action='store_true', help='Use an already imported/isolated initial tree')
    parser.add_argument('--verify-only', action='store_true', help='Check a cold reopened final tree without replaying operations')
    parser.add_argument('--replay-result', type=Path, help='Original strict replay report for verify-only')
    parser.add_argument('--portable-terminal-probe',action='store_true',help='Only allow TCGETS ENOTTY to EOPNOTSUPP; retain all byte checks')
    args = parser.parse_args()
    case = args.case.resolve(strict=True)
    root = args.root.absolute()
    out = args.out.absolute()
    if args.verify_only != bool(args.replay_result):
        raise ValueError('verify-only requires its original strict replay result')
    for p in (root, out):
        existing_root=p==root and (args.prepared_root or args.verify_only) and p.is_dir()
        if (p.exists() and not existing_root) or p.is_symlink() or p.is_relative_to(case) or case.is_relative_to(p):
            raise ValueError('New, disjoint root and output required')
    if root.is_relative_to(out) or out.is_relative_to(root):
        raise ValueError('Output must be outside the replay tree')
    out.mkdir(parents=True, exist_ok=False)
    root.parent.mkdir(parents=True, exist_ok=True)
    purpose='cold-readback' if args.verify_only else 'filesystem-route-qualification' if args.prepared_root else 'local-input-qualification'
    save(out/'started.json', dict(at=datetime.now(timezone.utc).isoformat(), purpose=purpose,
                                  case=str(case), binary_sha256=digest(args.binary),
                                  events_sha256=digest(case/'capture/events.jsonl'), automatic_retry=False))
    if not args.prepared_root and not args.verify_only:
        subprocess.run(['cp', '-a', '--', str(case/'initial'), str(root)], check=True)
    if not args.verify_only and tree(root) != json.loads((case/'initial-tree.json').read_text()):
        raise ValueError('Initial tree differs')
    if not args.verify_only and hardlinks(root) != hardlinks(case/'initial'):
        raise ValueError('Initial hardlink topology differs')
    command = [str(args.binary.resolve()), str(case/'capture/events.jsonl'),
               str(case/'capture/blobs'), str(root), str(out/'replay.json')]
    if args.portable_terminal_probe:command.append('--portable-terminal-probe')
    if args.verify_only:
        save(out/'replay-source.json',dict(path=str(args.replay_result),sha256=digest(args.replay_result),replayed=False))
        report=json.loads(args.replay_result.read_text())
    else:
        save(out/'command.json', dict(argv=command, strict=not args.portable_terminal_probe,
              byte_validation=True,portable_terminal_probe=args.portable_terminal_probe,has_deadline=False))
        begin = time.monotonic()
        with (out/'stdout.log').open('xb') as stdout, (out/'stderr.log').open('xb') as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            save(out/'process.json', dict(pid=child.pid, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
            code = child.wait()
        save(out/'exit.json', dict(exit_code=code, wall_seconds=time.monotonic()-begin))
        if code:
            raise RuntimeError('Strict replay failed; original output retained')
        report = json.loads((out/'replay.json').read_text())
    metrics = json.loads((case/'metrics.json').read_text())
    if not report['valid'] or not report['completed'] or report['remaining_handles']:
        raise ValueError('Incomplete strict replay')
    if report['mode']!=('strict-portable-terminal-probe' if args.portable_terminal_probe else 'strict'):
        raise ValueError('Unexpected replay validation mode')
    for actual, expected in [('operations','callbacks'), ('actual_read_bytes','actual_read_bytes'),
                             ('actual_write_bytes','actual_write_bytes')]:
        if report[actual] != metrics[expected]:
            raise ValueError('Replay work differs: '+actual)
    if tree(root) != json.loads((case/'final-tree.json').read_text()):
        raise ValueError('Final tree differs')
    expected_links = json.loads((case/'hardlink-check.json').read_text())['groups']
    if hardlinks(root) != expected_links:
        raise ValueError('Final hardlink topology differs')
    databases = []
    # Never open the replay DB itself: SQLite recovery can modify it.
    for db in root.rglob('opencode.db'):
        if db.is_symlink():
            raise ValueError('Unexpected DB symlink')
        before = digest(db)
        with tempfile.TemporaryDirectory(prefix='ax-db-qualification-', dir=out) as temporary:
            folder = Path(temporary)
            for suffix in ('', '-wal', '-shm'):
                source = db.with_name(db.name+suffix)
                if source.exists():
                    shutil.copyfile(source, folder/source.name)
            with sqlite3.connect(folder/db.name) as connection:
                check = connection.execute('pragma quick_check').fetchall()
                if check != [('ok',)]:
                    raise ValueError('Session DB integrity failed')
                counts = {name:connection.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0]
                          for name in ('session','message','part')}
        if digest(db) != before:
            raise ValueError('DB verifier changed replay data')
        databases.append(dict(path=db.relative_to(root).as_posix(), sha256=before, quick_check='ok', rows=counts))
    save(out/'verification.json', dict(passed=True, strict=not args.portable_terminal_probe,byte_validation=True,
         portable_terminal_probe=args.portable_terminal_probe,probe_errno_differences=report.get('probe_errno_differences',0),
         initial_tree_equal=None if args.verify_only else True, final_tree_equal=True,
         hardlink_topology_equal=True, databases=databases, callbacks=report['operations'],
         actual_read_bytes=report['actual_read_bytes'], actual_write_bytes=report['actual_write_bytes'],
         captured_repeated_read_ratio=metrics['repeated_read_ratio'],
         purpose=purpose,replayed=not args.verify_only,
         scope='Callback counts, file bytes, modes, links and copied SQLite integrity; explicit terminal-probe policy; no Agent resume or performance acceptance claim'))
    print((out/'verification.json').read_text())


if __name__ == '__main__':
    main()
