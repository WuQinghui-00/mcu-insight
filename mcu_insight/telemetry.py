"""MCU-Insight telemetry frames (schema v1).

See ``docs/telemetry-schema.md`` for the wire format.  Nested frame values are
flattened into dotted metric names so that every series fits one table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

SCHEMA_VERSION = 1


class TelemetryError(ValueError):
    """Raised when a line is a frame but not a well-formed one."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, bool))


@dataclass(frozen=True)
class TaskStack:
    name: str
    prio: int | None = None
    stack_total: int | None = None
    stack_free_min: int | None = None


@dataclass
class Frame:
    device: str
    uptime_ms: float
    version: int = SCHEMA_VERSION
    firmware: str = ""
    seq: int = 0
    heap: dict[str, float] = field(default_factory=dict)
    net: dict[str, float] = field(default_factory=dict)
    tasks: list[TaskStack] = field(default_factory=list)
    custom: dict[str, float] = field(default_factory=dict)
    raw: str = ""

    def flatten(self) -> dict[str, float]:
        """Nested frame -> dotted metric names."""
        values: dict[str, float] = {"uptime_ms": float(self.uptime_ms)}
        for key, value in self.heap.items():
            values[f"heap.{key}"] = float(value)
        for key, value in self.net.items():
            values[f"net.{key}"] = float(value)
        for task in self.tasks:
            if task.stack_free_min is not None:
                values[f"task.{task.name}.stack_free_min"] = float(task.stack_free_min)
            if task.stack_total is not None:
                values[f"task.{task.name}.stack_total"] = float(task.stack_total)
            if task.prio is not None:
                values[f"task.{task.name}.prio"] = float(task.prio)
        for key, value in self.custom.items():
            values[f"custom.{key}"] = float(value)
        return values

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "v": self.version,
            "device": self.device,
            "uptime_ms": self.uptime_ms,
        }
        if self.firmware:
            data["fw"] = self.firmware
        if self.seq:
            data["seq"] = self.seq
        if self.heap:
            data["heap"] = self.heap
        if self.net:
            data["net"] = self.net
        if self.tasks:
            data["tasks"] = [
                {
                    key: value
                    for key, value in (
                        ("name", task.name),
                        ("prio", task.prio),
                        ("stack_total", task.stack_total),
                        ("stack_free_min", task.stack_free_min),
                    )
                    if value is not None
                }
                for task in self.tasks
            ]
        if self.custom:
            data["custom"] = self.custom
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))


def looks_like_frame(line: str) -> bool:
    """Cheap test used to skip ESP-IDF log lines in a mixed stream."""
    stripped = line.strip()
    return stripped.startswith("{") and '"device"' in stripped and '"v"' in stripped


def parse_frame(line: str) -> Frame:
    """Parse one JSON line into a :class:`Frame`."""
    text = line.strip()
    if not text.startswith("{"):
        raise TelemetryError("line is not a JSON object")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TelemetryError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TelemetryError("frame must be a JSON object")

    version = data.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        raise TelemetryError("field 'v' must be an integer")
    if version > SCHEMA_VERSION:
        raise TelemetryError(f"unsupported schema version {version}")

    device = data.get("device")
    if not isinstance(device, str) or not device:
        raise TelemetryError("field 'device' must be a non-empty string")

    uptime = data.get("uptime_ms")
    if not _is_number(uptime) or isinstance(uptime, bool):
        raise TelemetryError("field 'uptime_ms' must be a number")

    seq = data.get("seq", 0)
    if not isinstance(seq, int) or isinstance(seq, bool):
        seq = 0

    tasks = []
    for entry in data.get("tasks") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        tasks.append(
            TaskStack(
                name=entry["name"],
                prio=entry.get("prio") if isinstance(entry.get("prio"), int) else None,
                stack_total=(
                    entry.get("stack_total")
                    if isinstance(entry.get("stack_total"), int)
                    else None
                ),
                stack_free_min=(
                    entry.get("stack_free_min")
                    if _is_number(entry.get("stack_free_min"))
                    else None
                ),
            )
        )

    def numbers(section: str) -> dict[str, float]:
        raw = data.get(section) or {}
        if not isinstance(raw, dict):
            return {}
        return {
            key: float(value)
            for key, value in raw.items()
            if isinstance(value, (int, float, bool))
        }

    return Frame(
        device=device,
        uptime_ms=float(uptime),
        version=version,
        firmware=data.get("fw") if isinstance(data.get("fw"), str) else "",
        seq=seq,
        heap=numbers("heap"),
        net=numbers("net"),
        tasks=tasks,
        custom=numbers("custom"),
        raw=text,
    )


def iter_frames(lines: Iterator[str]) -> Iterator[Frame]:
    """Yield frames from a mixed log stream, skipping everything else.

    Lines that look like frames but fail to parse raise :class:`TelemetryError`
    so the caller can count them separately from ordinary log noise.
    """
    for line in lines:
        if not looks_like_frame(line):
            continue
        yield parse_frame(line)
