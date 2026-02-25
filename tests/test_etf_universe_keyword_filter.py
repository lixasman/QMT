from __future__ import annotations

import pandas as pd

import etf_chip_engine.data.xtdata_provider as xtp


def test_get_industry_etf_universe_excludes_requested_keywords(monkeypatch) -> None:
    src = pd.DataFrame(
        {
            "code": ["510300.SH", "159934.SZ", "159920.SZ", "513500.SH"],
            "name": ["\u6caa\u6df1300ETF", "\u9ec4\u91d1ETF", "\u6052\u751fETF", "\u6807\u666e500ETF"],
        }
    )
    monkeypatch.setattr(xtp, "get_all_etf_universe", lambda: src)

    out = xtp.get_industry_etf_universe()

    assert set(out["code"].tolist()) == {"510300.SH", "159920.SZ"}


def test_get_industry_etf_universe_keyword_match_is_whitespace_and_case_insensitive(monkeypatch) -> None:
    src = pd.DataFrame(
        {
            "code": ["A.SH", "B.SH", "C.SH", "D.SH"],
            "name": [
                "\u7eb3 \u65af \u8fbe \u514b100ETF",
                "\u6210\u957fETF qdii",
                "\u6210\u957fETF QDII",
                "\u666e\u901a\u5bbd\u57faETF",
            ],
        }
    )
    monkeypatch.setattr(xtp, "get_all_etf_universe", lambda: src)

    out = xtp.get_industry_etf_universe(exclude_keywords=["\u7eb3\u65af\u8fbe\u514b", "QDII"])

    assert out["code"].tolist() == ["D.SH"]


def test_filter_etf_codes_by_keywords_keeps_unknown_and_drops_matched(monkeypatch) -> None:
    src = pd.DataFrame(
        {
            "code": ["510300.SH", "159934.SZ", "513500.SH"],
            "name": ["\u6caa\u6df1300ETF", "\u9ec4\u91d1ETF", "\u6807\u666e500ETF"],
        }
    )
    monkeypatch.setattr(xtp, "get_all_etf_universe", lambda: src)

    out = xtp.filter_etf_codes_by_keywords(["510300.SH", "159934.SZ", "000001.SZ"])

    assert out == ["510300.SH", "000001.SZ"]
