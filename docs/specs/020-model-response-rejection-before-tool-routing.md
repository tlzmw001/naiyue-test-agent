# Spec 020：模型响应进入工具路由前的拒绝式校验

状态：2026-10-01，已按 TDD 实施并完成本地确定性验证。参照 Pi `a69bef789` 的“模型错误先于工具执行”顺序；本项目继续由 LangChain `create_agent` 路由有效工具调用，不复制其循环。背景证据见[终止与重试调研](../research/loop-termination-retry-cancel-2026-10-01.md)。

后续设计确认：用户比较 Pi 对工具参数解析失败的处理后，决定保留本切片的保守规则；混合有效/无效调用仍整条拒绝，不做部分执行加错误工具结果。此决定不表示 Pi 具有相同的 `invalid_tool_calls` 数据结构。

## 当前数据流与问题

`SessionService.run → invoke_agent_run → create_agent.model_node → make_model_selection_middleware.select_model → handler(request.override(model=model))`。SDK 每次模型请求内部重试；成功时 LangChain 适配为 `ModelResponse.result` 中的 `AIMessage`，项目写入 `naiyue_stop_reason` 后交给框架。框架按 `AIMessage.tool_calls` 路由，不消费该项目字段。抛出的异常会记录 `model_call_failed` 和 `run_failed`；但一条已经返回的 `AIMessage` 即使被归一为 `error`，也可执行工具或使运行记录 `run_completed`。解析失败的原始工具调用可进入 `AIMessage.invalid_tool_calls`，框架不把它视为有效调用或运行失败。

## 输入、判断、终局契约

| 输入形状 | 本切片判断 | 模型/运行事件 | 调用者结果 |
| --- | --- | --- | --- |
| SDK 或适配器直接抛 `Exception`，无 `AIMessage` | 沿现有 `classify_model_failure` 路径；不由新门禁重试 | `model_call_failed → run_failed`，无 `run_completed` | `app.run` 抛原异常；CLI 安全展示失败类型 |
| `AIMessage` 原生元数据被 `normalize_stop_reason` 归为 `error`，无论是否有有效/无效工具调用 | 首要拒绝，固定原因码 `stop_reason_error` | 记录用量（若有）及 `model_call_failed(category=model_response_rejected, reason=stop_reason_error) → run_failed` | 不执行工具、不返回本次结果字典；抛 `ModelResponseRejected`，CLI 只展示其类型 |
| 非 `error` 的 `AIMessage.invalid_tool_calls` 非空，包括同时有有效调用 | 整条响应拒绝，固定原因码 `invalid_tool_calls` | 同上，reason=`invalid_tool_calls` | 不执行部分有效调用，也不造无法可靠配对的 `ToolMessage`；抛同类型异常 |
| 归一原因为 `tool_use`，但 `tool_calls` 和 `invalid_tool_calls` 都空 | 拒绝矛盾响应，固定原因码 `missing_tool_calls` | 同上，reason=`missing_tool_calls` | 不把它当自然结束；抛同类型异常 |
| `ModelResponse.result` 中无 `AIMessage`（包括空列表） | 拒绝无可检查的响应，固定原因码 `missing_ai_message` | 同上，reason=`missing_ai_message` | 抛同类型异常；不返回正常结果 |
| 正常有效工具调用；或明确 `length` 且有有效调用、没有无效调用 | 通过模型门禁 | 沿既有工具/运行事件 | 框架路由；`length` 仍由工具 hook 回填错误结果，允许模型重发 |

拒绝优先级：先 `error`，再 `invalid_tool_calls`，最后 `tool_use` 却无调用。`ModelResponseRejected.reason` 只能是上述固定码，不包含响应正文、工具参数、凭证、供应商错误文本或原始解析错误。`run_failed` 表示本次 `agent.invoke` 未返回成功结果；其事件只带现有 run/session 关联字段。CLI 继续使用现有异常展示，不改变对外函数签名。`SessionService.run` 的 `finally` 应释放 busy；应用继续沿用同一 session ID，checkpoint 实际内容须由测试记录，不先假定失败响应已持久化。

`run_completed` 仍只表示本次图调用正常返回，不等于回答质量合格。普通文本 `length`、`unknown`（含有效调用）、用户停止、工具业务错误、异步/流式路径不在此切片；有副作用通用工具上线前必须另定 `unknown` 的执行策略。此处不增加 LangChain `ModelRetryMiddleware`，响应拒绝也不触发 SDK 再试。

## 影响文件与验证

- `naiyue_test_agent/model_access/errors.py`：增加内部 `ModelResponseRejected` 及固定码的错误分类，保持原有供应商异常分类不变。
- `naiyue_test_agent/agent_runtime/__init__.py`：现有 `wrap_model_call` 在返回 `ModelResponse` 前记录用量并校验；复用当前异常日志/重新抛出路径，不新增工具路由器。工具 hook 不改。
- `tests/test_agent_runtime_response_gate.py`：现有 `test_agent_runtime.py` 已达 475 行，另建聚焦测试文件。先以假模型和实际 `create_agent` 验证错误消息有/无有效调用、无效调用、有/无混合调用、矛盾 `tool_use`、正常调用与 `length` 回归；检查事件顺序、实际工具执行次数、抛出异常及 busy 释放。若直接构造“无 AIMessage”不符合当前框架 `ModelResponse` 契约，先做定向 hook 测试并清楚记录其证据级别。
- `tests/test_application_cli.py`：用同一假模型应用链验证拒绝响应只展示安全失败类型，不展示回答正文；同会话下一次输入能继续。
- 更新 README、后续清单、交接；记录真实供应商失败响应尚未观察到。

先执行全量基线，再写失败测试确认红灯，再最小实现；最后定向/全量 pytest、`compileall`、`pip check`。L1/L2/L3 分别检查代码/测试存在、确有拒绝和脱敏分类、经应用组合进入 `create_agent` 并在工具路由之前拒绝。没有真实故障网络请求时只声称确定性行为。

## 实际验证与限制

- 基线 `.venv/bin/python -m pytest -q`：`130 passed in 3.92s`。先写 8 个测试场景，定向 `.venv/bin/python -m pytest -q tests/test_agent_runtime_response_gate.py` 实际 `8 failed in 0.93s`：错误/无效/矛盾响应未抛异常，或失败后会话未以预期方式恢复。最小实现后该组 `8 passed in 0.61s`，再增加明确错误优先级与 CLI 安全展示、checkpoint 形状断言。
- 最终全量 `.venv/bin/python -m pytest -q`：`140 passed in 3.65s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。定向 `tests/test_agent_runtime_response_gate.py tests/test_application_cli.py tests/test_agent_runtime_stop_reason.py` 在新加优先级/CLI 用例前为 `22 passed in 0.69s`；最终全量覆盖新增用例。
- L1/L2/L3：新增异常类与聚焦测试存在；模型 hook 在返回 `ModelResponse` 前拒绝，分类器只记录固定原因码；经 `build_local_application → app.run → create_agent` 的假模型测试证明所有拒绝路径不执行工具、只有 `run_failed`、`app.run` 抛异常。直接 hook 测试覆盖空响应和非 AI 消息；真实供应商适配器不会自然产生该测试形状，证据级别仅为边界注入。
- 不落盘端到端探针：合成 `content_filter + 有效工具调用` 得 `ModelResponseRejected(reason=stop_reason_error)`、实际工具执行 0 次；事件依序为 `run_started → model_selected → model_call_usage → model_call_failed → run_failed`，无 `run_completed` 或工具事件。失败后同一会话下一次成功运行的 checkpoint 消息为“首次 HumanMessage、第二次 HumanMessage、成功 AIMessage”；被拒绝的 AIMessage 未进入历史，但首次用户消息保留。CLI 显示 `运行失败（ModelResponseRejected）`，不展示被拒绝的模型正文。
- 尚无真实供应商失败响应样本；`unknown` 有效调用、普通文本 `length`、用户取消、异步路径及失败后用户消息是否应保留，均需后续单独定策。合成用量 7/3 token 只验证事件接线，不代表真实计费。
