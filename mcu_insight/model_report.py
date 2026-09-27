"""Human-readable rendering of a TensorFlow Lite model report."""

from __future__ import annotations

from .report import human_bytes
from .tflite import ModelReport


def render_model(report: ModelReport, top: int = 12) -> str:
    lines: list[str] = []

    lines.append("MCU-Insight - TensorFlow Lite model report")
    lines.append(f"model     : {report.path}")
    lines.append(
        f"size      : {report.file_size:,} B ({human_bytes(report.file_size)})"
    )
    lines.append(
        f"schema    : version {report.schema_version}   "
        f"subgraphs {report.subgraph_count}   "
        f"operators {report.operator_count}   "
        f"tensors {len(report.tensors)}"
    )
    if report.description:
        lines.append(f"description: {report.description}")

    lines.append("")
    lines.append("Memory")
    weights_share = report.weights_bytes / report.file_size if report.file_size else 0
    lines.append(
        f"  weights in file   : {report.weights_bytes:>9,} B  ({weights_share:.1%} of file, "
        f"{report.parameter_count:,} parameters)"
    )
    lines.append(
        f"  tensor arena peak : {report.arena_peak:>9,} B  "
        f"(worst case if nothing is reused: {report.arena_upper_bound:,} B)"
    )
    if report.arena_peak:
        saving = 1 - report.arena_peak / report.arena_upper_bound if report.arena_upper_bound else 0
        lines.append(f"  arena reuse saves : {saving:.1%}")

    if report.quantization:
        lines.append("")
        lines.append("Tensor types")
        for name, count in sorted(report.quantization.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:<20} {count:>4} tensor(s)")

    if report.operators:
        lines.append("")
        lines.append("Operators")

        counts: dict[str, int] = {}
        for operator in report.operators:
            label = f"{operator.name}({operator.code})"
            counts[label] = counts.get(label, 0) + 1
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {label:<28} {count:>4}")

    if report.tensors:
        lines.append("")
        ranked = sorted(report.tensors, key=lambda t: t.size_bytes, reverse=True)
        lines.append(f"Largest tensors (top {min(top, len(ranked))})")
        for tensor in ranked[:top]:
            shape = "x".join(str(d) for d in tensor.shape) or "scalar"
            kind = "constant" if tensor.is_constant else "runtime"
            lines.append(
                f"  {tensor.name:<20} {shape:>12}  {tensor.type_name:<8} "
                f"{tensor.size_bytes:>7,} B  {kind}"
            )

    warnings: list[str] = []
    if report.custom_ops:
        warnings.append(
            "custom operators need a hand-written kernel: " + ", ".join(report.custom_ops)
        )
    if report.unknown_ops:
        warnings.append(
            "operator codes outside the curated table (verify against your TFLite version): "
            + ", ".join(str(code) for code in report.unknown_ops)
        )
    if warnings:
        lines.append("")
        lines.append("Warnings")
        for warning in warnings:
            lines.append(f"  ! {warning}")

    return "\n".join(lines)


def model_to_dict(report: ModelReport) -> dict:
    return {
        "path": str(report.path),
        "file_size": report.file_size,
        "schema_version": report.schema_version,
        "description": report.description,
        "subgraphs": report.subgraph_count,
        "operator_count": report.operator_count,
        "weights_bytes": report.weights_bytes,
        "parameter_count": report.parameter_count,
        "arena_peak_bytes": report.arena_peak,
        "arena_upper_bound_bytes": report.arena_upper_bound,
        "inputs": report.inputs,
        "outputs": report.outputs,
        "quantization": report.quantization,
        "operators": report.operator_histogram,
        "custom_ops": report.custom_ops,
        "unknown_ops": report.unknown_ops,
        "tensors": [
            {
                "index": tensor.index,
                "name": tensor.name,
                "shape": tensor.shape,
                "type": tensor.type_name,
                "bytes": tensor.size_bytes,
                "constant": tensor.buffer_index != 0,
            }
            for tensor in report.tensors
        ],
    }
