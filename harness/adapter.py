"""Bounded JSON-over-stdin adapter; never silently substitutes a storage system.

Commands are operator-provided argv arrays, not shell snippets. An adapter process
receives one request and must emit one JSON object. Full command output remains in
private local log files; callers receive only bounded, structured results.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid


STATUSES = {"ok", "error", "unsupported", "rejected"}
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
OUTPUT_PATH_KEYS = {"output_path", "output_dir", "workspace_path", "workspace_dir",
                    "target_path", "target_dir", "destination_path", "destination_dir", "path"}
INPUT_PATH_KEYS = {"input_path", "input_dir", "source_path", "source_dir", "parent_path"}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP alone does not make terminate() recursive.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, timeout=15)
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Adapter:
    """One adapter instance may be called concurrently by scenario workers."""

    def __init__(self, config, *, log_dir=None, workspace_root=None, artifact_root=None):
        if isinstance(config, (str, os.PathLike)):
            config_path = Path(config).resolve()
            self.config = json.loads(config_path.read_text(encoding="utf-8"))
            self.config_dir = config_path.parent
        else:
            self.config = dict(config)
            self.config_dir = Path.cwd()
        if self.config.get("schema_version") != 1:
            raise ValueError("adapter schema_version must be 1")
        if not isinstance(self.config.get("operations", {}), dict):
            raise ValueError("adapter operations must be an object")
        self.baseline_id = self.config.get("baseline_id")
        self.scope = self.config.get("execution_scope", "real")
        if self.scope not in {"real", "local_reference"}:
            raise ValueError("execution_scope must be real or local_reference")
        if (self.baseline_id == "LOCAL_REFERENCE") != (self.scope == "local_reference"):
            raise ValueError("LOCAL_REFERENCE must be explicitly labelled local_reference")
        self.log_dir = Path(log_dir or tempfile.mkdtemp(prefix="ax-adapter-logs-")).resolve()
        _private_directory(self.log_dir)
        root = workspace_root or self.config.get("workspace_root")
        self.workspace_root = Path(root).resolve() if root else None
        # Only the runner supplies this path; an adapter config cannot broaden it.
        self.artifact_root = Path(artifact_root).resolve() if artifact_root else None
        self.allowed_input_roots = [Path(p).resolve() for p in self.config.get("allowed_input_roots", [])]
        self._lock = threading.Lock()
        self._counter = 0

    def supports(self, operation):
        spec = self.config.get("operations", {}).get(operation)
        return (self.config.get("state") == "configured" and isinstance(spec, dict)
                and isinstance(spec.get("argv"), list) and bool(spec["argv"]))

    def _check_paths(self, value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "artifact_output_dir":
                    if not isinstance(item, str) or not Path(item).is_absolute() or self.artifact_root is None:
                        raise ValueError("artifact output requires the runner's absolute root")
                    path = Path(item).resolve()
                    if (not _inside(path, self.artifact_root) or path == self.artifact_root
                            or (self.workspace_root is not None and (_inside(path, self.workspace_root)
                                or _inside(self.workspace_root, path)))
                            or _inside(path, self.log_dir) or _inside(self.log_dir, path)):
                        raise ValueError("artifact output must be separate from workload and command logs")
                if key in OUTPUT_PATH_KEYS | INPUT_PATH_KEYS and item is not None:
                    if not isinstance(item, str) or not Path(item).is_absolute():
                        raise ValueError("request path must be absolute")
                    path = Path(item).resolve()
                    roots = ([self.workspace_root] if self.workspace_root else [])
                    if key in INPUT_PATH_KEYS:
                        roots += self.allowed_input_roots
                    if not any(_inside(path, root) for root in roots):
                        raise ValueError("request path is outside the configured workspace/input roots")
                    if _inside(path, self.log_dir) or _inside(self.log_dir, path):
                        raise ValueError("workspace paths must be separate from adapter logs")
                self._check_paths(item)
        elif isinstance(value, list):
            for item in value:
                self._check_paths(item)

    def call(self, operation, payload, timeout_s=None):
        started = time.monotonic()
        with self._lock:
            self._counter += 1
            request_id = f"{self._counter:06d}-{uuid.uuid4().hex[:12]}"
        result = {"status": "error", "operation": operation, "request_id": request_id,
                  "baseline_id": self.baseline_id, "scope": self.scope, "data": None,
                  "evidence": None, "telemetry": None, "error": None, "logs": None,
                  "duration_s": None}

        def finish(status, error=None):
            result.update(status=status, error=error, duration_s=time.monotonic() - started)
            return result

        if not isinstance(operation, str) or not operation:
            return finish("rejected", "invalid_operation")
        if not self.supports(operation):
            return finish("unsupported", "operation_not_configured")
        if not isinstance(payload, dict):
            return finish("rejected", "payload_must_be_object")
        try:
            self._check_paths(payload)
        except (ValueError, OSError):
            return finish("rejected", "request_path_not_allowed")
        spec = self.config["operations"][operation]
        argv = spec["argv"]
        if not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv):
            return finish("rejected", "argv_must_be_nonempty_strings")
        timeout = timeout_s if timeout_s is not None else spec.get("timeout_s", self.config.get("default_timeout_s", 300))
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            return finish("rejected", "timeout_must_be_positive_finite_seconds")
        cwd = spec.get("cwd", self.config.get("command_cwd"))
        if cwd is not None:
            cwd = Path(cwd)
            if not cwd.is_absolute():
                cwd = self.config_dir / cwd
            cwd = str(cwd.resolve())
        request = {"schema_version": 1, "operation": operation, "payload": payload,
                   "workspace_root": str(self.workspace_root) if self.workspace_root else None,
                   "request_id": request_id, "baseline_id": self.baseline_id}
        try:
            request_bytes = json.dumps(request, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            return finish("rejected", "request_is_not_json")
        stdout_path = self.log_dir / f"{request_id}.stdout.json"
        stderr_path = self.log_dir / f"{request_id}.stderr.log"
        result["logs"] = {"stdout": str(stdout_path), "stderr": str(stderr_path)}
        process = None
        try:
            with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
                if os.name != "nt":
                    stdout_path.chmod(0o600)
                    stderr_path.chmod(0o600)
                kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                           stdout=stdout_file, stderr=stderr_file,
                                           shell=False, **kwargs)
                try:
                    process.communicate(input=request_bytes, timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        _kill_tree(process)
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=15)
                    return finish("error", "adapter_timeout")
            result["exit_code"] = process.returncode
            if process.returncode != 0:
                return finish("error", "adapter_nonzero_exit")
            if stdout_path.stat().st_size > MAX_RESPONSE_BYTES:
                return finish("error", "adapter_response_too_large")
            try:
                response = json.loads(stdout_path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            except (UnicodeError, ValueError):
                return finish("error", "adapter_invalid_json")
            if not isinstance(response, dict) or response.get("status") not in STATUSES:
                return finish("error", "adapter_invalid_response")
            if response.get("scope") != self.scope:
                return finish("error", "adapter_scope_mismatch")
            if response["status"] == "ok" and not response.get("evidence"):
                return finish("error", "adapter_missing_evidence")
            if response.get("telemetry") is not None and not isinstance(response["telemetry"], dict):
                return finish("error", "adapter_invalid_telemetry")
            result.update(data=response.get("data"), evidence=response.get("evidence"),
                          telemetry=response.get("telemetry"))
            # Untrusted command error text can contain credentials. Keep it in the
            # protected raw log, expose only an adapter-independent error code.
            return finish(response["status"], None if response["status"] == "ok" else "adapter_reported_" + response["status"])
        except (OSError, subprocess.SubprocessError):
            if process is not None and process.poll() is None:
                try:
                    _kill_tree(process)
                    process.wait(timeout=15)
                except (OSError, subprocess.SubprocessError):
                    process.kill()
            return finish("error", "adapter_process_failure")
