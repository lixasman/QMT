# GPT Fallback to DeepSeek Design

Date: 2026-03-16

## Summary
- 当 `DeepSeekClient` 以 GPT（OpenAI-compat）作为主通道时，若 `chat()` 发生任何异常，自动降级为 DeepSeek legacy 通道并记录 warning 日志。
- 不修改现有输出结构与 CLI 参数。

## Scope
- 影响范围：所有通过 `DeepSeekClient.chat()` 发起的 GPT 调用（含 `--signal-hot-top`）。
- 入口：`finintel/deepseek_client.py`。

## Trigger
- 主通道（GPT/OpenAI-compat）调用出现任意异常（包含超时、HTTP 错误、解析失败、业务异常）。

## Behavior
- 若主通道为 GPT 且检测到 `DEEPSEEK_API_KEY`：
  - 记录 warning：`FinIntel: GPT call failed, fallback to DeepSeek. err=...`
  - 使用 DeepSeek legacy 配置重试一次并返回结果。
- 若没有降级配置或降级也失败：直接抛出异常（保持现有失败语义）。

## Observability
- 仅日志告警（warning 级别），不新增输出字段。

## Non-goals
- 不新增/修改 CLI 选项。
- 不改变现有重试策略与超时默认值。
- 不修改输出 JSON/Markdown 结构。

## Risks
- GPT 失败时输出风格可能变化（降级模型差异）。
- 某些异常可能被降级掩盖，需要通过日志排查。

## Rollback
- 回滚 `finintel/deepseek_client.py` 的降级逻辑与配置构造。
