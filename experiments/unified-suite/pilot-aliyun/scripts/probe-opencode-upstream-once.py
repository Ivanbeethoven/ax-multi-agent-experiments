#!/usr/bin/env python3
"""Diagnose one capped full native OpenCode request. Default: zero upstream.

The original checkpoint is opened only by the pinned offline probe. The
optional real POST is sent by curl after the isolated OpenCode has exited.
No request, response, provider URL, or key is written to disk or stdout.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import subprocess
import sys
import time
import types
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve()
OFFLINE_EVIDENCE = HERE.parent.parent / "evidence/preparation/opencode-offline-output512-01"
PROBE = OFFLINE_EVIDENCE / "source.py"
PROBE_SHA = "06238c6f7702db2d9ab06e7f74358dd31d11154b03c2a749623f2d940d377f24"
BASELINE = OFFLINE_EVIDENCE / "result.json"
BASELINE_SHA = "aafa44d29d55b08548dd1cb3ded62534a41d0527a8b4ccfab6c043328fa319ea"
CONFIG_SHA = "a0706d9c28a98aec0e363288f1a411c699987c8e451e7bb794e72db56bc24e1f"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
CONNECT_SECONDS = 8
TOTAL_SECONDS = 45
MAX_REQUEST = 512 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
FINISH = {"stop", "length", "tool_calls", "function_call", "content_filter", "error"}
USAGE = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
         "prompt_cache_miss_tokens", "cached_tokens", "reasoning_tokens"}
DETAIL = {"prompt_tokens_details", "completion_tokens_details"}


class Refusal(ValueError):
    """Fixed diagnostic text; never use this for untrusted messages."""


def check(ok, reason):
    if not ok:
        raise Refusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(path, expected):
    raw = path.read_bytes()
    check(sha(raw) == expected, "pinned input SHA mismatch")
    return raw


def disable_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def anonymous():
    return os.memfd_create("opencode-full-request", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)


def seal(fd):
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK |
                fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)


def run_child(args):
    # Load exact reviewed source bytes. No import cache and no modified offline evidence.
    probe = types.ModuleType("reviewed_offline_opencode_probe")
    probe.__file__ = str(PROBE)
    exec(compile(pinned(PROBE, PROBE_SHA), str(PROBE), "exec"), probe.__dict__)
    fd = args.capture_fd
    check(os.fstat(fd).st_size == 0, "capture memfd is not empty")

    class Tee:
        def __init__(self, inner):
            self.inner = inner
            self.body = None

        def read(self, length):
            self.body = self.inner.read(length)
            return self.body

    class Capture(probe.FirstRequest):
        def do_POST(self):
            original = self.rfile
            tap = Tee(original)
            self.rfile = tap
            try:
                super().do_POST()
            finally:
                self.rfile = original
            if self.server.stats is not None and self.server.sent_400:
                check(tap.body is not None and 0 < len(tap.body) <= MAX_REQUEST,
                      "invalid captured request size")
                with os.fdopen(os.dup(fd), "wb") as dest:
                    dest.write(tap.body)
                seal(fd)

    probe.FirstRequest = Capture
    args.output_limit = 512
    evidence = probe.run_isolated(args, json.loads(args.child))
    check(0 < os.fstat(fd).st_size <= MAX_REQUEST, "missing sealed request")
    print(json.dumps(evidence, separators=(",", ":")))


def capture(args):
    fd = anonymous()
    try:
        ns = {key: os.readlink(f"/proc/self/ns/{key}") for key in ("net", "mnt", "pid")}
        result = subprocess.run(
            ["unshare", "--net", "--mount", "--propagation", "private", "--pid", "--fork",
             "--mount-proc", "--kill-child=SIGKILL", sys.executable, "-B", str(HERE),
             "--checkpoint", str(args.checkpoint), "--checkpoint-sha256", args.checkpoint_sha256,
             "--started", str(args.started), "--started-sha256", args.started_sha256,
             "--child", json.dumps(ns), "--capture-fd", str(fd)],
            pass_fds=(fd,), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35)
        check(result.returncode == 0, "isolated OpenCode capture failed")
        evidence = json.loads(result.stdout)
        baseline = json.loads(pinned(BASELINE, BASELINE_SHA))
        check(all(evidence[k] == baseline[k] for k in (
            "history", "request", "network", "checkpoint_sha256", "opencode_sha256",
            "original_started_sha256", "original_argv_unchanged", "original_tool_permissions_unchanged"))
              and evidence["process"]["upstream_requests"] == 0
              and evidence["process"]["surviving_child_processes"] == 0,
              "capture differs from the reviewed full 512 request")
        check(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE, "capture is unsealed")
        raw = os.pread(fd, MAX_REQUEST + 1, 0)
        check(len(raw) == evidence["request"]["body_bytes"], "capture length does not match proof")
        body = json.loads(raw)
        check(body["max_tokens"] == 512 and body["stream"] is True and "thinking" not in body,
              "captured request limit or stream mode differs")
        # Match the original run 02 relay's only transformation, including JSON serialization.
        body["thinking"] = {"type": "disabled"}
        forward = json.dumps(body).encode()
        check(len(forward) <= MAX_REQUEST, "forwarded request exceeds size limit")
        return evidence, {"captured_bytes": len(raw), "captured_sha256": sha(raw),
                          "forwarded_bytes": len(forward), "forwarded_sha256": sha(forward),
                          "thinking": "disabled", "max_tokens": 512}, forward
    finally:
        os.close(fd)


def provider_options(path):
    provider = json.loads(pinned(path, CONFIG_SHA).decode("utf-8-sig"))["provider"]["deepseek"]
    base, key = (provider["options"][k] for k in ("baseURL", "apiKey"))
    check(isinstance(base, str) and base.isascii() and 0 < len(base) <= 2048
          and all(32 < ord(c) < 127 for c in base), "invalid provider URL")
    url = urllib.parse.urlsplit(base)
    check(url.scheme == "https" and url.hostname and not (url.username or url.password or
          url.query or url.fragment) and url.port in (None, 443), "expected original HTTPS provider")
    check(isinstance(key, str) and 0 < len(key) <= 1024 and not key.startswith("{")
          and all(32 < ord(c) < 127 for c in key), "invalid provider credential")
    proxy = urllib.request.getproxies().get("https") if not urllib.request.proxy_bypass(url.netloc) else None
    if proxy:
        check(proxy.isascii() and len(proxy) <= 2048 and all(32 < ord(c) < 127 for c in proxy),
              "invalid HTTPS proxy settings")
        parsed = urllib.parse.urlsplit(proxy)
        check(parsed.scheme in ("http", "https") and parsed.hostname and parsed.port
              and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
              and parsed.path in ("", "/"), "only unauthenticated HTTP(S) CONNECT proxies are supported")
    check(not any(os.environ.get(k) for k in ("SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE")),
          "current environment has nondefault TLS trust")
    return base.rstrip("/") + "/chat/completions", key, proxy


def usage_counts(value, include_details=True):
    if not isinstance(value, dict):
        return {}
    out = {k: v for k, v in value.items() if k in USAGE and type(v) is int and 0 <= v < 2**63}
    if include_details:
        out.update({k: usage_counts(v, False) for k, v in value.items()
                    if k in DETAIL and isinstance(v, dict)})
    return out


class Response:
    def __init__(self):
        self.header = b""
        self.headers_seconds = None
        self.header_status = None
        self.event_stream = False
        self.buffer = b""
        self.event = []
        self.first_event_seconds = None
        self.response_bytes = 0
        self.events = self.invalid_events = 0
        self.done = False
        self.usage = {}
        self.finishes = set()
        self.calls = set()

    def headers(self, data, elapsed):
        self.header += data
        check(len(self.header) <= 64 * 1024, "upstream headers exceed bound")
        while b"\r\n\r\n" in self.header:
            block, self.header = self.header.split(b"\r\n\r\n", 1)
            rows = block.split(b"\r\n")
            status = rows[0].split()
            if len(status) < 2 or not status[0].startswith(b"HTTP/") or not status[1].isdigit():
                continue
            code = int(status[1])
            if code >= 200 and self.headers_seconds is None:
                self.headers_seconds, self.header_status = elapsed, code
                self.event_stream = any(
                    row.partition(b":")[0].lower() == b"content-type"
                    and row.partition(b":")[2].split(b";", 1)[0].strip().lower() == b"text/event-stream"
                    for row in rows[1:])

    def body(self, data, elapsed):
        self.response_bytes += len(data)
        check(self.response_bytes <= MAX_RESPONSE, "upstream response exceeds bound")
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if line.startswith(b"data:"):
                self.event.append(line[5:].lstrip(b" "))
            elif not line and self.event:
                value = b"\n".join(self.event)
                self.event.clear()
                self.event_data(value, elapsed)

    def event_data(self, data, elapsed):
        if data == b"[DONE]":
            self.done = True
            if self.first_event_seconds is None:
                self.first_event_seconds = elapsed
            return
        try:
            obj = json.loads(data)
        except (UnicodeError, ValueError):
            self.invalid_events += 1
            return
        if not isinstance(obj, dict):
            self.invalid_events += 1
            return
        if self.first_event_seconds is None:
            self.first_event_seconds = elapsed
        self.events += 1
        if counts := usage_counts(obj.get("usage")):
            self.usage = counts
        choices = obj.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            index = choice.get("index", 0)
            if type(index) is not int or not 0 <= index < 4096:
                continue
            reason = choice.get("finish_reason")
            if isinstance(reason, str):
                self.finishes.add(reason if reason in FINISH else "other")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            calls = delta.get("tool_calls")
            for call in calls if isinstance(calls, list) else []:
                pos = call.get("index") if isinstance(call, dict) else None
                if type(pos) is int and 0 <= pos < 4096:
                    self.calls.add((index, pos))
            if isinstance(delta.get("function_call"), dict):
                self.calls.add((index, -1))

    def summary(self):
        return dict(http_status=self.header_status, event_stream=self.event_stream,
                    response_bytes=self.response_bytes, final_header_seconds=self.headers_seconds,
                    first_sse_seconds=self.first_event_seconds if self.event_stream else None,
                    data_events=self.events if self.event_stream else 0,
                    invalid_sse_events=self.invalid_events if self.event_stream else 0,
                    done=self.done and self.event_stream,
                    usage=self.usage if self.event_stream else {},
                    finish_reasons=sorted(self.finishes) if self.event_stream else [],
                    tool_call_count=len(self.calls) if self.event_stream else 0)


def curl_timings(raw):
    keys = ("response_code", "dns_seconds", "tcp_seconds", "tls_seconds", "first_response_byte_seconds",
            "total_seconds", "uploaded_bytes", "redirect_count", "connect_count", "proxy_connect_status")
    items = raw.decode("ascii").split()
    if len(items) != len(keys):
        return {}
    times = {k: (float(v) or None) for k, v in zip(keys[1:6], items[1:6])}
    return dict(zip((keys[0], *keys[6:]), map(int, (items[0], *items[6:])))) | times


def last_phase(reply, timings, request_bytes):
    if reply.done and reply.event_stream:
        return "sse_done"
    if reply.first_event_seconds is not None and reply.event_stream:
        return "sse_data"
    if reply.headers_seconds is not None:
        return "final_http_headers"
    if timings.get("uploaded_bytes") == request_bytes:
        return "request_uploaded"
    if timings.get("tls_seconds") is not None:
        return "tls"
    if timings.get("proxy_connect_status"):
        return "proxy_connect_reply"
    for name in ("tcp", "dns"):
        if timings.get(f"{name}_seconds") is not None:
            return name
    return "no_transport_phase_observed"


def exchange(endpoint, key, proxy, payload):
    request_fd = anonymous()
    header_r, header_w = os.pipe()
    body_r, body_w = os.pipe()
    flags = "%{http_code} %{time_namelookup} %{time_connect} %{time_appconnect} " \
            "%{time_starttransfer} %{time_total} %{size_upload} %{num_redirects} %{num_connects} %{http_connect}"
    # URL, proxy address and auth are absent from argv/env; curl reads them from stdin.
    config = (f"url = {json.dumps(endpoint)}\n"
              f"proxy = {json.dumps(proxy or '')}\n"
              f"header = {json.dumps('Authorization: Bearer ' + key)}\n").encode()
    reply = Response()
    writeout = b""
    failed = None
    try:
        with os.fdopen(os.dup(request_fd), "wb") as f:
            f.write(payload)
        os.lseek(request_fd, 0, os.SEEK_SET)
        seal(request_fd)
        start = time.monotonic()
        with subprocess.Popen(
            ["/usr/bin/curl", "-q", "--config", "-", "--silent", "--http1.1", "--proto", "=https",
             "--noproxy", "", "--suppress-connect-headers", "--max-redirs", "0", "--retry", "0",
             "--request", "POST", "--header", "Content-Type: application/json", "--header", "Expect:",
             "--header", "Connection: close", "--user-agent", f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}",
             "--connect-timeout", str(CONNECT_SECONDS), "--max-time", str(TOTAL_SECONDS),
             "--max-filesize", str(MAX_RESPONSE), "--no-buffer", "--data-binary", f"@/proc/self/fd/{request_fd}",
             "--dump-header", f"/proc/self/fd/{header_w}", "--output", f"/proc/self/fd/{body_w}",
             "--write-out", flags],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV,
            pass_fds=(request_fd, header_w, body_w), preexec_fn=disable_core) as curl:
            os.close(header_w)
            os.close(body_w)
            header_w = body_w = None
            try:
                with curl.stdin:
                    curl.stdin.write(config)
                with selectors.DefaultSelector() as sel:
                    for fileno, name in ((header_r, "header"), (body_r, "body"), (curl.stdout.fileno(), "metrics")):
                        sel.register(fileno, selectors.EVENT_READ, name)
                    while sel.get_map():
                        left = TOTAL_SECONDS + 3 - (time.monotonic() - start)
                        check(left > 0, "upstream process exceeded its total deadline")
                        for entry, _ in sorted(sel.select(left), key=lambda event: event[0].data != "header"):
                            data = os.read(entry.fd, 8192)
                            elapsed = round(time.monotonic() - start, 6)
                            if not data:
                                sel.unregister(entry.fd)
                            elif entry.data == "header":
                                reply.headers(data, elapsed)
                            elif entry.data == "body":
                                reply.body(data, elapsed)
                            else:
                                writeout += data
                                check(len(writeout) <= 1024, "unexpected curl writeout")
                code = curl.wait(timeout=2)
            except (OSError, Refusal, subprocess.TimeoutExpired) as error:
                failed = type(error).__name__
                if curl.poll() is None:
                    curl.kill()
                code = curl.wait(timeout=2)
        timings = curl_timings(writeout)
        return reply.summary() | {"curl_exit": code, "transport_failure": failed, "timings": timings,
                                  "last_observed_phase": last_phase(reply, timings, len(payload))}
    finally:
        for fd in (request_fd, header_r, header_w, body_r, body_w):
            if fd is not None:
                os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--checkpoint-sha256", required=True)
    p.add_argument("--started", type=Path, required=True)
    p.add_argument("--started-sha256", required=True)
    p.add_argument("--out", type=Path)
    p.add_argument("--provider-config", type=Path, help="Original pinned config; required only for --execute-once")
    p.add_argument("--execute-once", action="store_true", help="Send exactly one capped upstream HTTPS POST")
    p.add_argument("--child", help=argparse.SUPPRESS)
    p.add_argument("--capture-fd", type=int, help=argparse.SUPPRESS)
    a = p.parse_args()
    try:
        disable_core()
        if a.child is not None:
            check(a.provider_config is None and not a.execute_once and a.out is None,
                  "isolated child cannot take upstream inputs")
            run_child(a)
            return 0
        check(os.geteuid() == 0 and a.out is not None and a.capture_fd is None,
              "Linux root and a fresh output path are required")
        check(a.execute_once == (a.provider_config is not None),
              "use both --execute-once and the original --provider-config, or neither")
        upstream = provider_options(a.provider_config) if a.execute_once else None
        # Reserve the output before capturing. The same output cannot be reused after failure.
        with a.out.open("x") as dest:
            record = dict(schema="opencode-upstream-once-v1", at=datetime.now(timezone.utc).isoformat(),
                          passed=False, upstream_attempts=0, no_retry=True, no_redirect=True,
                          offline_probe_sha256=PROBE_SHA, baseline_sha256=BASELINE_SHA,
                          script_sha256=sha(HERE.read_bytes()),
                          scope="Transport diagnostic only; no model reply reached OpenCode or ran tools.")
            try:
                record["offline_capture"], record["request"], payload = capture(a)
                if upstream is not None:
                    record.update(upstream_attempts=1, provider_config_sha256=CONFIG_SHA,
                                  connect_limit_seconds=CONNECT_SECONDS, total_limit_seconds=TOTAL_SECONDS,
                                  transport="curl HTTP/1.1 with system TLS trust; original relay used urllib",
                                  route="current urllib policy: CONNECT proxy" if upstream[2] else
                                        "current urllib policy: direct HTTPS",
                                  dns_tcp_scope="proxy endpoint" if upstream[2] else "origin endpoint")
                    reply = exchange(*upstream, payload)
                    record["upstream"] = reply
                    record["passed"] = (reply["http_status"] == 200 and reply["event_stream"]
                                        and reply["done"] and reply["curl_exit"] == 0
                                        and reply["timings"].get("redirect_count") == 0
                                        and reply["timings"].get("uploaded_bytes") == len(payload))
                else:
                    record["passed"] = True
            except Exception as error:
                record["error_type"] = type(error).__name__
            json.dump(record, dest, indent=2, sort_keys=True)
            dest.write("\n")
        return 0 if record["passed"] else 1
    except Refusal as error:
        print("diagnostic refused:", error, file=sys.stderr)
    except Exception as error:
        print("diagnostic failed:", type(error).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
