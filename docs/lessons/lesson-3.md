# Lesson 3：`create_agent` 循环与模型接入边界

日期：2026-09-29。当前环境：LangChain 1.4.2。此课只读本机安装源码、官方文档和项目代码；没有新增功能。用户理解状态待本课后复述，不因讲解而记为已掌握。

## 一个具体运行

输入 `{'messages': [('user', 'Read the note and answer.')]}`，假模型先返回 `AIMessage(tool_calls=[{'name': 'read_note', 'args': {}, 'id': 'read-1'}])`。框架执行工具，回填 `ToolMessage(content='rule: blocked user first', tool_call_id='read-1')`，再次调用模型，得到无工具调用的 `AIMessage(content='Use blocked-user priority.')`。本次只读探针实际输出节点 `__start__ / model / tools / __end__`，结果消息类型依次为 `HumanMessage / AIMessage / ToolMessage / AIMessage`。工具返回值不等于最终回答，第二次模型调用才决定回复。

## 安装源码的主要调用链

源码路径：`.venv/lib/python3.12/site-packages/langchain/agents/factory.py` 和 `.venv/lib/python3.12/site-packages/langgraph/prebuilt/tool_node.py`。

1. `create_agent` 将普通工具建成 `ToolNode`，创建 `model` 节点、middleware 节点和条件边，最后 `graph.compile(...)`；不是项目代码里的 Python `while`。
2. `model_node` 从状态中的 `messages` 组装 `ModelRequest`，含默认模型、可用工具、运行 context 和输出策略。当前项目的 `select_model` middleware 在这里通过注入的 `resolve_model(session_id)` 替换本次请求模型。
3. `_execute_model_sync` 为所选模型绑定工具，把系统消息置于消息列表开头，调用 `model_.invoke(messages)`，得到标准 `AIMessage`；`_build_commands` 将回复写回图状态。`AgentState.messages` 使用 `add_messages` reducer，所以新的 AI/工具消息按消息 ID 规则并入历史。
4. `_make_model_to_tools_edge` 查看最近 `AIMessage.tool_calls` 及已有 `ToolMessage.tool_call_id`：无工具调用则退出；有尚未回填的普通工具调用则转到 `tools`。`ToolNode` 按工具名执行并返回带同一 ID 的 `ToolMessage`。`_make_tools_to_model_edge` 默认回到下一轮 `before_model`/`model`；`return_direct`、结构化输出或中断等是例外。
5. `before_model` 在每次模型调用前运行，`after_model` 在模型回复后、普通工具执行前运行；它们不直接等同于 Pi 的 `turn_end`。下一条普通用户消息由新的 `invoke` 提交；要让框架自动复用历史，需要配置 checkpointer 并保持同一 `thread_id`（调用方手动传全量历史是另一种方式）。运行中插话另需明确排队语义。

普通工具调用的参数校验错误与工具执行中的异常不是一类结果：当前 `ToolNode` 默认把部分调用参数错误转为带错误状态的 `ToolMessage`，执行异常默认上抛；项目还没有为读写工具定义自己的失败记录与反馈策略。一个模型回复包含多个普通工具调用时，当前同步 `ToolNode` 可并发执行它们，未来文件写入需要处理冲突与顺序。

当前固定折扣实验的 `ToolStrategy(DiscountCase)` 另有特例：即使 `tools=[]`，框架也会把结构化 schema 暴露成输出工具；模型以该工具提交参数，框架解析成 `structured_response` 并补一个配对的 `ToolMessage`。这个 `ToolMessage` 不表示执行了文件/HTTP 工具。

## 模型能力的三级边界

| 级别 | 当前证据 |
| --- | --- |
| 框架接口 | `BaseChatModel`、供应商集成包、`init_chat_model` 与 `request.override(model=...)` 支持统一调用和按轮替换模型；具体模型仍需支持项目使用的工具调用等特性 |
| 项目接线 | 只有诊断脚本用 `ChatOpenAI` 加外部网关地址和 gpt-5.5；`agent_runtime` 接受上层传入的已创建模型及解析函数，尚无模型配置读取、注册、凭证引用或正式会话选型服务 |
| 已验证运行 | 一次真实网关探针模型→工具→模型；假模型 A→工具→B 的确定性测试。没有真实 Claude、开源/本地模型或跨供应商切换测试 |

项目环境中 `langchain_openai` 已安装；`langchain_anthropic`、`langchain_ollama` 和 `langchain_huggingface` 未安装。此事实不是框架缺少这些集成。未来本地模型可先通过兼容服务或专用适配包成为 `BaseChatModel`，但工具调用、流式输出、错误与上下文限制须用真实端点验证。

## 本次验证与理解检查

- `.venv/bin/python -m pytest -q tests/test_agent_runtime.py`：`3 passed in 0.12s`。
- 只读假模型探针：图节点 `['__end__', '__start__', 'model', 'tools']`；消息顺序 `HumanMessage → AIMessage(tool_calls=[read_note]) → ToolMessage(tool_call_id=read-1) → AIMessage`。
- 理解检查：为什么 `session_id` 已进入 middleware，第二次 `agent.invoke` 仍不会自动看到第一次对话？为什么折扣实验的 `ToolMessage` 不能证明真实文件工具执行？用户答复前只记“已讲解”，不记“独立实现”。

参考：[LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)、[自定义 middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[模型集成](https://docs.langchain.com/oss/python/langchain/models)、[会话短期记忆](https://docs.langchain.com/oss/python/langchain/short-term-memory)。
