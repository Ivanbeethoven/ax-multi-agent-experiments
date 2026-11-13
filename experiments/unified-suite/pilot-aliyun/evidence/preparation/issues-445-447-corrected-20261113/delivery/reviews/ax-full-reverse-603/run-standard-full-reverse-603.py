#!/usr/bin/env python3
"""Run the complete six-scene standard guard once per corrected arm."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path('/home/dev/ax-perf-issues-corrected-validation/compose')
checkpoint = json.loads((ROOT/'correctness-checkpoint-02.json').read_text())
assert checkpoint['passed'] is True and checkpoint['stage'] == 'corrected-readwrite-correctness'
assert len(checkpoint['accepted_cases']) == 4 and set(checkpoint['trace_proofs']) == {'baseline','candidate'}
OUT = ROOT/'matrices/regression-corrected512-reverse603'
OUT.mkdir(parents=True,exist_ok=False)
record = {'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'passed':False,'phases':[],
          'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'case_script_sha256':hashlib.sha256((ROOT/'run-compose-case.py').read_bytes()).hexdigest(),
          'scope':'Six complete scenes per arm; numeric regression assessment follows separately'}


def save():
    (OUT/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')


try:
    with Path('/home/dev/.ax-rbm-heavy.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        env = dict(os.environ,AX447_INHERITED_HEAVY_LOCK_FD=str(lock.fileno()))
        for arm in ('candidate','baseline'):
            identity = f'ax447-corrected-{arm}-regression-603'
            command = [sys.executable,str(ROOT/'run-compose-case.py'),arm,'regression','603','--sync','range']
            step = {'arm':arm,'case':'regression','trial':603,'command':command,
                    'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    'receipt':str(ROOT/'trials'/identity/'receipt.json')}
            space = os.statvfs(ROOT)
            reserve = 256 * 1024**3
            free = space.f_bavail * space.f_frsize
            minimum = max(32 * 1024**3, space.f_blocks * space.f_frsize // 5)
            step['capacity_preflight'] = {
                'free_bytes':free,'planning_peak_reserve_bytes':reserve,
                'remaining_after_reserve_bytes':free-reserve,'minimum_remaining_bytes':minimum,
                'free_inodes':space.f_favail,'total_inodes':space.f_files,
                'planning_reserved_inodes':100000,
                'scope':'Empirical safeguard, not a proven upper bound: reserve 256GiB before each arm, enforce 20% bytes/inodes at every scene, sample host capacity every 2s'}
            record['phases'].append(step)
            save()
            assert free-reserve >= minimum, 'Insufficient capacity before case; workload not started'
            assert space.f_favail-100000 >= space.f_files//5, 'Insufficient inodes before case'
            with (OUT/(identity+'.log')).open('wb') as log:
                process = subprocess.Popen(command,env=env,pass_fds=(lock.fileno(),),
                                           stdout=log,stderr=subprocess.STDOUT)
                capacity_passed = True
                with (OUT/(identity+'-capacity.jsonl')).open('w') as samples:
                    while True:
                        current = os.statvfs(ROOT)
                        sample = {'unix_ns':time.time_ns(),'free_bytes':current.f_bavail*current.f_frsize,
                                  'total_bytes':current.f_blocks*current.f_frsize,
                                  'free_inodes':current.f_favail,'total_inodes':current.f_files}
                        sample['passed'] = (sample['free_bytes'] >= sample['total_bytes']//5
                                            and current.f_favail >= current.f_files//5)
                        samples.write(json.dumps(sample)+'\n'); samples.flush()
                        if not sample['passed'] and capacity_passed:
                            capacity_passed = False
                            # Stop only this project's active workload if the host floor is crossed.
                            ids = subprocess.check_output(['docker','ps','-q','--filter',
                                'label=com.docker.compose.project='+identity],text=True).split()
                            for container_id in ids:
                                service = subprocess.check_output(['docker','inspect','--format',
                                    '{{index .Config.Labels "com.docker.compose.service"}}',container_id],text=True).strip()
                                if service == 'perf':
                                    subprocess.run(['docker','stop','--time','30',container_id],check=True,
                                                   stdout=log,stderr=subprocess.STDOUT)
                        try:
                            status = process.wait(timeout=2)
                            break
                        except subprocess.TimeoutExpired:
                            pass
            step.update(exit_code=status,capacity_samples_passed=capacity_passed,
                        finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            save()
            assert capacity_passed, identity+' crossed capacity floor; sample invalid'
            assert status == 0, identity+' failed; preserve raw artifacts'
            receipt = json.loads(Path(step['receipt']).read_text())
            assert receipt['qualified'] is True and receipt['status'] == 0
            assert len(receipt['artifact_directories']) == 1
            assert not (ROOT/'trials'/identity/'remaining-owned-containers.json').read_text().strip()
            artifact = Path(receipt['artifact_directories'][0])
            capacity_rows = [json.loads(line) for line in (artifact/'capacity-scenes.jsonl').read_text().splitlines()]
            expected_scenes = {f'fio-{mode}-direct{direct}' for mode in ('seqwrite','randwrite','randrw') for direct in (0,1)}
            assert len(capacity_rows) == 12 and all(row['passed'] for row in capacity_rows)
            assert {(row['tool'],row['phase']) for row in capacity_rows} == {(scene,phase) for scene in expected_scenes for phase in ('before_scene','after_scene')}
            assert not (artifact/'capacity.invalid').exists()
            with (OUT/(arm+'-phases.log')).open('w') as log:
                subprocess.run([sys.executable,str(ROOT/'summarize-compose-phases.py'),str(artifact)],
                               check=True,stdout=log)
            subprocess.run([sys.executable,str(ROOT/'summarize-client-cpu.py'),str(artifact),
                            '--output',str(artifact/'client-cpu-summary.json')],check=True)
            assert len(json.loads((artifact/'precise-phase-summary.json').read_text())['tools']) == 6
            step['receipt_sha256'] = hashlib.sha256(Path(step['receipt']).read_bytes()).hexdigest()
            save()
    record['passed'] = True
except Exception:
    record['error'] = traceback.format_exc()
    raise
finally:
    record['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save()
print(json.dumps({'standard_guard_execution_passed':record['passed']}),flush=True)
