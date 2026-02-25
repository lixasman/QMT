from __future__ import annotations

from ..constants import S_SENTIMENT_THRESHOLD


def compute_s_sentiment(sentiment_score: float) -> float:
    s = float(sentiment_score)
    return 1.0 if s <= float(S_SENTIMENT_THRESHOLD) else 0.0

