import argparse
import os
import sys
import threading
import time
from typing import Any, Optional, Tuple


def _as_sequence_len(x: Any) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (str, bytes)):
        return None
    try:
        return len(x)
    except Exception:
        return None


def _first_tick_from_datas(datas: Any, code: str) -> Optional[Any]:
    if datas is None:
        return None
    if isinstance(datas, dict):
        if code in datas:
            v = datas.get(code)
        else:
            if len(datas) == 1:
                v = next(iter(datas.values()))
            else:
                return None
        if isinstance(v, list) and v:
            return v[0]
        return v
    return datas


def _extract_field(tick: Any, field: str) -> Any:
    if tick is None:
        return None
    if isinstance(tick, dict):
        return tick.get(field)
    try:
        if hasattr(tick, "dtype") and getattr(tick.dtype, "names", None):
            names = set(tick.dtype.names or [])
            if field in names:
                return tick[field]
    except Exception:
        pass
    try:
        return getattr(tick, field)
    except Exception:
        return None


def _field_summary(x: Any) -> str:
    if x is None:
        return "None"
    ln = _as_sequence_len(x)
    if ln is None:
        try:
            return f"{type(x).__name__}={x}"
        except Exception:
            return f"{type(x).__name__}"
    if ln <= 20:
        try:
            return f"{type(x).__name__}(len={ln})={list(x)}"
        except Exception:
            return f"{type(x).__name__}(len={ln})"
    return f"{type(x).__name__}(len={ln})"


def _detect_depth_fields(tick: Any) -> Tuple[Optional[int], Optional[int]]:
    ask = _extract_field(tick, "askPrice")
    bid = _extract_field(tick, "bidPrice")
    return _as_sequence_len(ask), _as_sequence_len(bid)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", default=os.getenv("QMT_PROBE_CODE", "512480.SH"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("QMT_PROBE_TIMEOUT", "15")))
    args = parser.parse_args()

    try:
        from xtquant import xtdata  # type: ignore
    except Exception as e:
        print(f"IMPORT_ERROR: {e}")
        return 2

    code = args.code
    evt = threading.Event()
    holder = {"tick": None, "datas": None, "error": None}

    def on_data(datas):
        if evt.is_set():
            return
        holder["datas"] = datas
        tick = _first_tick_from_datas(datas, code)
        holder["tick"] = tick
        evt.set()

    try:
        seq = xtdata.subscribe_quote(code, period="tick", count=0, callback=on_data)
    except Exception as e:
        print(f"SUBSCRIBE_ERROR: {e}")
        return 3

    def run_loop():
        try:
            xtdata.run()
        except Exception as e:
            holder["error"] = e
            evt.set()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    got = evt.wait(timeout=args.timeout)

    try:
        xtdata.unsubscribe_quote(seq)
    except Exception:
        pass

    if not got:
        print(f"TIMEOUT: no tick received within {args.timeout}s, code={code}, seq={seq}")
        return 4

    if holder["error"] is not None and holder["tick"] is None:
        print(f"RUN_ERROR: {holder['error']}")
        return 5

    tick = holder["tick"]

    ask = _extract_field(tick, "askPrice")
    bid = _extract_field(tick, "bidPrice")
    ask_len, bid_len = _detect_depth_fields(tick)

    print(f"CODE={code}")
    print(f"TICK_TYPE={type(tick).__name__}")
    if hasattr(tick, "dtype") and getattr(tick.dtype, "names", None):
        print(f"DTYPE_FIELDS={list(tick.dtype.names)}")
    else:
        if isinstance(tick, dict):
            print(f"DICT_KEYS={sorted(list(tick.keys()))}")

    print(f"askPrice={_field_summary(ask)}")
    print(f"bidPrice={_field_summary(bid)}")
    print(f"ask_depth_len={ask_len}")
    print(f"bid_depth_len={bid_len}")

    if ask_len is None or bid_len is None:
        print("RESULT=UNKNOWN")
        return 0

    if ask_len >= 5 and bid_len >= 5:
        print("RESULT=DEPTH_5_OR_MORE")
        return 0

    print("RESULT=DEPTH_NOT_5")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

