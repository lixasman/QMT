## 目标

* 编写一个可直接运行的爬虫程序：抓取“财联社热度前五新闻”与“东方财富热度前五新闻”，并输出结构化结果（控制台 + JSON 文件）。

## 技术选型

* 使用 Python（requests + 解析模块）。

* 原则：优先调用站点公开的 JSON 接口；若接口变动/不可用，则回退到解析网页 HTML。

## 数据口径（Top5）

* 以两站各自“热榜/热度榜/热门资讯”的排序为准，取前 5 条。

* 每条新闻统一输出字段：source（站点）、rank、title、url、hot（热度/阅读/评论等平台提供的热度字段）、publish\_time（若能拿到）、crawl\_time。

## 财联社抓取方案

* **优先 API**：探测并调用财联社公开接口（常见域名为 api3.cls.cn / cls.cn 的 XHR JSON）。

  * 实现方式：先请求财联社对应热榜页面/入口页，解析页面中引用的脚本或配置，提取实际 XHR 接口地址与必要参数；再用 requests 携带常见浏览器请求头（User-Agent/Accept/Referer）请求 JSON。

* **回退 HTML**：若 JSON 不可用，直接抓取热榜页面 HTML，解析出热度排序前 5 的标题与链接。

## 东方财富抓取方案

* **优先官方热榜页**：以东方财富“热榜”入口页为起点（可见页面包含“资讯”tab）。

* **接口定位**：该页通常由前端脚本请求接口填充列表。

  * 实现方式：抓取该页面引用的 JS（例如 stockranking 的静态脚本），在脚本中扫描/匹配与“资讯/新闻/Information/News/ranking/collectapi”等相关的接口路径；找到后直接请求接口拿到 JSON。

* **回退 HTML**：若接口定位失败，则解析页面渲染前的 HTML（或抓取可直接输出榜单的备用页面）提取前 5。

## 工程结构（从零初始化，因为当前 d:\NewsGet 目录为空）

* 新建最小可运行项目：

  * pyproject.toml 或 requirements.txt

  * newsget/（包）

    * main.py（CLI 入口）

    * http.py（会话、超时、重试、通用请求头）

    * models.py（统一数据结构）

    * sources/cls.py（财联社抓取器）

    * sources/eastmoney.py（东方财富抓取器）

  * README.md（运行方式与输出示例）

## 反爬与稳定性

* 加入：超时、重试（指数退避）、简单限速（sleep）、必要请求头（User-Agent/Referer）。

* 对返回内容做健壮解析：

  * JSON：兼容 data/list/items 等不同字段名；缺字段时置空。

  * HTML：使用选择器/正则多方案兜底。

## 输出与运行方式

* CLI：`python -m newsget` 默认抓取两站并打印。

* 同时写入 `output/<timestamp>.json`（包含两站合并结果）。

## 验证

* 本地运行一次抓取，确认两站各返回 5 条。

* 添加最小的解析单元测试（可选）：用固定的样例响应（离线）验证字段抽取逻辑。

## 交付内容

* 可运行的 Python 爬虫代码 + 依赖声明 + README 使用说明 + 示例输出格式。

