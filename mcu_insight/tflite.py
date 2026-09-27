"""TensorFlow Lite model analysis: memory budget, operators, quantisation.

The parser walks the FlatBuffer directly using :mod:`mcu_insight.flatbuffer`,
so it has no dependency on TensorFlow, on ``flatbuffers`` or on numpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .flatbuffer import FlatBuffer, FlatBufferError, Table

# ---------------------------------------------------------------------------
# schema enums
# ---------------------------------------------------------------------------

TENSOR_TYPES: dict[int, tuple[str, int]] = {
    0: ("FLOAT32", 32),
    1: ("FLOAT16", 16),
    2: ("INT32", 32),
    3: ("UINT8", 8),
    4: ("INT64", 64),
    5: ("STRING", 0),
    6: ("BOOL", 8),
    7: ("INT16", 16),
    8: ("COMPLEX64", 64),
    9: ("INT8", 8),
    10: ("FLOAT64", 64),
    11: ("COMPLEX128", 128),
    12: ("UINT64", 64),
    13: ("RESOURCE", 0),
    14: ("VARIANT", 0),
    15: ("UINT32", 32),
    16: ("UINT16", 16),
    17: ("INT4", 4),
    18: ("BFLOAT16", 16),
}

#: Curated subset of ``BuiltinOperator``, covering the operators that show up in
#: MCU-sized models. Codes outside this table are reported numerically so that
#: an outdated table can never silently mislabel an operator.
BUILTIN_OPERATORS: dict[int, str] = {
    0: "ADD",
    1: "AVERAGE_POOL_2D",
    2: "CONCATENATION",
    3: "CONV_2D",
    4: "DEPTHWISE_CONV_2D",
    5: "DEPTH_TO_SPACE",
    6: "DEQUANTIZE",
    7: "EMBEDDING_LOOKUP",
    8: "FLOOR",
    9: "FULLY_CONNECTED",
    10: "HASHTABLE_LOOKUP",
    11: "L2_NORMALIZATION",
    12: "L2_POOL_2D",
    13: "LOCAL_RESPONSE_NORMALIZATION",
    14: "LOGISTIC",
    15: "LSH_PROJECTION",
    16: "LSTM",
    17: "MAX_POOL_2D",
    18: "MUL",
    19: "RELU",
    20: "RELU_N1_TO_1",
    21: "RELU6",
    22: "RESHAPE",
    23: "RESIZE_BILINEAR",
    24: "RNN",
    25: "SOFTMAX",
    26: "SPACE_TO_DEPTH",
    27: "SVDF",
    28: "TANH",
    32: "CUSTOM",
    34: "PAD",
    39: "TRANSPOSE",
    40: "MEAN",
    41: "SUB",
    42: "DIV",
    43: "SQUEEZE",
    45: "STRIDED_SLICE",
    49: "SPLIT",
    50: "LOG_SOFTMAX",
    53: "CAST",
    54: "PRELU",
    55: "MAXIMUM",
}

CUSTOM_OP_CODE = 32


def operator_name(code: int) -> str:
    name = BUILTIN_OPERATORS.get(code)
    return name if name else f"OP_{code}"


def tensor_type_name(type_id: int) -> str:
    entry = TENSOR_TYPES.get(type_id)
    return entry[0] if entry else f"TYPE_{type_id}"


def tensor_element_bits(type_id: int) -> int:
    entry = TENSOR_TYPES.get(type_id)
    return entry[1] if entry else 0


# ---------------------------------------------------------------------------
# parsed model
# ---------------------------------------------------------------------------


@dataclass
class Tensor:
    index: int
    name: str
    shape: list[int]
    type_id: int
    buffer_index: int
    element_bits: int
    buffer_bytes: int = 0
    scales: list[float] = field(default_factory=list)
    zero_points: list[int] = field(default_factory=list)

    @property
    def type_name(self) -> str:
        return tensor_type_name(self.type_id)

    @property
    def element_count(self) -> int:
        count = 1
        for dim in self.shape:
            count *= dim
        return count

    @property
    def size_bytes(self) -> int:
        return (self.element_count * self.element_bits + 7) // 8

    @property
    def is_quantized(self) -> bool:
        return bool(self.scales)

    @property
    def is_constant(self) -> bool:
        """A tensor is constant only when its buffer actually holds data.

        Converters give every tensor a buffer index; the activations point at
        empty buffers, so the index alone says nothing.
        """
        return self.buffer_bytes > 0


@dataclass
class Operator:
    index: int
    code: int
    name: str
    custom_code: str | None
    inputs: list[int]
    outputs: list[int]

    @property
    def is_custom(self) -> bool:
        return self.code == CUSTOM_OP_CODE


@dataclass
class ModelReport:
    path: Path
    file_size: int
    schema_version: int
    description: str
    subgraph_count: int
    operator_count: int
    tensors: list[Tensor]
    inputs: list[int]
    outputs: list[int]
    operators: list[Operator]
    weights_bytes: int
    arena_peak: int
    arena_upper_bound: int
    unknown_ops: list[int] = field(default_factory=list)
    custom_ops: list[str] = field(default_factory=list)
    quantization: dict[str, int] = field(default_factory=dict)

    @property
    def operator_histogram(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for operator in self.operators:
            counts[operator.name] = counts.get(operator.name, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    @property
    def parameter_count(self) -> int:
        return sum(t.element_count for t in self.tensors if t.is_constant)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _parse_tensor(table: Table, index: int, buffer_lengths: list[int]) -> Tensor:
    buffer_index = table.scalar(2, "u32", 0)
    quantization = table.table(4)
    scales: list[float] = []
    zero_points: list[int] = []
    if quantization is not None:
        scales = list(quantization.vector_scalars(2, "f32"))
        zero_points = list(quantization.vector_scalars(3, "i64"))
    type_id = table.scalar(1, "i8", 0)
    buffer_bytes = buffer_lengths[buffer_index] if buffer_index < len(buffer_lengths) else 0
    return Tensor(
        index=index,
        name=table.string(3) or f"tensor_{index}",
        shape=list(table.vector_scalars(0, "i32")),
        type_id=type_id,
        buffer_index=buffer_index,
        buffer_bytes=buffer_bytes,
        element_bits=tensor_element_bits(type_id),
        scales=scales,
        zero_points=zero_points,
    )


def _parse_operator_code(table: Table) -> tuple[int, str | None]:
    custom_code = table.string(1)
    builtin = table.scalar(3, "i32", -1)
    if builtin < 0:
        builtin = table.scalar(0, "i8", 0)
    return builtin, custom_code


def _parse_operator(table: Table, index: int, codes: list[tuple[int, str | None]]) -> Operator:
    opcode_index = table.scalar(0, "u32", 0)
    code, custom_code = codes[opcode_index] if opcode_index < len(codes) else (0, None)
    return Operator(
        index=index,
        code=code,
        name=operator_name(code),
        custom_code=custom_code,
        inputs=list(table.vector_scalars(1, "i32")),
        outputs=list(table.vector_scalars(2, "i32")),
    )


def arena_peak(tensors: list[Tensor], operators: list[Operator], outputs: list[int]) -> int:
    """Peak bytes of simultaneously live non-constant tensors.

    A tensor is allocated by the operator that writes it and released after the
    last operator that reads it, which is the model TFLite Micro's arena planner
    uses when it serialises execution.  Constant tensors (those backed by buffer
    data) live in the model itself and are excluded.
    """
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    for tensor_index in _graph_inputs(operators, tensors):
        if not tensors[tensor_index].is_constant:
            starts[tensor_index] = -1

    for operator in operators:
        for out in operator.outputs:
            if 0 <= out < len(tensors) and not tensors[out].is_constant:
                starts.setdefault(out, operator.index)
        for inp in operator.inputs:
            if 0 <= inp < len(tensors) and not tensors[inp].is_constant:
                ends[inp] = operator.index

    for tensor_index in outputs:
        if 0 <= tensor_index < len(tensors) and not tensors[tensor_index].is_constant:
            ends[tensor_index] = len(operators)

    for index in starts:
        ends.setdefault(index, starts[index])

    peak = 0
    for step in range(-1, len(operators) + 1):
        live = 0
        for tensor_index, start in starts.items():
            if start <= step <= ends[tensor_index]:
                live += tensors[tensor_index].size_bytes
        peak = max(peak, live)
    return peak


def _graph_inputs(operators: list[Operator], tensors: list[Tensor]) -> list[int]:
    produced = {out for op in operators for out in op.outputs}
    return [t.index for t in tensors if t.index not in produced]


def load_model(path: str | Path) -> ModelReport:
    """Parse a ``.tflite`` file into a :class:`ModelReport`."""
    model_path = Path(path)
    data = model_path.read_bytes()
    try:
        model = FlatBuffer(data)
    except FlatBufferError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{model_path} is not a FlatBuffer: {exc}") from exc

    root = model.root
    codes = [_parse_operator_code(t) for t in root.vector_tables(1)]
    subgraphs = root.vector_tables(2)
    buffers = root.vector_tables(4)

    buffer_lengths = [len(buffer.vector_bytes(0)) for buffer in buffers]
    weights_bytes = sum(buffer_lengths[1:])
# (weights are summed from buffer_lengths above)

    if not subgraphs:
        tensors: list[Tensor] = []
        operators: list[Operator] = []
        inputs: list[int] = []
        outputs: list[int] = []
    else:
        subgraph = subgraphs[0]
        tensors = [
            _parse_tensor(t, i, buffer_lengths)
            for i, t in enumerate(subgraph.vector_tables(0))
        ]
        operators = [_parse_operator(t, i, codes) for i, t in enumerate(subgraph.vector_tables(3))]
        inputs = list(subgraph.vector_scalars(1, "i32"))
        outputs = list(subgraph.vector_scalars(2, "i32"))

    quantization: dict[str, int] = {}
    for tensor in tensors:
        key = tensor.type_name + ("(quantised)" if tensor.is_quantized else "")
        quantization[key] = quantization.get(key, 0) + 1

    report = ModelReport(
        path=model_path,
        file_size=len(data),
        schema_version=root.scalar(0, "u32", 0),
        description=root.string(3) or "",
        subgraph_count=len(subgraphs),
        operator_count=len(operators),
        tensors=tensors,
        inputs=inputs,
        outputs=outputs,
        operators=operators,
        weights_bytes=weights_bytes,
        arena_peak=arena_peak(tensors, operators, outputs),
        arena_upper_bound=sum(t.size_bytes for t in tensors if not t.is_constant),
        quantization=quantization,
    )
    report.unknown_ops = sorted({op.code for op in report.operators if op.code not in BUILTIN_OPERATORS})
    report.custom_ops = sorted({op.custom_code or "<unnamed>" for op in report.operators if op.is_custom})
    return report

