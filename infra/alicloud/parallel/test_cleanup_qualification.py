"""Offline fail-closed cleanup qualification; never calls cloud APIs."""
import io
import json
import re
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import cleanup_parallel as cleanup
import parallel_controller as controller

ARMS=['native-kernel-default','overlay-kernel-default','packed-kernel-default','juicefs-kernel-default']

class CleanupQualification(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.plan=self.root/'plan.json'
        self.plan.write_text(json.dumps({'assignments':[{'worker_id':n,'arms':ARMS} for n in ('node-a','node-b','node-c')]}))
        self.patches=[patch.object(cleanup,'ROOT',self.root),patch.object(cleanup,'PLAN',self.plan),patch.object(cleanup,'ecs')]
        for p in self.patches:p.start()
        self.api=self.patches[-1].target.ecs
        self.state={'batch_id':'batch','plan_sha256':controller.sha(self.plan),'workers':{}}
        for n in ('node-a','node-b','node-c'):
            self.state['workers'][n]={'instance_id':'i-'+n,'source_node':n=='node-a','disks':['d-'+n+'-sys','d-'+n+'-data']}
            self.write(n)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def write(self,name,methods=None,reason='completed',duplicate=False,attempt=None):
        terminal={'run_id':'batch','worker_id':name,'terminal_reason':reason,'methods':methods or {a:{'status':'ok'} for a in ARMS}}
        if attempt is not None:terminal['attempt_id']=attempt
        p=self.root/(name+'.tar.gz');raw=json.dumps(terminal).encode()
        with tarfile.open(p,'w:gz') as tar:
            for _ in range(2 if duplicate else 1):
                info=tarfile.TarInfo('results/terminal.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
        self.state['workers'][name].update(archive=p.name,receipt={'run_id':'batch','worker_id':name,'bytes':p.stat().st_size,
            'sha256':controller.sha(p),'terminal_reason':reason,'known_credential_scan':'passed'})
        if attempt is not None:self.state['workers'][name]['receipt']['attempt_id']=attempt
    def reject(self):
        with self.assertRaises(ValueError):cleanup.execute(self.state,{})
        self.api.assert_not_called()
    def test_valid_complete_archives_only_qualify_not_delete(self):
        self.assertTrue(cleanup.evidence(self.state));self.api.assert_not_called()
    def use_attempt(self):
        self.state['attempt_id']='prep-current'
        for name in self.state['workers']:self.write(name,attempt='prep-current')
    def test_matching_attempt_qualifies(self):
        self.use_attempt();self.assertTrue(cleanup.evidence(self.state));self.api.assert_not_called()
    def test_old_attempt_receipt_rejected(self):
        self.use_attempt();self.state['workers']['node-b']['receipt']['attempt_id']='prep-old';self.reject()
    def test_missing_attempt_receipt_rejected(self):
        self.use_attempt();self.state['workers']['node-b']['receipt'].pop('attempt_id');self.reject()
    def test_old_attempt_terminal_rejected(self):
        self.use_attempt();self.write('node-b',attempt='prep-old')
        self.state['workers']['node-b']['receipt']['attempt_id']='prep-current';self.reject()
    def test_missing_attempt_terminal_rejected(self):
        self.use_attempt();self.write('node-b')
        self.state['workers']['node-b']['receipt']['attempt_id']='prep-current';self.reject()
    def test_frozen_plan_change_rejected(self):
        self.plan.write_text(self.plan.read_text()+' ');self.reject()
    def test_duplicate_worker_assignment_rejected(self):
        p=json.loads(self.plan.read_text());p['assignments'][1]['worker_id']='node-a'
        self.plan.write_text(json.dumps(p));self.state['plan_sha256']=controller.sha(self.plan);self.reject()
    def test_missing_arm_rejected(self):
        self.write('node-b',{a:{'status':'ok'} for a in ARMS[:-1]});self.reject()
    def test_extra_arm_rejected(self):
        self.write('node-b',{a:{'status':'ok'} for a in ARMS+['invented']});self.reject()
    def test_nonterminal_arm_rejected(self):
        self.write('node-b',{a:{'status':'running' if i==0 else 'ok'} for i,a in enumerate(ARMS)});self.reject()
    def test_false_completed_with_failed_arm_rejected(self):
        self.write('node-b',{a:{'status':'failed' if i==0 else 'ok'} for i,a in enumerate(ARMS)});self.reject()
    def test_failed_batch_retains_unrun_arms_and_can_qualify(self):
        self.write('node-b',{a:{'status':'failed' if i==0 else 'not_run'} for i,a in enumerate(ARMS)},reason='failed')
        self.assertTrue(cleanup.evidence(self.state));self.api.assert_not_called()
    def test_receipt_terminal_reason_mismatch_rejected(self):
        self.state['workers']['node-c']['receipt']['terminal_reason']='failed';self.reject()
    def test_receipt_identity_mismatch_rejected(self):
        self.state['workers']['node-c']['receipt']['worker_id']='node-a';self.reject()
    def test_unverified_secret_scan_rejected(self):
        self.state['workers']['node-c']['receipt']['known_credential_scan']='not_needed';self.reject()
    def test_duplicate_terminal_member_rejected(self):
        self.write('node-c',duplicate=True);self.reject()
    def test_hash_mismatch_rejected(self):
        (self.root/'node-c.tar.gz').write_bytes(b'corrupt');self.reject()
    def remote_gate_script(self):
        original={'run_id':'original','created_resources':{'instances':['i-node-a'],'security_group':'sg-source'}}
        self.api.side_effect=[{'Instances':{'Instance':[{'InstanceName':'original','SecurityGroupIds':{'SecurityGroupId':['sg-source']},
             'Tags':{'Tag':[{'TagKey':'ax-run','TagValue':'original'}]}}]}},
            {'Disks':{'Disk':[{'DiskId':d,'DeleteWithInstance':True} for d in self.state['workers']['node-a']['disks']]}}]
        captured=[]
        def gate(_iid,script,_name):
            captured.append(script)
            self.assertIn('juicefs',script);self.assertIn('findmnt -rn -o FSTYPE',script)
            self.assertIn('processes=$(ps -eo comm=) || exit 10',script)
            self.assertIn('mounts=$(findmnt -rn -o FSTYPE) || exit 11',script)
            self.assertIn('ax-parallel-prepare',script)
            raise RuntimeError('simulated active workload/mount')
        with patch.object(cleanup,'invoke',side_effect=gate):
            with self.assertRaises(RuntimeError):cleanup.execute(self.state,original)
        self.assertFalse(any(c.args[0].startswith('Delete') for c in self.api.call_args_list))
        self.assertEqual(len(captured),1)
        return captured[0]
    def test_process_and_mount_gate_precedes_deletion(self):
        self.remote_gate_script()
    def worker_cloud(self,action,**kw):
        if action=='DescribeInstances':
            ids=json.loads(kw['InstanceIds'])
            if len(ids)==3:return {'Instances':{'Instance':[]}}
            name=ids[0].removeprefix('i-');source=name=='node-a'
            return {'Instances':{'Instance':[{'InstanceName':'original' if source else 'batch-'+name,
                'SecurityGroupIds':{'SecurityGroupId':['sg-source' if source else 'sg-workers']},
                'Tags':{'Tag':[{'TagKey':'ax-run' if source else 'ax-batch','TagValue':'original' if source else 'batch'}]}}]}}
        if action=='DescribeDisks':
            if 'DiskIds' in kw:raise RuntimeError('test stops after instance deletions')
            name=kw['InstanceId'].removeprefix('i-')
            return {'Disks':{'Disk':[{'DiskId':v,'DeleteWithInstance':True} for v in self.state['workers'][name]['disks']]}}
        if action=='DeleteInstance':return {}
        raise AssertionError(action)
    def cleanup_original(self):
        self.state['worker_security_group']='sg-workers'
        return {'run_id':'original','created_resources':{'instances':['i-node-a'],'security_group':'sg-source'}}
    def test_all_workers_checked_before_deleting_bastion_last(self):
        original=self.cleanup_original();self.api.side_effect=self.worker_cloud;events=[]
        def gate(iid,script,name):events.append((name,iid))
        with patch.object(cleanup,'invoke',side_effect=gate):
            with self.assertRaisesRegex(RuntimeError,'test stops'):cleanup.execute(self.state,original)
        self.assertEqual(events[:3],[('ax-parallel-cleanup-gate','i-'+n) for n in ('node-a','node-b','node-c')])
        self.assertEqual([c.kwargs['InstanceId'] for c in self.api.call_args_list if c.args[0]=='DeleteInstance'],['i-node-c','i-node-b','i-node-a'])
    def test_last_worker_gate_failure_blocks_all_deletion_and_service_stop(self):
        original=self.cleanup_original();self.api.side_effect=self.worker_cloud;events=[]
        def gate(iid,script,name):
            events.append((name,iid))
            if iid=='i-node-c':raise RuntimeError('active last worker')
        with patch.object(cleanup,'invoke',side_effect=gate):
            with self.assertRaisesRegex(RuntimeError,'active last worker'):cleanup.execute(self.state,original)
        self.assertEqual(len(events),3)
        self.assertTrue(all(name=='ax-parallel-cleanup-gate' for name,_ in events))
        self.assertFalse(any(c.args[0].startswith('Delete') for c in self.api.call_args_list))
    def test_fusectl_control_mount_is_not_a_data_mount(self):
        script=self.remote_gate_script()
        pattern=re.search(r"grep -E '([^']+)'",script).group(1)
        # Exercise the expression emitted to findmnt's FSTYPE output. These ERE
        # operators have the same semantics in Python and the remote grep -E.
        for kind in ('fusectl','xfs','ext4','tmpfs'):
            with self.subTest(kind=kind):self.assertIsNone(re.search(pattern,kind))
        for kind in ('fuse','fuse.brewfs','fuse.juicefs','fuse.sshfs','fuseblk'):
            with self.subTest(kind=kind):self.assertIsNotNone(re.search(pattern,kind))

if __name__=='__main__':unittest.main()
