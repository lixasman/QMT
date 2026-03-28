from __future__ import annotations

from pathlib import Path


_BASE_DIR = Path(__file__).resolve().parent


CONFIG: dict[str, object] = {
    # Default bucket_size fallback. Per-symbol bucket_size should be aligned
    # to instrument PriceTick when available.
    "bucket_size": 0.01,
    # Chip engine parameters (stock defaults)
    "delta_max": 2.0,
    "premium_sensitivity": 0.03,
    "alpha": 0.85,
    "kappa1": 1.5,
    "kappa2": 1.5,
    "mm_ratio": 0.0,
    "k_diff": 0.08,
    "asr_k": 1.0,
    "pr_ema_span_short": 30,
    "pr_ema_span_long": 120,
    "tail_recalibrate_atr_k": 2.0,
    "cold_start_lookback": 60,
    "cold_start_decay": 0.95,
    "daily_tick_count": -1,
    # Output directories (isolated from ETF engine)
    "chip_snapshot_dir": str(_BASE_DIR / "data" / "chip_snapshots"),
    "l1_snapshot_dir": str(_BASE_DIR / "data" / "l1_snapshots"),
    # Parquet output fallback (same semantics as ETF service)
    "l1_fallback_csv": "0",
    # Volume unit handling: XtQuant often returns volume in "lots" (hand).
    "lot_size": 100.0,
    "tick_volume_in_lots": 1,
    # Daily bars volume unit may differ from tick volume in some environments.
    # If you see ADV/VPIN abnormal, try toggling this independently.
    "daily_volume_in_lots": 1,
    # One-time diagnostic to detect volume unit mismatch.
    "tick_volume_self_check": 1,
    "tick_volume_self_check_sample": 200,
    "tick_volume_self_check_vwap_ratio_low": 0.2,
    "tick_volume_self_check_vwap_ratio_high": 5.0,
    "daily_volume_self_check": 1,
    # Skew signal (used as MaxEnt premium_rate substitute)
    "skew_min_cum_volume_ratio": 0.01,
    "skew_min_cum_volume_lots": 200,
    # Stock-only: allow overriding the ETF engine's frozen kappa3 via instance override.
    "kappa3": 0.5,
    # Microstructure engine config (factor history isolated from ETF)
    "microstructure": {
        "bvc_lookback": 100,
        "bvc_min_periods": 20,
        "bvc_k_max": 10,
        "vpin_n_buckets": 50,
        "vpin_window": 50,
        "vpin_premium_threshold": 0.003,
        "vpin_premium_saturate": 0.01,
        "kyle_lambda_window": 1200,
        "feature_history_days": 60,
        "max_history_days": 120,
        "factor_history_dir": str(Path("data") / "factor_history_stock"),
        "ortho_min_history": 20,
    },
}
