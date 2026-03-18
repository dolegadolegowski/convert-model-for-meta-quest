from quest_model_optimizer.reduction import (
    compute_correction_ratio,
    compute_initial_ratio,
    compute_object_ratio_map,
    should_decimate,
)


def test_should_decimate():
    assert not should_decimate(300000, 300000)
    assert should_decimate(300001, 300000)


def test_initial_ratio():
    ratio = compute_initial_ratio(600000, 300000)
    assert 0.4 < ratio < 0.6


def test_correction_ratio():
    ratio = compute_correction_ratio(330000, 300000)
    assert 0.8 < ratio < 1.0


def test_object_ratio_map_preserves_small_objects():
    ratio_map = compute_object_ratio_map(
        face_counts={"small": 100, "large": 10000},
        target_total_faces=5000,
        min_object_faces_for_decimate=1000,
    )
    assert ratio_map["small"] == 1.0
    assert 0.1 < ratio_map["large"] < 1.0
