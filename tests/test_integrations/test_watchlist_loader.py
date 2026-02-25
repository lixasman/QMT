from __future__ import annotations

from datetime import datetime
from pathlib import Path

from integrations.watchlist_loader import load_watchlist_items


def test_load_watchlist_items_normalizes_etf_code(tmp_path: Path) -> None:
    chip_dir = tmp_path / "chip"
    fin_dir = tmp_path / "finintel"
    chip_dir.mkdir(parents=True, exist_ok=True)
    fin_dir.mkdir(parents=True, exist_ok=True)

    (chip_dir / "batch_results_20260209.csv").write_text(
        "code,trade_date,profit_ratio,dpc_peak_density,chip_engine_days\n"
        "512480,20260209,82.3,0.12,15\n",
        encoding="utf-8",
    )

    (fin_dir / "sentiment_512480_20260209.json").write_text(
        '{"sentiment_score_01": 0.65, "sentiment_score_100": 70}',
        encoding="utf-8",
    )

    now = datetime.fromisoformat("2026-02-10T10:00:00")
    res = load_watchlist_items(etf_codes=["512480"], now=now, integration_dir=tmp_path)

    assert len(res.items) == 1
    assert res.items[0].etf_code == "512480.SH"

    assert "512480.SH" in res.ext_factors
    assert "512480" in res.ext_factors
