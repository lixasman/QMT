from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Optional

import numpy as np

from core.warn_utils import info_once, warn_once
from etf_chip_engine.config import CONFIG
from etf_chip_engine.data.xtdata_provider import calc_atr_10, download_tick_data, get_daily_bars, get_etf_info, get_market_tick_data, get_total_shares_detail, require_xtdata
from etf_chip_engine.engine import ETFChipEngine, Snapshot
from etf_chip_engine.data.xtdata_provider import prev_trade_date

_LOT_SIZE = 100.0
_RUNTIME_WARNED: set[str] = set()


def _warn_runtime_once(key: str, msg: str) -> None:
    warn_once(key, msg, logger_name=__name__)
    if key in _RUNTIME_WARNED:
        return
    _RUNTIME_WARNED.add(key)
    print(f"[WARN] {msg}", flush=True)


def _as_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _get_tick_field(rec: Any, name: str) -> Any:
    if isinstance(rec, dict):
        return rec.get(name)
    try:
        names = getattr(rec.dtype, "names", None)
        if names and name in names:
            return rec[name]
    except Exception:
        pass
    return None


def _trade_date_from_any(v: Any) -> str:
    m = re.search(r"(\d{8})", str(v or ""))
    return m.group(1) if m else ""


def _history_before_trade_date(daily_df: Any, *, trade_date: str, keep_count: int):
    import pandas as pd  # type: ignore

    base = daily_df.copy() if isinstance(daily_df, pd.DataFrame) and not daily_df.empty else pd.DataFrame()
    if base.empty:
        return base.reset_index(drop=True)
    if "time" in base.columns:
        keep_mask = base["time"].map(_trade_date_from_any) != str(trade_date)
        base = base.loc[keep_mask].reset_index(drop=True)
    keep_n = max(int(keep_count), 0)
    if keep_n > 0 and len(base) > keep_n:
        base = base.tail(keep_n).reset_index(drop=True)
    return base.reset_index(drop=True)


@dataclass
class _EtfAccumulator:
    prev_amount: Optional[float] = None
    prev_volume: Optional[float] = None
    prev_last: Optional[float] = None

    def to_snapshot(self, rec: Any) -> Optional[Snapshot]:
        last = _as_float(_get_tick_field(rec, "lastPrice"), 0.0)
        if last <= 0:
            return None
        amount = _as_float(_get_tick_field(rec, "amount"), 0.0)
        volume = _as_float(_get_tick_field(rec, "volume"), 0.0)

        if self.prev_amount is None or self.prev_volume is None:
            self.prev_amount = amount
            self.prev_volume = volume
            self.prev_last = last
            return None

        delta_amount = max(amount - self.prev_amount, 0.0)
        # XtData tick.volume 为“手”，引擎内部统一使用“股/份”。
        delta_volume = max(volume - self.prev_volume, 0.0) * _LOT_SIZE

        self.prev_amount = amount
        self.prev_volume = volume

        prev_last = self.prev_last if self.prev_last is not None else last
        self.prev_last = last

        high = max(last, prev_last)
        low = min(last, prev_last)

        return Snapshot(high=high, low=low, close=last, volume=delta_volume, amount=delta_amount)


def run_realtime_once(
    *,
    etf_code: str,
    seconds: float = 12.0,
    min_etf_ticks: int = 3,
    top_components: int = 30,
    config: Optional[dict[str, object]] = None,
) -> dict[str, Any]:
    xtdata = require_xtdata()
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)

    engine = ETFChipEngine(cfg)

    today = datetime.now().strftime("%Y%m%d")
    prev_date = prev_trade_date(today)
    chip_dir = Path(str(cfg.get("chip_snapshot_dir")))
    chip_dir.mkdir(parents=True, exist_ok=True)
    prev_state = chip_dir / f"{etf_code.replace('.', '_')}_{prev_date}.npz" if prev_date else None
    if prev_state is not None and prev_state.exists():
        engine.load_state(etf_code, str(prev_state))
    else:
        lookback = int(cfg.get("cold_start_lookback", 60))
        daily_raw = get_daily_bars([etf_code], end_time=today, count=max(lookback, 0) + 1)
        daily_df = _history_before_trade_date(daily_raw, trade_date=today, keep_count=lookback)
        if str(prev_date or "").strip():
            last_td = _trade_date_from_any(daily_df["time"].iloc[-1]) if (daily_df is not None and not daily_df.empty and "time" in daily_df.columns) else ""
            if last_td != str(prev_date):
                raise RuntimeError(
                    f"stale daily history: context=realtime:{etf_code}:cold_start trade_date={today}"
                    f" expected_last={prev_date} last_daily={last_td or 'missing'}"
                )
        rt_detail = get_total_shares_detail(etf_code, trade_date=today)
        rt_shares = float(rt_detail.get("shares", 0.0))
        rt_source = str(rt_detail.get("source", "none"))
        if not rt_source.startswith("official_"):
            _warn_runtime_once(
                f"realtime_shares_source_fallback_cold_start:{today}:{etf_code}",
                (
                    "Realtime Shares: 官方份额不可用，冷启动已降级。"
                    f" code={etf_code} date={today} source={rt_source}"
                    f" reason={rt_detail.get('reason', '')}"
                ),
            )
        rt_atr = calc_atr_10(daily_df) if daily_df is not None and not daily_df.empty else 0.0
        engine.cold_start(etf_code, daily_df, total_shares=rt_shares, atr=rt_atr)
    rt_detail = get_total_shares_detail(etf_code, trade_date=today)
    rt_source = str(rt_detail.get("source", "none"))
    if not rt_source.startswith("official_"):
        _warn_runtime_once(
            f"realtime_shares_source_fallback:{today}:{etf_code}",
            (
                "Realtime Shares: 官方份额不可用，已降级。"
                f" code={etf_code} date={today} source={rt_source}"
                f" reason={rt_detail.get('reason', '')}"
            ),
        )
    engine.chips[etf_code].total_shares = float(rt_detail.get("shares", 0.0))

    etf_info = get_etf_info(etf_code)
    if etf_info:
        engine.attach_iopv(etf_code, etf_info)

    stock_list: list[str] = [etf_code]
    if etf_info and isinstance(etf_info.get("stocks"), dict):
        items = []
        for k, v in etf_info["stocks"].items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            items.append((k, _as_float(v.get("componentVolume"), 0.0)))
        items.sort(key=lambda x: x[1], reverse=True)
        stock_list.extend([k for k, _ in items[: max(int(top_components), 0)]])
    stock_list = list(dict.fromkeys(stock_list))

    try:
        download_tick_data(stock_list, today)
    except Exception as e:
        info_once(
            f"realtime_download_tick_failed:{etf_code}:{today}",
            f"Realtime: download_tick_data 失败，已降级继续: etf={etf_code} date={today} err={repr(e)}",
            logger_name=__name__,
        )

    acc = _EtfAccumulator()
    lock = threading.Lock()
    done = threading.Event()
    state: dict[str, Any] = {
        "etf_code": etf_code,
        "etf_ticks": 0,
        "mode": "subscribe_quote",
        "last_indicators": None,
        "last_premium_rate": None,
        "last_iopv": None,
    }

    def on_data(datas: dict) -> None:
        nonlocal acc
        with lock:
            for code, recs in datas.items():
                if not isinstance(code, str):
                    continue
                if not recs:
                    continue
                for rec in recs:
                    last = _as_float(_get_tick_field(rec, "lastPrice"), 0.0)
                    if last <= 0:
                        continue
                    if code != etf_code:
                        if etf_code in engine.iopv:
                            engine.iopv[etf_code].update_stock_price(code, last)
                        continue

                    snap = acc.to_snapshot(rec)
                    if snap is None:
                        continue
                    out = engine.process_snapshot(etf_code, snap)
                    state["etf_ticks"] += 1
                    state["last_indicators"] = out
                    if etf_code in engine.iopv:
                        iopv = engine.iopv[etf_code].calculate_iopv()
                        state["last_iopv"] = None if np.isnan(iopv) else float(iopv)
                        state["last_premium_rate"] = float(engine.iopv[etf_code].get_premium_rate(snap.close))

                    if state["etf_ticks"] >= int(min_etf_ticks):
                        done.set()

    subscribe_ids: list[Any] = []
    for code in stock_list:
        sid = xtdata.subscribe_quote(code, period="tick", count=0, callback=on_data)
        subscribe_ids.append((code, sid))

    done.wait(timeout=float(seconds))

    for code, sid in subscribe_ids:
        try:
            xtdata.unsubscribe_quote(sid)
        except Exception as e1:
            try:
                xtdata.unsubscribe_quote(code, "tick")
            except Exception as e2:
                info_once(
                    f"realtime_unsubscribe_failed:{code}",
                    f"Realtime: unsubscribe_quote 失败，可能导致重复订阅: code={code} sid={sid} err1={repr(e1)} err2={repr(e2)}",
                    logger_name=__name__,
                )

    if int(state["etf_ticks"]) <= 0:
        state["mode"] = "replay_market_data_tick"
        try:
            ft = xtdata.get_full_tick(stock_list[: min(len(stock_list), max(1, top_components + 1))])
        except Exception as e:
            info_once(
                f"realtime_get_full_tick_failed:{etf_code}",
                f"Realtime: get_full_tick 失败，已降级为空: etf={etf_code} err={repr(e)}",
                logger_name=__name__,
            )
            ft = {}
        if isinstance(ft, dict) and etf_code in engine.iopv:
            for code, rec in ft.items():
                if code == etf_code:
                    continue
                last = _as_float(_get_tick_field(rec, "lastPrice"), 0.0)
                if last > 0:
                    engine.iopv[etf_code].update_stock_price(str(code), last)

        try:
            md = get_market_tick_data(etf_code, today, count=3000)
            if md is None:
                ticks = None
            else:
                import pandas as pd  # type: ignore

                ticks = md.to_dict("records") if isinstance(md, pd.DataFrame) else md
        except Exception as e:
            info_once(
                f"realtime_replay_market_data_failed:{etf_code}",
                f"Realtime: get_market_data(tick) 失败，回放兜底不可用: etf={etf_code} err={repr(e)}",
                logger_name=__name__,
            )
            ticks = None

        if ticks is not None:
            for rec in ticks if isinstance(ticks, list) else np.asarray(ticks):
                with lock:
                    snap = acc.to_snapshot(rec)
                    if snap is None:
                        continue
                    out = engine.process_snapshot(etf_code, snap)
                    state["etf_ticks"] += 1
                    state["last_indicators"] = out
                    if etf_code in engine.iopv:
                        iopv = engine.iopv[etf_code].calculate_iopv()
                        state["last_iopv"] = None if np.isnan(iopv) else float(iopv)
                        state["last_premium_rate"] = float(engine.iopv[etf_code].get_premium_rate(snap.close))
                    if state["etf_ticks"] >= int(min_etf_ticks):
                        break

    with lock:
        return dict(state)


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--etf", required=True, help="如 560780.SH")
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--min-ticks", type=int, default=3)
    parser.add_argument("--top-components", type=int, default=30)
    args = parser.parse_args()

    result = run_realtime_once(
        etf_code=str(args.etf).upper(),
        seconds=float(args.seconds),
        min_etf_ticks=int(args.min_ticks),
        top_components=int(args.top_components),
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
