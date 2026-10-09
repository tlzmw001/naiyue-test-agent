# Spec 019：Claude 上下文截断与工具调用绑定

状态：2026-10-01，已按 TDD 实施并完成本地确定性验证。

## 当前数据流与问题

`application.SessionService.run → agent_runtime.invoke_agent_run → create_agent`。模型 middleware 从 LangChain `AIMessage.response_metadata` 调用 `model_access.normalize_stop_reason`，写入 `naiyue_stop_reason`；工具 middleware 读取最近的 `AIMessage` 和当前 `ToolCallRequest.tool_call`，决定是否执行 `handler(request)`。

Claude 官方将 `stop_reason=model_context_window_exceeded` 定义为上下文窗口截断；当前映射却返回 `unknown`，可能让截断的工具调用继续执行。当前工具 middleware 只在 ID 匹配且原因是 `length` 时阻止执行；若请求 ID 与最近模型消息不一致，则落入正常 `handler`，不能作为拒绝式边界。标准 `create_agent` 路由通常保持 ID 一致，本修复覆盖状态异常时的确定性行为。

## 本切片契约

1. `model_access` 将 Claude `model_context_window_exceeded` 归为现有 `length`，保留原始 `stop_reason`；`pause_turn` 保持当前 `stop`，不引入服务器工具续传。
2. 工具 middleware 先确认最近 `AIMessage.tool_calls` 含当前调用 ID；无最近模型消息或 ID 不一致时，不进入 `handler`，返回同一 `tool_call_id`、`status='error'` 且不含模型内容/参数的固定错误 `ToolMessage`，记录现有 `tool_started/tool_failed` 事件。ID 一致时保持原行为：`length` 短路，其他原因走 `handler`。
3. 不改变公开 API、事件字段、未知停止原因策略、普通文本截断终局或异步路径；不改配置和依赖。

## 影响文件与验证

- `naiyue_test_agent/model_access/stop_reason.py`：增加一个已核对的 Claude 原生值。
- `naiyue_test_agent/agent_runtime/__init__.py`：现有工具 hook 在执行前拒绝不一致的工具请求。
- `tests/test_model_stop_reason.py`：参数化映射和原字段保持回归。
- `tests/test_agent_runtime_stop_reason.py`：应用装配下验证 Claude 上下文截断工具不执行、错误结果进入下轮模型；直接注入 ID 不一致及缺失最近模型消息，验证 handler 零次调用、匹配错误结果及事件；已有正常/截断路径继续通过。
- 完成后更新 `docs/future-work.md` 与 `docs/handoff.md`，分开记录确定性测试和真实 Claude 截断尚未验证。

先运行现有全量测试作基线，再写最小失败测试并确认预期失败，修改生产代码后跑定向、全量 pytest、`compileall`、`pip check`。三级验证是实现存在、无占位逻辑、经 `create_agent` 应用入口消费。依据：[Claude stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)。

## 实际结果与证据边界

- 基线 `.venv/bin/python -m pytest -q`：`125 passed in 3.90s`。先新增映射、应用接线和异常请求测试，定向运行出现 4 个预期失败：上下文截断被归为 `unknown`，应用实际执行了截断工具，ID 不匹配及缺失最近模型消息均执行了 handler。
- 最小修改后定向测试先达到 `24 passed in 0.52s`；再补一条 `tool_use` 原因下的 ID 不一致回归，最终 `.venv/bin/python -m pytest -q tests/test_model_stop_reason.py tests/test_agent_runtime_stop_reason.py`：`25 passed in 0.56s`；全量：`130 passed in 3.75s`；`compileall` 退出 0；`pip check` 输出 `No broken requirements found.`。
- L1/L2/L3：两个现有模块均已修改，映射和拒绝分支有真实代码；应用装配的 `create_agent` 假模型测试证明 `model_context_window_exceeded` 到达工具边界后不会执行实际工具且错误结果回填；直接 hook 测试证明异常 ID 或无最近模型消息时 handler 未执行、工具失败事件被记录。异常 ID 分支不能由标准 `create_agent` 正常路由制造，故该分支只做了直接 hook 注入验证。
- 本切片没有真实 Claude 截断样本；普通文本截断的终局、未知元数据、有副作用工具、异步路径和服务器工具 `pause_turn` 续传仍未验证或实现。
