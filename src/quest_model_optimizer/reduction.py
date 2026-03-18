"""Geometry reduction math utilities (pure Python)."""

from __future__ import annotations


def clamp_ratio(value: float, min_ratio: float = 0.02, max_ratio: float = 1.0) -> float:
    return max(min_ratio, min(max_ratio, value))


def should_decimate(total_faces: int, face_limit: int) -> bool:
    return total_faces > face_limit


def compute_initial_ratio(total_faces: int, face_limit: int, safety: float = 0.995) -> float:
    if total_faces <= 0:
        return 1.0
    target = (face_limit * safety) / total_faces
    return clamp_ratio(target, min_ratio=0.02, max_ratio=1.0)


def compute_correction_ratio(current_faces: int, face_limit: int, safety: float = 0.99) -> float:
    if current_faces <= 0:
        return 1.0
    target = (face_limit * safety) / current_faces
    return clamp_ratio(target, min_ratio=0.05, max_ratio=0.999)
