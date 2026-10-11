# 后续开发与补充清单

更新日期：2026-10-09。这里集中记录**尚未实现或尚未验证**的工作；它不是半个月首期的交付承诺。开始某一项前先读当前代码与对应 Spec，以一个小切片重新确定范围和验收，按 TDD 推进。完成时记录代码入口、验证命令和实际结果，再从本清单移至交接记录；不能只因文件存在或测试全绿就勾销真实服务与业务效果验证。

总体顺序与层级见 [实施路线](implementation-roadmap.md)，已经做过的证据见 [交接记录](handoff.md)。本清单聚焦遗留事项，历史讨论不自动转成已批准需求。

最小 Web 已完成 [Spec077](specs/077-local-web-chat.md) 聊天基础及 [Spec078](specs/078-web-tools-and-approval.md)工作区工具/工具详情/逐项决定整批审批，沿用原权限和应用恢复接口。使用见 [Web说明](web-chat.md)、[工具说明](web-tools.md)。唯一下一步：先实际试用 Web 工具/审批闭环，以一次 read→审批→恢复→保存/显示作为系统代码学习入口；不自动扩大长期会话或权限工程。

Web 遗留：①更多真实供应商工具/审批及浏览器模型失败、部分写入、停止/保存故障矩阵，目前Web新增闭环证据是原生假模型加真实工具、SQLite和TCP；②安全 checkpoint 以外的完整历史/受限恢复，不能盲目重放副作用；③草稿与未执行队列当前只在内存，浏览器刷新/服务重启持久化另设计；④16 个已完成 run/256 事件/1MiB view 及 4096 条回执的长期资源与持久幂等策略，达到界限明确反馈；⑤多用户身份/会话归属、凭证/租户/工具隔离与共享调度/部署，不直接多 worker；⑥多模态/大消息压力/可访问性与浏览器矩阵。每项须独立 Spec 和可复现证据，不预建占位模块。

最新里程碑（Spec076）：统一request_stop/active事件，CLI薄桥接AsyncApplication/AsyncSqliteSaver；原生模型/摘要/SDK等待取消、工具排队与收尾、Bash进程终止及safe checkpoint恢复已接通。全量905例通过，真实默认网关一次逻辑调用取消通过；下一步最小Web与整体学习。下面Spec073–075叙述保留历史时点，当前API及限制以Spec076/README为准。

剩余异步/流式边界：自定义不响应信号工具的停止上界、更多供应商实网取消/部分输出故障、传输重连/客户端状态同步、CPU长上下文延迟、多模态/非文本delta和大payload/多消费者压力。当前工具默认settle，Bash信号作用于进程组，不保证逃离进程组的守护程序或远端副作用已终止；SDK服务端计算/计费不可据客户端取消证明。队列按事件数有界、overflow显式且生产继续；aget_messages仍非全历史。当前CLI待发送内容不跨退出持久保存，行输入桥不提供字符打印或Ctrl-C直接取消。

## 下一阶段：模型接入与运行边界

| ID | 待做或待补证据 | 当前边界与完成判据 |
| --- | --- | --- |
| M1 | 模型错误分类扩展与真实服务验证 | [Spec 006](specs/006-model-call-failure-event.md)已有同步失败事件；[Spec 008](specs/008-openai-authentication-failure.md)验证 OpenAI SDK 认证失败及当前网关无效凭证 401；[Spec 009](specs/009-non-openai-model-errors.md)实现 Anthropic/Ollama 稳定粗分类；[Spec 010](specs/010-model-request-policy.md)补 OpenAI SDK 超时/连接失败。[Spec 021](specs/021-unknown-model-observation.md)为同步路径未分类异常补安全异常类型及可信 SDK HTTP 状态，但日志尚未持久化或接入评测。其他 SDK/网关错误、歧义状态及真实 Anthropic/Ollama 服务行为仍待逐类取证；继续验证脱敏、原异常传播和重试边界。八类设计基线见[项目计划第 16 节](project-plan.md#16-模型层-thinking-与失败分类设计)。 |
| M2 | 异步、流式、取消及部分输出失败 | [Spec073](specs/073-async-model-call-middleware.md)已有无工具runtime的原生异步模型等待、响应门禁、内存checkpoint与取消异常传播测试，以及一次有界真实模型成功请求。[Spec074](specs/074-async-context-model-chain.md)随后接通prompt/摘要/一次恢复与状态流，新增34例、全量846例及真实摘要→回答通过。[Spec075](specs/075-integrated-async-application.md)已接应用/工具/存储和文本delta/完整校准，36例新增及882例、真实跨进程正常链路通过；活动取消、真实部分输出失败、SDK重试/副作用unknown和生产负载仍未验，不能把安全排空当作主动停止。 |
| M3 | 超时与有限重试 | [Spec 010](specs/010-model-request-policy.md)已对在线模型显式设置默认 60 秒 HTTP 超时与 2 次 SDK 重试，对 Ollama 设置 60 秒 HTTP 超时；本机 HTTP mock 验证在线 SDK 对 503 最多尝试 3 次。尚无整个 Agent turn 的严格墙钟预算、尝试级轨迹、取消及流式部分输出后的安全重试策略；Ollama 重试待本地模型可用后单独设计。终局失败不能变成普通 `AIMessage`。2026-10-10用户要求记录后续考虑项目侧外部重试：先比较保留SDK重试并补尝试观测，与由项目控制单次模型请求重试两种方案，尚未决定替换。若采用外部重试，需避免与SDK重试叠加，明确总请求次数/时间预算、可重试条件和取消传播；记录关联call_id、attempt序号、耗时、安全分类及退避，不记录凭据或原始请求/响应。验收至少覆盖首次成功、重试后成功、耗尽失败、不可重试直接失败、等待中取消及总次数约束；流式已输出内容、工具副作用和整个Agent run不能未经设计直接重放。 |
| M4 | 推理等级真实能力 | [Spec 005](specs/005-model-access-thinking.md)只证明配置、回退及本机适配器参数；HeiyuCode、OpenAI 官方、Claude、Ollama 的具体模型对所选等级的接受与实际效果未验证。能力信息按具体供应商/模型核验；不能把本地 `supported_levels` 当成远端事实。 |
| M5 | 每次调用可选的 thinking 等级 | 当前只在加载模型配置时确定默认等级。同会话下一轮修改等级的提交时点、成本边界与轨迹仍需设计及测试；保留 `model_default` 与显式 `off` 的区别。 |
| M6 | 提示词缓存控制与效果对照 | [Spec 007](specs/007-prompt-cache-usage-observation.md) 已记录同步模型成功调用的缓存读取量；HeiyuCode 两次合成请求中第二次报告 `cache_read_tokens=6912`，但尚未设置供应商专用缓存策略，也没有延迟、成本或长期命中率对照。控制参数须按供应商协议验证，不能把缺失统计当成零或把 LangChain 整体响应缓存混进来。 |
| M7 | 模型发现与更多真实供应商接入 | 当前列表来自 `.env/models.json`。模型 ID/能力自动发现要逐供应商核验；OpenAI 官方、Claude、本地 Ollama 目前只有本机构造/假模型证据，需真实模型→工具→模型与错误路径验证。用户目前没有本地模型，Ollama 实网验收暂缓，不将其作为当前切片的前置条件。 |

### Windows模型配置私有权限校验（2026-10-10）

用户已明确“先学习，双系统适配后面再做”；当前不启动兼容改造，具体平台契约进入该切片时再讨论。

- 当前model_access/__init__.py的S_IMODE(st_mode)&0077是POSIX权限位规则。CPython3.12.14原生Windows将常规文件只读属性映射为0444/0666，现有条件都会拒绝；chmod0600只能改变只读属性，不能建立仅当前用户可读的ACL。这是官方源码/文档结论，不是Windows实测，也不能据本机macOS通过宣称跨平台支持。
- 后续若明确支持Windows，单独设计平台权限校验/私有凭据存储契约，保持防止非授权用户读取凭据的目标，不直接跳过检查。完成判据：真实Windows环境覆盖仅允许预期主体、其他用户/广泛组可读、ACL继承/拒绝及不可确定权限的处理，证明正文读取前决策正确，并保留POSIX回归；与整个应用其他Windows依赖另行验收。
- 来源与课堂依据见[Lesson20](1.0/lessons/lesson-20.md)。当前仅记录，未修改产品或环境配置。

## 最小 CLI 之后、通用工具之前：运行控制

上下文异步最新边界：[Spec074](specs/074-async-context-model-chain.md)已完成project prompt、主动摘要与一次输入溢出恢复的异步模型IO，共用原算法，values/updates状态流与内存checkpoint通过。全量846例，一次真实两逻辑调用保留随机合成事实；唯一下一步工具wrapper/HITL观察、异步批次和IO，之后应用/存储/完整观察与文本delta。CPU序列化/token估算、回调/订阅仍同步，长上下文下事件循环延迟需代表性验证。

本机框架取消限制：独立模型子任务自行抛CancelledError、不伴随顶层Task.cancel时，当前BaseChatModel.agenerate的gather结果按Exception区分，随后读生成属性得到AttributeError。本片没有把这个错误猜测还原为取消；已验证的是外部Task.cancel的摘要/重发传播。后续需固定样例核对实际SDK内部取消、重试等待与应用清理，先评估上游修复/版本兼容再独立决策，不在此片升级依赖。raw httpx.ConnectError仍unknown、不给临时回退，新增测试锁定当前策略；SDK已适配连接异常正常回退。正式异步取消时，压缩已准备但未提交的状态观察、终局和副作用未知清理仍须接应用消费者验收。

异步最新边界：[Spec073](specs/073-async-model-call-middleware.md)已补选模门禁和请求观测的双路径，无工具底层runtime可ainvoke，14例新增/全量812例与一次有界真实模型通过。后续依次补项目prompt/摘要/溢出恢复、工具调度/IO/审批、异步应用与持久化、增量/传输。现有应用和CLI仍同步，SQLite saver及余下wrap未适配；下面早期缺awrap探针为迁移前证据。模型Task取消测试不等于CLI/Web活动停止、SDK重试或工具副作用取消，R3待办保持。

具体差距和证据见 [Pi loop 对照](pi-loop-gap-analysis.md)。这里记录顺序，不把三个能力视为一个完成项。

| ID | 待做或待补证据 | 当前边界与完成判据 |
| --- | --- | --- |
| R1 | 运行终局与异步控制基础 | [Spec 018](specs/018-model-stop-reason-and-truncated-tool-calls.md)已在同步路径统一可识别的模型结束原因，并用假模型验证明确截断的工具调用只回填错误结果、不执行工具；[Spec 020](specs/020-model-response-rejection-before-tool-routing.md)已让明确 `error` 消息走 `run_failed`。[Spec 024](specs/024-stop-after-current-turn.md)已让同步统一应用在当前 turn 后产生独立 `run_stopped`。真实供应商截断/失败/停止未验证；普通文本截断与 `unknown` 的运行语义仍待决定。正式异步/流式运行已按Spec075接线，活动取消仍未接线；迁移前同步wrapper曾在本地 `ainvoke` 探针中抛 `NotImplementedError`；Spec073已补齐低层选模与请求观测，Spec074再补上下文模型链；Spec075已接工具事件/批次、审批、应用与存储；活动取消和压力/更多真实供应商证据仍待补。有副作用工具上线前须确定 `unknown` 的拒绝策略。调研证据见[同步与异步调用记录](research/langchain-langgraph-sync-async-2026-09-30.md)。 |
| R2 | steering 与 follow-up 的后续验证 | [Spec 022](specs/022-synchronous-steering.md)与[Spec 023](specs/023-synchronous-follow-up.md)已在同进程同步路径实现两条独立队列：Steering 在下一次模型调用前交付，Follow-up 在自然结束时逐条交付；假模型验证优先级、FIFO、工具期间等待、关窗竞争、失败清理、递归上限及同一 run 关联。交互式 CLI 入口已用假模型事件测试和一次真实网关运行中输入验证；仍需跨进程/重启持久交付、更复杂工具/取消边界，以及 Web 入口；特别要验证消息已从队列取出后运行失败时 checkpoint 保留何种部分状态，以及下一次运行是否需要显式恢复或去重。完成时分别提供接线、权限和失败路径证据。 |
| R3 | 活动停止的扩展边界 | [Spec076](specs/076-active-stop.md)已覆盖run私有信号、模型/摘要/重试等待取消、工具排队/settle、Bash进程组终止、审批竞态、CLI保留及显式重发、safe stopped跨进程恢复；旧after-turn公开接口已迁移。剩余：自定义工具协作取消/停机上界、更多供应商真实中断、远端/逃离进程组进程与副作用核对、退出后待发送内容恢复、Ctrl-C/服务shutdown统一政策。当前默认同步/未知工具settle，不能以关闭流或HITL中断当成取消，也不保证服务端停止计费。 |
| R4 | 核对供应商结束值覆盖和语义 | [Spec 019](specs/019-claude-context-truncation-and-tool-call-binding.md) 已用确定性测试验证 Claude `model_context_window_exceeded → length`，并在工具 hook 对 ID 不一致或无最近模型消息的请求拒绝执行；尚无真实 Claude 截断响应。Pi `a69bef789` 也将 Claude `pause_turn` 映射为 `stop`；当前项目只接客户端工具，暂不增加通用续传。将来接 Claude 服务器工具前，须保存完整响应内容并按官方契约续传，单改枚举值不够。`sensitive`、OpenAI `network_error`/`end` 尚缺已核对官方结束值依据；未知/缺失元数据策略仍未收敛，通用副作用工具上线前需确定。[Spec 021](specs/021-unknown-model-observation.md)已为同步响应型 `unknown` 补独立结构化日志，含字段来源、受限原生原因码与工具调用数量；仍无持久化聚合、真实未知供应商样本或未知原因工具执行策略。详见 [Lesson 10](lessons/lesson-10.md)。 |
| R5 | 模型失败消息与运行终局 | [2026-10-01 调研](research/loop-termination-retry-cancel-2026-10-01.md)发现的明确错误/无效调用漏洞已按 [Spec 020](specs/020-model-response-rejection-before-tool-routing.md) 以 TDD 收敛：同步假模型路径在路由前拒绝，混合有效/无效调用整条拒绝，`run_failed` 且无工具执行。还须决定 `unknown`、普通文本 `length`、失败后 checkpoint 保留用户输入的契约，并获取真实供应商失败响应证据。在线 SDK 单次请求内部最多 2 次重试，不替代运行终局或用户取消；活动取消需先验证异步与重试等待的取消传播。 |
| R6 | 工具并发与串行策略 | [Spec 037](specs/037-conditional-tool-batch-scheduling.md)已把同步逐批调度接入运行层：工具缺省并行；任一已注册工具标记 `metadata['naiyue_execution_mode']='sequential'` 时，整批按模型调用 ID 顺序进入完整工具链。合成工具测试已覆盖纯读重叠、混合顺序、可恢复/致命错误、HITL 批准与拒绝、有限并发及同 Agent 双会话；全量 `312 passed`。后续 [Spec 038](specs/038-workspace-write-tool-design.md)已把真实 `write` 声明为 sequential，并用临时真实文件、假模型验证同轮读写读顺序、同目标双会话冲突、不同目标独立及结果未知后会话阻断；现已按 [Spec 039](specs/039-workspace-edit-tool.md)接入真实 `edit` 并验证 read→edit→read 顺序；仍无真实供应商混合请求或跨进程同目标保护。工作区外写审批须先有内容审查与授权绑定；Spec075已验证异步批次、有限并发与错误清理；持久checkpoint部分执行恢复/去重和更高并发压力另片验证。Pi `a69bef789` 的批次模式是设计参照，但其内置 `write/edit` 在核对版本没有显式 sequential 声明。 |
| R7 | 运行层旧装配入口收敛（本机已完成） | 用户授权后按[Spec082](specs/082-remove-obsolete-runtime-bridges.md)删除2个旧交互builder、3个workspace同步包装和旧自然结束回调分支；分项bind/getter及failure_data也移除，测试/探针已迁移。`build_runtime_agent`、`build_configured_runtime_agent`以及同步应用/持久会话仍有具体策略/探针消费者，详见Spec第3节；如后续统一这些消费者到异步，须同单元删除对应旧路径。最新后端1038/前端27与构建通过，旧入口恢复由新增回归阻止。 |
| R8 | 统一事件与消息生命周期 | [Spec 062](specs/062-application-run-event-contract.md)接 run，[Spec 063](specs/063-model-tool-event-integration.md)接模型/工具，[Spec 064](specs/064-complete-message-observation.md)接消息，[Spec 065](specs/065-turn-lifecycle.md)接 turn，[Spec 066](specs/066-control-request-events.md)接控制；[Spec 067](specs/067-context-operation-events.md)已接压缩/恢复四 type，本地事件实现723例通过，CLI消费按[Spec 068](specs/068-cli-event-display.md)已接线并回归。算法/原结果/异常/调度保持；前三次失败停止后，用户授权独立诊断，本轮真实 compressed 状态确认/四事实4/4通过，间歇性超时根因仍未知。CLI 实时完整消息/工具/控制/上下文/终局显示已接，审批详情仍取原结果；异常和结果只补缺，状态可读/模型门禁/HITL 生效与持久确认分开；模型用量非 SDK/摘要总费用。慢同步消费者、私有 HITL 版本兼容、控制/发布故障交付、持久悬挂运行和活动取消另验收；Spec075已有异步流/元数据、窗口控制与有界交付故障验证。 |

### R8 后续：消息状态与 turn（2026-10-08）

无网络十场景研究见 [消息状态时点](research/message-state-boundaries-2026-10-08.md)，[Spec 064](specs/064-complete-message-observation.md)已按用户确认先完成消息观察。历史去重、同 ID 修订、summary/移除、正常 graph_values 与失败 recovery_view、非 JSON 投影与日志边界已有本地测试；一次真实随机工具循环确认 4 条消息内容/ID 配对。尚未覆盖所有供应商 extra/reasoning、多模态、远端内容授权/脱敏、生产压力或持久交付。

turn 已按 [Spec 065](specs/065-turn-lifecycle.md)接线：响应与对应工具结果、后置检查均满足才 completed；审批暂停保存有限 JSON 延续，恢复沿用原 ID/origin，失败后重试新 ID。测试覆盖拒绝/人工结构化结果、fatal pending、停止、失败准备阶段和别名隔离；一次真实模型验证两个 turn 与四条消息配对。不能把 JSON 可序列化当作持久会话或重放安全证明。

控制请求已按 [Spec 066](specs/066-control-request-events.md)接线，启动窗口/延迟通知/旧新 run 重叠、失败清理、停止优先、审批恢复/跳过和消费者故障反例通过；真实合成 Steering 交付有界验证。保留私有 HITL helper 升级兼容性测试，不把新控制事件称为供应商读取、工具实际执行、持久日志或取消证明。

上下文已按 [Spec 067](specs/067-context-operation-events.md)收敛四 type 并接线，本地反例覆盖阈值/切模型/摘要回退/强制恢复/门禁拒绝/替换未提交、跨会话和消费者故障。预算/切点保留原日志，不继续扩展公开类别。正式状态确认和摘要统计已接；本轮原标准探针真实 compressed 验收通过，但真实明确溢出恢复及其他供应商仍未由此验证。

**独立待办：摘要真实调用间歇性长耗时。** 前三次 compressed 失败后先停止：首次原因未捕获；第二次探针预算不足已用回归修正；第三次摘要阶段60秒超时。用户随后授权独立排查，见[诊断记录](research/summary-request-latency-2026-10-08.md)：临时安全 HTTP 观测未复现超时，两条逻辑请求各一次返回200；原标准120秒探针两组returned，compressed 42.026秒、摘要17.964秒、applied状态与summary ID对应、四事实4/4。真实接线验收缺口已关闭，历史超时根因仍未知，不算修复。再次复现时须取得同一失败请求的 HTTP 开始/返回/异常/重试时序，区分网络、远端处理与本地解析/回调；完成判据是能解释失败请求的耗时来源，若需修复则有确定性回归及有界真实复验。未改 SDK 默认超时/重试，原超时请求实际用量仍未知；不以持续重复请求代替定位。

CLI已按[Spec 068](specs/068-cli-event-display.md)消费同一事件接口，独立有界真实模型验证两条回答、工具前可见/后续状态、Steering及唯一终局；既有日志不驱动终端双输出。仍无逐token、输入光标重绘、慢stdout隔离/背压、原生异步和活动取消；这些能力在正式异步/界面阶段另验收。开发主线下一步是最小持久会话，先确定应用元数据与框架checkpoint分别保存/恢复的契约。

### R3 补充：活动取消在异步阶段实施（2026-10-08）

用户要求明确保留此待办。当前 CLI `:stop` 只请求当前模型响应/已发出工具批次结束后停止，不取消正在等待的模型请求、SDK 重试或 Bash 子进程。完整活动取消在正式异步运行阶段独立设计和验收；流式展示不是取消的技术前提，也不能用关闭事件流或取消外层等待证明底层停止。

完成判据：控制请求被接受与实际 cancelled 终局分开；验证模型/摘要请求、SDK 重试等待、顺序批次等待及工具任务的传播，Bash 子进程及其相关进程的退出/清理范围，部分输出和已执行/未执行/副作用未知状态，checkpoint 留存与后续恢复；取消期间出现运行错误时保留真实失败原因。保留现有 after_turn 停止为单独模式，不因新增取消改变它的已验证语义。

## 核心业务闭环前必须逐片完成

最小持久会话已按[Spec070](specs/070-persistent-session-implementation.md)实施：SQLite＋官方checkpoint，CLI默认持久、--no-session临时、--resume选择/直达。781例与独立进程/有界真实延续通过，受限状态阻断和保存失败反例已验证。当前只恢复安全结束的会话；不重复调研已决定的存储方向，下一阶段按用户顺序复盘工具/上下文。

| ID | 待做或待补证据 | 当前边界与完成判据 |
| --- | --- | --- |
| C1 | 连续会话与实际切换 | [Spec 011](specs/011-in-memory-agent-continuation.md)已有内存 checkpointer；[Spec 014](specs/014-in-memory-session-service.md)已用应用服务和假模型验证同进程历史延续、A→B 切换、不同会话隔离、同会话 busy 拦截及失败释放。[Spec070](specs/070-persistent-session-implementation.md)已补本地安全结束会话跨进程恢复、持久选模与工作区、状态/确认checkpoint一致性和默认CLI；真实默认模型两进程延续通过。仍无可信Web身份/归属授权、真实跨供应商历史兼容、完整持久轨迹/历史树；opaque `session_id` 不能当权限凭证。[架构草案](agent-runtime-architecture-draft.md) |
| C2 | 通用受限文件工具 | [Spec 027](specs/027-workspace-read-tool.md)已完成首个只读 `read`；[Spec 028](specs/028-read-file-pagination.md)已增加 `offset/limit` 按行续读，单页**文件正文**最多 64 KiB/2000 行，续读提示另计；[Spec 029](specs/029-session-workspace-read-boundary.md)和[Spec 030](specs/030-read-permission-and-approval.md)已完成可信工作区与同进程 Read 审批边界。[Spec 033](specs/033-ls-tool.md)已实现 `ls`：默认当前工作区、排序/目录标记、500 条/50 KiB 限制、工作区外审批，按用户选择显示全部名称（含 dotfiles），但不放宽 Read 内容权限。用户确认超长单行由 Read 返回错误，不做行内游标；Bash 已接入，但超长单行时提示模型改用 Bash 的实际回退路径仍未验证。跨页文件变更没有快照、深页需要从文件开头重新扫描，read/ls/find/grep 已在临时工作区用已配置的真实模型完成合成样本调用并核对结果与原调用 ID；仍没有稳定性或答案质量评测；路径检查与打开之间仍有 TOCTOU 窗口，不能宣称沙箱。`ls` 的条目上限不限制目录扫描/排序成本，极大目录性能待代表性样本评估。[Spec 034](specs/034-find-and-grep-tools.md)已完成 `find` 与 `grep` 的独立工具、CLI 接线、`.gitignore` 文件发现、正文权限过滤及同进程审批；`rg` 为显式运行前提，缺失时返回工具错误，极大目录内存峰值与打包环境可用性仍待验证。已按 [Spec 035](specs/035-read-tool-rename.md)将工具完整改名为 `read`；同步批次调度已按 [Spec 037](specs/037-conditional-tool-batch-scheduling.md)接线；首个真实 `write` 已按 [Spec 038](specs/038-workspace-write-tool-design.md)在 CLI 接线：工作区内 UTF-8 整文件创建/覆盖、现有父目录、1 MiB 默认上限、目标级进程内冲突和临时文件替换；假模型与真实临时文件测试已验证结果回填和读写读顺序，临时工作区已有一次真实模型 write→edit→read 成功链路，业务写入质量仍未验证。现已按 [Spec 039](specs/039-workspace-edit-tool.md)接入工作区内现有 UTF-8 文件的精确多处替换，验证快照变化冲突、原 ID 结果回填与 CLI 接线；Pi 的模糊匹配、完整 diff 展示尚未实现；Bash 已由后续切片实施。工作区外可审批写入需先有可审查内容/差异并绑定授权的界面，不能复用只读的仅路径审批。每种新工具各自定义权限动作及执行校验。用户提到的 `base` 是否为命令工具需在该切片确认。不要把“设计用例”“生成 pytest”做成固定流程工具。 |
| C3 | `discount_system` 单场景产物与执行 | [Spec 002](specs/002-discount-policy-first-scenario.md)已确认公开契约，[Spec 003](specs/003-discount-case-design.md)仅有假模型结构化实验。需让模型读取证据、写 Markdown 用例和自动化代码，分别校验内容与可执行性；受控调用 SUT 后保留请求/响应、断言、唯一 `request_id` 和 DELETE 清理证据。 |
| C4 | 首次执行生成代码前的安全边界 | 未有可宣称的沙箱。用户确认先在单用户本地 CLI 中显式启用审批式宿主机 Bash，作为开发实验；它能访问当前用户可访问的工作区外文件和网络，不满足自动/无人值守、Web/SaaS 或生产测试执行的隔离门槛。上述场景仍须先验证目标白名单、方法/路径/文件权限、运行预算、审批与 OS 隔离；审批不等于隔离。用户停止请求须阻止后续调度并标记已开始工具副作用的未知状态，不能声称自动回滚。 |
| C5 | 关联轨迹与基础评测 | [Spec 013](specs/013-synchronous-run-events.md)与[Spec 015](specs/015-synchronous-tool-events.md)已在同步入口用结构化日志关联 run 开始/终局、选模、模型用量/失败及工具开始/完成/失败；旧路径未统一接入，也未配置日志消费者。下一切片须设计安全的事件消费与交付边界，区分日志诊断、Web 实时流、持久轨迹；若需要跨并发工具严格排序，再设计 per-run 序号与背压。未来还需检索/上下文/产物及取消事件和独立结果/过程评分器。以固定样本报告成功和失败案例，区分调用成功、用例质量和业务缺陷检出。 |
| C6 | 应用服务与本地 Web | [Spec 016](specs/016-local-application-composition.md) 已有进程内统一装配，并用假模型验证两轮历史与事件；[Spec 017](specs/017-local-cli-entry.md) 的同步 CLI 已调用同一服务，真实模型验证两轮合成文本输入，但不含测试工具或业务能力。尚无 Web 入口或身份认证。未来本地 Web 应调用同一服务，展示真实会话、模型选择、轨迹与测试结果；上线前验证回环监听、授权、浏览器来源和文件操作边界，不用假数据冒充运行结果。 |
| C7 | RPC 接口测试扩展 | 首个受控样本是 HTTP 折扣接口。HTTP 闭环稳定后，再选一条可复现 RPC 契约，验证协议客户端、超时、错误码、断言与清理；不能把 HTTP 通过写成 RPC 已支持。 |
| C8 | 权限与审批的后续扩展 | [Spec 029](specs/029-session-workspace-read-boundary.md)已将可信工作区按 run 注入；[Spec 030](specs/030-read-permission-and-approval.md)已对会话版 Read 实现工作区内 `allow`、敏感/无效路径 `deny`、工作区外普通文件 `ask`，通过 LangGraph interrupt/checkpoint/resume 做同进程审批，批准后按调用 ID 和目标重验并一次性授权。假模型验证批准、拒绝、同轮多工具、目标变化、跨会话隔离及 CLI 命令。尚待真实供应商验证、审批异常后的 checkpoint 一致性处理、跨重启持久恢复、用户身份归属和多用户隔离；路径校验与打开之间仍有 TOCTOU 窗口。受限 Auto 与完全信任模式的动作清单、审计及实际隔离分别写 Spec，不能用本地审批冒充沙箱或 SaaS 授权。 |

`ls` 与 `find` 已按 [Spec 033](specs/033-ls-tool.md)、[Spec 034](specs/034-find-and-grep-tools.md)复用同一进程内审批/一次性 grant，批准工作区外目录时只允许列名；`grep` 只允许逐个审批工作区外普通文件，不提供外部目录的批量正文搜索授权；Read 的正文权限不变。用户选择显示全部名称，包括 `.env`、`.git` 等；若以后接入多用户或不可信工作区，需用真实使用场景重新验证名称泄漏风险与授权策略，不能以当前单用户选择推断 SaaS 适用。

[Spec 046](specs/046-tool-approval-registration-boundary.md)已将现有五种工具的审批描述随工具注册，应用层用同一描述生成待审批项与一次性 grant，通用 runtime 仅装配 HITL 和传递 grant；合成新工具无需修改通用 runtime 即可暂停、批准、恢复，现有七工具真实模型合成探针通过。旧公开恢复入口保留只读/Bash 的兼容映射，不是新工具扩展点。后续新增审批工具需同时提供工具执行处重验，并测试批准/拒绝、目标变化、跨会话和副作用；运行中热插拔、审批持久恢复、Auto/完全信任规则、身份/租户隔离仍需独立设计和验证，不能把本片称为通用权限服务。

`read` 的文本加固已按 [Spec 031](specs/031-read-text-hardening.md)完成：严格 UTF-8、明显非文本控制字节拒绝、白名单安全错误，以及工作区内部有限放行 `.gitignore`/`.github`；假模型和工具测试通过。`ls` 的目录名可见性已由用户选择与 Pi 一致：包括敏感文件名及隐藏项，但 Read 仍按独立路径策略拒绝正文。图片与其他编码 Read 是用户确认的后续必做能力，见 L7/L8。

[Spec072](specs/072-read-permission-extension-seam.md)已完成会话Read的权限扩展样板：可信应用注入allow/ask/deny，when/review/执行统一上下文与规范化请求，默认约束及错误回填保持，全量798例通过。尚待逐工具接入同类机制；write/edit外部写须先设计内容审查和授权绑定，ls/find/grep须明确列名与正文搜索范围，Bash须独立定义命令授权与隔离。完成判据是每种工具的策略判断、审批展示、执行复核一致，覆盖撤销/会话隔离/错误回填；当前不提供CLI会话授权、Auto/全信任或多用户权限。按已认可顺序先原生异步/最小Web，再回头完善；活动停止、重启审批恢复和TOCTOU待办不因此消除。

`edit` 大文件切片已按 [Spec 040](specs/040-large-file-edit-plan.md)实现：原文件和生成结果不设固定文件大小上限，分块扫描、增量 UTF-8 校验、原文哈希及临时文件提交；模型提交的替换文本仍受输入预算限制。本地 16 MiB 临时文件短替换、跨块样本、变化冲突、模拟临时文件 I/O 失败及同目标 busy 已验证；尚无真实供应商、大容量或高并发工作负载的性能证据，也无跨进程事务保证。Pi `a69bef789` 的同目标 mutation queue 覆盖 `write/edit`；本项目当前同一模型批次已顺序执行，跨会话同目标仍返回 busy。若引入队列，须先确定并测试跨会话 `write→write` 是否允许后写静默覆盖、`edit` 是否在取得锁后读取最新版、等待上限/停止信号及不同文件并发；不能把单纯排队当作数据一致性保证。

本地宿主机 Bash 按用户最新取舍由 [Spec 042](specs/042-local-host-bash.md)实施；它以 CLI 显式开关和逐命令审批替代首片 OS 隔离，工作区只作为 cwd。后续仍须完成 [Spec 041](specs/041-bash-execution-feasibility.md) 的隔离后端探针和生成 pytest 的隔离执行闭环，才能用于无审批自动运行、多用户服务或生产测试。当前 LangChain Codex 策略与安装的 CLI 格式不一致，系统 `sandbox-exec` 严格读取 profile 也尚未成功运行基础命令；不把宿主机版的测试通过视为沙箱证据。

宿主机 Bash 的**结果链**对照见 [Pi 源码对照与本机探针](research/pi-bash-result-chain-2026-10-02.md)。[Spec 043](specs/043-bash-argument-and-output-feedback.md)已修复无效参数审批错误和长输出尾部/UTF-8 回填；[Spec 044](specs/044-bash-unknown-outcome-diagnostics.md)已让超时等副作用未知异常保留有界的部分诊断，CLI 安全展示，模型仍不能自动继续。[Spec 045](specs/045-workspace-bash-output-artifacts.md)按用户选择在工作区专用目录保留超出尾部窗口的 stdout/stderr 原始输出，给模型现有 Read 可消费的相对路径，并用显式 `:outputs clear` 两阶段清理；本地临时目录/真实子进程与假模型测试覆盖归档、额度、Git 忽略和清理边界。真实模型已在临时工作区通过精确命令审批执行短 Bash 并收到原 ID 工具结果；仍待实际 pytest 长日志及真实模型读取归档结果、单行超过 64 KiB、二进制/非 UTF-8 读取、跨进程容量与清理竞争、崩溃后孤儿文件，以及未来 SaaS 的租户隔离与保留策略；完成判据须分别针对这些场景做真实样本与权限验证。运行中增量展示与活动取消的源码对照/本机流式探针见[专项调研](research/bash-live-progress-and-cancel-2026-10-02.md)。用户决定暂不实施同步进度展示或运行中主动取消，等异步流式阶段一起设计；流式输出不是取消的技术前提。完成判据是运行中进度、模型/重试/工具取消传播、审批和终局状态均有接线及可复现验证，且明确副作用未知时的处理。不能把现有 `:stop` 的 turn 后停止称为命令取消。直接用户 Bash 入口仍需独立评估。当前默认 60 秒 Bash 超时是否适合较长 pytest 套件，也需用实际样本核对。

工具失败回填按 [Spec 032](specs/032-tool-failure-feedback-boundary.md)逐工具落实：已有有效调用 ID 的参数错误、执行失败和将来的结果后处理失败应给模型安全且能指导下一步的错误 `ToolMessage`。Read 与 `ls` 已用固定原因/行动提示覆盖预期错误与只读执行中的未知普通异常；假模型验证同 ID 结果进入下一模型输入。`find`/`grep` 的预期搜索错误和本地未知只读异常也已逐工具安全回填；其他工具未处理异常当前仍使 `run_failed`，尚无通用结果后处理。`write` 已把可确认未提交的错误回填，并在提交结果未知时阻断该会话后续运行/审批恢复；尚未处理持久 checkpoint/崩溃后去重。下一工具切片仍须验证错误回填、脱敏和下轮可见性；副作用未知、模型响应失败、checkpoint/审批控制故障不可被通用重试文案掩盖。

## 核心闭环之后的独立扩展

| ID | 待做或待补证据 | 当前边界与完成判据 |
| --- | --- | --- |
| L1 | 跨重启会话恢复 | Spec070已用SQLite应用元数据/官方同步saver保存安全完成会话，Spec075再接官方异步saver并通过真实两个进程的消息/工具/模型ID续聊。当前同库单实例/持久单活动run，重启审批/失败/未完成/人工核对仍阻断；后续高级恢复须明确partial工具副作用/决定和去重，不自动重放。不支持多用户身份、任意掉电一致性或SaaS凭证存储。 |
| L2 | RAG 与知识库 | 逐种验证 Markdown、网页、PDF 解析切分，再做关键词/向量、混合检索、重排、引用定位和权限/版本约束；用同一评测集做对照，不能只看回答示例。 |
| L3 | 上下文预算、Skill、压缩与独立记忆/MCP | [Spec 047](specs/047-session-system-prompt.md)已接入用户定稿的 CLI 基础提示词及会话工作区根目录 `AGENTS.md` 优先、`CLAUDE.md` 次之的每 run 冻结加载；假模型验证装配和隔离，尚无真实模型遵循指令的效果评测。[Spec 048](specs/048-model-request-context-observation.md)已记录逐次框架侧模型请求的安全消息/工具摘要和可用的供应商返回 `input_tokens`。[Spec 049](specs/049-model-context-limits.md)已让每模型配置窗口与单次输出上限，默认 128000/16384，并在响应后记录用量相对于满额输出预算的安全事件；配置值不是供应商真实能力证明。[Spec 051](specs/051-model-context-preflight.md)已按当前模型增加发送前近似估算与候选触发事件；[Spec 052](specs/052-completed-turn-compaction.md)已在本地应用新 run 开始时压缩完整旧轮次。[Spec 053](specs/053-bounded-compaction-evaluation.md)以有界真实网关固定样本验证两次摘要完成、四个事实保留和摘要耗时/用量；2048 上限在该短样本未触及，但压缩组总 token 高于对照组，不能推出长会话质量或节省成本。[Spec 054](specs/054-post-compaction-budget-guard.md)已用完整应用链路假模型反例证明压缩后保留轮次可能超过发送上界，并加估算复核阻断正常模型调用；真实供应商 token 误差未验证。[Spec 055](specs/055-invalid-tool-call-history-summary.md)已用合成/导入历史验证无效调用的独立摘要表示及后续纠正；正常新响应仍被模型门禁拒绝，真实摘要事实保留未评测。[Spec 056](specs/056-no-safe-cut-soft-threshold.md)已让软阈值触发、无完整切点但仍在估算发送上界内的请求继续运行；[Spec 057](specs/057-safe-mid-turn-compaction.md)已在发送预算确有需要时按安全消息边界切分同一轮次，假模型验证工具调用/结果成对保留。单条超大消息仍无法切分；真实供应商对中途摘要的事实保留、token 精度与成本尚未验证。[Spec 058](specs/058-conditional-compaction-fallback.md)已按 TDD 接入有条件摘要失败回退：只有已知临时异常或空/截断合法文本、原请求仍在估算发送上界内且历史结构安全时保留原历史继续；假模型验证禁用边界、状态与终局，未制造真实供应商故障。[Spec 059](specs/059-explicit-input-overflow-recovery.md)已在指定 OpenAI/兼容网关与 Anthropic SDK 明确输入拒绝时，强制压缩当前实际请求并重发一次；假模型验证不重跑工具、状态提交、结构化消息和失败终局，真实供应商恢复未验证。其他供应商/网关信号、部分 length、静默截断、多模态强制恢复及 run 结束后压缩仍分别决策；后续还需总耗时/费用预算，不能把 SDK 每次请求的重试上限当成整个 run 的总预算。[Spec 060](specs/060-project-skills-index-and-read.md)已接入项目 `.agents/skills` / `.claude/skills` 两组索引及通用 Read 懒加载：有可用 agents 时默认，否则 claude，来源与同名身份保留；应用注册只读范围并在普通/审批恢复入口传递，假模型验证隔离、错误回填、快照与预算，一次有界真实任务确认默认和点名备用各读到正文/引用。首片只扫描下一级项目 Skill、解析限 64 KiB；外部共享链接（现有 rndflow）、用户级来源、远端安装、手动命令、可信工作区及来源服从率/内容质量评测仍需独立切片。来源/version 与 session/run 的持久观测、读取 TOCTOU 仍待补，不能以目录白名单宣称沙箱。MCP 与长期记忆另行设计生命周期、权限和隔离；压缩与回退分支不能冒充持久会话恢复。 |
| L4 | 多用户 SaaS 与密钥管理 | 当前是单用户本地配置，无登录、租户隔离或远程部署能力。若进入 SaaS 阶段，重新选型身份、授权、密钥保存、审计、配额和数据隔离，并做越权/泄漏测试。 |
| L5 | 本地模型优化实验 | 先取得真实本地推理基线与可重复评测，再决定是否进行 SFT/偏好优化；隔离训练/留出集，记录数据、权重、成本与退化，不预先宣称收益。 |
| L6 | 会话回退与分支 | 当前应用只有同进程最新 checkpoint 续聊，没有列出历史、选择消息 B、回退或创建分支的入口。LangGraph `get_state_history`、指定旧 `checkpoint_id` 调用和 `update_state` 提供底层能力；本机假模型 A→B→C 后从 B 提交 D，观察到旧 C checkpoint 仍可读，最新状态变为 A→B→D。这不是应用功能。设计时明确用户可见消息与完成 checkpoint 的映射、原分支与新分支的稳定指针/会话归属、并发与权限、重启后持久恢复；用假模型验证 A→B→C 和 A→B→D 均可选取，再用有副作用工具验证不会把回退误称为文件/API 回滚。 |
| L7 | 多模态图片 Read | 用户明确要求通用 Agent 在接入具备视觉输入能力的模型后能直接读取图片；这不同于 RAG 知识库解析。先确定模型能力、支持格式/MIME、文件与图像尺寸预算、必要的缩放/转换，以及多模态 ToolMessage/模型输入的真实接线；复用工作区、审批和脱敏边界。完成判据是本地图片样本的允许/拒绝/超限测试、真实视觉模型观察图片内容并回答，以及无视觉能力模型的明确降级；当前未实现。 |
| L8 | 其他文本编码 Read | 用户明确要求未来读取非 UTF-8 文本。先用真实文件样本确定支持 GBK、UTF-16 等哪些编码，以及显式指定还是受控检测；保留原始编码与转码记录，不静默使用替换字符。完成判据是按编码与分页边界的字节样本测试、错误回填及真实模型消费验证；当前只支持严格 UTF-8。 |

## 学习与面试证据待补

- 上下文压缩的探针与决策见 [Spec 050](specs/050-context-compaction-feasibility.md)：用户选择不直接接入内置摘要中间件，借鉴 Pi 策略并复用 `create_agent` 状态/checkpoint。[Spec 051](specs/051-model-context-preflight.md)已按 TDD 接入当前模型发送前的近似预算/候选触发事件；[Spec 052](specs/052-completed-turn-compaction.md)已接入新 run 开始前的完整旧轮次摘要，确定性假模型覆盖工具配对、前次摘要、摘要预算/模型失败、checkpoint 历史和会话清理。[Spec 053](specs/053-bounded-compaction-evaluation.md)的固定合成历史经真实网关两次完成有界对照，2048 上限下摘要实际输出 263/312 token、四项事实均保留；该短样本的压缩组总 token 高于未压缩组，不代表长会话收益或真实货币费用。早先超过两分钟的长会话探针根因仍未知。[Spec 054](specs/054-post-compaction-budget-guard.md)已用红绿测试补压缩后发送预算复核；[Spec 055](specs/055-invalid-tool-call-history-summary.md)已用红绿测试证明合成旧历史中解析失败调用与后续纠正可进入摘要输入；[Spec 056](specs/056-no-safe-cut-soft-threshold.md)与 [Spec 057](specs/057-safe-mid-turn-compaction.md)已用假模型完整链路验证软阈值跳过与安全消息边界切分。[Spec 058](specs/058-conditional-compaction-fallback.md)已用假模型验证回退资格、发送边界与原历史继续执行；用户确认方案并追问 Pi 溢出恢复，新增代码尚未独立复写。真实中途摘要质量、跨重启导入未验证；[Spec 059](specs/059-explicit-input-overflow-recovery.md)的指定 SDK 明确溢出恢复已按 TDD 接线；恢复 wrapper 与框架 Command 的独立阅读/复写待补。[Spec 060](specs/060-project-skills-index-and-read.md)已完成双来源索引与 Read 闭环，新增解析/上下文/权限核心代码待逐行阅读和本人练习；其他恢复信号、共享 Skill 来源及质量评测、重启恢复按独立切片推进。

- [Spec 005](specs/005-model-access-thinking.md)的等级回退已讲解，用户反馈理解；供应商参数接线已讲解但尚无独立复写证据。Ollama 映射修正尚待讲解。[Spec 006](specs/006-model-call-failure-event.md) 的错误路径已讲解，用户正确判断 `record_selection` 抛错不会生成模型失败事件；完整路径的独立追踪、复写和追问能力尚待验证。
- 用户独立增加工具、修改 middleware、接入模型、编写评测及定位日志的能力，均需通过本人小改动或调试记录确认；助手实现和测试通过不能替代这些证据。
- 面试材料必须来自实际实现与实验：保留一次失败案例、追踪链路、修复前后验证和设计取舍；不编造生产规模或个人实现经历。


## 最小持久会话之后的独立边界（2026-10-08）

- 重启审批/异常/未完成运行恢复：当前只列出并阻断interrupted/failed/running/review_required。后续须匹配图interrupt、pending_turn/决定/工具授权和副作用核对，在进程崩溃/部分工具批次中证明不会自动批准或盲目重放；人工恢复协议先独立Spec。
- 存储长期可靠性：schema/框架版本迁移、损坏/备份恢复、checkpoint增长/清理及磁盘满，验证主异常/元数据与图状态关联、完成事件时点和数据保留。不自动删除用户DB或用旧确认checkpoint宣称文件回滚。
- 平台和并发：首片macOS/Linux fcntl同库独占、轻量单活动run；Windows、多进程/SaaS、锁/背压/权限、异步saver连接/任务取消另测。stdio失败/事件交付失败不代表保存回滚；持久事件仓库未实现。
- 核心代码待学：持久组合、元数据事务、_finish_run顺序、恢复与资源关闭。用户完成选型/CLI需求确认，不自动记为独立实现或追问掌握。

## 异常职责整理与诊断故障保护（2026-10-10）

完整目标蓝图见[Agent健壮性设计](agent-resilience-design.md)，2026-10-11用户确认可配对无效调用反馈/协议破坏停止；这些新行为不等于Spec080或当前工具能力已完成。独立完成判据：

- 反馈闭环：明确参数/校验/权限/未知工具与程序bug边界；可配对无效调用不执行、错误结果关联正确、有界修复；无/重复ID停止，控制与脱敏不回归。
- 安全工具重试：受控依赖样本声明并验证只读/幂等/未提交；实际次数、退避抖动、取消/总预算、授权及批次槽位一致；SUT失败不能被掩盖。
- 模型attempt治理：选择SDK或Runtime唯一所有者，保持当前默认模式兼容；统计attempt与总预算/停止，不叠加重试、不重放已执行工具或拼接草稿。
- 熔断与资源隔离：逻辑依赖健康计数、Closed/Open/HalfOpen、有限探测/冷却、实例与目标作用域、并发/队列预算；参数/测试错误不熔断，Open拒绝不再次计故障。
- 备用工具/模型：授权且实际注册的同能力候选，窗口/输出/工具/多模态/成本/数据限制校验；实际model轨迹、不悄悄改会话选择、不用历史冒充当前证据。
- 正常返回质量：传输/协议/业务质量独立，固定样本/规则/结果验证及对照；修复回合有界，不能因模型自评通过就宣称正确或质量低就标依赖离线。

各项先Spec再实施，未启用参数在对应阶段讨论，不增加占位模块、不升级依赖、不未经许可改.env。源码与本机测试、真实供应商与业务评测分开报告。

### 事件与异常共同收敛（2026-10-11，本机实现已验证）

见[共同执行Spec](specs/081-event-exception-convergence.md)及[收敛讨论](event-exception-convergence-design.md)。当前未提交工作区已按两片实现普通观察隔离、测试外置序列检查、集中run依赖和Spec080原异常标注；必要保存、控制、权限、工具恢复及事件线协议保持。Web无事件时同一请求不会在本进程重复执行，返回明确503；跨进程幂等与真实服务故障仍需独立设计和验证。

获批观察可读性补充已实施：86个调用点改为绑定方法，正常路径测试显式断言观察健康，compaction/tool_batch两条重复分类规则已去重。最新后端1011/前端27及构建通过；单参/双参分类适配、共享resolve和旧兼容入口保持，是否进一步集中适配准备工作另定范围。

后续按用户清理要求完成Spec082：旧兼容入口已全部按列明范围删除，真实Bash/关闭读取改用中心服务；最新后端1038/前端27与构建通过。单参/双参自定义分类器仍是当前配置能力，进一步集中适配准备工作另定范围；未来功能迁移自动清理被替代内部逻辑的规则已写AGENTS.md。

提交审查确认的既有Web投影限制：工具开始事件已到达、run终局事件丢失且应用抛异常时，run_delivery的失败收尾会令run为done/unconfirmed，但工具条目可能仍是running并显示“处理中”。独立最小假应用探针已复现，当前与HEAD失败分支核对一致，不是本轮新增。完成判据：注入失败终局缺失后，保留原应用失败，未确认的工具条目降为unconfirmed，run与SSE有界收尾，正常工具结果和已有成功兜底不回归；作为独立修复，当前未实施。

本机完成判据：跨事件序列检查移出Publisher并在测试显式断言；事件构造/观察器/听众普通故障不改变结果与原异常，控制和保存失败保留；观察缺口/终局丢失有界收尾；集中绑定无串会话/漏还原，原异常身份/cause/context保持；同步/异步/stream/CLI/Web回归及全量测试通过。仍需单独验证真实供应商、代表性并发和重启后回执行为。事件删减/custom进度接入另定消费者与协议，不自动实施。

### 写入结果不确定与核对恢复待评估（2026-10-11）

当前_replace_temp将替换边界全部OSError保守视为未知；会话随后要求核对，但尚无公开的同会话只读核对/明确解除闭环。已验证门禁不等于完整恢复能力。此处记录待讨论，不自动启动功能或删除门禁；异常结构收敛与工具策略调整分为独立切片。

完成判据：按实际执行阶段、已确认失败/真正未知给出判断与平台证据；明确是否仅中止本run、哪些后续只读操作可用于核对、哪些条件允许继续副作用操作；若保留会话门禁，提供明确恢复/解除入口及状态/持久化验证。覆盖替换前失败、提交明确拒绝、人工注入替换后故障、外部文件变化、核对失败/解除竞争和Bash差异。不能把故障注入当真实文件系统自然复现，不将同内容写入直接等同非幂等业务调用。

### 既有Spec079设计历史与实施状态

[异常地图](1.0/exception-architecture.md)与[Spec079](specs/079-runtime-failure-projection.md)为本轮具体整理草案，尚未实施。已用合成组件探针复现模型分类诊断ValueError遮盖原TimeoutError；需模型同步/异步回归先红灯，再统一运行失败数据构造与诊断回退。完成判据是模块/模型/工具/应用实际接线、原异常身份及已有阶段/终局/日志/控制策略保持、定向与全量测试通过。此片不补全部供应商分类或外部重试，不把文档/探针写完称修复完成。

最新要求覆盖分层独立、全局异常脉络与自定义扩展，原Spec079退回复审不直接实施。完成判据新增每层可独立验证/无反向依赖、模型/工具/App扩展不改不相关层、两应用映射隔离、框架ModelError族复用与兼容；正式分类/注入接口尚待确认，见[复审](1.0/exception-design-review.md)。用户能读局部代码不等于已能追踪完整故障链。

实施更新：用户批准复审方向后Spec079已完成上述本机验收，新增34例及后端962/前端27全部通过；诊断遮盖（含摘要cause）、合法unknown覆盖、旧桥接上下文及默认参数兼容已补回归解决。模型映射按目录/model_id快照，Runtime/App配置按实例，source可显式传给分类器；没有全局注册表或新的HTTP函数接口。下一步学习新版三条故障链；真实供应商异常、未知样本评测仓库、公共HTTP码扩展以及M3外部重试仍待后续，不将本机测试当完整生产验证。
