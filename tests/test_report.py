"""Unit tests for the self-contained HTML report.

The report is the part of the tool other people actually look at, so it gets
its own invariants: it must stay dependency-free, and it must not call a
decrease a regression when a decrease is an improvement.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.checks import check_store, load_config  # noqa: E402
from mcu_insight.html_report import (  # noqa: E402
    BOOT_WARMUP_MS,
    boot_boundaries,
    capture_axis,
    build_html,
    change_verdict,
    gather_fault_cases,
    regression_direction,
    sparkline,
    steady_samples,
)
from mcu_insight.simulator import iter_frames  # noqa: E402
from mcu_insight.store import Store  # noqa: E402
from mcu_insight.telemetry import parse_frame  # noqa: E402


class DirectionTest(unittest.TestCase):
    """A decrease is not automatically a regression."""

    def test_falling_free_resources_is_the_bad_direction(self):
        for metric in (
            "heap.min",
            "heap.free",
            "task.main.stack_free_min",
            "custom.arena_free",
            "custom.headroom_bytes",
            "custom.idle0_pct",
            "custom.light_sleep_pct",
            "custom.model_accuracy_pct",
            "custom.acc_triangle_pct",
        ):
            with self.subTest(metric=metric):
                self.assertEqual("down", regression_direction(metric))
                self.assertEqual("bad", change_verdict(metric, -12.0))
                self.assertEqual("ok", change_verdict(metric, 12.0))

    def test_rising_latency_is_the_bad_direction(self):
        for metric in (
            "custom.infer_p50_us",
            "custom.infer_p99_us",
            "custom.infer_mean_us",
            "custom.infer_max_us",
            "custom.loop_jitter_us",
        ):
            with self.subTest(metric=metric):
                self.assertEqual("up", regression_direction(metric))
                self.assertEqual("bad", change_verdict(metric, 1.0))
                self.assertEqual("ok", change_verdict(metric, -1.0))

    def test_unknown_direction_is_left_unjudged(self):
        # Uptime, sample counts and the classified label all move for reasons
        # that are not regressions, so the report must not colour them red.
        for metric in (
            "uptime_ms",
            "custom.infer_samples",
            "custom.model_class",
            "custom.model_expected",
            "seq",
            "heap.largest",
        ):
            with self.subTest(metric=metric):
                self.assertIsNone(regression_direction(metric))
                self.assertEqual("", change_verdict(metric, -999.0))

    def test_flat_trend_has_no_verdict(self):
        self.assertEqual("", change_verdict("heap.min", 0.0))


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._stores: list[Store] = []

    def tearDown(self) -> None:
        # Windows keeps the SQLite file locked while a handle is open.
        for store in self._stores:
            store.close()
        self._tmp.cleanup()

    def store_for(self, scenario: str, count: int = 10, name: str | None = None) -> Store:
        store = Store(self.dir / (name or (scenario + ".db")))
        self._stores.append(store)
        for line in iter_frames(scenario, count=count, seed=3):
            store.add(parse_frame(line))
        return store

    def config(self, rules) -> dict:
        path = self.dir / "budget.json"
        path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return load_config(path)

    def test_report_is_self_contained(self):
        store = self.store_for("steady", count=8)
        report = check_store(store, self.config([{"metric": "heap.min", "min": 1000, "stat": "min"}]))
        page = build_html(store, report)
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertTrue(page.endswith("</html>"))
        # No CDN, no local server, no script: the page has to work offline and
        # from a file:// URL, otherwise it cannot be handed to anyone.
        for needle in ("http://", "https://", "<script", " src=", " href="):
            self.assertNotIn(needle, page)
        self.assertEqual(page.count("<section>"), page.count("</section>"))

    def test_violation_reaches_the_page(self):
        store = self.store_for("heap-leak", count=10)
        report = check_store(store, self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}]))
        self.assertFalse(report.ok)
        page = build_html(store, report)
        self.assertIn('class="badge fail"', page)
        self.assertIn("heap.min", page)

    def test_healthy_capture_has_no_red_verdicts(self):
        store = self.store_for("steady", count=8)
        report = check_store(store, self.config([{"metric": "heap.min", "min": 1000, "stat": "min"}]))
        page = build_html(store, report)
        self.assertIn('class="badge pass"', page)
        self.assertNotIn("bad</td>", page)

    def test_sparkline_needs_two_points(self):
        self.assertIn("<svg", sparkline([(0.0, 1.0), (1.0, 2.0)]))
        self.assertNotIn("polyline", sparkline([(0.0, 1.0)]))

    def test_fault_matrix_is_rebuilt_from_the_captures(self):
        rules = [
            {"metric": "heap.min", "min": 135000, "stat": "min"},
            {"metric": "task.monitor.stack_free_min", "min": 1400, "stat": "min"},
            {"metric": "custom.loop_jitter_us", "max": 2000, "stat": "max"},
        ]
        for mode, scenario in (
            ("off", "steady"),
            ("heap", "heap-leak"),
            ("stack", "stack-creep"),
            ("busy", "latency-jitter"),
        ):
            self.store_for(scenario, count=20, name="fault-%s.db" % mode)

        cases = gather_fault_cases(self.dir, self.config(rules))
        self.assertEqual(
            [(label, expected) for label, _, expected in cases],
            [("baseline", False), ("heap leak", True), ("stack frame", True), ("busy wait", True)],
        )
        for label, detected, expected in cases:
            self.assertEqual(expected, detected, label)


class BootHandlingTest(unittest.TestCase):
    """A capture can span a reset, and a boot is not steady state."""

    def test_boot_boundaries_find_every_restart(self):
        points = [(0.0, 1.0), (5000.0, 2.0), (1000.0, 3.0), (6000.0, 4.0), (500.0, 5.0)]
        self.assertEqual([2, 4], boot_boundaries(points))

    def test_start_up_samples_are_skipped(self):
        points = [(0.0, 10.0), (5_000.0, 20.0), (20_000.0, 30.0)]
        begin, steady = steady_samples(points, warmup_ms=10_000.0)
        self.assertEqual(0, begin)
        self.assertEqual([30.0], [v for _, v in steady])

    def test_statistics_cover_the_newest_session_only(self):
        points = [(0.0, 1.0), (60_000.0, 2.0), (2_000.0, 3.0), (30_000.0, 4.0)]
        begin, steady = steady_samples(points, warmup_ms=10_000.0)
        self.assertEqual(2, begin)
        self.assertEqual([4.0], [v for _, v in steady])

    def test_a_short_session_falls_back_to_everything_it_has(self):
        points = [(0.0, 1.0), (60_000.0, 2.0), (2_000.0, 3.0)]
        begin, steady = steady_samples(points, warmup_ms=10_000.0)
        self.assertEqual(2, begin)
        self.assertEqual([3.0], [v for _, v in steady])

    def test_capture_axis_keeps_counting_across_a_reset(self):
        points = [(0.0, 1.0), (50_000.0, 2.0), (2_000.0, 3.0), (7_000.0, 4.0)]
        self.assertEqual([0.0, 50_000.0, 52_000.0, 57_000.0], capture_axis(points))
    def test_default_warmup_is_ten_seconds(self):
        self.assertEqual(10_000.0, BOOT_WARMUP_MS)


class RebootMarkerTest(ReportTest):
    def test_capture_across_a_reset_is_marked_on_the_page(self):
        store = Store(self.dir / "reboot.db")
        self._stores.append(store)
        for line in iter_frames("steady", count=8, seed=1):
            store.add(parse_frame(line))
        # A second run that restarts at uptime 0 is what a reset looks like.
        for line in iter_frames("steady", count=8, seed=1, start_uptime_ms=0.0):
            store.add(parse_frame(line))
        device = store.devices()[0]
        self.assertEqual(1, len(store.reboot_frames(device)))

        report = check_store(store, self.config([{"metric": "heap.min", "min": 1000, "stat": "min"}]))
        page = build_html(store, report)
        self.assertIn("stroke-dasharray", page)
        self.assertIn("1 reboot(s) in this capture", page)
        self.assertIn("newest boot session", page)

    def test_clean_capture_has_no_marker_and_no_reboot_note(self):
        store = self.store_for("steady", count=8)
        report = check_store(store, self.config([{"metric": "heap.min", "min": 1000, "stat": "min"}]))
        page = build_html(store, report)
        self.assertNotIn("stroke-dasharray", page)
        self.assertNotIn("reboot(s) in this capture", page)


if __name__ == "__main__":
    unittest.main()