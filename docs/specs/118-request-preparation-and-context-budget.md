# Spec118：统一模型请求准备、主动压缩与事件时点

日期：2026-10-20。状态：**118-01/02/03已完成本机离线验收；本Spec已收尾，119纯观测阶段亦已完成**。第13/14节的“下一片”为对应历史时点，当前以第15节为准。

本稿承接动态配置与自动压缩的架构讨论，本文记录可独立审阅的实现合同。草案阅读基线为`1d34b6f`，本轮实施基线为HEAD `c5ca965`加工作树；LangChain 1.4.2、langchain-core 1.6.6、LangGraph 1.2.12。MCP-09/07等已提交成果不得回退其可用工具筛选、配置身份和旧批次复核。草案Pi对照固定 `73f8de80bc87021375f8b525292ac07492253002`，AgentX对照本机 `3fcfa0e`。

## 1. 用户已确认与本稿范围

已确认方向：

1. 首次与后续正常模型请求统一经过准备与预算决策，不等模型被拒绝才处理已发现的超阈值输入。
2. 判断包含本次已提交输入、已选补充消息、历史、工具结果、系统提示词和工具定义；未提交草稿及未选入本次请求的队列项不计入。
3. 达到主动压缩条件后先处理；压缩后按本地输入发送预算判断能否发送，不要求再次低于软触发阈值。
4. 保留明确输入溢出后的强制压缩与当前请求单次重试，不重放此前工具。
5. 复用现有完整轮次/轮内安全切点，不增加超大单条输入的通用分块摘要或截断兜底；现有安全方案无法满足发送预算时保留明确失败。
6. 只保留框架callback来源的一套模型执行事件，对外沿用model_call_started / model_call_finished名称；不再并列发布逻辑model_call与model_invocation两组。

2026-10-20用户确认剩余两项按建议执行：请求配置从准备开始固定到本次发送；普通首次估算不可用可观察降级，已知超预算/明确overflow及必要输入/状态处理失败不能借此放行。结合已确认的单套callback事件，按第11节切片实施；第8节仍只保留当前安全压缩范围。

本轮已授权更新Spec并分片实现及测试；不升级依赖、不改环境配置或真实用户数据库，不提交或发布。每片记录实际验证，未实施项不提前声明完成。

## 2. 实施前基线行为与需要更正的理解

### 2.1 当前不是“第一次不能检查”

实施前 `_ContextCompactionMiddleware.abefore_agent` 已检查普通新 run 的首请求。后续模型请求虽在 `observe_model_request` 中估算 `needs_compaction`，该值只进入日志/投影，不驱动压缩。下述为迁移前事实，01/02结果分别见第13/14节。

首工具视图在 `ToolSetMiddleware.before_agent` 准备，首个 before_model 通过 `_naiyue_first_tool_view` 跳过刷新；之后才每次刷新工具。压缩模块先解析模型，真正模型 wrapper 后面再次选模。这个首轮特例来自当前挂载，不是 LangChain 限制。

`before_model/abefore_model` 本来就可以覆盖首请求及后续请求。将主动检查移到统一请求准备边界后，不保留“首轮自动压缩”与“后续检查”两套业务算法。主动 threshold、供应商 overflow 恢复、manual 操作仍具有不同触发和提交语义。

### 2.2 当前事件事实

`run_started` 在 SessionExecution 的 run 资源准备前发布；项目指令/Skills 读取是 run 级准备。

目前 `_ModelSelectionMiddleware._prepare` 先选模型、创建 model_call_id，然后调用 `TurnObserver.begin`，紧接着发布 `model_call_started`。因此当前 turn_started 并不覆盖初始压缩；model_call_started 后仍有工具/提示词装配和输入适配。

上下文压缩的 applied 由可读图状态确认；summary 返回本身不是提交确认。普通观察故障保持隔离，不能据事件是否送达决定是否调用模型。

## 3. 目标职责与唯一主链

```text
应用准入 → run_started
  → run 级项目指令/Skills/资源范围准备
  → 图的控制边界：检查停止、接入本次选中的补充消息
  → turn_started（预留本轮逻辑 model_call_id）
  → 请求准备：模型/思考/工具视图/提示词/预算
      ├→ 可发送：形成发送事实
      └→ 达到软阈值：切点/摘要/必要状态替换/预算复核
  → 模型输入适配/框架绑定
  → callback发布model_call_started（一次框架模型执行）→ 模型
  → callback发布model_call_finished → 项目响应门禁
  → 权限批次准备/Auto/HITL → 工具执行 → 图合入结果
  → 本轮结果配对可见 → turn_finished
  → 继续时重新经过同一请求准备入口
  → 应用既有必要结算/保存/释放 → run_finished
```

“准备在 turn_started 之后”指本轮请求准备。run 级文件读取仍在 run_started 之后、首个 turn 之前；它失败时不虚构一个模型 turn。根控制消息接入也保留正式图更新，不移交给事件消费者。

### 3.1 建议沿现有实现收拢

- 一个请求准备入口协调当前的工具视图、提示词装配、选模与压缩算法；现有纯算法与具体职责函数继续独立。
- 使用 node-style `abefore_model` 返回必要的图状态更新；模型 wrapper 消费已准备事实并实施主模型执行、响应门禁及局部溢出恢复。
- 不用仅修改 `request.messages` 的临时副本冒充图中已提交的压缩；也不绕过 create_agent 自写图或 SDK 调用循环。
- 根与子装配使用同一准备合同；根独有 Steering/Follow-up 接入继续只挂根图，子模型/工具仍按实例固定事实解析。
- 正式审批恢复先继续原 pending 批次；到达下一次模型请求时再准备新事实，不能为统一首轮流程而重新审查/替换旧批。

现有 LangChain 官方 SummarizationMiddleware 的 before_model/abefore_model 已采用消息更新方式；它的固定摘要策略不直接替换本项目的动态模型、工具预算与恢复合同。

## 4. 请求事实、配置固定与生效时点（已确认）

### 4.1 所有者与内部边界

| 数据 | 权威所有者 | 请求准备如何使用 |
|---|---|---|
| 根模型/思考/活动工具名 | AgentSession 的 SessionState | 在一次短状态锁读取中取得相互对应的选择值 |
| 子模型/角色工具名 | 当前子实例 | 读取该实例固定值，不跟随根后续切换 |
| 项目指令/Skills 索引 | ActiveRun | 使用 run 已准备材料，不逐请求重扫磁盘 |
| 工具定义/可用性 | ToolRegistry 及 MCP 配置拥有者 | 形成当前请求视图，保留原定义及有效性检查 |
| 消息 | LangGraph 当前 state | 读取包含本次输入的可信状态；压缩更新交图提交 |
| 权限模式/会话许可 | SessionState，随后保存进工具批次 | 继续在响应后读取，不提前并入模型请求快照 |
| 估算与观察结果 | 准备过程的派生数据 | 用于预算/投影，不代替模型、消息或许可身份 |

新增一个窄的内部 `SessionPort.resolve_request_configuration` 能力，由 AgentSession 在短锁内一次读取模型ID/思考档位/活动名，再解析对应模型；摘要和网络IO不持有会话锁。不修改现有公开 AsyncApplication setter 签名。当前调用方就是统一请求准备，不新增配置注册平台。

运行时复用 invocation context 保存一份当次 prepared 记录，代替分散选择，不新增 ContextVar 或持久化服务。记录包含预留调用ID、选定模型、活动工具名、模型工具定义和系统消息；agent_path及原始工具视图沿已有context，预算计算沿现有算法与观察对象，不重复保存。模型对象、锁或工具对象不写进 checkpoint/事件。

### 4.2 一次准备固定到本次发送

选择模型A、思考档位H及工具视图V1后，主动摘要、预算复核及本次主请求使用同一份固定事实；摘要期间改B/V2留到下一模型请求，同一run中的后续请求亦可生效。权限仍按响应后批次边界生效。该语义替代当前可能两次解析到不同模型的时点，已获用户确认。

不采用“摘要结束立即用B”及其整组重准备，不建设持续追赶设置变化的无界重启循环。停止信号照常检查；本次固定不会锁住设置修改，也不代表改变已开始请求，用户需要立即换配置时可停止后重新发起。

MCP-09 的正式停用/重载门禁仍可阻断旧定义执行；请求固定不构成覆盖运行中撤销检查或旧审批身份检查的许可。

## 5. 两条预算线与首轮统一

沿用当前模型 metadata/config 来源，不写死 token 数：

- 软触发阈值 T = max(0, window - max(max_output, compaction_reserve))。
- 本地输入发送预算 B = window - max_output。
- 输入 E 使用本次模型的同一消息转换入口、同一系统提示词与工具视图计算。执行准备与请求观察不得重新解析另一模型/工具选择。
- 沿用当前 E > T 的触发比较，E == T 不因本片静默改变行为。异常预算（如 B <= 0）须在请求前显式处理。

首请求与第二、第三次请求都走同一判断；不再依靠 before_agent 的首轮专用压缩及首工具视图跳过标记维持一致性。

| 情况 | 目标行为 |
|---|---|
| E <= T 且 E <= B | 不调用摘要，准备好后发送 |
| E > T，有可用摘要计划 | 主动摘要、验证并提交候选，重新估算 |
| 压缩后 T < E_after <= B | 放行，不为了回到 T 以下反复摘要 |
| 没有完整旧轮次 | 继续寻找保持工具配对的轮内安全消息边界 |
| 没有可摘要前缀且 E <= B | 可明确记录 skipped 后发送，不把软阈值当硬拒绝线 |
| 完整轮次方案后仍 E > B | 沿现有算法寻找轮内安全切点；不新增任意单条消息切割 |
| 现有安全方案都无法满足 B | 明确报告无法容纳；不伪造摘要、不隐瞒丢失，不发送本地已知超预算的正常请求 |
| 普通请求首次总量估算不可用/缺少窗口资料 | 记录本地预算未验证，跳过依赖该估算的主动压缩，允许原请求发送；不伪造0或已通过 |
| 总量可用而分类明细/展示失败 | 使用可信总量继续预算判断，明细不可用不等于总量未知 |
| 已知E>B后候选校验/重估不可用 | 不放行已知超预算原输入，不提交未经校验的候选；仅按原有明确回退条件处理 |
| 明确overflow后无法有效压缩/复核 | 恢复失败，不原样重发，不增加恢复次数 |
| 输入转换/配置校验/必要状态提交失败 | 原错误传播，不归类为估算降级 |

摘要调用的输入/输出也必须满足其自身预算；不能把放不下的历史原样交给同一个摘要模型，指望它自行缩小输入。

普通摘要临时失败但原输入仍在 B 内时，可沿用现有明确可回退条件，并报告 fallback；已超 B 的输入不能按该条件放行。强制 overflow 恢复也不能回退到已被拒绝输入。

## 6. 单套模型事件、时点与身份

### 6.1 本轮确定的方向与保留职责

用户要求核对Pi后只保留一套模型调用事件，优先框架callback来源，能沿用model_call名称则沿用。固定Pi没有本项目这种并列的逻辑call与框架invocation两组模型事件，故本稿采用：

- 保留当前ModelUsageCallback的框架观察来源，对外仅发布model_call_started / model_call_finished；不再发布model_invocation_*，也不双发别名。
- 移除_ModelSelectionMiddleware发布的逻辑model_call事件，保留选模、响应门禁、失败描述、原异常、TurnObserver.accept及ContextOperationObserver确认等实际职责。
- LangChain仍调用on_chat_model_start / on_llm_end / on_llm_error；改名发生在项目emit_event及事件注册/消费者，不修改依赖或callback方法名。
- 新model_call_finished沿用框架returned / failed / cancelled与usage_status合同；returned不是项目accepted。门禁拒绝仍raise，通过既有turn/run失败、失败描述与日志呈现，不新增第二套模型结束事件。

单套事件方向及第11节剩余决定均已确认，按分片实施；事件事实仍不承担执行调度。

### 6.2 逻辑身份与框架执行身份

进入请求准备时仍生成逻辑关联C1，调用TurnObserver.begin(C1)产生T1。既有turn、context_snapshot、压缩/恢复载荷中的model_call_id继续表示这个逻辑关联，不为事件改名迁移这些字段。

每次框架模型执行由callback取得独立I1，来自LangChain callback run_id，单套模型事件仍使用当前invocation_id字段。此ID与应用run_id、逻辑C1不同；保留字段名和用量表invocation_id，不因事件名称改变重写历史用量数据。

例如本轮T1/C1在overflow恢复中可能产生I1(agent)、I2(summary)、I3(agent)。模型事件按I各自配对，turn/上下文按既有C/T关联；不能把新model_call事件的invocation_id与TurnStartedData.model_call_id当作同一ID比较。本片不新增直接关联这两种ID的注册层或映射平台。

ContextOperationObserver.model_call_started目前是直接调用的内部绑定方法，不是事件订阅。将该绑定与事件发布分开后沿用原状态合同；不因删除逻辑事件删除它的绑定副作用。

### 6.3 正常、主动压缩时序

```text
run_started R1
  → run资源准备
  → turn_started T1 / 逻辑C1
  → 请求准备
      → [compaction_started O1 / C1]
      → [model_call_started I_summary, purpose=summary]
      → [model_call_finished I_summary, returned/failed/cancelled]
      → [候选验证、图状态替换并确认可读]
      → [compaction_finished O1, applied]
  → 主请求输入适配
  → model_context_snapshot C1, running
  → 框架模型绑定
  → callback: model_call_started I_agent, purpose=agent
  → 框架模型执行
  → callback: model_call_finished I_agent, returned/failed/cancelled
  → model_context_snapshot C1, returned/failed
  → 项目响应门禁（可能raise ModelResponseRejected）
  → 权限/工具/消息提交
  → turn_finished T1
```

方括号只在需要摘要时出现。所有模型调用使用同一事件类型，以purpose区分；摘要模型执行可以早于正常agent执行。不能把“准备完成”直接解释成“model_call_started已发布”，也不能因摘要在准备中调用模型而声称所有purpose都在准备完成后才开始。

新model_call_started确认框架已进入这次ChatModel执行；正常agent请求的准备已在其前完成。它仍不证明HTTP已发出、远端收到或SDK内部重试次数。本机BaseChatModel在on_chat_model_start后仍有实际生成/缓存等分支，不把callback理解为网络送达确认。

图提交成功与事件确认分别验证；applied只来自已提交且可读的消息状态，不能在摘要函数返回时发布。图与既有观察对象直接推进状态，普通观察事件不承担调度。上图表示健康观察路径，不等待事件送达或新增checkpoint轮询。

### 6.4 失败、停止、恢复与门禁

| 分支 | 健康观察路径的事实 |
|---|---|
| run资源读取失败 | run_started后失败收尾；不虚构turn或模型执行 |
| turn开始后准备失败 | turn_finished failed；尚未进入的主模型无model_call事件，已开始的摘要按自己的I结算 |
| 准备时用户停止/外部取消 | 保持两种控制的原异常与必要收尾区别；已开始的模型执行结算cancelled，未进入的执行不补假开始 |
| 输入适配/模型绑定在callback前失败 | 无主模型调用事件；既有turn/run失败保留原错误，不能为配对虚构callback已开始 |
| 框架返回但响应门禁拒绝 | model_call_finished returned且保留可用usage；随后turn/run failed报告ModelResponseRejected；没有第二个model_call_finished failed |
| 显式overflow后恢复 | 同一T1/C1，I1(agent failed)→I2(summary)→I3(agent retry)各一对；retry仍只一次，不重放此前工具 |
| 审批恢复 | 原turn_resumed/工具批次继续；下一模型请求才开新turn与准备 |

取消沿用现有turn failed加cancelled/user_stop描述及run stopped合同，不改control_delivered/control_applied，不新增turn stopped状态。准备失败/停止时合法关闭turn，不能退化为incomplete_turn观察故障。

### 6.5 现有用途与迁移合同

| 当前事件/消费者 | 当前用途 | 本稿迁移 |
|---|---|---|
| 逻辑model_call_started→CLI | 显示模型正在处理请求，子来源同样显示 | 接单套model_call_started，仅purpose=agent显示这条提示；摘要/Auto不冒充正常回复 |
| 逻辑model_call_finished→日志/诊断/订阅者 | 项目accepted/failed、stop_reason及工具ID | 停止该事件发布；保留实际门禁、原异常、turn/run结果、消息/工具事实与现有日志 |
| model_invocation_*→ModelUsagePersistence及model_usage_storage | 每次模型执行和用量，覆盖主/子/摘要/Auto/恢复 | 改订阅及事件类型校验为model_call_*，保留invocation载荷字段、表及查询口径 |
| 两组事件→diagnostic_persistence / run_events日志 | 保存有限诊断事实 | 新运行只存一组模型事件，日志按实际载荷投影；历史诊断保留原名称/载荷，不按新语义重解释 |
| Web事件投影 | 当前两组模型事件没有专用产品状态分支，仅推进全流sequence；上下文另由model_context_snapshot消费 | 保持sequence及根/子路由，不靠删帧模拟协议迁移；相关测试和当前事件文档同步 |
| EventSequenceChecker及行为测试 | 两组分别配对；部分测试用逻辑开始点观察配置/轮次 | 改为按invocation_id校验单组模型配对；逻辑准备/门禁断言移至真实函数/turn/run/已有观察点，保留行为、异常身份和权限断言 |

上述消费者是观察用途，没有生产订阅者通过model_call事件实施选模、审批、工具调度或必要保存。TurnObserver.begin/accept与ContextOperationObserver内部方法由主链直接调用，不能与同名事件混淆。

既有事件订阅入口不改签名。原model_call载荷及accepted语义会迁移，属于用户本轮明确要求的事件协议调整；仓库内实际消费者与测试必须一并更新。新数据继续使用现有invocation载荷类，不保留旧逻辑载荷或双发兼容层。历史SQLite用量不迁移，诊断旧JSON只作为历史事实读取；当前没有证据表明存在需额外兼容的仓库外消费者。

model_context_snapshot仍表达请求输入与本地估算，终态补可用usage；不拿压缩前估算冒充最终发送输入。失败usage缺失保持unavailable，不伪造0。页面草稿预算、账单核对与前端重设计不在本片。

## 7. 正常、失败与保存例子

### 7.1 同 run 工具结果增长

C1 正常调用并执行 T_read；工具结果进入图后，C2 准备发现 E>T。先开始 C2 所属 turn，按 C2 选定模型/工具做摘要与提交。E_after 仍高于 T、但不超过 B 时进入正常模型执行，callback按独立invocation_id发布model_call_started。T_read 执行次数仍为1。

### 7.2 摘要期间改配置

C2固定模型A、工具V1后用户改B/V2。按第4节已确认合同，本次继续A/V1，C3读取B/V2；项目材料仍来自run。MCP失效/许可复核照常，不能拿固定视图绕过。

### 7.3 压缩成功但模型仍拒绝

T2/C2内完成主动压缩与本地复核后进入主模型执行；供应商明确输入溢出，沿当前wrapper强制压缩并重试当前请求一次，各次框架执行分别发布单套model_call事件，逻辑C2不变。原工具不重放；恢复重试响应未通过门禁时，不把恢复候选作为成功提交。

### 7.4 必要提交失败

摘要返回不代表上下文已替换。必要图写入/提交失败时阻断主请求，保留可核对的源状态和错误，应用按既有保存/人工核对合同收尾。合法图更新已完成后，普通观察失败仍隔离。

## 8. 超大输入与切割：Pi事实及本片范围

### 8.1 固定 Pi 实际做了什么

`AgentSession._runAutoCompaction → prepareCompaction → findProjectedCutPoint → compact`：允许在 user/assistant 等安全消息边界切开长 turn，分别生成旧历史摘要与 turn 前缀摘要，再与保留尾部组成上下文。不是只允许切完整用户轮次。

`serializeConversation` 发送摘要材料时，每份工具结果文本截到2000字符并附省略标记；user 文本没有同样的任意字符截断分支。它不是主模型工具结果的一般截断开关。

单个超大新 user 输入可能找不到可摘要前缀，prepareCompaction 返回 undefined，调用者不压缩；摘要模型失败也会报告 compaction_end 失败。已核对主链没有任意单消息按 token 分块、多轮摘要合并的通用保证。

以上为静态源码事实，未运行 Pi 或发送模型请求。不能把“Pi有split turn”说成“Pi能自动切割任何巨大单条输入”。

### 8.2 本项目保留的路径

1. 复用当前安全消息切点和工具配对校验，完整旧轮次不足时再找轮内切点。
2. E<=B 且没有可摘要旧前缀时，明确 skipped 后可发送，不因软阈值直接失败。
3. E>B 时，继续尝试现有合法压缩方案；无法满足预算则明确失败，保留原异常和必要收尾，不发送本地已知超预算的正常请求。

### 8.3 已收回的范围

用户本轮明确不增加通用超大单条输入处理。因此撤回前稿的“按预算分块摘要”和“显式截断并增加原文读取入口”候选，不再列为实施前待决项，也不为它们增加存储、归档或重新生成机制。

此范围不取消现有轮内安全消息切点；仍须保持工具调用/结果配对，不能截成半个工具参数JSON，不能把摘要说成新的用户授权。Pi没有通用保证只构成本轮对照事实；不以参考项目作为所有未来场景都无需扩展的证明。

## 9. 影响文件与迁移边界（计划，不是已创建组件）

预计超过8个生产文件，另有多组行为测试与当前文档同步；无新服务或依赖。

| 文件/范围 | 计划职责 |
|---|---|
| agent_runtime/request_preparation.py（候选新模块） | 容纳请求准备协调，避免当前 context_compaction.py 越过500行；复用现有算法 |
| agent_runtime/__init__.py、subagents.py | 根/子挂统一阶段，移除普通首轮专用压缩接线 |
| application/agent_session.py、session_service.py、agent_runtime/session_port.py | 窄的请求配置读取能力，根/子归属与短锁 |
| tool_execution/toolset.py | 保留视图/授权路由；移除被替代的首轮刷新特例和重复准备 |
| context_compaction.py、context_summary_io.py | 复用现有切点、预算、摘要与提交合同，不新增单消息缩减算法 |
| call_observation.py | 消费准备结果，删除逻辑model_call事件发布及无消费者载荷，保留选模、门禁、异常身份和直接观察调用 |
| model_usage_observation.py、agent_runtime/events.py | callback保留，事件统一为model_call_*；沿用invocation字段与结果语义 |
| application/model_usage_persistence.py、model_usage_storage.py、diagnostic_persistence.py、cli_display.py | 消费与校验迁移，CLI按purpose区分；数据库表/查询及历史事实保留 |
| turn_observation.py、context_operation_observation.py、active_stop.py | 提前开启turn，绑定预留ID；准备失败/停止合法结束 |
| context_observation.py、context_usage.py、model_access/messages.py | 发送输入/估算/快照一致，保留供应商格式转换 |
| context_recovery.py | 保留显式溢出局部恢复，接同一预算与准备事实 |
| application/run_events.py 与当前事件消费者 | 检查时点，不改变必要保存主合同 |
| 当前事件文档、tests/event_sequence.py及CLI/Web行为测试 | 迁移单套事件合同及真实行为断言，不保留双发或放宽检查 |

实施前搜索并逐项列出 make_context_compaction_middleware、model_inputs、首轮标记与旧模型选择消费者。只删除被本主链确实替代的准备分支；手动压缩/恢复算法、真实同步IO、框架hook仍有消费者的保留。

## 10. 验收与测试设计

本节是各片的验收计划；01/02已执行部分以第13/14节实际结果为准，03尚未执行，不将计划命令冒充完成证据。

新增测试先红灯，再最小实现。优先扩展现有以下套件，必要时新建一个聚焦请求准备的测试文件，不建立平行教学框架。

| 验收场景 | 确定性断言 |
|---|---|
| 首请求/后续请求统一 | 两者都经过相同准备路径；低阈值无摘要调用 |
| 新输入、工具结果、已选Steering/Follow-up | 计入当次E；未提交草稿/未选队列不计入 |
| 主动压缩 | 主请求前发生；E_after位于(T,B]仍发送；不重复压到T以下 |
| 无完整旧轮次 | 安全轮内边界仍可压缩，工具调用/结果不拆坏 |
| 无旧前缀、E<=B | skipped可观察，正常发送 |
| 超大单消息且无安全方案 | 明确失败；不截断、不伪造摘要、不发送正常主请求，既有状态及必要收尾保持 |
| 摘要期间切配置 | 请求用选定事实；后续请求刷新；会话/子实例隔离 |
| 正常事件顺序 | run/turn/准备操作/model调用顺序及ID一致，原快照不冒充最终输入 |
| 准备失败/停止/外部取消 | 无主调用开始假事件；turn合法关闭；原异常及必要收尾保持 |
| 单套模型事件/用量 | 普通执行一对；overflow为agent/summary/agent三对且turn一个；原model_invocation_*不再发布；失败缺usage不伪造0 |
| 门禁拒绝/绑定失败 | 前者模型returned且turn/run failed、工具不执行；后者callback前无模型事件且原失败保留 |
| CLI/诊断/历史 | agent显示模型提示，summary/Auto不显示该提示；新诊断一组，历史用量/诊断不重写 |
| 显式overflow | 同一逻辑调用只重试一次；此前工具执行次数不增 |
| 审批/恢复/MCP09 | 旧批不重审、不换参数/身份；新请求按可用视图准备 |
| 必要保存失败/观察失败 | 前者阻断/可核对；后者隔离且末尾显式检查诊断 |

主要现有入口：tests/test_context_compaction.py、test_context_overflow_recovery.py、test_context_operation_events.py、test_turn_observation.py、test_runtime_middleware_contracts.py、test_dynamic_tools.py、test_control_races_and_failures.py、test_application_run_events.py、test_subagent_execution.py、test_mcp_runtime_configuration_interactions.py、test_model_invocation_paths.py、test_model_usage_paths.py、test_model_usage_persistence_paths.py、test_model_tool_subscriptions.py、test_cli_event_display.py。EventSequenceChecker继续校验配对，并补新的正常时点及准备失败断言，不放宽旧身份/权限检查。

实施时计划命令：

```bash
.venv/bin/python -m pytest -q tests/test_context_compaction.py tests/test_context_overflow_recovery.py tests/test_context_operation_events.py tests/test_turn_observation.py tests/test_runtime_middleware_contracts.py
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent
.venv/bin/python -m pip check
git diff --check
```

原生工具PATH需实施时先核对实际目录，不把此历史目录存在视为保证。新增测试文件及最终定向命令随已批准设计补入，不声称本轮执行。无模型网络请求可以完成确定性验收；实网/发布另行授权。

## 11. 已确认决定与实现切片

用户确认：本次请求配置固定到发送；普通首次估算不可用允许带诊断发送，已知超预算、明确overflow及必要处理失败保持阻断。事件统一方向、turn覆盖请求准备及取消沿当前编码的合同按本文执行。

| 切片 | 范围与消费者 | 独立验收 |
|---|---|---|
| 118-01 单套模型事件 | callback发布model_call_*；移除逻辑发布/无消费者载荷；迁移用量/诊断/CLI/脚本/测试，保留门禁、原异常、turn/context直接副作用 | 普通/根子/摘要/Auto/overflow按invocation_id配对；门禁拒绝为模型returned＋run失败；历史数据不重写；原行为断言迁移而非删减 |
| 118-02 统一请求准备 | 短锁请求配置快照；根子统一abefore_model，固定模型/思考/工具/提示词；提交压缩状态，提前turn并清理首轮特例/重复解析 | 首次及后续增长均主动检查；摘要中改设置下一请求生效；MCP撤销及审批旧批复核保持；准备失败/取消合法结算 |
| 118-03 降级、恢复与交付 | 总量/明细失败分离、首次未知与已知超限区分；同配置overflow局部恢复；迁移剩余探针/当前文档并做安装门禁 | 普通未知允许发送且诊断明确；已知超限/候选未知/必要失败不放行；恢复一次不重放工具；完整回归/构建及离线安装接线 |

每片先看到正确失败测试，再实现、定向及全量验证后收尾。118-01不提前新增119耗时或HTTP事件；118全部实现/验收后再推进119。实现超过8份生产/测试文件，按上述职责分别验证，不另增服务、依赖或公共API破坏性修改。

### 11.1 118-02实施接点

沿用现有make_model_selection_middleware及其公开调用形式，把选模/逻辑ID建立移到该middleware的abefore_model；wrap只消费当次prepared事实和执行原门禁，不再选模。准备协调放request_preparation.py，使用已有invocation context，无新增ContextVar或服务。正式根/子图传入SessionPort.resolve_request_configuration，返回(model_id, 已按思考档位解析的model, active_tool_names)，在AgentSession短锁内读取选择；独立图保留原resolver合同并经过同一准备协调。

root的Steering/停止接入hook明确置于准备hook之前，overflow恢复wrapper仍保持在模型门禁内侧；不能为调整before_model顺序改变wrapper进入/返回顺序。准备生成逻辑C并开始turn/context关联，再固定配置、准备工具视图/提示词，调用既有压缩算法并返回正式图更新。工具wrapper继续使用该视图与原撤销/授权检查，ToolSet不再装配第二份模型输入或保留首轮跳过标记。

预算中原输入/候选输入计数复用prepare_model_messages的实际模型输入转换，保留canonical历史与安全切点；转换失败属于必要输入失败，不冒充估算降级。正常返回提交压缩状态后才进入模型；准备异常/外部取消保留原异常并结算已开始turn，UserStop按原控制理由结算后跳end，不补主模型开始。准备阶段使用独立安全诊断与runtime失败描述，不把配置/输入准备失败记录为主模型执行失败；context操作仍直接通知原失败收尾。首次未知/强制恢复等更完整降级矩阵仍在03验收。

### 11.2 118-03实施接点（用户确认后）

沿context_usage.estimate_context先计算本次输入总量，再独立计算分类明细。status继续表示总量可用性，新增groups_status=available|unavailable；总量未知时保留null/空groups，只有明细失败时保留可信总量、groups为空。image_count独立从实际消息统计，不因明细失败伪造0。read_snapshot严格校验新字段与总量/明细一致性，兼容不带groups_status的历史v1记录，读旧值不改写历史；原JSON元数据承载，无数据库迁移。ContextEstimate同步可选字段，原上下文面板总量可用时照常显示，明细不可用则不渲染明细，无新增提示/页面。

context_compaction保留现有算法与返回的图消息更新。必要prepare_model_messages转换、已提供预算配置的类型/正值及B>0校验先完成，不进入普通首次估算的catch；缺失预算资料与首次总量估算异常明确记录context_budget_unverified及有限关联/固定reason，普通请求可不压缩发送，强制恢复不能把未知当成有效候选。后续候选/摘要输入/最终重估失败继续阻断，不新增通用catch后放行或无界重准备。context_observation的可选统计不参与执行决策，也不能将准备阶段未知改写成已通过。

正常主链仍为118-02准备/图提交/模型链；明细故障只使明细为空。失败链为必要处理原错误或已知超限候选失败→原turn/run收尾，主模型不发送、候选不提交。复用原context_recovery验证同配置/单次重试/不重放工具/门禁拒绝不提交/停止取消与必要保存；已有行为无缺口则只补证据，不重写恢复机制。影响context_usage、context_compaction、contextTypes、ContextPanel及对应后端/前端测试；确需修改观察或恢复接线时先在本节补理由。先红灯→最小实现→专项/全量/标准构建与干净安装，最后更新当前指引与交接；不增加119耗时、HTTP事件、任务硬预算、依赖或真实配置修改。

## 12. 来源与本轮证据

- 本项目：agent_runtime装配、ToolSet/模型wrapper、context_compaction/recovery、TurnObserver/ContextOperationObserver、SessionState与MCP09当前工作树；现有测试仅阅读。
- [Pi请求准备/压缩入口](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/agent-session.ts)。
- [Pi切点与分轮摘要](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/compaction/compaction.ts)：findProjectedCutPoint、prepareCompaction、compact、generateTurnPrefixSummary。
- [Pi摘要序列化](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/compaction/utils.ts)：serializeConversation、truncateForSummary，2000字符只用于摘要材料里的工具结果。
- AgentX本机3fcfa0e：ContextProcessor.processContext → TokenDomainService.processMessages → SummarizeTokenOverflowStrategy.process；按消息数门槛保留尾部后调用摘要，不提供本项目按完整请求token预算与工具配对的现成合同，不直接采用。
- [LangChain自定义middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)，另核对本机factory及SummarizationMiddleware.abefore_model消息更新方式。
- Pi固定版本[AgentEvent](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/types.ts)、[底层响应流事件](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/ai/src/types.ts)及[streamAssistantResponse](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/agent-loop.ts)：底层start/done/error映射为message_start/update/end；没有本项目这种并列逻辑call/invocation两组模型事件；AgentSessionEvent也无这两组。
- AgentX本机3fcfa0e：TracingMessageHandler.onModelCallCompleted→TraceCollector.recordModelCall→ModelCalledEvent→TraceEventListener.handleModelCalled→traceDomainService.recordAiResponse；这条完成后轨迹保存链不构成本项目保留双组模型事件的依据。
- [LangChain AsyncCallbackHandler](https://reference.langchain.com/python/langchain-core/callbacks/base/AsyncCallbackHandler)及本机BaseChatModel.agenerate/astream：on_chat_model_start、on_llm_end、on_llm_error的真实调用边界。

草案阶段仅源码/文档核对及Spec结构/链接检查，未实现产品或调用模型供应商；实施结果另在本Spec记录。不新增用户掌握或独立调试证据。

## 13. 118-01实际实现与验收

本片只统一模型执行事件，不移动turn开始、不合并请求准备，不新增119的duration_ms或HTTP尝试。第3—5节及第6节提前turn的目标时序由02/03继续完成；不能把01完成理解为整个118完成。

### 13.1 当前主链及删除/保留项

正常：SessionService安装已有ModelUsageCallback → RunObservation绑定根/子RunServices → 原选模middleware建立逻辑C并直接通知turn/context → 框架callback按独立I发布model_call_started → 原模型执行 → callback发布model_call_finished(returned，实际usage) → 原响应快照/门禁/TurnObserver.accept → 权限/工具/原必要保存及run终局。摘要和Auto也由同一callback发布，purpose区分，不插入虚假正常agent轮次。

失败：SDK抛原异常E时callback记录failed及有限error_type；原middleware描述并重新raise同一E，应用执行原结算。框架返回但门禁拒绝时，callback只记录returned，门禁仍raise ModelResponseRejected，turn/run保留真实拒绝原因；不再补逻辑failed模型事件。取消沿当前callback/task done兜底最多结算一次，停止/必要保存和原异常合同保持。

- callback改发布model_call_*；保留ModelInvocationStartedData/FinishedData及invocation_id载荷。公开注册由32种减至30种，不发布model_invocation_*别名。
- 删除旧ModelCallStartedData/FinishedData、选模middleware的逻辑开始/结束发布和只供其失败载荷使用的响应参数；不留旧入口兼容层。
- 保留选模、原响应门禁、错误分类/安全日志、TurnObserver.begin/accept及ContextOperationObserver直接绑定/接受/失败调用，它们仍有实际执行消费者。
- ModelUsagePersistence、model_usage_storage按新事件名消费同一载荷；表、schema版本、历史invocation_id及用量查询算法不改。诊断保存新单套事件，历史旧名称/JSON原样读回，不重解释accepted或重写旧行。
- CLI普通模型提示只响应purpose=agent；summary/auto_fast/auto_detailed不冒充普通回复。Web仍沿原通用事件推进sequence，消息与上下文投影保持。
- tests/event_sequence.py将模型配对统一为invocation_id，保留工具、turn、控制、context配对及明确异常检查。逻辑关联断言改查turn/context；门禁结果改查原异常/run/消息，不削减权限、身份或状态断言。
- 两份现有探针迁移：probe_async_model显式安装正式callback及run范围，框架执行I与逻辑C分别检查；probe_message_state_boundaries改观察returned时点，不再将callback返回称为accepted。

生产仅7份现有文件：agent_runtime的events/call_observation/model_usage_observation，以及application的cli_display/diagnostic_persistence/model_usage_persistence/model_usage_storage；无新生产模块或依赖。测试夹具按正式构造器提供naiyue_model_id元数据，直接图测试显式注册callback并绑定RunServices；没有在生产中猜缺失身份或恢复逻辑事件。

### 13.2 实际验证

新增9项合同测试，首次7项中的6项正确红灯后最小实现转绿，另补旧事件名无别名及历史诊断读回测试。首轮全量3312通过/13个旧协议断言或夹具接点失败，逐项迁移身份元数据、序号/次数、Auto用途及错误载荷；迁移专项94项、事件/用量/取消/观察故障组130项通过。最终全后端**3327 passed in228.27s**；前端**46文件366 passed**，TS/Vite通过；架构/安全专项及主审未发现本片问题。

```bash
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec118-01/wheels
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec118-01/wheels --output build/spec118-01/install-macos-arm64.json
.venv/bin/python -m scripts.probe_message_state_boundaries
.venv/bin/python -m compileall -q naiyue_test_agent scripts
.venv/bin/python -m pip check
git diff --check
```

本机Mac ARM64候选287文件；157份生产Python与wheel源码字节一致，AST/500行、compile/pip/diff通过。标准干净安装的原工具/Web/CLI/审批/SQLite及MCP门禁均passed，MCP合成调用8次、进程全部退出、供应商0次。10场景消息状态探针PASS且network=none；前端只有测试事件协议迁移，没有产品UI变化，未额外声称视觉验收。

证据位于build/spec118-01：backend-first.log、migration-regression.log、event-regression.log、backend-final.log、frontend.log、frontend-build.log、wheel-build.log、install-macos-arm64.json及message-state-probe.log。wheel SHA256为806574354a7abe2b244cefaedfdd0b28c93010d896336e0beae53e0974a3e125，仅本地候选、源码版本仍0.1.1；不代表发布或其他平台/供应商/业务效果已验。

下一片118-02：请求配置短锁快照、统一abefore_model准备、首轮特例及重复解析清理，固定模型/思考/工具/提示词，提前turn并保留准备失败/取消与MCP/审批边界。03继续降级/恢复与交付收尾，118完成后才119。未修改真实环境配置、用户库或并行课堂文档，未提交/push/tag/发布。

## 14. 118-02实际实现与验收

### 14.1 当前主链与数据归属

正常：根图inject_before_model先检查停止、正式接入选中消息 → select_model.abefore_model预留逻辑C、开始turn/context关联 → SessionService.resolve_request_configuration → AgentSession在短锁内读取(model_id, thinking_level, active_tool_names)，解锁后由catalog生成对应model → prepare_request固定原工具视图/模型工具定义/完整SystemMessage → _acompact_messages以同一model/prompt/tools及prepare_model_messages转换的输入计数 → 必要压缩返回RemoveMessage与摘要/保留消息，由图正式提交 → 原模型wrapper只消费prepared事实 → 原输入转换、观察、callback、门禁、权限/工具与结算。后续请求重复同一入口；子图按实例模型/工具准备，不接根控制消息。

prepared只在当次runtime.context中，内容为{model_id, model_call_id, model, active_tool_names, tools, system_message}；不保存到SQLite/checkpoint，不共享到其他会话。准备阶段对tool_execution只提交原可序列化工具名，响应后的批次字段沿原流程生成。摘要期间改模型/思考/工具不阻塞；当前摘要、重估和主发送沿同一份配置，下一模型请求读取新值，同run及下run均成立。动态角色索引/完整prompt也只准备一次，预算和发送使用同一个SystemMessage。

失败：配置/工具/输入准备抛原错误E → 原context操作失败通知 → runtime失败描述、安全model_request_preparation_failed诊断 → 已开始turn按原原因failed → 同一E继续到应用必要结算，不产生尚未开始的主模型callback事件。准备阶段停止按UserStop结算并跳end；外部取消继续抛同一CancelledError。摘要已进入callback时按自己的invocation_id结算。主模型执行错误仍沿原_failed描述/日志；两种失败不混记。

### 14.2 删除项与保留的实际消费者

- 删除首轮_ContextCompactionMiddleware及工厂/根子挂载、_naiyue_first_tool_view、ToolSet的首轮与后续刷新hook、model_inputs/_model_request/模型wrapper；统一入口已承担这些职责，不留并行旧链。
- ToolSet保留prepare_tool_view、_inputs及原工具执行wrapper；MCP启停/定义身份、审批旧批与实际执行授权均沿原复核。模型视图固定不构成执行许可。
- 保留_acompact_messages及原切点/摘要/配对算法，实际消费者为统一准备、手动压缩和明确overflow恢复；不新增单消息分块或任意截断。
- 保留AgentSession/SessionService.resolve_model，实际消费者仍有SessionExecution的手动压缩及独立建图调用方；公开build_runtime_agent与旧resolver调用形式保持，只将其内部hook迁移到准备阶段。
- 根控制hook单独放在准备前，overflow wrapper保持在门禁内侧；输入适配、请求观察、callback、Auto/HITL、必要保存和异常边界仍有原实际调用方。

本片改8份现有生产文件并新增request_preparation.py（29行）；未改依赖、环境配置、数据库schema或Web产品界面。协议夹具、直接middleware测试和3份压缩探针测试迁到新实际入口，保留异常/配对/权限断言，增加每次请求预算与发送prompt/tools一致的检查。

### 14.3 验证记录与下一片

新增核心6项先在旧实现正确失败，再最小实现转绿；另补缺失会话上下文2项（None先红灯）、配置/工具准备失败2项及同run配置切换1项，当前11项通过。152项定向回归通过。首轮全量3330通过/5失败：2项暴露准备失败误记model_failure，修源代码并保留原断言；3项探针仍指向已替换入口，迁移并加强每请求输入一致性。修正后相关37项通过；11项最终新增测试含观察健康检查通过。

最终全后端**3338 passed in221.45s**；前端**46文件366 passed**、TypeScript/Vite构建成功，仅保留既有chunk体积提示。架构/安全专项两轮复核与主审无新增发现。158份生产Python的AST/500行、compileall、pip check及tracked diff检查通过；4份本轮拥有的untracked文件空白检查无诊断（no-index允许退出1表示差异）。Spec118/119本地链接通过。

```bash
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
.venv/bin/python -m pytest tests/test_request_preparation.py -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec118-02/wheels-delivery
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec118-02/wheels-delivery --output build/spec118-02/install-delivery-macos-arm64.json
.venv/bin/python -m scripts.probe_message_state_boundaries
.venv/bin/python -m compileall -q naiyue_test_agent scripts
.venv/bin/python -m pip check
git diff --check
```

本机最终wheel288文件、158份源码字节一致，标准源外干净安装的原生工具/Web/CLI/审批/SQLite/MCP均passed；MCP合成调用8次、进程全部退出、供应商0次。消息状态探针10场景PASS且network=none。源码末尾空白修正后重新构建；构建器对已有输出目录的覆盖按合同拒绝，改用新wheels-delivery目录，无旧产物删除或覆盖。最终SHA256为04439436e7dc92f8e176df342c3c35b8fabc57e16c85413ea94b88738e1170ce，源码版本仍0.1.1，仅本地候选，不代表发布或其他平台原机验证。

证据目录build/spec118-02：red.log、green-first.log、missing-scope-red.log、regression-final.log、prepare-log-red.log、preparation-log-green.log、tool-context-red.log、migration-green.log、preparation-final.log、backend-first.log、backend-final.log、frontend.log、frontend-build.log、wheel-build-delivery.log、archive-delivery.json、install-delivery-macos-arm64.json和message-state-probe.log；首轮与中间候选保留，最终产物以wheels-delivery为准。

唯一下一片118-03：补齐首次总量未知与分类明细失败的区别、已知超预算后候选未知及必要失败阻断、同配置overflow局部恢复矩阵，迁移剩余当前指引/探针并完成118整体验收。03未实施；119继续等待118完成。本片未提交/push/tag/发布，不以离线验证推定真实供应商/其他平台/业务效果或用户独立掌握。

## 15. 118-03实际实现与Spec118收尾

### 15.1 当前主链、失败合同与接线

context_usage.estimate_context先独立估算整份输入总量，再统计groups；只有明细异常时保留总量和实际image_count、groups_status=unavailable及空groups。总量异常保持status=unavailable/total=null，不伪造0。read_snapshot校验新状态配对与明细合计，旧v1无groups_status记录按旧状态校验、原样读取；ModelContextSnapshotData/消息metadata/SQLite/查询沿原链传递，ContextEstimate可选字段与原ContextPanel同步，无新增表/迁移/事件。界面只在明细不可用时隐藏明细，保留可用总量，无新增提示或页面。

请求准备仍由118-02入口拥有。context_compaction先执行必要prepare_model_messages，再验证已提供预算配置的类型/正值及B>0；它们失败按原错误传播，不进入估算catch。缺失资料或首次估算故障记录context_budget_unverified及session/run/model/fixed reason/force，普通请求允许原输入发送；force则明确compaction_unavailable。候选计划、摘要输入、摘要结果及最终重估继续使用原阻断，不回退到已知超预算输入，不提交未经验证候选。

context_recovery源码保持：原request的模型/思考/工具/提示词用于强制摘要及一次重试；只override消息，不重放Agent或工具。SDK MockTransport组合验证先工具一次、再overflow、摘要期间切配置、原配置重发、下请求切换；四次首run模型执行按agent/agent/summary/agent配对，turn配对健康，恢复终态recovered/retry_attempts=1。已有第二次拒绝、门禁不提交、停止取消、观察失败与必要保存合同沿原测试验证。

本片仅4份生产文件增量：context_usage.py、context_compaction.py、frontend的contextTypes.ts/ContextPanel.tsx；无新生产服务/类/依赖，未改context_observation/context_recovery接线。旧首轮hook、重复准备及model_invocation_*生产发布/消费搜索均无遗留；测试里只保留明确无别名和历史协议读回断言。当前Web指引与119第2节已更新为统一后事实，学习文档保持。

### 15.2 实际验证与范围

新增19项后端、1项前端。后端核心9项在旧代码正确红灯、8项原保护绿灯后最小实现；快照测试最初缺必需model_id的夹具已补齐后重跑有效红灯，不把该夹具错误计入产品失败。后续补SQLite重开与SDK恢复组合；新组合测试误用了compaction的applied，核对原事件合同后改为recovery的recovered，生产终态未改。最终新增19项及相关151项通过，前端定向6项通过。

最终后端**3357 passed in219.82s**，前端**46文件367 passed**，TS/Vite构建成功（既有chunk体积提示）。架构、安全及主审无新增发现；158份生产Python AST/500行、compileall、pip check、diff及本轮新文件空白检查通过；Spec118/119/Web指引本地链接通过。10场景消息状态探针PASS/network=none。

```bash
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
.venv/bin/python -m pytest tests/test_context_budget_degradation.py -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec118-03/wheels
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec118-03/wheels --output build/spec118-03/install-macos-arm64.json
.venv/bin/python -m scripts.probe_message_state_boundaries
.venv/bin/python -m compileall -q naiyue_test_agent scripts
.venv/bin/python -m pip check
git diff --check
```

本机wheel288文件、158份源码字节一致；标准源外干净安装的原生工具/Web/CLI/审批/SQLite/MCP均passed，MCP合成调用8次、进程全部退出、供应商0次。SHA256 c7561b37756e24847dab078736f59fb59b35667d08e3fe6e26ae3a51010edea4，版本仍0.1.1，仅本地候选。证据build/spec118-03：red-valid.log、green-first.log、extended-final.log、regression-first.log、frontend-red/green/all.log、backend-first.log、frontend-build.log、wheel-build.log、archive.json、install-macos-arm64.json及message-state-probe.log。

Spec118已按确认的三片收尾；真实供应商/其他平台原机/业务效果与用户独立掌握未在本片证明。下一片119-01先说明单套callback模型、工具dispatch和根run的duration_ms接入，用户确认后实施；119当前仍无耗时/HTTP尝试产品改动。未改真实环境配置/用户库/依赖或并行课堂，未commit/push/tag/发布。
