from __future__ import annotations

from pathlib import Path


ETF_EXCLUDE_KEYWORDS: list[str] = [
    "货币",
    "债",
    "存单",
    "豆粕",
    "黄金",
    "QDII",
    "跨境",
    "海外",
    "美国",
    "纳斯达克",
    "标普",
    "道琼斯",
    "巴西",
    "日经",
    "德国",
    "法国",
]


_BASE_DIR = Path(__file__).resolve().parent


CONFIG: dict[str, object] = {
    "bucket_size": 0.001,
    # maxent_max_iter, maxent_tol, kappa3 已冻结为 ETFChipEngine 类常量（v3 终审 S5）
    "delta_max": 2.0,                # δ 偏度上限（奇函数 cubic 映射）
    "premium_sensitivity": 0.0005,   # tanh 缩放因子 ≈ 5bps 以下为死区
    "alpha": 0.5,
    "kappa1": 1.5,
    "kappa2": 1.5,
    "creation_sigma_k": 0.2,       # 申购注入 σ = creation_sigma_k × 日内极差
    "creation_large_threshold": 0.05,  # 大额申购阈值（占总份额比例）
    "creation_large_sigma_k": 0.3,    # 大额申购时的强制最小 σ 系数
    "mm_ratio": 0.30,               # 做市/套利成交占比折扣（v2 审计 P0）
    "k_diff": 0.08,
    "asr_k": 1.0,
    "pr_ema_span_short": 30,       # PR 短期 EMA 窗口（快照数 ≈ 1.5 分钟）
    "pr_ema_span_long": 120,       # PR 长期 EMA 窗口（快照数 ≈ 6 分钟）
    "tail_recalibrate_atr_k": 2.0, # 冷启动再校准尾部截断阈值（×ATR）
    "cold_start_lookback": 60,
    "cold_start_decay": 0.95,
    "daily_tick_count": -1,
    "tick_download_chunk_timeout_sec": 45,
    "snapshot_dir": str(_BASE_DIR / "data" / "snapshots"),
    "chip_snapshot_dir": str(_BASE_DIR / "data" / "chip_snapshots"),
    "l1_snapshot_dir": str(_BASE_DIR / "data" / "l1_snapshots"),
    "l1_fallback_csv": "0",
    "microstructure": {
        "bvc_lookback": 100,
        "bvc_min_periods": 20,
        "vpin_n_buckets": 50,
        "vpin_window": 50,
        "vpin_premium_threshold": 0.003,
        "vpin_premium_saturate": 0.01,
        "kyle_lambda_window": 1200,
        "feature_history_days": 60,
        "max_history_days": 120,
        "factor_history_dir": str(Path("data") / "factor_history"),
    },
}
