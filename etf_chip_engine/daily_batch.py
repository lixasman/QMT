from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.warn_utils import warn_once
from etf_chip_engine.config import CONFIG
from etf_chip_engine.data.xtdata_provider import cleanup_xtdata_dated_files, cleanup_xtdata_trade_date_files, latest_trade_date, prev_trade_date, require_xtdata
from etf_chip_engine.service import IndustryETFChipService


DEFAULT_RETENTION_DAYS = 0


def _resolve_trade_date(date_arg: str) -> str:
    if date_arg and date_arg.lower() not in {"auto", "latest"}:
        return date_arg
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    latest = latest_trade_date(today) or prev_trade_date(today) or today
    if latest != today:
        return latest

    cutoff = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < cutoff:
        return prev_trade_date(today) or today
    return today


def _zones_to_json(v: Any) -> str:
    if v is None:
        return "[]"
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return "[]"
        try:
            obj = json.loads(s)
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            try:
                import ast

                obj2 = ast.literal_eval(s)
                return json.dumps(obj2, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                warn_once(
                    f"zones_parse_failed:{s[:80]}",
                    f"Integration: dense_zones parse failed, downgraded to empty array: {s[:160]}",
                )
                return "[]"
    if isinstance(v, (list, dict)):
        try:
            return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return "[]"
    return "[]"


def _iter_zones(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            obj = json.loads(s)
        except Exception:
            try:
                import ast

                obj = ast.literal_eval(s)
            except Exception:
                return []
    else:
        obj = v
    if not isinstance(obj, list):
        return []
    out: list[dict[str, Any]] = []
    for x in obj:
        if isinstance(x, dict):
            out.append(x)
    return out


def _dpc_peak_density(v: Any) -> float:
    zones = _iter_zones(v)
    peak = 0.0
    for z in zones:
        try:
            d = float(z.get("density", 0.0) or 0.0)
        except Exception:
            d = 0.0
        if d > peak:
            peak = d
    return float(peak)


def _max_density_price(v: Any, *, zone_type: str) -> Optional[float]:
    zones = _iter_zones(v)
    best_d = -1.0
    best_p: Optional[float] = None
    for z in zones:
        if str(z.get("type") or "") != zone_type:
            continue
        try:
            p = float(z.get("price", 0.0) or 0.0)
            d = float(z.get("density", 0.0) or 0.0)
        except Exception:
            continue
        if d > best_d:
            best_d = d
            best_p = p
    return best_p


def _factor_history_dirs() -> list[Path]:
    out: list[Path] = []
    ms_cfg = CONFIG.get("microstructure")
    if isinstance(ms_cfg, dict):
        raw = str(ms_cfg.get("factor_history_dir", "") or "").strip()
        if raw:
            out.append(Path(raw))
    legacy = Path("etf_chip_engine") / "data" / "factor_history"
    if not out:
        out.append(legacy)
    elif legacy not in out:
        out.append(legacy)
    return out


def _count_history_rows(path: Path) -> Optional[int]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        try:
            n = 0
            with path.open("r", encoding="utf-8") as f:
                for _ in f:
                    n += 1
            return int(max(0, n - 1))
        except Exception as e:
            warn_once(
                f"factor_history_read_failed:{str(path)}",
                f"Integration: factor_history read failed, downgraded to 0 days: {path} err={repr(e)}",
                logger_name=__name__,
            )
            return None

    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore

            return int(max(0, pq.ParquetFile(path).metadata.num_rows))
        except Exception as e:
            warn_once(
                f"factor_history_parquet_read_failed:{str(path)}",
                f"Integration: factor_history parquet read failed, downgraded to 0 days: {path} err={repr(e)}",
                logger_name=__name__,
            )
            return None
    return None


def _count_factor_history_days(*, code: str) -> int:
    key = code.replace(".", "_")
    for d in _factor_history_dirs():
        for ext in (".csv", ".parquet"):
            p = d / f"{key}{ext}"
            if not p.exists():
                continue
            rows = _count_history_rows(p)
            if rows is not None:
                return int(max(0, rows))
    return 0


@dataclass(frozen=True)
class _IntegrationPaths:
    out_dir: Path
    chip_dir: Path


def _integration_paths() -> _IntegrationPaths:
    out = Path("output") / "integration"
    chip = out / "chip"
    chip.mkdir(parents=True, exist_ok=True)
    return _IntegrationPaths(out_dir=out, chip_dir=chip)


def _parse_yyyymmdd(v: str) -> Optional[date]:
    if not re.fullmatch(r"\d{8}", str(v)):
        return None
    try:
        return datetime.strptime(str(v), "%Y%m%d").date()
    except Exception:
        return None


def _remove_path(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_dir():
        n_files = 0
        n_bytes = 0
        for f in path.rglob("*"):
            if f.is_file():
                n_files += 1
                try:
                    n_bytes += int(f.stat().st_size)
                except Exception:
                    pass
        shutil.rmtree(path)
        return n_files, n_bytes
    n_bytes = 0
    try:
        n_bytes = int(path.stat().st_size)
    except Exception:
        n_bytes = 0
    path.unlink()
    return 1, n_bytes


def _cleanup_dated_paths(
    *,
    entries: list[tuple[Path, date]],
    keep_days: int,
    today: date,
) -> dict[str, int]:
    keep_i = int(max(int(keep_days), 0))
    if keep_i <= 0:
        return {"checked": int(len(entries)), "removed_paths": 0, "removed_files": 0, "removed_bytes": 0}

    cutoff = today - timedelta(days=keep_i)
    removed_paths = 0
    removed_files = 0
    removed_bytes = 0
    for p, d in entries:
        if d >= cutoff:
            continue
        try:
            c_files, c_bytes = _remove_path(p)
            removed_paths += 1
            removed_files += int(c_files)
            removed_bytes += int(c_bytes)
        except Exception as e:
            warn_once(
                f"retention_cleanup_failed:{str(p)}",
                f"Retention: failed to clean expired files, downgraded skip: path={p} err={repr(e)}",
            )
    return {
        "checked": int(len(entries)),
        "removed_paths": int(removed_paths),
        "removed_files": int(removed_files),
        "removed_bytes": int(removed_bytes),
    }


def _collect_l1_snapshot_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    for d in root.iterdir():
        if not d.is_dir():
            continue
        dd = _parse_yyyymmdd(d.name)
        if dd is not None:
            out.append((d, dd))
    return out


def _collect_chip_snapshot_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    pat = re.compile(r"_(\d{8})\.(?:npz|ema\.json)$")
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = pat.search(p.name)
        if not m:
            continue
        dd = _parse_yyyymmdd(m.group(1))
        if dd is not None:
            out.append((p, dd))
    return out


def _collect_batch_result_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    pat = re.compile(r"^batch_results_(\d{8})(?:_[^.]+)?\.csv$")
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if not m:
            continue
        dd = _parse_yyyymmdd(m.group(1))
        if dd is not None:
            out.append((p, dd))
    return out


def _collect_tick_state_entries(root: Path) -> list[tuple[Path, date]]:
    out: list[tuple[Path, date]] = []
    if not root.exists():
        return out
    pat = re.compile(r"^tick_(\d{8})\.json$")
    for p in root.iterdir():
        if not p.is_file():
            continue
        m = pat.match(p.name)
        if not m:
            continue
        dd = _parse_yyyymmdd(m.group(1))
        if dd is not None:
            out.append((p, dd))
    return out


def _apply_data_retention(
    *,
    keep_days: int = DEFAULT_RETENTION_DAYS,
    today: Optional[date] = None,
    base_dir: str | Path = ".",
) -> dict[str, Any]:
    root = Path(base_dir)
    today_d = today if today is not None else datetime.now().date()
    keep_i = int(max(int(keep_days), 0))

    chip_data_dir = root / "etf_chip_engine" / "data"
    output_dir = root / "output"
    stats: dict[str, Any] = {
        "enabled": bool(keep_i > 0),
        "keep_days": int(keep_i),
        "today": today_d.strftime("%Y%m%d"),
        "project": {},
        "xtdata": {},
    }
    if keep_i <= 0:
        return stats

    project_groups: list[tuple[str, list[tuple[Path, date]]]] = [
        ("l1_snapshots", _collect_l1_snapshot_entries(chip_data_dir / "l1_snapshots")),
        ("chip_snapshots", _collect_chip_snapshot_entries(chip_data_dir / "chip_snapshots")),
        ("batch_results", _collect_batch_result_entries(chip_data_dir)),
        ("integration_batch_results", _collect_batch_result_entries(output_dir / "integration" / "chip")),
        ("tick_download_state", _collect_tick_state_entries(output_dir / "cache" / "chip_tick_download")),
    ]

    for name, entries in project_groups:
        stats["project"][name] = _cleanup_dated_paths(entries=entries, keep_days=keep_i, today=today_d)

    try:
        stats["xtdata"] = cleanup_xtdata_dated_files(keep_days=keep_i, today=today_d)
    except Exception as e:
        warn_once(
            "retention_xtdata_cleanup_failed",
            f"Retention: xtdata cleanup failed, downgraded skip: err={repr(e)}",
        )
        stats["xtdata"] = {
            "enabled": True,
            "keep_days": int(keep_i),
            "removed_files": 0,
            "removed_bytes": 0,
            "error": repr(e),
        }
    return stats


def run_daily_batch(
    *,
    trade_date: str,
    limit: Optional[int] = None,
    codes: Optional[list[str]] = None,
    l1_csv: bool = False,
    force_download: bool = False,
    cleanup_trade_date_tick: bool = False,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    industry_etf_min_a_share_ratio: Optional[float] = None,
    industry_etf_max_constituents: Optional[int] = None,
    liquidity_prefilter_enabled: Optional[bool] = None,
    out: str | Path | None = None,
) -> Path:
    t0 = time.perf_counter()
    require_xtdata()
    td = _resolve_trade_date(str(trade_date))

    svc_cfg: dict[str, object] = {}
    if bool(l1_csv):
        svc_cfg["l1_fallback_csv"] = "1"
    if industry_etf_min_a_share_ratio is not None:
        svc_cfg["industry_etf_min_a_share_ratio"] = float(industry_etf_min_a_share_ratio)
    if industry_etf_max_constituents is not None:
        svc_cfg["industry_etf_max_constituents"] = int(industry_etf_max_constituents)
    if liquidity_prefilter_enabled is not None:
        svc_cfg["liquidity_prefilter_enabled"] = 1 if bool(liquidity_prefilter_enabled) else 0

    svc = IndustryETFChipService(config=(svc_cfg or None))
    t1 = time.perf_counter()
    df = svc.run_daily(td, limit=limit, codes=codes, force_download=bool(force_download))
    t2 = time.perf_counter()

    out_path: Path
    if out is not None and str(out).strip():
        out_path = Path(str(out))
    else:
        if codes and len(codes) == 1:
            out_path = Path("etf_chip_engine") / "data" / f"batch_results_{td}_{codes[0].replace('.', '_')}.csv"
        else:
            out_path = Path("etf_chip_engine") / "data" / f"batch_results_{td}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    t3 = time.perf_counter()

    try:
        paths = _integration_paths()
        p2 = paths.chip_dir / f"batch_results_{td}.csv"
        df2 = df.copy()
        dz = df2.get("dense_zones") if hasattr(df2, "get") else None
        if dz is not None:
            df2["dense_zones_json"] = df2["dense_zones"].map(_zones_to_json)
            df2["dpc_peak_density"] = df2["dense_zones"].map(_dpc_peak_density)
            df2["support_price_max_density"] = df2["dense_zones"].map(lambda v: _max_density_price(v, zone_type="support"))
            df2["resistance_price_max_density"] = df2["dense_zones"].map(lambda v: _max_density_price(v, zone_type="resistance"))
        if "code" in df2.columns:
            df2["chip_engine_days"] = df2["code"].map(lambda c: _count_factor_history_days(code=str(c)))
        df2.to_csv(p2, index=False, encoding="utf-8-sig")
    except Exception:
        pass
    t4 = time.perf_counter()
    cleanup_trade_date_stats: dict[str, Any]
    if bool(cleanup_trade_date_tick):
        try:
            cleanup_trade_date_stats = cleanup_xtdata_trade_date_files(trade_date=str(td))
        except Exception as e:
            warn_once(
                "xtdata_trade_date_cleanup_failed",
                f"XtData: failed to clean same-day tick cache, downgraded skip: trade_date={td} err={repr(e)}",
            )
            cleanup_trade_date_stats = {
                "enabled": True,
                "trade_date": str(td),
                "removed_files": 0,
                "removed_bytes": 0,
                "error": repr(e),
            }
    else:
        cleanup_trade_date_stats = {
            "enabled": False,
            "trade_date": str(td),
            "removed_files": 0,
            "removed_bytes": 0,
            "reason": "disabled",
        }
    t4b = time.perf_counter()
    retention_days_i = int(max(int(retention_days), 0))
    if retention_days_i > 0:
        retention_stats = _apply_data_retention(keep_days=retention_days_i)
    else:
        retention_stats = {
            "enabled": False,
            "keep_days": int(retention_days_i),
            "today": datetime.now().date().strftime("%Y%m%d"),
            "project": {},
            "xtdata": {},
            "reason": "disabled",
        }
    t5 = time.perf_counter()

    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.daily_batch.run_daily_batch",
                "trade_date": str(td),
                "rows": int(len(df)),
                "force_download": bool(force_download),
                "cleanup_trade_date_tick": bool(cleanup_trade_date_tick),
                "retention_days": int(retention_days_i),
                "industry_etf_min_a_share_ratio": (
                    None if industry_etf_min_a_share_ratio is None else float(industry_etf_min_a_share_ratio)
                ),
                "industry_etf_max_constituents": (
                    None if industry_etf_max_constituents is None else int(industry_etf_max_constituents)
                ),
                "liquidity_prefilter_enabled": (
                    None if liquidity_prefilter_enabled is None else bool(liquidity_prefilter_enabled)
                ),
                "xtdata_trade_date_cleanup": cleanup_trade_date_stats,
                "retention": retention_stats,
                "seconds": {
                    "init": round(t1 - t0, 3),
                    "compute": round(t2 - t1, 3),
                    "write_main_csv": round(t3 - t2, 3),
                    "write_integration_csv": round(t4 - t3, 3),
                    "cleanup_xtdata_trade_date": round(t4b - t4, 3),
                    "retention_cleanup": round(t5 - t4b, 3),
                    "total": round(t5 - t0, 3),
                },
            },
            ensure_ascii=False,
        )
    )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="auto")
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", default="0")
    parser.add_argument("--code", default="")
    parser.add_argument("--l1-csv", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--cleanup-trade-date-tick", action="store_true", help="clean same-day xtdata tick files after batch (default: disabled)")
    parser.add_argument(
        "--retention-days",
        default=str(DEFAULT_RETENTION_DAYS),
        help="delete expired historical artifacts only when > 0; default: 0 (disabled)",
    )
    parser.add_argument(
        "--industry-etf-min-a-share-ratio",
        default="",
        help="override service admission filter threshold, e.g. 0.0 to disable A-share ratio filter",
    )
    parser.add_argument(
        "--industry-etf-max-constituents",
        default="",
        help="override service admission filter constituent cap, e.g. 0 to disable",
    )
    parser.add_argument(
        "--liquidity-prefilter-enabled",
        default="",
        help="override liquidity prefilter switch: 1/0/true/false",
    )
    args = parser.parse_args()

    lim = int(str(args.limit).strip() or "0")
    retention_days = int(str(args.retention_days).strip() or str(DEFAULT_RETENTION_DAYS))
    code = str(args.code).strip()
    codes = [code] if code else None
    trade_date = _resolve_trade_date(str(args.date))
    min_a_share_ratio: Optional[float] = None
    if str(args.industry_etf_min_a_share_ratio).strip():
        min_a_share_ratio = float(str(args.industry_etf_min_a_share_ratio).strip())
    max_constituents: Optional[int] = None
    if str(args.industry_etf_max_constituents).strip():
        max_constituents = int(str(args.industry_etf_max_constituents).strip())
    liq_prefilter_enabled: Optional[bool] = None
    if str(args.liquidity_prefilter_enabled).strip():
        s = str(args.liquidity_prefilter_enabled).strip().lower()
        liq_prefilter_enabled = s in {"1", "true", "t", "yes", "y", "on"}
    out_path = run_daily_batch(
        trade_date=trade_date,
        limit=(lim if lim > 0 else None),
        codes=codes,
        l1_csv=bool(args.l1_csv),
        force_download=bool(args.force_download),
        cleanup_trade_date_tick=bool(args.cleanup_trade_date_tick),
        retention_days=int(max(retention_days, 0)),
        industry_etf_min_a_share_ratio=min_a_share_ratio,
        industry_etf_max_constituents=max_constituents,
        liquidity_prefilter_enabled=liq_prefilter_enabled,
        out=(str(args.out).strip() or None),
    )
    print("trade_date", trade_date)
    print("saved", str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

