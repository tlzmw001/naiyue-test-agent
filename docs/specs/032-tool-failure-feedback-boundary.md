# Spec 032：工具失败回填给模型的边界

状态：2026-10-02，Read 的可行动安全错误与下一模型 turn 验证已实施。尚无通用结果后处理 hook，不为它预先增加空实现。用户要求工具调用前后的可恢复问题回到 `ToolMessage`，供下一次模型调用调整，而非直接使 Agent 退出。

## 当前数据流

`AIMessage.tool_calls` 中有效的 `id/name/args` → `create_agent` 的 `ToolNode` → 本项目 `@wrap_tool_call` → 工具 schema/执行 → `ToolMessage(tool_call_id=...)` → 下一次模型调用。

- `read_file` 当前已有 `handle_validation_error`，以及 `_read_file → ToolException → handle_tool_error`。参数不合法、文件不存在、无权限、非法 UTF-8、非文本字节和超长行均返回安全的错误 `ToolMessage`。未知工具名由框架 `ToolNode` 返回错误 `ToolMessage`。截断或与最近模型回复不匹配的有效调用由项目 hook 返回错误 `ToolMessage`，工具不执行。
- 其他工具执行或结果规范化时抛出的未处理异常，目前由 `_record_tool_call` 记录 `tool_failed` 后重新抛出，运行记 `run_failed`；已有 `tests/test_agent_runtime_tool_events.py` 固定此行为。本项目尚无通用的“工具结果验证/改写”后处理 hook。
- `AIMessage.invalid_tool_calls` 属模型响应解析/门禁阶段，尚未形成可安全配对的有效工具调用；按 [Spec 020](020-model-response-rejection-before-tool-routing.md)整条拒绝，不伪造 `ToolMessage`。

## 目标契约与例子

只要存在可配对的有效 `tool_call_id`，且失败属于模型可以据此更换参数、改用其他工具或向用户说明的**工具调用结果**，返回 `ToolMessage(status='error', tool_call_id=原 ID, name=原工具名)`，保留安全、可行动的原因；记录 `tool_failed`，继续 Agent 循环。模型下一步是否重试由模型决定；`run_completed` 只表示图调用正常返回，不表示工具成功或业务目标达成。

例：模型提出 `read_file(path='missing.md', id='call-7')`，文件不存在 → 工具输出同 ID 的错误 `ToolMessage`，内容解释“路径无可读普通文件，可能不存在或被工作区策略阻止；检查路径或换一个允许的文件” → 下一轮模型可以换文件或向用户说明。路径具体值、原始异常及敏感文件存在性不回显。

本次 Read 切片的消息规则：参数错误说明允许的 `path/offset/limit` 形状和范围；路径分类失败不揭示敏感目标是否存在，但列出“未找到普通文件或被工作区策略拒绝”的可能原因和下一步；工作区外尚未获批说明需要审批；编码、非文本字节和超长行指出具体限制与可行动的替代方向；实际打开/读取 I/O 失败说明读操作未完成；无会话工作区说明必须先选择工作区。所有提示是固定文本，不拼接参数、路径或原始异常。当前既有错误 `ToolMessage` 的 ID/status 不变。

| 发生处 | 目标处理 | 当前状态 |
| --- | --- | --- |
| 已有有效 ID 的工具参数校验、未知工具名、目标不存在或被权限规则拒绝 | 安全错误 `ToolMessage`；工具不执行或执行失败后模型可调整 | Read/框架已覆盖相应路径 |
| 工具明确报告失败、工具本地可恢复异常 | 错误 `ToolMessage`；不泄露原始异常/文件路径/凭证 | Read 的预期错误已覆盖；新工具逐一指定安全映射 |
| 将来结果校验/改写后发现内容无效，或该后处理产生可归属本次调用的失败 | 以原调用 ID 返回错误 `ToolMessage`，不能把坏结果当成功结果；后处理的位置和异常分类在功能 Spec 中验证 | 尚无通用后处理 |
| 模型 API、响应本身错误或 `invalid_tool_calls`、checkpoint/审批状态损坏、用户停止、无法确认副作用结果的运行级故障 | 不伪造工具成功或可安全重试的结果；保留相应失败/停止终局 | 现有模型门禁与运行终局保持 |

工具可能写文件或调用外部服务时，异常**不证明未产生副作用**。向模型回填时必须明确“结果未知，勿自动重试”，或者由运行边界停止并等待人工确认；不能用统一的“请重试”鼓励重复写入。用户停止和 LangGraph interrupt 是控制信号，不能转换成普通工具错误。原始异常内容不得直接回填模型；日志只记录安全分类/类型与关联 ID。

## 参考证据与实现方向

- Pi `a69bef789` 的 `packages/agent/src/agent-loop.ts`：`prepareToolCall` 把工具不存在、参数验证和 `beforeToolCall` 阻止处理成即时错误结果；`executePreparedToolCall` 捕获工具异常；`finalizeExecutedToolCall` 捕获 `afterToolCall` 异常；最终 `createToolResultMessage` 携带原 `toolCallId` 与 `isError`。这证明它的工具阶段与后处理阶段有错误回填，但不等于本项目应吞下 checkpoint 或副作用未知错误。
- AgentX `3fcfa0e` 的 `AbstractBuiltInToolProvider.executeTools` 对其内置工具的参数/执行异常返回带错误前缀的字符串，`createToolExecutor` 接入 `AiServices`；它没有在这一层提供 Python `ToolMessage(status='error')` 同等契约，不能把其错误字符串误说成结构化失败状态。
- 本机 LangChain 1.4.2 的 `ToolNode` 默认将工具调用/参数错误转成错误 `ToolMessage`，未知执行异常可上抛；`ToolErrorMiddleware` 可选择性将异常转换为错误 `ToolMessage`，`on_error` 返回 `None` 则保留上抛。实际实施优先沿用这些机制与现有工具级安全错误处理；不自写新的工具循环或默认自动重试。

## 待实施切片的验证

本次先写失败测试，覆盖 Read 参数错误、缺失/受限路径、外部未获批、非法编码、非文本、长行、I/O 与无工作区提示的具体可行动内容和脱敏；用假模型经应用实际装配确认同 ID 错误 `ToolMessage` 出现在下一次模型输入中，并记录 `tool_failed → run_completed`。最小实现只改 Read 的固定错误映射与相关测试，不增加工具自动重试或全局异常转换；既有精确字符串测试按明确变更后的契约同步更新。定向和全量 pytest、compileall、pip check 验证。

以后新增工具或结果后处理时，各自先写红灯测试：有效调用的参数/执行/后处理失败均有同 ID 的错误 `ToolMessage`，失败内容经过脱敏，下一模型 turn 能看到它；再覆盖多工具批次、审批拒绝、停止/中断、模型解析错误及可能产生副作用的失败。对当前尚未处理的任意异常，先按工具是否只读、是否可能产生副作用确定转换策略，再改变 `tests/test_agent_runtime_tool_events.py` 中原异常上抛的既有契约。

## Read 切片的实现与证据

- `read_file.py` 的固定安全提示现在分别说明参数形状、路径不可用的可能原因、外部目标审批、UTF-8/非文本/单行预算、I/O 和未设置会话工作区，均给模型一个可行动的下一步。路径失败不透露敏感目标是否存在；未知 `ToolException` 走安全兜底。只读工具函数中的其他普通异常也记录类型后回填“未知失败、无内容返回”，不回显原文；`GraphBubbleUp`（含中断）明确上抛。`@wrap_tool_call` 对其返回记 `tool_failed`，不改变其他工具的异常语义。
- 新测试先红：说明性提示与兜底的 3 个用例为 `3 failed`；未知只读异常从 `Path.open` 冒出为单独 `1 failed`；中断被误转成工具错误又得到单独 `1 failed`。分别最小修复后转绿。旧 Read 测试中精确比较短提示的断言按本 Spec 的**输出契约变更**更新为精确比较新固定文本；没有放宽权限、状态、脱敏或分页断言。
- 假模型经 `build_local_application → app.run → create_agent → read_file → 第二次模型调用` 看到与原调用 ID 配对的两条错误 `ToolMessage`，一次是缺失路径、一次是参数错误；工具事件均为 `tool_failed`，最终可有 `run_completed`。本机测试未证明真实供应商如何理解或是否会选择正确的下一步。
- 定向 `.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_workspace.py tests/test_application_read_approval.py tests/test_agent_runtime_tool_events.py` 为 `56 passed in 0.73s`（在新增审批提示断言前）。最终 `.venv/bin/python -m pytest -q` 为 `237 passed in 3.77s`；`compileall` 退出 0，`pip check` 无依赖冲突；详细边界见交接第 125 节。
