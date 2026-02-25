from __future__ import annotations

from t0.kde_support import find_nearest_support
from t0.types import DenseZone


def test_find_nearest_support_selects_upper_below_price() -> None:
    zones = [
        DenseZone(upper=1.050, lower=1.045, strength=0.8),
        DenseZone(upper=1.030, lower=1.020, strength=0.9),
        DenseZone(upper=1.060, lower=1.055, strength=0.7),
    ]
    s = find_nearest_support(zones=zones, price=1.055)
    assert s == 1.05

    s = find_nearest_support(zones=zones, price=1.000)
    assert s is None

