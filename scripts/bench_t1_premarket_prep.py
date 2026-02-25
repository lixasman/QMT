from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.state_manager import StateManager
from integrations.premarket_prep import ensure_tminus1_ready


def main() -> int:
    now = datetime.now().astimezone()
    st = StateManager("data/state/portfolio.json").load()
    watch_codes = ["512480.SH", "159107.SZ"]
    pos_codes = list(st.positions.keys())

    hot_top = 15
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        hot_top = 0

    t0 = time.perf_counter()
    r = ensure_tminus1_ready(now=now, watch_codes=watch_codes, position_codes=pos_codes, hot_top=hot_top)
    t1 = time.perf_counter()

    obj = {
        "bench": "t1_premarket_prep",
        "now": now.isoformat(timespec="seconds"),
        "hot_top": int(hot_top),
        "result": {
            "t_minus_1": r.t_minus_1,
            "chip_ready": bool(r.chip_ready),
            "hot_csv": r.hot_csv,
            "hot_codes": len(r.hot_codes),
            "sentiment_ready_codes": len(r.sentiment_ready_codes),
        },
        "seconds_total": round(t1 - t0, 3),
    }
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
