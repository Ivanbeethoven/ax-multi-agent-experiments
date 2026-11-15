import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
CONCURRENT = ROOT / "bench" / "cluster" / "runtime" / "c1_concurrent.py"


def load_concurrent_pair():
    stub_cluster_lib = ModuleType("cluster_lib")
    stub_cluster_lib.collector_mark_all = lambda _group, label: {"label": label}
    stub_cluster_lib.summarize_phase_delta = lambda start, end: {
        "from": start["value"], "to": end["value"]
    }
    stub_cluster_lib.utc_iso = lambda: "2026-11-15T00:00:00+00:00"
    with patch.dict(sys.modules, {"cluster_lib": stub_cluster_lib}):
        module_spec = importlib.util.spec_from_file_location("c1_concurrent_test_module", CONCURRENT)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    return module


def wait_for_waiting_thread(pair, label, count=1, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        barrier = pair.barriers.get(label)
        if barrier is not None and barrier.n_waiting >= count:
            return
        time.sleep(0.001)
    raise AssertionError(f"barrier {label!r} did not reach {count} waiters")


class ConcurrentPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pair_module = load_concurrent_pair()

    def test_same_mark_takes_one_snapshot_after_both_threads_arrive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pair = self.pair_module.ConcurrentPair("group", temp_dir)
            capture_entered = threading.Event()
            release_capture = threading.Event()
            returned = [threading.Event(), threading.Event()]
            results = [None, None]
            errors = []
            calls = []

            def capture(group, label):
                calls.append((group, label))
                capture_entered.set()
                if not release_capture.wait(2):
                    raise TimeoutError("snapshot stub was not released")
                return {"group": group, "label": label, "sample": 1}

            def mark(index):
                try:
                    results[index] = pair.mark("cold_start")
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    returned[index].set()

            with patch.object(self.pair_module.cl, "collector_mark_all", side_effect=capture):
                threads = [threading.Thread(target=mark, args=(0,)),
                           threading.Thread(target=mark, args=(1,))]
                threads[0].start()
                wait_for_waiting_thread(pair, "cold_start")
                self.assertEqual(calls, [])
                threads[1].start()
                self.assertTrue(capture_entered.wait(2))
                self.assertFalse(returned[0].is_set())
                self.assertFalse(returned[1].is_set())
                release_capture.set()
                for thread in threads:
                    thread.join(2)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(calls, [("group", "cold_start")])
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0]["sample"], 1)
            self.assertTrue((Path(temp_dir) / "cold_start.json").is_file())

    def test_abort_releases_a_waiting_thread(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pair = self.pair_module.ConcurrentPair("group", temp_dir)
            errors = []

            def wait_at_barrier():
                try:
                    pair.wait("upload_done")
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=wait_at_barrier)
            thread.start()
            wait_for_waiting_thread(pair, "upload_done")
            pair.abort()
            thread.join(2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], threading.BrokenBarrierError)

    def test_finish_writes_shared_backend_metrics_to_group_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pair = self.pair_module.ConcurrentPair("shared-group", temp_dir)
            pair.snapshots.update({
                "cold_start": {"value": 1}, "cold_done": {"value": 2},
                "hot_start": {"value": 3}, "hot_done": {"value": 4},
                "write_start": {"value": 5}, "upload_done": {"value": 6},
            })
            pair.times.update({"cold_start": {"utc": "t1"},
                               "cold_done": {"utc": "t2"}})

            result = pair.finish(["juicefs", "juicefs-overlay"], True)

            result_path = Path(temp_dir) / "result.json"
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result, saved)
            self.assertEqual(saved["backend_scope"], "both-concurrent-workloads")
            self.assertEqual(saved["maximum_parallel_conditions"], 2)
            self.assertEqual(saved["systems"], ["juicefs", "juicefs-overlay"])
            self.assertEqual(set(saved["backend_delta"]), {"cold", "hot", "write"})
            self.assertEqual(sorted(path.name for path in Path(temp_dir).iterdir()), ["result.json"])


if __name__ == "__main__":
    unittest.main()
