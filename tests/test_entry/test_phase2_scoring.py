from __future__ import annotations

from entry.scoring import compute_entry_score
from entry.phase2 import compute_phase2_signals
from entry.types import WatchlistItem


def test_phase2_scoring_scenarios_1_7() -> None:
    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 1, "S_chip_pr": 0, "S_trend": 0, "S_micro": 0}
    )
    assert score == 0.55
    assert trig is True
    assert strong is False


def test_phase2_s_micro_missing_override() -> None:
    w_missing = WatchlistItem(etf_code="512480.SH", sentiment_score=50, profit_ratio=0.0)
    sig0 = compute_phase2_signals(bars=[], watch=w_missing)
    assert sig0["S_micro"] == 0.0

    sig1 = compute_phase2_signals(bars=[], watch=w_missing, s_micro_missing=0.1)
    assert sig1["S_micro"] == 0.1

    w_present = WatchlistItem(etf_code="512480.SH", sentiment_score=50, profit_ratio=0.0, ofi_daily=1.0, vpin_rank=0.5, vs_max=2.0)
    sig2 = compute_phase2_signals(bars=[], watch=w_present, s_micro_missing=0.1)
    assert sig2["S_micro"] == 1.0

    score, trig, strong = compute_entry_score(
        {"S_squeeze": 1, "S_volume": 0, "S_chip_pr": 0, "S_trend": 1, "S_micro": 0}
    )
    assert score == 0.45
    assert trig is True
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
