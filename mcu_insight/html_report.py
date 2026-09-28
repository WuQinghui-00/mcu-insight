"""Self-contained HTML report.

One file, no server, no CDN, no dependencies: the data is inlined and the
charts are hand-written SVG, so the page opens from the filesystem and can be
published or committed as-is. That matters here because the whole project runs
without network access.

    mcu-insight report --db capture.db --config budgets/signal.json --out report.html
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from .analysis import ImageReport
from .checks import CheckReport
from .store import Store

#: Metric families worth a trend card, in display order.
TREND_HINTS = (
    "heap.min",
    "stack_free_min",
    "idle",
    "light_sleep",
    "infer_p",
    "model_accuracy",
    "arena_used",
)

#: Which direction of change is the worrying one, matched on metric-name
#: substrings. A regression is not the same thing as a decrease: free heap
#: falling is bad, inference latency falling is good, and uptime is neither.
#: Anything unmatched is reported as "changed" with no verdict rather than
#: being guessed at, because a wrong red badge is worse than a neutral one.
_REGRESSION_DIRECTION = (
    ("stack_free", "down"),  # less stack left = closer to an overflow
    ("heap.min", "down"),  # less heap left = closer to a failed allocation
    ("heap.free", "down"),
    ("arena_free", "down"),
    ("arena_used", "up"),
    ("headroom", "down"),
    ("idle", "down"),  # idle share falling = more CPU stolen by something else
    ("sleep", "down"),
    ("accuracy", "down"),
    ("acc_", "down"),  # per-class accuracy, e.g. acc_triangle_pct
    ("confidence", "down"),
    ("infer_p", "up"),  # a rising latency percentile = slower inference
    ("infer_min", "up"),
    ("infer_mean", "up"),
    ("infer_max", "up"),
    ("latency", "up"),
    ("jitter", "up"),
    ("drift", "up"),
    ("reset_count", "up"),
)


def regression_direction(metric: str) -> str | None:
    """Return "up"/"down" when a rise/fall in *metric* is the bad direction."""
    name = metric.lower()
    for hint, bad_way in _REGRESSION_DIRECTION:
        if hint in name:
            return bad_way
    return None


def change_verdict(metric: str, delta: float) -> str:
    """Classify a delta as "bad", "ok", or "" when the direction is unknown."""
    bad_way = regression_direction(metric)
    if bad_way is None or delta == 0:
        return ""
    worse_off = delta > 0 if bad_way == "up" else delta < 0
    return "bad" if worse_off else "ok"

#: Samples taken inside this window after a boot are start-up, not steady state:
#: Wi-Fi, MQTT and the telemetry task are still allocating, so a "historical
#: minimum" metric such as heap.min is still sitting at its post-boot peak.
#: Those samples stay on the chart but are kept out of the card statistics.
BOOT_WARMUP_MS = 10_000.0


def boot_boundaries(points: list[tuple[float, float]]) -> list[int]:
    """Indices where a new boot session starts, i.e. uptime went backwards."""
    return [i for i in range(1, len(points)) if points[i][0] < points[i - 1][0]]


def steady_delta(points: list[tuple[float, float]]) -> float:
    """Change across the steady part of the newest boot session."""
    _, steady = steady_samples(points)
    return steady[-1][1] - steady[0][1]


def capture_axis(points: list[tuple[float, float]]) -> list[float]:
    """x values that keep increasing across a reset.

    ``uptime_ms`` restarts at zero after a reboot, so plotting it directly makes
    the line jump backwards and puts the reboot marker at the left edge next to
    the start of the first session. Carrying the elapsed time over keeps the
    chart readable and puts the marker where the reset actually happened.
    """
    axis = []
    offset = 0.0
    previous: float | None = None
    for uptime, _ in points:
        if previous is not None and uptime < previous:
            offset += previous
        axis.append(uptime + offset)
        previous = uptime
    return axis


def steady_samples(
    points: list[tuple[float, float]], warmup_ms: float = BOOT_WARMUP_MS
) -> tuple[int, list[tuple[float, float]]]:
    """Return (start of the newest boot session, samples that are steady state)."""
    starts = [0] + boot_boundaries(points)
    begin = starts[-1]
    session = points[begin:]
    steady = [p for p in session if p[0] - session[0][0] >= warmup_ms]
    return begin, steady or session

STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px; font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
       background: #f6f7f9; color: #1c2024; }
h1 { font-size: 22px; margin: 0; letter-spacing: -0.01em; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em;
     color: #6b7280; margin: 0 0 12px; }
.sub { color: #6b7280; margin: 6px 0 0; font-size: 13px; }
.wrap { max-width: 1080px; margin: 0 auto; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
          padding: 20px 22px; margin-top: 18px; }
.grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.card { border: 1px solid #eef0f3; border-radius: 8px; padding: 12px 14px; }
.card h3 { margin: 0 0 2px; font-size: 13px; font-weight: 600; }
.card .value { font: 600 20px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; margin: 6px 0 2px; }
.card .range { color: #6b7280; font-size: 11px; font-family: ui-monospace, monospace; }
.pass { color: #067647; background: #ecfdf3; border-color: #abefc6; }
.fail { color: #b42318; background: #fef3f2; border-color: #fecdca; }
.badge { display: inline-block; font: 600 11px/1 ui-monospace, monospace; padding: 5px 8px;
         border: 1px solid; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: #6b7280; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.06em; padding: 4px 8px 8px 0; }
td { padding: 6px 8px 6px 0; border-top: 1px solid #f1f3f5;
     font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
td.name { font-family: inherit; }
.num { text-align: right; }
.bar { height: 8px; border-radius: 5px; background: #eef0f3; overflow: hidden; }
.bar span { display: block; height: 100%; background: #3b82f6; }
.ok { color: #067647; } .bad { color: #b42318; }
.note { color: #6b7280; font-size: 12px; }
"""


def _esc(value) -> str:
    return html.escape(str(value))


def sparkline(points: list[tuple[float, float]], width: int = 300, height: int = 56,
              stroke: str = "#3b82f6",
              markers: list[float] | tuple[float, ...] = ()) -> str:
    """A tiny line chart as inline SVG, with optional dashed vertical markers."""
    if len(points) < 2:
        return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"></svg>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span_x = (max(xs) - min(xs)) or 1.0
    span_y = (max(ys) - min(ys)) or 1.0
    pad = 4

    def scale_x(value: float) -> float:
        return pad + (value - min(xs)) / span_x * (width - 2 * pad)

    coords = []
    for x, y in points:
        px = scale_x(x)
        py = height - pad - (y - min(ys)) / span_y * (height - 2 * pad)
        coords.append(f"{px:.1f},{py:.1f}")
    marks = "".join(
        f'<line x1="{scale_x(mark):.1f}" y1="0" x2="{scale_x(mark):.1f}" y2="{height}"'
        ' stroke="#9aa3af" stroke-width="1" stroke-dasharray="3 3"/>'
        for mark in markers
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" role="img">'
        f'{marks}'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.6" '
        f'stroke-linejoin="round" points="{" ".join(coords)}"/></svg>'
    )

def _trend_note(store: Store, device: str) -> str:
    """Explain how to read the cards, including how boot handling was applied."""
    note = (
        '<p class="note">Cards are scaled to their own range, so compare shapes, not '
        "slopes. Card statistics cover the newest boot session and skip the first "
        f"{BOOT_WARMUP_MS / 1000:.0f} s after boot, while start-up is still allocating; "
        "the checks above still cover every frame.</p>"
    )
    reboots = len(store.reboot_frames(device)) if device else 0
    if reboots:
        note += (
            f'<p class="note">{reboots} reboot(s) in this capture - '
            "the dashed line marks each one.</p>"
        )
    return note


def _trend_cards(store: Store, device: str, metrics: list[str], top: int) -> str:
    cards = []
    for metric in metrics[:top]:
        points = store.series(device, metric)
        if not points:
            continue
        axis = capture_axis(points)
        chart = list(zip(axis, [v for _, v in points]))
        marks = [axis[i] for i in boot_boundaries(points)]
        _, steady = steady_samples(points)
        values = [v for _, v in steady]
        delta = values[-1] - values[0]
        colour = "#b42318" if change_verdict(metric, delta) == "bad" else "#3b82f6"
        cards.append(
            '<div class="card">'
            f'<h3>{_esc(metric)}</h3>'
            f'<div class="value">{points[-1][1]:,.0f}</div>'
            f'<div class="range">min {min(values):,.0f} · max {max(values):,.0f} · '
            f'change {delta:+,.0f}</div>'
            f"{sparkline(chart, stroke=colour, markers=marks)}"
            "</div>"
        )
    return f'<div class="grid">{"".join(cards)}</div>' if cards else "<p class=note>no data</p>"

def _checks_block(report: CheckReport) -> str:
    rows = []
    for rule in report.rules:
        rows.append(
            f'<tr><td class="name">{_esc(rule.pattern)}</td>'
            f'<td class="name">{_esc(rule.describe())}</td></tr>'
        )
    body = [
        f'<p><span class="badge {"pass" if report.ok else "fail"}">'
        f'{"PASS" if report.ok else "FAIL"}</span> '
        f'<span class="note">{len(report.rules)} rules · '
        f'{report.checked_metrics} metrics evaluated</span></p>'
    ]
    if report.violations:
        body.append("<table><tr><th>Violation</th><th>Detail</th></tr>")
        for violation in report.violations:
            body.append(
                f'<tr><td class="name">{_esc(violation.metric)}</td>'
                f'<td class="name">{_esc(violation.describe())}</td></tr>'
            )
        body.append("</table>")
    else:
        body.append('<p class="note">No threshold violations.</p>')
    if rows:
        body.append('<table><tr><th>Rule</th><th>Bound</th></tr>' + "".join(rows) + "</table>")
    return "".join(body)


def _changes_block(report: CheckReport) -> str:
    if not report.changes:
        return '<p class="note">No baseline loaded, or nothing changed beyond the threshold.</p>'
    rows = ["<table><tr><th>Metric</th><th class=num>Baseline</th><th class=num>Now</th>"
            "<th class=num>Change</th></tr>"]
    for change in report.changes:
        amount = f"{change.percent:+.1%}" if change.percent is not None else f"{change.delta:+,.0f}"
        cls = change_verdict(change.metric, change.delta)
        cell_class = ("num " + cls).strip()
        rows.append(
            f'<tr><td class="name">{_esc(change.metric)}</td>'
            f'<td class="num">{change.baseline:,.0f}</td>'
            f'<td class="num">{change.current:,.0f}</td>'
            f'<td class="{cell_class}">{amount}</td></tr>'
        )
    rows.append("</table>")
    rows.append("<p class=note>Coloured values moved in the worrying direction; "
                "uncoloured values changed without a known better-or-worse direction.</p>")
    return "".join(rows)


def _build_block(build: ImageReport, partition: int | None) -> str:
    used = build.actual_image_size or build.estimated_image_size
    lines = [f"<p class=note>flash {used:,} B · DRAM static {build.dram.used:,} B "
             f"/ {build.dram.capacity or 0:,} B · IRAM {build.iram.used:,} B</p>"]
    if partition:
        ratio = min(1.0, used / partition)
        lines.append(
            f'<p class=note>app partition {partition:,} B · used {ratio:.1%}</p>'
            f'<div class="bar"><span style="width:{ratio:.1%}"></span></div>'
        )
    rows = ["<table><tr><th>Component</th><th class=num>Flash</th></tr>"]
    for name, size in list(build.components.items())[:8]:
        rows.append(f'<tr><td class="name">{_esc(name)}</td>'
                    f'<td class="num">{size:,}</td></tr>')
    rows.append("</table>")
    return "".join(lines + rows)


def _fault_block(cases) -> str:
    if not cases:
        return ""
    rows = ["<table><tr><th>Injection</th><th>Detected</th><th>Expected</th></tr>"]
    for label, detected, expected in cases:
        mark = "yes" if detected else "no"
        good = detected == expected
        rows.append(
            f'<tr><td class="name">{_esc(label)}</td>'
            f'<td class="{"ok" if good else "bad"}">{mark}</td>'
            f'<td class="name">{"yes" if expected else "no"}</td></tr>'
        )
    rows.append("</table>")
    return "".join(rows)


def _pick_trends(store: Store, device: str, report: CheckReport, top: int) -> list[str]:
    """Cards worth drawing, ranked by how far the metric moved in steady state."""
    stats = store.metric_stats(device)
    wanted = [metric for metric in stats if any(hint in metric for hint in TREND_HINTS)]
    moved = {}
    for metric in wanted:
        points = store.series(device, metric)
        moved[metric] = abs(steady_delta(points)) if points else 0.0
    ranked = sorted(wanted, key=lambda metric: -moved[metric])
    for violation in report.violations:
        if violation.metric in stats and violation.metric not in ranked:
            ranked.insert(0, violation.metric)
    return ranked[:top]

def _subtitle_span(store: Store, device: str, seconds: float) -> str:
    """How much time the capture covers, without calling a reboot uptime."""
    if not device:
        return f"{seconds:,.0f} s of uptime"
    reboots = len(store.reboot_frames(device))
    if reboots:
        return f"{reboots + 1} boot sessions"
    return f"{seconds:,.0f} s of uptime"


def build_html(store: Store, report: CheckReport, build: ImageReport | None = None,
               partition: int | None = None, fault_cases=None, device: str | None = None,
               top: int = 8) -> str:
    devices = report.devices or store.devices()
    target = device or (devices[0] if devices else "")
    stats = store.metric_stats(target) if target else {}
    span = store.uptime_range(target) if target else None
    seconds = (span[1] - span[0]) / 1000 if span else 0
    firmware = store.latest_firmware(target) if target else ""

    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        f"<title>MCU-Insight · {_esc(target)}</title>",
        f"<style>{STYLE}</style></head><body><div class=wrap>",
        "<h1>MCU-Insight</h1>",
        f'<p class=sub>{_esc(target)} · fw {_esc(firmware or "-")} · '
        f'{store.frame_count(target)} frames · {_subtitle_span(store, target, seconds)}</p>',
        "<section><h2>Checks</h2>", _checks_block(report), "</section>",
        "<section><h2>Baseline changes</h2>", _changes_block(report), "</section>",
        "<section><h2>Metric trends</h2>",
        _trend_note(store, target),
        _trend_cards(store, target, _pick_trends(store, target, report, top), top),
        "</section>",
    ]
    if build is not None:
        parts += ["<section><h2>Build resources</h2>", _build_block(build, partition), "</section>"]
    faults = _fault_block(fault_cases)
    if faults:
        parts += ["<section><h2>Fault injection matrix</h2>", faults, "</section>"]
    parts.append("</div></body></html>")
    return "".join(parts)

#: Fault modes produced by tools/fault_matrix.py, and whether a violation is
#: the expected outcome.
FAULT_EXPECTATIONS = {
    "off": ("baseline", False),
    "heap": ("heap leak", True),
    "stack": ("stack frame", True),
    "busy": ("busy wait", True),
}


def gather_fault_cases(directory: str | Path, config: dict):
    """Rebuild the fault matrix from the capture files the runner left behind."""
    from .checks import check_store

    folder = Path(directory)
    cases = []
    for mode, (label, expected) in FAULT_EXPECTATIONS.items():
        path = folder / f"fault-{mode}.db"
        if not path.is_file():
            continue
        with Store(path) as store:
            report = check_store(store, config)
        cases.append((label, not report.ok, expected))
    return cases