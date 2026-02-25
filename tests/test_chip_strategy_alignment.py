import math
import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("numpy 未安装，跳过相关测试") from None

from etf_chip_engine.models import ChipDistribution
from etf_chip_engine.modules.iopv_calculator import IOPVCalculator
from etf_chip_engine.modules.redemption import RedemptionCorrector


class TestChipStrategyAlignment(unittest.TestCase):
    def test_iopv_extrapolates_by_component_count(self) -> None:
        etf_info = {
            "stocks": {f"{i:06d}.SZ": {"componentVolume": 1} for i in range(10)},
            "cashBalance": 10.0,
            "reportUnit": 100.0,
        }
        calc = IOPVCalculator(etf_info)
        for i in range(5):
            calc.update_stock_price(f"{i:06d}.SZ", 10.0)
        iopv = calc.calculate_iopv()
        self.assertTrue(math.isfinite(iopv))
        self.assertAlmostEqual(iopv, 1.1, places=7)

    def test_iopv_returns_nan_when_coverage_below_threshold(self) -> None:
        etf_info = {
            "stocks": {f"{i:06d}.SZ": {"componentVolume": 1} for i in range(10)},
            "cashBalance": 0.0,
            "reportUnit": 100.0,
        }
        calc = IOPVCalculator(etf_info)
        for i in range(4):
            calc.update_stock_price(f"{i:06d}.SZ", 10.0)
        self.assertTrue(math.isnan(calc.calculate_iopv()))

    def test_creation_injection_uses_sigma_5_buckets(self) -> None:
        chips = ChipDistribution(
            etf_code="TEST.SZ",
            base_price=0.0,
            bucket_size=0.001,
            chips=np.zeros(3001, dtype=np.float32),
            total_shares=0.0,
        )
        corrector = RedemptionCorrector()
        corrector.apply_creation(chips, delta_shares=1000.0, vwap=1.0)

        idx = chips.price_to_index(1.0)
        self.assertGreater(chips.chips[idx], 0.0)
        ratio = float(chips.chips[idx + 5] / chips.chips[idx])
        self.assertAlmostEqual(ratio, math.exp(-0.5), delta=0.02)
        self.assertAlmostEqual(float(chips.chips.sum()), 1000.0, delta=1e-3)
        self.assertEqual(float(chips.chips[idx + 16]), 0.0)

    def test_engine_process_daily_smoke(self) -> None:
        import pandas as pd

        from etf_chip_engine.config import CONFIG
        from etf_chip_engine.engine import ETFChipEngine, Snapshot

        eng = ETFChipEngine(CONFIG)
        daily = pd.DataFrame(
            [
                {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000},
                {"open": 1.0, "high": 1.02, "low": 0.98, "close": 1.01, "volume": 1200},
            ]
        )
        eng.cold_start("TEST.SZ", daily)
        eng.chips["TEST.SZ"].total_shares = 1_000_000.0

        snaps = pd.DataFrame(
            [
                {"high": 1.001, "low": 0.999, "close": 1.0, "volume": 100.0, "amount": 100.0},
                {"high": 1.003, "low": 0.998, "close": 1.002, "volume": 200.0, "amount": 200.4},
                {"high": 1.004, "low": 1.0, "close": 1.003, "volume": 150.0, "amount": 150.45},
            ]
        )
        out = eng.process_daily("TEST.SZ", snaps, shares_today=1_000_100.0, shares_yesterday=1_000_000.0, atr=0.02)
        self.assertIn("profit_ratio", out)
        self.assertIn("dense_zones", out)
        self.assertIn("asr", out)

        out2 = eng.process_snapshot("TEST.SZ", Snapshot(high=1.005, low=1.000, close=1.002, volume=50.0, amount=50.1))
        self.assertIn("profit_ratio", out2)
        self.assertIn("dense_zones", out2)


if __name__ == "__main__":
    unittest.main()
