from __future__ import annotations

from datetime import date, datetime
from typing import Any


def normalize_high_chase_signal_source(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"", "all", "all_signals", "signal_fired", "phase2"}:
        return "all_signals"
    if raw in {"missed", "missed_executable", "phase3_missed_executable", "insufficient_cash"}:
        return "missed_executable"
    return "all_signals"


def phase2_signal_reference_price(*, close_signal_day: float, h_signal: float) -> float:
    close_px = float(close_signal_day)
    if close_px > 0:
        return float(close_px)
    high_px = float(h_signal)
    if high_px > 0:
        return float(high_px)
    return 0.0


def _parse_signal_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except Exception:
            return None
    if len(s) >= 10 and "-" in s:
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None
    return None


def decode_high_chase_signal_rows(raw: object) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        signal_day: date | None = None
        ref_price = 0.0
        if isinstance(item, dict):
            signal_day = _parse_signal_date(item.get("signal_date"))
            try:
                ref_price = float(item.get("ref_price") or 0.0)
            except Exception:
                ref_price = 0.0
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            signal_day = _parse_signal_date(item[0])
            try:
                ref_price = float(item[1])
            except Exception:
                ref_price = 0.0
        if signal_day is None or ref_price <= 0:
            continue
        out.append((signal_day, float(ref_price)))
    out.sort(key=lambda x: x[0])
    return out


def encode_high_chase_signal_rows(rows: list[tuple[date, float]]) -> list[dict[str, object]]:
    return [
        {
            "signal_date": signal_day.strftime("%Y%m%d"),
            "ref_price": float(ref_price),
        }
        for signal_day, ref_price in list(rows)
        if float(ref_price) > 0
    ]


def scale_high_chase_signal_rows(*, rows: list[tuple[date, float]], price_factor: float) -> list[tuple[date, float]]:
    factor = float(price_factor)
    if factor <= 0:
        return list(rows)
    scaled: list[tuple[date, float]] = []
    for signal_day, ref_price in list(rows):
        px = float(ref_price)
        if px <= 0:
            continue
        scaled.append((signal_day, float(px) * factor))
    scaled.sort(key=lambda x: x[0])
    return scaled


def prune_high_chase_signal_rows(
    *,
    rows: list[tuple[date, float]],
    now_day: date,
    lookback_days: int,
) -> list[tuple[date, float]]:
    lookback = int(max(1, int(lookback_days)))
    kept: list[tuple[date, float]] = []
    for signal_day, ref_price in list(rows):
        px = float(ref_price)
        if px <= 0:
            continue
        try:
            age_days = int((now_day - signal_day).days)
        except Exception:
            continue
        if age_days < 0 or age_days > lookback:
            continue
        kept.append((signal_day, px))
    kept.sort(key=lambda x: x[0])
    return kept


def remember_high_chase_signal(
    *,
    rows: list[tuple[date, float]],
    now_day: date,
    ref_price: float,
    lookback_days: int,
) -> tuple[list[tuple[date, float]], bool]:
    px = float(ref_price)
    if px <= 0:
        return prune_high_chase_signal_rows(rows=rows, now_day=now_day, lookback_days=lookback_days), False
    kept = prune_high_chase_signal_rows(rows=rows, now_day=now_day, lookback_days=lookback_days)
    for signal_day, old_price in kept:
        if signal_day == now_day and abs(float(old_price) - px) <= 1e-12:
            return kept, False
    kept.append((now_day, px))
    kept.sort(key=lambda x: x[0])
    return kept, True


def should_block_high_chase_signal(
    *,
    rows: list[tuple[date, float]],
    now_day: date,
    ref_price: float,
    lookback_days: int,
    max_rise: float,
) -> tuple[list[tuple[date, float]], bool, str]:
    px = float(ref_price)
    kept = prune_high_chase_signal_rows(rows=rows, now_day=now_day, lookback_days=lookback_days)
    if px <= 0 or not kept:
        return kept, False, ""
    first_day, first_price = kept[0]
    rise = float(px / float(first_price) - 1.0) if float(first_price) > 0 else 0.0
    threshold = float(max(0.0, float(max_rise)))
    if float(rise) + 1e-12 < float(threshold):
        return kept, False, ""
    return (
        kept,
        True,
        (
            f"bt_high_chase_after_first_signal first_day={first_day.isoformat()} "
            f"first_price={float(first_price):.6f} current_price={float(px):.6f} "
            f"rise={float(rise):.4f} threshold={float(threshold):.4f}"
        ),
    )
