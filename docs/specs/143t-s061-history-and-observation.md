# Spec143T：S06.1 持久历史与实时观察

状态：2026-11-02 S06.1a和S06.1b均已完成本机验证，分别验收；本机绑定与正式侧栏仍待后续分片。产品入口以143s为准，不恢复云端长期记忆或本机Agent提交任务。

## 1. 当前链与缺口

S06.1a后的`RunUnitOfWork.finish_run → RunnerJournal.finish/_messages → RunnerClient.publish → CloudRuntime.append_events → CloudHistory.append`已提供必要保存、版本/根子/回复投影、同epoch/seq重传和ACK，以及owner分页/详情。Runner尚未投递实时观察和完整用量，侧栏过程仍待S06.1b接线。

Coordinator只打开自有history库，不调用任务`open_storage`或打开其SQLite/WAL。checkpoint仍归Runner，聊天可读与checkpoint可续跑分别判断。

## 2. 两个单元

| 单元 | 范围 | 完成边界 |
| --- | --- | --- |
| S06.1a | 完整档案合同、消息版本/回复/根子投影、长正文、分页/详情 | 必要保存与游标原子确认；停容器可查完整已确认档案。无正式UI、实时token或云端记忆。 |
| S06.1b | 有界实时观察、用量、进度快照/SSE与重连 | 复用a的正式档案校正草稿；断流/慢客户端不影响执行；为侧栏提供真实接口。 |

不将两者混成一个“通用事件总线”重写。当前`StreamingMessageProjection`、聊天DTO、回复/用量算法按实际字段复用；不复制本机Host或把普通observer当必要保存。

## 3. 持久事件与语义投影

继续使用现有task_id/generation/epoch/seq及source_generation。新代凭证可补发旧来源outbox；连接/运行变化不重置持久epoch。

| 事实 | 必须包含的身份/内容 | 生产者与校验 |
| --- | --- | --- |
| 消息/修订 | thread/agent_path、message_id、revision、reply_id、parent_message_id、消息类型、正文摘要与完整内容身份、发生时间 | Runner档案必要保存生产；Coordinator校验枚举/大小、父子和版本一致性，不采信为所有者权限。 |
| 回复边界/子Agent | root/child thread、agent_id/path、父run/tool_call与回复输入边界 | Runner实际注册关系生产，不能仅由正文字符串推断。 |
| 运行/控制终局 | 原command/run、状态、已确认终局seq、保存和输出完整性 | 沿现有owner回执与Runner报告配对；报告不修改全局授权。 |
| 附件/大输出 | 受作用域约束的产物ID、类型、大小/hash、是否可用 | 字节不放SSE/普通JSON；没有实际产物则明确不可用。 |

当前owner运行命令是用户输入权威来源，任务自报human标记不能产生新的所有者输入。系统提示、密钥、隐藏内部状态不作为公共消息DTO泄露；展示模型实际公开的文本/思考摘要，不伪造内部推理。

事件台账、消息版本/回复投影和接收游标在Coordinator同一事务提交后ACK。重复seq同摘要无重复更新；冲突/缺口拒绝。字段新增必须贯通Runner生产、契约、序列化、数据库、查询DTO与测试。既有B历史格式使用自有版本检查和可恢复迁移；不重置日志、伪造缺失旧字段或运行A迁移。

分页固定读取上界，消息在该上界后的新修订不能污染前一页；保留可核验的消息版本或等价的固定版本索引。投影可从自有已确认台账重建，不能从任务数据库“修复”。迁移/重建失败有明确状态，不静默清库。

## 4. 长正文与有界读取

长正文不能因为超过单帧而使必要保存永久失败，也不能用截断预览代替保存。按同一消息版本分段提交，记录分段序号、总长度和完整sha256；只有段齐、hash一致且提交标记确认后才进入可读正式档案。未完成部分保留为待确认，根终局不能绕过它宣布必要历史保存成功。

条数、单帧/总正文、段数量、分页字节、读取耗时和临时未完成数据均有配置上限；超限返回明确错误。分页返回截断预览时附详情引用/截断标识，详情使用版本＋offset读取，不能让任意正文突破内存预算。二进制按独立产物处理。

每次查询为有界短读事务；SSE等待、网络发送和逐页翻阅不持有事务。过期/伪造/跨task/thread游标拒绝或要求重新快照；不能跳过未知缺口。

## 5. 查询与订阅接口

以下为拟新增owner路由，均先鉴权、校验实例和task再读取；task凭证不得使用。原`/history/events`保留为同一台账的诊断查询，不另建历史真相。

| 接口 | 合同 |
| --- | --- |
| `GET /api/cloud/v1/tasks/{id}/history` | 有界最近窗口；可选child标识；返回消息DTO、固定coverage和分页cursor。 |
| `GET /api/cloud/v1/tasks/{id}/history/messages/{message_id}` | 明确thread/revision、offset/limit，返回有界正文/思考摘要/工具详情；版本变更明确报错。 |
| `GET /api/cloud/v1/tasks/{id}/usage` | 按模型/调用身份聚合，分页，含capture_incomplete与来源范围。 |
| `GET /api/cloud/v1/tasks/{id}/snapshot` | 正式档案水位、任务状态、最近窗口和可用实时草稿，不承诺未确认内容已持久保存。 |
| `GET /api/cloud/v1/tasks/{id}/stream` | owner SSE；有界快照/增量、心跳与重同步信号；支持受校验的Last-Event-ID，不接受URL中的认证凭证。 |

本机S06.2服务按持久关联转发这些任务接口；不能把主会话消息透明转发到云端。新能力仅在真实注册/接线后出现在capabilities中，无占位成功路由。

## 6. 实时观察与恢复

Runner将现有运行事件以快速有界队列接入，后台投递在现有受鉴权任务通道上新增严格的观察帧。帧核验task/generation/boot/connection与run/根子归属，不能替代必要事件的HTTP/outbox ACK或更新所有者回执。

持久日志水位和实时展示水位分开：前者沿task epoch/seq；后者使用观察流epoch/seq、Coordinator重启后重建的快照基线。连接重建不能假装旧草稿仍完整。字数/时间合并delta，保留消息与工具调用身份；队列溢出标记不完整并发送重同步指示，不能无限缓存或阻塞模型。

订阅先取得一致的快照与展示水位，再续增量；获取快照至开始订阅之间的事件必须可补齐。重复帧不重复应用，缺口/epoch变化重取快照，不猜测拼接。缓存窗口不足时回到正式档案＋最新可用草稿；丢失草稿明确标记，正式消息到达后按ID/版本替换而非重复追加。

用量复用已有invocation_id/根子归属去重，SDK重试沿原观察合同计量，不把代理上下游两份流量相加。用量和诊断依旧是可观测降级项：缺失显示不完整，不覆盖主异常或把已完成任务改失败。

## 7. 正常/失败链与实施文件

正常：Runner必要保存完整消息→outbox→Coordinator台账/投影/游标事务→ACK；owner停容器查history仍可读。运行中观察→有界展示投影→SSE→侧栏草稿，最终档案按消息ID校正。

失败：段缺失/hash错→不发布该正式消息；ACK丢失→同seq补发；SSE缺口/重启→快照重同步；checkpoint损坏→历史仍可读但不能承诺续跑。观察异常要保留诊断，不冒充必要保存异常，也不得吞掉测试检查器失败。

主要影响`storage/runner_journal.py`、`storage/unit_of_work.py`、`contracts/cloud_events.py`、`storage/cloud/history.py/schema.py`、`cloud_runner/kernel.py/transport.py`、`application/cloud/runtime.py`和`service/cloud`查询路由；实时装配复用现有消息/用量模块。超过8文件，实施时按a/b分别TDD和回归，不在构建失败状态继续新功能。

拟测试入口：`tests/cloud/test_history.py`、`test_history_projection.py`、`test_history_stream.py`、`test_history_usage.py`。覆盖消息修订分页、根子配对、大正文分段/超限/取消、重复/冲突/缺口、snapshot订阅竞态、慢客户端/重启、停容器读取、observer故障与必要保存身份。未实施前这些名称不是可运行证据；实现后的全量/构建门禁沿143g。

## 8. S06.1a 实施细化

小消息继续一个必要事件；v2消息头增加真实归档的revision/position、线程注册关系和当前回复边界。大消息仅将body_json分段，段头、顺序、总字节/hash和commit严格核验；待收齐数据有独立额度。Coordinator保存台账、版本投影、线程/回复关系和经管理命令核验的输入来源。旧B v1库只升级自有schema，旧消息保留诊断台账并明确metadata_incomplete，不补造旧线程/版本。

查询增加`GET .../history/threads`有界线程目录。history默认根线程最近窗口，按稳定position向前分页；签名cursor绑定实例/task/thread/epoch/coverage和有效期，固定上界选择当时最新revision。详情明确thread/revision，按公开字段content/reasoning/tool_calls/file_change/mcp_result的文本表示进行offset读取，禁止读取原始SDK消息JSON。公共message_view从Web层移到agent_runtime共享展示模块，所有现有消费者同时迁移，避免应用层导入Web和重复投影实现。SQL仍仅由storage持有；CT1→单库短事务，无新锁、不跨网络持锁。

S06.1a只接真实history/threads/detail能力和内部CLI查询，usage/snapshot/stream留待S06.1b，不注册占位接口。历史正文上限、未完成总额度、段数、预览/详情字符、cursor有效期统一由HistoryLimits配置，TaskBootstrap传给Runner。必要保存失败继续回滚本机档案与outbox，未知执行不自动重放。

## 9. S06.1a 实际接线与验证记录

实现链为`RunUnitOfWork.finish_run → RunnerJournal → storage/runner_history.append_messages → message_frames → outbox → CloudRuntime.append_events → CloudHistory.append → history_projection.project`，台账/来源证据/分段/投影/水位同事务提交。长正文commit前没有正式消息；缺段或损坏不能让根/控制终局越过。Small消息仍一条事件，不重传全会话。system消息不导出公共档案。

查询链为`service/cloud/history_routes → CloudArchive → CT1 → CloudHistory/history_reads`；只打开管理history目录。history v2表保留每个完整revision及其接收seq，页固定coverage选择该时点版本；public DTO保留工具call ID/name/status和回复边界。正文按字段详情读，预览不含完整工具参数/隐藏SDK配置；正文/参数完整数据保存在档案，可有界读取。代码文件、二进制和测试产物仍走143b/后续S07.2，不冒充聊天正文。

v1管理history升级为v2时不修改events或epoch。既有v1消息保留诊断记录，经过形状/hash校验的旧消息仅作为首次较高revision和旧工具父记录的存在证据；保持`metadata_incomplete`，不编造旧版本、线程关系或所有者输入。投影可在`CloudHistory.rebuild()`从自有journal重建，重建核对原摘要并原子回滚失败，不读任务库。当前没有运行中的公开重建/运维接口。

默认HistoryLimits：单正文16MiB、待收齐32MiB/16消息、最多16384段、预览512字符、详情16384字符、正文JSON深度64、cursor3600秒；全部可通过显式CloudSettings配置并冻结传给TaskBootstrap。响应继续受max_frame_bytes限制，SQL和读事务受history_read_timeout_seconds限制。超限明确失败，不用截断冒充完整保存。

内部CLI：`nyt cloud history TASK --settings CONNECTION --view messages [--thread THREAD] [--cursor CURSOR] [--limit N]`；`--view threads`读目录，`--view detail --thread THREAD --message-id ID --revision N [--field content|reasoning|tool_calls|file_change|mcp_result] [--offset N] [--limit N]`读详情。原默认`--view events`保留台账诊断消费者。message_view已迁入agent_runtime共享模块，本机Web/CLI/探针消费者一起迁移，原Web定义删除，不保留兼容转发。

本片验证证据归档`build/spec143-s061a-20261102/`。先失败测试覆盖版本分页/长正文/所有者API/CLI，审查回归覆盖旧B修订、双工具结果、超大整数/畸形SDK与有界预览；可靠性验证还包括收齐前终局拒绝、临时额度/跨run、读超时释放事务、摘要损坏重建回滚及真实Runner长回复关闭/Coordinator重开后完整读取。最终全量、构建和安装结果以该目录verification.json为准，进行中的命令不构成通过证据。

最终结果：后端两分片3388＋3188＝6576项、前端69文件568项与TS/Vite构建、四平台标准wheel/Twine及Mac源码目录外干净安装通过；467个Python源文件逐包匹配，旧6550项测试身份全部保留、新增26项。独立安全/架构复核5项发现已关闭。最初全量发现诊断探针仍引用旧message_view，迁移实际脚本后两分片重新完整通过。详见[verification](../../build/spec143-s061a-20261102/verification.json)。无真实供应商请求、公司/最终容器验收、配置修改、提交或部署；不含实时观察/UI/云端长期记忆。唯一下一片S06.1b，当前剩余13单元。

## 10. S06.1b 实施细化

正常链：`SessionService.subscribe_events → Runner有界观察队列 → RunnerClient独立观察发送协程 → 原control WS严格observations帧 → Coordinator鉴权/fence/owner run核验 → 可选usage保存与实时投影 → owner snapshot/SSE`。不增加第二Agent循环或必需outbox。Runner观察器在固定会话创建后绑定，恢复重装SessionService时重新绑定，关闭时解除。原ModelUsageCallback已安装，复用其invocation_id和归一化计数，不重复注册回调或统计模型代理流量。

帧拥有独立Runner观察epoch/seq，带真实thread及根子来源；只输出显式公开消息、工具/进度/有限诊断与用量字段。相邻同消息delta在队列内合并，保留起止index；队列/字节/单记录/实时缓存/回放环/订阅数全部受ObservationLimits限制。离线只保留有界队列；超限、来源缺口或重连清除不完整草稿并标记capture_incomplete，不伪造完整过程。观察帧不能更新管理命令回执、正式档案水位或权限。

Coordinator实时状态仅由owner loop操作，逐thread复用StreamingMessageProjection；根子scope与owner run先在CT1内校验。usage存于Coordinator自有history v3的独立表，按invocation_id核验归属和重复终局，复用既有用量汇总算法；重启将未闭合捕获标记不完整，已落盘用量可查。usage IO/冲突降级有诊断，不覆盖必要保存/执行状态。schema升级只增加自有表，保留v1/v2日志与投影。

snapshot组合管理任务状态、143a正式窗口及有界实时视图；观察版本在异步读期间变化则有限重试，不能把旧快照配新水位。SSE用按task/thread/instance/epoch/seq签名的Last-Event-ID；订阅取得快照后从该水位回放，缺口/重启给resync和新快照。正式历史ACK及控制状态变化使订阅重新快照，按消息ID用已确认版本替换草稿。慢客户端只占订阅引用和共享有界回放环，不持DB事务；每次send限时、订阅有绝对期限，取消/关闭释放引用。服务停服先封口/唤醒观察订阅，再排空监听/worker与现有资源。

新增owner `snapshot/stream/usage`只在真实接线后声明capability，task令牌不能读。首版仍无正式UI/云端长期记忆/自然语言提交/新网络策略；内部CLI补snapshot/usage入口，观察链用本机真实HTTP/WS/SSE＋确定性模型验证。失败测试覆盖缺口/重放、根子配对、snapshot竞态、慢send/订阅回收、observer/usage故障与主异常、历史校正和重启。

框架核验：当前Starlette会将send里的TimeoutError（OSError子类）改写为ClientDisconnect；云端通用OSError处理在已发送响应头后也不能再返回JSON。SSE因此在发送边界转为`ObservationStreamTimeout`，保留原TimeoutError为cause并记录固定诊断；响应finally释放lease。测试按该明确合同检查类型、原始cause和清理，不放宽成任意异常。

## 11. S06.1b 实际接线与证据

Runner正常链：`open_runner/恢复重装 → bind_observations → SessionService.subscribe_events → observation_encoding.encode_event → RunnerObservations → RunnerClient.observer → 原control WS`。观察队列只持RO1短锁，不做IO；相邻同消息delta合并并保留起止index，未知正文快照明确记捕获缺口。system/隐藏SDK配置/原始供应商响应不进入公开观察DTO。已有ModelUsageCallback继续负责归一化，未另装一套模型统计。

Coordinator正常链：`RunnerControl.receive → CloudObservations._admit → CT1内control.verify → 可选usage.save／独立confirmations → LiveTask/逐thread StreamingMessageProjection → snapshot或ObservationLease`。认证/代际/fence失败在可选IO捕获之外，不能降级为普通用量故障；返回owner loop后重核连接。usage恢复/保存失败有诊断与capture_incomplete，必要journal不依赖其成功。history v3只增加独立usage表；重开未闭合捕获标为不完整，未知计数仍为None。

正式校正使用同一coverage下的完整公开内容指纹或已保存run身份，而非只按message_id过滤：同ID的新修订仍可见，重复的已确认上下文不会复活；窗口外的保留草稿也受确认查询覆盖。确认查询独立于usage写入失败。公开内容指纹在截断前生成，区别于原始SDK正文body_sha256，不包含隐藏SDK元数据。

snapshot通过观察版本前后校验获得一致水位；繁忙时最多重试配置次数并返回明确snapshot_busy。SSE的update携带有界实时窗口且replace=true，消费方替换该窗口；正式窗口沿143a保存，不因实时裁剪而删除。根子按thread/source分开，默认看root，thread_id可读取child；所有消息在正式确认前均为provisional。Last-Event-ID绑定task/thread/instance/display epoch/seq；源观察缺口、换连接或缓存不足均触发resync。初始与重同步快照、update都按encode_sse的最终字节预算计算，含id行与cursor；读事务不会延续到网络发送或等待下一页。

默认ObservationLimits：队列256条/512KiB，单记录32KiB，每批64条，50ms合并投递，文本2048字符；每线程64消息、32线程、64工具、聚合512KiB；64任务缓存、128回放事件/1MiB；全局64/每任务8订阅，心跳10秒、send截止15秒、订阅绝对期限1800秒、快照最多3次/默认10消息、cursor3600秒。值均由CloudSettings显式配置并冻结传Runner；接收端再次核对预算。订阅在异步读后重核额度；淘汰覆盖消息/工具/运行摘要，丢失明确不完整。

HTTP能力为owner `GET /tasks/{task}/snapshot?thread_id=&limit=`、`/usage?after_model=&limit=`与`/stream?thread_id=`（路径前缀/api/cloud/v1）。stream使用Authorization和Last-Event-ID请求头，拒绝URL凭证和重复游标头。CLI为`nyt cloud snapshot TASK --settings CONNECTION [--thread THREAD] [--limit N]`、`nyt cloud usage TASK --settings CONNECTION [--after-model MODEL] [--limit N]`。关闭先封口/唤醒SSE，再排空监听及原控制/模型/DB；退出侧栏或关闭订阅不发送stop给Agent。

证据目录为`build/spec143-s061b-20261102/`。TDD红灯包括队列/接口/CLI、并发订阅、连接替换和升代吊销、可选恢复隔离、正式消息/新修订合并、工具/最终SSE预算与快照不可用缺口。真实本机HTTP/WS/SSE测试在Shell仍执行时观察进度，UI断开后执行继续、重连可见正式历史；真实子Agent验证父工具关系、4次调用的根子统计与正式档案校正。用量通道允许晚于必要任务终局到达，联调用真实根run_finished观察帧同步后再精确断言次数，不用任务完成时间冒充统计已收齐。

测试夹具调整：停服重绑测试补真实新增的observations.aclose接口，并保留原端口重绑/维护断言及新增两次关闭断言；原S06.1a的usage未注册断言迁为真实200/空统计断言。新loopback HTTP探针设置trust_env=false以隔离本机代理，未修改环境变量或真实网络策略。没有修改conftest、降低计数/失败断言、删除测试或加skip。最终全量与构建结果以该目录verification.json为准，进行中的命令不构成通过证据。

S06.1b最终结果：新增26项且旧6576项身份全部保留，完整后端3147＋3455＝6602项、前端69文件568项/TS/Vite、四平台标准wheel/Twine和Mac源码目录外干净安装通过。四包476个Python源文件逐字节匹配当前源码；安全/架构独立复核关闭所报问题，最后快照不可用缺口先红后绿并在最终全量重新验证。详见[verification](../../build/spec143-s061b-20261102/verification.json)与[review](../../build/spec143-s061b-20261102/review.md)。无真实供应商/公司业务/最终Linux容器验收，无新依赖、真实配置、Docker、提交或部署操作。唯一下一片S06.2a，原14单元剩余12项。
