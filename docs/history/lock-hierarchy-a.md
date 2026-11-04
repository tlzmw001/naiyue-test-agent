# A阶段锁与并发历史记录

本页保留退役前设计原文供查证。2026-11-03 S09已退出这些A专用实现；本页不作为当前锁规则或可运行入口。当前规则见[锁层级](../lock-hierarchy.md)。

## Spec141F 空环境继续准备

服务single-flight准备任务在activity.use之前处理原stopped空环境。先取activity.manage预留，再在worker
读取scope/冻结selection/输入三标记/完整operation记录；仅明确空环境调用既有_perform_action(start)。
manage是跨IO预留而非跨await持有的同步锁；DKR2和DB仍只在worker取得，不能在use内反向申请manage。
start保存running后同一worker写本次resumed_here事实，run_blocking排空后才释放预留。
再次取消/准备失败的空环境收尾重新取manage，只stop本次恢复的旧环境；created_here资格仍由原fresh回执决定。
不新增互斥锁，不在owner loop执行DB/同步Docker，不在start worker中调用interrupt管理等待自身。

## Spec141E 并行首次准备

服务准备任务同时拥有snapshot与container两个分支，均经run_blocking在工作线程执行。
snapshot仍持GI2(input)并分段取得DB；container仍持DKR2(binding)并分段取得DB及连接授权。
两个分支不互取对方的同步锁，不持DB等另一分支；只由外层协程await_settled(gather)排空，
完成后才进入原安装的DKR2→GI2路径。失败在释放activity使用预留后，先取得activity.manage预留，
在worker读冻结记录与完整operation清单，再调用既有_perform_action进入DKR2；不持同步锁跨await。
仅服务新建成功回执证明容器和双卷均本次新建、明确未安装、输入三字段全空、无任何操作、精确scope的running环境才可回滚；
真实失败销毁本次空容器/双卷，取消仍沿stop语义，unknown/既有资源不动。不得调用interrupt管理等待自身。
on_created回执仅在DKR2内保存running且确认fresh_resources后设置本次内存事实，不取其他锁/IO，调用方排空worker后才读取。

SandboxTransport._starting是传输owner-loop独占的精确连接身份集合，无新增线程锁。
只有已保存creating环境的service._create可登记；_open在第一次main尝试且无await的短段消耗，
finally撤销未用许可；普通get/reconnect/recover不登记。允许DKR2→runtime.call，transport loop不回取DKR2/DB。
Recorder仍为PRF1叶锁；并行阶段只记录时间区间，区间并集计算在锁外，不形成新锁嵌套。

## Spec141D 独立准备计时脚本

`scripts/profile_sandbox_preparation.py` 的 `Recorder._lock` 只用于该脚本进程内的当前轮次、
有界事件追加和结束快照；不接入产品运行时。Git/管理方法可能在既有生产锁内完成，因此允许
既有锁 → Recorder短锁；Recorder锁内没有日志、文件、数据库、网络、await或被观测方法调用，
不存在Recorder → 生产锁的反向路径。汇总与JSON输出均在锁外；包装器在调用原方法前释放该锁。

## Spec141B 常驻传输（2026-10-29，实施中）

复核后的修正约束见[Spec141B第10.5/10.6节](../specs/141b-persistent-sandbox-execution.md#105-单请求结束后的迟到回复接收责任有界保留)：pending→retired迁移及其接收额度由原transport loop原子拥有；部分帧和缓存另有有限预算。binding清理为单批single-flight，默认策略最多2次终止/共享10秒总预算；失败记录与封锁分离，普通get不重新触发清理。显式恢复只能开启新的有限批次，原资源未确认退出不得开放任一lane。源码已按这些约束接线并经专项验证；完整构建与公司验收状态以Spec检查点为准。

修正实施：EnvironmentActivity.ause在原短RLock保护的使用权标记之外await worker touch；touch版本登记和uncertain只在短锁内更新，不持锁做数据库IO。版本只保留每binding最新在途更新，旧更新结束不得覆盖较新失败。operation算法获取仓储句柄也交worker，不在transport loop等待P1；watcher完成回调由原owner loop按task身份删除登记。

- EOP1同时保护有界_searches及原control/Event。request_register的UDS存活租约由请求handler持有；socket等EOF不持EOP1，finally回取EOP1取消/释放，搜索finally同锁退登记。不得在EOP1里等搜索/子进程结束。
- Web待发送沿原W1登记producer及回执，锁外等待环境和执行。stop_submission在W1内标记取消，在锁外排空producer；精确expected_run_id先于W3投影绑定过滤。原RunAPI准入经S1/既有runtime锁，在RunLifecycle.admit_run同一临界区比较冻结设置、验证queued图片归属并消费；该段无网络/DB IO。失败/取消在原图片runtime锁下转retained，不新增锁或通用队列。
- EnvironmentService 独占 SandboxTransport；其一个 SandboxRuntime 线程拥有所有 asyncio 流和连接表。调用方跨 loop 仅交付 coroutine/Future，不转移流、锁或连接对象。runtime 的 RLock 只保护启动/封口，实际网络和关闭在锁外；不得从 transport loop 同步回调 runtime.call。
- 连接表由 transport loop 串行更新；键是 binding+lane，内容校验完整容器/daemon/session/冻结配置。建连任务是 single-flight，global connecting semaphore 仅限制建连；不同 binding 不持共享网络 mutex。binding 级 invalidating 标记同时封住 main/bulk；不能在旧通道关闭过程中开另一 lane。
- 每通道普通/控制分别限制活动和排队；控制名额不被普通排队占用。FrameStream 一个有界优先队列、一个 writer；每条消息每次最多排一帧，writer 在实际首次 write 前设置发送责任。尚未发送的取消帧不写出；已发送请求保持回执托管。
- stop/destroy 在 DKR2 下仅封口并启动异步连接退休，不等待尚在核验的旧建连，再走独立管理命令。start/recover 等退休完成后重建。连接退休失败保留标记并传播/诊断，不能当作未获得资源吞掉。
- envd 每次启动生成 generation，UDS请求先检查代际再分派副作用；attach TaskGroup 中任一致命协议错误取消主读取和其他桥接任务，不停止 envd 已托管的任务。工作负载停止仍由原 EOP1/journal 合同处理。
- 控制端 operation 算法同属 SandboxRuntime loop。原 SOP2 线程 RLock 改为按 `(binding,operation_id)` 的 asyncio.Lock；等待者计数归零即移除，不随历史任务无限增长。submit/refresh/background 的状态与归档更新串行；stop 先走控制槽，再进入同一 operation 锁核对最终状态，不等待输出下载才发送。锁内本地 DB/归档 IO 委托有界工作线程，不同步回调 runtime.call。
- 同步 Git输入、Git结果、服务关闭仍通过 submit/refresh/stop 的明确跨 loop 桥接进入同一套算法。ResultService.capture 只保留 DKR2 负责结果下载/发布，移除其对旧 operation RLock 的嵌套；操作状态只按字段保存，不能覆盖交付 metadata。
- 同 operation 的 submission Event 仅覆盖本地 reserve、一次远端提交和必要回执保存；取消等待者不能提前释放。stop 先等此事件，避免控制帧超越未发送完整的提交，再绕过输出锁发控制请求。事件在所有提交终局移除，身份为 binding+operation_id。

以下领域表的 SOP/准备/交付入口已按141B迁移同步；其余历史版本说明仅作背景。



## R1-10 环境生命周期与 envd

| 标识 | 保护对象与入口 | 顺序与禁止事项 |
|---|---|---|
| DKR1 `EnvironmentService._guard` | 已知 binding ID 到生命周期锁的字典；仅 `_lock` 短查询/插入 | 在 worker 中使用，释放后才取得 DKR2；不在此锁内访问仓储、Docker、文件或回调。 |
| DKR2 每 binding 的 `RLock` | create（包含最初 reserve）、start/stop/destroy/recover；全部从 EnvironmentAPI 的 run_blocking 进入 | `DKR2 → DB(registry)` 只做短记录提交/读取，事务释放后再 Docker IO。禁止持 DB 等待 DKR2。不同 binding 不互相等待；事件循环不取得它。文件 RPC 不持 DKR2，仍允许容器内原 Write/Edit 目标锁发现同文件冲突。 |

创建 UUID 在本地确定后，先预留绑定活动使用权，再取得 DKR2 并公开 creating 记录；默认并发 destroy 报 env_busy；显式 interrupt 先封活动准入、请求准备检查点取消并排空已发创建，才进入 DKR2 销毁，不会先返回 destroyed 后被 create 复活。查找字典锁 DKR1 不与 DKR2 同时持有。ProjectService.environment_records 只在 worker 用原项目写锁获取/初始化仓储句柄，离开该锁和数据库事务才连接 Docker；轻量 execution_facts 不取得项目写锁。

R1-13D-04 的 EnvironmentActivity._lock 是叶级短内存 RLock，只保护使用计数、管理标记和时间保存不确定集合。use/manage 上下文保留的是标记，不持该锁跨 await、SQL、绑定锁或 Docker IO。run/审批恢复/压缩/重生成、输入安装和结果捕获全程持使用权，管理独占标记与使用权互斥；Worker 的单次 RPC 和观察刷新也取得使用权。只读刷新不更新时间，本地 env_busy 不写 unknown/stale，后台采集稍后重试。空闲检查在管理前后核对 journal，避免因扫描而打断健康观察。首次执行终局且输出结算才更新时间。

额度通过 DB(registry) 同一短写事务读取计数并预留 creating/starting，释放数据库锁后才连接 Docker；不新增配额锁。常驻根生命周期停止检查器并封操作入口，排空进程、run 和子宿主后，才停止全部未销毁原容器（包括上次登记 stopped 的资源），不持会话/线程锁等待 Docker，不删除卷。失败尝试剩余容器，保留首异常。

envd 的 Unix socketserver 在每个容器中处理请求，其框架线程管理不是应用线程锁；容器内复用原 `_active_targets_lock` 与目标占用表、进程输出/归档锁，实例不跨容器共享。协议不携带锁、publisher 或运行中的 Python 对象。R1-11 的 Shell/argv 通过登记操作入口执行；原生 fd/rg 仍在该容器内搜索。Find/Grep 的控制端 async 入口已用 asyncio.to_thread 执行只读 require_file_target，不能在 owner loop 等待同步 Docker 校验，也不反向导入运行时层。

DockerCLI 使用既有 HostProcessRunner，输入仅来自有界临时文件的 stdin_fd；临时文件持有到原有 spawn/IO/进程组结算结束，没有另建 Timer、管道读线程或另一套超时回收器。Shell/AITest 默认 DEVNULL 不变，后台管理器显式拒绝借用该传输 FD。关闭应用不自动删除/停止容器；R1-11 在注册表关闭前确认停止所属活动进程。

## R1-11 操作日志、归档和关闭

| 标识 | 保护对象与入口 | 顺序与禁止事项 |
|---|---|---|
| SOP1 `SandboxOperationService._condition` 内部 `RLock` | 接受门、提交中计数及按项目的归档清理/写入预留；`accepting/close/output_guard/_archive_activity` | 仅短内存操作；释放后才取得 SOP2、查询注册表或执行 RPC。`close.wait_for` 等待时释放条件锁；不持条件锁扫描/停止操作。归档清理在释放条件锁后查询操作事实；标记保留至清理结束，submit/refresh 在 SOP2 下短取 SOP1，释放后执行 RPC/归档 IO。不得持 SOP1 等 SOP2/DB/O3。所有同步入口在 worker 执行。 |
| SOP2 `(binding,operation_id)` 的 `asyncio.Lock` | SandboxRuntime loop 的 submit/refresh/background 状态及归档单飞；计数归零删除 | 锁内可 await 原生 RPC，并将必要 DB/归档 IO 交有界 worker；worker 不同步回取 SOP2。stop 先等 submission Event，然后绕过输出锁发控制，最后通过独立 refresh 取锁。不同 ID 不叠持，无重入。同步 Git/关闭入口由 runtime.call 进入同一算法。 |
| EOP1 `EnvironmentOperations._lock` | envd 活跃控制对象及关闭标记；submit/stop/background/close | `EOP1 → EOP2`，预留 journal 提交后才启动 worker；原生进程等待、stop 的 done.wait 在释放 EOP1 后执行。worker 先完成 EOP2 写入并释放，再取得 EOP1 清活跃项。不得持 EOP2 等 EOP1。 |
| EOP2 `SandboxJournal.lock` | 容器内单连接 SQLite 的预留、状态、有界输出和 close | 短同步数据库事务；不跨 Shell/文件执行、线程完成等待或 Docker IO。journal 在进程输出回调中被取得，但内部不回调原生 capture/控制对象。 |
| F4 `/state/journal.lock` | envd journal 的 OS 排他文件锁 | 非阻塞，初始化取得至 journal 关闭；只允许该状态卷一个 envd owner。重启将旧活动行标 unknown，不采用 PID 或重放。文件锁不代替 EOP2；Spec141A由不同UID和受保护父目录阻止任务访问journal，旧同UID镜像不具备该边界。 |

采集 task 的 `_watchers` 字典只由所属事件循环读写，无线程锁；task 使用空 Context。已退出的 task 可由新查询重启。归档的 offset/文件身份以本机库为准，必须在 SOP2 下先 fsync 再提交进度；首次创建文件与必要身份保存由同一个 run_blocking 托管，取消也排空，避免留下无身份归档；不会把数据库锁跨过 RPC 或 fsync。现有 O3 全局输出锁未拆分，线程池和记忆锁优化仍按用户约定暂缓。

Spec141A的工作区根FD由EnvdFiles拥有：生产serve上下文先关闭Server并等待非daemon请求线程完成，再执行EnvironmentOperations.close停止/等待活动操作、关闭journal，最后关闭根FD。不能颠倒为先关FD再等待读者，也不能在EOP1内join请求线程。未新增锁或请求线程池。

## R1-12A 输入快照、接收与安装

| 标识 | 保护对象与入口 | 顺序与禁止事项 |
|---|---|---|
| GI1 `InputService._guard` | input_id 到准备锁的字典，`_lock` 短查询/插入 | 叶锁；释放后才取得 GI2，无 IO、SQL、Git 或 RPC。 |
| GI2 每 input_id 的 `RLock` | `prepare` 的预留/本机快照/必要保存；会话 setup 首次登记；安装 input_id 引用的必要保存；`_transfer` 首次生成对应 delta；`retire` 按全部引用回收输入 | `GI2 → {DB(registry), DB(project)}` 分段短事务；首次会话登记允许 `GI2 → A2 → S3`，释放短锁才保存项目库。不持 DB 做目录删除或 Git IO。安装引用登记与 delta 允许 `DKR2 → GI2`，会话删除允许 `S1 → GI2`；不得从 GI2 回取 DKR2、S1 或 SOP2，不同时持有多个输入锁。 |
| GIR1 每 envd 的 `InputReceiver._lock` | begin/status/chunk/probe/cache/reserve_install/installation/install；接收状态和缓存对象 | `EOP1 → GIR1` 只用于安装前检查空目录、缓存标记与持久安装预留；预留阶段包含有限文件 IO/fsync。cache/install 可持 GIR1 做有界 Git/磁盘 IO，但不回取 EOP1/EOP2；worker 释放 GIR1 后才提交 journal，再释放 EOP2 后取得 EOP1 清活动项。 |

Spec141C：GI2 内的 Windows 只读输入删除重试使用已有安全文件打开器；句柄禁止 FILE_SHARE_DELETE，
持有范围只覆盖普通单链接文件身份核对和 READONLY 属性清除，关闭后再核对目录链/文件身份并 unlink。
该句柄不跨 SQL/RPC，不调用应用回调或取得上游锁；未新增线程锁或改变 GI2 的先后关系。

输入生命周期扩展已有接受门：`prepare/install` 从开始到必要记录保存均计入 SOP1 的接受计数，实际工作不持 SOP1。关闭先封口，等接受计数归零；已经在传输的安装可完成对象接收，但若正式 submit 前已关闭，必须以 env_stopped 拒绝，不能越过封口开始检出。

同 binding 安装复用 DKR2，允许 `DKR2 → {GI2, SOP2, DB(registry)}`，各阶段分段进入。操作仓储的懒加载可能短暂进入 P1，再进入 DB(registry)，不持 DB 反取任一上层锁。`operation_status` 在 SOP2 下取回原结果并释放后，才经 `confirm` 进入 DKR2 做必要保存；禁止 SOP2 → DKR2 的嵌套。安装内部同线程重入 DKR2 只用于 confirm；不跨 binding 叠持。

envd 在同一个 EOP1 临界区内完成“活动操作/读取数检查 → journal 预留 → installation 标记 → _input_pending”，文件只读调用在 EOP1 下增加读取计数后释放锁，再执行文件操作，结束后回取 EOP1 减计数。因而安装不能与已进入的读取或进程交错，新读取/提交也不能看到半份检出；hello、原操作状态/输出/停止仍可用于核对。失败/unknown 安装在 envd 重启后继续封住新执行；成功 journal 之后，控制端仍必须保存 binding.input_snapshot，未确认绑定的普通文件/进程入口拒绝，输入管理和原操作查询保留。

本片新增三个显式锁构造位置（GI1、GI2 工厂、GIR1）；没有新增线程池、全应用项目锁或数据库事务内的远端等待。

Spec138：显式 `EnvironmentAPI.retire_input` 对已加载项目直接借宿主 operation，不取得 host gate；会话删除仍持 S1，但不引入 S1 → G4 的反向依赖，公开 close 继续以活动 operation 拒绝关闭。冷项目先取得 G4、重新检查已加载宿主后借原项目库，未持 S1；保留 G4 至输入回收完成，阻止此间打开/关闭项目库。GI2 内依次查询会话 setup、未销毁环境、关联结果/交付记录后删除应用输入目录与输入记录，不跨两个数据库叠持事务。活动 prepare 先结算；安装在 DKR2 → GI2 内落地环境 input_id 后释放 GI2，后续提交才进入 SOP2。destroyed 环境的新结果提交在预留操作之前经 validate 拒绝，既存结果记录本身阻止输入回收。


## R1-12B 会话绑定与环境管理

Spec141B：S1 `history_lock(session_id)` 仍串行会话身份预留/删除及显式会话动作，同请求派生同一 session/input/binding。后台准备由服务内有界 single-flight task 持原 host owner.operation 与 environment 使用权，允许释放首个 HTTP 请求；删除门禁在环境尚未登记时也检查该 binding 的活跃准备。准备与管理只留预留标记，不在短线程锁里 await。准备进度 RLock 只保护阶段/计数/取消标记，释放后做 IO；其父 registry RLock 仅查询/快照/有界登记，不跨 IO。服务关闭先请求准备取消并排空，再关闭 operations/host/transport。

生命周期在S1内，按既有全局准入锁→SessionService._lock→actor短状态锁原子检查busy，并设置`_environment_management`集合。随后释放所有线程锁才await Docker，finally仅回取SessionService短锁释放预留。新run/压缩/审批续跑在准备前和正式_admit复核管理事实，不能在异步status检查后插入执行。此集合是同一准入事实，没有轮询或额外线程锁；history_lock不代替准入锁。关闭宿主仍沿原_closing复核拒绝。

Spec138：验收脚本仍使用的低层 EnvironmentAPI.create 先查询/打开所属宿主，释放 G4 后借宿主 operation 并取得原 S1，再次读取 session owner 后才创建；禁止持 G4 等待 S1。会话删除同样检查普通会话的直接环境关联，未销毁环境存在时拒绝删除。低层创建与正式沙箱会话均不新增独立 owner 锁。

GitSource首次打开项目历史宿主复用项目操作日志/P1初始化项目库；无本机根，不做Git fetch。注册身份轻量读取仍只持DB(registry)短查询锁，工具校验不取ProjectService写操作锁。原SOP2查询/输出/停止按绑定查询，不在事务里执行容器IO。本片不改默认线程池和归档全局锁。

## R1-13A 结果捕获与导入

SessionAPI 的结果捕获在原 S1/history_lock 内使用原 manage_environment 准入预留；管理预留只在短锁内设置，覆盖捕获、传输、校验、本机引用发布及必要保存。新 run/环境生命周期/删除不能在捕获中插入。只读结果列表和差异不获取管理预留，也不依赖容器运行。

Spec141B：ResultService 保留 `DKR2(binding)` 保护捕获/下载/本机发布；同步 submit/refresh 经 runtime.call 进入独立 SOP2，不再持线程 RLock 重入网络算法。每个短数据库事务结束后才执行 RPC/Git/fsync。整个捕获与保存加入 SOP1 接受计数，关闭先封口、排空，再扫描原操作；无 DB → DKR2/SOP2 反向嵌套。同 ID 的不可变结果只下载/校验一次，不同绑定不串行。

envd 的 EOP1 在一次临界区内检查活跃操作/读者/未确认进程、持久预留 git_result 并设置 `_result_pending`；释放后 worker 做独立索引快照和有界 Git。新执行/文件读取在 EOP1 中检查 pending 并拒绝；hello、原状态/输出/停止保留。worker 先完成 EOP2 journal 写入，释放后才回取 EOP1 清预留；不持 EOP2 等 Git/线程完成。结果 chunk 只读 completed 的原文件，不新增互斥锁。捕获未知不会重放，也不把未知进程视为已停止。

## R1-13B 本机受控交付

复用 ScopeTasks 的新 key `('delivery',project_id)` 串行同项目交付。它可短暂进入 G4(host)→A1→A2→{S3,B1}，检查本项目运行/环境管理和所有已加载会话的后台清理，设置 A1 下的项目交付标记。B1 deletion_guard 在 yield 前释放自身锁；G4/A1/A2 也在进入工作线程前释放。交付标记保留至整个动作和必要保存结束，不把Git等待放入任何线程锁。同项目只读历史仍可取得 G4，其他项目不受影响。

项目 rename/trust/relocate/remove/close 共用 G4 的 project_change，取得 G4 后短查 A1 标记；交付也在 G4 内预留，因此冷项目的根 EnvironmentAPI 路径同样与位置变更双向互斥。准备和正式运行准入分别短查 A1，不从 A2 回取 A1。生命周期只有在 accepted operations、runs、进程清理全部结算成功后才在 A1 下注销 SessionService；关闭失败保留阻断事实，不依赖 WeakSet 的垃圾回收时机。

Spec141B：工作线程方向为 `delivery gate → {P1, DB(registry)}`；唯一生产 execute/acknowledge 写入口由 EnvironmentAPI 持已有每项目 delivery gate 和 RunAdmission 预留，移除无消费者的旧同步 SOP2。固定本机结果不与远端进程锁共享职责；SOP1 仅计数，不在 IO 期间持条件锁。GitDeliveries 的状态/意图/核对/人工确认均短事务，discard 的完成与结果标志在同一事务。执行、只读核对均不取得 DKR2，不从 DB 反取 SOP2/G4/A1；不新增执行器、mutex、全局网络锁。关闭沿原接受门封口与 owner.operation/run_blocking 排空，不把交付 journal 当作 envd 进程停止。

提交前归档保护补充：TaskAPI 的预览/清理将 guard 获取和文件操作一起放入 worker；A2 只建立本机任务预留后释放，再进入沙箱项目清理预留及 DB 查询。项目有活动、unknown、stale 或未采集完的 shell/argv 时拒绝清理；清理期间拒绝同项目新提交与采集。不同项目的预留互不阻塞，条件锁只保护内存计数，不跨 RPC/SQL/文件 IO。
