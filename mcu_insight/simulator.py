"""A synthetic device, so the host side can be developed without hardware.

Every scenario reproduces a failure mode the tool is meant to catch, which also
makes this the fault injector for the regression stage.
"""

from __future__ import annotations

import json
import random
from typing import Iterator

SCENARIOS = ("steady", "heap-leak", "stack-creep", "latency-jitter")

#: name, priority, stack size, stack free at boot (bytes)
TASKS = (
    ("sensor", 3, 4096, 2860),
    ("control", 2, 4096, 3110),
    ("display", 2, 4096, 2400),
    ("monitor", 1, 4096, 1950),
)

HEAP_FREE_AT_BOOT = 145_000


def make_frame(
    scenario: str = "steady",
    seq: int = 0,
    uptime_ms: float = 0.0,
    device: str = "esp32-light-monitor",
    firmware: str = "sim0001",
    rng: random.Random | None = None,
) -> dict:
    """Build one frame as a plain dict."""
    rng = rng or random.Random(seed=1)

    heap_free = HEAP_FREE_AT_BOOT + rng.randint(-700, 700)
    if scenario == "heap-leak":
        heap_free -= 1500 * seq
    largest = max(0, heap_free - 12_000)

    tasks = []
    for name, priority, total, free_at_boot in TASKS:
        free = free_at_boot + rng.randint(-24, 24)
        if scenario == "stack-creep" and name == "monitor":
            free -= 45 * seq
        tasks.append(
            {
                "name": name,
                "prio": priority,
                "stack_total": total,
                "stack_free_min": max(96, int(free)),
            }
        )

    jitter = 150 + rng.randint(-25, 25)
    if scenario == "latency-jitter" and seq % 7 == 0:
        jitter *= 18

    return {
        "v": 1,
        "device": device,
        "fw": firmware,
        "seq": seq,
        "uptime_ms": uptime_ms,
        "heap": {
            "free": int(heap_free),
            "min": int(heap_free - rng.randint(0, 400)),
            "largest": int(largest),
        },
        "net": {
            "rssi": -50 + rng.randint(-8, 8),
            "disconnects": 0,
            "mqtt_online": 1,
        },
        "tasks": tasks,
        "custom": {"loop_jitter_us": int(jitter), "loop_period_ms": 200},
    }


def iter_frames(
    scenario: str = "steady",
    count: int = 10,
    interval_ms: float = 1000.0,
    device: str = "esp32-light-monitor",
    firmware: str = "sim0001",
    seed: int = 1,
    start_uptime_ms: float = 0.0,
) -> Iterator[str]:
    """Yield JSON lines, one per frame."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    rng = random.Random(seed)
    uptime = start_uptime_ms
    for seq in range(count):
        frame = make_frame(scenario, seq, uptime, device, firmware, rng)
        yield json.dumps(frame, separators=(",", ":"))
        uptime += interval_ms
