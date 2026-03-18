from quest_model_optimizer.reduction import (
    compute_correction_ratio,
    compute_initial_ratio,
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
