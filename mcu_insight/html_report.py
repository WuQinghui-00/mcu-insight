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
              stroke: str = "#3b82f6") -> str:
    """A tiny line chart as inline SVG."""
    if len(points) < 2:
        return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"></svg>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    span_x = (max(xs) - min(xs)) or 1.0
    span_y = (max(ys) - min(ys)) or 1.0
    pad = 4
    coords = []
    for x, y in points:
        px = pad + (x - min(xs)) / span_x * (width - 2 * pad)
        py = height - pad - (y - min(ys)) / span_y * (height - 2 * pad)
        coords.append(f"{px:.1f},{py:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" role="img">'
        f'<polyline fill="none" stroke="{stroke}" stroke-width="1.6" '
        f'stroke-linejoin="round" points="{" ".join(coords)}"/></svg>'
    )


def _trend_cards(store: Store, device: str, metrics: list[str], top: int) -> str:
    cards = []
    for metric in metrics[:top]:
        points = store.series(device, metric)
        if not points:
            continue
        values = [v for _, v in points]
        first, last = values[0], values[-1]
        delta = last - first
        colour = "#b42318" if delta < 0 and "min" in metric else "#3b82f6"
        cards.append(
            '<div class="card">'
            f'<h3>{_esc(metric)}</h3>'
            f'<div class="value">{last:,.0f}</div>'
            f'<div class="range">min {min(values):,.0f} · max {max(values):,.0f} · '
            f'change {delta:+,.0f}</div>'
            f'{sparkline(points, stroke=colour)}'
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
        cls = "bad" if (change.delta < 0) else "ok"
        rows.append(
            f'<tr><td class="name">{_esc(change.metric)}</td>'
            f'<td class="num">{change.baseline:,.0f}</td>'
            f'<td class="num">{change.current:,.0f}</td>'
            f'<td class="num {cls}">{amount}</td></tr>'
        )
    rows.append("</table>")
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
    stats = store.metric_stats(device)
    wanted: list[str] = []
    for metric in stats:
        if any(hint in metric for hint in TREND_HINTS):
            wanted.append(metric)
    ranked = sorted(wanted, key=lambda m: -abs(stats[m].change))
    for violation in report.violations:
        if violation.metric in stats and violation.metric not in ranked:
            ranked.insert(0, violation.metric)
    return ranked[:top]


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
        f'{store.frame_count(target)} frames · {seconds:,.0f} s of uptime</p>',
        "<section><h2>Checks</h2>", _checks_block(report), "</section>",
        "<section><h2>Baseline changes</h2>", _changes_block(report), "</section>",
        "<section><h2>Metric trends</h2>",
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