from __future__ import annotations

from .types import ScaleSignalConditions
from .types import ScalePrereqItem
from .constants import (
    SCALE_CHIP_DENSITY_TOP_PCT,
    SCALE_CHIP_TOUCH_ATR14_BAND,
    SCALE_MICRO_VOL_SHRINK_THRESHOLD,
    SCALE_PULLBACK_ATR14_MULT,
)


def evaluate_scale_signal_conditions(
    *,
    kama_rising_days: int,
    elder_impulse_green: bool,
    pullback_atr14_multiple: float,
    above_chandelier_stop: bool,
    chip_density_rank: float,
    chip_touch_distance_atr14: float,
    micro_vol_ratio: float,
    micro_support_held: bool,
    micro_bullish_close: bool,
) -> ScaleSignalConditions:
    trend_ok = (int(kama_rising_days) >= 2) and bool(elder_impulse_green)
    pullback_ok = (float(pullback_atr14_multiple) >= float(SCALE_PULLBACK_ATR14_MULT)) and bool(above_chandelier_stop)
    chip_ok = (float(chip_density_rank) >= (1.0 - float(SCALE_CHIP_DENSITY_TOP_PCT))) and (
        float(chip_touch_distance_atr14) <= float(SCALE_CHIP_TOUCH_ATR14_BAND)
    )
    micro_ok = (float(micro_vol_ratio) <= float(SCALE_MICRO_VOL_SHRINK_THRESHOLD)) and bool(micro_support_held) and bool(
        micro_bullish_close
    )

    items = {
        "1_trend": ScalePrereqItem(passed=bool(trend_ok), value={"kama_rising_days": int(kama_rising_days), "impulse_green": bool(elder_impulse_green)}),
        "2_pullback": ScalePrereqItem(
            passed=bool(pullback_ok),
            value={"pullback_atr": float(pullback_atr14_multiple), "above_chandelier": bool(above_chandelier_stop)},
            threshold={"atr_mult": float(SCALE_PULLBACK_ATR14_MULT)},
        ),
        "3_chip_support": ScalePrereqItem(
            passed=bool(chip_ok),
            value={"density_rank": float(chip_density_rank), "distance_atr": float(chip_touch_distance_atr14)},
            threshold={"density_min_rank": round(1.0 - float(SCALE_CHIP_DENSITY_TOP_PCT), 4), "distance_band": float(SCALE_CHIP_TOUCH_ATR14_BAND)},
        ),
        "4_micro_confirm": ScalePrereqItem(
            passed=bool(micro_ok),
            value={"vol_ratio": float(micro_vol_ratio), "support_held": bool(micro_support_held), "bullish_close": bool(micro_bullish_close)},
            threshold={"vol_ratio_max": float(SCALE_MICRO_VOL_SHRINK_THRESHOLD)},
        ),
    }
    passed = all(bool(v.passed) for v in items.values())
    return ScaleSignalConditions(passed=bool(passed), items=items)
