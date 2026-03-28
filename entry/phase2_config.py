from __future__ import annotations

from .constants import SCORE_THRESHOLD

_PHASE2_SCORE_THRESHOLD = float(SCORE_THRESHOLD)
_PHASE2_CONTINUATION_ENABLED = False
_PHASE2_CONTINUATION_CHIP_MIN = 0.60
_PHASE2_CONTINUATION_MICRO_MIN = 0.40
_PHASE2_CONTINUATION_LOOKBACK = 10
_PHASE2_CONTINUATION_EXPIRE_DAYS = 1
_PHASE2_CONTINUATION_MIN_CLOSE_BREAKOUT_PCT = 0.0
_PHASE2_CONTINUATION_MATURE_BLOCK_ENABLED = False
_PHASE2_CONTINUATION_MATURE_LEG_DAYS = 5
_PHASE2_CONTINUATION_MATURE_BIAS_ATR = 2.0
_PHASE2_CONTINUATION_MATURE_NEAR_HIGH_ATR = 0.5
_PHASE2_CONTINUATION_MATURE_PULLBACK_LOOKBACK = 4
_PHASE2_CONTINUATION_MATURE_MIN_PULLBACK_BIAS = 0.2


def set_phase2_score_threshold(v: float) -> None:
    global _PHASE2_SCORE_THRESHOLD
    _PHASE2_SCORE_THRESHOLD = float(v)


def get_phase2_score_threshold() -> float:
    return float(_PHASE2_SCORE_THRESHOLD)


def set_phase2_continuation_config(
    *,
    enabled: bool,
    chip_min: float | None = None,
    micro_min: float | None = None,
    lookback: int | None = None,
    expire_days: int | None = None,
    min_close_breakout_pct: float | None = None,
    mature_block_enabled: bool | None = None,
    mature_leg_days: int | None = None,
    mature_bias_atr: float | None = None,
    mature_near_high_atr: float | None = None,
    mature_pullback_lookback: int | None = None,
    mature_min_pullback_bias: float | None = None,
) -> None:
    global _PHASE2_CONTINUATION_ENABLED
    global _PHASE2_CONTINUATION_CHIP_MIN
    global _PHASE2_CONTINUATION_MICRO_MIN
    global _PHASE2_CONTINUATION_LOOKBACK
    global _PHASE2_CONTINUATION_EXPIRE_DAYS
    global _PHASE2_CONTINUATION_MIN_CLOSE_BREAKOUT_PCT
    global _PHASE2_CONTINUATION_MATURE_BLOCK_ENABLED
    global _PHASE2_CONTINUATION_MATURE_LEG_DAYS
    global _PHASE2_CONTINUATION_MATURE_BIAS_ATR
    global _PHASE2_CONTINUATION_MATURE_NEAR_HIGH_ATR
    global _PHASE2_CONTINUATION_MATURE_PULLBACK_LOOKBACK
    global _PHASE2_CONTINUATION_MATURE_MIN_PULLBACK_BIAS
    _PHASE2_CONTINUATION_ENABLED = bool(enabled)
    if chip_min is not None:
        _PHASE2_CONTINUATION_CHIP_MIN = float(max(0.0, min(1.0, float(chip_min))))
    if micro_min is not None:
        _PHASE2_CONTINUATION_MICRO_MIN = float(max(0.0, min(1.0, float(micro_min))))
    if lookback is not None:
        _PHASE2_CONTINUATION_LOOKBACK = int(max(2, int(lookback)))
    if expire_days is not None:
        _PHASE2_CONTINUATION_EXPIRE_DAYS = int(max(1, int(expire_days)))
    if min_close_breakout_pct is not None:
        _PHASE2_CONTINUATION_MIN_CLOSE_BREAKOUT_PCT = float(max(0.0, min(1.0, float(min_close_breakout_pct))))
    if mature_block_enabled is not None:
        _PHASE2_CONTINUATION_MATURE_BLOCK_ENABLED = bool(mature_block_enabled)
    if mature_leg_days is not None:
        _PHASE2_CONTINUATION_MATURE_LEG_DAYS = int(max(1, int(mature_leg_days)))
    if mature_bias_atr is not None:
        _PHASE2_CONTINUATION_MATURE_BIAS_ATR = float(max(0.0, float(mature_bias_atr)))
    if mature_near_high_atr is not None:
        _PHASE2_CONTINUATION_MATURE_NEAR_HIGH_ATR = float(max(0.0, float(mature_near_high_atr)))
    if mature_pullback_lookback is not None:
        _PHASE2_CONTINUATION_MATURE_PULLBACK_LOOKBACK = int(max(1, int(mature_pullback_lookback)))
    if mature_min_pullback_bias is not None:
        _PHASE2_CONTINUATION_MATURE_MIN_PULLBACK_BIAS = float(mature_min_pullback_bias)


def get_phase2_continuation_config() -> dict[str, int | float | bool]:
    return {
        "enabled": bool(_PHASE2_CONTINUATION_ENABLED),
        "chip_min": float(_PHASE2_CONTINUATION_CHIP_MIN),
        "micro_min": float(_PHASE2_CONTINUATION_MICRO_MIN),
        "lookback": int(_PHASE2_CONTINUATION_LOOKBACK),
        "expire_days": int(_PHASE2_CONTINUATION_EXPIRE_DAYS),
        "min_close_breakout_pct": float(_PHASE2_CONTINUATION_MIN_CLOSE_BREAKOUT_PCT),
        "mature_block_enabled": bool(_PHASE2_CONTINUATION_MATURE_BLOCK_ENABLED),
        "mature_leg_days": int(_PHASE2_CONTINUATION_MATURE_LEG_DAYS),
        "mature_bias_atr": float(_PHASE2_CONTINUATION_MATURE_BIAS_ATR),
        "mature_near_high_atr": float(_PHASE2_CONTINUATION_MATURE_NEAR_HIGH_ATR),
        "mature_pullback_lookback": int(_PHASE2_CONTINUATION_MATURE_PULLBACK_LOOKBACK),
        "mature_min_pullback_bias": float(_PHASE2_CONTINUATION_MATURE_MIN_PULLBACK_BIAS),
    }
