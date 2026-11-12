import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS=Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts'
SPEC=importlib.util.spec_from_file_location('c1_b_scratch',SCRIPTS/'run-b-c1-scratch.py')
c1=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c1)

class ScratchOwnershipTest(unittest.TestCase):
    def setup_attempt(self):
        a=object.__new__(c1.Attempt)
        a.tag='ax-c1b-0123456789'
        a.old={'d-original'}
        return a

    def disk(self,attempt,**overrides):
        values=dict(DiskId='d-scratch',DiskName=attempt.tag+'-scratch-0',InstanceId='',
                    Type='data',ZoneId=c1.ZONE,Status='Available',Size=c1.SIZE,Category='cloud_essd',
                    PerformanceLevel='PL1',DiskChargeType='PostPaid',MultiAttach='Disabled',
                    Tags={'Tag':[dict(TagKey='campaign',TagValue=attempt.tag),
                                 dict(TagKey='role',TagValue='c1-b-scratch')]})
        values.update(overrides)
        return values

    def test_detached_provider_tag_can_only_name_original_b_objects(self):
        a=self.setup_attempt()
        row=self.disk(a)
        tags=row['Tags']['Tag']
        tags.append(dict(TagKey='acs:ecs:lastAttachedInstance',TagValue=c1.OBJECT_HOST))
        a.disks=Mock(return_value=[row])
        self.assertEqual(a.owned(),[row])
        tags[-1]['TagValue']='i-another'
        with self.assertRaisesRegex(ValueError,'ownership'):
            a.owned()

    def test_wrong_size_shape_original_id_or_foreign_attachment_refuses_cleanup(self):
        a=self.setup_attempt()
        for changed in ({'Size':60},{'DiskId':'d-original'},{'InstanceId':'i-another'},
                        {'PerformanceLevel':'PL0'},{'DiskName':a.tag+'-scratch-99'}):
            with self.subTest(changed=changed):
                a.disks=Mock(return_value=[self.disk(a,**changed)])
                with self.assertRaisesRegex(ValueError,'ownership'):
                    a.owned()

    def test_unrelated_disks_are_never_selected(self):
        a=self.setup_attempt()
        a.disks=Mock(return_value=[self.disk(a,Tags={'Tag':[dict(TagKey='campaign',TagValue='other')]})])
        self.assertEqual(a.owned(),[])



WORKER_SPEC=importlib.util.spec_from_file_location('c1_b_scratch_worker',SCRIPTS/'worker-b-c1-scratch.py')
worker=importlib.util.module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(worker)


class ScratchArmTests(unittest.TestCase):
    def test_remote_claim_submits_systemd_once_even_if_launch_rpc_replayed(self):
        with tempfile.TemporaryDirectory(prefix='ax-c1b-') as tmp, patch.object(worker,'run') as systemd:
            root=Path(tmp)
            self.assertTrue(worker.submit_arm(root,'brewfs',240)['submitted'])
            self.assertEqual(worker.submit_arm(root,'brewfs',240),
                             dict(submitted=False,already_claimed=True))
            systemd.assert_called_once()
            self.assertIn('--property=Restart=no',systemd.call_args.args)
            self.assertIn('--no-block',systemd.call_args.args)

    def state(self,terminal=False,**overrides):
        worker_id=dict(invocation='a'*32,pid=501,start_ticks=123,utc_ns=1)
        finished=dict(passed=True,runtime=worker_id,result_sha256='c'*64,utc_ns=2)
        row=dict(launch={'unit':'owned'},runtime=worker_id,terminal=finished if terminal else None,
                 InvocationID=worker_id['invocation'],MainPID=str(worker_id['pid']),
                 ActiveState='inactive' if terminal else 'active',NRestarts='0',LoadState='loaded',
                 ExecMainStatus='0',observed_result_sha256='c'*64 if terminal else None)
        row.update(overrides)
        return row

    def test_ambiguous_ssh_start_never_resubmits_and_waits_for_actual_cold_close(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c1.time,'sleep'):
            a=object.__new__(c1.Attempt)
            a.out=Path(tmp)
            a.tag='ax-c1b-0123456789'
            a.active=Mock()
            a.ssh=Mock(side_effect=[
                subprocess.TimeoutExpired('launch',20),
                subprocess.CompletedProcess('status',0,json.dumps(self.state())),
                subprocess.CompletedProcess('status',0,json.dumps(self.state(terminal=True))),
            ])
            a.arm_once('brewfs','/scoped/worker.py',240)
            actions=[call.args[3] for call in a.ssh.call_args_list]
            self.assertEqual(actions,['launch-arm','arm-status','arm-status'])
            self.assertFalse(json.loads((a.out/'brewfs-launch-transport.json').read_text())['retried'])

    def test_no_success_on_missing_digest_or_changed_service_invocation(self):
        first,done=c1.arm_progress(self.state(),None)
        self.assertFalse(done)
        with self.assertRaisesRegex(ValueError,'cold close'):
            c1.arm_progress(self.state(terminal=True,observed_result_sha256='f'*64),first)
        with self.assertRaisesRegex(ValueError,'restarted'):
            c1.arm_progress(self.state(NRestarts='1'),first)
        with self.assertRaisesRegex(ValueError,'differs from first worker'):
            c1.arm_progress(self.state(InvocationID='b'*32),first)
        self.assertEqual(c1.arm_progress(self.state(terminal=True,LoadState='not-found',InvocationID='0'*32),first),
                         (first,True))
