# Spec092：重启后的历史、继续沟通和前置审批恢复

2026-10-17入口版本边界：Spec093已把应用消费者和诊断探针迁到原生异步App，删除独立同步应用/持久化入口；下文早期源码名称和运行记录保留当时证据。当前API用arun/aresume_tool_approval、aget_session_history/arecover_session；框架本身的同步能力与本项目低层同步策略不因此删除。

日期：2026-10-17。状态：**092A/B/C及打开会话时自动恢复已实施；最终验证见第5.1节。** 接续[090](090-tool-wait-stop-and-author-contract.md)、[091](091-tool-progress.md)。按092A历史读取 → 092B中断后续聊 → 092C前置审批恢复实施。

## 0. 2026-10-17确认：打开会话自动恢复

用户确认取消正常路径的额外恢复点击。Web选择旧会话后，先GET只读历史，再自动POST已有recover接口；CLI明确选择旧会话后自动调用同一恢复逻辑。服务启动不扫描或改写全部历史。只恢复本地状态，不调用模型、工具、Auto，不自动批准或发送草稿。

- 执行中断：自动执行092B的三次更新，恢复ready并显示说明；新用户消息才开始运行。
- 严格前置审批：自动请求restore_approval，复核成功后直接显示原审批卡片，仍整批零执行。不能自动continue取消待审批次。
- 复核/保存失败：保留可读历史与草稿，刷新当前checkpoint，停止自动重试；提供手动重试。待审不再适用时，用户仍可明确continue放弃旧批次。
- 缺失/损坏快照、配置不可用、当前进程已装载/blocked/活动run仍受原准入约束，不强制改ready。

历史查询只描述严格审批候选，开放restore_approval候选动作；真正本地复核在recover的准入区间执行，GET不新增权限准备或保存。复用PermissionMiddleware原_prepare/_default提取的prepare_permission_call及默认判定函数，普通执行和恢复两个实际消费者共用。复核检查当前批次工具集合属于会话活动集合、当前公开参数、subject/anchor/执行环境及side_effects，原allow必须当前本地仍allow，当前Deny不可覆盖。旧session_allow不复活；不读取项目或Skill正文来恢复旧资源豁免，依赖该豁免而当前不能直接allow的批次需重新提出。

恢复复核补充：ExecutionAuthorization还把持久批次中的session_allow传给递归工具，单纯清空内存会话并不能让旧临时规则失效。因此首版不恢复带非空session_allow快照的旧审批批次（包括规则暂时未用于顶层调用的情况）；保留历史与明确continue放弃入口，由新批次使用当前规则。保持原interrupt需要不改图，本片不增加恢复专用授权上下文来覆盖旧快照。

新增/修改：现有session_recovery.py增加validate_saved_approval和restore分支；SessionPersistence._install_session接收已验证pending；permissions.py提取现有本地准备逻辑；RecoveryInput/WebApi开放restore_approval；useConversation自动恢复与失败手动重试共用原恢复入口，保持选择代次检查，审批中断不触发模型/工具/SSE；CLI删旧continue确认提示、沿原审批命令处理恢复pending。无需新表、新依赖、新生产模块或重写图。

验收：sync/async真实SQLite关闭重开，allow+ask混合顺序批次、批准/拒绝/停止、来源run与approval_retry、无旧grant/临时授权；当前Deny、参数/工具/目标/环境变化拒绝且无写入；必要保存失败不安装；Web授权/旧checkpoint/并发锁不变。前端自动一次、草稿、选择切换竞态、失败不循环、待审卡片只待用户决定、列表刷新降级；CLI无continue输入、新消息才执行。下文092A/B的显式入口记载保留阶段事实，当前产品默认以本节为准。

## 1. 产品行为与边界

服务现在已能重启、列旧会话、创建新会话；缺口是旧会话state不是ready时restore拒绝，连历史和继续沟通都受阻。本次保留真实故障事实，同时使可读会话恢复可用：

| 重启时事实 | 本次行为 |
|---|---|
| ready且图/元数据一致、没有待执行任务 | 沿原restore_session/arestore_session恢复 |
| 存在可读checkpoint，旧run未正常结束 | 可看已保存历史，显示恢复原因和可执行动作；不会自动开始模型/工具 |
| 确认是本批工具执行前的官方审批暂停，当前条件仍适用 | 打开会话时自动复核并重建pending，仍整批零执行，批准后走原恢复链 |
| 执行阶段中断、普通模型失败、部分结果已保存 | 打开会话自动整理：保留真实结果、补未确认配对、结束旧执行路径，新的用户消息再启动新run |
| checkpoint缺失/不可读、配对身份歧义、权限/工具配置不可用 | 返回具体原因；能读的历史仍可展示，执行受限只影响此会话；不伪造成功或阻断其他会话启动 |

不交付任意节点原样续跑、外部副作用恰好一次、进程恢复/文件回滚、数据库损坏修复、跨重启Web请求幂等、SubAgent恢复或日志备份体系。`review_required`来自仍活着的当前进程未确认清理时，不允许用本入口绕过资源settle；首版恢复针对重启后尚未装入内存的会话。

[Claude Code官方恢复合同](https://code.claude.com/docs/en/sessions#what-a-resumed-session-restores)明确默认让模型看到前进程工具被中断、先核查效果，而不自动重跑；另有可选自动继续开关，本项目不引入该开关。Pi a69bef789在transformMessages补同ID错误以完成模型输入配对。AgentX 3fcfa0e从已保存消息重建对话，未提供等价HITL恢复证据。这里借鉴恢复语义，继续使用本项目官方SQLite checkpointer。

## 2. 当前链与实际框架依据

```text
服务启动 → SessionStorage/SqliteSaver或AsyncSqliteSaver
 → list_sessions正常
 → WebService.session/CLI选择旧会话
 → SessionPersistence._restore_steps
 → state != ready直接拒绝
```

元数据只在正常ready时推进确认checkpoint指针；失败/审批暂停仍可能有更新的框架checkpoint、pending writes与tasks。不能用旧元数据指针假装后续动作从未发生，也不能仅凭state=running认定此刻有活进程。

当前安装版本为LangChain1.4.2、LangGraph1.2.12、prebuilt1.1.0，保持依赖。框架[update_state接口](https://reference.langchain.com/python/langgraph/pregel/main/Pregel/update_state)用于显式更新；本机源码Pregel.bulk_update_state/abulk_update_state有`values is None and as_node == END`分支：合入已完成task写入并清除待执行任务，再创建checkpoint。

2026-10-16本机临时库探针已验证：重开SQLite后，从真实create_agent前置审批checkpoint清除任务、补同IDerror、再次结束任务后可正常新输入；并行快任务成功+另一任务失败时END保留已保存成功结果，无任务重跑；原checkpoint原始数据仍可读。可重复脚本为[probe_session_recovery_design.py](../examples/probe_session_recovery_design.py)，记录见[能力盘点第12节](../research/tool-system-capability-audit-2026-10-16.md#12-实现前spec与可重复框架探针)。此为框架设计探针，不是本产品恢复API已经实现。

## 3. 092A：只读历史和分类

本节保留092A交付时的边界和验收记录；当前已由092B开放显式continue，最新入口和验证见第4节，历史读取本身仍只读。

新增`application/session_recovery.py`，放恢复读/计划/执行步骤，复用现有IOCall/run_steps/arun_steps；不再扩展已经433行的session_service.py。文件超过500行才沿“纯计划”和“IO执行”责任拆分，不预建服务层级。

拟定接口：

```python
SessionPersistence.history(session_id, agent) -> dict
SessionPersistence.ahistory(session_id, agent) -> dict
PersistentApplication.get_session_history(session_id) -> dict
AsyncPersistentApplication.aget_session_history(session_id) -> dict
```

新增能力不改变原run/resume/restore签名。history只读取，不安装内存会话、不写checkpoint、不触发模型/工具/Auto。它取当前thread最新可读snapshot，说明与元数据最后确认指针的区别，不能悄悄只显示旧ready快照。正常投影仍调用现有message_view，图片按现有文字投影与帧预算处理。

示意返回（messages仍为应用消息对象，HTTP层再序列化）：

```python
{
  'session_id': 's', 'state': 'running', 'messages': [...],
  'checkpoint_id': 'latest-cp', 'checkpoint_ns': '',
  'recovery': {
    'kind': 'interrupted_execution',
    'reason': 'previous_run_unfinished',
    'actions': ['continue'],
    'unconfirmed_tool_call_ids': ['call-edit'],
  },
}
```

kind取ready/pending_approval/interrupted_execution/unavailable。checkpoint_id是用户操作时的乐观并发校验值，不是许可；必须同时校验session/thread/namespace。新建但从未运行的ready会话允许没有checkpoint，沿原空会话恢复；未确认运行且没有可用checkpoint才报告恢复不可用。history对坏数据记录安全类型与原因，不能把任意解码异常吞成空历史。

纯函数`classify_recovery(row, snapshot)`仅据保存事实分类。待审候选须满足唯一根审批节点及唯一官方interrupt、批次/最新AI调用/动作精确匹配、本批没有工具结果或已完成工具task写入、没有任务错误、没有恢复中的marker。不把任意GraphInterrupt当人工工具审批。

092A同步改WebService.session：未装入且受限的会话返回只读历史与recovery，can_run=false；GET不做修复。已ready/已加载会话继续当前路径。CLI会话选择可先展示原因。该片结束时“继续”和“恢复审批”的执行入口仍未开放，不将只读片宣称完整恢复。

### 3.1 092A获准实施的具体范围

本片只新增session_recovery.py的共享history_steps和纯classify_recovery，不提前编写092B的build_recovery_update或092C的授权复核函数。SessionPersistence.history/ahistory复用run_steps/arun_steps；PersistentApplication/AsyncPersistentApplication及CLI薄桥接分别提供对应入口。SessionService、模型接入、权限与ToolBatch无本片改动，不新增数据库表、后台恢复任务或新的ContextVar。

当前WebService.session会在未加载会话上先调用arestore_session，_restore_steps遇到非ready直接拒绝；目标是对未加载且受限会话改走aget_session_history，只读storage.get得到thread_id，再用agent.aget_state读取该thread最新snapshot，classify_recovery返回保存事实及缺结果调用ID，消息沿原message_view/帧预算投影。已加载/ready会话沿原路径；不能从旧row.state=running推断当前仍有活进程，不把旧run进度/SSE拼成当前活动run。

前端只给SessionView增加可选recovery字段，沿原useConversation接收消息；App/Conversation展示只读原因并使用现有can_run=false禁发起执行。当前live=null、没有新run或工具进度；不新增继续/恢复审批按钮，不调用尚未实现的recover路由。CLI的--resume选择到受限会话时先检查原工作区归属，展示已保存消息和原因后结束只读查看，不进入原输入/执行循环。092A对外actions=[]，上节actions=['continue']为完整092目标示意，直到B/C执行入口真实接入才开放对应动作。

示意：row保存state=running且最后确认指针cp-old；最新可读snapshot为cp-new，已有上一批Read结果及下一批Edit请求，Edit缺少对应ToolMessage。history返回cp-new的原messages、kind=interrupted_execution、reason=previous_run_unfinished、unconfirmed_tool_call_ids=['call-edit']；不补造error，不重跑Edit，不把state改ready。用户能查看已有事实；092B之后才由明确操作修复旧执行路径并继续沟通。若最新checkpoint读取失败，返回unavailable和安全原因，记录错误类型；不能把它伪装成空历史或静默退回cp-old。

响应的checkpoint_id/checkpoint_ns为最新快照身份，confirmed_checkpoint_id/confirmed_checkpoint_ns为元数据最后确认指针。messages仍为原BaseMessage对象。配置校验只检查当前模型/活动工具/权限模式/工作区可用性，不执行工具的准备或Auto；不可用时保留可读历史并将kind标为unavailable。最新快照身份不属于该thread或不是根namespace时不暴露其消息。当前进程已装载会话的显式history查询只读返回session_current_process受限原因；Web已装载路径继续沿原视图读取，不误报重启。

Web未装载的非ready会话直接走history；ready恢复因checkpoint、模型或活动配置不可用而被拒绝时也可退到只读history，原执行门禁不放宽。只读响应live=null、active_run_id/latest_run_id=null、pending_approval=false、approval_requests=[]、can_run=false；保存的审批候选由recovery.kind表达。前端只读时禁切模型与发送，仍能创建新会话；CLI在原工作区检查通过后展示安全文本投影并退出，不读新输入。

本片验证以真实临时SQLite为主：ready空/完成会话、running/failed/review_required历史、严格待审候选/非审批interrupt、checkpoint缺失/错误/身份不匹配、配置缺失仍可读事实、同进程活跃会话/跨工作区/API授权。读前后检查checkpoint及元数据不变、内存会话未安装、模型/工具/Auto调用计数均零，sync/async结果一致。扩展Web/CLI/前端只读展示测试并跑全量，恢复执行/强退修复及批准后执行留给092B/C。

### 3.2 092A实际接线与验收（2026-10-17）

只新增一个后端生产模块`application/session_recovery.py`（157行），没有新增运行层对象或数据库字段。新增`SessionPersistence.history/ahistory`、`PersistentApplication.get_session_history`、`AsyncPersistentApplication.aget_session_history`及CLI薄桥的对应入口；CLI展示器增加`show_history`。前端沿原SessionView/useConversation/Conversation/ToolCall接线，没有新的恢复客户端。

正常示例：旧元数据state=running、确认指针cp-old，最新快照cp-new中有Read的真实结果和缺结果的Edit请求：

```text
GET /api/sessions/s（原token/Host/Origin与工作区检查）
 → WebService.session：未装载、非ready
 → AsyncPersistentApplication.aget_session_history
 → SessionPersistence.ahistory → arun_steps(history_steps)
 → IOCall(storage.get, 's')：只读元数据，得到原thread_id
 → IOCall(agent.aget_state, thread配置)：只读最新快照，含已完成pending writes投影
 → classify_recovery：按每条AI批次配对，得到缺结果ID和保存状态
 → 当前模型/活动工具/模式/工作区可用性检查：仅查配置和目录，不准备工具或Auto
 → history返回messages=原BaseMessage、checkpoint_id=cp-new、confirmed_checkpoint_id=cp-old
 → Web沿原message_view和帧预算投影，can_run=false、live=null、actions=[]
 → Conversation显示只读原因，Read“已有结果”、Edit“结果未确认”
```

上述读取不调用invoke/update_state、不安装sessions、不产生新run，不补ToolMessage。CLI选择受限会话时同样走共享history_steps，终端安全投影后退出；不订阅运行事件、不进入input循环。原restore/arestore仍拒绝非ready或不一致快照；同进程已装载Web会话继续沿原视图。显式history读取已装载会话给出session_current_process，不能用它绕过活进程的资源收尾。

失败示例：最新checkpoint读取/解码抛ValueError，history记录`session_history_read_failed error_type=ValueError`，返回kind=unavailable、reason=checkpoint_unreadable；不退回cp-old，不把空messages解释为安全完成。成功解码但thread/namespace不符时返回checkpoint_identity_mismatch，不暴露其他thread正文；消息结构/配对歧义明确受限。旧模型、活动工具或模式缺失、工作区不可用时，保留已读消息并返回saved_configuration_unavailable；整个应用启动配置仍需合法。

待审批候选由真实create_agent根节点/唯一task与interrupt、当前批次ID和逐项参数匹配验证；额外task、其他节点、namespace、已完成task.result、错误、结果消息或恢复marker均不能被误认为可恢复审批。本片不重建pending/grant，也不开放批准。

验证：先看到26例因新API/模块不存在失败；Web/CLI/前端先分别复现原409/初始化拒绝/无只读提示。随后新增坏消息与缺失ID两例红灯，补类型校验和安全缺结果集合。旧Web“重启GET=409”断言迁移为历史200且执行409，保留原异常脱敏、模型零调用和不安装断言；原restore拒绝测试保持。没有放宽断言、skip、依赖或真实配置修改。

- 新增后端43例（history35、Web6、CLI1、终端显示1）；全量`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1906 passed in57.41s**，日志`/tmp/naiyue-092a-all-final.log`。PATH用于本机已安装rg/fd，不是生产硬编码。
- `npm --prefix frontend test -- --run` → **10文件34 passed**；`npm --prefix frontend run build` → TypeScript通过、Vite186模块、CSS11.26kB/JS368.38kB。
- `compileall`、`pip check`、Python AST及108生产模块≤500行、`git diff --check`通过。
- 真实临时SQLite关闭重开、sync/async结果一致、读前后数据库逐条dump不变、原消息ID/内容块/artifact保留、跨批复用call ID配对、并行部分pending writes投影、工具/Auto/invoke/update_state零调用均有确定性断言。无真实模型请求。
- 离线临时库＋真实本地Web在1280×900及375×812浏览器验收：只读提示滚动后仍可见，真实结果与未确认卡片正确，禁发/禁切模型，Enter不执行，窄屏无横向溢出；截图`/tmp/naiyue-092a-desktop.png`、`/tmp/naiyue-092a-mobile.png`。临时页面和服务已关闭。

没有实现recover API、继续按钮、审批重建或恢复写入。唯一下一片为092B审阅；092C及086仍未实施，Windows/Linux原机恢复不在本次证据内。

## 4. 092B：新输入前结束旧执行路径

拟定新接口：

```python
SessionPersistence.recover(session_id, agent, *, checkpoint_id, action) -> dict
SessionPersistence.arecover(session_id, agent, *, checkpoint_id, action) -> dict
PersistentApplication.recover_session(session_id, *, checkpoint_id, action) -> dict
AsyncPersistentApplication.arecover_session(session_id, *, checkpoint_id, action) -> dict
# action='continue'；092C另支持'restore_approval'
```

恢复由明确用户操作触发；工具/模型不能调用本入口作为授权。使用当前应用/库操作准入，拒绝已加载活跃会话、另一次恢复或实际checkpoint已变化；持有原工作区整库进程锁。busy在内存准入锁下设定，必要IO在锁外经已有critical IO执行，不能锁住事件循环。

### 三次框架更新，不调用invoke(None)

2026-10-17用户确认保留三次更新。理由不是get_state看不到pending writes，也不是框架禁止两次：当前最新get_state会投影已保存任务写入，但普通update_state从原checkpoint重建channels，不自动以该投影视图为基底。临时SQLite对比中，只补缺失B消息再END导致新状态丢失A真实结果；显式写回完整messages可在两次更新保住A及artifact，却须由应用承担已有状态回写。最小图另测已保存的非消息字段：只回写messages不能保留该字段，三次方式由框架完整合入任务输出。

项目实际create_agent图也验证了三组顺序（中间关闭重开SQLite、最后新输入、旧工具均零重跑）：两次增量丢A；两次完整消息保留A；三次增量保留A。两次方案均给原checkpoint关联的旧任务写入增加了run_stopped；三次不改变原checkpoint正文及原pending writes。框架普通更新会复用tools任务ID并先put_writes，再保存新checkpoint。因此先END将旧任务合并/结束和恢复修补分开，也使恢复marker有清楚的阶段归属。仅完整消息两次成功不等于全状态/各保存故障点已证明等价。

本机实验命令：`.venv/bin/python /tmp/naiyue-092b-update-order.py` → order_probe=PASS cases=8；`PYTHONPATH=tests:. .venv/bin/python /tmp/naiyue-092b-agent-order.py` → actual_agent_order_probe=PASS cases=3。临时脚本不作为生产入口；092B须将保留原消息/结果/artifact及原任务写入、不重放、三阶段崩溃重试固化为项目测试。

1. 读取最新snapshot和原checkpoint身份；保留原thread_id。生成recovery_id，构造不含正文的恢复记录。
2. `update_state(config, None, as_node=END)` / async版本：合入已完成pending writes，清掉旧执行任务，生成第一个恢复checkpoint。**此后重新get_state**，以实际合入的结果为准。
3. `build_recovery_update(snapshot, record)`按每条AI消息与其紧随的ToolMessage建立配对。真实已保存结果原样保留；只给缺失结果补ToolMessage，清空旧tool_execution及run_stopped标记。`update_state(config, update, as_node='tools')`写入更新，as_node只选择现有state writer，不执行tools节点。
4. 再`update_state(config, None, as_node=END)`，结束第3步可能产生的后续路由；读取并验证next/tasks为空、全部调用配对、恢复记录完整。
5. 最后SessionStorage.finish_run一次事务更新为ready、确认新checkpoint指针、last_run_status='recovered'。安装内存会话，旧队列/一次grant/进度/当前run状态不恢复，session_allow=[]。
6. 返回恢复结果；只有用户的新消息才调用原arun/run，重新加载当前项目指令、活动工具和权限，创建新run。

每次更新携带`config['metadata']['naiyue_recovery']`，值为**JSON字符串**，内容含version/recovery_id/source_checkpoint_id/source_namespace/原state/phase。当前SQLite saver仅透传标量metadata，不直接存字典；本机重开库探针已验证该字符串和对应checkpoint一起保存。phase依次cleared/paired/finished，不新增数据库表/列或修改schema4，不写LangGraph内部表/私有channel。

实施细节：record另绑定session_id/thread_id，并保存源状态是否为严格前置审批（pre_tool_approval）；重试严格校验版本、身份、source和phase，不凭任意metadata字符串开放恢复。checkpoint_id必须匹配当前最新快照，即使阶段marker存在也不接受旧页面直接恢复；刷新history取得当前ID后显式重试。已完成且已确认的ready会话不重复恢复。

SessionPersistence增加当前应用唯一的_recovering准入标识，复用_lock短临界区，与start/restore安装相互检查；必要IO仍在锁外。当前会话已装载、保存失败_blocked、其他run活跃或恢复重入均不允许用本入口绕过。提取现有配置准备及内存安装为SessionPersistence内部方法，restore与recover共用，不重建SessionService。

Web的POST recover正文仅checkpoint_id/action='continue'，沿原授权和工作区过滤，并使用RunDelivery.start_lock与start/resume串行准入；不创建DeliveredRun或幂等回执，重复旧checkpoint请求409。成功后返回刷新后的SessionView。history对配置/消息/身份可用且未装载的中断状态开放actions=['continue']，不提供restore_approval。前端按钮明确“结束旧运行并继续会话”，恢复中禁重入、切会话和发送；仅恢复状态，不自动提交草稿或调用模型。CLI在交互终端展示历史后要求输入continue才恢复，空行退出；非交互仍只读退出。恢复失败保留历史及输入，必须刷新当前checkpoint再重试。

原checkpoint仍存在；恢复追加新checkpoint，不执行回滚或删除。记录保存source身份用于查证，不能用get_state(旧checkpoint_id).next是否等于最新查询时next来判断数据是否被改；框架对显式历史查询的pending-write投影不同，验收比较原始checkpoint内容与保存结果。

### 缺结果的具体数据

```python
ToolMessage(
    id='recovery-' + stable_digest(assistant_message_id, tool_call_id),
    name='edit', tool_call_id='call-edit', status='error',
    content='The previous run ended before a result was recorded. '
            'The operation may already have taken effect. '
            'Inspect the current state before retrying; it was not automatically replayed.',
    additional_kwargs={'naiyue_recovery': {'recovery_id': '...', 'reason': 'result_unconfirmed'}},
)
```

稳定ID避免修复重试重复追加；按所属AI消息绑定，不能只用可复用的call_id全历史去重。非法/重复身份、冲突多结果等无法安全配对时返回具体受限原因，不删除原调用或编造成功。若明确是整个前置审批批次未放行，用户选择continue等于放弃该旧批次，可准确反馈“审批前取消、未执行”；其他情况一律不以缺少日志推断未执行。

恢复不会重新发布旧tool_call_started/finished来冒充本run执行。UI通过恢复说明和ToolMessage知道旧动作未确认；本次恢复没有伪造run_id。默认诊断只记录session/recovery/checkpoint身份与原因，不记录正文或参数。

### 恢复本身又中断

第2步的cleared marker与“合入结果/清除任务”在同一个官方checkpoint写入。第2步之前失败，旧状态不变；之后强退，最新snapshot marker指出原始source和已到达阶段。再次显式recover沿此recovery_id和实际snapshot幂等补齐，保持同ID结果，只追加尚缺配对；不回到旧任务续跑。

finished已保存但finish_run失败时，下次核对finished marker、完整配对和无next后只补元数据确认。任何必要写入未确认都不安装可执行ready会话；历史仍可读、其他会话可启动。不得只catch保存失败后把state改ready，也不要求跨checkpointer/元数据两连接的大事务。

普通模型失败且没有孤立工具时也走该结束旧任务/重置run标记路径，保留已保存用户输入。不补虚构助手答案，不自动把原用户请求重新提交给模型。旧宿主命令可能仍存在，本次不按遗留PID盲目终止、不保证副作用已经停止；恢复说明要求核查。

### 4.1 092B实际接线与调用链（2026-10-17）

本片没有新建生产模块、表或ContextVar。现有session_recovery.py增加recovery_record/build_recovery_update/recovery_steps；SessionPersistence增加recover/arecover和短临界区准入，原restore字段准备/内存安装提为_configuration_steps/_install_session，history/restore/recover共用实际入口。运行层、工具执行和SessionService不变。

```text
用户明确选择continue
 → Web POST /api/sessions/{id}/recover：RecoveryInput只接受checkpoint_id/action
 → 原token/Host/Origin与工作区过滤
 → WebService.recover持有RunDelivery已有start_lock，拒绝活跃run及旧checkpoint
 → AsyncPersistentApplication.arecover_session
 → SessionPersistence.arecover / _recover_steps：拒绝已装载、_blocked、活动run和恢复重入
 → arun_steps(recovery_steps)：只在必要IO外持有短锁
 → 现有配置准备、最新快照/根thread/namespace/请求checkpoint核对
 → 如果有marker，校验版本/完整字段/所属session与thread，读取原source核对存在与身份
 → 首次依次写cleared(END)、paired(tools writer)、finished(END)，各次读取确认实际新checkpoint
 → 重试按已保存phase跳过已完成操作；finished后仅补元数据确认
 → 检查next/tasks为空、消息完整配对、旧tool_execution清空和run_stopped=false
 → SessionStorage.finish_run确认ready及新checkpoint，随后_install_session安装空队列/临时授权
 → 返回刷新SessionView，不创建DeliveredRun、回执、run或SSE
 → 用户新消息才进入原arun/权限/模型/工具链
```

同步入口用同一generator及run_steps；CLI薄桥调用async入口。CLI交互终端显示历史后输入continue才恢复，空行退出；非交互仍只读退出。前端沿原useConversation新增recover，成功应用返回视图并保留草稿，不自动发送；失败刷新当前checkpoint，用户再次明确操作才重试。侧栏刷新为可降级观察，失败不能改写已确认恢复结果。RunDelivery已有start_lock足够，本片不修改其代码，不增加无用途转发方法。

build_recovery_update仅写新增ToolMessage，原结果/ID/内容块/artifact不重建。恢复消息ID按所属AI消息ID与call ID生成稳定摘要；本批未确认结果写result_unconfirmed，严格前置审批取消写cancelled_before_approval及明确未执行。其他GraphInterrupt或副作用不明不冒称未执行。恢复不调用invoke/ainvoke、工具准备/执行或Auto，不伪造原run终局事件。

失败路径：必要checkpoint或元数据写入失败时记录安全error_type并返回session_save_unconfirmed，不安装会话；当前phase已落盘则刷新后可显式重试。异步取消等待已开始写入settle后传播，finally释放恢复准入。源checkpoint及其原pending writes保持，恢复修补属于后续checkpoint。metadata写成功后、内存安装前强退时，下次按普通ready恢复；若新run尚未生成checkpoint即失败，允许只确认已完成恢复；若新run已保存新输入后模型失败，则生成新的recovery_id处理当前源状态。

确定性入口：test_session_recovery.py、test_session_recovery_crash.py、test_recovery_pending_writes.py、test_web_recovery.py及CLI/前端history用例。先10例核心红灯后实现转绿，Web3例和前端入口先复现缺口；新增侧栏刷新故障先红后修，不修改全局测试基础设施。092A旧actions=[]断言按已授权continue合同迁移，数据库只读/模型零调用/执行门禁和脱敏断言保留。

独立子进程在before_cleared/cleared/during_paired_writes/paired/finished/before_metadata/after_metadata七处明确阶段os._exit，sync/async共14例，使用进程退出码而非sleep猜时点。另有真实Write提交文件后、返回工具结果前强退，验证重启不阻塞、效果保留且不重放。真实并行任务在A结果已入SQLite后才允许B失败，恢复保留A原ID/artifact、原checkpoint及原任务写入。

最终验证：新增后端44例，`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1950 passed in67.43s**（/tmp/naiyue-092b-all-final.log）。前端`npm --prefix frontend test -- --run` → **10文件37 passed**；`npm --prefix frontend run build` → TypeScript通过、Vite186模块，CSS11.49kB/JS369.88kB（/tmp/naiyue-092b-ui-final.log、/tmp/naiyue-092b-build-final.log）。compileall、pip check、AST/生产文件≤500行、文档文件链接及diff检查通过。

本地临时库＋假模型Web在1280×900/375×812验收：恢复前按钮和说明、恢复后真实Read结果及Edit未确认说明、草稿不丢、发送/切模型重新可用，无横向溢出或浏览器错误；模型调用数为0。截图/tmp/naiyue-092b-before-desktop.png、/tmp/naiyue-092b-before-mobile.png、/tmp/naiyue-092b-after-desktop.png、/tmp/naiyue-092b-after-mobile.png，页面和临时服务已关闭。最终仅增加侧栏刷新故障的降级分支，由前端回归验证；真实配置/依赖/用户库未修改，无暂存/提交/push，未做真实模型请求或Windows/Linux原机恢复。

## 5. 092C：只恢复确认尚未执行的审批

`restore_approval`不清图、不补未知消息，复用原官方interrupt和Command(resume=...)。GET描述候选动作，打开会话自动选择此动作，以下复核全部通过才安装pending；失败保留历史，不自动改走continue：

1. 前置暂停结构满足092A条件；实际图节点、prepared calls和interrupt action_requests一致。不能只看元数据state=interrupted。
2. 当前模型/工具可用，原参数仍通过当前公开schema；按当前启动配置、真实工作区/目标/环境重新做**本地**权限准备与目标检查，不调用模型/Auto，不执行工具、不读取文件正文。
3. 保存subject与重新准备subject等价且目标anchor未变化。原allow调用现在仍可由当前本地规则/default直接allow；原deny仍deny。原ask保持人工审批，当前Deny不能被批准覆盖。
4. 当前session_allow为空，旧batch.session_allow必须为空，避免递归工具复活旧临时范围；旧Auto得到allow、现在无法在本地证明allow的调用也不偷用旧授权。整个批次保留明确continue入口，由模型在新run重新提出；不在恢复GET内偷偷重跑Auto。
5. 旧batch.mode仍有效；同进程冻结批次/模式下批生效保持。来源run_id必须有真实元数据记录，不能生成一个不存在的旧run身份。

为复用现有逻辑，`agent_runtime/tool_execution/permissions.py`把现有_prepare及_default的确定性部分迁为模块内`prepare_permission_call(...)`和对应默认判定函数：PermissionMiddleware.prepare_local_batch与application恢复校验两个实际调用方共用；删除无消费者的旧转发，不复制一条权限管线。模型审查仍只在原middleware钩子里发生。

验证通过：复用application/session_approval.py::pending_tool_approval，把snapshot.values和唯一task.interrupts组成原结果形状，重建items/batch_id/message_count；requested_run_id取原真实row.run_id。pending_turn=None，沿当前TurnObserver.activate_resume已有approval_retry分支创建新观察turn，不伪造跨进程旧turn事件连续性。

SessionStorage.finish_run把可恢复审批的元数据规范为interrupted并指向实际审批checkpoint，然后安装session：busy=false、pending有效、session_allow=[]。这样原Web对state=running的拦截不误挡崩溃前仅漏存元数据的纯待审。

批准链保持：

```text
用户决定 → SessionService._resume_steps建立新run
 → prepare_resume产生新的OneUseApprovalGrants（精确batch/call/subject绑定）
 → 原Command(resume=...)恢复官方HITL
 → 原ToolBatch.after_model/aafter_model建立本次上下文计划
 → ExecutionAuthorization消费新grant → require_file_target最终目标复核
 → 真正工具执行 → 原保存/事件合同
```

不持久化或复活旧grant/permit，不用mode/“恢复”按钮自动批准。原pending不可用/条件变化时不把整会话永久封死，展示原因及continue；新调用仍走当前Deny/Allow/默认策略/模式。

### 5.1 092C实际接线与当前入口（2026-10-17）

新增生产模块/表/依赖均为零。后端修改6个现有文件：permissions.py把_prepare/_default移为prepare_permission_call/default_permission_decision，原逐批准备与恢复复核共用；session_recovery.py增加validate_saved_approval及restore_approval分支；session_persistence.py的_install_session接收已验证pending；cli.py自动选择动作并处理失效审批的明确放弃；web/schemas.py开放动作，web/errors.py映射固定公开原因。session_approval.py的pending_tool_approval/prepare_resume、现有应用薄桥、WebService/RunDelivery不改。

前端修改5个现有文件：useConversation.selectSession读到旧会话后自动调用原recover，按kind选择continue或restore_approval；成功共用applySession并保留草稿；失败刷新最新视图但不再自动恢复。api.ts开放动作与错误说明，App/Conversation传递手动动作、显示恢复中/失败重试；原ApprovalPanel直接消费返回的approval_run_id/approval_requests，无新恢复客户端。过期选择的GET响应先检查selectionGeneration，不触发旧会话恢复。

```text
Web选择旧会话 → GET session → history_steps只读分类
 → useConversation.selectSession自动选择动作 → POST recover（原授权/工作区/start_lock）
 → arecover_session → SessionPersistence._recover_steps（原准入）
 → recovery_steps读最新snapshot并核对请求checkpoint
   ├─ interrupted_execution：continue，沿092B三次更新保存ready
   └─ pending_approval：validate_saved_approval
       → 当前注册表/schema/本地Deny-Allow-default及subject/anchor/环境复核
       → pending_tool_approval重建items/batch_id/message_count，requested_run_id取真实row.run_id
       → finish_run(interrupted, 原审批checkpoint) → _install_session(pending=...)
 → 返回SessionView：审批卡片或可新输入；无DeliveredRun/回执/旧事件/模型或工具调用
```

用户提交批准/拒绝后，仍走原WebService.approve → RunDelivery.resume → SessionService._resume_steps → prepare_resume新建一次性grants → Command(resume=...) → 固定官方审批节点 → ToolBatch.after_model/aafter_model建立当前计划 → ExecutionAuthorization消费新grant → 工具最终目标复核与执行。pending_turn=None使观察沿已有approval_retry起新turn，随后模型轮产生model turn，不伪造跨进程连续turn。官方拒绝ToolMessage先于允许调用的工具结果入图，此顺序不改；整批串行执行合同保持。

正常例子：Read工作区内文件为allow，Write外部文件为ask，关闭并重开SQLite，自动恢复直接显示Write审批；此时Read/Write均未执行。批准后按原批次执行各一次；拒绝Write只执行Read，Write得到官方拒绝消息。失败例子：软链接实际目标变化、当前Deny、旧Auto allow无法本地证明或非空旧session_allow，restore_approval返回approval_restore_unavailable，不改checkpoint、不安装会话，也不自动continue。Web保留历史/草稿和按钮；CLI交互仅此失效情形允许明确输入continue放弃旧批次，正常恢复无额外确认，非交互不替用户放弃。

新增test_restart_approval.py；扩展现有test_persistent_cli.py、test_web_recovery.py和frontend/history.test.tsx。核心11例先红（缺动作/restore分支），前端自动入口与CLI去确认先红后实现；新测试中工具拒绝结果顺序及turn字段按现有正式合同纠正，不修改框架行为或削弱旧断言。旧CLI纯只读测试迁为配置缺失的真实受限场景，仍断言不消费输入/不调用模型/数据库不变。没有改conftest或共享test helper。

真实独立子进程在“审批interrupt已保存但终局元数据未保存”和“恢复元数据已保存但内存安装前”os._exit(91)，重开后均可恢复原pending并批准执行一次。浏览器临时SQLite＋假模型验证：打开待审批直接出现卡片、打开执行中断会话自动整理并显示未确认说明，输入可用，无额外点击、无控制台error；验收期间模型调用0。截图/tmp/naiyue-092c-approval.png、/tmp/naiyue-092c-ready.png；临时页面/服务已关闭。

最终验证：新增后端21例，`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1971 passed in69.72s**（/tmp/naiyue-092c-all-complete.log）；前端新增3例，`npm --prefix frontend test -- --run` → **10文件40 passed**（/tmp/naiyue-092c-ui-final.log）；`npm --prefix frontend run build` → TypeScript通过、Vite186模块，CSS11.49kB/JS370.78kB（/tmp/naiyue-092c-build-final.log）。compileall、pip check、285份AST/108生产模块≤500行、当前文档文件链接、diff检查通过。新增强退两例均按明确保存阶段退出，无sleep猜测；未改全局测试基础设施。当前证据为macOS真实SQLite/文件IO及原生异步假模型，未做真实供应商请求或Windows/Linux原机恢复；WIN1保持开放。原三次更新/幂等阶段恢复、源checkpoint保留及必要写入失败门禁不变；不声明任意节点续跑或副作用恰好一次。

## 6. 文件地图与界面入口

| 文件 | 修改 |
|---|---|
| `application/session_recovery.py`（新增） | history/classify/build_recovery_update/validate_saved_approval及共享IO步骤；实际入口为SessionPersistence。 |
| `application/session_persistence.py` | history/ahistory/recover/arecover委托共享步骤；复用/提取恢复ready会话的字段校验和安装，保留当前进程保存失败_blocked及活跃准入保护。 |
| `application/persistent.py`、`async_persistent.py`、`cli_async.py` | 新history/recover API及CLI桥接，原签名保持；不对内存无持久应用伪造恢复。 |
| `application/session_approval.py`、`agent_runtime/tool_execution/permissions.py` | 上述精确复核/原pending形状复用；没有第二套审批恢复协议。 |
| `web/service.py`、`app.py`、`schemas.py` | GET返回只读recovery；新增POST `/api/sessions/{session_id}/recover`，正文checkpoint_id/action；复用原token/工作区授权/准入锁。成功后刷新session，再走原start或approve。 |
| `web/run_delivery.py` | 为恢复复用start/resume的同一会话操作锁；恢复不伪造DeliveredRun/旧SSE/receipt。并发恢复/启动/批准只能一项获准，旧checkpoint请求409。 |
| `frontend/src/types.ts`、`api.ts`、`useConversation.ts`、`components/Conversation.tsx`、`App.tsx` | 只读历史提示、继续会话/恢复待审批按钮、恢复中禁重入；成功后重新拉当前消息和pending；保留真实结果与未确认提示，勿把旧running永远显示为活跃。 |
| `application/cli.py`及`cli_display.py` | 复用旧会话选择流程显示恢复原因；受限会话明确选择continue或restore_approval，确认后再输入/审批，不在启动时自动执行旧调用。 |
| `session_storage.py` | 原schema/finish_run接口足够，预计不修改；若实施发现需要额外持久字段，须先回到本Spec讨论，不能临时加表。 |

前端扩展现有会话hook和Conversation，不新增平行的恢复客户端。授权检查业务入口显式可见；history/recover都要校验当前服务工作区，不能绕过原本的API token检查。文件地图中的显式选择恢复按钮是092A/B阶段交互，092C改为打开时自动触发；按钮只用于失败重试或明确放弃。session_approval、RunDelivery及持久应用薄桥本片均只复用，未新增转发或修改。

## 7. 正常/失败例子和测试

**部分执行强退**：模型提出Read A、Edit B；A结果已存，B可能替换成功但结果未存，服务强退。重启可看历史；用户继续，END先合入A，B补同ID未确认error，原Edit不执行；新输入“检查刚才修改是否生效”进入新run，模型通过当前Read权限核查。B的文件不自动回滚，不因补error声称未修改。

**前置待审强退**：Read A本地allow、Bash B为ask，整批尚未执行。重启校验图与当前策略，恢复同一批pending；批准B新建grant，A/B仍按批次顺序执行，各一次。若当前Deny新增匹配B或A的旧allow来自已消失临时规则，则不恢复批准，用户继续时终结该未放行批次并重新沟通。

**恢复保存失败**：清任务/补消息完成后元数据写入失败，返回session_save_unconfirmed，不能开放执行；重开库看到finished marker，仅补元数据，不再生成第二条B结果。原图保存错误/必要清理错误不能被进度观察故障覆盖。

拟新增测试：`test_session_history.py`、`test_session_recovery.py`、`test_session_recovery_crash.py`、`test_restart_approval.py`、`test_web_recovery.py`；扩展现有持久化、Web、CLI和turn事件测试。旧“所有非ready一律禁止”测试迁为精确分类、动作和保存断言，保留损坏/mismatch/真实权限/跨工作区拒绝，不能直接删除失败边界。

先写失败测试证明旧API/门禁与目标不符，再实施。真实临时SQLite、独立子进程通过Event/明确阶段通知后os._exit，在工具前、提交后结果保存前、pending-write后及三步修复/元数据间强退；父进程重开验证。不用极短sleep猜持久时点。

必须断言：原source checkpoint原始内容未改、已保存结果/ID/artifact保留、缺结果仅补一次、原工具计数不增加、next/tasks为空、无恢复期间模型调用、metadata指针最后确认；sync/async均覆盖。审批另测allow+ask混合串行批次、当前Deny/目标/工具变化、Auto不重复审查、grant不可复用、批准后执行一次、approval_retry观察合同及旧Web receipt不被冒称跨重启幂等。

```bash
.venv/bin/python -m pytest -q tests/test_session_history.py tests/test_session_recovery.py tests/test_session_recovery_crash.py tests/test_restart_approval.py tests/test_web_recovery.py
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

以上为完整092目标命令，test_restart_approval.py已随092C实施。092A/B实际入口与验证分别见第3.2和4.1节，当前自动入口见第0和5.1节；不将设计探针替代产品TDD/完整回归/Windows原机。086以后独立处理子thread恢复，不通过本Spec默认启用；进度与旧事件只保留当前可确认事实，不作为执行证明。
