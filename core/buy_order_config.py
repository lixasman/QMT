from __future__ import annotations

_AGGRESSIVE_BUY_MULTIPLIER = 1.003
_AGGRESSIVE_BUY_USE_ASK1 = False


def set_aggressive_buy_pricing(*, multiplier: float | None = None, use_ask1: bool | None = None) -> None:
    global _AGGRESSIVE_BUY_MULTIPLIER, _AGGRESSIVE_BUY_USE_ASK1
    if multiplier is not None:
        v = float(multiplier)
        if v <= 0.0:
            raise AssertionError(f"aggressive buy multiplier must be > 0, got {multiplier}")
        _AGGRESSIVE_BUY_MULTIPLIER = float(v)
    if use_ask1 is not None:
        _AGGRESSIVE_BUY_USE_ASK1 = bool(use_ask1)


def get_aggressive_buy_multiplier() -> float:
    return float(_AGGRESSIVE_BUY_MULTIPLIER)


def get_aggressive_buy_use_ask1() -> bool:
    return bool(_AGGRESSIVE_BUY_USE_ASK1)
