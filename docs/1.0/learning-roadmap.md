# 全项目代码学习路线

代码版本对应（Spec093，2026-10-17）：历史课堂讲过的`LocalApplication.run → SessionService.run → run_steps`已撤销；当前从`AsyncApplication.arun → SessionService.arun → arun_steps`进入同一准入/结果生成器。`async_session.py`实际方法并回`session_service.py`，`persistent.py`由`async_persistent.py`取代，finish仅保留`_afinish_run`。已学的session/thread/run归属、IOCall/send/throw、异常优先级与IO settle机制仍适用；变动及当前消费者见[Spec093](../specs/093-architecture-semantics-and-sync-cleanup.md)，不代表本轮已完成新的课堂或独立掌握。

当前学习安排（2026-10-14）：第六章8节职责/主分支讲解收束，最后一节见[Lesson73](lessons/lesson-73.md)，全章入口Lesson66–73。唯一下一步第七章上下文工程，先建立模型上下文组成与来源地图。前五章综合复盘见[Lesson65](lessons/lesson-65.md)。本节审批/恢复既有测试20 passed in1.40s，限本机fake与同进程合同，不宣称跨重启审批、实网模型或生产保证。讲解收束不等于逐行覆盖；用户有循环跳转/settle自述与失败隔离设计提问，第六章本人调试及独立实现未评估。后续为7上下文工程、8持久化与CLI、9Web/前端、10测试与复盘；改进见[后续清单TE9](../future-work.md)，未启动新功能。分类器练习仍暂缓，发布实现保留。

记录方式：用户确认完整小课/模块收束一次；当前进度只在本页维护，lesson保留知识/理解证据，handoff作短交接，索引不复制即时进度，不逐轮写流水或重复跑测试。

## 十章主题与小节估计

按当前模块/分支方法重新组织，不按历史lesson编号或聊天轮次计数。一节解决一个完整机制，含数据形状、分支/调用图和必要单步；相关机制可合并，难点可拆分。当前范围约57–75主题小节，从第三章起约49–64；是范围预算，不是固定完课时间或掌握保证。前两章不重学，跨章复访只补新职责。

| 章 | 主题 | 预计小节 | 主要内容 |
|---|---|---:|---|
| 1 | 系统地图与启动入口 | 2–3 | 模型/运行时/工具/应用职责、启动装配、Agent/会话/运行区别、请求总路线；主要讲解已完成 |
| 2 | 模型接入 | 6–8 | 配置/目录、供应商构造、thinking、超时/SDK重试、资源归属、异常与结束原因；主要讲解已完成 |
| 3 | Agent循环与middleware | 6–8 | 消息形状、Agent创建、状态/工具回填、多轮循环、hook顺序、handler、context/选模与调用方式 |
| 4 | 事件、消息与观察 | 5–7 | 标识/序号、生产/订阅/投影、delta/快照/终局、观察故障、JSON/去重；复用已讲失败合同 |
| 5 | 会话运行与控制 | 7–9 | session/thread/run、busy/队列、steering/follow-up、停止/取消、任务归属、IOCall/send/throw/settle、并发 |
| 6 | 工具、权限与审批 | 8 | schema/注册、Read/ls/find/grep、write/edit、Bash、串并行批次、错误反馈、权限/HITL/审批恢复 |
| 7 | 上下文工程 | 6–8 | 项目指令/Skill、token/窗口/预算、输出限制、安全切点、摘要替换/验证、回退/溢出恢复 |
| 8 | 持久化与CLI | 5–7 | SQLite/checkpoint、确认保存、独占/恢复/受限状态、失败/重启、CLI线程/异步桥和事件消费 |
| 9 | Web与前端 | 8–12 | 服务/接口、任务/SSE、回执/游标/重同步、必要TS/React、状态/并发、聊天/工具/审批交互 |
| 10 | 测试任务与全链路复盘 | 4–5 | 通用Agent结合提示词/Skills和工具完成测试任务；假模型/集成/业务评测区别、排查、扩展点、完整轨迹与可证实贡献；历史Discount原型已移除 |

### 第三章建议顺序

1. HumanMessage/AIMessage/ToolMessage形状与一轮模型→工具→模型。
2. Agent装配和单次运行：create_agent产物、准备配置与执行输入。
3. 消息状态更新、工具结果配对/回填、多轮循环与结束。
4. before/after hook注册、收集与实际执行顺序。
5. wrap_model_call/wrap_tool_call的handler进入/返回方向、短路与异常。
6. 显式context与动态模型选择，引用已学RunServices/描述合同。
7. 按难点单列或合并：invoke/ainvoke/stream/astream的执行/输出差别；取消/settle留第5章。
8. 按需一轮→多轮综合测试轨迹复盘，不重复第二章分类算法。

### 第四章建议顺序：当前按6节组织

1. 事件主链：执行事实→事件数据→发布器→订阅者/投影；区别ToolMessage等会话消息与观察事件。
2. 关联与数据合同：session/run/turn/model_call/tool_call/message标识、seq，事件类型/payload及JSON投影边界。
3. 发布与订阅：RunEventPublisher、交付顺序、重入/并发边界、监听故障与同步慢消费者限制；复用第二章异常隔离结论。
4. 完整消息观察：历史基线、消息完成/修订/移除、ID与去重；只讲压缩观察事实，算法留第7章。
5. delta与完整校准：模型分片→message_delta→草稿投影→完整消息确认；此时本人走读delta_is_available测试。
6. turn及过程事件总图：model/tool/turn/run的关系，控制请求接受/交付/生效与上下文操作观察；执行政策与保存实现分别留第5/7/8章。

6节是当前主题安排，保持总表5–7节范围，按难点合并/拆分。第四、五章覆盖后端运行/观察/控制主干，工具/权限（6）、上下文（7）、存储/CLI（8）及Web后端（9）仍是关键内容；不据章节讲完推断本人独立能力。

六个主体问题加两项可合并内容，故估计6–8节。关键框架机制需要时核对当前源码和最小探针；控制/压缩/持久化首次出现只给合同/数据形状，深入留对应章。未实现RAG/MCP/沙箱等长期方向不冒充当前10章已有能力。

## 第二章异常重建：计划6节小课

固定主线：一个模型请求最终抛TimeoutError→生成FailureDescription→原异常标注→裸raise→应用读取描述并补stage→必要收尾→终局/调用者得到原异常。前5课持续使用这个例子；先形成完整地图，再局部实现，最后回同一条主线复盘。错误分类、恢复动作与事件观察分别说明。

| 计划小课 | 单一问题与主要代码 | 本课检查/产物 |
|---|---|---|
| Lesson39：先重建地图 | 原异常、FailureDescription、RunFailureData与run状态分别是什么；用具体数据追主线。只定位边界，不深入分类/配置算法 | 用户能指出对象从哪里产生、传给谁；区分局部return与外层raise、描述与终局 |
| Lesson40：模型异常怎样变成安全描述 | model_access/errors内置分类、输入溢出识别、unknown安全诊断；FailureDescription字段/校验，resolve的无自定义规则路径 | 从TimeoutError再换成一个已安装框架/SDK错误，追类别/原因/描述；说明unknown及transient不能自动决定重试 |
| Lesson41：自定义规则如何选择 | ErrorClassifier固定(error,source)、normalize注册校验、resolve自定义→内置→兜底 | None与合法unknown对比；写一个同步分类器，用本机合成异常验证原异常不被规则故障替换 |
| Lesson42：本次运行使用哪份规则 | ModelCatalog配置/快照、Runtime普通model委托、RunObservation.bind/RunServices、make_invocation_context；只读主线所需部分 | 标出准备阶段与执行阶段；区分model_describer(model_id,error)和classifier(error,source)，追一份配置到本次调用 |
| Lesson43：描述怎样随原异常回到应用 | describe_failure_in_context、attach/read标注、middleware捕获/裸raise、App读取本run描述与加stage | 追同一异常与描述身份；演示不可写/foreign-run回退，区分局部返回、属性副作用与异常传播 |
| Lesson44：谁决定结果与综合练习 | 应用failed/终局的必要顺序、普通观察隔离；停止/可恢复工具仅做结果层对照 | 独立追完整TimeoutError链，注入坏分类器及观察故障分别预测结果；简述模型调用失败与run failed/stopped的区别 |

当前默认方法：先给模块目的、真实输入/输出与边界；列该问题的完整分支，再按分支追各层交互、函数与结果。保留一张总图/分支表作为导航，优先解释决定分支或合同的少数关键函数；其余实现等用户点名，不以逐行覆盖为推进条件。复杂语法只在所选函数中按需展开。模块级复述/练习放在完整主题之后，不默认每轮检查题。6项是主题分组，可以按理解合并推进；原30–60分钟/总4–6小时只是旧节奏估计，新节奏不保证固定课时。

用户进一步确认每个分支配独立可运行测试，结合本人debug：优先复用、必要时补测试；给命令/断点/关键变量及预期。当前分类选择的9个分支入口见[分支调试指南](exception-branch-debug-guide.md)，从None→内置与合法unknown结束两条对照开始。助手通过测试不代表用户已亲自运行；IDE/环境配置不自动更改。

范围：本轮学清普通模型失败、描述生成/选择/配置/回传、应用结果与观察的必要边界；不要求在第二章读完所有异常分支。middleware注册/框架调度留第3章，完整事件状态机第4章，UserStop/外部取消的调度与settle第5章，工具反馈/未知副作用第6章，压缩/溢出恢复算法第7章，SQLite保存/清理/恢复第8章。本轮只在相关边界说明输入/输出、验证与后续阅读位置，不把定位记为逐行学完。

完成标准：能用当前源码独立追一条普通失败，能写并验证(error,source)分类器，能解释None/unknown、原异常/描述/终局、诊断失败的回退，能预测坏分类器和观察故障的不同影响。这些要有本人复述/代码/调试证据；只说懂了仍记自述理解。达到本范围检查后返回第二章stop_reason.py，再进入下一开发切片；不是等待全项目学习结束。

2026-10-12用户最新调整：分类器本人练习先不实施，按用户指令先回stop_reason继续课堂；上述独立实现完成标准保留未达/未验收，不作为阻止继续学习的门槛，不自动追加补课或代写实现。

Lesson39–44仅是计划编号，用户开始对应课后才建立课堂笔记并记录真实进度。Lesson38保留之前部分讲解与“标注尚不代表应用收尾”的正确回答，不把它算整链掌握；新课以体系重建为目标，旧记录不覆盖。

## 历史课堂定位（以下保留此前阶段记录）

2026-10-11当前衔接：[Spec081](../specs/081-event-exception-convergence.md)已实施，原reporter/id表回传改为原异常属性标注，运行依赖集中为RunServices。用户明确此前整体未理解，现进入[Lesson38](lessons/lesson-38.md)，先普通模型超时回传与观察隔离，再回第二章stop_reason；以下旧课堂记录保留历史，不作为当前接线说明。理解检查待答，独立实现/追问未评估。

后续接口清理见[Spec082](../specs/082-remove-obsolete-runtime-bridges.md)：旧分项bind/getter、failure_data、两个旧交互builder及workspace调用包装已删除，旧课堂中的相关名字仅作历史。当前入口为中心RunServices及统一交互装配；同步保留项有具体消费者清单，代码规模表仍是当时快照。本次实现不升级用户掌握记录。

093C版本对应：历史`invoke_agent_run`改用`async_invocation.ainvoke_agent_run`；resource普通/审批恢复改用`ainvoke_agent_run_with_resources/aresume_agent_run_with_resources`；`invoke_with_message_observation`改用`async_message_observation.ainvoke_with_message_observation`。测试直接await，不经同步应用代理；建图仍是共享同步函数。

状态：2026-10-10。**用户自述第一章已讲内容学会，独立实现与面试追问未评估；第2章errors旧版及Lesson28失败链已讲，整体独立追踪尚未证明。用户批准架构复审方向后，Spec079分层契约/实例扩展已实现（后端962/前端27测试通过），新版核心待学，先按[异常地图](exception-architecture.md)从contracts→model→runtime→application追三条故障链，再回第2章stop_reason。** 第一章见[Lesson18](lessons/lesson-18.md)/[Lesson19](lessons/lesson-19.md)。真实试用/断点/确定性探针作为相应章节的学习素材；下方原代码统计保留2026-10-09盘点，新增模块行数单独列示，不伪造全项目重新测量。

新版异常现从[Lesson29](lessons/lesson-29.md)建立普通模型超时的完整地图；仅展开内部报告/身份复用，分类器算法与其余故障分支待学，不把架构地图定位当作核心代码全部读完。

数据类见[Lesson30](lessons/lesson-30.md)，描述选择见[Lesson31](lessons/lesson-31.md)，分类器签名适配见[Lesson32](lessons/lesson-32.md)，模型目录快照见[Lesson33](lessons/lesson-33.md)，当前[Lesson34](lessons/lesson-34.md)读取RuntimeErrorPolicy构造及普通模型委托，控制/保护/普通运行分支待逐行读。

用户在Lesson34反馈整体链再次迷糊，暂停新分支；先用TimeoutError实际数据流复盘“分类器是函数、描述是产物”及准备/执行区别。此前局部答对不表示整链已掌握。

用户正确区分原异常与描述后说“继续”，[Lesson35](lessons/lesson-35.md)仅推进无效模型响应这一保护分支，按消息→异常→描述→报告/传播读；其他保护/普通运行分支仍未逐行学习。

工具写提交结果不确定/cause与会话核对见[Lesson36](lessons/lesson-36.md)。

现[Lesson37](lessons/lesson-37.md)只追原生异步模型等待中UserStop→图停止结果→stopped终局；外部取消/Graph控制/普通runtime/tool与App未报告回退仍待学。

用户明确要求完整对象/调用/数据/分支图，反馈逐行内容疲劳。当前暂停Lesson38及其余新分支，优先[异常系统总图](exception-architecture.md)的1A节；新图整理是讲解导航，不补记所有分支已学/整链已掌握。后续按用户点名节点深入。

## 代码规模与统计口径

以下保留早期工作区统计快照，不代表当前文件清单；其中`test_agent`原型已由Spec093C移除，2026-10-18清理其残留缓存/目录。原统计包括当时已提交、未提交和新文件，不是只统计HEAD。物理行按UTF-8文本的`splitlines()`计数，包含空行、注释和文档字符串；非空行只去掉空白行，仍可能含注释，不能称为纯逻辑代码行数。没有运行模型请求。

| 分类 | 范围 | 文件数 | 物理行 | 非空行 |
|---|---|---:|---:|---:|
| 后端产品代码 | naiyue_test_agent/**/*.py | 73 | 10,330 | 8,985 |
| 前端产品代码 | frontend/src 中非 *.test.* 的 TS/TSX/CSS | 15 | 937 | 890 |
| Python测试及测试辅助代码 | tests/**/*.py | 96 | 17,507 | 14,649 |
| 前端测试 | frontend/src 中 *.test.ts/tsx | 9 | 408 | 385 |
| 诊断/验证脚本 | scripts/**/*.py | 18 | 2,756 | 2,429 |
| 前端构建配置与HTML入口 | index.html、vite.config.ts、tsconfig.json、package.json | 4 | 62 | 61 |

产品代码合计 **11,267物理行 / 9,875非空行**；加上测试及探针为 **31,938物理行**，再加表中4个前端构建文件为32,000行。排除 `.venv`、`node_modules`、`dist`、`__pycache__`、lockfile、文档、第三方参考仓库/Skill与私有模型配置。后端包含模型/运行时/工具/应用/测试策略/Web入口，**HTTP Web入口本身是740行**，不是整个后端。

| 后端模块 | 文件数 | 物理行 |
|---|---:|---:|
| model_access | 5 | 619 |
| agent_runtime（不含tools子包） | 27 | 4,012 |
| agent_runtime/tools | 9 | 2,131 |
| application | 18 | 2,730 |
| web | 11 | 740 |
| test_agent | 2 | 97 |
| 根包标记 | 1 | 1 |
| 合计 | 73 | 10,330 |

代码行数仅表示阅读范围，不评价质量、复杂度、生产成熟度或用户贡献。测试行数较多，不代表已经有HTTP/RPC测试业务效果或生产负载证据。

## 顺序：先建立地图，再按机制读实现，最后串起产品

10章是主题分组，不是10次课。每章拆成多个小课，每次围绕一个函数组/一条数据流，必要时停在一行。开发继续自底向上；学习先看整体地图，再模型/循环/事件/会话/工具/上下文/存储/界面，避免从Web入口一路陷入所有模块。

| 章 | 主题与主要入口 | 读完要能解释的问题 | 课堂产物 |
|---|---|---|---|
| 1 | 系统地图与启动入口；README、web/__main__.py、web/app.py、application/_assemble_application、agent_runtime/build_runtime_agent只看定位与边界 | 模型/运行时/工具/应用如何组织；Web/CLI、框架、项目代码各负责什么；create_agent创建与run/会话的区别；当前业务边界 | 架构图、对象关系、一次请求总路线、接下来阅读地图 |
| 2 | 模型接入；model_access的5个文件 | 配置如何被加载和真正使用；供应商适配/基类、thinking回退、上下文与输出参数、失败分类/结束原因、超时/SDK重试、客户端资源归属 | 一条配置到模型的调用链、默认值/异常表、接入模型的小练习 |
| 3 | Agent循环与middleware；agent_runtime/__init__、invocation_context、async_invocation、resource_invocation，application/__init__ | Human/AI/Tool消息结构；模型怎样请求工具、结果怎样回填；create_agent如何收集hook并构图；reducer/jump_to/handler顺序；同步与异步调用入口 | 真实数据结构与一轮→多轮轨迹、hook时序、框架/项目职责表 |
| 4 | 事件、消息与观察；events、call/message/turn/control/context观察模块，application/run_events、stream_projection | 谁生产/消费事件；run/turn/message/tool标识与序号；delta/完整snapshot/终局；异常和终局的关系；JSON序列化/去重；事件不能替代授权或保存 | 事件生产→交付→投影的数据流、各状态证据、unknown/失败诊断样例 |
| 5 | 会话运行与控制；session_service（已合入async_session）、session_controls、async_application、async_event_stream、io_steps及run_control/active_stop/async_utils | session/thread/run归属；busy/队列/开窗关窗；Steering与Follow-up；停止/失败/收尾优先级；async任务归属、断开与取消、IOCall/send/throw、已开始线程IO的settle | 完整状态/信号表、正常/干预/停止/异常路径、并发与取消练习 |
| 6 | 工具执行、调度、权限与审批；tools全部工具、tool_batch/async_tool_batch、tool_approval、file_permissions；复访session_service审批部分 | 工具定义/schema/metadata注册；Runtime.context注入；read分页与路径；ls/find/grep；write/edit原子性；Bash进程/输出；串并行；ToolMessage错误；HITL和一次性授权 | 七工具共同范式与差异、批次顺序、审批→恢复/拒绝链、新增工具练习 |
| 7 | 上下文工程；project_instructions、skills、project_prompt、context_observation、context_compaction、context_summary_io、context_recovery | 系统prompt/AGENTS/CLAUDE/Skill来源和加载时点；近似token/usage/窗口/预留/阈值；安全切点、摘要替换、回退、一次溢出恢复；上下文不等于全部展示历史 | 数字预算例子、切点场景表、压缩/恢复轨迹和失败验证 |
| 8 | 持久化与CLI；session_storage、session_persistence、persistent/async_persistent、cli/cli_async/cli_display；复访SessionService finalize | 元数据与官方checkpoint分别存什么；保存确认与终局顺序；同库独占；安全恢复/受限状态；CLI线程桥与异步应用；哪些状态没持久化 | SQLite/checkpoint关系图、重启/保存失败案例、CLI完整入口复盘 |
| 9 | Web与前端；web全部文件、frontend全部产品代码及构建入口 | lifespan/REST/schema/本地访问边界；生产任务和SSE连接分离；回执/游标/重同步；TS类型、JSX、React state/ref/effect、API/fetch/SSE；聊天/工具/审批投影、模型选择、草稿与操作竞态 | 一次浏览器操作全链路、请求/事件/界面状态表、前端小改动 |
| 10 | 验证与全链路复盘；代表性tests/scripts、正式异步入口复访 | 历史业务原型已撤销；当前通用Agent主入口的职责；假模型/真实集成/业务评测证据区别；怎样复现/定位；扩展模型/middleware/工具/评测；哪些长期能力尚未做 | 独立追踪/小实现/故障排查/面试连续追问与真实贡献清单 |

第1章只建立地图，不一口气解释所有启动依赖。第一次出现尚未学习的模块时先讲输入/输出与职责，详细实现登记为后续阅读项；不能把一次定位算作该文件已经通读。长文件按机制分段、跨章复访，例如运行装配中的控制/压缩接线和SessionService中的审批/finalize。

**第一小课（已开始）：** 从 `python -m naiyue_test_agent.web` 的入口画到模型目录、SessionService、create_agent及SQLite的装配图，区分启动、会话、运行。先定位装配依赖，不把地图定位记为通读；接着逐段读入口核心代码。

## 每个小课怎么学

1. 先给一个具体任务及当时的真实数据结构，用示意数据明确标注，不能让用户猜变量形状。
2. 给入口与最小调用图，追踪生产者→消费者、参数/状态/返回值/副作用，说明正常与异常路径。
3. 先列完整分支表：触发条件、负责层、调用函数、输入/输出和状态变化，再追各层交互。先把完整分支说明白，不按几行代码切碎课堂。
4. 主动展开决定分支选择、跨层合同或关键副作用的少数函数；其余仅定位，用户点名后深入。框架源码仅在解释所选机制所必需时核对实际版本，区分API形状与真实执行证据。
5. 配套读一到两条相关测试，说明变量由谁赋值、断言究竟证明什么；必要时用断点/有界离线探针，不自动发收费请求。
6. 在完整模块主题之后用复述、小改动或故障假设检查，不默认每轮提题；用户说懂了仍只记自述理解，不自动升级独立实现/追问。
7. 笔记按模块目的、分支、各层调用及待深入函数组织；说“继续”接当前分支脉络，保留已理解内容，不要求先读完所有函数行。

每天1–2小时包含讲解、问题、调试和练习。按实际理解速度安排，不承诺固定天数读完全部源代码，也不把“看完”自动等同“完全掌握”。

## 覆盖与掌握记录

下文文件表是**计划覆盖**，不是已经讲过。当前产品源文件88个，加上4个前端构建/HTML文件共92个，均有主负责章节。公共薄包装/同步兼容链路也读清职责和调用者，不因Web用异步就擅自删掉或视为无用。测试/探针在对应章节选代表阅读，最终第10章复盘；不要求把17,888行测试逐例念一遍。

后续每个文件/关键函数分别记录：未读、已讲解、引导后理解、独立实现、可应对追问；功能验证证据独立于人的掌握记录。独立实现需本人代码/调试操作，追问能力需实际回答，当前本轮均未评估。旧lesson1–17作为历史笔记复用，内容可能反映同步/旧名称阶段，讲课时核对当前源码；不覆盖旧笔记；后续新课写入docs/1.0/lessons，从lesson-18起按小课连续编号。

`docs/关键知识点.md`只有用户明确点名要求记入时才修改。后续课堂笔记写docs/1.0/lessons，不能自动把本路线或每次讲解写入关键知识点。

## 代表性验证材料（随对应章节读，不另起一轮开发）

- 模型：test_model_access、test_model_limits、test_model_request_context、test_model_stop_reason。
- 循环/事件：test_agent_runtime、test_agent_runtime_response_gate、test_async_model_middleware、test_message_observation、test_turn_observation、test_control_observation。
- 控制/工具：test_async_application、test_control_races_and_failures、test_active_stop、test_async_tool_batch、test_read_tool、test_tool_approval_registration、test_host_bash及各工具测试。
- 上下文：test_session_system_prompt、test_project_skills、test_context_compaction、test_context_compaction_fallback、test_context_overflow_recovery。
- 持久化：test_persistent_sessions、test_persistent_processes、test_async_persistent、test_cli_async_stop。
- Web/前端：test_web_api、test_web_tools、test_web_delivery及SSE/事件/审批/Conversation/App/useConversation相关前端测试。
- 集成：Discount历史测试已移除；probe_async_application、probe_active_stop、probe_web、probe_web_tools的离线方式优先；真实供应商探针必须区分收费调用和业务评测。

## 文件阅读清单

2026-10-16源码定位更新：085-01统一错误、[085-02目录/显式依赖](../specs/085-02-tool-package-boundaries.md)、[085-03A设置/Read接线](../specs/085-03-read-settings.md)、[085-03B Ls/Write/Edit设置](../specs/085-03b-file-tool-settings.md)与[085-04启动注册](../specs/085-04-tool-registration.md)已实施；下表指当前入口，lesson保留当时版本；085-04后工厂返回ToolSpec，原工具属性/调用通过.tool，模式读取execution_mode。此处只更新位置与行数，不新增已学/独立掌握记录。

下表由本次源码清单逐文件建立，每个文件只指定一个主章节，跨章机制复访按课堂进度登记。

### 第1章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/config_paths.py` | 11 | 085-03A新增用户默认目录/模型路径，无框架依赖；实现不代表已学 |
| `naiyue_test_agent/__init__.py` | 1 | 待系统复盘 |
| `naiyue_test_agent/contracts/__init__.py` | 1 | Spec079新增基础契约包，待学 |
| `naiyue_test_agent/contracts/failures.py` | 146 | Spec079共享描述/分类与签名适配，待学 |

### 第2章

以下行数保留历史盘点，阅读状态更新至2026-10-12。当前主要剩stop_reason后半及章末复盘；暂缓的本人练习保留未验收，按用户指令继续。

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/model_access/__init__.py` | 350 | 加载/构造/目录已讲（Lesson20–24）；bind_model_output_limit待第7章；独立实现/追问未评估 |
| `naiyue_test_agent/model_access/catalog_lifecycle.py` | 67 | 准备/登记/关闭核心已讲（Lesson25）；独立实现/追问未评估 |
| `naiyue_test_agent/model_access/errors.py` | 107 | 旧版Lesson26及当前核心Lesson39–44已讲；SDK所有映射条目未逐行覆盖，独立实现/追问未验收 |
| `naiyue_test_agent/model_access/stop_reason.py` | 82 | Lesson45正常/tool_use自述理解，length/error/unknown/诊断/优先级核心已讲；整模块独立验收未完成 |
| `naiyue_test_agent/model_access/thinking.py` | 63 | 核心已讲解：Lesson23；回退检查题正确，独立实现/追问未评估 |

### 第3章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/__init__.py` | 262 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/async_invocation.py` | 36 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/invocation_context.py` | 29 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/resource_invocation.py` | 60 | 待系统复盘 |
| `naiyue_test_agent/application/__init__.py` | 104 | 待系统复盘 |

### 第4章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/async_message_observation.py` | 138 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/call_observation.py` | 204 | 原failure_data行号属历史版本（Lesson27）；现仅模型与公共事件助手，工具/HITL迁下列执行模块；已讲状态不变 |
| `naiyue_test_agent/agent_runtime/context_events.py` | 90 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_operation_observation.py` | 194 | 待系统复盘 |
| `naiyue_test_agent/contracts/session_events.py` | — | 原control_events转发模块已按Spec082删除，控制/审批数据从当前契约文件阅读；行数未纳入旧快照重算 |
| `naiyue_test_agent/agent_runtime/control_observation.py` | 172 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/events.py` | 287 | safe_error_type/RunFailureData24–45已讲（Lesson27）；其余待系统复盘 |
| `naiyue_test_agent/agent_runtime/errors.py` | 99 | Spec079运行层异常目录/策略与报告端口，待学 |
| `naiyue_test_agent/agent_runtime/message_observation.py` | 205 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/stream_events.py` | 23 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/turn_events.py` | 84 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/turn_observation.py` | 203 | 待系统复盘 |
| `naiyue_test_agent/application/run_events.py` | 172 | RunObservation初始化关键字段/failed123–148、finish163–170已讲（Lesson28）；cancelled及订阅/日志等其他部分待读 |
| `naiyue_test_agent/application/errors.py` | 17 | Spec079应用错误目录，待学；旧导入保留同一类型 |
| `naiyue_test_agent/application/failure_policy.py` | 51 | Spec079应用映射/try_describe，待学 |
| `naiyue_test_agent/application/stream_projection.py` | 57 | 待系统复盘 |

### 第5章

当前按8节安排，每节覆盖一个完整机制，可包含多个分支与测试，不按条件或聊天轮次拆节。先用同步入口理解状态与合同，再展开真正影响停止和收尾的异步机制；第四章的事件观察仅引用结论。

| 节 | 主题 | 要解决的问题与阅读边界 |
|---|---|---|
| 1 | session/thread/run与busy准入 | 谁保存会话状态、谁标识checkpoint历史、谁标识单次运行；同会话互斥与不同会话隔离。先用同步测试，审批/人工核对只说明准入合同 |
| 2 | IOCall与生命周期驱动 | _run_steps交出待执行操作，arun_steps用send回传结果、throw回传异常；当前只走异步驱动，同步run_steps属历史版本，异步调度内部步过 |
| 3 | 一次run从准备到收尾 | 准备、执行、结果处理、finally与busy释放；正常/失败及主要/次要异常。必要保存只讲输入输出，存储算法留第8章 |
| 4 | steering与follow_up实际交付 | 接受、队列、交付边界、优先顺序和输入窗口；关联第四章已讲的control事件，不重讲观察器内部 |
| 5 | 用户停止与外部取消 | request_stop与run私有信号如何进入模型/工具边界；未开始与已开始、stopped与failed的区别 |
| 6 | 已开始操作的settle | 为何取消等待不能证明线程IO已经停止；run_blocking/await_settled与收尾、busy的关系。工具权限与写入算法留第6章 |
| 7 | 异步任务归属与消费断开 | _execute/_stream、后台任务、事件消费者和aclose；消费者断开与生产任务取消的区别，SSE协议留第9章 |
| 8 | 前五章全流程复盘与并发收束 | 用户要求六组详细场景：启动/正常循环、控制输入、模型失败与观察故障、异步流与断开、停止/取消/settle、并发与旧run隔离；按Lesson65逐组走读，可跨多次课堂，不按聊天轮次算新节 |

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/active_stop.py` | 62 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/async_utils.py` | 46 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/run_control.py` | 63 | 待系统复盘 |
| `naiyue_test_agent/application/async_application.py` | 189 | 待系统复盘 |
| `naiyue_test_agent/application/async_event_stream.py` | 76 | 待系统复盘 |
| `naiyue_test_agent/application/async_session.py`（历史） | 88 | 093已并入session_service，不再作为当前入口 |
| `naiyue_test_agent/application/io_steps.py` | 51 | 待系统复盘 |
| `naiyue_test_agent/application/session_controls.py` | 150 | 待系统复盘 |
| `naiyue_test_agent/application/session_service.py` | 472 | 失败catch/finally局部、_cleanup_run396–417与_finish_run419–457已讲（Lesson28）；其他主流程待系统复盘 |

### 第6章

当前先按8节安排，沿真实模块职责与分支推进；已讲过的工具回填、批次和停止合同只引用结论，重点展开新职责。

| 节 | 主题 | 要解决的问题 |
|---|---|---|
| 1 | 工具注册、schema与结果 | 普通函数如何成为工具，模型可见参数与ToolRuntime注入如何区分，参数校验/执行/ToolMessage分别由谁处理；先用确定性工具测试，不深入Agent循环 |
| 2 | Read完整分支 | 工作区内读取、分页、字节/行数、编码/非文本、文件变化和可读错误；权限与审批先标边界 |
| 3 | ls/find/grep | 文件发现与正文搜索的区别，忽略规则、边界、输出限制、rg依赖和错误反馈 |
| 4 | write/edit与提交边界 | 创建/替换/精确编辑、快照冲突、目标互斥、临时文件/原子替换、结果未知与核对 |
| 5 | Bash进程与输出 | opt-in/审批、进程组、stdout/stderr消费/截断/归档、超时/停止与副作用未知 |
| 6 | 工具批次、异常与执行反馈 | 工具属性→批次计划、顺序/有界并行、未开始/已开始与结果配对；完整区分参数校验/ToolException转error ToolMessage、普通异常是否由具体工具转换、GraphBubbleUp/取消/副作用结果未知/调度故障继续传播；串起工具实现→BaseTool→ToolNode/middleware→Agent循环与应用结果，配对应确定性测试；复用第五章停止/settle合同 |
| 7 | 权限与工具授权 | 路径规范化/隐藏敏感路径、当前Read allow/ask/deny扩展、其他工具的实际边界；审批与OS隔离分开 |
| 8 | 审批中断与恢复 | 工具声明审批→HITL interrupt→pending状态→批准/拒绝→一次性授权/执行复核→整批恢复及重复/不匹配/失败分支 |

每节优先已有确定性测试，列完整分支与关键字段；复杂异步协调段连续运行后观察。第1节见[Lesson66](lessons/lesson-66.md)，第2节见[Lesson67](lessons/lesson-67.md)，第3节见[Lesson68](lessons/lesson-68.md)，第4节见[Lesson69](lessons/lesson-69.md)，第5节见[Lesson70](lessons/lesson-70.md)，第6节见[Lesson71](lessons/lesson-71.md)，第7节见[Lesson72](lessons/lesson-72.md)，第8节见[Lesson73](lessons/lesson-73.md)。全章职责/主分支已讲，不代表全部细节逐行覆盖；本人调试/独立实现未评估。

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/tool_execution/observation.py` | 165 | 原工具日志/dispatch观察迁入；错误反馈已按085-01统一，见Lesson71版本指引 |
| `naiyue_test_agent/agent_runtime/tool_execution/approval.py` | 60 | 原ObservedHITL迁入，审批行为保持，见Lesson73版本指引 |
| `naiyue_test_agent/agent_runtime/tool_execution/feedback.py` | 45 | 085-01公共ToolError回调；实施不等于用户已掌握 |
| `naiyue_test_agent/agent_runtime/tool_execution/async_batch.py` | 80 | 职责/主分支见[Lesson71](lessons/lesson-71.md) |
| `naiyue_test_agent/tools/permissions.py` | 160 | 原两权限声明模块合并；职责/主分支见[Lesson72](lessons/lesson-72.md)、[Lesson73](lessons/lesson-73.md) |
| `naiyue_test_agent/agent_runtime/tool_execution/batch.py` | 180 | 职责/主分支见[Lesson71](lessons/lesson-71.md) |
| `naiyue_test_agent/tools/settings.py` | 86 | 085-03A/03B冻结配置与安全回退，接Read/Ls/Write/Edit；实现不代表已学 |
| `naiyue_test_agent/tools/registry.py` | 102 | 085-04 ToolSpec与启动登记/输入合同校验；实现不代表已学 |
| `naiyue_test_agent/tools/builtin.py` | 24 | 085-04 CLI/Web共用内置集合；工厂返回Spec，原工具在.tool |
| `naiyue_test_agent/tools/schemas.py` | 16 | 085-03B四工具共用的公开/执行模型派生；实际校验用args_schema，模型导出过滤runtime |
| `naiyue_test_agent/tools/__init__.py` | 19 | 职责/主分支见[Lesson66](lessons/lesson-66.md) |
| `naiyue_test_agent/tools/bash.py` | 295 | 职责/主分支见[Lesson70](lessons/lesson-70.md) |
| `naiyue_test_agent/tools/output.py` | 199 | 职责/主分支见[Lesson70](lessons/lesson-70.md) |
| `naiyue_test_agent/tools/edit.py` | 247 | 职责/主分支见[Lesson69](lessons/lesson-69.md) |
| `naiyue_test_agent/tools/find.py` | 191 | 职责/主分支见[Lesson68](lessons/lesson-68.md) |
| `naiyue_test_agent/tools/grep.py` | 315 | 职责/主分支见[Lesson68](lessons/lesson-68.md) |
| `naiyue_test_agent/tools/ls.py` | 155 | 职责/主分支见[Lesson68](lessons/lesson-68.md)；085-03B额度由settings传入 |
| `naiyue_test_agent/tools/read.py` | 326 | 职责/主分支见[Lesson67](lessons/lesson-67.md)；schema派生改用schemas.py |
| `naiyue_test_agent/tools/write.py` | 203 | 职责/主分支见[Lesson69](lessons/lesson-69.md)；085-03B移除旧env入口 |

### 第7章

下一小节先建立模型上下文组成、来源与装配入口，再按项目指令/Skills、预算、压缩与恢复等机制推进；尚未开展。

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/context_compaction.py` | 458 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_observation.py` | 181 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_recovery.py` | 127 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_summary_io.py` | 39 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/project_prompt.py` | 40 | 待系统复盘 |
| `naiyue_test_agent/application/project_instructions.py` | 56 | 待系统复盘 |
| `naiyue_test_agent/application/skills.py` | 194 | 待系统复盘 |

### 第8章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/application/async_persistent.py` | 79 | 待系统复盘 |
| `naiyue_test_agent/application/cli.py` | 469 | 待系统复盘 |
| `naiyue_test_agent/application/cli_async.py` | 110 | 待系统复盘 |
| `naiyue_test_agent/application/cli_display.py` | 200 | 待系统复盘 |
| `naiyue_test_agent/application/persistent.py`（历史） | 29 | 093已移除，当前入口async_persistent.py |
| `naiyue_test_agent/application/session_persistence.py` | 95 | 待系统复盘 |
| `naiyue_test_agent/application/session_storage.py` | 149 | 待系统复盘 |

### 第9章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `frontend/index.html` | 13 | 待系统复盘 |
| `frontend/package.json` | 27 | 待系统复盘 |
| `frontend/src/App.module.css` | 118 | 待系统复盘 |
| `frontend/src/App.tsx` | 56 | 待系统复盘 |
| `frontend/src/api.ts` | 86 | 待系统复盘 |
| `frontend/src/components/ApprovalPanel.tsx` | 32 | 待系统复盘 |
| `frontend/src/components/Composer.tsx` | 44 | 待系统复盘 |
| `frontend/src/components/Conversation.tsx` | 74 | 待系统复盘 |
| `frontend/src/components/Icons.tsx` | 12 | 待系统复盘 |
| `frontend/src/components/Sidebar.tsx` | 30 | 待系统复盘 |
| `frontend/src/components/ToolCall.tsx` | 20 | 待系统复盘 |
| `frontend/src/events.ts` | 70 | 待系统复盘 |
| `frontend/src/global.css` | 14 | 待系统复盘 |
| `frontend/src/main.tsx` | 6 | 待系统复盘 |
| `frontend/src/sse.ts` | 56 | 待系统复盘 |
| `frontend/src/types.ts` | 71 | 待系统复盘 |
| `frontend/src/useConversation.ts` | 248 | 待系统复盘 |
| `frontend/tsconfig.json` | 15 | 待系统复盘 |
| `frontend/vite.config.ts` | 7 | 待系统复盘 |
| `naiyue_test_agent/web/__init__.py` | 1 | 待系统复盘 |
| `naiyue_test_agent/web/__main__.py` | 65 | 待系统复盘 |
| `naiyue_test_agent/web/app.py` | 109 | 待系统复盘 |
| `naiyue_test_agent/web/auth.py` | 17 | 待系统复盘 |
| `naiyue_test_agent/web/errors.py` | 33 | 待系统复盘 |
| `naiyue_test_agent/web/headers.py` | 23 | 待系统复盘 |
| `naiyue_test_agent/web/run_delivery.py` | 293 | 待系统复盘 |
| `naiyue_test_agent/web/schemas.py` | 49 | 待系统复盘 |
| `naiyue_test_agent/web/serialization.py` | 33 | 待系统复盘 |
| `naiyue_test_agent/web/service.py` | 86 | 待系统复盘 |
| `naiyue_test_agent/web/settings.py` | 52 | 待系统复盘 |

### 第10章

Discount固定业务原型及测试已按用户确认移除，本章改用当前异步运行与验证入口复盘。历史课堂保留，不要求重新系统学习。

## 复现物理行数统计

```python
from pathlib import Path

def count(paths):
    paths = sorted(paths)
    return len(paths), sum(len(p.read_text().splitlines()) for p in paths)

backend = list(Path('naiyue_test_agent').rglob('*.py'))
front = [p for p in Path('frontend/src').rglob('*')
         if p.is_file() and p.suffix in ('.ts', '.tsx', '.css')]
print('backend product:', count(backend))
print('frontend product:', count(p for p in front if '.test.' not in p.name))
print('Python tests:', count(Path('tests').rglob('*.py')))
print('frontend tests:', count(p for p in front if '.test.' in p.name))
print('probe scripts:', count(Path('scripts').rglob('*.py')))
```

从项目根目录执行。这是文档中的统计示例，没有新增产品模块。
