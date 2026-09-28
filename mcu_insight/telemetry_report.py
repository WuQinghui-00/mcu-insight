"""Human-readable rendering of stored telemetry."""

from __future__ import annotations

from .store import Store
from .trends import steady_samples


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
    # Read the trend the way the report does. A boot is not steady state, and a
    # high-water mark is still at its post-boot peak in the first frames, so
    # comparing the first sample with the last reports a leak that is really
    # the first second of the run.
    trends = []
    for name in stats:
        points = store.series(target, name, since_id=since)
        _, steady = steady_samples(points)
        trends.append((name, steady[0][1], steady[-1][1], steady[-1][1] - steady[0][1],
                       len(steady), len(points)))
    trends.sort(key=lambda item: (-abs(item[3]), item[0]))
    frames_in_session = store.frame_count(target, since_id=since)

    lines.append("")
    lines.append(f"Metric trends ({target}, {frames_in_session} frames)")
    for name, first, last, delta, steady_count, total in trends[:top]:
        lines.append(
            f"  {name:<34} {format_number(first):>10} -> "
            f"{format_number(last):>10}   change {delta:+,.0f}"
        )
    if trends and trends[0][4] < trends[0][5]:
        lines.append(
            "  note: the first samples after a boot are start-up, not steady state;"
            " they are left out of these numbers"
        )

    stable = [t for t in trends[top:] if abs(t[3]) < 1e-9]
    if stable:
        lines.append("")
        lines.append(f"{len(stable)} further metric(s) unchanged over the capture.")
    return "\n".join(lines)
