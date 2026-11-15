import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from bench.runtime.fork_candidate import PATCH, SOURCE, TREE, verify_candidate


class CandidatePins(unittest.TestCase):
    def test_rejects_stale_binary_wrong_patch_and_conflicting_success(self):
        with tempfile.TemporaryDirectory() as folder:
            binary, receipt = Path(folder)/'brewfs', Path(folder)/'build.json'
            binary.write_bytes(b'qualified candidate')
            valid = dict(source_commit=SOURCE, source_tree=TREE, candidate_patch_sha256=PATCH,
                         artifact_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(), passed=True)
            receipt.write_text(json.dumps(valid))
            self.assertEqual(verify_candidate(binary,receipt,SOURCE)['artifact_sha256'],valid['artifact_sha256'])
            for change in ({'artifact_sha256':'0'*64}, {'candidate_patch_sha256':'0'*64},
                           {'patch_sha256':'0'*64}, {'source_tree':'0'*40},
                           {'passed':False,'build_exit_code':0}, {'build_exit_code':1}):
                with self.subTest(change=change):
                    receipt.write_text(json.dumps(valid | change))
                    with self.assertRaises(ValueError):
                        verify_candidate(binary,receipt,SOURCE)

    def test_missing_success_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            binary, receipt = Path(folder)/'brewfs', Path(folder)/'build.json'
            binary.write_bytes(b'candidate')
            receipt.write_text(json.dumps(dict(source_commit=SOURCE, source_tree=TREE,
                                               candidate_patch_sha256=PATCH)))
            with self.assertRaises(ValueError):
                verify_candidate(binary,receipt,SOURCE)
