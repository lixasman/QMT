from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / 'scripts' / 'plot_backtest_hourly_svg.py'
    spec = importlib.util.spec_from_file_location('plot_backtest_hourly_svg_test', module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _write_xt_tick_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['code', 'time', 'current'])
        writer.writeheader()
        writer.writerow({'code': '159755', 'time': '20250715092500', 'current': '0.990'})
        writer.writerow({'code': '159755', 'time': '20250715093000', 'current': '1.010'})
        writer.writerow({'code': '159755', 'time': '20250715103000', 'current': '1.030'})
        writer.writerow({'code': '159755', 'time': '20250715150100', 'current': '1.040'})


def test_plot_hourly_svg_supports_nested_tick_data_and_xt_columns(tmp_path: Path) -> None:
    tick_root = tmp_path / 'tick_data'
    _write_xt_tick_csv(tick_root / '2025' / '07' / '2025-07-15' / '159755.csv')

    trade_days = mod._list_trade_days(tick_root=tick_root, start='20250715', end='20250715')
    points, covered_days = mod._load_hourly_close_for_code(
        tick_root=tick_root,
        trade_days=trade_days,
        code='159755.SZ',
    )

    assert trade_days == ['20250715']
    assert covered_days == 1
    assert points == [
        (datetime(2025, 7, 15, 9, 30), 1.01),
        (datetime(2025, 7, 15, 10, 30), 1.03),
    ]
