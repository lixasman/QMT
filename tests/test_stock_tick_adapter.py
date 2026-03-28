from __future__ import annotations

import pandas as pd

from stock_chip_engine.data.tick_adapter import ticks_to_snapshots


def test_tick_adapter_keeps_first_cum_increment() -> None:
    # The first tick can already contain non-zero cumulative amount/volume
    # (e.g., call auction / first trade). The adapter should not drop it.
    ticks = pd.DataFrame(
        {
            "time": [93000, 93003, 93006],
            "lastPrice": [10.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.0],
            "low": [10.0, 10.0, 10.0],
            # cumulative
            "amount": [5000.0, 7000.0, 9000.0],
            "volume": [5.0, 7.0, 9.0],
        }
    )

    snapshots = ticks_to_snapshots(ticks, lot_size=100.0, volume_in_lots=True)
    assert snapshots is not None
    assert len(snapshots) == 3

    # First row keeps cum increment, and later rows are diffs.
    assert float(snapshots["amount"].iloc[0]) == 5000.0
    assert float(snapshots["volume"].iloc[0]) == 5.0 * 100.0
    assert float(snapshots["amount"].iloc[1]) == 2000.0
    assert float(snapshots["volume"].iloc[1]) == 2.0 * 100.0

