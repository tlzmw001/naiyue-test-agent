# LangChain / LangGraph 同步与异步调用调研

日期：2026-09-30。范围：Python LangChain `create_agent` / LangGraph 的调用入口及两个可核对的开源项目；回答“实际使用是同步还是异步”，不统计行业占比，不做实现选型或代码修改。

## 结论

两种调用都在使用，选择主要由入口需求决定。需要完整结果的脚本、CLI 和示例常见 `invoke`；需要与其他 I/O 并发但只取完整结果的路径可用 `ainvoke`；需要逐段反馈的路径使用 `stream` 或 `astream`（新应用还可考虑事件流接口）。这四种接口构成“是否异步 × 是否逐段输出”两个维度，不能把 `ainvoke` 当作流式输出，也不能把 `stream` 当作异步。

本轮只抽查官方文档和两个 LangChain 组织的项目，不能据此声称“多数项目”采用哪一种。项目实际入口、框架版本、工具与 middleware 决定可用路径。

## 官方接口与调用场景

| 调用 | 调用者得到什么 | 典型入口 |
|---|---|---|
| `invoke` | 阻塞到本轮结束，返回最终状态 | 简单脚本、CLI、确定性测试 |
| `ainvoke` | `await` 到本轮结束，返回最终状态 | 异步服务中的非流式请求、并发 I/O |
| `stream` | 同步迭代中逐段获得事件或更新 | 同步流式消费者 |
| `astream` | 异步迭代中逐段获得事件或更新 | 异步 Web / 协议服务的实时进度 |

- [LangGraph Graph API 的 Async 章节](https://docs.langchain.com/oss/python/langgraph/use-graph-api#async)明确要求异步节点使用 `async def` 和 `await`，然后以 `ainvoke` 或 `astream` 调用；其示例为 `await llm.ainvoke(...)` → `await graph.ainvoke(...)`。这说明官方支持原生异步图，而非规定所有项目必须异步。
- [LangChain Streaming 文档](https://docs.langchain.com/oss/python/langchain/streaming)同时展示 `stream` / `astream` 和不同流模式；当前文档建议新应用考虑事件流来分别消费消息、工具调用和状态。流式与调用协程属于不同维度。
- [LangChain Runnable API 参考](https://reference.langchain.com/python/langchain-core/runnables/base)说明普通 Runnable 的默认异步方法可以通过线程池执行同步实现；这只是默认实现，不表示整条 Agent 链天然非阻塞。本机 LangGraph `Pregel.ainvoke` 覆盖了该默认方法，并通过 `astream` 执行图；内部模型、工具、存储和 middleware 仍需分别核对。
- LangGraph 的 `durability="sync"/"async"` 描述 checkpoint 写入时机，与 `invoke` / `ainvoke` 的调用方式不是同一个选择。

## 源码案例

| 来源和定位 | 看到的调用 | 能证明的范围 |
|---|---|---|
| [Deep Agents README，固定提交 dd0e2f5](https://github.com/langchain-ai/deepagents/blob/dd0e2f5366da3704585a372aa905734cafcec900/README.md) | Quickstart 使用 `agent.invoke(...)` | 官方维护项目提供同步完整结果入口；README 不代表其所有运行入口 |
| [同仓库 ACP 服务，固定提交 dd0e2f5](https://github.com/langchain-ai/deepagents/blob/dd0e2f5366da3704585a372aa905734cafcec900/libs/acp/deepagents_acp/server.py) | `async def prompt` 内用 `async for ... in agent.astream(...)`，还用异步状态读取 / 更新 | 同一个项目面向交互协议时采用异步流式入口；不能推断其所有工具都原生异步 |
| [Open Deep Research，固定提交 1b7d2e8](https://github.com/langchain-ai/open_deep_research/blob/1b7d2e80db9faa586165c60e09096dbbfd483a64/src/open_deep_research/deep_researcher.py) | 图节点为 `async def`，模型和工具调用使用 `await ...ainvoke(...)` | 多步骤 I/O 工作流采用异步节点；该仓库已于 2026-08-21 归档，只作为源码案例，不代表当前生产状态 |

这些案例的共同点是按调用入口选择 API；单个 README 示例和少量开源仓库不能作为“行业使用比例”的统计样本。

## 本项目的直接证据

- 当前链路是 `LocalApplication.run → SessionService.run → invoke_agent_run → agent.invoke`；[运行入口](/Users/zmw/naiyue-test-agent/naiyue_test_agent/agent_runtime/__init__.py)和 [CLI Spec](/Users/zmw/naiyue-test-agent/docs/specs/017-local-cli-entry.md)都是同步路径。它适合目前 CLI 的整轮输入和整轮输出，尚不提供运行中输入、逐段反馈或停止。
- 本地安装版本 `langchain 1.4.2`、`langchain_core 1.6.6`；`Pregel` 提供 `invoke`、`ainvoke`、`stream`、`astream` 及事件流方法。接口存在不等于现有 Agent 已能直接换入口。
- 不落盘假模型探针：现有 `build_runtime_agent` 用相同模型与 context 执行 `agent.invoke(...)`，实际输出 `invoke_result: probe`；执行 `await agent.ainvoke(...)`，实际输出 `ainvoke_error: NotImplementedError Asynchronous implementation of awrap_model_call is not available...`。当前选模 middleware 用同步 `@wrap_model_call`，工具事件 middleware 用同步 `@wrap_tool_call`。因此现有装配不能直接改成 `ainvoke`；先补齐并验证异步 hook 才能进入异步运行切片。该探针没有访问真实模型或网络。
- 已有 [Pi loop 差距分析](/Users/zmw/naiyue-test-agent/docs/pi-loop-gap-analysis.md)对另一个异步假工具证明过 `ainvoke` 任务取消会传到该工具；这不是本项目当前装配的异步可用证明，也不证明真实模型请求或外部副作用可取消。
- 旁证：[Pi 本地 Agent loop](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent-loop.ts) `a69bef789` 用异步流、等待模型/工具并传 `AbortSignal`；[AgentX 本地对话处理器](/Users/zmw/AgentX/AgentX/src/main/java/org/xhy/application/conversation/service/message/AbstractMessageHandler.java) `3fcfa0e` 同时有同步 `chat` 和流式回调。这两者分别是 TypeScript 自写 loop 和 Java LangChain4j fork，不作为 Python LangChain 用量样本。

## 对当前项目的建议与验证边界

维持当前同步 CLI 路径，等运行控制切片明确停止、运行终局和事件契约后，再为需要实时反馈的入口设计异步流式路径。先用假模型将现有选模、工具事件 middleware 补齐异步实现，并验证同会话 busy、异常、取消、部分输出及工具副作用状态；随后分别测试所选模型适配器和外部工具的真实 I/O 边界。若只需异步服务等待完整结果，`ainvoke` 足够；若要前端持续看到进度，验证 `astream` / 事件流。不能仅把同步调用放进 `async def` 就声称获得非阻塞、取消或流式能力。

本建议是调研后的待确认方向，没有修改架构决策、Spec、生产代码、配置或依赖。

## 本轮验证记录

1. `pwd` 和 `git rev-parse --show-toplevel` 均输出 `/Users/zmw/naiyue-test-agent`。
2. 用 `.venv/bin/python` 检查版本与方法：`langchain 1.4.2`、`langchain_core 1.6.6`，`Pregel.invoke/ainvoke` 均存在；源码显示前者迭代 `self.stream`，后者迭代 `self.astream`。
3. 用相同假模型和当前 `build_runtime_agent` 作不落盘探针：同步输出 `probe`，异步在 `awrap_model_call` 处抛 `NotImplementedError`。这是当前路径的确定性接口证据，不是压力测试或真实网络证据。
4. 本轮是只读调研与文档记录，没有运行项目构建或完整测试；不把此前测试结果当成本轮新验证。
