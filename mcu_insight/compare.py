"""Regression comparison between two build artefacts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .analysis import ImageReport
from .report import human_bytes


@dataclass(frozen=True)
class Delta:
    """Change of one quantity between two builds."""

    name: str
    before: int
    after: int

    @property
    def change(self) -> int:
        return self.after - self.before

    @property
    def percent(self) -> float | None:
        if self.before == 0:
            return None
        return self.change / self.before

    @property
    def is_growth(self) -> bool:
        return self.change > 0

    def format_change(self) -> str:
        sign = "+" if self.change >= 0 else "-"
        text = f"{sign}{abs(self.change):,} B"
        if self.percent is not None:
            text += f" ({self.percent:+.1%})"
        return text


@dataclass
class Comparison:
    before: ImageReport
    after: ImageReport
    partition_size: int | None = None
    image: Delta = field(default_factory=lambda: Delta("image", 0, 0))
    sections: list[Delta] = field(default_factory=list)
    components: list[Delta] = field(default_factory=list)
    ram: list[Delta] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.image.change != 0 or any(d.change for d in self.sections)

    @property
    def usage_before(self) -> float | None:
        if not self.partition_size:
            return None
        return (self.before.actual_image_size or self.before.estimated_image_size) / self.partition_size

    @property
    def usage_after(self) -> float | None:
        if not self.partition_size:
            return None
        return (self.after.actual_image_size or self.after.estimated_image_size) / self.partition_size

    @property
    def overflow(self) -> bool:
        """True when the new image no longer fits the app partition."""
        if not self.partition_size:
            return False
        size = self.after.actual_image_size or self.after.estimated_image_size
        return size > self.partition_size


def _image_size(report: ImageReport) -> int:
    return report.actual_image_size or report.estimated_image_size


def _deltas(
    before: dict[str, int],
    after: dict[str, int],
    min_change: int = 0,
) -> list[Delta]:
    names = set(before) | set(after)
    deltas = [
        Delta(name=name, before=before.get(name, 0), after=after.get(name, 0))
        for name in names
    ]
    deltas = [d for d in deltas if abs(d.change) > min_change]
    return sorted(deltas, key=lambda d: (-abs(d.change), d.name))


def compare_reports(
    before: ImageReport,
    after: ImageReport,
    partition_size: int | None = None,
    min_change: int = 0,
) -> Comparison:
    comparison = Comparison(before=before, after=after, partition_size=partition_size)
    comparison.image = Delta("image", _image_size(before), _image_size(after))

    comparison.sections = _deltas(
        {s.name: s.size for s in before.stored_sections},
        {s.name: s.size for s in after.stored_sections},
        min_change,
    )
    comparison.components = _deltas(before.components, after.components, min_change)
    comparison.ram = _deltas(
        {
            "DRAM": before.dram.used,
            "IRAM": before.iram.used,
            "RTC": before.rtc.used,
        },
        {
            "DRAM": after.dram.used,
            "IRAM": after.iram.used,
            "RTC": after.rtc.used,
        },
        min_change,
    )
    return comparison


def render(comparison: Comparison, top: int = 12) -> str:
    before = comparison.before
    after = comparison.after
    lines: list[str] = []

    lines.append("MCU-Insight - regression report")
    lines.append(f"before    : {before.map_file.path}")
    lines.append(f"after     : {after.map_file.path}")
    lines.append("")

    lines.append(f"Flash image   {comparison.image.before:>10,} B -> "
                 f"{comparison.image.after:>10,} B   {comparison.image.format_change()}")
    if comparison.partition_size:
        usage_before = comparison.usage_before or 0
        usage_after = comparison.usage_after or 0
        lines.append(
            f"App partition {comparison.partition_size:>10,} B   "
            f"{usage_before:.1%} -> {usage_after:.1%}   "
            f"free {human_bytes(comparison.partition_size - comparison.image.after)}"
        )
        if comparison.overflow:
            lines.append("")
            lines.append("  *** the image no longer fits the app partition ***")

    if comparison.sections:
        lines.append("")
        lines.append("Sections that changed")
        for delta in comparison.sections[:top]:
            lines.append(
                f"  {delta.name:<18} {delta.before:>9,} -> {delta.after:>9,} B   "
                f"{delta.format_change()}"
            )

    if comparison.ram:
        lines.append("")
        lines.append("Static RAM that changed")
        for delta in comparison.ram[:top]:
            lines.append(
                f"  {delta.name:<18} {delta.before:>9,} -> {delta.after:>9,} B   "
                f"{delta.format_change()}"
            )

    if comparison.components:
        lines.append("")
        lines.append(f"Components that changed (top {top})")
        for index, delta in enumerate(comparison.components[:top], start=1):
            lines.append(
                f"  {index:>2}. {delta.name:<30} {delta.before:>9,} -> {delta.after:>9,} B   "
                f"{delta.format_change()}"
            )

    if not comparison.changed:
        lines.append("")
        lines.append("No change in flash footprint between the two builds.")

    return "\n".join(lines)


def to_dict(comparison: Comparison) -> dict:
    def dump(delta: Delta) -> dict:
        return {
            "name": delta.name,
            "before": delta.before,
            "after": delta.after,
            "change": delta.change,
            "percent": delta.percent,
        }

    return {
        "before_map": str(comparison.before.map_file.path),
        "after_map": str(comparison.after.map_file.path),
        "image": dump(comparison.image),
        "partition_bytes": comparison.partition_size,
        "usage_before": comparison.usage_before,
        "usage_after": comparison.usage_after,
        "overflow": comparison.overflow,
        "sections": [dump(d) for d in comparison.sections],
        "ram": [dump(d) for d in comparison.ram],
        "components": [dump(d) for d in comparison.components],
    }
