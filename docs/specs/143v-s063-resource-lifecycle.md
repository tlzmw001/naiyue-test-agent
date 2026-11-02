# Spec143V：S06.3 资源收敛、释放与任务删除

状态：2026-11-02实施前源码审查后拆为S06.3a（周期维护/容量门禁）与S06.3b（删除/关联核销），a/b均已完成本机验证，未提交/部署。保留S02/S04.2b的隔离、静止/必要保存/unknown与物理换代合同，不自动删除用户数据。

## 1. 当前事实与本片目标

S06.3实施前已有`CloudLifecycle.command/sweep_idle`和原子活动/保留额度，`stop_environment`通过后端kill并核对容器不存在后结算，保留任务持久目录；当时server仅维护输入staging，尚无周期回收与数据删除入口。S06.3a已接通周期资源维护，S06.3b当前主链见第10–11节；原始问题记录不代表当前仍缺同一入口。

本片补Coordinator生命周期维护、只读容量状态、完整删除状态机和本机会话删除保护的核销。不能把这些隐含到S10更新或S11验收时才首次实现。任务数不等于聊天数；仅新增独立任务触发环境准入。

## 2. 控制对象与接口

| 操作 | 行为 | 持久数据 |
| --- | --- | --- |
| 本机停止本轮 | 仅控制本机根执行 | 不取消已受理云端任务。 |
| 云端停止本轮 | 现有owner control stop，收束根子/受管后台、必要保存/ACK | 保留workspace/state/过程/结果，可继续。 |
| 释放云端环境 | 先停止并等待必要保存，静止后stop_environment，核验物理容器不存在 | 保留workspace/state/history/results，后续显式启动原task新代。 |
| 删除云端任务 | 用户确认范围，停容器并确认旧写入者消失后，清理该任务持久数据 | 云端该task的workspace/state/history/results删除；本机已取回材料、主聊天不删除。 |

复用原控制和释放入口；拟新增owner `GET /api/cloud/v1/capacity`、`POST /api/cloud/v1/tasks/{id}/delete`和原commands查询。删除请求包含command_id、expected_revision/generation、经预览确定的task身份，以及需要时的acknowledge_unknown；鉴权和作用域检查在业务边界显式执行。task凭证无删除/容量管理权限。

本机通过已核验binding提供删除预览/确认/查询，不让浏览器传任意目录或后端容器ID；新能力真实接线后再发布。只读容量返回已占/预留/unknown/保留数量、已知磁盘使用与是否可创建，信息未知不能显示为零。

## 3. 周期回收与并发准入

在现有Coordinator async服务生命周期中启动一个拥有明确关闭者的维护作业，复用现有输入维护/错误诊断模式，不新增系统daemon。每轮有界扫描任务，调用同一sweep_idle→环境命令合同；间隔走配置，Coordinator不可用期间不靠后端TTL销毁。

自动释放必须同时满足：Runner报告静止、无根子/受管后台、输出收齐、必要历史和该轮要求的结果均已确认、无在途控制/环境/捕获/换代、无unknown，且超过idle_stop_seconds。看过程/下载已固定结果不刷新执行活动。维护中的新run/控制与释放通过同一任务预留/CAS互斥，停前再核对，不能凭一次心跳停掉新运行。

续跑、手动释放、自动释放共用真实命令/回执和后端核对；失联、活动保存失败和无法确认旧容器消失均保持占额。维护失败记录安全原因与重试状态，不静默吞错、不无限高频重试、不阻塞全部任务API。

原子准入统一统计正在启动、运行、尚未确认释放的资源；保留数包括未删除任务。额度不足首版明确拒绝新的创建/启动，不做无界排队或自动扩容。已有受理命令按原回执核对，不以重试绕过额度。

## 4. 磁盘边界

CPU/内存限额控制运行资源，停止容器不减少workspace/state/结果占用。沿既有input/artifact字节上限和保留数，补托管目录使用/剩余空间信息、创建和下载前容量门禁、staging失败残留清理；参数走配置。空间不足拒绝新准备/捕获，保留诊断和已确认数据，不自动删最老任务。

使用量核对不得打开任务SQLite；目录扫描不跟随任务链接，不跨允许挂载，条目/时间有界。扫描不完整显示unknown并保守拒绝新分配，不能靠递归resolve越界计量。结果/输入引用计数从注册表确定，不靠文件名猜测。

首版不承诺卷级实时硬磁盘配额：目录计量和水位门禁不能防止运行命令瞬间写满磁盘；部署须为托管数据保留容量并监控。若公司要求硬配额，需先验证目标机文件系统/存储驱动能力，不能把Docker内存限制或周期扫描包装成磁盘硬隔离。本片不擅自改分区/挂载/daemon配置。

## 5. 删除状态机与失败恢复

删除是明确的所有者动作，不因关侧栏/删聊天/空闲到期自动发生。预览显示目标task、云端数据范围、已有结果和未确认状态；点击确认只授权该task，不能清其他任务/共享输入/镜像。

| 阶段 | 必要行为与重试 |
| --- | --- |
| reserve | 正常任务先通过已有control stop完成必要保存/静止，再原命令去重与CAS登记deleting/原容器身份；禁止新run/捕获/启动/续租式复活。 |
| stop/verify | 正常未静止返回requires_stop，不在deleting后调用会被凭证封口的正常stop；若已丢失/损坏且unknown，需用户显式确认可能丢失未保存内容。旧容器不存在必须由后端证实，不能以令牌吊销代替。 |
| remove task data | 只处理注册表给出的可信task父目录和明确自有history/results索引；停止后用不跟随链接的删除方式。Coordinator可关/删除自己的history库，但不打开任务SQLite/WAL做修复或清理。 |
| release references | 解除该task的输入/release引用，不立即删仍被其他任务使用的输入/镜像。同步更新产物/结果可用性，不能留下可下载成功的假索引。 |
| finish | 持久墓碑和原命令回执，明确数据已不可续跑。只有必要清理与引用更新均确认后才退出保留额度和本机关联删除保护。 |

磁盘/权限/网络失败保持deleting或unknown和具体阶段；原命令重试接着核对，不回到新task、不能伪造业务成功。后端不能确认旧写入者终止时停止删除；已删除部分目录后也不能把状态改回完整ready。必要诊断只记录task/命令/安全码，不打印不可信文件内容或凭证。

删除前先关闭该task的查询/传输新准入，并等待已获准的短读取/捕获操作收束；SSE结束为删除事件。不能一边删除一边允许新的下载或恢复。从墓碑查询可取得删除回执，避免本机因404无法区分未创建与已删除。

## 6. 本机会话保护

按用户已确认选择，任何关联未删除云端任务（运行、停止、完成、unknown/deleting）均阻止删除本机会话/所属项目。提示先取回需要的结果并处理/删除该task；释放容器不解除关联，不把“已停止”误作“已清理”。首版不做任务转移至全局列表。

本机只有从目标实例原命令/墓碑确认删除成功后，才核销关联保护。断网或陈旧缓存不能猜已删。解除保护与本地操作状态提交应原子；创建关联、删除父会话与云端删除回包的并发有明确预留顺序，实际接线同片更新lock-hierarchy。

## 7. 验证与边界

主要影响`service/cloud/server.py`、cloud lifecycle/registry/目录清理、容量/删除路由、local binding及本机会话删除；复用当前stop/backend验证和路径保护，新增状态/枚举同步契约/DB/序列化/测试。无需新依赖，不修改真实环境或执行任何实际公司删除。

拟测试`tests/cloud/test_resource_maintenance.py`、`test_task_delete.py`、`test_cloud_session_delete_guard.py`：周期实际接线、长任务/未确认结果/unknown阻止回收、查看不续活动、准入竞态/额度、后端kill后仍存在、删除ACK丢失和逐阶段崩溃、链接/FIFO/权限错误、共享输入仍被引用、并发读取/下载、断网父会话删除拒绝和墓碑核销。真实Linux非root清理与Windows父会话交互在S09/S11验，单元替身不替代实机。

## 8. S06.3a实施边界与当前调用链

本次先完成资源部分；S06.3b再实施第5–6节。拆分原因是源码确认删除存在独立的必要保存/凭证与长传输排空合同：先标deleting会使正常stop与历史ACK被拒；历史查询还可能重建已删库。不能把删除视为stop之后递归删目录的附带操作。

资源配置新增`resource_limits`：维护间隔、每轮任务数、扫描条目/深度/时间预算、磁盘快照缓存时长、最小剩余字节及可选托管逻辑字节上限。默认值属于公开配置模型，不改真实配置。容量统计使用registry短事务聚合，磁盘计量独立完成，不读取Runner数据库。计量是按目录项累加的逻辑字节（硬链接可重复计数），与`statvfs`可用空间分别报告；不冒充原子文件系统快照或硬配额。

正常链：server拥有的资源维护协程→`CloudLifecycle.sweep_idle`有界task_id分页→原command/CAS二次核对→既有后端kill/get→任务stopped，工作区不删除。游标轮转避免非空闲头部饿死后续任务；一个任务的维护失败记录安全码，不挡同轮其他任务。未知物理状态继续占额。显式在途artifact/control字段也阻止空闲停止，观察读取不刷新静止时间。

失败链：输入/任务创建、环境start/refresh安装（含重开后的原start reconcile）、artifact捕获/prepare及实际输入/产物上传在分配前调用同一磁盘门禁；free不足、计量未知或逻辑字节水位不足→安全CloudError→不创建新环境/transfer/产物写入。终局原命令重试先读原回执；重传已确认输入段不重新分配字节。已完整接收后的input/artifact发布及已发布对象的原索引恢复属于必要收尾，不加新分配门禁，真实IO失败照常可观察。磁盘低水位不阻止停止、历史必要保存或下载已固定文件。下载流不在Coordinator分配另一份文件，客户端本地结果空间门禁归S08.1。已确认input_ready的工作区重开不重复计完整输入；未确认首次安装则按manifest大小保守判断，不为计量打开任务状态库。

计量使用固定root目录FD、逐级O_NOFOLLOW/目录身份核对；只lstat文件和特殊项，不打开内容，不跟随链接。Linux逐目录核对FD的mount ID（含同设备bind mount）；其他平台核对st_dev。条目/深度/截止耗尽或权限/挂载/身份错误均返回unknown和安全码，部分字节不作为完整已知用量。截止在扫描检查点核对，不能强行中断内核文件系统阻塞。可用空间每次门禁重新取值，逻辑用量按配置短缓存，周期强制刷新；门禁是软水位，不能阻止运行命令瞬间写满盘。

owner `GET /api/cloud/v1/capacity`返回instance_id、active/reserved/unknown/retained和上限、磁盘完整性/逻辑字节/剩余字节/扫描时点与拒绝原因、can_create。task listener无此管理路由，owner鉴权先于扫描。capacity是只读建议，最终任务数/活动额度仍由既有同事务准入保证；容量GET不探测/修改容器，不解除unknown。

本片验证：配置边界、容量认证/真实HTTP接线、有界轮转/隔离失败/静止保护/CAS竞态、kill未确认仍占额、无follow symlink/FIFO/越挂载、预算/权限故障、低水位前后旧transfer/环境启动/产物准备/上传门禁、已确认重试与必要保存/释放可用。全量回归及标准构建后再转S06.3b；未实现删除前，本机关联删除保护保持原合同。

## 9. S06.3a本机实现与验收（2026-11-02）

实际入口是server的`_maintain_resources`与owner容量API；`CloudResources`在runtime初始化并由输入/环境/产物准入真实消费。原`LifecycleRepository.tasks`无界扫描已删除，任务读取统一task_page，既有任务活动/保留额度原子准入保留。资源锁CR1已同步lock-hierarchy；配置与正常/失败操作见[cloud-resources](../cloud-resources.md)。

专项命令`.venv/bin/python -m pytest tests/cloud/test_resource_maintenance.py tests/cloud/test_resource_admission.py tests/cloud/test_server_restart.py -q`实际46 passed；云端全套544 passed。完整后端按`python -m scripts.ci_shards --index 0/1 --count 2`分别3217/3486，共6703 passed，无fail/error/skip；新增45项，原6658精确节点保留。前端69文件/568项及TypeScript/Vite构建通过，既有chunk警告保持。四目标标准wheel/Twine与Mac源外干净安装通过，491个Python源逐包一致，安装provider_requests=0；pip check、compileall和diff检查通过。

独立架构/安全审查纠正必要发布门禁、已安装工作区重复预算、关闭后继续整页回收、遗漏锁文档等问题，均已复核关闭。源码冻结及逐包校验、原测试身份、审查与实际命令结果见[verification](../../build/spec143-s063-20261102/verification.json)/[review](../../build/spec143-s063-20261102/review.md)。新增server fake字段是为维护作业真实接线扩展原生命周期测试，原socket重绑定与关闭断言保留；未删除/弱化旧行为断言。

没有真实Linux非root/bind mount/容器停止或Windows/公司/供应商验收，没有修改真实配置、依赖或用户数据，没有提交/发布/部署。第5–6节删除状态机与本机关联核销尚未实施，仍是唯一下一片S06.3b；本片不自动解除任何父会话保护。现15单元完成5项、剩余10项，无新增用户掌握证据。

## 10. S06.3b实施细化

新增owner `GET /tasks/{id}/delete-preview`、`POST /tasks/{id}/delete`及原删除命令reconcile。预览固定instance/task/revision/generation与数据范围的digest；POST带command_id、confirm_task_id、expected_revision/generation、preview_digest、acknowledge_unknown。正常active/保存/控制/产物/换代未完成拒绝，用户先走原停止/恢复；异常边界显式确认才允许丢弃未保存现场。临时读取不改变预览。重复原ID先读原回执，不重新预览或改请求；恢复只重试同一删除阶段，GET只读，不自动执行。

删除回执在既有commands表保存typed DeleteOperation；CloudTask增加delete_command_id，不另建数据库或第二任务运行时。reserve原子保存deleting、原物理身份、回执并吊销凭证；finish前保持retained。先排空该task已发出的旧环境producer（防迟到create），再原get/身份核对/kill/get并find核对；无sandbox ID的unknown必须用原环境metadata find，匹配含糊拒绝清理。旧producer的update/finish/迟到detach不得改写deleting。stage持久为reserved/stopped/cleaning/finished，异常保留原阶段+unknown、安全码；原IDreconcile重启恢复。

短历史读取仍由CT1排空，_history在返回缓存或建库前拒绝删除态；跨段snapshot允许并发删除时返回task_not_available，不拼接半套内容。产物prepare/upload/commit/capture、环境_execute及download open用任务IO短预留，get在CT1内完成；准入检查与计数登记在CT1下完成，IO计数锁不跨实际IO，删除在CT1外等待既有IO排空。download lease从创建携带task_id并串行open/read/close，删除主动关闭该task下载、拒绝后续读取。模型已有流按task关闭；SSE发送task_deleting终止观察（不证明删除成功）且不再resync，新订阅在owner loop封口。delete_drain_timeout_seconds显式配置，默认30秒，超时保留删除阶段/额度，不强关仍写数据的worker。

原create已发出但没有可信sandbox ID时，RPC返回异常后的服务端仍可能执行；find零匹配保持unknown/占额，不能作为“创建已结束”的证据。至少发现并核验一个实例后，先持久记录其ID，再kill/get/find确认；丢kill ACK后可凭该已知ID继续核对。多匹配拒绝清理。删除管理准入有界预留max_retained_environments回执余量，普通max_receipts已满仍能删除已有任务；不无限绕过总上限。

清理复用resource_disk FD打开/mount身份，在固定root下仅处理tasks/id、artifacts/id、artifact-transfers/id与history/id.sqlite及自有WAL/SHM/journal/lock；先CT1关闭/移出历史缓存，不读Runner SQLite。条目/深度/截止复用resource_limits，失败可原ID重试，不自动chmod/chown。共享inputs/镜像保留，引用按非deleted任务确定。完成事务同时清历史/产物索引、凭证，旧同task回执脱敏为task_deleted（保留原ID/digest防重放），task最小身份墓碑及原删除成功回执。

本机复用现有command journal/producer/连接：binding下delete-preview读取可信远端预览；delete确认先持久固定请求再派发，离线/404保留unknown，显式retry才reconcile。允许确认删除越过旧unknown命令，但不取消本机父聊天。只有同instance/task/原ID/digest/generation且finished墓碑的成功回执，才在同ProjectStore事务记录成功、清理旧任务草稿/命令正文并更新binding JSON及SQL retained=false。已取回文件与主会话不动；父会话最终guard复用原事务，不加兼容入口。正式UI仍S06.4。

同一owner从其他客户端删除后，本机read会先核对deleted任务元数据与delete_command_id，再GET该原回执，由adopt_deleted在同事务严格核验/核销。单独deleted状态或404均不够。成功record后execute/observe直接返回终局，不再fresh访问已允许删除的父会话；旧命令脱敏后的GET/retry/迟到失败回写保持原终局，不复活binding。墓碑用严格DeleteTombstone拒绝非有限时间和布尔代际。管理数据删除为逻辑清理，不承诺底层介质安全擦除。

## 11. S06.3b本机实现与验收（2026-11-02）

真实主链为owner delete-preview/delete/reconcile→CloudDeletions原ID producer→DeletionRepository原子预留→TaskIO封口/排空→backend物理核验→历史缓存关闭/固定FD清理→同事务脱敏旧回执/索引/凭证/最小墓碑。local binding删除API→原command journal/producer/固定请求→严格原成功回执→同ProjectStore事务retained核销；其他owner客户端的删除走metadata原ID查询/adopt，未绕过原保护。主聊天/长期记忆和已取回文件保持。

新增38项行为回归，根专项（含原下载取消回归）39 passed，云端全套582 passed。完整后端命令`.venv/bin/python -m scripts.ci_shards --index 0/1 --count 2`分别3232/3509，共6741 passed，无fail/error/skip；原6703精确节点保留。前端69文件/568项、TS/Vite构建通过；四目标标准wheel/Twine及Mac源外安装通过，497个Python源逐包匹配，安装provider_requests=0。原ZIP负例及Vite chunk警告保持；pip check/compileall/diff检查通过。

独立架构/安全审查发现并关闭远端create在RPC失败后迟到、已关闭WS阻断物理删除、迟到SSE订阅、旧正文脱敏消费者、墓碑非有限值、成功核销后父会话删除竞态；相应确定性回归与真实owner/local路由集成通过。旧ArtifactDownload直接构造fixture迁移为显式task身份，原取消/真实read排空/close断言不变；下载清理fixture补真实模板固定身份，不放宽后端核验。TI1/CP6同步锁表，操作说明见[cloud-task-deletion](../cloud-task-deletion.md)，证据[verification](../../build/spec143-s063b-20261102/verification.json)/[review](../../build/spec143-s063b-20261102/review.md)。

真实Linux非root/bind mount/OpenSandbox和Windows/公司验收另列；本轮未操作真实开发机/Docker/用户数据，未修改真实配置/依赖，无提交/发布。源码与原脏工作按基线保护。现15单元完成6项、剩余9项；唯一下一片S06.4候选侧栏，无新增用户掌握证据。
