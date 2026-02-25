import unittest
from datetime import datetime, timedelta, timezone

try:
    import requests
except ModuleNotFoundError:
    raise unittest.SkipTest("requests 未安装，跳过相关测试") from None

from newsget.sources.eastmoney_etf import _extract_apidata_content_html, _extract_first_table_holdings
from newsget.sources.eastmoney_stock import _parse_jsonp


class TestEtfFeatures(unittest.TestCase):
    def test_parse_etf_holdings_apidata(self) -> None:
        raw = (
            'var apidata={ content:"<div><h4><a title=\\\'ETF测试\\\'>ETF测试</a></h4>'
            "<table><tr><th>序号</th><th>股票代码</th><th>股票名称</th></tr>"
            "<tr><td>1</td><td>600519</td><td>贵州茅台</td></tr>"
            "<tr><td>2</td><td>300750</td><td>宁德时代</td></tr>"
            "</table></div>\",arryear:[2025] }"
        )
        html = _extract_apidata_content_html(raw)
        self.assertIsNotNone(html)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        holdings = _extract_first_table_holdings(soup, topline=10)
        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0].stock_code, "600519")

    def test_parse_jsonp(self) -> None:
        obj = _parse_jsonp('cb({"Data":[{"Title":"t","Url":"https://x","DateTime":"2026-02-08 10:00:00"}]})')
        self.assertIsInstance(obj, dict)
        self.assertIn("Data", obj)


if __name__ == "__main__":
    unittest.main()
