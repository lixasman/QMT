from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from .types import SignalFired, WatchlistItem


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(body, encoding="utf-8")


def archive_watchlist(*, base_dir: str | Path, d: date, watchlist: list[WatchlistItem]) -> Path:
    base = Path(base_dir)
    out = base / "watchlist_daily" / f"{d.strftime('%Y%m%d')}.json"
    payload = [asdict(x) for x in watchlist]
    _write_json(out, {"date": d.strftime("%Y%m%d"), "watchlist": payload})
    return out


def archive_signal_fired(*, base_dir: str | Path, fired: SignalFired, event_id: Optional[str] = None) -> Path:
    eid = event_id or str(uuid4())
    base = Path(base_dir)
    out = base / "entry_events" / f"{fired.signal_date.strftime('%Y%m%d')}_{fired.etf_code}.json"
    w = fired.watchlist
    signals = dict(fired.signals)
    signals.update(
        {
            "sentiment_score": int(w.sentiment_score),
            "profit_ratio": float(w.profit_ratio),
            "vpin_rank": w.vpin_rank,
            "ofi_daily": w.ofi_daily,
            "vs_max": w.vs_max,
        }
    )
    payload: dict[str, Any] = {
        "event_id": eid,
        "etf_code": fired.etf_code,
        "signal_date": fired.signal_date.strftime("%Y-%m-%d"),
        "score_entry": float(fired.score),
        "is_strong": bool(fired.is_strong),
        "signals": signals,
        "h_signal": float(fired.h_signal),
        "l_signal": float(fired.l_signal),
        "close_signal_day": float(fired.close_signal_day),
        "atr_20": float(fired.atr_20),
        "watchlist": asdict(w),
        "outcome": {"status": "PENDING"},
    }
    _write_json(out, payload)
    return out


def archive_near_miss(
    *,
    base_dir: str | Path,
    d: date,
    etf_code: str,
    score_entry: float,
    signals: dict[str, Any],
    h_signal: float,
    l_signal: float,
    close_signal_day: float,
    event_id: Optional[str] = None,
) -> Optional[Path]:
    score = float(score_entry)
    if not (0.25 <= score < 0.45):
        return None
    eid = event_id or str(uuid4())
    base = Path(base_dir)
    out = base / "near_miss_events" / f"{d.strftime('%Y%m%d')}_{etf_code}.json"
    payload: dict[str, Any] = {
        "event_id": eid,
        "etf_code": etf_code,
        "signal_date": d.strftime("%Y-%m-%d"),
        "score_entry": score,
        "near_miss_reason": "score_below_threshold",
        "score_gap": round(0.45 - score, 4),
        "signals": signals,
        "h_signal": float(h_signal),
        "l_signal": float(l_signal),
        "close_signal_day": float(close_signal_day),
        "outcome": {},
    }
    _write_json(out, payload)
    return out

