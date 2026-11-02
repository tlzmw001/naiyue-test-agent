# Spec143U：S06.2 手动任务管理与本机关联

状态：2026-11-02 S06.2a/b完成本机验证；下一片S06.3。首版入口固定为加号菜单/侧栏，不注册本机Agent创建云端任务或追加远端指令的工具。

## 1. 实施前基础与装配边界（截至S06.1b）

现有`CloudConnection/CloudClient`是显式loopback HTTP客户端；CLI已能导入、创建、启动、发送、停止/恢复与下载单产物，但没有本机主服务中的持久关联、SSH生命周期或侧栏适配。`TaskQueries`可复用任务查询呈现；`BackgroundProcessManager`是本机进程保管，不承担云端存活。现有子Agent等待其驱动完成，不能直接改名作为脱机任务。

本机现有Host/Agent/记忆继续使用；连接/任务模块附加在同一本机服务，Coordinator/Runner不复制本机Host，不引入第二运行框架。本机source_handle和远端input_id分离，云端永远不能指定本机路径。

| 单元 | 交付 | 不混入的职责 |
| --- | --- | --- |
| S06.2a | 私有profile/SSH连接管理、握手、持久关联、手动新建/准备/列表与读取 | 不自动生成任务提示词，不启动模型，不复制主会话上下文，不做结果应用。 |
| S06.2b | 首条/后续消息、停止/释放/恢复适配、重启/断线对账、运行与任务草稿身份 | 复用既有owner控制与恢复算法；结果下载/分析草稿归S08.1，完整数据删除/周期回收归S06.3。 |

## 2. 连接与配置

连接信息由本机私有profile提供：profile_id、已配置SSH Host别名、owner远端监听与本机转发端点、预期instance_id、认证令牌引用、默认模板和云端逻辑项目映射。敏感值只留私有配置/内存，不下发浏览器或任务容器；端口/地址无代码常量，不修改用户SSH文件、known_hosts或真实settings。

有现成隧道时可按profile复用；应用管理隧道时只启动本模块拥有的OpenSSH进程，使用参数数组及已有key/agent，不自动接受未知主机密钥/保存密码。端口被占、认证失败和主机身份不符明确报错。退出本机服务只关其连接/进程，不取消远端任务或杀用户既有隧道。业务反向中转与管理连接分别显示依赖，不把连接成功当预发布可达。

握手核对instance/protocol/capabilities后才开放变更；profile改指其他实例时不能重绑旧任务。不同profile指向同实例也不能凭项目同名推导身份。UI请求只选已配置profile_id，不接受任意URL/凭证/宿主路径。

## 3. 本机持久记录与并发

在本机项目的既有受控存储边界保存关联和操作记录，不复用A的environment/bundle表；不开云端SQLite。云端分组通过已有project API与本机project建立显式映射。首版云端任务入口要求已选项目/主会话；无项目时显示选择项目指引，不隐式上传任意目录。

| 记录 | 必须字段/不变量 |
| --- | --- |
| 任务关联 | binding_id、local_project_id/local_session_id、profile_id/instance_id/cloud_project_id、task_id、input_id、创建时间、关联版本、云端状态/游标缓存；task/input在对应步骤确认前可为空，不用占位ID，一经受理不可改绑。 |
| 本机操作 | command_id、binding_id、kind、请求摘要、固定步骤ID/回执、阶段、错误/时间；没有本机run/tool_call时不伪造。 |
| 接收索引 | 由S08.1补充固定result/material身份、校验与下载状态；不将“可下载”误作已应用/已分析。 |

用户操作先持久登记，再执行网络/文件IO；跨数据库不承诺原子提交。创建的各步骤ID在IO前固定，ACK丢失查原回执，不能换ID创建第二个任务。已受理正文和model_id连同远端revision/generation快照固定，重试不重新拼装另一个请求。

新增任务与删除本机会话共用持久准入/删除保护：进入本机长IO前预留该会话操作，短事务结束后再IO。删除不得穿过尚未完成的创建关联；创建完成也不能落到已经删除的父会话。实现时把实际锁/预留顺序同步到lock-hierarchy，不在锁/SQLite事务中等SSH或上传。

## 4. 本机API与当前云端API映射

以下为本机API合同，已接线项目见第8/11节；完整SSE侧栏适配仍归S06.4。均先沿现有本机鉴权/Host/Origin/实例检查，再校验项目/会话和关联。浏览器只见binding_id与脱敏状态，不能靠任意task_id穿透关联；operation响应带command_id以供对账。运维CLI仍可直接使用受权owner接口，但不等于模型工具。

| 本机接口 | 输入与行为 |
| --- | --- |
| `GET /api/client/v1/cloud-profiles` | 返回可用profile/模板和连接状态，不返回令牌、SSH私钥或真实配置正文。 |
| `GET /api/client/v1/cloud-tasks` | 必填local_session_id，校验真实项目归属；有界分页，运行中优先，能查询已保留的完成/停止任务。 |
| `POST /api/client/v1/cloud-tasks` | command_id、local_session_id、profile_id、source_handle、template_id；准备固定输入、云端task与环境，返回binding/操作记录。不得接收本机run/tool_call或自动生成首条消息。 |
| `GET /api/client/v1/cloud-tasks/{binding}` | 关联身份＋连接/准备/环境/运行/保存/结果分别呈现，不把多维状态压成一个成功布尔值。 |
| `GET /api/client/v1/cloud-operations/{command}` | 仅返回当前本机项目/会话持有的原操作；不创建新运行。 |
| `POST /api/client/v1/cloud-tasks/{binding}/messages` | command_id、text、model_id、预期关联版本；服务端持久固定真实StartRun请求后映射现有`/tasks/{task}/runs`。 |
| `POST /api/client/v1/cloud-tasks/{binding}/control` | command_id、stop/recover及原运行/检查点身份；适配既有`/control`，不复制恢复算法。 |
| `POST /api/client/v1/cloud-tasks/{binding}/environment` | start/release及预期版本；release先收束并确认保存，再适配stop_environment，未知须独立明确确认。 |
| 关联下的history/snapshot/stream | 只读转发143t接口；本机主会话API仍本机执行。二进制和SSE使用独立有界流，不调用当前会读完整JSON的`CloudClient.request`。 |

删除接口由143v、结果接口由143w定义，不能提前注册空实现。现有owner `GET tasks/{id}`、commands、runs/control继续作为唯一云端控制入口；本机层不获得容器后端管理权限。

## 5. 正常场景

加号菜单只打开面板。用户点新增→本机先存操作→复用已授权项目来源捕获/缓存输入→远端验证输入→原ID创建task→原ID启动环境→Runner就绪；主聊天不等待新任务执行，且新建不调用模型。各阶段真实显示；上传未完成关机后任务不会被误标为已接管执行。

用户在任务底部发送首条消息→本机固定StartRun请求并持久登记→Coordinator按现有CAS准入→Runner开始→本机可退出，运行继续。后续消息选原binding/task，保留workspace/state，不重传原项目、不新建独立任务；环境已释放先显示恢复准备并启动原任务环境。

无自动steering或隐藏待发队列：云端本轮执行中可编辑草稿，发送保持禁用并说明先等待或停止；停止/必要保存确认后发送新一轮。连接断开保留草稿；切换binding后旧回包不得覆盖当前草稿或输入目标。本机主输入框不受云端状态禁用。

沿当前StartRun.message合同，首版云端任务底部输入为文本；代码/proto通过固定输入导入。云端输入不显示未接线的图片上传控件，本机主聊天现有图片能力保持。查看已有产物/图片与给模型发送多模态输入不是同一能力。

## 6. 失败、重启与删除父会话

- HTTP返回丢失先查原命令；若不能证明操作未开始，保留unknown，不另发一次同义新命令。准备失败区分可重试阶段和必须核对状态；取消只处理所属操作，不销毁既有其他任务。
- 本机重启只恢复关联、操作和观察，不调用模型/发送草稿；SSH重连读取原task/command/history水位，不能重放Agent循环。
- 模型/应用失败复用143q显式recover；Runner崩溃先确认旧容器不能写，再恢复。未知Shell/RPC不自动重试。正常completed/stopped且保存确认后可发送新消息。
- 用户已确认：仍有关联未删除云端任务时阻止删除本机会话，包括已停止/完成但仍保留的任务。提示先取回需要的结果并处理/删除关联任务；首版不做自动转移或项目全局孤儿列表，不静默取消远端。后端业务层检查，不能仅禁用前端按钮。
- 本机项目删除及其它批量会话删除同样核对关联。云端删除完成的墓碑/回执确认后才允许解除保护；本机断网不能用陈旧缓存猜测已删除。

## 7. 验证和清理

### S06.2a 实施细化（2026-11-02）

- 私有配置由 `[cloud_tasks].profiles_file` 显式引用 JSON；没有配置时列表为空，不探测机器。profile 内引用现有 CloudConnection 私有文件，声明 external/owned 隧道模式、SSH Host 别名和模板集合。只读配置，不写真实 settings/SSH 文件。
- 新增 `POST /api/client/v1/cloud-sources?local_project_id=...`，正文为 local_session_id 和显式 Selection；可选择 Git 清单模式（include/exclude），由已有 select_git 产生 SourcePlan。服务端从已信任 LocalSource 解析根并捕获，返回 source_handle/input_id/大小，不返回宿主路径。仅 URL/ref 的 GitSource 首版要求先准备本机目录。source_handle 绑定项目和主会话，不接受任意 root。捕获不等于云端接管。
- 其余关联接口也必填 local_project_id，服务端从该项目的受控库解析 binding/command 并核对 local_session_id；不跨项目搜索任意 task。新前缀独立有界读体，producer 不依赖旧 `/api/v1` 内存回执中间件。
- 本机项目库升级到 schema 21，源句柄、目标映射、关联、操作与会话准入在同一受控库；最终会话删除在原写事务检查关联，项目 deletion_guard 同样检查。短事务后才开始 SSH/HTTP。来源捕获和准备复用已有项目 Host（会加载已配置的本机模型目录，但不调用模型）；本片不另造 Host 装配链。
- 创建返回 202 和原 operation/binding；服务端托管准备。重复 POST 同 command_id 只重用固定输入和步骤请求；GET 不产生变更。新增 `POST /cloud-operations/{command}/retry` 和 `/cancel`，仅在所属项目/会话内操作原准备；取消不销毁已受理远端任务，远端创建是否发生未知时保留删除保护。
- project/create/environment 的步骤 command_id 与环境完整 CAS 请求在 IO 前持久化；未知时只查原回执，重试不重造请求。操作成功仅代表环境准备，单独呈现 Runner 的 booting/waiting_model/ready，不承诺模型已经可运行。
- 本机关闭先封口并排空已开始准备，再关闭本模块自有 SSH；不发送远端 stop/delete。真实关机中断留下持久操作，重启只读恢复，由显式 retry 继续核对；S06.2b 再补统一离线对账。
- 项目在预留 deleting 前检查已保留关联，最终删除 guard 仍复查；拒绝后保持可管理。整库恢复不得倒退外部任务身份：当前库有 retained 关联时拒绝恢复；schema21 所需云端表/字段不完整，或当前库损坏/丢失但仍有云端输入缓存时，要求先做受控对账。既有纯本机损坏库恢复能力保留，不用旧备份猜远端不存在。
- history/snapshot/usage 的本机关联 JSON 读取已接线，使用已有 CloudClient 有界响应并核本机响应预算；完整线程/详情选择及 SSE 的侧栏适配在 S06.4 接入，不提前挂空流接口。原 Coordinator 的完整历史/SSE/CLI 仍可独立读取。

## 8. S06.2a 当前主链与验收范围

| 场景 | 真实入口、生产者与消费者 | 状态与副作用 |
| --- | --- | --- |
| 冻结来源 | `service.cloud_tasks.source → LocalTaskDomain.capture → select_git(可选)/capture_cached → LocalTaskRepo.add_source` | 根来自已信任 LocalSource，execution authority 前后核对；输入清单/原字节进入本机受控缓存，只向浏览器返回 source_handle/input_id/大小。源文件不改写，不产生 Git commit/bundle。 |
| 新增任务 | `create → ManualCloudTasks.submit → 独立 admit → repo.reserve → _launch → preparation.prepare` | LC0/父 history_lock 下短事务固定关联、请求和步骤 ID，再释放锁；HTTP 等待者取消不丢 producer。202 为本机接管准备，不冒充远端模型已执行。 |
| 远端准备 | `prepare → ProfileConnections/ConnectedProfile → CloudClient → 原 project/transfer/task/environment API` | 握手先核 instance/protocol/capabilities；上传复用 S03.2 缓存/续传/commit。原环境 CAS 请求、回执持久化；prepared 只说明后端输入准备，Runner ready/waiting_model 等单独显示。没有 `/runs` 请求。 |
| 回执丢失 | `repo.fail(unknown) → retry → 原 /commands/{id}` | 固定原输入、项目/任务/环境请求；环境未知沿既有 reconcile，不重建恢复算法。回执 command/operation/digest 先验证，再允许 reconcile。 |
| 取消与未知 | `cancel → repo.cancel → producer finally/settle_cancel`；`read/retry → reconcile_created(dispatch=False)` | 尚未派发 task 且 producer 已排空才解除保护。已受理/可能受理时保持关联；取消后仅查询原创建回执恢复 task_id，不再 POST task/start。明确的远端准入额度拒绝可取消，不留下虚构任务。 |
| 只读观察 | `task/history/observation → manager.read/view → binding 解析目标 → 原 owner API` | 主会话/长期记忆/运行记录不写入；列表来自本机持久缓存，单任务 GET 会查询远端并更新缓存。离线错误不擅自移除关联。 |
| 本机关闭 | `ScopeServices._shutdown → manager.close → operations → flights → connections.close → Application.aclose` | 不关闭在途读取的客户端；退出的 owned SSH 退休后可重建，旧客户端等借用归还。external 隧道不接管，本机关闭不发 stop/delete。 |

确定性入口为 `test_manual_task_bindings.py`、`test_manual_cloud_profiles.py`、`test_manual_task_preparation.py`、`test_manual_task_routes.py`、`test_manual_task_integration.py`、`test_cloud_session_delete_guard.py`。集成测试使用真实 Coordinator API、分段上传/commit、归档安装和受控 Backend，验证同一输入创建两个独立工作区；不等价于真实 Docker、Windows SSH 或公司预发布网络验收。

清理：私有 JSON 读取收敛到 `cloud_files.configuration.read_settings`；CLI 的 serve/input/task 及对应测试迁入同一实现，旧 `cli.cloud.load_settings` 定义/别名/消费全部移除。schema21 同步迁移/备份检查/RAM profile/历史 schema 夹具，保留旧行为断言；A 正式消费者尚未被本片替代，按 S09 删除，不提前保留或新建 A/B 兼容层。

本片证据根：`build/spec143-s062a-20261102/`。最终全量、标准 wheel 与源外安装结果写入该目录 `verification.json`，原失败/修正日志保留。下一片只做 S06.2b 的消息、停止/释放/恢复和重启对账；资源删除仍 S06.3，正式侧栏仍 S06.4，结果接收仍 S08.1。

## 9. S06.2a 验证结果（2026-11-02）

| 证据 | 实际结果 | 证明边界 |
| --- | --- | --- |
| 本片六个 focused 测试文件 | 28 passed | 私有配置/握手、来源/身份、原ID准备、取消/额度/关闭竞态、删除和旧备份保护、真实 owner API 导入/安装/双工作区。 |
| `scripts.ci_shards --index 0/1 --count 2` | 3191＋3443＝6634 passed，0 failure/error/skip；一条既有重复 ZIP 负例警告 | 当前全部后端节点。新增32项覆盖；旧未知版本参数21顺延为22，其余6601精确节点保留，无行为删除。 |
| 前端测试与标准构建 | 69文件/568 tests passed；TypeScript/Vite 构建退出0 | 正式本机/A界面回归；本片未开放正式云端侧栏。既有大 chunk 提示保留。 |
| 四目标标准 wheel / Twine | macOS ARM64、Linux x86_64/ARM64、Windows x86_64 全通过 | 四包485个Python源逐字节匹配，application hash一致；Linux/Windows此项为交叉构建，不是原机运行。 |
| Mac源外全新venv安装 | 标准安装/产品烟测通过，provider_requests=0 | 包完整性与当前本机功能；不替代Windows SSH、Docker或公司网络。 |
| 独立只读架构/安全复核 | 无剩余阻塞，关键竞态和结构损坏恢复均有探针/回归 | 不以源码复核替代实机门禁。 |

未提交、发布或部署；没有改真实配置、依赖、用户数据或 Docker 资源。已完成组件的存在/实现/接线均通过真实本机服务入口核验；下一片 S06.2b 不自动开始。

主要影响本机service/scopes/装配、受控存储与会话删除、cloud_files连接模块、现有cloud客户端/控制API；超过8文件。优先扩展现有profile验证/任务查询/HTTP鉴权和消息保存，不复制A SandboxChannel或Docker代理。角色/字段、schema迁移、API、DTO和测试同步；没有真正接线的API不出现在能力列表。

拟测试`tests/cloud/test_manual_task_bindings.py`、`test_manual_task_commands.py`、`test_manual_task_restart.py`、`test_cloud_session_delete_guard.py`。分别覆盖无模型创建/首条发送、同源缓存、额度失败、ACK丢失、跨会话/实例目标、准备取消、旧回包/草稿、父删除与创建竞态、本机关闭但云端继续、停止/恢复和私有配置脱敏。真实Windows OpenSSH/文件系统另做原机证据，全量/构建沿143g；不以ASGI替身宣称公司链路已通过。

## 10. S06.2b 实施细化（2026-11-02）

- 在既有项目库 operation 表保存 message/stop/recover/start/release 台账；binding 增加独立 draft_revision/draft。请求固定 binding、instance、task、正文/model、用户预期关联版本和完整远端 CAS；复用现有表，不新建云端记忆或伪造主会话 run。元数据未变化的读取不推进 binding revision。
- 新增关联下 `POST messages/control/environment`，正文带 local_session_id/command_id/expected_binding_revision；message 带 text/model_id、可选 draft_revision。重复ID先核原请求摘要，不因当前版本变化重新拼正文或CAS。返回202是本机接管派发，operation中的远端 receipt 区分 running/accepted 与必要历史确认的终局。
- messages 在已释放环境下，固定独立 env-start 步骤ID，启动原任务环境；Runner ready 后才冻结 run CAS。等待有界且显示 waiting_runner，超时后仅由用户显式 retry 继续，没有忙碌任务的隐藏消息队列。模型缺失、忙碌、保存未确认时不发送新 run。后续消息复用原 workspace/input，不重传文件。
- stop/recover 复用现有 TaskControl；recover 只整理 actor/检查点/必要状态，不自动执行模型。release 不隐式代发 stop：活动任务先明确停止，必要保存和静止确认后才能释放；否则返回可操作的 busy/保存阻塞状态。未知强制释放必须显式 acknowledge_unknown 且固定 expected_generation，防止确认后换代误杀。
- 派发边界前持久登记，HTTP waiter 取消不丢派发；任务通道与控制通道分别有界，stop 不等待整轮模型。message 在派发中/未知而没有可核对的 run 身份时，stop/release 不得凭一次 active_run_id=null 报成功。最终 CAS 仍由 Coordinator 准入。
- 新增 `GET binding/operations`（有界分页）发现重启前原命令，`GET operation`/显式 reconcile 仅查询原远端命令并更新本地台账；404保留unknown，不自动重发消息/草稿。显式 retry 仅继续尚未派发的步骤；已派发消息只查原命令，stop/recover或环境步骤可沿既有同ID对账继续，不换请求。
- 新增 `GET/PUT binding/draft`，以独立 draft_revision 做CAS并返回 binding_id/local_session_id。发送回执仅在匹配提交的草稿版本/正文/model时清空；晚回包不能清掉后续编辑或其他任务草稿。前端选中目标/旧回包呈现归S06.4，不在本片提前改UI。
- 本机 shutdown 排空短派发/读取，不等待远端模型完成，不发stop/release；启动本机服务仅恢复台账，首次查看按原ID只读对账。完整删除归S06.3，结果和分析草稿归S08.1。
- 对外operation/操作列表返回有界身份/状态摘要，正文、冻结payload和完整receipt留受控台账；列表先分页ID、逐条读取，终局不重复访问远端，最终JSON核本机字节预算。长正文从正式云端历史读取，不在重连列表重复复制。
- 强制release除generation外，还冻结确认时缓存的cloud revision/active_run；同代新工作也必须重新确认。Runner等待的剩余期限传给实际HTTP GET。producer失败时先排空其他派发再关连接，不覆盖已受理结果。

## 11. S06.2b 当前主链与验收范围

| 场景 | 实际调用与数据 | 行为边界 |
| --- | --- | --- |
| 发送 | `service.cloud_tasks.message → ManualTaskCommands.submit → LocalTaskCommands.reserve → launch/command_dispatch.execute` | 原用户正文/model、binding/instance/task和期望关联版本先登记，再网络。独立admit不随HTTP取消丢失；不注册Agent自然语言工具、不写主会话。 |
| 受理 | `fixed → journal.fix/dispatch → CloudClient POST runs → RunnerRuns.start → validate → journal.record` | 完整StartRun/摘要/派发标志在IO前固定。running/202映射accepted；必要历史之后的owner succeeded/failed才是终局。正文/model/command/resource/generation/digest逐项核对。 |
| 环境已释放 | `execute → 独立environment步骤 → 原CloudLifecycle start → 有界等待Runner → 固定run请求` | 原workspace/input保留，不再上传。等待失败只显示waiting_runner/错误，重启不自动投递；用户明确retry才继续未派发的运行步骤。 |
| 停止／恢复／释放 | `control/environment → TaskControl/EnvironmentCommand → 既有owner服务` | stop有独立控制容量；无已知run身份时不能越过未决派发。recover只整理actor/检查点，不新建模型run。release要求先明确停止和保存/静止确认；强制确认固定所见代际/版本/active run。 |
| 断线/重启 | `GET binding/operations → journal.index → manager._observe → GET commands/task` | ID分页发现原命令，逐条只读核对并返回摘要；不需要客户端记住重启前内存里的command ID。404保留unknown，消息不会再次POST。显式控制/环境retry也不重拼payload。 |
| 草稿 | `GET/PUT binding/draft → journal.save_draft`；受理时`record → _clear_draft` | 独立draft_revision；只清除匹配原版本/正文/model的草稿。后续编辑、另一binding和主输入框不受旧ACK影响。正式UI的目标呈现归S06.4。 |
| 关闭/故障 | `manager.close → operations → 所有创建/命令flights → connections.close` | 短派发结算后退出，不等远端整轮模型。单一producer异常不能抢先关闭其他producer的连接；主异常排空后传播。 |

明确拒绝与未知分别保存：owner准入前的环境/命令额度拒绝不会伪装成unknown；保留原拒绝，新操作用新ID，不能在原ID下换CAS。回执演进不依赖`updated_at`墙上时间排序：unknown不被晚到running覆盖，可信终局可在时钟回拨时仍收束；终局不可倒退。

新增专项入口：`test_manual_task_commands.py`、`test_manual_task_restart.py`、`test_manual_commands_routes.py`、`test_manual_commands_integration.py`。真实owner API集成使用受控Backend和Runner协议输入，覆盖发送、停止两份必要历史确认、释放、原工作区重启后再发及输入只上传一次；不是实际Docker/供应商/Windows验收。真实本机服务测试验证所有新API接线、鉴权/实例/父归属、有界摘要和草稿CAS。

共享与清理：沿用原profile/HTTP/owner运行控制/环境恢复/输入安装，不复制A通道或新建恢复框架。新增叶模块分别承担受控命令台账和本机短派发，现有manager统一拥有生命周期；操作索引使用真正分页ID查询，没有保留全量正文列表旧入口。未替代的A正式链仍按S09退出。

证据目录：`build/spec143-s062b-20261102/`；最终全量/构建/安装见`verification.json`，原红灯、修订和独立复核记录保留。下一片为143v S06.3周期维护、容量与任务删除；正式候选侧栏仍S06.4，文件接收/分析仍S08.1。

## 12. S06.2b 验证结果（2026-11-02）

| 证据 | 实际结果 | 边界 |
| --- | --- | --- |
| 四个新增专项测试文件 | 24 passed | 原请求/草稿CAS、接受与终局、停止/恢复、派发/关闭/容量交错、时钟回拨、明确额度拒绝、重启只读与长正文预算；真实本机和owner API接线。 |
| 当前全部后端 | 3186＋3472＝6658 passed，0 failure/error/skip | 原6634精确测试节点全部保留，新增24项。保留一条原有ZIP重复名负例警告。 |
| 前端回归/构建 | 69文件/568 tests passed，TypeScript/Vite退出0 | 未提前开放正式侧栏，原有chunk体积提示保留。 |
| 四目标标准wheel/Twine | macOS ARM64、Linux x86_64/ARM64、Windows x86_64全部通过 | 每包488个Python源与最终代码逐字节相同，application hash一致；交叉构建不代替原机运行。 |
| Mac源外干净venv安装 | 标准安装/产品烟测通过，provider_requests=0 | 不代表真实公司网络/Windows SSH/最终Docker容器通过。 |
| 独立只读架构/安全复核 | 无剩余阻塞；clock尾改有单独复核 | 操作摘要5041625→28468字节的独立探针及必要历史集成见review；新容量拒绝回归同样保留红绿日志。 |

本片不提交、发布、部署，不修改依赖、真实配置、用户数据或Docker资源。下一片S06.3先接真实周期维护/容量，再核验删除状态机和父会话保护核销；不自动进入下一片。
