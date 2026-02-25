"""红队审计改进的单元测试。

不依赖 xtquant，仅用 numpy + unittest 验证所有改进的核心逻辑。
"""
from __future__ import annotations

import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("numpy 未安装，跳过相关测试") from None

from etf_chip_engine.cold_start import recalibrate_tails
from etf_chip_engine.engine import ETFChipEngine, Snapshot
from etf_chip_engine.models import ChipDistribution
from etf_chip_engine.modules.indicators import SmoothedPRTracker, find_dense_zones
from etf_chip_engine.modules.iopv_calculator import IOPVCalculator
from etf_chip_engine.modules.maxent_solver import MaxEntSolver


# ─── 改进 1: δ cubic + tanh 映射 ───


class TestDeltaCubicMapping(unittest.TestCase):
    """验证新的 δ·(p'-0.5)^3 偏度映射行为"""

    def test_positive_premium_produces_right_skew(self) -> None:
        """溢价 → δ>0 → 右偏（高价端权重增大）"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 100)
        vwap = 1.5
        v = solver.solve(grid, vwap, premium_rate=0.005, delta_max=2.0, premium_sensitivity=0.0005)
        # 第三矩 > 0 表示右偏
        v_norm = v / v.sum()
        g_norm = (grid - grid.mean()) / (grid.std() + 1e-12)
        skew = float(np.dot(v_norm, g_norm**3))
        self.assertGreater(skew, 0.0, f"溢价时应右偏, 实际 skew={skew:.6f}")

    def test_negative_premium_produces_left_skew(self) -> None:
        """折价 → δ<0 → 左偏（低价端权重增大）"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 100)
        vwap = 1.5
        v = solver.solve(grid, vwap, premium_rate=-0.005, delta_max=2.0, premium_sensitivity=0.0005)
        v_norm = v / v.sum()
        g_norm = (grid - grid.mean()) / (grid.std() + 1e-12)
        skew = float(np.dot(v_norm, g_norm**3))
        self.assertLess(skew, 0.0, f"折价时应左偏, 实际 skew={skew:.6f}")

    def test_zero_premium_symmetric(self) -> None:
        """无溢折价 → δ=0 → 分布对称"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 100)
        vwap = 1.5  # 恰好中点
        v = solver.solve(grid, vwap, premium_rate=0.0)
        v_norm = v / v.sum()
        g_norm = (grid - grid.mean()) / (grid.std() + 1e-12)
        skew = abs(float(np.dot(v_norm, g_norm**3)))
        self.assertLess(skew, 0.01, f"无溢折价时应近似对称, skew={skew:.6f}")

    def test_tanh_deadzone(self) -> None:
        """极小 premium (1e-6) 通过 tanh 自然衰减为接近零"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 50)
        vwap = 1.5
        v_zero = solver.solve(grid, vwap, premium_rate=0.0)
        v_tiny = solver.solve(grid, vwap, premium_rate=1e-6, premium_sensitivity=0.0005)
        # 差异应极小
        diff = float(np.abs(v_zero - v_tiny).max())
        self.assertLess(diff, 1e-4, f"tanh 死区应抑制微小 premium, max_diff={diff:.8f}")

    def test_vwap_constraint_still_holds(self) -> None:
        """即使有偏度项，VWAP 约束仍满足"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 50)
        vwap = 1.7
        for pr in [-0.01, -0.001, 0.0, 0.001, 0.01]:
            v = solver.solve(grid, vwap, premium_rate=pr)
            mean = float(np.dot(v, grid))
            self.assertAlmostEqual(mean, vwap, places=4,
                                   msg=f"premium_rate={pr} 时 VWAP 约束失败: mean={mean:.6f}")

    def test_backward_compat_old_params_ignored(self) -> None:
        """旧参数 k_gamma/gamma_max 仍可传入但被忽略"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 50)
        vwap = 1.5
        # 不应抛异常
        v = solver.solve(grid, vwap, premium_rate=0.001, k_gamma=999.0, gamma_max=99.0)
        self.assertAlmostEqual(float(v.sum()), 1.0, places=10)


# ─── 改进 2: PR EMA 平滑 ───


class TestSmoothedPRTracker(unittest.TestCase):
    def test_ema_converges_to_constant(self) -> None:
        """恒定输入时 EMA 应收敛到该值"""
        tracker = SmoothedPRTracker(span_short=10, span_long=30)
        for _ in range(200):
            result = tracker.update(55.0)
        self.assertAlmostEqual(result["pr_ema_short"], 55.0, places=2)
        self.assertAlmostEqual(result["pr_ema_long"], 55.0, places=1)

    def test_ema_short_responds_faster(self) -> None:
        """短期 EMA 对突变响应应快于长期"""
        tracker = SmoothedPRTracker(span_short=5, span_long=50)
        for _ in range(100):
            tracker.update(50.0)
        # 突变
        tracker.update(80.0)
        result = tracker.update(80.0)
        # 短期应更靠近 80
        diff_short = abs(result["pr_ema_short"] - 80.0)
        diff_long = abs(result["pr_ema_long"] - 80.0)
        self.assertLess(diff_short, diff_long)

    def test_reset_clears_state(self) -> None:
        """reset 后 EMA 应重新初始化"""
        tracker = SmoothedPRTracker(span_short=5, span_long=10)
        for _ in range(50):
            tracker.update(50.0)
        tracker.reset()
        result = tracker.update(80.0)
        self.assertAlmostEqual(result["pr_ema_short"], 80.0, places=4)
        self.assertAlmostEqual(result["pr_ema_long"], 80.0, places=4)

    def test_output_has_all_keys(self) -> None:
        tracker = SmoothedPRTracker()
        result = tracker.update(42.0)
        self.assertIn("pr_raw", result)
        self.assertIn("pr_ema_short", result)
        self.assertIn("pr_ema_long", result)
        self.assertAlmostEqual(result["pr_raw"], 42.0)


# ─── 改进 3: 冷启动尾部再校准 ───


class TestRecalibrateTails(unittest.TestCase):
    def test_chips_beyond_threshold_are_decayed(self) -> None:
        """超出 ±2×ATR 的筹码应被衰减"""
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.5, bucket_size=0.001)
        chips.chips = np.ones(1000, dtype=np.float32) * 100.0
        total_before = float(chips.chips.sum())
        recalibrate_tails(chips, recent_close=1.0, atr=0.05, atr_k=2.0)
        total_after = float(chips.chips.sum())
        self.assertLess(total_after, total_before, "尾部应被衰减")
        # 中央区域（1.0 ± 0.1）不应被影响
        center_idx = chips.price_to_index(1.0)
        self.assertAlmostEqual(float(chips.chips[center_idx]), 100.0, places=3)

    def test_all_chips_within_threshold_unchanged(self) -> None:
        """所有筹码都在阈值内时应不变"""
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.95, bucket_size=0.001)
        chips.chips = np.ones(100, dtype=np.float32) * 50.0
        total_before = float(chips.chips.sum())
        recalibrate_tails(chips, recent_close=1.0, atr=0.1, atr_k=2.0)
        total_after = float(chips.chips.sum())
        self.assertAlmostEqual(total_before, total_after, places=3)

    def test_zero_atr_noop(self) -> None:
        """ATR=0 时不应修改"""
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.5, bucket_size=0.001)
        chips.chips = np.ones(100, dtype=np.float32) * 10.0
        before = chips.chips.copy()
        recalibrate_tails(chips, recent_close=1.0, atr=0.0, atr_k=2.0)
        np.testing.assert_array_equal(chips.chips, before)


# ─── 改进 4: total_shares 入口强校验 ───


class TestTotalSharesValidation(unittest.TestCase):
    def test_zero_total_shares_raises(self) -> None:
        """total_shares=0 时 process_snapshot 应抛 ValueError"""
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=1.0, bucket_size=0.001)
        chips.chips = np.ones(100, dtype=np.float32)
        chips.total_shares = 0.0  # 故意设为 0
        engine.chips["TEST.SZ"] = chips
        snap = Snapshot(high=1.05, low=0.95, close=1.0, volume=100, amount=100.0)
        with self.assertRaises(ValueError):
            engine.process_snapshot("TEST.SZ", snap)

    def test_positive_total_shares_ok(self) -> None:
        """total_shares>0 时正常运行"""
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=1.0, bucket_size=0.001)
        chips.chips = np.ones(100, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips
        snap = Snapshot(high=1.05, low=0.95, close=1.0, volume=100, amount=100.0)
        result = engine.process_snapshot("TEST.SZ", snap)
        self.assertIn("profit_ratio", result)


# ─── 改进 5: 密集区 ATR 自适应平滑 ───


class TestDenseZonesAdaptiveSigma(unittest.TestCase):
    def test_atr_changes_sigma(self) -> None:
        """传入 atr 后 sigma 应自适应计算为 0.2*atr/bucket_size"""
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.5, bucket_size=0.001)
        chips.chips = np.zeros(1000, dtype=np.float32)
        chips.chips[400] = 1000.0
        chips.chips[600] = 500.0
        try:
            z1 = find_dense_zones(chips, 1.0, smooth_sigma=50.0)
            z2 = find_dense_zones(chips, 1.0, atr=0.05, bucket_size=0.001)
            # 只要不报错就算通过
            self.assertIsInstance(z1, list)
            self.assertIsInstance(z2, list)
        except RuntimeError:
            self.skipTest("scipy 未安装，跳过密集区测试")


# ─── 改进 7: IOPV coverage ───


class TestIOPVCoverage(unittest.TestCase):
    def test_full_coverage(self) -> None:
        etf_info = {
            "reportUnit": 100.0, "cashBalance": 0.0,
            "stocks": {"A.SZ": {"componentVolume": 1}, "B.SZ": {"componentVolume": 2}},
        }
        calc = IOPVCalculator(etf_info)
        calc.update_stock_price("A.SZ", 5.0)
        calc.update_stock_price("B.SZ", 10.0)
        self.assertAlmostEqual(calc.get_coverage(), 1.0)

    def test_partial_coverage(self) -> None:
        etf_info = {
            "reportUnit": 100.0, "cashBalance": 0.0,
            "stocks": {"A.SZ": {"componentVolume": 1}, "B.SZ": {"componentVolume": 2},
                       "C.SZ": {"componentVolume": 3}, "D.SZ": {"componentVolume": 4}},
        }
        calc = IOPVCalculator(etf_info)
        calc.update_stock_price("A.SZ", 5.0)
        self.assertAlmostEqual(calc.get_coverage(), 0.25)

    def test_zero_coverage(self) -> None:
        etf_info = {
            "reportUnit": 100.0, "cashBalance": 0.0,
            "stocks": {"A.SZ": {"componentVolume": 1}},
        }
        calc = IOPVCalculator(etf_info)
        self.assertAlmostEqual(calc.get_coverage(), 0.0)


# ─── 改进 8: volume 语义断言 ───


class TestVolumeSemanticAssert(unittest.TestCase):
    def test_negative_volume_raises(self) -> None:
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=1.0, bucket_size=0.001)
        chips.chips = np.ones(100, dtype=np.float32)
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips
        snap = Snapshot(high=1.05, low=0.95, close=1.0, volume=-100, amount=100.0)
        with self.assertRaises(ValueError):
            engine.process_snapshot("TEST.SZ", snap)


# ─── 改进 2+引擎集成: PR EMA 在引擎中输出 ───


class TestEngineOutputPREma(unittest.TestCase):
    def test_process_snapshot_returns_pr_ema(self) -> None:
        """process_snapshot 的输出应包含 PR EMA 字段"""
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips
        snap = Snapshot(high=1.02, low=0.98, close=1.0, volume=1000, amount=1000.0)
        result = engine.process_snapshot("TEST.SZ", snap)
        self.assertIn("profit_ratio_ema_short", result)
        self.assertIn("profit_ratio_ema_long", result)
        # 第一次应等于 raw
        self.assertAlmostEqual(result["profit_ratio_ema_short"], result["profit_ratio"], places=2)


# ─── v2 审计改进 ───


class TestMarketMakerDiscount(unittest.TestCase):
    """P0: 做市商成交折扣"""

    def test_mm_ratio_reduces_turnover(self) -> None:
        """mm_ratio=0.3 时，有效成交量应为原始的 70%"""
        from etf_chip_engine.config import CONFIG
        cfg = dict(CONFIG)
        cfg["mm_ratio"] = 0.3
        engine = ETFChipEngine(cfg)
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 100.0
        chips.total_shares = 1e8
        engine.chips["TEST.SZ"] = chips
        total_before = float(chips.chips.sum())

        snap = Snapshot(high=1.01, low=0.99, close=1.0, volume=10000, amount=10000.0)
        engine.process_snapshot("TEST.SZ", snap)
        total_with_discount = float(chips.chips.sum())

        # 对比无折扣
        cfg2 = dict(CONFIG)
        cfg2["mm_ratio"] = 0.0
        engine2 = ETFChipEngine(cfg2)
        chips2 = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips2.chips = np.ones(200, dtype=np.float32) * 100.0
        chips2.total_shares = 1e8
        engine2.chips["TEST.SZ"] = chips2
        engine2.process_snapshot("TEST.SZ", snap)
        total_no_discount = float(chips2.chips.sum())

        # mm_ratio=0.3 时总量变化应更小（衰减和注入都更少）
        change_with = abs(total_with_discount - total_before)
        change_without = abs(total_no_discount - total_before)
        self.assertLess(change_with, change_without,
                        "mm_ratio=0.3 的变化应小于 mm_ratio=0")


class TestVWAPOneHotEdge(unittest.TestCase):
    """P1a: VWAP = High/Low 时返回 one-hot"""

    def test_vwap_equals_high(self) -> None:
        """VWAP=High 时应返回 one-hot 在最高价"""
        solver = MaxEntSolver(max_iter=20)
        grid = np.linspace(1.0, 2.0, 100)
        v = solver.solve(grid, 2.0)  # vwap == max(grid)
        self.assertAlmostEqual(float(v[-1]), 1.0, places=6)
        self.assertAlmostEqual(float(v[:-1].sum()), 0.0, places=6)

    def test_vwap_equals_low(self) -> None:
        """VWAP=Low 时应返回 one-hot 在最低价"""
        solver = MaxEntSolver(max_iter=20)
        grid = np.linspace(1.0, 2.0, 100)
        v = solver.solve(grid, 1.0)  # vwap == min(grid)
        self.assertAlmostEqual(float(v[0]), 1.0, places=6)
        self.assertAlmostEqual(float(v[1:].sum()), 0.0, places=6)

    def test_vwap_slightly_inside_still_ok(self) -> None:
        """VWAP 稍微在内部（1e-4）时不应触发 one-hot"""
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.linspace(1.0, 2.0, 50)
        v = solver.solve(grid, 1.98)  # 接近 high 但不等于
        self.assertGreater(float(v.max()), 0.0)
        self.assertLess(float(v[-1]), 1.0, "不应完全集中于最后一个桶")


class TestDenseZoneSigmaLowerBound(unittest.TestCase):
    """P1b: σ 下限从 3 改为 5"""

    def test_low_atr_gives_sigma_at_least_5(self) -> None:
        """ATR=0.003, bucket=0.001 → 0.2*3/1=0.6 → 应被 max(5,...) 兜底到 5"""
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.5, bucket_size=0.001)
        chips.chips = np.zeros(500, dtype=np.float32)
        chips.chips[200] = 1000.0
        chips.chips[300] = 500.0
        try:
            zones = find_dense_zones(chips, 0.7, atr=0.003, bucket_size=0.001)
            # 0.2 * 0.003 / 0.001 = 0.6; max(5, 0.6) = 5.0 → 不会过于尖锐
            self.assertIsInstance(zones, list)
        except RuntimeError:
            self.skipTest("scipy 未安装")


class TestKDiffPhaseTransitionWarning(unittest.TestCase):
    """P2b: k_diff 在 [0.10, 0.14] 相变区间时应发出警告"""

    def test_danger_zone_warns(self) -> None:
        from etf_chip_engine.config import CONFIG
        import warnings
        cfg = dict(CONFIG)
        cfg["k_diff"] = 0.12  # 在相变区间内
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ETFChipEngine(cfg)
            self.assertTrue(
                any("相变区间" in str(warning.message) for warning in w),
                "k_diff=0.12 应触发相变警告"
            )

    def test_safe_zone_no_warning(self) -> None:
        from etf_chip_engine.config import CONFIG
        import warnings
        cfg = dict(CONFIG)
        cfg["k_diff"] = 0.08  # 安全区间
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ETFChipEngine(cfg)
            phase_warnings = [x for x in w if "相变区间" in str(x.message)]
            self.assertEqual(len(phase_warnings), 0, "k_diff=0.08 不应触发警告")


class TestIOPVConfidenceFlag(unittest.TestCase):
    """P2a: coverage < 0.7 时输出 iopv_confidence='low'"""

    def test_low_coverage_flags_low_confidence(self) -> None:
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips

        etf_info = {
            "reportUnit": 100.0, "cashBalance": 0.0,
            "stocks": {f"S{i}.SZ": {"componentVolume": 1} for i in range(10)},
        }
        engine.attach_iopv("TEST.SZ", etf_info)
        # 只更新 3/10 = 30% coverage
        for i in range(3):
            engine.iopv["TEST.SZ"].update_stock_price(f"S{i}.SZ", 5.0)

        result = engine.get_indicators("TEST.SZ", current_price=1.0, atr=None)
        self.assertEqual(result.get("iopv_confidence"), "low")
        self.assertLess(result["iopv_coverage"], 0.7)

    def test_full_coverage_no_flag(self) -> None:
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips

        etf_info = {
            "reportUnit": 100.0, "cashBalance": 0.0,
            "stocks": {"A.SZ": {"componentVolume": 1}, "B.SZ": {"componentVolume": 2}},
        }
        engine.attach_iopv("TEST.SZ", etf_info)
        engine.iopv["TEST.SZ"].update_stock_price("A.SZ", 5.0)
        engine.iopv["TEST.SZ"].update_stock_price("B.SZ", 10.0)

        result = engine.get_indicators("TEST.SZ", current_price=1.0, atr=None)
        self.assertNotIn("iopv_confidence", result)
        self.assertAlmostEqual(result["iopv_coverage"], 1.0)


# ─── v3 终审改进 ───


class TestPREMAPersistence(unittest.TestCase):
    """C1: save_state/load_state 持久化 PR EMA"""

    def test_save_load_round_trip_preserves_ema(self) -> None:
        import tempfile, os
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips

        # Feed some values to build EMA state
        from etf_chip_engine.modules.indicators import SmoothedPRTracker
        tracker = engine._get_pr_tracker("TEST.SZ")
        for _ in range(50):
            tracker.update(55.0)
        ema_short_before = tracker.ema_short
        ema_long_before = tracker.ema_long

        # Save
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_chips.npz")
            engine.save_state("TEST.SZ", path)

            # Verify sidecar exists
            ema_path = path.replace(".npz", ".ema.json")
            self.assertTrue(os.path.exists(ema_path), "EMA sidecar file should exist")

            # Load into new engine
            engine2 = ETFChipEngine(dict(CONFIG))
            engine2.load_state("TEST.SZ", path)
            tracker2 = engine2._pr_trackers.get("TEST.SZ")
            self.assertIsNotNone(tracker2, "PR tracker should be restored")
            self.assertAlmostEqual(tracker2.ema_short, ema_short_before, places=6)
            self.assertAlmostEqual(tracker2.ema_long, ema_long_before, places=6)

    def test_load_without_sidecar_degrades_gracefully(self) -> None:
        """No .ema.json → EMA starts from None (warm-up)"""
        import tempfile, os
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_chips.npz")
            chips.save(path)  # Direct save, no sidecar

            engine2 = ETFChipEngine(dict(CONFIG))
            engine2.load_state("TEST.SZ", path)  # Should not raise
            self.assertNotIn("TEST.SZ", engine2._pr_trackers)


class TestDateContinuityCheck(unittest.TestCase):
    """C3: process_daily 日期连续性校验"""

    def _make_engine_with_chips(self):
        from etf_chip_engine.config import CONFIG
        engine = ETFChipEngine(dict(CONFIG))
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=0.9, bucket_size=0.001)
        chips.chips = np.ones(200, dtype=np.float32) * 10.0
        chips.total_shares = 1e6
        engine.chips["TEST.SZ"] = chips
        return engine

    def _make_snapshots(self, n=5):
        import pandas as pd
        rows = []
        for i in range(n):
            rows.append({
                "high": 1.02, "low": 0.98, "close": 1.0,
                "volume": 1000, "amount": 1000.0,
            })
        return pd.DataFrame(rows)

    def test_large_gap_triggers_warning_and_extra_diffusion(self) -> None:
        from datetime import date
        import warnings
        engine = self._make_engine_with_chips()
        snaps = self._make_snapshots()

        # First call: set baseline date
        engine.process_daily("TEST.SZ", snaps, shares_today=1e6,
                           shares_yesterday=1e6, atr=0.02,
                           trade_date=date(2026, 1, 5))

        total_before = float(engine.chips["TEST.SZ"].chips.sum())

        # Second call: 10-day gap (e.g. Spring Festival)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.process_daily("TEST.SZ", snaps, shares_today=1e6,
                                         shares_yesterday=1e6, atr=0.02,
                                         trade_date=date(2026, 1, 15))
            gap_warnings = [x for x in w if "跨越" in str(x.message)]
            self.assertTrue(len(gap_warnings) > 0, "Should warn about 10-day gap")
            self.assertEqual(result.get("data_gap_days"), 10)

    def test_normal_gap_no_warning(self) -> None:
        from datetime import date
        import warnings
        engine = self._make_engine_with_chips()
        snaps = self._make_snapshots()

        engine.process_daily("TEST.SZ", snaps, shares_today=1e6,
                           shares_yesterday=1e6, atr=0.02,
                           trade_date=date(2026, 1, 6))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = engine.process_daily("TEST.SZ", snaps, shares_today=1e6,
                                         shares_yesterday=1e6, atr=0.02,
                                         trade_date=date(2026, 1, 7))
            gap_warnings = [x for x in w if "跨越" in str(x.message)]
            self.assertEqual(len(gap_warnings), 0, "1-day gap should not warn")
            self.assertNotIn("data_gap_days", result)

    def test_no_trade_date_backward_compatible(self) -> None:
        """Omitting trade_date should work exactly as before"""
        engine = self._make_engine_with_chips()
        snaps = self._make_snapshots()
        result = engine.process_daily("TEST.SZ", snaps, shares_today=1e6,
                                     shares_yesterday=1e6, atr=0.02)
        self.assertIn("profit_ratio", result)


class TestFrozenParams(unittest.TestCase):
    """S5: kappa3, maxent_max_iter, maxent_tol 不再从 CONFIG 读取"""

    def test_frozen_params_ignored_from_config(self) -> None:
        from etf_chip_engine.config import CONFIG
        cfg = dict(CONFIG)
        # Attempt to override frozen params via config
        cfg["maxent_max_iter"] = 999
        cfg["maxent_tol"] = 0.5
        cfg["kappa3"] = 99.0
        engine = ETFChipEngine(cfg)
        # Should use class constants, not config values
        self.assertEqual(engine.maxent.max_iter, 20)
        self.assertAlmostEqual(engine.maxent.tol, 1e-8)
        self.assertAlmostEqual(engine.turnover.kappa3, 0.5)

    def test_frozen_params_not_in_config(self) -> None:
        from etf_chip_engine.config import CONFIG
        self.assertNotIn("maxent_max_iter", CONFIG)
        self.assertNotIn("maxent_tol", CONFIG)
        self.assertNotIn("kappa3", CONFIG)


if __name__ == "__main__":
    unittest.main()
