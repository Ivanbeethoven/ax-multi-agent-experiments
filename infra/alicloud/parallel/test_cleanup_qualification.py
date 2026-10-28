"""Offline fail-closed cleanup qualification; never calls cloud APIs."""
import io
import json
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
    def write(self,name,methods=None,reason='completed',duplicate=False):
        terminal={'run_id':'batch','worker_id':name,'terminal_reason':reason,'methods':methods or {a:{'status':'ok'} for a in ARMS}}
        p=self.root/(name+'.tar.gz');raw=json.dumps(terminal).encode()
        with tarfile.open(p,'w:gz') as tar:
            for _ in range(2 if duplicate else 1):
                info=tarfile.TarInfo('results/terminal.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
        self.state['workers'][name].update(archive=p.name,receipt={'run_id':'batch','worker_id':name,'bytes':p.stat().st_size,
            'sha256':controller.sha(p),'terminal_reason':reason,'known_credential_scan':'passed'})
    def reject(self):
        with self.assertRaises(ValueError):cleanup.execute(self.state,{})
        self.api.assert_not_called()
    def test_valid_complete_archives_only_qualify_not_delete(self):
        self.assertTrue(cleanup.evidence(self.state));self.api.assert_not_called()
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
    def test_process_and_mount_gate_precedes_deletion(self):
        original={'run_id':'original','created_resources':{'instances':['i-node-a'],'security_group':'sg-source'}}
        self.api.side_effect=[{'Instances':{'Instance':[{'InstanceName':'original','SecurityGroupIds':{'SecurityGroupId':['sg-source']},
             'Tags':{'Tag':[{'TagKey':'ax-run','TagValue':'original'}]}}]}},
            {'Disks':{'Disk':[{'DiskId':d,'DeleteWithInstance':True} for d in self.state['workers']['node-a']['disks']]}}]
        def gate(_iid,script,_name):
            self.assertIn('juicefs',script);self.assertIn('findmnt -rn -o FSTYPE',script)
            self.assertIn('processes=$(ps -eo comm=) || exit 10',script)
            self.assertIn('mounts=$(findmnt -rn -o FSTYPE) || exit 11',script)
            self.assertIn('ax-parallel-prepare',script)
            raise RuntimeError('simulated active workload/mount')
        with patch.object(cleanup,'invoke',side_effect=gate):
            with self.assertRaises(RuntimeError):cleanup.execute(self.state,original)
        self.assertFalse(any(c.args[0].startswith('Delete') for c in self.api.call_args_list))

if __name__=='__main__':unittest.main()
