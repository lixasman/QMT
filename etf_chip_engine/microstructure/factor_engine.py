"""M9 — Microstructure Factor Engine (V2.1 orchestrator).

Executes the full M0-M8 pipeline for a single ETF on a single day.

V2.1 changes
-------------
- Integrates M0 (Preprocessor), M1 (Microprice), M7 (Orthogonalizer).
- Three-level BVC with Microprice input.
- ADV_60-based dynamic VPIN buckets.
- Kyle's Lambda via Microprice with intercept.
- RV Bipower from close, VWAP deviation.
- Volume Surprise log(V/ADV_60).
- Expanded diagnostics.
- Factor history schema updated.
- Backward-compatible CSV feature keys preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from etf_chip_engine.microstructure.auxiliary_factors import (
    kyle_lambda,
    rv_bipower,
    volume_surprise,
    vwap_deviation,
)
from etf_chip_engine.microstructure.bvc import BulkVolumeClassifier
from etf_chip_engine.microstructure.feature_pipeline import FeaturePipeline
from etf_chip_engine.microstructure.microprice import compute_microprice
from etf_chip_engine.microstructure.ofi import ContStoikovOFI
from etf_chip_engine.microstructure.orthogonalizer import FactorOrthogonalizer
from etf_chip_engine.microstructure.preprocessor import SnapshotPreprocessor
from etf_chip_engine.microstructure.vpin import VPINCalculator


# ── Helpers ───────────────────────────────────────────────────────────────

def _normalize_code_key(code: str) -> str:
    return (code or "").strip().upper().replace(".", "_")


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    if path.suffix.lower() == ".parquet":
        try:
            return pd.read_parquet(path)
        except Exception:
            csv_path = path.with_suffix(".csv")
            if csv_path.exists():
                try:
                    return pd.read_csv(csv_path, dtype={"trade_date": str})
                except Exception:
                    return pd.DataFrame()
            return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"trade_date": str})
    except Exception:
        return pd.DataFrame()


def _write_history(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        try:
            df.to_parquet(path, index=False)
            return
        except Exception:
            csv_path = path.with_suffix(".csv")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            return
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ── V2.1 History Schema ──────────────────────────────────────────────────
# Columns stored in per-ETF factor_history files.
_HISTORY_COLUMNS = [
    "vpin_raw", "vpin_filtered", "vpin_max", "delta_vpin",
    "ofi_daily", "ofi_240", "ofi_skew", "ofi_95th", "ofi_price_divergence",
    "ofi_20", "ofi_60", "ofi_am_sum", "ofi_pm_sum", "ofi_pos_ratio",
    "kyle_lambda", "rv_bipower", "jump_ratio",
    "vwap_dev", "volume_surprise", "bvc_quality",
    "n_buckets_actual", "bucket_size",
    # Legacy compat (V1 callers may still reference these)
    "vpin_orthogonalized",
]


class MicrostructureEngine:
    """V2.1 factor engine orchestrating M0 → M8.

    Parameters (via config dict)
    ----------------------------
    microstructure.bvc_lookback, bvc_min_periods, bvc_k_max
    microstructure.vpin_n_buckets, vpin_window, vpin_premium_*
    microstructure.kyle_lambda_window
    microstructure.feature_history_days, max_history_days
    microstructure.factor_history_dir
    microstructure.ortho_min_history
    """

    def __init__(self, config: dict[str, object]):
        ms_cfg = config.get("microstructure") if isinstance(config.get("microstructure"), dict) else {}
        ms_cfg = ms_cfg or {}

        self.preprocessor = SnapshotPreprocessor()

        self.bvc = BulkVolumeClassifier(
            lookback=int(ms_cfg.get("bvc_lookback", 100)),
            min_periods=int(ms_cfg.get("bvc_min_periods", 20)),
            k_max=int(ms_cfg.get("bvc_k_max", 10)),
        )
        self.vpin = VPINCalculator(
            n_buckets=int(ms_cfg.get("vpin_n_buckets", 50)),
            window=int(ms_cfg.get("vpin_window", 50)),
            premium_threshold=float(ms_cfg.get("vpin_premium_threshold", 0.003)),
            premium_saturate=float(ms_cfg.get("vpin_premium_saturate", 0.01)),
        )
        self.ofi = ContStoikovOFI()
        self.orthogonalizer = FactorOrthogonalizer(
            min_history=int(ms_cfg.get("ortho_min_history", 20)),
        )
        self.pipeline = FeaturePipeline(
            history_days=int(ms_cfg.get("feature_history_days", 60)),
        )
        self.kyle_lambda_window = int(ms_cfg.get("kyle_lambda_window", 1200))
        self.max_history_days = int(ms_cfg.get("max_history_days", 60))
        self.history_dir = Path(
            str(ms_cfg.get(
                "factor_history_dir",
                Path("etf_chip_engine") / "data" / "factor_history",
            ))
        )

    # ── History I/O ───────────────────────────────────────────────────

    def _history_path(self, etf_code: str) -> Path:
        key = _normalize_code_key(etf_code)
        return self.history_dir / f"{key}.parquet"

    def _load_history_arrays(self, etf_code: str) -> dict[str, np.ndarray]:
        """Load rolling factor history for time-series normalization."""
        df = _read_history(self._history_path(etf_code))
        out: dict[str, np.ndarray] = {}
        if df is None or df.empty:
            return out

        for col in _HISTORY_COLUMNS:
            # Try both bare name and ms_ prefix for legacy compat
            for candidate in (col, f"ms_{col}"):
                if candidate in df.columns:
                    out[col] = pd.to_numeric(
                        df[candidate], errors="coerce"
                    ).to_numpy(dtype=np.float64)
                    break
        return out

    def _load_yesterday_meta(self, etf_code: str) -> dict[str, object]:
        """Load yesterday's metadata for ΔVPIN comparability."""
        df = _read_history(self._history_path(etf_code))
        if df is None or df.empty:
            return {}
        last = df.iloc[-1]
        return {
            "vpin_raw": float(last.get("vpin_raw", float("nan"))) if "vpin_raw" in df.columns else None,
            "n_buckets_actual": int(last.get("n_buckets_actual", 0)) if "n_buckets_actual" in df.columns else None,
            "bucket_size": float(last.get("bucket_size", 0)) if "bucket_size" in df.columns else None,
        }

    def _append_history(self, etf_code: str, trade_date: str, raw: dict[str, float]) -> None:
        """Append today's raw factors to the rolling history file."""
        path = self._history_path(etf_code)
        df = _read_history(path)

        # Normalize legacy column names
        if df is not None and not df.empty:
            rename_map: dict[str, str] = {}
            for c in df.columns:
                if c.startswith("ms_"):
                    base = c[3:]
                    if base not in df.columns:
                        rename_map[c] = base
            if rename_map:
                df = df.rename(columns=rename_map)

        row = {"trade_date": str(trade_date)}
        row.update({k: float(v) if v is not None and np.isfinite(v) else float("nan") for k, v in raw.items()})
        df2 = (
            pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            if df is not None and not df.empty
            else pd.DataFrame([row])
        )
        if "trade_date" in df2.columns:
            df2["trade_date"] = df2["trade_date"].astype(str)
            df2 = df2.drop_duplicates(subset=["trade_date"], keep="last")
            df2 = df2.sort_values("trade_date")
        if df2.shape[0] > self.max_history_days:
            df2 = df2.tail(self.max_history_days).reset_index(drop=True)
        _write_history(df2, path)

    # ── Main pipeline ─────────────────────────────────────────────────

    def process_daily(
        self,
        *,
        etf_code: str,
        trade_date: str,
        snapshots: pd.DataFrame,
        premium_rates: Optional[pd.Series] = None,
        adv_60: Optional[float] = None,
    ) -> dict[str, object]:
        """Execute the full M0→M8 pipeline for one ETF on one day.

        Parameters
        ----------
        etf_code, trade_date : str
            Identifiers.
        snapshots : pd.DataFrame
            Raw XtQuant snapshots.
        premium_rates : pd.Series or None
            Premium/discount rates aligned to snapshots.
        adv_60 : float or None
            60-day average daily volume.  If None, VPIN falls back to
            intraday bucket sizing.

        Returns
        -------
        dict with keys: ``raw``, ``features``, ``diagnostics``.
        """
        # ── Step 0: M0 Preprocessor ───────────────────────────────────
        clean, quality_meta = self.preprocessor.process(snapshots)

        if clean is None or clean.empty:
            return self._empty_result(quality_meta)

        # ── Step 1: M1 Microprice ─────────────────────────────────────
        microprice = compute_microprice(clean)

        # ── Step 2: M2 BVC ────────────────────────────────────────────
        bvc_result = self.bvc.classify(clean, microprice)
        # Write v_buy/v_sell into clean for VPIN bucket builder
        clean = clean.copy()
        clean["v_buy"] = bvc_result.v_buy
        clean["v_sell"] = bvc_result.v_sell

        # ── Step 3: M3 VPIN ───────────────────────────────────────────
        yesterday_meta = self._load_yesterday_meta(etf_code)
        vpin_result = self.vpin.compute(
            clean,
            premium_rates=premium_rates,
            adv_60=adv_60,
            vpin_yesterday=yesterday_meta.get("vpin_raw"),
            n_buckets_yesterday=yesterday_meta.get("n_buckets_actual"),
            bucket_size_yesterday=yesterday_meta.get("bucket_size"),
        )

        # ── Step 4: M4 OFI ───────────────────────────────────────────
        limit_mask = clean["limit_locked_mask"] if "limit_locked_mask" in clean.columns else None
        valid_cont_mask = clean["valid_continuous_mask"] if "valid_continuous_mask" in clean.columns else None
        ofi_result = self.ofi.compute(
            clean,
            microprice=microprice,
            limit_locked_mask=limit_mask,
            valid_mask=valid_cont_mask,
        )

        # ── Step 5: M5 Kyle's Lambda ─────────────────────────────────
        lambda_result = kyle_lambda(
            microprice, bvc_result.v_buy, bvc_result.v_sell,
            window=self.kyle_lambda_window,
        )

        # ── Step 6: M6 Auxiliary factors ──────────────────────────────
        rv = rv_bipower(clean["close"].to_numpy(dtype=np.float64, copy=False))
        vwap = vwap_deviation(clean)

        total_volume = float(clean["volume"].sum())
        vs = volume_surprise(total_volume, adv_60 if adv_60 else 0.0)

        # ── Step 7: Assemble raw factors ──────────────────────────────
        raw: dict[str, float] = {
            # VPIN
            "vpin_raw": float(vpin_result.vpin_raw),
            "vpin_filtered": float(vpin_result.vpin_filtered),
            "vpin_max": float(vpin_result.vpin_max),
            "delta_vpin": float(vpin_result.delta_vpin),
            # OFI
            "ofi_daily": float(ofi_result.ofi_daily),
            "ofi_20": float(ofi_result.ofi_20),
            "ofi_60": float(ofi_result.ofi_60),
            "ofi_240": float(ofi_result.ofi_240),
            "ofi_skew": float(ofi_result.ofi_skew),
            "ofi_95th": float(ofi_result.ofi_95th),
            "ofi_price_divergence": float(ofi_result.ofi_price_divergence),
            "ofi_am_sum": float(ofi_result.ofi_am_sum),
            "ofi_pm_sum": float(ofi_result.ofi_pm_sum),
            "ofi_pos_ratio": float(ofi_result.ofi_pos_ratio),
            "queue_pressure": float(ofi_result.queue_pressure),
            # Lambda
            "kyle_lambda": float(lambda_result.lambda_val),
            # RV
            "rv_bipower": float(rv["rv_bipower"]),
            "jump_ratio": float(rv["jump_ratio"]),
            # VWAP
            "vwap_dev": float(vwap["vwap_dev"]),
            # Volume Surprise
            "volume_surprise": float(vs),
            # Quality
            "bvc_quality": float(bvc_result.bvc_quality),
            "n_buckets_actual": float(vpin_result.n_buckets_actual),
            "bucket_size": float(vpin_result.bucket_size),
        }

        # ── Step 7.5: M7 Orthogonalization ────────────────────────────
        history = self._load_history_arrays(etf_code)
        vpin_ortho = self.orthogonalizer.orthogonalize_vpin(
            vpin_history=history.get("vpin_raw", np.array([])),
            vs_history=history.get("volume_surprise", np.array([])),
            rv_history=history.get("rv_bipower", np.array([])),
            vpin_today=raw["vpin_raw"],
            vs_today=raw["volume_surprise"],
            rv_today=raw["rv_bipower"],
        )
        raw["vpin_orthogonalized"] = float(vpin_ortho)

        # ── Step 8: M8 Feature standardization ────────────────────────
        features = self.pipeline.build_daily_features(raw, history)

        # ── Step 9: Persist history ───────────────────────────────────
        self._append_history(etf_code, trade_date, raw)

        # ── Step 10: Diagnostics ──────────────────────────────────────
        is_cold_start = len(history.get("vpin_raw", np.array([]))) < 5
        diagnostics: dict[str, object] = {
            **quality_meta,
            "bvc_levels": bvc_result.level_counts,
            "lambda_r2": float(lambda_result.r_squared),
            "lambda_intercept": float(lambda_result.intercept),
            "lambda_n_obs": int(lambda_result.n_obs),
            "lambda_sv_std": float(lambda_result.signed_vol_std),
            "ofi_gap_count": int(ofi_result.gap_count),
            "ofi_mm_filtered": float(ofi_result.mm_filtered_ratio),
            "n_buckets_actual": int(vpin_result.n_buckets_actual),
            "bucket_size": float(vpin_result.bucket_size),
            "delta_vpin_valid": bool(vpin_result.delta_vpin_valid),
            "vpin_window_used": int(vpin_result.vpin_window_used),
            "ap_filtered_ratio": float(vpin_result.ap_filtered_ratio),
            "cold_start": is_cold_start,
            "zero_volume_ratio": float(vwap.get("zero_volume_ratio", 0.0)),
            "first_trade_index": int(vwap.get("first_trade_index", -1)),
            "rv_valid_count": int(rv.get("rv_valid_count", 0)),
        }

        # ── Legacy compat: build ms_ prefixed meta for CSV output ────
        meta: dict[str, object] = {
            "ms_snapshot_has_l1": bool(ofi_result.available),
            "ms_ofi_available": bool(ofi_result.available),
            "ms_n_buckets_actual": int(vpin_result.n_buckets_actual),
        }

        return {
            "raw": raw,
            "features": features,
            "diagnostics": diagnostics,
            "meta": meta,
        }

    def _empty_result(self, quality_meta: dict) -> dict[str, object]:
        """Return a NaN-filled result when input data is empty."""
        return {
            "raw": {},
            "features": {},
            "diagnostics": {**quality_meta, "cold_start": True},
            "meta": {
                "ms_snapshot_has_l1": False,
                "ms_ofi_available": False,
                "ms_n_buckets_actual": 0,
            },
        }

    # ── Cross-sectional aggregation (V2.1) ────────────────────────────

    @staticmethod
    def process_all_etfs(
        per_etf_results: dict[str, dict],
        pipeline: FeaturePipeline,
        cross_sectional_features: Optional[list[str]] = None,
    ) -> dict[str, dict]:
        """Apply cross-sectional ranking across all ETFs (V2.1).

        ★ Call in daily_batch.py AFTER all per-ETF process_daily() calls.
        """
        if cross_sectional_features is None:
            cross_sectional_features = [
                "vpin_rank", "delta_vpin_z", "ofi_skew_z",
                "kyle_lambda_z", "rv_bipower_z", "vwap_dev_z",
            ]

        for feat_name in cross_sectional_features:
            xs_vals: dict[str, float] = {}
            for code, res in per_etf_results.items():
                val = res.get("features", {}).get(feat_name)
                if val is not None:
                    xs_vals[code] = float(val)

            xs_ranked = pipeline.cross_sectional_rank(xs_vals)
            for code, ranked_val in xs_ranked.items():
                if code in per_etf_results:
                    per_etf_results[code].setdefault("features", {})[f"{feat_name}_xs"] = ranked_val

        return per_etf_results
