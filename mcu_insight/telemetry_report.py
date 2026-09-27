"""Human-readable rendering of stored telemetry."""

from __future__ import annotations

from .store import MetricStats, Store


def format_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e12:
        return f"{int(value):,}"
    return f"{value:,.2f}"


def render_summary(store: Store, device: str | None = None, top: int = 12) -> str:
    lines: list[str] = []
    lines.append("MCU-Insight - telemetry summary")
    lines.append(f"database  : {store.path}")

    devices = store.devices()
    if not devices:
        lines.append("")
        lines.append("No frames stored yet.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Devices")
    for name in devices:
        span = store.uptime_range(name)
        seconds = (span[1] - span[0]) / 1000 if span else 0
        firmware = store.latest_firmware(name) or "-"
        reboot_count = len(store.reboot_frames(name))
        reboot_note = f"   {reboot_count} reboot(s)" if reboot_count else ""
        lines.append(
            f"  {name:<24} {store.frame_count(name):>6} frames   "
            f"fw {firmware:<10} {seconds:,.1f} s of uptime{reboot_note}"
        )

    target = device or devices[0]
    if target not in devices:
        lines.append("")
        lines.append(f"No frames for device {target!r}.")
        return "\n".join(lines)

    sessions = store.sessions(target)
    since = None
    if len(sessions) > 1:
        start, _ = max(sessions, key=lambda item: item[1])
        since = start
        lines.append("")
        lines.append(
            f"  note: {len(sessions)} boot sessions in this capture; trends below "
            f"cover the longest one ({store.frame_count(target, since_id=since)} frames)."
        )

    stats = store.metric_stats(target, since_id=since)
    ranked: list[MetricStats] = sorted(
        stats.values(), key=lambda s: (-abs(s.change), s.metric)
    )
    frames_in_session = store.frame_count(target, since_id=since)

    lines.append("")
    lines.append(f"Metric trends ({target}, {frames_in_session} frames)")
    for entry in ranked[:top]:
        lines.append(
            f"  {entry.metric:<34} {format_number(entry.first):>10} -> "
            f"{format_number(entry.last):>10}   change {entry.change:+,.0f}"
        )

    stable = [s for s in ranked[top:] if abs(s.change) < 1e-9]
    if stable:
        lines.append("")
        lines.append(f"{len(stable)} further metric(s) unchanged over the capture.")
    return "\n".join(lines)
