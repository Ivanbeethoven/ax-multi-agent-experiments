import importlib.util,io,json,pathlib,sys,tarfile,tempfile,unittest
root=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(root/'infra/alicloud/parallel'))
spec=importlib.util.spec_from_file_location('trace_summary',root/'infra/alicloud/parallel/summarize_trace_results.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class SummaryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
  self.state={'batch_id':'batch','attempt_id':'attempt','workers':{},'supplemental_replays':[]}
  for node in ('node-a','node-b','node-c'):
   statuses={x:('failed' if node=='node-a' and x=='juicefs-kernel-default' else 'ok') for x in m.ARMS}
   self.state['workers'][node]=self.archive(node,node+'-main.tar.gz','batch',statuses)
  self.add_supplement();self.save()
 def tearDown(self):self.tmp.cleanup()
 def save(self): (self.root/'parallel-state.local.json').write_text(json.dumps(self.state))
 def archive(self,node,name,run,statuses,unsafe=False,counts=10):
  terminal={'worker_id':node,'run_id':run,'attempt_id':'attempt','strict_correctness':False,
   'terminal_reason':'failed' if 'failed' in statuses.values() else 'completed',
   'methods':{k:{'status':v} for k,v in statuses.items()}}
  docs={'results/terminal.json':terminal}
  for label,status in statuses.items():
   if status=='not_run':continue
   path='results/'+label+'/'
   docs[path+'result.json']={'status':status,'mode':'trace'}
   if status=='ok':
    r={'valid':False,'completed':True,'mode':'performance','operations':counts,'actual_read_bytes':25,
       'actual_write_bytes':30,'remaining_handles':0,'execution_seconds':3.5,'probe_errno_differences':0}
    docs[path+'replay.json']=r;docs[path+'trace-result.json']={'status':'ok','completed':True,'replay':r}
  if unsafe:docs['../escape']={}
  path=self.root/name
  with tarfile.open(path,'w:gz') as t:
   for filename,value in docs.items():
    raw=json.dumps(value).encode();info=tarfile.TarInfo(filename);info.size=len(raw);t.addfile(info,io.BytesIO(raw))
  return {'archive':name,'receipt':{'worker_id':node,'run_id':run,'attempt_id':'attempt','known_credential_scan':'passed',
   'terminal_reason':terminal['terminal_reason'],'bytes':path.stat().st_size,'sha256':m.sha(path)}}
 def add_supplement(self):
  item=self.archive('node-a','retry.tar.gz','batch-retry',{'juicefs-kernel-default':'ok'})
  item.update(worker_id='node-a',run_id='batch-retry',parent_attempt_id='attempt',arm='juicefs-kernel-default',status='archived')
  self.state['supplemental_replays'].append(item)
 def test_failure_preserved_and_retry_separate(self):
  x=m.summarize(self.root/'parallel-state.local.json');self.assertEqual(len(x['rows']),13);self.assertEqual(x['selected_count'],12)
  target=[r for r in x['rows'] if r['node']=='node-a' and r['method']=='juicefs'];self.assertEqual(len(target),2)
  failed=next(r for r in target if r['source_kind']=='main');self.assertIsNone(failed['execution_seconds']);self.assertFalse(failed['selected_for_comparison']);self.assertEqual(failed['supplement_run_id'],'batch-retry')
  retry=next(r for r in target if r['source_kind']=='supplement');self.assertTrue(retry['selected_for_comparison']);self.assertEqual(retry['execution_seconds'],3.5)
 def test_digest_mismatch_rejected(self):
  self.state['workers']['node-b']['receipt']['sha256']='0'*64;self.save()
  with self.assertRaises(ValueError):m.summarize(self.root/'parallel-state.local.json')
 def test_duplicate_success_rejected_without_selection(self):
  self.add_supplement();self.state['supplemental_replays'][-1]['run_id']='batch-retry2'
  self.state['supplemental_replays'][-1].update(self.archive('node-a','retry2.tar.gz','batch-retry2',{'juicefs-kernel-default':'ok'}));self.save()
  with self.assertRaisesRegex(ValueError,'multiple successful'):m.summarize(self.root/'parallel-state.local.json')
 def test_incomplete_supplement_rejected(self):
  self.state['supplemental_replays'][0]['status']='running';self.save()
  with self.assertRaises(ValueError):m.summarize(self.root/'parallel-state.local.json')
 def test_unsafe_tar_rejected(self):
  self.state['workers']['node-b']=self.archive('node-b','bad.tar.gz','batch',{k:'ok' for k in m.ARMS},unsafe=True);self.save()
  with self.assertRaises(ValueError):m.summarize(self.root/'parallel-state.local.json')
 def test_different_work_count_rejected(self):
  self.state['workers']['node-c']=self.archive('node-c','different.tar.gz','batch',{k:'ok' for k in m.ARMS},counts=11);self.save()
  with self.assertRaisesRegex(ValueError,'differ'):m.summarize(self.root/'parallel-state.local.json')

if __name__=='__main__':unittest.main(verbosity=2)
