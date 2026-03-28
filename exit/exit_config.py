from __future__ import annotations

from .constants import K_CHIP_DECAY, K_NORMAL, K_REDUCED, LAYER1_SELL_DISCOUNT
from .constants import LAYER2_THRESHOLD

_EXIT_K_NORMAL = float(K_NORMAL)
_EXIT_K_CHIP_DECAY = float(K_CHIP_DECAY)
_EXIT_K_REDUCED = float(K_REDUCED)
_EXIT_LAYER2_THRESHOLD = float(LAYER2_THRESHOLD)
_EXIT_LAYER2_SCORE_LOG = False
_EXIT_ATR_PCT_MIN: float | None = 0.025
_EXIT_ATR_PCT_MAX: float | None = 0.04
_EXIT_K_ACCEL_ENABLED = True
_EXIT_K_ACCEL_STEP_PCT = 0.05
_EXIT_K_ACCEL_STEP_K = 0.2
_EXIT_K_ACCEL_KMIN = 1.0
_EXIT_LAYER1_SELL_DISCOUNT = float(LAYER1_SELL_DISCOUNT)
_EXIT_LAYER1_USE_STOP_PRICE = False


def set_exit_k(*, k_normal: float | None = None, k_chip_decay: float | None = None, k_reduced: float | None = None) -> None:
    global _EXIT_K_NORMAL, _EXIT_K_CHIP_DECAY, _EXIT_K_REDUCED
    if k_normal is not None:
        _EXIT_K_NORMAL = float(k_normal)
    if k_chip_decay is not None:
        _EXIT_K_CHIP_DECAY = float(k_chip_decay)
    if k_reduced is not None:
        _EXIT_K_REDUCED = float(k_reduced)


def set_exit_layer2_threshold(v: float | None) -> None:
    global _EXIT_LAYER2_THRESHOLD
    if v is None:
        return
    _EXIT_LAYER2_THRESHOLD = float(v)


def set_exit_layer2_score_log(v: bool | None) -> None:
    global _EXIT_LAYER2_SCORE_LOG
    if v is None:
        return
    _EXIT_LAYER2_SCORE_LOG = bool(v)


def set_exit_atr_pct_bounds(*, min_pct: float | None = None, max_pct: float | None = None) -> None:
    global _EXIT_ATR_PCT_MIN, _EXIT_ATR_PCT_MAX
    _EXIT_ATR_PCT_MIN = float(min_pct) if min_pct is not None else None
    _EXIT_ATR_PCT_MAX = float(max_pct) if max_pct is not None else None


def set_exit_k_accel(
    *,
    enabled: bool | None = None,
    step_pct: float | None = None,
    step_k: float | None = None,
    k_min: float | None = None,
) -> None:
    global _EXIT_K_ACCEL_ENABLED, _EXIT_K_ACCEL_STEP_PCT, _EXIT_K_ACCEL_STEP_K, _EXIT_K_ACCEL_KMIN
    if enabled is not None:
        _EXIT_K_ACCEL_ENABLED = bool(enabled)
    if step_pct is not None:
        _EXIT_K_ACCEL_STEP_PCT = float(step_pct)
    if step_k is not None:
        _EXIT_K_ACCEL_STEP_K = float(step_k)
    if k_min is not None:
        _EXIT_K_ACCEL_KMIN = float(k_min)


def set_exit_layer1_order_pricing(*, sell_discount: float | None = None, use_stop_price: bool | None = None) -> None:
    global _EXIT_LAYER1_SELL_DISCOUNT, _EXIT_LAYER1_USE_STOP_PRICE
    if sell_discount is not None:
        v = float(sell_discount)
        if v <= 0.0:
            raise AssertionError(f"exit layer1 sell discount must be > 0, got {sell_discount}")
        _EXIT_LAYER1_SELL_DISCOUNT = float(v)
    if use_stop_price is not None:
        _EXIT_LAYER1_USE_STOP_PRICE = bool(use_stop_price)


def get_exit_k_normal() -> float:
    return float(_EXIT_K_NORMAL)


def get_exit_k_chip_decay() -> float:
    return float(_EXIT_K_CHIP_DECAY)


def get_exit_k_reduced() -> float:
    return float(_EXIT_K_REDUCED)


def get_exit_k_accel() -> tuple[bool, float, float, float]:
    return (
        bool(_EXIT_K_ACCEL_ENABLED),
        float(_EXIT_K_ACCEL_STEP_PCT),
        float(_EXIT_K_ACCEL_STEP_K),
        float(_EXIT_K_ACCEL_KMIN),
    )


def get_exit_layer1_sell_discount() -> float:
    return float(_EXIT_LAYER1_SELL_DISCOUNT)


def get_exit_layer1_use_stop_price() -> bool:
    return bool(_EXIT_LAYER1_USE_STOP_PRICE)


def get_exit_layer2_threshold() -> float:
    return float(_EXIT_LAYER2_THRESHOLD)


def get_exit_layer2_score_log() -> bool:
    return bool(_EXIT_LAYER2_SCORE_LOG)


def get_exit_atr_pct_min() -> float | None:
    return float(_EXIT_ATR_PCT_MIN) if _EXIT_ATR_PCT_MIN is not None else None


def get_exit_atr_pct_max() -> float | None:
    return float(_EXIT_ATR_PCT_MAX) if _EXIT_ATR_PCT_MAX is not None else None
