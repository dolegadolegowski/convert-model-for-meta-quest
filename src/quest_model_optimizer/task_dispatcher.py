"""Task parsing and dispatch for remote worker claims."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .worker_models import JobClaim, JobTask, ProcessingOutcome

_TASK_TYPE_KEYS = ("task_type", "job_type", "type")
_TASK_PARAMS_KEYS = ("task_params", "job_params", "params", "task_options", "job_options")
_FACE_LIMIT_KEYS = (
    "face_limit",
    "target_triangles",
    "max_triangles",
    "triangle_limit",
    "triangle_target",
    "target_faces",
    "max_faces",
    "face_target",
)


def _coerce_task_type(value: Any) -> str:
    normalized = str(value or "convert").strip().lower().replace("-", "_")
    if normalized in {"", "default", "cleanup", "cleanup_glb", "convert_to_glb", "optimize"}:
        return "convert"
    if normalized in {"reduce", "reduce_size", "decimate", "decimation"}:
        return "reduce_size"
    return normalized


def _merge_dict(target: dict[str, Any], candidate: Any) -> None:
    if isinstance(candidate, dict):
        for key, value in candidate.items():
            target[str(key)] = value


def extract_job_task(payload: dict[str, Any] | None) -> JobTask:
    if not isinstance(payload, dict):
        return JobTask()

    task_type: str | None = None
    params: dict[str, Any] = {}
    nested_task = payload.get("task")

    if isinstance(nested_task, dict):
        for key in _TASK_TYPE_KEYS:
            if nested_task.get(key):
                task_type = _coerce_task_type(nested_task.get(key))
                break
        for key in _TASK_PARAMS_KEYS:
            _merge_dict(params, nested_task.get(key))

    if task_type is None:
        for key in _TASK_TYPE_KEYS:
            if payload.get(key):
                task_type = _coerce_task_type(payload.get(key))
                break

    for key in _TASK_PARAMS_KEYS:
        _merge_dict(params, payload.get(key))

    if _coerce_task_type(task_type) == "reduce_size":
        for key in _FACE_LIMIT_KEYS:
            if key in payload and key not in params:
                params[key] = payload.get(key)

    return JobTask(task_type=_coerce_task_type(task_type), params=params)


def resolve_reduce_size_face_limit(params: dict[str, Any], default_face_limit: int = 400000) -> int:
    for key in _FACE_LIMIT_KEYS:
        if key not in params or params.get(key) in (None, ""):
            continue
        try:
            requested = int(params[key])
        except (TypeError, ValueError):
            continue
        if requested <= 0:
            continue
        return max(1000, min(requested, int(default_face_limit)))
    return max(1000, int(default_face_limit))


def dispatch_claim_processing(
    processor: Any,
    claim: JobClaim,
    input_path: Path,
    output_path: Path,
    report_path: Path,
) -> ProcessingOutcome:
    if hasattr(processor, "process_claim"):
        return processor.process_claim(claim, input_path, output_path, report_path)

    task = extract_job_task(claim.payload)
    if task.task_type == "convert":
        return processor.process(input_path, output_path, report_path)

    if task.task_type == "reduce_size":
        if hasattr(processor, "with_option_overrides"):
            default_face_limit = getattr(getattr(processor, "options", None), "face_limit", 400000)
            face_limit = resolve_reduce_size_face_limit(task.params, default_face_limit=default_face_limit)
            specialized = processor.with_option_overrides(face_limit=face_limit, fail_if_over_limit=True)
            return specialized.process(input_path, output_path, report_path)
        return processor.process(input_path, output_path, report_path)

    raise ValueError(f"unsupported task_type '{task.task_type}'")
