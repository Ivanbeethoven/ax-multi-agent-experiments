"""Execute the C4 fork, range update, and three-way merge conditions."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

import cluster_lib as cl
import c4_support as support
from run_c2 import batch_start_mounts, batch_stop_mounts, VERIFY_HOST_MAP

sys.path.insert(0,str(cl.REPO_ROOT))
from bench.cluster.c4_workload import make_changes

POSIX_TOOL=Path('/srv/ax-cluster/sources/brewfs-5750af24/tools/experiments/workspace_merge/merge_posix.py')
UNAVAILABLE={'host-shared-read-cache','native-merge-strategy-switches'}


class Run:
    def __init__(self,condition,system,repeat,out):
        self.condition=condition;self.system=system;self.repeat=repeat;self.out=Path(out)
        self.out.mkdir(parents=True,exist_ok=True)
        self.tag=f"{condition['id'].lower()}-{support.TAGS[system]}-r{repeat}-a{self.out.name}"
        self.params={**condition['parameters'],**condition.get('system_parameters',{}).get(system,{})}
        self.parent=None;self.active=[];self.stages={}

    def stage(self,name,operation):
        before=cl.collector_mark_all(self.tag,name+'-start')
        utc=time.time_ns();start=time.monotonic()
        try:result=operation()
        except Exception:
            support.save(self.out/(name+'-before.json'),before)
            raise
        elapsed=time.monotonic()-start;end=time.time_ns()
        after=cl.collector_mark_all(self.tag,name+'-end')
        record={'name':name,'seconds':elapsed,'started_utc_ns':utc,'finished_utc_ns':end,
                'resources':cl.summarize_phase_delta(before,after)}
        self.stages[name]=record
        support.save(self.out/(name+'-samples.json'),{'before':before,'after':after})
        support.save(self.out/(name+'-result.json'),result)
        with (self.out/'stages.jsonl').open('a') as stream:stream.write(json.dumps(record)+'\n')
        return result

    def register(self):
        by_host=defaultdict(list)
        for mount in self.active:by_host[mount['host_name']].append(mount)
        cl.collector_set_mounts(dict(by_host))

    def mount(self,workspaces,places,label,phase='measure'):
        requests=[];specs={}
        for index,(workspace,(host,slot)) in enumerate(zip(workspaces,places)):
            identifier=f'{self.tag}-{label}-{index:03d}'
            requests.append(support.request(self.parent,workspace,identifier,host,slot,phase,
                                             self.params.get('row_cache_mib',64)))
            specs[identifier]=support.spec(self.parent,workspace,cl.endpoint_for_slot(slot))
        config=self.out/(label+'-mounts.json');cl.generate_mounts_json(requests,config)
        mounts=batch_start_mounts(config,specs,self.tag+'-'+label,register_collector=False)
        by_id={mount['id']:mount for mount in mounts}
        mounts=[by_id[request['id']] for request in requests]
        self.active.extend(mounts);self.register()
        support.save(self.out/(label+'-started.json'),mounts)
        return mounts

    def stop(self,mounts,label):
        if not mounts:return []
        records=batch_stop_mounts(mounts,self.tag+'-'+label,clear_collector=False,remove_cache=True)
        support.save(self.out/(label+'-stopped.json'),records)
        if len(records)!=len(mounts) or any(not record.get('unmounted_cleanly') for record in records):
            raise RuntimeError('One or more C4 mounts did not stop cleanly: '+label)
        ids={mount['id'] for mount in mounts}
        self.active=[mount for mount in self.active if mount['id'] not in ids];self.register()
        return records

    def batch(self,mounts,action,label,changes=None,full_file=False):
        grouped=defaultdict(list)
        for index,mount in enumerate(mounts):
            grouped[mount['host_name']].append({**mount,'action':action,
                'files':self.parent['files'],'file_bytes':self.parent['file_bytes'],
                'changes':changes[index] if changes is not None else [],'full_file':full_file,
                'output':str(cl.RUN_ROOT/'hosts'/mount['host_name']/'c4'/self.tag/label/mount['id'])})
        def host_run(item):
            host,tasks=item;path=self.out/f'{label}-{host}-tasks.json';support.save(path,tasks)
            remote=str(cl.RUN_ROOT/'hosts'/host/'batch'/f'{self.tag}-{label}.json')
            cl.sync_file_to_host(host,path,remote)
            result=support.command(['python3',str(cl.SCRIPTS_DIR/'host_batch_runner.py'),
                                    'c4-batch','--tasks',remote],self.out/f'{label}-{host}',host)
            document=json.loads(result.stdout)
            if not document['ok']:raise RuntimeError('C4 batch failed on '+host)
            return document['results']
        with ThreadPoolExecutor(max_workers=len(grouped)) as pool:results=list(pool.map(host_run,grouped.items()))
        return [result for group in results for result in group]

    def cold_verify(self,workspaces,changes,label,places=None):
        places=places or support.placements(len(workspaces))
        mounts=self.stage(label+'-mount',lambda:self.mount(workspaces,places,label,phase='verify'))
        try:
            first=self.stage(label+'-first-read',lambda:self.batch(mounts,'first-read',label+'-first',changes))
            full=self.stage(label+'-full-read',lambda:self.batch(mounts,'verify',label+'-full',changes))
        finally:self.stage(label+'-unmount',lambda:self.stop(mounts,label))
        return {'first_read':first,'full':full}

    def native_merge(self,child,target,index,expect_conflict=False,output_name=None):
        private=Path('/srv/ax-cluster/private/operations/direct/run-001')/self.tag
        private.mkdir(parents=True,exist_ok=True)
        operation=private/f'child-{index:03d}.json'
        result=support.command(['python3','-m','bench.cluster.workspace_merge',
            '--binary',str(cl.BIN_DIR/'brewfs'),'--meta-url',support.REDIS_URL,
            '--namespace',self.parent['namespace'],'--child',child,'--target',target,
            '--operation-file',str(operation)],self.out/(output_name or f'merge-{index:03d}'),
            secrets=True,accepted=(2,) if expect_conflict else (0,))
        document=json.loads(result.stdout)
        if expect_conflict:
            if document.get('committed') or not document.get('conflicts'):raise RuntimeError('C4 conflict was not reported')
        elif not document.get('committed'):raise RuntimeError('C4 merge did not commit')
        return document

    def native_correctness(self,result):
        retry=self.stage('retry',lambda:self.native_merge(result['children'][0],result['target'],0,output_name='retry'))
        if retry!=result['merge'][0]:raise RuntimeError('Merge retry returned a different result')
        pair,_=support.fork(self.parent,2,self.tag+'-conflict',self.out/'conflict')
        mounts=self.mount(pair,[('A366',3),('A355',1)],'conflict')
        changes=[make_changes(1,kind='range-overwrite',seed=100),make_changes(1,kind='range-overwrite',seed=200)]
        try:
            self.batch(mounts,'apply','conflict-write',changes)
            cl.wait_uploads(mounts)
        finally:self.stop(mounts,'conflict-written')
        conflict=self.stage('conflict',lambda:self.native_merge(pair[0],pair[1],1000,expect_conflict=True,output_name='conflict'))
        unchanged=self.cold_verify([pair[1]],[changes[1]],'conflict-target',places=[('A366',3)])
        return {'retry':retry,'conflict':conflict,'target_unchanged':unchanged}

    def fork_test(self):
        count=self.params['children'];places=support.placements(count,self.params['placement']=='same-host')
        children,fork_seconds=self.stage('fork',lambda:support.fork(self.parent,count,self.tag,self.out/'fork',self.params.get('fork_metadata','shared')))
        mounts=self.stage('mount',lambda:self.mount(children,places,'children'))
        is_update=self.condition['family'] in ('partial-update','full-file-update')
        changes=[make_changes(self.parent['files'],child=i,kind='range-overwrite',write_bytes=self.params.get('write_kib',4)*1024) for i in range(count)] if is_update else [[] for _ in children]
        try:
            if is_update:
                application=self.stage('write',lambda:self.batch(mounts,'apply','write',changes,self.params.get('extent_update')=='full-file'))
                upload=self.stage('upload',lambda:cl.wait_uploads(mounts))
            else:
                application=self.stage('cold-read',lambda:self.batch(mounts,'read','cold-read'))
                warm=self.stage('warm-read',lambda:self.batch(mounts,'read','warm-read'))
                upload=[]
        finally:self.stage('unmount',lambda:self.stop(mounts,'children'))
        rotated=[(VERIFY_HOST_MAP[host],slot) for host,slot in places]
        verification=self.cold_verify(children,changes,'verify',places=rotated)
        sibling,_=support.fork(self.parent,1,self.tag+'-sibling',self.out/'sibling')
        original=self.cold_verify(sibling,[[]],'original',places=[('A355',1)])
        return {'workspaces':children,'fork_command_seconds':fork_seconds,'application':application,
                'warm_read':warm if not is_update else None,'upload':upload,
                'verification':verification,'original_verification':original}

    def merge_test(self):
        count=self.params['children'];changes_count=self.params.get('changes',100)
        children,_=self.stage('fork-children',lambda:support.fork(self.parent,count,self.tag,self.out/'fork'))
        targets,_=support.fork(self.parent,1,self.tag+'-target',self.out/'target');target=targets[0]
        kind=self.params.get('change_mix','mixed-v1')
        if kind=='metadata-only-v1':kind='metadata-only'
        changes=[make_changes(changes_count,child=index,start_index=index*changes_count,
                    kind=kind,write_bytes=self.params.get('write_kib',4)*1024) for index in range(count)]
        target_changes=make_changes(self.params.get('target_changes',0),child=63,start_index=self.parent['files']//2,seed=1000)
        support.save(self.out/'expected-changes.json',{'children':changes,'target':target_changes})
        child_mounts=self.stage('mount-children',lambda:self.mount(children,support.placements(count),'children'))
        target_mounts=self.stage('mount-target',lambda:self.mount([target],[('A366',2)],'target'))
        native=self.system=='brewfs-native-overlay' and self.params.get('merge_adapter')!='posix-three-way'
        if not native:
            base,_=support.fork(self.parent,1,self.tag+'-base',self.out/'base-copy')
            base_mounts=self.stage('mount-base',lambda:self.mount(base,[('A366',1)],'base'))
        self.stage('child-task',lambda:self.batch(child_mounts,'apply','child-task',changes))
        self.stage('target-task',lambda:self.batch(target_mounts,'apply','target-task',[target_changes]))
        self.stage('upload',lambda:cl.wait_uploads(child_mounts+target_mounts))
        results=[];pause_names=['upload']
        if native:
            self.stage('offline',lambda:self.stop(child_mounts+target_mounts,'before-merge'));pause_names.append('offline')
            for index,child in enumerate(children):
                name=f'merge-{index:03d}';results.append(self.stage(name,lambda child=child,index=index:self.native_merge(child,target,index)))
                pause_names.append(name)
        else:
            for index,child in enumerate(children):
                original=child_mounts[index]
                if original['host_name']!='A366':
                    name=f'move-child-{index:03d}'
                    def move(original=original,child=child,index=index):
                        self.stop([original],f'child-source-{index:03d}')
                        return self.mount([child],[('A366',40)],f'local-child-{index:03d}')[0]
                    local=self.stage(name,move);pause_names.append(name)
                else:local=original
                name=f'merge-{index:03d}'
                def merge(local=local,index=index):
                    reply=support.command(['python3',str(POSIX_TOOL),base_mounts[0]['mount_directory'],
                                           local['mount_directory'],target_mounts[0]['mount_directory']],self.out/f'merge-{index:03d}')
                    return json.loads(reply.stdout)
                results.append(self.stage(name,merge));pause_names.append(name)
                self.stage(f'child-stop-{index:03d}',lambda local=local,index=index:self.stop([local],f'child-done-{index:03d}'))
                pause_names.append(f'child-stop-{index:03d}')
            self.stage('merge-upload',lambda:cl.wait_uploads(target_mounts));pause_names.append('merge-upload')
            self.stage('offline',lambda:self.stop(list(self.active),'after-merge'));pause_names.append('offline')
        expected=target_changes+[item for entries in changes for item in entries]
        verification=self.cold_verify([target],[expected],'verify',places=[('A355',1)])
        pause_names.extend(['verify-mount','verify-first-read'])
        return {'children':children,'target':target,'merge':results,'verification':verification,
                'business_pause_seconds':sum(self.stages[name]['seconds'] for name in pause_names),
                'business_pause_components':pause_names,
                'merge_command_seconds':sum(self.stages[f'merge-{index:03d}']['seconds'] for index in range(count))}

    def execute(self):
        support.save(self.out/'condition.json',{'condition':self.condition,'system':self.system,'repeat':self.repeat})
        self.parent=support.prepare_parent(self.system,self.params['files'],self.params['file_bytes'])
        support.save(self.out/'parent.json',self.parent)
        try:
            result=self.merge_test() if self.condition['family'].startswith('merge-') else self.fork_test()
            result.update({'state':'passed','condition':self.condition['id'],'system':self.system,
                           'repeat':self.repeat,'stages':self.stages,'parent_prepare_seconds':self.parent['prepare_seconds']})
            return result
        finally:
            if self.active:self.stop(list(self.active),'final')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--condition',action='append')
    parser.add_argument('--system',action='append')
    parser.add_argument('--functional',action='store_true')
    opts=parser.parse_args()
    plan=json.loads((cl.RUN_ROOT/'plan/C4.json').read_text())
    if opts.functional:
        condition=next(item for item in plan['conditions'] if item['id']=='C4-merge-size-01')
        condition={**condition,'id':'C4-functional','parameters':{**condition['parameters'],'files':1000,'changes':10}}
        conditions=[condition];repeats=[1]
    else:conditions=plan['conditions'];repeats=[1,2,3]
    with cl.MeasurementLock():
        for condition in conditions:
            if opts.condition and condition['id'] not in opts.condition:continue
            for repeat in repeats:
                systems=condition['systems'];offset=(repeat-1)%len(systems)
                for system in systems[offset:]+systems[:offset]:
                    if opts.system and system not in opts.system:continue
                    parameters={**condition['parameters'],**condition.get('system_parameters',{}).get(system,{})}
                    if set(parameters.get('requires',[])) & UNAVAILABLE:
                        print(json.dumps({'condition':condition['id'],'system':system,'state':'implementation-required'}),flush=True)
                        continue
                    base=cl.RUN_ROOT/('functional/C4' if opts.functional else 'C4')/condition['id']/system/f'r{repeat}'
                    if (base/'result.json').is_file() and json.loads((base/'result.json').read_text()).get('state')=='passed':continue
                    base.mkdir(parents=True,exist_ok=True)
                    attempt=1
                    while (base/f'attempt-{attempt:02d}').exists():attempt+=1
                    out=base/f'attempt-{attempt:02d}'
                    print(json.dumps({'condition':condition['id'],'system':system,'repeat':repeat,'state':'running'}),flush=True)
                    try:
                        run=Run(condition,system,repeat,out)
                        result=run.execute()
                        if opts.functional and system=='brewfs-native-overlay':
                            result['correctness']=run.native_correctness(result)
                        support.save(out/'result.json',result);support.save(base/'result.json',result)
                        if not opts.functional:cl.update_status('C4',condition['id'],system,repeat,'passed',{'result_path':str((base/'result.json').relative_to(cl.RUN_ROOT))})
                    except Exception as error:
                        support.save(out/'failure.json',{'type':type(error).__name__,'message':str(error),'time':cl.utc_iso()})
                        if not opts.functional:cl.update_status('C4',condition['id'],system,repeat,'failed',{'directory':str(out)})
                        raise
                    print(json.dumps({'condition':condition['id'],'system':system,'repeat':repeat,'state':'passed'}),flush=True)


if __name__=='__main__':main()
