"""Command-line interface for the model conversion pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .logging_utils import configure_logging
from .paths import ensure_dir, output_filename_for
from .runner import detect_blender_executable, run_blender_pipeline
from .version import read_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import 3D model in Blender, cap faces, clean geometry, export GLB.",
    )
    parser.add_argument("--input", required=True, help="Input model path")
    parser.add_argument("--output", help="Explicit output .glb path")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory for generated GLB when --output is not provided",
    )
    parser.add_argument(
        "--report-dir",
        default="reports",
        help="Directory for JSON execution report",
    )
    parser.add_argument("--face-limit", type=int, default=300000)
    parser.add_argument("--blender-exec", help="Path to Blender executable")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print final report JSON summary on stdout",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print tool version and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(read_version())
        return 0

    logger = configure_logging(args.log_level)

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        logger.error("Input file does not exist: %s", input_path)
        return 2

    output_dir = ensure_dir(Path(args.output_dir).expanduser().resolve())
    report_dir = ensure_dir(Path(args.report_dir).expanduser().resolve())

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_path = output_dir / output_filename_for(input_path)

    report_path = report_dir / f"{input_path.stem}_report.json"
    blender_exec = detect_blender_executable(args.blender_exec)

    logger.info("Version: %s", read_version())
    logger.info("Input: %s", input_path)
    logger.info("Output: %s", output_path)
    logger.info("Report: %s", report_path)
    logger.info("Face limit: %s", args.face_limit)
    logger.info("Blender executable: %s", blender_exec)

    result = run_blender_pipeline(
        input_path=input_path,
        output_path=output_path,
        report_path=report_path,
        face_limit=args.face_limit,
        blender_exec=blender_exec,
        log_level=args.log_level,
    )

    report = result.get("report", {})
    if result["returncode"] != 0:
        logger.error("Blender process failed with code %s", result["returncode"])
        if result.get("stderr"):
            logger.error("Blender stderr:\n%s", result["stderr"].strip())
        if report:
            logger.error("Partial report: %s", json.dumps(report, ensure_ascii=True))
        return 1

    logger.info(
        "Completed. faces_before=%s faces_final=%s decimate_applied=%s",
        report.get("faces_before"),
        report.get("faces_final"),
        report.get("decimate", {}).get("applied"),
    )

    if args.print_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
