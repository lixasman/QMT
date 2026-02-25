import unittest

try:
    import requests
except ModuleNotFoundError:
    raise unittest.SkipTest("requests 未安装，跳过相关测试") from None

from newsget.sources.cls import _extract_cls_content_from_html
from newsget.sources.eastmoney import _extract_eastmoney_content_from_html


class TestContentParsers(unittest.TestCase):
    def test_extract_eastmoney_content(self) -> None:
        html = """
        <html><body>
          <div id="ContentBody">
            <p>第一段</p>
            <p>第二段</p>
            <p class="em_media">（文章来源：测试来源）</p>
          </div>
          <div class="zwothers"><span>原标题：不应出现</span></div>
        </body></html>
        """
        text = _extract_eastmoney_content_from_html(html)
        self.assertIsNotNone(text)
        self.assertIn("第一段", text or "")
        self.assertNotIn("原标题：", text or "")

    def test_extract_cls_content(self) -> None:
        html = """
        <html><body>
          <div class="header">导航</div>
          <div class="content">
            <p>财联社讯：第一段</p>
            <p>第二段</p>
          </div>
          <div class="comment">张三 回复 李四：不错</div>
        </body></html>
        """
        text = _extract_cls_content_from_html(html)
        self.assertIsNotNone(text)
        self.assertIn("第一段", text or "")
        self.assertNotIn("回复", text or "")


if __name__ == "__main__":
    unittest.main()
