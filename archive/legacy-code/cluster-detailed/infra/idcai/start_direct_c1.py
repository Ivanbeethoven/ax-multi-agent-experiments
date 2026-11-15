"""Install the release, check the filesystems, and execute the C1 plan."""
import json
from pathlib import Path
import subprocess
import time

ROOT=Path('/srv/ax-cluster/runs/direct/run-001')
SCRIPTS=Path('/srv/ax-cluster/direct/scripts')
PYTHON='/srv/ax-idcai-20261113/venv/bin/python3'
RELEASE=Path('/srv/ax-cluster/releases/5750af24')


def stage(name,command):
    started=time.time_ns();mono=time.monotonic()
    print(json.dumps({'stage':name,'state':'running'}),flush=True)
    with (ROOT/(name+'.log')).open('a') as log:
        result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
    record={'stage':name,'started_utc_ns':started,'finished_utc_ns':time.time_ns(),
            'seconds':time.monotonic()-mono,'returncode':result.returncode}
    with (ROOT/'schedule.jsonl').open('a') as log:log.write(json.dumps(record)+'\n')
    print(json.dumps(record),flush=True)
    if result.returncode:raise RuntimeError(name+' exited with '+str(result.returncode))


def checks_ready():
    paths = [ROOT/'hosts/collector-overhead-8mounts.json', ROOT/'mounts/smoke-verification.json',
             ROOT/'functional/c1-reader/validation.json']
    if not all(path.is_file() for path in paths):
        return False
    calibration, trace, reader = [json.loads(path.read_text()) for path in paths]
    engines = ('brewfs-native-overlay', 'juicefs', 'juicefs-overlay')
    singles = trace.get('single_task_trace_smoke', {})
    files = json.loads((ROOT/'mounts/file-checks.json').read_text())
    return (calibration.get('passed') is True and len(calibration.get('pairs', [])) == 3
            and reader.get('changed_content_rejected') is True
            and all(singles.get(engine, {}).get('verify', {}).get('passed') is True for engine in engines)
            and all(files.get(engine, {}).get('passed') is True for engine in ('juicefs', 'juicefs-overlay'))
            and len(trace.get('collector_flush', {})) == 6
            and all(item.get('ok') is True for item in trace['collector_flush'].values()))


def main():
    campaign=json.loads(Path('/srv/ax-cluster/direct/repo/configs/campaign-direct.json').read_text())
    if campaign.get('review_state') == 'awaiting-confirmation':
        print('Experiment plan is awaiting user confirmation.', flush=True)
        return
    deadline=time.monotonic()+3600
    while not (RELEASE/'admin-build.json').is_file():
        state=subprocess.check_output(['systemctl','show','ax-direct-build-admin-5750af24-reuse.service','--property=ActiveState','--value'],text=True).strip()
        if state=='failed' or time.monotonic()>deadline:raise RuntimeError('Workspace tool build did not complete')
        time.sleep(5)
    while subprocess.run(['systemctl','is-active','--quiet','ax-direct-network-focus.service']).returncode==0:time.sleep(5)
    version_file=ROOT/'versions.json'
    version=json.loads(version_file.read_text()) if version_file.exists() else {}
    release=json.loads((RELEASE/'build.json').read_text())
    if version.get('brewfs_build',{}).get('source_commit') != release['source_commit']:
        stage('install-release',[PYTHON,str(SCRIPTS/'install_direct_release.py'),'--release',str(RELEASE)])
    elif checks_ready():
        print('Using completed filesystem, trace, and collector checks for this release', flush=True)
        stage('C1-execution',[PYTHON,str(SCRIPTS/'run_c1.py'),'--stage','all'])
        return
    stage('C1-reader-check',[PYTHON,str(SCRIPTS/'refresh_c1_helper.py')])
    stage('filesystem-checks-juicefs',[PYTHON,str(SCRIPTS/'smoke_and_calibrate.py'),'--stage','files','--engines','juicefs','juicefs-overlay'])
    stage('trace-and-collector-checks',[PYTHON,str(SCRIPTS/'smoke_and_calibrate.py'),'--stage','trace'])
    calibration=json.loads((ROOT/'hosts/collector-overhead-8mounts.json').read_text())
    if not calibration['passed']:raise RuntimeError('Collector overhead exceeds the planned threshold')
    stage('C1-execution',[PYTHON,str(SCRIPTS/'run_c1.py'),'--stage','all'])


if __name__=='__main__':main()
