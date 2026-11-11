#!/usr/bin/env python3
"""One bounded real C4 native resume, using the pinned local JuiceFS workflow."""
import ctypes
import ctypes.util
from contextlib import closing
import errno
import hashlib
import json
import os
from pathlib import Path
import resource
import select
import socket
import socketserver
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.request
import uuid


HERE = Path(__file__).resolve()
BASE = HERE.parent
PINNED = {
    "qualify-session-shared-local.py": "fab94ad399624681f774d7dcf46791f839b2d44eb8f3e004780f0451df48ea4d",
    "session-worker.py": "bc6030cb04942b18cf0987ff5e319a710db17c6af809c8c60eb8fed16d6415f0",
    "session-relay.py": "c77d3c2081493245be946951a6d9cc554e8a2871baf65ab8d69e3d881ef8be87",
    "prepare-session-input.py": "7523756166432750a6a2def1f20519b34a04f8d0f7b33f8cd0f4d2240ad8d163",
}
PROVIDER_SHA = "a0706d9c28a98aec0e363288f1a411c699987c8e451e7bb794e72db56bc24e1f"
FIRST_SHA = "3a792e6a0068fc3e73332289cb8be92e16315ae8cb4a2c496ed7d2d630631537"
ORIGINAL_STARTED_SHA = "be04e08b1c6d973f63be01e7e6c5811d1105d3a46f5898120e9f70134970bebf"
ORIGINAL_STARTED = BASE.parent / "evidence/preparation/session-shared-juicefs-02/native-resume/started.json"
LIVE_PROBE = BASE.parent / "evidence/preparation/opencode-upstream-once-512-01/result.json"
LIVE_PROBE_SHA = "a3c590155228dc73e684be8a8f8af6b43c7fd4fa14b205c79c34790e075b9e6e"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
NOBODY = 65534
MAX_STEPS = 4
WALL_SECONDS = 205
CGROUP_ROOT = Path("/sys/fs/cgroup")
LIMITS = {"cpu.max": "100000 100000", "memory.max": str(768 * 1024**2),
          "memory.swap.max": "0", "pids.max": "64"}
DENY = ("mount", "umount2", "pivot_root", "move_mount", "open_tree", "fsopen", "fsconfig",
        "fsmount", "mount_setattr", "setns", "unshare", "ptrace", "process_vm_readv",
        "process_vm_writev", "bpf", "perf_event_open", "keyctl", "add_key", "request_key")


def require(test, message):
    if not test:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pinned_source(path):
    data = path.read_bytes().replace(b"\r\n", b"\n")
    require(sha(data) == PINNED[path.name], "original qualification dependency SHA differs")
    return data.decode("utf-8")


def original_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(pinned_source(path), str(path), "exec"), module.__dict__)
    return module


def pin_json(path, digest):
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    require(sha(raw) == digest, "original JSON evidence SHA differs")
    return json.loads(raw)


def private_write(path, value):
    with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as f:
        json.dump(value, f)
        f.write("\n")


class Gate:
    """Allow new successful continuation steps; a failure or duplicate latches closed."""

    def __init__(self, first_sha=FIRST_SHA, on_close=None):
        self.lock = threading.Lock()
        self.first_sha = first_sha
        self.sent = set()
        self.in_flight = False
        self.rejections = []
        self.reason = None
        self.tools_sha = None
        self.on_close = on_close
        self.stream = None

    def reject(self, reason):
        if self.reason is None:
            self.reason = reason
            if self.on_close is not None:
                self.on_close()
        if len(self.rejections) < 16:
            self.rejections.append(reason)
        return False

    def reserve(self, count, closed):
        with self.lock:
            if self.reason:
                return self.reject(self.reason)
            if count >= MAX_STEPS:
                return self.reject("budget_exhausted")
            if count != closed or self.in_flight:
                return self.reject("concurrent_reservation")
            self.in_flight = True
            self.stream = None
            return True

    def validate(self, request):
        with self.lock:
            try:
                data = request.data
                value = json.loads(data)
                digest = sha(data)
                tools = sha(json.dumps(value["tools"], separators=(",", ":")).encode())
                if digest in self.sent:
                    return self.reject("duplicate_body")
                if not self.in_flight or len(self.sent) >= MAX_STEPS:
                    return self.reject("unreserved_or_budget_exhausted")
                if (len(data) > 512 * 1024 or value.get("model") != "deepseek-v4-flash"
                        or type(value.get("max_tokens")) is not int or value["max_tokens"] != 512
                        or value.get("stream") is not True
                        or value.get("thinking") != {"type": "disabled"}):
                    return self.reject("unexpected_model_limit_or_stream_shape")
                if not self.sent and digest != self.first_sha:
                    return self.reject("first_body_sha_mismatch")
                if self.tools_sha is not None and tools != self.tools_sha:
                    return self.reject("tools_changed")
                self.tools_sha = tools
                self.sent.add(digest)
                return True
            except (TypeError, ValueError, KeyError):
                return self.reject("invalid_body_shape")

    def observe(self, row):
        with self.lock:
            self.in_flight = False
            row["stream_completion"] = self.stream.summary() if self.stream else None
            if row["outcome"] != "complete" or row["status"] != 200 or row["response_bytes"] <= 0:
                self.reject("previous_upstream_not_complete")
            elif self.stream is None or not self.stream.valid():
                self.reject("previous_sse_not_complete")

    def summary(self):
        with self.lock:
            return dict(upstream_posts_attempted=len(self.sent), budget=MAX_STEPS,
                        first_forwarded_body_sha256=self.first_sha, closed_reason=self.reason,
                        rejection_reasons=list(self.rejections), automatic_retry=False)


class ObservedStream:
    """Pass bytes and headers through unchanged; retain only SSE event counts."""

    def __init__(self, response, start):
        self.response, self.start = response, start
        self.status, self.headers = response.status, response.headers
        self.headers_seconds = round(time.monotonic() - start, 6)
        self.first_sse_seconds = None
        self.tail = b""
        self.data_frames = 0
        self.done = self.overlong = self.invalid = False
        self.sse = self.headers.get("Content-Type", "").split(";", 1)[0].lower() == "text/event-stream"

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        return self.response.__exit__(*args)

    def read1(self, size):
        data = self.response.read1(size)
        if not self.sse or self.overlong:
            return data
        rows = (self.tail + data).split(b"\n")
        self.tail = rows.pop()
        if len(self.tail) > 512 * 1024 or any(len(row) > 512 * 1024 for row in rows):
            self.overlong = True
            self.tail = b""
            return data
        for line in rows:
            if not line.startswith(b"data:"):
                continue
            event = line[5:].strip()
            if event == b"[DONE]":
                self.done = True
            elif event and not self.done:
                try:
                    value = json.loads(event)
                    if not isinstance(value, dict) or "error" in value or not (
                            isinstance(value.get("choices"), list) or isinstance(value.get("usage"), dict)):
                        self.invalid = True
                        continue
                except (ValueError, RecursionError):
                    self.invalid = True
                    continue
                self.data_frames += 1
                if self.first_sse_seconds is None:
                    self.first_sse_seconds = round(time.monotonic() - self.start, 6)
        return data

    def valid(self):
        return self.sse and self.done and self.data_frames > 0 and not (self.overlong or self.invalid)

    def summary(self):
        return dict(event_stream=self.sse, data_frames=self.data_frames, done=self.done,
                    headers_seconds=self.headers_seconds, first_sse_seconds=self.first_sse_seconds,
                    event_exceeded_bound=self.overlong, invalid_event=self.invalid)


def pump(left, right):
    with right:
        left.settimeout(190)
        right.settimeout(190)
        peers = {left: right, right: left}
        while peers:
            ready, _, _ = select.select(list(peers), [], [], 190)
            if not ready:
                return
            for peer in ready:
                chunk = peer.recv(32768)
                if chunk:
                    peers[peer].sendall(chunk)
                else:
                    try:
                        peers[peer].shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    del peers[peer]


def quiet_handler(open_peer):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                pump(self.request, open_peer())
            except OSError:
                pass
    return Handler


class ThreadUnix(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class ThreadTCP(socketserver.ThreadingTCPServer):
    daemon_threads = True


def unix_socket(path):
    out = socket.socket(socket.AF_UNIX)
    try:
        out.connect(path)
        return out
    except BaseException:
        out.close()
        raise


def rlimits():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))


def seccomp():
    lib = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    context = lib.seccomp_init(0x7FFF0000)
    require(context, "seccomp filter initialization failed")
    fd = os.memfd_create("c4-seccomp", os.MFD_CLOEXEC)
    try:
        for name in DENY:
            number = lib.seccomp_syscall_resolve_name(name.encode())
            require(number >= 0 and lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) == 0,
                    "required sandbox syscall filter unavailable")
        require(lib.seccomp_export_bpf(context, fd) == 0, "sandbox filter export failed")
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise
    finally:
        lib.seccomp_release(context)


def run_cmd(*args):
    return subprocess.run(args, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def enter(scratch, group, parent_net):
    rlimits()
    require(os.readlink("/proc/self/ns/net") != parent_net, "shared network namespace")
    (group / "cgroup.procs").write_text(str(os.getpid()))
    run_cmd("/usr/sbin/ip", "link", "set", "lo", "up")
    for version in ("-4", "-6"):
        require(not json.loads(run_cmd("/usr/sbin/ip", "-j", version, "route", "show", "table", "main").stdout),
                "sandbox has an external route")
    require([x["ifname"] for x in json.loads(run_cmd("/usr/sbin/ip", "-j", "link").stdout)] == ["lo"],
            "sandbox has a non-loopback interface")
    launch = json.loads((scratch / "launch.json").read_bytes())
    worker, binary, home = (launch[k] for k in ("workspace", "argv", "home"))
    fd = seccomp()
    try:
        command = ["/usr/bin/setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs",
                   "/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--share-net", "--disable-userns",
                   "--cap-drop", "ALL", "--die-with-parent", "--new-session", "--clearenv", "--seccomp", str(fd),
                   "--uid", "65534", "--gid", "65534", "--ro-bind", "/usr", "/usr",
                   "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                   "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        if launch["port"] is not None:
            command += ["--ro-bind", str(scratch / "bridge.sock"), "/run/ax-c4-bridge.sock"]
        command += [
                   "--ro-bind", str(scratch / "entry.py"), "/run/ax-c4-entry.py",
                   "--ro-bind", str(scratch / "sandbox.json"), "/run/ax-c4-sandbox.json",
                   "--ro-bind", str(scratch / "binary"), binary[0],
                   "--bind", str(scratch / "workspace"), worker, "--bind", str(scratch / "home"), home,
                   "--chdir", launch["cwd"], "/usr/bin/python3", "-B", "/run/ax-c4-entry.py", "__inside"]
        return subprocess.run(command, env=ENV, pass_fds=(fd,)).returncode
    finally:
        os.close(fd)


def inside():
    launch = json.loads(Path("/run/ax-c4-sandbox.json").read_bytes())
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(os.getuid() == NOBODY and os.getgid() == NOBODY and status["NoNewPrivs"].strip() == "1"
            and all(int(status[key].strip(), 16) == 0 for key in ("CapEff", "CapPrm", "CapBnd")),
            "unexpected sandbox process privileges")
    require(not any(Path(path).exists() for path in ("/mnt/c", "/root", "/sys", "/etc/shadow", "/home/hxy/.ssh",
                                                     "/home/hxy/.aws", "/home/hxy/.config", "/run/WSL")),
            "host credential path visible in sandbox")
    require(not any(Path(launch["home"]).iterdir()), "OpenCode HOME is not empty")
    if launch["port"] is None:
        return subprocess.run(launch["argv"], cwd=launch["cwd"], env=launch["env"]).returncode
    print(json.dumps(dict(type="sandbox_guard", uid=NOBODY, gid=NOBODY, no_new_privileges=True,
                          zero_caps=True, only_loopback=True, empty_home=True,
                          host_root_and_credentials_hidden=True)), flush=True)
    with ThreadTCP(("127.0.0.1", launch["port"]),
                   quiet_handler(lambda: unix_socket("/run/ax-c4-bridge.sock"))) as bridge:
        threading.Thread(target=bridge.serve_forever, daemon=True).start()
        try:
            return subprocess.run(launch["argv"], cwd=launch["cwd"], env=launch["env"]).returncode
        finally:
            bridge.shutdown()


class Sandbox:
    def __init__(self, port, workspace, out):
        self.port, self.workspace, self.out = port, Path(workspace), Path(out)
        self.mounts = []
        self.group = self.temp = self.bridge = self.bridge_thread = self.timer = self.process = None
        self.deadline = False
        self.closed = False

    def bind(self, src, dest):
        (dest.mkdir if src.is_dir() else dest.touch)()
        run_cmd("/usr/bin/mount", "--bind", str(src), str(dest))
        self.mounts.append(dest)

    def popen(self, argv, **kw):
        current = Path(kw["env"]["HOME"])
        require(current.is_dir() and not list(current.iterdir()), "expected original empty HOME")
        os.chown(current, NOBODY, NOBODY)
        self.temp = tempfile.TemporaryDirectory(prefix="c4-native-capped-", dir="/var/tmp")
        scratch = Path(self.temp.name)
        scratch.chmod(0o711)
        self.group = CGROUP_ROOT / ("ax-c4-capped-" + uuid.uuid4().hex)
        self.group.mkdir(mode=0o700)
        for name, val in LIMITS.items():
            field = self.group / name
            field.write_text(val)
            require(field.read_text().strip() == val, "sandbox cgroup limit did not apply")
        self.bind(Path(argv[0]), scratch / "binary")
        self.bind(HERE, scratch / "entry.py")
        self.bind(self.workspace, scratch / "workspace")
        self.bind(current, scratch / "home")
        if self.port is not None:
            bridge_path = scratch / "bridge.sock"
            self.bridge = ThreadUnix(str(bridge_path), quiet_handler(
                lambda: socket.create_connection(("127.0.0.1", self.port), timeout=5)))
            bridge_path.chmod(0o600)
            os.chown(bridge_path, NOBODY, NOBODY)
            self.bridge_thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
            self.bridge_thread.start()
        launch = dict(argv=argv, env=kw.pop("env"), cwd=str(kw.pop("cwd")), home=str(current),
                      port=self.port, workspace=str(self.workspace))
        private_write(scratch / "launch.json", launch)
        private_write(scratch / "sandbox.json", launch)
        os.chown(scratch / "sandbox.json", NOBODY, NOBODY)
        parent_net = os.readlink("/proc/self/ns/net")
        self.timer = threading.Timer(WALL_SECONDS, self.timeout)
        self.timer.start()
        self.process = subprocess.Popen(["/usr/bin/unshare", "--net", "--fork", "--kill-child=SIGKILL",
                                         sys.executable, "-B", str(HERE), "__enter", str(scratch),
                                         str(self.group), parent_net], env=ENV, stdin=subprocess.DEVNULL, **kw)
        return self.process

    def timeout(self):
        self.deadline = True
        self.kill()

    def kill(self):
        if self.group is not None:
            try:
                (self.group / "cgroup.kill").write_text("1")
            except FileNotFoundError:
                pass

    def close(self):
        if self.closed:
            return
        if self.timer:
            self.timer.cancel()
        if self.group is not None and self.group.exists():
            self.kill()
            for _ in range(20):
                if "populated 0" in (self.group / "cgroup.events").read_text():
                    break
                time.sleep(.05)
            require("populated 0" in (self.group / "cgroup.events").read_text(), "sandbox children remain")
            self.group.rmdir()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.bridge:
            if self.bridge_thread and self.bridge_thread.is_alive():
                self.bridge.shutdown()
                self.bridge_thread.join(timeout=2)
            self.bridge.server_close()
        for path in reversed(self.mounts):
            run_cmd("/usr/bin/umount", str(path))
        if self.temp:
            self.temp.cleanup()
        self.closed = True


def safe_resumed_tree(workspace):
    count = total = 0
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        for name in dirs + files:
            s = (Path(directory) / name).lstat()
            count += 1
            require(count <= 10_000, "resumed workspace has too many entries")
            require(stat.S_ISDIR(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink == 1),
                    "resumed workspace includes a link or special file")
            if stat.S_ISREG(s.st_mode):
                total += s.st_size
                require(total <= 64 * 1024**2, "resumed workspace exceeds local check limit")


def calls_match(workspace, session_id, previous, event_path, final_export):
    before = {p.get("callID") for m in previous["messages"] for p in m["parts"] if p.get("type") == "tool"}
    def completed(part):
        return part.get("type") == "tool" and part.get("state", {}).get("status") == "completed"
    current = {p.get("callID") for m in final_export["messages"] for p in m["parts"] if completed(p)} - before
    events = set()
    for line in event_path.read_text().splitlines():
        if line.startswith("{"):
            event = json.loads(line)
            part = event.get("part", {})
            if event.get("type") == "tool_use" and event.get("sessionID") == session_id and completed(part):
                events.add(part.get("callID"))
    db = workspace / "source/data/opencode/opencode.db"
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as c:
        require(c.execute("PRAGMA quick_check").fetchall() == [("ok",)], "cold resumed SQLite check failed")
        db_calls = {row[0] for row in c.execute(
            "SELECT json_extract(data,'$.callID') FROM part WHERE session_id=? AND "
            "json_extract(data,'$.type')='tool' AND json_extract(data,'$.state.status')='completed'", (session_id,))}
    require(current and current == events == db_calls - before and None not in current,
            "native completed tool IDs did not match cold export and DB")
    return [sha(s.encode()) for s in sorted(current)]


class Hooks:
    def __init__(self):
        self.gate = Gate(on_close=self.stop_sandbox)
        self.sandbox = None
        self.port = None

    def preflight(self, args):
        require(args.resume and args.execute_once and args.thinking_mode == "disabled",
                "live capped qualification requires --resume --thinking-mode disabled --execute-once")
        require(os.geteuid() == 0 and os.readlink("/proc/self/ns/mnt") != args.parent_mount_namespace,
                "root in a private mount namespace is required")
        roots = [row.split(" - ", 1)[0].split() for row in Path("/proc/self/mountinfo").read_text().splitlines()]
        require(all(not any(token.startswith("shared:") for token in row[6:])
                    for row in roots if row[4] in ("/", "/srv", "/srv/ax-pilot-session", "/var", "/var/tmp")),
                "mount propagation is not private")
        require(sha(args.provider_config.read_bytes()) == PROVIDER_SHA, "not the original provider config")
        probe = pin_json(LIVE_PROBE, LIVE_PROBE_SHA)
        require(probe["passed"] is True and probe["provider_config_sha256"] == PROVIDER_SHA
                and probe["request"]["forwarded_sha256"] == FIRST_SHA, "not the original complete request proof")
        require(CGROUP_ROOT.is_dir() and Path("/usr/bin/bwrap").is_file(), "cgroup v2 and bubblewrap required")

    def stop_sandbox(self):
        if self.sandbox is not None:
            self.sandbox.kill()

    def settings(self, settings, workspace):
        model = settings["provider"]["experiment"]["models"]["deepseek-v4-flash"]
        require(set(model) == {"name"}, "not the original model declaration")
        model["limit"] = {"context": 0, "output": 512}
        for directory, dirs, files in os.walk(workspace, followlinks=False):
            os.chown(directory, NOBODY, NOBODY, follow_symlinks=False)
            for name in dirs + files:
                os.chown(Path(directory) / name, NOBODY, NOBODY, follow_symlinks=False)

    def opener(self, relay):
        upstream = urllib.request.build_opener(relay.NoRedirect())
        def open_one(request, timeout):
            require(timeout == 180, "original urllib timeout changed")
            if not self.gate.validate(request):
                raise OSError("capped resume request rejected locally")
            start = time.monotonic()
            response = upstream.open(request, timeout=timeout)
            self.gate.stream = ObservedStream(response, start)
            return self.gate.stream
        return types.SimpleNamespace(open=open_one)

    def worker(self, worker):
        actual = worker.run
        outer = self
        def run(request, output):
            resuming = request["action"] == "resume"
            require(request["action"] in ("resume", "export"), "unexpected native phase")
            require(not resuming or outer.sandbox is None, "second native run refused")
            sandbox = Sandbox(outer.port if resuming else None, request["workspace"], output)
            if resuming:
                outer.sandbox = sandbox
            original = pin_json(ORIGINAL_STARTED, ORIGINAL_STARTED_SHA)
            def launch(argv, **kw):
                expected = original["argv"] if resuming else [original["argv"][0], "export", request["session_id"]]
                require(argv == expected and str(kw["cwd"]) == original["session_state"]["project"],
                        "original native argv or working directory differs")
                proc = sandbox.popen(argv, **kw)
                def wait():
                    code = proc.wait()
                    # End all descendant writes before original worker code opens any task output.
                    sandbox.close()
                    if code == 0:
                        safe_resumed_tree(Path(request["workspace"]))
                    return code
                return types.SimpleNamespace(stdout=proc.stdout, pid=proc.pid, wait=wait)
            # Keep native events in the private output files; do not print raw tool data.
            with open(os.devnull, "wb") as sink:
                worker.subprocess = types.SimpleNamespace(Popen=launch, PIPE=subprocess.PIPE)
                worker.sys = types.SimpleNamespace(stdout=types.SimpleNamespace(buffer=sink))
                try:
                    result = actual(request, output)
                finally:
                    worker.subprocess, worker.sys = subprocess, sys
                    sandbox.close()
            require(not sandbox.deadline, "sandbox wall clock limit reached")
            return result
        worker.run = run

    def final(self, worker, native, workspace, output):
        before = json.loads((output / "cold-export/export.json").read_bytes())
        after = worker.run(native, output / "cold-final-export")
        from bench.session_check import check_after
        prefix = json.loads((output / "session-prefix.json").read_bytes())
        check_after(prefix, after, True)
        call_shas = calls_match(workspace, native["session_id"], before, output / "native-resume/stdout", after)
        with (output / "native-resume/stdout").open() as f:
            proof = json.loads(f.readline())
        require(proof.get("type") == "sandbox_guard" and proof["empty_home"], "missing sandbox guard")
        require(self.gate.reason is None and 1 <= len(self.gate.sent) <= MAX_STEPS,
                "the bounded native model sequence did not close successfully")
        return dict(cold_sqlite_tool_matches=len(call_shas), completed_call_sha256=call_shas, sandbox=proof,
                    cgroup_limits=LIMITS, native_wall_limit_seconds=WALL_SECONDS,
                    cap=512, original_urllib_timeout_seconds=180)


def adapt(text):
    """Keep the known runner; expose only these reviewed qualification hooks."""
    changes = (
        ("    spec=importlib.util.spec_from_file_location(name,file)\n    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result",
         "    return pinned_module(name,file)"),
        ("    a=p.parse_args()", "    p.add_argument('--execute-once',action='store_true')\n    a=p.parse_args()\n    hooks.preflight(a)"),
        ("    shutil.copyfile(Path(__file__),a.out/'executed-source.py')",
         "    (a.out/'executed-source.py').write_text(adapted_source)"),
        ("        script_sha256=digest(Path(__file__)),thinking_mode=", "        script_sha256=digest(Path(__file__)),adapted_sha256=digest(a.out/'executed-source.py'),thinking_mode="),
        ("    checkpoint=load(a.checkpoint_directory/'checkpoint.json')", "    checkpoint=load(a.checkpoint_directory/'checkpoint.json')\n    write_new(a.out/'session-prefix.json',checkpoint['session_prefix'])"),
        ("    worker=module('local_shared_session_worker',Path(__file__).with_name('session-worker.py'))",
         "    worker=module('local_shared_session_worker',Path(__file__).with_name('session-worker.py'))\n    hooks.worker(worker)"),
        ("            calls.append(row)", "            hooks.gate.observe(row)\n            calls.append(row)"),
        ("            if reserved>=16:return False", "            if not hooks.gate.reserve(reserved,len(calls)):return False"),
        ("max_requests=16,thinking_mode=a.thinking_mode", "max_requests=4,thinking_mode=a.thinking_mode"),
        ("relay.handler(relay_config,record,reserve)", "relay.handler(relay_config,record,reserve,opener=hooks.opener(relay))"),
        ("    url='http://127.0.0.1:'+str(server.server_port)+'/v1'", "    hooks.port=server.server_port\n    url='http://127.0.0.1:'+str(server.server_port)+'/v1'"),
        ("            os.fchmod(stream.fileno(),0o600);json.dump(settings,stream)",
         "            os.fchmod(stream.fileno(),0o600)\n            hooks.settings(settings,workspace)\n            json.dump(settings,stream)"),
        ("            result.update(cold_final_bytes_equal=True,cold_full_resumed_workspace_equal=True)",
         "            result['capped_native_proof']=hooks.final(worker,native,workspace,a.out)\n            if prep.tree(original)!=load(a.build_root/'session-checkpoint-package-01/source-tree.json'):\n                raise ValueError('Original checkpoint tree changed')\n            result.update(cold_final_bytes_equal=True,cold_full_resumed_workspace_equal=True,original_checkpoint_unchanged=True)"),
        ("        write_new(a.out/'result.json',result);print(json.dumps(result))",
         "        result['model_attempt_control']=hooks.gate.summary()\n        write_new(a.out/'result.json',result);print(json.dumps(result))"),
    )
    for old, new in changes:
        require(text.count(old) == 1, "original qualification hook no longer unique")
        text = text.replace(old, new, 1)
    return text


def main():
    if sys.argv[1:2] == ["__enter"]:
        return enter(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    if sys.argv[1:2] == ["__inside"]:
        return inside()
    old = BASE / "qualify-session-shared-local.py"
    for filename in PINNED:
        pinned_source(BASE / filename)
    text = adapt(pinned_source(old))
    module = dict(__name__="capped_qualification", __file__=str(old), hooks=Hooks(), adapted_source=text,
                  pinned_module=original_module)
    exec(compile(text, str(old), "exec"), module)
    module["main"]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
