# Spec111：剩余会话呈现、末尾重生成与内容投影

日期：2026-10-18。状态：**已按批准范围实现并完成本机离线验收；结果见第13节；已纳入本批提交范围，具体Git状态以仓库记录为准**。

用户要求把Spec103剩余五项一次讨论清楚。本稿覆盖B3自动标题、U1请求/回复归属、完整Write/Edit差异、U12主会话最后一次任务重新生成、U13供应商可展示思考。用户已确认本地标题、仅末尾重生成、新结果替换旧回复、整任务重做且不回滚文件；不做历史编辑和版本选择。此前纯前端批及Spec110边界清理均已提交，实施以当前异步入口为准，不回退或复制已替换的转发层。

## 1. 总体建议与外部行为

| 能力 | 本稿推荐 | 模型请求与复杂度 |
|---|---|---|
| 自动标题 | 首条有效人类任务生成有限长度文本标题，只在title为空时保存，不覆盖手工名称 | 无额外模型调用；小 |
| 回复归属 | 人类任务有稳定reply_id；中间说明、AI调用和工具结果归同一视觉回复；steering补充本任务，follow_up开始新任务 | 无额外调用；中 |
| 工具差异 | 执行时采集真实有限差异，用ToolMessage.artifact承载，模型文本反馈与UI数据分开 | 无额外调用；中 |
| 末尾重新生成 | 只重做最后一次人类任务，新回复替换该次旧回复，后续基于新结果；不提供历史编辑或版本切换 | 用户触发新的正常Agent运行；中，需要必要保存和防重复执行 |
| 思考展示 | 展示供应商明确返回的可见思考/摘要；标准块复用框架，明确配置的CC扩展薄适配 | 展示不另发请求；申请摘要可能增加原请求输出/计费；中 |

采用当前最小完整路径：本地标题、稳定回复、真实差异/思考与末尾重生成。不再提供“另开会话还是保留版本”的选择；原完整分支候选已被用户否决，不能在实现中以扩展性为由加回分支表、版本API或正文去重平台。

本轮不增加历史消息编辑、历史版本浏览/选择、文件自动回滚、工作区快照、Git worktree、子实例单独重生成、任意图节点时间旅行、后台Agent、新沙箱、checkpoint修剪或项目重试。主任务重新执行可自然创建新子实例；较早任务的子实例继续只读，被替换末尾任务的旧子实例不再出现在当前聊天列表。

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
- [OpenAI可见思考摘要](https://developers.openai.com/api/docs/guides/reasoning#reasoning-summaries)：原始reasoning token不公开，summary需要显式请求且支持情况依模型而异；auto选择模型支持的最详细摘要，不能当作最短/最省的选项。

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

末尾重生成动作
  → 应用准入、最后任务和当前会话修订校验
  → 从服务端保存的请求起点准备新thread
  → 必要事务替换聊天末尾并切换该Session的活动thread
  → 原异步Agent执行、权限、审批、必要保存
```

| 数据 | 含义、权威来源与消费者 |
|---|---|
| session_id | 一条逻辑会话；继续由一个AgentSession拥有行为和运行名额 |
| thread_id | LangGraph执行/保存范围；会话只有一个活动根，重生成先准备新根再原子替换，不直接让浏览器指定 |
| reply_id | 一次人类任务的回复归属；由应用输入/图状态产生，不使用run_id替代 |
| message_id/tool_call_id | 原始消息/工具配对身份保持；工具通过真实父AI调用继承归属 |
| conversation_revision | 仅替换末尾时递增的会话修订，拒绝旧动作和迟到显示，不代表可切换的历史版本 |
| source_checkpoint_id | 服务端保存的请求起点；仅取明确context，不重放其待调度任务 |

职责保持：ModelAccess处理协议与标准内容；Runtime处理图内归属与事件；Application/AgentSession处理末尾替换、准入及保存；Web负责认证、有界投影及身份检查；前端处理视图和交互。不新增持有Agent的第二套服务或全局Context，也不把SQLite/HTTP放进Runtime。

## 4. 自动标题

推荐首条普通人类任务，折叠空白/控制字符后截取有限字符。新增AppSettings.session_title_max_chars，默认40，范围1—120；样例只更新文档，不动真实配置。空会话仍为空，不用示例数据。

链：原必要finish保存成功 → SessionPersistence._ensure_title读取根档案首条真实普通输入 → SessionStorage.ensure_title以title IS NULL条件更新 → 原会话列表刷新。标题是可降级元信息：生成/写入失败记录固定诊断，沿用现有时间标签，不覆盖已成功的运行或原异常；后续必要finish/prepare可再尝试。普通GET保持只读。

手工重命名与自动生成竞争时，条件SQL确保手工名称保留；已有非空标题保持，切模型/重新生成不重命名。模型智能概括不在本方案暗增调用。

## 5. 请求归属、起点与视觉回复

普通输入开启新reply_id；steering继续当前reply；follow_up实际注入时开启新reply。排队但未送达的补充不伪造成历史消息。审批恢复继承原reply，子任务内部自己的消息归属独立，并保留父请求引用。

例如用户“查登录接口”属于R1；AI说明→Read→Bash→审批暂停/恢复→结论均属R1；途中steering“也看失败分支”仍属R1；结束后注入“再看注册接口”的follow_up属于R2，即使R1/R2共用一个run。

实施方法：

- Application准备真实输入时生成稳定消息ID及input_kind；ActiveRun队列改为最小QueuedInput(message_id,text,kind)，停止保留内容仍返回原文本合同。prepare_control_messages生成权威HumanMessage，再交观察器记录，删除ControlObserver生成必要消息身份的职责；观察故障不改变归属。
- middleware状态增加active_reply_id；初始输入在现有异步调用入口设置该字段，不新增before_model图节点；现有before_model/after_agent控制hook分别按steering/follow_up更新。恢复不生成新的reply。
- 新增reply_identity模块的ReplyIdentityMiddleware.awrap_model_call，放在模型包装最外层，正常返回时复制ModelResponse中的AI消息并补项目归属。它必须在结果首次写入图之前生效；仅用after_model会先产生一份无归属的模型结果checkpoint/快照。异常保持原身份传播，供应商原内容/工具参数保持。根/子装配共同接入，确切顺序用现有middleware合同测试核对；不新增同步应用执行链。
- 工具不增加一层执行包装。其reply_id通过真实tool_call_id→父AI消息的标记确定；MessageStateObserver和归档收集器投影该关系。模型增量从当前图状态的active_reply_id取归属，终稿再以已标记的消息校准，不等待after_model后重新排列。没有匹配父调用的工具结果明确归属未知，不猜测邻接任务。
- 归档同步保存naiyue_reply_boundaries：session/thread/reply、输入消息、起点checkpoint、最终边界和完成/停止事实。起点取该输入首次进入状态前的父checkpoint；复制时必须验证目标输入不在种子消息内、工具配对完整。与聊天保存同事务，不依赖事件旁路。schema10→11，chat_messages增加可空reply_id，预览SQL也能返回归属；保存边界用于可靠识别末尾，不开放历史重做。
- 继续finish同步＋历史消费前prepare补录，不改成逐token写档案。压缩保留标记；起点索引引用旧checkpoint，未来修剪必须保护这些锚点。
- Conversation按(reply_id, scope)做视觉容器，内部消息及工具顺序/ID锚点保持，只在整段回复开头显示分隔线，末尾一处操作栏。复制按顺序取已加载模型正文，不包含工具/思考；缺前页时明确“复制已加载内容”，不假装已经取全。跨分页标记还有较早部分，继续原有上滑加载/锚点合同。

旧记录无可靠标记时保留原展示，不根据相邻Human/run_id强行补分组。新轮次有完整事实；最后一轮若缺可信起点则保留浏览、重生成不可用，并说明原因。这属于真实历史数据边界，不保留旧执行API或测试专用兼容链。

## 6. 完整文件差异与patch

沿用ToolMessage.artifact，不把diff追加进模型上下文。新增有限file_change结构版本：operation/path/firstChangedLine/diff/preview/patch、各字段完整性、不可用原因及必要字节数；Edit沿用已有采集器，保留原字段含义。Write改为content_and_artifact返回，模型content仍是当前创建/覆盖反馈。

Write在目标占用保护内、提交前获取有限旧文本快照，实际新内容为after。只有已有读取授权时才获取旧正文；纯写授权仍可写，旧差异标为read_not_allowed，不为了装饰UI另发Auto审查或人工审批。新建文件可表示从空内容到实际新内容。目标变化按现有冲突错误处理，二进制/非UTF-8/超预算明确不给完整patch，不伪造旧内容。

Write新增max_diff_input_bytes默认1 MiB、max_diff_bytes/max_preview_bytes默认64 KiB、max_patch_bytes默认1 MiB，与已存在Edit预算对齐但分工具可配；保留无默认执行期限与中途停止检查。反馈准备在不可逆提交之前完成；提交成功后可选Web投影失败不能把写入伪装成未发生；提交结果未知走已有明确错误合同。

链：Write/Edit实际执行 → content+artifact → 原ToolMessage/checkpoint/档案 → 有限file_change快照摘要 → ToolResult；展开/下载通过认证消息详情端点读取已保存数据，不再次读取当前文件。新增GET /api/sessions/{sid}/messages/{sequence}/change及/patch，带消息revision、conversation_revision、可选agent_id，服务端校验当前scope。patch仅在真实完整且未超预算时提供attachment；没有“应用patch/回滚”按钮。

默认页仍受history_message_bytes等预算，增加截断标记及按需详情，不能因artifact撑爆SSE/首屏。任意自定义工具artifact不自动全量公开；希望此视图的自定义工具可返回同一可验证file_change结构，普通文本工具不需改造。父/子共用组件，旧Edit文本结果继续可读。

## 7. 思考内容

新增统一的可见reasoning投影，取实际reasoning块的可见文字；首版在每条消息内合并为一个显示通道（block_index=0）；保留正文通道。签名、加密内容、任意extras不成为思考正文；原始模型历史和协议所需字段继续由ModelAccess保留，显示转换不回写历史。

| 接口 | 接法 |
|---|---|
| Responses | 从content_blocks取供应商返回的reasoning summary。request.reasoning_summary可选auto/concise/detailed，不配不追加请求参数；只用于明确支持的Responses配置 |
| Anthropic | 标准thinking→reasoning投影；不因为打开UI就提高thinking预算 |
| CC兼容网关 | profile.reasoning_output可选reasoning_content；未配用框架标准块，不能凭模型名字猜字段。声明后使用窄适配器保留完整/流式reasoning_content，并在该协议需要时随assistant历史正确回传；不解析正文中的think标签 |

新增model_access/completions_reasoning.py，沿当前Responses薄适配模式，仅覆盖必要的chunk/result/请求字段转换；对当前固定SDK签名写合同测试，不复制完整客户端或加供应商依赖。模型构造与ModelCatalog.with_thinking统一合并Responses的effort/summary，避免同时传reasoning_effort与reasoning导致档位被忽略；主/子/摘要/Auto继续共用配置入口。

Runtime增加message_reasoning_delta，单独按message序号去重（block_index固定0，不承诺多块交错重建），终稿reasoning快照校准；不把reasoning塞进message_delta的正文。MessageStateObserver、Web当前视图、归档查询及正文详情共用有限投影；摘要/Auto仍按现有标签过滤，不混入用户聊天。

根/子Conversation在对应AI消息处提供默认折叠的“思考内容”区域；无内容不显示，停止/失败沿原未完成标记，不编造“思考了N秒”或将token数当正文。复制回复默认不带思考。reasoning计入现有消息/帧预算，预览可截断并有按需读取；供应商真实用量保持原独立口径。

请求摘要或更多推理可能改变该请求输出量，展示本身不发额外请求。不支持的供应商只显示已有正文，不自动换模型或降级重发。

## 8. 仅最后一次任务重新生成

只在主会话最后一次任务处提供“重新生成”。会话空闲、必要保存已确认、无待审批/未完成恢复/结果待核对时才允许执行；旧任务不能重做，子侧栏不提供独立重做。新结果替换该次旧回复，不提供历史编辑、版本切换或从旧回复继续聊天。

```text
用户1 → 回复1 → 用户2 → 回复2 A
                         ↓ 点击末尾重新生成
用户1 → 回复1 → 用户2 → 回复2 A2 → 后续对话
```

重做使用当前模型/推理档位/工具集合/权限模式。重发原任务实际收到的人类输入，包括该任务已送达的steering；未送达队列不伪造成历史。旧模型回复、工具结果及执行许可不作为新任务上下文。较早任务及其子实例的真实档案保留；被替换任务重新委派时创建新子实例，不复用旧driver。

一个AgentSession仍只有一个ActiveRun，不增加进程、独立SessionService或额外模型客户端。内部先准备新thread，只为在必要保存提交前保护当前上下文；这不是可选择的用户版本。SessionState不增加active_branch_id，仅增加conversation_revision表示末尾替换后的当前修订。

正常与失败链：

1. 新增AsyncApplication.aregenerate_last_reply，经当前应用生命周期/根准入，校验request_id、expected_reply_id及expected_conversation_revision。目标必须仍是服务端最后任务，源checkpoint只由reply边界读取。重复请求返回原动作状态；并发普通发送/重生成继续由同会话准入互斥。
2. 从任务起点读取context，复制messages及明确的上下文事实，不复制tool_execution、一次性许可、待执行Send、run_stopped/队列/driver。验证目标任务输入不在前缀、工具配对完整。若起点缺失，拒绝动作并保留旧结果，不凭UI文本猜测上下文。
3. 新thread经官方aupdate_state的START writer写前缀，再None+END清调度并保存请求前锚点；随后同样两次更新保存实际重发的人类输入，避免激活后崩溃丢失输入。验证next/interrupt为空，四次准备更新均不得调用模型或工具。旧活动根在此时仍可读；准备失败不切换，不删除旧结果。
4. 一个必要元数据事务记录重做动作，更新活动thread/会话修订/保存身份，并替换可见档案末尾：较早根消息在SQL中迁移scope，保留正文/消息ID/序号；删除目标任务及之后的旧显示记录和对应不再引用的子档案。新head直接指向准备好的checkpoint，不能把已压缩的context当作完整历史。切换前失败事务回滚；切换后不得靠切回旧结果掩盖已经执行的副作用。
5. 在同一次准入中进入现有SessionExecution阶段，生成新的输入身份/reply_id并正常执行、审批及finish，不递归调用公开arun重复准入。一次性授权不复用；session_allow、当前模式和显式Deny继续按原合同评估。若新运行停止/失败，保留新任务真实状态及原恢复流程。
6. request_id在持久重做动作表中按会话唯一，记录目标、源/目标thread、修订、实际run_id及状态。事务后进程崩溃由现有必要保存/启动恢复处理，不自动重新发请求；重试同一动作返回已保存状态，仅有真实Web job时才给可订阅身份。事务前遗留准备checkpoint不自动执行，也不引入checkpoint清理规则。

schema11→12只增加末尾重做动作表、会话conversation_revision及消息source_thread_id。scope迁移时保留原(source_thread_id,source_checkpoint_id)，不能把旧来源伪造成新thread产生。现有body_json/body_hash保持，不引入分支表、chat_bodies表或多版本正文去重。较早reply边界保留真实来源，新任务边界使用准备好的新起点；所有查询/保存仍走当前拥有者和参数化SQL。整会话删除清理新动作记录；不删除工作区文件，用量和诊断仍保留所有实际运行。

归档收集改为当前活动根及其真实父调用引用的子范围，不扫描被替换旧根/孤立旧子图。UI中的旧结果替换不等于删除框架checkpoint；后者继续既有保存/整会话删除合同，安全修剪仍明确延期。

新增认证POST /api/sessions/{sid}/regenerate，参数request_id/expected_reply_id/expected_conversation_revision；浏览器不提交任意thread/checkpoint或替换后的文本。返回当前修订、动作状态及真实job身份。没有branches或切换版本API。普通发送可选携带expected_conversation_revision，未传时原子采用当前值；新UI总是显式校验，现有公开发送签名保持。

Web session/run、分页及消息详情绑定当前修订/活动scope；重做时取消旧历史/SSE读取，迟到响应不能覆盖新结果。只有最后回复操作栏显示重生成入口；运行、审批及恢复门禁给出已有状态，成功启动后仍用现有消息/进展/停止组件。根子审批继续统一主会话。累计用量包含新旧实际调用，上下文估算只看当前上下文，不因显示替换扣减历史费用。

**文件状态不回滚。** 旧回复已把文件改为v2，重生成时文件仍是v2。动作提示“工作区未回滚，工具可能再次执行”；已知有副作用时要求本次明确确认。之后照常按当前权限模式执行，不把此提示变成另一套权限系统或自动撤销机制。

## 9. 主要文件与方法

本批明显超过8个文件，按职责分片；下列为生产落点，均需对应行为/故障测试，不把全部方法堆入现有大文件。

| 范围 | 扩展现有入口 / 必要新增模块 |
|---|---|
| 标题 | settings.py/AppSettings；session_persistence.py/_ensure_title；SessionStorage.ensure_title；文档配置样例 |
| 输入归属 | contracts/replies.py/QueuedInput、session_state.py/ActiveRun队列；session_controls.py/mark_human_messages、prepare_control_messages；ControlObserver改为观察已生成消息；SessionPort受控输入消费；agent_runtime/__init__.py两个注入hook/图字段 |
| 图与事件 | 新agent_runtime/reply_identity.py；message_observation.py/_snapshot、observe；async_message_observation.py/delta；stream_events.py/events.py及根/子装配注册 |
| 归属保存 | 新application/reply_storage.py；chat_history.py/collect_chat_updates；session_storage.py保存事务/迁移；chat_queries.py有界读取 |
| 差异 | tools/write.py、edit_diff.py、settings.py；新增tools/file_change.py有限结构/投影，现有ToolResult替换文本解析主路径；Web消息详情端点 |
| 思考 | 新model_access/completions_reasoning.py；model_access/__init__.py构造、profile/request校验及with_thinking；新增model_access/reasoning.py有限投影；新增前端ReasoningContent组件 |
| 末尾重生成 | 新application/session_regeneration.py负责AgentSession受控操作；reply_storage.py保存动作/末尾事务；SessionState/SessionPersistence当前根与修订；chat_queries及子档案保留真实来源 |
| Web与UI | schemas.py/service.py/app.py、serialization.py、chat_history.py、run_delivery及现有投影；types/events/api/useConversation/useMessageHistory/useSubagents的scope；Conversation回复容器和最后任务重做操作；复用现有模型/审批/工具组件 |

新增模块均有当前消费者：reply_identity服务根/子图，reply_storage服务finish/prepare/历史及末尾替换，file_change服务两种工具与有界Web，CC适配仅服务声明该协议的模型，session_regeneration服务最后任务重做。没有通用插件加载器、可替换存储平台或新全局回调。

## 10. 实施拆分与验收

| 片 | 内容 | 必须验证的核心合同 |
|---|---|---|
| 111-01 | 自动标题 | 空值/超长/中文、手动竞争、失败不影响主结果、重开保存、零新增模型请求 |
| 111-02 | 权威输入种类与reply图事实 | 普通/steering/follow_up、审批跨run、根子、压缩、停止与观察故障不改变归属；hook真实顺序 |
| 111-03 | 归属/起点保存及视觉回复 | schema11、finish/prepare/崩溃补录、跨页锚点、一次操作栏、旧记录明确缺少能力 |
| 111-04 | Write/Edit结构化差异与patch | 实际before/after、授权读取、目标变化、大文件/Unicode/换行/符号链接、截断、刷新、根子、下载scope、0新执行请求 |
| 111-05 | 标准与CC思考全链 | 合成完整/流式/签名/加密块、CC请求回传、effort/summary合并、停止/失联/分页预算、根子/摘要/Auto过滤 |
| 111-06 | 末尾重生成的保存、执行、API与UI | schema12、起点准确、较早完整历史保留、末尾替换、清调度零执行、新权限/子实例、幂等/迟到/保存失败/重启、旧文件不回滚、375px入口 |
| 111-07 | 五项组合验收 | 长历史+压缩+审批+子任务+末尾重做+两次重启，分别核对聊天、checkpoint、真实副作用次数和累计用量 |

以上7片是同一批内部依赖与验证单元，不要求用户逐片确认；收到整批实施指令后连续做到组合验收。内部顺序为标题→归属→保存/显示→差异→思考→末尾重做→组合验收，每单元先失败测试再最小接线，不在上一单元红灯时叠加新功能；沿当前生产入口迁移旧消费者，不放宽断言或增测试专用兼容逻辑。

验收命令：根目录PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q；frontend目录npm test -- --run、npm run build；git diff --check及500行/分层/接线检查。先用固定离线模型、临时SQLite和故障注入验证，再做1440/375真实页面，检查正文/思考/差异/末尾重做及真实工具副作用次数。真实供应商与预算另获授权，不在本批默认请求；无新账号/密钥或依赖要求。

数据迁移必须事务验证schema10/11→12、失败回滚、原历史/审批/用量不丢，使用临时库做升级/恢复演练。schema升级不能声称可仅回退代码降级。仅改文档配置样例，真实配置/用户库不在本批直接修改范围；不执行发布、用户数据清理或Git提交。末尾显示替换只由用户明确点击重生成的正常功能动作触发。

## 11. 已确认选择与实施边界

1. 本地首任务短标题，不增加模型生成标题的请求；用户已确认。
2. 只重新生成最后一次任务，新回复作为后续依据；历史编辑、完整分支及版本切换移出本轮；用户已确认。
3. 重做整次Agent任务，可能再次调用工具，工作区文件不回滚，按当前权限重新评估；用户已确认。

Write没有读取授权就不取得旧正文；思考只显示真实返回，CC扩展明确配置；最后一轮无可信起点时保留浏览、不给不可靠重做。没有新增阻塞产品决策。本轮已按第10节连续实施并验收，真实结果和保留边界见第13节。

## 12. 实施复核补充边界

实施验收补入：超预算历史行仍以有界SQL提取合法file_change元信息，保留详情入口；所有正文/思考/差异详情均绑定conversation_revision，普通发送必须实际传递页面修订；Write的旧内容读取审查复用已核验requested/resolved事实，不能因软链接解析丢掉别名上的Read Deny；提交冲突比较设备/inode/size/mtime/ctime，不将读取导致的atime变化视为内容修改。无实际改动时firstChangedLine为空。以上先有失败回归再修复，不扩大到历史版本切换。


## 13. 实际实现、调用链与验收（2026-10-18）

七个内部单元已连续完成，范围为首任务本地标题、权威reply归属/一次操作栏、Write/Edit真实差异、最后任务重生成及真实可见思考。没有历史编辑/版本选择、文件回滚、新模型请求重试或checkpoint修剪。数据库schema12沿现有迁移接入；所有生产源文件低于500行，未增加依赖或真实模型配置。

### 13.1 可检查的当前主链

- 普通输入：`AsyncPersistentApplication.arun → SessionService/AgentSession.arun → mark_human_messages`产生真实HumanMessage ID、`naiyue_human_input`、`naiyue_reply_id`、input_kind；初始invoke写active_reply_id。`ReplyIdentityMiddleware.awrap_model_call`在模型结果首次写图前标记AI消息；ToolMessage通过真实父tool_call_id归属。steering沿当前reply，follow_up打开新reply，审批恢复保留原图事实。`collect_chat_updates → SessionStorage.finish_run`同事务保存消息与起点；不逐token写档案。
- 本地标题：必要finish/prepare成功后，`SessionPersistence._ensure_title → SessionStorage.ensure_title`从首条根任务生成有界文本，SQL仅在title为空时写入；手工名称不被覆盖。标题IO失败只写固定诊断，原执行结果/异常不改变。预算`session_title_max_chars`默认40，样例已注释。
- 文件变更：`Write → require_file_target → _write`在目标占用内核验原许可和目标；`read_feedback_allowed`保留原requested/resolved事实，独立匹配Read Deny/Allow/默认策略。`write_feedback`在提交前收集有限before/after，Edit沿原采集器加结构标识；模型content不增加diff。`ToolMessage.artifact → 快照/档案 → change_projection → FileChange`，小摘要在首屏，完整保存数据由认证change/patch端点按消息revision+conversation_revision+根子归属读取。超预算首屏通过SQL标量摘要保留入口，未读取整份artifact。
- 可见思考：模型标准content_blocks或明确启用的CC适配器 → `visible_reasoning` → 独立`message_reasoning_delta`与最终消息快照 → 根/子共用`ReasoningContent`。正文和思考索引独立；快照修正草稿。签名/加密块不显示，异常块只记录类型并降级，摘要/Auto事件仍被过滤。归档大消息有有限reasoning预览，按需读取不调用模型。
- 最后任务重生成：`WebApi.regenerate → WebService.regenerate → RunDelivery → AsyncPersistentApplication.aregenerate_last_reply → session_regeneration.regenerate`。准入后校验末尾reply及epoch，按保存的起点恢复前缀；四次官方START/END更新保存前缀、请求前锚点、实际重发输入并清调度，准备阶段零模型/工具执行。`reply_storage.activate + AgentSession.activate_regenerated_thread`在同一取消结算工作调用完成DB/内存切换，然后沿现有`AgentSession.arun`执行/审批/保存，只有一次ActiveRun。较早正文保留身份及来源，旧末尾和不再引用的子档案退出当前显示，新子调用有新实例。请求幂等记录持久化，旧Web SSE/分页/正文/差异/思考请求受当前修订隔离。

失败链已验证：起点/修订错误和准备写失败不切根；激活SQL故障事务回滚原末尾；激活窗口取消会等DB及内存切换结算再释放名额，保存的原输入可经重启恢复且不自动执行工具。切换后的实际工具失败/停止继续原恢复合同，不切回旧结果。普通缺记录正文仍404，旧修订为409。重新生成会再次执行工具，文件保持当时真实状态。

### 13.2 配置与旧消费者迁移

CC网关要保留其非标准字段时，在对应模型加`"profile": {"reasoning_output": "reasoning_content"}`；仅适用于明确声明该协议的chat_completions。Responses可配`"request": {"reasoning_summary": "auto"}`（也支持concise/detailed），未配不新增summary字段；切换推理档位保留summary。供应商没返回可见内容时不显示面板；本轮未替用户开启任何真实供应商配置。

旧ControlObserver生成必要输入身份的入口已迁移为观察已准备消息的record_messages；排队string消费者改为QueuedInput，未保留平行兼容路径。历史记录本身无reply标记时仍可浏览，不猜造边界。schema历史测试用真实旧表布局辅助函数移除后续字段后构造旧版本，再验证完整迁移，没有仅把版本号改旧而留下新结构；基础helper修改仅服务这种迁移事实。旧Edit文本显示继续服务实际历史数据。原同步工具IO/框架hook保留真实消费者，未恢复同步应用执行链。

### 13.3 本轮实际运行证据

| 验证 | 结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q -o faulthandler_timeout=60` | **2946 passed in125.64s**；`/tmp/naiyue-111-backend-final.log`。faulthandler仅诊断挂起，不增加产品执行期限 |
| 随后新增一条完整组合 `pytest -q tests/test_conversation_features_acceptance.py` | **1 passed in1.20s**；长历史→压缩→第一次重开→子Read主会话审批→最后任务重生成→新的子审批→第二次重开；固定11次离线调用，完整聊天前缀不变、压缩context保留、旧子入口失效、累计11次/110输入/55输出且查询不新增调用 |
| 末尾/取消/子实例组合 `pytest -q tests/test_last_regeneration.py tests/test_web_regeneration.py` | **8 passed in1.80s**；含激活事务故障和取消结算窗口 |
| 修后文件/历史专项 `pytest -q tests/test_file_change.py tests/test_web_file_change.py tests/test_web_regeneration.py tests/test_history_page_contract.py tests/test_write_tool.py` | **50 passed in2.62s**；相应缺口先有红灯 |
| `npm --prefix frontend test -- --run` | **39文件289 passed**；最终日志`/tmp/naiyue-111-ui-final2.log` |
| `npm --prefix frontend run build` | TypeScript零错误，Vite成功；JS gzip209.27kB，保留现有>500kB chunk提示；`/tmp/naiyue-111-build-final.log` |
| 结构与依赖 | 429份Python AST、Python/前端源文件500行检查、compileall、pip check、git diff --check通过 |

架构复核确认的较大差异入口、旧正文修订，安全复核确认的软链接Read Deny及前端epoch实传均已补红灯并修复；窄范围独立复验12项后端、5项前端通过，无确认遗留。另修复反馈读取atime误判冲突、空修改首行错误、观察异常块和额外hook挤占既有图步数等本轮回归，保留原断言和执行合同。首轮测试中的旧schema/旧输入身份/原工具返回消费者已按当前生产合同迁移，没有skip或放宽断言。

真实浏览器使用最终生产构建、临时Web/SQLite/工作区和原生图，固定6次离线模型调用、2次Read及2次Write、0次真实供应商。首任务自动标题；整次工具循环1条分隔线/1组复制操作；实际patch下载内容为原文件到第一版；重生成后仅第二版回复，diff从实际第一版到第二版，conversation_revision=1；刷新/重开仍可读差异和思考。1440px与375px均无页面横向溢出，窄屏实测375/375，控制台warn/error为空。截图：[桌面](/tmp/naiyue-111-desktop-final.png)、[375px](/tmp/naiyue-111-mobile-final.png)。临时服务/库/浏览器页及下载文件已清理，viewport复位，截图与日志保留。

### 13.4 交付边界与下一步

本节是实施收尾快照；随后用户已授权与Spec112打包和Spec113视觉修改一起分主题提交并push，实际Git身份以仓库记录为准。上述全量结果属于本机源码验收，发布包仍须从冻结提交重建并另验。0次真实供应商请求，未修改真实配置、用户会话库或课堂掌握记录；不代表Windows/Linux原机、各供应商reasoning协议实网或业务效果通过。普通旧档案缺可信起点时不可重生成；reasoning按消息合并为一个可见通道，未做多块交错恢复；预算内详情才可读取，不承诺无限长正文。checkpoint修剪、项目模型重试/安全工具重试继续延期。

本批五项已收尾；唯一下一步为查看这批实现和页面效果，再按用户指令提交或安排独立验收，不自动进入延期项目。
