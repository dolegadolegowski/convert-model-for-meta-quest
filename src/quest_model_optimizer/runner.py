"""External Blender process runner."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def detect_blender_executable(explicit_path: str | None = None) -> str:
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    env_path = os.getenv("BLENDER_EXECUTABLE")
    if env_path:
        candidates.append(env_path)
    which_path = shutil.which("blender")
    if which_path:
        candidates.append(which_path)
    if platform.system() == "Darwin":
        candidates.append("/Applications/Blender.app/Contents/MacOS/Blender")
    candidates.append("blender")

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    # Final fallback, may still resolve from PATH at runtime.
    return candidates[-1]


def run_blender_pipeline(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    face_limit: int,
    blender_exec: str,
    log_level: str,
    max_decimate_passes: int,
    initial_target_safety: float,
    correction_target_safety: float,
    cleanup_merge_distance: float,
    cleanup_degenerate_distance: float,
    min_object_faces_for_decimate: int,
    cleanup_skip_normal_recalc_above_faces: int,
) -> dict[str, Any]:
    worker_script = Path(__file__).resolve().parent / "blender_worker.py"
    cmd = [
        blender_exec,
        "--background",
        "--factory-startup",
        "--python",
        str(worker_script),
        "--",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--report",
        str(report_path),
        "--face-limit",
        str(face_limit),
        "--log-level",
        log_level.upper(),
        "--max-decimate-passes",
        str(max_decimate_passes),
        "--initial-target-safety",
        str(initial_target_safety),
        "--correction-target-safety",
        str(correction_target_safety),
        "--cleanup-merge-distance",
        str(cleanup_merge_distance),
        "--cleanup-degenerate-distance",
        str(cleanup_degenerate_distance),
        "--min-object-faces-for-decimate",
        str(min_object_faces_for_decimate),
        "--cleanup-skip-normal-recalc-above-faces",
        str(cleanup_skip_normal_recalc_above_faces),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)

    result: dict[str, Any] = {
        "command": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "report_path": str(report_path),
    }

    if report_path.exists():
        try:
            result["report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result["report_parse_error"] = "Failed to parse report JSON"

    return result
