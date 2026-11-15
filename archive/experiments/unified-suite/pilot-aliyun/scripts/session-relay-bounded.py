#!/usr/bin/env python3
"""Independent cloud C4 relay: the qualified four-step gate, no local sandbox.

The original full-body SHA is fixed in the pinned qualification module. A fresh
output directory is required for each explicitly reviewed attempt; no retries.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import threading
import time


BASE = Path(__file__).resolve().parent
CAPPED_SHA = "ef4e949dc902d40e1266a4dbfb700192df736909ea5b14e57fb99f7a42e02d1e"
RELAY_SHA = "c77d3c2081493245be946951a6d9cc554e8a2871baf65ab8d69e3d881ef8be87"


def dependency(name, filename, expected):
    path = BASE / filename
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    if path.is_symlink() or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Bounded relay dependency SHA differs")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def private_json(path, value):
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        json.dump(value, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class Control:
    def __init__(self, capped, output, stop):
        self.hooks = capped.Hooks()
        # Only the qualified opener is used. Never install Hooks.worker or its
        # devnull stdout/local bubblewrap sandbox on the cloud native worker.
        self.gate = self.hooks.gate = capped.Gate(on_close=stop.set)
        self.output = output
        self.lock = threading.Lock()
        self.closed = False
        self.reserved = self.recorded = 0
        self.log = open(output / "calls.jsonl", "x",
                        opener=lambda name, flags: os.open(name, flags, 0o600))

    def snapshot(self):
        return self.gate.summary() | dict(reserved=self.reserved, recorded=self.recorded,
                                         in_flight=self.gate.in_flight, closed=self.closed,
                                         request_bodies_logged=False)

    def reserve(self):
        with self.lock:
            # An already-accepted handler may reach reserve after finalization.
            # Refuse it without changing the completed attempt's gate result.
            if self.closed:
                return False
            allowed = self.gate.reserve(self.reserved, self.recorded)
            if allowed:
                self.reserved += 1
            private_json(self.output / "state.json", self.snapshot())
            return allowed

    def record(self, row):
        with self.lock:
            self.gate.observe(row)
            self.recorded += 1
            self.log.write(json.dumps(row) + "\n")
            self.log.flush()
            os.fsync(self.log.fileno())
            private_json(self.output / "state.json", self.snapshot())

    def server_error(self):
        with self.lock:
            self.closed = True
            with self.gate.lock:
                self.gate.reject("server_error")
            private_json(self.output / "state.json", self.snapshot())

    def finish(self, reason=None):
        with self.lock:
            self.closed = True
            if reason:
                with self.gate.lock:
                    self.gate.reject(reason)
            value = self.snapshot()
            value["passed"] = (value["closed_reason"] is None and
                               0 < value["upstream_posts_attempted"] <= value["budget"] and
                               self.reserved == self.recorded and not value["in_flight"])
            private_json(self.output / "state.json", self.snapshot())
            private_json(self.output / "result.json", value)
            return value


def serve(config_path, output, wall_seconds):
    if type(wall_seconds) is not int or not 1 <= wall_seconds <= 900:
        raise ValueError("Explicit relay lifetime of 1..900 seconds required")
    capped = dependency("cloud_capped_gate", "qualify-session-shared-capped.py", CAPPED_SHA)
    relay = dependency("cloud_original_relay", "session-relay.py", RELAY_SHA)
    config = relay.configuration(config_path)
    if config["max_requests"] != 4 or config.get("thinking_mode") != "disabled":
        raise ValueError("Independent private relay config must fix four requests and disabled thinking")
    output = Path(output)
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("New absolute private relay output required")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    stop = threading.Event()
    control = Control(capped, output, stop)
    handler = relay.handler(config, control.record, control.reserve, opener=control.hooks.opener(relay))

    class Handler(handler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

    class Server(relay.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, *args):
            # Exceptions can contain upstream information; only gate metadata
            # belongs in receipts, even if the previous request completed.
            control.server_error()

    previous = {}
    server = None
    deadline = time.monotonic() + wall_seconds
    try:
        server = Server((config["bind"], config["port"]), Handler)
        server.timeout = .2
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        private_json(output / "ready.json", dict(pid=os.getpid(), bind=config["bind"],
            port=server.server_port, max_requests=4, output_tokens=512, thinking_mode="disabled",
            first_forwarded_body_sha256=capped.FIRST_SHA, total_timeout_seconds=wall_seconds,
            dependencies=dict(capped=CAPPED_SHA, relay=RELAY_SHA),
            request_bodies_logged=False, provider_master_key_in_workspace=False,
            scope="Bounded relay availability; not a native handoff result"))
        while not stop.is_set() and time.monotonic() < deadline:
            server.handle_request()
        reason = "relay_lifetime_exceeded" if time.monotonic() >= deadline else None
        if control.gate.in_flight and reason is None and control.gate.reason is None:
            reason = "relay_stopped_in_flight"
        return control.finish(reason)
    finally:
        if server is not None:
            server.server_close()
        for signum, previous_handler in previous.items():
            signal.signal(signum, previous_handler)
        # Daemon request threads end with this dedicated entry-point process.
        # Keep their metadata log valid until process exit, including failures.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=int, required=True)
    args = parser.parse_args()
    try:
        result = serve(args.private_config, args.out, args.wall_seconds)
    except Exception as error:
        print(json.dumps(dict(passed=False, error_type=type(error).__name__, automatic_retry=False)))
        return 1
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
