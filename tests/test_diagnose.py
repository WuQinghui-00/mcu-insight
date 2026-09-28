"""Unit tests for the evidence pack.

Everything here runs offline: no endpoint, no key, no network. That is the
point of splitting evidence gathering from the model call.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.checks import (  # noqa: E402
    check_store,
    load_baseline,
    load_config,
    save_baseline,
)
from mcu_insight.cli import main  # noqa: E402
from mcu_insight.diagnose import (  # noqa: E402
    audit_answer,
    build_prompt,
    collect_changes,
    fit_pack,
    gather_evidence,
    metric_kind,
    metric_label,
    metric_meaning as metric_meaning_for,
    metric_unit,
    pack_evidence_ids,
    read_sdkconfig,
    render_evidence,
    sample_series,
)
from mcu_insight.simulator import iter_frames  # noqa: E402
from mcu_insight.store import Store  # noqa: E402
from mcu_insight.telemetry import parse_frame  # noqa: E402


class TempWorkspace:
    """Shared helpers. Not a TestCase, so unittest does not collect it twice."""

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

    def config(self, rules) -> Path:
        path = self.dir / "budget.json"
        path.write_text(json.dumps({"rules": rules}), encoding="utf-8")
        return path

    def pack_for(self, scenario: str, rules, count: int = 10,
                 database: str = "captures/demo.db", **kwargs) -> dict:
        store = self.store_for(scenario, count=count)
        config = self.config(rules)
        report = check_store(store, load_config(config))
        return gather_evidence(store, report, database=database,
                               config_path=config, **kwargs)

class EvidenceTest(TempWorkspace, unittest.TestCase):
    def test_violation_leads_the_pack(self):
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 135000, "stat": "min"}],
                             count=12)
        self.assertFalse(pack["verdict"]["ok"])
        self.assertEqual("heap.min", pack["verdict"]["violations"][0]["metric"])

        text = render_evidence(pack)
        self.assertIn("VIOLATION heap.min", text)
        series = next(m for m in pack["metrics"] if m["name"] == "heap.min")
        self.assertGreater(len(series["series_ms"]), 5)
        self.assertIn("series heap.min", text)

    def test_healthy_capture_says_so_out_loud(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1000, "stat": "min"}], count=8)
        self.assertIn("VIOLATION none", render_evidence(pack))

    def test_a_fault_capture_declares_what_it_is(self):
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 10 ** 9, "stat": "min"}],
                             count=8, database="captures/fault-heap.db")
        text = render_evidence(pack)
        self.assertIn("provenance", text)
        self.assertIn("heap leak is injected on purpose", text)
        self.assertIn("deliberate test behaviour", text)

    def test_an_ordinary_capture_claims_no_provenance(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1, "stat": "min"}], count=6)
        self.assertEqual([], pack["provenance"])

    def test_a_free_text_note_is_carried_into_the_pack(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1, "stat": "min"}], count=6,
                             note=["captured after the 100 Hz change, board on 4 MB flash"])
        self.assertIn("100 Hz change", render_evidence(pack))
    def test_capture_shape_is_recorded(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1000, "stat": "min"}], count=8)
        self.assertEqual(8, pack["capture"]["frames"])
        self.assertEqual(1, pack["capture"]["boot_sessions"])

    def test_sdkconfig_is_narrowed_to_keys_that_move_resources(self):
        project = self.dir / "fw"
        project.mkdir()
        (project / "sdkconfig").write_text(
            "# CONFIG_FOO is not set\n"
            "CONFIG_FREERTOS_HZ=1000\n"
            'CONFIG_COMPILER_OPTIMIZATION_LEVEL="s"\n'
            "CONFIG_SOME_PERIPHERAL_DEFAULT=17\n",
            encoding="utf-8",
        )
        self.assertEqual(
            {"CONFIG_FREERTOS_HZ": "1000", "CONFIG_COMPILER_OPTIMIZATION_LEVEL": "s"},
            read_sdkconfig(project / "sdkconfig"),
        )

    def test_missing_sdkconfig_is_not_an_error(self):
        self.assertEqual({}, read_sdkconfig(self.dir / "nope" / "sdkconfig"))

    def test_working_tree_without_git_is_handled(self):
        project = self.dir / "empty"
        project.mkdir()
        changes = collect_changes(project)
        self.assertFalse(changes["available"])
        self.assertIn("not available", render_evidence(
            self.pack_for("steady", [{"metric": "heap.min", "min": 1000, "stat": "min"}],
                          count=6, project_dir=project)))

    def test_working_tree_is_recorded(self):
        def fake_runner(args, **kwargs):
            if "log" in args:
                out = "5babcdb feat: mark reboots\n"
            elif "status" in args:
                out = " M main/main.c\n"
            else:
                out = " main/main.c | 12 ++++++---\n"
            return subprocess.CompletedProcess(args, 0, out, "")

        changes = collect_changes(self.dir, runner=fake_runner)
        self.assertTrue(changes["available"])
        self.assertEqual("5babcdb feat: mark reboots", changes["head"])
        self.assertEqual([" M main/main.c"], changes["modified"])
        self.assertEqual([" main/main.c | 12 ++++++---"], changes["diffstat"])

    def test_flat_series_is_compressed_to_one_line(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1000, "stat": "min"}], count=8)
        metric = next(m for m in pack["metrics"] if m["name"] == "task.monitor.stack_free_min")
        metric["series_ms"] = [[float(index * 1000), 2000.0] for index in range(8)]
        metric["constant"] = True
        self.assertIn("constant at 2,000 across 8 samples", render_evidence(pack))

    def test_changed_metrics_are_labelled_with_their_direction(self):
        steady = self.store_for("steady", count=6, name="steady.db")
        baseline_path = self.dir / "baseline.json"
        save_baseline(steady, baseline_path)

        store = self.store_for("heap-leak", count=40, name="leak.db")
        config = self.config([{"metric": "heap.min", "min": 10 ** 9, "stat": "min"}])
        report = check_store(store, load_config(config), load_baseline(baseline_path), baseline_path)
        pack = gather_evidence(store, report, database="leak.db")

        labels = {change["metric"]: change["direction"] for change in pack["verdict"]["changed"]}
        self.assertEqual("worse", labels.get("heap.min"))
        self.assertIn("changed (worse) heap.min", render_evidence(pack))
    def test_downsampling_keeps_both_ends(self):
        points = [(float(i), float(i)) for i in range(100)]
        series, downsampled = sample_series(points, 10)
        self.assertTrue(downsampled)
        self.assertEqual(10, len(series))
        self.assertEqual([0.0, 0.0], series[0])
        self.assertEqual([99.0, 99.0], series[-1])

    def test_short_series_is_left_alone(self):
        series, downsampled = sample_series([(0.0, 1.0), (1.0, 2.0)], 10)
        self.assertFalse(downsampled)
        self.assertEqual([[0.0, 1.0], [1.0, 2.0]], series)

    def test_pack_is_trimmed_to_the_size_budget(self):
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 10 ** 9, "stat": "min"}],
                             count=60, max_samples=60)
        full = render_evidence(pack)
        trimmed, text = fit_pack(pack, max_bytes=6_000)
        self.assertTrue(trimmed["truncated"])
        self.assertLess(len(text), len(full))
        self.assertLessEqual(len(text.encode("utf-8")), 6_000)
        self.assertTrue(any(metric.get("dropped_series") for metric in trimmed["metrics"]))
        # the flagged series is the one thing that must survive
        kept = next(m for m in trimmed["metrics"] if m["name"] == "heap.min")
        self.assertTrue(kept["series_ms"])

    def test_pack_inside_the_budget_is_left_alone(self):
        pack = self.pack_for("steady", [{"metric": "heap.min", "min": 1000, "stat": "min"}], count=6)
        trimmed, text = fit_pack(pack, max_bytes=24_000)
        self.assertFalse(trimmed["truncated"])
        self.assertEqual(render_evidence(pack), text)

    def test_prompt_demands_cited_evidence(self):
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 10 ** 9, "stat": "min"}],
                             count=8)
        prompt = build_prompt(render_evidence(pack))
        self.assertIn("[E1]", prompt)
        self.assertIn("Cite an evidence id", prompt)
        self.assertIn("Do not invent", prompt)
        self.assertIn("heap.min", prompt)


class DiagnoseCommandTest(TempWorkspace, unittest.TestCase):
    def database(self) -> Path:
        self.store_for("heap-leak", count=10, name="cli.db")
        return self.dir / "cli.db"

    def test_dry_run_prints_the_pack(self):
        config = self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["diagnose", "--db", str(self.database()), "--config", str(config),
                         "--dry-run"])
        self.assertEqual(0, code)
        self.assertIn("MCU-INSIGHT EVIDENCE PACK", buffer.getvalue())
        self.assertIn("VIOLATION heap.min", buffer.getvalue())

    def test_without_dry_run_it_refuses_to_invent_a_model_call(self):
        config = self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["diagnose", "--db", str(self.database()), "--config", str(config)])
        self.assertEqual(2, code)
        self.assertIn("does not call a model", stderr.getvalue())

    def test_note_flag_reaches_the_pack(self):
        config = self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["diagnose", "--db", str(self.database()), "--config", str(config),
                         "--dry-run", "--note", "board is on hotspot Boo, 4 MB flash"])
        self.assertEqual(0, code)
        self.assertIn("board is on hotspot Boo", buffer.getvalue())
    def test_out_writes_the_prompt(self):
        config = self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}])
        target = self.dir / "prompt.txt"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["diagnose", "--db", str(self.database()), "--config", str(config),
                         "--dry-run", "--out", str(target)])
        self.assertEqual(0, code)
        self.assertIn("Cite an evidence id", target.read_text(encoding="utf-8"))


    def test_units_and_meaning_reach_the_rendered_lines(self):
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 10 ** 9, "stat": "min"}],
                             count=8,
                             metric_meta={"heap.min": {"description": "smallest free heap since boot"}})
        text = render_evidence(pack)
        self.assertIn("heap.min (B, high-water mark, monotonic since boot)", text)
        self.assertIn("smallest free heap since boot", text)
        self.assertIn("series heap.min [B]", text)


class MetricMeaningTest(unittest.TestCase):
    """A number without a unit is not evidence, it is arithmetic."""

    def test_units_come_from_the_naming_convention(self):
        for name, unit in (
            ("heap.free", "B"),
            ("heap.min", "B"),
            ("task.wifi.stack_free_min", "B"),
            ("custom.arena_used_bytes", "B"),
            ("custom.infer_p99_us", "us"),
            ("custom.loop_period_ms", "ms"),
            ("custom.idle0_pct", "%"),
            ("custom.sample_rate_hz", "Hz"),
            ("custom.infer_samples", "count"),
            ("net.rssi", "dBm"),
        ):
            with self.subTest(metric=name):
                self.assertEqual(unit, metric_unit(name))

    def test_a_name_that_implies_nothing_gets_no_unit(self):
        # Guessing a unit is worse than admitting there is none.
        self.assertIsNone(metric_unit("custom.model_class"))
        self.assertIsNone(metric_unit("custom.model_expected"))

    def test_high_water_marks_are_labelled_as_monotonic(self):
        self.assertIn("high-water mark", metric_kind("heap.min"))
        self.assertIn("high-water mark", metric_kind("task.main.stack_free_min"))
        self.assertEqual("instantaneous sample", metric_kind("heap.free"))

    def test_counters_and_configured_values_are_separated(self):
        self.assertEqual("counter since boot", metric_kind("custom.infer_samples"))
        self.assertEqual("clock since boot", metric_kind("uptime_ms"))
        self.assertEqual("configured priority", metric_kind("task.wifi.prio"))
        self.assertEqual("configured stack size", metric_kind("task.wifi.stack_total"))

    def test_the_project_file_overrides_the_convention(self):
        meta = {"custom.sample_rate_hz": {"kind": "build-time constant",
                                          "description": "set at build time"}}
        meaning = metric_meaning_for("custom.sample_rate_hz", meta)
        self.assertEqual("build-time constant", meaning["kind"])
        self.assertEqual("Hz", meaning["unit"])  # still inferred from the name
        self.assertEqual("set at build time", meaning["description"])
class AuditTest(TempWorkspace, unittest.TestCase):
    """The tool checks the answer, not the model."""

    def pack_text(self) -> str:
        pack = self.pack_for("heap-leak", [{"metric": "heap.min", "min": 135000, "stat": "min"}],
                             count=12)
        return render_evidence(pack)

    def test_fabricated_evidence_id_is_caught(self):
        result = audit_answer("The heap fell [E11] and the cause is obvious [E99].",
                              self.pack_text())
        self.assertEqual(["E99"], result["unknown_ids"])
        self.assertFalse(result["ok"])

    def test_invented_metric_name_is_caught(self):
        result = audit_answer("The custom.heap_fragmentation_index is over budget [E2].",
                              self.pack_text())
        self.assertEqual(["custom.heap_fragmentation_index"], result["unknown_metrics"])
        self.assertFalse(result["ok"])

    def test_paths_are_not_mistaken_for_metrics(self):
        # Real false positives from a real answer: a capture file and a build
        # artefact whose names end in a metric family the pack knows.
        answer = ("- Re-run the capture that produced captures\\fault-heap.db and compare\n"
                  "  build/heap.map and notes/heap.md against it [E2].\n")
        result = audit_answer(answer, self.pack_text())
        self.assertEqual([], result["unknown_metrics"])
        self.assertTrue(result["ok"])

    def test_a_genuine_invented_name_is_still_caught_next_to_a_path(self):
        answer = "- Compare captures\\fault-heap.db [E2] with custom.heap_index [E2].\n"
        result = audit_answer(answer, self.pack_text())
        self.assertEqual(["custom.heap_index"], result["unknown_metrics"])
    def test_source_file_names_are_not_mistaken_for_metrics(self):
        result = audit_answer("The allocation is in main.c and dac_wave.c [E2].", self.pack_text())
        self.assertEqual([], result["unknown_metrics"])
        self.assertTrue(result["ok"])

    def test_a_wrapped_bullet_is_one_claim(self):
        answer = ("- The heap drains 1.5 KB per loop because a payload buffer is never\n"
                  "  released, which matches the falling largest block [E11].\n"
                  "- The transmitter task keeps a pointer past the end of the buffer.\n")
        result = audit_answer(answer, self.pack_text())
        self.assertTrue(result["ok"])
        self.assertEqual(1, len(result["uncited_lines"]))
        self.assertIn("transmitter task", result["uncited_lines"][0])

    def test_a_fully_cited_answer_passes(self):
        text = self.pack_text()
        ids = sorted(pack_evidence_ids(text), key=lambda item: int(item[1:]))
        answer = (f"- The capture is described by [{ids[0]}] and the rules by [{ids[4]}],\n"
                  f"  {ids[5]}.\n")
        result = audit_answer(answer, text)
        self.assertTrue(result["ok"])
        self.assertEqual([], result["uncited_lines"])

    def test_headings_and_short_lines_are_not_claims(self):
        answer = "Root cause\n-----------\n- The heap fell [E11] as the series shows.\n"
        result = audit_answer(answer, self.pack_text())
        self.assertEqual([], result["uncited_lines"])


class AuditCommandTest(TempWorkspace, unittest.TestCase):
    def test_out_needs_no_dry_run_and_points_at_the_audit(self):
        self.store_for("heap-leak", count=10, name="cli.db")
        config = self.config([{"metric": "heap.min", "min": 135000, "stat": "min"}])
        target = self.dir / "prompt.txt"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["diagnose", "--db", str(self.dir / "cli.db"),
                         "--config", str(config), "--out", str(target)])
        self.assertEqual(0, code)
        self.assertIn("mcu-insight audit --pack", buffer.getvalue())
        self.assertIn("EVIDENCE", target.read_text(encoding="utf-8"))

    def test_audit_command_fails_on_a_fabricated_id(self):
        pack = self.dir / "prompt.txt"
        pack.write_text("- The capture is described by [E1] and more.\n", encoding="utf-8")
        answer = self.dir / "answer.md"
        answer.write_text("- The leak is real [E1] and also caused by [E404] here.\n",
                          encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["audit", "--pack", str(pack), "--answer", str(answer)])
        self.assertEqual(1, code)
        self.assertIn("E404", buffer.getvalue())
if __name__ == "__main__":
    unittest.main()