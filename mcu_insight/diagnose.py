"""Assemble the evidence a diagnosis needs, offline and testable.

Rules and statistics decide *whether* something is wrong. This module collects
*what is known* about the device, the build and the working tree into one
bounded, citable evidence pack. Turning that pack into prose is a separate
step, so everything here runs without a network connection or an API key,
which is also what makes it testable.
"""

from __future__ import annotations

import copy
import re
import subprocess
from pathlib import Path

from .analysis import ImageReport
from .checks import CheckReport, change_verdict
from .store import Store
from .trends import slope_per_second, steady_samples, steady_span_ms

#: Size budget for the rendered pack. Evidence that cannot be read in one go is
#: evidence that will be ignored, and an unbounded dump is not a prompt.
DEFAULT_MAX_BYTES = 36_000

#: Samples kept per metric when the recorded series is longer than this.
DEFAULT_MAX_SAMPLES = 40

#: Patch lines kept in the evidence pack, and per file. A diff the reader
#: skims is worse than none, but a patch that stops before the changed line is
#: worse still: the range that introduced a fault is the one thing that lets
#: an answer name a call site instead of describing a shape.
#: Below this, a slope is printed as flat rather than listed. It is a display
#: threshold, not a judgement: the rule bounds are the judgement.
RATE_NOISE_FLOOR = 1.0

DEFAULT_PATCH_LINES = 300
DEFAULT_PATCH_BODY = 160

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


def collect_changes(project_dir: str | Path, runner=subprocess.run, since: str | None = None,
                    max_lines: int = DEFAULT_PATCH_LINES) -> dict:
    """The working tree and its patch, so the change under test is in evidence.

    ``since`` is the revision the comparison starts from -- normally the one
    the baseline recorded. Without it the patch can only describe the working
    tree, and when the tree is clean the most recent commit is the only
    candidate change; that is a guess about relevance, so it is labelled as
    one rather than presented as the diff.
    """
    folder = Path(project_dir)
    if not folder.is_dir():
        return {"project": str(folder), "available": False}

    def git(*args: str):
        try:
            # Git speaks UTF-8, the platform default on a Chinese Windows is
            # GBK, and a diff with any non-ascii byte in it turns stdout into
            # None without raising here. Decode explicitly.
            done = runner(["git", "-C", str(folder), *args], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=15)
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
    range_args = ["--no-pager", "diff", since] if since else ["--no-pager", "diff", "HEAD"]
    if since:
        label = f"since {since}"
    else:
        label = "working tree against HEAD"
    raw = git(*range_args) or ""
    if not raw.strip() and not since:
        # A clean tree says nothing about the change under test, so fall back
        # to the commit that just landed, and say what it is.
        raw = git("--no-pager", "show", "--patch", "--stat", "HEAD") or ""
        label = "the most recent commit, which need not be the change under test"

    patch = [line.rstrip() for line in raw.splitlines()]
    truncated = len(patch) > max_lines
    return {
        "project": str(folder),
        "available": True,
        "head": head.splitlines()[0].strip() if head.strip() else "",
        "modified": [line.rstrip() for line in status.splitlines() if line.strip()][:20],
        "diffstat": [
            line.rstrip() for line in (git("--no-pager", "diff", "--stat", "HEAD") or "").splitlines()
            if line.strip()
        ][-10:],
        "since": since,
        "patch": patch[:max_lines],
        "patch_lines": len(patch),
        "patch_truncated": truncated,
        "patch_label": label,
    }


#: Units for the fields the telemetry schema defines, where the name alone is
#: not enough to guess.
KNOWN_UNITS = {
    "uptime_ms": "ms",
    "seq": "count",
    "net.rssi": "dBm",
    "net.disconnects": "count",
    "net.reconnects": "count",
    "net.mqtt_online": "flag",
}

#: Kinds that the naming convention gives away, checked in this order.
KNOWN_KINDS = {
    "uptime_ms": "clock since boot",
    "seq": "clock since boot",
    "net.disconnects": "counter since boot",
    "net.reconnects": "counter since boot",
    "net.mqtt_online": "flag",
}


def metric_unit(name: str) -> str | None:
    """The unit implied by a metric name, or None when nothing implies one."""
    lower = name.lower()
    if lower in KNOWN_UNITS:
        return KNOWN_UNITS[lower]
    for suffix, unit in (("_us", "us"), ("_ms", "ms"), ("_hz", "Hz"), ("_pct", "%")):
        if lower.endswith(suffix):
            return unit
    if lower.endswith("_bytes") or lower.endswith("stack_free") or lower.endswith("stack_total"):
        return "B"
    if lower.startswith("heap.") or lower.startswith("arena_") or "stack_free_min" in lower:
        return "B"
    if lower.endswith(("_samples", "_count", "_retries", "_total")):
        return "count"
    if lower.endswith(("_ratio", "_fraction")):
        return "ratio"
    return None


def metric_kind(name: str) -> str:
    """How the number behaves over time, which decides how it may be read."""
    lower = name.lower()
    if lower in KNOWN_KINDS:
        return KNOWN_KINDS[lower]
    if lower.endswith("stack_free_min") or lower.endswith("heap.min"):
        # A high-water mark only ever gets worse. A fall is the deepest point
        # reached since boot, never a recovery, and reading it as a trend line
        # turns start-up into a "leak".
        return "high-water mark, monotonic since boot"
    if lower.endswith(".prio"):
        return "configured priority"
    if lower.endswith("stack_total"):
        return "configured stack size"
    if lower.endswith(("_samples", "_count", "_retries", "_total")):
        return "counter since boot"
    return "instantaneous sample"


def metric_meaning(name: str, meta: dict | None = None) -> dict:
    """Unit, kind and description: the project file wins over the convention."""
    entry = (meta or {}).get(name) or {}
    return {
        "unit": entry.get("unit") or metric_unit(name),
        "kind": entry.get("kind") or metric_kind(name),
        "description": entry.get("description", ""),
    }


def metric_label(entry: dict) -> str:
    """The bracketed annotation that rides along with a metric line."""
    parts = [part for part in (entry.get("unit"), entry.get("kind")) if part]
    return f" ({', '.join(parts)})" if parts else ""


#: Databases written by tools/fault_matrix.py carry the injected fault in their
#: name. Saying so changes the diagnosis completely: a drain that is a test
#: case is not a regression, and a model that does not know the difference will
#: name a call site that is doing exactly what it was told to do.
FAULT_CAPTURES = {
    "fault-off.db": "FAULT off, the baseline case, nothing injected",
    "fault-heap.db": "FAULT heap, a heap leak is injected on purpose",
    "fault-stack.db": "FAULT stack, an oversized stack frame is injected on purpose",
    "fault-busy.db": "FAULT busy, a high priority busy loop is injected on purpose",
}


def capture_provenance(database: str, notes: list[str] | None = None) -> list[str]:
    """What the capture is, decided before its numbers are read as a regression."""
    provenance = []
    name = Path(database).name.lower() if database else ""
    if name in FAULT_CAPTURES:
        provenance.append(
            f"{FAULT_CAPTURES[name]}. The file name says so, so unless it was renamed,"
            " what follows is deliberate test behaviour, not a regression in the"
            " application code."
        )
    for note in notes or ():
        if note:
            provenance.append(note)
    return provenance


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


#: A unified diff starts a new file here.
DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def split_patch(lines: list[str], max_files: int = 6,
                max_body: int = DEFAULT_PATCH_BODY) -> list[tuple[str, list[str]]]:
    """Group a unified diff into (file name, body) pairs, bounded in both."""
    files: list[tuple[str, list[str]]] = []
    for line in lines:
        header = DIFF_HEADER.match(line)
        if header:
            files.append((header.group(2), []))
            continue
        if not files:
            files.append(("<commit header>", []))
        if len(files[-1][1]) < max_body:
            files[-1][1].append(line)
    return files[:max_files]


def resolve_diff_since(explicit: str | None, baseline_meta: dict | None) -> str | None:
    """Which revision to diff from: the flag wins, then the baseline's own."""
    if explicit:
        return explicit
    revision = (baseline_meta or {}).get("revision")
    return revision or None


def gather_evidence(store: Store, report: CheckReport, *, database: str = "",
                    device: str | None = None, config_path: str | Path | None = None,
                    build: ImageReport | None = None, partition: int | None = None,
                    project_dir: str | Path | None = None,
                    note: list[str] | None = None,
                    metric_meta: dict | None = None,
                    diff_since: str | None = None,
                    patch_lines: int = DEFAULT_PATCH_LINES,
                    baseline_meta: dict | None = None,
                    baseline_detail: dict | None = None,
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
        points = store.series(target, name)
        series, downsampled = sample_series(points, max_samples)
        rate = slope_per_second(steady_samples(points)[1])
        with_series.append({
            "name": name,
            "count": entry.count,
            "last": entry.last,
            "min": entry.minimum,
            "max": entry.maximum,
            "series_ms": series,
            "downsampled": downsampled,
            "constant": len({value for _, value in series}) <= 1,
            "rate_per_s": None if rate is None else round(rate, 3),
            **metric_meaning(name, metric_meta),
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
            **metric_meaning(name, metric_meta),
        })

    config_file = Path(project_dir) / "sdkconfig" if project_dir else None
    since = resolve_diff_since(diff_since, baseline_meta)
    changes = (collect_changes(project_dir, since=since, max_lines=patch_lines)
               if project_dir else {"available": False})

    return {
        "device": target,
        "firmware": store.latest_firmware(target) if target else "",
        "budget": str(config_path) if config_path else "",
        "provenance": capture_provenance(database, note),
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
        "baseline_meta": dict(baseline_meta or {}),
        "baseline": _baseline_summary(baseline_detail, target, store),
        "notes": [],
        "truncated": False,
    }


def _baseline_summary(detail: dict | None, device: str, store: Store) -> dict | None:
    """What the baseline was, next to what this capture is.

    The two things a comparison needs and a flat value cannot supply: how long
    the baseline watched for, and how fast each metric was moving while it did.
    """
    if not detail:
        return None
    entry = detail.get(device) or {}
    metrics = entry.get("metrics") or {}
    rates = {
        name: value["rate_per_s"]
        for name, value in metrics.items()
        if isinstance(value, dict) and value.get("rate_per_s") is not None
    }
    uptimes = store.uptimes(device) if device else []
    recorded_uptime = metrics.get("uptime_ms")
    if isinstance(recorded_uptime, dict):
        recorded_uptime = recorded_uptime.get("last")
    return {
        "capture": entry.get("capture") or {},
        "recorded_metrics": len(metrics),
        "rates": rates,
        "recorded_uptime_ms": recorded_uptime,
        "current_steady_span_ms": round(
            steady_span_ms([(uptime, 0.0) for uptime in uptimes]), 1
        ),
    }


def _render_baseline(pack: dict, add) -> None:
    """The baseline block: its shape, its rates, and whether a delta is fair."""
    baseline = pack.get("baseline")
    if not baseline:
        return
    capture = baseline.get("capture") or {}
    base_span = capture.get("steady_span_ms")
    current_span = baseline.get("current_steady_span_ms") or 0.0

    if base_span:
        add(f"baseline capture: {capture.get('frames', 0)} frames,"
            f" {capture.get('boot_sessions', 0)} boot session(s),"
            f" {base_span / 1000:.0f} s of steady state")
    else:
        recorded = baseline.get("recorded_uptime_ms")
        if recorded:
            add(f"baseline capture: not recorded -- the file predates the capture shape."
                f" The one duration it carries is uptime_ms {recorded:,.0f}, the point"
                " the device had reached when the snapshot was taken")
        else:
            add("baseline capture: not recorded -- the baseline file predates the capture"
                " shape, so how long it watched for is unknown")

    comparisons = []
    omitted = 0
    for metric in pack["metrics"]:
        recorded = (baseline.get("rates") or {}).get(metric["name"])
        current = metric.get("rate_per_s")
        if recorded is None or current is None:
            continue
        # A pair that sits still on both sides says nothing, and a line per
        # stationary metric buries the ones that moved.
        if max(abs(recorded), abs(current)) < RATE_NOISE_FLOOR:
            omitted += 1
            continue
        comparisons.append(f"{metric['name']} {recorded:+.1f}/s -> {current:+.1f}/s")
    if comparisons:
        tail = f"; {omitted} more barely move on either side" if omitted else ""
        add("baseline rates per second, then now: " + "; ".join(comparisons) + tail)

    if not base_span:
        recorded = baseline.get("recorded_uptime_ms")
        if recorded and current_span:
            add(f"note the baseline was taken after {recorded / 1000:.0f} s of uptime"
                f" and this capture holds {current_span / 1000:.0f} s of steady state."
                " A level delta on a high-water mark carries part of that difference,"
                " so the rates are the ones to compare; a baseline saved from now on"
                " records its own span and settles it")
        else:
            add("note a level delta cannot be told apart from a shorter soak when the"
                " baseline records no duration; compare rates, and re-save the baseline"
                " so it records its shape")
    elif current_span and max(base_span, current_span) >= 2 * min(base_span, current_span):
        add(f"note the baseline ran {base_span / 1000:.0f} s of steady state and this"
            f" capture {current_span / 1000:.0f} s, so level deltas on high-water marks"
            " carry that difference; the rates above are the ones to compare")
    else:
        add(f"note the baseline ran {base_span / 1000:.0f} s of steady state against"
            f" {current_span / 1000:.0f} s here, so the level deltas are comparable")


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
    for item in pack.get("provenance", []):
        add(f"provenance {item}")
    if pack.get("budget"):
        add(f"budget file {pack['budget']}")
    _render_baseline(pack, add)
    baseline_meta = pack.get("baseline_meta") or {}
    if baseline_meta.get("revision"):
        add(f"baseline revision {baseline_meta['revision']}, recorded when the baseline was saved")
    elif pack.get("budget"):
        add("baseline revision unknown: the baseline file records no revision, so the"
            " diff range has to be given with --diff-since")

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
        line = (f"metric {metric['name']}{metric_label(metric)}: last {metric['last']:,.0f},"
                f" min {metric['min']:,.0f}, max {metric['max']:,.0f}"
                f" over {metric['count']} samples")
        if metric.get("description"):
            line += f"; {metric['description']}"
        add(line)
        series = metric.get("series_ms") or []
        if metric.get("constant"):
            # A flat series costs the same as a moving one and says far less.
            add(f"series {metric['name']}: constant at {series[0][1]:,.0f} across"
                f" {len(series)} samples")
        elif series:
            facts = ", ".join(f"{point[0]:,.0f}ms={point[1]:,.0f}" for point in series)
            note = " (down-sampled)" if metric.get("downsampled") else ""
            unit = f" [{metric['unit']}]" if metric.get("unit") else ""
            add(f"series {metric['name']}{unit}{note}: {facts}")
        elif metric.get("dropped_series"):
            add(f"series {metric['name']}: dropped to fit the size budget")
    for other in pack["others"]:
        line = (f"metric {other['name']}{metric_label(other)}: last {other['last']:,.0f},"
                f" min {other['min']:,.0f}, max {other['max']:,.0f}")
        if other.get("description"):
            line += f"; {other['description']}"
        add(line)

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
        patch = changes.get("patch") or []
        if patch:
            label = changes.get("patch_label", "patch")
            note = ", truncated" if changes.get("patch_truncated") else ""
            add(f"patch ({label}): {changes.get('patch_lines', len(patch))} line(s){note}")
            for name, body in split_patch(patch):
                indented = "\n".join("      " + line for line in body)
                add(f"patch file {name}:\n{indented}")
        else:
            add("patch none: the working tree is clean and no revision range was given")
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


#: A citation as the prompt asks for it, or the model simply uses: [E12].
CITATION = re.compile(r"\[E(\d+)\]")

#: Dotted lowercase tokens such as heap.min or task.wifi.stack_free_min. The
#: lookbehind keeps `fault-heap.db` from being read as the metric `heap.db`,
#: because the hyphen, slash and dot say this token is part of a path.
DOTTED = re.compile(r"(?<![\w.\-/\\])[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")

#: File suffixes that are never metric names, however metric-like they look.
FILE_SUFFIXES = frozenset(("c", "h", "cpp", "hpp", "py", "db", "txt", "md", "json",
                           "bin", "map", "elf", "html", "yaml", "yml", "log", "csv",
                           "cmake", "ini", "cfg"))


def looks_like_a_file(token: str) -> bool:
    """True when a dotted token ends in a source or artefact suffix."""
    return token.rsplit(".", 1)[-1] in FILE_SUFFIXES


def pack_evidence_ids(evidence_text: str) -> set[str]:
    """Every id the pack actually defines."""
    return {f"E{int(match)}" for match in CITATION.findall(evidence_text)}


def pack_metric_prefixes(names: set[str]) -> set[str]:
    """Every dotted prefix of a known name: ``task.wifi`` for
    ``task.wifi.stack_free_min``. An answer that says "the task.wifi task" is
    referring to something the pack knows, not inventing a metric.
    """
    prefixes = set()
    for name in names:
        parts = name.split(".")
        for index in range(1, len(parts)):
            prefixes.add(".".join(parts[:index]))
    return prefixes


def pack_rule_names(evidence_text: str) -> set[str]:
    """Names the rules themselves introduce.

    A rule written as ``custom.idle*_pct`` makes ``custom.idle`` a name an
    answer may legitimately use, and the name check should not call that
    invented.
    """
    names = set()
    for line in evidence_text.splitlines():
        if "] rule " in line:
            names.update(DOTTED.findall(line))
    return names


def pack_metric_families(evidence_text: str) -> set[str]:
    """First segment of every metric name in the pack: heap, task, custom, ..."""
    families = set()
    for name in pack_metric_names(evidence_text):
        families.add(name.split(".")[0])
    return families


def pack_metric_names(evidence_text: str) -> set[str]:
    names = set()
    for line in evidence_text.splitlines():
        for marker in ("metric ", "series "):
            position = line.find(marker)
            if position < 0:
                continue
            # "metric heap.min (B, high-water mark): ..." and "series heap.min [B]: ..."
            # both have to parse back to the bare name, or the audit reports a
            # metric as missing from the pack that defines it.
            token = line[position + len(marker):].split(":")[0].strip().split(" ")[0].strip()
            if token:
                names.add(token)
    return names


BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def _claims(answer: str) -> list[str]:
    """Split an answer into claims. A claim is a bullet, not a line.

    Markdown wraps a single sentence across several lines, so judging one line
    at a time reports an uncited claim whenever a citation happens to land on
    the next line.
    """
    claims: list[str] = []
    in_code = False
    for raw in answer.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line:
            continue
        if BULLET.match(line):
            claims.append(BULLET.sub("", line))
        elif line.endswith(":") and len(line) < 80:
            continue  # a heading, not a claim
        elif claims:
            claims[-1] = claims[-1] + " " + line
        else:
            claims.append(line)
    return [claim for claim in claims if len(claim) >= 25]


def audit_answer(answer: str, evidence_text: str) -> dict:
    """Check an answer against the pack instead of trusting it.

    Three failure modes are worth catching, and all three are mechanical:
    an id that the pack never defines, a metric name that does not exist, and
    a claim with no citation at all.
    """
    known_ids = pack_evidence_ids(evidence_text)
    metric_names = pack_metric_names(evidence_text)
    known_names = (metric_names | pack_metric_prefixes(metric_names)
                   | pack_rule_names(evidence_text))
    families = pack_metric_families(evidence_text)

    cited = [f"E{int(match)}" for match in CITATION.findall(answer)]
    unknown_ids = sorted({citation for citation in cited if citation not in known_ids})

    mentioned = {
        token for token in DOTTED.findall(answer)
        if token.split(".")[0] in families and not looks_like_a_file(token)
    }
    unknown_metrics = sorted(
        token for token in mentioned if token not in known_names
    )

    uncited = [claim[:120] for claim in _claims(answer) if not CITATION.search(claim)]

    return {
        # A fabricated id is unambiguous: the pack has no such line. An
        # unknown name is a heuristic, and three of the four answers that
        # tripped it were referring to a path, a labelled line, or a task.
        # Heuristics warn; only ids fail.
        "ok": not unknown_ids,
        "citations": {"total": len(cited), "unique": len(set(cited))},
        "unknown_ids": unknown_ids,
        "unknown_metrics": unknown_metrics,
        "uncited_lines": uncited,
        "known_ids": len(known_ids),
    }


def render_audit(result: dict) -> str:
    lines = ["MCU-INSIGHT ANSWER AUDIT"]
    lines.append(f"citations   : {result['citations']['unique']} distinct of"
                 f" {result['citations']['total']} in the pack ({result['known_ids']} available)")
    if result["unknown_ids"]:
        lines.append("unknown id  : " + ", ".join(result["unknown_ids"])
                     + "  <- not in the pack, so the claim cannot be checked")
    else:
        lines.append("unknown id  : none")
    if result["unknown_metrics"]:
        lines.append("unknown name: " + ", ".join(result["unknown_metrics"])
                     + "  <- warning: no such metric in the pack")
    else:
        lines.append("unknown name: none")
    lines.append(f"uncited line: {len(result['uncited_lines'])} (a warning, not an error)")
    for line in result["uncited_lines"]:
        lines.append(f"    {line}")
    warnings = len(result["unknown_metrics"]) + len(result["uncited_lines"])
    lines.append("")
    if result["ok"]:
        lines.append("RESULT: OK" + (f" with {warnings} warning(s)" if warnings else ""))
    else:
        lines.append("RESULT: the answer cites an evidence id that does not exist")
    return "\n".join(lines) + "\n"

PROMPT_EN = """You are diagnosing a resource problem in ESP-IDF / FreeRTOS firmware.

Answer from the evidence below and nothing else.

How to answer:

1. Cite an evidence id such as [E12] for every factual claim. A claim with no id
   is a guess, and a guess is worse than saying nothing.
2. Do not invent metric names, values, files or configuration keys.
3. The checks only say *that* a budget was missed. Explain *why*, from the
   series and the diff.
4. Be short. This is a conclusion for a busy engineer, not an essay.

Shape it exactly like this and add nothing else. At most three bullets per
section, one sentence each, 400 words in total:

- Cause: the single most likely one, a confidence, and its evidence ids.
- Numbers: the measurement next to the code that explains it, for example
  "-660 B/s, which is 128 bytes every 200 ms".
- Cannot rule out: only what the evidence genuinely cannot separate, one line
  each. If there is nothing, write "none".
- Fix: one sentence, naming the file and the line to change.
- Verify: one command, the metric that should move, and the value to expect.

Do not restate the evidence pack. Do not explain the tool.

EVIDENCE
--------
"""

PROMPT_ZH = """你在诊断一个 ESP-IDF / FreeRTOS 固件的资源问题。

只根据下面的证据回答，不要用别的东西。

回答要求：

1. 每条事实都要引用证据编号，例如 [E12]。没有编号的说法就是猜，而猜比不说更糟。
2. 不要编造指标名、数值、文件名或配置项。
3. 检查只告诉你越界了，不告诉你为什么。为什么，要从时序和补丁里推。
4. 写短。这是给忙人看的一段结论，不是一篇论文。

严格按下面这个格式写，别加别的内容。每节最多 3 条，每条一句话，全文不超过 400 字，用中文：

- 根因：最可能的那一个，给一个置信度，附证据编号。
- 关键数字：把测量和代码对上，例如「-660 B/s，也就是每 200 ms 分配 128 字节」。
- 排除不掉的可能：只写现有证据真的分不开的，一条一行；没有就写「无」。
- 修复：一句话，指到哪个文件哪一行。
- 验证：一条命令，哪个指标应该变成多少。

不要复述证据包，不要解释这个工具。

证据
----
"""

def build_prompt(evidence_text: str, lang: str = "en") -> str:
    """The instruction block plus the pack, ready to hand to a model.

    The pack stays English -- metric names and evidence ids are identifiers, and
    translating them would break the audit -- while the instructions can be
    written in the language you want the answer in.
    """
    return (PROMPT_ZH if lang == "zh" else PROMPT_EN) + evidence_text
