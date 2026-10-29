import io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import parallel_controller as controller
import recover_archives as recovery
class MirrorTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir();self.dest=self.root/'mirror';self.patterns={'AWS_ACCESS_KEY_ID':b'test-access-id','AWS_SECRET_ACCESS_KEY':b'test-private-secret'}
 def tearDown(self):self.temp.cleanup()
 def test_exact_allowed_log_redaction_retains_original_and_metric_bytes(self):
  log=self.source/'juicefs-kernel-default/0001-format.stderr';log.parent.mkdir();log.write_bytes(b'format access-key=test-access-id\n');metric=self.source/'replay.json';metric.write_bytes(b'{"operations":1747109,"execution_seconds":33.4}\n')
  r=recovery.sanitized_mirror(self.source,self.dest,self.patterns,{'juicefs-kernel-default/0001-format.stderr':{'AWS_ACCESS_KEY_ID'}})
  self.assertIn(b'test-access-id',log.read_bytes());self.assertNotIn(b'test-access-id',(self.dest/log.relative_to(self.source)).read_bytes());self.assertEqual(metric.read_bytes(),(self.dest/'replay.json').read_bytes());self.assertEqual(r['redactions'][0]['replacement_counts'],{'AWS_ACCESS_KEY_ID':1});self.assertFalse(r['measurement_values_modified'])
 def test_unknown_credential_location_creates_no_mirror(self):
  (self.source/'replay.json').write_bytes(b'test-access-id')
  with self.assertRaisesRegex(ValueError,'unreviewed credential location'):recovery.sanitized_mirror(self.source,self.dest,self.patterns,{})
  self.assertFalse(self.dest.exists())
 def test_secret_key_in_allowed_log_is_still_rejected(self):
  (self.source/'stderr').write_bytes(b'test-private-secret')
  with self.assertRaises(ValueError):recovery.sanitized_mirror(self.source,self.dest,self.patterns,{'stderr':{'AWS_ACCESS_KEY_ID'}})
 def test_signed_url_marker_never_gets_partially_redacted(self):
  (self.source/'stderr').write_bytes(b'https://example.invalid/?Signature=unrecognized')
  with self.assertRaisesRegex(ValueError,'signed-URL'):recovery.sanitized_mirror(self.source,self.dest,self.patterns,{})
 def test_existing_mirror_cannot_be_overwritten(self):
  self.dest.mkdir()
  with self.assertRaises(ValueError):recovery.sanitized_mirror(self.source,self.dest,self.patterns,{})
class SupplementalIdentityTests(unittest.TestCase):
 def test_retry_has_distinct_identity_and_single_jfs_arm(self):
  c={'worker_id':'node-a','run_id':'batch','attempt_id':'attempt','arms':['native-kernel-default','juicefs-kernel-default']};tag,run,arms=recovery.case_identity(c,True,True);self.assertEqual((tag,run,arms),('attempt-jfs-retry01','batch-a-jfs-retry01',{'juicefs-kernel-default'}))
 def test_retry_cannot_upload_over_parent_matrix(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-a'},True,False)
 def test_other_node_cannot_claim_A_retry(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-b'},True,True)
 def test_C_native_retry_has_distinct_identity(self):
  c={'worker_id':'node-c','run_id':'batch','attempt_id':'attempt'};self.assertEqual(recovery.case_identity(c,False,True,True),('attempt-native-retry01','batch-c-native-retry01',{'native-kernel-default'}))
 def test_C_native_retry_cannot_overwrite_main_upload(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-c'},False,False,True)
 def test_supplemental_cases_cannot_be_combined(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-c'},True,True,True)
 def test_C_second_preflight_attempt_has_separate_archive_identity(self):
  c={'worker_id':'node-c','run_id':'batch','attempt_id':'attempt'};self.assertEqual(recovery.case_identity(c,False,True,True,2),('attempt-native-retry02','batch-c-native-retry02',{'native-kernel-default'}))
 def test_second_ordinal_cannot_redirect_A_JFS(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-a'},True,True,False,2)
 def test_unrecorded_ordinal_is_rejected(self):
  with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-c'},False,True,True,4)
 def test_C_third_preflight_attempt_has_separate_archive_identity(self):
  c={'worker_id':'node-c','run_id':'batch','attempt_id':'attempt'};self.assertEqual(recovery.case_identity(c,False,True,True,3),('attempt-native-retry03','batch-c-native-retry03',{'native-kernel-default'}))
 def test_third_ordinal_cannot_redirect_A_JFS_or_main(self):
  for jfs in (True,False):
   with self.assertRaises(ValueError):recovery.case_identity({'worker_id':'node-a'},jfs,True,False,3)
class CachedArchiveEvidenceTests(unittest.TestCase):
 def test_cached_small_fallback_requires_hold_and_no_network(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);archive=root/'fallback.tar.gz';terminal={'methods':{'overlay-kernel-default':{'status':'ok'}}}
   with tarfile.open(archive,'w:gz') as tar:
    raw=json.dumps(terminal).encode();m=tarfile.TarInfo('results/terminal.json');m.size=len(raw);tar.addfile(m,io.BytesIO(raw))
   state={'workers':{'node-b':{'receipt':{'sha256':'x'},'archive':archive.name,'terminal':terminal}}}
   with patch.object(controller,'ROOT',root),patch.object(controller,'STATE',root/'state.json'),patch.object(controller,'get_json') as network:
    self.assertFalse(controller.collect(state,{'created_resources':{'bucket':'bucket'}}));network.assert_not_called()
   self.assertIn('evidence_hold',state['workers']['node-b']);self.assertTrue((root/'state.json').is_file())
if __name__=='__main__':unittest.main()
