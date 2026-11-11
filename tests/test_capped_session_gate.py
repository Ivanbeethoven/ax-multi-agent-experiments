import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest


SCRIPT = (Path(__file__).resolve().parents[1] /
          "experiments/unified-suite/pilot-aliyun/scripts/qualify-session-shared-capped.py")


@unittest.skipUnless(sys.platform.startswith("linux"), "qualification is Linux/WSL only")
class CappedSessionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("capped_session", SCRIPT)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def request(self, step, *, tools=None):
        return SimpleNamespace(data=json.dumps(dict(model="deepseek-v4-flash", stream=True, max_tokens=512,
                                                    thinking={"type": "disabled"}, messages=[step],
                                                    tools=tools or ["same"])).encode())

    def stream(self, chunks):
        response = SimpleNamespace(status=200, headers={"Content-Type": "text/event-stream"},
                                   read1=lambda _: next(chunks, b""))
        import time
        stream = self.m.ObservedStream(response, time.monotonic())
        while stream.read1(65536):
            pass
        return stream

    def complete(self, gate):
        gate.stream = self.stream(iter((b'data: {"choices":[]}\r\n\r\n',
                                        b"data: [DO", b"NE]\r\n\r\n")))
        row = dict(outcome="complete", status=200, response_bytes=64)
        gate.observe(row)
        return row

    def test_four_distinct_complete_steps_then_hard_budget(self):
        request = self.request(0)
        gate = self.m.Gate(self.m.sha(request.data))
        for step in range(4):
            self.assertTrue(gate.reserve(step, step))
            self.assertTrue(gate.validate(self.request(step)))
            self.assertTrue(self.complete(gate)["stream_completion"]["done"])
        self.assertFalse(gate.reserve(4, 4))
        self.assertEqual(gate.summary()["closed_reason"], "budget_exhausted")
        self.assertEqual(gate.summary()["upstream_posts_attempted"], 4)

    def test_wrong_first_body_or_duplicate_never_reaches_upstream_twice(self):
        first = self.request(0)
        gate = self.m.Gate(self.m.sha(first.data))
        self.assertTrue(gate.reserve(0, 0))
        self.assertFalse(gate.validate(self.request(1)))
        self.assertEqual(gate.summary()["upstream_posts_attempted"], 0)
        self.assertEqual(gate.summary()["closed_reason"], "first_body_sha_mismatch")
        gate = self.m.Gate(self.m.sha(first.data))
        self.assertTrue(gate.reserve(0, 0))
        self.assertTrue(gate.validate(first))
        self.complete(gate)
        self.assertTrue(gate.reserve(1, 1))
        self.assertFalse(gate.validate(first))
        self.assertFalse(gate.reserve(2, 2))
        self.assertEqual(gate.summary()["upstream_posts_attempted"], 1)
        self.assertEqual(gate.summary()["closed_reason"], "duplicate_body")

    def test_followup_tool_schema_change_closes_gate(self):
        first = self.request(0)
        gate = self.m.Gate(self.m.sha(first.data))
        self.assertTrue(gate.reserve(0, 0))
        self.assertTrue(gate.validate(first))
        self.complete(gate)
        self.assertTrue(gate.reserve(1, 1))
        self.assertFalse(gate.validate(self.request(1, tools=["different"])))
        self.assertEqual(gate.summary()["upstream_posts_attempted"], 1)
        self.assertEqual(gate.summary()["closed_reason"], "tools_changed")

    def test_truncated_or_error_stream_and_transport_failure_close_gate(self):
        request = self.request(0)
        for chunks, row, reason in (
            ((b'data: {"choices":[]}\n\n',), dict(outcome="complete", status=200, response_bytes=24),
             "previous_sse_not_complete"),
            ((b'data: {"error":{"message":"mock"}}\n\ndata: [DONE]\n\n',),
             dict(outcome="complete", status=200, response_bytes=24), "previous_sse_not_complete"),
            ((), dict(outcome="transport-failed", status=None, response_bytes=0), "previous_upstream_not_complete"),
        ):
            gate = self.m.Gate(self.m.sha(request.data))
            self.assertTrue(gate.reserve(0, 0))
            self.assertTrue(gate.validate(request))
            gate.stream = self.stream(iter(chunks)) if chunks else None
            gate.observe(row)
            self.assertFalse(gate.reserve(1, 1))
            self.assertEqual(gate.summary()["closed_reason"], reason)

    def test_cold_tool_ids_match_native_event_export_and_database(self):
        with tempfile.TemporaryDirectory() as temp:
            w = Path(temp)
            db = w / "source/data/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            def part(call_id):
                return {"type": "tool", "callID": call_id, "state": {"status": "completed"}}
            def export(*parts):
                return {"messages": [{"parts": list(parts)}]}
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE part(session_id TEXT, data TEXT)")
                conn.executemany("INSERT INTO part VALUES ('sid', ?)",
                                 [(json.dumps(part(name)),) for name in ("old", "new")])
            events = w / "events"
            events.write_text(json.dumps(dict(sessionID="sid", type="tool_use", part=part("new"))) + "\n")
            self.assertEqual(self.m.calls_match(w, "sid", export(part("old")), events,
                                               export(part("old"), part("new"))), [self.m.sha(b"new")])
            with self.assertRaisesRegex(ValueError, "did not match"):
                self.m.calls_match(w, "sid", export(part("old")), events,
                                   export(part("old"), part("other")))

    def test_qualification_hooks_only_apply_to_pinned_source(self):
        for name in self.m.PINNED:
            self.m.pinned_source(SCRIPT.with_name(name))
        source = self.m.pinned_source(SCRIPT.with_name("qualify-session-shared-local.py"))
        compile(self.m.adapt(source), "reviewed qualification", "exec")
        with self.assertRaises(ValueError):
            self.m.adapt(source.replace("    a=p.parse_args()", "    a=p.parse_args([])"))

    def test_host_symlink_and_hardlink_are_refused_before_export(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            workspace = parent / "workspace"
            workspace.mkdir()
            protected = parent / "host"
            protected.write_bytes(b"outside")
            output = workspace / "final.txt"
            output.symlink_to(protected)
            with self.assertRaisesRegex(ValueError, "link or special file"):
                self.m.safe_resumed_tree(workspace)
            output.unlink()
            output.hardlink_to(protected)
            with self.assertRaisesRegex(ValueError, "link or special file"):
                self.m.safe_resumed_tree(workspace)
            self.assertEqual(protected.read_bytes(), b"outside")


if __name__ == "__main__":
    unittest.main()
