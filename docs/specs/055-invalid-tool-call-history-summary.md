# Spec 055：旧历史无效工具调用的可摘要表示

状态：2026-10-03，已按 TDD 实施；只有合成/导入旧历史的确定性证据。

## 当前数据流与问题

`SessionService.run → create_agent.before_agent → context_compaction` 对将移除的旧前缀先调用 `_validate_removed_prefix`，再把消息序列化为摘要模型的文本输入。当前校验只要看到 `AIMessage.invalid_tool_calls` 就报 `unsupported_summary_content`。因此，即使后面有用户纠正和成功回复，这段旧历史仍无法摘要。

本项目模型 middleware 在新响应交给 LangChain 工具路由之前已拒绝任何 `invalid_tool_calls`，包括同条响应中混有有效调用；这条门禁保持不变。LangChain `InvalidToolCall` 包含可能为空的 `id/name/args/error`，其中 `args` 是未经解析的原始字符串。普通工具执行错误是有有效调用 ID 配对的 `ToolMessage(status='error')`，当前校验允许摘要，两者不能混为一谈。Pi 的摘要序列化会区分助手工具调用与工具结果，但 Pi 没有 LangChain 同名的 `invalid_tool_calls` 字段；本片只借鉴区分调用与结果的表示原则，不声称逐字照搬 Pi。

## 本片契约

- 只对**已经出现在待摘要旧历史中**的 `AIMessage.invalid_tool_calls` 放开校验；不改变模型新响应的拒绝规则，也不重新执行历史工具。
- 在摘要请求中，把每个无效调用序列化为独立的 `<invalid_tool_call parse_status="invalid">` 元素，完整保留 `id/name/args/error` 四个字段及缺失值。字段内容按 JSON 表示并转义为 XML 文本；不把原始参数变成可执行的参数字典，不把无效调用写成 `<tool_call>` 或 `ToolMessage`。摘要系统指令明确：这只是解析失败记录，没有对应工具结果时不得描述为执行成功；后续用户纠正与真实工具结果按各自消息保留。
- 对正常有效 `tool_calls → ToolMessage` 的完整配对规则维持原样，错误 `ToolMessage(status='error')` 也作为真实失败结果序列化。若无效调用 ID 与同一条 AI 消息中的有效调用 ID 相同，关系不明确，固定报 `ambiguous_tool_call_id`，不让摘要模型猜配对。旧 `function_call`、非文本多模态、未配对有效工具结果仍按原规则失败。
- 摘要输入预算、压缩后预算、日志脱敏和失败时 checkpoint 保留不变；不增加真实网关请求，也不修改 `.env`、公开 API 或依赖。

## 影响文件与验证

- `agent_runtime/context_compaction.py`：放开可表示的无效调用，增加显式序列化与同条消息 ID 冲突保护，更新摘要指令。不新建抽象或改 Agent 循环。
- `tests/test_context_compaction.py`：先写失败测试。通过本地应用完整链路提供**合成/导入历史**（不是新模型响应），验证 `invalid_tool_calls`、后续纠正、摘要与正常继续；断言原始参数及解析错误完整转义、无虚构工具结果。另覆盖混合有效/无效调用各自的身份与失败工具结果、同 ID 歧义拒绝。已有模型响应门禁测试继续保证新响应仍被拒绝。
- 更新本 Spec、`docs/handoff.md`、`docs/future-work.md`；设计未变，不修改总体四层架构或 `docs/关键知识点.md`。
- 定向与全量 pytest、`compileall`、`pip check`。本片只能验证确定性结构与接线，不能证明真实摘要模型一定正确保留纠正事实；后续须用固定任务评测。

## 实际验证与剩余边界

- 基线 `.venv/bin/python -m pytest -q tests/test_context_compaction.py` 为 `9 passed in 0.90s`。先写三个失败测试：完整应用链路在 `_validate_removed_prefix` 报 `unsupported_summary_content`，混合有效/无效调用在相同位置失败，同一 AI 消息重复 ID 未报指定歧义原因。红灯命令 `.venv/bin/python -m pytest -q tests/test_context_compaction.py -k 'restored_invalid or mixed_restored or same_ai_message'` 为 `3 failed, 9 deselected in 0.70s`。
- 最小实现只改旧历史校验与序列化：保留 `id/name/args/error` 原值，JSON 字符串置于 XML 转义文本中，使用独立 `invalid_tool_call` 标记，系统指令禁止推断执行或结果；同条 AI 消息有效/无效调用 ID 重叠时报固定错误。三个新增测试转绿，定向连同模型新响应拒绝测试为 `21 passed in 0.66s`；全量 `.venv/bin/python -m pytest -q` 为 `455 passed in 14.76s`；`compileall -q naiyue_test_agent tests scripts` 退出 0；`pip check` 输出 `No broken requirements found.`。
- 本片的“旧历史”通过本地应用调用输入模拟，真实运行的新模型响应仍在 [Spec 020](020-model-response-rejection-before-tool-routing.md) 门禁处被拒绝，不能将此功能描述成新响应错误后自动恢复。完整链路测试只检查传给摘要模型的消息结构及假模型继续，摘要事实质量、跨重启导入、真实供应商对无效调用历史的行为尚未验证。若保留轮次或序列化后的摘要请求超窗口，现有预算保护仍会失败，不静默裁剪原始参数。
