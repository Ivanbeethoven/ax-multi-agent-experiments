from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "bench" / "cluster" / "runtime" / "queue_packed_reads.py"
GIB = 1024 ** 3


def load_runner():
    spec = importlib.util.spec_from_file_location("queue_packed_reads_under_test", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackedQueueWaitingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.jobs = [
            {"id": "fio-read", "group": "01-io"},
            {"id": "packed-read", "group": "03-packed"},
            {"id": "replay-1", "group": "04-agent-replay"},
            {"id": "replay-2", "group": "04-agent-replay"},
        ]
        self.status = {
            "jobs": {
                "fio-read": {"state": "pending"},
                "packed-read": {"state": "running"},
                "replay-1": {"state": "passed"},
                "replay-2": {"state": "passed"},
            }
        }

    def _waiting(self, *, active_units=(), binary_ready=True, free_bytes=16 * GIB):
        return self.runner.waiting(
            self.status,
            self.jobs,
            active_units,
            binary_ready,
            free_bytes,
        )

    def test_nonpassed_replay_blocks_queue_including_failed_jobs(self):
        for state in ("pending", "running", "failed"):
            with self.subTest(state=state):
                self.status["jobs"]["replay-2"]["state"] = state
                self.assertTrue(self._waiting())

    def test_active_unit_blocks_queue(self):
        self.assertTrue(self._waiting(active_units=["ax-run.service"]))

    def test_missing_binary_blocks_queue(self):
        self.assertTrue(self._waiting(binary_ready=False))

    def test_free_space_below_16_gib_blocks_queue(self):
        self.assertTrue(self._waiting(free_bytes=16 * GIB - 1))

    def test_all_replays_passed_allows_queue_despite_unfinished_other_groups(self):
        reasons = self._waiting(free_bytes=16 * GIB)

        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
