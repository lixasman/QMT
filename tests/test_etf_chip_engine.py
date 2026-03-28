import unittest

try:
    import numpy as np
except ModuleNotFoundError:
    raise unittest.SkipTest("numpy 未安装，跳过相关测试") from None

from etf_chip_engine.data.tick_adapter import ticks_to_snapshots
from etf_chip_engine.models import ChipDistribution
from etf_chip_engine.modules.diffusion import apply_brownian_diffusion
from etf_chip_engine.modules.iopv_calculator import IOPVCalculator
from etf_chip_engine.modules.maxent_solver import MaxEntSolver


class TestIOPVCalculator(unittest.TestCase):
    def test_iopv_basic(self) -> None:
        etf_info = {
            "reportUnit": 100.0,
            "cashBalance": 10.0,
            "stocks": {
                "000001.SZ": {"componentVolume": 2},
                "000002.SZ": {"componentVolume": 3},
            },
        }
        calc = IOPVCalculator(etf_info)
        calc.update_stock_price("000001.SZ", 5.0)
        calc.update_stock_price("000002.SZ", 10.0)
        iopv = calc.calculate_iopv()
        self.assertAlmostEqual(iopv, (2 * 5.0 + 3 * 10.0 + 10.0) / 100.0, places=12)
        pr = calc.get_premium_rate(0.6)
        self.assertAlmostEqual(pr, (0.6 - iopv) / iopv, places=12)

    def test_iopv_insufficient_coverage(self) -> None:
        etf_info = {
            "reportUnit": 100.0,
            "cashBalance": 0.0,
            "stocks": {
                "000001.SZ": {"componentVolume": 2},
                "000002.SZ": {"componentVolume": 3},
                "000003.SZ": {"componentVolume": 4},
                "000004.SZ": {"componentVolume": 5},
            },
        }
        calc = IOPVCalculator(etf_info)
        calc.update_stock_price("000001.SZ", 5.0)
        iopv = calc.calculate_iopv()
        self.assertTrue(np.isnan(iopv))


class TestMaxEntSolver(unittest.TestCase):
    def test_maxent_mean_matches_vwap(self) -> None:
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.array([1.0, 1.5, 2.0, 2.5], dtype=np.float64)
        vwap = 2.1
        v = solver.solve(grid, vwap, premium_rate=0.0)
        self.assertEqual(v.shape, grid.shape)
        self.assertTrue(np.all(v >= 0))
        self.assertAlmostEqual(float(v.sum()), 1.0, places=10)
        mean = float(np.dot(v, grid))
        self.assertAlmostEqual(mean, vwap, places=6)

    def test_maxent_gamma_changes_shape(self) -> None:
        solver = MaxEntSolver(max_iter=200, tol=1e-12)
        grid = np.array([1.0, 1.5, 2.0, 2.5], dtype=np.float64)
        vwap = 2.1
        v0 = solver.solve(grid, vwap, premium_rate=0.0)
        v1 = solver.solve(grid, vwap, premium_rate=0.001, k_gamma=500.0, gamma_max=2.0)
        self.assertAlmostEqual(float(np.dot(v0, grid)), vwap, places=6)
        self.assertAlmostEqual(float(np.dot(v1, grid)), vwap, places=6)
        self.assertGreater(float(np.abs(v1 - v0).sum()), 1e-6)


class TestTickAdapter(unittest.TestCase):
    def test_ticks_to_snapshots_diff(self) -> None:
        dt = np.dtype(
            [
                ("time", "f8"),
                ("lastPrice", "f8"),
                ("high", "f8"),
                ("low", "f8"),
                ("amount", "f8"),
                ("volume", "f8"),
            ]
        )
        ticks = np.array(
            [
                (1.0, 1.0, 1.0, 1.0, 10.0, 100.0),
                (2.0, 1.01, 1.01, 1.0, 30.0, 140.0),
                (3.0, 1.02, 1.02, 1.0, 30.0, 140.0),
            ],
            dtype=dt,
        )
        df = ticks_to_snapshots(ticks)
        self.assertEqual(
            list(df.columns),
            ["time", "open", "high", "low", "close", "volume", "amount", "bid1", "bid1_vol", "ask1", "ask1_vol"],
        )
        self.assertEqual(len(df), 3)
        self.assertAlmostEqual(float(df.loc[0, "open"]), 1.0)
        self.assertAlmostEqual(float(df.loc[1, "open"]), 1.0)
        self.assertAlmostEqual(float(df.loc[2, "open"]), 1.01)
        self.assertAlmostEqual(float(df.loc[0, "volume"]), 0.0)
        self.assertAlmostEqual(float(df.loc[1, "volume"]), 4000.0)
        self.assertAlmostEqual(float(df.loc[1, "amount"]), 20.0)
        self.assertAlmostEqual(float(df.loc[2, "volume"]), 0.0)
        self.assertAlmostEqual(float(df.loc[2, "amount"]), 0.0)
        self.assertAlmostEqual(float(df.loc[1, "bid1"]), 0.0)
        self.assertAlmostEqual(float(df.loc[1, "ask1"]), 0.0)


class TestDiffusion(unittest.TestCase):
    def test_diffusion_conserves_mass(self) -> None:
        chips = ChipDistribution(etf_code="TEST.SZ", base_price=1.0, bucket_size=0.001)
        chips.chips = np.zeros(1000, dtype=np.float32)
        chips.chips[500] = 1000.0
        total_before = float(chips.chips.sum())
        try:
            apply_brownian_diffusion(chips, atr=0.02, k_diff=0.08)
        except RuntimeError:
            self.skipTest("scipy 未安装，跳过 diffusion 测试")
        total_after = float(chips.chips.sum())
        self.assertAlmostEqual(total_before, total_after, places=3)


if __name__ == "__main__":
    unittest.main()
