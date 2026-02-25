## 现状定位（已确认）
- finintel 当前“获利盘比例”来自 `price_rank_60d`（60 日收盘分位），在 [etf_signal_pipeline.py](file:///d:/Quantitative_Trading/finintel/etf_signal_pipeline.py#L796-L799) 计算并写入 `fields["price_rank_60d"]`。
- `PROMPT_ETF_SIGNAL` 在 [prompts.py](file:///d:/Quantitative_Trading/finintel/prompts.py#L107-L198) 中使用 `{price_rank_60d}` 渲染。

## 目标改动
- 将 prompt 中“获利盘比例”从“60日收盘分位”**替换为 etf_chip_engine 日批量产出的筹码口径**：
  - `profit_ratio` → `chip_profit_ratio`
  - `dense_zones` → `chip_dense_zones`
  - `asr` → `chip_asr`
- 保留 `price_rank_60d`，但改名为“价格分位(60日收盘)”或“位置分位”，不再占用“获利盘比例”的表达。

## 技术方案
### 1) 在 finintel 侧加入“读取日批量 CSV”的适配层（不依赖 pandas）
- 新增 `load_chip_factors(etf_code_norm)`（可放在 `finintel/etf_signal_pipeline.py` 或独立 `finintel/chip_factors.py`）：
  - 查找 `etf_chip_engine/data/batch_results_YYYYMMDD.csv` 的最新文件（或用环境变量 `CHIP_BATCH_CSV` 指定绝对路径）。
  - 用 `csv.DictReader` 按 `code` 精确匹配（例如 `560780.SH`）。
  - `dense_zones`：先 `json.loads`，失败则 `ast.literal_eval`，并格式化为短文本（限制长度，避免 prompt 膨胀）。

### 2) 缺失时必须有告警输出（你要求的）
- 在以下情况使用 `logging.getLogger(__name__).warning(...)` 输出明确告警（包含 etf_code、缺失原因、fallback 行为）：
  - 找不到任何 `batch_results_*.csv`
  - 找到 CSV 但找不到该 ETF 行
  - 该 ETF 行存在但关键字段为空/不可解析（profit_ratio/asr/dense_zones）
- 可选：增加一个环境变量开关 `CHIP_FACTOR_STRICT=1`：
  - 严格模式下缺失直接 raise（用于你想强制发现问题的场景）；默认模式只 warning 并把字段填成“数据缺失”。

### 3) 将筹码字段写入 fields 并用于 prompt 渲染
- 在 [run_etf_signal_pipeline](file:///d:/Quantitative_Trading/finintel/etf_signal_pipeline.py#L1006-L1126) 中，`fields` 生成后、`PROMPT_ETF_SIGNAL.format_map(fields)` 之前：
  - `chip = load_chip_factors(etf_code_norm)`
  - `fields.update(chip)`
- 统一新增 keys：
  - `chip_trade_date`、`chip_profit_ratio`、`chip_dense_zones`、`chip_asr`

### 4) 修改 prompts.py：确保“替换已完成”
- 把原行：`- **获利盘比例**： 当前价格处于近 60 日 {price_rank_60d}% 分位`
- 改为（示例）：
  - `- **获利盘比例(筹码口径)**：{chip_profit_ratio}%（{chip_trade_date}）`
  - `- **筹码密集区**：{chip_dense_zones}`
  - `- **ASR 因子**：{chip_asr}`
- 同节新增保留原分位但改名：
  - `- **价格分位(60日收盘)**：{price_rank_60d}%`

## 自测与验收（你要求的两点：替换确认 + 不再用60日收盘当获利盘）
- 增加一个可运行的自测入口（例如 `python -m finintel --signal-etf 560780 --timing` 或单独 debug 开关）：
  1) 运行后在日志里打印：`chip_profit_ratio/chip_trade_date` 的取值来源文件名（CSV 路径）
  2) 对生成的 `prompt` 做断言/检查：
     - 必须包含 `获利盘比例(筹码口径)` 或 `{chip_profit_ratio}` 渲染结果
     - 必须不再出现旧文案 `获利盘比例： 当前价格处于近 60 日`（即确认替换真的生效）
- 若处于缺失 fallback：
  - 也必须在日志里看到 warning（方便你及时发现），并且 prompt 中“筹码口径获利盘”显示为“数据缺失”。

## 预计改动文件
- 修改：`finintel/prompts.py`（替换占位符与文案）
- 修改：`finintel/etf_signal_pipeline.py`（读取 CSV、填充 fields、缺失告警、自测断言）
- 可选新增：`finintel/chip_factors.py`（把 CSV 解析逻辑独立出来，便于维护）

确认后我会按上述方案落地代码，并跑一次信号流程，给出：1) 日批量因子成功读到的日志；2) prompt 片段证明“获利盘比例”已换成筹码口径。