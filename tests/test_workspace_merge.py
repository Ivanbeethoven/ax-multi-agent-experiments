import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from bench.cluster.workspace_merge import run_merge


class WorkspaceMergeTests(unittest.TestCase):
    def test_operation_survives_lost_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operation.json"
            child, target = str(uuid.uuid4()), str(uuid.uuid4())
            with patch("bench.cluster.workspace_merge.subprocess.run", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    run_merge("graftfs", "redis://localhost", "test", child, target, path)
            before = path.read_bytes()
            operation = json.loads(before)["operation"]
            result = dict(request=dict(child=child, target=target, operation=operation), committed=True)
            with patch("bench.cluster.workspace_merge.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, json.dumps(result), "")) as call:
                self.assertEqual(run_merge("graftfs", "redis://localhost", "test", child, target, path), result)
                self.assertEqual(call.call_args.args[0][-1], operation)
            self.assertEqual(path.read_bytes(), before)

    def test_foreign_request_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operation.json"
            path.write_text(json.dumps(dict(identity={}, operation=str(uuid.uuid4()))))
            with patch("bench.cluster.workspace_merge.subprocess.run") as call:
                with self.assertRaises(ValueError):
                    run_merge("graftfs", "redis://localhost", "test", str(uuid.uuid4()), str(uuid.uuid4()), path)
                call.assert_not_called()

    def test_conflict_result_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operation.json"
            child, target = str(uuid.uuid4()), str(uuid.uuid4())
            def execute(command, **options):
                result = dict(request=dict(child=child, target=target, operation=command[-1]),
                              committed=False, conflicts=[dict(ino=2, reason="both modified")])
                return subprocess.CompletedProcess(command, 1, json.dumps(result), "conflict")
            with patch("bench.cluster.workspace_merge.subprocess.run", side_effect=execute):
                result = run_merge("graftfs", "redis://localhost", "test", child, target, path)
            self.assertFalse(result["committed"])
            self.assertEqual(len(result["conflicts"]), 1)

    def test_foreign_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = dict(request={}, committed=True)
            with patch("bench.cluster.workspace_merge.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, json.dumps(result), "")):
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    run_merge("graftfs", "redis://localhost", "test", str(uuid.uuid4()),
                              str(uuid.uuid4()), Path(tmp) / "operation.json")

    def test_non_object_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("bench.cluster.workspace_merge.subprocess.run", return_value=
                       subprocess.CompletedProcess([], 0, "[]", "")):
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    run_merge("graftfs", "redis://localhost", "test", str(uuid.uuid4()),
                              str(uuid.uuid4()), Path(tmp) / "operation.json")

    def test_malformed_operation_record_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operation.json"
            path.write_text("[]")
            with patch("bench.cluster.workspace_merge.subprocess.run") as call:
                with self.assertRaises(ValueError):
                    run_merge("graftfs", "redis://localhost", "test", str(uuid.uuid4()),
                              str(uuid.uuid4()), path)
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
