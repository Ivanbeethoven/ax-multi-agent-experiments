"""Run C2 through C4 after the active C1 service has finished."""
import json
from pathlib import Path
import subprocess
import time

from start_direct_c1 import stage, ROOT, SCRIPTS, PYTHON


def main():
    unit='ax-direct-c1-after-layout.service'
    while subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0:
        time.sleep(15)
    code=subprocess.check_output(['systemctl','show',unit,'--property=ExecMainStatus','--value'],text=True).strip()
    if code!='0':raise RuntimeError('C1 execution requires attention before the next group')
    status=json.loads((ROOT/'status.json').read_text())
    c1=status['groups']['C1']
    if c1['completed_system_runs']!=c1['total_system_runs']:
        raise RuntimeError('C1 has unfinished conditions')
    stage('C2-execution',[PYTHON,str(SCRIPTS/'run_c2.py')])
    stage('C3-execution',[PYTHON,str(SCRIPTS/'run_c3.py')])
    stage('C4-functional',[PYTHON,str(SCRIPTS/'run_c4.py'),'--functional'])
    stage('C4-execution',[PYTHON,str(SCRIPTS/'run_c4.py')])
    status=json.loads((ROOT/'status.json').read_text())
    remaining={group:{'completed':record['completed_system_runs'],'total':record['total_system_runs']}
               for group,record in status['groups'].items()
               if record['completed_system_runs']!=record['total_system_runs']}
    (ROOT/'remaining.json').write_text(json.dumps(remaining,indent=2)+'\n')
    print(json.dumps({'finished_groups':list(status['groups']),'remaining':remaining}),flush=True)


if __name__=='__main__':main()
