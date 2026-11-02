# Spec143J：S01 合同、持久回执与鉴权底座

日期：2026-11-01。状态：**用户已批准实施，S01实现与专项验收通过；最终全量/构建结果见第13节。** 总方案[143](143-cloud-task-b.md)，装配前置已完成[143i](143i-runner-assembly-spike.md)。本片收尾后讲S02方案，不自动跨片编码；下面第1—12节保留已批准合同，实际入口以第13节为准。

## 1. 本片目标与边界

先建立独立Coordinator控制底座：身份/目录、持久命令回执、所有者与任务权限、任务元数据和最小历史接收。可验收闭环为“所有者创建逻辑项目→断线或服务重开→用原命令ID查到唯一项目和原回执”；同时用隔离夹具核对任务身份与历史投递。现有A正式入口保持到S09，B没有用户可见A/B开关。

本片不创建Docker容器/Runner，不请求模型，不上传项目文件，不实现完整聊天查询/记忆/模型代理/镜像更新或前端。尚未实现的POST tasks/运行/审批执行/上传路由不注册，不返回假的accepted/ready或空实现。S02接后端、S03.1接最小输入后再启用相关能力。

本片可在当前机器用临时根、真实SQLite和ASGI请求验证，不依赖开发机、模型key或Docker。原S00按用户报告通过；新增实机项按143h分项在S02实际后端、S04 Runner、S05真实模型接入前验证，不把未使用的外部依赖当成本片离线合同测试的阻塞，也不追认为已通过。

## 2. 已核对的来源与采用理由

| 来源/固定位置 | 实际调用与异常路径 | 本片处理 |
| --- | --- | --- |
| A：`service/receipts.py:IdempotentCommands → CommandReceipts.execute/_produce` | 鉴权先于缓冲/占用ID，同ID同摘要复用async task；entries是内存dict，HTTP调用方断开不取消producer；有界窗口满拒绝 | 继承先鉴权、摘要冲突和不随HTTP取消；不把内存cache当B的重启幂等实现 |
| A：`storage/sandbox_operations.py:reserve/get/save`及`environment_registry.py` | 在registry短事务中查原ID/摘要、预留资源，unknown跨重开保留，未确认资源继续占额 | 复用持久事实、CAS/准入与unknown语义；不复制binding/bundle表或调用A迁移器 |
| A：`web/auth.py:authorize`、`service/ownership.py:ServiceOwner`、`storage/files.py` | Host/Bearer/Origin显式校验，私有目录/安全FD/不可继承OS锁；锁所有权不由残留文件判断 | 复用已有检查/文件原语，B新根/数据库格式/所有者生命周期独立；任务入口另做作用域鉴权 |
| Pi `a69bef789bc95abf0acee16f7b4660b70b650bb9`：`rpc-mode.ts:handleCommand(prompt)` | 从command.id构造response；prompt preflight成功才输出success，之后异常不伪造第二个初始响应 | 保留接收/执行完成区别；这条RPC路径不构成本产品持久幂等证据，不移植stdio协议 |
| AgentX `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`：`ConversationAppService.prepareEnvironmentWithModel → SessionDomainService.getSession`，历史入口→find | 数据查询同时约束sessionId/userId，不存在抛BusinessException；不只检查路径里有ID | 服务端从凭证推导task/project，再核对资源；不引入Java运行时或多用户平台 |
| OpenSandbox `a6c0cfc`：`middleware/auth.py:dispatch/_authenticate_single_tenant` | 单租户无key配置可跳过鉴权；管理key不是本应用task/generation凭证 | B缺所有者凭证启动失败，所有任务请求显式验证；不照搬无key跳过逻辑 |
| ZCode `a60ba6a`：ConnectionManager→adapter.installOrUpdate→ensureDaemon | 发行包校验/私有staging与运行服务连接分阶段；不能把安装完成当会话完成 | 镜像准备与Coordinator登记分开，详143c；不在S01混入下载/部署职责 |

Pi/AgentX本轮沿相关生产分支核对，非仅引用名称。当前环境LangChain1.4.2/LangGraph1.2.12、FastAPI0.143.0/Starlette1.7.0、Pydantic2.13.5；S01不修改Agent图或升级依赖。FastAPI/async context资源管理与SQLite事务沿[官方生命周期](https://fastapi.tiangolo.com/advanced/events/)、[事务说明](https://www.sqlite.org/lang_transaction.html)核对；不自建RPC、ORM、JWT或新的后台调度框架。

## 3. 数据流与资源拥有者

```text
open_cloud_runtime(显式CloudSettings)
  → 私有新根/实例OS锁 → cloud.sqlite + history目录 → CloudRuntime
       ├─ owner_app：逻辑项目、命令回执、任务元数据、历史读取
       └─ task_app：单任务身份、必要事件接收

所有者请求 → Host/Origin/Bearer/协议/实例检查 → 规范参数
  → 查原command_id → 原回执 或 短事务创建项目＋回执 → HTTP响应

任务请求 → 任务凭证上下文 → task gate内重核当前代/有效期
  → 自有history库：事件＋接收游标同事务 → ACK
```

两个FastAPI app显式注册不同路由，共享同一个已打开CloudRuntime和实例锁。运行入口只打开/关闭一次资源；S02再接两个真实监听，不启动两个各自拥有同一数据根的独立进程。测试以相同runtime和ASGITransport调用真实路由，不靠直接调用未接线函数验收。app工厂不暗中调用现有create_service_app/open_service_application，也不建Agent。

实施细化：runtime用CT0条件变量仅登记在途本地请求/封口，关闭先拒新请求、排空已登记工作线程，再关history/registry和所有权；CT0不跨DB IO持有，也不与CT1嵌套。客户端取消不能使仍在提交的线程使用已关闭的数据库。已ready根丢失registry不自动重建，仅合法initializing状态可恢复未完成初始化。

复核细化：ready的registry缺失/零字节均拒绝初始化。registry.meta保存每个history的initializing/ready登记，先登记初始化、创建并核验自有history，再登记ready；已登记ready的history缺失/空文件/身份或游标不一致须对账，不能自动重建并换epoch。两个库仍不是原子事务，初始化中断只按这两个阶段恢复；事件ACK仍在history事务提交后返回。历史页同时受条数和max_frame_bytes预算约束，回滚批次错误只报告事务前已确认上界。

## 4. 新根、配置与SQLite格式

CloudSettings显式传cloud.data_dir、owner_listen/task_listen、所有者SecretStr及协议/额度；不自动加载真实.env或改写当前settings。数值预算沿现有ServiceOptions的已确认起点：max_frame_bytes=1MiB、max_receipts=4096，暴露为B配置且严格校验；新增history_batch_events=128、json_depth_limit=32、history_page_limit=100和task_token_ttl_seconds必须配置可调，限额不是线协议常量。地址/端口不硬编码：owner仅loopback，task只允许已配置专用bridge地址；测试用虚拟authority不实际开端口。

实施中的历史读预算为history_read_timeout_seconds，默认5秒、配置可调；SQLite progress handler触发时返回history_read_timeout并复原处理器/事务，不将其解释为任务执行失败。条数/字节限制并用，此预算不承诺中断操作系统内阻塞的磁盘IO。具体监听/密钥只从显式配置取得，测试地址仅用于ASGI夹具。

专用根必须为空或有合法B身份。拒绝A/普通本机根、未知非空根、链接/硬链接别名、宽权限或类型错误；调用方给出的保护根与B根不得重叠。不扫描/迁移A数据。初始创建前核对根、取得不可继承OS锁；以独占写入的`cloud-root.json`记录format/instance_id/schema与initializing/ready。崩溃重开只对有本实例合法初始化标识且DB格式一致的未完成步骤对账，不能凭一个目录存在就接管；旧格式拒绝且不执行迁移/写marker。

根与管理目录归服务身份、0700；DB/marker/lock文件0600且普通单链接文件，核对父目录/sidecar及安全FD。文件锁复用storage.files，不因锁文件仍在就认定服务在线；不使用或暴露旧发现文件/所有者token。

复用已有Database的资源栈/RLock/transaction：新增`Database.open_cloud(path, profile=registry|history)`，只允许两种内部固定profile，不调用initialize_store/initialize_registry或配置项目FTS/记忆。对应initializer放storage/cloud，设置独立application_id/user_version=1、WAL、synchronous=FULL、foreign_keys=ON；现有签名/本机入口不变。现存DB先以只读检查验证实际连接的格式/版本，再做允许的初始化，不因打开错误格式而执行A迁移。源码依赖检查防止新B导入envd/沙箱通道/bundle合同。

| 位置 | S01实际记录 | 权威与约束 |
| --- | --- | --- |
| cloud.sqlite/meta | format/schema/instance_id | 新安装稳定实例身份，重开不换；协议版本与包版本分别存/比较 |
| cloud.sqlite/projects | project_id UUID、name、revision、created_at | 纯逻辑分组，无本地绝对路径；创建与命令回执同事务 |
| cloud.sqlite/tasks | task_id=根session_id（32hex）、project_id、input_id（64hex）、template/release/state_schema、generation/revision、lifecycle等143a字段 | 内部仓储由后续S02准入调用；S01测试用真实记录夹具，不注册假创建/启动API、不写sandbox/bundle字段 |
| cloud.sqlite/commands | UUID command_id、资源归属、operation、expected_generation、request_digest、status、结果/错误码、时间 | 持久幂等；状态accepted/running/succeeded/failed/unknown，不能用缓存淘汰使原ID重新执行 |
| cloud.sqlite/task_credentials | credential_id、随机token的SHA-256、task/project/generation、用途集合、expires_at、revoked_at | 不存token原文；只有控制面内部发行/吊销方法，任务路由不能mint或扩大用途 |
| history/<task_id>.sqlite | meta(task_id/epoch/last_seq)＋events(epoch/seq、source_generation、run_id、kind、payload_json/digest、接收时间) | Coordinator拥有、任务不挂载；接收事件与游标同一history事务，registry与history不声称跨库原子 |

input/result/release身份只定义已确定的类型与引用，不预建未被本片消费的传输/更新表或空仓储。完整聊天/用量/审批视图在S04/S06接入；S01的history提供有界持久事件台账与读取，不宣称已有完整会话恢复。

## 5. 鉴权合同

所有者请求复用web.authorize的Host/Origin/固定Bearer检查及常量时间比较；X-Service-Instance若提供必须匹配，变更请求另带X-Cloud-Protocol=1并拒绝不兼容。GET instance完成已鉴权的初次协议发现，不要求客户端预知instance_id；响应只有实例/协议/版本与当前已接线能力，不返回根路径/凭证。用户权限始终在业务路由可见地校验，不仅靠中间件。

任务token采用`secrets.token_urlsafe(32)`生成的随机不透明凭证，服务器只存摘要，不引入JWT依赖。绑定task/project/generation、用途和有效期；用途至少分identity:read、events:append，S04/S05再启用已有合同的control/model/memory用途。token原文仅在内部bootstrap/续发交接返回，repr/日志/argv/异常无原文，落地沿143a单文件RO挂载，不返回给浏览器。过期/吊销和数据库错误按拒绝处理，不降级匿名。

task_app不注册owner路由，也不因提交一个owner Bearer就接受为task；owner_app不接受task token。任务URL id、envelope task_id与凭证绑定三者相同；project从凭证/登记推导，不接受请求扩大。每次具有副作用的本地提交在task gate内重核generation/凭证/状态，防止校验后与吊销/换代交错。早到/晚到错误、未知资源均使用稳定错误码，不泄漏其他任务内容。

任务同UID可读任务token的限制保持143a第8节；随机token不是同任务进程隔离机制。任务事件只是该任务的数据，不能写所有者审批/命令终局、改变task.lifecycle、令牌scope或注册发行；这些由后续所有者控制路径确认。

## 6. S01真实接线的最小接口

| 入口 | 本片真实行为 |
| --- | --- |
| owner `GET /api/cloud/v1/instance` | 已鉴权实例/协议/版本/已接线能力 |
| owner `GET/POST /api/cloud/v1/projects` | 列逻辑项目；POST严格请求为command_id/name，创建项目＋成功回执同事务，返回201及固定project/receipt |
| owner `GET /api/cloud/v1/commands/{command_id}` | 查原持久回执，未知ID404；请求断线/重启后不复制执行 |
| owner `GET /api/cloud/v1/tasks/{task_id}` | 返回真实登记元数据，未登记404，不假造容器ready |
| owner `GET /api/cloud/v1/tasks/{task_id}/history/events` | epoch/after_seq/limit分页读取自有台账，返回页上界，短事务；epoch不匹配409，后续S06扩展snapshot消费 |
| task `GET /internal/cloud/v1/tasks/{task_id}/identity` | 校验task token后仅返回绑定的task/project/generation与已确认历史游标，不返回凭证/路径 |
| task `POST /internal/cloud/v1/tasks/{task_id}/events` | 按第8节持久接收/去重/ACK；不执行模型、工具或所有者命令 |

所有请求严格extra=forbid、枚举/ID/体积校验，错误为`{error:{code,command_id?}}`，不返回原始IO/SQL/上游正文。API的功能说明与capabilities只广告上述已接线能力；CLI/Web正式入口、WS控制通道及其他internal路由留到各自切片，S01不注册占位处理器。

## 7. 持久幂等、CAS与崩溃

摘要由规范化后的operation/资源ID/目标generation/有效payload计算；不包含Authorization、传输请求ID、连接时间等易变字段。command_id是应用操作ID，X-Request-ID只用于请求追踪，不自动当成同一语义；旧A的request_id/binding格式不兼容解析。

顺序固定：鉴权/请求形状与大小检查→查原command_id/摘要/归属→若相同返回原回执→若不同409 command_id_conflict→新命令才验证revision/generation和额度→预留/必要本地副作用→提交回执。重试不能先因当前revision改变而拒绝已经成功的原命令。

项目创建在一次registry写事务里插入项目和succeeded回执；重复同请求返回同project_id、created_at、结果与HTTP状态，不新建项目。并发同ID仅一个事务赢，不用“先查再无约束插入”的内存判断。task元数据CAS使用WHERE revision/expected_generation，变更行数不为1即冲突；generation更替还必须由S02确认旧容器停止，S01的CAS不替代物理写入者隔离。

崩溃在commit前则本地项目/回执一起回滚；commit后响应丢失则原ID查回。存储提交本身不确定时以原ID查询/重开核对，不换ID重做。未来外部操作先持久accepted，不能把外部容器/模型动作放SQLite事务；重启保留accepted/running/unknown给对应切片对账，不通用重放或自动标失败。

达到回执额度先拒绝新命令，原ID仍可查/去重；本片不静默删除已接受记录或实现泛化GC。metadata/result/payload均有配置预算，数据缺失返回真实空态/404，不用示例记录补位。

## 8. 历史接收、换代与两库并发

批次形状固定为task_id、generation（当前投递者）、epoch、events；每事件为seq、source_generation（生成时的代）、run_id、kind与payload。kind首版只接受history_record和run_observation；payload是有界规范JSON数据（非可信指令），不解释为权限或生命周期动作，禁止NaN/Infinity/重复JSON键，深度/总字节/条数受配置约束。S04/S06在既定台账上接现有最终回复/运行DTO的语义投影，不复制第二套模型消息格式。

epoch代表该任务持久事件日志，不因每次HTTP连接/run/Runner换代自动重置；初次seq从1开始，后续连续。当前generation必须匹配token与registry；source_generation允许为不大于当前代的历史来源，因此新Runner可用当前凭证补发旧outbox。旧代token/连接仍拒绝；旧来源数据不因此取得当前命令/审批权限。丢失/损坏日志导致epoch变化须显式对账，不静默跳过缺口。

同epoch/seq同摘要重复返回原ACK；不同摘要409，序号跳跃409并返回已确认上界；一个批次冲突则整体不提交新条目。history事件＋last_seq在同事务提交后ACK；先读最大seq再开长流不算原子衔接。读者返回该短事务的页上界，SSE在S06另用cursor续接。

拟CT1为每个已登记task的本地门闩：credential吊销、generation变更、历史append都先取得同一CT1，再分段取得registry/history短DB锁；不能从DB反向获取CT1，也不同时持两库事务。任务鉴权初查不分配任意ID锁，提交前在CT1内重核；generation变更尚未实施外部停止时不能靠CT1宣称旧容器已停。无网络/模型await在CT1或DB事务内；同步SQLite放工作线程执行，不在事件循环阻塞。

锁的持有者、获取入口和反向路径在实现同片写入docs/lock-hierarchy.md；本方案不把尚未存在的锁标为当前实现。两个Coor进程争同根由OS锁拒绝，不依赖仅进程内CT1保证全局单写。

## 9. 正常与失败例子

正常项目命令：owner带command_id=C创建项目→鉴权→事务产生P/成功回执→HTTP丢失→重启同实例→同C同body得到同P/回执。task场景用夹具登记T/g=1并发行仅events用途token→提交E/seq1→落盘ACK1→重开发送同事件仍ACK1→owner读取恰好一条；任务库被换FIFO/链接不影响查询，因为整个链不打开tasks下任何SQLite。

失败：同C改name返回409且无第二项目；伪造task_id/跨代/过期token在历史事务前拒绝；两个并发同序号不同payload只能一个成功，另一个冲突。g=2吊销旧token后，g=1请求被拒；g=2可补发原source_generation=1的同epoch连续事件。任务payload声称approved或succeeded，只作为不可信数据，不能修改owner记录。SQLite提交失败保留稳定错误码/原ID，日志不输出请求正文或key。

## 10. 文件范围、复用与清理

预计涉及十余个小模块及测试，按职责拆分且每文件≤500行；不是一个单文件改动，也不新增独立网络服务或依赖。实施前再核对实际消费者，文件名可以按职责细分但不得改变本片合同。

| 范围 | 拟定拥有者与真实调用方 |
| --- | --- |
| contracts/cloud_tasks.py、cloud_events.py | ID/状态/回执/批次Pydantic合同；仓储/应用/路由/测试共同使用，无A DTO兼容分支 |
| application/cloud/settings.py、runtime.py | 显式配置、单所有者context/资源栈；app工厂和后续S02运行入口消费 |
| storage/database.py新增open_cloud；storage/cloud/schema.py、registry.py、credentials.py、history.py | 复用既有事务/关闭算法，独立格式和实际仓储；全部SQL在storage，应用层保留摘要/权限/代际编排；禁止打开旧项目库/执行A迁移 |
| application/cloud/commands.py、credentials.py | 直接项目命令与任务凭证/代际检查；不建万能command registry、handler插件或任意回调层 |
| service/cloud/app.py、auth.py、routes.py | 两组app和显式业务鉴权/真实路由；后续超过500行再按owner/task职责拆，不做无消费者转发包装 |
| tests/cloud/test_contracts.py、test_auth.py、test_commands.py、test_history_ingress.py、test_storage.py、test_admission_races.py | 真实SQLite/ASGI/进程锁/确定性并发与故障断言，测试夹具可登记任务但产品不造示例数据 |
| docs/lock-hierarchy.md、143系列/handoff | 实现时同步真实锁、入口、结果与删除/保留消费者 |

普通本机仍消费web.authorize、ServiceOwner、Database.open_registry和原内存HTTP回执，本片不删它们。B中若出现临时重复入口，在同片正式接线后删除；A正式运行退出仍在S09，不先删断当前产品。只复用通用文件/事务原语和确实匹配的算法，不让新B依赖旧sandbox/bundle生命周期。

## 11. 可验证实施单元

1. 写失败测试并实现合同/新根/独占格式和数据库重开；错根/旧库/第二owner均不产生越权副作用。
2. 写失败测试并实现显式所有者鉴权、任务credential发行/校验/吊销与两组app接线；真实ASGI负向请求在末尾检查DB/文件无变化。
3. 写失败测试并实现项目创建＋持久回执/CAS；并发同ID、重启、commit前后故障、响应丢失、额度达到后的原ID查询全部有断言。
4. 写失败测试并实现history台账/ACK/分页；epoch/重复/缺口/摘要冲突、跨代补发、吊销并发及tasks目录恶意替换不影响管理域。
5. 从app入口跑正常/失败闭环，检查新B禁导入A、无模型/容器调用、配置/数据库/序列化全链一致；迁移清理和正式构建回归完成后关闭S01，再讲S02。

以上为一个S01切片内的小单元，进入批准实施后连续推进，不逐函数请求许可。故障/并发用Event/Barrier或事务提交点同步，不依赖极短sleep；检查主异常、必要事件、副作用与失败降级，不能只断言返回码。

## 12. 验收与退出条件

专项拟命令：`.venv/bin/python -m pytest tests/cloud/test_contracts.py tests/cloud/test_auth.py tests/cloud/test_commands.py tests/cloud/test_history_ingress.py tests/cloud/test_storage.py tests/cloud/test_admission_races.py -q`。这些测试在实施时创建，本轮不能报告为已通过；无全量/标准构建证据不能提交或宣称产品切片完成。提交门禁沿143g现有两分片后端、前端/标准构建/安装/pip check，不新增发布流程。

本片成功的L1/L2/L3证据必须来自真实app路由→应用命令→SQLite及重开，不以仓储函数存在代替接线。缺真实模型/容器不是本片失败，因为它们不在能力声明中；S02/S04/S05仍要各自门禁。首版依赖的最脆弱前提是单机本地磁盘＋单Coordinator所有者；第二owner由OS锁拒绝，网络共享盘不受支持，目标机挂载类型在S02部署门禁核实。S01不宣称能通用识别所有网络挂载，不扩展多节点数据库来绕过。

回退仅丢弃本片代码/测试夹具或停用专用B候选入口，A正式入口/真实数据未迁移；真实B根不自动删除/回滚，配置与部署另确认。任何外部动作尚未接线不能伪报执行完成。

前一轮只读方案证据在`build/spec143-s01-plan-20261101/`；实施结果单独记录如下，不能将两轮证据混为产品验证。

## 13. 实际接线、验证与限制（2026-11-01）

真实打开入口为[open_cloud_runtime](../../naiyue_test_agent/application/cloud/runtime.py)，应用入口为[create_cloud_apps](../../naiyue_test_agent/service/cloud/app.py)；两个app共享一个runtime，不接正式Web/CLI或真实监听。显式新根/安全文件原语、独占OS锁、cloud格式DB、项目/任务/回执、摘要凭证、CT0排空/CT1提交与有界历史均有实际消费者和测试。

正常例子的真实函数链：owner请求→owner_authorize→CreateProject→线程内runtime.operation→CloudCommands.create_project生成摘要/DTO→CloudRegistry.commit_project短事务写项目/成功回执→201；关闭/重开后原ID在仓储事务中先查原回执，同摘要返回原project/时间/result。任务事件→task_bearer→authorize→受限JSON/EventBatch→append_events→CT1内重核当前凭证/批次代际→_history对账初始化登记→CloudHistory.append一次事务写事件/last_seq→ACK；重复只ACK，不插入第二条。

失败例子：同ID改name在registry事务中409且无新项目；提交前故障回滚项目与回执，提交后ACK丢失以原ID查回。批次冲突整批回滚，错误只报告原已确认上界；当前代不符在创建history之前拒绝。管理库丢失/空文件/实例元数据丢失、已登记history丢失或游标不一致均拒绝自动重建，任务SQLite换FIFO/链接不进入本链。HTTP取消后已登记线程继续完成，关闭排空后才释放DB/根；清理次要异常记录但保留主异常身份。

本片新增7个专项测试文件及独立B夹具，78项通过；连同现有8项storage依赖边界共86项通过。根/接口缺失、游标/字节预算/参数拒绝、代际伪造、空库、清理异常和读预算分别留存红灯后转绿；历史初始化中断、并发吊销、提交前后不确定性使用真实SQLite和Event/Barrier验证。真实ASGI入口探针结果在`build/spec143-s01-20261101/entry-acceptance.json`：重开同实例/同项目回执、同ACK、历史仅1条、伪造代际409，0模型/容器请求。其内容明确是确定性夹具，不是Agent或业务测试效果。

删除/保留：全量门禁发现应用层SQL后已移到storage仓储，应用原SQL路径删除，没有双写或第二套处理器；测试中临时设置转发函数移除。普通本机仍使用web.authorize/ServiceOwner/Database原入口和内存HTTP回执，故保留真实消费者；A的envd/沙箱通道与旧数据本片不改，S09才正式退出。无新增依赖、版本升级或真实配置改写。

最终全量、构建、安装、源指纹和权限/接线检查统一见`build/spec143-s01-20261101/verification.json`。本片没有构建Runner镜像、连接公司开发机、请求真实模型或调用预发布；Kit作为S02模板强制合同保留，不能将本片Agent wheel构建当Kit镜像已交付。S02下一方案见143f对应节，实机操作仍按明确目标/范围授权。

最终结果：两个完整后端分片3267＋3007＝6274项通过，0失败/错误/skip；前端568项通过、TS/Vite构建通过（已有体积提示）。标准Mac ARM64与Linux x64 wheel构建/Twine检查通过，全部Python源码逐项与工作区一致，两包application_sha256同为`02052f23ba8e7e918cc3f61774489d59ffcbb241c319177316b21dd51b5f2453`；Mac源外干净安装/产品smoke/pip check通过，Linux仅交叉构建未作本轮实机运行验收。既有1079个源文件仅Database新增入口，另外25个S01源文件；最大改动Python文件235行，git diff --check通过。无提交/push/发布/真实配置/远端操作。
