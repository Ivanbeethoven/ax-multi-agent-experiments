"""Reconcile native Packed counters with the independent S3 proxy."""
import os
from pathlib import Path
import re
import time

from bench import warm_lower


def difference(before, after):
    return {key: after[key] - before[key] for key in before}


def runtime_proxy(counters):
    return dict(full_requests=counters['gets'] - counters['ranges'],
                range_requests=counters['ranges'], head_requests=counters['heads'],
                received_bytes=counters['bytes'])


def evaluate(snapshots, expected_bytes, mode, mount_s3, end_to_end_s3, unmount_s3):
    """The off arm must fail only the hot-lower gate, while counters stay valid."""
    labels = ('before', 'cold-drained', 'warm-end')
    rows = [snapshots[label]['fields'] for label in labels]
    hot = warm_lower.assess(rows, expected_bytes)
    reasons = []
    if mode == 'on':
        if not hot['passed']:
            reasons.extend(hot['reasons'])
    elif mode == 'off':
        expected_reason = 'second pass still reads the Packed lower backend'
        if hot['passed'] or hot['reasons'] != [expected_reason]:
            reasons.append('off arm did not show exactly the expected hot-lower rejection')
        warm = hot['second_pass']
        if warm['range_requests'] + warm['full_requests'] <= 0 or warm['received_bytes'] <= 0:
            reasons.append('off second pass did not show positive backend reads and bytes')
    else:
        raise ValueError('Expected on or off mode')

    # Native runtime counters are compared relative to the post-mount sample.
    # The proxy includes the mount, so remove the actual mount observation.
    native = difference(rows[0], rows[-1])
    proxy = runtime_proxy(difference(mount_s3, end_to_end_s3))
    native_runtime = {key: native[key] for key in proxy}
    if native_runtime != proxy:
        reasons.append('native runtime counters differ from proxy E2E minus mount')
    if any(value < 0 for value in proxy.values()):
        reasons.append('proxy counters reset')
    if end_to_end_s3['errors'] != 0:
        reasons.append('proxy observed a backend error or retry; native calls are not wire attempts')
    if rows[0]['failures'] != 0:
        reasons.append('native runtime already recorded a failure during mount/stats initialization')
    if any(unmount_s3[key] != 0 for key in ('gets', 'ranges', 'heads', 'bytes', 'errors')):
        reasons.append('unmount performed backend work after the final native snapshot')

    phase_matches = []
    for first, last in zip(labels, labels[1:]):
        left, right = snapshots[first], snapshots[last]
        for snapshot in (left, right):
            if snapshot['proxy_before'] != snapshot['proxy_after'] or snapshot['proxy_after']['active'] != 0:
                reasons.append('backend was not quiescent at a native gate boundary')
        actual = difference(left['fields'], right['fields'])
        observed = runtime_proxy(difference(left['proxy_after'], right['proxy_after']))
        match = {key: actual[key] for key in observed} == observed
        phase_matches.append(dict(start=first, end=last, native={key: actual[key] for key in observed},
                                  proxy=observed, matched=match))
        if not match:
            reasons.append('native/proxy phase boundary mismatch')
    return dict(required=True, passed=not reasons, reasons=reasons,
                warm_lower_assessment=hot, native_runtime=native_runtime,
                proxy_runtime_excluding_mount=proxy, phase_reconciliation=phase_matches,
                unmount_backend_quiet=not any(unmount_s3[key] for key in
                                              ('gets', 'ranges', 'heads', 'bytes', 'errors')))


class NativeProbe:
    def __init__(self, point, work, side, counters, expected_bytes, deadline):
        self.point, self.work = Path(point), Path(work)
        self.side, self.counters, self.expected_bytes = side, counters, expected_bytes
        self.required = re.fullmatch(r'(?:off|on)-[0-3]', side) is not None
        self.deadline = deadline
        self.snapshots = {}
        self.sampling_ns = 0

    def sample(self, label):
        if not self.required:
            return
        if label == 'before':
            # The first real FUSE .stats open can resolve root permission
            # metadata. Preserve that traffic and cost, then establish a
            # second, strictly quiet baseline without a fixed sleep.
            self._sample_once('bootstrap')
        self._sample_once(label)

    def _sample_once(self, label):
        started = time.monotonic_ns()
        # The cold-end snapshot is diagnostic; the gate uses cold-drained.
        # Gate boundaries wait for proxy handlers to finish, without adding a
        # fixed idle window. Any actual wait remains in raw end-to-end time.
        if label != 'cold-end':
            while self.counters()['active'] != 0:
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('Native snapshot boundary did not drain')
                time.sleep(.001)
        boundary_wait_ns = time.monotonic_ns() - started
        before = self.counters()
        fd = os.open(self.point / '.stats', os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(warm_lower.MAX_STATS_BYTES + 1)
        after = self.counters()
        if len(raw) > warm_lower.MAX_STATS_BYTES:
            raise ValueError('Native stats exceed bounded reader')
        filename = 'native-' + self.side + '-' + label + '.stats'
        with (self.work / filename).open('xb') as stream:
            stream.write(raw)
        fields = warm_lower.parse(raw)  # Missing counters never become zero.
        elapsed = time.monotonic_ns() - started
        self.sampling_ns += elapsed
        self.snapshots[label] = dict(fields=fields, raw_file=filename,
                                     proxy_before=before, proxy_after=after, sampling_ns=elapsed,
                                     boundary_wait_ns=boundary_wait_ns,
                                     stats_io_parse_ns=elapsed - boundary_wait_ns)

    def finish(self, mount_s3, end_to_end_s3, unmount_s3):
        if not self.required:
            return dict(required=False, scope='Correctness visit; no cold-to-hot native gate applied')
        result = evaluate(self.snapshots, self.expected_bytes, self.side.split('-')[0],
                          mount_s3, end_to_end_s3, unmount_s3)
        bootstrap = self.snapshots['bootstrap']
        before = self.snapshots['before']
        initialization = dict(
            sampling_ns=bootstrap['sampling_ns'] + before['sampling_ns'],
            proxy=runtime_proxy(difference(bootstrap['proxy_before'], before['proxy_after'])),
            native_cumulative_at_bootstrap=bootstrap['fields'],
            native_between_bootstrap_and_before=difference(bootstrap['fields'], before['fields']),
            note='Initialization is included in mount S3/preparation and E2E; native bootstrap values are cumulative, not inferred from a missing earlier sample')
        return dict(result, snapshots=self.snapshots, sampling_ns=self.sampling_ns,
                    sampling_initialization=initialization,
                    timing_note='First traversal/read follows recorded stats initialization; phase timers exclude samples, and raw E2E includes all sampling overhead')
