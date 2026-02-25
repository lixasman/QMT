from __future__ import annotations

from entry.scoring import compute_entry_score


def test_phase2_scoring_scenarios_1_7() -> None:
    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 1, "S_chip_pr": 0, "S_trend": 0, "S_micro": 0}
    )
    assert score == 0.55
    assert trig is True
    assert strong is False

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 0, "S_chip_pr": 0, "S_trend": 1, "S_micro": 0}
    )
    assert score == 0.45
    assert trig is False
    assert strong is False

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 0, "S_chip_pr": 0.8, "S_trend": 0, "S_micro": 0}
    )
    assert score == 0.46
    assert trig is True
    assert strong is False

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 0, "S_chip_pr": 0.5, "S_trend": 0, "S_micro": 0}
    )
    assert score == 0.40
    assert trig is False
    assert strong is False

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 1, "S_chip_pr": 1.0, "S_trend": 1, "S_micro": 1.0}
    )
    assert score == 1.00
    assert trig is True
    assert strong is True

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 0, "S_chip_pr": 0.75, "S_trend": 1, "S_micro": 0}
    )
    assert score == 0.60
    assert trig is True
    assert strong is False

