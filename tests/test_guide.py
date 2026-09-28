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
    "component_files": ["CMakeLists.txt", "include/mcu_telemetry.h", "mcu_telemetry.c"],
    "analyze": "Flash image\n  actual .bin : 1,065,728 B",
    "model": "Memory\n  tensor arena peak : 96 B",
    "summary": "MCU-Insight - telemetry summary\nDevices\n  board 5 frames",
    "check": "Violations (1)\n  ! heap.free: falls at -956.9 per second\nRESULT: FAIL",
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
        for key in ("analyze", "model", "summary", "check", "evidence", "answer", "audit"):
            self.assertIn(FACTS[key].splitlines()[0], page, key)

    def test_there_are_two_routes_and_they_are_labelled(self):
        page = render("en", FACTS)
        self.assertIn("A. Change nothing", page)
        self.assertIn("B. Add three lines", page)
        self.assertIn("You touch three places", page)

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

    def test_the_copied_file_list_comes_from_the_component(self):
        # The list of files step 1 writes is read from the component itself, so
        # a new file in the agent shows up on the page without editing it.
        page = render("en", {**FACTS, "component_files": ["CMakeLists.txt", "mcu_telemetry.c"]})
        self.assertIn("CMakeLists.txt", page)
        self.assertIn("mcu_telemetry.c", page)
        self.assertIn("created by the command", page)

    def test_every_step_says_which_directory_it_belongs_in(self):
        page = render("en", FACTS)
        self.assertGreaterEqual(page.count('class="where"'), 7, "each command names its directory")
        self.assertIn("run inside mcu-insight", page)
        self.assertIn("run inside My-Project", page)

    def test_the_three_lines_are_marked_with_where_they_go(self):
        page = render("en", FACTS)
        for marker in ("1. at the very top", "2. outside app_main",
                       "3. after the hardware is up"):
            self.assertIn(marker, page)

    def test_the_chinese_page_marks_the_lines_in_chinese(self):
        page = render("zh", FACTS)
        self.assertIn("第 1 行", page)
        self.assertIn("第 3 行", page)
        self.assertIn("业务代码一个字都不用改", page)

    def test_the_page_says_what_is_reported_without_any_code(self):
        # The point of the minimal snippet: the reader does not have to decide
        # what to watch, because heap, stacks, idle and sleep come for free.
        page = render("en", FACTS)
        self.assertIn("how much stack every task has left", page)
        self.assertIn("CONFIG_FREERTOS_USE_TRACE_FACILITY=y", page)
        self.assertIn("CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y", page)
        self.assertIn("CONFIG_PM_ENABLE=y", page)
        self.assertIn("you do not have to decide what to watch", page)

    def test_the_optional_extras_are_marked_optional(self):
        page = render("en", FACTS)
        self.assertIn("mcu_telemetry_register_task", page)
        self.assertIn("mcu_telemetry_histogram_add", page)
        self.assertIn("optional:", page)

    def test_the_page_links_to_a_report_you_can_open(self):
        self.assertIn('href="report-demo.html"', render("en", FACTS))

    def test_the_page_shows_what_a_diagnosis_contains(self):
        page = render("en", FACTS)
        for part in ("Root cause:", "Alternatives:", "Fix:", "Verification:"):
            self.assertIn(part, page, part)
        self.assertIn("--lang zh", page, "the page says how to ask for a Chinese answer")

    def test_the_chinese_page_explains_the_answer_language(self):
        page = render("zh", FACTS)
        self.assertIn("根因", page)
        self.assertIn("用中文回答", page)
        self.assertIn("标识符", page)

    def test_the_page_ends_with_what_the_tool_is_worth(self):
        page = render("en", FACTS)
        self.assertIn("So what is it worth", page)
        self.assertIn("It sees what an AI cannot", page)
        self.assertIn("does not let the AI make things up", page)
    def test_the_page_lists_what_the_reader_ends_up_with(self):
        page = render("en", FACTS)
        for artifact in ("report.html", "captures/board.db", "baseline.json",
                         "prompt.txt and answer.md", "exit code of check"):
            self.assertIn(artifact, page, artifact)
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