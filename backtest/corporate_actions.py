from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from core.interfaces import Bar
from core.models import PendingEntry, PositionState


_SPLIT_PRICE_FACTORS: tuple[float, ...] = (
    0.2,
    0.25,
    1.0 / 3.0,
    0.5,
    2.0,
    3.0,
    4.0,
    5.0,
)
_SPLIT_FACTOR_TOLERANCE = 0.03


@dataclass(frozen=True)
class CorporateActionEvent:
    etf_code: str
    effective_day: date
    price_factor: float
    quantity_factor: float
    raw_ratio: float
    reason: str
    source: str


def infer_split_price_factor(*, prev_close: float, next_open: float) -> float | None:
    pc = float(prev_close)
    op = float(next_open)
    if pc <= 0 or op <= 0:
        return None
    ratio = float(op / pc)
    nearest = min(_SPLIT_PRICE_FACTORS, key=lambda x: abs(float(x) - ratio))
    if abs(float(nearest) - ratio) / max(abs(float(nearest)), 1e-12) > float(_SPLIT_FACTOR_TOLERANCE):
        return None
    return float(nearest)


def infer_split_events_from_daily_bars(*, etf_code: str, bars: Sequence[Bar]) -> list[CorporateActionEvent]:
    out: list[CorporateActionEvent] = []
    seq = sorted(list(bars), key=lambda x: x.time)
    for prev_bar, cur_bar in zip(seq[:-1], seq[1:]):
        factor = infer_split_price_factor(prev_close=float(prev_bar.close), next_open=float(cur_bar.open))
        if factor is None:
            continue
        out.append(
            CorporateActionEvent(
                etf_code=str(etf_code),
                effective_day=cur_bar.time.date(),
                price_factor=float(factor),
                quantity_factor=float(1.0 / float(factor)),
                raw_ratio=float(cur_bar.open) / float(prev_bar.close),
                reason=("SPLIT" if float(factor) < 1.0 else "REVERSE_SPLIT"),
                source="inferred_daily_open_gap",
            )
        )
    return out


def cumulative_price_factor(*, events: Sequence[CorporateActionEvent], from_day: date, to_day: date) -> float:
    if to_day <= from_day:
        return 1.0
    factor = 1.0
    for ev in events:
        if from_day < ev.effective_day <= to_day:
            factor *= float(ev.price_factor)
    return float(factor)


def rescale_bar(bar: Bar, *, price_factor: float) -> Bar:
    factor = float(price_factor)
    qty_factor = float(1.0 / factor) if factor > 0 else 1.0
    return Bar(
        time=bar.time,
        open=float(bar.open) * factor,
        high=float(bar.high) * factor,
        low=float(bar.low) * factor,
        close=float(bar.close) * factor,
        volume=float(bar.volume) * qty_factor,
        amount=float(bar.amount),
    )


def rescale_qty(*, qty: int, price_factor: float) -> int:
    factor = float(price_factor)
    if factor <= 0:
        return int(qty)
    return int(round(float(qty) / factor))


def apply_price_factor_to_position_state(*, ps: PositionState, price_factor: float) -> None:
    factor = float(price_factor)
    if factor <= 0:
        return
    ps.base_qty = int(rescale_qty(qty=int(ps.base_qty), price_factor=factor))
    ps.scale_1_qty = int(rescale_qty(qty=int(ps.scale_1_qty), price_factor=factor))
    ps.scale_2_qty = int(rescale_qty(qty=int(ps.scale_2_qty), price_factor=factor))
    ps.total_qty = int(rescale_qty(qty=int(ps.total_qty), price_factor=factor))
    ps.same_day_buy_qty = int(rescale_qty(qty=int(getattr(ps, "same_day_buy_qty", 0) or 0), price_factor=factor))
    if float(ps.avg_cost) > 0:
        ps.avg_cost = float(ps.avg_cost) * factor
    if float(ps.highest_high) > 0:
        ps.highest_high = float(ps.highest_high) * factor
    if float(ps.lifeboat_tight_stop) > 0:
        ps.lifeboat_tight_stop = float(ps.lifeboat_tight_stop) * factor
    if ps.auction_volume_history:
        qty_factor = float(1.0 / factor)
        ps.auction_volume_history = [float(x) * qty_factor for x in ps.auction_volume_history]
    for pending in ps.pending_sell_locked:
        pending.locked_qty = int(rescale_qty(qty=int(pending.locked_qty), price_factor=factor))
    for pending in ps.pending_sell_unfilled:
        pending.locked_qty = int(rescale_qty(qty=int(pending.locked_qty), price_factor=factor))


def apply_price_factor_to_pending_entries(*, pending_entries: Sequence[PendingEntry], etf_code: str, price_factor: float) -> int:
    factor = float(price_factor)
    changed = 0
    for pe in pending_entries:
        if str(pe.etf_code) != str(etf_code):
            continue
        pe.h_signal = float(pe.h_signal) * factor
        pe.l_signal = float(pe.l_signal) * factor
        pe.close_signal_day = float(pe.close_signal_day) * factor
        pe.atr_20 = float(pe.atr_20) * factor
        pe.trial_qty = int(rescale_qty(qty=int(pe.trial_qty), price_factor=factor))
        pe.confirm_qty = int(rescale_qty(qty=int(pe.confirm_qty), price_factor=factor))
        if pe.trial_price is not None and float(pe.trial_price) > 0:
            pe.trial_price = float(pe.trial_price) * factor
        if pe.confirm_price is not None and float(pe.confirm_price) > 0:
            pe.confirm_price = float(pe.confirm_price) * factor
        changed += 1
    return int(changed)
