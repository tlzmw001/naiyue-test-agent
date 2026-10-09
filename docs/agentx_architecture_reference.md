> 2026-09-28 导入的历史调研快照；原核验日期为 2026-09-23。其中“未选型”等表述属于历史状态，当前已选方案以 README 和 project-plan.md 为准。

# AgentX 源码参考：不用 LangGraph 如何组织 Agent

核验日期：2026-09-23。用途：为测试 Agent 重设计提供源码依据，不代表已批准技术选型。

## 1. 仓库与证据边界

- 用户确认的仓库：https://github.com/lucky-aeon/AgentX
- 本地浅克隆：`/Users/zmw/AgentX`，`git clone --depth 1`。
- 核验提交：`3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`。
- 本轮只读源码；没有安装依赖、构建、运行测试、启动服务或请求真实模型。不能据此证明运行成功、生产可靠性或评测效果。
- AgentX 仓库保持干净；研究笔记保存在 AIAutoTest 的设计文档目录。

## 2. 最关键的结论

它使用 Java 17、Spring Boot 和 LangChain4j，并不是 Python LangChain。`AgentX/pom.xml:31` 声明版本 `1.0.4.3-beta7-SNAPSHOT`，依赖 groupId 为 `com.github.lucky-aeon.langchain4j`，来源是作者的 fork。

当前默认流式聊天路径使用 `AiServices` 的模型—工具循环；AgentX 自己实现上下文准备、工具装配、会话/消息管理、长期记忆、SSE 传输和追踪。已核验的这条路径不依赖 LangGraph。

丰富的产品功能不要求使用图编排；它们可以通过循环的输入、工具执行器、回调和外围应用服务接入。但使用框架现成循环，与应用自己掌控循环的每一轮，是两个不同层次。

## 3. 当前默认聊天调用链

以下源码路径均相对于 `/Users/zmw/AgentX/AgentX/src/main/java/org/xhy/`。

```text
ConversationAppService.chat
  → prepareEnvironmentByRequestType：准备会话、模型、历史等上下文
  → MessageHandlerFactory.getHandler：普通聊天选 agentMessageHandler
  → AgentMessageHandler（继承 TracingMessageHandler / AbstractMessageHandler）
  → AbstractMessageHandler.chat
      → 初始化 MessageWindowChatMemory、加入历史/摘要/记忆
      → provideTools：创建 MCP ToolProvider
      → processStreamingChat
          → buildStreamingAgent：AiServices + 模型 + memory + 两类工具
          → agent.chat：得到 TokenStream，注册回调并启动
              → LangChain4j 内部模型—工具循环
              → 工具/文本事件、最终响应、异常回调
                  → SSE、消息持久化、追踪、异步记忆抽取
```

主要定位：

| 源文件 | 位置及职责 |
|---|---|
| `application/conversation/service/ConversationAppService.java` | `chat:140`；Token 上下文策略处理约 `385` |
| `application/conversation/service/handler/MessageHandlerFactory.java` | `getHandler:30`，默认选择 AgentMessageHandler |
| `application/conversation/service/message/agent/AgentMessageHandler.java` | `provideTools:42`，接入 MCP 工具 |
| `application/conversation/service/message/AbstractMessageHandler.java` | `processStreamingChat:229`、`processChat:330`、`buildStreamingAgent:470` |

## 4. 模型—工具循环到底在哪

依赖 fork 的对应 tag 指向提交：`d110efc4fd9d9e0dd63b9907181873778a733181`。

[AiServiceStreamingResponseHandler.java 源码](https://github.com/lucky-aeon/langchain4j/blob/d110efc4fd9d9e0dd63b9907181873778a733181/langchain4j/src/main/java/dev/langchain4j/service/AiServiceStreamingResponseHandler.java#L186)

`onCompleteResponse` 的实际控制流：

1. 把模型响应加入当前 memory。
2. 如果响应包含工具请求，依次按工具名查找 ToolExecutor 并执行。
3. 把执行结果转为 ToolExecutionResultMessage，加入 memory，触发工具完成回调。
4. 用新消息历史再次构建模型请求，并创建下一轮响应处理器。
5. 再调用 streamingChatModel.chat，下一轮完成后重复以上步骤。
6. 没有工具请求时，触发应用注册的最终响应回调。

因此没有在 AgentX 中看到 `while`，不代表没有循环；这个版本通过响应回调再次发起模型请求实现循环。未对整个 fork 与上游做 diff，不能声称所有行为均与官方版本一致。

## 5. 各扩展能力怎样接入

| 能力 | 已核验机制 | 对我们项目的启示 |
|---|---|---|
| 本地工具 | BuiltInToolRegistry 扫描 Spring 的 BuiltInTool provider，得到 ToolSpecification → ToolExecutor 映射，再注册给 AiServices | 工具定义和执行器可以独立于循环 |
| RAG 工具 | RagBuiltInToolProvider 定义 `knowledge_search`，执行时调用 RAGSearchAppService | 检索服务独立，Agent 只持有适配工具；本轮不评价检索质量 |
| 外部 MCP | AgentToolManager 使用 HttpMcpTransport、DefaultMcpClient、McpToolProvider；最终注册给 AiServices | MCP 是外部工具接入路径；不意味着整个 Agent 已作为 MCP Server 暴露 |
| 短期上下文 | 每次请求创建 InMemoryChatMemoryStore，填充应用加载的历史、摘要、系统提示；窗口上限 1000 条消息 | 请求中的 memory 与持久化会话不是同一个对象；消息数也不是 token 预算 |
| 上下文压缩 | ConversationAppService 调用 tokenDomainService.processMessages，更新保留消息与摘要 | 上下文策略可以在调用模型之前由应用控制；不能据此断言每次工具回合都重新压缩 |
| 长期记忆读取 | buildMemorySection → MemoryDomainService.searchRelevant，向量检索按 userId 过滤，结果追加到 system prompt | 回忆检索是模型调用前的上下文处理 |
| 长期记忆写入 | 成功且非公开/非 RAG 对话结束后，将当前用户文本交给异步抽取；MemoryDomainService 去重/合并、数据库保存和向量写入 | 写入策略、可信度和失效策略属于应用，不是窗口自动完成的功能 |
| 会话 | 消息/上下文由领域服务持久化；ChatSessionManager 管理 sessionId 对应的活跃 SSE 连接及中断标记 | 活跃连接、对话历史、任务执行检查点需要区分 |
| 追踪 | TracingMessageHandler 在开始、模型完成、工具完成、结束等钩子调用 TraceCollector | 在模型/工具边界记录事件可独立于图框架 |

工具源码：`application/conversation/service/message/builtin/BuiltInToolRegistry.java:95`、`builtin/rag/RagBuiltInToolProvider.java:65`、`agent/AgentToolManager.java:36`。

记忆源码：`AbstractMessageHandler.java:191` / `:554`、`domain/memory/service/MemoryExtractorService.java:123`、`MemoryDomainService.java:62` / `:139`。

README 指向独立 MCP gateway 子仓库。本轮只核验 AgentX 一侧的 MCP 消费接线，没有审计该网关的进程隔离或沙箱。

## 6. 不能直接当成已验证能力的地方

### 6.1 工作流类存在，不等于当前入口使用

仓库包含 AgentWorkflowContext、AgentWorkflowState、TaskSplitHandler 等代码，AgentMessageHandler 类注释也提到任务拆分。然而当前默认入口实际走 AiServices；本轮在主 Java 源码中搜索未找到 `new AgentWorkflowContext` 或 `AgentEventBus.register` 的调用。

准确结论：当前核验的默认聊天链未接入这套拆分工作流。不能仅凭目录/注释宣称每次聊天都执行显式 Planner → Executor → Summary 状态机。

### 6.2 追踪不等于完整评测

主流式路径的 `onModelCallCompleted` 在最终 `onCompleteResponse` 回调触发；对应依赖会累计工具循环内的 token usage。因此不能把这里一条完成记录直接解释为每一次底层模型请求的独立 span。

`buildToolCallInfo` 将工具回调记录标为 `success(true)`；这不足以证明工具返回的业务操作成功，更不足以证明生成测试正确。本轮没有核验到与用户目标匹配的完整数据集、轨迹评分器和版本回归评测链，不据此断言整个仓库绝无其他相关代码。

我们仍需自己设计：每轮模型调用记录、每次工具调用证据、语义成功判断、任务评分、版本固定、重复实验与回归报告。

### 6.3 生产可靠性需要运行证据

源码中记忆召回异常会返回空串，调度记忆写入的异常被忽略。这类降级可保持聊天可用，但学习项目应记录降级事件，避免“记忆未生效却无法观测”。追踪上下文使用 InheritableThreadLocal，文件自身也提示线程池复用风险；本轮没有并发复现，不将其报告为已确认线上故障。

没有运行故障注入、权限测试、工具取消验证或沙箱逃逸检查，因此不宣称系统具备全面生产保证。

## 7. 对框架选择的修正

之前把“想自定义扩展”直接推到 StateGraph，理由不充分。需要按循环控制要求区分：

1. 复用现成循环：类似 AgentX，用框架循环，外围自主实现工具、记忆、上下文、追踪。
2. 自写小循环：Python + 模型/工具组件，自己控制调用顺序、工具错误策略、预算和逐步事件；持久化、取消、恢复等也需承担设计和验证成本。
3. 自定义图：用 StateGraph 明确状态和流转，并利用已有检查点/中断机制；仍需应用负责权限和副作用幂等。

这三种路线都能构建测试 Agent。不能以“不用图就不能生产”或“用了图就自动生产级”作为选择依据。

还需避免跨生态类比：Python LangChain 当前 `create_agent` 底层建立在 LangGraph 上；不显式编写 StateGraph 不等于内部没有 LangGraph。LangChain4j 是不同的 Java 项目，AgentX 的实现不能直接用于推断 Python 包的内部结构。

官方参考：[LangChain overview](https://docs.langchain.com/oss/python/langchain/overview)、[LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)。

本次不冻结选型、不改此前排期、不替换 Pi。下一步应结合用户想亲自控制的循环行为，确定最小运行内核，再讨论各模块设计。
