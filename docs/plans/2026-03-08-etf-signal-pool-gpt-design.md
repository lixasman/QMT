# ETF 情绪分析候选池扩展与 GPT 切换设计

## 1. 背景与目标

- 当前批量情绪分析链路基于 `finintel/main.py` 的 `--signal-hot-top`，默认从热门 ETF 中筛选 `15` 只进行分析。
- 最新回测表明，固定的 50 只 ETF 候选池已具备可接受的长期表现，因此需要把“市场热度”与“固定池趋势突破”组合起来，作为次日交易池来源。
- 本次设计目标是：在不改变现有主入口和输出结构的前提下，将批量情绪分析升级为“热门 Top10 + 固定 50 池中当日涨幅大于 1% 的 ETF 的并集分析”，同时加入自动清理与 GPT 兼容接口切换。

## 2. 已确认输入

- 固定 50 只 ETF 清单文件：`backtest/default_universe_50.txt`
- 文件格式：每行一个 ETF 代码，当前内容为标准 `XXXXXX.SH/SZ` 形式
- 热门池口径：保留现有热度选择逻辑，仅将默认数量由 `15` 下调为 `10`
- 补充池口径：仅在这 50 只 ETF 内，按最新交易日 `close / prev_close - 1 > 1%` 筛选
- 最终分析池：热门 `Top10` 与固定 50 池上涨 `>1%` ETF 的并集、去重
- 历史清理范围：单 ETF 分析输出、评价文件、热门池汇总文件、批量总结果文件一起清理
- LLM 切换目标：暂时以 GPT 兼容接口替代当前 DeepSeek 默认配置
  - `model = "gpt-5.4"`
  - `base_url = "https://www.leishen-ai.cn/openai"`
  - `api_key_env = "CRS_OAI_KEY"`

## 3. 方案选择

已选方案：**方案 A——在现有热门池流程上追加“固定 50 池涨幅补充”**。

选择理由：

- 保留既有入口 `--signal-hot-top`，运行方式对现有日常调度最友好
- 复用当前热门池、批量分析、文件输出与次日交易池衔接逻辑
- 变更面最小，风险最低
- 输出目录和文件命名可基本保持兼容，减少对下游消费逻辑的冲击

不采用的方案：

- 单独新增“固定池增强模式”：语义更清晰，但会引入新的运维入口
- 全量配置化：长期更灵活，但本次需求会被做重，不符合最小改动原则

## 4. 架构与数据流

保留 `finintel/main.py` 中 `--signal-hot-top` 作为唯一批量入口，并将其升级为统一编排器。

新的数据流如下：

1. 通过现有 `select_top_hot_etfs(...)` 选出热门 `Top10`
2. 读取 `backtest/default_universe_50.txt`
3. 使用现有日线数据链路，仅对这 50 只 ETF 取最新两根有效日线，计算最新交易日涨幅
4. 筛出涨幅 `> 1%` 的固定池 ETF
5. 将“热门 Top10”与“固定池上涨 ETF”做并集并按 ETF 代码去重
6. 对最终池统一执行 `run_etf_signal_pipeline(...)`
7. 保持现有单 ETF 输出、汇总 CSV、批量总 JSON 的命名风格
8. 在本次批量任务完成后，执行一次“保留最近 3 天”的历史文件清理

建议职责划分：

- `finintel/main.py`
  - 批量任务总调度
  - 合并热门池与固定池上涨补充池
  - 为汇总结果增加“来源标记”字段
  - 执行历史清理
- `finintel/etf_selector.py`
  - 保留现有热门 ETF 筛选能力
  - 新增固定 50 池文件读取与涨幅筛选辅助函数
- `finintel/deepseek_client.py`
  - 保持 OpenAI 兼容请求结构
  - 调整默认环境变量读取与默认模型/网关
- `strategy_config.py`
  - 将默认 `hot_top` 从 `15` 改为 `10`

## 5. 规则定义

### 5.1 热门池规则

- 继续使用现有热门 ETF 排名与主题分散逻辑
- 默认 `hot_top = 10`
- 热门池仍为批量分析的基础池

### 5.2 固定 50 池涨幅补充规则

- 读取 `backtest/default_universe_50.txt`
- 仅在这 50 只 ETF 范围内判断是否纳入补充池
- 涨幅定义为：最新交易日 `close / prev_close - 1`
- 纳入条件：涨幅严格大于 `1%`
- 取不到最近两根有效日线、停牌、数据缺失的 ETF 直接跳过，并记录 warning

### 5.3 最终分析池规则

- 最终分析池 = `热门 Top10 ∪ 固定50池上涨>1% ETF`
- 按 ETF 代码去重
- 若同一 ETF 同时命中两种来源，仅分析一次
- 汇总结果中保留来源字段，建议值如下：
  - `hot`
  - `universe_up_gt_1pct`
  - `hot+universe_up_gt_1pct`

### 5.4 历史清理规则

- 每次批量分析完成后执行一次
- 删除“早于 3 天前”的历史输出
- 清理范围包括：
  - `output/finintel_signal_*.json`
  - `output/finintel_signal_*.md`
  - `output/eval/finintel_signal_eval_*`
  - `output/finintel_signal_hot_*.csv`
  - 批量总 JSON 等 `finintel_signal*` 同类产物
- 仅匹配 `finintel_signal*` 命名模式，避免误删其他模块输出

### 5.5 LLM 切换规则

- 暂时不再使用当前 DeepSeek 默认环境变量作为首选配置
- 默认切换为：
  - 环境变量：`CRS_OAI_KEY`
  - 默认网关：`https://www.leishen-ai.cn/openai`
  - 默认模型：`gpt-5.4`
- 若 `CRS_OAI_KEY` 缺失，则初始化阶段直接报错终止
- 保留现有重试、退避、JSON 降级重试逻辑

## 6. 异常处理与容错

- `backtest/default_universe_50.txt` 缺失：批量任务直接失败，避免静默降级
- 单只 ETF 日线数据不足：跳过该 ETF，不中断整批任务
- `CRS_OAI_KEY` 未设置：LLM 客户端初始化直接失败
- GPT 网关调用失败：沿用现有重试与指数退避
- 历史清理失败：仅记录 warning，不影响当天情绪分析结果
- 已存在当日结果的 ETF：保留当前“跳过已生成文件”的幂等行为

## 7. 验证思路

需要覆盖的核心验证点：

- 固定 50 池文件可以正确解析为 ETF 代码列表
- 最新交易日涨幅 `>1%` 的筛选逻辑正确
- 热门池与补充池的并集和去重逻辑正确
- 汇总结果中可以识别 ETF 的来源字段
- 历史清理仅删除超出保留期且命中 `finintel_signal*` 模式的文件
- LLM 客户端默认读取 `CRS_OAI_KEY`，并使用 `gpt-5.4` 与 `https://www.leishen-ai.cn/openai`
- 默认 `hot_top` 已调整为 `10`

## 8. 影响范围

预期最小改动文件：

- `finintel/main.py`
- `finintel/etf_selector.py`
- `finintel/deepseek_client.py`
- `strategy_config.py`
- `tests/` 下新增或修改的 FinIntel 相关测试文件
- 如实施阶段需要对外说明行为变更，可补充更新 `README.md`

## 9. 非目标

- 不新增全新的批量运行入口
- 不修改依赖文件或执行依赖安装/升级
- 不改变现有单 ETF 输出的主命名规范
- 不重构现有热门 ETF 评分模型
- 不在本次需求中扩展到股票池或其他资产类别

## 10. 开放问题

- 无。关键口径均已确认。

