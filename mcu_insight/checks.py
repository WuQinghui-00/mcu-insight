"""Threshold rules and baseline comparison over stored telemetry.

Detection is deliberately rule based, not AI based: a threshold breach or a
step change against a stored baseline is what a CI job can assert on. The model
side of the project is for explaining a failure, not for deciding whether one
happened.

Configuration is JSON so that it needs no extra dependency::

    {
      "rules": [
        {"metric": "heap.min",                  "min": 60000},
        {"metric": "task.*.stack_free_min",     "min": 512},
        {"metric": "custom.infer_p99_us",       "max": 2000},
        {"metric": "custom.model_accuracy_pct", "min": 90}
      ],
      "baseline": {"path": "baseline.json", "max_change_pct": 15}
    }

Metric names are matched with shell style wildcards; every metric that matches
is checked, so a single rule can cover every task stack at once.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path

from .store import Store
from .trends import slope_per_second, steady_samples, steady_span_ms


class ConfigError(ValueError):
    """Raised when the check configuration cannot be used."""


@dataclass
class Rule:
    pattern: str
    minimum: float | None = None
    maximum: float | None = None
    #: Which observation to test: the latest sample, or the extreme seen so far.
    #: Resource budgets want "min" (the stack never dropped below X), latency
    #: ceilings want "max", running averages want "last".
    stat: str = "last"
    #: Skip the rule until the metric has at least this many samples: a running
    #: average restarted by a reboot is noise for the first minutes.
    min_samples: int = 0
    #: Rate bounds, in units per second, for metrics whose *slope* is the thing
    #: that matters. A high-water mark compared against a floor answers "how low
    #: did it get", and that answer depends on how long the capture ran: an
    #: identical leak passes a 60 s capture and fails a 250 s one. A slope
    #: answers the same question whatever the duration.
    min_rate_per_s: float | None = None
    max_rate_per_s: float | None = None
    #: Refuse to judge until the steady part of the capture is this long. Worth
    #: setting on rate rules: a slope measured over ten seconds is noise, and
    #: "not measured" is a more honest answer than a pass nobody established.
    min_span_ms: float | None = None

    @property
    def is_rate(self) -> bool:
        return self.min_rate_per_s is not None or self.max_rate_per_s is not None

    def matches(self, metric: str) -> bool:
        return fnmatch.fnmatchcase(metric, self.pattern)

    def value_from(self, entry) -> float:
        if self.stat == "min":
            return entry.minimum
        if self.stat == "max":
            return entry.maximum
        return entry.last

    def describe(self) -> str:
        if self.is_rate:
            text = self._bound_text()
        elif self.stat != "last":
            text = f"{self.stat} " + self._bound_text()
        else:
            text = self._bound_text()
        if self.min_span_ms:
            text += f" over >= {self.min_span_ms / 1000:g} s of steady state"
        return text

    def _bound_text(self) -> str:
        if self.is_rate:
            if self.min_rate_per_s is not None and self.max_rate_per_s is not None:
                return (f"{self.min_rate_per_s:g}/s <= slope <= "
                        f"{self.max_rate_per_s:g}/s")
            if self.min_rate_per_s is not None:
                return f"slope >= {self.min_rate_per_s:g}/s"
            return f"slope <= {self.max_rate_per_s:g}/s"
        if self.minimum is not None and self.maximum is not None:
            return f"{self.minimum:g} <= value <= {self.maximum:g}"
        if self.minimum is not None:
            return f">= {self.minimum:g}"
        if self.maximum is not None:
            return f"<= {self.maximum:g}"
        return "no bound"


@dataclass
class Violation:
    metric: str
    value: float
    rule: Rule
    device: str

    def describe(self) -> str:
        if self.rule.is_rate:
            return f"falls at {self.value:+,.1f} per second, {self.rule.describe()}"
        if self.rule.minimum is not None and self.value < self.rule.minimum:
            return f"{self.value:g} is below {self.rule.minimum:g}"
        if self.rule.maximum is not None and self.value > self.rule.maximum:
            return f"{self.value:g} is above {self.rule.maximum:g}"
        return f"{self.value:g} violates {self.rule.describe()}"


@dataclass
class Change:
    metric: str
    baseline: float
    current: float

    @property
    def delta(self) -> float:
        return self.current - self.baseline

    @property
    def percent(self) -> float | None:
        if self.baseline == 0:
            return None
        return self.delta / abs(self.baseline)


@dataclass
class CheckReport:
    db: Path
    devices: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    checked_metrics: int = 0
    skipped_metrics: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    baseline_path: Path | None = None
    new_metrics: list[str] = field(default_factory=list)
    #: Rules that could not be judged, usually because the capture is shorter
    #: than the rule requires. Kept apart from violations: "we did not measure
    #: long enough to know" is a different answer from "it is fine".
    unevaluated: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        # Not judging a rule is not a pass. A build cannot be called green on
        # the strength of a check that was never carried out.
        return not self.violations and not self.unevaluated


def load_config(path: str | Path) -> dict:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"config not found: {config_path}")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    return data


def parse_rules(config: dict) -> list[Rule]:
    raw = config.get("rules", [])
    if not isinstance(raw, list):
        raise ConfigError("'rules' must be a list")
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict) or "metric" not in entry:
            raise ConfigError(f"rule needs a 'metric': {entry!r}")
        minimum = entry.get("min")
        maximum = entry.get("max")
        min_rate = entry.get("min_rate_per_s")
        max_rate = entry.get("max_rate_per_s")
        if minimum is None and maximum is None and min_rate is None and max_rate is None:
            raise ConfigError(
                f"rule needs 'min', 'max', 'min_rate_per_s' or 'max_rate_per_s': {entry!r}"
            )
        stat = str(entry.get("stat", "last")).lower()
        if stat not in {"last", "min", "max"}:
            raise ConfigError(f"rule stat must be last, min or max: {entry!r}")
        span = entry.get("min_span_ms")
        rules.append(
            Rule(
                pattern=str(entry["metric"]),
                minimum=None if minimum is None else float(minimum),
                maximum=None if maximum is None else float(maximum),
                stat=stat,
                min_samples=int(entry.get("min_samples", 0)),
                min_rate_per_s=None if min_rate is None else float(min_rate),
                max_rate_per_s=None if max_rate is None else float(max_rate),
                min_span_ms=None if span is None else float(span),
            )
        )
    return rules


def save_baseline(store: Store, path: str | Path, meta: dict | None = None) -> dict:
    """Snapshot the latest value of every metric, per device.

    ``meta`` records what the snapshot describes -- the revision it was taken
    from, above all. A baseline that only carries numbers can say that
    something changed but not what it changed from, which is the difference
    between "the heap fell 66%" and "the heap fell 66% since this commit".

    Each metric keeps its last value together with the extremes, the sample
    count and the steady slope, and each device keeps the shape of the capture
    it came from. Without that shape a comparison cannot tell a change from a
    shorter soak: a high-water mark falls further the longer you watch it, so
    "heap.min is 66% lower" means one thing when both captures ran 250 s and
    something else when one ran 250 s and the other 108 s. A slope on both
    sides answers the question on its own.
    """
    snapshot: dict = {}
    if meta:
        snapshot["_meta"] = dict(meta)
    for device in store.devices():
        stats = store.metric_stats(device)
        metrics = {}
        for name, entry in stats.items():
            rate = slope_per_second(steady_samples(store.series(device, name))[1])
            metrics[name] = {
                "last": entry.last,
                "min": entry.minimum,
                "max": entry.maximum,
                "count": entry.count,
                "rate_per_s": None if rate is None else round(rate, 3),
            }
        uptimes = store.uptimes(device)
        snapshot[device] = {
            "capture": {
                "frames": len(uptimes),
                "boot_sessions": len(store.sessions(device)),
                "steady_span_ms": round(
                    steady_span_ms([(uptime, 0.0) for uptime in uptimes]), 1
                ),
            },
            "metrics": metrics,
        }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot


def load_baseline(path: str | Path) -> dict[str, dict[str, float]]:
    baseline_path = Path(path)
    if not baseline_path.is_file():
        raise ConfigError(f"baseline not found: {baseline_path}")
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    # Keys starting with an underscore are the baseline talking about itself,
    # not devices. Comparisons iterate over the devices they found in the
    # capture, so this was already harmless; being explicit keeps it that way.
    baseline: dict[str, dict[str, float]] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "metrics" in value:
            baseline[key] = {
                name: (entry["last"] if isinstance(entry, dict) else entry)
                for name, entry in value["metrics"].items()
            }
        else:
            baseline[key] = value
    return baseline


def baseline_detail(path: str | Path) -> dict:
    """The baseline as saved, with old flat files lifted into the new shape.

    A file written before the shape existed has no capture block and no slope
    per metric. Saying so is the point: a comparison that cannot see how long
    the baseline ran cannot separate a change from a shorter soak.
    """
    baseline_path = Path(path)
    if not baseline_path.is_file():
        return {}
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    detail: dict = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "metrics" in value:
            detail[key] = value
        else:
            detail[key] = {
                "capture": {},
                "metrics": {name: {"last": number} for name, number in value.items()},
            }
    return detail


def load_baseline_meta(path: str | Path) -> dict:
    """What the baseline recorded about itself, empty when it recorded nothing."""
    baseline_path = Path(path)
    if not baseline_path.is_file():
        return {}
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    meta = data.get("_meta")
    return meta if isinstance(meta, dict) else {}


#: Which direction of change is the worrying one, matched on metric-name
#: substrings. A regression is not the same thing as a decrease: free heap
#: falling is bad, inference latency falling is good, and uptime is neither.
#: Anything unmatched is reported as "changed" with no verdict rather than
#: being guessed at, because a wrong red badge is worse than a neutral one.
_REGRESSION_DIRECTION = (
    ("stack_free", "down"),  # less stack left = closer to an overflow
    ("heap.min", "down"),  # less heap left = closer to a failed allocation
    ("heap.free", "down"),
    ("largest", "down"),  # the biggest free block shrinking = fragmentation
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


def check_store(
    store: Store,
    config: dict,
    baseline: dict[str, dict[str, float]] | None = None,
    baseline_path: Path | None = None,
) -> CheckReport:
    rules = parse_rules(config)
    report = CheckReport(db=store.path, rules=rules, baseline_path=baseline_path)
    report.devices = store.devices()

    seen: set[str] = set()
    for device in report.devices:
        stats = store.metric_stats(device)
        for name, entry in stats.items():
            for rule in rules:
                if not rule.matches(name):
                    continue
                if entry.count < rule.min_samples:
                    report.skipped_metrics.append(f"{device}:{name}")
                    continue
                seen.add(f"{device}:{name}")
                if rule.is_rate:
                    points = store.series(device, name)
                    span = steady_span_ms(points)
                    if rule.min_span_ms and span < rule.min_span_ms:
                        report.unevaluated.append(
                            f"{device} {name}: {rule.describe()} -- the capture holds"
                            f" only {span / 1000:.0f} s of steady state"
                        )
                        continue
                    slope = slope_per_second(steady_samples(points)[1])
                    if slope is None:
                        report.skipped_metrics.append(f"{device}:{name}")
                        continue
                    bad = (
                        rule.min_rate_per_s is not None and slope < rule.min_rate_per_s
                    ) or (
                        rule.max_rate_per_s is not None and slope > rule.max_rate_per_s
                    )
                    if bad:
                        report.violations.append(
                            Violation(metric=name, value=slope, rule=rule, device=device)
                        )
                    continue
                value = rule.value_from(entry)
                bad = (rule.minimum is not None and value < rule.minimum) or (
                    rule.maximum is not None and value > rule.maximum
                )
                if bad:
                    report.violations.append(
                        Violation(metric=name, value=value, rule=rule, device=device)
                    )
    report.checked_metrics = len(seen)

    if baseline:
        limit = float(config.get("baseline", {}).get("max_change_pct", 10.0))
        for device in report.devices:
            current = {n: e.last for n, e in store.metric_stats(device).items()}
            previous = baseline.get(device, {})
            for name, value in current.items():
                if name not in previous:
                    report.new_metrics.append(f"{device}:{name}")
                    continue
                change = Change(metric=name, baseline=previous[name], current=value)
                percent = change.percent
                if percent is None:
                    if abs(change.delta) > 0:
                        report.changes.append(change)
                    continue
                if abs(percent) * 100 >= limit:
                    report.changes.append(change)
        report.changes.sort(key=lambda c: -abs(c.percent or 0))
    return report


def render(report: CheckReport, top: int = 12) -> str:
    lines: list[str] = []
    lines.append("MCU-Insight - resource checks")
    lines.append(f"database  : {report.db}")
    lines.append(f"devices   : {', '.join(report.devices) or '(none)'}")
    lines.append(
        f"rules     : {len(report.rules)}   metrics matched: {report.checked_metrics}"
    )

    lines.append("")
    if report.violations:
        lines.append(f"Violations ({len(report.violations)})")
        for violation in report.violations:
            lines.append(
                f"  ! {violation.device} {violation.metric}: {violation.describe()}"
            )
    else:
        lines.append("No threshold violations.")

    if report.changes:
        lines.append("")
        lines.append(f"Changed since baseline ({report.baseline_path})")
        for change in report.changes[:top]:
            if change.percent is None:
                amount = f"{change.delta:+,.0f}"
            else:
                amount = f"{change.percent:+.1%}"
            lines.append(
                f"  {change.metric:<34} {change.baseline:>10,.0f} -> "
                f"{change.current:>10,.0f}   {amount}"
            )

    if report.unevaluated:
        lines.append("")
        lines.append(f"Not judged ({len(report.unevaluated)})")
        for item in report.unevaluated:
            lines.append(f"  ? {item}")

    if report.skipped_metrics:
        lines.append("")
        lines.append(
            f"{len(report.skipped_metrics)} metric(s) skipped (below min_samples)"
        )

    if report.new_metrics:
        lines.append("")
        lines.append(f"{len(report.new_metrics)} metric(s) not present in the baseline")

    lines.append("")
    if report.violations:
        verdict = "FAIL"
    elif report.unevaluated:
        verdict = f"INCOMPLETE ({len(report.unevaluated)} rule(s) not judged)"
    else:
        verdict = "PASS"
    lines.append("RESULT: " + verdict)
    return "\n".join(lines)


def to_dict(report: CheckReport) -> dict:
    return {
        "database": str(report.db),
        "devices": report.devices,
        "checked_metrics": report.checked_metrics,
        "skipped_metrics": report.skipped_metrics,
        "unevaluated": report.unevaluated,
        "ok": report.ok,
        "violations": [
            {
                "device": v.device,
                "metric": v.metric,
                "value": v.value,
                "detail": v.describe(),
            }
            for v in report.violations
        ],
        "changes": [
            {
                "metric": c.metric,
                "baseline": c.baseline,
                "current": c.current,
                "delta": c.delta,
                "percent": c.percent,
            }
            for c in report.changes
        ],
        "new_metrics": report.new_metrics,
    }
