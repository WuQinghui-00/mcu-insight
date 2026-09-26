"""Turn a parsed linker map into a flash / RAM resource report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .mapfile import MapFile, OutputSection, parse_map

#: Sections that never end up in the flashed image.
DEBUG_PREFIXES = (
    ".debug_",
    ".zdebug_",
    ".comment",
    ".xtensa.info",
    ".xt.prop",
    ".xt.lit",
)

#: Zero-initialised sections: they occupy RAM but no flash space.
ZERO_INIT_MARKERS = (".bss", ".noinit")

#: Libraries that come from the toolchain rather than from a project component.
TOOLCHAIN_MARKERS = ("xtensa-esp-elf", "riscv32-esp-elf", "lib/gcc/")


def is_debug(section: OutputSection) -> bool:
    return section.name.startswith(DEBUG_PREFIXES)


def is_loaded(section: OutputSection) -> bool:
    """True for sections that have a real run-time address."""
    return section.address != 0 and not is_debug(section)


def is_zero_init(section: OutputSection) -> bool:
    return any(marker in section.name for marker in ZERO_INIT_MARKERS)


def stored_in_image(section: OutputSection) -> bool:
    """Sections copied out of flash at boot still cost flash space."""
    return is_loaded(section) and not is_zero_init(section)


def normalize_component(archive: str) -> str:
    """Turn an archive path into a short, stable component label.

    The linker records paths such as::

        esp-idf/main/libmain.a
        E:/esp/.../components/esp_wifi/lib/esp32/libesp_wifi.a
        E:/esp/.../xtensa-esp-elf/lib/esp32/libgcc.a

    which collapse to ``esp-idf/main``, ``esp-idf/esp_wifi`` and ``toolchain``.
    This is a heuristic: it depends on ESP-IDF's build directory layout.
    """
    if not archive:
        return "(unknown)"
    path = archive.replace("\\", "/")

    if any(marker in path for marker in TOOLCHAIN_MARKERS):
        return "toolchain"
    if "/components/" in path:
        component = path.split("/components/", 1)[1].split("/", 1)[0]
        return f"esp-idf/{component}"
    if path.startswith("esp-idf/"):
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) > 1 else path
    if "/" in path:
        return path.rsplit("/", 1)[0]
    return path


@dataclass
class StaticRam:
    """Static RAM usage of one memory region group."""

    used: int = 0
    sections: list[OutputSection] = field(default_factory=list)
    capacity: int | None = None
    region_names: tuple[str, ...] = ()

    @property
    def free(self) -> int | None:
        if self.capacity is None:
            return None
        return self.capacity - self.used

    @property
    def ratio(self) -> float | None:
        if not self.capacity:
            return None
        return self.used / self.capacity


@dataclass
class ImageReport:
    map_file: MapFile
    estimated_image_size: int = 0
    actual_image_size: int | None = None
    stored_sections: list[OutputSection] = field(default_factory=list)
    dram: StaticRam = field(default_factory=StaticRam)
    iram: StaticRam = field(default_factory=StaticRam)
    rtc: StaticRam = field(default_factory=StaticRam)
    components: dict[str, int] = field(default_factory=dict)

    @property
    def image_delta(self) -> int | None:
        """Difference between our estimate and the real ``.bin`` size."""
        if self.actual_image_size is None:
            return None
        return self.actual_image_size - self.estimated_image_size

    @property
    def unattributed_bytes(self) -> int:
        """Image bytes the linker did not attribute to any input section."""
        covered = sum(section.covered_size for section in self.stored_sections)
        return self.estimated_image_size - covered


def _flash_window_names(map_file: MapFile) -> set[str]:
    """Regions that only mirror flash, e.g. the IROM window on ESP32.

    ``iram0_2_seg`` covers ``0x400d0020`` and holds ``.flash.text``: it looks
    like IRAM but it is the instruction cache window onto flash, so counting it
    as RAM capacity would be wrong.
    """
    windows: set[str] = set()
    for section in map_file.sections:
        if not section.name.startswith(".flash."):
            continue
        region = map_file.region_for(section.address)
        if region is not None:
            windows.add(region.name)
    return windows


def _group_sections(map_file: MapFile) -> dict[str, list[OutputSection]]:
    groups: dict[str, list[OutputSection]] = {"dram": [], "iram": [], "rtc": []}
    for section in map_file.sections:
        if not is_loaded(section):
            continue
        if section.name.startswith((".dram0.", ".noinit", ".ext_ram.")):
            groups["dram"].append(section)
        elif section.name.startswith(".iram0."):
            groups["iram"].append(section)
        elif section.name.startswith((".rtc.", ".rtc_")):
            groups["rtc"].append(section)
    return groups


def _capacity_for(
    map_file: MapFile,
    prefixes: tuple[str, ...],
    flash_windows: set[str],
) -> tuple[int | None, tuple[str, ...]]:
    regions = [
        r
        for r in map_file.regions
        if r.name.startswith(prefixes) and r.length > 0 and r.name not in flash_windows
    ]
    if not regions:
        return None, ()
    return sum(r.length for r in regions), tuple(r.name for r in regions)


def build_report(map_path: str | Path, bin_path: str | Path | None = None) -> ImageReport:
    map_file = parse_map(map_path)
    groups = _group_sections(map_file)
    stored = sorted(
        (s for s in map_file.sections if stored_in_image(s)),
        key=lambda s: s.size,
        reverse=True,
    )

    report = ImageReport(map_file=map_file)
    report.stored_sections = stored
    report.estimated_image_size = sum(s.size for s in stored)

    if bin_path is not None:
        report.actual_image_size = Path(bin_path).stat().st_size

    flash_windows = _flash_window_names(map_file)
    for key, prefixes in (
        ("dram", ("dram0_",)),
        ("iram", ("iram0_",)),
        ("rtc", ("rtc_",)),
    ):
        sections = sorted(groups[key], key=lambda s: s.size, reverse=True)
        capacity, names = _capacity_for(map_file, prefixes, flash_windows)
        setattr(
            report,
            key,
            StaticRam(
                used=sum(s.size for s in sections),
                sections=sections,
                capacity=capacity,
                region_names=names,
            ),
        )

    components: dict[str, int] = {}
    for section in stored:
        for raw_name, size in section.by_component().items():
            name = normalize_component(raw_name)
            components[name] = components.get(name, 0) + size
    report.components = dict(sorted(components.items(), key=lambda kv: kv[1], reverse=True))
    return report