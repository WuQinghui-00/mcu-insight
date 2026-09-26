"""Unit tests for the TensorFlow Lite model parser.

No TFLite model is available offline, so the fixture is constructed byte by
byte with the writer in ``fbwrite`` and the expected numbers are derived from
the schema by hand.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

from fbwrite import Builder  # noqa: E402
from mcu_insight.tflite import (  # noqa: E402
    BUILTIN_OPERATORS,
    load_model,
    operator_name,
    tensor_type_name,
)

# Tensor indices used by the fixture.
T_INPUT, T_WEIGHTS, T_BIAS, T_HIDDEN, T_OUTPUT = 0, 1, 2, 3, 4

OP_FULLY_CONNECTED, OP_SOFTMAX, OP_CUSTOM = 9, 25, 32


def build_model(
    extra_opcode: tuple[int, str | None] | None = None,
    description: str = "mcu-insight fixture",
) -> bytes:
    """Build a five-tensor, two-operator model (optionally with a third op)."""
    b = Builder()

    # --- parents first, children afterwards -------------------------------
    model = b.add_table(scalars={0: ("u32", 3)}, offset_fields=(1, 2, 3, 4))

    opcode_count = 3 if extra_opcode else 2
    opcode_vec = b.add_offset_vector(opcode_count)
    subgraph_vec = b.add_offset_vector(1)
    desc = b.add_string(description)
    buffer_vec = b.add_offset_vector(3)

    code_fc = b.add_table(scalars={2: ("i32", 1), 3: ("i32", OP_FULLY_CONNECTED)})
    code_softmax = b.add_table(scalars={2: ("i32", 1), 3: ("i32", OP_SOFTMAX)})
    code_extra = None
    if extra_opcode:
        code, custom = extra_opcode
        scalars = {2: ("i32", 1)}
        if code >= 0:
            scalars[3] = ("i32", code)
        code_extra = b.add_table(scalars=scalars, offset_fields=(1,) if custom else ())
        if custom:
            b.set_offset(code_extra, 1, b.add_string(custom))

    subgraph = b.add_table(offset_fields=(0, 1, 2, 3, 4))

    buf_empty = b.add_table()
    data_weights = bytes(range(32))
    buf_weights = b.add_table(offset_fields=(0,))
    data_bias = bytes(16)
    buf_bias = b.add_table(offset_fields=(0,))

    tensor_vec = b.add_offset_vector(5)
    inputs_vec = b.add_scalar_vector("i32", [T_INPUT])
    outputs_vec = b.add_scalar_vector("i32", [T_OUTPUT])
    operator_vec = b.add_offset_vector(2 + (1 if extra_opcode else 0))
    subgraph_name = b.add_string("main")

    # --- tensors and their children ---------------------------------------
    tensors = []
    specs = [
        ("input", [1, 8], 9, 0),
        ("weights", [4, 8], 9, 1),
        ("bias", [4], 2, 2),
        ("hidden", [1, 4], 9, 0),
        ("output", [1, 4], 9, 0),
    ]
    for name, shape, type_id, buffer_index in specs:
        # The table must come before its children: uoffsets only point forward.
        tensor = b.add_table(
            scalars={1: ("i8", type_id), 2: ("u32", buffer_index)},
            offset_fields=(0, 3, 4),
        )
        shape_vec = b.add_scalar_vector("i32", shape)
        name_str = b.add_string(name)
        quantization = b.add_table()  # empty quantisation parameters
        b.set_offset(tensor, 0, shape_vec)
        b.set_offset(tensor, 3, name_str)
        b.set_offset(tensor, 4, quantization)
        tensors.append(tensor)

    for index, tensor in enumerate(tensors):
        b.set_vector_offset(tensor_vec, index, tensor.pos)

    # --- operators ---------------------------------------------------------
    op_fc = b.add_table(
        scalars={0: ("u32", 0)},
        offset_fields=(1, 2),
    )
    fc_inputs = b.add_scalar_vector("i32", [T_INPUT, T_WEIGHTS, T_BIAS])
    fc_outputs = b.add_scalar_vector("i32", [T_HIDDEN])
    b.set_offset(op_fc, 1, fc_inputs)
    b.set_offset(op_fc, 2, fc_outputs)

    op_softmax = b.add_table(scalars={0: ("u32", 1)}, offset_fields=(1, 2))
    sm_inputs = b.add_scalar_vector("i32", [T_HIDDEN])
    sm_outputs = b.add_scalar_vector("i32", [T_OUTPUT])
    b.set_offset(op_softmax, 1, sm_inputs)
    b.set_offset(op_softmax, 2, sm_outputs)

    operators = [op_fc, op_softmax]
    if extra_opcode:
        op_extra = b.add_table(scalars={0: ("u32", 2)}, offset_fields=(1, 2))
        extra_inputs = b.add_scalar_vector("i32", [T_OUTPUT])
        extra_outputs = b.add_scalar_vector("i32", [T_OUTPUT])
        b.set_offset(op_extra, 1, extra_inputs)
        b.set_offset(op_extra, 2, extra_outputs)
        operators.append(op_extra)

    for index, operator in enumerate(operators):
        b.set_vector_offset(operator_vec, index, operator.pos)

    # --- wire everything together -----------------------------------------
    b.set_offset(model, 1, opcode_vec)
    b.set_offset(model, 2, subgraph_vec)
    b.set_offset(model, 3, desc)
    b.set_offset(model, 4, buffer_vec)

    b.set_vector_offset(opcode_vec, 0, code_fc.pos)
    b.set_vector_offset(opcode_vec, 1, code_softmax.pos)
    if code_extra is not None:
        b.set_vector_offset(opcode_vec, 2, code_extra.pos)
    b.set_vector_offset(subgraph_vec, 0, subgraph.pos)
    b.set_vector_offset(buffer_vec, 0, buf_empty.pos)
    b.set_vector_offset(buffer_vec, 1, buf_weights.pos)
    b.set_vector_offset(buffer_vec, 2, buf_bias.pos)
    b.set_offset(buf_weights, 0, b.add_byte_vector(data_weights))
    b.set_offset(buf_bias, 0, b.add_byte_vector(data_bias))

    b.set_offset(subgraph, 0, tensor_vec)
    b.set_offset(subgraph, 1, inputs_vec)
    b.set_offset(subgraph, 2, outputs_vec)
    b.set_offset(subgraph, 3, operator_vec)
    b.set_offset(subgraph, 4, subgraph_name)

    return b.finish(model.pos)


class ModelParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "fixture.tflite"
        self.path.write_bytes(build_model())
        self.report = load_model(self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_header_fields(self) -> None:
        self.assertEqual(self.report.schema_version, 3)
        self.assertEqual(self.report.description, "mcu-insight fixture")
        self.assertEqual(self.report.subgraph_count, 1)
        self.assertEqual(self.report.file_size, self.path.stat().st_size)

    def test_tensors(self) -> None:
        names = [t.name for t in self.report.tensors]
        self.assertEqual(names, ["input", "weights", "bias", "hidden", "output"])
        weights = self.report.tensors[T_WEIGHTS]
        self.assertEqual(weights.shape, [4, 8])
        self.assertEqual(weights.type_name, "INT8")
        self.assertEqual(weights.element_count, 32)
        self.assertEqual(weights.size_bytes, 32)
        self.assertEqual(self.report.tensors[T_INPUT].size_bytes, 8)
        self.assertEqual(self.report.tensors[T_BIAS].size_bytes, 16)

    def test_inputs_and_outputs(self) -> None:
        self.assertEqual(self.report.inputs, [T_INPUT])
        self.assertEqual(self.report.outputs, [T_OUTPUT])

    def test_operators(self) -> None:
        names = [op.name for op in self.report.operators]
        self.assertEqual(names, ["FULLY_CONNECTED", "SOFTMAX"])
        self.assertEqual(self.report.operators[0].inputs, [T_INPUT, T_WEIGHTS, T_BIAS])
        self.assertEqual(self.report.operators[0].outputs, [T_HIDDEN])
        self.assertEqual(self.report.operator_histogram, {"FULLY_CONNECTED": 1, "SOFTMAX": 1})

    def test_weights_and_parameters(self) -> None:
        self.assertEqual(self.report.weights_bytes, 32 + 16)
        self.assertEqual(self.report.parameter_count, 32 + 4)

    def test_arena_peak_uses_tensor_lifetimes(self) -> None:
        # input(8) is alive while op0 also allocates hidden(4) -> 12
        # hidden(4) and output(4) overlap around op1     -> 8
        self.assertEqual(self.report.arena_peak, 12)
        self.assertEqual(self.report.arena_upper_bound, 8 + 4 + 4)

    def test_quantization_is_reported(self) -> None:
        self.assertIn("INT8", self.report.quantization)
        self.assertEqual(self.report.quantization["INT8"], 4)


class OperatorTableTest(unittest.TestCase):
    def test_known_operators(self) -> None:
        self.assertEqual(operator_name(3), "CONV_2D")
        self.assertEqual(operator_name(9), "FULLY_CONNECTED")
        self.assertEqual(operator_name(32), "CUSTOM")

    def test_unknown_operator_keeps_its_code(self) -> None:
        self.assertEqual(operator_name(4242), "OP_4242")

    def test_every_curated_code_is_unique(self) -> None:
        self.assertEqual(len(BUILTIN_OPERATORS), len(set(BUILTIN_OPERATORS.values())))

    def test_tensor_type_names(self) -> None:
        self.assertEqual(tensor_type_name(9), "INT8")
        self.assertEqual(tensor_type_name(0), "FLOAT32")
        self.assertEqual(tensor_type_name(200), "TYPE_200")


class OperatorFlagTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, data: bytes):
        path = self.dir / "m.tflite"
        path.write_bytes(data)
        return load_model(path)

    def test_custom_operator_is_flagged(self) -> None:
        report = self._load(build_model(extra_opcode=(OP_CUSTOM, "MyCustomOp")))
        self.assertEqual(report.custom_ops, ["MyCustomOp"])
        self.assertEqual(report.unknown_ops, [])
        self.assertIn("CUSTOM", report.operator_histogram)

    def test_unknown_operator_code_is_flagged(self) -> None:
        report = self._load(build_model(extra_opcode=(9999, None)))
        self.assertEqual(report.unknown_ops, [9999])
        self.assertIn("OP_9999", report.operator_histogram)

    def test_extra_operator_does_not_change_the_arena_peak(self) -> None:
        report = self._load(build_model(extra_opcode=(OP_CUSTOM, "MyCustomOp")))
        self.assertEqual(report.arena_peak, 12)


if __name__ == "__main__":
    unittest.main()
