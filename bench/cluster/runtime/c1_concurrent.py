"""Coordinate two C1 workloads sharing the object and metadata servers."""
import json
import threading
import time
from pathlib import Path

import cluster_lib as cl


class ConcurrentPair:
    def __init__(self, group_id, directory):
        self.group_id = group_id
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.barriers = {}
        self.snapshots = {}
        self.times = {}
        self.failed = False

    def _barrier(self, name, action=None):
        with self.lock:
            if self.failed:
                raise threading.BrokenBarrierError('Concurrent workload failed')
            return self.barriers.setdefault(name, threading.Barrier(2, action=action, timeout=7500))

    def mark(self, label):
        def capture():
            snapshot = cl.collector_mark_all(self.group_id, label)
            self.snapshots[label] = snapshot
            self.times[label] = {'utc': cl.utc_iso(), 'monotonic_seconds': time.monotonic()}
            (self.directory / (label + '.json')).write_text(json.dumps(snapshot) + '\n')
        self._barrier(label, capture).wait()
        return self.snapshots[label]

    def wait(self, label):
        self._barrier(label).wait()

    def abort(self):
        with self.lock:
            self.failed = True
            barriers = list(self.barriers.values())
        for barrier in barriers:
            barrier.abort()

    def finish(self, systems, passed):
        intervals = [('cold', 'cold_start', 'cold_done'), ('hot', 'hot_start', 'hot_done'),
                     ('write', 'write_start', 'upload_done')]
        backend = {}
        for phase, start, end in intervals:
            if start in self.snapshots and end in self.snapshots:
                backend[phase] = cl.summarize_phase_delta(self.snapshots[start], self.snapshots[end])
        result = {'execution_mode': 'shared-backend-concurrent', 'maximum_parallel_conditions': 2,
                  'group_id': self.group_id, 'systems': systems, 'passed': passed,
                  'phase_times': self.times, 'backend_scope': 'both-concurrent-workloads',
                  'backend_delta': backend}
        (self.directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
