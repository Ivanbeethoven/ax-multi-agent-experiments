import ast
from copy import deepcopy
from pathlib import Path
import statistics
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
FINALIZER = ROOT / "bench" / "cluster" / "runtime" / "finalize_trace_checks.py"


def merge_histograms(_histograms):
    return {"p50_us": 100, "p95_us": 800, "p99_us": 32000, "count": 8 * 94130}


def load_build_report():
    source = ast.parse(FINALIZER.read_text(encoding="utf-8"))
    function = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_report"
    )
    namespace = {
        "cl": SimpleNamespace(
            merge_log2_histograms=merge_histograms,
            utc_iso=lambda: "2026-11-15T00:00:00+00:00",
        ),
        "statistics": statistics,
    }
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(FINALIZER), "exec"), namespace)
    return namespace["build_report"]


def inputs(on_seconds=100.0, off_seconds=100.0):
    timings = []
    for pair, mode in ((1, "on"), (1, "off"), (2, "off"),
                       (2, "on"), (3, "on"), (3, "off")):
        seconds = on_seconds if mode == "on" else off_seconds
        timings.append((pair, mode, seconds, 100.0, 32000))

    documents = {}
    for pair, mode, *_ in timings:
        for index in range(8):
            documents[f"cal-p{pair}-{mode}-m{index}-replay.json"] = {
                "completed": True,
                "mode": "performance",
                "valid": False,
                "operations": 94130,
                "remaining_handles": 0,
                "think_time": {"all_boundaries_applied": True},
                "latency_us": {"all": {"bucket": index}},
                "active_replay_seconds": 100.0,
                "fs_operation_seconds": 30.0,
            }
    return timings, documents


class TraceFinalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_report = staticmethod(load_build_report())

    def test_three_complete_pairs_produce_a_passing_report(self):
        timings, documents = inputs()

        report = self.build_report(timings, documents)

        self.assertEqual(report["schema"], "ax-collector-overhead-calibration-v1")
        self.assertEqual(len(report["pairs"]), 3)
        self.assertEqual([pair["order"] for pair in report["pairs"]], [
            ["on", "off"], ["off", "on"], ["on", "off"]
        ])
        self.assertTrue(report["passed"])
        self.assertEqual(report["pairs"][0]["on"]["total_operations"], 8 * 94130)

    def test_valid_false_performance_reports_are_summarized(self):
        timings, documents = inputs()

        report = self.build_report(timings, documents)

        self.assertTrue(report["passed"])
        self.assertTrue(all(doc["mode"] == "performance" for doc in documents.values()))
        self.assertTrue(all(doc["valid"] is False for doc in documents.values()))

    def test_missing_on_or_off_measurement_is_rejected(self):
        for missing in ((1, "on"), (2, "off")):
            with self.subTest(missing=missing):
                timings, documents = inputs()
                timings = [row for row in timings if row[:2] != missing]

                with self.assertRaises(ValueError):
                    self.build_report(timings, documents)

    def test_incomplete_or_nonperformance_replay_is_rejected(self):
        for field, value in (("completed", False), ("mode", "smoke")):
            with self.subTest(field=field):
                timings, documents = inputs()
                documents = deepcopy(documents)
                documents["cal-p2-on-m5-replay.json"][field] = value

                with self.assertRaises(ValueError):
                    self.build_report(timings, documents)

    def test_logged_active_time_or_p99_must_match_replay_data(self):
        for mismatch in ("active", "p99"):
            with self.subTest(mismatch=mismatch):
                timings, documents = inputs()
                if mismatch == "active":
                    pair, mode, seconds, active, p99 = timings[0]
                    timings[0] = (pair, mode, seconds, active + 0.01, p99)
                else:
                    pair, mode, seconds, active, p99 = timings[0]
                    timings[0] = (pair, mode, seconds, active, p99 + 1)

                with self.assertRaises(ValueError):
                    self.build_report(timings, documents)

    def test_median_makespan_difference_over_five_percent_does_not_pass(self):
        timings, documents = inputs(on_seconds=106.0, off_seconds=100.0)

        report = self.build_report(timings, documents)

        self.assertAlmostEqual(report["median_relative_makespan_diff"], 0.06)
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
