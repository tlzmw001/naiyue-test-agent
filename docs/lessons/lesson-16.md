# Lesson 16：同一运行的自然完成、用户停止与失败

日期：2026-10-01。状态：已讲解；用户理解、独立实现和面试追问能力尚待其后续表现验证。承接 [Lesson 15](lesson-15.md) 的 `S1/T1/R1/H0` 示意数据流。本课讲解时只解释同步统一应用，CLI 当时尚无停止命令；后续已按 [Spec 026](../specs/026-cli-graceful-stop.md)增加 `:stop`。真实供应商故障和活动取消没有覆盖。

## 先分清四种东西

- **应用会话状态**：`busy`、`steering_open`、`stop_requested`、两条字符串队列、`run_id`，由 `SessionService` 持有并用同一把锁保护；`thread_id` 在同一会话内稳定，供 checkpoint 找历史。
- **图状态**：`messages`、`model_turn_seen`、`run_stopped`。`before_agent` 每次运行把后两者重置为 `False`，`after_model` 在模型成功返回后设 `model_turn_seen=True`。
- **模型响应结束原因**：只有模型 SDK/适配器已经返回 `AIMessage` 时才可能从 `response_metadata` 归一成项目 `naiyue_stop_reason`：`stop/tool_use/length/error/unknown`。它不是 Python 异常，也不是 `stop_requested`。
- **事件日志**：`run_started/run_completed/run_stopped/run_failed` 是观测记录；`steering_accepted/injected/discarded` 和 Follow-up 对应事件记录不同交付阶段。日志不是控制标志，也不是持久化事务记录。测试用的 `entered/release` 为 `threading.Event`，产品没有这两个标志。

共同入口：CLI 工作线程调用 `app.run → SessionService.run → invoke_agent_run → agent.invoke`。应用先设 `busy=True/open=True/stop_requested=False` 并给本次生成 `R1`，框架 `before_agent` 重置图状态。`invoke_agent_run` 先记一次 `run_started`。以下各分支均从这里出发。

## 三种主要终局

| 路径与触发 | 控制与消息如何流动 | 返回/事件/清理 |
| --- | --- | --- |
| 自然完成 | 模型正常回复；`after_model` 设 `model_turn_seen=True`。工具如有调用先执行并回填。`before_model` 可取 Steering；自然结束时 `after_agent → finish_run_boundary` 优先取 Steering，再逐条 Follow-up；都空才将 `steering_open=False`。 | `agent.invoke` 返回结果，`run_stopped=False`，记唯一 `run_completed`。随后 `SessionService.run.finally` 清剩余队列并复位 `busy/open/stop_requested/run_id`。CLI 从本次输入 ID 后打印各条非空模型文本。 |
| 应用 API 请求优雅停止 | `app.request_stop_after_turn(S1)` 在锁内将 `stop_requested=True`、`steering_open=False`，记 `stop_requested`。已发出的模型请求、SDK 内重试和已发出的工具批次不会被活动取消。`before_model` 若本 run 已有模型响应则跳到结束边界；无工具时当前回复后进入 `after_agent`。`finish_run_boundary` 先看停止，不再取 Steering/Follow-up；`after_agent` 写 `run_stopped=True`。首次模型请求前收到停止仍允许该次请求完成，但不注入排队消息。 | 若当前工作正常收尾，`agent.invoke` 返回带 `run_stopped=True` 的结果，记唯一 `run_stopped`，不记 `run_completed`；`finally` 清理并记待取消息 discarded。返回结果可能含当前模型回答和已完成工具结果。本课讲解时 CLI 无此命令；后续 `:stop` 已接入，`:quit` 仍仅 `join()` 等旧运行结束。 |
| 抛出未被框架处理的异常 | 模型请求、模型返回校验、工具、图调度或其他执行点抛出异常；若当前模型/工具尚在执行，其失败优先于此前的停止请求。异常到 `invoke_agent_run` 的 `except`，不会得到本次成功结果字典。 | 记唯一 `run_failed`，再原样上抛；`SessionService.run.finally` 仍释放 busy、清待取队列并记 discarded。CLI `_run_and_print` 捕获异常，只显示 `运行失败（异常类型）`，不打印正文或部分成功回答。新运行可复用同一 session/thread，但失败后的 checkpoint 内容不是事务回滚保证。 |

停止并非活动取消：若用户在模型 API 正在等待时请求停止，API 仍可返回或抛错；返回成功时才能走安全边界并得到 `run_stopped`，抛错则是 `run_failed`。`run_stopped` 是技术终局，不表示业务任务完成或外部副作用已回滚。新的 run 会再次将应用 `stop_requested=False`，图的 `model_turn_seen/run_stopped=False`。

## “error” 的几条不同来源

| 来源 | 在哪一层识别 | 典型可观察结果 |
| --- | --- | --- |
| 在线模型 SDK/HTTP 客户端直接抛 Python 异常，例如超时、连接、认证失败 | 异常从 `handler(request.override(model=model))` 抛出。在线 SDK 先按其自身配置对可重试请求至多重试 2 次；最终仍失败才由 `classify_model_failure` 分类。分类覆盖部分供应商/异常，其他为 `unknown/unclassified`，未知记录安全异常类型及受限状态码。 | 模型 middleware 记 `model_call_failed` 并 **重抛原异常**；运行层记 `run_failed`；无正常 `AIMessage`，不进入工具。SDK 内每次尝试没有独立项目事件。 |
| 模型已返回 `AIMessage`，原生结束字段映射为 `naiyue_stop_reason='error'`（例：`finish_reason='content_filter'`、`status='failed'`） | 模型 middleware 先记录可用 usage，再按优先级检查返回消息；抛项目 `ModelResponseRejected(reason='stop_reason_error')`。随后才检查 `invalid_tool_calls` 和矛盾 `tool_use`。 | 固定原因码进入 `model_call_failed(category=model_response_rejected)`；原异常上抛为 `run_failed`。被拒绝消息不会进入框架工具路由，CLI 不展示其正文。此拒绝发生在 SDK 已返回之后，不触发 SDK 重试。 |
| 模型返回非 `error`，但有 `invalid_tool_calls`，或声明 `tool_use` 却无有效调用，或无 `AIMessage` | 同一模型门禁依次抛 `ModelResponseRejected('invalid_tool_calls'/'missing_tool_calls'/'missing_ai_message')`。混合有效/无效调用整条拒绝。 | 与上一行相同，固定原因码、模型失败事件、运行失败，无工具执行。无 AI 消息只由直接 hook 注入测试覆盖。 |
| 工具内部抛普通 `RuntimeError` 等且未被工具配置处理 | 工具 middleware 记 `tool_started → tool_failed` 并重抛；框架若未将其处理为工具结果，异常到运行边界。 | `run_failed`；这不是模型异常，不能默认期待 `model_call_failed`。已有假工具测试验证普通异常原样上抛。 |
| 工具使用 `ToolException` 且配置为可处理，或工具边界主动回填错误 `ToolMessage` | 工具 middleware 记 `tool_failed`，但框架得到 `ToolMessage(status='error')` 并可继续模型循环。 | 整次 run **可能仍是 `run_completed`**。`tool_failed` 表示该次工具失败，不等于运行终局失败；测试中模型在错误工具结果后回复并自然结束。 |
| 图递归上限、模型选择/上下文校验、CLI 结果解析等其他 Python 错误 | 由实际抛出位置决定。`run_started` 之后、模型 hook `try` 之外的异常可直接到运行边界；CLI 在 `app.run` 返回后解析结果也可能出错。 | 图内未处理异常有 `run_failed`，但不保证有 `model_call_failed`；CLI 后处理失败可能已有 `run_completed`，随后只记 `cli_run_failed`。初始化失败发生在 run 之前，只有 CLI 初始化失败提示，没有 `run_started`。 |

当前 `unknown` 结束原因只记录脱敏观测，未自动判为失败；普通文本 `length` 也未一律判失败。有有效调用且 `length` 时，工具边界返回配对的错误 `ToolMessage`、不执行真实工具，让模型可重新发完整调用。这些语义有已知限制，有副作用工具上线前还要收敛 `unknown` 的安全策略。

已入队消息的结局也要区分：尚在队列中的消息在停止/异常收尾由 `finally` 清除并记 discarded；已经取出并交给图状态的消息不会重新排队，也没有通用的撤销、去重或 checkpoint 事务保证。确定性测试观察到模型响应门禁拒绝时，先前用户输入可留在 checkpoint、被拒绝的 AI 响应未进入历史；不能由这一个样本推出所有异常的 checkpoint 形状。

## 本次核对与理解状态

定向命令：`.venv/bin/python -m pytest -q tests/test_application_stop_after_turn.py tests/test_agent_runtime_response_gate.py tests/test_agent_runtime_tool_events.py tests/test_application_cli.py tests/test_application_follow_up.py::test_failure_discards_pending_follow_up_and_rejects_idle_input`；实际 `31 passed in 0.95s`。本轮只读生产代码、测试和既有 Spec，新增本学习记录；未实施新功能、未验证真实供应商故障、活动取消或所有异常位置的 checkpoint 恢复。

理解检查：若 `S`、`F` 已 accepted，随后用户请求停止，而正在进行的模型请求抛 `RuntimeError`，最终应看到哪个终局事件？`S/F` 会不会作为 `HumanMessage` 被注入？为什么？用户答案尚未收集，不能把本课讲解记成独立掌握。
