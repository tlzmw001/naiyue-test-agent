# Lesson 11：错误模型消息怎样变成运行失败

状态：2026-10-01，机制与实现已讲解；用户先确认总体方案并要求把输入、分类、状态、事件和调用者结果说清。理解检查、独立实现、面试追问尚无证据，不能记为已掌握。实现/测试证据见 [Spec 020](../specs/020-model-response-rejection-before-tool-routing.md)。

## 先看具体消息

合成示例：模型适配器返回 `AIMessage(content='private', tool_calls=[{'name': 'write_probe', 'args': {}, 'id': 'call-1'}], response_metadata={'finish_reason': 'content_filter'})`。`content_filter` 是原生结束字段；项目 `normalize_stop_reason` 把它归为统一值 `error`。这是一条**已经返回的消息**，不是 Python 抛出的异常。框架若直接读 `tool_calls`，会尝试执行 `write_probe`；门禁在模型 middleware 返回给框架之前阻止这一点。

调用链：`app.run → SessionService.run → invoke_agent_run → create_agent → select_model → handler(model.invoke)`；handler 返回后，select_model 先写 `naiyue_stop_reason=error` 并记录可用的用量，再抛只含 `stop_reason_error` 的 `ModelResponseRejected`。同一 middleware 记录 `model_call_failed`；异常回到 `invoke_agent_run` 时记录 `run_failed`，向上抛给应用。因 `create_agent` 未拿到成功的模型节点结果，工具节点没有执行机会。没有结果字典，也没有 `ToolMessage`；CLI 只输出 `运行失败（ModelResponseRejected）`，下一次输入仍可使用同一会话。

本机不落盘探针实际输出：`app_result=exception ModelResponseRejected reason=stop_reason_error`、`tool_executions=0`；事件依序 `run_started、model_selected、model_call_usage、model_call_failed、run_failed`。没有 `run_completed`。事件中的 reason 是固定码，不是模型正文或工具参数。`run_completed` 原意仅为本次 `agent.invoke` 正常返回，不能单独证明业务结果质量；当前拒绝分支不会记录它。

## 不同 error 来源和同一响应内的优先级

| 来源/形状 | 谁先判断 | 这次运行怎样结束 |
| --- | --- | --- |
| SDK/适配器直接抛异常，例如请求重试耗尽或解析过程抛出 | 还没有可用 `AIMessage`；现有 `except Exception` 对异常分类 | `model_call_failed → run_failed`，原异常继续抛出；没有工具或结果字典。 |
| 已返回的 `AIMessage` 的原生字段归一为 `error`，无论有效/无效工具调用为何 | 模型 hook 在返回框架前优先拒绝 | `ModelResponseRejected.reason=stop_reason_error`；整个工具批次 0 次执行。 |
| 不是 `error`，但 `invalid_tool_calls` 非空 | 模型 hook 拒绝整条消息；包括同时存在有效调用的情况 | reason=`invalid_tool_calls`，不猜测怎样为无效调用配 `ToolMessage`；有效调用也 0 次执行。 |
| 统一原因为 `tool_use`，但有效/无效调用都为空 | 模型 hook 判定信号与内容矛盾 | reason=`missing_tool_calls`，不伪装为自然结束。 |
| `ModelResponse.result` 没有 `AIMessage` | 模型 hook 无可检查的模型消息 | reason=`missing_ai_message`；该分支仅用直接 hook 注入验证。 |
| 有效 `tool_calls` 且消息通过门禁 | LangChain 根据调用内容路由 | 原有工具 hook 仍检查 `length` 与 ID；正常调用进入实际工具，截断调用只回填错误工具结果。 |

优先级意味着同一消息既是 `content_filter` 又含无效工具调用时，记录 `stop_reason_error`；不会同时生成两个终局。单个工具自己的执行失败可变成错误 `ToolMessage` 并让模型继续，属于另一层，不等于这里的模型响应拒绝。

## 运行结果与 checkpoint

失败时 `app.run(...)` **抛异常而不返回结果字典**。`SessionService.run` 的 `finally` 把 busy 复位，因此同一会话可再发起运行。确定性测试实际观察到：第一次输入 `first` 触发拒绝，第二次输入 `second` 得到 `recovered`；最终历史为 `HumanMessage('first')、HumanMessage('second')、AIMessage('recovered')`。被拒绝的 AIMessage 不在历史里，但第一次用户消息仍在 checkpoint。当前没有“撤销用户输入”的事务语义，是否保留这条未获回答的输入以后单独设计。

SDK 重试发生在 handler 内部的单次网络请求阶段；已经返回的 `AIMessage` 被门禁拒绝，不会触发这项 SDK 重试，也不会自动重跑整个 Agent。`unknown` + 有效调用、普通文本 `length`、用户停止仍是后续切片，不把本节称为完整运行终局策略。
