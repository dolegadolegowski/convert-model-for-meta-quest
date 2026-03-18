"""Formatting helpers for worker status lines."""

from __future__ import annotations

from typing import Any


def build_geometry_summary(input_filename: str, report: dict[str, Any]) -> str:
    faces_before = report.get("faces_before")
    faces_final = report.get("faces_final")
    decimate_applied = bool(report.get("decimate", {}).get("applied", False))
    change_label = "decimate" if decimate_applied else "no-decimate"

    if faces_before is None or faces_final is None:
        return f"{input_filename}: unknown -> unknown ({change_label})"

    return f"{input_filename}: {faces_before} -> {faces_final} ({change_label})"
