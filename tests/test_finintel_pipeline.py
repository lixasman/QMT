import unittest
from unittest import mock

try:
    import requests
except ModuleNotFoundError:
    raise unittest.SkipTest("requests 未安装，跳过相关测试") from None

from newsget.models import NewsItem

from finintel.pipeline import extract_json_object, render_prompt, run_pipeline


class FakeDeepSeek:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, *, system: str, user: str, temperature: float = 0.0, force_json: bool = False) -> str:
        self.calls.append({"system": system, "user": user, "temperature": temperature, "force_json": force_json})
        if force_json:
            return '{"top_3":[{"rank":1,"summary":"S1","reason":"R1"},{"rank":2,"summary":"S2","reason":"R2"},{"rank":3,"summary":"S3","reason":"R3"}]}'
        return "S1"


class TestFinIntelPipeline(unittest.TestCase):
    def test_render_prompt(self) -> None:
        tpl = "A={{raw_news_text}} B={{summaries_list}}"
        out = render_prompt(tpl, raw_news_text="X", summaries_list="Y")
        self.assertEqual(out, "A=X B=Y")

    def test_extract_json_object(self) -> None:
        obj = extract_json_object('{"a":1}')
        self.assertEqual(obj["a"], 1)
        obj2 = extract_json_object("xxx {\"b\":2} yyy")
        self.assertEqual(obj2["b"], 2)

    def test_run_pipeline_with_mocks(self) -> None:
        fake_items = [
            NewsItem(source="财联社", rank=1, title="T1", url="U1", content="C1", crawl_time="c"),
            NewsItem(source="东方财富", rank=1, title="T2", url="U2", content="C2", crawl_time="c"),
        ]

        deepseek = FakeDeepSeek()
        session = object()

        with mock.patch("finintel.pipeline.fetch_top10_news", return_value=fake_items):
            out = run_pipeline(session, deepseek, debug=False, max_workers=2)
        self.assertIn("top_3", out)
        self.assertEqual(len(out["top_3"]), 3)
        self.assertEqual(out["top_3"][0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
