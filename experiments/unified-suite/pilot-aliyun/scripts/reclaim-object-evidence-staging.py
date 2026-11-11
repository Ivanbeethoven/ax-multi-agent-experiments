#!/usr/bin/env python3
"""Verify and, on explicit request, reclaim only already-archived object evidence copies."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile

from bench.archive_check import MANIFEST, safe
from bench.runtime.common import digest, load, pinned, write_new
from bench.runtime.pilot_pools import active_conflicts, resource_guard

ROOT = Path('/srv/ax-objects-0')
STAGE = re.compile(r'(?:evidence/[a-z0-9-]+|retained-calibrations/[a-z0-9-]+/cal-[a-z0-9-]+/raw)')


def now():
    return datetime.now(timezone.utc).isoformat()


def node(s):
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if boot != s['boot_id'] or ROOT.stat().st_dev != s['device']:
        raise ValueError('Wrong original object boot or data device')
    def free():
        v = os.statvfs(ROOT)
        return v.f_bavail * v.f_frsize
    initial = free()
    groups = []
    paths = set()
    for entry in s['copies']:
        name = entry['stage']
        if not STAGE.fullmatch(name) or name in paths:
            raise ValueError('Unique explicit temporary archive staging path required')
        paths.add(name)
        path = ROOT/name
        if path.resolve(strict=True) != path or path.stat().st_dev != s['device'] or not path.is_dir():
            raise ValueError('Staging path moved or crosses a filesystem')
        archive = pinned(entry['worker_archive'])
        if (not archive.is_relative_to(ROOT) or archive.resolve(strict=True) != archive or
                archive.is_relative_to(path) or archive.suffixes[-2:] != ['.tar', '.gz']):
            raise ValueError('Original archive must survive outside this staging directory')
        with tarfile.open(archive, 'r:gz') as stream:
            item = stream.next()
            if item.name != MANIFEST or not item.isfile() or item.size > 16*1024**2:
                raise ValueError('Bounded first-member manifest required')
            manifest = json.load(stream.extractfile(item))
        kind = 'calibration-'+path.parent.name+'-objects' if name.startswith('retained-calibrations/') else 'objects'
        if (manifest.get('schema') != 'suite-archive-manifest-v1' or manifest.get('deferred') != [] or
                manifest.get('kind') != kind or not manifest.get('files')):
            raise ValueError('Exact complete objects archive required')
        seen = {}
        allocated = path.stat().st_blocks * 512
        for parent, dirs, files in os.walk(path, followlinks=False):
            for child in dirs + files:
                p = Path(parent)/child
                info = p.lstat()
                if info.st_dev != s['device'] or (info.st_nlink > 1 and not stat.S_ISDIR(info.st_mode)):
                    raise ValueError('Unexpected device or non-exclusive staged file: '+str(p))
                allocated += info.st_blocks * 512
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Unexpected link or special file: '+str(p))
                rel = safe(p.relative_to(path).as_posix())
                with p.open('rb') as reader:
                    seen[rel] = dict(bytes=info.st_size, sha256=hashlib.file_digest(reader, 'sha256').hexdigest())
        if seen != manifest['files']:
            raise ValueError('Staged files do not match archive membership, sizes and SHA256: '+name)
        groups.append(dict(stage=name, files=len(seen), allocated_bytes=allocated, archive=entry['worker_archive']))
    allocated = sum(g['allocated_bytes'] for g in groups)
    if initial + allocated < s['minimum_after_bytes']:
        raise ValueError('Reviewed copies do not supply the required free-space margin')
    if s['apply'] is True:
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('Refusing recursive cleanup without fd-based safety')
        for entry in groups:
            shutil.rmtree(ROOT/entry['stage'])
    final = free()
    return dict(passed=not s['apply'] or final>=s['minimum_after_bytes'], at=now(), applied=s['apply'],
                boot_id=boot, initial_free_bytes=initial, final_free_bytes=final,
                proven_staging_bytes=allocated, groups=groups)


def coordinator(a):
    request = dict(path=str(a.request), sha256=a.sha256)
    s = load(pinned(request))
    if (s.get('schema') != 'c4-closed-object-staging-reclaim-v1' or s.get('reviewed') is not True or
            type(s.get('apply')) is not bool or s['apply'] != a.apply or not s.get('copies')):
        raise ValueError('Reviewed request and explicit matching apply mode required')
    canonical = Path(s['canonical'])
    plan = load(pinned(s['plan']))
    cell = next(x for x in plan['cells'] if x['id'] == s['cell_id'])
    if cell['pool'] != 'pool-a' or cell['suite'] != 'C4' or cell['case'] != 'fork-1':
        raise ValueError('Expected the existing A C4 resource lock')
    for item in s['copies']:
        archive = item['worker_archive']
        durable = load(pinned(item['durable_member_receipt']))
        if (durable.get('passed') is not True or durable.get('worker_archive') != archive or
                durable.get('durable_member_sha256') != archive['sha256'] or
                not durable.get('independent_readback')):
            raise ValueError('Independent durable member verification required')
    out = a.out
    out.mkdir(parents=True, exist_ok=False)
    write_new(out/'intent.json', dict(at=now(), request=request, automatic_retry=False))
    with resource_guard(canonical, cell, plan):
        for control in {canonical, Path(s['plan']['path']).parent.parent, *map(Path,s['other_lease_controls'])}:
            if active_conflicts(control, cell):
                raise ValueError('An active cell owns this pool or its resources')
        ssh = pinned(s['ssh_config'])
        script = pinned(s['worker_tool'])
        if (not re.fullmatch(r'/srv/ax-objects-0/preparation/[a-z0-9-]+\.py',s['node_tool']) or
                not re.fullmatch(r'/srv/ax-objects-0/code-[a-z0-9-]+',s['node_repo'])):
            raise ValueError('Explicit object preparation tool and frozen code path required')
        subprocess.run(['scp','-F',str(ssh),str(script),'pool-a-objects:'+s['node_tool']],check=True)
        target = ['ssh','-F',str(ssh),'pool-a-objects']
        actual = subprocess.check_output(target+[shlex.join(['sha256sum',s['node_tool']])],text=True).split()[0]
        if actual != s['worker_tool']['sha256']:
            raise ValueError('Uploaded worker verifier differs')
        call = target+[shlex.join(['env','PYTHONDONTWRITEBYTECODE=1','PYTHONPATH='+s['node_repo'],
                                  'python3',s['node_tool'],'--node'])]
        with (out/'stdout').open('xb') as stdout, (out/'stderr').open('xb') as stderr:
            result = subprocess.run(call,input=json.dumps(s).encode(),stdout=stdout,stderr=stderr)
        result.check_returncode()
        payload = load(out/'stdout')
        write_new(out/'result.json',dict(payload, request=request, automatic_retry=False))
        if payload['passed'] is not True:
            raise ValueError('Actual free space remains below the reviewed margin')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node',action='store_true')
    p.add_argument('--request',type=Path)
    p.add_argument('--sha256')
    p.add_argument('--out',type=Path)
    p.add_argument('--apply',action='store_true')
    a = p.parse_args()
    if a.node:
        print(json.dumps(node(json.load(sys.stdin))))
    elif a.request and a.sha256 and a.out:
        coordinator(a)
    else:
        p.error('provide --request, --sha256 and --out')
