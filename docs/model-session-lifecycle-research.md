# Agent 创建与多模型会话：定向调研（2026-09-29）

更新说明：下文“会话模型固定”的建议是提出时的候选前提。用户随后明确需要参考 Pi 的 turn 边界在同一会话运行中切换；当前设计讨论以 [Agent 运行、会话与工具架构草案](agent-runtime-architecture-draft.md) 为准。不要把下文按请求构造的建议误读为已确认方案。

## 问题与证据边界

场景：用户在会话 A 选择模型 A，另开会话 B 选择模型 B；同一会话可能继续多轮。本次只决定如何理解 Agent 对象、模型选择和会话状态的生命周期，不实现模型配置、存储或 UI。以下是官方文档和三个项目的定向样本，不是行业使用率抽样；因此无法据此断言“多数人”选哪种。

需要分开看四件事：`create_agent(...)` 构造可调用的编译图；`agent.invoke(...)` 启动一次运行；`thread_id` 标识需要延续的状态；checkpointer 保存该状态。新会话不要求新建图，重建图也不会自动丢失或自动恢复历史；实际结果取决于 checkpointer、`thread_id`、图/状态兼容性和应用层会话归属。会话选择的模型配置须由应用明确保存或传入，`thread_id` 本身不是模型配置，也不是权限校验。

## 一手材料

| 来源 | 观察到的做法 | 不能据此推断 |
| --- | --- | --- |
| [LangChain 短期记忆文档](https://docs.langchain.com/oss/python/langchain/short-term-memory) | 一个 `create_agent` 配合 checkpointer，同一 `thread_id` 连续 `invoke`；生产持久化示例换数据库 checkpointer。 | 官方示例不等于生产系统普查，也未回答多模型应如何选。 |
| [LangChain 动态模型选择文档](https://docs.langchain.com/oss/python/langchain/models#dynamic-model-selection) | `wrap_model_call` 在模型调用边界用 `request.override(model=...)` 选模型；一个 Agent 可在不同调用使用不同模型。 | 框架支持不等于固定会话模型也必须使用 middleware。 |
| Pi，当前本地 HEAD `a69bef789`：[新会话](https://github.com/earendil-works/pi/blob/a69bef789/packages/coding-agent/src/core/agent-session-runtime.ts#L226-L258)、[创建 Agent](https://github.com/earendil-works/pi/blob/a69bef789/packages/coding-agent/src/core/sdk.ts#L304)、[切换模型](https://github.com/earendil-works/pi/blob/a69bef789/packages/coding-agent/src/core/agent-session.ts#L1598-L1616) | 新会话替换 runtime 并创建 Agent；已有会话切换模型时更新该会话 Agent 的模型状态并记入会话记录。 | Pi 的单用户编码会话管理不能直接照搬为多租户服务，也不是每轮对话都新建 Agent。 |
| AgentX，当前本地 HEAD `3fcfa0e`：[依赖](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e/AgentX/pom.xml#L249-L268)、[聊天路径](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e/AgentX/src/main/java/org/xhy/application/conversation/service/message/AbstractMessageHandler.java#L115-L147)、[流式路径](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e/AgentX/src/main/java/org/xhy/application/conversation/service/message/AbstractMessageHandler.java#L229-L241)、[Agent 构造](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e/AgentX/src/main/java/org/xhy/application/conversation/service/message/AbstractMessageHandler.java#L469-L487) | Java LangChain4j fork；聊天请求创建内存并装入历史，流式路径按本次供应商/模型创建客户端，再经 `AiServices` 构造 Agent。 | 这是流式请求路径，不是“每开新会话才构造”；Java 的 `AiServices` 开销不能等同 Python `create_agent`。 |
| [DeepAgents ACP server](https://github.com/langchain-ai/deepagents/blob/main/libs/acp/deepagents_acp/server.py) | 接收现成编译图或按会话上下文构造图的 factory；切换会话时，前者复用图、后者按会话模型等配置重建。 | 同一项目支持两种模式，不能将其中一种说成唯一惯例。该链接指向可变的 `main`，采纳前需复核。 |

## 方案比较

| 方案 | 适合条件 | 主要代价或风险 |
| --- | --- | --- |
| 复用一个图，在 middleware 中每次模型调用选型 | 真要按轮次/状态动态路由、故障切换或中途换模型；框架官方支持。 | 选型逻辑每轮都会运行；若从可变全局变量取“当前模型”，A/B 会话会串模型。需用本次运行/会话上下文和授权配置解析，记录实际调用模型，并验证不同供应商的工具、结构化输出与历史兼容性。 |
| 每个会话构造一次 Agent | 模型及工具集合在整个会话固定，进程内会话数量有限。 | 常驻对象及客户端可能随会话数增长；重启、多进程、配置更新与会话回收均需管理。图对象仍不能代替可持久恢复的会话状态。 |
| 每次请求按会话配置构造 Agent | 入口无状态、每次运行前可明确解析模型配置；不需保留每会话图对象。 | 每次构造图/客户端的耗时和资源代价需实测；同一会话使用相同状态结构、持久 checkpointer 与稳定 `thread_id` 才能可靠续聊。配置版本/工具变化后恢复也要验证。 |
| 按模型配置缓存 Agent | 真实测量发现构造开销显著且配置复用率高。 | 缓存键须包含租户、凭证引用、工具/策略版本；失效、凭证轮换和隔离复杂，首期无数据时不宜加入。 |

## 对本项目的建议（待设计确认）

首个 MVP 仅需“会话 A 固定模型 A、会话 B 固定模型 B”，暂不需要一轮内动态路由。优先考虑：应用入口按会话解析并校验模型配置；每次请求以选中的模型构造 Agent，再用同一状态结构、checkpointer 与该会话 `thread_id` 调用；记录会话选定模型与每次实际模型调用。这样先把“模型配置属于哪个会话”和“历史属于哪个会话”两个契约做明确，再通过真实工具链、A/B/A 交错请求和故障注入验证。此处是设计建议，不是已实现能力，也不改变已确认的 Python + `create_agent` 技术路线。

若图构造/客户端初始化的实测开销显著，评估按配置缓存或按会话复用；若需要一轮内按步骤换模型，再考虑 `wrap_model_call`。两种升级都要验证并发隔离、凭证轮换、供应商能力、版本变化及恢复行为。首次演示可用内存 checkpointer 检查消息流；生产可靠性需要可持久化方案、会话归属授权和重启恢复测试。没有这几项证据，不能称为生产级会话能力。

## 本轮验证与遗留问题

- 通过本地源码行与官方文档交叉核对上述调用位置。本轮在项目虚拟环境运行以下不调用外网的验证命令；每次循环都重新调用 `create_agent`，但共用同一个内存 checkpointer，并用不同 `thread_id` 隔离会话：

```bash
.venv/bin/python - <<'PY'
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
models = {'A': FakeListChatModel(responses=['A-reply']), 'B': FakeListChatModel(responses=['B-reply'])}
for session_id, model_id in [('A', 'A'), ('B', 'B'), ('A', 'A')]:
    agent = create_agent(model=models[model_id], tools=[], checkpointer=checkpointer)
    result = agent.invoke({'messages': [('user', 'ping')]}, config={'configurable': {'thread_id': session_id}})
    print(f'{session_id} {model_id} {result["messages"][-1].content} messages={len(result["messages"])}')
PY
```

实际退出码 0，输出依次为 `A A A-reply messages=2`、`B B B-reply messages=2`、`A A A-reply messages=4`。这只证明当前进程中的确定性样例；没有真实供应商、跨进程、并发或持久化证据。
- 尚无可信的行业使用比例数据，不能回答“多数人”究竟是哪种。下一步设计时先确定会话模型能否中途修改及持久化边界，再写最小 Spec 与验证样例；在这之前不实施模型适配层。
