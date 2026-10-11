# 全项目代码学习路线

2026-10-11当前衔接：[Spec081](../specs/081-event-exception-convergence.md)已实施，原reporter/id表回传改为原异常属性标注，运行依赖集中为RunServices。用户明确此前整体未理解，现进入[Lesson38](lessons/lesson-38.md)，先普通模型超时回传与观察隔离，再回第二章stop_reason；以下旧课堂记录保留历史，不作为当前接线说明。理解检查待答，独立实现/追问未评估。

后续接口清理见[Spec082](../specs/082-remove-obsolete-runtime-bridges.md)：旧分项bind/getter、failure_data、两个旧交互builder及workspace调用包装已删除，旧课堂中的相关名字仅作历史。当前入口为中心RunServices及统一交互装配；同步保留项有具体消费者清单，代码规模表仍是当时快照。本次实现不升级用户掌握记录。

状态：2026-10-10。**用户自述第一章已讲内容学会，独立实现与面试追问未评估；第2章errors旧版及Lesson28失败链已讲，整体独立追踪尚未证明。用户批准架构复审方向后，Spec079分层契约/实例扩展已实现（后端962/前端27测试通过），新版核心待学，先按[异常地图](exception-architecture.md)从contracts→model→runtime→application追三条故障链，再回第2章stop_reason。** 第一章见[Lesson18](lessons/lesson-18.md)/[Lesson19](lessons/lesson-19.md)。真实试用/断点/确定性探针作为相应章节的学习素材；下方原代码统计保留2026-10-09盘点，新增模块行数单独列示，不伪造全项目重新测量。

新版异常现从[Lesson29](lessons/lesson-29.md)建立普通模型超时的完整地图；仅展开内部报告/身份复用，分类器算法与其余故障分支待学，不把架构地图定位当作核心代码全部读完。

数据类见[Lesson30](lessons/lesson-30.md)，描述选择见[Lesson31](lessons/lesson-31.md)，分类器签名适配见[Lesson32](lessons/lesson-32.md)，模型目录快照见[Lesson33](lessons/lesson-33.md)，当前[Lesson34](lessons/lesson-34.md)读取RuntimeErrorPolicy构造及普通模型委托，控制/保护/普通运行分支待逐行读。

用户在Lesson34反馈整体链再次迷糊，暂停新分支；先用TimeoutError实际数据流复盘“分类器是函数、描述是产物”及准备/执行区别。此前局部答对不表示整链已掌握。

用户正确区分原异常与描述后说“继续”，[Lesson35](lessons/lesson-35.md)仅推进无效模型响应这一保护分支，按消息→异常→描述→报告/传播读；其他保护/普通运行分支仍未逐行学习。

工具写提交结果不确定/cause与会话核对见[Lesson36](lessons/lesson-36.md)。

现[Lesson37](lessons/lesson-37.md)只追原生异步模型等待中UserStop→图停止结果→stopped终局；外部取消/Graph控制/普通runtime/tool与App未报告回退仍待学。

用户明确要求完整对象/调用/数据/分支图，反馈逐行内容疲劳。当前暂停Lesson38及其余新分支，优先[异常系统总图](exception-architecture.md)的1A节；新图整理是讲解导航，不补记所有分支已学/整链已掌握。后续按用户点名节点深入。

## 代码规模与统计口径

以下来自本次工作区实际文件，包括已提交、未提交和新文件；不是只统计HEAD。物理行按UTF-8文本的`splitlines()`计数，包含空行、注释和文档字符串；非空行只去掉空白行，仍可能含注释，不能称为纯逻辑代码行数。没有运行模型请求。

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
| 1 | 系统地图与启动入口；README、web/__main__.py、web/app.py、application/_assemble_application、agent_runtime/build_runtime_agent只看定位与边界 | 四层如何组织；Web/CLI、框架、项目代码各负责什么；create_agent创建与run/会话的区别；当前业务边界 | 架构图、对象关系、一次请求总路线、接下来阅读地图 |
| 2 | 模型接入；model_access的5个文件 | 配置如何被加载和真正使用；供应商适配/基类、thinking回退、上下文与输出参数、失败分类/结束原因、超时/SDK重试、客户端资源归属 | 一条配置到模型的调用链、默认值/异常表、接入模型的小练习 |
| 3 | Agent循环与middleware；agent_runtime/__init__、invocation_context、async_invocation、resource_invocation，application/__init__ | Human/AI/Tool消息结构；模型怎样请求工具、结果怎样回填；create_agent如何收集hook并构图；reducer/jump_to/handler顺序；同步与异步调用入口 | 真实数据结构与一轮→多轮轨迹、hook时序、框架/项目职责表 |
| 4 | 事件、消息与观察；events、call/message/turn/control/context观察模块，application/run_events、stream_projection | 谁生产/消费事件；run/turn/message/tool标识与序号；delta/完整snapshot/终局；异常和终局的关系；JSON序列化/去重；事件不能替代授权或保存 | 事件生产→交付→投影的数据流、各状态证据、unknown/失败诊断样例 |
| 5 | 会话运行与控制；session_service、session_controls、async_session、async_application、async_event_stream、io_steps及run_control/active_stop/async_utils | session/thread/run归属；busy/队列/开窗关窗；Steering与Follow-up；停止/失败/收尾优先级；async任务归属、断开与取消、IOCall/send/throw、已开始线程IO的settle | 完整状态/信号表、正常/干预/停止/异常路径、并发与取消练习 |
| 6 | 工具执行、调度、权限与审批；tools全部工具、tool_batch/async_tool_batch、tool_approval、file_permissions；复访session_service审批部分 | 工具定义/schema/metadata注册；Runtime.context注入；read分页与路径；ls/find/grep；write/edit原子性；Bash进程/输出；串并行；ToolMessage错误；HITL和一次性授权 | 七工具共同范式与差异、批次顺序、审批→恢复/拒绝链、新增工具练习 |
| 7 | 上下文工程；project_instructions、skills、project_prompt、context_observation、context_compaction、context_summary_io、context_recovery | 系统prompt/AGENTS/CLAUDE/Skill来源和加载时点；近似token/usage/窗口/预留/阈值；安全切点、摘要替换、回退、一次溢出恢复；上下文不等于全部展示历史 | 数字预算例子、切点场景表、压缩/恢复轨迹和失败验证 |
| 8 | 持久化与CLI；session_storage、session_persistence、persistent/async_persistent、cli/cli_async/cli_display；复访SessionService finalize | 元数据与官方checkpoint分别存什么；保存确认与终局顺序；同库独占；安全恢复/受限状态；CLI线程桥与异步应用；哪些状态没持久化 | SQLite/checkpoint关系图、重启/保存失败案例、CLI完整入口复盘 |
| 9 | Web与前端；web全部文件、frontend全部产品代码及构建入口 | lifespan/REST/schema/本地访问边界；生产任务和SSE连接分离；回执/游标/重同步；TS类型、JSX、React state/ref/effect、API/fetch/SSE；聊天/工具/审批投影、模型选择、草稿与操作竞态 | 一次浏览器操作全链路、请求/事件/界面状态表、前端小改动 |
| 10 | 测试策略、验证与全链路复盘；test_agent/discount_case_design、代表性tests/scripts；各入口复访 | 早期结构化用例样例与当前通用Agent主入口的关系；假模型/真实集成/业务评测证据区别；怎样复现/定位；扩展模型/middleware/工具/评测；哪些长期能力尚未做 | 独立追踪/小实现/故障排查/面试连续追问与真实贡献清单 |

第1章只建立地图，不一口气解释所有启动依赖。第一次出现尚未学习的模块时先讲输入/输出与职责，详细实现登记为后续阅读项；不能把一次定位算作该文件已经通读。长文件按机制分段、跨章复访，例如运行装配中的控制/压缩接线和SessionService中的审批/finalize。

**第一小课（已开始）：** 从 `python -m naiyue_test_agent.web` 的入口画到模型目录、SessionService、create_agent及SQLite的装配图，区分启动、会话、运行。先定位装配依赖，不把地图定位记为通读；接着逐段读入口核心代码。

## 每个小课怎么学

1. 先给一个具体任务及当时的真实数据结构，用示意数据明确标注，不能让用户猜变量形状。
2. 给入口与最小调用图，追踪生产者→消费者、参数/状态/返回值/副作用，说明正常与异常路径。
3. 逐段逐行讲项目核心代码；常规Python基础/import可合并，复杂推导式/闭包/装饰器/泛型/send-throw以及TS/React机制展开成普通写法和例子。
4. 框架核心问题追到本机实际版本的必要源码：create_agent图与middleware收集、状态reducer、ToolNode、HITL/Command、checkpoint，以及API异步/流式与真实IO差异。不无目的通读整个第三方包。
5. 配套读一到两条相关测试，说明变量由谁赋值、断言究竟证明什么；必要时用断点/有界离线探针，不自动发收费请求。
6. 用一个短复述、独立小改动或故障假设检查；用户说懂了记自述理解，不自动升级独立实现/面试追问。用户不想答时不强迫。
7. 用课堂笔记保存当前读到哪一函数/代码段、仍有疑问的行、待复访点、下一小课；说“继续”接当前位置，不重启整章或跳过未读核心部分。

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
- 业务/集成：test_discount_case_design；probe_async_application、probe_active_stop、probe_web、probe_web_tools的离线方式优先；真实供应商探针必须区分收费调用和业务评测。

## 文件阅读清单

下表由本次源码清单逐文件建立，每个文件只指定一个主章节，跨章机制复访按课堂进度登记。

### 第1章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/__init__.py` | 1 | 待系统复盘 |
| `naiyue_test_agent/contracts/__init__.py` | 1 | Spec079新增基础契约包，待学 |
| `naiyue_test_agent/contracts/failures.py` | 101 | Spec079共享描述/分类与签名适配，待学 |

### 第2章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/model_access/__init__.py` | 318 | 加载/构造/目录已讲（Lesson20–24）；bind_model_output_limit待第7章；独立实现/追问未评估 |
| `naiyue_test_agent/model_access/catalog_lifecycle.py` | 67 | 准备/登记/关闭核心已讲（Lesson25）；独立实现/追问未评估 |
| `naiyue_test_agent/model_access/errors.py` | 89 | Lesson26讲旧版；Spec079框架ModelError/describe新增与别名变更待学，独立实现/追问未评估 |
| `naiyue_test_agent/model_access/stop_reason.py` | 82 | 待系统复盘 |
| `naiyue_test_agent/model_access/thinking.py` | 63 | 核心已讲解：Lesson23；回退检查题正确，独立实现/追问未评估 |

### 第3章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/__init__.py` | 363 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/async_invocation.py` | 36 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/invocation_context.py` | 30 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/resource_invocation.py` | 60 | 待系统复盘 |
| `naiyue_test_agent/application/__init__.py` | 104 | 待系统复盘 |

### 第4章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/async_message_observation.py` | 133 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/call_observation.py` | 410 | failure_data263–272已讲（Lesson27）；模型失败生产/消费局部见Lesson26，其余待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_events.py` | 90 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_operation_observation.py` | 211 | 待系统复盘 |
| `naiyue_test_agent/contracts/session_events.py` | — | 原control_events转发模块已按Spec082删除，控制/审批数据从当前契约文件阅读；行数未纳入旧快照重算 |
| `naiyue_test_agent/agent_runtime/control_observation.py` | 189 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/events.py` | 428 | safe_error_type/RunFailureData24–45已讲（Lesson27）；其余待系统复盘 |
| `naiyue_test_agent/agent_runtime/errors.py` | 114 | Spec079运行层异常目录/策略与报告端口，待学 |
| `naiyue_test_agent/agent_runtime/message_observation.py` | 203 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/stream_events.py` | 23 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/turn_events.py` | 84 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/turn_observation.py` | 220 | 待系统复盘 |
| `naiyue_test_agent/application/run_events.py` | 170 | RunObservation初始化关键字段/failed123–148、finish163–170已讲（Lesson28）；cancelled及订阅/日志等其他部分待读 |
| `naiyue_test_agent/application/errors.py` | 17 | Spec079应用错误目录，待学；旧导入保留同一类型 |
| `naiyue_test_agent/application/failure_policy.py` | 51 | Spec079应用映射/try_describe，待学 |
| `naiyue_test_agent/application/stream_projection.py` | 57 | 待系统复盘 |

### 第5章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/active_stop.py` | 60 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/async_utils.py` | 46 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/run_control.py` | 80 | 待系统复盘 |
| `naiyue_test_agent/application/async_application.py` | 175 | 待系统复盘 |
| `naiyue_test_agent/application/async_event_stream.py` | 76 | 待系统复盘 |
| `naiyue_test_agent/application/async_session.py` | 88 | 待系统复盘 |
| `naiyue_test_agent/application/io_steps.py` | 51 | 待系统复盘 |
| `naiyue_test_agent/application/session_controls.py` | 167 | 待系统复盘 |
| `naiyue_test_agent/application/session_service.py` | 457 | 失败catch/finally局部、_cleanup_run396–417与_finish_run419–457已讲（Lesson28）；其他主流程待系统复盘 |

### 第6章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/async_tool_batch.py` | 80 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/file_permissions.py` | 72 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tool_approval.py` | 93 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tool_batch.py` | 192 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/__init__.py` | 16 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/bash.py` | 296 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/bash_output.py` | 199 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/edit.py` | 267 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/find.py` | 221 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/grep.py` | 350 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/ls.py` | 186 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/read.py` | 357 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/tools/write.py` | 239 | 待系统复盘 |

### 第7章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/agent_runtime/context_compaction.py` | 457 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_observation.py` | 181 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_recovery.py` | 125 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/context_summary_io.py` | 39 | 待系统复盘 |
| `naiyue_test_agent/agent_runtime/project_prompt.py` | 40 | 待系统复盘 |
| `naiyue_test_agent/application/project_instructions.py` | 56 | 待系统复盘 |
| `naiyue_test_agent/application/skills.py` | 194 | 待系统复盘 |

### 第8章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/application/async_persistent.py` | 77 | 待系统复盘 |
| `naiyue_test_agent/application/cli.py` | 476 | 待系统复盘 |
| `naiyue_test_agent/application/cli_async.py` | 110 | 待系统复盘 |
| `naiyue_test_agent/application/cli_display.py` | 200 | 待系统复盘 |
| `naiyue_test_agent/application/persistent.py` | 27 | 待系统复盘 |
| `naiyue_test_agent/application/session_persistence.py` | 95 | 待系统复盘 |
| `naiyue_test_agent/application/session_storage.py` | 150 | 待系统复盘 |

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
| `naiyue_test_agent/web/__main__.py` | 63 | 待系统复盘 |
| `naiyue_test_agent/web/app.py` | 114 | 待系统复盘 |
| `naiyue_test_agent/web/auth.py` | 17 | 待系统复盘 |
| `naiyue_test_agent/web/errors.py` | 33 | 待系统复盘 |
| `naiyue_test_agent/web/headers.py` | 23 | 待系统复盘 |
| `naiyue_test_agent/web/run_delivery.py` | 271 | 待系统复盘 |
| `naiyue_test_agent/web/schemas.py` | 49 | 待系统复盘 |
| `naiyue_test_agent/web/serialization.py` | 33 | 待系统复盘 |
| `naiyue_test_agent/web/service.py` | 86 | 待系统复盘 |
| `naiyue_test_agent/web/settings.py` | 50 | 待系统复盘 |

### 第10章

| 文件 | 物理行数 | 计划阅读状态 |
|---|---:|---|
| `naiyue_test_agent/test_agent/__init__.py` | 1 | 待系统复盘 |
| `naiyue_test_agent/test_agent/discount_case_design.py` | 96 | 待系统复盘 |

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
