"""Start or resume the selected experiment suite through one SSH connection."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['01-io', '02-fork', '03-packed', '04-agent-replay'])
    parser.add_argument('--host', default='idcai-60001')
    parser.add_argument('--reuse-prepared', help='Remote volume.json to verify again before continuing')
    parser.add_argument('--start-at', help='Start at this condition and continue through the remaining plan')
    parser.add_argument('--calibrate-after-io', action='store_true')
    options = parser.parse_args()
    if options.group and options.calibrate_after_io:
        parser.error('Sampling calibration requires the complete suite')
    if (options.reuse_prepared or options.start_at) and options.calibrate_after_io:
        parser.error('Condition selection uses the suite entrypoint directly')
    source = '''
import json,sys,subprocess,shutil
from pathlib import Path
sys.path.insert(0,'/srv/ax-cluster/direct/repo')
sys.path.insert(0,'/srv/ax-cluster/direct/scripts')
from runtime_context import RUN_ROOT,RUN_ID
from bench.cluster.suite import build,export
root=Path('/srv/ax-cluster/direct/repo')
suite=json.loads((root/'configs/experiment-suite.json').read_text())
if suite['approval']!='approved':raise RuntimeError('Experiment configuration is not approved')
if not (RUN_ROOT/'plan.json').exists():export(build(suite),RUN_ROOT)
expected=build(suite)
actual=json.loads((RUN_ROOT/'plan.json').read_text())
if actual!=expected:raise RuntimeError('Saved run plan differs from the current configuration')
(RUN_ROOT/'mounts').mkdir(exist_ok=True)
for name in ['experiment-suite.json','mount-profiles.json','runtime.json','network.json','rustfs-backends.json']:
 shutil.copy2(root/'configs'/name,RUN_ROOT/'config'/name)
shutil.copy2(root/'source-record.json',RUN_ROOT/'versions/repository.json')
from inventory_suite import capture
capture()
unit='ax-'+RUN_ID
active=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True)
if active.stdout.strip()=='active':raise RuntimeError('Experiment service is already active')
command=['systemd-run','--unit='+unit,'--collect','-p','WorkingDirectory='+str(root),
 '-p','MemoryMax=8G','-p','MemorySwapMax=0','-p','LimitNOFILE=1048576',
 '-p','StandardOutput=append:'+str(RUN_ROOT/'logs/suite.log'),
 '-p','StandardError=append:'+str(RUN_ROOT/'logs/suite.log'),'--',
 '/srv/ax-idcai-20261113/venv/bin/python3','/srv/ax-cluster/direct/scripts/ENTRYPOINT']
group=GROUP
if group:command.extend(['--group',group])
reuse_prepared=REUSE_PREPARED
if reuse_prepared:command.extend(['--reuse-prepared',reuse_prepared])
start_at=START_AT
if start_at:command.extend(['--start-at',start_at])
subprocess.run(command,check=True)
print(RUN_ROOT)
'''.replace('group=GROUP', 'group=' + repr(options.group)).replace(
        'reuse_prepared=REUSE_PREPARED', 'reuse_prepared=' + repr(options.reuse_prepared)).replace(
        'start_at=START_AT', 'start_at=' + repr(options.start_at)).replace(
        'ENTRYPOINT', 'continue_suite.py' if options.calibrate_after_io else 'run_suite.py')
    subprocess.run(['ssh', options.host, '/srv/ax-idcai-20261113/venv/bin/python3 -'], input=source.encode(), check=True)


if __name__ == '__main__':
    main()
