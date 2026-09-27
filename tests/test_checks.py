"""Unit tests for the threshold rules and the baseline comparison."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.checks import (  # noqa: E402
    ConfigError,
    check_store,
    load_baseline,
    load_config,
    parse_rules,
    save_baseline,
    to_dict,
)
from mcu_insight.simulator import iter_frames  # noqa: E402
from mcu_insight.store import Store  # noqa: E402
from mcu_insight.telemetry import parse_frame  # noqa: E402


class CheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self._stores: list[Store] = []

    def tearDown(self) -> None:
        # Close the databases first: Windows keeps the SQLite file locked and
        # the temporary directory cannot be removed while a handle is open.
        for store in self._stores:
            store.close()
        self._tmp.cleanup()

    def store_for(self, scenario: str, count: int = 6) -> Store:
        store = Store(self.dir / f"{scenario}.db")
        self._stores.append(store)
        for line in iter_frames(scenario, count=count, seed=3):
            store.add(parse_frame(line))
        return store

    def config(self, rules, baseline=None) -> Path:
        payload: dict = {"rules": rules}
        if baseline:
            payload["baseline"] = {"path": baseline, "max_change_pct": 20}
        path = self.dir / "budget.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    # -- rules ---------------------------------------------------------
    def test_wildcards_match_every_task(self) -> None:
        rules = parse_rules(
            {"rules": [{"metric": "task.*.stack_free_min", "min": 1}]}
        )
        self.assertTrue(rules[0].matches("task.sensor.stack_free_min"))
        self.assertTrue(rules[0].matches("task.monitor.stack_free_min"))
        self.assertFalse(rules[0].matches("heap.free"))

    def test_rule_without_bound_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_rules({"rules": [{"metric": "heap.free"}]})

    def test_missing_config_file(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(self.dir / "nope.json")

    def test_invalid_json(self) -> None:
        path = self.dir / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)

    # -- verdicts ------------------------------------------------------
    def test_healthy_run_passes(self) -> None:
        store = self.store_for("steady")
        config = {"rules": [{"metric": "heap.min", "min": 100000}]}
        report = check_store(store, config)
        self.assertTrue(report.ok)
        self.assertEqual(report.violations, [])
        self.assertGreater(report.checked_metrics, 0)

    def test_stack_budget_violation_is_reported(self) -> None:
        store = self.store_for("steady")
        # The simulated monitor task has ~1950 bytes free, the sensor one more.
        config = {"rules": [{"metric": "task.*.stack_free_min", "min": 5000}]}
        report = check_store(store, config)
        self.assertFalse(report.ok)
        self.assertTrue(
            all(v.metric.startswith("task.") for v in report.violations)
        )
        self.assertIn("below 5000", report.violations[0].describe())

    def test_latency_ceiling_is_enforced(self) -> None:
        store = self.store_for("latency-jitter")
        config = {
            "rules": [{"metric": "custom.loop_jitter_us", "max": 300, "stat": "max"}]
        }
        report = check_store(store, config)
        self.assertFalse(report.ok)
        self.assertIn("above 300", report.violations[0].describe())

    def test_stat_selector_decides_which_observation_is_tested(self) -> None:
        store = self.store_for("latency-jitter")
        latest = {"rules": [{"metric": "custom.loop_jitter_us", "max": 300}]}
        worst = {
            "rules": [{"metric": "custom.loop_jitter_us", "max": 300, "stat": "max"}]
        }
        # The spike happens early, so only the "max" rule sees it.
        self.assertTrue(check_store(store, latest).ok)
        self.assertFalse(check_store(store, worst).ok)

    def test_unknown_stat_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            parse_rules({"rules": [{"metric": "heap.free", "max": 1, "stat": "median"}]})

    def test_rules_that_match_nothing_do_not_fail(self) -> None:
        store = self.store_for("steady")
        config = {"rules": [{"metric": "custom.does_not_exist", "min": 10}]}
        report = check_store(store, config)
        self.assertTrue(report.ok)
        self.assertEqual(report.checked_metrics, 0)

    # -- baseline ------------------------------------------------------
    def test_baseline_roundtrip(self) -> None:
        store = self.store_for("steady")
        path = self.dir / "baseline.json"
        snapshot = save_baseline(store, path)
        self.assertIn("esp32-light-monitor", snapshot)
        self.assertEqual(load_baseline(path), snapshot)

    def test_heap_leak_shows_up_against_the_baseline(self) -> None:
        healthy = self.store_for("steady")
        baseline_path = self.dir / "baseline.json"
        save_baseline(healthy, baseline_path)
        baseline = load_baseline(baseline_path)

        leaking = self.store_for("heap-leak", count=30)
        config = {"rules": [], "baseline": {"max_change_pct": 20}}
        report = check_store(leaking, config, baseline, baseline_path)
        changed = {c.metric: c for c in report.changes}
        self.assertIn("heap.free", changed)
        self.assertLess(changed["heap.free"].percent or 0, -0.2)
        self.assertTrue(report.ok, "the leak is a change, not a threshold breach")

    def test_metric_absent_from_baseline_is_listed(self) -> None:
        healthy = self.store_for("steady")
        baseline_path = self.dir / "baseline.json"
        save_baseline(healthy, baseline_path)
        baseline = load_baseline(baseline_path)
        # The jitter scenario adds custom.loop_jitter_us, which steady also has,
        # so drop it from the baseline to simulate a newly added metric.
        for device in baseline.values():
            device.pop("custom.loop_jitter_time_us", None)
        writing = self.store_for("steady")
        config = {"rules": [], "baseline": {"max_change_pct": 20}}
        report = check_store(writing, config, baseline, baseline_path)
        self.assertIsInstance(report.new_metrics, list)

    # -- json ----------------------------------------------------------
    def test_json_output_shape(self) -> None:
        store = self.store_for("steady")
        config = {"rules": [{"metric": "heap.min", "min": 999999}]}
        payload = to_dict(check_store(store, config))
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["violations"]), 1)
        self.assertEqual(payload["violations"][0]["metric"], "heap.min")


if __name__ == "__main__":
    unittest.main()
