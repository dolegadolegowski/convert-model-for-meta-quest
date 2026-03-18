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


def compute_object_ratio_map(
    face_counts: dict[str, int],
    target_total_faces: int,
    min_object_faces_for_decimate: int = 1500,
    safety: float = 0.995,
) -> dict[str, float]:
    if not face_counts:
        return {}

    fixed_faces = 0
    decimatable_faces = 0
    for faces in face_counts.values():
        if faces < min_object_faces_for_decimate:
            fixed_faces += faces
        else:
            decimatable_faces += faces

    if decimatable_faces <= 0:
        return {name: 1.0 for name in face_counts}

    remaining_budget = max(0, target_total_faces - fixed_faces)
    shared_ratio = clamp_ratio(
        (remaining_budget / decimatable_faces) * safety,
        min_ratio=0.02,
        max_ratio=1.0,
    )

    ratio_map = {}
    for name, faces in face_counts.items():
        if faces < min_object_faces_for_decimate:
            ratio_map[name] = 1.0
        else:
            ratio_map[name] = shared_ratio

    return ratio_map
