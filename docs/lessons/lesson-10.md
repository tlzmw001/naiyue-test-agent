# Lesson 10：结束原因从模型接入层到工具边界

状态：2026-09-30，代码地图与数据流已准备；本轮从真实 `AIMessage` 形状开始，讲解第一段：`normalize_stop_reason` 的 OpenAI 结束字段、保留原生元数据以及模型 middleware 写入项目字段。工具 hook 的逐行讲解留待下一段。本切片由助手实现；用户确认了架构职责和 Pi 的截断工具处理方式，但不能据此记为已能独立编写或应对面试追问。

## 一条具体数据流

示意的 LangChain 消息 `AIMessage(tool_calls=[{'id': 'cut', ...}], response_metadata={'finish_reason': 'length'})` 进入现有模型 middleware。`model_access.normalize_stop_reason` 从保留的原始元数据算出 `length`；middleware 在同一 `AIMessage.response_metadata` 写入 `naiyue_stop_reason='length'`。框架接着准备执行 `cut`，运行层 `wrap_tool_call` 从 `request.state['messages']` 找到最近且含 `cut` 的 `AIMessage`；它返回 `ToolMessage(tool_call_id='cut', status='error')` 而不调用 `handler(request)`。LangChain 把该结果放回消息历史，下一次模型调用可重新发完整工具调用。测试中第一个实际工具执行次数为 0，第二个完整调用执行 1 次。

## 代码地图

| 文件 | 阅读重点 |
| --- | --- |
| `model_access/stop_reason.py` | 哪个字段对应哪个统一值；为什么未知/缺失字段不当作正常结束；函数为何不改原始元数据。 |
| `agent_runtime/__init__.py` | `handler(request.override(model=model))` 后谁写 `naiyue_stop_reason`；工具 hook 怎么匹配调用 ID、短路并回填错误。 |
| `tests/test_model_stop_reason.py` | 四类适配元数据的纯映射与原始字段保留。 |
| `tests/test_agent_runtime_stop_reason.py` | 哪个断言证明工具未执行，哪个断言证明错误结果进入下一次模型输入和完整调用实际执行。 |

## 证据边界与待复习

假模型通过真实 `build_local_application → create_agent` 证明当前同步接线；已有私有配置经同一应用入口的真实请求观察到正常 `finish_reason=stop → naiyue_stop_reason=stop`，没有真实截断样本。模型最终文本被截断但没有工具调用时，当前运行终局仍未细分。未知结束元数据保持既有工具调用路径，不能把这一切片宣传为完整执行安全保障。后续需用用户复述或小改动检查理解，再记录独立能力。

本轮本地复核：`.venv/bin/python -m pytest -q tests/test_model_stop_reason.py tests/test_agent_runtime_stop_reason.py` 输出 `20 passed in 0.80s`。单独构造 `finish_reason='length'` 且带 `tool_calls` 的 `AIMessage`，纯函数返回 `length`，调用前后原生 `response_metadata` 都是 `{'finish_reason': 'length'}`。学习状态仅为第一段“已讲解”；理解检查、独立实现和面试追问均未验证。

2026-10-01 分支场景补课：四个互斥字段分支分别来自当前安装的 OpenAI Chat Completions (`finish_reason`)、OpenAI Responses (`status/incomplete_details`)、Anthropic (`stop_reason`) 和 Ollama (`done_reason`) 适配消息。统一结果只能表达当前代码已识别的五类，不代表各供应商所有停止语义都已覆盖。核对 [OpenAI Chat 文档](https://developers.openai.com/api/reference/resources/chat)、[OpenAI Responses 推理文档](https://developers.openai.com/api/docs/guides/reasoning)、[Claude 停止原因文档](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons) 与 [Ollama Chat 文档](https://docs.ollama.com/api/chat) 后发现：Claude `pause_turn` 是服务器工具暂停、应继续会话，当前映射为 `stop` 会丢失语义；Claude `model_context_window_exceeded` 是截断，当前映射为 `unknown`；`sensitive`、OpenAI `network_error` 和 `end` 尚未在这些官方结束值中找到依据，只能视作未验证兼容分支。此处只记录调查结果，不将其说成已修复。用户主动追问每个分支的触发场景，尚未据此记录为独立实现或面试能力。

2026-10-01 用户表示已理解供应商分支；这只记录为该部分的自述理解。随后讲解第二段 `_record_tool_call`：`ToolCallRequest` 的 `tool_call/state/runtime` 形状、反向找到最近 `AIMessage`、通过调用 ID 和 `length` 判断、返回错误 `ToolMessage` 时不调用 `handler`，以及正常执行和异常路径。本机 LangGraph `ToolNode._run_one` 显示 `handler` 最终通向 `_execute_tool_sync → tool.invoke`，wrapper 直接返回则不会进入执行函数。本轮定向 `.venv/bin/python -m pytest -q tests/test_agent_runtime_stop_reason.py` 为 `2 passed in 0.82s`。工具 hook 当前记为“已讲解”；理解检查、独立实现、面试追问均未验证。

2026-10-01 用户追问调用 ID 的 `any(...)` 判断。核对本机 LangChain `factory.model_to_tools`：待执行调用从最近 `AIMessage.tool_calls` 取出并用 `Send('tools', [tool_call])` 发给 ToolNode；本机 LangGraph `ToolNode._run_one` 把该调用装入 `ToolCallRequest.tool_call`，同时从图通道取状态。正常 `create_agent` 路径里 ID 匹配是已有框架不变量，这个 `any(...)` 对当前路径基本冗余，不应称为必需安全校验。无文件最小探针：最近 AI 的调用 ID 为 `cut`、统一原因 `length` 时，请求 ID=`cut` 得 `handler_called=False/status=error`；人为换成 ID=`other` 得 `handler_called=True/status=success`。因此不一致时现有逻辑会放行实际执行；若未来允许工具调用与消息状态分离，必须先明确是断言不变量还是拒绝执行，不能把当前判断宣传为 fail-closed 防护。用户指出了正常链路的关键不变量；独立实现和面试追问仍未验证。

2026-10-01 对 Pi `a69bef789` 的 `pause_turn` 补充完整调用链并修正前面的过强判断：`packages/ai/src/api/anthropic-messages.ts:1381` 把它映射成 `stop`，保留 `rawStopReason`；`packages/agent/src/agent-loop.ts:193-274` 只对 `error/aborted` 直接终止、对真实客户端 `toolCall` 继续工具循环。Claude 官方说明 `pause_turn` 来自服务器工具暂停，正常不会同时留下待执行客户端 `tool_use`；所以在 Pi 当前默认客户端工具路径，它通常没有 `toolCall`，若无已排队的 steering/follow-up，内层循环退出并发 `agent_end`，没有看到按该原生原因自动重发请求。Pi 的该适配器默认 `params.tools` 来自客户端工具转换，解析内容块只覆盖 text/thinking/tool_use；所核对路径没有证明能保留服务器工具块并按官方要求原样续传。之前把 `pause_turn → stop` 直接定性为当前客户端工具路径的错误并不准确：它是 Pi 也采用的范围取舍；若以后支持 Claude 服务器工具，就需要另做暂停续传契约和验证。证据为本机源码静态追踪，未做真实 Claude `pause_turn` 请求。官方续传要求见 [Claude 文档](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)。
