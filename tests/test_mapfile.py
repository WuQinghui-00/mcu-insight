"""Unit tests for the linker map parser and the resource report."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.analysis import build_report, normalize_component  # noqa: E402
from mcu_insight.mapfile import parse_map  # noqa: E402

SAMPLE = Path(__file__).parent / "data" / "sample.map"


class ParseMapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.map_file = parse_map(SAMPLE)

    def test_memory_regions(self) -> None:
        names = [r.name for r in self.map_file.regions]
        self.assertEqual(
            names,
            ["iram0_0_seg", "dram0_0_seg", "drom0_0_seg", "iram0_2_seg", "*default*"],
        )
        iram = self.map_file.regions[0]
        self.assertEqual(iram.origin, 0x40080000)
        self.assertEqual(iram.length, 0x00020000)
        self.assertEqual(iram.attributes, "xr")

    def test_output_sections(self) -> None:
        names = [s.name for s in self.map_file.sections]
        self.assertEqual(
            names,
            [".iram0.text", ".dram0.bss", ".flash.rodata", ".flash.text", ".debug_info"],
        )

    def test_sizes_and_symbol_lines_are_ignored(self) -> None:
        iram = self.map_file.section(".iram0.text")
        self.assertEqual(iram.size, 0x40)
        # .text.a, .text.b, .text.c and one *fill*; the symbol line is not a contribution.
        self.assertEqual(len(iram.contributions), 4)
        self.assertEqual(iram.fill_size, 0x8)
        self.assertEqual(iram.covered_size, 0x10 + 0x20 + 0x8)

    def test_source_is_carried_over_to_following_contributions(self) -> None:
        iram = self.map_file.section(".iram0.text")
        last = [c for c in iram.contributions if not c.is_fill][-1]
        self.assertEqual(last.section, ".text.c")
        # .text.c has no file on its line, so it must inherit the previous one.
        self.assertEqual(last.archive, "esp-idf/efuse/libefuse.a")

    def test_relaxation_annotation_is_skipped(self) -> None:
        rodata = self.map_file.section(".flash.rodata")
        self.assertEqual(len(rodata.contributions), 1)
        self.assertEqual(rodata.contributions[0].size, 0x30)

    def test_region_lookup_prefers_the_smallest_match(self) -> None:
        # 0x40080010 is inside iram0_0_seg only.
        self.assertEqual(self.map_file.region_for(0x40080010).name, "iram0_0_seg")
        # 0x400d0020 is the flash instruction window, not real IRAM.
        self.assertEqual(self.map_file.region_for(0x400d0020).name, "iram0_2_seg")


class ComponentNormalisationTest(unittest.TestCase):
    def test_esp_idf_component(self) -> None:
        self.assertEqual(normalize_component("esp-idf/main/libmain.a"), "esp-idf/main")
        self.assertEqual(
            normalize_component("esp-idf/mbedtls/mbedtls/library"),
            "esp-idf/mbedtls",
        )

    def test_absolute_component_path(self) -> None:
        archive = "E:/esp/Espressif/frameworks/esp-idf-v5.3.1/components/esp_wifi/lib/esp32"
        self.assertEqual(normalize_component(archive), "esp-idf/esp_wifi")

    def test_toolchain(self) -> None:
        archive = "E:/esp/Espressif/tools/xtensa-esp-elf/esp-13.2.0/lib/gcc/esp32"
        self.assertEqual(normalize_component(archive), "toolchain")


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_report(SAMPLE)

    def test_flash_image_excludes_bss_and_debug(self) -> None:
        # .iram0.text + .flash.rodata + .flash.text, no .dram0.bss, no .debug_info
        self.assertEqual(self.report.estimated_image_size, 0x40 + 0x30 + 0x50)

    def test_flash_window_is_not_counted_as_iram_capacity(self) -> None:
        self.assertEqual(self.report.iram.capacity, 0x20000)
        self.assertEqual(self.report.iram.used, 0x40)

    def test_dram_capacity_and_usage(self) -> None:
        self.assertEqual(self.report.dram.capacity, 0x2C200)
        self.assertEqual(self.report.dram.used, 0x20)
        self.assertEqual(self.report.dram.free, 0x2C200 - 0x20)

    def test_components_are_normalised(self) -> None:
        self.assertIn("esp-idf/main", self.report.components)


if __name__ == "__main__":
    unittest.main()
