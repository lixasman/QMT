from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .types import BreakerDecision, ReconciliationResult, RegimeResult, RoundTripResult, T0Signal


def _append_jsonl(*, log_path: str | Path, obj: dict[str, Any]) -> None:
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_regime(*, log_path: str | Path, result: RegimeResult, etf_code: str, fsm_state: str = "") -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_REGIME",
            "timestamp": result.computed_at.strftime("%Y-%m-%d %H:%M:%S"),
            "etf_code": str(etf_code),
            "regime_active": bool(result.regime_active),
            "reason": str(result.reason),
            "auction_vol_ratio": float(result.auction_vol_ratio),
            "atr5_percentile": float(result.atr5_percentile),
            "fsm_state": str(fsm_state),
        },
    )


def log_signal(*, log_path: str | Path, signal: T0Signal) -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_SIGNAL",
            "timestamp": signal.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "etf_code": str(signal.etf_code),
            "signal_type": str(signal.signal_type),
            "vwap": float(signal.vwap),
            "sigma": float(signal.sigma),
            "k_value": float(signal.k_value),
            "target_price": float(signal.target_price),
            "trend_state": str(signal.trend_state),
            "kde_support": bool(signal.kde_support),
            "kde_zone_price": float(signal.kde_zone_price) if signal.kde_zone_price is not None else None,
            "iopv_confidence": str(signal.confidence),
            "action": str(signal.action),
            "order_price": float(signal.target_price),
            "order_amount": float(signal.amount),
        },
    )


def log_round_trip(*, log_path: str | Path, rt: RoundTripResult) -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_ROUND_TRIP",
            "timestamp": rt.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "etf_code": str(rt.etf_code),
            "direction": str(rt.direction),
            "buy_price": float(rt.buy_price),
            "sell_price": float(rt.sell_price),
            "quantity": int(rt.quantity),
            "commission": float(rt.commission),
            "net_pnl_bps": float(rt.net_pnl_bps),
            "net_pnl_cny": float(rt.net_pnl_cny),
            "actual_be_bps": float(rt.actual_be_bps),
            "daily_round_trip_count": int(rt.daily_round_trip_count),
            "consecutive_loss_count": int(rt.consecutive_loss_count),
            "t0_daily_pnl": float(rt.t0_daily_pnl),
        },
    )


def log_breaker(*, log_path: str | Path, d: BreakerDecision) -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_BREAKER",
            "timestamp": d.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "etf_code": str(d.etf_code),
            "breaker_layer": str(d.breaker_layer),
            "trigger_value": float(d.trigger_value),
            "threshold": float(d.threshold),
            "action": str(d.action),
            "note": str(d.note),
        },
    )


def log_reconciliation(*, log_path: str | Path, r: ReconciliationResult) -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_RECONCILIATION",
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger": str(r.trigger),
            "order_id": int(r.order_id),
            "case": str(r.case),
            "memory_state": str(r.memory_status.value),
            "broker_state": str(r.broker_status.value),
            "action": str(r.action),
            "position_sync": dict(r.position_sync),
        },
    )


def log_audit(*, log_path: str | Path, timestamp: datetime, etf_code: str, items: dict[str, Any]) -> None:
    _append_jsonl(
        log_path=log_path,
        obj={
            "type": "T0_AUDIT",
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "etf_code": str(etf_code),
            "items": dict(items),
        },
    )
