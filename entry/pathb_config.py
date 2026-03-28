from __future__ import annotations

# Path-B ATR multiplier for Phase3 confirmation.
_PATHB_ATR_MULT = 0.5
_PATHB_CHIP_MIN = 0.85
_PATHB_REQUIRE_TREND = True
_PATHB_REQUIRE_VWAP_STRICT = True


def set_pathb_atr_mult(v: float) -> None:
    global _PATHB_ATR_MULT
    _PATHB_ATR_MULT = float(v)


def get_pathb_atr_mult() -> float:
    return float(_PATHB_ATR_MULT)


def set_pathb_chip_min(v: float) -> None:
    global _PATHB_CHIP_MIN
    _PATHB_CHIP_MIN = float(v)


def get_pathb_chip_min() -> float:
    return float(_PATHB_CHIP_MIN)


def set_pathb_require_trend(v: bool) -> None:
    global _PATHB_REQUIRE_TREND
    _PATHB_REQUIRE_TREND = bool(v)


def get_pathb_require_trend() -> bool:
    return bool(_PATHB_REQUIRE_TREND)


def set_pathb_require_vwap_strict(v: bool) -> None:
    global _PATHB_REQUIRE_VWAP_STRICT
    _PATHB_REQUIRE_VWAP_STRICT = bool(v)


def get_pathb_require_vwap_strict() -> bool:
    return bool(_PATHB_REQUIRE_VWAP_STRICT)
