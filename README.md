# Naiyue Test Agent

面向 HTTP/RPC 接口测试的 AI Agent 项目，当前已实现本地模型—工具循环、会话、事件流及同步／异步应用基础链路；RAG 与业务评测按后续切片推进。

项目目录与仓库名：`naiyue-test-agent`。初始化日期：2026-09-28。

## 项目目标

这个项目用于真实测试工作，也用于测试开发、AI 测试相关岗位的简历和面试展示。以生产级工程要求为目标，围绕真实场景验证效果、可靠性、权限边界和可维护性；当前已有本地 Agent 基础链路及验证，尚无已验证的生产业务能力。

用户有三年测试开发经验。代码主要由 AI 助手实现，开发过程同时承担教学任务：用户要逐步能够独立读懂、编写、修改和调试关键代码，并在面试中解释架构、机制、实验结果与取舍。

## 已确认的技术方向

- Python + LangChain `create_agent`，通过自定义 middleware 扩展模型—工具循环。
- 四层架构：模型接入 → Agent 运行 → 测试 Agent 策略 → 应用入口。
- 上下文、工具/MCP、记忆和轨迹均可自定义接入；RAG、测试执行和评测保持独立模块。
- 支持多个模型配置和按运行选择；本地模型、动态切换和故障备用按阶段验证。
- 首期复用标准循环，不手写 StateGraph；`create_agent` 底层仍使用 LangGraph。
- 评测从第一版开始，结果质量与轨迹行为分别评价。
- 首期可展示入口选择本地 Web；先用 CLI 验证核心链路，再接同一应用服务。当前不做多用户或账号登录；以后若部署为 SaaS，须另行完成身份、授权、租户隔离和持久化验证。

开发环境已使用 Python 3.12 + venv + pip，依赖版本记录在 requirements.txt。首个模型接入选择 HeiyuCode（https://www.heiyucode.com）与 gpt-5.5；已通过 Responses API 验证最小响应和一次 create_agent 工具循环。持久会话现采用本地SQLite元数据和官方checkpoint，已验证安全完成会话的同步/异步保存及跨进程恢复；受限恢复、多用户隔离及SaaS部署仍是后续独立能力。

## 范围与时间

首批业务：基于知识库设计用例，结合源码与用例生成、校验和执行 HTTP/RPC 自动化测试。

首期约半个月，每天 1–2 小时，合计 15–30 小时，包含学习时间。先打通一个有验证证据的核心闭环，再持续扩展 PDF/网页解析、重排、完整记忆、MCP、安全与沙箱、本地模型和训练实验。

## 阅读顺序

系统代码课堂使用 [全项目代码学习路线](docs/1.0/learning-roadmap.md)：当前前后端行数、10章阅读顺序、逐文件覆盖表与掌握标准。用户说“开始”后一次一个小课；下面按开发阶段列出的Spec/旧lesson作为参考，不按文档编号直接逐个上课。

1. [AGENTS.md](AGENTS.md)：工程规则与开发学习方式。
2. [开发学习交接](docs/handoff.md)：最新进度、首个单元及新会话提示词。
3. [项目设计与排期](docs/project-plan.md)：目标、模块边界、评测和已确认选型。
4. [分层命名与实施路线](docs/implementation-roadmap.md)：四层职责、当前代码位置与首期小目标顺序。
5. [AgentX 源码参考](docs/agentx_architecture_reference.md)：2026-09-23 的静态调研记录。
6. [首个运行层 Spec](docs/specs/001-model-selection-boundary.md)与[Lesson 1](docs/lessons/lesson-1.md)：模型调用边界按会话选模的设计、代码地图和理解检查。
7. [首个 HTTP 业务场景 Spec](docs/specs/002-discount-policy-first-scenario.md)：`discount_system` 折扣规则优先级冲突的公开契约、验收样本与证据边界。
8. [结构化用例设计 Spec](docs/specs/003-discount-case-design.md)与[Lesson 2](docs/lessons/lesson-2.md)：从公开契约文本到 Pydantic 用例、结构化工具调用与引用校验。
9. [本地模型配置 Spec](docs/specs/004-local-model-config-format.md)：`.env/models.json` 的格式、读取、接线与验证边界。
10. [模型接入 thinking Spec](docs/specs/005-model-access-thinking.md)：`model_access/` 包、默认等级回退、供应商参数映射及证据边界。
11. [后续开发与补充清单](docs/future-work.md)：集中记录未实现、未验证和待学习的事项及完成判据。
12. [提示词缓存用量观测 Spec](docs/specs/007-prompt-cache-usage-observation.md)：同步模型调用的缓存统计与 HeiyuCode 合成前缀实验。
13. [OpenAI 认证失败分类 Spec](docs/specs/008-openai-authentication-failure.md)：SDK 原生异常的脱敏分类与当前网关无效凭证探针。
14. [Anthropic 与 Ollama 错误分类 Spec](docs/specs/009-non-openai-model-errors.md)：两家 SDK 的保守分类、本机 HTTP mock 和 Agent 接线验证。
15. [模型请求超时与重试 Spec](docs/specs/010-model-request-policy.md)：默认 60 秒、在线模型 2 次 SDK 重试的配置及本机请求次数验证。
16. [同进程连续消息 Spec](docs/specs/011-in-memory-agent-continuation.md)：可选 checkpointer 的接线、跨两次调用的历史延续与假模型切换证据。
17. [Agent 运行层包归类 Spec](docs/specs/012-agent-runtime-package.md)：`agent_runtime/` 的职责与公开导入兼容验证。
18. [同步运行事件 Spec](docs/specs/013-synchronous-run-events.md)：新同步入口将 run 开始/终局与模型选型、用量/失败按 run ID 关联；仅有确定性测试接线。
19. [进程内会话服务 Spec](docs/specs/014-in-memory-session-service.md)：应用层可信会话映射、模型切换与同会话忙碌规则。
20. [同步运行路径中的工具事件 Spec](docs/specs/015-synchronous-tool-events.md)：当前 `invoke` 路径的工具开始/完成/失败事件及 run 会话关联。
21. [进程内应用组合 Spec](docs/specs/016-local-application-composition.md)：一份模型目录与会话服务、Agent、内存 checkpoint 的配对入口。
22. [Pi loop 差距分析](docs/pi-loop-gap-analysis.md)：最小 CLI 之后、通用工具之前的 steering、follow-up、停止语义与 LangChain 接线探针。
23. [最小本地 CLI Spec](docs/specs/017-local-cli-entry.md)：同一会话的连续输入、选模与安全失败展示。
24. [模型结束原因与截断工具调用 Spec](docs/specs/018-model-stop-reason-and-truncated-tool-calls.md)：统一适配元数据，截断工具调用回填错误结果而不执行工具。
25. [Claude 上下文截断与工具调用绑定 Spec](docs/specs/019-claude-context-truncation-and-tool-call-binding.md)：补充已知截断值，并拒绝与最近模型消息不一致的工具请求。
26. [模型响应拒绝 Spec](docs/specs/020-model-response-rejection-before-tool-routing.md)与[Lesson 11](docs/lessons/lesson-11.md)：明确错误、无效工具调用在框架路由前拒绝；逐项说明事件、异常与 checkpoint 结果。
27. [两种 unknown 的观测 Spec](docs/specs/021-unknown-model-observation.md)：未知异常的安全类型/状态码，以及未知模型结束原因的字段来源与工具调用数量。
28. [同步 Steering Spec](docs/specs/022-synchronous-steering.md)：活跃运行消息队列、模型调用前注入、自然结束续跑及失败清理。
29. [同步 Follow-up Spec](docs/specs/023-synchronous-follow-up.md)：自然结束时逐条续跑、Steering 优先级、关窗与失败清理。
30. [当前 turn 后优雅停止 Spec](docs/specs/024-stop-after-current-turn.md)与[Lesson 14](docs/lessons/lesson-14.md)：同步运行在安全边界停止，区分 `run_stopped` 与失败及自然完成。
31. [交互式 CLI 消息 Spec](docs/specs/025-interactive-cli-messages.md)：运行中普通输入作为 Steering、`:follow` 排队，并展示本次新增的模型文本。
32. [CLI 优雅停止 Spec](docs/specs/026-cli-graceful-stop.md)：`:stop` 复用应用层停止信号，并区分请求已接受、运行已停止与失败。
33. [工作区只读文件工具 Spec](docs/specs/027-workspace-read-tool.md)：显式工作区、严格参数、受限文件读取及 CLI 接线。
34. [Read 分页与权限实施顺序 Spec](docs/specs/028-read-file-pagination.md)：大文件按行续读、输出预算，以及工作区与审批的后续切片顺序。
35. [会话工作区 Read 边界 Spec](docs/specs/029-session-workspace-read-boundary.md)：同一 Agent 的不同会话按可信运行上下文读取各自工作区。
36. [Read 权限与审批 Spec](docs/specs/030-read-permission-and-approval.md)：工作区外普通文件按调用中断，CLI 批准或拒绝后在同一 checkpoint 恢复并重新校验路径。
37. [Read 文本加固 Spec](docs/specs/031-read-text-hardening.md)：严格 UTF-8、非文本控制字节、安全错误类别及普通源码点文件规则。
38. [工具错误回流 Spec](docs/specs/032-tool-failure-feedback-boundary.md)：Read 的可行动安全错误与模型下一轮回填验证，以及其他工具和后处理的边界。
39. [工作区目录 ls Spec](docs/specs/033-ls-tool.md)：Pi 风格目录列名、工作区外审批、说明性工具错误及本地验证。
40. [find 与 grep 搜索工具 Spec](docs/specs/034-find-and-grep-tools.md)：`.gitignore` 文件发现、Read 正文权限、审批边界与真实模型链路探针。
41. [工具批次调度 Spec](docs/specs/037-conditional-tool-batch-scheduling.md)：模型同轮多工具中任一顺序工具触发整批按调用顺序执行。
42. [工作区 write 工具 Spec](docs/specs/038-workspace-write-tool-design.md)：整文件写入、权限/冲突/未知结果边界与本地验证。
43. [工作区 edit 工具 Spec](docs/specs/039-workspace-edit-tool.md)：现有文本文件的精确多处替换、快照冲突和本地验证。

后续进度以 `docs/handoff.md` 为入口；未完成事项维护在 `docs/future-work.md`；总体决策更新 `docs/project-plan.md`；每个复杂开发单元开始前再写对应 Spec。

## 当前状态

最新状态（2026-10-09）：最小本地 Web 对话已接通现有异步应用、领域事件和 SQLite/checkpoint。下列段落保留早期机制记录，能力与限制以文末 Web 入口、当前交接和对应阶段 Spec 为准；早期“尚无 Web/持久会话”不是当前状态。

已准备本地 Git 仓库、协作规则、交接文档及 Python 3.12 虚拟环境，安装 LangChain、OpenAI/Anthropic/Ollama 模型适配包和 pytest。依赖检查、最小真实模型响应及一次带 middleware 观察的 create_agent 工具循环通过。正式运行层已实现按可信 `session_id` 在每次模型调用前选模，并由确定性测试验证一次运行内 A→工具→B、会话隔离和缺失 ID 的拒绝。模型接入层现可从 `.env/models.json` 加载配置并构造四类适配对象，配置驱动的 HeiyuCode 真实工具循环通过；OpenAI 官方、Claude、Ollama 尚未真实调用。首个测试策略模块用假模型从提供的 `discount_system` 公开契约文本提交结构化用例。尚无真实模型业务用例、HTTP 执行、Web、持久会话、完整轨迹系统或业务评测；也没有独立的应用构建目标。

首个 HTTP 场景已选 `discount_system` 的折扣规则优先级冲突，公开契约和 SUT 源码已核对；结构化用例设计已用假模型通过 `create_agent` 验证。模型接入代码现归入 `model_access/` 包，并用本地配置可选地指定单模型默认 thinking 等级；确定性测试验证了 Pi 式回退和适配器参数接线，真实供应商执行等级尚未验证。模型请求未配置策略时使用 60 秒 HTTP 超时，在线 SDK 最多重试 2 次；本机 HTTP mock 验证了可重试 503 的尝试次数，Ollama 重试尚未实现。运行层现归入 `agent_runtime/` 包，假模型验证同进程跨 `invoke` 的历史延续与选模；同步运行入口已把 run 开始/终局、模型和工具边界按 run ID 关联，并在 run 事件中记录会话 ID。`application.SessionService` 已用假模型验证进程内会话映射、切换和同会话 busy 规则；`build_local_application` 现将一份模型目录、该服务、一个 Agent 和一个内存 checkpointer 配对，经新入口的假模型测试验证两轮历史、切换、隔离与事件。最小本地 CLI 已调用同一应用对象，真实模型合成文本验证了同一进程两轮输入；旧诊断入口尚未统一接入。当前 CLI 通过输入线程、运行 worker 和专属 asyncio loop 调用异步应用，接受 Steering/Follow-up 和活动停止；持久化与可消费事件已接入，Web、身份授权和业务评测仍待后续。选模 middleware 的核心语法与调用已讲解；用户对新增 thinking、请求策略及工具事件代码的独立实现和面试追问能力尚未验证。

模型接入层现把已知供应商结束字段归一为项目 `naiyue_stop_reason`，包括将 Claude 的 `model_context_window_exceeded` 归为截断。运行层对明确截断的工具调用，以及与最近模型消息不一致的工具请求，回填错误 `ToolMessage`，不执行实际工具函数；本地假模型验证了截断结果回填，直接 hook 注入验证了不一致请求拒绝。真实网关只观察过正常结束字段，真实截断、缺失元数据策略、普通文本截断终局和异步路径尚未验证。CLI 选择工作区时已注册 `read/ls/find/grep/write/edit`；`write` 仅允许工作区内整文件创建/覆盖，`edit` 仅允许对工作区内现有 UTF-8 文件做精确替换，真实供应商调用尚未验证。

同步模型 middleware 现对已适配的 `AIMessage` 做进入工具路由前的校验：明确 `error`、任何 `invalid_tool_calls`、声明 `tool_use` 却没有有效调用，以及无 AI 消息，均抛出固定原因码的 `ModelResponseRejected`。本地假模型经应用入口验证不执行工具、记录 `run_failed` 而无 `run_completed`，CLI 只展示安全异常类型；真实供应商失败响应尚未验证。`unknown`、普通文本截断、用户停止及异步路径仍待单独设计。

两类 `unknown` 已有最小结构化日志观测：未分类模型异常额外记录安全的异常类型及可信 SDK HTTP 状态；未识别的模型结束原因单独记录来源字段、受限原生枚举码和有效/无效工具调用数量。假模型经应用入口验证接线与脱敏；当前 `unknown` 工具放行规则未改变，日志不是持久化评测仓库，也没有真实未知供应商样本。

本地应用现支持同步运行中的 Steering 与 Follow-up：其他线程可对同一活跃会话调用 `app.steer(session_id, text)` 或 `app.follow_up(session_id, text)` 入队。Steering 在下一次模型调用前加入上下文；Follow-up 只在原本要自然结束时按 FIFO 每次取一条续跑，且同一终局点 Steering 优先。假模型测试验证了工具结果后的顺序、同一 run 续跑、关窗拒绝、失败清理和递归上限失败。

统一停止接口现为 `app.request_stop(session_id, run_id=可选目标ID)`，旧 `request_stop_after_turn` 已迁移移除。原生异步模型、摘要及 SDK 重试等待可取消；已开始的普通工具等待收尾，排队工具回填带原因的未执行结果。Bash 终止进程组后保留部分输出，副作用不确定仍失败并要求核对。正常停止在 checkpoint/元数据保存确认后报告 `stopped/active`；保存失败、真实工具异常和外部 Task.cancel 不改成正常停止。同步库模型的线程回退不能强杀，因此同步模型只在协作边界停止，正式 CLI 使用异步入口。

## 应用运行事件

同步应用已按 [Spec 062](docs/specs/062-application-run-event-contract.md)接入 `app.subscribe_events(handler)` 的 run 开始/清理终局；[Spec 063](docs/specs/063-model-tool-event-integration.md)接模型/工具，[Spec 064](docs/specs/064-complete-message-observation.md)接完整消息/修订/上下文移除，[Spec 065](docs/specs/065-turn-lifecycle.md)接 turn/审批延续，[Spec 066](docs/specs/066-control-request-events.md)接控制请求接受、开始交付、状态交付或生效、丢弃/不确定及审批请求。共用 session/run 与序号，默认日志不含正文、目标/工具参数；完整内容只给受信任本地订阅者。状态可读、HITL 处理/停止生效与 checkpoint 持久确认分别定义；原结果、interrupt、异常及权限保持。全量 697 例与有界真实 gpt-5.5 Steering/工具/消息/turn 配对通过。CLI 消费迁移、热订阅、活动取消、正式异步及持久恢复仍未接入。会话服务在 `application/session_service.py`，控制方法按 500 行限制移至内部 `application/session_controls.py`；原公开导入和签名保持。

[Spec 067](docs/specs/067-context-operation-events.md)随后接入压缩/明确输入溢出恢复的开始与结束四 type，当前总注册26种；status/trigger/stage表达不同结果，预算估算/切点仍是诊断日志。结束成功须状态替换可读、恢复还须重试响应过门禁，不代表持久保存或整个 run 成功；算法/策略保持。全量最新723例、语法编译/依赖检查通过；前三次真实 compressed 失败后先停止，用户随后授权独立诊断。本轮安全 HTTP 观测及原标准探针均通过真实压缩状态确认/四事实4/4；标准压缩组42.026秒、对照3.656秒，历史间歇性超时未复现，根因仍未知。详见[摘要耗时诊断](docs/research/summary-request-latency-2026-10-08.md)。CLI消费已按下述Spec068接入，持久化与正式异步仍待后续。

## Skill 索引与按需读取

选择工作区且注册会话 `read` 后，应用在每个 run 发现 `.agents/skills/<目录>/SKILL.md` 和 `.claude/skills/<目录>/SKILL.md`。有效条目的 `name`、`description`、规范文件位置组成两组提示词索引；有可用 agents 条目时默认 agents，否则 claude。用户可以通过自然语言点名 `.claude/skills` 来源，同名 Skill 保留各自来源。这个选择是模型提示词约定，不是确定性的自然语言路由器。

Skill 文件使用标准 YAML frontmatter；首片解析限 64 KiB，名称最多 64 字符，描述最多 1024 字符。正文初始不进入模型上下文，需要时模型调用通用 `read`，引用资源也按需读取。应用注册对应 Skill 目录的只读范围，普通与审批恢复都传入 Runtime.context；模型不能通过工具参数修改该范围。其他隐藏/敏感目标、外部链接和未注册资源仍受已有规则约束；加载 Skill 不授权 write/edit/bash 或自动执行脚本。索引在一个 run 内固定，正文读当前文件版本；新 run 刷新索引，压缩移除正文后可再次读取。

仅扫描两个项目来源的下一级目录，不自动扫描用户级/共享目录。跨工作区链接会诊断并排除，当前 `.agents/skills/rndflow` 链接不属于运行 Agent 可加载的首片范围；此限制不修改链接。没有可用 Skill 或没有会话 Read 时不添加空清单。格式错误与冲突有安全诊断，读取失败以说明性 ToolMessage 回流。设计与证据见 [Spec 060](docs/specs/060-project-skills-index-and-read.md)。

有界真实探针使用临时工作区，核对默认与点名备用来源的实际正文/引用读取及工具结果配对：

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m scripts.probe_skill_loading --config .env/models.json --total-timeout-seconds 120
```

2026-10-07 本机一次探针通过，默认 agents 和点名 claude 各有两次成功 Read；这证明受控任务中当前配置网关的接线可用，不代表任意 Skill/供应商的业务效果、完整可信工作区或沙箱隔离。

## 本地开发环境

当前使用 Python 3.12.14；直接依赖为 langchain 1.4.2、langchain-openai 1.6.6、langchain-anthropic 1.7.5、langchain-ollama 1.1.0 和 pytest 9.1.1。requirements.txt 保存本次 macOS arm64 / Python 3.12 环境的完整依赖版本快照，其他平台尚未验证。

在项目根目录激活已创建的环境：

```bash
source .venv/bin/activate
python --version
python -m pip check
```

预期输出分别为 `Python 3.12.14` 和 `No broken requirements found.`。

新环境可通过以下命令安装同一版本清单：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

接口导入检查（不调用模型）：

```bash
.venv/bin/python -c 'from langchain.agents import create_agent; from langchain.agents.middleware import AgentMiddleware, wrap_model_call, wrap_tool_call; from langchain.messages import AIMessage, HumanMessage, ToolMessage; from langchain.tools import tool; print("Agent imports: PASS")'
```

环境与导入检查不代表 Agent 功能测试通过。依赖安装不会自动完成模型配置或项目轨迹接线。

## 最小本地 CLI

已有私有模型配置时，在项目根目录运行：

```bash
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json
```

需要让模型读取工作区内文本文件时，显式附加 `--workspace /绝对路径/项目目录`：

```bash
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json --workspace /绝对路径/项目目录
```

单用户本地开发时，如需让模型提出并运行 Bash 命令，额外传 `--host-bash`：

```bash
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json --workspace /绝对路径/项目目录 --host-bash
```

此开关默认关闭，且每条有效 Bash 命令都会在执行前暂停，终端展示完整命令与工作区；输入 `:approve` 或 `:deny`，多项调用用 `:review ...`。无效工具参数不进入审批，错误以原调用 ID 回填给模型。批准的命令以工作区为当前目录，但运行在宿主机上，**可以读取/修改当前用户有权限访问的工作区外文件，也可以访问网络**。审批是人的授权，不是沙箱；启动时会再次提示。子进程不继承模型 API key 等环境变量，仍可主动读取宿主机文件；因此不要在不可信工作区、无人值守或共享服务中启用。单次默认 60 秒、最多 300 秒，stdout/stderr 分别最多回填末尾 4096 原始字节。超过这个窗口时，工具尝试将两条流的原始输出分别保存在工作区的 `naiyue-agent-output/`，在结果中返回相对路径；普通 UTF-8 多行日志可用 `read` 分页回看，无须额外审批。目录只在需要归档时创建，内含 Git 忽略规则，目录权限 0700、文件 0600。单命令默认最多保存 16 MiB、当前进程单工作区默认最多 128 MiB，分别可通过 `NAIYUE_BASH_OUTPUT_COMMAND_BYTES`、`NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES` 设置正整数字节上限。超限或写入失败会明确标记 `archive_complete=false`，命令输出仍会持续排空并保留末尾；这些文件不是不可篡改审计记录。超时或输出管道不完整时，会话要求人工核对，CLI 展示已有路径及有界部分输出，不能据此判断命令是否完成或副作用是否发生，也不会自动让模型继续运行。二进制、非 UTF-8 和超长单行输出尚不能由当前 `read` 完整消费；隔离执行模式仍待 [Spec 041](docs/specs/041-bash-execution-feasibility.md) 验证。

输出文件不会自动过期或删除。运行结束后输入 `:outputs clear` 查看当前工作区将清理的文件数和字节数，再输入 `:outputs clear confirm` 才执行；运行中或目录包含陌生文件时会拒绝。清理只针对应用创建的归档文件，不删除目录或用户文件；日志可能包含敏感测试数据，选择工作区和保留时间时须留意。当前清理与容量协调只在一个 CLI 进程内验证，不提供跨进程或 SaaS 隔离保证。详见 [Spec 045](docs/specs/045-workspace-bash-output-artifacts.md)。

提供工作区后 CLI 才注册 `read`，并把规范化根目录存进当前应用会话；工具执行时通过可信运行上下文取得该会话的根目录。未提供时保持纯聊天。工作区根不能是隐藏目录，也不能位于 `.env`、`.ssh` 或 `.git` 内。工作区内普通 UTF-8 文本可直接读取，包括普通 `.gitignore` 文件和 `.github` 目录下的非敏感文件；工作区外、未命中敏感规则的普通文件先暂停，在终端展示规范化目标，逐个等候人工决定。输入 `:approve` 批准唯一待审调用，`:deny` 拒绝它；同时有多项待审时输入 `:review approve reject ...`，按显示顺序为每项给出决定。决定数量错误时不恢复。新普通输入在待审期间被拒绝，`:quit` 会提示内存审批状态无法在重启后恢复。其他隐藏路径、工作区外任何隐藏路径、`.pem`/`.key`、路径遍历和非普通文件直接拒绝；批准后工具还会重新解析路径，并要求目标与本次调用的一次性授权一致。不会执行写入。当前这是同进程内存 checkpoint 的本地审批，尚无身份授权、持久审批或 OS 级沙箱保证。

不超过 64 KiB 且不超过 2000 行时，`read` 直接返回原文；超出任一预算便按行返回一页，并用 `next_offset` 提示下一次读取的行号。64 KiB 只限制**文件正文**，提示文字另计，因此整个工具结果可略大于 64 KiB。模型也可显式传 `offset`（从 1 开始）和 `limit`（1–2000 行）。返回的正文页严格按 UTF-8 解码；参数、路径/权限、审批、编码、非文本、长行、I/O 与无工作区错误均回填带原因和下一步指引的安全 `ToolMessage`，不包含路径、原始异常或文件内容。路径不可用时为避免泄露敏感文件存在性，只列出“可能缺失、非普通文件或被工作区策略阻止”的原因范围。只读 Read 中未知普通异常也回填安全错误；LangGraph 中断仍上抛。文件在两页之间变化时，行号不代表同一个快照。工作区隔离、审批暂停/恢复与 CLI 命令通过假模型确定性测试；当前网关的受控七工具任务和双来源 Skill 正文/引用任务已观察到真实模型调用本地 Read 的正常路径。拒绝与故障路径仍主要依据确定性测试，不能扩大为任意供应商保证。图片 Read 与其他文本编码 Read 是后续独立能力，不由 RAG 模块替代。

启动后直接输入文字并回车；同一进程内的输入属于同一会话，历史由内存 checkpoint 保存。在交互式终端中，模型仍在运行时直接输入文字会作为 Steering 排队，输入 `:follow <文字>` 会作为 Follow-up 排队；界面显示“已排队”只代表应用接受，模型回复会在这次运行结束后按顺序显示。输入 `:stop` 请求活动停止；“已请求”只表示登记，关键操作收尾及保存确认后才显示“已停止”。停止时未消费的 Steering/Follow-up 和此后输入保留为待发送内容，`:pending` 查看，`:send` 明确重新提交；收尾期间拒绝启动新 run，准入失败不清空待发送内容。输入 `:model <model_id>` 切换下一次模型调用的选型，输入 `:quit` 或发送 EOF 会等待当前运行结束后退出。配置路径须显式提供；可用模型 ID 由该配置决定。通过管道发送多行时仍逐轮运行，不自动变成 Steering。选择工作区后模型可使用 `write` 创建或整文件覆盖该目录内的 UTF-8 文件，也可使用 `edit` 对现有 UTF-8 文件做精确多处替换；`edit` 用分块读取与临时文件写回，不设固定文件大小上限，但单次提交的替换文本仍受输入预算约束，处理还受实际磁盘空间与运行时间影响。此入口尚不提供工作区外写入审批、专用测试执行策略、业务提示词或逐 token 打印；默认持久模式可以恢复安全完成及安全停止的会话。工作线程刚启动尚未开窗或已进入收尾时，应用可能拒绝 `:stop`，CLI 会明确提示未接受。

## 真实模型接入诊断

本地模型配置为 Git 忽略的 `.env/models.json`，格式见 [Spec 004](docs/specs/004-local-model-config-format.md)。当前实际文件只配置已有凭证的 HeiyuCode 网关；创建时权限为 `0600`。`naiyue_test_agent.model_access.load_model_catalog` 读取并校验配置，`naiyue_test_agent.agent_runtime.build_configured_runtime_agent` 将它接到现有按会话选模的 middleware。下面的命令会调用真实模型，发送的只有随机诊断值和工具提示；最多两次模型调用，90 秒截止，不输出密钥或诊断值：

```bash
.venv/bin/python -m scripts.probe_configured_tool_loop --model-id heiyu-gpt-5.5
```

2026-09-29 实际输出：`status=PASS model_calls=2 tool_executions=1 final_matches_tool_result=true`。Claude 和 Ollama 的对象构造及假模型接线测试已通过，但没有相应凭证或本地推理服务的真实运行证据。该本地明文凭证文件不作为未来服务端的密钥管理方案。

用户当前将凭证保存在 `.env/passwd.md`（`.env` 是目录），格式为一行 `OPENAI_API_KEY=...`；该路径已被 Git 忽略。脚本仅读取指定文件，不修改凭证，也不打印密钥。不对来源未知的文件执行 shell source。

```bash
.venv/bin/python scripts/probe_tool_loop.py \
  --key-file .env/passwd.md \
  --base-url https://www.heiyucode.com \
  --model gpt-5.5
```

这是会发起真实付费 API 请求的诊断命令。脚本限制最多两次模型调用，SDK 重试为 0，每个请求超时默认 45 秒；仅发送诊断提示及随机值，关闭本次进程的托管 tracing。使用 Responses API、流式接收和 store=False；不据此宣称第三方服务没有其他留存。

2026-09-28 实际结果：`status=PASS`、`model_calls=2`、`tool_executions=1`、`tool_result_in_second_input=true`、`final_matches_tool_result=true`。两次模型调用报告的 token 用量合计 216，耗时 8.94 秒。响应元数据均报告 gpt-5.5，不能独立证明网关后端模型身份。

诊断断言检查真实执行次数、tool_call_id 配对、第二轮 middleware 输入中存在实际工具结果，以及最终回答精确匹配该结果。它证明这一次基本工具链路可用，不代表业务用例质量、完整异常恢复、持久轨迹或生产能力已验证。

## 参考项目

- 原测试平台与待测示例：`/Users/zmw/AIAutoTest`。
- Pi 源码：`/Users/zmw/naiyue-personal-workspace/pi`。
- AgentX 源码：`/Users/zmw/AgentX`。

以上是当前机器的参考位置，不应硬编码到生产实现。新项目采用独立目录，不默认复制旧平台、修改待测源码或替换现有 Pi。

[Spec 068](docs/specs/068-cli-event-display.md)已将CLI接入同一事件接口：完整助手消息及时显示，修订明确标记，消息/结果与控制回执去重；审批列表保留目标/命令，四种终局只显示一次，观察缺失时用原结果/异常兜底。工具/上下文显示只表达已有事实，Bash副作用未知的部分输出与人工核对提示保持。原同步线程与权限策略不改；有界真实CLI运行入口/展示器探针验证两条回答、纯工具、Steering及唯一终局。输入光标重绘、逐token、原生异步及生产压力仍未实现或验证；最小持久会话现按Spec070接入。


## 本地持久会话（Spec 070）

CLI默认创建持久新会话，显示会话ID；默认数据库为`~/.naiyue-test-agent/sessions.sqlite`，可用`--session-db`覆盖。应用元数据和官方SqliteSaver分别负责会话信息/图状态，现有`build_local_application`仍是内存入口。只恢复已安全结束且记录与checkpoint一致的会话，选模/workspace从元数据恢复；相同session/thread、新run ID。完整行为与限制见[Spec070](docs/specs/070-persistent-session-implementation.md)。

在项目根目录执行：

```bash
# 默认保存新会话，启用本工作区文件工具
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json --workspace .

# 显式临时会话，同进程仍支持多轮，退出后不可恢复
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json --workspace . --no-session

# 列出所选数据库历史会话；交互终端输入编号/ID选择，空行取消
.venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json --resume
```

直接恢复使用`--resume <session_id>`，替换为CLI显示的真实ID；恢复会沿用保存的工作区，另给不一致的`--workspace`会失败。非交互的无IDresume只列索引并退出，不消耗业务输入。临时模式不打开会话DB，工具业务文件按原规则保存；临时与resume/显式DB路径互斥。Bash仍需本次显式`--host-bash`并走审批，不因恢复自动开启。

本地确定性全量`781 passed`，两个独立离线进程和一次有界真实默认模型探针验证历史延续；真实探针可运行`.venv/bin/python -m scripts.probe_persistent_session --real`，最多两逻辑请求、95秒总时限，临时DB/合成事实，只输出验收统计。证明机制接线，不代表业务评测或生产规模。

当前macOS/Linux、本地轻量单CLI/单活动run，第二个同库入口会拒绝；默认目录和DB/lock/WAL/SHM私有权限已验证，不是沙箱。待审批、失败、未确认结束或副作用待核对的会话重启只识别/阻断，不能自动续跑；同进程审批保持。全历史浏览/分支、重启高级恢复、长期备份/清理/迁移、多用户/原生异步/活动取消分别在后续切片实现。

[Spec072](docs/specs/072-read-permission-extension-seam.md)已给会话Read增加权限扩展接口：可信调用方可通过`attach_file_permission_policy`注入同步allow/ask/deny策略，审批判断、带会话/资源上下文的review和执行前复核共用规则；无策略时沿用当前默认行为。策略异常回填安全工具错误，敏感/无效目标仍拒绝。17个新增测试与全量798例通过；仅Read接入，没有新增CLI权限模式，其他工具、成熟权限服务、原生异步及Web另片推进。

[Spec073](docs/specs/073-async-model-call-middleware.md)已接通无工具runtime的原生异步模型调用：选模/结束原因/响应门禁/用量事件和请求观测共用同步规则，`ainvoke`等待模型后返回完整结果，InMemorySaver历史可用。新增14例、全量812例通过；`.venv/bin/python -m scripts.probe_async_model`用现有默认模型验证一次真实请求、回答、模型事件配对与内存checkpoint（会发送随机合成文本，默认95秒父截止）。应用/CLI、摘要恢复、工具、SQLite持久化和token增量仍待适配；模型Task取消传播测试不代表用户活动停止或服务端取消保证。

[Spec074](docs/specs/074-async-context-model-chain.md)随后接通项目prompt、主动摘要及一次明确输入溢出恢复的异步模型IO，同步/异步共用原压缩算法。34例新增/全量846例通过，取消摘要和重发的确定性测试保留原信号；`ainvoke`完整结果及`astream`的values/updates状态替换/内存checkpoint已验证。真实上下文探针`.venv/bin/python -m scripts.probe_async_model --with-context --timeout-seconds 120`通过摘要+回答两逻辑请求和随机合成事实，125秒父截止，只调整探针内存预算，不读取用户文件/持久库或修改配置。仍无token增量、异步工具/审批/应用/SQLite或真实活动取消证明；CPU估算和事件订阅仍同步，框架内部子任务取消限制见待办。

## 正式异步应用与事件流

[Spec075](docs/specs/075-integrated-async-application.md)已合并接通工具、应用、SQLite及正式流式消费；新36例、全量882例通过。这是Spec075时点的异步基础；CLI接线和活动停止已由下述Spec076完成。

- `build_async_local_application`创建内存应用；`open_async_persistent_application`异步上下文管理器拥有SQLite/saver和SDK资源，参数`database_path`明确指定数据库。
- `acreate_session` / `acreate_workspace_session`、`aselect_model`、`arun` / `aresume_tool_approval`；同会话busy/待审批/人工核对保持，`aget_messages`只返回当前checkpoint的深拷贝消息。
- `astream_run` / `astream_tool_approval`消费领域事件，文本`message_delta`永远临时；完整snapshot按消息ID替换草稿，终局在关键清理/保存确认之后。`StreamingMessageProjection`提供展示状态，不让runtime依赖界面。
- `steer`、`follow_up`、`request_stop`使用run私有控制；并行批次遵循`max_concurrency`，异步缺省4，任一sequential整批原顺序。普通同步工具由框架在线程运行，Bash仍是宿主机线程内进程/逐命令审批，Spec076已接进程组停止，不能称为沙箱。
- `capacity`默认256限制交付队列的事件数，溢出抛`EventStreamOverflow`要求重新同步；关闭消费不停止生产，应用持有后台任务，`aclose`排空后关闭其资源。复杂CPU计算/旧同步订阅者仍须快速，大payload/SaaS压力和完整历史恢复另验收。

下面的内存示例会使用已有模型配置发出真实请求，只打印事件类型：

```python
import asyncio
from naiyue_test_agent.application.async_application import build_async_local_application

async def main():
    app = await build_async_local_application(
        '.env/models.json', tools=[], system_prompt='', response_format=None,
    )
    try:
        session = await app.acreate_session(app.default_model_id)
        async for event in app.astream_run(session, [('user', '请简短打个招呼')]):
            print(event.event_type)
    finally:
        await app.aclose()

asyncio.run(main())
```

可重复验证：`.venv/bin/python -m scripts.probe_async_application --offline`执行两个独立离线进程与临时真实SQLite；去掉`--offline`使用默认模型，在180秒总截止内最多首进程2次、恢复1次逻辑调用，验证增量、纯随机工具、Steering、保存确认和旧上下文。真实输出为`async_application_probe=PASS mode=real two_processes=True delta=verified tool=1 steering=verified terminal_after_save=verified history_used=True session_id=stable thread_id=stable run_id=new checkpoint=advanced`。不访问用户文件/业务系统、不修改模型配置，也不统计SDK重试尝试或实际费用。Web是下一阶段；重启审批/failed等仍识别并阻断。


## 统一活动停止与 CLI 异步接线

[Spec076](docs/specs/076-active-stop.md)沿用Pi的run互斥、停止信号、操作收尾和消息保留原则，结合本项目checkpoint安全恢复检查。CLI仍使用普通行输入和完整回答打印，薄桥在专属loop调用AsyncApplication/AsyncSqliteSaver；等待模型时输入线程保持响应，并非所有文件IO已改成原生异步。停止后进入待发送模式，`:send`显式提交保留内容；空队列时`:send`退出待发送模式。待发送内容只在内存，不在退出后恢复。

本片新增23例，全量905例通过；确定性验证覆盖模型/摘要/SDK重试、串行及并发等待、原子写入、审批准备竞态、Bash实际进程、失败优先、部分文本未确认、同进程/两个独立进程恢复和CLI显式重发。真实默认网关探针（一次逻辑调用；SDK HTTP重试不计入该次数）只发送合成请求：

```bash
.venv/bin/python -m scripts.probe_active_stop --timeout-seconds 120
```

实际输出：`active_stop_probe=PASS mode=real logical_calls=1 delta=observed stopped=verified fake_answer=absent checkpoint=safe`。加`--offline`可离线重复验证。这个结果证明客户端停止等待和安全结束，不证明供应商服务器停止计算/计费。任意不响应信号的自定义工具没有固定秒数停止保证；关键写入/保存仍须settle，Bash副作用未知时不会自动恢复。最小Web与整体学习为下一步。

## 最小本地 Web 对话（Spec077）

React+TypeScript/Vite 页面参考已确认的 ChatKit 浅色聊天外观；FastAPI 同源提供构建文件、REST 动作与 SSE，直接复用现有异步应用及 SQLite/checkpoint。当前支持配置内模型选择、持久会话列表/安全恢复、完整消息与文本增量、运行中 Steering/Follow-up、活动停止和明确重发。Spec077首片仅聊天；[Spec078](docs/specs/078-web-tools-and-approval.md)随后接通工作区文件工具、可折叠工具详情和逐项决定/整批提交审批。

本机已安装并构建。新环境先安装原 Python requirements，再执行 `.venv/bin/python -m pip install -r requirements-web.txt`、`npm --prefix frontend ci`、`npm --prefix frontend run build`。启动：

```bash
.venv/bin/python -m naiyue_test_agent.web --config .env/models.json
```

打开终端打印的本地访问链接，默认随机可用端口和本地访问凭据；运行只需 Python 服务。可选 `--workspace /绝对路径`、`--session-db /绝对路径/sessions.sqlite`、`--port 端口`。同一个数据库只允许一个应用进程使用。服务启动不编辑 `.env`，模型 key 不发送到浏览器。

启用现有文件工具：

```bash
.venv/bin/python -m naiyue_test_agent.web --config .env/models.json --workspace /绝对工作区路径
```

工作区模式注册 `read/ls/find/grep/write/edit`，原权限、错误回填和串并行策略继续生效。工作区外可审批的操作会先暂停，在页面核对每项批准/拒绝后提交整批；未选择时不会默认批准。Bash 需额外显式 `--host-bash`，每次命令仍审批，它是宿主机命令而非沙箱。无工作区继续聊天；重启后的待审批会话仍阻断自动恢复。

工具实现/HTTP契约/验证见 [Web工具说明](docs/web-tools.md)。离线真实 TCP/审批与两进程恢复验收运行 `.venv/bin/python -m scripts.probe_web_tools`，不产生收费请求。

运行中普通消息为 Steering，勾选“结束后跟进”即 Follow-up。停止期间输入继续可编辑，未执行内容保留，明确重新发送后才启动新轮。断开 SSE 不取消生产，恢复 view/序号用于同步，完成状态依据保存后的领域终局。浏览器草稿和部分待发送内容只在内存；安全会话恢复不等于完整历史归档或 SaaS。

使用、代码地图、实际验证与限制见 [Web 对话说明](docs/web-chat.md) 和 [Spec077](docs/specs/077-local-web-chat.md)。离线 TCP/两进程探针：

```bash
.venv/bin/python -m scripts.probe_web --timeout-seconds 60
```

输出 `web_probe=PASS mode=offline tcp=True sse_delta=True disconnect_keeps_run=True stop=True retained=True two_processes=True history_used=True duplicate_safe=True`。浏览器真实默认模型两次逻辑调用验证了精确回答与活动停止，桌面/375px 视口无横向溢出；这些证据不替代业务评测、工具实网审批或生产负载验证。
