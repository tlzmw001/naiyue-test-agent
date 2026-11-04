# 锁层级与获取约定

## Spec143 S10.1/S10.2 显式更新、维护与离线运维

| 锁／预留 | 当前消费者 | 顺序、IO与恢复 |
| --- | --- | --- |
| UP1 `UpdateStore` per-command OS锁 | create/execute、CLI cancel、status/launch探测；私有jobs/UUID.lock | worker按UP1→UP2取得，FD不可继承；新job登记在UP1下短取UP2核对GC fence，先放UP2再放UP1。CLI GC确认/恢复启用只推进原job意图，launch探测后释放再spawn，子worker不继承父锁。status读取原子JSON并探测是否仍有producer，不靠PID判定完成。 |
| UP2 jobs/build.lock | 同一部署目录的下载/构建/预检/登记/升级备份、完整备份/恢复/GC | 只在独立Updater进程持有，可跨其HTTP/Docker IO。在线更新按UP1→UP2→prepare锁→Owner API，不直接进入Owner DB；Coordinator不回调Updater。离线按UP1→UP2→CO0→registry DB，恢复启用的两个CO0按绝对路径排序且非阻塞；先独占原根并物理确认无task/helper writer，再打开管理DB。不得从CO0/DB反取UP1/UP2，离线释放后才重新启动Coordinator。 |
| registry DB发行/维护CAS | ready/active、全局manifest身份、maintenance、新任务选定snapshot | 同一短事务登记指针与回执；task创建最终事务解析logical template active并固定。新run/start/capture/delete/transfer在真实预留事务检查maintenance，原命令重复先查旧回执。 |
| CT1→registry DB 升级预留 | UpgradeRepository reserve/verified/commit/cancel | 环境占用字段阻止并发start/delete/replace；release:upgrade不进入EnvironmentOperation解析。backend find、Docker raw备份、容器kill/rm/身份复核均在CT1/DB外；提交与取消重查原op/代际/摘要。 |
| MI1 持久纯IO租约 | 输入/产物下载、结果GC | DB事务中maintenance检查与lease登记原子；lease是跨IO预留，不持DB锁跨IO。已经deleting的GC可按原command续接；租约与deleting状态持续计入draining。CO0独占启动且接受新IO前退休旧进程纯IO lease，未决业务command/任务unknown保留。 |

物理备份容器由固定name与instance/task/command/reservation/image身份共同约束；worker中断/超时/取消先确认原容器退出。未确认保留任务预留，不释放为可重启。单个job日志与请求只在UP1下推进，网络等待取消不产生第二个激活。

恢复/GC在CO0下先持久registry格式2，再root格式2，再写guard，旧读者即使拿锁前缓存root1也会被DB格式拒绝。restore先封旧根再开新根；GC deleting跨进程标记阻止Coordinator和新job准入，finalizing只收束标记，不重复物理删除。两个不同work_dir仍由同一CO0/原guard互斥。S11验收脚本仅持独立输出probe.lock，任务线程各写独立journal，不嵌套产品锁或共享HTTP client；不得并发复用同一输出目录。

## Spec143 S06.2a 本机手动委派

| 锁／预留 | 保护对象和消费者 | 顺序与关闭 |
| --- | --- | --- |
| LC0 `ManualCloudTasks.admission` | 同一本机服务的短准入；`submit` 的独立 admit producer | LC0 → 本机会话 `SessionPersistence.history_lock` → 项目 DB。锁内只读私有配置和登记本机记录，不做 SSH/HTTP/上传；输入捕获在此锁外。父删除持 history_lock → DB，禁止 DB/父锁反向进入 LC0。 |
| LC1 `operations/flights/slots`（事件循环预留） | submit/read/view/retry/cancel 的在途 Future、已托管准备和准入额度 | 同 loop 变更集合无 await；slot 在 DB reserve 前占用，准入与 retry 共用容量。请求等待取消须排空独立 admit，不能取消已经持久接管的准备。封口 → 等已受理操作 → 排空 flights → 关 HTTP/SSH，之后才由 Application 关闭 Host/DB。 |
| LC2 `ProfileConnections.opening/connections/borrowed/retired`（同 loop） | 每 profile single-flight 建连、自有 SSH 和 HTTP client 借用 | 建连/握手在 worker，不持 LC0/会话锁/DB。返回客户端前登记借用；确认自有 SSH 已退出可退休并重开，旧 client 等最后借用归还才关闭。不触碰 external SSH；主服务关闭先排空 LC1。 |
| LC4 `ManualCloudTasks.observers`（S06.4，同 loop） | 固定 binding 的有界只读 SSE reader；登记/容量判断无 await，无新增线程锁 | 持 manager/domain operation 生命周期预留，DB读取在worker短事务后借用LC2；不持LC0/DB跨SSE。封口→取消reader→显式关闭并排空上游iterator→归还LC2借用→移除LC4→排空LC1→关闭HTTP/SSH。发送/清理失败保留主异常，关闭观察不发远端stop/delete。 |
| DB 云端关联准入／删除保护 | `LocalTaskRepo.reserve`、`RunUnitOfWork.delete`、项目 `deletion_guard`、离线 restore | 父存在检查和关联插入同一写事务；最终会话删除在其写事务重查。取消须等 producer 收束且确定未派发 task 才解除保护；未知或已受理始终保留。项目在登记 deleting 前预查，最终 registry removal 下仍持项目文件锁复查。离线 restore 持原实例文件锁，保留关联时拒绝整库回退。 |

本片无跨 SQLite/网络长事务，不把本机进程退出解释为远端任务停止；来源冻结前后核对既有 execution authority，缓存字节复用既有 OS 文件锁。

### S06.2b 命令和草稿扩展

| 预留／锁 | 保护对象与顺序 | 释放边界 |
| --- | --- | --- |
| LC0 新消费者 `ManualTaskCommands.submit` | 独立 admit producer 在既有 admission 下分配 command slot、同库固定请求身份；仅本机 DB，不做网络。DB 事务结束后 `_launch`，HTTP waiter 取消不取消已登记派发 | LC0 → DB；不反向获取，不持锁等模型/SSH。主会话删除仍由 retained binding 阻止，无伪造本机 run。 |
| LC3 `commands.slots/flights`（同 loop） | message/start/release 与 stop/recover 分开计数；后者有独立控制名额，普通派发拥塞不耗尽停止能力。键为 project+command，原ID复用；同 binding 未决派发由台账检查，不靠网络长锁 | 持久登记/冻结/派发标志均先于IO；callback 移除 flight。受理回执到达即结束本机派发，不等待整个远端模型。 |
| DB 命令／草稿 CAS | 原用户摘要、完整 owner 请求和receipt；草稿独立 revision。强制释放同时固定所见 cloud revision/generation/active_run，不能用fresh状态重基确认 | 每次短事务；必要历史终局来自owner回执。旧ACK仅清除匹配草稿版本，读取不重复推进未变化的binding revision。 |

LC1 关闭仍先封口/排空在途读和admit，再排空全部创建/命令 flights；任一producer失败须等待其余producer完成后传播主异常，最后关闭连接/Host/DB。操作列表按ID逐条读且只输出摘要，终局台账不再访问远端；GET/重连不调POST或重放模型。

## Spec143 S01/S02/S03/S04/S07.1 控制、输入、Runner与产物

新增B仅使用显式新根，普通本机/A仍沿下方原表。B真实入口为`create_cloud_apps`，SQLite工作在`anyio.to_thread`线程中。

| 编号／锁 | 保护对象与入口 | 获取顺序与边界 |
| --- | --- | --- |
| CO0 `CloudOwner.fd` | `ownership.py`的私有根OS文件锁，运行期独占同一B根，FD不可继承 | 非阻塞取得，第二实例拒绝；资源打开前取得、排空/关闭全部DB后释放。不依赖文件存在判断在线，不与A服务发现锁共用 |
| CT0 `CloudRuntime._requests` | `runtime.py:operation/close`的Condition，封口与在途本地工作线程计数 | 仅短临界区更新计数；wait_for排空时释放锁。先释放CT0再进入CT1/DB，不跨IO持有，不在CT1/DB中反向获取。关闭由外部owner线程执行，不在已登记请求内调用 |
| CG1 `TaskGates._guard` | `task_gates.py:hold`的Lock，注册任务gate的引用计数/移除 | registry存在性查询先结束DB事务，再取得CG1；先释放CG1再等CT1，先释放CT1再回CG1减引用。不允许CG1→CT1/DB嵌套 |
| CT1 每task gate | `TaskGates.hold`；`TaskCredentials.issue/revoke`、runtime的`identity/append_events/history_page/replace_task` | 允许CT1→DB(cloud registry)或DB(本task history)，两库事务逐段且不同时持有；不反向DB→CT1。不跨网络/模型await，代际CAS不冒充后端物理停止确认 |
| CT1 S02 生命周期使用方 | `CloudLifecycle._reserve/_update/finish` | 准入/阶段/终结在worker取得CT1→registry DB；`TaskDirectories.prepare`目录IO在CT1外、CO0独占期的worker中执行，持久操作预留阻止同task第二操作。不持CT1跨SDK await；活动额度检查与任务/回执写入同一DB事务，不用进程内计数替代 |
| CR1 S06.3a资源计量缓存 | `CloudResources._lock`，`disk/ensure/capacity`保护逻辑用量缓存及一次扫描发布 | worker可独立取得，或环境/产物准入CT1→CR1；CR1内只有FD锚定的有界元数据扫描，不取得CT1/DB，不跨网络。capacity先结束registry短事务才取CR1；空闲磁盘statvfs在CR1释放后读取。首次/过期扫描按检查点核对预算，不能强制中断内核文件系统阻塞，可暂时阻塞同task准入；CR1无反向调用。server资源维护先按原CP1排空已派发命令，stopping逐task阻止后续派发，再退出维护，SDK/DB仍由原owner关闭 |
| TI1 S06.3b任务IO计数 | `TaskIO.condition/active`，同步hold与异步use；环境_execute、产物prepare/upload/commit/capture、download.open | entry在worker先CT1→registry读准入，结束DB事务，再CT1→TI1加计数；两锁均释放后才实际IO/SDK await。finally仅TI1减计数/唤醒，不反取CT1/DB。异步entry通过CW0并drain取消，防worker已加计数但调用方丢失；drain在CT1/DB外取得TI1，wait_for释放Condition等待，超时保留deleting。普通历史/get短读只用CT1，无额外跨IO锁 |
| CT1/CP6 S06.3b删除状态与producer | `CloudDeletions.reserve/stage`、`CommandProducers`与DeletionRepository | owner loop持producer身份，worker CT1→registry短事务固定预览/命令并封deleting；不持CT1/DB跨网络或TI1等待。先按task关闭下载CD1/模型流，再TI1排空旧环境/产物IO，证明物理停止后，CT1关/摘history缓存；固定FD递归清理在全部锁外。最后CT1→registry同事务墓碑/回执/索引/旧正文脱敏。backend关闭先封口并排空delete producer，再关原control/models/artifacts/lifecycle和SDK；本机核销与父删除共用原ProjectStore事务，成功回执后不再fresh读已可删除的父数据 |
| CP1 S02 producer预留（非线程锁） | `CloudLifecycle.producers`（`CommandProducers.flights/closing`），只在同一owner事件循环调用 | `CommandProducers.submit`至注册task无await，原command ID合并等待；HTTP取消不取消producer。对账producer开始时重读持久回执。关闭封口→shield排空全部producer→清空runtime.lifecycle→关闭SDK transport→CT0排空/DB/CO0；预留期间可await网络，不能作为同步锁在worker访问 |
| CF2 模板准备OS锁 | `prepare_template`的输出父目录`.template-prepare.lock` | 独立受信CLI持有；非阻塞取得、构建/完整预检/私有回执落盘后释放。与Coordinator/CT0/CT1/DB无调用嵌套；不继承到Docker子进程，不持有任何任务资源 |
| CP2 输入操作预留（非线程锁） | `CloudInputs.busy/jobs/uploads/closing`，同一owner loop；`perform`及raw `receive` | commit/cancel producer持transfer键，再持input键；HTTP只shield等待，取消不释放实际producer的预留。不同transfer同I提交/取消互斥，不等锁而返回busy。raw流取消须排空正在执行的本地write/fsync/close后释放transfer键，不持SQLite事务跨await。shutdown先独立排空inputs，再排空lifecycle，任一失败仍关闭后续资源 |
| CP2 S03.2 扩展 | 同一transfer的段PUT/status恢复/TTL取消，以及input键的commit对账 | 热路径先核身份；IO fsync/close后才CAS写段/offset/文件身份。状态GET如需恢复也由原perform producer持transfer→input预留，取消等待者不释放producer；活动PUT只读已确认offset。维护只在owner loop读busy并在worker重核updated_at/状态后原cancel；不在worker修改busy。Server维护协程收到stopping后结束，监听/维护排空后才关闭inputs/DB |
| CF4 本地输入cache OS锁 | cloud_files.cache的私有cache.lock格式/容量及inputs/I发布 | 非阻塞，持有到本次捕获/必要回执/真实额度核对/rename发布完成；同cache串行，不跨SQLite/Coordinator锁，不继承给进程。来源选择在拿cache锁前完成，capture仅做本地事实复核。Windows锁字节初始化与签名分开处理，空/半签名只在受限私有初始化根恢复 |
| CW0 本地IO工作（非线程锁） | `application/cloud/work.py:cloud_work` | worker登记CT0计数后释放CT0，才访问文件/DB。普通元数据请求保留S01取消立即返回语义；CP2生产者独立持预留，raw IO用drain_cancel等待真实worker结束，包括重复取消。失败可观察，必要状态不由HTTP等待者生死决定 |
| CF3 输入捕获上传OS锁 | `InputClient.upload`的私有capture/upload.lock | 独立客户端非阻塞取得，持有到本次网络交互结束；锁FD不继承。instance绑定的上传command_id先fsync日志/父目录再发POST，ACK丢失后原ID重试。与远端CO0/CT1/DB无嵌套，不锁用户源目录 |
| CF1 B数据库OS锁 | `Database.open_cloud → initialize_cloud`，每库`.lock` | 在CO0所有权内打开；history可在对应CT1内首次打开，非阻塞文件锁固定已验证管理目录。Database关闭后释放，无任务路径/其他库互相获取 |
| DB(cloud registry/history) | 复用`Database.lock/transaction/close`，各库独立RLock | 叶锁，不同时持有两库事务，不调用应用回调；历史读按条数/字节预算短事务，响应/SSE不占事务。S06.1a在CT1内先分笔读取registry命令来源，再单history事务提交台账/分段/版本投影/ACK；投影仅做纯数据校验，不反调registry或Runner。分页/目录/详情带SQL进度与读截止检查，返回分离数据后才签cursor/构造HTTP响应；不持DB锁等待客户端或下一页 |
| CP3 Runner连接/运行/控制producer预留（非线程锁） | `RunnerControl.attaching`、`RunnerRuns.producers`、`RunnerActionCommands.producers`，只在Coordinator owner loop写 | 同task attach在首次DB await前独占登记真实producer；调用方取消只取消shield等待，不释放仍在bind/发布的producer。运行/控制reserve、finish、activity在worker持CT1→registry或history DB短事务；控制回执与关联旧run回执在同一registry事务结算。关闭先封口，排空attach/run/control producer后关闭连接；不得用worker访问loop字典决定准入，命令显式传当前connection ID及凭证再核验 |
| CS1 每连接异步发送门 | Coordinator `Connection.lock`、Runner `RunnerClient.send_lock`为不同进程/实例 | 仅串行本连接发送。Coordinator取得发送门后才worker重核凭证/fence和最终帧预算，再await发送；不持CT1/DB跨网络。连接替换及过期后的排队帧不能靠排队前校验放行 |
| CP4 产物占用与producer（非线程锁） | `CloudArtifacts.producers/busy/uploads/downloads/closing`仅在owner loop写；reserve/unconfirmed/finish在worker取得CT1 | Capture在任务DB中持久artifact_operation_id，阻止run/环境准入；commit按task/artifact预留，HTTP只shield等待。PUT只在接收前鉴权取得固定长度/hash/超时租约，结束重核generation/原操作；新commit仍用当前token。关闭control先封口并排空capture/commit producer，再断WS；artifacts关闭排空PUT和有超时的响应lease后才CT0/关库。worker不读写loop预留集合 |
| CD1 下载lease异步门 | `ArtifactDownload.lock`，单响应FD读与关闭 | 取得CD1后调用CW0并排空实际read/close；不从CT1/DB反取CD1。文件hash/身份在流末核对，响应超时/取消先drain正在读的worker再关同一FD；登记lease至finished期间阻止runtime提前关库 |
| RP2 Runner产物占用（非线程锁） | `RunnerArtifacts.active_task`与Kernel.phase、私有capture journal | 根run和capture在持久admit的首次await前预留phase=blocked；同loop互斥，局部SQL事务短持DB。固定spool fsync/rename→journal captured→上传commit ACK→journal available/恢复phase→fenced finished；任何必要保存/未知边界保持占用，不重扫源文件；关闭排空本地capture再关闭Session/DB |
| RS1 Runner私有DB/状态 | 复用`open_storage`的OS文件锁、ProjectStore DB RLock及独立saver连接；固定一个task | `RunUnitOfWork.finish_run`同一DB事务内先按旧归档判定历史差分并写journal，再写chat/状态，任一失败全部回滚；journal不反调应用锁。checkpoint仍为另一连接，不声称与该事务原子。Coordinator不获取此DB锁或打开此库 |
| RP1 Runner根运行占用 | `RunnerKernel.phase/active_task`＋RunnerJournal持久command；仅Runner loop发起 | 在工具副作用前先持久命令。断线不取消active_task；关闭请求现有SessionService停止并等待必要保存/后台资源收束。静止检查使用既有background deletion_guard预留（其线程锁在yield前释放）再读会话状态，不持DB/线程锁await |
| RP3 Runner停止/恢复占用（非线程锁） | `RunnerActions.task/command_id/closing`与Kernel.phase；固定一个task，只有Runner loop注册 | 持久准入首次await前先预留phase；WS接收取消须等真实准入及producer注册排空，原ID合并，不取消独立控制任务。根run/产物捕获/控制互斥；控制中仅stop可等待已登记根运行，根不反向等待控制。stop沿A2/S3/C1请求，释放线程锁后等待根子驱动及B1后台worker；recover封口旧SessionService后才重装配并进入S1/S2/DB/saver，DB与saver不叠持。关闭先排空控制，再产物/后台/DB；静止报告受last_run_id与持久操作/unknown阻塞保护 |
| CP5 S05模型请求租约（非线程锁） | `ModelRelay.leases/closing/client`只在Coordinator owner loop操作；`CloudModels.admit/check`在worker持CT1 | admit在CT1→registry短事务核对任务凭证/用途/代际/profile/模型后产生不可变ModelGrant；释放CT1，再在loop无await预留并发额度，才读有界body。出站前worker再核grant的task/gen/profile/可执行状态，不重验已轮换token；新请求仍完整鉴权。不得从worker访问leases，不持CT1/DB跨HTTP。成功流及有界错误体的绝对deadline覆盖下游send，Response finally排空upstream.close并释放额度。生产服务先停监听并按请求绝对deadline有界排空，再close control、模型客户端、backend/runtime DB；ModelRelay.aclose另可封口/取消并排空活跃request owner/lease。同步runtime.close不代替async资源关闭，不承诺服务SIGTERM瞬时完成 |
| RP4 S05模型当前凭证（无锁不可变引用） | `RunnerModels.bootstrap`的替换，只由Runner控制loop更新；请求hook每次取同一bootstrap引用 | 控制renewed或RO bootstrap身份核对后替换token/期限，SDK重试也在send前取当前值。同步SDK请求hook只读一次引用，不访问SQLite/loop预留。ModelCatalog/with_thinking副本共享同一受控HTTP客户端及hook；不在hook取会话/模型锁或调用控制面。固定内部endpoint，不走工具代理环境；关闭在根/子/后台收束之后，次要关闭失败不覆盖主异常 |
| RO1 S06.1b Runner观察队列短锁 | `RunnerObservations._lock`保护有界deque/字节/序号/丢失计数 | 生产者沿既有运行publisher投递后进入RO1；只做有限入队/相邻delta合并或drain，不调用会话、DB、网络或反向publisher。订阅解除在RO1外；发送协程释放RO1才取CS1，观察send有绝对截止，网络失败不取消Agent根循环。恢复重装SessionService后重绑，Runner关闭始终解除订阅 |
| CO1 S06.1b实时投影/订阅（owner-loop预留，非线程锁） | `CloudObservations.states/leases`、`LiveTask`与回放环 | 仅owner loop访问；worker在CT1→registry验证fence/run，释放后才单history库保存可选usage/查询确认内容。IO前后均核connection，旧连接不能写新视图；worker不访问loop集合。快照按观察版本有限重读；订阅在await快照后重核额度，再无await登记。SSE等待/发送不持CT1/DB，关闭先封口/唤醒leases，再排空监听与原控制/模型/数据库 |

## S09旧A锁退出

2026-11-03：A环境/传输/准备/操作/输入/结果/交付锁与准备计时脚本已退出；记录见[A阶段历史锁说明](history/lock-hierarchy-a.md)。当前本机运行与B任务遵循下方仍有消费者的锁表；不再获取A管理预留或等待环境发送的门闩。

核对日期：2026-11-03。当前适用主工作区S09正式接线实现；旧A专用锁已移至历史记录，普通本机及B当前锁继续维护。

本表是修改并发代码时的维护合同，记录保护对象、允许获取的入口和当前嵌套关系；不宣称已经形式化证明无死锁，也不新增运行时锁检查。核对范围是 `naiyue_test_agent/` 生产代码中的显式锁、条件变量、信号量、文件占用锁，以及应用直接使用的 saver 锁。测试里的同步对象、SDK/SQLite/日志库内部锁不逐个列入。

## 怎么读顺序

- `A → B`：持有 A 时允许获取 B，禁止新增反向获取。通过被调用函数获取 B 也算嵌套。
- `A → {B, C}`：两条独立关系，不表示可以同时持有 B 和 C。没有列出的组合，不视为已经获准的嵌套；新增时先追调用链，再更新本表。
- “先释放再……”表示两个阶段，不得画成嵌套。`RLock` 只允许同一线程重入同一实例，不允许颠倒不同锁的顺序；普通 `Lock` 和 `asyncio.Lock` 不可重入。
- “叶锁”表示当前没有继续获取其他项目锁。表中的入口可以经公开业务方法间接调用；跨模块直接操作私有锁，只限已列出的现有协作入口。
- `threading.Lock/RLock` 临界区不得新增 `await`、等待工作线程结束或跨循环同步等待。已有归档、记忆查询在锁内做同步 IO 的事实见下表；本表不把它们标成已优化。
- 同一类的不同实例不是同一把锁。除明确列出的项目→个人数据库和 CLI 同线程重入路径外，不新增跨会话、跨项目的同类锁嵌套，也不一次获取多个项目的 scope gate。

## 锁层级表

下表中的源文件链接同时作为获取入口的查证位置。`DB(project)`、`DB(personal)`、`DB(registry)` 是 `Database.lock` 的不同实例。

| 编号／锁 | 类型与保护对象 | 允许在哪里获取 | 和其他锁的顺序／持有边界 |
| --- | --- | --- | --- |
| G1 `ScopeServices._scopes.serial(scope_id)` | 异步锁，每个 Web scope 的服务创建、关闭互斥 | [service/scopes.py](../naiyue_test_agent/service/scopes.py) 的 `get/_open/close_project`，通过 [ScopeTasks](../naiyue_test_agent/application/lifetime.py) | `G1 → G4`；创建时可间接进入项目服务。不同 scope 各自独立，不用它串行所有项目。 |
| G2 `ScopeTasks.serial(('selection_root', root))` | 异步锁，旧入口按规范化根目录协调准备 | [ProjectSelection.prepare](../naiyue_test_agent/application/projects/selection.py) | `G2 → G3 → G4`，缺少某个 key 时跳过该层；期间可进入 P1 和旧 workspace 文件锁 F2。 |
| G3 `ScopeTasks.serial(('selection_store', path))` | 异步锁，旧数据库路径的准备／导入互斥 | 同上；只在已确定旧库路径后获取 | `G3 → G4`；允许进入 P1／F3。不得从 G4 反向准备旧目录。 |
| G4 `HostManager._scopes.serial(('host', id))`／`('temporary', id)` | 异步锁，同一 project/chats/临时 host 的打开、列历史、关闭、移除、定位互斥 | [HostManager](../naiyue_test_agent/application/projects/host_manager.py) 的 `_scopes.open/serial`；`ProjectSelection._prepare` 的最终检查 | `G4 → {P1, G5, A1}`；host 打开／关闭期间也会进入本 host 的会话、资源和数据库入口。同 key 不可重入；跨项目不叠持 G4。冷打开共用 task，不再持全局打开锁。 |
| G5 `DeferredModels._lock` | 异步锁，模型目录首次构造／准备的单次发布 | [DeferredModels.get](../naiyue_test_agent/application/service_application.py) | 可在 G4 内获取；模型准备可涉及自身的模型／HTTP 局部锁。不得在加载回调中反向打开 host 或获取 G4。 |
| P1 `ProjectService._lock` | `RLock`，项目注册服务的懒加载、操作预留／恢复、关闭 | [ProjectService](../naiyue_test_agent/application/projects/service.py) 的 list/get/add/create/rename/relocate/remove/import/reconcile/close | `P1 → DB(registry)`；项目建库、导入、移除还允许 `P1 → F3`。保留同线程 `get/reconcile` 重入；不在持有 P1 时反向获取 G1—G4。运行期 `RegisteredExecution` 直接读取 registry.execution_facts，只取得 DB(registry)，不反取 P1。 |
| A1 `RunAdmission.lock` | `RLock`，全应用根运行的容量检查与准入提交 | [SessionService._admit](../naiyue_test_agent/application/sessions/session_service.py)；[RunAdmission.available/release](../naiyue_test_agent/application/sessions/run_admission.py)；available要求调用者已持有A1，release只在生命周期确认排空后调用 | `A1 → A2 → S3`；统计时逐个取得 A2，释放 A2 后读取对应 actor 的 S3，不同时锁多个 service。目录／注册 revision 验证在取得 A1 前完成。 |
| A2 `SessionService._lock` | `Lock`，已加载 actor、子 Agent 角色、组装／关闭状态的注册表 | [SessionService](../naiyue_test_agent/application/sessions/session_service.py) 的注册／查找／快照／准入／删除 guard；`RunAdmission.available` | `A2 → {S3, B1, E1, M1}`：分别用于读 actor、检查清理、取订阅快照、冻结模型错误映射。不得反向获取 A1 或 S2。建图、创建会话的必要 SQL、运行及关闭等待在释放 A2 后执行。 |
| S1 `SessionPersistence._history_locks[session_id]` | 异步锁，同会话准备、收尾、恢复、删除和记忆证据保存的协调 | [SessionPersistence](../naiyue_test_agent/application/sessions/session_persistence.py) 的 `aprepare/afinish/arecover/adelete`；[MemoryAPI.query](../naiyue_test_agent/application/memory/api.py) 的写入分支 | `S1 → {S2, D1, DB, D2}`。这是共同外层门闩，各分支分段获取内层锁；SQL 事务与 saver 调用不交叉持有。`arestore` 自己只用 S2 预留，`aprepare` 可在 S1 内调用它。 |
| S2 `SessionPersistence._lock` | `Lock`，`_active/_recovering/_blocked` 的预留与短状态更新 | [SessionPersistence](../naiyue_test_agent/application/sessions/session_persistence.py)；[ProjectHost._check_idle](../naiyue_test_agent/application/projects/host.py) 只读取协调状态 | `S2 → A2 → S3`，用于 `_delete/_install_session` 等配对检查。预留后释放 S2 再做 SQL、saver 或 `await`；`start` 的 mark_running、afinish 的归档／裁剪都不持 S2 做 IO。 |
| S3 `SessionRuntime.lock` | [SessionRuntime](../naiyue_test_agent/application/sessions/session_state.py) 的 `Lock`，同一 actor 的 SessionState、ActiveRun、控制队列、审批、存活子实例 | [AgentSession](../naiyue_test_agent/application/sessions/agent_session.py)、[RunLifecycle](../naiyue_test_agent/application/sessions/session_execution.py)、[ControlQueue](../naiyue_test_agent/application/sessions/session_controls.py)、[ApprovalBook](../naiyue_test_agent/application/sessions/session_approval.py)、[SubagentRegistry](../naiyue_test_agent/application/subagents/session_subagents.py)、[subagent_records.admit](../naiyue_test_agent/application/subagents/subagent_records.py) | `S3 → {C1, E2, E4}`，只做停止标记、控制记录、turn 保存／恢复。四个协作者共用这一个实例；调用者已持锁时使用不再次获取 S3 的内部 guard。不得反向获取 A2／S2；不得执行模型／工具／SQL、控制事件 flush 或等待子实例。 |
| S4 `AgentSession._model_selection_lock` | `Lock`，模型／思考档位的落库与内存提交顺序 | [AgentSession.select_model/select_thinking](../naiyue_test_agent/application/sessions/agent_session.py) | `S4 → {DB(project), S3}`，SQL 返回释放 DB 后才修改 S3；思考档位先在 S3 下核对，释放后落库，再取 S3 提交。 |
| S5 `AgentSession._tool_selection_lock` | `Lock`，工具选择的落库与内存提交顺序 | [AgentSession.set_active_tools](../naiyue_test_agent/application/sessions/agent_session.py) | `S5 → {DB(project), S3}`，两把内层锁分段获取；registry 选择校验在 S5 前。S4/S5/S6 不互相嵌套。 |
| S6 `AgentSession._permission_selection_lock` | `Lock`，权限模式的落库与内存提交顺序 | [AgentSession.set_permission_mode](../naiyue_test_agent/application/sessions/agent_session.py) | `S6 → {DB(project), S3}`，两把内层锁分段获取；不从 S3 回取选择锁。 |
| B1 `BackgroundProcessManager._lock` | `RLock`，后台进程记录、调用去重、终局通知、删除／输出清理标记 | [background_tasks.py](../naiyue_test_agent/application/sessions/background_tasks.py) 的注册、短状态、查询、stop、stop_session、guard、close | A2 可获取 B1；注册的目录、登记与环境核验先在线程完成（不持 B1），返回循环后在 B1 下复核关闭／删除／清理与去重，冻结 EnvironmentRef；读 `control.requested` 只查询 Event，不取得 C1 mutex。`stop/stop_session/aclose` 同线程重入B1标记停止；释放后等待worker/核对输出关闭，stop_session不封口整个管理器。不得在B1内等待进程、磁盘或取得A2/S2。 |
| D1 `PersonalMemory.lock`，亦为 `MemoryAccess._lock` | `RLock`，全应用共享个人库生命周期及各 MemoryAccess 查询／修改 | [PersonalMemory.repository/close](../naiyue_test_agent/application/memory/personal_memory.py)、[MemoryAccess.query/close](../naiyue_test_agent/application/memory/memory_access.py) | `D1 → DB(project) → DB(personal)`；个人写入先固定项目证据，再打开个人写事务。两个别名是同一把锁，允许同线程重入；当前锁内包含同步查询与 IO，跨项目也会竞争，本次未优化。 |
| DB `Database.lock`（项目／chats／RAM／个人／注册各实例） | `RLock`，单连接、显式事务、读写和 close 的互斥 | [Database.transaction/read_rows/close](../naiyue_test_agent/storage/database.py)；各 Repo／[RunUnitOfWork](../naiyue_test_agent/storage/unit_of_work.py) 通过 transaction 进入 | 通常为叶锁；唯一明确的双库事务是 [MemoryRepo.edit_with_sources](../naiyue_test_agent/storage/memory.py) 的 `DB(project) → DB(personal)`。同库复用一个事务；即使 RLock 可重入，`transaction` 仍拒绝嵌套事务。禁止 DB 锁内反向调用应用、获取 D1 或进入 saver。 |
| D2 `AsyncSqliteSaver.lock` | 框架 `asyncio.Lock`，第二条 SQLite 连接的 setup/read/write/history 游标 | [open_storage](../naiyue_test_agent/storage/resources.py) 创建；图的状态／运行 API，以及 [裁剪](../naiyue_test_agent/storage/checkpoint_retention.py) 的官方 `checkpointer.aget_tuple` | S1 可包住 saver 调用；应用不直接 acquire D2，不从 DB 临界区进入 saver。框架 `alist` 在 `yield` 期间仍持 D2，history 迭代体不得重新调用同 saver 或开启依赖它结束的数据库写事务。 |
| J1 `MemoryJobs._context_lock` | `Lock`，`_eligible/_used` 跨线程目录使用标记 | [MemoryJobs.prepare/directory_attempt/after_finish](../naiyue_test_agent/application/memory/memory_jobs.py) | 叶锁；准备数据／保存 marker 在锁外。它不保护整个 job 注册表，后者由所属事件循环管理。 |
| J2 `MemoryJobs._slots` | 异步信号量，后台记忆任务并发额度 | [memory_job_runner._batch](../naiyue_test_agent/application/memory/memory_job_runner.py) 中的批次执行 | 可在额度内执行模型、记忆读写、C1 和观测入口；不持 J1 排队，也不递归请求同一额度。它是并发限额，不是数据库互斥锁。 |
| T1 `AsyncBatchPlan.condition`（含其内部异步锁） | 条件变量，串行工具批次的 position/failed 与唤醒 | [AsyncBatchPlan.execute](../naiyue_test_agent/agent_runtime/tool_execution/async_batch.py) 的顺序分支 | 叶锁；`wait_for` 等待时释放条件锁，轮到自己后也先释放，再 `await handler()`。不得持 T1 执行工具。 |
| T2 `AsyncBatchPlan.semaphore` | 异步信号量，并行工具批次的数量上限 | 同上，并行分支 | `T2 →` 具体工具的权限／MCP／文件／进程／记忆局部锁与额度；T1/T2 是替代分支，不嵌套。同批内部不得在 handler 中递归等待自己的额度。 |
| T3 `MCPSessionResources._refresh_lock` | 异步锁，同一 host 的服务器配置／工具目录刷新发布 | [refresh_mcp](../naiyue_test_agent/mcp_client/refresh.py) | `T3 → T5`（发布 ToolRegistry）。候选连接探测不经 T4；不在 T5 下回调刷新。锁内可以等待候选连接，但不是所有 MCP 调用的全局互斥。 |
| T4 `MCPSessionResources._slots[server_id]` | 异步信号量，每服务器借用并发数；构造／刷新新增服务器时创建 | [MCPSessionResources.borrow](../naiyue_test_agent/mcp_client/resources.py)；[refresh.py](../naiyue_test_agent/mcp_client/refresh.py) 只创建新增槽 | 保持至借用及必要收尾完成；不在该槽内调用 refresh 或再借同服务器。MCP owner 结构由事件循环管理，不额外假设有一把连接 mutex。 |
| T5 `ToolRegistry._lock` | `Lock`，工具定义注册、MCP 原子发布、名称快照／选择 | [ToolRegistry](../naiyue_test_agent/tools/registry.py) 的 register/get/publish/select/names | 叶锁；规则校验不执行工具／审批／MCP IO。工具可用性判断在 `select` 返回、释放锁后执行。 |
| T6 `OneUseApprovalGrants._lock` | `Lock`，精确审批授权的一次性消费 | [OneUseApprovalGrants.consume](../naiyue_test_agent/tools/permissions.py) | 叶锁；校验 subject 与删除在同一临界区；释放后执行工具，不包住工具副作用。 |
| T7 `tools.write._active_targets_lock` | 模块 `Lock`，write/edit 共用的规范化绝对目标忙碌集合 | [write._reserve_target](../naiyue_test_agent/tools/write.py)，文件后端复用该入口 | 叶锁；只检查／增删集合，`yield` 前释放；保留目标预留期间执行文件 IO，不等于持有 T7。不同目标独立，重复目标立即报忙。预留不能阻止任务直接写文件，提交仍需FD/身份/内容检查。 |
| O1 `ProcessCapture._lock` | `RLock`，一个进程的输出累积、前后台切换、capture 收尾快照 | [ProcessCapture.record/promote/finish/snapshot](../naiyue_test_agent/tools/process_execution.py) | `O1 → O2 → O3`。输出 worker 可获取；不在内部回调 B1、会话或 Web 控制。 |
| O2 `ShellOutputArchive._lock` | `Lock`，一个归档的 prefix/handles/paths/bytes/关闭事实 | [ShellOutputArchive](../naiyue_test_agent/tools/output.py) 的 record/promote/activate/finish/snapshot/available/mark_incomplete 等 | `O2 → O3`；`_activate/_write` 为已持 O2 的内部入口，不重复获取 O2。当前可能持锁读写磁盘。 |
| O3 `tools.output._GLOBAL_LOCK` | 模块 `Lock`，当前所有 Shell 归档目录校验、文件创建／写入与预览／清理 | [preview_shell_outputs/clear_shell_outputs](../naiyue_test_agent/tools/output.py)、`ShellOutputArchive._write/available` | 叶锁；禁止 `O3 → O2/O1/B1`。当前是进程级全局锁，跨项目也会竞争；未实现按归档根拆分。 |
| C1 `RunControl._lock` | `Lock`，停止原因及正在等待的模型 task 集合 | [RunControl](../naiyue_test_agent/agent_runtime/run_control.py) 的 mark_requested/request/await_model | `S3 → C1` 仅作短标记；request 先释放 C1 再通知事件循环取消 task，await_model 释放后 await。`signal` 为 Event，其内部锁由标准库管理。 |
| E1 `ApplicationEventSubscriptions._lock` | `Lock`，订阅注册／退订以及每个 publisher 的 handler 快照 | [ApplicationEventSubscriptions](../naiyue_test_agent/application/observability/run_events.py) | `A2 → E1`；叶锁。构造完快照就释放，不持 E1 执行 handler。 |
| E2 `ControlObserver._record_lock` | `Lock`，控制记录、队列对应关系、outbox | [ControlObserver](../naiyue_test_agent/agent_runtime/control_observation.py) 的记录／选择／close，及 flush 取队列 | `S3 → E2`；`E3 → E2`。叶锁，禁止在 E2 内 flush、publish 或取得 S3。 |
| E3 `ControlObserver._send_lock` | `Lock`，控制事件 FIFO 的单发送者 | 同上，`flush` | `E3 → {E2, E8}`，取一条后释放 E2，再获取 E8 发布。必须在释放 S3 后调用；handler 不得重新进入同一 flush。 |
| E4 `TurnObserver._lock` | `RLock`，turn 的状态、配对结果与暂停／恢复快照 | [TurnObserver](../naiyue_test_agent/agent_runtime/turn_observation.py) 的状态方法 | `S3 → E4` 的 snapshot/restore 不发布；正常观测 `E4 → E8`。允许内部方法同线程重入；handler 不得反向查询／修改 turn。 |
| E5 `ContextOperationObserver._lock` | `RLock`，压缩／上下文恢复记录、指标、outbox | [ContextOperationObserver](../naiyue_test_agent/agent_runtime/context_operation_observation.py) 的记录方法与 flush 取队列 | `E6 → E5`；记录完成先释放 E5，再 flush。E5 是叶锁，禁止 E5 内获取 E6 或 publish。 |
| E6 `ContextOperationObserver._send_lock` | `Lock`，压缩／恢复观测的单发送者 | 同上，`flush` | `E6 → {E5, E8}`；先释放 E5 再发布，不允许 handler 同步重入 flush。 |
| E7 `ToolProgressObserver._lock` | `RLock`，工具批次／调用的开始、进度和结束状态 | [ToolProgressObserver](../naiyue_test_agent/agent_runtime/tool_execution/observation.py) 的 begin_dispatch/accept_custom/finish | `E7 → E8`，当前 accept_custom 在锁内 publish；不得从订阅者回调同一 observer。 |
| E8 `RunEventPublisher._lock` | `RLock`，同一根 run 的事件序号、pending FIFO、handler 失败隔离；子 publisher 共用根实例 | [RunEventPublisher._publish/_deliver](../naiyue_test_agent/agent_runtime/events.py) | **持 E8 同步执行 handler**：`E8 → {E9, E10, E11, W3, L1}`。内置 handler 只缓冲／投影／显示。禁止从 handler 进入应用准入、会话控制／查询、其他上游 observer 或等待运行结束。 |
| E9 `ModelUsagePersistence._lock` | `Lock`，用量事件队列、字节数、pending／降级事实 | [ModelUsagePersistence](../naiyue_test_agent/application/observability/model_usage_persistence.py) 的 accept/drain/flush/close | `E8 → E9`；叶锁。drain、aflush_run 均先释放 E9，再做数据库 IO 或 await。退订不持 E9。 |
| E10 `DiagnosticPersistence._lock` | `Lock`，诊断队列、预算和每 run 的收尾／降级事实 | [DiagnosticPersistence](../naiyue_test_agent/application/observability/diagnostic_persistence.py) 的 accept/drain/close | `E8 → E10`；叶锁。SQL 与 await 在锁外，close 先退订再获取 E10。 |
| E11 `AsyncEventStream._lock` | `Lock`，事件缓冲、溢出、关闭和唤醒合并标记 | [AsyncEventStream](../naiyue_test_agent/application/observability/async_event_stream.py) | `E8 → E11`；叶锁。close 先退订再持 E11 清队列；读取释放后等 Event，通知只向循环投递唤醒。 |
| M1 `ModelCatalog._error_lock` | `Lock`，模型错误分类器配置及 run 的冻结快照 | [ModelCatalog](../naiyue_test_agent/model_access/__init__.py) 的 configure_error_classifiers/describe_failure/failure_describer | `A2 → M1`；叶锁。用户分类器执行在锁外，不持锁调用模型或应用。 |
| M2 `ModelUsageCallback._lock` | `RLock`，活跃模型调用、task、HTTP 观测引用 | [ModelUsageCallback](../naiyue_test_agent/agent_runtime/model_usage_observation.py) 的 on_chat_model_start/_finish | 构造／绑定观测时可 `M2 → {M3, M4}`；结束时取走记录并释放 M2，再 close HTTP 观测和 publish。 |
| M3 `_ClientToken.lock` | `RLock`，单 HTTP client 的弱引用 call 集合与 closed 状态 | [http_observation.py](../naiyue_test_agent/model_access/http_observation.py) 的 add/remove/close | `M4 → M3` 可发生于 close 内 release；close 先释放 M3 再通知各 call 关闭。禁止 `M3 → M4/E8`。 |
| M4 `HttpCallObservation._lock` | `RLock`，一次模型调用的 pending HTTP attempts、关闭和引用释放 | 同上，begin/response/close/release/fail | `M4 → {E8, M3}`；begin/response/close 可持锁发送观测，close→release 同线程重入。禁止订阅者回调 HTTP 观测；独立 release 会先释放 M4 再 remove token，但外层 close 仍持 M4。 |
| W1 `RunDelivery.start_lock` | 异步锁，一个 WebService 的启动、恢复、删除及启动封口 | [RunDelivery._launch/seal](../naiyue_test_agent/web/run_delivery.py)、[WebService.delete_session/recover](../naiyue_test_agent/web/service.py) | `W1 → S1`（恢复／删除）及应用短控制入口；seed_data 可取得 W3。producer 被注册后释放 W1，再等待 started future，不持 W1 等整个 run。不和 W2 叠持。 |
| W2 `RunDelivery.control_lock` | 异步锁，控制请求幂等 receipt 的检查与提交 | [RunDelivery.control](../naiyue_test_agent/web/run_delivery.py) | `W2 → A2 → S3`（调用控制入口），返回后才另取 W3 更新投影；不得从 W3 回调控制入口／获取 W1/W2。 |
| W3 `DeliveredRun.lock` | `Lock`，单 Web run 的投影、事件窗、工具／子实例、终局与快照 | [DeliveredRun](../naiyue_test_agent/web/delivered_run.py)、RunDelivery producer/control、WebService 快照入口 | `E8 → W3`，W1/W2 也可短取 W3。叶锁，observer 只投影并投递唤醒；stream/view 释放后才 await/yield。on_started 通过 loop 投递，不能持 W3 同步启动应用操作。 |
| L1 `CliEventDisplay._lock` | `RLock`，一次 CLI 展示台账及写终端顺序 | [CliEventDisplay](../naiyue_test_agent/cli/cli_display.py) 的显示／事件／收尾方法 | `E8 → L1`；内部 write 重入。同线程子显示有“父 L1 → 子 L1 → 父 L1 重入”的固定路径；writer 只写展示，不回调应用或取得 publisher。不得把此重入例外推广为跨线程交叉获取不同 display。 |
| L2 `cli.chat.main` 的 `pending_lock` | 局部 `Lock`，工作线程提交成功／失败后的 pending_input 更新 | [cli/chat.py](../naiyue_test_agent/cli/chat.py) 的 start_run.execute | 当前是叶锁；仅保护这里已有的线程端列表变更，不宣称所有列表读取都在该锁下。锁内不 emit 或调用应用。 |
| F1 `service/owner.lock` | OS 文件排他锁，一个 data_dir 的服务所有权 | [ServiceOwner.__enter__/__exit__、service_running](../naiyue_test_agent/service/ownership.py) | 服务启动先取得，服务资源全部关闭后释放；probe 非阻塞，成功后立即释放。它是生命周期所有权，不是每个请求重新获取的 mutex；运行回调不得重取。 |
| F2 `<root>/.naiyue/workspace.lock` | OS 文件锁，旧 marker 创建／复制工作区身份处理 | [prepare_workspace](../naiyue_test_agent/workspaces.py) 的 create 分支 | `F2 → F3` 的 `_read_binding`；F2 当前 blocking=True，在 blocking worker 执行，不放到事件循环直接等待。非旧目录准备不获取 F2。 |
| F3 `<database>.lock`（项目／chats 库、`projects.sqlite` 注册库） | OS 文件排他锁，禁止第二 owner／还原与已打开库争用 | [Database.__init__](../naiyue_test_agent/storage/database.py)、[registry_database](../naiyue_test_agent/storage/registry_database.py)、[locked_project_reader](../naiyue_test_agent/storage/project_databases.py)、[workspaces._read_binding](../naiyue_test_agent/workspaces.py)、[restore_backup](../naiyue_test_agent/storage/restore.py) | 均为非阻塞取得；常规 owner 持有至关库，冷读／维护按自身 scope 持有。项目导入／删除保留项目 F3 时可进入 `DB(registry)`，不得反向重开同一项目 owner。个人库初始化**没有**这层文件锁，使用 DB(personal) 和 SQLite。 |

## 关键顺序，按真实调用链核对

### 一次运行准入

`prepare_admission/confirm_admission` 先在 worker 核对目录和注册 revision，返回后才进入 `_admit`：

```text
A1 应用准入
  → A2 会话注册表
    → S3 actor 状态（一次快照／admit_run）
    → B1 输出清理标记（另一次短检查）
    → E1 / M1（另一次快照）
```

这些内层分支不是同时获取。离开准入后才 `start` 保存、模型／工具执行；不得从 S3 或后台 worker 回取 A1/A2。

### 会话删除、历史收尾与恢复

删除先用后台／记忆 guard 建立删除标记，再取 S1；`_delete` 中按 `S2 → A2 → S3` 预留，释放短锁后执行 checkpoint 遍历、MCP 释放、数据库删除，最后短暂回取 S2/A2 清状态。

`BackgroundProcessManager.deletion_guard/output_guard` 在 `yield` 前已释放 B1；`MemoryJobs.deletion_guard` 是所属事件循环上的状态预留，不是一把额外 mutex。因此不能把整个删除／清理过程画成“持 B1 等 S1”。`SessionService.deletion_guard` 则确实持 A2 穿过 yield，且只用于 `_delete` 的短预留，不做 IO。

正常收尾 `afinish` 持 S1，依次完成 saver 快照、聊天归档事务和可选裁剪。S2 只保护状态检查／阻断标记，事务期间不持有。裁剪 `_inventory/_candidates/_commit` 的 DB 事务和官方 saver 读取分段完成；不新增 `DB → D2`，也不把 S1 扩成整个应用的全局锁。

### 记忆与归档

```text
记忆写入：S1 → D1 共享记忆锁 → DB(project) → DB(personal)
归档写入：O1 ProcessCapture → O2 ShellOutputArchive → O3 _GLOBAL_LOCK
```

记忆证据保存完成后才进入 D1；写 workspace 记忆时只用同一个 DB 事务。个人写入由 `edit_with_sources` 固定项目源快照，再进入个人写事务。不能先拿个人 DB 再读项目证据。

归档查询在 B1 下只取 task 元数据，释放后才 snapshot/available。清理保留“禁止新任务进入”的标记，真正清文件时才取 O3。当前 D1/O3 的跨项目竞争是已知实现事实；按范围拆锁、挪走 IO 属于另外的改动，本表未实施。

### 事件回调是锁顺序的一部分

```text
E3 发送锁 → E2（取出后释放）→ E8 publisher → 内置订阅者局部锁
E6 发送锁 → E5（取出后释放）→ E8 publisher → 内置订阅者局部锁
E4 / E7 / M4 → E8 publisher → 内置订阅者局部锁
```

上面两行的 E2/E5 与 E8 是分段获取，外层发送锁仍持有。记录控制时可以 `S3 → E2`，但必须释放 S3 后 flush。用量／诊断接收者先放队列，落库由 drain 在队列锁外完成。

订阅 API 目前不会拦截用户 handler 的反向调用；“handler 不回调应用、不获取上游锁、不等待 run”是维护约束。不能因 publisher 使用 RLock、单线程探针能通过，就允许跨线程反向获取。CLI 父子显示的同线程重入是表内的特定例外。

## 文件锁、SQL 锁与故障边界

F1/F3 常规 owner 的占用跨越整个资源生命周期；这与“每次业务操作阻塞等待再获取”不同。SQLite 自身的读写／页锁是另一层：两条连接即使分别受 DB 和 D2 保护，仍可能竞争同一个文件写锁。`storage.busy_timeout_ms` 只控制 SQLite 等待，不替代应用锁顺序，也不代表超时后自动重试副作用。

正常例子：A 会话裁剪在 worker 内取得 DB(project) 的短写事务；B 会话 checkpoint 在另一连接按 busy_timeout 等待；A 释放事务后 B 继续。失败例子：第二个 owner 取得同一个 F3 失败，直接返回 `storage_in_use/project_in_use` 等边界错误；不能删除 lock 文件、绕过 owner 或持应用短锁自旋重试。

这两个例子的实现入口分别是 [checkpoint_retention.py](../naiyue_test_agent/storage/checkpoint_retention.py)、[resources.py](../naiyue_test_agent/storage/resources.py) 与 [files.py](../naiyue_test_agent/storage/files.py)。已有回归可查 [test_sqlite_busy_timeout.py](../tests/test_sqlite_busy_timeout.py) 和 [test_service_ownership.py](../tests/test_service_ownership.py)；本次文档整理没有重新运行这些行为测试。

## 新增／调整时必须同步维护

新增锁、改变保护对象／作用域、增加或改变锁嵌套（含间接调用和同步回调）时，必须在**同一个变更**更新本表；不能等到出现死锁后补记。仅改函数位置也要更新获取入口链接。

1. 写明锁实例归属、保护数据、允许入口、是否跨 await/IO、释放与取消收尾。别名要指出是否共用同一个实例。
2. 写出新增 `A → B` 边及生产函数调用链，检查表内是否已有 `B → A` 或能绕回 A 的路径；涉及同类多实例时写出稳定顺序，不依赖 dict 遍历顺序。
3. 将“锁内读取对象后释放，再做 IO／回调”的边界一并保留；预留标记和实际持锁分开描述。不要为了表格整齐把独立锁串成全局链。
4. 并发行为变动按项目 TDD 规则添加确定性回归，用 Event／状态同步验证先后及取消释放，避免紧 sleep；文档更新不能替代运行验证。

本次清点覆盖 48 处显式构造／`default_factory=Lock`（包括同类 gate 的一个工厂、MCP 槽的构造／刷新两个位置），按真实实例族展开为上表；另列 F1—F3 与框架 D2。复查入口：

```sh
rg -n 'Lock\(|RLock\(|Semaphore\(|Condition\(|default_factory=Lock|acquire_store_lock|\.acquire\(|with .*lock|serial\(' naiyue_test_agent --glob '*.py'
git diff --check -- AGENTS.md docs/lock-hierarchy.md docs/handoff.md
```

文本搜索只是查漏入口；获取顺序仍需追到调用函数、contextmanager 的 yield 两侧和同步 handler，不能仅靠直接 `with` 的静态嵌套判断。


## R1-09 进程能力准备任务（不新增锁）

HostProcesses 在 RunLifecycle.admit_run 的短锁内只保存同一 HostFileSystem 绑定，不做 IO。`_prepared` 按本次 run 和配置共用解释器准备 task：正常应用调用只在所属事件循环中查找／写入，创建 task 后才 await；发现和版本探测在线程完成，task 使用空 Context，不继承 run 观察器。取消等待者仍等待必要准备收尾，不取消其他等待者。这里没有新增 mutex、没有把请求／输出／许可放入缓存，也不允许后台进程持有 runtime。

BackgroundProcessManager.submit/foreground 的 `_validate_binding` 在取得 B1 前完成执行目录、项目登记/revision、请求环境与归档根检查，数据库短锁在返回循环前释放；随后 `_register` 仍在 B1 内只作内存注册和 worker 所有权交接。关闭可以发生在这两个阶段之间，第二阶段必须拒绝；此间不构成 DB → B1 嵌套。O1 → O2 → O3 的捕获／归档关系及现有优化暂缓决定保持。

### Spec130 图片输入

- `CommandReceipts.images.active` 是服务事件循环独占计数，无线程锁。认证后、读取图片正文前预留，拒绝时立即释放；创建receipt producer后归producer，HTTP断线不释放正在处理的名额。不在S3等同步锁内等待处理名额或执行Pillow/磁盘/网络IO。
- `SessionRuntime.pending_images` 由既有S3状态锁保护，与引导/跟进队列一起原子入队、选择和保留。锁内只修改内存／复制已规范化内容，不做解码、归档和模型调用。
- 仍遵守既有S3→ControlObserver记录锁顺序；observer.flush在S3外。必要保存及graph读取均在S3外，保存成功后重新获取S3释放已保存输入；图片下载先借内容副本再释放S3，之后解码。
- 图片control在取得`RunDelivery.control_lock`之前完成规范化，取得后重新检查目标run和输入窗口；用户停止不等待图片worker。停止已完成后才返回的图片输入按未接受拒绝，worker及请求名额仍排空后释放。
- pending列表先在S3内取得不可变输入引用和状态快照，再在锁外做元数据/文字投影。pending媒体以服务器唯一输入ID归属，不伪造canonical source_revision；历史图片仍校验canonical摘要。长保留文字使用既有frame/detail预算分块读取，回执不携带完整正文或legacy文字副本。

### S07.2 结果保存与下载

- CR1（持久预留，非线程锁）：root/result准入在原CT1→registry事务中同时预留两条receipt；终局与result_pending同事务。等待静止不占artifact_operation_id，允许stop；begin才在TaskIO→CT1→registry短事务登记同result slot。扫描/解包/Git/网络均在Runner，不持Coordinator门跨IO；prepare只封存槽位，commit在TaskIO内短门读取引用、门外有界清单读取、短门CAS发布。列表SQL只在storage registry，未持事务等待页面。
- CR2（Runner loop事实）：单publisher推进持久result计划；freezing只在实际文件冻结/排空期间为true，阻止新命令/静止宣告，不改变blocked/ready事实。扫描前attempt marker落盘；源读取worker取消先await_settled后解除freezing。冻结spool完整原子发布后才网络传输；已有spool不重扫，不新增Coordinator读任务DB。
- CLD1（本机loop下载lease，非线程锁）：ManualCloudTasks.downloads持有DownloadLease，Queue(1) producer独占上游HTTP、binding连接借用及manager/domain operation；浏览器响应只消费有界队列。关闭先取消/排空producer及source，再归还connection/operation和移除lease；未开始producer的取消也显式移除。消费者同时观察队列与producer完成，不能在关闭后永久等待空队列。manager关闭先排空观察/下载，再等其他operation、命令和连接关闭；不停止云端任务。
- 原ArtifactDownload.lock继续保护固定FD与读取worker排空，Range只调整同一FD offset/精确remaining；range解析失败/响应超时/取消均关闭lease，主异常不被次要close异常替换。删除仍按原TI1关闭下载、排空TaskIO后清理文件。没有新增线程锁或跨任务反向嵌套。


## Spec143 S08 本机材料与代码交付

- `ReceiveFiles.publication`是每项目私有内容发布短同步锁：只保护no-clobber链接发布及容量扫描→接收槽位预留。调用顺序为publication→项目DB短事务；正文hash、网络传输和等待不持此锁，DB不反向申请publication。已有相同SHA只核对，不替换inode。
- `ManualCloudTasks.admission`沿现有owner loop准入，登记接收/交付single-flight及额度，释放后才下载/解包/验证；不在worker申请该async锁。`LocalDeliveries.flights/writers`仅owner loop修改，writers限定每项目同一时刻一个写入交付，执行也受全局producer预算。它不锁住外部编辑器/普通本机Agent，不提供跨进程文件CAS。
- 交付台账preparing/executing与`cloud_local.check_deletion`同项目DB事务互斥；先持久状态，再启动worker。worker文件IO不持DB锁，路径日志/备份先于副作用；每次必要台账保存取独立短事务，无文件父句柄→等待owner loop反向链。
- `CloudInputs.busy[input:<id>]`及downloads lease沿现有owner loop预留；固定归档FD物理读取在worker，取消/关闭需等待物理读收束后归还预留。无新增线程锁，不持registry/DB事务跨HTTP发送。

### Spec143AB 结果保留与接收保护

- 沿用CT1 → registry DB短事务；next_run只生成superseded候选，旧结果更新与新root/result预留在同一事务提交，失败不留下部分替代。begin与该事务共用CT1；没有新增线程锁。
- GC先借TI1/TaskIO计数，CT1内短取TI1.condition读取计数后立即释放condition，再做租约查询/删除意图DB事务；CT1阻止此间新TaskIO准入。禁止持condition跨DB/磁盘IO。只有自身一个IO预留且无活动租约/最新保护时可标deleting；释放CT1后清固定文件，再回CT1/CAS同时核销artifact/lease与保留索引。完整task删除封口并等待TI1排空，不能与GC并删。
- ArtifactDownload.open持有TI1预留至FD物理关闭，读worker仍在原async lock中排空；close失败且FD未关闭时保留downloads登记/未完成状态，后续aclose可重试。open失败、Range失败和取消均走同一关闭路径。InputDownload没有task预留，仍用原input busy生命周期。
- 本机接收用服务端owner短租约，元数据前申请、每个Range前续租、传输producer退出释放；不持本机publication/DB锁跨HTTP。TTL负责进程失联收敛，本机已完成材料不永久钉住云端副本；任务删除同事务移除自身租约。

### Spec143AE 验收修复与目录保存

沿用ManualCloudTasks.admission，LocalDeliveries.writers保留项目预留，并增加destinations根及父链目录身份预留；根身份落在另一预留的父链时拒绝，因此同路径、嵌套和大小写别名均互斥，兄弟目录不因共同祖先互斥。有界本机目录事实在admission内await worker获取，owner loop内比较/登记后释放；网络、解包和用户目录写入不持该async锁，worker不回取admission。producer finally释放预留；不持DB事务跨文件IO，不增加线程锁。

显式材料refresh沿原receive single-flight，先在短项目DB事务读原材料身份；完整hash在publication锁外。损坏普通blob仅在publication内复核已观察身份并移至私有隔离位置，后续仍用原no-clobber发布。最后项目DB短事务核对原MaterialRef与旧identity CAS，提交新identity与available；不持publication进入这次最终事务，普通GET/retry/工具读不触发刷新。应用completed及基线元数据同一项目DB事务，prepare/read不推进基线；没有新增DB→admission或DB→publication反向链。

### Spec143AK 本机缓存清理

`ReceiveFiles.publication`另保护users计数/cleaning标记，`use()`仅登记与退出持短锁；预留跨IO/await但线程锁不跨。接收submit/retry、交付prepare/recheck/execute借use至登记flight；材料验证/读取借use至FD关闭。cleanup在owner admission内核对该项目无接收/交付flight并取得独占预留，释放admission后读取项目DB引用，再以既有固定父句柄执行孤儿清理；取消等待worker完成后才释放预留。标记检查不进入DB，DB不反向取publication；同任务并发仍沿已有owner/store生命周期，不增加跨进程承诺。

## Spec143AN 输入范围与服务隧道

- `ServiceNetworks.lock` 是本机async操作锁，串行保护目标状态JSON、原切换意图与SSH进程表；change/cancel/supervisor/close为唯一入口。锁内可await文件/网络/进程IO，不持线程锁或项目DB锁。锁内只向ProfileConnections借用连接，不进入LC0、会话历史锁或项目DB；ProfileConnections不回调ServiceNetworks。关闭先停supervisor、排空接受的切换并回收反向SSH，再关闭控制连接。状态写入使用已有私有原子JSON，不改用户配置。
- Coordinator网络切换的持久标记保存在registry.meta。begin在单个registry DB写事务内SQL检查所有绑定该network_id的任务静止性并设置switching；不获取多个CT1任务锁。RunnerRuns保持CT1→registry DB，reserve在相同写事务检查switching及命令携带的network revision。begin/finish/cancel均不持DB跨网络，SSH副作用在本机执行，断线保留原标记。禁止DB持锁时反取CT1，也不按墙钟超时自动释放switching。
- 范围预览/捕获复用owner.operation和既有执行根校验、输入缓存OS锁；未增加新的跨进程文件锁。预览不预留输入身份，捕获重新校验。
