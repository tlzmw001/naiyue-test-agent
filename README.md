# Naiyue Test Agent

当前进度：[Spec109两片](docs/specs/109-web-replay-and-persistent-diagnostics.md#9-109-02实际结果与spec109收尾)已完成Web每根256条/4 MiB重播双预算和schema10持久诊断。关键事件/根摘要分开，根子共享额度，1 MiB单根/30天/256 MiB工作区默认可配置；有限队列、关闭排空、缺口和整组淘汰已接线。新增只读诊断分页API，冷查询不装载图；原聊天/用量/checkpoint及审批恢复保持。后端2817、前端198及211模块构建通过，0次真实供应商请求。下一步先讨论Spec103的GFM消息呈现，再逐片推进其他UI；checkpoint修剪和两种项目重试继续延期，原机/更多供应商/PyPI交付独立。

面向 HTTP/RPC 接口测试的 AI Agent 项目，当前已实现本地模型—工具循环、会话、事件流及统一异步应用链路；RAG 与业务评测按后续切片推进。

项目目录与仓库名：`naiyue-test-agent`。初始化日期：2026-09-28。

当前架构收敛见 [Spec093](docs/specs/093-architecture-semantics-and-sync-cleanup.md)：CLI/Web统一使用`AsyncApplication`，CLI保留终端线程桥；独立同步App及其持久化工厂已删除；093C继续删除历史Discount原型及本项目同步Runtime执行面，低层测试/探针迁至异步。`contracts`保留跨层共享事实，响应拒绝异常归Runtime。已学旧入口与当前函数的对应关系见[学习路线](docs/1.0/learning-roadmap.md)。

[Spec102第一片](docs/specs/102-session-state-lifecycle-refactor.md#16-102-01实际结果与下一片2026-10-18)已将应用侧18字段会话字典迁为SessionState，新建/恢复共用默认值，外部读取、恢复安装和移除通过SessionService受控入口。消息/checkpoint与聊天归档保持独立；SQLite格式和公开应用API保持。[102-02](docs/specs/102-session-state-lifecycle-refactor.md#18-102-02实际结果与下一片2026-10-18)已将运行/审批恢复/保存/历史/重启恢复改为直接async，共享准入、资源准备与失败核对并删除IOCall驱动；阻塞IO和关键写入仍保留取消收尾。[102-03](docs/specs/102-session-state-lifecycle-refactor.md#20-102-03实际结果与下一片2026-10-18)已迁移测试/探针并删除旧configured工厂和选模空回调；模型选择的现有日志/事件保持。[102-04](docs/specs/102-session-state-lifecycle-refactor.md#23-102-04实际结果与下一片2026-10-18)已通过SessionPort集中声明根/子装配需要的会话方法，由同一个SessionService提供；根控制hook限定root，子模型/工具快照与权限/提示词取值沿原机制。[102-05](docs/specs/102-05-content-projection.md#5-实际结果2026-10-18)已统一完整内容/流式文本投影：content_text默认保留文字和有限图片说明，delta模式只取真实文字块；CLI转义和Responses响应ID/增量序号保持，旧重复解析及Web纯别名已删除。[102-06](docs/specs/102-06-web-setting-errors.md#6-实际结果2026-10-18)已将Web三项设置的await/异常处理合并为局部_apply_setting，门禁、具体422/日志、成功确认值及取消传播保持；[102-07](docs/specs/102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)已补真实根/子装配顺序说明与生产链验收，保留当前排序；7个已确认分片全部完成。原问题第7项跨会话并发转104草案，先完成105架构收敛。

当前 Web 侧栏见[Spec095](docs/specs/095-subagent-sidebar.md)：顶部“子智能体”先按名称选择实例，详情不显示名称，直接复用主会话消息流与工具卡片。审批统一留在主会话，侧栏不因审批自动打开；已保存的子记录可只读查看，不恢复或重跑旧子工具。Skills 列表与显式指令见下文。

[Spec096：上下文构成查看](docs/specs/096-context-inspector.md)已接发送键旁入口，按当前模型输入展示本地估算，并单独展示最近请求的供应商用量；只读查看不调用模型或触发压缩。

[Spec097：输入框指令与模型设置](docs/specs/097-composer-commands-model-thinking.md)已接模式左侧加号，当前包含“压缩上下文”和“技能”，不重复其他按钮已有功能，也支持输入`/compact`。压缩需空闲且没有待审批，复用原摘要并保留最近完整一轮，不自动继续运行。模型/推理入口位于上下文和发送键之间，替代顶部选择器；档位来自模型配置，选择按会话保存、下次模型请求生效，切模型后回到配置默认，不改配置文件或子角色默认。

[Spec099：会话管理与排序](docs/specs/099-session-sidebar-management.md)已接左侧会话右键及省略号菜单，只有“重命名、置顶／取消置顶、删除”。鼠标按住会话拖动到同组目标前方或组末，松开保存顺序，刷新及服务重启后保留；置顶与普通会话分别排序，不提供上移/下移按钮。删除需确认，只清理该会话及关联子记录，不删除工作区文件；执行中的会话不可删除。输入框模型按钮直接显示当前模型名和推理强度，桌面/窄屏均不显示指针图标或展开箭头。

会话SQLite当前schema10，自动事务迁移现行工作区库4—9（含thinking_level、名称、置顶、顺序、聊天档案、调用用量及有限持久诊断）；Spec087所述旧全局库1/2/3仍不接受、不自动迁移或删除。

[Spec101：聊天归档、有界查询与滚动历史](docs/specs/101-web-history-pagination.md#14-101-02实施合同本轮授权)已接应用finish与恢复收尾：已保存checkpoint及pending消息整理到同一SQLite的独立聊天表，压缩保留原文，根/子分别存档；消息、进度和收尾元数据在本方事务中确认。Web选中会话显式POST准备已保存历史，普通GET只读档案分页；发送/压缩/审批等使用轻量状态准入，不再受整份聊天JSON大小阻断。最近窗口、签名游标、原消息JSON分块详情及跨页工具父ID已接；101-03已接根/子上滑分页、阅读锚点、失败重试和SSE缺口合并，实时终稿/已加载旧页保持，超大消息明确预览；完整正文展开界面未增加。101-04已完成长历史、自动/手动压缩、服务重开、真实进程退出及根/子审批边界的离线组合验收；恢复查看不重跑旧工具。最终证据见[Spec第21节](docs/specs/101-web-history-pagination.md#21-101-04实际结果与spec101收尾2026-10-18)。

[Spec100：真实供应商有界验收](docs/specs/100-real-provider-acceptance.md)已完成首轮Responses网关验收，实际52/60次发送（B8/C17/D17/E10），16功能场景通过，根多工具批准/双子任务审批2项未通过，见[报告及未闭合问题](docs/research/provider-acceptance-2026-10-17.md)。已修复Responses消息ID归并及摘要text块处理；未改网关配置或生产流式默认。图片来自Pillow，无生图API；临时工作区/SQLite与有限报告分开，未操作用户会话库。`.venv/bin/python -m scripts.probe_provider_acceptance --config <模型配置路径> --model-id <配置内模型ID>`仍只读零请求预检；另有显式`python -m scripts.run_provider_acceptance --live --config ... --model-id ... --report <新路径>`执行器，默认失败停止，不自动重试或重置预算。当前验收已停止，不要为得到全绿无界重跑。

Spec100后续已保留流式补齐Responses最终工具参数：修复前完整事件诊断确认无参数delta，完成事件有完整参数；模型接入层局部归集后仍委托官方适配器。根批次批准、双子智能体独立审批均定向实网通过，新增9次、累计61次；原52次与失败记录保留，见[修复/复验及证据边界](docs/research/responses-final-args-2026-10-17.md)。未改真实配置、权限/图或前端。

## 项目目标

这个项目用于真实测试工作，也用于测试开发、AI 测试相关岗位的简历和面试展示。以生产级工程要求为目标，围绕真实场景验证效果、可靠性、权限边界和可维护性；当前已有本地 Agent 基础链路及验证，尚无已验证的生产业务能力。

用户有三年测试开发经验。代码主要由 AI 助手实现，开发过程同时承担教学任务：用户要逐步能够独立读懂、编写、修改和调试关键代码，并在面试中解释架构、机制、实验结果与取舍。

## 已确认的技术方向

- Python + LangChain `create_agent`，通过自定义 middleware 扩展模型—工具循环。
- 按职责划分模型接入、Agent运行、通用工具和应用入口；测试任务通过提示词/Skills与注册工具完成，不再保留独立`test_agent`策略层。
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
4. [分层命名与实施路线](docs/implementation-roadmap.md)：当前模块职责与调用方向，以及标明版本的早期实施顺序。
5. [AgentX 源码参考](docs/agentx_architecture_reference.md)：2026-09-23 的静态调研记录。
6. [首个运行层 Spec](docs/specs/001-model-selection-boundary.md)与[Lesson 1](docs/lessons/lesson-1.md)：模型调用边界按会话选模的设计、代码地图和理解检查。
7. [首个 HTTP 业务场景 Spec](docs/specs/002-discount-policy-first-scenario.md)：历史`discount_system`契约与证据边界，需求已撤销。
8. [结构化用例设计 Spec](docs/specs/003-discount-case-design.md)与[Lesson 2](docs/lessons/lesson-2.md)：历史结构化用例实验，093C已删除对应实现和测试。
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

搜索需要服务PATH中已安装的`rg`和`fd`（Linux的同程序名`fdfind`也可）；项目不会自动下载或安装。完整额度及其启动快照规则见[当前设置样例](docs/examples/settings.example.toml)。

2026-10-16已完成085-15本机工具复盘，后续按用户决定取消模型/会话文件的私密权限位门槛；Spec087接工作区UUID存储与跨平台锁，Spec088/089继续补Windows文件提交、归档与路径授权比较。ReplaceFileW分支保留目标DACL，普通/扩展盘符及UNC在权限规则和范围判断中等价；设备/ADS/歧义名称作为普通文件工具不支持输入反馈。已有目标属性和操作系统访问错误仍受尊重。最新验收见[handoff](docs/handoff.md)，平台证据见[复盘](docs/research/windows-compatibility-audit.md)。当前只有macOS真实IO及Windows语义/分支mock，没有Windows/Linux原机安装、ACL、UNC/junction或双Shell成功证据；WIN1仍开放。正常串行排队期限、SubAgent086、UI/完整沙箱另排。

2026-10-16工具错误入口按[Spec085-01](docs/specs/085-01-unified-tool-errors.md)迁移：工具用`ToolException`保留具体公开提示，官方`ToolErrorMiddleware`统一生成同调用ID的错误消息；普通文件/自定义工具执行异常也可反馈给模型继续处理，不自动重试。原提示白名单、逐工具`handle_tool_error`及`WriteOutcomeUnknown`已移除；Write/Edit提交未确认明确反馈“结果未知”，不因此自动锁会话。停止、中断、真实调度/必要保存保护保留；01阶段仅接Bash具体错误；该旧边界已在085-12随runner迁移完成。随后按[Spec085-02](docs/specs/085-02-tool-package-boundaries.md)完成目录与依赖迁移：具体工具及现有权限声明位于`naiyue_test_agent/tools/`，调度、工具观察、HITL与错误转换位于`agent_runtime/tool_execution/`；旧路径已移除。Bash停止对象由现有context显式传入；02当时保持执行/审批行为，当前收尾以085-12为准。后端1101与前端27测试、构建通过。注册/设置、动态工具和新权限继续逐片推进，当前已实施范围及下一片入口以上段为准。

2026-10-11最新收尾：Spec083已统一分类器为`(error, source)`，保护分类直接构造描述并保留诊断回退；分支测试与VS Code单步指南已补齐，第二章课堂当前到Lesson43。此次提交前重新验证：后端1060通过、前端9文件27通过，TypeScript/Vite构建、compileall与依赖检查通过。当前工程状态及已记录限制见[交接第323节](docs/handoff.md#323-当前代码提交与推送收尾2026-10-11)，下列Spec082结果为此前阶段快照。

2026-10-11更新：按[Spec082](docs/specs/082-remove-obsolete-runtime-bridges.md)清理22个被替代函数、旧交互装配/自然结束分支及无用导入；运行依赖统一使用RunServices，旧分项bind/getter和failure_data已移除。CLI/Web继续采用正式异步应用；该时点同步应用/持久会话和低层Runtime仍有消费者；093已迁移应用和Runtime消费者，删除独立同步执行链；Discount历史原型已按用户确认移除。最新本机后端1038、前端27及构建通过；下文早期阶段记录保留历史，不作为废弃接口的使用指引。

最新状态（2026-10-09）：最小本地 Web 对话已接通现有异步应用、领域事件和 SQLite/checkpoint。下列段落保留早期机制记录，能力与限制以文末 Web 入口、当前交接和对应阶段 Spec 为准；早期“尚无 Web/持久会话”不是当前状态。

已准备本地 Git 仓库、协作规则、交接文档及 Python 3.12 虚拟环境，安装 LangChain、OpenAI/Anthropic/Ollama 模型适配包和 pytest。依赖检查、最小真实模型响应及一次带 middleware 观察的 create_agent 工具循环通过。正式运行层已实现按可信 `session_id` 在每次模型调用前选模，并由确定性测试验证一次运行内 A→工具→B、会话隔离和缺失 ID 的拒绝。模型接入层现可从 `.env/models.json` 加载配置并构造四类适配对象，配置驱动的 HeiyuCode 真实工具循环通过；OpenAI 官方、Claude、Ollama 尚未真实调用。首个测试策略模块用假模型从提供的 `discount_system` 公开契约文本提交结构化用例。尚无真实模型业务用例、HTTP 执行、Web、持久会话、完整轨迹系统或业务评测；也没有独立的应用构建目标。

首个 HTTP 场景已选 `discount_system` 的折扣规则优先级冲突，公开契约和 SUT 源码已核对；结构化用例设计已用假模型通过 `create_agent` 验证。模型接入代码现归入 `model_access/` 包，并用本地配置可选地指定单模型默认 thinking 等级；确定性测试验证了 Pi 式回退和适配器参数接线，真实供应商执行等级尚未验证。模型请求未配置策略时使用 60 秒 HTTP 超时，在线 SDK 最多重试 2 次；本机 HTTP mock 验证了可重试 503 的尝试次数，Ollama 重试尚未实现。运行层现归入 `agent_runtime/` 包，假模型验证同进程跨 `invoke` 的历史延续与选模；同步运行入口已把 run 开始/终局、模型和工具边界按 run ID 关联，并在 run 事件中记录会话 ID。`application.SessionService` 已用假模型验证进程内会话映射、切换和同会话 busy 规则；历史同步`build_local_application`（已由093撤销，当前用`build_async_local_application`）将一份模型目录、该服务、一个 Agent 和一个内存 checkpointer 配对，经新入口的假模型测试验证两轮历史、切换、隔离与事件。最小本地 CLI 已调用同一应用对象，真实模型合成文本验证了同一进程两轮输入；旧诊断入口尚未统一接入。当前 CLI 通过输入线程、运行 worker 和专属 asyncio loop 调用异步应用，接受 Steering/Follow-up 和活动停止；持久化与可消费事件已接入，Web、身份授权和业务评测仍待后续。选模 middleware 的核心语法与调用已讲解；用户对新增 thinking、请求策略及工具事件代码的独立实现和面试追问能力尚未验证。

模型接入层现把已知供应商结束字段归一为项目 `naiyue_stop_reason`，包括将 Claude 的 `model_context_window_exceeded` 归为截断。运行层对明确截断的工具调用，以及与最近模型消息不一致的工具请求，回填错误 `ToolMessage`，不执行实际工具函数；本地假模型验证了截断结果回填，直接 hook 注入验证了不一致请求拒绝。真实网关只观察过正常结束字段，真实截断、缺失元数据策略、普通文本截断终局和异步路径尚未验证。CLI 选择工作区时已注册 `read/ls/find/grep/write/edit`；`write` 仅允许工作区内整文件创建/覆盖，`edit` 仅允许对工作区内现有 UTF-8 文件做精确替换，真实供应商调用尚未验证。

同步模型 middleware 现对已适配的 `AIMessage` 做进入工具路由前的校验：明确 `error`、任何 `invalid_tool_calls`、声明 `tool_use` 却没有有效调用，以及无 AI 消息，均抛出固定原因码的 `ModelResponseRejected`。本地假模型经应用入口验证不执行工具、记录 `run_failed` 而无 `run_completed`，CLI 只展示安全异常类型；真实供应商失败响应尚未验证。`unknown`、普通文本截断、用户停止及异步路径仍待单独设计。

两类 `unknown` 已有最小结构化日志观测：未分类模型异常额外记录安全的异常类型及可信 SDK HTTP 状态；未识别的模型结束原因单独记录来源字段、受限原生枚举码和有效/无效工具调用数量。假模型经应用入口验证接线与脱敏；当前 `unknown` 工具放行规则未改变，日志不是持久化评测仓库，也没有真实未知供应商样本。

本地应用现支持同步运行中的 Steering 与 Follow-up：其他线程可对同一活跃会话调用 `app.steer(session_id, text)` 或 `app.follow_up(session_id, text)` 入队。Steering 在下一次模型调用前加入上下文；Follow-up 只在原本要自然结束时按 FIFO 每次取一条续跑，且同一终局点 Steering 优先。假模型测试验证了工具结果后的顺序、同一 run 续跑、关窗拒绝、失败清理和递归上限失败。

统一停止接口现为 `app.request_stop(session_id, run_id=可选目标ID)`，旧 `request_stop_after_turn` 已迁移移除。原生异步模型、摘要及 SDK 重试等待可取消；已开始的普通工具等待收尾，排队工具回填带原因的未执行结果。Bash经共享runner终止并回收后保留部分输出，以停止工具结果配对；仅必要收尾未确认时上抛ProcessCleanupError并要求人工核对。正常停止在 checkpoint/元数据保存确认后报告 `stopped/active`；保存失败、真实工具异常和外部 Task.cancel 不改成正常停止。同步库模型的线程回退不能强杀，因此同步模型只在协作边界停止，正式 CLI 使用异步入口。

## 应用运行事件

同步应用已按 [Spec 062](docs/specs/062-application-run-event-contract.md)接入 `app.subscribe_events(handler)` 的 run 开始/清理终局；[Spec 063](docs/specs/063-model-tool-event-integration.md)接模型/工具，[Spec 064](docs/specs/064-complete-message-observation.md)接完整消息/修订/上下文移除，[Spec 065](docs/specs/065-turn-lifecycle.md)接 turn/审批延续，[Spec 066](docs/specs/066-control-request-events.md)接控制请求接受、开始交付、状态交付或生效、丢弃/不确定及审批请求。共用 session/run 与序号，默认日志不含正文、目标/工具参数；完整内容只给受信任本地订阅者。状态可读、HITL 处理/停止生效与 checkpoint 持久确认分别定义；原结果、interrupt、异常及权限保持。全量 697 例与有界真实 gpt-5.5 Steering/工具/消息/turn 配对通过。CLI 消费迁移、热订阅、活动取消、正式异步及持久恢复仍未接入。会话服务在 `application/session_service.py`，控制方法按 500 行限制移至内部 `application/session_controls.py`；原公开导入和签名保持。

[Spec 067](docs/specs/067-context-operation-events.md)随后接入压缩/明确输入溢出恢复的开始与结束四 type，当前总注册26种；status/trigger/stage表达不同结果，预算估算/切点仍是诊断日志。结束成功须状态替换可读、恢复还须重试响应过门禁，不代表持久保存或整个 run 成功；算法/策略保持。全量最新723例、语法编译/依赖检查通过；前三次真实 compressed 失败后先停止，用户随后授权独立诊断。本轮安全 HTTP 观测及原标准探针均通过真实压缩状态确认/四事实4/4；标准压缩组42.026秒、对照3.656秒，历史间歇性超时未复现，根因仍未知。详见[摘要耗时诊断](docs/research/summary-request-latency-2026-10-08.md)。CLI消费已按下述Spec068接入，持久化与正式异步仍待后续。

## Skill 索引与按需读取

[Spec098：技能列表与显式指令](docs/specs/098-skills-picker-and-explicit-command.md)支持输入`/skills`或加号→技能查看当前可发现的全部条目。列表每次打开/刷新重新发现，展示名称、说明、来源和指令；不创建会话或加载正文到模型。选择填入`/技能名`并保留草稿，补充任务后自己发送。`.claude`来源使用`/claude:技能名`；与内建指令重名的agents技能使用`/agents:技能名`，来源不会因默认项变化而被替换。发送前核对条目仍可用且read已启用，再沿原消息/Read/权限链加载正文。Read关闭时可查看但不能选择，不自动改工具。显式选择是模型索引约定，不保证真实模型一定执行技能。

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

模型条目的当前格式见[models.example.json](docs/examples/models.example.json)。占位URL/密钥/模型名需要自行填写；OpenAI/openai_compatible必须显式设置`api`为`chat_completions`或`responses`，按实际能力设置可选`profile.image_inputs`。旧配置没有api会在启动前报错；修改配置后重启服务，不自动迁移真实文件。

缓存能力用`profile.prompt_cache`声明：`protocol`为`openai`、`openai_options`或`anthropic`，`retentions`列出实际支持的保留时间。未设置`request.prompt_cache.retention`时，自动选声明集合中的最短值，与列表顺序无关；需要较长时间时在request中覆盖。样例仅演示端点支持`in_memory`及`24h`的写法，不代表所有兼容网关都支持；能力未知就省略，明确不支持设为`false`，两者均不添加控制参数。只支持24h的模型应声明`["24h"]`。Anthropic可声明5m/1h；新OpenAI options协议可声明30m。非法或矛盾配置在启动前报错，不能根据模型别名猜能力。

缓存默认参数在模型创建时设置，普通对话、子智能体、摘要和Auto各自沿用所选模型的配置；不增加请求或会话key，不改真实配置。参数存在不证明命中；供应商返回用量及费用口径仍按[M6后续分片](docs/specs/107-model-request-prompt-cache.md#6-验收与剩余分片)独立验收。

将私有模型配置放在`~/.naiyue-test-agent/models.json`后，在项目根目录运行（默认配置目录由用户home决定）：

```bash
.venv/bin/python -m naiyue_test_agent.application.cli
```

需要让模型读取工作区内文本文件时，显式附加 `--workspace /绝对路径/项目目录`：

```bash
.venv/bin/python -m naiyue_test_agent.application.cli --workspace /绝对路径/项目目录
```

CLI/Web统一登记六个文件工具及Bash、PowerShell，共八项。有工作区的新会话默认启用六个文件工具，无工作区默认为空；两个Shell通过后端会话工具选择API启用，默认策略Ask，各自精确Allow/Deny命令规则互不通用。旧命令工具专用开关已取消；工具选择界面与CLI交互命令留到后续。

Bash/PowerShell默认未活动，Default下未命中Allow的有效命令在执行前整批暂停；输入`:approve`或`:deny`，多项用`:review ...`。无效参数以原调用ID反馈模型。批准后每次新建非交互进程，以工作区为cwd、无stdin/TTY，使用当前用户的本机文件及网络权限；这是宿主执行，尚无沙箱。默认继承父环境（包括业务测试凭据），配置environment.values/path_prepend/unset_env可覆盖、前置PATH目录及最终移除；Bash默认移除BASH_ENV与NAIYUE_WEB_TOKEN，PowerShell默认移除后者，不自动加载项目.env。跨调用不延续cd/export。

两个Shell分别配置解释器和限额，见[当前配置样例](docs/examples/settings.example.toml)：默认60秒、最多300秒，合并stdout/stderr，末尾正文同时限2000行/64KiB。增量解码并清理ANSI、规范换行和转义控制字符，标明有损、实际行范围及部分行；超长末行返回安全UTF-8尾段。超限/转换时将含原前缀的原始输出归档到naiyue-agent-output/，必要时另建UTF-8文本视图，使用返回的Read路径分页。每调用实际落盘16MiB、同进程同工作区跨Shell/会话共128MiB；双份都计额，配额/磁盘失败仍排空并保留尾部，明确archive_complete=false和可能缺口。短普通文本不落盘，旧stdout/stderr日志仍可读/清理。超时给具体工具错误，用户停止结束本轮；未确认收尾的ProcessCleanupError上抛。Windows优先pwsh再PowerShell5.1，Bash只自动找原生Git Bash；无自动安装，缺解释器明确不可用。装配会运行有界固定版本探测，失败标unknown；PowerShell默认尊重策略，可配置该子进程bypass，不改机器策略。Windows全项目可运行性待085-15/WIN1实机复盘。

输出文件不会自动过期或删除。运行结束后输入 `:outputs clear` 查看当前工作区将清理的文件数和字节数，再输入 `:outputs clear confirm` 才执行；运行中或目录包含陌生文件时会拒绝。清理只针对应用创建的归档文件，不删除目录或用户文件；日志可能包含敏感测试数据，选择工作区和保留时间时须留意。当前清理与容量协调只在一个 CLI 进程内验证，不提供跨进程或 SaaS 隔离保证。详见 [Spec 045](docs/specs/045-workspace-bash-output-artifacts.md)。

提供工作区后 CLI 默认启用 `read`，并把规范化根目录存进当前应用会话；工具执行时通过可信运行上下文取得该会话的根目录。未提供时保持纯聊天。工作区根不能是隐藏目录，也不能位于 `.env`、`.ssh` 或 `.git` 内。工作区内普通 UTF-8 文本可直接读取，包括普通 `.gitignore` 文件和 `.github` 目录下的非敏感文件；工作区外、未命中敏感规则的普通文件先暂停，在终端展示规范化目标，逐个等候人工决定。输入 `:approve` 批准唯一待审调用，`:deny` 拒绝它；同时有多项待审时输入 `:review approve reject ...`，按显示顺序为每项给出决定。决定数量错误时不恢复。新普通输入在待审期间被拒绝，`:quit` 会提示内存审批状态无法在重启后恢复。其他隐藏路径、工作区外任何隐藏路径、`.pem`/`.key`、路径遍历和非普通文件直接拒绝；批准后工具还会重新解析路径，并要求目标与本次调用的一次性授权一致。不会执行写入。当前这是同进程内存 checkpoint 的本地审批，尚无身份授权、持久审批或 OS 级沙箱保证。

不超过 64 KiB 且不超过 2000 行时，`read` 直接返回原文；超出任一预算便按行返回一页，并用 `next_offset` 提示下一次读取的行号。64 KiB 只限制**文件正文**，提示文字另计，因此整个工具结果可略大于 64 KiB。模型也可显式传 `offset`（从 1 开始）和 `limit`（1–2000 行）。返回的正文页严格按 UTF-8 解码；参数、路径/权限、审批、编码、非文本、长行、I/O 与无工作区错误均回填带原因和下一步指引的安全 `ToolMessage`，不包含路径、原始异常或文件内容。路径不可用时为避免泄露敏感文件存在性，只列出“可能缺失、非普通文件或被工作区策略阻止”的原因范围。只读 Read 中未知普通异常也回填安全错误；LangGraph 中断仍上抛。文件在两页之间变化时，行号不代表同一个快照。工作区隔离、审批暂停/恢复与 CLI 命令通过假模型确定性测试；当前网关的受控七工具任务和双来源 Skill 正文/引用任务已观察到真实模型调用本地 Read 的正常路径。拒绝与故障路径仍主要依据确定性测试，不能扩大为任意供应商保证。图片 Read 与其他文本编码 Read 是后续独立能力，不由 RAG 模块替代。

启动后直接输入文字并回车；同一进程内的输入属于同一会话，历史由内存 checkpoint 保存。在交互式终端中，模型仍在运行时直接输入文字会作为 Steering 排队，输入 `:follow <文字>` 会作为 Follow-up 排队；界面显示“已排队”只代表应用接受，模型回复会在这次运行结束后按顺序显示。输入 `:stop` 请求活动停止；“已请求”只表示登记，关键操作收尾及保存确认后才显示“已停止”。停止时未消费的 Steering/Follow-up 和此后输入保留为待发送内容，`:pending` 查看，`:send` 明确重新提交；收尾期间拒绝启动新 run，准入失败不清空待发送内容。输入 `:model <model_id>` 切换下一次模型调用的选型，输入 `:quit` 或发送 EOF 会等待当前运行结束后退出。模型配置缺省从用户目录读取，也可显式覆盖；可用模型 ID 由该配置决定。通过管道发送多行时仍逐轮运行，不自动变成 Steering。选择工作区后模型可使用 `write` 创建或整文件覆盖该目录内的 UTF-8 文件，也可使用 `edit` 对现有 UTF-8 文件做精确多处替换；`edit` 用分块读取与临时文件写回，不设固定文件大小上限，但单次提交的替换文本仍受输入预算约束，处理还受实际磁盘空间与运行时间影响。此入口尚不提供工作区外写入审批、专用测试执行策略、业务提示词或逐 token 打印；默认持久模式可以恢复安全完成及安全停止的会话。工作线程刚启动尚未开窗或已进入收尾时，应用可能拒绝 `:stop`，CLI 会明确提示未接受。


工具运行设置默认读`~/.naiyue-test-agent/settings.toml`，与模型文件同目录；不自动从工作区读取或创建配置。Read/Ls支持`default_limit/max_limit/max_output_bytes/max_path_chars`，分别是缺省行数或条目数、模型允许请求的上限、UTF-8正文字节预算、路径字符上限；Write/Edit分别支持`max_input_bytes/max_path_chars`。Write预算计算content，Edit预算计算全部oldText+newText的UTF-8字节总量，不限制原文件或修改后文件大小。旧Write额度环境变量不再读取；搜索与Bash/PowerShell设置也已接入。参考[当前可用样例](docs/examples/settings.example.toml)，完整未来样例不能整份套用。设置在启动创建工具时固定，修改后重启应用生效；直接Python使用可`load_tool_settings()`后将相应`.tools.read/ls/write/edit`交给工具工厂的`settings=`。权限模式、Allow/Deny及可选Auto模型配置已由此文件启动校验；权限结构非法会终止启动，不沿工具额度的默认回退。

工具接入按[085-04](docs/specs/085-04-tool-registration.md)、[085-05](docs/specs/085-05-dynamic-tools.md)和[085-06](docs/specs/085-06-permission-pipeline.md)统一为`ToolSpec(tool, input_schema, execution_mode, side_effects, prompt_snippet, prompt_guidelines, permissions)`。工厂返回Spec，原工具和公开参数类分别为`.tool/.input_schema`。自定义工具可用`ToolPermission(default='allow'|'ask')`，文件目标可用`FilePermission(path_field, operation)`，均在`tools.permissions`；未声明权限默认Ask。`prepare(args, context)`准备JSON事实，`default_decision(subject)`只返回allow/ask，不调用模型；明确禁止放Deny规则。实际工具须在IO前显式调用`require_tool_permit`或`require_file_target`。`build_*application(..., tools=[spec, ...], initial_active_tools=[名称, ...], settings=ToolSettings(...))`只接Spec；省略设置则加载默认用户文件，省略初始选择则启用传入定义。CLI/Web共用`create_builtin_tools(settings)`。App提供`get_registered_tools/register_tool/get_active_tools/set_active_tools`；异步写操作为`aregister_tool/aset_active_tools`。登记不自动启用，活动选择从下次主模型请求生效；模式`get_permission_mode/set_permission_mode`从下个工具批次生效，异步写操作为`aset_permission_mode`。模型定义、提示词、压缩预算和执行共用本次集合，审批恢复沿已保存批次。`resume_tool_approval(..., remember_approved=[tool_call_id])`只记本次确实批准的精确目标，不写配置/DB。旧when/review/grant_factory、Read专用策略metadata和逐工具grant已移除；Auto已按[085-07](docs/specs/085-07-auto-permission-review.md)接入；配置两个已有模型ID后可选择。

子Agent角色、执行及CLI/Web审批已接[Spec086-S1—S6](docs/specs/086-subagent-file-map.md#l-s6执行细案2026-10-17用户已授权)：在同一`~/.naiyue-test-agent/settings.toml`中用`[[subagents]]`配置`name/description/system_prompt/tool_names`，可选`model_id/max_model_calls`；带注释示例见[settings.example.toml](docs/examples/settings.example.toml)。内存App、持久App、CLI/Web启动均校验并编译同一冻结定义；修改后重启生效，不自动热替换。角色引用的工具必须已经注册，自定义工具通过可信启动代码的`tools`参数接入后才能在配置中引用。定义/工具/模型错误会明确阻止启动并清理资源；运行中可`await app.aregister_subagent(SubAgentSpec(...))`增加新角色，`app.get_registered_subagents()`返回有序tuple，CLI桥提供同名同步外观；重名拒绝、不覆盖。角色不写会话DB，运行时新增角色重启后需重新登记。S3已把`create_subagent_tool()`纳入CLI/Web共用内置集合，仍沿会话活动工具选择；每次委派创建独立历史/thread，冻结模型/工具集合，父只收到有界报告与artifact。可选`max_model_calls`为正整数，省略不加额外次数限制；通过官方middleware按子thread累计主模型调用，到限返回工具错误，已执行副作用不回滚。`[tools.subagent]`设置报告的`max_result_lines/max_result_bytes`，默认2000行/65536字节。S3的内部Ask在checkpoint保存后登记当前子批并等待决定，原驱动者用同thread/interrupt恢复，父工具不重入，权限准备和Auto不重跑。`app.pending_subagent_approvals(session_id)`查询当前子批；`await app.asubmit_subagent_approval(session_id, approval_batch_id=..., origin_run_id=..., decisions=[{"approval_id": ..., "decision": "approve"}], remember_approved=())`只提交一个实例当前批的全部行并返回原root run回执，CLI桥提供去a前缀方法。临时记住只接受本批批准的approval_id，限制本会话，不写配置/DB。父提示词只显示活动subagent的角色名/短描述，运行中新登记从下一模型请求可见，不泄露完整角色prompt。

S4已接正式CLI/Web：`app.pending_approval_batches(session_id)`返回根paused_root及当前live_child分组，运行中的子审批返回原root run回执，继续同一SSE。Web使用`approval_batch_id`和`approval_id`提交，支持仅本会话记住批准项；旧HTTP审批字段已删除，客户端/服务端须一起更新。CLI用`:approvals`查询；单批可`:approve`/`:deny`，多批须指定完整批ID，多行用`:review <批次ID> approve reject --remember=1`。子状态和实例入口显示在父委派下；完整消息、工具参数/结果和进度现由[Spec095子智能体侧栏](docs/specs/095-subagent-sidebar.md)展示，子消息不追加到根聊天。S5已接单独停止和关闭收尾：子详情/审批卡片可“停止该子任务”，CLI用`:stop <agent_path>`；默认`:stop`停根，根暂停审批时也能停止本批。Python用`request_stop(session_id, run_id=..., agent_path=...)`；暂停根用`await app.astop_tool_approval(session_id, approval_batch_id=..., origin_run_id=...)`或对应stream入口。批准已接受的历史不改写，已开始IO保留真实结果；子checkpoint失败转具体工具错误、兄弟继续，根保存失败仍报错。App关闭先停后等，交互CLI EOF/quit先停当前运行；管道仍完成已提交普通请求，无输入时停止子审批等待。S6已用真实Read/Write/SQLite验证完整执行、ready关闭重开和新实例委派；仅同步模型SDK时仍在异步子图内部走框架线程回退，停止/取消/关闭会等待已启动线程结束。子待审或子文件已提交后进程崩溃，重开沿092补根调用“结果未确认”，不执行旧工具、不复活子driver/Future；新消息才会运行。当前有macOS真实文件/SQLite/HTTP/SSE/CLI和桌面/375px浏览器的假模型证据，真实供应商和Windows原机尚未验证。

自定义工具的完整注册示例、停止检查、IO期限、异常传播和资源所有权见[工具作者约定](docs/tool-authoring.md)。090B已为Read/图片/Write/Edit接入原RunControl的安全点检查，提交前停止不提交目标内容，已经进入提交则等待并保留真实结果；Ls仍返回授权部分结果及停止提示。注册成功只证明结构有效，不证明任意SDK可取消或资源自动收尾。[091A](docs/specs/091-tool-progress.md)已接八内置和自定义ToolProgress后端事件，统一[progress]预算默认200ms/8KiB；工具只报事实，运行层校验身份/终结后发布，[091B](docs/specs/091-tool-progress.md#8-091b实施与验收记录)已在Web/CLI维护最新展示快照，卡片显示阶段、真实计数、尾部、历时及最后活动时间；停止接受后保持停止中直到真实终局，重连恢复最新快照。同步写入官方stream_writer、异步走custom，保持原并发及停止合同。进度不进模型消息/checkpoint，文件正文和图片不进进度。[090C](docs/specs/090-tool-wait-stop-and-author-contract.md#8-090c实施与验收记录)已统一Bash/PowerShell/Grep/Find的timeout：省略/null/0无限，正整数指定本次秒数，无300秒上限；Grep全搜索共用期限，停止和必要清理保持。重启恢复092继续按片推进。

## 真实模型接入诊断

模型默认配置为`~/.naiyue-test-agent/models.json`，JSON格式见[Spec004](docs/specs/004-local-model-config-format.md)；当前不按文件私密权限位限制读取。原项目文件`.env/models.json`不自动搬迁；需要指定其他文件时传`--config <路径>`。`load_model_catalog`读取并校验配置，正式`build_async_local_application`创建会话、按会话选模并负责关闭模型客户端。102-03已移除旧configured工厂与record_selection空回调。下面的诊断命令支持项目已有的OpenAI/兼容接口、Anthropic与Ollama适配器，会调用真实模型；仅发送随机诊断值和工具提示，HTTP发送前共用两次预算（包括摘要/Auto/子调用），仅诊断独占的OpenAI/Anthropic客户端禁用SDK重试，90秒截止，不输出密钥或诊断值。未知适配器会在发送前报错，混合目录仍使用同一预算。

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

CLI默认创建持久新会话并显示ID。CLI/Web未传`--workspace`时使用启动终端的当前目录，显式参数优先；每个工作区在`.naiyue/workspace.json`持有本地UUID，库默认位于`~/.naiyue-test-agent/data/workspaces/<UUID>/sessions.sqlite`。可在全局`settings.toml`的`[storage] data_dir`配置绝对目录（Windows可放D盘），或用`--session-db`覆盖单个库路径；真实配置不自动改写。设置含义与Windows写法见[配置样例](docs/examples/settings.example.toml)。

不同工作区可以分别启动服务、打开各自浏览器窗口，各有多个会话。同一数据库仍只允许一个应用持有；CLI与Web选择相同工作区及数据位置时共用历史。配置位置和数据位置独立，数据目录不可用时明确失败。关闭服务后，携带`.naiyue`改名/移动目录，可自动重绑并继续原历史；不依赖下次启动终端所在目录（用`--workspace`选定目标即可）。复制目录默认检测重复ID并拒绝接管，用`--new-workspace`给副本新ID和空历史；不用于重置原目录，也不能与`--resume`、`--session-db`、`--no-session`合用。标识是本机身份，勿提交到版本控制；运行中移动或跨电脑搬历史不在本次支持范围。

应用元数据和官方saver分别负责会话信息/图状态；普通restore只开放安全结束且checkpoint一致的会话，相同session/thread、新run。[Spec092](docs/specs/092-session-restart-recovery.md#0-2026-10-17确认打开会话自动恢复)支持打开旧会话时自动恢复：GET仍只读最新历史，Web选择会话后自动POST恢复，CLI选择旧会话后自动恢复。执行中断时用三次官方更新保留真实结果、补未确认说明并结束旧任务，确认保存才开放新输入；确认停在工具审批前时按当前本地规则复核，通过后直接恢复审批卡片，批准前整批不执行。恢复不调用模型/工具/Auto、不发草稿；失败保留历史和手动重试，不自动取消失效审批。旧临时授权不跨重启。schema4不兼容旧全局库，不做迁移、双读或自动删除。详见[Spec087](docs/specs/087-workspace-session-storage.md)，原始持久合同见[Spec070](docs/specs/070-persistent-session-implementation.md)。

在项目根目录执行：

```bash
# 默认保存新会话，启用本工作区文件工具
.venv/bin/python -m naiyue_test_agent.application.cli --workspace .

# 显式临时会话，同进程仍支持多轮，退出后不可恢复
.venv/bin/python -m naiyue_test_agent.application.cli --workspace . --no-session

# 列出所选工作区历史会话；交互终端输入编号/ID选择，空行取消
.venv/bin/python -m naiyue_test_agent.application.cli --resume
```

直接选择使用`--resume <session_id>`，替换为CLI显示的真实ID；先选工作区再选择其中会话，自动移动重绑会同步更新会话根路径。安全完成会话可继续输入；中断会话自动整理后开放新输入，前置审批自动复核并恢复原待审项，用`:review approve/reject`提交决定。原审批条件变化时不自动取消；交互终端可明确输入`continue`放弃旧批次，空行退出。无ID非交互resume只列索引，不消耗业务输入。临时模式不打开会话DB，工具业务文件按原规则保存；临时与resume/显式DB路径互斥。活动工具名称随会话恢复，Bash被选择后执行仍走审批；旧模型/工具/模式不可用时，可读历史仍可查看，执行保持受限。整个应用启动所需配置仍须合法。

本地确定性全量`781 passed`，两个独立离线进程和一次有界真实默认模型探针验证历史延续；真实探针可运行`.venv/bin/python -m scripts.probe_persistent_session --real`，最多两逻辑请求、95秒总时限，临时DB/合成事实，只输出验收统计。证明机制接线，不代表业务评测或生产规模。

存储锁已分别接POSIX flock和Windows CreateFileW/msvcrt；本机验证在macOS完成，Windows/Linux实机验收仍待执行，不能据此宣称全项目Windows可用。已有文件不要求私密权限位，仍检查普通文件、链接、实际IO和schema。待审批、失败、未确认结束或副作用待核对会话重启仍阻断；同进程审批保持。高级恢复、长期记忆、历史数据清理与同项目多进程协调另排。

[Spec072](docs/specs/072-read-permission-extension-seam.md)已给会话Read增加权限扩展接口：可信调用方可通过`attach_file_permission_policy`注入同步allow/ask/deny策略，审批判断、带会话/资源上下文的review和执行前复核共用规则；无策略时沿用当前默认行为。策略异常回填安全工具错误，敏感/无效目标仍拒绝。17个新增测试与全量798例通过；仅Read接入，没有新增CLI权限模式，其他工具、成熟权限服务、原生异步及Web另片推进。

[Spec073](docs/specs/073-async-model-call-middleware.md)已接通无工具runtime的原生异步模型调用：选模/结束原因/响应门禁/用量事件和请求观测共用同步规则，`ainvoke`等待模型后返回完整结果，InMemorySaver历史可用。新增14例、全量812例通过；`.venv/bin/python -m scripts.probe_async_model`用现有默认模型验证一次真实请求、回答、模型事件配对与内存checkpoint（会发送随机合成文本，默认95秒父截止）。应用/CLI、摘要恢复、工具、SQLite持久化和token增量仍待适配；模型Task取消传播测试不代表用户活动停止或服务端取消保证。

[Spec074](docs/specs/074-async-context-model-chain.md)随后接通项目prompt、主动摘要及一次明确输入溢出恢复的异步模型IO，同步/异步共用原压缩算法。34例新增/全量846例通过，取消摘要和重发的确定性测试保留原信号；`ainvoke`完整结果及`astream`的values/updates状态替换/内存checkpoint已验证。真实上下文探针`.venv/bin/python -m scripts.probe_async_model --with-context --timeout-seconds 120`通过摘要+回答两逻辑请求和随机合成事实，125秒父截止，只调整探针内存预算，不读取用户文件/持久库或修改配置。仍无token增量、异步工具/审批/应用/SQLite或真实活动取消证明；CPU估算和事件订阅仍同步，框架内部子任务取消限制见待办。

## 正式异步应用与事件流

[Spec093](docs/specs/093-architecture-semantics-and-sync-cleanup.md)已移除独立同步App，以下为唯一产品应用执行链。

[Spec075](docs/specs/075-integrated-async-application.md)已合并接通工具、应用、SQLite及正式流式消费；新36例、全量882例通过。这是Spec075时点的异步基础；CLI接线和活动停止已由下述Spec076完成。

- `build_async_local_application`创建内存应用；`open_async_persistent_application`异步上下文管理器拥有SQLite/saver和SDK资源，参数`database_path`明确指定数据库。
- `acreate_session` / `acreate_workspace_session`、`aselect_model`、`arun` / `aresume_tool_approval`；同会话busy/待审批/人工核对保持，`aget_messages`只返回当前checkpoint的深拷贝消息。
- `astream_run` / `astream_tool_approval`消费领域事件，文本`message_delta`永远临时；完整snapshot按消息ID替换草稿，终局在关键清理/保存确认之后。`StreamingMessageProjection`提供展示状态，不让runtime依赖界面。
- `steer`、`follow_up`、`request_stop`使用run私有控制；并行批次遵循`max_concurrency`，异步缺省4，任一sequential整批原顺序。普通同步工具由框架在线程运行，Bash通过原生async共享runner执行宿主进程，按规则/模式审批，停止先确认进程收尾，不能称为沙箱。
- `capacity`默认256限制交付队列的事件数，溢出抛`EventStreamOverflow`要求重新同步；关闭消费不停止生产，应用持有后台任务，`aclose`排空后关闭其资源。复杂CPU计算/旧同步订阅者仍须快速，大payload/SaaS压力和完整历史恢复另验收。

下面的内存示例会使用已有模型配置发出真实请求，只打印事件类型：

```python
import asyncio
from naiyue_test_agent.application.async_application import build_async_local_application

async def main():
    app = await build_async_local_application(
        None, tools=[], system_prompt='', response_format=None,
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

实际输出：`active_stop_probe=PASS mode=real logical_calls=1 delta=observed stopped=verified fake_answer=absent checkpoint=safe`。加`--offline`可离线重复验证。这个结果证明客户端停止等待和安全结束，不证明供应商服务器停止计算/计费。任意不响应信号的自定义工具没有固定秒数停止保证；关键写入/保存仍须settle，Bash仅必要进程收尾未确认时阻断；普通超时错误可回填模型。最小Web与整体学习为下一步。

## 最小本地 Web 对话（Spec077）

React+TypeScript/Vite 页面参考已确认的 ChatKit 浅色聊天外观；FastAPI 同源提供构建文件、REST 动作与 SSE，直接复用现有异步应用及 SQLite/checkpoint。当前支持配置内模型选择、持久会话列表/安全恢复、完整消息与文本增量、运行中 Steering/Follow-up、活动停止和明确重发。Spec077首片仅聊天；[Spec078](docs/specs/078-web-tools-and-approval.md)随后接通工作区文件工具、可折叠工具详情和逐项决定/整批提交审批。

本机已安装并构建。新环境先安装原 Python requirements，再执行 `.venv/bin/python -m pip install -r requirements-web.txt`、`npm --prefix frontend ci`、`npm --prefix frontend run build`。启动：

```bash
.venv/bin/python -m naiyue_test_agent.web
```

打开终端打印的本地访问链接，默认随机可用端口和本地访问凭据；运行只需 Python 服务。可选 `--workspace /绝对路径`、`--session-db /绝对路径/sessions.sqlite`、`--port 端口`。同一个数据库只允许一个应用进程使用。服务启动不编辑 `.env`，模型 key 不发送到浏览器。

启用现有文件工具：

```bash
.venv/bin/python -m naiyue_test_agent.web --workspace /绝对工作区路径
```

所有模式均登记八个内置定义；工作区新会话默认启用 `read/ls/find/grep/write/edit`，原权限、错误回填和串并行策略继续生效。工作区外可审批的操作会先暂停，在页面核对每项批准/拒绝后提交整批；未选择时不会默认批准。Bash通过后端会话活动集合选择，页面暂不提供工具管理按钮，默认Ask并按显式规则/模式处理。未传工作区使用启动cwd；重启后的待审批会话仍阻断自动恢复。

工具实现/HTTP契约/验证见 [Web工具说明](docs/web-tools.md)。离线真实 TCP/审批与两进程恢复验收运行 `.venv/bin/python -m scripts.probe_web_tools`，不产生收费请求。

运行中普通消息为 Steering，勾选“结束后跟进”即 Follow-up。停止期间输入继续可编辑，未执行内容保留，明确重新发送后才启动新轮。断开 SSE 不取消生产，恢复 view/序号用于同步，完成状态依据保存后的领域终局。浏览器草稿和部分待发送内容只在内存；安全会话恢复不等于完整历史归档或 SaaS。

使用、代码地图、实际验证与限制见 [Web 对话说明](docs/web-chat.md) 和 [Spec077](docs/specs/077-local-web-chat.md)。离线 TCP/两进程探针：

```bash
.venv/bin/python -m scripts.probe_web --timeout-seconds 60
```

输出 `web_probe=PASS mode=offline tcp=True sse_delta=True disconnect_keeps_run=True stop=True retained=True two_processes=True history_used=True duplicate_safe=True`。浏览器真实默认模型两次逻辑调用验证了精确回答与活动停止，桌面/375px 视口无横向溢出；这些证据不替代业务评测、工具实网审批或生产负载验证。
