"""Command line interface for MCU-Insight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import compare as compare_mod
from .analysis import build_report
from .model_report import model_to_dict, render_model
from .report import render
from .tflite import load_model


def parse_size(text: str) -> int:
    """Parse ``1500K`` / ``2M`` / ``1048576`` into bytes."""
    cleaned = text.strip().replace("_", "")
    multiplier = 1
    suffix = cleaned[-1:].upper()
    if suffix in {"K", "M", "G"}:
        multiplier = {"K": 1024, "M": 1024**2, "G": 1024**3}[suffix]
        cleaned = cleaned[:-1]
    return int(cleaned, 0) * multiplier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu-insight",
        description="Static resource analysis for embedded firmware and TinyML models.",
    )
    parser.add_argument("--version", action="version", version=f"mcu-insight {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze",
        help="Summarise the flash / RAM footprint of one linked firmware image.",
    )
    analyze.add_argument("map", help="path to the .map file produced by the linker")
    analyze.add_argument("--bin", dest="binary", help="path to the .bin file, for cross-checking")
    analyze.add_argument(
        "--partition",
        help="app partition size, e.g. 1500K or 0x177000 (default: 1500K)",
    )
    analyze.add_argument("--top", type=int, default=10, help="how many components to list")
    analyze.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    compare = sub.add_parser(
        "compare",
        help="Show what changed in the flash / RAM footprint between two builds.",
    )
    compare.add_argument("before", help="baseline .map file")
    compare.add_argument("after", help="new .map file")
    compare.add_argument("--before-bin", help="baseline .bin file, for exact image sizes")
    compare.add_argument("--after-bin", help="new .bin file, for exact image sizes")
    compare.add_argument(
        "--partition",
        help="app partition size, e.g. 1500K or 0x177000 (default: 1500K)",
    )
    compare.add_argument(
        "--min-change",
        type=int,
        default=0,
        help="only report entries whose size changed by more than this many bytes",
    )
    compare.add_argument("--top", type=int, default=12, help="how many entries to list")
    compare.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    model = sub.add_parser(
        "model",
        help="Analyse a TensorFlow Lite model: arena, weights, operators, quantisation.",
    )
    model.add_argument("tflite", help="path to the .tflite model")
    model.add_argument("--top", type=int, default=12, help="how many tensors to list")
    model.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def _report_to_dict(report, partition_size):
    return {
        "map": str(report.map_file.path),
        "image": {
            "estimated_bytes": report.estimated_image_size,
            "actual_bytes": report.actual_image_size,
            "delta_bytes": report.image_delta,
            "partition_bytes": partition_size,
        },
        "sections": [
            {
                "name": section.name,
                "address": section.address,
                "size": section.size,
                "stored_in_flash": True,
            }
            for section in report.stored_sections
        ],
        "ram": {
            "dram": {"used": report.dram.used, "capacity": report.dram.capacity},
            "iram": {"used": report.iram.used, "capacity": report.iram.capacity},
            "rtc": {"used": report.rtc.used, "capacity": report.rtc.capacity},
        },
        "components": report.components,
    }


def _check_file(path: Path, label: str) -> bool:
    if not path.is_file():
        print(f"error: {label} not found: {path}", file=sys.stderr)
        return False
    return True


def cmd_analyze(args: argparse.Namespace) -> int:
    map_path = Path(args.map)
    if not _check_file(map_path, "map file"):
        return 2
    binary = Path(args.binary) if args.binary else None
    if binary is not None and not _check_file(binary, "binary"):
        return 2

    partition_size = parse_size(args.partition) if args.partition else 1500 * 1024
    report = build_report(map_path, binary)

    if args.json:
        print(json.dumps(_report_to_dict(report, partition_size), indent=2))
    else:
        print(render(report, partition_size=partition_size, top=args.top))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    before_map, after_map = Path(args.before), Path(args.after)
    if not _check_file(before_map, "baseline map file"):
        return 2
    if not _check_file(after_map, "new map file"):
        return 2
    before_bin = Path(args.before_bin) if args.before_bin else None
    after_bin = Path(args.after_bin) if args.after_bin else None
    if before_bin is not None and not _check_file(before_bin, "baseline binary"):
        return 2
    if after_bin is not None and not _check_file(after_bin, "new binary"):
        return 2

    partition_size = parse_size(args.partition) if args.partition else 1500 * 1024
    comparison = compare_mod.compare_reports(
        build_report(before_map, before_bin),
        build_report(after_map, after_bin),
        partition_size=partition_size,
        min_change=args.min_change,
    )

    if args.json:
        print(json.dumps(compare_mod.to_dict(comparison), indent=2))
    else:
        print(compare_mod.render(comparison, top=args.top))

    return 1 if comparison.overflow else 0


def cmd_model(args: argparse.Namespace) -> int:
    path = Path(args.tflite)
    if not _check_file(path, "model file"):
        return 2
    try:
        report = load_model(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(model_to_dict(report), indent=2))
    else:
        print(render_model(report, top=args.top))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "compare":
        return cmd_compare(args)
    if args.command == "model":
        return cmd_model(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())