"""Tests for the microstructure factor engine (V2.1).

All existing test logic is preserved; only the interface adapters (how
tests call the modules) are updated to match V2.1 signatures.
"""

import tempfile
import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("numpy 未安装，跳过相关测试") from None

try:
    import pandas as pd
except ModuleNotFoundError:
    raise unittest.SkipTest("pandas 未安装，跳过相关测试") from None

from etf_chip_engine.microstructure.auxiliary_factors import (
    asr_velocity,
    kyle_lambda,
    normalize_robust_zscore,
    premium_vpin_cross,
    profit_ofi_divergence,
    rv_bipower,
    volume_surprise,
    vwap_deviation,
    LambdaResult,
)
from etf_chip_engine.microstructure.bvc import BulkVolumeClassifier, BVCResult
from etf_chip_engine.microstructure.factor_engine import MicrostructureEngine
from etf_chip_engine.microstructure.microprice import compute_microprice
from etf_chip_engine.microstructure.ofi import ContStoikovOFI
from etf_chip_engine.microstructure.orthogonalizer import FactorOrthogonalizer
from etf_chip_engine.microstructure.preprocessor import SnapshotPreprocessor
from etf_chip_engine.microstructure.vpin import VPINCalculator


class TestBVC(unittest.TestCase):
    def test_bvc_sigma_zero_half_half(self) -> None:
        """When close == open for all snapshots, Φ(0) = 0.5 → v_buy = v_sell."""
        n = 30
        df = pd.DataFrame(
            {
                "open": np.ones(n, dtype=np.float64),
                "close": np.ones(n, dtype=np.float64),
                "volume": np.full(n, 10.0, dtype=np.float64),
            }
        )
        result = BulkVolumeClassifier(lookback=100, min_periods=20).classify(df)
        self.assertIsInstance(result, BVCResult)
        self.assertTrue(np.allclose(result.v_buy, 5.0, atol=1e-12))
        self.assertTrue(np.allclose(result.v_sell, 5.0, atol=1e-12))

    def test_bvc_three_level_with_microprice(self) -> None:
        """V2.1: Three-level BVC with Microprice input."""
        n = 30
        close = np.full(n, 10.0, dtype=np.float64)
        close[5] = 10.01  # one price change → Level 1
        df = pd.DataFrame(
            {
                "close": close,
                "volume": np.full(n, 10.0, dtype=np.float64),
                "bid1": np.full(n, 9.99, dtype=np.float64),
                "bid1_vol": np.full(n, 100.0, dtype=np.float64),
                "ask1": np.full(n, 10.01, dtype=np.float64),
                "ask1_vol": np.full(n, 100.0, dtype=np.float64),
            }
        )
        mp = compute_microprice(df)
        result = BulkVolumeClassifier(lookback=100, min_periods=20).classify(df, mp)
        self.assertIsInstance(result, BVCResult)
        self.assertEqual(len(result.v_buy), n)
        self.assertIn("L1", result.level_counts)
        self.assertGreater(result.bvc_quality, 0.0)


class TestVPIN(unittest.TestCase):
    def test_vpin_simple_two_buckets(self) -> None:
        """Two perfect buckets: one all-buy, one all-sell → VPIN = 1.0."""
        df = pd.DataFrame(
            {
                "volume": [10.0, 10.0],
                "v_buy": [10.0, 0.0],
                "v_sell": [0.0, 10.0],
            }
        )
        prem = pd.Series([0.0, 0.0])
        calc = VPINCalculator(n_buckets=2, window=1, min_buckets=1)
        res = calc.compute(df, premium_rates=prem)
        self.assertEqual(res.n_buckets_actual, 2)
        self.assertAlmostEqual(res.vpin_raw, 1.0, places=12)
        self.assertAlmostEqual(res.vpin_filtered, 1.0, places=12)
        self.assertAlmostEqual(res.vpin_max, 1.0, places=12)

    def test_vpin_delta_vpin(self) -> None:
        """V2.1: delta_vpin is computed when yesterday data is provided."""
        df = pd.DataFrame(
            {
                "volume": [100.0] * 20,
                "v_buy": [60.0] * 20,
                "v_sell": [40.0] * 20,
            }
        )
        # total_volume=2000, n_buckets=10 → bucket_size=200.0
        # yesterday bucket_size must be within ±30% of 200.0 for comparability
        calc = VPINCalculator(n_buckets=10, window=5, min_buckets=5)
        res = calc.compute(
            df,
            vpin_yesterday=0.1,
            n_buckets_yesterday=10,
            bucket_size_yesterday=200.0,
        )
        if res.n_buckets_actual >= 5:
            self.assertTrue(res.delta_vpin_valid)
            self.assertFalse(np.isnan(res.delta_vpin))


class TestOFI(unittest.TestCase):
    def test_ofi_piecewise(self) -> None:
        """Stoikov OFI piecewise logic: price up → +vol, price down → -vol."""
        df = pd.DataFrame(
            {
                "bid1": [10.0, 10.01, 10.0],
                "bid1_vol": [100.0, 120.0, 110.0],
                "ask1": [11.0, 11.0, 10.99],
                "ask1_vol": [100.0, 90.0, 95.0],
            }
        )
        res = ContStoikovOFI().compute(df)
        self.assertTrue(res.available)
        self.assertEqual(len(res.ofi_series), 3)
        self.assertAlmostEqual(float(res.ofi_series[1]), 130.0, places=12)
        self.assertAlmostEqual(float(res.ofi_series[2]), -215.0, places=12)
        self.assertAlmostEqual(res.ofi_daily, -85.0, places=12)

    def test_ofi_shape_features(self) -> None:
        """V2.1: OFIResult includes new shape feature fields."""
        df = pd.DataFrame(
            {
                "bid1": [10.0, 10.01, 10.0, 10.01],
                "bid1_vol": [100.0, 120.0, 80.0, 150.0],
                "ask1": [11.0, 11.0, 10.99, 11.01],
                "ask1_vol": [100.0, 90.0, 95.0, 110.0],
            }
        )
        res = ContStoikovOFI().compute(df)
        self.assertTrue(res.available)
        self.assertTrue(hasattr(res, "ofi_skew"))
        self.assertTrue(hasattr(res, "ofi_95th"))
        self.assertTrue(hasattr(res, "ofi_pos_ratio"))


class TestVolumeSurprise(unittest.TestCase):
    def test_volume_surprise_log_ratio(self) -> None:
        """V2.1: VS = log(total_volume / adv_60)."""
        vs = volume_surprise(1000.0, 500.0)
        self.assertAlmostEqual(vs, np.log(2.0), places=12)

    def test_volume_surprise_invalid_inputs(self) -> None:
        """V2.1: returns NaN for zero or negative inputs."""
        self.assertTrue(np.isnan(volume_surprise(0.0, 500.0)))
        self.assertTrue(np.isnan(volume_surprise(1000.0, 0.0)))
        self.assertTrue(np.isnan(volume_surprise(-1.0, 500.0)))


class TestKyleLambda(unittest.TestCase):
    def test_lambda_returns_result(self) -> None:
        """V2.1: kyle_lambda returns LambdaResult, not float."""
        n = 200
        mp = np.cumsum(np.random.randn(n) * 0.001) + 10.0
        v_buy = np.abs(np.random.randn(n)) * 100
        v_sell = np.abs(np.random.randn(n)) * 100
        result = kyle_lambda(mp, v_buy, v_sell, window=200)
        self.assertIsInstance(result, LambdaResult)
        self.assertGreater(result.n_obs, 50)
        self.assertFalse(np.isnan(result.lambda_val))
        self.assertFalse(np.isnan(result.intercept))

    def test_lambda_too_few_obs(self) -> None:
        """V2.1: returns NaN when observations < 50."""
        mp = np.ones(20)
        vb = np.ones(20)
        vs = np.ones(20)
        result = kyle_lambda(mp, vb, vs, window=20)
        self.assertTrue(np.isnan(result.lambda_val))


class TestRVBipower(unittest.TestCase):
    def test_rv_bipower_normal(self) -> None:
        """V2.1: RV from close with zero-trade protection."""
        close = np.cumsum(np.random.randn(500) * 0.001) + 10.0
        rv = rv_bipower(close)
        self.assertFalse(np.isnan(rv["rv_bipower"]))
        self.assertFalse(np.isnan(rv["rv_classical"]))
        self.assertGreaterEqual(rv["jump_ratio"], 0.0)
        self.assertGreater(rv["rv_valid_count"], 100)

    def test_rv_bipower_flat_prices(self) -> None:
        """V2.1: flat prices → valid_count < 100 → NaN."""
        close = np.full(200, 10.0)
        rv = rv_bipower(close)
        self.assertTrue(np.isnan(rv["rv_bipower"]))
        self.assertEqual(rv["rv_valid_count"], 0)


class TestVWAPDeviation(unittest.TestCase):
    def test_vwap_normal(self) -> None:
        """V2.1: VWAP deviation with NaN for zero-volume intervals."""
        df = pd.DataFrame(
            {
                "amount": [100.0, 200.0, 300.0],
                "volume": [10.0, 20.0, 30.0],
                "close": [10.0, 10.0, 10.0],
            }
        )
        result = vwap_deviation(df)
        self.assertAlmostEqual(result["vwap_dev"], 0.0, places=6)
        self.assertLess(result["zero_volume_ratio"], 0.01)

    def test_vwap_no_volume(self) -> None:
        """V2.1: all zero volume → NaN."""
        df = pd.DataFrame(
            {
                "amount": [0.0, 0.0],
                "volume": [0.0, 0.0],
                "close": [10.0, 10.0],
            }
        )
        result = vwap_deviation(df)
        self.assertTrue(np.isnan(result["vwap_dev"]))


class TestRobustZScore(unittest.TestCase):
    def test_robust_zscore_cold_start(self) -> None:
        """V2.1: cold-start → NaN (not 0.0)."""
        z = normalize_robust_zscore(5.0, np.array([1.0, 2.0]))
        self.assertTrue(np.isnan(z))

    def test_robust_zscore_normal(self) -> None:
        """V2.1: MAD-based z-score for normal data."""
        history = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        z = normalize_robust_zscore(10.0, history)
        self.assertFalse(np.isnan(z))
        self.assertGreater(z, 0.0)
        self.assertLessEqual(z, 3.0)


class TestPreprocessor(unittest.TestCase):
    def test_preprocessor_outputs_masks(self) -> None:
        """V2.1: M0 preprocessor adds mask columns."""
        n = 10
        df = pd.DataFrame(
            {
                "time": list(range(n)),
                "bid1": np.full(n, 9.99),
                "bid1_vol": np.full(n, 100.0),
                "ask1": np.full(n, 10.01),
                "ask1_vol": np.full(n, 100.0),
                "close": np.full(n, 10.0),
                "volume": np.full(n, 10.0),
                "amount": np.full(n, 100.0),
            }
        )
        pp = SnapshotPreprocessor()
        out, quality = pp.process(df)
        self.assertIn("limit_locked_mask", out.columns)
        self.assertIn("valid_continuous_mask", out.columns)
        self.assertIn("session_mask", out.columns)
        self.assertGreater(quality["n_total"], 0)


class TestOrthogonalizer(unittest.TestCase):
    def test_cold_start_returns_raw(self) -> None:
        """V2.1: M7 orthogonalizer cold-start → raw VPIN."""
        ortho = FactorOrthogonalizer(min_history=20)
        vpin = ortho.orthogonalize_vpin(
            vpin_history=np.array([0.5] * 5),
            vs_history=np.array([0.1] * 5),
            rv_history=np.array([0.01] * 5),
            vpin_today=0.6,
            vs_today=0.2,
            rv_today=0.02,
        )
        self.assertAlmostEqual(vpin, 0.6, places=12)

    def test_sufficient_history_returns_residual(self) -> None:
        """V2.1: with sufficient history, returns orthogonalized residual."""
        np.random.seed(42)
        n = 30
        vs = np.random.randn(n)
        rv = np.random.randn(n)
        vpin = 0.5 + 0.3 * vs + 0.2 * rv + np.random.randn(n) * 0.01

        ortho = FactorOrthogonalizer(min_history=20)
        result = ortho.orthogonalize_vpin(
            vpin_history=vpin, vs_history=vs, rv_history=rv,
            vpin_today=1.0, vs_today=0.5, rv_today=0.3,
        )
        # Result should be the residual — should differ from raw 1.0
        self.assertFalse(np.isnan(result))
        self.assertNotAlmostEqual(result, 1.0, places=2)


class TestMicrostructureEngine(unittest.TestCase):
    def test_engine_outputs_expected_keys(self) -> None:
        n = 60
        close_arr = np.full(n, 10.0, dtype=np.float64)
        close_arr[10] = 10.01
        close_arr[30] = 9.99
        snapshots = pd.DataFrame(
            {
                "time": np.arange(n, dtype=np.int64),
                "open": np.full(n, 10.0, dtype=np.float64),
                "high": np.full(n, 10.1, dtype=np.float64),
                "low": np.full(n, 9.9, dtype=np.float64),
                "close": close_arr,
                "volume": np.full(n, 10.0, dtype=np.float64),
                "amount": np.full(n, 100.0, dtype=np.float64),
                "bid1": np.full(n, 9.99, dtype=np.float64),
                "bid1_vol": np.full(n, 100.0, dtype=np.float64),
                "ask1": np.full(n, 10.01, dtype=np.float64),
                "ask1_vol": np.full(n, 100.0, dtype=np.float64),
            }
        )
        with tempfile.TemporaryDirectory() as td:
            config = {
                "microstructure": {
                    "bvc_lookback": 100,
                    "bvc_min_periods": 20,
                    "vpin_n_buckets": 5,
                    "vpin_window": 2,
                    "vpin_premium_threshold": 0.003,
                    "vpin_premium_saturate": 0.01,
                    "kyle_lambda_window": 1200,
                    "feature_history_days": 60,
                    "max_history_days": 60,
                    "factor_history_dir": td,
                }
            }
            eng = MicrostructureEngine(config)
            out = eng.process_daily(
                etf_code="159915.SZ",
                trade_date="20260219",
                snapshots=snapshots,
                premium_rates=pd.Series(np.zeros(n)),
            )
            self.assertIn("raw", out)
            self.assertIn("features", out)
            self.assertIn("diagnostics", out)
            raw = out["raw"]
            feat = out["features"]
            # V2.1 raw keys
            for k in ("vpin_filtered", "vpin_max", "delta_vpin", "ofi_daily",
                       "ofi_skew", "kyle_lambda", "rv_bipower", "vwap_dev",
                       "volume_surprise", "bvc_quality"):
                self.assertIn(k, raw, msg=f"raw missing key: {k}")
            # V2.1 feature keys (backward compat)
            for k in ("vpin_rank", "vpin_max_rank", "ofi_daily_z",
                       "kyle_lambda_z", "vs_max_logz"):
                self.assertIn(k, feat, msg=f"features missing key: {k}")


class TestCrossFactors(unittest.TestCase):
    def test_premium_vpin_cross(self) -> None:
        # V2.1: premium_vpin_cross(premium_rate, vpin_raw)
        self.assertAlmostEqual(premium_vpin_cross(0.0, 0.8), 0.8, places=12)
        self.assertAlmostEqual(premium_vpin_cross(0.002, 0.8), 0.8 * (1.0 - 0.002 / 0.005), places=12)

    def test_profit_ofi_divergence(self) -> None:
        # V2.1: bidirectional — preserves sign of ofi_daily_z
        self.assertAlmostEqual(profit_ofi_divergence(40.0, -1.0), 0.0, places=12)
        # profit_excess = (70-50)/50 = 0.4; result = 0.4 * (-2.0) = -0.8
        self.assertAlmostEqual(profit_ofi_divergence(70.0, -2.0), -0.8, places=12)
        # positive OFI: result = 0.4 * 1.0 = 0.4 (no longer 0.0)
        self.assertAlmostEqual(profit_ofi_divergence(70.0, 1.0), 0.4, places=12)

    def test_asr_velocity(self) -> None:
        # V2.1: returns dict with 1d + 5d
        result = asr_velocity(0.3, 0.2)
        self.assertIsInstance(result, dict)
        self.assertAlmostEqual(result["asr_velocity_1d"], 0.1, places=12)
        self.assertTrue(np.isnan(result["asr_velocity_5d"]))  # no 5d data
        # With 5d data
        result2 = asr_velocity(0.5, 0.4, asr_5d_ago=0.2)
        self.assertAlmostEqual(result2["asr_velocity_5d"], 0.06, places=12)


if __name__ == "__main__":
    unittest.main()
