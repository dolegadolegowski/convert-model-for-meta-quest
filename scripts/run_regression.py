#!/usr/bin/env python3
"""Regression runner for provided 3D assets."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
REPORT_DIR = PROJECT_ROOT / "reports"

DEFAULT_CASES = [
    "/Users/damiandd/Desktop/kosz.obj",
    "/Users/damiandd/Desktop/robot.obj",
    "/Users/damiandd/Desktop/HOL.obj",
    "/Users/damiandd/Desktop/puzle kosci/All.obj",
    "/Users/damiandd/Desktop/Modele studenci/szkielet/Group4.obj",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run integration regression suite")
    parser.add_argument("--blender-exec", default="/Applications/Blender.app/Contents/MacOS/Blender")
    parser.add_argument("--face-limit", type=int, default=300000)
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of model cases to run in parallel (default: 1)",
    )
    parser.add_argument(
        "--inputs",
        nargs="*",
        default=DEFAULT_CASES,
        help="Override list of test model input files",
    )
    return parser.parse_args()


def evaluate_checks(report: dict, input_path: Path, output_path: Path, face_limit: int, stdout: str) -> dict:
    faces_before = int(report.get("faces_before", 0) or 0)
    faces_final = int(report.get("faces_final", 0) or 0)
    decimate_applied = bool(report.get("decimate", {}).get("applied", False))
    mesh_count = int(report.get("mesh_count", 0) or 0)

    checks = {
        "import_succeeds": report.get("status") == "success" and bool(report.get("import", {}).get("success")),
        "empty_scene_before_import": int(report.get("import", {}).get("scene_object_count_before_import", -1)) == 0,
        "faces_read": faces_before > 0,
        "decimate_condition": (faces_before > face_limit) == decimate_applied,
        "face_limit_respected_if_needed": True if faces_before <= face_limit else faces_final < face_limit,
        "cleanup_no_crash": int(report.get("cleanup", {}).get("error_count", 1)) == 0,
        "export_succeeds": bool(report.get("export", {}).get("output_exists", False)) and output_path.exists(),
        "output_path_expected": str(output_path).startswith(str(OUTPUT_DIR.resolve())),
        "logs_readable": "[SUMMARY]" in stdout or report.get("status") == "success",
        "multi_object_predictable": True if mesh_count <= 1 else faces_final >= 0,
    }
    checks["all_passed"] = all(checks.values())
    return checks


def run_case(input_file: str, blender_exec: str, face_limit: int) -> dict:
    input_path = Path(input_file).expanduser().resolve()
    output_path = OUTPUT_DIR / f"{input_path.stem}_optimized.glb"
    report_path = REPORT_DIR / f"{input_path.stem}_report.json"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "optimize_model.py"),
        "--input",
        str(input_path),
        "--output-dir",
        str(OUTPUT_DIR),
        "--report-dir",
        str(REPORT_DIR),
        "--face-limit",
        str(face_limit),
        "--blender-exec",
        blender_exec,
        "--log-level",
        "INFO",
    ]

    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    duration = round(time.time() - start, 3)

    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {"status": "error", "error": "missing report"}

    checks = evaluate_checks(report, input_path, output_path, face_limit, proc.stdout)
    timings = report.get("timings", {})
    stage_timings = {
        "import_seconds": float(timings.get("import_seconds", 0.0) or 0.0),
        "cleanup_seconds": float(timings.get("cleanup_seconds", 0.0) or 0.0),
        "export_seconds": float(timings.get("export_seconds", 0.0) or 0.0),
        "total_seconds_reported": float(timings.get("total_seconds", 0.0) or 0.0),
    }
    stage_pairs = [
        ("import_seconds", stage_timings["import_seconds"]),
        ("cleanup_seconds", stage_timings["cleanup_seconds"]),
        ("export_seconds", stage_timings["export_seconds"]),
    ]
    bottleneck_stage, bottleneck_time = max(stage_pairs, key=lambda item: item[1])

    return {
        "input": str(input_path),
        "duration_seconds": duration,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
        "report": report,
        "checks": checks,
        "timings": stage_timings,
        "bottleneck_stage": bottleneck_stage,
        "bottleneck_seconds": round(float(bottleneck_time), 4),
    }


def build_performance_stats(cases: list[dict]) -> dict:
    if not cases:
        return {}

    total_values = [float(c["timings"].get("total_seconds_reported", 0.0) or 0.0) for c in cases]
    import_values = [float(c["timings"].get("import_seconds", 0.0) or 0.0) for c in cases]
    cleanup_values = [float(c["timings"].get("cleanup_seconds", 0.0) or 0.0) for c in cases]
    export_values = [float(c["timings"].get("export_seconds", 0.0) or 0.0) for c in cases]

    return {
        "average_total_seconds": round(statistics.mean(total_values), 4),
        "max_total_seconds": round(max(total_values), 4),
        "average_import_seconds": round(statistics.mean(import_values), 4),
        "average_cleanup_seconds": round(statistics.mean(cleanup_values), 4),
        "average_export_seconds": round(statistics.mean(export_values), 4),
    }


def write_markdown_summary(summary: dict, markdown_path: Path) -> None:
    lines = [
        "# Regression Summary",
        "",
        f"- overall_ok: `{summary.get('overall_ok')}`",
        f"- face_limit: `{summary.get('face_limit')}`",
        f"- blender_exec: `{summary.get('blender_exec')}`",
        "",
        "## Cases",
    ]

    for case in summary.get("cases", []):
        report = case.get("report", {})
        lines.extend(
            [
                f"- `{case['input']}`",
                f"  - returncode: `{case['returncode']}`",
                f"  - all_passed: `{case['checks'].get('all_passed')}`",
                f"  - faces_before: `{report.get('faces_before')}`",
                f"  - faces_final: `{report.get('faces_final')}`",
                f"  - bottleneck: `{case.get('bottleneck_stage')}` ({case.get('bottleneck_seconds')}s)",
            ]
        )

    perf = summary.get("performance", {})
    if perf:
        lines.extend(
            [
                "",
                "## Performance",
                "",
                f"- average_total_seconds: `{perf.get('average_total_seconds')}`",
                f"- max_total_seconds: `{perf.get('max_total_seconds')}`",
                f"- average_import_seconds: `{perf.get('average_import_seconds')}`",
                f"- average_cleanup_seconds: `{perf.get('average_cleanup_seconds')}`",
                f"- average_export_seconds: `{perf.get('average_export_seconds')}`",
            ]
        )

    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    summary = {
        "face_limit": args.face_limit,
        "blender_exec": args.blender_exec,
        "cases": [],
    }

    jobs = max(1, int(args.jobs))

    if jobs == 1:
        cases = [
            run_case(input_file=input_file, blender_exec=args.blender_exec, face_limit=args.face_limit)
            for input_file in args.inputs
        ]
    else:
        cases = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(run_case, input_file, args.blender_exec, args.face_limit)
                for input_file in args.inputs
            ]
            for future in concurrent.futures.as_completed(futures):
                cases.append(future.result())
        cases.sort(key=lambda c: c["input"])

    summary["cases"] = cases
    overall_ok = all(c["returncode"] == 0 and c["checks"]["all_passed"] for c in cases)

    summary["overall_ok"] = overall_ok
    summary["performance"] = build_performance_stats(summary["cases"])

    out_path = REPORT_DIR / "regression_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    write_markdown_summary(summary, REPORT_DIR / "regression_summary.md")

    print(json.dumps({
        "overall_ok": overall_ok,
        "summary_path": str(out_path),
        "cases": [
            {
                "input": c["input"],
                "returncode": c["returncode"],
                "all_passed": c["checks"]["all_passed"],
                "faces_before": c["report"].get("faces_before"),
                "faces_final": c["report"].get("faces_final"),
                "decimate_applied": c["report"].get("decimate", {}).get("applied"),
                "duration_seconds": c["duration_seconds"],
            }
            for c in summary["cases"]
        ],
    }, indent=2, ensure_ascii=True))

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
