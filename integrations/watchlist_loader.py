from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from core.warn_utils import warn_once
from entry.types import WatchlistItem


def normalize_etf_code(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return s
    if "." in s:
        return s
    if not (len(s) == 6 and s.isdigit()):
        return s
    if s.startswith(("5", "6", "9")):
        return f"{s}.SH"
    return f"{s}.SZ"


def code6(code: str) -> str:
    s = str(code or "").strip().upper()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    return s if len(s) == 6 and s.isdigit() else ""


def _today_yyyymmdd(now: Optional[datetime] = None) -> str:
    dt = datetime.now().astimezone() if now is None else now.astimezone()
    return dt.strftime("%Y%m%d")


def _pick_latest_before(files: Iterable[Path], *, today: str, date_re: str) -> Optional[Path]:
    best_date = ""
    best: Optional[Path] = None
    for p in files:
        m = re.search(date_re, p.name)
        if not m:
            continue
        d = m.group(1)
        if not (len(d) == 8 and d.isdigit()):
            continue
        if d >= today:
            continue
        if d > best_date:
            best_date = d
            best = p
    return best


def _parse_float(v: object) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except Exception:
        return None


def _parse_int(v: object) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return int(float(v))  # type: ignore[arg-type]
    except Exception:
        return None


def _load_chip_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                code = normalize_etf_code(str(row.get("code") or ""))
                if not code:
                    continue
                out[code] = {str(k): ("" if v is None else str(v)) for k, v in row.items()}
    except Exception as e:
        warn_once(f"chip_csv_read_failed:{str(path)}", f"Integration: 读取 chip CSV 失败，已降级为空数据: {path} err={repr(e)}")
        return {}
    return out


def _load_sentiment_obj(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        warn_once(f"sentiment_json_parse_failed:{str(path)}", f"Integration: 解析 sentiment JSON 失败，已降级为空数据: {path}")
        return {}


def _micro_caution(*, vpin_rank: Optional[float], ofi_daily: Optional[float]) -> bool:
    mc = False
    if vpin_rank is not None and float(vpin_rank) > 0.70:
        mc = True
    if ofi_daily is not None and float(ofi_daily) < 0:
        mc = True
    return mc


@dataclass(frozen=True)
class WatchlistLoadResult:
    items: list[WatchlistItem]
    ext_factors: dict[str, dict[str, object]]
    chip_csv_path: Optional[str] = None


def load_watchlist_items(
    *,
    etf_codes: Iterable[str],
    now: Optional[datetime] = None,
    integration_dir: str | Path = "output/integration",
) -> WatchlistLoadResult:
    today = _today_yyyymmdd(now)
    base = Path(integration_dir)
    chip_dir = base / "chip"
    fin_dir = base / "finintel"

    chip_files = sorted(chip_dir.glob("batch_results_*.csv"), reverse=True) if chip_dir.exists() else []
    chip_csv = _pick_latest_before(chip_files, today=today, date_re=r"batch_results_(\d{8})\.csv$")
    chip_rows = _load_chip_rows(chip_csv) if chip_csv is not None else {}
    chip_date = ""
    if chip_csv is not None:
        m = re.search(r"batch_results_(\d{8})\.csv$", chip_csv.name)
        chip_date = m.group(1) if m else ""
    else:
        warn_once(f"chip_csv_missing:{today}", f"Integration: 未找到 {chip_dir}\\batch_results_*.csv（仅使用 today={today} 之前的文件），筹码/微观因子将降级为缺失")

    out_items: list[WatchlistItem] = []
    ext: dict[str, dict[str, object]] = {}

    for raw_code in list(etf_codes):
        c0 = str(raw_code).strip()
        if not c0:
            continue
        cn = normalize_etf_code(c0)
        c6 = code6(c0)

        s_path = None
        if fin_dir.exists() and c6:
            candidates = list(fin_dir.glob(f"sentiment_{c6}_*.json"))
            best = _pick_latest_before(candidates, today=today, date_re=rf"sentiment_{re.escape(c6)}_(\d{{8}})\.json$")
            s_path = best
        if s_path is None and c6:
            warn_once(
                f"sentiment_missing:{c6}:{today}",
                f"Integration: 未找到 sentiment JSON（仅使用 today={today} 之前的文件），etf={cn} dir={fin_dir}，情绪分数将降级为默认值",
            )
        s_obj = _load_sentiment_obj(s_path) if s_path is not None else {}

        score100 = _parse_int(s_obj.get("sentiment_score_100"))
        score01 = _parse_float(s_obj.get("sentiment_score_01"))
        if score100 is None:
            score100 = 50
        if score01 is None:
            score01 = 0.5

        r = chip_rows.get(cn) or {}
        if not r and chip_csv is not None:
            warn_once(
                f"chip_row_missing:{cn}:{chip_date}",
                f"Integration: chip CSV 未找到该 ETF 行，etf={cn} file={chip_csv.name}，筹码/微观因子将降级为缺失",
            )
        profit_ratio = _parse_float(r.get("profit_ratio"))
        if profit_ratio is None:
            profit_ratio = 0.0

        nearest_resistance = _parse_float(r.get("resistance_price_max_density"))
        vpin_rank = _parse_float(r.get("ms_vpin_rank"))
        ofi_daily = _parse_float(r.get("ms_ofi_daily_z"))
        vs_max = _parse_float(r.get("ms_vs_max_logz"))

        item = WatchlistItem(
            etf_code=str(cn),
            sentiment_score=int(score100),
            profit_ratio=float(profit_ratio),
            nearest_resistance=(None if nearest_resistance is None else float(nearest_resistance)),
            micro_caution=_micro_caution(vpin_rank=vpin_rank, ofi_daily=ofi_daily),
            vpin_rank=(None if vpin_rank is None else float(vpin_rank)),
            ofi_daily=(None if ofi_daily is None else float(ofi_daily)),
            vs_max=(None if vs_max is None else float(vs_max)),
            extra={"sentiment_score_01": float(score01)},
        )
        out_items.append(item)

        dense_zones_json = str(r.get("dense_zones_json") or "[]")
        support = _parse_float(r.get("support_price_max_density"))
        chip_days = _parse_int(r.get("chip_engine_days"))
        dpc_peak = _parse_float(r.get("dpc_peak_density"))

        ext_payload: dict[str, object] = {
            "sentiment_score_01": float(score01),
            "profit_ratio": float(profit_ratio),
            "dpc_peak_density": float(dpc_peak or 0.0),
            "dense_zones_json": dense_zones_json if dense_zones_json else "[]",
            "support_price_max_density": support,
            "ms_vs_max_logz": vs_max,
            "chip_engine_days": int(chip_days or 0),
            "chip_trade_date": chip_date,
        }

        ext[str(cn)] = dict(ext_payload)
        if cn and str(c0) != str(cn):
            ext[str(c0)] = dict(ext_payload)

    return WatchlistLoadResult(items=out_items, ext_factors=ext, chip_csv_path=(None if chip_csv is None else str(chip_csv)))
