from __future__ import annotations

from datetime import datetime

import pytest

from t0.regime import compute_regime


def test_regime_scenarios_1_4() -> None:
    ts = datetime(2026, 2, 23, 9, 26, 0)

    r = compute_regime(auction_vol_ratio=1.8, atr5_percentile=50.0, computed_at=ts)
    assert r.regime_active is True

    r = compute_regime(auction_vol_ratio=1.0, atr5_percentile=70.0, computed_at=ts)
    assert r.regime_active is True

    r = compute_regime(auction_vol_ratio=1.2, atr5_percentile=60.0, computed_at=ts)
    assert r.regime_active is False

    r1 = compute_regime(auction_vol_ratio=1.2, atr5_percentile=60.0, computed_at=ts)
    assert r1.regime_active is False

    with pytest.raises(AssertionError):
        _ = compute_regime(auction_vol_ratio=1.8, atr5_percentile=80.0, computed_at=datetime(2026, 2, 23, 10, 30, 0))
