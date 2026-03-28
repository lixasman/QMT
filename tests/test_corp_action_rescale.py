from __future__ import annotations

import numpy as np

from etf_chip_engine.models import ChipDistribution
from stock_chip_engine.modules.corp_actions import rescale_chip_distribution


def test_rescale_chip_distribution_mass_preserving_and_peak_shift() -> None:
    chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.0, bucket_size=0.01)
    chips.ensure_range(0.0, 20.0, padding_buckets=0)
    chips.total_shares = 1_000_000.0

    idx = chips.price_to_index(10.0)
    chips.chips[:] = 0.0
    chips.chips[idx] = np.float32(12345.0)
    total_before = float(chips.chips.sum())

    out = rescale_chip_distribution(chips, price_factor=0.5, new_bucket_size=0.01)
    total_after = float(out.chips.sum())

    assert abs(total_after - total_before) / max(total_before, 1e-12) < 1e-6

    peak_idx = int(np.argmax(out.chips))
    peak_price = float(out.index_to_price(peak_idx))
    assert abs(peak_price - 5.0) <= 0.02  # within 2 buckets

