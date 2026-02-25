from __future__ import annotations

from datetime import datetime

from core.cash_manager import CashManager
from core.enums import FSMState

from .types import T0Decision
from .constants import (
    T0_BASE_EXPOSURE_RATIO,
    T0_DAILY_LOSS_MAX,
    T0_EXTREME_DOWN_PCT,
    T0_EXTREME_UP_PCT,
    T0_FORBIDDEN_WINDOWS,
    T0_RETURN_MIN,
    T0_VWAP_SIGMA_MULT,
)


def decide_t0_operation(
    *,
    now: datetime,
    etf_code: str,
    position_state: str,
    t0_frozen: bool,
    current_return: float,
    daily_t0_loss: float,
    base_value: float,
    available_reserve: float,
    price: float,
    vwap: float,
    sigma: float,
    daily_change: float,
    cash_manager: CashManager,
) -> T0Decision:
    _ = cash_manager
    ts = now
    code = str(etf_code)
    state_raw = str(position_state)
    try:
        st = FSMState(state_raw)
    except Exception:
        st = FSMState.S0_IDLE

    ret = float(current_return)
    loss = float(daily_t0_loss)
    frozen = bool(t0_frozen)

    if st in (FSMState.S0_IDLE, FSMState.S1_TRIAL, FSMState.S5_REDUCED):
        if frozen is False:
            enabled = False
        else:
            enabled = False
        if enabled:
            raise AssertionError(f"T+0 在 {st} 不应激活")
    else:
        enabled = (not frozen) and (ret > float(T0_RETURN_MIN)) and (loss < float(T0_DAILY_LOSS_MAX))

    max_exposure = 0.0
    if enabled:
        max_exposure = float(min(float(base_value) * float(T0_BASE_EXPOSURE_RATIO), float(available_reserve)))
        if max_exposure < 0:
            max_exposure = 0.0

    for w in T0_FORBIDDEN_WINDOWS:
        if w[0] <= ts.time() <= w[1]:
            return T0Decision(
                etf_code=code,
                timestamp=ts,
                enabled=bool(enabled),
                direction="HOLD",
                max_exposure=float(max_exposure),
                reason="FORBIDDEN_WINDOW",
                order=None,
                constraints={"position_state": st.value, "t0_frozen": bool(frozen)},
            )

    if not enabled:
        return T0Decision(
            etf_code=code,
            timestamp=ts,
            enabled=False,
            direction="HOLD",
            max_exposure=0.0,
            reason="T0_DISABLED",
            order=None,
            constraints={"position_state": st.value, "t0_frozen": bool(frozen), "return": float(ret), "daily_loss": float(loss)},
        )

    px = float(price)
    vw = float(vwap)
    sg = float(sigma)
    if sg <= 0:
        return T0Decision(
            etf_code=code,
            timestamp=ts,
            enabled=True,
            direction="HOLD",
            max_exposure=float(max_exposure),
            reason="SIGMA_INVALID",
            order=None,
            constraints={"price": float(px), "vwap": float(vw), "sigma": float(sg)},
        )

    up_th = float(vw) + float(T0_VWAP_SIGMA_MULT) * float(sg)
    down_th = float(vw) - float(T0_VWAP_SIGMA_MULT) * float(sg)

    if float(px) > float(up_th):
        if float(daily_change) > float(T0_EXTREME_UP_PCT):
            return T0Decision(
                etf_code=code,
                timestamp=ts,
                enabled=True,
                direction="HOLD",
                max_exposure=float(max_exposure),
                reason="EXTREME_UP_FREEZE_REVERSE",
                order=None,
                constraints={"daily_change": float(daily_change), "threshold": float(T0_EXTREME_UP_PCT), "trigger": "price>vwap+1.5sigma"},
            )
        return T0Decision(
            etf_code=code,
            timestamp=ts,
            enabled=True,
            direction="REVERSE_T_SELL",
            max_exposure=float(max_exposure),
            reason="PRICE_ABOVE_VWAP_BAND",
            order=None,
            constraints={"price": float(px), "vwap": float(vw), "sigma": float(sg)},
        )

    if float(px) < float(down_th):
        if float(daily_change) < float(T0_EXTREME_DOWN_PCT):
            return T0Decision(
                etf_code=code,
                timestamp=ts,
                enabled=True,
                direction="HOLD",
                max_exposure=float(max_exposure),
                reason="EXTREME_DOWN_FREEZE_FORWARD",
                order=None,
                constraints={"daily_change": float(daily_change), "threshold": float(T0_EXTREME_DOWN_PCT), "trigger": "price<vwap-1.5sigma"},
            )
        return T0Decision(
            etf_code=code,
            timestamp=ts,
            enabled=True,
            direction="FORWARD_T_BUY",
            max_exposure=float(max_exposure),
            reason="PRICE_BELOW_VWAP_BAND",
            order=None,
            constraints={"price": float(px), "vwap": float(vw), "sigma": float(sg)},
        )

    return T0Decision(
        etf_code=code,
        timestamp=ts,
        enabled=True,
        direction="HOLD",
        max_exposure=float(max_exposure),
        reason="NO_VWAP_DEVIATION",
        order=None,
        constraints={"price": float(px), "vwap": float(vw), "sigma": float(sg)},
    )
