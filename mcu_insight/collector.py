"""Ingest telemetry frames from a device stream into the store."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .store import Store
from .telemetry import TelemetryError, iter_frames


@dataclass
class CollectStats:
    frames: int = 0
    skipped: int = 0
    errors: int = 0
    last_device: str = ""

    def __str__(self) -> str:
        return (
            f"{self.frames} frame(s) stored, {self.skipped} line(s) ignored, "
            f"{self.errors} malformed frame(s)"
        )


def iter_lines(source: str) -> Iterator[str]:
    """Yield raw lines from ``stdin``, ``file:PATH`` or ``serial:PORT[@BAUD]``.

    A serial port needs ``pyserial``; it ships with the ESP-IDF Python
    environment, so ``collect`` works there without further installation.
    """
    if source == "stdin":
        yield from sys.stdin
        return

    if source.startswith("file:"):
        path = Path(source[len("file:"):])
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return

    if source.startswith("serial:"):
        spec = source[len("serial:"):]
        port, _, baud = spec.partition("@")
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - host dependent
            raise RuntimeError(
                "reading a serial port needs pyserial (pip install pyserial)"
            ) from exc
        with serial.Serial(port, int(baud or 115200), timeout=1) as handle:
            while True:
                raw = handle.readline()
                if not raw:
                    continue
                yield raw.decode("utf-8", errors="replace")
        return

    raise ValueError(f"unsupported source: {source}")


def collect(
    source: str,
    store: Store,
    limit: int | None = None,
    verbose: bool = False,
) -> CollectStats:
    """Read ``source`` and store every frame found in it."""
    stats = CollectStats()
    if limit is not None and limit <= 0:
        return stats

    for line in iter_lines(source):
        if not line.strip():
            continue
        if not line.lstrip().startswith("{"):
            stats.skipped += 1
            continue
        try:
            frame = next(iter(iter_frames(iter([line]))))
        except StopIteration:
            stats.skipped += 1
            continue
        except TelemetryError as exc:
            stats.errors += 1
            if verbose:
                print(f"malformed frame: {exc}", file=sys.stderr)
            continue

        store.add(frame)
        stats.frames += 1
        stats.last_device = frame.device
        if verbose:
            print(f"{frame.device} #{frame.seq} uptime={frame.uptime_ms:.0f} ms")
        if limit is not None and stats.frames >= limit:
            break
    return stats
