# sentiment-history 设计

日期：2026-03-18

## 1. 背景
用户每天运行 `python -m finintel --signal-hot-top 10 --signal-hot-all-50 --no-trace`，希望将 50 只 ETF 的情绪评级长期累计保存，以便后续回测使用。

## 2. 目标
- 每次运行批量情绪因子后，将当日 50 只 ETF 的情绪评级追加到一个累计 CSV。
- 文件路径固定为：`output/finintel_50ETF_sentiment_history/finintel_sentiment_history.csv`。
- 至少包含 `date`、`code`、`grade`（可额外带 `name`、`confidence`）。
- 同一天重复运行时按 `date+code` 去重，保留最新结果。

## 3. 非目标
- 不引入数据库或新依赖。
- 不改变既有情绪评分/生成逻辑。
- 不改变现有输出文件格式。

## 4. 设计概述
### 4.1 数据来源
复用 `--signal-hot-top` 批量流程中的 `summary_rows`：已包含 `code`、`name`、`grade`、`confidence`。

### 4.2 累计写入与去重
新增函数（如 `_append_hot_etf_sentiment_history(rows, day)`）：
- 生成目标目录与文件。
- 构造 DataFrame：`date, code, grade, name, confidence`。
- 若文件存在：读取旧数据并与新数据拼接，再按 `date+code` 去重（保留最新）。
- 按 UTF-8-SIG 保存，方便 Excel 直接打开。

### 4.3 调用位置
在批量流程末尾（`_emit_hot_etf_sentiment_summary` 之后）调用该写入函数。

## 5. 兼容性
- 默认行为不变，仅新增累计文件输出。
- 若目录/文件不存在将自动创建。

## 6. 验证方式
- 单测：构造历史文件与新数据，确认去重后行数正确。
- 手工：运行命令后检查历史文件中 `date+code` 唯一且包含当日 50 行。
