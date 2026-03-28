# HotETF 情绪评级终端汇总 设计

## 背景

`python -m finintel --signal-hot-top N --no-trace` 会为候选 ETF 生成当日情绪因子输出，但终端日志中缺少当日评级的直观汇总，用户需要快速查看 A/B 等级及对应分数。

## 目标

- 批量模式结束后在 stderr 输出当日候选 ETF 的情绪评级汇总。
- 汇总包含：代码、名称（若有）、评级（A-E）、分数（优先用模型分数）、置信度。
- 不影响 stdout 的 JSON 输出结构与现有管道。
- 跳过已生成的 ETF 也能显示当日评级与分数。

## 非目标

- 不修改 CSV 输出结构。
- 不引入新的命令行参数或配置项。
- 不改变单只 `--signal-etf` 的输出行为。

## 架构概述

在 `finintel/main.py` 的 `--signal-hot-top` 分支新增汇总器，按候选池顺序采集每只 ETF 的评级与分数，并在批量流程结束后用 `logging.warning` 输出到 stderr。

## 数据来源与降级

优先级从高到低：

1. 当前 `run_etf_signal_pipeline` 返回的 `sentiment_struct`。
2. 当日 `output/integration/finintel/sentiment_<etf6>_<YYYYMMDD>.json`。
3. 当日 `output/finintel_signal_<etf6>_<YYYYMMDD>.json` 中的 `deepseek_output`，用现有映射逻辑生成评级与分数。
4. 仍失败则标记为 `N/A`。

跳过已生成的 ETF 直接走第 2 或第 3 级读取路径。

## 输出格式

stderr 示例：

```
HotETF Summary 20260318 (n=12)
510300.SH 沪深300 A 85 HIGH
159107.SZ 成长ETF B 70 MEDIUM
```

名称为空时省略名称字段但保留分隔空格。

## 错误处理

读取文件失败或格式异常时，降级到下一级数据源；所有降级通过 warning 级别日志提示但不终止批量流程。

## 测试策略

- 新增单元测试覆盖：从现有文件读取评级与分数、降级路径、跳过 ETF 的汇总。
- 复用 `tests/test_finintel_hot_signal_batch.py` 的结构，使用临时目录与 monkeypatch 避免真实网络调用。
