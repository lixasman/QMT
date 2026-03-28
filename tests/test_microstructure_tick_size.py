from __future__ import annotations

import numpy as np
import pandas as pd

from etf_chip_engine.microstructure.bvc import BulkVolumeClassifier
from etf_chip_engine.microstructure.ofi import ContStoikovOFI


def test_bvc_tick_size_level2_gate() -> None:
    n = 20
    close = np.full(n, 10.0, dtype=np.float64)  # flat close -> forces L2/L3 paths
    volume = np.full(n, 100.0, dtype=np.float64)
    df = pd.DataFrame({"close": close, "volume": volume})

    # Microprice drifts by 0.005 which is:
    # - > 0.002 (ETF default 2*tick=0.001) => Level 2 triggers
    # - < 0.02  (stock 2*tick=0.01)        => Level 2 should NOT trigger
    microprice = 10.0 + np.arange(n, dtype=np.float64) * 0.005

    bvc = BulkVolumeClassifier()
    res_default = bvc.classify(df, microprice=microprice)
    res_stock = bvc.classify(df, microprice=microprice, tick_size=0.01)

    assert res_default.level_counts.get("L2", 0) > 0
    assert res_stock.level_counts.get("L2", 0) == 0


def test_ofi_tick_size_integerize_effect() -> None:
    # Create a tiny 0.001 price wobble: it should be treated as "no tick move"
    # under tick_size=0.01, but as a tick move under tick_size=0.001.
    df = pd.DataFrame(
        {
            "bid1": [10.000, 10.001, 10.000],
            # Keep ask side unchanged so MM symmetric filter does not zero OFI.
            "ask1": [10.010, 10.010, 10.010],
            "bid1_vol": [100.0, 100.0, 100.0],
            "ask1_vol": [100.0, 100.0, 100.0],
            "close": [10.005, 10.006, 10.005],
        }
    )

    ofi = ContStoikovOFI()
    res_default = ofi.compute(df)  # default tick_size=0.001
    res_stock = ofi.compute(df, tick_size=0.01)

    # Compare the t=1 contribution; should differ due to integerization granularity.
    v0 = float(res_default.ofi_series[1])
    v1 = float(res_stock.ofi_series[1])
    assert v0 != v1
