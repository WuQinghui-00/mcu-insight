"""Assemble the evidence a diagnosis needs, offline and testable.

Rules and statistics decide *whether* something is wrong. This module collects
*what is known* about the device, the build and the working tree into one
bounded, citable evidence pack. Turning that pack into prose is a separate
step, so everything here runs without a network connection or an API key,
which is also what makes it testable.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

from .analysis import ImageReport
from .checks import CheckReport, change_verdict
from .store import Store

#: Size budget for the rendered pack. Evidence that cannot be read in one go is
#: evidence that will be ignored, and an unbounded dump is not a prompt.
DEFAULT_MAX_BYTES = 24_000

#: Samples kept per metric when the recorded series is longer than this.
DEFAULT_MAX_SAMPLES = 40

#: Metrics worth a full series even when no rule mentions them.
INTERESTING = (
    "heap.min",
    "heap.free",
    "heap.largest",
    "stack_free_min",
    "idle",
    "sleep",
    "infer_p",
    "infer_max",
    "model_accuracy",
    "arena",
)

#: sdkconfig keys that change resource behaviour. The real file is thousands of
#: peripheral defaults; only these explain a memory or latency regression.
SDKCONFIG_PREFIXES = (
    "CONFIG_COMPILER_OPTIMIZATION",
    "CONFIG_FREERTOS_HZ",
    "CONFIG_FREERTOS_IDLE_TIME_BEFORE_SLEEP",
    "CONFIG_FREERTOS_USE_TRACE_DATA",
    "CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS",
    "CONFIG_ESP_TASK_WDT",
    "CONFIG_ESP_INT_WDT",
    "CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ",
    "CONFIG_ESP_MAIN_TASK_STACK_SIZE",
    "CONFIG_ESP_TIMER_TASK_STACK_SIZE",
    "CONFIG_ESP_SYSTEM_EVENT_TASK_STACK_SIZE",
    "CONFIG_PM_",
    "CONFIG_SPIRAM",
    "CONFIG_LOG_DEFAULT_LEVEL",
)


def sample_series(points: list[tuple[float, float]],
                  max_samples: int = DEFAULT_MAX_SAMPLES) -> tuple[list[list[float]], bool]:
    """Down-sample a series, always keeping the first and the last sample."""
    if max_samples < 2:
        raise ValueError("max_samples must be at least 2")
    if len(points) <= max_samples:
        return [[float(x), float(y)] for x, y in points], False
    step = (len(points) - 1) / (max_samples - 1)
    picked = [points[round(index * step)] for index in range(max_samples)]
    return [[float(x), float(y)] for x, y in picked], True


def read_sdkconfig(path: str | Path) -> dict[str, str]:
    """The sdkconfig entries that can move a resource number, in file order."""
    file = Path(path)
    if not file.is_file():
        return {}
    items: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith(SDKCONFIG_PREFIXES):
            items[key] = value.strip().strip('"')
    return dict(sorted(items.items()))


def collect_changes(project_dir: str | Path, runner=subprocess.run) -> dict:
    """What the working tree looks like, so a fresh edit is part of the evidence."""
    folder = Path(project_dir)
    if not folder.is_dir():
        return {"project": str(folder), "available": False}

    def git(*args: str):
        try:
            done = runner(["git", "-C", str(folder), *args],
                          capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        # Not stripped: " M file" means the change is unstaged while "M  file"
        # means it is staged, so the leading column carries information.
        return done.stdout

    head = git("--no-pager", "log", "-1", "--oneline")
    if head is None:
        return {"project": str(folder), "available": False}
    status = git("--no-pager", "status", "--short") or ""
    diffstat = git("--no-pager", "diff", "--stat", "HEAD") or ""
    return {
        "project": str(folder),
        "available": True,
        "head": head.splitlines()[0].strip() if head.strip() else "",
        "modified": [line.rstrip() for line in status.splitlines() if line.strip()][:20],
        "diffstat": [line.rstrip() for line in diffstat.splitlines() if line.strip()][-10:],
    }


def _build_summary(build: ImageReport | None, partition: int | None) -> dict | None:
    if build is None:
        return None
    used = build.actual_image_size or build.estimated_image_size
    return {
        "flash": used,
        "flash_estimated": build.estimated_image_size,
        "partition": partition,
        "partition_used_pct": round(used / partition * 100, 1) if partition else None,
        "dram_used": build.dram.used,
        "dram_capacity": build.dram.capacity,
        "iram_used": build.iram.used,
        "top_components": [[name, size] for name, size in list(build.components.items())[:8]],
    }


def gather_evidence(store: Store, report: CheckReport, *, database: str = "",
                    device: str | None = None, config_path: str | Path | None = None,
                    build: ImageReport | None = None, partition: int | None = None,
                    project_dir: str | Path | None = None,
                    max_samples: int = DEFAULT_MAX_SAMPLES) -> dict:
    """Collect everything that is known, and nothing that is invented."""
    devices = report.devices or store.devices()
    target = device or (devices[0] if devices else "")
    stats = store.metric_stats(target) if target else {}

    flagged = [violation.metric for violation in report.violations]
    interesting = [name for name in stats if any(hint in name for hint in INTERESTING)]

    with_series, without_series = [], []
    for name in dict.fromkeys(flagged + interesting):
        entry = stats.get(name)
        if entry is None:
            continue
        series, downsampled = sample_series(store.series(target, name), max_samples)
        with_series.append({
            "name": name,
            "count": entry.count,
            "last": entry.last,
            "min": entry.minimum,
            "max": entry.maximum,
            "series_ms": series,
            "downsampled": downsampled,
            "constant": len({value for _, value in series}) <= 1,
        })

    covered = {metric["name"] for metric in with_series}
    for name in sorted(stats):
        if name in covered:
            continue
        entry = stats[name]
        without_series.append({
            "name": name,
            "count": entry.count,
            "last": entry.last,
            "min": entry.minimum,
            "max": entry.maximum,
        })

    config_file = Path(project_dir) / "sdkconfig" if project_dir else None
    changes = collect_changes(project_dir) if project_dir else {"available": False}

    return {
        "device": target,
        "firmware": store.latest_firmware(target) if target else "",
        "budget": str(config_path) if config_path else "",
        "capture": {
            "database": database,
            "frames": store.frame_count(target) if target else 0,
            "boot_sessions": len(store.sessions(target)) if target else 0,
            "reboots": len(store.reboot_frames(target)) if target else 0,
        },
        "verdict": {
            "ok": report.ok,
            "violations": [
                {"metric": violation.metric, "detail": violation.describe()}
                for violation in report.violations
            ],
            "rules": [
                {"pattern": rule.pattern, "bound": rule.describe()} for rule in report.rules
            ],
            "changed": [
                {
                    "metric": change.metric,
                    "baseline": change.baseline,
                    "current": change.current,
                    "percent": change.percent,
                    "direction": {"bad": "worse", "ok": "better"}.get(
                        change_verdict(change.metric, change.delta), "moved"
                    ),
                }
                for change in report.changes
            ],
        },
        "metrics": with_series,
        "others": without_series,
        "build": _build_summary(build, partition),
        "config": (
            {"file": str(config_file), "items": read_sdkconfig(config_file)}
            if config_file else None
        ),
        "changes": changes,
        "notes": [],
        "truncated": False,
    }


def render_evidence(pack: dict) -> str:
    """Render the pack as flat, numbered lines that a reader can cite."""
    lines: list[str] = []
    counter = 0

    def add(text: str) -> None:
        nonlocal counter
        counter += 1
        lines.append(f"[E{counter}] {text}")

    capture = pack["capture"]
    add(f"device {pack['device']} (firmware {pack['firmware'] or 'unknown'})")
    add(f"capture {capture['frames']} frames over {capture['boot_sessions']} boot session(s)"
        f" from {capture['database'] or 'an unnamed database'}")
    if pack.get("budget"):
        add(f"budget file {pack['budget']}")

    verdict = pack["verdict"]
    if verdict["violations"]:
        for violation in verdict["violations"]:
            add(f"VIOLATION {violation['metric']}: {violation['detail']}")
    else:
        add("VIOLATION none: every budget rule passed for this capture")
    for rule in verdict["rules"]:
        add(f"rule {rule['pattern']} {rule['bound']}")
    for change in verdict["changed"]:
        percent = "" if change["percent"] is None else f" ({change['percent']:+.1%})"
        add(f"changed ({change['direction']}) {change['metric']}: baseline"
            f" {change['baseline']:,.0f} -> now {change['current']:,.0f}{percent}")

    for metric in pack["metrics"]:
        add(f"metric {metric['name']}: last {metric['last']:,.0f}, min {metric['min']:,.0f},"
            f" max {metric['max']:,.0f} over {metric['count']} samples")
        series = metric.get("series_ms") or []
        if metric.get("constant"):
            # A flat series costs the same as a moving one and says far less.
            add(f"series {metric['name']}: constant at {series[0][1]:,.0f} across"
                f" {len(series)} samples")
        elif series:
            facts = ", ".join(f"{point[0]:,.0f}ms={point[1]:,.0f}" for point in series)
            note = " (down-sampled)" if metric.get("downsampled") else ""
            add(f"series {metric['name']}{note}: {facts}")
        elif metric.get("dropped_series"):
            add(f"series {metric['name']}: dropped to fit the size budget")
    for other in pack["others"]:
        add(f"metric {other['name']}: last {other['last']:,.0f}, min {other['min']:,.0f},"
            f" max {other['max']:,.0f}")

    build = pack["build"]
    if build:
        span = ""
        if build.get("partition"):
            span = (f" of a {build['partition']:,} B partition"
                    f" ({build['partition_used_pct']}%)")
        add(f"build flash {build['flash']:,} B{span},"
            f" DRAM {build['dram_used']:,}/{build['dram_capacity']:,} B,"
            f" IRAM {build['iram_used']:,} B")
        if build["top_components"]:
            add("build top components: "
                + "; ".join(f"{name} {size:,} B" for name, size in build["top_components"]))

    config = pack["config"]
    if config and config["items"]:
        add(f"config {config['file']}: "
            + "; ".join(f"{key}={value}" for key, value in config["items"].items()))

    changes = pack["changes"]
    if changes.get("available"):
        add(f"working tree {changes['project']} at {changes['head']}")
        for line in changes["modified"]:
            add(f"modified {line}")
        for line in changes["diffstat"]:
            add(f"diffstat {line.strip()}")
    else:
        add("working tree not available: no project directory or not a git checkout")

    for note in pack.get("notes", []):
        add(f"note {note}")

    return "MCU-INSIGHT EVIDENCE PACK\n" + "\n".join(lines) + "\n"


def fit_pack(pack: dict, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[dict, str]:
    """Drop the least relevant series until the rendered pack fits the budget."""
    work = copy.deepcopy(pack)
    text = render_evidence(work)
    if len(text.encode("utf-8")) <= max_bytes:
        return work, text

    flagged = {violation["metric"] for violation in work["verdict"]["violations"]}
    droppable = sorted(
        [metric for metric in work["metrics"]
         if metric["name"] not in flagged and metric.get("series_ms")],
        key=lambda metric: -len(metric["series_ms"]),
    )
    for metric in droppable:
        metric["series_ms"] = []
        metric["dropped_series"] = True
        text = render_evidence(work)
        if len(text.encode("utf-8")) <= max_bytes:
            break

    work["truncated"] = True
    text = render_evidence(work)
    if len(text.encode("utf-8")) > max_bytes:
        work["notes"].append("pack still exceeds the size budget after dropping series")
        text = render_evidence(work)
    return work, text


PROMPT = """You are diagnosing a resource problem in ESP-IDF / FreeRTOS firmware
on an ESP32-class device.

Answer from the evidence below and nothing else:

1. Cite an evidence id such as [E12] for every factual claim. A claim with no id
   is a guess, and a guess is worse than saying nothing.
2. If the evidence cannot separate two explanations, say so, and name the
   measurement that would separate them.
3. Do not invent metric names, values, files or configuration keys. If the pack
   does not contain something you need, ask for it.
4. The checks only say *that* a budget was missed. Explain *why*, using the
   series, the build numbers and the diff.

Answer in this shape:

- Root cause: the single most likely one, a confidence level, and the evidence
  ids behind it.
- Alternatives: what else could produce this, and which observation would rule
  each one out.
- Fix: the smallest change that addresses the root cause.
- Verification: which command to run afterwards and which metric should move,
  and by roughly how much.

EVIDENCE
--------
"""


def build_prompt(evidence_text: str) -> str:
    """The instruction block plus the pack, ready to hand to a model."""
    return PROMPT + evidence_text