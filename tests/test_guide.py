"""Tests for the landing-page builder.

The commands it runs against real captures are not repeated here; these check
what it builds out of whatever those runs return.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_guide import TEXT, paragraph_containing, render, take  # noqa: E402

FACTS = {
    "summary": "MCU-Insight - telemetry summary\nDevices\n  board 5 frames",
    "check_fail": "Violations (1)\n  ! heap.free: falls at -956.9 per second\nRESULT: FAIL",
    "analyze": "Flash image\n  actual .bin : 1,065,728 B",
    "model": "Memory\n  tensor arena peak : 96 B",
    "evidence": "[E1] device board\n[E2] capture 21 frames",
    "answer": "The leak is s_leak_sink = malloc(2048) once per loop [E98].",
    "audit": "citations   : 50 distinct of 126 in the pack\nRESULT: OK",
}


class GuidePageTest(unittest.TestCase):
    def test_both_languages_render_a_complete_page(self):
        for lang in ("en", "zh"):
            with self.subTest(lang=lang):
                page = render(lang, FACTS)
                self.assertTrue(page.startswith("<!doctype html>"))
                self.assertTrue(page.endswith("</html>"))
                self.assertIn(TEXT[lang]["page_title"], page)
                self.assertEqual(page.count("<section>"), page.count("</section>"))

    def test_the_page_carries_the_outputs_it_was_given(self):
        page = render("en", FACTS)
        for key in ("summary", "check_fail", "analyze", "model", "evidence", "audit"):
            self.assertIn(FACTS[key].splitlines()[0], page, key)

    def test_the_page_needs_no_javascript_and_no_network(self):
        page = render("en", FACTS)
        self.assertNotIn("<script", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_the_language_switch_points_both_ways(self):
        self.assertIn('href="index.zh.html"', render("en", FACTS))
        self.assertIn('href="index.html"', render("zh", FACTS))

    def test_a_repository_link_appears_only_when_one_is_given(self):
        self.assertNotIn("github.com", render("en", FACTS))
        page = render("en", FACTS, "https://github.com/me/mcu-insight")
        self.assertIn('href="https://github.com/me/mcu-insight"', page)

    def test_output_is_escaped_rather_than_injected(self):
        page = render("en", {**FACTS, "evidence": "<script>alert(1)</script>"})
        self.assertNotIn("<script", page)
        self.assertIn("&lt;script&gt;", page)

    def test_take_says_when_it_dropped_lines(self):
        self.assertEqual("a\nb\n...", take("a\nb\nc\nd", 2))
        self.assertEqual("a\nb", take("a\nb", 5))


class ExcerptTest(unittest.TestCase):
    def test_a_paragraph_is_read_up_to_the_blank_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "answer.md"
            path.write_text(
                "# Answer\n\nThe leak is `s_leak_sink = malloc(2048)` once per loop [E98].\n"
                "Confidence: high.\n\nNext section\n",
                encoding="utf-8",
            )
            excerpt = paragraph_containing(path, "s_leak_sink = malloc(2048)")
        self.assertIn("once per loop [E98].", excerpt)
        self.assertIn("Confidence: high.", excerpt)
        self.assertNotIn("Next section", excerpt)
        self.assertNotIn("`", excerpt)

    def test_a_missing_needle_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "answer.md"
            path.write_text("nothing here\n", encoding="utf-8")
            self.assertEqual("", paragraph_containing(path, "s_leak_sink"))


if __name__ == "__main__":
    unittest.main()