"""Parser for GNU ld linker map files produced by ESP-IDF builds.

ESP-IDF links with ``-Wl,--cref`` and ``-Wl,--print-memory-usage``, so every
build directory contains a ``.map`` file with two blocks we care about::

    Memory Configuration

    Name             Origin             Length             Attributes
    iram0_0_seg      0x40080000         0x00020000         xr
    ...

    Linker script and memory map

    .flash.text     0x400d0020         0xa572a
     *(.literal .text)
     .text.sensor_task
                    0x400d0020        0x120 CMakeFiles/...obj
    ...

The parser turns both blocks into plain dataclasses so the rest of the tool can
reason about the flash / RAM budget without importing anything from the ESP-IDF
Python environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_SECTION_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s*$"
)
_FILL_RE = re.compile(
    r"^\s+\*fill\*\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s*$"
)
_CONTRIB_RE = re.compile(
    r"^\s+(?P<addr>0x[0-9a-fA-F]+)\s+(?P<size>0x[0-9a-fA-F]+)\s*(?P<src>.*?)\s*$"
)
_ONELINE_RE = re.compile(
    r"^\s+(?P<name>\S+)\s+(?P<addr>0x[0-9a-fA-F]+)\s+"
    r"(?P<size>0x[0-9a-fA-F]+)\s*(?P<src>.*?)\s*$"
)
_NAME_ONLY_RE = re.compile(r"^\s+(?P<name>\S+)\s*$")
_REGION_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<origin>0x[0-9a-fA-F]+)\s+"
    r"(?P<length>0x[0-9a-fA-F]+)\s*(?P<attrs>\S*)\s*$"
)
_MEMBER_RE = re.compile(r"^(?P<archive>[^(]+?)(?:\((?P<member>[^)]*)\))?$")


@dataclass(frozen=True)
class MemoryRegion:
    """One row of the ``Memory Configuration`` table."""

    name: str
    origin: int
    length: int
    attributes: str = ""

    @property
    def end(self) -> int:
        return self.origin + self.length

    def contains(self, address: int) -> bool:
        return self.origin <= address < self.end


@dataclass(frozen=True)
class Contribution:
    """A single input section placed inside an output section."""

    section: str
    address: int
    size: int
    source: str = ""
    is_fill: bool = False

    @property
    def archive(self) -> str:
        """``esp-idf/main/libmain.a`` for ``esp-idf/main/libmain.a(foo.c.obj)``."""
        if not self.source:
            return ""
        return _MEMBER_RE.match(self.source).group("archive")

    @property
    def member(self) -> str:
        """``foo.c.obj`` for ``esp-idf/main/libmain.a(foo.c.obj)``."""
        if not self.source:
            return ""
        return _MEMBER_RE.match(self.source).group("member") or ""

    @property
    def component(self) -> str:
        """Raw archive directory, normalised later by ``analysis.normalize_component``."""
        archive = self.archive
        if not archive:
            return ""
        return archive.rsplit("/", 1)[0] if "/" in archive else archive


@dataclass
class OutputSection:
    """An output section such as ``.flash.text`` or ``.dram0.bss``."""

    name: str
    address: int
    size: int
    contributions: list[Contribution] = field(default_factory=list)

    def coverage(self) -> list[tuple[Contribution, int]]:
        """Non-overlapping ``(contribution, size)`` pairs inside this section.

        Xtensa relaxation and COMDAT folding make the linker emit several input
        sections at the *same* address, for example
        ``.rodata.write_reg.str1.4`` and
        ``.rodata.esp_efuse_utility_process.str1.4`` both starting at
        ``0x3f414c9c``.  Summing the raw sizes therefore over-counts.  We walk
        the contributions in address order and clip each one to the bytes the
        previous ones did not already claim, so the total never exceeds the
        output section size.
        """
        items = sorted(
            (c for c in self.contributions if not c.is_fill),
            key=lambda c: (c.address, -c.size),
        )
        cursor = self.address
        end = self.address + self.size
        result: list[tuple[Contribution, int]] = []
        for contribution in items:
            start = max(contribution.address, cursor)
            stop = min(contribution.address + contribution.size, end)
            if stop <= start:
                continue
            result.append((contribution, stop - start))
            cursor = stop
        return result

    @property
    def attributed_size(self) -> int:
        """Sum of the raw input section sizes (can exceed ``size`` when relaxed)."""
        return sum(c.size for c in self.contributions)

    @property
    def covered_size(self) -> int:
        """Bytes of this section claimed by at least one contribution."""
        return sum(size for _, size in self.coverage())

    @property
    def fill_size(self) -> int:
        return sum(c.size for c in self.contributions if c.is_fill)

    def by_component(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for contribution, size in self.coverage():
            key = contribution.component or "(unknown)"
            sizes[key] = sizes.get(key, 0) + size
        return sizes


@dataclass
class MapFile:
    """Everything we extract from one ``.map`` file."""

    path: Path
    regions: list[MemoryRegion] = field(default_factory=list)
    sections: list[OutputSection] = field(default_factory=list)

    def region_for(self, address: int) -> MemoryRegion | None:
        """Smallest region containing ``address`` (the most specific one)."""
        matches = [r for r in self.regions if r.contains(address)]
        if not matches:
            return None
        return min(matches, key=lambda r: r.length)

    def section(self, name: str) -> OutputSection | None:
        for section in self.sections:
            if section.name == name:
                return section
        return None

    def iter_contributions(self) -> Iterator[Contribution]:
        for section in self.sections:
            yield from section.contributions


def parse_map(path: str | Path) -> MapFile:
    """Parse ``path`` and return a :class:`MapFile`.

    Lines we deliberately ignore: ``LOAD`` statements, wildcard input section
    patterns such as ``*(.bss .bss.*)``, symbol-only lines, and the
    ``(size before relaxing)`` annotations emitted when Xtensa relaxation is
    active.
    """
    map_path = Path(path)
    result = MapFile(path=map_path)

    mode = ""
    current: OutputSection | None = None
    pending_name: str | None = None
    last_source = ""

    with map_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n").rstrip("\r")

            if line.startswith("Memory Configuration"):
                mode = "regions"
                continue
            if line.startswith("Linker script and memory map"):
                mode = "sections"
                continue
            if line.startswith("Cross Reference Table"):
                mode = "xref"
                continue

            if mode == "regions":
                if not line.strip():
                    continue
                match = _REGION_RE.match(line)
                if match:
                    result.regions.append(
                        MemoryRegion(
                            name=match.group("name"),
                            origin=int(match.group("origin"), 16),
                            length=int(match.group("length"), 16),
                            attributes=match.group("attrs") or "",
                        )
                    )
                continue

            if mode != "sections":
                continue
            if not line.strip():
                pending_name = None
                continue

            match = _SECTION_RE.match(line)
            if match:
                current = OutputSection(
                    name=match.group("name"),
                    address=int(match.group("addr"), 16),
                    size=int(match.group("size"), 16),
                )
                result.sections.append(current)
                pending_name = None
                last_source = ""
                continue

            if current is None:
                continue

            stripped = line.lstrip()

            if stripped.startswith("*fill*"):
                match = _FILL_RE.match(line)
                if match:
                    current.contributions.append(
                        Contribution(
                            section=".fill",
                            address=int(match.group("addr"), 16),
                            size=int(match.group("size"), 16),
                            is_fill=True,
                        )
                    )
                continue

            if stripped.startswith("0x"):
                match = _CONTRIB_RE.match(line)
                if match:
                    raw_source = match.group("src").strip()
                    if raw_source:
                        last_source = raw_source
                    current.contributions.append(
                        Contribution(
                            section=pending_name or "",
                            address=int(match.group("addr"), 16),
                            size=int(match.group("size"), 16),
                            source=raw_source or last_source,
                        )
                    )
                    pending_name = None
                continue

            match = _ONELINE_RE.match(line)
            if match:
                raw_source = match.group("src").strip()
                if raw_source:
                    last_source = raw_source
                current.contributions.append(
                    Contribution(
                        section=match.group("name"),
                        address=int(match.group("addr"), 16),
                        size=int(match.group("size"), 16),
                        source=raw_source or last_source,
                    )
                )
                pending_name = None
                continue

            match = _NAME_ONLY_RE.match(line)
            if match and not match.group("name").startswith("*"):
                pending_name = match.group("name")
                continue

    return result