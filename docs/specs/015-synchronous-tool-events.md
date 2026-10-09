# Spec 015：同步运行路径中的安全工具事件

状态：2026-09-30；已按 TDD 实施，本地确定性验证通过。前置：[Spec 013](013-synchronous-run-events.md) 的同步 run/模型日志、[Spec 014](014-in-memory-session-service.md) 的可信会话映射，以及[事件来源探针](../event-source-probe.md)。

## 当前数据流与问题

`SessionService.run → invoke_agent_run → agent.invoke → create_agent` 已接入 run 开始/终局与模型选择、用量/失败日志。模型要求调用工具时，LangChain 执行工具并回填 `ToolMessage`；项目没有工具开始、完成或失败事件，无法用当前 run ID 对照工具边界。普通 Python 日志的 `extra` 在默认配置下不是持久轨迹或 Web 推送。

本机假模型探针证实，`@wrap_tool_call` 可在当前同步路径取得工具名、调用 ID 和 `runtime.context.run_id`；工具可能直接抛异常，也可能由已配置的框架/工具错误处理返回 `ToolMessage(status='error')`。本切片只观测这条现有路径，不重写循环或切换到实验性 v3 `stream_events`。

## 本切片契约

- 在 `agent_runtime` 装配中加入 `wrap_tool_call` middleware，不改变已有公开函数签名。每次实际进入 hook 的工具调用按顺序记录 `tool_started`；若 handler 正常返回 `ToolMessage(status='error')`，记录 `tool_failed` 并原样返回，让框架照常回填错误结果；若 handler 抛 `Exception`，记录 `tool_failed` 后以裸 `raise` 原样传播；其他正常返回记录 `tool_completed` 并原样返回。
- 工具日志使用现有项目 logger 的 `extra['tool_event']`，字段限于固定 `event`、`tool_name`、`tool_call_id`，存在 `run_id` 时增加该 ID。不得记录工具参数、结果、原始错误正文、提示词、回答、凭证或内部 `thread_id`。`tool_completed` 只表示工具 handler 返回且没有 `ToolMessage.status='error'`，不代替业务效果评测。其他 `Command` 返回的内部状态语义留到实际出现时单独验证。
- `run_started/run_completed/run_failed` 的 `extra['run_event']` 增加应用层提供的 `session_id`，继续保留 `run_id`，不写内部 `thread_id`。模型事件字段保持不变；旧的直接 `agent.invoke` 路径没有 run ID 时，工具事件也不伪造 run ID。
- 保持当前终局错误语义：抛出的工具异常导致 `run_failed` 并向应用层传播；错误 `ToolMessage` 允许 Agent 后续模型调用，若最终正常返回则 `run_completed`。不会把抛异常改成正常 AI 回复。日志事件不是已配置的生产事件消费者、持久存储或完整审计链。
- 本切片不加跨工具全序号、异步/流式 hook、取消、工具重试、事件 sink、Web 或持久化。并发工具事件的实际到达顺序不能作为固定契约；未来事件消费端若要求严格排序，需另行设计并测试 per-run 序号。

## 影响文件与验证

- `naiyue_test_agent/agent_runtime/__init__.py`：新增同步工具 hook 并接入现有装配，运行开始/终局日志带 `session_id`。
- 新增 `tests/test_agent_runtime_tool_events.py`：先写失败测试，经真实 `build_runtime_agent → create_agent` 的假模型/假工具路径检查成功、抛异常、错误 `ToolMessage`、直接 invoke 兼容、事件关联与脱敏。
- `tests/test_agent_runtime.py`、`tests/test_application_session.py`：仅将已有精确 `run_event` 断言同步到新增 `session_id` 契约，保留原有运行顺序/错误/隔离断言；单文件保持不超过 500 行。
- `docs/specs/013-synchronous-run-events.md`：记录后续切片对 run 事件字段的扩展，不改写该阶段历史测试结果；`README.md`、`docs/implementation-roadmap.md`、`docs/future-work.md`、`docs/handoff.md` 同步当前能力与限制。

验证：先跑新增定向测试确认因缺少工具事件/`session_id` 而红灯；最小实现后定向与全量 pytest、`compileall`、`pip check` 通过。L3 必须穿过项目装配和 `invoke_agent_run`；只构造日志字典不算接线。此次不调用真实供应商、不修改 `.env` 或依赖；真实并发、Web 事件传输和生产日志配置均不在验证范围。

## 实际验证与限制

- 基线 `.venv/bin/python -m pytest -q`：`93 passed in 3.66s`。先新增 4 个工具事件测试，经 `build_runtime_agent` 的真实编译图执行后，`.venv/bin/python -m pytest -q tests/test_agent_runtime_tool_events.py` 输出 `4 failed in 0.93s`；预期失败均为缺少 `tool_event`。
- 最小实现只在运行层加入 `wrap_tool_call`，并为 run 生命周期日志增加 `session_id`。新增测试已通过；已有 2 个精确 `run_event` 断言因新字段而失败，随后按新契约补充该字段，保留原顺序、run ID 和失败传播断言。定向 `.venv/bin/python -m pytest -q tests/test_agent_runtime_tool_events.py tests/test_agent_runtime.py tests/test_application_session.py` 输出 `29 passed in 0.63s`。
- 进一步在已有应用测试中断言 `SessionService.run` 产生的工具事件与 run 开始事件共用同一 run ID，run 生命周期保留该服务的外部 session ID。最终 `.venv/bin/python -m pytest -q` 输出 `97 passed in 4.00s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。运行层 185 行，旧运行层测试 475 行，新工具事件测试 162 行，均低于 500 行。
- L1/L2/L3：hook 真正传给 `create_agent`；测试经项目 `invoke_agent_run` 观察一次模型→工具→模型事件顺序、抛出工具异常时同一异常对象上抛与 `run_failed`、错误 `ToolMessage(status='error')` 时 `tool_failed` 与 `run_completed`、直接 `agent.invoke` 不伪造 run ID；应用服务路径也验证了同一 run 关联。合成敏感提示词、参数、工具输出、最终回答、错误正文和内部 thread ID 未进入测试捕获的事件字段或日志文字。
- 证据只覆盖当前同步本地假模型和工具。普通运行仍未配置默认 INFO 日志 handler/formatter 或事件消费者；没有真实模型工具事件、Web 实时推送、异步 hook、取消、跨并发严格序号或持久审计。`tool_completed` 表示 handler 返回，不是业务结果正确。
- 当前工作树的项目文件整体为 untracked；`git diff --check` 无法覆盖这些新文件，因此不把它的退出码当作内容审查或版本控制验收。未提交代码。
