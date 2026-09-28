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
    quiet: bool = False,
) -> CollectStats:
    """Read ``source`` and store every frame found in it.

    Progress goes to stdout unless ``quiet``. A capture that says nothing for
    two minutes cannot be told apart from a hang, and what a reader does with a
    command that looks hung is kill it. Every frame is committed as it arrives,
    so stopping early keeps whatever came in.
    """
    stats = CollectStats()
    if limit is not None and limit <= 0:
        return stats

    if not quiet:
        goal = f", stopping after {limit} frame(s)" if limit is not None else ""
        print(f"listening on {source} -- storing into {store.path}{goal}", flush=True)
        print("Ctrl+C stops early; frames already received are on disk", flush=True)

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
        if not quiet:
            seen = f"{stats.frames}/{limit}" if limit is not None else str(stats.frames)
            print(f"  {seen:>8}  {frame.device} #{frame.seq} "
                  f"uptime={frame.uptime_ms:,.0f} ms", flush=True)
        if limit is not None and stats.frames >= limit:
            break
    return stats
