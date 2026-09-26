"""Unit tests for the regression comparison."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.analysis import build_report  # noqa: E402
from mcu_insight.compare import compare_reports  # noqa: E402

SAMPLE = Path(__file__).parent / "data" / "sample.map"

#: The sample map holds 0x30 B of flash rodata and 0x20 B of bss.
GROWN_RODATA = 0x1030
GROWN_BSS = 0x1020


def make_grown(directory: Path) -> Path:
    """Return a copy of the sample map with a bigger rodata and bss."""
    text = SAMPLE.read_text(encoding="utf-8")
    replacements = [
        (".flash.rodata   0x3f400020        0x30", ".flash.rodata   0x3f400020       0x1030"),
        (" .rodata.s      0x3f400020        0x30", " .rodata.s      0x3f400020       0x1030"),
        (".dram0.bss      0x3ffb0000        0x20", ".dram0.bss      0x3ffb0000       0x1020"),
        (" .bss.buf       0x3ffb0000        0x18", " .bss.buf       0x3ffb0000       0x1018"),
    ]
    for old, new in replacements:
        if old not in text:
            raise AssertionError(f"fixture changed, anchor missing: {old!r}")
        text = text.replace(old, new)
    path = directory / "grown.map"
    path.write_text(text, encoding="utf-8")
    return path


class CompareTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        grown = make_grown(Path(self._tmp.name))
        self.before = build_report(SAMPLE)
        self.after = build_report(grown)
        self.comparison = compare_reports(self.before, self.after, partition_size=1500 * 1024)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_image_growth(self) -> None:
        self.assertEqual(self.comparison.image.before, 0x40 + 0x30 + 0x50)
        self.assertEqual(self.comparison.image.after, 0x40 + GROWN_RODATA + 0x50)
        self.assertEqual(self.comparison.image.change, GROWN_RODATA - 0x30)
        self.assertTrue(self.comparison.changed)

    def test_section_delta_is_reported(self) -> None:
        by_name = {d.name: d for d in self.comparison.sections}
        self.assertIn(".flash.rodata", by_name)
        self.assertEqual(by_name[".flash.rodata"].change, GROWN_RODATA - 0x30)
        self.assertAlmostEqual(by_name[".flash.rodata"].percent, (GROWN_RODATA - 0x30) / 0x30)

    def test_ram_delta_is_reported(self) -> None:
        by_name = {d.name: d for d in self.comparison.ram}
        self.assertEqual(by_name["DRAM"].change, GROWN_BSS - 0x20)

    def test_unchanged_entries_are_filtered_out(self) -> None:
        names = [d.name for d in self.comparison.sections]
        self.assertNotIn(".flash.text", names)

    def test_component_delta_is_normalised(self) -> None:
        by_name = {d.name: d for d in self.comparison.components}
        self.assertIn("esp-idf/main", by_name)
        self.assertGreater(by_name["esp-idf/main"].change, 0)

    def test_min_change_filter(self) -> None:
        filtered = compare_reports(self.before, self.after, min_change=100_000)
        self.assertEqual(filtered.sections, [])
        self.assertEqual(filtered.components, [])

    def test_identical_builds_report_no_change(self) -> None:
        same = compare_reports(self.before, build_report(SAMPLE))
        self.assertFalse(same.changed)
        self.assertEqual(same.sections, [])

    def test_overflow_is_detected(self) -> None:
        tight = compare_reports(self.before, self.after, partition_size=0x100)
        self.assertTrue(tight.overflow)


if __name__ == "__main__":
    unittest.main()
