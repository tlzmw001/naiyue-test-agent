# Lesson 9：模型响应原因、运行终局与停止请求

状态：2026-09-30，已核对当前项目、Pi `a69bef789`、AgentX `3fcfa0e` 与本机 LangChain `1.4.2` 的相关调用点。本轮只讲机制和首切片边界；尚无用户复述、独立实现或面试追问证据，也没有新的运行控制实现。

## 一个截断例子

以下是示意数据，不是当前网关的实际响应：LangChain 适配后的 `AIMessage(tool_calls=[...], response_metadata={'finish_reason': 'length'})`。`length` 描述**这一条模型响应**因输出上限截断；它不表示用户按了停止，也不保证工具参数完整。如果框架继续调用工具且 `agent.invoke` 最终返回，当前项目会记录 `run_completed`。这只能说明整次调用没有抛异常，不能说明输出可被测试 Agent 安全使用。

| 概念 | 生产者与时点 | 例子 | 当前项目状态 |
| --- | --- | --- | --- |
| 模型响应原因 | 供应商先返回原始字段，LangChain 适配器把它放进 `AIMessage.response_metadata`；字段仍因供应商/API 不同 | 正常结束、工具调用、截断 | 当前没有项目级规范化或用于工具调度的保护 |
| 运行终局 | Agent 运行层在一次 `run_id` 结束时判定 | 已完成、失败；未来还有按边界停止或取消 | 当前 `agent.invoke` 返回即 `run_completed`，抛 `Exception` 即 `run_failed` |
| 停止请求 | 用户或应用在运行过程中发出 | 立即取消、当前 turn 完成后停止 | 当前同步 CLI 不读取运行中的新输入，尚无停止接口 |

## 代码地图与来源边界

- 项目 `agent_runtime/__init__.py`：模型 middleware 目前处理选模、异常和用量；`invoke_agent_run` 用 `agent.invoke` 的返回/异常记录运行终局。
- 本机 LangChain `agents/factory.py` 工具路由依据最近 `AIMessage.tool_calls`，此路径没有读取 `response_metadata.finish_reason`；本机源码事实，不代替所有供应商的真实行为验证。
- Pi `packages/ai/src/api/` 供应商适配器把原生 `finish_reason`、`stop_reason` 或 `status/incomplete_details` 映射为统一的 `AssistantMessage.stopReason`，同时保留 `rawStopReason`；`packages/agent/src/agent-loop.ts` 才使用统一值。`error/aborted` 直接终止；`length` 且有工具调用时生成失败工具结果，不执行截断参数，再让模型有机会重新发完整调用。没有工具调用的 `length` 不走这个保护分支，不能笼统称“Pi 把所有截断运行判失败”。
- AgentX 的 `ChatSessionManager.interruptSession` 设置会话中断标志并关闭 SSE；本机尚未追到模型/工具调用链消费标志的证据，不能视为底层请求已取消。

## 下一设计点

第一个最小切片建议只处理**模型响应原因与运行终局的关系**：明确何种可观察的截断信号应阻止工具执行、如何安全上报终局；使用假模型分别验证正常回答、工具调用、截断和异常。异步活动运行、steering、follow-up、用户停止请求各自后置。具体接口、缺失元数据的处理和事件命名须在 Spec 中确定，再按 TDD 实现。

理解检查：若模型响应已标记 `length`，但 `agent.invoke` 后来正常返回，当前 `run_completed` 能否证明这条回答完整且工具参数安全？为什么？

## 用户追问后的字段来源核验

- 本项目 middleware 接收到 `ModelResponse.result` 中的 LangChain `AIMessage`，不是供应商原始 HTTP JSON。当前安装版本的 OpenAI Chat Completions 适配路径把响应的 `choice.finish_reason` 放入 `ChatGeneration.generation_info`，LangChain Core 再并入 `AIMessage.response_metadata`；Anthropic 适配路径保留 `stop_reason`；OpenAI Responses API 路径还可能保留 `status` 与 `incomplete_details`。因此不能只认一个 `finish_reason` 字段。
- 用已有私有配置发一条合成提示 `Reply with only OK.` 的真实探针，退出码 0，只打印类型与元数据字段：`AIMessage`、`finish_reason=stop`，没有 `stop_reason/status/incomplete_reason`。未打印回答、凭证或响应正文。它只证实此配置下**正常结束**时适配消息的形状，尚未制造或观察真实截断。
- 设计含义：若借鉴 Pi，须先在本项目建立可验证的供应商字段到项目统一原因的映射，再决定在工具路由前如何消费；不能直接把 Pi 的 `stopReason` 当成 LangChain `AIMessage` 的现成属性。用户尚未对此数据流复述或独立实现。
