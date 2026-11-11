"""Offline checks: no listening socket, provider, mount, or real cgroup mutation."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1] / "experiments/unified-suite/pilot-aliyun/scripts"


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@unittest.skipUnless(sys.platform.startswith("linux"), "cloud entry points require Linux")
class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = module("bounded_relay_test", "session-relay-bounded.py")
        cls.capped = cls.m.dependency("gate_test", "qualify-session-shared-capped.py", cls.m.CAPPED_SHA)
        cls.relay = cls.m.dependency("relay_test", "session-relay.py", cls.m.RELAY_SHA)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.stop = threading.Event()
        self.control = self.m.Control(self.capped, self.output, self.stop)
        self.addCleanup(self.control.log.close)
        self.requests = []
        self.frames = b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
        self.config = dict(bind="127.0.0.1", port=18444, base_url="https://provider.invalid/v1",
                           api_key="never-log-provider-key", relay_key="r" * 40, model="deepseek-v4-flash",
                           max_requests=4, thinking_mode="disabled")

    def body(self, step=0):
        return dict(model="deepseek-v4-flash", max_tokens=512, stream=True,
                    messages=[dict(role="user", content="never-log-private-prompt-" + str(step))],
                    tools=[dict(type="function", function=dict(name="same"))])

    def expected_first(self):
        # Synthetic offline body only. The executable has no flag/config field
        # to replace its pinned original full-body SHA.
        body = self.body() | dict(thinking={"type": "disabled"})
        self.control.gate.first_sha = hashlib.sha256(json.dumps(body).encode()).hexdigest()

    def post(self, body):
        outer = self

        class Response:
            status = 200
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self):
                self.stream = io.BytesIO(outer.frames)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read1(self, size):
                return self.stream.read(size)

        class Opener:
            def open(self, request, timeout):
                outer.assertEqual(timeout, 180)
                outer.requests.append(request)
                return Response()

        with patch.object(self.capped.urllib.request, "build_opener", return_value=Opener()):
            handler = self.relay.handler(self.config, self.control.record, self.control.reserve,
                                         self.control.hooks.opener(self.relay))
        request = object.__new__(handler)
        raw = json.dumps(body).encode()
        request.headers = {"Authorization": "Bearer " + self.config["relay_key"], "Content-Length": str(len(raw))}
        request.path = "/v1/chat/completions"
        request.rfile, request.wfile = io.BytesIO(raw), io.BytesIO()
        statuses = []
        request.send_response = statuses.append
        request.send_header = lambda *_: None
        request.end_headers = lambda: None
        request.do_POST()
        return statuses, request.wfile.getvalue()

    def test_original_first_sha_is_fixed_and_mismatch_is_retained_without_upstream(self):
        self.assertEqual(self.control.gate.first_sha,
                         "3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537")
        self.assertEqual(self.post(self.body())[0], [502])
        self.assertEqual(self.requests, [])
        self.assertTrue(self.stop.is_set())
        result = self.control.finish()
        self.assertFalse(result["passed"])
        self.assertEqual(result["closed_reason"], "first_body_sha_mismatch")
        self.assertEqual(json.loads((self.output / "result.json").read_text()), result)

    def test_handler_and_qualified_opener_preserve_stream_and_enforce_four_requests(self):
        self.expected_first()
        for step in range(4):
            self.assertEqual(self.post(self.body(step)), ([200], self.frames))
        result = self.control.snapshot()
        self.assertIsNone(result["closed_reason"])
        self.assertEqual(result["recorded"], 4)
        self.assertEqual(self.post(self.body(4))[0], [429])
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.control.finish()["closed_reason"], "budget_exhausted")
        for request in self.requests:
            forwarded = json.loads(request.data)
            self.assertEqual(forwarded["thinking"], {"type": "disabled"})
            self.assertEqual(forwarded["max_tokens"], 512)
        retained = "".join(p.read_text() for p in self.output.iterdir())
        for secret in (self.config["api_key"], self.config["relay_key"], "never-log-private-prompt", "provider.invalid"):
            self.assertNotIn(secret, retained)

    def test_accepted_request_cannot_reserve_after_successful_finish(self):
        self.expected_first()
        self.assertEqual(self.post(self.body())[0], [200])
        accepted, release = threading.Event(), threading.Event()
        reserve = self.control.reserve
        responses, errors = [], []

        def delayed_reserve():
            accepted.set()
            if not release.wait(timeout=2):
                raise RuntimeError("offline reservation barrier timed out")
            return reserve()

        def pending_handler():
            try:
                responses.append(self.post(self.body(1)))
            except BaseException as error:
                errors.append(error)

        with patch.object(self.control, "reserve", delayed_reserve):
            thread = threading.Thread(target=pending_handler)
            thread.start()
            try:
                self.assertTrue(accepted.wait(timeout=2))
                result = self.control.finish()
                self.assertTrue(result["passed"])
                self.assertTrue(result["closed"])
            finally:
                release.set()
                thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(responses[0][0], [429])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.control.reserve())
        self.assertIsNone(self.control.gate.reason)
        self.assertEqual(self.control.finish(), result)
        self.assertEqual(json.loads((self.output / "result.json").read_text()), result)

    def test_server_error_after_completed_request_is_safe_failure(self):
        outer = self
        actual_control = self.m.Control
        closed = []

        def control(capped, output, stop):
            outer.control = actual_control(capped, output, stop)
            outer.addCleanup(outer.control.log.close)
            outer.expected_first()
            return outer.control

        class OfflineServer:
            # Exercise the real serve/handle_error route without a socket.
            def __init__(self, address, handler):
                self.server_port = address[1]

            def handle_request(self):
                outer.assertEqual(outer.post(outer.body())[0], [200])
                outer.assertFalse(outer.control.gate.in_flight)
                try:
                    raise RuntimeError("never-log-server-exception-provider-key")
                except RuntimeError:
                    self.handle_error(None, ("never-log-client-address", 0))

            def server_close(self):
                closed.append(True)

        dependencies = lambda name, filename, expected: self.capped if "capped" in filename else self.relay
        with patch.object(self.m, "dependency", side_effect=dependencies), \
                patch.object(self.m, "Control", side_effect=control), \
                patch.object(self.relay, "configuration", return_value=self.config), \
                patch.object(self.relay, "ThreadingHTTPServer", OfflineServer):
            result = self.m.serve(self.output / "unused-private-config", self.output / "serve", 30)
        self.assertEqual(closed, [True])
        self.assertFalse(result["passed"])
        self.assertTrue(result["closed"])
        self.assertEqual(result["closed_reason"], "server_error")
        self.assertEqual((result["reserved"], result["recorded"]), (1, 1))
        self.assertEqual(self.post(self.body(1))[0], [429])
        self.assertEqual(len(self.requests), 1)
        retained = "".join(path.read_text() for path in (self.output / "serve").iterdir())
        self.assertNotIn("never-log", retained)
        self.assertNotIn(self.config["api_key"], retained)
        self.assertNotIn(self.config["relay_key"], retained)

    def test_duplicate_and_incomplete_stream_latch_closed(self):
        self.expected_first()
        self.assertEqual(self.post(self.body())[0], [200])
        self.assertEqual(self.post(self.body())[0], [502])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.control.finish()["closed_reason"], "duplicate_body")

    def test_incomplete_stream_and_concurrency_fail_closed(self):
        self.expected_first()
        self.frames = b'data: {"choices":[]}\n\n'
        self.post(self.body())
        self.assertEqual(self.control.finish()["closed_reason"], "previous_sse_not_complete")
        self.assertEqual(self.post(self.body(1))[0], [429])
        self.assertEqual(len(self.requests), 1)
        with tempfile.TemporaryDirectory() as temporary:
            control = self.m.Control(self.capped, Path(temporary), threading.Event())
            try:
                self.assertTrue(control.reserve())
                self.assertFalse(control.reserve())
                self.assertEqual(control.finish()["closed_reason"], "concurrent_reservation")
            finally:
                control.log.close()

    def test_dependency_change_is_refused(self):
        with self.assertRaisesRegex(ValueError, "SHA differs"):
            self.m.dependency("changed", "session-relay.py", "0" * 64)


@unittest.skipUnless(sys.platform.startswith("linux"), "cloud entry points require Linux")
class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = module("bounded_worker_test", "session-worker-bounded.py")

    def test_explicit_timeout_and_output_cap_are_required_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for seconds in (None, 0, True, 901, 1.5):
                with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "total_timeout_seconds"):
                    self.m.validate(dict(action="resume", total_timeout_seconds=seconds), root / "out")
            config = root / "config.json"
            config.write_text(json.dumps(dict(provider={"experiment": {"models": {"deepseek-v4-flash": {}}}})))
            with self.assertRaisesRegex(ValueError, "512-token"):
                self.m.validate(dict(action="resume", total_timeout_seconds=205, config=str(config)), root / "out")

    def test_child_group_does_not_change_parent_limits_or_kill_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            slot = Path(temporary) / "ax-slot-1"
            slot.mkdir()
            for name, value in (("cpu.max", "200000 100000"), ("memory.max", "8589934592"),
                                ("cgroup.procs", "fuse-pid\n"), ("cgroup.kill", "")):
                (slot / name).write_text(value)
            real_read, real_mkdir, real_rmdir = Path.read_text, Path.mkdir, Path.rmdir

            def read(path, *args, **kwargs):
                return "0::/ax-slot-1\n" if str(path) == "/proc/self/cgroup" else real_read(path, *args, **kwargs)

            def mkdir(path, *args, **kwargs):
                real_mkdir(path, *args, **kwargs)
                if path.parent == slot:
                    for name, value in (("cgroup.kill", ""), ("cgroup.events", "populated 0\n"), ("cgroup.procs", "")):
                        (path / name).write_text(value)

            def rmdir(path):
                self.assertEqual(path.parent, slot)
                self.assertEqual((path / "cgroup.kill").read_text(), "1")
                self.assertFalse((path / "cpu.max").exists())
                for item in path.iterdir():
                    item.unlink()
                real_rmdir(path)

            with patch.object(self.m, "SLOT", slot), patch.object(self.m, "cgroup_limits", return_value=self.m.BUDGET), \
                    patch.object(Path, "read_text", read), patch.object(Path, "mkdir", mkdir), patch.object(Path, "rmdir", rmdir):
                group = self.m.Group()
                group.enter()
                self.assertEqual((group.path / "cgroup.procs").read_text(), str(os.getpid()))
                group.close()
            self.assertEqual((slot / "cpu.max").read_text(), "200000 100000")
            self.assertEqual((slot / "memory.max").read_text(), "8589934592")
            self.assertEqual((slot / "cgroup.procs").read_text(), "fuse-pid\n")
            self.assertEqual((slot / "cgroup.kill").read_text(), "")

    def test_interrupt_and_failed_cleanup_cannot_report_success(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                request = root / "request.json"
                request.write_text(json.dumps(dict(action="export", total_timeout_seconds=60)))
                group = Mock(path=root / "owned-child", budget=self.m.BUDGET)
                if cleanup_fails:
                    group.close.side_effect = RuntimeError("offline cleanup failure")
                process = Mock(pid=1234)
                process.wait.side_effect = [self.m.Interrupted(), 0]
                with patch.object(self.m, "Group", return_value=group), \
                        patch.object(self.m.subprocess, "Popen", return_value=process) as popen:
                    result = self.m.run(pin(request), root / "out")
                group.close.assert_called_once()
                self.assertFalse(result["passed"])
                self.assertEqual(result["error_type"], "Interrupted")
                self.assertEqual(result["descendants_cleared"], not cleanup_fails)
                self.assertEqual(popen.call_args.kwargs["preexec_fn"], group.enter)
                self.assertNotIn("stdout", popen.call_args.kwargs)
                self.assertNotIn("stderr", popen.call_args.kwargs)

    def test_populated_child_is_retained_after_cleanup_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            group = object.__new__(self.m.Group)
            group.path = Path(temporary)
            (group.path / "cgroup.events").write_text("populated 1\n")
            with patch.object(self.m.time, "monotonic", side_effect=[1, 7]), \
                    self.assertRaisesRegex(RuntimeError, "descendants remain"):
                group.close()
            self.assertEqual((group.path / "cgroup.kill").read_text(), "1")
            self.assertTrue(group.path.is_dir())

    def fixture(self, root, holds_stdout):
        workspace = root / "workspace"
        project = workspace / "repo"
        project.mkdir(parents=True)
        (project / "checkpoint.txt").write_bytes(b"ready\n")
        state = {}
        for name in ("data", "state", "cache", "config"):
            directory = workspace / name
            directory.mkdir()
            state[name] = str(directory)
        config = workspace / "opencode.json"
        config.write_text(json.dumps(dict(model="experiment/deepseek-v4-flash", provider={"experiment": {
            "options": {"baseURL": "http://127.0.0.1:18444/v1", "apiKey": "offline-never-used"},
            "models": {"deepseek-v4-flash": {"limit": {"context": 0, "output": 512}}}}})))
        checkpoint = root / "checkpoint.json"
        checkpoint.write_text(json.dumps(dict(session_id="ses_offline", resume_prompt="offline fixture",
                                             expected_final_sha256=hashlib.sha256(b"done\n").hexdigest())))
        binary = root / "fake-opencode"
        event = dict(type="tool_use", sessionID="ses_offline",
                     part=dict(callID="new-offline-tool", state=dict(status="completed")))
        binary.write_text("#!" + sys.executable + "\n" +
            "import json,os,subprocess,sys\nfrom pathlib import Path\n" +
            "Path('final.txt').write_bytes(b'done\\n')\n" +
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']" +
            ("" if holds_stdout else ",stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL") + ")\n" +
            "Path('offline-pids.json').write_text(json.dumps([os.getpid(),child.pid]))\n" +
            "print(" + repr(json.dumps(event)) + ",flush=True)\n")
        binary.chmod(0o700)
        request = root / "request.json"
        request.write_text(json.dumps(dict(action="resume", total_timeout_seconds=2,
            binary=pin(binary), workspace=str(workspace), project=str(project), config=str(config),
            relay_url="http://127.0.0.1:18444/v1", state=state, session_id="ses_offline", checkpoint=pin(checkpoint))))
        return request, project, event

    def test_native_json_is_live_and_descendants_end_on_success_and_timeout(self):
        # Run the real original session-worker and two real offline child
        # processes. Only the kernel cgroup adapter is replaced by an isolated
        # process-group test double; no cloud, FUSE, service or model is used.
        for holds_stdout in (False, True):
            with self.subTest(timeout=holds_stdout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                request, project, event = self.fixture(root, holds_stdout)
                harness = root / "harness.py"
                harness.write_text("""import importlib.util,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('bounded',sys.argv.pop(1))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class FakeGroup:
    def __init__(self):
        self.path=Path(sys.argv[-1]).with_name('fake-cgroup')
        self.path.mkdir();self.budget=m.BUDGET
    def enter(self):
        os.setsid();(self.path/'pid').write_text(str(os.getpid()))
    def close(self):
        try:os.killpg(int((self.path/'pid').read_text()),signal.SIGKILL)
        except ProcessLookupError:pass
m.Group=FakeGroup
raise SystemExit(m.main())
""")
                output = root / "out"
                process = subprocess.Popen([sys.executable, str(harness), str(BASE / "session-worker-bounded.py"),
                    "--request", str(request), "--sha256", pin(request)["sha256"], "--out", str(output)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    import select
                    ready, _, _ = select.select([process.stdout], [], [], 1.5)
                    self.assertTrue(ready, "first tool event was buffered until native phase exit")
                    self.assertEqual(json.loads(process.stdout.readline()), event)
                    if holds_stdout:
                        self.assertIsNone(process.poll(), "event must be visible while native worker is running")
                    tail, stderr = process.communicate(timeout=8)
                    self.assertEqual(tail, b"")
                    self.assertEqual(stderr, b"")
                    self.assertEqual(process.returncode, 1 if holds_stdout else 0)
                    receipt = json.loads((output / "bounded.json").read_text())
                    self.assertEqual(receipt["timed_out"], holds_stdout)
                    self.assertEqual(receipt["passed"], not holds_stdout)
                    self.assertTrue(receipt["descendants_cleared"])
                    self.assertEqual((output / "empty-home").is_dir(), True)
                    if not holds_stdout:
                        native = json.loads((output / "result.json").read_text())
                        self.assertEqual(native["completed_native_tools"], 1)
                    for pid in json.loads((project / "offline-pids.json").read_text()):
                        status = Path(f"/proc/{pid}/stat")
                        self.assertTrue(not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] == "Z")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    process.stdout.close()
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
