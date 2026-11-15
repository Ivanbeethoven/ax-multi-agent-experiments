"""Close only private smoke resources; a failed query is never proof of removal."""
import json
from pathlib import Path, PurePosixPath
import re


def mounts_below(roots, text=None):
    text = Path('/proc/self/mountinfo').read_text() if text is None else text
    roots = [PurePosixPath(p) for p in roots]
    found = []
    for line in text.splitlines():
        left, _ = line.split(' - ', 1)
        point = PurePosixPath(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), left.split()[4]))
        if any(point == root or point.is_relative_to(root) for root in roots):
            found.append(str(point))
    return found


def containers(call):
    result = call('docker', 'container', 'ls', '-a', '--no-trunc', '--format', '{{.ID}} {{.Names}}')
    if result.returncode:
        raise RuntimeError('Docker inventory failed; container absence is unknown')
    rows = {}
    for line in result.stdout.splitlines():
        ident, name = line.split()
        if not re.fullmatch(r'[0-9a-f]{64}', ident):
            raise ValueError('Docker returned an incomplete container identity')
        rows[name] = ident
    return rows


def close_private_services(*, call, owner, out, scratch, points, redis, processes=(), clean=str):
    result = dict(mounts_absent=False, rustfs_removed=False, redis_exited=False,
                  filesystem_processes_exited=False, errors={})
    # Every cleanup step is attempted, even when another step fails.
    for index, proc in enumerate(processes):
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception as error:
            result['errors']['filesystem-'+str(index)] = clean(str(error))[-1500:]
    try:
        result['filesystem_processes_exited'] = all(proc.poll() is not None for proc in processes)
    except Exception as error:
        result['errors']['filesystem-status'] = clean(str(error))[-1500:]
    try:
        result['remaining_mounts'] = mounts_below([scratch, *points])
        result['mounts_absent'] = not result['remaining_mounts']
    except Exception as error:
        result['errors']['mounts'] = clean(str(error))[-1500:]
    try:
        ident = containers(call).get(owner)
        if ident is not None:
            actual = json.loads(call('docker', 'container', 'inspect', ident).stdout)
            if len(actual) != 1 or actual[0].get('Id') != ident:
                raise ValueError('Container identity changed')
            labels = actual[0].get('Config', {}).get('Labels') or {}
            if labels.get('ax.experiment.run') != owner or labels.get('ax.experiment.attempt') != str(out.resolve()):
                raise ValueError('Container ownership labels differ; refusing removal')
            try:
                log = call('docker', 'logs', '--tail', '25', ident)
                (out / 'rustfs.log').write_text(clean(log.stdout + log.stderr), encoding='utf-8')
            except Exception as error:
                result['errors']['container-log'] = clean(str(error))[-1500:]
            call('docker', 'rm', '-fv', ident)
        after = containers(call)
        result['rustfs_removed'] = owner not in after and (ident is None or ident not in after.values())
        result['container_id'] = ident
    except Exception as error:
        result['errors']['container'] = clean(str(error))[-1500:]
    try:
        if redis is not None and redis.poll() is None:
            redis.terminate()
            redis.wait(timeout=5)
        result['redis_exited'] = redis is None or redis.poll() is not None
    except Exception as error:
        result['errors']['redis'] = clean(str(error))[-1500:]
    result['passed'] = (not result['errors'] and all(result[key] for key in
        ('mounts_absent', 'rustfs_removed', 'redis_exited', 'filesystem_processes_exited')))
    return result
