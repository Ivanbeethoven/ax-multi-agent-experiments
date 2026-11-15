"""Read compact C1 through C4 progress for the local scheduled monitor."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess

ROOT=Path('/srv/ax-cluster/runs/direct/run-001')
UNITS=['ax-direct-c1-after-layout.service','ax-direct-campaign.service']


def summarize(status,services):
    groups={};running=[];failed=[]
    for group,record in status['groups'].items():
        groups[group]={key:record.get(key,0) for key in
                       ('completed_conditions','total_conditions','completed_system_runs','total_system_runs','pending_feature_conditions')}
        for condition,entry in record['conditions'].items():
            for run,item in entry.get('runs',{}).items():
                if item['state'] in ('running','failed'):
                    (running if item['state']=='running' else failed).append(
                        {'group':group,'condition':condition,'run':run,'updated_at':item.get('updated_at')})
    complete=all(group['completed_system_runs']==group['total_system_runs']
                 and group['completed_conditions']==group['total_conditions']
                 and group['pending_feature_conditions']==0 for group in groups.values())
    active=any(service['ActiveState'] in ('active','activating') for service in services.values())
    attention=any(service['ActiveState']=='failed' for service in services.values())
    state='complete' if complete else 'needs-attention' if attention else 'running' if active else 'incomplete'
    if status.get('plan_review') == 'awaiting-confirmation':
        state='awaiting-confirmation'
    return {'state':state,'groups':groups,'running':running,'failed_runs':failed,'services':services}


def snapshot(root=ROOT):
    status=json.loads((root/'status.json').read_text())
    services={}
    for unit in UNITS:
        output=subprocess.check_output(['systemctl','show',unit,
            '--property=ActiveState,SubState,ExecMainStatus'],text=True)
        services[unit]=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    result=summarize(status,services)
    result['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    temporary=root/'progress.json.tmp';temporary.write_text(json.dumps(result,indent=2)+'\n')
    temporary.replace(root/'progress.json')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['once'])
    parser.parse_args()
    print(json.dumps(snapshot()))


if __name__=='__main__':main()
