from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Sequence

from core.interfaces import Bar
from core.warn_utils import warn_once
from entry.signals.trend import ema, kama


def _atr_wilder(bars: Sequence[Bar], *, period: int = 14) -> float:
    p = int(period)
    if p <= 0 or len(bars) < 2:
        return 0.0
    atr = float(bars[0].high) - float(bars[0].low)
    alpha = 1.0 / float(p)
    for i in range(1, len(bars)):
        tr = max(
            float(bars[i].high) - float(bars[i].low),
            abs(float(bars[i].high) - float(bars[i - 1].close)),
            abs(float(bars[i].low) - float(bars[i - 1].close)),
        )
        atr = (1.0 - alpha) * float(atr) + alpha * float(tr)
    return float(atr)


def compute_kama_rising_days(closes: list[float], *, period: int = 10) -> int:
    vals = kama(list(closes), period)
    if len(vals) < 2:
        return 0
    days = 0
    for i in range(len(vals) - 1, 0, -1):
        if float(vals[i]) > float(vals[i - 1]):
            days += 1
        else:
            break
    return int(days)


def compute_elder_impulse_green(closes: list[float]) -> bool:
    if len(closes) < 30:
        return False
    ema13 = ema(closes, 13)
    ema13_rising = float(ema13[-1]) > float(ema13[-2])
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [float(a) - float(b) for a, b in zip(ema12, ema26)]
    signal = ema(macd_line, 9)
    hist = [float(m) - float(s) for m, s in zip(macd_line, signal)]
    return bool(ema13_rising and float(hist[-1]) > float(hist[-2]))


def chip_zone_features(*, etf_code: str, dense_zones_json: str, last_price: float, atr14: float) -> tuple[float, float]:
    if not dense_zones_json or float(atr14) <= 0:
        return 0.0, 0.0
    parse_failed = False
    try:
        zones = json.loads(dense_zones_json)
    except Exception:
        parse_failed = True
        zones = []
    if not isinstance(zones, list) or not zones:
        if parse_failed and str(dense_zones_json or "").strip() not in ("", "[]"):
            warn_once(f"chip_zones_json_invalid:{str(etf_code)}", f"Chip: dense_zones_json 解析失败，已降级为空 zones: etf={etf_code}", logger_name=__name__)
        return 0.0, 0.0
    parsed: list[dict[str, float]] = []
    bad_item = 0
    bad_fields = 0
    for z in zones:
        if not isinstance(z, dict):
            bad_item += 1
            continue
        try:
            p = float(z.get("price", 0.0) or 0.0)
            d = float(z.get("density", 0.0) or 0.0)
        except Exception:
            bad_fields += 1
            continue
        parsed.append({"price": float(p), "density": float(d)})
    if not parsed:
        if bad_item or bad_fields:
            warn_once(
                f"chip_zones_all_invalid:{str(etf_code)}",
                f"Chip: dense_zones 全部不可用，已降级为无 zones: etf={etf_code} bad_item={bad_item} bad_fields={bad_fields} total={len(zones)}",
                logger_name=__name__,
            )
        return 0.0, 0.0
    if parse_failed or bad_item or bad_fields:
        warn_once(
            f"chip_zones_partial_invalid:{str(etf_code)}",
            f"Chip: dense_zones 存在容错丢弃，已汇总告警: etf={etf_code} parse_failed={parse_failed} kept={len(parsed)}/{len(zones)} bad_item={bad_item} bad_fields={bad_fields}",
            logger_name=__name__,
        )
    parsed.sort(key=lambda x: abs(float(x["price"]) - float(last_price)))
    nearest = parsed[0]
    distance = abs(float(nearest["price"]) - float(last_price)) / float(atr14)
    densities = sorted(float(x["density"]) for x in parsed)
    nd = float(nearest["density"])
    rank = float(sum(1 for d in densities if float(d) <= float(nd)) / len(densities)) if densities else 0.0
    return float(round(rank, 4)), float(round(distance, 4))


def vwap_proxy_from_daily_bar(bar: Optional[Bar]) -> float:
    if bar is None:
        return 0.0
    vol = float(bar.volume)
    amt = float(bar.amount)
    if vol > 0 and amt > 0:
        return float(amt / vol)
    return float(bar.close)


@dataclass(frozen=True)
class ScaleFeatures:
    unrealized_profit_atr14_multiple: float
    score_soft: float
    kama_rising_days: int
    elder_impulse_green: bool
    pullback_atr14_multiple: float
    chip_density_rank: float
    chip_touch_distance_atr14: float
    micro_vol_ratio: float
    micro_support_held: bool
    micro_bullish_close: bool


def aggregate_scale_features(
    *,
    etf_code: str,
    bars: list[Bar],
    last_price: float,
    avg_cost: float,
    highest_high: float,
    dense_zones_json: str,
    support_price: Optional[float],
    ms_vs_max_logz: Optional[float],
    score_soft: float,
    tick_size: float,
) -> ScaleFeatures:
    closes = [float(b.close) for b in bars]
    atr14 = _atr_wilder(bars, period=14)
    up = (float(last_price) - float(avg_cost)) / float(atr14) if float(atr14) > 0 else 0.0
    pullback = (float(highest_high) - float(last_price)) / float(atr14) if float(atr14) > 0 else 0.0
    chip_rank, chip_dist = chip_zone_features(etf_code=str(etf_code), dense_zones_json=str(dense_zones_json or "[]"), last_price=float(last_price), atr14=float(atr14))
    mv = float(ms_vs_max_logz) if ms_vs_max_logz is not None else 0.0
    micro_vol = float(max(0.0, min(1.0, mv / 3.0)))
    sup = support_price
    micro_support = bool(sup is not None and float(last_price) >= float(sup) - 2.0 * float(tick_size))
    kama_days = compute_kama_rising_days(closes)
    elder_green = compute_elder_impulse_green(closes)
    vwap_px = vwap_proxy_from_daily_bar(bars[-1] if bars else None)
    micro_bullish = bool(float(last_price) > float(vwap_px) and int(kama_days) >= 1)
    return ScaleFeatures(
        unrealized_profit_atr14_multiple=float(up),
        score_soft=float(score_soft),
        kama_rising_days=int(kama_days),
        elder_impulse_green=bool(elder_green),
        pullback_atr14_multiple=float(pullback),
        chip_density_rank=float(chip_rank),
        chip_touch_distance_atr14=float(chip_dist),
        micro_vol_ratio=float(micro_vol),
        micro_support_held=bool(micro_support),
        micro_bullish_close=bool(micro_bullish),
    )
