import io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import parallel_controller as controller
import cleanup_parallel as cleanup
ARMS=['native-kernel-default','overlay-kernel-default','packed-kernel-default','juicefs-kernel-default']
class GateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.planpath=self.root/'plan.json'
  self.plan={'schema_version':2,'workers':['node-a','node-b','node-c'],'planned_cells':12,'arm_controls':{a:{'group':'kernel-default','requested_kernel_metadata_ttl_ms':None,'requested_open_keep_cache':None} for a in ARMS},'assignments':[{'worker_id':w,'arms':ARMS[i:]+ARMS[:i]} for i,w in enumerate(('node-a','node-b','node-c'))]}
  self.plan['phases']=[{'phase':i+1,'workers':{a['worker_id']:a['arms'][i] for a in self.plan['assignments']}} for i in range(4)];self.planpath.write_text(json.dumps(self.plan));(self.root/'runner-ready.json').write_text(json.dumps({'runner_commit':controller.RUNNER,'arms':ARMS,'plan_sha256':controller.sha(self.planpath)}))
  self.patches=[patch.object(controller,'ROOT',self.root),patch.object(controller,'PLAN',self.planpath),patch.object(controller,'STATE',self.root/'state.json')]
  for p in self.patches:p.start()
  self.assets={'brewfs_source':'350dc108a3bffcb5e7e27cc4279dfb5729febe60','rustfs_source':'a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed','registry_image':{},'credential_scan':'passed','binaries':{x:{} for x in ('brewfs','packed_v3_snapshot_fixture','full-fuse-replay','ax-fs-probe','juicefs')}}
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def test_positive_reviewed_normal_gate(self):self.assertTrue(controller.gate(self.assets,self.plan,controller.RUNNER))
 def test_pause_stops_complete_assets(self):
  (self.root/'PAUSED-FOR-CACHE-PLAN').write_text('review')
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,controller.RUNNER)
 def test_wrong_source(self):
  self.assets['brewfs_source']='wrong'
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,controller.RUNNER)
 def test_wrong_runner(self):
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,'0'*40)
 def test_duplicate_worker(self):
  self.plan['assignments'][1]['worker_id']='node-a'
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,controller.RUNNER)
 def test_legacy_cache_controls_rejected(self):
  self.plan['arm_controls'][ARMS[0]]['requested_kernel_metadata_ttl_ms']=0
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,controller.RUNNER)
 def test_plan_mutation(self):
  self.planpath.write_text(self.planpath.read_text()+' ')
  with self.assertRaises(ValueError):controller.gate(self.assets,self.plan,controller.RUNNER)
 def test_provision_pause_before_cloud(self):
  with patch.object(controller,'ecs') as api,patch.object(controller,'revalidate',side_effect=ValueError('paused')):
   with self.assertRaises(ValueError):controller.provision({}, {})
   api.assert_not_called()
 def test_extra_limit(self):
  with patch.object(controller,'revalidate'),patch.object(controller,'ecs') as api:
   with self.assertRaises(ValueError):controller.provision({'runner_bundle':{'commit':controller.RUNNER},'workers':{str(i):{'source_node':False} for i in range(3)}},{})
   api.assert_not_called()
 def test_existing_build_node_not_recreated(self):
  state={'batch_id':'batch','runner_bundle':{'commit':controller.RUNNER},'ssh_key_imported':True,'ssh_key_name':'key','ssh_source':'192.0.2.1/32','source_ssh_rule_created':True,'worker_security_group':'sg-test','ssh_rule_created':True,'workers':{'node-a':{'source_node':True,'instance_id':'i-source','data_disk_id':'d-a'},'node-b':{'source_node':False,'instance_id':'i-existing','data_disk_id':'d-b'}}};created=[]
  def api(action,**kw):
   if action=='RunInstances':created.append(kw);return {'InstanceIdSets':{'InstanceIdSet':['i-new']}}
   if action=='DescribeInstances':return {'Instances':{'Instance':[{'Status':'Running','PublicIpAddress':{'IpAddress':[]},'VpcAttributes':{'PrivateIpAddress':{'IpAddress':['192.0.2.3']}}}]}}
   if action=='DescribeDisks':return {'Disks':{'Disk':[{'DiskId':'d-new-sys','Type':'system','Size':40,'DeleteWithInstance':True},{'DiskId':'d-new-data','Type':'data','Size':500,'DeleteWithInstance':True}]}}
   raise AssertionError(action)
  with patch.object(controller,'revalidate'),patch.object(controller,'ecs',side_effect=api),patch.object(controller,'quote',return_value=({},1.5106)):
   controller.provision(state,{'approved_spec':{'vpc_id':'vpc','vswitch_id':'vsw'}});controller.provision(state,{'approved_spec':{'vpc_id':'vpc','vswitch_id':'vsw'}})
  self.assertEqual(len(created),1);self.assertTrue(state['workers']['node-c']['private_only']);self.assertEqual(state['workers']['node-b']['instance_id'],'i-existing');self.assertEqual(state['workers']['node-c']['instance_id'],'i-new')
class PrivateProxyTests(unittest.TestCase):
 def test_only_public_dependency_https_targets(self):
  from private_egress_proxy import allowed
  self.assertTrue(allowed('github.com',443,True));self.assertFalse(allowed('github.com.attacker.invalid',443,True));self.assertFalse(allowed('127.0.0.1',443,True));self.assertFalse(allowed('169.254.169.254',443,True));self.assertFalse(allowed('github.com',22,True));self.assertFalse(allowed('user@github.com',443,True))
 def test_only_ubuntu_http_and_no_extra_ports(self):
  from private_egress_proxy import allowed
  self.assertTrue(allowed('security.ubuntu.com',80,False));self.assertFalse(allowed('security.ubuntu.com',443,False));self.assertFalse(allowed('example.com',80,False))
class WrapperTransferTests(unittest.TestCase):
 def test_executable_sources_have_unix_shebang(self):
  p=Path(controller.__file__).parent/'rustfs_cargo_handoff.py';self.assertEqual(p.read_bytes().splitlines(keepends=True)[0],b'#!/usr/bin/python3\n')
class ExternalAttemptTests(unittest.TestCase):
 def check(self,provenance,status):
  import ast
  source=ast.parse((Path(controller.__file__).parent/'rustfs_import_external.py').read_text());function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='candidate_state');ns={'R':'source'};exec(compile(ast.Module(body=[function],type_ignores=[]),'<actual candidate_state>','exec'),ns);return ns['candidate_state'](provenance,status,'builder','new')
 def test_old_failure_cannot_abort_current_attempt(self):self.assertEqual(self.check({'build_attempt_id':'old'},{'build_attempt_id':'old','stage':'failed'}),'pending')
 def test_new_failure_detected_even_when_old_provenance_exists(self):self.assertEqual(self.check({'build_attempt_id':'old'},{'build_attempt_id':'new','source':'source','instance':'builder','stage':'failed'}),'failed')
 def test_new_provenance_only_is_ready(self):self.assertEqual(self.check({'build_attempt_id':'new','source':'source','builder_instance':'builder'},None),'ready')
 def test_wrong_builder_rejected(self):
  with self.assertRaises(ValueError):self.check(None,{'build_attempt_id':'new','source':'source','instance':'other','stage':'failed'})
if __name__=='__main__':unittest.main()
