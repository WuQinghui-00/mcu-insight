"""How a recorded series is read: boot sessions, steady state, and slope.

These live apart from the report and the checks because both need them. A
comparison and a rate rule disagree about nothing here: a capture can span a
reset, a boot is not steady state, and a slope is only meaningful once the
start-up transient is behind you.
"""

from __future__ import annotations

#: Samples taken inside this window after a boot are start-up, not steady state:
#: Wi-Fi, MQTT and the telemetry task are still allocating, so a "historical
#: minimum" metric such as heap.min is still sitting at its post-boot peak.
BOOT_WARMUP_MS = 10_000.0


def boot_boundaries(points: list[tuple[float, float]]) -> list[int]:
    """Indices where a new boot session starts, i.e. uptime went backwards."""
    return [i for i in range(1, len(points)) if points[i][0] < points[i - 1][0]]


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


def steady_delta(points: list[tuple[float, float]]) -> float:
    """Change across the steady part of the newest boot session."""
    _, steady = steady_samples(points)
    return steady[-1][1] - steady[0][1]


def steady_span_ms(points: list[tuple[float, float]],
                   warmup_ms: float = BOOT_WARMUP_MS) -> float:
    """How long the steady part of the newest session lasts, in milliseconds."""
    _, steady = steady_samples(points, warmup_ms)
    if len(steady) < 2:
        return 0.0
    return steady[-1][0] - steady[0][0]


def slope_per_second(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of a series, in units per second.

    This is what a rate rule needs and a threshold cannot give: the same leak
    measured over 60 s and over 250 s yields the same slope, while a high-water
    mark compared against a floor yields two different verdicts.
    """
    if len(points) < 2:
        return None
    xs = [(uptime - points[0][0]) / 1000.0 for uptime, _ in points]
    ys = [value for _, value in points]
    count = len(xs)
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread