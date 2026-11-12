import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from bench.runtime.smoke_journal import SmokeJournal


class SmokeJournalTests(unittest.TestCase):
    def test_failed_command_retains_timing_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stages.jsonl'
            journal=SmokeJournal(path,('test-secret',))
            with self.assertRaises(subprocess.CalledProcessError):
                journal.run([sys.executable,'-c','raise SystemExit(7)','test-secret'],
                            check=True,capture_output=True,timeout=3)
            raw=path.read_text()
            self.assertNotIn('test-secret',raw)
            rows=[json.loads(line) for line in raw.splitlines()]
            self.assertEqual([row['event'] for row in rows],['start','end'])
            self.assertEqual(rows[-1]['status'],'failed')
            self.assertGreater(rows[-1]['elapsed_ns'],0)

    @unittest.skipUnless(sys.platform=='linux','Linux deadline')
    def test_deadline_records_failure_restores_handler_and_allows_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=SmokeJournal(Path(folder)/'stages.jsonl')
            before=signal.getsignal(signal.SIGALRM)
            cleaned=False
            try:
                with self.assertRaises(TimeoutError):
                    with journal.stage('blocked-read',timeout=.05):
                        time.sleep(2)
            finally:
                cleaned=True
            self.assertTrue(cleaned)
            self.assertEqual(signal.getsignal(signal.SIGALRM),before)
            self.assertEqual(signal.getitimer(signal.ITIMER_REAL)[0],0)
            self.assertEqual(json.loads(journal.path.read_text().splitlines()[-1])['status'],'failed')
