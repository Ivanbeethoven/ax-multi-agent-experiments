import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/c2-publish-only.py'
spec = importlib.util.spec_from_file_location('c2_pubdiag', SCRIPT)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class PublishOnly(unittest.TestCase):
    def test_metadata_must_be_stopped_even_after_workers_start(self):
        ids = dict(client='client', objects='objects', metadata='metadata')
        rows = [dict(InstanceId=role, InstanceName=name, ZoneId='cn-hangzhou-j',
                     Status='Stopped' if role=='metadata' else 'Running', StoppedMode='StopCharging')
                for role,name in diag.ROLE_NAMES.items()]
        api = dict(RequestId='mock', TotalCount=3, Instances={'Instance':rows})
        self.assertTrue(diag.exact_status({'IDS':ids},api,{'metadata'}))
        rows[-1]['Status'] = 'Running'
        self.assertFalse(diag.exact_status({'IDS':ids},api,{'metadata'}))
        rows[-1].update(Status='Stopped', StoppedMode='KeepCharging')
        self.assertFalse(diag.exact_status({'IDS':ids},api,{'metadata'}))

    def test_container_binds_require_exact_destinations_and_writable_mode(self):
        rows = [dict(Destination=f'/data/rustfs{i}',Source=f'/srv/ax-rustfs-data-{i}',
                     Type='bind',RW=True) for i in range(4)]
        self.assertEqual(diag.data_binds(rows)['/data/rustfs0'],'/srv/ax-rustfs-data-0')
        rows[0]['RW'] = False
        with self.assertRaises(ValueError):
            diag.data_binds(rows)
        rows[0]['RW'] = True
        rows.append(dict(Destination='/data',Source='/tmp',Type='bind',RW=True))
        with self.assertRaises(ValueError):
            diag.data_binds(rows)

    @unittest.skipUnless(sys.platform=='linux', 'uses real /proc and POSIX child signals')
    def test_process_deadline_reports_timeout_not_success_even_after_reaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'cgroup.procs').touch()
            marker=threading.Event()
            command=[sys.executable,'-c','import time; time.sleep(20)']
            with patch.object(diag,'SLOT',out),patch.object(diag,'PRODUCER_SECONDS',1.2):
                self.assertFalse(diag.execute_producer(command,os.environ,out,time.time()+30,lambda:None,marker))
            self.assertTrue(marker.is_set())
            result=json.loads((out/'producer-exit.json').read_text())
            self.assertEqual(result['outcome'],'timeout')
            self.assertLess(result['exit_code'],0)
            self.assertIsInstance(result['start_ticks'],int)
            self.assertFalse(Path(f"/proc/{result['pid']}").exists())
            samples=[json.loads(row) for row in (out/'process.ndjson').read_text().splitlines()]
            self.assertGreaterEqual(len(samples),1)
            self.assertTrue({'cpu_user_ticks','io','wchan_counts','rustfs_tcp'}<=set(samples[0]))

    def test_list_records_count_without_keys(self):
        class S3:
            def list_objects_v2(self, **args):
                self.args = args
                return {'Contents':[{'Size':3,'Key':'should-not-be-logged'}],'IsTruncated':True}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            done=threading.Event()
            done.set()
            s3=S3()
            diag.object_counts(s3,'fixture',out,done)
            self.assertEqual(s3.args,dict(Bucket='fixture',MaxKeys=1000))
            raw=(out/'object-count.ndjson').read_text()
            self.assertNotIn('should-not-be-logged',raw)
            self.assertTrue(json.loads(raw)['truncated'])
            self.assertEqual(json.loads(raw)['bytes'],3)


if __name__ == '__main__':
    unittest.main()
