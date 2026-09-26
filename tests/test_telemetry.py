"""Unit tests for telemetry frames, storage and collection."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.collector import collect  # noqa: E402
from mcu_insight.simulator import SCENARIOS, make_frame  # noqa: E402
from mcu_insight.simulator import iter_frames as simulate_frames  # noqa: E402
from mcu_insight.store import Store  # noqa: E402
from mcu_insight.telemetry import (  # noqa: E402
    TelemetryError,
    looks_like_frame,
    parse_frame,
)
from mcu_insight.telemetry import iter_frames as telemetry_frames  # noqa: E402
from mcu_insight.telemetry_report import render_summary  # noqa: E402

GOOD_FRAME = {
    "v": 1,
    "device": "esp32-light-monitor",
    "fw": "abc1234",
    "seq": 7,
    "uptime_ms": 12_000,
    "heap": {"free": 145_000, "min": 140_000, "largest": 130_000},
    "net": {"rssi": -52, "mqtt_online": True},
    "tasks": [
        {"name": "sensor", "prio": 3, "stack_total": 4096, "stack_free_min": 2048},
        {"name": "monitor", "prio": 1, "stack_total": 4096, "stack_free_min": 1950},
    ],
    "custom": {"loop_jitter_us": 180},
}


def line(**overrides) -> str:
    data = json.loads(json.dumps(GOOD_FRAME))
    data.update(overrides)
    return json.dumps(data)


class ParseTest(unittest.TestCase):
    def test_valid_frame(self) -> None:
        frame = parse_frame(line())
        self.assertEqual(frame.device, "esp32-light-monitor")
        self.assertEqual(frame.firmware, "abc1234")
        self.assertEqual(frame.seq, 7)
        self.assertEqual(frame.uptime_ms, 12_000)
        self.assertEqual(len(frame.tasks), 2)

    def test_flatten_uses_dotted_metric_names(self) -> None:
        values = parse_frame(line()).flatten()
        self.assertEqual(values["heap.free"], 145_000)
        self.assertEqual(values["net.rssi"], -52)
        self.assertEqual(values["task.sensor.stack_free_min"], 2048)
        self.assertEqual(values["task.monitor.stack_total"], 4096)
        self.assertEqual(values["custom.loop_jitter_us"], 180)
        self.assertEqual(values["uptime_ms"], 12_000)

    def test_booleans_become_numbers(self) -> None:
        values = parse_frame(line()).flatten()
        self.assertEqual(values["net.mqtt_online"], 1.0)

    def test_missing_required_fields_are_rejected(self) -> None:
        with self.assertRaises(TelemetryError):
            parse_frame(json.dumps({"device": "d", "uptime_ms": 1}))
        with self.assertRaises(TelemetryError):
            parse_frame(json.dumps({"v": 1, "uptime_ms": 1}))
        with self.assertRaises(TelemetryError):
            parse_frame(json.dumps({"v": 1, "device": "d"}))

    def test_newer_schema_version_is_rejected(self) -> None:
        with self.assertRaises(TelemetryError):
            parse_frame(line(v=2))

    def test_non_json_line_raises(self) -> None:
        with self.assertRaises(TelemetryError):
            parse_frame("I (1234) MAIN: booting")

    def test_malformed_json_raises(self) -> None:
        with self.assertRaises(TelemetryError):
            parse_frame('{"v": 1, "device": "d", "uptime_ms":}')

    def test_unknown_fields_are_ignored(self) -> None:
        frame = parse_frame(line(future_field={"a": 1}))
        self.assertEqual(frame.device, "esp32-light-monitor")

    def test_looks_like_frame_skips_log_lines(self) -> None:
        self.assertFalse(looks_like_frame("I (1234) MAIN: ready"))
        self.assertFalse(looks_like_frame('{"heap": 1}'))
        self.assertTrue(looks_like_frame(line()))


class StreamTest(unittest.TestCase):
    def test_log_noise_is_skipped(self) -> None:
        stream = [
            "I (1162) MAIN: System: LightMonitor v1.0.0\n",
            "W (1390) WIFI: reconnect #1 in 1087 ms\n",
            line() + "\n",
            "I (1500) SENSOR_TASK: State: DARK, Voltage: 500mV\n",
            line(seq=8) + "\n",
            "\n",
        ]
        frames = list(telemetry_frames(iter(stream)))
        self.assertEqual([f.seq for f in frames], [7, 8])

    def test_malformed_frame_propagates(self) -> None:
        broken = '{"v": 1, "device": "d", "uptime_ms": 1, "heap": '
        with self.assertRaises(TelemetryError):
            list(telemetry_frames(iter([broken + "\n"])))


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "telemetry.db"
        self.store = Store(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_roundtrip(self) -> None:
        self.store.add(parse_frame(line(seq=1, uptime_ms=1000)))
        self.store.add(parse_frame(line(seq=2, uptime_ms=2000, heap={"free": 140_000})))
        self.assertEqual(self.store.devices(), ["esp32-light-monitor"])
        self.assertEqual(self.store.frame_count("esp32-light-monitor"), 2)
        self.assertEqual(self.store.uptime_range("esp32-light-monitor"), (1000.0, 2000.0))
        self.assertEqual(self.store.latest_firmware("esp32-light-monitor"), "abc1234")

    def test_metric_stats_track_first_last_and_extremes(self) -> None:
        for seq, free in enumerate([145_000, 143_000, 141_000]):
            self.store.add(parse_frame(line(seq=seq, uptime_ms=seq * 1000, heap={"free": free})))
        stats = self.store.metric_stats("esp32-light-monitor")["heap.free"]
        self.assertEqual(stats.count, 3)
        self.assertEqual(stats.first, 145_000)
        self.assertEqual(stats.last, 141_000)
        self.assertEqual(stats.minimum, 141_000)
        self.assertEqual(stats.maximum, 145_000)
        self.assertEqual(stats.change, -4000)

    def test_series_is_ordered_by_arrival(self) -> None:
        for seq, free in enumerate([100, 200, 300]):
            self.store.add(parse_frame(line(seq=seq, uptime_ms=seq, heap={"free": free})))
        self.assertEqual(self.store.series("esp32-light-monitor", "heap.free"), [(0.0, 100.0), (1.0, 200.0), (2.0, 300.0)])

    def test_summary_mentions_the_device(self) -> None:
        self.store.add(parse_frame(line()))
        text = render_summary(self.store)
        self.assertIn("esp32-light-monitor", text)
        self.assertIn("heap.free", text)

    def test_empty_store_summary(self) -> None:
        self.assertIn("No frames stored yet", render_summary(self.store))


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "telemetry.db"
        self.capture = self.dir / "capture.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_capture(self, lines) -> None:
        self.capture.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_collect_skips_noise_and_counts_errors(self) -> None:
        self._write_capture(
            [
                "I (100) MAIN: hello",
                line(seq=1),
                '{"v": 1, "device": "d", "uptime_ms": ',
                line(seq=2),
            ]
        )
        with Store(self.db) as store:
            stats = collect(f"file:{self.capture}", store)
            self.assertEqual(stats.frames, 2)
            self.assertEqual(stats.skipped, 1)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(store.frame_count("esp32-light-monitor"), 2)

    def test_limit_stops_early(self) -> None:
        self._write_capture([line(seq=i) for i in range(10)])
        with Store(self.db) as store:
            stats = collect(f"file:{self.capture}", store, limit=3)
            self.assertEqual(stats.frames, 3)

    def test_unsupported_source(self) -> None:
        with Store(self.db) as store:
            with self.assertRaises(ValueError):
                collect("carrier-pigeon", store)

    def test_end_to_end_from_the_simulator(self) -> None:
        self._write_capture(list(simulate_frames("heap-leak", count=15, seed=3)))
        with Store(self.db) as store:
            stats = collect(f"file:{self.capture}", store)
            self.assertEqual(stats.frames, 15)
            stats_heap = store.metric_stats("esp32-light-monitor")["heap.free"]
            self.assertLess(stats_heap.last, stats_heap.first)


class SimulatorTest(unittest.TestCase):
    def test_every_scenario_produces_valid_frames(self) -> None:
        for scenario in SCENARIOS:
            frames = [parse_frame(l) for l in simulate_frames(scenario, count=5, seed=1)]
            self.assertEqual(len(frames), 5, scenario)
            for frame in frames:
                self.assertEqual(frame.device, "esp32-light-monitor")

    def test_steady_state_is_flat_within_noise(self) -> None:
        values = [parse_frame(l).heap["free"] for l in simulate_frames("steady", count=20, seed=1)]
        self.assertLess(max(values) - min(values), 3000)

    def test_heap_leak_decreases_monotonically(self) -> None:
        values = [parse_frame(l).heap["free"] for l in simulate_frames("heap-leak", count=10, seed=1)]
        self.assertTrue(all(b < a for a, b in zip(values, values[1:])))

    def test_stack_creep_only_touches_the_monitor_task(self) -> None:
        first = parse_frame(next(iter(simulate_frames("stack-creep", count=1, seed=1))))
        last = parse_frame(list(simulate_frames("stack-creep", count=12, seed=1))[-1])
        by_name = {t.name: t.stack_free_min for t in last.tasks}
        original = {t.name: t.stack_free_min for t in first.tasks}
        self.assertLess(by_name["monitor"], original["monitor"])
        self.assertLess(abs(by_name["sensor"] - original["sensor"]), 100)

    def test_latency_jitter_has_spikes(self) -> None:
        values = [parse_frame(l).custom["loop_jitter_us"] for l in simulate_frames("latency-jitter", count=14, seed=1)]
        self.assertGreater(max(values), 5 * min(values))

    def test_unknown_scenario_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            list(simulate_frames("nope"))

    def test_make_frame_uses_the_given_rng(self) -> None:
        import random

        first = make_frame("steady", 0, rng=random.Random(9))
        second = make_frame("steady", 0, rng=random.Random(9))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
