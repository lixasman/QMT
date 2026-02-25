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
from etf_chip_engine.data.xtdata_provider import cleanup_xtdata_dated_files, cleanup_xtdata_trade_date_files, latest_trade_date, prev_trade_date, require_xtdata
from etf_chip_engine.service import IndustryETFChipService


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
                warn_once(f"zones_parse_failed:{s[:80]}", f"Integration: dense_zones 解析失败，已降级为空数组: {s[:160]}")
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


def _count_factor_history_days(*, code: str) -> int:
    p = Path("etf_chip_engine") / "data" / "factor_history" / f"{code.replace('.', '_')}.csv"
    if not p.exists():
        return 0
    try:
        n = 0
        with p.open("r", encoding="utf-8") as f:
            for _ in f:
                n += 1
        return int(max(0, n - 1))
    except Exception as e:
        warn_once(f"factor_history_read_failed:{str(p)}", f"Integration: factor_history 读取失败，已降级为 0 天: {p} err={repr(e)}")
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
                f"Retention: 清理过期文件失败，已降级跳过: path={p} err={repr(e)}",
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
    keep_days: int = 365,
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
        warn_once("retention_xtdata_cleanup_failed", f"Retention: xtdata 清理失败，已降级跳过: err={repr(e)}")
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
    retention_days: int = 365,
    out: str | Path | None = None,
) -> Path:
    t0 = time.perf_counter()
    require_xtdata()
    td = _resolve_trade_date(str(trade_date))

    svc = IndustryETFChipService(config={"l1_fallback_csv": "1"} if bool(l1_csv) else None)
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
    try:
        cleanup_trade_date_stats = cleanup_xtdata_trade_date_files(trade_date=str(td))
    except Exception as e:
        warn_once("xtdata_trade_date_cleanup_failed", f"XtData: 清理当日 tick 缓存失败，已降级跳过: trade_date={td} err={repr(e)}")
        cleanup_trade_date_stats = {
            "enabled": True,
            "trade_date": str(td),
            "removed_files": 0,
            "removed_bytes": 0,
            "error": repr(e),
        }
    t4b = time.perf_counter()
    retention_stats = _apply_data_retention(keep_days=int(retention_days))
    t5 = time.perf_counter()

    print(
        json.dumps(
            {
                "timing": "etf_chip_engine.daily_batch.run_daily_batch",
                "trade_date": str(td),
                "rows": int(len(df)),
                "force_download": bool(force_download),
                "retention_days": int(retention_days),
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
    parser.add_argument("--retention-days", default="365")
    args = parser.parse_args()

    lim = int(str(args.limit).strip() or "0")
    retention_days = int(str(args.retention_days).strip() or "365")
    code = str(args.code).strip()
    codes = [code] if code else None
    trade_date = _resolve_trade_date(str(args.date))
    out_path = run_daily_batch(
        trade_date=trade_date,
        limit=(lim if lim > 0 else None),
        codes=codes,
        l1_csv=bool(args.l1_csv),
        force_download=bool(args.force_download),
        retention_days=int(max(retention_days, 0)),
        out=(str(args.out).strip() or None),
    )
    print("trade_date", trade_date)
    print("saved", str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
