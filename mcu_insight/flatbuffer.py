"""Minimal FlatBuffers reader.

Only the subset needed to walk a TensorFlow Lite model is implemented: the
root table, vtable field lookup, strings, scalar vectors and offset vectors.
The layout rules come from the FlatBuffers binary format:

* byte 0 holds a ``uoffset`` to the root table;
* a table starts with a signed ``soffset`` such that ``vtable = table - soffset``;
* the vtable is ``[uint16 vtable_size][uint16 table_size][uint16 field_offset...]``
  where each field offset is relative to the table, and ``0`` means "absent";
* strings and vectors are prefixed with a ``uint32`` length;
* every ``uoffset`` points *forward*, relative to the position of the field.
"""

from __future__ import annotations

import struct
from typing import Iterator

_FORMATS = {
    "i8": ("b", 1),
    "u8": ("B", 1),
    "i16": ("h", 2),
    "u16": ("H", 2),
    "i32": ("i", 4),
    "u32": ("I", 4),
    "i64": ("q", 8),
    "u64": ("Q", 8),
    "f32": ("f", 4),
    "f64": ("d", 8),
}


class FlatBufferError(ValueError):
    """Raised when the buffer is not a well-formed FlatBuffer."""


class Table:
    """A FlatBuffers table, with fields addressed by schema index."""

    def __init__(self, buf: memoryview, pos: int):
        self._buf = buf
        self.pos = pos
        vtable = pos - self._read(pos, "i32")
        self._vtable = vtable
        self._vtable_size = self._read(vtable, "u16")
        self._table_size = self._read(vtable + 2, "u16")

    # -- internals ---------------------------------------------------------
    def _read(self, pos: int, fmt: str) -> int:
        code, size = _FORMATS[fmt]
        if pos < 0 or pos + size > len(self._buf):
            raise FlatBufferError(f"field at {pos} is outside the buffer")
        return struct.unpack_from("<" + code, self._buf, pos)[0]

    def field_pos(self, index: int) -> int | None:
        """Absolute position of a field, or ``None`` when it uses its default."""
        entry = 4 + index * 2
        if entry + 2 > self._vtable_size:
            return None
        offset = self._read(self._vtable + entry, "u16")
        if offset == 0:
            return None
        return self.pos + offset

    def _deref(self, pos: int) -> int:
        return pos + self._read(pos, "u32")

    # -- field accessors ---------------------------------------------------
    def scalar(self, index: int, fmt: str, default: int | float):
        pos = self.field_pos(index)
        if pos is None:
            return default
        return self._read(pos, fmt)

    def string(self, index: int) -> str | None:
        pos = self.field_pos(index)
        if pos is None:
            return None
        start = self._deref(pos)
        length = self._read(start, "u32")
        return bytes(self._buf[start + 4:start + 4 + length]).decode("utf-8", "replace")

    def table(self, index: int) -> "Table | None":
        pos = self.field_pos(index)
        if pos is None:
            return None
        return Table(self._buf, self._deref(pos))

    def _vector(self, index: int) -> int | None:
        pos = self.field_pos(index)
        if pos is None:
            return None
        return self._deref(pos)

    def vector_len(self, index: int) -> int:
        start = self._vector(index)
        if start is None:
            return 0
        return self._read(start, "u32")

    def vector_scalars(self, index: int, fmt: str) -> list:
        start = self._vector(index)
        if start is None:
            return []
        count = self._read(start, "u32")
        _, size = _FORMATS[fmt]
        return [self._read(start + 4 + i * size, fmt) for i in range(count)]

    def vector_bytes(self, index: int) -> bytes:
        start = self._vector(index)
        if start is None:
            return b""
        count = self._read(start, "u32")
        return bytes(self._buf[start + 4:start + 4 + count])

    def vector_tables(self, index: int) -> list["Table"]:
        start = self._vector(index)
        if start is None:
            return []
        count = self._read(start, "u32")
        tables = []
        for i in range(count):
            element = start + 4 + i * 4
            tables.append(Table(self._buf, self._deref(element)))
        return tables

    def __iter__(self) -> Iterator["Table"]:
        return iter(self.vector_tables(0))


class FlatBuffer:
    """Entry point: reads the root table of a FlatBuffer."""

    def __init__(self, data: bytes | bytearray | memoryview):
        self._buf = memoryview(bytes(data))
        if len(self._buf) < 8:
            raise FlatBufferError("buffer is too small to be a FlatBuffer")
        root = struct.unpack_from("<I", self._buf, 0)[0]
        if root >= len(self._buf):
            raise FlatBufferError(f"root offset {root} is outside the buffer")
        self.root = Table(self._buf, root)

    @property
    def size(self) -> int:
        return len(self._buf)
