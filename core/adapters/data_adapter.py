from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import numpy as np

from ..interfaces import Bar, DataAdapter, InstrumentInfo, TickSnapshot
from ..price_utils import limit_down_price, limit_up_price
from ..constants import L1_STALE_THRESHOLD_SEC
from ..enums import DataQuality
from ..xtdata_parsing import xtdata_field_dict_to_df
from ..warn_utils import warn_once


try:
    from xtquant import xtdata  # type: ignore
except Exception:
    xtdata = None


class XtDataAdapter(DataAdapter):
    def __init__(self) -> None:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")

    def get_snapshot(self, etf_code: str) -> TickSnapshot:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")
        ticks = xtdata.get_full_tick([etf_code])
        if not isinstance(ticks, dict) or etf_code not in ticks:
            raise RuntimeError(f"full tick missing: {etf_code}")
        t = ticks[etf_code] or {}
        ts = t.get("time") or t.get("timestamp")
        ts_dt = datetime.now()
        if isinstance(ts, (int, float)):
            v = float(ts)
            if v > 1e12:
                ts_dt = datetime.fromtimestamp(v / 1000.0)
            else:
                ts_dt = datetime.fromtimestamp(v)
        ask = t.get("askPrice")
        bid = t.get("bidPrice")
        ask1 = float(ask[0]) if isinstance(ask, (list, tuple)) and ask else float(t.get("ask1") or 0.0)
        bid1 = float(bid[0]) if isinstance(bid, (list, tuple)) and bid else float(t.get("bid1") or 0.0)
        askv = t.get("askVol") or t.get("askVolume") or t.get("askQty") or t.get("ask1_vol")
        bidv = t.get("bidVol") or t.get("bidVolume") or t.get("bidQty") or t.get("bid1_vol")
        ask1_vol = int(askv[0]) if isinstance(askv, (list, tuple)) and askv else int(askv or 0)
        bid1_vol = int(bidv[0]) if isinstance(bidv, (list, tuple)) and bidv else int(bidv or 0)
        dq = DataQuality.OK
        if ts is None:
            dq = DataQuality.MISSING
        else:
            staleness = (datetime.now() - ts_dt).total_seconds()
            if staleness > float(L1_STALE_THRESHOLD_SEC):
                dq = DataQuality.STALE
        return TickSnapshot(
            timestamp=ts_dt,
            last_price=float(t.get("lastPrice") or t.get("price") or 0.0),
            volume=int(t.get("volume") or 0),
            amount=float(t.get("amount") or 0.0),
            ask1_price=ask1,
            bid1_price=bid1,
            ask1_vol=ask1_vol,
            bid1_vol=bid1_vol,
            iopv=(None if t.get("iopv") is None else float(t.get("iopv"))),
            stock_status=int(t.get("stockStatus") or 0),
            data_quality=dq,
        )

    def get_bars(self, etf_code: str, period: str, count: int) -> list[Bar]:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")
        data = xtdata.get_market_data(
            field_list=["time", "open", "high", "low", "close", "volume", "amount"],
            stock_list=[etf_code],
            period=period,
            count=int(count),
        )
        if data is None:
            return []
        try:
            df = xtdata_field_dict_to_df(data, stock_code=etf_code, fields=["open", "high", "low", "close", "volume", "amount"])
            if df is not None and not df.empty:
                def _to_dt(ts: float) -> datetime:
                    v = int(ts)
                    if v <= 0:
                        return datetime.now()
                    if 20_000_000_000_00 <= v <= 29_999_999_999_99:
                        s = str(v)
                        try:
                            return datetime.strptime(s[:14], "%Y%m%d%H%M%S")
                        except Exception:
                            return datetime.now()
                    if v > 1_000_000_000_000:
                        return datetime.fromtimestamp(v / 1000.0)
                    return datetime.fromtimestamp(v)

                bars: list[Bar] = []
                for _, r in df.iterrows():
                    bars.append(
                        Bar(
                            time=_to_dt(float(r.get("time") or 0.0)),
                            open=float(r.get("open") or 0.0),
                            high=float(r.get("high") or 0.0),
                            low=float(r.get("low") or 0.0),
                            close=float(r.get("close") or 0.0),
                            volume=float(r.get("volume") or 0.0),
                            amount=float(r.get("amount") or 0.0),
                        )
                    )
                return bars

            warn_once(
                f"xtdata_get_bars_unsupported:{etf_code}:{period}",
                f"Data: get_bars 返回结构不支持，已降级为空数据 etf={etf_code} period={period} count={int(count)}",
            )
            return []
        except Exception as e:
            warn_once(
                f"xtdata_get_bars_failed:{etf_code}:{period}",
                f"Data: get_bars 解析失败，已降级为空数据 etf={etf_code} period={period} count={int(count)} err={repr(e)}",
            )
            return []

    def get_instrument_info(self, etf_code: str) -> InstrumentInfo:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")
        info: dict[str, Any] = {}
        fn = getattr(xtdata, "get_instrument_detail", None)
        if callable(fn):
            info = fn(etf_code) or {}
        name = str(info.get("InstrumentName") or info.get("name") or etf_code)
        prev_close = float(info.get("PreClose") or info.get("prev_close") or 0.0)
        up = limit_up_price(prev_close)
        down = limit_down_price(prev_close)
        return InstrumentInfo(etf_code=etf_code, instrument_name=name, prev_close=prev_close, limit_up=up, limit_down=down)

    def subscribe_quote(self, etf_code: str, callback: Any) -> None:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")
        fn2 = getattr(xtdata, "subscribe_quote", None)
        if callable(fn2):
            fn2(etf_code, period="tick", count=0, callback=callback)
            return
        raise RuntimeError("xtdata subscribe api not available")

    def get_auction_volume(self, etf_code: str, date: str) -> float:
        if xtdata is None:
            raise RuntimeError("xtquant.xtdata is not available")
        start_time = f"{date}091500"
        end_time = f"{date}092500"
        data = xtdata.get_market_data(
            field_list=["stockStatus", "volume"],
            stock_list=[etf_code],
            period="tick",
            start_time=start_time,
            end_time=end_time,
            count=-1,
        )
        if not isinstance(data, dict) or etf_code not in data or data[etf_code] is None:
            raise RuntimeError(f"auction tick missing: {etf_code} {date}")
        arr = data[etf_code]
        names = getattr(arr, "dtype", None)
        if names is None or getattr(names, "names", None) is None:
            raise RuntimeError("auction tick dtype not supported")
        status_name = "stockStatus" if "stockStatus" in arr.dtype.names else None
        volume_name = "volume" if "volume" in arr.dtype.names else None
        if status_name is None or volume_name is None:
            raise RuntimeError("auction tick missing fields")
        last_vol: Optional[float] = None
        for row in arr:
            st = int(row[status_name])
            if st == 12:
                last_vol = float(row[volume_name])
        if last_vol is None:
            raise RuntimeError("auction tick no auction status")
        return float(last_vol)
