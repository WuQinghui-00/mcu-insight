"""Tests for capturing: progress while it runs, and silence when asked."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcu_insight.collector import collect  # noqa: E402
from mcu_insight.store import Store  # noqa: E402

FRAME = '{"v":1,"device":"board","seq":%d,"uptime_ms":%d,"heap":{"free":100000}}'


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.source = self.dir / "frames.jsonl"
        self.source.write_text(
            "\n".join(FRAME % (index, index * 1000) for index in range(1, 4)) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_progress_is_printed_by_default(self):
        # A capture that says nothing for two minutes looks like a hang, and
        # what a reader does with a hang is kill it.
        buffer = io.StringIO()
        with Store(self.dir / "cap.db") as store, contextlib.redirect_stdout(buffer):
            stats = collect(f"file:{self.source}", store, limit=3)
        output = buffer.getvalue()
        self.assertIn("listening on file:", output)
        self.assertIn("Ctrl+C stops early", output)
        self.assertIn("3/3", output)
        self.assertEqual(3, stats.frames)

    def test_quiet_says_nothing(self):
        buffer = io.StringIO()
        with Store(self.dir / "quiet.db") as store, contextlib.redirect_stdout(buffer):
            collect(f"file:{self.source}", store, quiet=True)
        self.assertEqual("", buffer.getvalue())

    def test_every_frame_is_committed_as_it_arrives(self):
        # Ctrl+C is safe: the store commits per frame, so stopping early keeps
        # whatever came in.
        with Store(self.dir / "committed.db") as store:
            collect(f"file:{self.source}", store, limit=2, quiet=True)
        with Store(self.dir / "committed.db") as reopened:
            self.assertEqual(2, reopened.frame_count("board"))


if __name__ == "__main__":
    unittest.main()