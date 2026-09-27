"""Run the fault injection matrix against a board and assert detection.

The point is evidence, not a demo: for every injected fault the budget checks
must report a violation, and the clean baseline must stay clean. The runner
encodes three lessons learned the hard way:

* the first console command after a reset is eaten by the bootloader, so a
  throwaway command is sent first;
* a memory leak is cumulative, so every case starts from a fresh boot;
* a running average restarted by that boot needs a warm-up, which the rules
  express with min_samples.

Requires firmware built with ENABLE_FAULT_INJECTION = 1.

    python tools/fault_matrix.py --port COM19
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mcu_insight import checks
from mcu_insight.store import Store
from mcu_insight.telemetry import iter_frames

DEVICE = "esp32-signal-processor"

#: label, fault mode, should the check fail?
CASES = [
    ("baseline", "off", False),
    ("heap leak", "heap", True),
    ("stack frame", "stack", True),
    ("busy wait", "busy", True),
]

#: metric, which observation to show
INTERESTING = [
    ("heap.min", "minimum"),
    ("task.fault_busy.stack_free_min", "minimum"),
    ("task.main.stack_free_min", "minimum"),
    ("custom.idle0_pct", "minimum"),
]


@dataclass
class Result:
    label: str
    mode: str
    expected_fail: bool
    frames: int = 0
    failed: bool = False
    violations: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def detected(self) -> bool:
        return self.failed

    @property
    def matches_expectation(self) -> bool:
        return self.detected == self.expected_fail


def reboot(port, settle: float) -> None:
    """Pulse RTS to reset the board, then wait for it to come up."""
    port.setDTR(False)
    port.setRTS(True)
    time.sleep(0.2)
    port.setRTS(False)
    time.sleep(settle)


def send(port, command: str, pause: float = 0.6) -> None:
    port.write((command + "\n").encode("ascii"))
    time.sleep(pause)


def collect(port, seconds: float, db_path: Path) -> int:
    if db_path.exists():
        db_path.unlink()
    frames = 0
    with Store(db_path) as store:
        deadline = time.time() + seconds
        while time.time() < deadline:
            line = port.readline().decode("utf-8", "replace")
            if not line.lstrip().startswith("{"):
                continue
            try:
                store.add(next(iter(iter_frames(iter([line])))))
                frames += 1
            except Exception:
                continue
    return frames


def run_case(port, label: str, mode: str, expected_fail: bool, seconds: float,
             config: dict, out_dir: Path) -> Result:
    result = Result(label=label, mode=mode, expected_fail=expected_fail)

    reboot(port, settle=9.0)
    send(port, "FAULT off")             # this one is eaten by the bootloader
    send(port, "FAULT " + mode)

    db_path = out_dir / ("fault-" + mode + ".db")
    result.frames = collect(port, seconds, db_path)

    with Store(db_path) as store:
        report = checks.check_store(store, config)
        stats = store.metric_stats(DEVICE)
        result.failed = not report.ok
        result.violations = [v.describe() for v in report.violations]
        for name, observation in INTERESTING:
            entry = stats.get(name)
            if entry is not None:
                result.metrics[name] = getattr(entry, observation)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM19")
    parser.add_argument("--seconds", type=float, default=100.0,
                        help="measurement window per case")
    parser.add_argument("--config",
                        default=str(REPO_ROOT / "budgets" / "fault_matrix.json"))
    parser.add_argument("--out", default=str(REPO_ROOT / "captures"))
    args = parser.parse_args(argv)

    try:
        import serial
    except ImportError:
        print("error: pyserial is required (it ships with the ESP-IDF Python env)",
              file=sys.stderr)
        return 2

    config = checks.load_config(args.config)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    with serial.Serial(args.port, 115200, timeout=0.3) as port:
        for label, mode, expected_fail in CASES:
            print("--- " + label + ": FAULT " + mode + " ---", flush=True)
            result = run_case(port, label, mode, expected_fail, args.seconds,
                              config, out_dir)
            results.append(result)
            detail = ", ".join(result.violations) if result.violations else "no violation"
            state = "FAIL" if result.detected else "PASS"
            print("    %d frames, %s: %s" % (result.frames, state, detail), flush=True)
        send(port, "FAULT off")

    print()
    header = "%-12s%8s%10s%11s%10s%7s   %8s  %8s" % (
        "injection", "frames", "heap.min", "fault stk", "main stk", "idle0",
        "detected", "expected")
    print(header)
    print("-" * len(header))
    all_ok = True
    for result in results:
        cells = ""
        for name, _ in INTERESTING:
            value = result.metrics.get(name)
            cells += "%10s" % (("%d" % value) if value is not None else "-")
        verdict = "yes" if result.detected else "no"
        expected = "yes" if result.expected_fail else "no"
        mark = "" if result.matches_expectation else "   <-- MISMATCH"
        all_ok = all_ok and result.matches_expectation
        print("%-12s%8d%s   %8s  %8s%s" % (
            result.label, result.frames, cells, verdict, expected, mark))

    print()
    if all_ok:
        print("RESULT: detection verified for every case")
    else:
        print("RESULT: expectation mismatch")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())