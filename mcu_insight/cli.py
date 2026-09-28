"""Command line interface for MCU-Insight."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import checks as checks_mod
from . import compare as compare_mod
from . import diagnose as diagnose_mod
from .analysis import build_report
from .collector import collect
from .html_report import build_html, gather_fault_cases
from .model_report import model_to_dict, render_model
from .report import render
from .simulator import SCENARIOS, iter_frames
from .store import Store
from .telemetry_report import render_summary
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
        description="Static and runtime resource analysis for embedded firmware.",
    )
    parser.add_argument("--version", action="version", version=f"mcu-insight {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze",
        help="Summarise the flash / RAM footprint of one linked firmware image.",
    )
    analyze.add_argument("map", help="path to the .map file produced by the linker")
    analyze.add_argument("--bin", dest="binary", help="path to the .bin file, for cross-checking")
    analyze.add_argument("--partition", help="app partition size, e.g. 1500K or 0x177000")
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
    compare.add_argument("--partition", help="app partition size, e.g. 1500K")
    compare.add_argument("--min-change", type=int, default=0)
    compare.add_argument("--top", type=int, default=12)
    compare.add_argument("--json", action="store_true")

    model = sub.add_parser(
        "model",
        help="Analyse a TensorFlow Lite model: arena, weights, operators, quantisation.",
    )
    model.add_argument("tflite", help="path to the .tflite model")
    model.add_argument("--top", type=int, default=12)
    model.add_argument("--json", action="store_true")

    collect_parser = sub.add_parser(
        "collect",
        help="Read telemetry frames from a device stream and store them.",
    )
    collect_parser.add_argument("--db", required=True, help="path to the SQLite database")
    collect_parser.add_argument(
        "--source",
        default="stdin",
        help="stdin (default), file:PATH, or serial:PORT[@BAUD]",
    )
    collect_parser.add_argument("--limit", type=int, help="stop after this many frames")
    collect_parser.add_argument("--quiet", action="store_true")
    collect_parser.add_argument("--verbose", action="store_true")

    summary = sub.add_parser(
        "summary",
        help="Show stored telemetry: devices, frame counts and metric trends.",
    )
    summary.add_argument("--db", required=True)
    summary.add_argument("--device")
    summary.add_argument("--top", type=int, default=12)

    simulate = sub.add_parser(
        "simulate",
        help="Emit synthetic telemetry frames, for developing the host side without hardware.",
    )
    simulate.add_argument("--scenario", default="steady", choices=SCENARIOS)
    simulate.add_argument("--count", type=int, default=10)
    simulate.add_argument("--interval-ms", type=float, default=1000.0)
    simulate.add_argument("--device", default="esp32-light-monitor")
    simulate.add_argument("--firmware", default="sim0001")
    simulate.add_argument("--seed", type=int, default=1)

    check = sub.add_parser(
        "check",
        help="Assert resource budgets and compare against a stored baseline.",
    )
    check.add_argument("--db", required=True, help="path to the SQLite database")
    check.add_argument("--config", help="JSON file with threshold rules")
    check.add_argument("--baseline", help="baseline JSON (defaults to the one in the config)")
    check.add_argument("--save-baseline", help="store the current values as a baseline")
    check.add_argument("--top", type=int, default=12)
    check.add_argument("--json", action="store_true")

    report = sub.add_parser(
        "report",
        help="Write a self-contained HTML report of one capture.",
    )
    report.add_argument("--db", required=True, help="path to the SQLite database")
    report.add_argument("--config", required=True, help="JSON file with threshold rules")
    report.add_argument("--baseline", help="baseline JSON (defaults to the one in the config)")
    report.add_argument("--map", help="path to a .map file, for the build resource blocks")
    report.add_argument("--bin", dest="binary", help="path to the matching .bin file")
    report.add_argument("--partition", help="app partition size, e.g. 1500K")
    report.add_argument("--faults", help="directory of fault-*.db captures to summarise")
    report.add_argument(
        "--fault-config",
        help="budget used to re-check the fault captures "
             "(default: fault_matrix.json next to --config)",
    )
    report.add_argument("--device", help="device to report on (default: the busiest)")
    report.add_argument("--top", type=int, default=8, help="how many trend cards to draw")
    report.add_argument("--out", default="report.html", help="output file")

    diagnose = sub.add_parser(
        "diagnose",
        help="Assemble the evidence for an AI diagnosis (offline with --dry-run).",
    )
    diagnose.add_argument("--db", required=True, help="path to the SQLite database")
    diagnose.add_argument("--config", required=True, help="JSON file with threshold rules")
    diagnose.add_argument("--baseline", help="baseline JSON (defaults to the one in the config)")
    diagnose.add_argument("--map", help="path to a .map file, for the build summary")
    diagnose.add_argument("--bin", dest="binary", help="path to the matching .bin file")
    diagnose.add_argument("--partition", help="app partition size, e.g. 1500K")
    diagnose.add_argument("--project", help="firmware project directory: sdkconfig and git state")
    diagnose.add_argument("--device", help="device to report on (default: the busiest)")
    diagnose.add_argument("--max-samples", type=int, default=diagnose_mod.DEFAULT_MAX_SAMPLES,
                          help="samples kept per metric series")
    diagnose.add_argument("--max-bytes", type=int, default=diagnose_mod.DEFAULT_MAX_BYTES,
                          help="size budget for the rendered evidence pack")
    diagnose.add_argument("--dry-run", action="store_true",
                          help="print the evidence pack and call nothing")
    diagnose.add_argument("--out", help="write the model prompt (instructions + evidence) here")
    diagnose.add_argument("--json", action="store_true", help="emit the pack as JSON")

    audit = sub.add_parser(
        "audit",
        help="Check a model answer against an evidence pack: ids, names, citations.",
    )
    audit.add_argument("--answer", required=True, help="file containing the answer to check")
    audit.add_argument("--pack", required=True,
                       help="the prompt file written by diagnose --out")
    audit.add_argument("--json", action="store_true", help="emit the result as JSON")
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
            {"name": section.name, "address": section.address, "size": section.size}
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


def cmd_collect(args: argparse.Namespace) -> int:
    with Store(args.db) as store:
        try:
            stats = collect(args.source, store, limit=args.limit, verbose=args.verbose)
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not args.quiet:
            print(stats)
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.is_file():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2
    with Store(db) as store:
        print(render_summary(store, device=args.device, top=args.top))
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    for line in iter_frames(
        scenario=args.scenario,
        count=args.count,
        interval_ms=args.interval_ms,
        device=args.device,
        firmware=args.firmware,
        seed=args.seed,
    ):
        print(line)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.is_file():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2

    with Store(db) as store:
        if args.save_baseline:
            snapshot = checks_mod.save_baseline(store, args.save_baseline)
            print(
                f"baseline saved: {args.save_baseline} "
                f"({len(snapshot)} device(s))"
            )
            return 0

        if not args.config:
            print("error: --config is required unless --save-baseline is used", file=sys.stderr)
            return 2
        try:
            config = checks_mod.load_config(args.config)
        except checks_mod.ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        baseline_path = Path(args.baseline) if args.baseline else None
        if baseline_path is None:
            configured = config.get("baseline", {}).get("path")
            if configured:
                candidate = Path(configured)
                if not candidate.is_absolute():
                    candidate = Path(args.config).parent / candidate
                baseline_path = candidate

        baseline = None
        if baseline_path is not None:
            try:
                baseline = checks_mod.load_baseline(baseline_path)
            except checks_mod.ConfigError as exc:
                print(f"warning: {exc}", file=sys.stderr)
                baseline_path = None

        report = checks_mod.check_store(store, config, baseline, baseline_path)

    if args.json:
        print(json.dumps(checks_mod.to_dict(report), indent=2))
    else:
        print(checks_mod.render(report, top=args.top))
    return 0 if report.ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.is_file():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2
    try:
        config = checks_mod.load_config(args.config)
    except checks_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline) if args.baseline else None
    if baseline_path is None:
        configured = config.get("baseline", {}).get("path")
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = Path(args.config).parent / candidate
            baseline_path = candidate if candidate.is_file() else None

    build = None
    partition = None
    if args.map:
        if not _check_file(Path(args.map), "map file"):
            return 2
        binary = Path(args.binary) if args.binary else None
        build = build_report(Path(args.map), binary)
        partition = parse_size(args.partition) if args.partition else None

    baseline = checks_mod.load_baseline(baseline_path) if baseline_path else None

    with Store(db) as store:
        report = checks_mod.check_store(store, config, baseline, baseline_path)
        faults = None
        if args.faults:
            fault_config = config
            candidate = (Path(args.fault_config) if args.fault_config
                         else Path(args.config).parent / "fault_matrix.json")
            if candidate.is_file():
                fault_config = checks_mod.load_config(candidate)
            else:
                print(f"warning: {candidate} not found, reusing {args.config}",
                      file=sys.stderr)
            faults = gather_fault_cases(args.faults, fault_config)
        page = build_html(
            store,
            report,
            build=build,
            partition=partition,
            fault_cases=faults,
            device=args.device,
            top=args.top,
        )

    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    state = "PASS" if report.ok else "FAIL"
    print(f"{out} written ({len(page):,} bytes), checks {state}")
    return 0 if report.ok else 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Assemble the evidence. Calling a model is a separate, later step."""
    db = Path(args.db)
    if not db.is_file():
        print(f"error: database not found: {db}", file=sys.stderr)
        return 2
    try:
        config = checks_mod.load_config(args.config)
    except checks_mod.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline) if args.baseline else None
    if baseline_path is None:
        configured = config.get("baseline", {}).get("path")
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = Path(args.config).parent / candidate
            baseline_path = candidate if candidate.is_file() else None

    build = None
    partition = None
    if args.map:
        if not _check_file(Path(args.map), "map file"):
            return 2
        build = build_report(Path(args.map), Path(args.binary) if args.binary else None)
        partition = parse_size(args.partition) if args.partition else None

    baseline = checks_mod.load_baseline(baseline_path) if baseline_path else None

    with Store(db) as store:
        report = checks_mod.check_store(store, config, baseline, baseline_path)
        pack = diagnose_mod.gather_evidence(
            store, report, database=str(db), device=args.device, config_path=args.config,
            build=build, partition=partition, project_dir=args.project,
            max_samples=args.max_samples,
        )

    pack, text = diagnose_mod.fit_pack(pack, args.max_bytes)

    if not args.dry_run and not args.out:
        print("error: this build does not call a model. Use --out FILE to write the prompt "
              "and paste it into the model yourself, or --dry-run to read the evidence pack.",
              file=sys.stderr)
        return 2

    if args.out:
        prompt = diagnose_mod.build_prompt(text)
        Path(args.out).write_text(prompt, encoding="utf-8")
        print(f"prompt written: {args.out} ({len(prompt):,} chars)")
        print(f"next: paste it into a model, save the answer, then run")
        print(f"      mcu-insight audit --pack {args.out} --answer <answer file>")
        if not args.dry_run:
            return 0

    if args.json:
        print(json.dumps(pack, indent=2))
    else:
        print(text, end="")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Check the answer, not the model."""
    pack_path = Path(args.pack)
    answer_path = Path(args.answer)
    for path, label in ((pack_path, "pack"), (answer_path, "answer")):
        if not path.is_file():
            print(f"error: {label} file not found: {path}", file=sys.stderr)
            return 2

    result = diagnose_mod.audit_answer(
        answer_path.read_text(encoding="utf-8", errors="replace"),
        pack_path.read_text(encoding="utf-8", errors="replace"),
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(diagnose_mod.render_audit(result), end="")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {
        "analyze": cmd_analyze,
        "compare": cmd_compare,
        "model": cmd_model,
        "collect": cmd_collect,
        "summary": cmd_summary,
        "simulate": cmd_simulate,
        "check": cmd_check,
        "report": cmd_report,
        "diagnose": cmd_diagnose,
        "audit": cmd_audit,
    }.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())