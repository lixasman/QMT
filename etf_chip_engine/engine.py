from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Dict, Optional

import json
import warnings

import numpy as np
import pandas as pd

from etf_chip_engine.models import ChipDistribution
from etf_chip_engine.modules import (
    IOPVCalculator,
    MaxEntSolver,
    RedemptionCorrector,
    SmoothedPRTracker,
    TurnoverModel,
    apply_brownian_diffusion,
    calc_asr,
    calc_profit_ratio,
    find_dense_zones,
)


@dataclass(frozen=True)
class Snapshot:
    high: float
    low: float
    close: float
    volume: float
    amount: float
    premium_rate: float = 0.0


class ETFChipEngine:
    # v3 终审 S5: 冻结为硬编码常量（不再从 CONFIG 读取）
    _MAXENT_MAX_ITER: int = 20
    _MAXENT_TOL: float = 1e-8
    _KAPPA3: float = 0.5  # 深套区止损释放（ETF 跌>20% 极罕见，几乎不触发）

    def __init__(self, config: dict):
        self.config = dict(config)

        # v2 审计 P2b: k_diff 相变安全距离校验
        k_diff = float(self.config.get("k_diff", 0.08))
        if 0.10 <= k_diff <= 0.14:
            warnings.warn(
                f"k_diff={k_diff} 处于相变区间 [0.10, 0.14]，密集区数量可能骤变。"
                "建议调整到 ≤0.08 或 ≥0.16",
                UserWarning,
                stacklevel=2,
            )

        self.maxent = MaxEntSolver(
            max_iter=self._MAXENT_MAX_ITER,
            tol=self._MAXENT_TOL,
        )
        self.turnover = TurnoverModel(
            alpha=float(self.config.get("alpha", 0.5)),
            kappa1=float(self.config.get("kappa1", 1.5)),
            kappa2=float(self.config.get("kappa2", 1.5)),
            kappa3=self._KAPPA3,
        )
        self.redemption = RedemptionCorrector()
        self.chips: Dict[str, ChipDistribution] = {}
        self.iopv: Dict[str, IOPVCalculator] = {}
        # 改进 2: PR EMA 平滑器（每 ETF 独立维护状态）
        self._pr_trackers: Dict[str, SmoothedPRTracker] = {}
        # v3 终审 C3: 记录每 ETF 最近处理的交易日
        self._last_trade_dates: Dict[str, date] = {}

    def _get_pr_tracker(self, etf_code: str) -> SmoothedPRTracker:
        if etf_code not in self._pr_trackers:
            self._pr_trackers[etf_code] = SmoothedPRTracker(
                span_short=int(self.config.get("pr_ema_span_short", 30)),
                span_long=int(self.config.get("pr_ema_span_long", 120)),
            )
        return self._pr_trackers[etf_code]

    def cold_start(
        self,
        etf_code: str,
        daily_df: pd.DataFrame,
        *,
        total_shares: float = 0.0,
        atr: float = 0.0,
    ) -> ChipDistribution:
        from etf_chip_engine.cold_start import cold_start_from_daily, recalibrate_tails

        bucket_size = float(self.config.get("bucket_size", 0.001))
        close0 = float(daily_df["close"].iloc[-1]) if not daily_df.empty else 1.0
        base_price = float(np.floor((close0 * 0.5) / bucket_size) * bucket_size)

        chips = ChipDistribution(etf_code=etf_code, base_price=base_price, bucket_size=bucket_size)
        chips.ensure_range(close0 * 0.5, close0 * 1.5, padding_buckets=200)

        decay = float(self.config.get("cold_start_decay", 0.95))
        cold_start_from_daily(daily_df, chips, decay=decay, total_shares=float(total_shares))

        # 改进 3: 冷启动后再校准——按 ±atr_k×ATR 截断尾部幽灵筹码
        if atr > 0:
            atr_k = float(self.config.get("tail_recalibrate_atr_k", 2.0))
            recalibrate_tails(chips, close0, float(atr), atr_k=atr_k)

        self.chips[etf_code] = chips
        return chips

    def attach_iopv(self, etf_code: str, etf_info: dict) -> None:
        self.iopv[etf_code] = IOPVCalculator(etf_info, etf_code=str(etf_code))

    def process_snapshot(self, etf_code: str, snapshot: dict | Snapshot) -> dict:
        if isinstance(snapshot, Snapshot):
            snap = snapshot
        else:
            snap = Snapshot(
                high=float(snapshot["high"]),
                low=float(snapshot["low"]),
                close=float(snapshot["close"]),
                volume=float(snapshot["volume"]),
                amount=float(snapshot["amount"]),
                premium_rate=float(snapshot.get("premium_rate", 0.0)),
            )

        # 改进 8: volume 语义断言
        if snap.volume < 0:
            raise ValueError(
                f"snapshot volume 为负 ({snap.volume})，请确认数据是否为增量而非累计值"
            )

        chips = self.chips[etf_code]

        # 改进 4: total_shares 入口强校验
        if chips.total_shares <= 0:
            raise ValueError(
                f"{etf_code}: total_shares={chips.total_shares} 无效，"
                "请在 cold_start 或 load_state 后设置 total_shares"
            )

        if snap.volume <= 0:
            return self.get_indicators(etf_code, current_price=snap.close, atr=None)

        high, low = snap.high, snap.low
        if high < low:
            high, low = low, high
        chips.ensure_range(low, high, padding_buckets=20)

        # v2 审计 P0: 做市/套利成交折扣——仅对有效成交量建模
        mm_ratio = float(self.config.get("mm_ratio", 0.0))
        volume_eff = snap.volume * (1.0 - mm_ratio)

        vwap = snap.amount / max(snap.volume, 1e-12)

        # 改进 7: IOPV coverage 衰减 premium
        premium = snap.premium_rate
        if "premium_rate" not in (snapshot.__dict__ if isinstance(snapshot, Snapshot) else snapshot):
            if etf_code in self.iopv:
                raw_premium = self.iopv[etf_code].get_premium_rate(snap.close)
                coverage = self.iopv[etf_code].get_coverage()
                premium = raw_premium if float(coverage) <= 0 else (raw_premium * coverage)

        grid = np.arange(low, high + chips.bucket_size, chips.bucket_size, dtype=np.float64)
        if grid.size < 2:
            grid = np.array([snap.close], dtype=np.float64)

        # 改进 1: 使用新的 δ cubic + tanh 映射参数
        v_dist = self.maxent.solve(
            grid,
            vwap,
            premium_rate=premium,
            delta_max=float(self.config.get("delta_max", 2.0)),
            premium_sensitivity=float(self.config.get("premium_sensitivity", 0.0005)),
        )

        full_grid = chips.get_price_grid()
        base_tr = volume_eff / chips.total_shares if chips.total_shares > 0 else 0.0
        tr = self.turnover.calculate_turnover(full_grid, snap.close, base_tr)
        chips.chips *= (1.0 - np.clip(tr, 0.0, 1.0)).astype(np.float32)

        new_vol = v_dist * volume_eff
        indices = np.rint((grid - chips.base_price) / chips.bucket_size).astype(np.int64)
        valid = (indices >= 0) & (indices < len(chips.chips))
        if valid.any():
            np.add.at(chips.chips, indices[valid], new_vol[valid].astype(np.float32))

        chips.last_update = datetime.now()
        return self.get_indicators(etf_code, current_price=snap.close, atr=None)

    def process_daily(
        self,
        etf_code: str,
        snapshots: pd.DataFrame,
        *,
        shares_today: float,
        shares_yesterday: float,
        atr: float,
        trade_date: date | None = None,
    ) -> dict:
        if etf_code not in self.chips:
            raise RuntimeError(f"未初始化筹码分布: {etf_code}")

        # v3 终审 C3: 日期连续性校验
        data_gap_days = 0
        if trade_date is not None:
            last_td = self._last_trade_dates.get(etf_code)
            if last_td is not None:
                gap = (trade_date - last_td).days
                if gap > 5:  # 超过 5 日历日（≈3 个交易日以上）
                    data_gap_days = gap
                    warnings.warn(
                        f"{etf_code}: 上次处理日 {last_td} → 本次 {trade_date}，"
                        f"跨越 {gap} 天。将按 gap 倍数执行额外扩散。",
                        UserWarning,
                        stacklevel=2,
                    )
            self._last_trade_dates[etf_code] = trade_date

        # 重置 PR tracker（新交易日重新平滑）
        self._get_pr_tracker(etf_code).reset()

        for _, row in snapshots.iterrows():
            self.process_snapshot(etf_code, row.to_dict())

        delta = float(shares_today) - float(shares_yesterday)
        if abs(delta) > 0:
            last_close = float(snapshots["close"].iloc[-1])
            day_vwap = float(snapshots["amount"].sum()) / max(float(snapshots["volume"].sum()), 1e-12)
            if delta > 0:
                # 动态 sigma：日内极差的 creation_sigma_k 倍作为 σ_price，转换为桶数
                daily_range = float(snapshots["high"].max()) - float(snapshots["low"].min())
                creation_sigma_k = float(self.config.get("creation_sigma_k", 0.2))
                sigma_price = creation_sigma_k * daily_range
                sigma_buckets = sigma_price / self.chips[etf_code].bucket_size

                # 改进 6: 大额申购强制加宽 σ
                large_threshold = float(self.config.get("creation_large_threshold", 0.05))
                share_ratio = delta / max(float(shares_yesterday), 1e-12)
                if share_ratio > large_threshold:
                    large_sigma_k = float(self.config.get("creation_large_sigma_k", 0.3))
                    sigma_buckets = max(
                        sigma_buckets,
                        large_sigma_k * daily_range / self.chips[etf_code].bucket_size,
                    )

                self.redemption.apply_creation(self.chips[etf_code], delta, day_vwap,
                                               sigma_buckets=sigma_buckets)
            else:
                self.redemption.apply_redemption(self.chips[etf_code], delta, last_close)
            self.chips[etf_code].total_shares = float(shares_today)

        # 日终布朗扩散（含跨日 gap 补偿：按跳过天数倍增）
        diffusion_rounds = max(1, data_gap_days // 2) if data_gap_days > 0 else 1
        k_diff = float(self.config.get("k_diff", 0.08))
        for _ in range(diffusion_rounds):
            apply_brownian_diffusion(self.chips[etf_code], float(atr), k_diff=k_diff)

        result = self.get_indicators(etf_code, current_price=float(snapshots["close"].iloc[-1]), atr=float(atr))
        if data_gap_days > 0:
            result["data_gap_days"] = data_gap_days
        return result

    def get_indicators(self, etf_code: str, *, current_price: float, atr: Optional[float]) -> dict:
        chips = self.chips[etf_code]
        raw_pr = calc_profit_ratio(chips, float(current_price))

        # 改进 2: PR EMA 平滑
        pr_data = self._get_pr_tracker(etf_code).update(raw_pr)

        out: dict = {
            "etf_code": etf_code,
            "profit_ratio": raw_pr,
            "profit_ratio_ema_short": pr_data["pr_ema_short"],
            "profit_ratio_ema_long": pr_data["pr_ema_long"],
            # 改进 5: 密集区 ATR 自适应平滑
            "dense_zones": find_dense_zones(
                chips, float(current_price),
                atr=atr, bucket_size=chips.bucket_size, top_n=20,
            ),
        }
        if atr is not None:
            out["asr"] = calc_asr(chips, float(current_price), atr=float(atr), k=float(self.config.get("asr_k", 1.0)))

        # v2 审计 P2a: IOPV coverage 不足时输出置信度标记
        if etf_code in self.iopv:
            cov = self.iopv[etf_code].get_coverage()
            out["iopv_coverage"] = round(cov, 4)
            if cov < 0.7:
                out["iopv_confidence"] = "low"

        return out

    def save_state(self, etf_code: str, path: str) -> None:
        """持久化筹码分布 + PR EMA 状态。

        v3 终审 C1: 除 ChipDistribution 外，PR EMA 状态写入同目录 sidecar 文件。
        """
        from pathlib import Path as _P
        self.chips[etf_code].save(path)
        # PR EMA sidecar
        tracker = self._pr_trackers.get(etf_code)
        if tracker is not None and tracker.ema_short is not None:
            ema_path = _P(path).with_suffix(".ema.json")
            ema_data = {
                "ema_short": tracker.ema_short,
                "ema_long": tracker.ema_long,
            }
            ema_path.write_text(json.dumps(ema_data), encoding="utf-8")

    def load_state(self, etf_code: str, path: str) -> None:
        """加载筹码分布 + PR EMA 状态（如有）。"""
        from pathlib import Path as _P
        self.chips[etf_code] = ChipDistribution.load(path, etf_code)
        # 尝试恢复 PR EMA
        ema_path = _P(path).with_suffix(".ema.json")
        if ema_path.exists():
            try:
                ema_data = json.loads(ema_path.read_text(encoding="utf-8"))
                tracker = self._get_pr_tracker(etf_code)
                tracker.ema_short = float(ema_data["ema_short"])
                tracker.ema_long = float(ema_data["ema_long"])
            except (json.JSONDecodeError, KeyError, ValueError):
                pass  # sidecar 损坏时静默降级，EMA 将 warm-up
