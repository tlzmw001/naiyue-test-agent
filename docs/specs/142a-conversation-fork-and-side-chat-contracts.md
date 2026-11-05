# Spec142A：分叉与辅助对话的数据、接口和生命周期

状态：2026-11-01 方案稿，未实施。持久分叉采用用户已选定的“复制当前工作区到独立沙箱”；辅助按需准备及复制/审批细节随[主Spec](142-conversation-fork-and-side-chat.md)审阅。

云端接线由[Spec143A](143a-runtime-and-state.md)与[143B](143b-input-and-delivery.md)替代本稿的A阶段binding/bundle实现引用；稳定聊天边界与当前工作区独立复制的行为合同继续保留，正式实施142前据B实际接口收束，不建立旧协议兼容。

## 1. 稳定快照

稳定点是必要保存完成的、完整成功回复之后的正式图状态。新建 `naiyue_conversation_heads` 记录每个持久根会话的 `session_id/thread_id/checkpoint_id/conversation_revision/reply_id/upper_sequence/generation`，与该轮档案和元数据同事务提交。不能把“模型流结束”或“有 assistant 文本”视为保存成功。

| 情况 | 分叉 | 辅助对话导出点 |
| --- | --- | --- |
| 空会话/首次运行未完整保存 | 不可用 | 不可用，提示先完成一轮 |
| idle 且最新任务完整成功保存 | 最新回复 | 当前稳定点 |
| 新一轮正在运行/待审批 | 不可用 | 运行开始前最近稳定点；排除新一轮全部内容 |
| 本轮停止/失败，仍有以前稳定点 | 不可用 | 以前稳定点；界面说明排除了失败轮 |
| 必要保存失败/来源版本不明 | 不可用 | 不可用；先处理原保存问题 |
| 重生成切换 thread/会话恢复对账中 | 不可用 | 暂不可用，不能跨版本采用旧锚点 |
| 手动压缩完整保存 | 仅在最后完整回复仍是当前尾部且上下文可核验时可用 | 更新为压缩后的有效上下文；不是恢复全部旧原文 |

`generation` 只描述稳定导出内容的变化，不复用 `conversation_revision`。正常成功保存、必要上下文压缩保存及替换式重生成激活按真实主链更新/失效锚点。旧库迁移不批量猜测 checkpoint：启动后在正式会话恢复入口核实完整回复与档案水位，再补锚点；无法证明则返回 `no_stable_context`，不发起模型/工具重放。

稳定锚点受 checkpoint retention 保护。导出期间使用 S1，不能在导出尚未取值时删除其 checkpoint；新版本激活、会话删除、修剪与导出必须确定性并发验证。独立分叉成功后不再依赖源 checkpoint，来源引用不成为永久保护父库全部历史的理由。

## 2. 导出结构、值复制与恢复

拟新增内部值对象 `ConversationSnapshot`，只有两个消费者：持久分叉 seed 与辅助 RAM seed。

| 字段组 | 生产者/形状 | 消费者/约束 |
| --- | --- | --- |
| `source` | 服务端解析的 scope/project/sid/thread、稳定 reply/checkpoint/revision/generation、水位 | 校验、来源和审计；不是新会话执行授权 |
| `model_messages` | 官方 `aget_state` 返回的当前有效消息，包括压缩摘要、必要 System/AI/Tool/Human 内容 | 新 thread seed；保存后状态必须 `next=()`、无 pending task/interrupt |
| `archive_rows` | `ConversationRepo` 按稳定水位取完整正文与已归档投影 | 持久分叉完整聊天；辅助只导入其有效上下文对应的最小 RAM 档案，不复制整份未压缩历史 |
| `selection` | 当前模型/推理档位、活动工具名单、可用权限规则选择的值副本 | 新会话独立选择；不得携带模型客户端、凭证、活连接或审批授权 |
| `execution` | 已验证项目身份、Host绑定或Sandbox模板/源工作区捕获请求 | 生成目标私有输入；不继承父binding使用权限 |
| `display_origin` | 父会话名称快照、回复时间 | 来源导航；不参与模型恢复和权限判断 |

导出锁序：进入宿主lifetime operation→短状态锁取得源准备预留→源S1内核验稳定锚点/读官方checkpoint/复制档案→释放S1→持有环境use预留但不持线程锁，捕获/下载/校验当前工作区→目标私有输入ready→源S1内CAS和发布→释放所有预留。S1期间不调用模型、MCP、Docker或远端文件接口；不得持有S2事务/线程短锁跨await，也不等待另一个会话S1。

源准备预留是SessionState中的operation_id标记，用于两个真实消费者的工作区捕获。普通start、compact、regenerate、delete和环境写入口必须检查它，防止预留后另一个请求开始修改；只读历史允许。标记取得/释放只持现有短锁，跨IO的是预留。EnvironmentActivity.use阻止管理/销毁；捕获前核实无运行或unknown任务，不通过manage包裹一个内部又要use的调用，避免自我冲突。失败/取消/关闭所有出口释放；重启只按持久操作记录对账，无聊天运行复活。

持久分叉的归档复制按源稳定水位和预算在一致读事务内完成，不能分多次无快照读取而混入新消息；复制期间 S1 允许阻塞同会话保存/删除，但不持有运行准入短锁，不阻塞其他会话。大对象超预算直接失败，避免用永久源引用规避复制成本。

复制规则：

1. 新建 sid/thread、reply/message/tool_call id 映射；模型消息与档案共用同一映射。ToolMessage 的 call id、AI tool_calls、reply归属和工具结果 parent引用全部同步。保留顺序、正文与必要 provider 消息字段；校验无未配对工具调用，不能只复制 assistant 文本。
2. 压缩后的上下文原样复制并保持摘要边界；完整档案另外保留。禁止将历史所有原文加回模型消息。辅助新输入前加明确 side boundary，继承消息不是当前任务。
3. 图片沿用已经规范化的内容块与字节、原始说明和规范化版本；不重新转码。持久分叉的认证图片路由按新 sid/message/revision 查询；辅助图片字节仅在 RAM，其预览受当前 lease 身份约束。
4. ToolMessage 的模型可见文本/结果状态保留。历史附件下载、Shell进程、任务详情与子Agent深链不复制可执行控制权。首期将依赖父运行归档的附件/子会话入口标为“来源产物未复制”，移除可操作链接；不伪造可下载文件，不让新 sid 越权取父产物。
5. 已经写入档案正文的工具摘要、Write/Edit差异与有限输出投影可以随正文复制；原始大输出/附件并不因此获得独立副本。UI须区分可查看的复制正文与未复制的外部产物。
6. 历史 provider usage 可作为只读消息元数据保留，但不重新计入 B 或 S 的真实请求/费用汇总；新会话统计从实际新请求开始。继承部分标注为来源历史。
7. 不复制 provider端 conversation id、response chaining id、checkpoint tasks/pending writes、队列、活子实例、流程控制状态、审批Future/allowlist授权、read缓存授权、进程句柄、环境管理令牌。保留模型协议必要的工具配对字段，不依赖旧 provider会话才能发送。
8. 有效规则仍受当前应用权限设置约束；工具删除、模型失效或环境失效不得悄悄采用默认替代物。可展示缺失并要求用户明确重新选择，再按现有接口处理。

框架用官方状态接口种入 messages，并按当前已验证的 START/END 更新模式建立没有待调度节点的状态；第一条新用户消息才进入图运行。不能恢复源 snapshot 的任务或直接 invoke空输入。LangGraph 的时间回放会再次执行未来节点，不能拿它替代本期的“复制历史、零重放”合同，参见[官方 time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)。

## 3. 持久分叉存储与操作对账

计划项目库 schema20→21，沿用备份/迁移、参数化 SQL、UnitOfWork 与仓储模块边界。

| 新表 | 字段/约束 | 用途与删除边界 |
| --- | --- | --- |
| `naiyue_conversation_heads` | 第1节字段；session主键，generation单调递增 | 当前可导出稳定状态；仅随所属会话删除 |
| `naiyue_session_origins` | child_session_id主键、source_session_id/thread/reply/checkpoint/revision/generation、source_title、created_at、可空workspace_snapshot_json（捕获时间/commit/tree/digest/排除清单/目标input_id） | 来源导航与文件快照事实；child随删，source没有级联外键，源删后显示不可访问 |
| `naiyue_session_fork_operations` | request_id主键、source_session_id、规范请求fingerprint、target_session_id/thread_id、state、cancel_requested、error_code、created_at | 幂等、取消和跨连接创建对账；不保存凭证或消息正文 |

操作状态：`reserved → workspace_ready → seeded → committed`；Host没有复制阶段时直接reserved→seeded，确定失败为`failed`。操作记录增加源binding、capture_operation_id、目标input_id和snapshot摘要，恢复只沿这些已固定身份查询。不把请求中断直接判成失败。相同request_id/fingerprint只能关联一个target；不同fingerprint冲突。已提交目标后来被用户删除，旧request_id返回`fork_target_deleted`，不能重建。

正常链：

1. 通过scope认证和归属检查，校验请求，取得源准备预留，在S1内核验稳定点并预留操作及目标身份。
2. 导出上下文；按第7节捕获当前工作区，准备目标私有输入并记workspace_ready；新checkpointer thread写入完成后确认`next=()`和配对。此时目标不进入会话列表，不可被普通运行入口访问。
3. 用一笔元数据事务插入目标会话、复制档案/水位、来源、目标稳定锚点及操作 `committed`。保存目标元数据引用的正是已验证新 checkpoint。
4. 返回 B 的身份和普通会话视图，后续沿现有 scope新会话入口读取。观察投递失败不回滚已提交会话；前端按同 request_id对账。

失败链：seed失败保留源不变，操作记失败，精确清理新 thread/输入；清理失败单独可观测且不覆盖主异常。seed后publish前崩溃，重启仅检查预留target和commit事实：未发布者清理自己的孤儿并记失败；已提交者返回同target。不得再次复制到第二个target，不自动发模型请求。

持久分叉准备期间源自己的新运行/替换/删除被准备预留拒绝，不等待它们完成后偷偷改用新版本；最后发布仍在源S1内核对源存在、身份和捕获的revision/generation，任何变化都拒绝发布。发布/失败后释放预留，源立即可继续；B使用已保存的上下文和文件值，不再依赖源。

删除 B 清理B自己的图/档案/来源/输入与任务，不按origin把A或其他兄弟分叉视为子Agent删除。来源导航必须重新走 scope权限；仅知道source_id不授予访问。

备份检查器同步核验新表、目标身份、有效锚点与操作状态；旧数据来源为空。导出导入、整库备份/恢复和历史分页也必须同步覆盖，不能只改SessionState和UI。

## 4. Web与应用接口

实际消费者为 Web 主聊天，首期不增加 CLI命令。已有公开函数与路由签名不作破坏性改变；新增参数均采用可选字段/新增端点，旧临时scope合同不隐式改变。

### 4.1 动作能力

当前session view增加`conversation_actions`：`fork`、`side_chat`各含`available/reason/source_reply_id/snapshot_token`；另含可空`preparation={operation_id,kind,phase,cancellable}`，供当前页/另一页面展示源准备并禁用冲突写入口，不能伪造一个模型run。令牌从sid/thread/checkpoint/revision/generation/水位计算，只用于比较，不是授权；请求仍重新核验scope和归属，不能信任前端checkpoint或执行根。

`reason`至少覆盖：`empty`、`running`、`approval_pending`、`save_failed`、`no_stable_context`、`unsupported_session_kind`、`environment_unavailable`、`environment_busy`、`fork_preparing`。公开理由不暴露凭证或内部文件路径。

### 4.2 创建持久分叉

`POST /api/v1/scopes/{scope_id}/sessions/{session_id}/fork`。

请求头：沿现有 `X-Request-ID` UUID及认证；请求体仅 `expected_reply_id`、`expected_conversation_revision`、`expected_snapshot_token`。scope和session从受控路由解析，不能任意注入 source project。

成功 201 返回 `session_id`、`thread_id`、`origin`、`operation_id` 和 `replayed=false`；相同操作重试返回200和同身份、`replayed=true`。进度由当前命令回执机制承载；持久操作表才是服务重启后的事实。无需把完整聊天作为创建响应推送。

冲突409：`conversation_changed`、`fork_busy`、`fork_operation_conflict`、`fork_target_deleted`、`fork_source_busy`、`fork_workspace_changed`；不存在404，权限按现有403/认证拒绝；超复制预算413 `conversation_copy_too_large`，工作区超限沿GitInputSettings错误；必要保存或环境错误按现有Web映射。复用`WebError`，不添加另一套错误信封。

应用入口拟为 `SessionAPI.fork(session_id, *, request_id, expected_reply_id, expected_conversation_revision, expected_snapshot_token)`；只返回身份和来源，不偷偷启动运行。现有 `regenerate`入口不改语义。

创建取消使用新增`POST /api/v1/scopes/{scope_id}/sessions/{session_id}/fork-operations/{operation_id}/cancel`，头部沿现有认证/UUID回执，body为空。应用显式核验源归属，幂等设置cancel_requested、阻止后续publish，只取消该捕获操作，不调用父run.stop或环境stop。返回真实`cancelling/failed/committed`；已committed返回目标身份，不能删掉B冒充取消。确定终止的创建记录error_code=fork_cancelled；未知捕获排空/对账未确认时保持cancelling并显示原因。前端AbortController只停止等待，不等同服务端取消。

### 4.3 创建辅助实例

`POST /api/v1/scopes/{scope_id}/sessions/{session_id}/side-chat`。请求头同上，体仅 `expected_snapshot_token`。来源只允许持久根，当前打开的辅助实例不可作为源。

返回`scope_id=temp-...`、`session_id`、`lease_url`、`source_reply_id`、`snapshot_label`、`execution_summary`。`lease_url`由服务端当前路由生成。此时仅预留RAM导出/执行归属，不存在持久聊天行；lease激活后seed RAM且ready，前端才开放发送。execution_summary区分`context_only/workspace_preparing/ready/failed`，包含文件快照真实时间及不可用原因，不能把文字ready当成沙箱ready。

每个浏览器页面最多一个实例；同request_id在该临时生命周期内返回同身份。切换主会话/项目先按关闭流程收尾，普通面板切换仅折叠。其他页面可独立打开，仍受全局 RAM/运行/沙箱额度限制。不同页面不共享审批、消息和草稿，不提供浏览器持久化的实例恢复。

继续复用 `GET /api/v1/temporary/{identity}/lease` 与 `DELETE /api/v1/temporary/{identity}`；发送/停止/审批等沿 `/api/v1/scopes/{temp_id}` 的现有端点执行。新增side创建入口按来源核验后装配RAM项目上下文；不能接受任意root参数替代项目授权。

没有lease的发送返回原 `temporary_lease_required`；已关闭身份返回closed/not_found。旧创建请求重试不能重新创建已关闭会话；旧事件、迟到响应、旧审批不允许绑定到新实例。

显式关闭成功返回204；清理失败返回 `temporary_cleanup_unconfirmed`和可重试状态。浏览器只持有RAM实例id，实例关闭后清空正文/草稿/图片对象URL，不写localStorage、IndexedDB、服务worker缓存或URL查询正文。临时正文、图片及状态响应设置`Cache-Control: no-store`；RAM SQLite不允许把含正文的临时排序/中间表外溢到磁盘，并由存储验证覆盖。

### 4.4 请求授权和日志

所有新增路由放入现有认证和scope归属检查；应用边界校验源sid属于该项目且是允许类型，不能只靠前端禁用。临时scope须有效lease；图片/正文/审批/运行控制都验证自己的scope、sid、run和revision。

身份与fingerprint不记录消息正文；日志仅operation/scope/session标识、阶段和有界错误码。snapshot_token不是凭证也不能替代权限。网络和必要IO错误必须可观测，禁止空catch；不会把普通观察失败升级成执行失败。

## 5. 辅助状态、工具和保存

```text
closed → creating → active ↔ collapsed
                    │
                    └→ closing → closed
                         └→ cleanup_unconfirmed → closing（精确重试收尾）
creating失败 → 释放预留 → closed，并保留主输入草稿
```

这是UI/资源生命周期，不能代替session的running/awaiting_approval/failed等原状态。lease断开即开始收尾，不做隐式重连复活；网络中断后明确告知本辅助已结束，用户可新开。折叠仍保持lease和订阅，不通过卸载组件实现折叠。

每个辅助实例：RAM ProjectStore与官方RAM checkpointer、自己的model选择/工具选择/approval grants/stream/control；借用应用catalog与全局admission，不关闭共享客户端。临时宿主只借用已核验项目事实/执行authority，不发布到projects持久host列表；不得因registered_project参数而把辅助聊天误绑定到持久会话库。

所有必要保存仍执行，但保存目标为RAM。usage/diagnostics保存在RAM且按自己run统计；不触发持久标题生成、概览、记忆提取/归并/索引作业。继承的已有记忆文本若已在模型消息中不伪造删除；不额外读取/写入长期记忆工具或跨会话历史。

工具选择为父活动工具快照与当前可用工具的交集，再移除本期禁用能力。辅助Sandbox未捕获/安装时，文件、进程、AITest、依赖工作区的MCP均不注册；仅保留有真实能力的上下文问答/联网/独立MCP，UI与模型都获知限制。每次用户发送前可尝试一次源空闲准备，源忙立即返回可见原因，不自动等待或重试；捕获成功后固定独立输入，之后只重试本输入的安装，不再次跟随父文件。保留本实例Read缓存和审批生命周期，不带父缓存。

| 工具类别 | 本稿执行合同 |
| --- | --- |
| Read/LS/Find/Grep/WebFetch/WebSearch | 现有显式权限与目标核验；允许规则不变，不因辅助身份扩权 |
| Write/Edit | 沿现有文件能力，额外要求本实例本调用明确批准；原deny仍拒绝 |
| Shell/AITest/未知MCP | 均视为可能修改，强制本实例审批；不得根据命令名猜成只读 |
| 可信注册为read_only的MCP | 当前权限规则仍有效；远端hint或模型声称不构成可信分类 |
| 子Agent、memory、跨会话history、环境管理 | 首期不注册对应调用能力；手工伪造调用也在服务端拒绝 |
| 后台进程查询/停止 | 仅自己的scope/run任务，关闭全部排空；不允许接管父任务id |

工具审批规则通过执行权限入口强制，不仅写提示词；父会话auto或session_allow不放行辅助写调用。批准一次仅消费当前工具批次/调用，不产生跨实例持久授权。“明确请求才修改”的提示词指导模型意图，真正的副作用授权由当前批准事件提供。

## 6. 关闭、取消与故障

必要顺序：封口新提交与投递 → 取消未开始命令/拒绝待审批 → 请求本实例停止 → 等待工具/进程达到可确认终态 → 排空delivery → 关闭本实例MCP及RAM资源 → 清理自己输出/输入/沙箱 → 释放RAM额度和lease。清理顺序可按现有依赖细化，但不能先删任务事实再停止未知任务。

父/辅助可并行，共用全局admission。达到上限时辅助发送得到现有准入错误，不能停止父任务腾名额。父停止只作用父run，辅助停止只作用辅助run；父会话删除先关闭由它仍打开的辅助实例，收尾未确认则沿现有busy拒绝父删除，不能绕过临时资源收尾。

关闭按钮在已发送/正在运行时不增加“是否保存对话”的二次弹窗，直接停止并收尾；若有尚未发送草稿/未提交图片，使用现有丢弃草稿确认模式。关闭成功后再次打开生成全新scope/sid/thread；重用组件也必须清空旧序列、审批与流订阅。

进程关闭失败、SSH断连、工具提交后结果未知不伪称已终止。记录确切拥有者和阶段，界面保留轻量“收尾未确认”条目供重试，聊天正文不为恢复而落盘。正常关闭前可短暂显示RAM正文，用户主动离开/刷新后正文不可恢复。

服务重启只对账临时执行拥有权表里的操作资源，不恢复辅助会话或重放Shell/MCP；未知执行先查后停，不能删除正在未知状态下工作的容器。清理须核对应用/项目/临时scope/session/binding/input及创建所有权；任何不匹配保留现场并报错。

## 7. 执行环境及临时资源

用户已明确选择：**持久分叉复制当前工作区到独立沙箱**。本节替换原独立初始输入候选；共享父沙箱和从初始模板重新取源均不建设。Host沿用经authority核验的同一项目根，不能宣称其文件隔离；这里的复制合同针对Sandbox。

### 7.1 文件范围

复制当前已提交内容、tracked修改/删除、以及现有输入策略允许的非忽略未跟踪文件。继承父捕获时冻结模板选择，新建目标session/input/binding/container/卷。忽略文件、敏感路径、外部链接、unsupported文件与Git输入预算继续遵守现有规则；被排除项带原因展示，不能说“整个容器已复制”。若tracked内容不满足安全/文件规则则拒绝，不静默丢失。

不复制工作区外依赖、系统软件、容器内存/网络连接、数据库服务状态、后台进程、审批、凭证/SSH-agent、Git hooks/config/reflog。Git对象通过受验证bundle导入，不原样拷贝`.git`目录。工作区的最终内容以合成snapshot commit作为B新基线；保留来源HEAD/树的可查事实，不承诺原staged/unstaged分组、分支名、stash或所有refs完全相同。新增/修改/删除和可执行位必须一致，二进制按实际文件支持规则处理。

未跟踪文件先走现有`result_options`得到untracked/blocked；默认捕获允许集合，blocked/ignored项在分叉进度和来源详情可见。前后两次候选集合不同则拒绝或要求用户重新发起，不偷偷漏掉捕获期间新增的文件。不得读取应用控制目录、Host路径或源工作区之外内容。

### 7.2 复用现有捕获链

当前已有可复用生产链：`envd.git_results.ResultExporter.capture`→`SnapshotBuilder.prepare`，不会改写源HEAD/index/工作文件；controller `ResultService`下载`result_chunk`并用`ResultImporter.verify`校验bundle/来源/对象/树。**现有ResultService.capture最后还会publish结果ref到用户仓库，分叉不能直接调用整条链**。

拟在其现有职责内拆出“捕获/下载/验证到私有隔离目录”阶段，供原结果取回和新workspace fork消费；原结果取回继续显式publish，分叉只取得verified receipt/私有对象库，禁止调用`ResultImporter.publish`。不新建通用结果平台，也不复制整条捕获算法。复用已有RPC种类，原则上不改envd身份协议；实现中发现旧端缺少必要保证须先更新Spec，不用绕过检查兜底。

正常路径：

1. 源会话准备预留内核验状态、project/session/binding/input/模板和无unfinished/unknown任务；取得EnvironmentActivity.use，阻止源管理/销毁。对已有stopped源返回明确“先恢复原环境”错误，不因分叉自动start父环境。
2. 固定capture_operation_id与源input身份，查候选文件，用既有journal提交一次git_result捕获。该内部操作标记`purpose=conversation_fork`及目标operation/scope，不作为新聊天任务，也不混入普通用户结果列表。
3. 捕获后按既有chunk上限断点下载，只续传同一receipt的不可变bytes；验证result/binding/project/session/input、原输入、output_commit、bundle_size/SHA、fsck/树/manifest/文件模式、来源project revision与排除清单。前后HEAD/文件身份/内容与候选变化沿SnapshotBuilder和补充候选核验拒绝，不能只信命令exit0。
4. 在应用私有目标目录构造新`refs/naiyue/input/{new_input_id}`和标准input.bundle，再运行输入bundle校验；新InputSnapshot的original_head/input_snapshot均为捕获output_commit，manifest/tree/size/hash均来自已验证对象。新GitInputRecord与execution_setup引用自己的新input_id，include/settings与后续prepare完全一致。不得把旧input_id仅改字符串后复用旧receipt。现有capture操作metadata登记target_session_id/target_input_id和creating状态，InputService.retire必须保护该未发布输入；成功发布后转committed并由普通目标会话接管保护，失败清理后解除。跨库崩溃只核对目标publish事实后解除，不提前丢pin。
5. 原子登记目标ready输入及来源事实；新会话普通prepare_input看到既有ready输入，必须校验并返回它，不能从当前Host/GitSource重做快照覆盖它。目标第一次发送按原准备流水线创建自己的沙箱并安装该输入；安装后再次核对工作区树与绑定身份。

捕获使用源的现有envd控制/只读Git快照能力，不启动模型、AITest或用户Shell任务；不更改源工作文件/HEAD/index，不向用户仓库新增结果ref。源侧必要export缓存/journal属于有界操作事实，沿现有控制目录/缓存规则，不当作新聊天历史；目标输入、图和档案均独立，删源后B仍可运行。

### 7.3 并发、取消和失败

源busy/unknown在捕获前返回`fork_source_busy`；应用控制的新写入口被准备预留阻止。源外部管理员/绕过应用的写入不在互斥保证内；捕获发现变化返回`fork_workspace_changed`，不承诺文件系统事务快照，不暂停/杀死外部进程来强制成功。

源断连/捕获结果未知：只查询同capture_operation_id，不重新提交一份可能不同的现场。结果已ready时可续传同receipt；未知持续则返回未确认，保留最小操作事实。确定复制/校验/发布失败只清理本次私有目标，不碰父输入/卷/仓库；主异常与secondary清理异常分别可见。

取消创建时封口目标publish，排空已提交捕获操作，再清理本次目标staging/input/thread并释放源预留；不stop或destroy父沙箱。发布成功后取消/HTTP断连不删除B。同request_id不重新捕获新的工作区，已提交返回同B，未发布崩溃按第3节对账。

目标安装失败不更换父快照、不重复制源最新文件；保留B/已验证输入，走普通环境恢复/重试。模板不可用、额度不足、输入超限、Git历史不符合现有descendant检查均明确失败，不退回初始输入、默认模板、共享父binding或Host执行。

### 7.4 辅助对话的按需文件环境

辅助采用同一独立复制机制作为本稿建议，不在打开时为一句问答立即克隆环境。打开先完成稳定文字快照；首次用户发送前，若源闲则按7.2取得文件快照，随后沿发送前原准备流水线安装。源忙时不阻塞其任务，S可做上下文问答和有真实能力的独立联网/MCP调用，明确显示文件/Shell能力尚不可用；下次用户发送前可再尝试一次，不能后台自动等待或调用工具猜源文件。

执行准备以发送前的现有准备阶段完成，不另建工具内部的懒加载runtime。文件快照成功后就冻结，不再自动跟随父文件。文字快照时间与文件快照时间可能不同，UI/边界提示分别说明；没有文件快照时不能把父历史描述当当前文件检查结果。源准备预留只覆盖短期捕获，不延伸到辅助的模型问答/多轮生命期。

RAM SessionState新增仅本实例存活的side_context值，含source身份/文字水位、requested工具和execution_phase/目标setup；由创建seed生产，发送前资源准备及history投影消费，不写持久项目库。context_only阶段在准备/能力边界显式返回无files/processes/workspace-MCP的PreparedExecution，并把execution_root留空，**不调用project_local/Host解析兜底**。捕获/安装成功只在无活动run、S自身S1保护下切到自己的sandbox/setup，再进入正常admission；不修改原主会话执行绑定，不对外新增任意运行中切换环境的API。继承档案以reference_upper_sequence在服务端投影过滤，不能只用CSS隐藏。

辅助执行的最小持久事实在注册库新增`temporary_execution_owners`：scope_id、project_id、临时session_id、binding_id、input_id、capture_operation_id、创建身份、state和最后错误码。无聊天正文、标题、模型checkpoint或可恢复对话内容，仅用于崩溃后精确收尾，不供历史列表或模型检索。注册库使用独立迁移版本，不套用项目库schema21。

创建外部资源前先登记owner；RAM会话保存自己的execution_setup，普通持久项目库不插入S的session行。捕获、安装、运行、停止仍走EnvironmentAPI及journal；身份检查按自己的session/binding/input执行，无源身份借用。临时产物放应用data_dir私有scope目录，路径须核验。

关闭S：封口→停止自己的任务并确认终态→精确销毁本次临时容器/卷→清理自己的输入/输出和owner。没有分配环境时不做Docker动作；捕获阶段取消只取消本次捕获，不stop父沙箱。结果未知时保留最小收尾事实并显示未确认，不先删journal制造“无残留”。已批准的Host/外部服务写入不回滚；不提供转正/保存临时沙箱功能。

## 8. 预算与并发

新增 `conversation_copy` 设置为**本稿拟定默认**：`max_snapshot_bytes=64 MiB`、`max_fork_archive_bytes=256 MiB`；新增 `side_chat` 设置为**本稿拟定默认**：`max_instances=8`、`max_total_bytes=256 MiB`。它们当前不存在，不能当成已经生效的限制。配置模型和schema可随实现增加；真实`.env`、用户配置文件不能随代码擅改。

snapshot预算覆盖序列化后的有效消息/规范化图片/边界和必要元数据；fork归档按复制正文累计，辅助RAM总额按自身checkpoint/档案/usage/诊断/草稿上传的已接收字节保守记账。超过预算在创建或必要保存前拒绝本次新增内容，保留以前稳定内容与未发草稿，不截断成功回答冒充完整。内存分配须边读取边计数，不能先无界载入后检查。

实例和RAM额度由现有临时scope owner串行管理，预留与释放一致；不是仅靠前端“一页一个”。持久分叉操作遵守现有命令回执准入，操作表只保存最小身份/状态，不保存正文。本期保留幂等tombstone直至所属项目显式移除，防止已删除目标被旧request_id重新创建；不另建自动操作历史清理平台。

运行准入、沙箱running/retained、图片、消息帧及工具输出上限继续消费当前配置；复制预算不替代它们。snapshot放服务器内部，不通过单个巨大HTTP/SSE frame传递；源正文在分页端点读取。

新锁/预留、输入克隆锁序和临时owner必须在实施时同步到[lock-hierarchy](../lock-hierarchy.md)，覆盖间接调用和清理反向路径。本轮不向当前锁表写入尚不存在的机制。
