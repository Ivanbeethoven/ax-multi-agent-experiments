import io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import parallel_controller as controller
import recover_preparation as recovery
import cleanup_parallel as cleanup
ARMS=['native-kernel-default','overlay-kernel-default','packed-kernel-default','juicefs-kernel-default']
class RecoveryEvidenceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.plan=self.root/'plan.json';self.plan.write_text(json.dumps({'assignments':[{'worker_id':n,'arms':ARMS} for n in ('node-a','node-b','node-c')]}));self.state={'batch_id':'batch','plan_sha256':controller.sha(self.plan),'workers':{}}
  self.patches=[patch.object(recovery,'ROOT',self.root),patch.object(cleanup,'ROOT',self.root),patch.object(cleanup,'PLAN',self.plan)]
  for p in self.patches:p.start()
  for n in ('node-a','node-b','node-c'):self.write(n)
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def write(self,name,started=False):
  methods={a:{'status':'ok' if started and i==0 else 'not_run'} for i,a in enumerate(ARMS)};t={'run_id':'batch','worker_id':name,'terminal_reason':'failed','methods':methods};p=self.root/(name+'-results.tar.gz');raw=json.dumps(t).encode()
  with tarfile.open(p,'w:gz') as tar:
   info=tarfile.TarInfo('results/terminal.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
  self.state['workers'][name]={'instance_id':'i-'+name,'archive':p.name,'terminal':t,'receipt':{'run_id':'batch','worker_id':name,'terminal_reason':'failed','known_credential_scan':'passed','sha256':controller.sha(p),'bytes':p.stat().st_size}}
 def test_all_old_archives_receipts_and_state_retained_before_reuse(self):
  history=recovery.snapshot_attempt(self.state)
  for n,item in self.state['workers'].items():
   self.assertEqual(controller.sha(history/item['archive']),item['receipt']['sha256']);self.assertEqual(json.loads((history/(n+'-receipt.json')).read_text()),item['receipt'])
  self.assertEqual(json.loads((history/'parallel-state.local.json').read_text()),self.state)
 def test_existing_history_receipt_must_still_match(self):
  history=recovery.snapshot_attempt(self.state);(history/'node-a-receipt.json').write_text('{}')
  with self.assertRaises(ValueError):recovery.snapshot_attempt(self.state)
 def test_history_state_and_receipt_hashes_are_verified(self):
  history=recovery.snapshot_attempt(self.state);receipt=json.loads((history/'retention-receipt.json').read_text());self.assertEqual(len(receipt['evidence_files']),4);(history/'parallel-state.local.json').write_text('{}')
  with self.assertRaises(ValueError):recovery.snapshot_attempt(self.state)
 def test_corrupt_old_archive_fails_before_history_is_created(self):
  (self.root/'node-b-results.tar.gz').write_bytes(b'bad')
  with self.assertRaises(ValueError):recovery.snapshot_attempt(self.state)
  self.assertFalse((self.root/'attempt-history').exists())
 def test_actual_started_cell_refuses_preparation_retry(self):
  self.write('node-a',started=True)
  with self.assertRaises(ValueError):recovery.snapshot_attempt(self.state)
 def test_state_cannot_hide_started_archive_method(self):
  self.write('node-a',started=True);self.state['workers']['node-a']['terminal']={**self.state['workers']['node-a']['terminal'],'methods':{a:{'status':'not_run'} for a in ARMS}}
  with self.assertRaises(ValueError):recovery.snapshot_attempt(self.state)
class MissingBackendTests(unittest.TestCase):
 def test_embedded_reset_recognizes_only_explicit_named_absence(self):
  import ast,subprocess
  body=recovery.RESET.split("python3 - <<'PY'\n",1)[1].split('\nPY',1)[0];tree=ast.parse(body);condition=next(n.test for n in ast.walk(tree) if isinstance(n,ast.If) and isinstance(n.test,ast.UnaryOp) and any(isinstance(x,ast.Constant) and x.value=='[]' for x in ast.walk(n.test)))
  code=compile(ast.Expression(condition),'<actual absence guard>','eval')
  for error in ('error: no such object: ax-parallel-rustfs\n','Error: No such object: ax-parallel-rustfs\n'):
   self.assertFalse(eval(code,{'r':subprocess.CompletedProcess([],1,'[]\n',error)}))
  for error in ('cannot connect to Docker daemon','error: no such object: another-container'):
   self.assertTrue(eval(code,{'r':subprocess.CompletedProcess([],1,'[]\n',error)}))
class AttemptControlTests(unittest.TestCase):
 def test_attempt_prefix_is_disjoint_and_path_safe(self):
  self.assertEqual(controller.results_prefix({}),'parallel-results/');self.assertEqual(controller.results_prefix({'attempt_id':'prep-2'}),'parallel-results/prep-2/')
  with self.assertRaises(ValueError):controller.results_prefix({'attempt_id':'../other'})
 def test_old_receipt_rejected_before_any_download(self):
  with patch.object(controller,'get_json',return_value={'attempt_id':'old'}),patch.object(controller,'cli') as cli:
   with self.assertRaises(ValueError):controller.collect({'attempt_id':'new','workers':{'node-a':{}}},{'created_resources':{'bucket':'bucket'}})
   cli.assert_not_called()
 def test_old_terminal_rejected_even_with_new_attempt_receipt(self):
  with tempfile.TemporaryDirectory() as d,patch.object(controller,'ROOT',Path(d)),patch.object(controller,'get_json') as get,patch.object(controller,'cli'):
   root=Path(d);path=root/'node-a-results.tar.gz';raw=json.dumps({'attempt_id':'old'}).encode()
   with tarfile.open(path,'w:gz') as tar:
    info=tarfile.TarInfo('results/terminal.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
   get.return_value={'attempt_id':'new','run_id':'batch','worker_id':'node-a','terminal_reason':'failed','known_credential_scan':'passed','sha256':controller.sha(path),'bytes':path.stat().st_size}
   with self.assertRaisesRegex(ValueError,'terminal belongs'):controller.collect({'attempt_id':'new','batch_id':'batch','workers':{'node-a':{}}},{'created_resources':{'bucket':'bucket'}})
 def test_all_not_run_main_holds_before_any_cleanup_process(self):
  import sys,contextlib
  with tempfile.TemporaryDirectory() as d,contextlib.ExitStack() as stack:
   root=Path(d);orig=root/'original';orig.mkdir();(orig/'state.local.json').write_text(json.dumps({'created_resources':{'bucket':'bucket'}}));plan=root/'plan.json';plan.write_text('{}');statepath=root/'state.json';commit='f'*40
   state={'batch_id':'batch','runner_commit':commit,'plan_sha256':controller.sha(plan),'workers':{n:{'instance_id':'i-'+n,'backup_ssh_verified':True,'terminal':{'methods':{a:{'status':'not_run'} for a in ARMS}}} for n in ('node-a','node-b','node-c')}};statepath.write_text(json.dumps(state))
   for name,value in [('ROOT',root),('ORIGINAL',orig),('STATE',statepath),('PLAN',plan)]:stack.enter_context(patch.object(controller,name,value))
   stack.enter_context(patch.object(controller,'frozen_contract',return_value={'runner_commit':commit}));stack.enter_context(patch.object(controller,'get_json',return_value={'ready':True}));stack.enter_context(patch.object(sys,'argv',['controller','--execute','--once']))
   for name in ('refresh_build_progress','gate','prepare_runner_bundle','takeover_collector','provision','deploy'):stack.enter_context(patch.object(controller,name))
   stack.enter_context(patch.object(controller,'collect',return_value=True));revoke=stack.enter_context(patch.object(controller,'authorize_launch'));process=stack.enter_context(patch.object(controller.subprocess,'run'))
   with contextlib.redirect_stdout(io.StringIO()):controller.main()
   self.assertEqual(json.loads(statepath.read_text())['phase'],'repair_hold');self.assertFalse(json.loads(statepath.read_text())['launch_authorized']);revoke.assert_called_once();process.assert_not_called()
 def test_unstarted_node_requires_repair_even_if_another_node_ran(self):
  state={'workers':{'a':{'terminal':{'methods':{'arm':{'status':'not_run'}}}},'b':{'terminal':{'methods':{'arm':{'status':'ok'}}}}}}
  self.assertTrue(controller.preparation_failure_needs_repair(state));state['workers']['a']['terminal']['methods']['arm']['status']='failed';self.assertFalse(controller.preparation_failure_needs_repair(state))
if __name__=='__main__':unittest.main()
