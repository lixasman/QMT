import argparse
import threading
import time
from datetime import datetime
from typing import Any, Optional


def _now_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _latest_trade_date(xtdata: Any, market: str = "SH") -> str:
    end = _now_yyyymmdd()
    dates = xtdata.get_trading_dates(market, start_time="", end_time=end, count=1)
    if isinstance(dates, list) and dates:
        v = dates[-1]
        s = str(v)
        if s.isdigit() and len(s) >= 8:
            return s[:8]
    return end


def _brief(x: Any) -> str:
    if x is None:
        return "None"
    if isinstance(x, dict):
        return f"dict(keys={list(x.keys())[:10]})"
    try:
        ln = len(x)
        return f"{type(x).__name__}(len={ln})"
    except Exception:
        return type(x).__name__


def _extract_tick_len(raw: Any, code: str) -> Optional[int]:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    v = raw.get(code)
    if v is None and len(raw) == 1:
        v = next(iter(raw.values()))
    if v is None:
        return None
    if isinstance(v, dict) and v:
        v = next(iter(v.values()))
    try:
        return len(v)
    except Exception:
        return None


def _extract_kline_len(raw: Any, code: str) -> Optional[int]:
    if raw is None or not isinstance(raw, dict) or not raw:
        return None
    df = None
    for v in raw.values():
        df = v
        break
    try:
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            return None
        if code in df.index:
            row = df.loc[code]
        else:
            row = df.iloc[0]
        return int(row.shape[0])
    except Exception:
        return None


def _print_tick_head(raw: Any, code: str, n: int = 3) -> None:
    if raw is None or not isinstance(raw, dict) or not raw:
        print("TICK_HEAD=None")
        return
    v = raw.get(code)
    if v is None and len(raw) == 1:
        v = next(iter(raw.values()))
    if v is None:
        print("TICK_HEAD=None")
        return
    if isinstance(v, dict) and v:
        v = next(iter(v.values()))
    try:
        head = v[:n]
    except Exception:
        head = v
    print(f"TICK_HEAD={head}")


def _probe_subscribe_tick(xtdata: Any, code: str, timeout_s: float) -> None:
    evt = threading.Event()
    holder = {"datas": None, "tick": None, "run_error": None}

    def on_data(datas):
        if evt.is_set():
            return
        holder["datas"] = datas
        tick = None
        if isinstance(datas, dict):
            if code in datas:
                tick = datas.get(code)
            elif len(datas) == 1:
                tick = next(iter(datas.values()))
        holder["tick"] = tick
        evt.set()

    seq = xtdata.subscribe_quote(code, period="tick", count=0, callback=on_data)
    print(f"SUBSCRIBE_SEQ={seq}")

    def _run():
        try:
            xtdata.run()
        except Exception as e:
            holder["run_error"] = e
            evt.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    got = evt.wait(timeout=timeout_s)

    try:
        xtdata.unsubscribe_quote(seq)
    except Exception:
        pass

    if not got:
        print(f"SUBSCRIBE_RESULT=TIMEOUT timeout_s={timeout_s}")
        return
    if holder["run_error"] is not None and holder["tick"] is None:
        print(f"SUBSCRIBE_RESULT=RUN_ERROR err={repr(holder['run_error'])}")
        return
    tick = holder["tick"]
    if isinstance(tick, list) and tick:
        tick0 = tick[0]
    else:
        tick0 = tick
    print(f"SUBSCRIBE_RESULT=OK tick_type={type(tick0).__name__} tick0={tick0}")


def _probe_history_tick(xtdata: Any, code: str, trade_date: str, count: int, do_download: bool) -> None:
    start_market = f"{trade_date}093000"
    end_market = f"{trade_date}151000"
    start_local = f"{trade_date}000000"
    end_local = f"{trade_date}235959"

    fields = ["time", "lastPrice", "high", "low", "amount", "volume", "bidPrice1", "bidVol1", "askPrice1", "askVol1"]

    raw_market = xtdata.get_market_data(
        field_list=fields,
        stock_list=[code],
        period="tick",
        start_time=start_market,
        end_time=end_market,
        count=count,
        dividend_type="none",
        fill_data=True,
    )
    ln_market = _extract_tick_len(raw_market, code)
    print(f"MARKET_TICK raw={_brief(raw_market)} tick_len={ln_market}")
    _print_tick_head(raw_market, code)

    raw_local_before = xtdata.get_local_data(
        field_list=fields,
        stock_list=[code],
        period="tick",
        start_time=start_local,
        end_time=end_local,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    ln_local_before = _extract_tick_len(raw_local_before, code)
    print(f"LOCAL_TICK_BEFORE raw={_brief(raw_local_before)} tick_len={ln_local_before}")

    raw_1m = xtdata.get_market_data(
        field_list=["time", "open", "high", "low", "close", "volume", "amount"],
        stock_list=[code],
        period="1m",
        start_time=start_market,
        end_time=end_market,
        count=-1,
        dividend_type="none",
        fill_data=True,
    )
    ln_1m = _extract_kline_len(raw_1m, code)
    print(f"MARKET_1M raw={_brief(raw_1m)} bar_count={ln_1m}")

    if do_download:
        try:
            xtdata.download_history_data(code, "tick", trade_date, trade_date, None)
        except TypeError:
            try:
                xtdata.download_history_data(code, "tick", trade_date, trade_date)
            except TypeError:
                xtdata.download_history_data(code, "tick", trade_date)
        raw_local_after = xtdata.get_local_data(
            field_list=fields,
            stock_list=[code],
            period="tick",
            start_time=start_local,
            end_time=end_local,
            count=-1,
            dividend_type="none",
            fill_data=True,
        )
        ln_local_after = _extract_tick_len(raw_local_after, code)
        print(f"LOCAL_TICK_AFTER raw={_brief(raw_local_after)} tick_len={ln_local_after}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default="510300.SH")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--count", type=int, default=3000)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--subscribe", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    try:
        from xtquant import xtdata  # type: ignore
    except Exception as e:
        print(f"IMPORT_ERROR: {e}")
        return 2

    code = str(args.code).strip().upper()
    trade_date = str(args.trade_date).strip()
    if not trade_date:
        trade_date = _latest_trade_date(xtdata, "SH")

    print(f"CODE={code}")
    print(f"TRADE_DATE={trade_date}")

    try:
        _ = xtdata.get_instrument_detail(code, False)
        print("MINIQMT_CONNECT=OK")
    except Exception as e:
        print(f"MINIQMT_CONNECT=ERROR err={repr(e)}")

    try:
        _probe_history_tick(xtdata, code, trade_date, int(args.count), bool(args.download))
    except Exception as e:
        print(f"HISTORY_PROBE_ERROR err={repr(e)}")

    if args.subscribe:
        try:
            _probe_subscribe_tick(xtdata, code, float(args.timeout))
        except Exception as e:
            print(f"SUBSCRIBE_PROBE_ERROR err={repr(e)}")

    time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
