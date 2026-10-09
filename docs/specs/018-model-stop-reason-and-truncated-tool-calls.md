# Spec 018：统一模型结束原因与截断工具调用保护

状态：2026-09-30，已按本 Spec 实施并完成本地确定性验证。用户确认借鉴 Pi 的职责分层：`model_access` 统一各适配器返回的结束原因，`agent_runtime` 在工具边界消费；`length` 下已解析的工具调用只回填错误工具结果，实际工具函数不执行，让模型有机会重发。参照 Pi 本机 `a69bef789`、项目 LangChain `1.4.2`/LangGraph `1.2.12`。

## 当前数据流与问题

`SessionService.run → invoke_agent_run → create_agent`；模型调用经 `make_model_selection_middleware → handler` 后得到 `ModelResponse.result` 中的 LangChain `AIMessage`，并非供应商原始 JSON。`response_metadata` 可能是 OpenAI Chat Completions 的 `finish_reason`、Responses API 的 `status/incomplete_details`、Anthropic 的 `stop_reason` 或 Ollama 的 `done_reason`。当前运行层只记录用量，工具路由由 LangChain 依据 `AIMessage.tool_calls` 决定；本机该路由没有检查截断元数据。

Pi 的供应商适配器先保留 `rawStopReason` 并生成统一 `stopReason`；loop 遇 `length` 且有工具调用时生成对应的错误工具结果，不调用工具执行函数，再继续模型循环。Pi 并不把所有 `length` 统一当作整次运行失败。我们只迁移此切片所需的契约，不复制 Pi 的自写循环。

## 本切片契约

1. `model_access` 增加纯函数，将当前支持的适配消息元数据映射为项目统一的 `stop`、`tool_use`、`length`、`error` 或 `unknown`。OpenAI Chat Completions 的 `stop/tool_calls/length/content_filter`，Responses API 的 `completed/incomplete(max_output_tokens)/failed`，Anthropic 的 `end_turn/tool_use/max_tokens/refusal`，Ollama 的 `done_reason=stop/length` 均有确定性测试。若原字段表示正常结束但消息带 `tool_calls`，统一为 `tool_use`。未知或缺失字段保留为 `unknown`，不得伪称正常完成；原始字段保留在原 `response_metadata`，不打印响应正文。
2. 运行层在现有 `wrap_model_call` 返回前，将统一原因写入该 `AIMessage.response_metadata` 的项目命名字段；不修改下层公开函数签名或模型响应的正文、`tool_calls`。已有选模、失败和用量事件保持原契约。
3. 在现有同步 `wrap_tool_call` 中，先找到当前工具调用对应的最近 `AIMessage`。若统一原因为 `length`，不调用 `handler(request)`，直接返回相同 `tool_call_id`、`status='error'` 的 `ToolMessage`，正文只使用固定的安全提示，让模型重新发完整调用。现有工具事件须仍记录开始和失败；正常工具调用仍按原路径执行。调用 ID 不匹配时不将其他模型消息的原因误套到本调用。
4. 仅有明确 `length` 触发该保护。`unknown` 不被猜测成 `stop`，但此切片不改变其既有工具执行路径；这是当前有限兼容策略，今后引入具有副作用的通用工具前需另定缺失结束元数据的阻断规则。普通文本 `length`、`error/aborted` 的运行终局、运行预算、异步/流式 hook、用户取消另做切片；不把本切片称为完整 Pi stopReason 或生产级执行保护。

## 影响文件与验证

- 新增 `naiyue_test_agent/model_access/stop_reason.py`：纯映射与项目命名元数据键；不访问网络、配置或 Agent 层。
- 修改 `naiyue_test_agent/agent_runtime/__init__.py`：已有模型 hook 记录统一原因，已有工具 hook 对截断调用短路并回填错误结果，不添加独立循环。
- 新增 `tests/test_model_stop_reason.py` 和 `tests/test_agent_runtime_stop_reason.py`：先确认红灯；一组测试覆盖四类适配元数据的正常、截断、错误、未知与原始字段保留；另一组通过真实 `create_agent` + 假模型/假工具证明“首轮截断→工具执行 0 次→错误 ToolMessage 进第二轮→模型重发→完整调用执行 1 次→最终回答”，并核对 `tool_call_id`、事件及脱敏。已有测试不改断言以掩盖回归。
- 更新 README、项目设计、路线、后续清单、交接和学习记录，区分假模型行为、真实网关正常结束探针、真实截断未验证。

验证命令：定向与全量 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`、`.venv/bin/python -m pip check`。L1/L2/L3 分别验证文件存在、真实映射/短路实现、经应用装配的 `create_agent` 实际消费。不得编辑 `.env` 或输出凭证；当前无可控的真实供应商截断样本，不把假模型断言说成网关行为。

## 实际实施与证据

- 实施前全量基线 `.venv/bin/python -m pytest -q` 为 `105 passed in 3.74s`。先用不落盘假模型探针验证 `wrap_model_call` 可标注 `AIMessage.response_metadata`，`wrap_tool_call` 可从 `request.state['messages']` 看到该标注并直接返回错误 `ToolMessage`；实际工具调用 0 次，模型得到结果后继续。探针退出 0，仅证明当前框架边界可接线。
- 先新增映射和运行测试，定向命令在收集阶段因 `model_access.stop_reason` 尚不存在报 2 个 `ModuleNotFoundError`，退出码 2。最小实现后定向测试 `19 passed in 0.61s`；补充同一条截断消息的两个工具调用都不执行的场景，定向测试 `20 passed in 0.69s`。全量、编译及依赖结果见交接记录。
- 映射为纯函数，原生字段不删除；模型 middleware 只添 `naiyue_stop_reason`，工具 middleware 仅在最近 `AIMessage` 含当前 `tool_call_id` 且统一原因是 `length` 时短路。经 `build_local_application → app.run → create_agent` 的假模型验证：截断调用返回错误工具结果，实际工具未执行；第二次完整工具调用执行一次，最终正常回答。现有 `tool_started/tool_failed` 事件代表工具边界处理，不能据此断言实际处理函数已进入。
- 使用已有私有配置通过 `build_local_application → app.run` 发一条合成无工具提示，退出 0；只打印 `AIMessage` 类型、原生 `finish_reason=stop` 与归一化 `naiyue_stop_reason=stop`，证明正常结束的真实网关消息接入了新归一化路径，不输出回答或凭证。本切片没有真实 `length`、Anthropic/Ollama 服务或 OpenAI Responses 截断样本。无原生结束字段时统一为 `unknown`，本切片保持既有工具调用行为；第一次具有副作用的通用工具上线前必须重新评估缺失字段的拒绝策略。普通文本截断的运行终局、异步/流式和用户停止仍未实现。
