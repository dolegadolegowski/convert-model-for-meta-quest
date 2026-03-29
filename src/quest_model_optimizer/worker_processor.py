"""Adapter that runs existing Blender pipeline for worker jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .runner import detect_blender_executable, run_blender_pipeline
from .worker_models import ProcessingOutcome


@dataclass
class PipelineOptions:
    face_limit: int = 400000
    log_level: str = "INFO"
    max_decimate_passes: int = 4
    initial_target_safety: float = 0.995
    correction_target_safety: float = 0.99
    cleanup_merge_distance: float = 1e-6
    cleanup_degenerate_distance: float = 1e-8
    min_object_faces_for_decimate: int = 1500
    cleanup_skip_normal_recalc_above_faces: int = 500000
    blender_timeout_seconds: int = 1800
    fail_if_over_limit: bool = True


class PipelineProcessor:
    """Runs the current Remote3Dworker pipeline as-is."""

    def __init__(
        self,
        blender_exec: str | None = None,
        options: PipelineOptions | None = None,
    ) -> None:
        self.blender_exec = detect_blender_executable(blender_exec)
        self.options = options or PipelineOptions()

    def with_option_overrides(self, **overrides: object) -> "PipelineProcessor":
        return PipelineProcessor(
            blender_exec=self.blender_exec,
            options=replace(self.options, **overrides),
        )

    def process(self, input_path: Path, output_path: Path, report_path: Path) -> ProcessingOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        result = run_blender_pipeline(
            input_path=input_path,
            output_path=output_path,
            report_path=report_path,
            face_limit=self.options.face_limit,
            blender_exec=self.blender_exec,
            log_level=self.options.log_level,
            max_decimate_passes=self.options.max_decimate_passes,
            initial_target_safety=self.options.initial_target_safety,
            correction_target_safety=self.options.correction_target_safety,
            cleanup_merge_distance=self.options.cleanup_merge_distance,
            cleanup_degenerate_distance=self.options.cleanup_degenerate_distance,
            min_object_faces_for_decimate=self.options.min_object_faces_for_decimate,
            cleanup_skip_normal_recalc_above_faces=self.options.cleanup_skip_normal_recalc_above_faces,
            blender_timeout_seconds=self.options.blender_timeout_seconds,
            fail_if_over_limit=self.options.fail_if_over_limit,
        )

        report = result.get("report") or {}
        if not report and report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                report = {}

        success = result.get("returncode", 1) == 0 and output_path.exists()
        error = None
        if not success:
            error = report.get("error") if isinstance(report, dict) else None
            if not error:
                error = f"pipeline failed with return code {result.get('returncode')}"

        return ProcessingOutcome(
            success=bool(success),
            output_path=output_path,
            report_path=report_path,
            report=report if isinstance(report, dict) else {},
            returncode=int(result.get("returncode", 1)),
            error=error,
        )
