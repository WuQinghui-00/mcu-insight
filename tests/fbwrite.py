"""A tiny FlatBuffers *writer*, used only to build test fixtures.

The production code only ever reads ``.tflite`` files, but no TFLite model or
``flatbuffers`` package is available offline, so the tests construct their own
well-formed buffers.  The layout rules mirror the format specification:

* object addresses are assigned by appending to ``buf``;
* every ``uoffset`` points forward, so a parent is always written before its
  children and the pointer slots are patched afterwards;
* a table's vtable is written immediately before the table, which makes the
  table's leading ``soffset`` positive.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_SIZES = {
    "i8": 1,
    "u8": 1,
    "i16": 2,
    "u16": 2,
    "i32": 4,
    "u32": 4,
    "i64": 8,
    "u64": 8,
    "f32": 4,
    "f64": 8,
}


_CODES = {
    "i8": "b",
    "u8": "B",
    "i16": "h",
    "u16": "H",
    "i32": "i",
    "u32": "I",
    "i64": "q",
    "u64": "Q",
    "f32": "f",
    "f64": "d",
}

@dataclass
class TableHandle:
    pos: int
    offsets: dict[int, int]


def _pos(value) -> int:
    # accepts a raw offset or a TableHandle
    return value.pos if isinstance(value, TableHandle) else int(value)


class Builder:
    """Builds a FlatBuffer in forward order so that uoffsets stay positive."""

    def __init__(self) -> None:
        self.buf = bytearray(b"\x00\x00\x00\x00")  # root uoffset placeholder

    # -- helpers -----------------------------------------------------------
    def _align(self, size: int) -> None:
        while len(self.buf) % size:
            self.buf.append(0)

    # -- objects -----------------------------------------------------------
    def add_string(self, text: str) -> int:
        self._align(4)
        pos = len(self.buf)
        data = text.encode("utf-8")
        self.buf += struct.pack("<I", len(data)) + data + b"\x00"
        return pos

    def add_scalar_vector(self, fmt: str, values) -> int:
        self._align(4)
        pos = len(self.buf)
        self.buf += struct.pack("<I", len(values))
        for value in values:
            self.buf += struct.pack("<" + _CODES[fmt], value)
        return pos

    def add_byte_vector(self, data: bytes) -> int:
        self._align(4)
        pos = len(self.buf)
        self.buf += struct.pack("<I", len(data)) + bytes(data)
        return pos

    def add_offset_vector(self, count: int) -> int:
        """Reserve a vector of ``count`` uoffsets; patch with set_vector_offset."""
        self._align(4)
        pos = len(self.buf)
        self.buf += struct.pack("<I", count) + b"\x00" * (4 * count)
        return pos

    def set_vector_offset(self, vector_pos: int, index: int, target_pos: int) -> None:
        element = _pos(vector_pos) + 4 + 4 * index
        struct.pack_into("<I", self.buf, element, _pos(target_pos) - element)

    def add_table(
        self,
        scalars: dict[int, tuple[str, int]] | None = None,
        offset_fields: tuple[int, ...] = (),
    ) -> TableHandle:
        scalars = scalars or {}
        fields = sorted(set(scalars) | set(offset_fields))
        field_count = max(fields) + 1 if fields else 0
        vtable_size = 4 + 2 * field_count

        self._align(2)
        vtable_pos = len(self.buf)
        self.buf += b"\x00" * vtable_size

        self._align(4)
        table_pos = len(self.buf)
        offsets: dict[int, int] = {}
        cursor = 4  # the leading soffset
        for index in fields:
            fmt = scalars[index][0] if index in scalars else "u32"
            size = _SIZES[fmt]
            if cursor % size:
                cursor += size - (cursor % size)
            offsets[index] = cursor
            cursor += size
        table_size = cursor + (-cursor % 4)
        self.buf += b"\x00" * table_size

        for index, (fmt, value) in scalars.items():
            struct.pack_into("<" + _CODES[fmt], self.buf, table_pos + offsets[index], value)

        vtable = struct.pack("<HH", vtable_size, table_size)
        vtable += b"".join(struct.pack("<H", offsets.get(i, 0)) for i in range(field_count))
        self.buf[vtable_pos:vtable_pos + vtable_size] = vtable
        struct.pack_into("<i", self.buf, table_pos, table_pos - vtable_pos)
        return TableHandle(table_pos, offsets)

    def set_offset(self, table: TableHandle, index: int, target_pos: int) -> None:
        field = _pos(table) + table.offsets[index]
        struct.pack_into("<I", self.buf, field, _pos(target_pos) - field)

    def finish(self, root_pos: int) -> bytes:
        struct.pack_into("<I", self.buf, 0, root_pos)
        return bytes(self.buf)
