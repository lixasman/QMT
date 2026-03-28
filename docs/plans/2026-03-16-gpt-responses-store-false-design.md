# GPT responses store=false design

Date: 2026-03-16

## Summary
- 仅对 GPT（Leishen OpenAI-compat）/v1/responses 请求补充 store=false，避免 400 拒绝。
- 不影响 DeepSeek 直连与现有降级逻辑。

## Scope
- 文件：finintel/deepseek_client.py
- 影响：仅当 base_url 为 https://www.leishen-ai.cn/openai 且 wire_api=responses 时生效。

## Behavior
- 构造 /v1/responses payload 时，若命中 GPT 通道则加入 `store: false`。
- 失败仍按现有逻辑：GPT 失败 -> warning -> DeepSeek 降级。

## Non-goals
- 不修改 DeepSeek 直连参数。
- 不改变调用方接口与输出结构。

## Testing
- 单测验证 responses payload 包含 store=false。

## Risks
- 仅适配 Leishen OpenAI-compat 网关的 store 约束。

## Rollback
- 移除 payload 中的 store=false 分支。
