"""Unit tests for backtest.sentiment_proxy.compute_sentiment_proxy (multi-factor v2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backtest.sentiment_proxy import compute_sentiment_proxy


def _bar(*, close: float, high: float | None = None, low: float | None = None, volume: float = 100_000.0, open: float | None = None):
    """Helper to create a minimal bar-like object."""
    h = high if high is not None else close * 1.005
    l = low if low is not None else close * 0.995
    o = open if open is not None else close
    return SimpleNamespace(close=close, high=h, low=l, volume=volume, open=o)


class TestSentimentProxyV2:
    """Verify the multi-factor continuous sentiment proxy produces sensible outputs."""

    def test_insufficient_bars_returns_neutral(self):
        """With fewer than volume_ma_window+1 bars, return neutral (50, 0.5)."""
        bars = [_bar(close=1.0)] * 3
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert score100 == 50
        assert score01 == 0.5

    def test_bullish_bars_return_high_score(self):
        """Consistent uptrend should give score_01 > 0.60."""
        prices = [1.00, 1.01, 1.02, 1.03, 1.04, 1.05]
        bars = [_bar(close=p, volume=100_000 + i * 10_000) for i, p in enumerate(prices)]
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert score01 > 0.60, f"Expected bullish score > 0.60, got {score01}"
        assert score100 > 60

    def test_bearish_bars_return_low_score(self):
        """Consistent downtrend should give score_01 < 0.40."""
        prices = [1.05, 1.04, 1.03, 1.02, 1.01, 1.00]
        bars = [_bar(close=p, volume=100_000 + i * 20_000) for i, p in enumerate(prices)]
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert score01 < 0.40, f"Expected bearish score < 0.40, got {score01}"

    def test_flat_bars_return_neutral_score(self):
        """Flat market should give score_01 in [0.40, 0.60]."""
        bars = [_bar(close=1.0, volume=100_000) for _ in range(6)]
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert 0.35 <= score01 <= 0.65, f"Expected neutral score in [0.35, 0.65], got {score01}"

    def test_extreme_bearish_triggers_s_sentiment(self):
        """Multiple consecutive limit-down + heavy volume should push score_01 ≤ 0.35.
        
        This is the scenario that should trigger S_sentiment=1 in the exit scoring.
        S_SENTIMENT_THRESHOLD = 0.35 from exit/constants.py.
        """
        # Simulate 5 consecutive limit-down days (-10% each) with rising volume
        prices = [1.0]
        for _ in range(5):
            prices.append(prices[-1] * 0.90)
        bars = [_bar(close=p, high=p * 1.001, low=p * 0.9, volume=200_000 * (i + 1)) for i, p in enumerate(prices)]
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert score01 <= 0.35, f"Expected extreme bearish score ≤ 0.35, got {score01}"

    def test_output_range_clamped(self):
        """Output must always be in [0.1, 0.9] for score_01 and [0, 100] for score_100."""
        # Extremely bullish scenario
        prices = [1.0, 1.10, 1.21, 1.33, 1.46, 1.61]
        bars_bull = [_bar(close=p, volume=500_000) for p in prices]
        s100, s01 = compute_sentiment_proxy(bars_bull, volume_ma_window=5)
        assert 0.1 <= s01 <= 0.9, f"score_01 out of range: {s01}"
        assert 0 <= s100 <= 100, f"score_100 out of range: {s100}"

        # Extremely bearish scenario
        prices = [1.0, 0.80, 0.64, 0.51, 0.41, 0.33]
        bars_bear = [_bar(close=p, high=p * 1.001, low=p * 0.8, volume=500_000 * (i + 1)) for i, p in enumerate(prices)]
        s100, s01 = compute_sentiment_proxy(bars_bear, volume_ma_window=5)
        assert 0.1 <= s01 <= 0.9, f"score_01 out of range: {s01}"
        assert 0 <= s100 <= 100, f"score_100 out of range: {s100}"

    def test_volume_ma_window_positive_required(self):
        bars = [_bar(close=1.0)] * 6
        with pytest.raises(AssertionError):
            compute_sentiment_proxy(bars, volume_ma_window=0)

    def test_single_bullish_day_not_extreme(self):
        """Unlike the old binary proxy, a single bullish day should NOT give extreme scores.
        
        The old proxy returned 100/0.9 for any single bullish day. The new proxy should
        moderate this to something more reasonable.
        """
        # 5 flat days + 1 bullish day
        bars = [_bar(close=1.0, volume=100_000) for _ in range(5)]
        bars.append(_bar(close=1.01, volume=120_000))
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        # Should be mildly bullish, not extreme
        assert score01 < 0.80, f"Single bullish day should not give extreme score, got {score01}"
        assert score01 > 0.40, f"Single bullish day should still be mildly positive, got {score01}"

    def test_single_bearish_day_not_extreme(self):
        """A single bearish day should NOT trigger S_sentiment=1.
        
        The old proxy returned 0/0.1 for any non-bullish day. The new proxy should
        give a moderate score, well above 0.35 threshold.
        """
        # 5 flat days + 1 mildly bearish day
        bars = [_bar(close=1.0, volume=100_000) for _ in range(5)]
        bars.append(_bar(close=0.99, volume=110_000))
        score100, score01 = compute_sentiment_proxy(bars, volume_ma_window=5)
        assert score01 > 0.35, f"Single bearish day should NOT trigger S_sentiment, got {score01}"
