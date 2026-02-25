import unittest
from unittest import mock

try:
    import requests
except ModuleNotFoundError:
    raise unittest.SkipTest("requests 未安装，跳过相关测试") from None

from newsget.models import NewsItem
from newsget.sources.eastmoney_etf import EtfHolding
from newsget.sources.eastmoney_stock import StockLatestNews
from newsget.sources.cls_search import ClsSearchHit

from finintel.etf_pipeline import run_etf_pipeline


class FakeDeepSeek:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, *, system: str, user: str, temperature: float = 0.0, force_json: bool = False) -> str:
        self.calls.append({"system": system, "temperature": temperature, "force_json": force_json})
        if force_json:
            return '{"top_3":[{"rank":1,"summary":"S1","reason":"R1"}]}'
        return "S1"


class TestFinIntelEtfPipeline(unittest.TestCase):
    def test_run_etf_pipeline_with_mocks(self) -> None:
        holdings = [EtfHolding(stock_code="300750", stock_name="宁德时代")]
        deepseek = FakeDeepSeek()
        session = object()

        with mock.patch("finintel.etf_pipeline.fetch_etf_top10_holdings", return_value=("ETF测试", "2025-12-31", holdings)):
            with mock.patch(
                "finintel.etf_pipeline.fetch_stock_latest_news_from_akshare",
                return_value=mock.Mock(title="AK新闻标题", url="https://finance.eastmoney.com/a/1.html", publish_time="2026-02-08 10:00:00", content="正文"),
            ):
                out = run_etf_pipeline(session, deepseek, etf_code="510300", debug=False, max_workers=2, etf_source="akshare")

        self.assertEqual(out["etf_code"], "510300")
        self.assertIn("top_3", out)
        self.assertEqual(len(out["top_3"]), 1)


if __name__ == "__main__":
    unittest.main()
