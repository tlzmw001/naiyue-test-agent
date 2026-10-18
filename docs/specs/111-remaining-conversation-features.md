# Spec111：剩余会话呈现、版本与内容投影

日期：2026-10-18。状态：**统一方案草案，供本轮讨论；尚未获实施授权，未修改产品代码**。

用户要求把Spec103剩余五项一次讨论清楚。本稿覆盖B3自动标题、U1请求/回复归属、完整Write/Edit差异、U12主会话编辑重发/重新生成、U13供应商可展示思考。此前纯前端批已交付；Spec110在同工作区并行迁移，实施前以其最终入口为准，不回退或复制已替换的转发层。

## 1. 总体建议与外部行为

| 能力 | 本稿推荐 | 模型请求与复杂度 |
|---|---|---|
| 自动标题 | 首条有效人类任务生成有限长度文本标题，只在title为空时保存，不覆盖手工名称 | 无额外模型调用；小 |
| 回复归属 | 人类任务有稳定reply_id；中间说明、AI调用和工具结果归同一视觉回复；steering补充本任务，follow_up开始新任务 | 无额外调用；中 |
| 工具差异 | 执行时采集真实有限差异，用ToolMessage.artifact承载，模型文本反馈与UI数据分开 | 无额外调用；中 |
| 编辑/重新生成 | 同一逻辑会话保留多个版本路径，每条分支使用独立根thread；旧版本保留，浏览不执行，选中的版本决定后续上下文 | 用户触发新的正常Agent运行；大 |
| 思考展示 | 展示供应商明确返回的可见思考/摘要；标准块复用框架，明确配置的CC扩展薄适配 | 展示不另发请求；申请摘要可能增加原请求输出/计费；中 |

最小替代方案：自动标题采用本文本地方案；版本功能可只提供“从此处新建会话”，复用当前一会话一thread而不做同会话版本选择。本文优先推荐同会话版本，外部体验更完整，代价是必须一起处理分支归属、分页与保存，不能当作按钮小改。

本轮不增加文件自动回滚、工作区快照、Git worktree、子实例单独重生成、任意图节点时间旅行、后台Agent、新沙箱、checkpoint修剪或项目重试。主任务重新执行可自然创建新子实例；旧子实例继续只读。

## 2. 当前事实与参照

- 当前SessionState拥有一个thread_id；ActiveRun是瞬时根运行，包含字符串steering/follow_up队列；SessionService负责准入。正式CLI/Web都走AsyncApplication，根/子共用create_agent装配，非同步应用执行链。
- SessionStorage已有title和条件更新所需事务入口；Sidebar对空title回退时间。没有自动标题生产链。
- mark_human_messages只标记真实人类输入，尚无稳定reply_id/input_kind；run_id不能覆盖审批跨run或一个run内多个follow_up。ControlObserver目前生成控制消息ID，但属于可失败的观察层，不能让新归属依赖它。
- collect_chat_updates按已选checkpoint祖先增量保存完整LangChain消息JSON；档案有scope/sequence/revision/body_hash和工具父消息ID，没有用户请求归属。chat_queries/Web返回有界页，正文另按身份分块。
- Edit已有firstChangedLine/diff/preview/patch及独立截断标记；Write仅返回创建/覆盖和字节数。message_view和message_completed快照没有UI artifact投影。
- content_text过滤非文本块；_StreamState.delta只发文本。完整LangChain消息可能已有原生reasoning/thinking；有数据不等于Web已经展示。

参照以本机源码为准：Pi `a69bef789`，AgentX `3fcfa0e`；两者相关生产源码无本地修改，Pi仅有未跟踪docs。

- Pi SessionManager.buildSessionInfo提取firstMessage，session-selector用name优先、否则firstMessage；setSessionName单独写session_info。本文采用无额外请求的名称来源，但将自动标题保存到现有SQLite元数据。
- AgentX AbstractMessageHandler正常处理后调用smartRenameSession：首次对话另启线程，用用户默认模型和专用系统提示生成名称，再更新SessionDomainService。证明智能名称需要实际独立调用，本文不默认引入该请求/生命周期/用量用途。
- Pi navigateTree切leaf，buildSessionContext从该路径构造消息；createBranchedSession可将一条路径另存新会话。它说明“保存全部历史”和“当前上下文路径”可以分开，不证明本项目只改checkpoint指针即可。
- Pi assistant-message识别thinking块并独立呈现；AgentX的RagMessageHandler通过onPartialReasoning发送独立思考进度。后者仅作已核对的RAG分支事实，不推广为所有AgentX路径都统一持久化思考。

框架依据：当前安装LangChain1.4.2、LangGraph1.2.12、core/openai1.6.6、anthropic1.7.5。

- [官方时间旅行](https://docs.langchain.com/oss/python/langgraph/use-time-travel)：重放会重新执行后续节点/外部请求/interrupt；update_state创建新checkpoint，不撤销文件副作用。
- [官方消息](https://docs.langchain.com/oss/python/langchain/messages)：content_blocks可统一reasoning；artifact用于程序访问且不作为工具文本发给模型。
- [ChatOpenAI接入边界](https://docs.langchain.com/oss/python/integrations/chat/openai)：第三方reasoning_content等扩展不会由普通ChatOpenAI自动保留。

本机无网络模型探针：标准Anthropic thinking与Responses summary可归一为reasoning且原消息未变化；ChatOpenAI._create_chat_result确实丢失合成CC响应的reasoning_content。create_agent+InMemorySaver探针验证新thread先经START写入空/已有消息，再None+END结束待调度任务，next为空、模型调用数不增加；新消息正常调用一次模型，原thread保留。首次错误尝试把values与as_node=END一起提交，被框架拒绝；最终方案明确分两次更新。此证据没有覆盖本项目权限/恢复全链，不能冒充功能验收。

## 3. 共同的数据与分层

```text
人类提交 / 已送达的控制消息
  → Application准备消息ID、输入种类
  → Runtime把reply_id写入图状态/AI消息
  → checkpoint保存权威事实
  → finish/prepare收集归属、起点与聊天档案
  → Web有界投影 / SSE
  → 同一Conversation按归属呈现，保留消息ID锚点

编辑/重生成动作
  → 应用准入和版本校验
  → 从服务端保存的请求起点准备新thread
  → 保存分支并切换该Session的活动thread
  → 原异步Agent执行、权限、审批、必要保存
```

| 数据 | 含义、权威来源与消费者 |
|---|---|
| session_id | 一条逻辑会话；继续由一个AgentSession拥有行为和运行名额 |
| thread_id | LangGraph执行/保存范围；每个根分支一个，不直接让浏览器指定 |
| reply_id | 一次人类任务的回复归属；由应用输入/图状态产生，不使用run_id替代 |
| message_id/tool_call_id | 原始消息/工具配对身份保持；工具通过真实父AI调用继承归属 |
| branch_id | 会话内版本路径；分支映射根thread，父分支/分叉请求形成版本关系 |
| source_checkpoint_id | 服务端保存的请求起点；仅取明确context，不重放其待调度任务 |

职责保持：ModelAccess处理协议与标准内容；Runtime处理图内归属与事件；Application/AgentSession处理版本、准入及保存；Web负责认证、有界投影及身份检查；前端处理视图和交互。不新增持有Agent的第二套服务或全局Context，也不把SQLite/HTTP放进Runtime。

## 4. 自动标题

推荐首条普通人类任务，折叠空白/控制字符后截取有限字符。新增AppSettings.session_title_max_chars，默认40，范围1—120；样例只更新文档，不动真实配置。空会话仍为空，不用示例数据。

链：原必要finish保存成功 → SessionPersistence._ensure_title读取根档案首条真实普通输入 → SessionStorage.ensure_title以title IS NULL条件更新 → 原会话列表刷新。标题是可降级元信息：生成/写入失败记录固定诊断，沿用现有时间标签，不覆盖已成功的运行或原异常；后续必要finish/prepare可再尝试。普通GET保持只读。

手工重命名与自动生成竞争时，条件SQL确保手工名称保留；已有非空标题保持，切模型/分支不重命名。模型智能概括作为用户可选后续方案，不在本方案暗增调用。

## 5. 请求归属、起点与视觉回复

普通输入开启新reply_id；steering继续当前reply；follow_up实际注入时开启新reply。排队但未送达的补充不伪造成历史消息。审批恢复继承原reply，子任务内部自己的消息归属独立，并保留父请求引用。

例如用户“查登录接口”属于R1；AI说明→Read→Bash→审批暂停/恢复→结论均属R1；途中steering“也看失败分支”仍属R1；结束后注入“再看注册接口”的follow_up属于R2，即使R1/R2共用一个run。

实施方法：

- Application准备真实输入时生成稳定消息ID及input_kind；ActiveRun队列改为最小QueuedInput(message_id,text,kind)，停止保留内容仍返回原文本合同。prepare_control_messages生成权威HumanMessage，再交观察器记录，删除ControlObserver生成必要消息身份的职责；观察故障不改变归属。
- InteractiveRunState增加active_reply_id；初始输入设置该字段，现有before_model/after_agent控制hook分别按steering/follow_up更新。恢复不生成新的reply。
- 新增reply_identity模块的官方after_model hook，最后注册以便反序after_model时先标记AI输出，再进入停止/权限/批次分支；复制消息并补项目元数据，不修改供应商原内容/工具参数。根/子装配共同接入，确切顺序用现有middleware合同测试核对。
- 工具不增加一层执行包装。其reply_id通过真实tool_call_id→父AI消息的标记确定；MessageStateObserver和归档收集器投影该关系。没有匹配父调用的结果明确归属未知，不猜测邻接任务。
- 归档同步保存naiyue_reply_boundaries：session/thread/reply、输入消息、起点checkpoint、最终边界和完成/停止事实。起点取该输入首次进入状态前的父checkpoint；复制时必须验证目标输入不在种子消息内、工具配对完整。与聊天保存同事务，不依赖事件旁路。schema10→11，chat_messages增加可空reply_id，预览SQL也能返回归属。
- 继续finish同步＋历史消费前prepare补录，不改成逐token写档案。压缩保留标记；起点索引引用旧checkpoint，未来修剪必须保护这些锚点。
- Conversation按(reply_id, scope)做视觉容器，内部消息及工具顺序/ID锚点保持，只在整段回复开头显示分隔线，末尾一处操作栏。复制按顺序取已加载模型正文，不包含工具/思考；缺前页时明确“复制已加载内容”，不假装已经取全。跨分页标记还有较早部分，继续原有上滑加载/锚点合同。

旧记录无可靠标记时保留原展示，不根据相邻Human/run_id强行补分组。新轮次有完整事实；旧轮次缺可信起点时可浏览但编辑/重生成不可用，并说明原因。这属于真实历史数据边界，不保留旧执行API或测试专用兼容链。

## 6. 完整文件差异与patch

沿用ToolMessage.artifact，不把diff追加进模型上下文。新增有限file_change结构版本：operation/path/firstChangedLine/diff/preview/patch、各字段完整性、不可用原因及必要字节数；Edit沿用已有采集器，保留原字段含义。Write改为content_and_artifact返回，模型content仍是当前创建/覆盖反馈。

Write在目标占用保护内、提交前获取有限旧文本快照，实际新内容为after。只有已有读取授权时才获取旧正文；纯写授权仍可写，旧差异标为read_not_allowed，不为了装饰UI另发Auto审查或人工审批。新建文件可表示从空内容到实际新内容。目标变化按现有冲突错误处理，二进制/非UTF-8/超预算明确不给完整patch，不伪造旧内容。

Write新增max_diff_input_bytes默认1 MiB、max_diff_bytes/max_preview_bytes默认64 KiB、max_patch_bytes默认1 MiB，与已存在Edit预算对齐但分工具可配；保留无默认执行期限与中途停止检查。反馈准备在不可逆提交之前完成；提交成功后可选Web投影失败不能把写入伪装成未发生；提交结果未知走已有明确错误合同。

链：Write/Edit实际执行 → content+artifact → 原ToolMessage/checkpoint/档案 → 有限file_change快照摘要 → ToolResult；展开/下载通过认证消息详情端点读取已保存数据，不再次读取当前文件。新增GET /api/sessions/{sid}/messages/{sequence}/change及/patch，带revision、branch_id、可选agent_id，服务端校验scope。patch仅在真实完整且未超预算时提供attachment；没有“应用patch/回滚”按钮。

默认页仍受history_message_bytes等预算，增加截断标记及按需详情，不能因artifact撑爆SSE/首屏。任意自定义工具artifact不自动全量公开；希望此视图的自定义工具可返回同一可验证file_change结构，普通文本工具不需改造。父/子共用组件，旧Edit文本结果继续可读。

## 7. 思考内容

新增统一的可见reasoning投影，取实际reasoning块的文字和稳定块索引；保留正文通道。签名、加密内容、任意extras不成为思考正文；原始模型历史和协议所需字段继续由ModelAccess保留，显示转换不回写历史。

| 接口 | 接法 |
|---|---|
| Responses | 从content_blocks取供应商返回的reasoning summary。request.reasoning_summary可选auto/concise/detailed，不配不追加请求参数；只用于明确支持的Responses配置 |
| Anthropic | 标准thinking→reasoning投影；不因为打开UI就提高thinking预算 |
| CC兼容网关 | profile.reasoning_output可选reasoning_content；未配用框架标准块，不能凭模型名字猜字段。声明后使用窄适配器保留完整/流式reasoning_content，并在该协议需要时随assistant历史正确回传；不解析正文中的think标签 |

新增model_access/completions_reasoning.py，沿当前Responses薄适配模式，仅覆盖必要的chunk/result/请求字段转换；对当前固定SDK签名写合同测试，不复制完整客户端或加供应商依赖。thinking.py与ModelCatalog.with_thinking统一合并Responses的effort/summary，避免同时传reasoning_effort与reasoning导致档位被忽略；主/子/摘要/Auto继续共用配置入口。

Runtime增加message_reasoning_delta，单独按message/block序号去重，终稿reasoning快照校准；不把reasoning塞进message_delta的正文。MessageStateObserver、Web当前视图、归档查询及正文详情共用有限投影；摘要/Auto仍按现有标签过滤，不混入用户聊天。

根/子Conversation在对应AI消息处提供默认折叠的“思考内容”区域；无内容不显示，停止/失败沿原未完成标记，不编造“思考了N秒”或将token数当正文。复制回复默认不带思考。reasoning计入现有消息/帧预算，预览可截断并有按需读取；供应商真实用量保持原独立口径。

请求摘要或更多推理可能改变该请求输出量，展示本身不发额外请求。不支持的供应商只显示已有正文，不自动换模型或降级重发。

## 8. 编辑、重新生成与分支

推荐同一逻辑会话内切版本，每个根分支独立thread；一个AgentSession仍只有一个ActiveRun。模型客户端、注册表、工作区和当前会话设置共享，不增加进程或并发名额。当前SessionState新增active_branch_id，thread_id继续是当前执行根；旧分支的图/档案可读但没有活跃driver。

```text
用户1 → 回复1 → 用户2原版 → 回复2原版 → 后续原路径
               ├ 用户2修改版 → 回复2新版 → 新后续路径
               └ 用户2原文   → 回复2再生成 → 另一新路径
```

编辑历史用户消息，或重新生成其整次Agent回复，都从该请求之前的上下文创建新分支。后续旧路径保留；新路径不夹带旧回复/旧工具结果。重生成使用原输入文字，编辑使用新文字；使用当前模型/推理档位/工具集合/权限模式。旧子实例不恢复，新模型委派产生新的实例。

新增BranchRecord持久事实：branch_id/session_id/thread_id/parent_branch_id/fork_reply_id/source_checkpoint_id/creation_request_id/status/created_at。新增应用内部session_branches模块实现AgentSession的受控分支操作，branch_storage负责SQL；不新增第二个SessionService。

保存与失败顺序：

1. 原应用操作生命周期与根准入先保证空闲/额度；校验源branch、reply、选择revision和恢复门禁。运行、待审批、恢复未完成、结果需核对时可以只读浏览，不能通过分支绕过执行门禁。
2. 用服务端起点索引读取源context；只复制messages及明确的会话上下文事实，不复制tool_execution、一次性许可、待执行Send、旧run_stopped/控制队列/driver。验证目标输入不在前缀、没有未配对调用。
3. 新thread经官方aupdate_state的START writer写种子，再None+END清除调度；验证next/interrupt为空后才接受新的普通输入。不能把END与非空values合并，也不直接invoke旧checkpoint恢复任务。
4. checkpoint种子先准备，随后一个元数据事务复制档案前缀引用、建立分支并切活动thread；必要保存采用既有取消收尾。失败在切换前保留旧分支；切换后运行失败保留新分支的真实失败状态，不能靠切回旧指针掩盖已发生的副作用。
5. 原AsyncApplication/SessionExecution开始新运行，权限管线重新评估；之前的一次性授权不复用。本会话allow及当前模式仍按原合同生效，显式Deny优先。
6. creation_request_id持久唯一；重复动作返回已存在分支/状态，不再次启动。跨重启无活跃job时返回recovery_required或已保存状态，不编造可订阅的旧run。准备中分支只作staging记录，不自动执行；源分支仍可读。没有新增checkpoint清理/删除策略。

档案保存不能按每个版本复制整份正文。schema11→12增加分支表与会话内不可变chat_bodies(session_id,body_hash,body_json)，消息行持有body_hash引用。创建分支用SQL复制所选前缀的有限身份/版本引用，正文同会话去重；旧引用不会随源消息修订而变。现有查询join后仍返回原有分块/预览合同；不为旧测试保留冗余body_json列。种子context由原checkpoint提供，完整聊天前缀由档案提供，两者不能互相冒充。删除整会话同时清理其分支/正文引用，不影响工作区文件和其他会话。

子实例新身份加入分支范围；继承前缀保留旧实例的真实来源引用，只读查询仍检查该分支中的父调用。归档收集只同步当前根和本次相关子范围，不能随分支数增长每次扫描所有旧根/旧子图。

API为新增或可选参数，不删除现有公开应用签名：AsyncApplication.afork_reply/aswitch_branch，Web GET /api/sessions/{sid}/branches、POST /api/sessions/{sid}/branches、PUT /api/sessions/{sid}/active-branch。创建动作包含request_id/source_branch_id/reply_id/action/text及expected_branch_revision；浏览器不提交任意thread或checkpoint。返回branch_id、状态和仅确有活跃job时的run_id。普通发送可带expected branch，未指定时原子选当前活动分支；新前端总是传明确版本。

Web session/run投影增加branch_id/选择revision；分页cursor绑定session/branch/agent/窗口/修订，切分支取消旧读取与SSE，迟到响应不能写入新版本。版本选择器展示如1/2，选择只切上下文不执行；“编辑并发送/重新生成”才开启新运行。子侧栏仍统一主会话审批。用量按原run_id保存，累计包含所有版本实际花费，复制前缀不复制计费记录，上下文估算只看当前分支。

**文件状态不回滚。** 例如旧回复已把文件改为v2，切到此前聊天版本时文件仍是v2。若重做会重新运行写文件/Bash/外部操作，动作前提示“工作区未回滚，工具可能再次执行”；已知有副作用时要求本次明确确认。之后照常按当前权限模式执行，不把此提示变成另一套权限系统或自动撤销机制。

## 9. 主要文件与方法

本批明显超过8个文件，按职责分片；下列为生产落点，均需对应行为/故障测试，不把全部方法堆入现有大文件。

| 范围 | 扩展现有入口 / 必要新增模块 |
|---|---|
| 标题 | settings.py/AppSettings；session_persistence.py/_ensure_title；SessionStorage.ensure_title；文档配置样例 |
| 输入归属 | session_state.py/QueuedInput、ActiveRun队列；session_controls.py/mark_human_messages、prepare_control_messages；ControlObserver改为观察已生成消息；SessionPort受控输入消费；agent_runtime/__init__.py两个注入hook/图字段 |
| 图与事件 | 新agent_runtime/reply_identity.py；message_observation.py/_snapshot、observe；async_message_observation.py/delta；stream_events.py/events.py及根/子装配注册 |
| 归属保存 | 新application/reply_storage.py；chat_history.py/collect_chat_updates；session_storage.py保存事务/迁移；chat_queries.py有界读取 |
| 差异 | tools/write.py、edit_diff.py、settings.py；新增tools/file_change.py有限结构/投影，现有ToolResult替换文本解析主路径；Web消息详情端点 |
| 思考 | 新model_access/completions_reasoning.py；model_access/__init__.py构造、profile/request校验及with_thinking；thinking.py、messages.py内容投影；新增前端ReasoningContent组件 |
| 分支 | 新application/session_branches.py、branch_storage.py；AgentSession/SessionState受控切换；SessionPersistence准备/恢复与保存；chat_queries归属查询；subagent_history/session_subagents分支身份 |
| Web与UI | schemas.py/service.py/app.py、serialization.py、chat_history.py、run_delivery及现有投影；types/events/api/useConversation/useMessageHistory/useSubagents的scope；Conversation回复容器、新BranchControls/EditMessage；复用现有模型/审批/工具组件 |

新增模块均有当前消费者：reply_identity服务根/子图，reply_storage服务finish/prepare/历史，file_change服务两种工具与有界Web，CC适配仅服务声明该协议的模型，branch_storage/session_branches服务两种动作和只读版本。没有通用插件加载器、可替换存储平台或新全局回调。

## 10. 实施拆分与验收

| 片 | 内容 | 必须验证的核心合同 |
|---|---|---|
| 111-01 | 自动标题 | 空值/超长/中文、手动竞争、失败不影响主结果、重开保存、零新增模型请求 |
| 111-02 | 权威输入种类与reply图事实 | 普通/steering/follow_up、审批跨run、根子、压缩、停止与观察故障不改变归属；hook真实顺序 |
| 111-03 | 归属/起点保存及视觉回复 | schema11、finish/prepare/崩溃补录、跨页锚点、一次操作栏、旧记录明确缺少能力 |
| 111-04 | Write/Edit结构化差异与patch | 实际before/after、授权读取、目标变化、大文件/Unicode/换行/符号链接、截断、刷新、根子、下载scope、0新执行请求 |
| 111-05 | 标准与CC思考全链 | 合成完整/流式/签名/加密块、CC请求回传、effort/summary合并、停止/失联/分页预算、根子/摘要/Auto过滤 |
| 111-06 | 分支存储与受控上下文切换 | schema12、正文引用冻结/去重、staging崩溃点、同会话准入、冷读取无图执行、跨版本cursor拒绝 |
| 111-07 | 编辑/重生成应用执行及API | 起点准确、清调度不调用模型工具、新权限/子实例、幂等/迟到/保存失败/重启、旧文件不回滚 |
| 111-08 | 编辑/版本选择UI | 主会话编辑器与取消/草稿、原文重生成、版本路径后续、状态/焦点/375px、侧栏沿选中分支 |
| 111-09 | 五项组合验收 | 长历史+压缩+审批+子任务+新版本+两次重启，分别核对聊天、checkpoint、真实副作用次数和累计用量 |

每片先失败测试再最小接线，沿当前生产入口迁移旧消费者，不放宽断言或增测试专用兼容逻辑。全量命令继续为带原生工具PATH的pytest、frontend vitest、TypeScript/Vite build；复杂分支先固定离线模型/临时SQLite/故障注入，再做1440/375真实页面。真实供应商模型与调用预算另获授权，不在讨论轮发请求；无新账号/密钥或依赖要求。

数据迁移必须事务验证schema10/11→最终版本、失败回滚、原历史/审批/用量不丢；升级后的分支格式不能声称可无数据迁移退回旧程序，实施前需临时库恢复演练。未改真实配置/用户库，不执行发布、删除用户数据或Git提交。

## 11. 本轮需要统一确认的产品选择

1. 标题采用首条任务的本地短文本，还是增加一次模型生成？推荐本地短文本。
2. 编辑/重生成采用同一会话内保留版本，还是首版仅另开新会话？推荐同会话版本，并承担第8节明确的存储/恢复工作。
3. 重生成重新执行整次Agent任务，可能调用工具，且不回滚文件；还是只让模型重写最终文字？推荐前者，按当前权限重新评估，副作用提示与旧记录起点限制如上。

其他推荐边界：Write没有读取授权就不取得旧正文；思考只显示真实返回，CC扩展明确配置；无可信归属/起点的旧轮次保留浏览。若用户调整这些语义，先同步本稿，不进入实现后再临时改合同。
