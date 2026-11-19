# Spec143BQ-S3：完整删除后的 Coordinator 共享输入回收

状态：已接通产品入口。用户已接受本机与 Coordinator 配套升级。范围承接[主 Spec](143bq-cloud-results-and-input-reclamation.md)与[Spec143BP §13](143bp-local-settings-and-cache-cleanup.md#13-留给后续云端优化的摘要)。

## 1. 不变量与触发

只有原完整删除命令的 task deletion 已成功，才执行本片输入回收。释放环境、移除本机关联、普通查询、启动、定时维护都不触发。重试只是原删除命令的显式核对/继续，不创建另一项独立删除任务。

完整删除的原预览/确认范围同步加入“尝试回收本任务使用且当前已无依赖的共享输入”，把该策略纳入原 preview_digest。预览可显示当时的候选/保护原因，但不承诺最终可回收数；执行前仍固定候选并重新检查依赖。旧确认摘要不可默默扩展到新删除范围。

必须同时成立才删除候选：

1. 候选 ID 在任务删除预留时已经持久固定，属于该任务曾成功关联的 input/attachment。
2. 候选的这一代实际包仍与预留事实一致，目录与受管文件身份可核验。
3. 没有其他未完整删除任务/附件引用，没有在途上传、提交、关联、读取或必要恢复占用。
4. 检查依赖与标记 retiring 在同一权威事务中完成；所有新引用/占用入口检查 retiring，不能在零引用检查后插入新消费者。
5. 只有磁盘实际删除/经恢复核对确已不存在，才能在同一记账事务中核销原包容量。失败/状态未知仍保守计费。

任务删除成功是不可被后续缓存错误撤回的事实。清理失败不能把 task.lifecycle 改回 unknown、恢复旧标题或阻塞其他任务运行。

## 2. 当前链路与缺口

`CloudDeletions.reserve` 验证 preview/revision/generation，持 CT1 写 deleting 与 DeleteOperation；execute 关闭相关流，排空 task_io，确认物理停止，清理受管 task 目录；stage(complete=True) 写墓碑，清空 attachments/results，存储层对旧命令正文脱敏。

因此删除后再枚举 input ID 会丢来源；当前 execute 也在 receipt.succeeded 时直接返回。新增候选必须早于上述清空，后续核对必须允许“任务已成功，只继续清理”。

`InputRepository` 以 meta 的 `input:<id>` / `transfer:<id>` 登记包和传输，容量来自 committed 包与未终结传输。`CloudInputs` 的 CP2 busy 保护提交/取消/读取中的实际 IO，但没有跨请求的上传到关联保护。`cloud_files.input_upload.upload` 命中既有输入包时直接 GET 返回，甚至不建 transfer；不能只检查未提交 transfer。

本机 `_deleted` 会退役 binding；command_dispatch 和 observers 也可能在主回执成功后停止。必须把清理状态接入原命令摘要，并保留退役 binding 的受限回执查询路径。

## 3. 持久数据合同

不引入新服务或通用引用计数系统。沿用 Coordinator registry 的 meta、commands、tasks，新增有版本的私有记录；业务层集中校验，不允许消费者直接改 JSON 绕过规则。

### 3.1 输入的一代实际包

`input-state:<input_id>` 保存：`input_id`、随机不可复用的 `incarnation_id`、`state=ready|retiring|deleted`、`package_digest`、`archive_identity`、`accounted_bytes`、`retiring_command_id?`。

InputPackage 的内容身份、archive SHA 与现有磁盘布局不变；incarnation 区分相同内容 ID 删除后重新上传的不同实体。旧回执只能处理它记录的那一代，不能按相同 input_id 删除新实体。未完成新上传仍由原 transfer 管理，commit 时同事务发布 ready 状态与所属 incarnation。

transfer 的提交回执补固定 incarnation；已回收代的 committed/cache_hit 回执不能再次证明“输入可用”。重复查询可显示原提交事实，但再次使用时必须核验当前代；不一致返回 `input_retired`，新上传使用新的操作 ID。

### 3.2 必要的跨请求占用

`input-use:<use_id>` 保存：input_id、incarnation_id（包尚未发布时可空）、`purpose=associate|recover`、原操作 ID、`state=held|consumed|released`、成功关联时的 consumer 身份。use_id 是请求 command_id；同 ID 不同输入/目的拒绝。

- associate：在 GET 复用已有包或开始上传之前建立，覆盖上传、提交、等待任务/附件关联的整个间隙。
- recover：确有跨 Range 请求或重启后继续读取需要时建立；单次服务内读取仍由原真实 IO 预留保护。
- 成功关联与 held → consumed 在同一 registry 事务完成；引用持有者变为 task/attachment，不出现瞬间无保护。
- 取消或明确放弃且实际 IO 已排空后显式 release。未知结果先核对原关联命令；不把断网、客户端退出或时间经过当成完成/取消。
- recover 在完整校验和本机必要保存完成后 release。进程消失但操作尚可能恢复时仍保护；本轮不引入占用 TTL。

associate 活动数量受现有 input_limits.max_pending 约束，recover 受 max_downloads 约束；占用创建回执计入既有 command 容量。明确释放/消费后不占活动额度；未核对的残留可能保留空间和额度，界面必须说明原因并可对原操作继续/取消，不能暗中超时清除。

### 3.3 原删除回执上的清理记录

DeleteOperation 在 reserve 增加 `input_cleanup`，与 task deleting 同事务保存：

| 字段 | 定义 |
|---|---|
| `schema` | 本清理子记录格式版本 |
| `state` | `pending / running / completed / partial / failed / unknown` |
| `candidates` | 去重后固定列表；每项 input_id、incarnation_id、package_digest、accounted_bytes、原目录身份 |
| 每项 `state` | `pending / retiring / reclaimed / retained / failed / unknown` |
| 每项 `reason` | 被谁保护、身份变化、系统错误等稳定 code 与可读原因 |
| 每项 `progress` | 已核验删除的受管条目、目录终结、记账是否完成 |
| `released_quota_bytes` | 已确认物理结果后一次性释放的逻辑额度 |
| `verified_deleted_bytes` | 能证明由本次删除的文件字节；不含事前已不存在或无法归因的字节 |
| `bytes_complete` | 中断后无法完整归因时为 false，不能伪造精确物理释放量 |

候选来自删除前 task.input_id 与 task.attachments 各 input_id。当前 attachments 不支持移除/替换，同一附件 ID 不同内容拒绝，因此两者并集覆盖本轮需要的“曾使用”；不为未存在的更换输入功能预建历史图。

在删除预留的 registry 写事务内固定候选及输入代号；事务同时封住该任务的后续关联。候选上限是当前合同允许的一项基础输入与 1,024 项附件，去重后保存。候选身份元数据缺失标明不可安全清理；固定 ID 成功后不因此阻止原任务删除。连候选 ID/回执都不能持久保存则原删除尚未受理，不进行物理操作。

清理完成但有受保护包时，总状态仍可为 completed，计数明确 retained；没有依赖不是未来保证，不承诺它们稍后自动消失。失败项存在时为 partial（已有完成项）或 failed；无法核定 IO/账本边界为 unknown。

## 4. 占用获取与关联接口

以下为 owner HTTP 内部协作接口，不是新增模型工具，沿用 owner 认证、实例核验和协议版本检查。

| 接口/扩展 | 合同 |
|---|---|
| `POST /api/cloud/v1/input-uses` | command_id、input_id、purpose、origin_operation_id；幂等建立 held；retiring 则拒绝 |
| `GET /api/cloud/v1/input-uses/{use_id}` | 只读核对范围/状态，不恢复占用或执行 IO |
| `DELETE /api/cloud/v1/input-uses/{use_id}` | 幂等 release；已 consumed 返回原消费事实；实际活跃 IO 未排空则拒绝 |
| CreateTransfer | 用于后续关联的上传携带 use_id；验证输入身份，提交时绑定 incarnation |
| CreateTask / AttachInput | 有 input 时必须携带有效 associate use_id；检查 ready 和同代，并原子消费；无 input 的新任务不需要 |
| owner archive 分段读取 | v2 + `X-Input-Use-Id`，绑定 recover 占用及原代；每段仍进入真实读取预留 |

独立 CLI 上传若没有随后的任务关联意图，原 transfer 的在途保护保持；提交后从未关联的包继续保留。CLI 的“上传并关联”、本机附件、缓存命中和恢复入口必须按用途接入，不能只改常见 UI 路径。

`GET /inputs/{id}` 的纯信息查询只持短读取预留，不创建持久占用；它不保证未来关联成功。写路径的 cache hit 必须先 acquire associate use，再 GET，不能先读成功后才补占用。

占用使用同一个原操作 ID 跨本机重启恢复。上传提交成功但关联失败：可恢复错误保留 held，用户明确取消才 release；cancel 响应未知先 GET/reconcile，不凭本机内存删除占用。若包从未关联，即使 release 后也不由本次任务候选回收。

Runner 对自己任务输入/附件的读取继续由已认证 task/attachment 引用与 task_io 保护；不因 owner 新接口要求改变 Runner 协议。任务删除先关闭该任务的读取并排空，不关闭其他任务正在读取同一输入的流。

## 5. 并发门禁与锁顺序

继承 [lock-hierarchy](../lock-hierarchy.md) 的 CO0、CT0、CT1、CP2、DB 规则。本片不持线程锁或 SQLite 事务跨 HTTP、磁盘递归删除或等待 IO。

1. 上传、提交、owner/Runner 实际读取和回收均从 owner loop 使用现有 CP2 input key。回收需要独占；存在活跃 reader/producer 时报告 retained/busy，不等待对方持有的 DB。
2. 在 CP2 预留后，worker 用单个 registry 短事务重新检查当前代、其他活任务/附件、未终结 transfer、held uses 和必要恢复事实，满足才 CAS ready → retiring 并记录原删除命令 ID。
3. task/attachment 新引用通过 CT1 → registry 写事务检查 ready 与 held use，并在同一事务写引用/消费 use。回收不在 DB 内取得 CT1；SQLite 写事务与 retiring 标记提供跨入口互斥。
4. acquire use、cache-hit create、commit、task create/attach、metadata/archive open 均显式检查生命周期。ready 检查和建立新保护必须原子；不能检查后释放锁再登记保护。
5. 释放 DB 后执行受管删除。finally 在真实 IO 结束后归还 CP2；异常保持 retiring/回执证据，下次原命令核对。输入关闭与删除 producer 排空后才关闭 DB/CO0。

接入清单必须包括：现有包 GET 快路径、不同 transfer 同 input 并发、上传 raw stream、提交幂等重放、任务创建、附件重放、Runner attachment 下载、owner range 下载、恢复与 release。内存 busy 保护实际 IO，持久 use/retiring 保护跨请求与重启，二者不能互相代替。

依赖查询以固定候选 ID 为条件读取权威 registry；这不是寻找任意孤儿包的全局扫描。其他任务处于 deleting 但尚未成功时仍计为引用。deleted 墓碑残留的 input_id 不算活动引用。

新增入口/间接调用的顺序必须在实现同片更新 lock-hierarchy；测试用事件屏障验证“先 acquire 则保留、先 retiring 则关联拒绝”两种顺序，不能靠 sleep 猜竞争结果。

## 6. 删除执行与容量核销

原任务删除流程保持到墓碑/主回执 succeeded 为止。之后进入独立清理异常边界；`CloudDeletions.execute` 的 succeeded 快速返回改为：task 已完成时只核对/继续原 input_cleanup，绝不再次 stop 或重删任务目录。

每个固定候选独立推进：

1. 读取原记录，检查本命令/实例/任务代/候选代；reclaimed 不再操作。
2. 任一当前依赖存在则 retained，记录原因；不会为了腾空间强制取消别人的任务、上传或读取。
3. 无依赖，按第 5 节置 retiring。在磁盘 IO 前保存受管目录和文件身份；只允许原 `inputs/<validated_content_id>/`，不可从模型/HTTP 接受清理路径。
4. 复用 task_cleanup 的 FD 锚定、no-follow、身份/挂载边界规则，核对 `archive`、`input.json`、`manifest.json`、`selection.json` 与实际发布的必要受管项。未知额外项、链接、目录替换或外部挂载明确失败；不扩大递归范围。
5. 逐项删除并留下可恢复进度，最后确认该代受管目录不存在。Windows 删除被占用、权限错误等保留 errno/winerror 和路径/步骤，不能静默或退出服务。
6. 在 registry 同事务确认 retiring 仍属于本命令/同代，移除该代 input 包计费记录，状态改 deleted，更新候选 reclaimed 与一次性 released_quota_bytes。任何输入目录仍可能保有需清理字节时不核销整包额度。

物理删除与 SQLite 不跨系统原子，因此采用保守阶段而非伪事务。磁盘已删但账本失败：保持原额度、unknown；原命令核对目录身份/缺失事实后一次性补记账。不得先删 meta 再尝试 unlink。

本次发现已不存在而没有自己删的证据时，可记录 `confirmed_absent`，在核对确无原实体后释放原占用额度，但 verified_deleted_bytes 不增加。外部替换成其他实体时不做“补删除”，记录 identity_changed；不能将未验证路径缺失直接当作同代完成。

文件数据/父目录同步与窗口内进度持久化沿用现有安全文件工具。清理算法不改上传归档、复制策略、解压、成果轮换或 Runner outbox 行为；只增加这组受管输入的删除执行。

## 7. 核对与幂等重试

查询沿用 `GET /commands/{delete_command_id}`；继续沿用 `POST /commands/{delete_command_id}/reconcile`。GET 严格只读。成功的主回执仍允许显式 reconcile 清理未完成/失败/retained 项，候选 ID/代不扩大；新 use 释放不触发后台重试。

| 中断点 | 核对/重试结果 |
|---|---|
| 候选持久化前 | 未受理，不触发任务或输入删除 |
| 候选固定、任务未完全删除 | 按原任务删除恢复；不提前回收输入 |
| 墓碑已写、清理未开始 | 主任务成功；从原候选开始 |
| retiring 已写、尚未 unlink | 核对身份后继续；新 acquire/attach 拒绝 |
| 删除一部分或 unlink 后未 ACK | 核对原进度和剩余文件，缺失项不重计字节；不释放整包容量 |
| 全部磁盘已删、账本未核销 | 验证不存在后补一次记账，不重删其他路径 |
| reclaim 完成后同内容重传 | 新 incarnation；旧候选不触碰新包 |
| 两任务并发删除同一输入 | 仍活引用时保留；最后引用消失后仅一个命令 CAS retiring；另一回执关联既有结果或报告已被同代回收，不能重复核销 |

两个删除命令候选相同且一个正处于 retiring 时，后者记录已有回收命令 ID/unknown 或 retained；只能由持有 retiring 的原命令完成该代删除。不得抢占原 IO 或把“正在处理”说成已回收。

必要恢复记录不通过年龄猜测失效。本轮不加后台 reconcile、TTL 或全局扫描；用户对原操作继续/取消/重试后才能改变相应事实。

## 8. 本机结果与失败展示

CommandReceipt.status 继续描述原 task deletion。result.input_cleanup 独立表达 pending/running/completed/partial/failed/unknown、计数、包 ID、原因和是否可对原命令重试。主 error_code 不被缓存错误覆盖。

示例文本：`任务已删除。共享输入：回收 2 个，仍被使用 1 个，失败 1 个。失败原因：archive 删除被拒绝（具体系统错误）。` 数字均来自真实回执，无数据时不显示示例。

本机接线要求：

- `_deleted` 仍及时 retired binding，原 task 成功不等待清理全绿；同时保留本次删除 command 的固定 profile/instance/task/generation/digest/tombstone 和 cleanup 摘要。
- `ManualTaskCommands`、command_dispatch、command_summary 不因主 status=succeeded 跳过清理结果的新快照。新版本快照做单调/身份校验，旧快照不能覆盖新清理进度。
- 退役后使用原 command 限定的 receipt 连接，只允许 GET 该回执与 POST 其 reconcile；不能开放任意已退役任务操作或重新创建绑定。
- 在云端任务列表保留“删除记录”入口，按当前来源会话分页查询本机原删除操作，列出已删任务及清理结果；当前详情删成功后先显示结果或跳到该记录，不能直接返回并丢失失败。
- 未接收到 ACK 显示“结果待核对”；“核对结果”为只读 GET，“重试输入回收”显式调用原 reconcile。并发点击合并原命令。
- 原来源会话/项目被用户完整删除后，本机历史不承诺继续存在；Coordinator 原命令仍按现有保留规则可由已认证管理 API/CLI 按 command ID 核对，不为此建立另一个全局历史库。

## 9. 协议、迁移与回退

OWNER_PROTOCOL 从 1 升为 2；Runner protocol 和 task state schema 不随之修改。所有 owner 写入口在副作用前要求 v2；旧端显示 required/current version 与“升级本机和 Coordinator”的信息，不能把 426/版本错误伪装为网络故障。archive 恢复读取也要求 v2/use，纯健康/版本发现可以保持原能力。

registry 私有格式与 root ownership guard 从当前 2 推进到 3；即使 meta 使用原 SQL 表，也必须让旧 Coordinator 拒绝打开含新保护语义的根。同步严格 schema 校验、初始化、升级、备份/还原与离线命令，不让旧程序忽略 use/retiring 后继续写。

升级顺序：

1. 停止旧本机和 Coordinator 的新写准入，排空已开始的上传/读取/删除 IO，保存未终结回执；把已知旧必要恢复检查纳入原维护/升级成功条件，未解决或未登记的依赖使升级检查失败，不能开放回收。备份按现有流程，用户数据不在设计阶段执行迁移。
2. 在 CO0 独占下升级私有格式，为已登记 package 建立 incarnation；为未终结传输补恢复保护。新表/字段及迁移状态必须同步序列化、DB 映射与备份校验。
3. 旧删除命令尚未成功时，从仍存在的 task/input/attachment 固定候选后再恢复；旧命令已经成功而从未固定候选，则标记 `legacy_candidates_unavailable`、不猜历史 ID 或扫描输入目录补候选。
4. 本机新版本将未终结的关联/上传操作先取得 use 再请求续传/关联。升级前仅 GET 命中但未登记的旧操作不能原样重放；v2 必须重新核验，包已不存在时用仍保留的本机冻结输入按新命令重传，缺少来源则明确不可恢复。
5. S2 已退出旧补丁取回，不以旧 base/patch 读取接口启动新业务操作；已知中断旧交付若仍有远端输入依赖，先按 S2 完成核对或登记 recover 占用。本机证据继续保护，不能把“客户端已升级”推断为旧本机缓存可删。
6. 完成版本/根格式检查后开放 v2 写入与本片回收；旧客户端发送旧请求均拒绝，不兼容不带保护的写路径。

迁移读登记数据是格式升级，不寻找任意孤儿包；从未关联且已提交包仍保留。旧版无法表达的跨请求保护不追溯伪造为已登记；通过关闭旧客户端写入口、清晰重启/重传合同收束该边界。

升级中断必须可在维护状态继续：格式记录先封口，事务迁移完成后才宣布 ready。旧程序不能在“DB 已升级、根标记未升级”的窗口重新接管。还原旧备份只用于明确的整实例回退流程，不能只降版本字段；已经真正删除的字节无法靠回退软件恢复。

## 10. 文件与验收

主要修改：`contracts/cloud_delete.py`、`cloud_inputs.py`、`cloud_versions.py`；`application/cloud/deletions.py`、`inputs.py`、`input_download.py`、`ownership.py`；`storage/cloud/deletions.py`、`inputs.py`、`schema.py`；owner input/attachment/command routes；`cloud_files/input_upload.py`、client/CLI；本机 attachments/commands/command_dispatch、cloud_local_commands；前端 CloudTasks/CloudTaskDetail/CloudTaskActions 与相关类型/API。

新增专门模块 `application/cloud/input_reclamation.py` 和 `storage/cloud/input_lifecycle.py` 承担本次输入生命周期；输入占用合同独立小文件在需要保证 500 行限制时拆出。删除执行复用安全路径算法，但不能将 task_cleanup 的 task 根范围直接放宽为任意 root。

测试覆盖完整矩阵见[验收文档](143bq-04-acceptance.md)：共享引用、cache-hit/关联窗口、真正 reader、恢复、每个崩溃阶段、容量不提前核销、重传代隔离、独立结果展示、旧协议与升级中断。测试全绿之前不宣称此回收可用于真实云端数据。

## 11. 实施边界

跨请求占用通过 owner `X-Input-Use-Id` 与关联命令绑定，create/attach 在写事务内消费。v1 无法枚举跨请求的历史恢复，因此迁移为这些包登记代号并标记 `input_legacy_use_unverified`。该状态保留容量和实体，并进入原删除回执。升级后新包使用 v2 占用合同，原删除任务只核对其固定候选代。

## 12. 审查修订

Coordinator 使用已认证的 legacy cutover 回执记录历史来源核对。原 owner 凭证在 v1 中没有客户端身份，来源清单由部署方核对。部署方排空旧端，核对历史来源，先通过已有 input-use 登记仍需要的输入，再提交核对回执。服务端验证所列占用仍有效。核对回执可重复提交，覆盖这次实例切换涉及的旧代。缺少完整来源时继续保留旧包，普通任务与新包回收照常。原删除命令在核对完成后可重试同一候选代；核对接口只写证据。配额仍在物理删除后核销。

在同一轮修复附件取消和 CLI 明确拒绝后的占用释放，输入元数据读取互斥，以及回收中断与整实例备份之间的准入。已知任务删除成功但连接断开时，核对返回失败原因，删除记录保留已确认结果。

部署时先核对所有使用过该 Coordinator 的 owner 客户端和旧交付记录。只有来源清单完整、仍需保留的依赖已登记后，才提交一次核对回执。可以重用已有输入占用接口登记 `recover` 或 `associate`。核对请求的字段为 `command_id`、`sources_verified: true` 和 `input_use_ids`。最后一个字段填写仍有效的输入占用 ID，已核实没有此类占用时填空数组。

```bash
nyt cloud verify-legacy-inputs --settings "$cloud_connection_settings" --request "$legacy_cutover_receipt"
```

v1 客户端缺少稳定身份，实例无法自行证明历史来源清单的完备性。来源不全时延后提交回执即可，其间仍能使用普通任务和 v2 输入。旧客户端附带 v1 协议的请求收到 426，随响应提供版本信息。已安装的旧前端使用原有错误文案，部署方须在配套升级通知中告知客户端升级顺序。
