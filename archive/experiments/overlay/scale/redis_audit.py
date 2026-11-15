"""One untimed MONITOR pass after performance counters have been captured."""
from collections import Counter
import json
from pathlib import Path
import re
import socket
import threading
import time
import uuid

LINE = re.compile(rb'^\+([0-9.]+) \[\d+ ([^\]]+)\] "([^" ]+)"(?: |\r?\n?$)')


def parse(line):
    match = LINE.match(line)
    assert match, repr(line[:160])
    return dict(timestamp=match[1].decode(), source='lua' if match[2] == b'lua' else 'client',
                command=match[3].decode().upper())


def audit(port, operation, output):
    output = Path(output)
    start, end = ('ax-monitor-' + uuid.uuid4().hex + '-' + x for x in ('start', 'end'))
    started, finished = threading.Event(), threading.Event()
    counts = {'client': Counter(), 'lua': Counter()}
    errors = []
    connection = socket.create_connection(('127.0.0.1', port), timeout=120)
    reader = connection.makefile('rb')
    connection.sendall(b'*1\r\n$7\r\nMONITOR\r\n')
    assert reader.readline() == b'+OK\r\n'

    def marker(value):
        payload = value.encode()
        with socket.create_connection(('127.0.0.1', port), timeout=5) as client:
            client.sendall(b'*2\r\n$4\r\nPING\r\n$' + str(len(payload)).encode() + b'\r\n' + payload + b'\r\n')
            assert client.recv(4096), 'marker connection closed'

    def collect():
        try:
            with output.with_suffix('.ndjson').open('x') as stream:
                while True:
                    line = reader.readline()
                    assert line, 'MONITOR disconnected before end marker'
                    if start.encode() in line:
                        started.set()
                        continue
                    if end.encode() in line:
                        finished.set()
                        return
                    if started.is_set():
                        row = parse(line)
                        counts[row['source']][row['command']] += 1
                        stream.write(json.dumps(row) + '\n')
        except BaseException as error:
            errors.append(type(error).__name__ + ': ' + str(error))
            started.set()
            finished.set()

    worker = threading.Thread(target=collect, daemon=True)
    worker.start()
    passed = False
    elapsed = None
    try:
        marker(start)
        assert started.wait(5) and not errors, errors
        before = time.monotonic_ns()
        operation()
        elapsed = time.monotonic_ns() - before
        marker(end)
        assert finished.wait(10) and not errors, errors
        passed = True
    finally:
        connection.shutdown(socket.SHUT_RDWR)
        worker.join(timeout=5)
        reader.close()
        connection.close()
        assert not worker.is_alive(), 'MONITOR must stop before returning'
        output.write_text(json.dumps(dict(passed=passed, errors=errors, timed=False,
            scope='one identical workload pass after timing; excludes marker PINGs',
            audit_elapsed_ns=elapsed, counts=counts,
            client_commands=sum(counts['client'].values()), lua_commands=sum(counts['lua'].values()),
            monitor_closed=True), indent=2))
