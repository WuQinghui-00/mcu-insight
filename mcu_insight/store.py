"""SQLite storage for telemetry frames."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .telemetry import Frame

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device      TEXT    NOT NULL,
    firmware    TEXT,
    seq         INTEGER,
    uptime_ms   REAL,
    received_at REAL    NOT NULL,
    raw         TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
    frame_id INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
    metric   TEXT    NOT NULL,
    value    REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_metric ON samples(metric, frame_id);
CREATE INDEX IF NOT EXISTS idx_frames_device ON frames(device, id);
"""


@dataclass
class MetricStats:
    metric: str
    count: int
    first: float
    last: float
    minimum: float
    maximum: float

    @property
    def change(self) -> float:
        return self.last - self.first


class Store:
    """Append-only telemetry storage."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- writing -----------------------------------------------------------
    def add(self, frame: Frame, received_at: float | None = None) -> int:
        cursor = self._conn.execute(
            "INSERT INTO frames (device, firmware, seq, uptime_ms, received_at, raw)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                frame.device,
                frame.firmware,
                frame.seq,
                frame.uptime_ms,
                time.time() if received_at is None else received_at,
                frame.raw or frame.to_json(),
            ),
        )
        frame_id = int(cursor.lastrowid)
        self._conn.executemany(
            "INSERT INTO samples (frame_id, metric, value) VALUES (?, ?, ?)",
            [(frame_id, metric, value) for metric, value in frame.flatten().items()],
        )
        self._conn.commit()
        return frame_id

    # -- reading -----------------------------------------------------------
    def devices(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT device, COUNT(*) AS n FROM frames GROUP BY device ORDER BY n DESC, device"
        )
        return [row[0] for row in rows]

    def frame_count(self, device: str | None = None, since_id: int | None = None) -> int:
        if device is None:
            return int(self._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
        query = "SELECT COUNT(*) FROM frames WHERE device = ?"
        params: list = [device]
        if since_id is not None:
            query += " AND id >= ?"
            params.append(since_id)
        return int(self._conn.execute(query, params).fetchone()[0])

    def uptime_range(self, device: str) -> tuple[float, float] | None:
        row = self._conn.execute(
            "SELECT MIN(uptime_ms), MAX(uptime_ms) FROM frames WHERE device = ?",
            (device,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0]), float(row[1])

    def latest_firmware(self, device: str) -> str:
        row = self._conn.execute(
            "SELECT firmware FROM frames WHERE device = ? AND firmware IS NOT NULL"
            " ORDER BY id DESC LIMIT 1",
            (device,),
        ).fetchone()
        return row[0] if row else ""

    def reboot_frames(self, device: str) -> list[int]:
        """Frame ids where uptime_ms went backwards, i.e. the device restarted.

        Reflashing or pressing reset mid-capture is normal, and the frames
        before and after belong to different boot sessions; mixing them makes
        trends meaningless.
        """
        rows = self._conn.execute(
            "SELECT id, uptime_ms FROM frames WHERE device = ? ORDER BY id", (device,)
        ).fetchall()
        reboots: list[int] = []
        previous: float | None = None
        for frame_id, uptime in rows:
            if previous is not None and uptime < previous:
                reboots.append(int(frame_id))
            previous = uptime
        return reboots

    def sessions(self, device: str) -> list[tuple[int, int]]:
        """Boot sessions as (first frame id, frame count), oldest first."""
        rows = self._conn.execute(
            "SELECT id, uptime_ms FROM frames WHERE device = ? ORDER BY id", (device,)
        ).fetchall()
        sessions: list[tuple[int, int]] = []
        previous: float | None = None
        for frame_id, uptime in rows:
            if previous is None or uptime < previous:
                sessions.append((int(frame_id), 0))
            start, count = sessions[-1]
            sessions[-1] = (start, count + 1)
            previous = uptime
        return sessions

    def metric_stats(self, device: str, since_id: int | None = None) -> dict[str, MetricStats]:
        query = (
            "SELECT s.metric, s.value FROM samples s JOIN frames f ON f.id = s.frame_id"
            " WHERE f.device = ?"
        )
        params: list = [device]
        if since_id is not None:
            query += " AND f.id >= ?"
            params.append(since_id)
        query += " ORDER BY f.id"
        rows = self._conn.execute(query, params)
        stats: dict[str, MetricStats] = {}
        for metric, value in rows:
            value = float(value)
            entry = stats.get(metric)
            if entry is None:
                stats[metric] = MetricStats(metric, 1, value, value, value, value)
            else:
                entry.count += 1
                entry.last = value
                entry.minimum = min(entry.minimum, value)
                entry.maximum = max(entry.maximum, value)
        return stats

    def series(self, device: str, metric: str) -> list[tuple[float, float]]:
        rows = self._conn.execute(
            "SELECT f.uptime_ms, s.value FROM samples s JOIN frames f ON f.id = s.frame_id"
            " WHERE f.device = ? AND s.metric = ? ORDER BY f.id",
            (device, metric),
        )
        return [(float(uptime), float(value)) for uptime, value in rows]
