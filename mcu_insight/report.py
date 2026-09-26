"""Human-readable rendering of a resource report."""

from __future__ import annotations

from .analysis import ImageReport


def human_bytes(value: int) -> str:
    if abs(value) >= 1024 * 1024:
        return f"{value / (1024 * 1024):.2f} MiB"
    if abs(value) >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value} B"


def _bar(ratio: float, width: int = 24) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "#" * filled + "." * (width - filled)


def render(report: ImageReport, partition_size: int | None = None, top: int = 10) -> str:
    lines: list[str] = []
    map_file = report.map_file

    lines.append("MCU-Insight - static resource report")
    lines.append(f"map       : {map_file.path}")
    lines.append("")

    lines.append("Flash image")
    lines.append(
        f"  estimated   : {report.estimated_image_size:>10,} B  "
        f"({human_bytes(report.estimated_image_size)})"
    )
    if report.actual_image_size is not None:
        delta = report.image_delta or 0
        sign = "+" if delta >= 0 else "-"
        lines.append(
            f"  actual .bin : {report.actual_image_size:>10,} B  "
            f"({human_bytes(report.actual_image_size)})  delta {sign}{abs(delta)} B"
        )
    if partition_size:
        used = report.actual_image_size or report.estimated_image_size
        ratio = used / partition_size
        lines.append("")
        lines.append(f"  app partition: {partition_size:,} B ({human_bytes(partition_size)})")
        lines.append(f"  used         : {used:,} B ({ratio:.1%})  [{_bar(ratio)}]")
        lines.append(
            f"  free         : {partition_size - used:,} B "
            f"({human_bytes(partition_size - used)}, {1 - ratio:.1%})"
        )

    lines.append("")
    lines.append("Sections stored in flash")
    for section in report.stored_sections:
        if section.size == 0:
            continue
        share = section.size / report.estimated_image_size if report.estimated_image_size else 0
        region = map_file.region_for(section.address)
        region_name = region.name if region else "-"
        lines.append(f"  {section.name:<18} {section.size:>9,} B  {share:>6.1%}  {region_name}")

    lines.append("")
    lines.append("Static RAM (link-time)")
    for label, ram in (("DRAM", report.dram), ("IRAM", report.iram), ("RTC", report.rtc)):
        if ram.used == 0 and not ram.sections:
            continue
        if ram.capacity:
            lines.append(
                f"  {label:<5} {ram.used:>9,} B / {ram.capacity:,} B "
                f"({ram.ratio:.1%})  free {ram.free:,} B"
            )
        else:
            lines.append(f"  {label:<5} {ram.used:>9,} B")
        for section in ram.sections:
            if section.size == 0:
                continue
            lines.append(f"        {section.name:<18} {section.size:>9,} B")

    if report.dram.capacity:
        lines.append("")
        lines.append(
            f"  note: the {report.dram.free:,} B of free DRAM is shared by the "
            "heap and every task stack."
        )

    if report.components:
        lines.append("")
        lines.append(f"Top {top} components by attributed flash size")
        for index, (name, size) in enumerate(list(report.components.items())[:top], start=1):
            share = size / report.estimated_image_size if report.estimated_image_size else 0
            lines.append(f"  {index:>2}. {name:<30} {size:>9,} B  {share:>6.1%}")
        lines.append(
            f"      {'(alignment / relaxation)':<30} "
            f"{report.unattributed_bytes:>9,} B  "
            f"{report.unattributed_bytes / report.estimated_image_size:>6.1%}"
            if report.estimated_image_size
            else ""
        )

    return "\n".join(line for line in lines if line is not None)