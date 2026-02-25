from __future__ import annotations

import unittest
from datetime import datetime

from newsget.models import NewsItem, now_iso, pick_first


class TestModels(unittest.TestCase):
    def test_now_iso_is_iso8601(self) -> None:
        s = now_iso()
        dt = datetime.fromisoformat(s)
        self.assertIsNotNone(dt.tzinfo)

    def test_pick_first(self) -> None:
        self.assertEqual(pick_first({"a": None, "b": "x"}, ["a", "b"]), "x")
        self.assertIsNone(pick_first({"a": "", "b": None}, ["a", "b"]))

    def test_news_item_to_dict(self) -> None:
        it = NewsItem(source="s", rank=1, title="t", url="u", crawl_time="c")
        d = it.to_dict()
        self.assertEqual(d["source"], "s")
        self.assertEqual(d["rank"], 1)


if __name__ == "__main__":
    unittest.main()

