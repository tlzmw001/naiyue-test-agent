# Spec143BQ-S1：一个工具完成查询、下载与核对

状态：已接通产品入口。范围与用户确认见[主 Spec](143bq-cloud-results-and-input-reclamation.md)。

## 1. 外部合同

模型只看到一个 `cloud_result`。采用顶层字段和按动作校验的严格 schema；不套用 memory 的私有编译器，不增加嵌套动作解释平台。未知字段、动作无关字段、空 ID、非法分页在产生副作用前拒绝。

| action | 字段 | 行为 |
|---|---|---|
| `query` | `binding_id?`、`result_id?`、`offset=0`、`limit=20` | 无 binding：当前来源会话的关联任务；有 binding 无 result：该任务结果；有两者：固定结果普通产物 |
| `download` 新请求 | `binding_id`、`result_id`、`artifact_id`、`path`、`force=false` | 保存一个普通产物到完整本机文件路径；程序生成 operation_id |
| `download` 恢复 | 只有 `operation_id` | 在新权限检查后恢复同一个固定请求；不能同时改 path/force/结果身份 |
| `status` | 只有 `operation_id` | 查询本会话的原操作；不启动下载、写入或重试 |

`result_id` 不能脱离 binding 使用。ID 复用项目现有类型；分页沿用 offset 上限 1,000,000、limit 1—100。查询逐层分页，返回 `next_offset`，不一次枚举全部任务及全部结果。没有匹配项返回空数组。

规范化后只保留该动作字段：query 填分页默认值，新 download 填 force=false，resume/status 不携带这些默认字段。权限绑定与请求摘要都使用同一份规范化结果，不能因 schema 的无关默认值产生另一种实际请求。

`path` 是最终文件绝对路径，包含文件名，上限沿用现有路径合同。其父目录必须已存在；需要新建目录时主 Agent 先使用已有文件/进程能力。用户指定目录时，模型结合产物文件名构造完整路径；改名直接换 `path`。一次调用不保存整棵云端工作区，也不自动解压。

```json
{"action":"query","binding_id":"11111111-1111-4111-8111-111111111111","offset":0,"limit":20}
```

```json
{"action":"download","binding_id":"11111111-1111-4111-8111-111111111111","result_id":"22222222-2222-4222-8222-222222222222","artifact_id":"33333333-3333-4333-8333-333333333333","path":"/Users/example/reports/changes.zip","force":false}
```

以上路径/ID 只示意合同形状，不作为产品数据。最终 ID 的格式由现有合同 validator 决定，不以示例放宽校验。

## 2. 输出与错误

所有响应含 `action`。query 按 level 返回有界 `items`；任务包含 binding/task/title/state，结果包含 result/source_run/availability，产物包含 artifact/relative_name/size/sha256/媒体类型。远端字段只取白名单，不回传凭证、连接配置或任意大正文。

下载/状态包含：

| 字段 | 来源与含义 |
|---|---|
| `operation_id` | 原 delivery command_id；同一次实际操作的持久身份 |
| `state` | `in_progress / conflict / completed / partial / failed / unknown` |
| `binding_id / result_id / artifact_id` | 固定的来源，不能随 latest 改动 |
| `path` | 经校验的目标绝对路径；只有目标确实存在且已核验才作为成功文件返回 |
| `outcome` | 完成时 `written / already_present`；无内容相同判断证据不报 already_present |
| `size / sha256` | 固定元数据及实际完整性校验结果 |
| `conflict` | 路径、冲突类型、是否允许 force、目标观察事实；不泄漏本地正文 |
| `error` | 稳定 code、步骤、可读原因、可重试性；必要时有界 errno/winerror |
| `progress` | 原 receive/delivery 的实际进度；未完成字节不算已保存 |

`in_progress` 表示已受理，不是完成。`download` 在持久预留并交给现有 producer 后即可返回；主 Agent 必须用 `status` 核对完成后再读取目标。status 只是有界快照，主 Agent 不得宣称在本轮结束后会自动继续处理。

可预期失败，如冲突、结果轮换、容量不足、路径/会话不匹配，以结构化工具结果回填。框架级参数错误、权限机制错误保留原异常类别；关键 IO 错误记录诊断并映射到稳定业务错误，不将任意异常文本或凭证传给模型。取消/断线不能编造失败或成功：用持久回执核对。

单文件也可能在写入后的回执收尾失败时成为 partial/unknown；是否写入以原日志、目标身份和内容核对，不把重试直接当作再次覆盖许可。

## 3. 冲突规则

1. 目标不存在：正常保存。目标为相同内容普通文件：核验后返回 already_present。
2. 目标为不同内容普通文件，`force=false`：返回 conflict、force_allowed=true；零目标写入。模型依据结果询问用户。
3. 用户选择覆盖：发起新 `download`，相同来源/路径，`force=true`；重新预检并应用现有文件权限。允许第一次调用就 force=true。
4. 用户选择改名：新 download 使用新路径和默认 force=false，不自动选择名字、不自动编号。
5. force 只覆盖这一个普通文件的内容冲突；不绕过任务作用域、路径规则、目标身份变化、目录/符号链接/重解析点冲突、容量、SHA 校验或并发 writer。
6. 原冲突操作保持可查询；新尝试使用新 operation_id，可以复用已完整校验的 receive/cache。没有 resolve_conflict 动作、一次性冲突 token 或全目录强制选项。

模型已有明确覆盖指令时直接传 force；工具不额外验证一次“先问再答”的人工工作流。用户意图与写权限沿用当前权限机制，不能以参数名 force 自授宿主权限。

## 4. 权限与作用域接线

注册条件：当前项目已有可信 LocalSource，持久来源主会话，当前执行为匹配的 Host 文件环境。临时会话、普通无目录聊天、子 Agent、Runner 不注册此能力；应用边界再次校验身份，不能只靠工具目录隐藏。

复用 RunCapabilities 窄端口，新增 `cloud_results` 查询/执行能力，不向 runtime 暴露完整 ManualCloudTasks、DB 或 HTTP client。端口绑定当前 ProjectHost/LocalTaskDomain 与服务级单一 ManualCloudTasks。模型不提供 source_session_id、project_id、instance_id、URL、token 或任意远端路径。

端口有两个当前调用方明确的方法：`prepare(request, source_session_id, project_id)` 给 permission.prepare 返回只读的固定本机目标计划，`execute(request, source_session_id, project_id, prepared_plan)` 给工具执行器调用；身份参数均来自 runtime。prepare 不下载、不建目录、不写回执，新请求只准备文件权限，resume 才读取原持久计划；execute 完成 permit 核对后才查询固定远端元数据与启动必要 IO。采用现有 domain 上的方法直接绑定，避免工具依赖应用存储或重新创建管理器。

实际接线位置：

- `service/app.py` 注册新 ToolSpec；`service/scopes.py` 的已有管理器经项目装配交给窄端口。
- `application/projects/host_assembly.py`、`application/sessions/session_service.py`、`agent_session.py`、`session_execution.py` 与 `application/observability/run_events.py` 把端口放入该次运行。
- `contracts/run_ports.py`、`run_context.py` 增加 typed port；`tools/registry.py` 的可见性与子作用域限制同步。
- 不以 `has_materials` 决定是否注册新工具，否则尚未接收结果时无法 query/download。每次查询读当前关联，不冻结为建 Agent 时的任务列表。

新工具整体按 sequential 调度，因为含写动作；query/status 默认只读，download 使用现有文件写权限规则，不能将整个工具默认 allow。`path` 为顶层字段，permissions.path_field=`path`，单一目标复用 FilePermission 的路径解析和 requested/resolved 匹配。

恢复请求在 permission.prepare 中只读原操作，取出其固定路径/force/来源；查不到或跨会话即拒绝。subject 含完整规范化请求和固定下载计划摘要；execute 再核对 permit 的会话、batch、tool_call、环境、项目 revision、工具名和完整参数。

权限细节必须显式实现：

- query/status 没有文件目标，不伪造 resolved；download 的 deny 同时检查 requested/resolved，不因目标在目录外而漏过 deny。
- 默认目录外、敏感路径沿用当前 ask/deny；Auto/NoAsk/BypassApprovals 等模式继续遵循现有策略。
- 当前“记住文件路径”不能使一次 `force=false` 自动授权 `force=true`。对此工具，会话许可绑定规范化计划摘要（来源、完整路径、force），不使用仅 resolved 的宽化分支。用户配置的显式通配 allow/deny 仍按其配置生效。
- force 或路径变化必然成为新调用和新 permit；resume 也重新授权原计划，不把数据库中的旧准入视为跨运行的有效 permit。
- resume 的 public args 只有 operation_id，不能直接调用假设 args[path] 必存在的旧 require_file_permit 分支。核对完整工具 permit 与原计划摘要后，用现有 FilePort.verify_target 检验实际目标；不向模型参数偷偷补 path 来伪造一次相同调用。
- 本片只增加此工具的明确许可处理，不修改其他文件工具/记忆工具的参数规则。

## 5. 数据来源与固定身份

query 的 task list 来自 `LocalTaskDomain.repo.list(local_session_id)`；详情通过 `ManualCloudTasks.view` 的现有连接与范围验证，不能直接相信模型提供的 task ID。远端 list/detail 继续核对 task、result、input/package 的现有身份。

download 只接受元数据中 `role=artifact` 的普通产物；文件名来自固定 metadata，bytes 来自受认证下载通道。新接收固定 `include_code=false`，选中一个 artifact_id。旧任务中的普通 artifact 同样可下载；manifest/patch 不作为完整源码提供。

已选择 result 后不再解析 latest。下载开始前取得既有 result lease；过期/轮换且无完整本机缓存时返回 unavailable。已有完整本机缓存可继续保存，但必须校验来源会话、完整元数据和 bytes；不把陈旧半包当作可用。

## 6. 复用持久交付，不建立第二套下载状态机

使用现有 `naiyue_cloud_deliveries` 行作为对外 operation：command_id 由程序从当前运行/tool_call 的稳定身份产生并在 IO 前保存。其 request_digest 绑定完整下载计划；相同 ID 不同请求拒绝。手动交付的 UUID 行为保持。

DeliveryRecord 等待接收复用 `preparing` 阶段。已有 metadata 增加有版本的 `tool_download` 子记录：固定 binding/result/artifact/size/sha、receive_id、目标 path、force、来源会话与执行绑定。不新建 operations 表，不复制 receive 的分片进度。

1. 通过当前身份/权限和只读远端 metadata 核对后，原 DeliveryRepository 原子登记准备中的交付，并固定 receive_id。
2. LocalDeliveries 持有原 flight；专用编排函数启动/核对 LocalReceives，使用原缓存容量、项目/会话保护及取消排空。
3. receive.available 后进行 files 预检。无冲突或 force 允许时，在同一授权 producer 内推进原 execute；有冲突停在 prepared，工具投影为 conflict。
4. 写入仍通过 delivery_files → review/approved → apply_changes；原 checkpoint、备份、原子替换、目标 writer 保护保持。
5. 同一操作重复请求合并已有 flight。status 不启动 producer；服务重启后未终结且无 flight 的阶段显示 unknown/interrupted，显式 download(operation_id) 才核对并恢复。
6. 回执不可写、请求摘要不符、目录身份变化、原缓存损坏均明确失败。已完成操作不重新写文件；核对时目标后来被用户改动，报告回执完成但当前文件已变化，不把旧 SHA 冒充当前 SHA。

接收与交付操作记录都有现有容量上限；预留沿用 receive_limits.max_results/max_active，不新增默认后台清扫或无限日志。等待接收的新阶段也纳入父会话删除阻塞、关闭排空和活动查询。

受理后 producer 的取消/生命周期复用现有 cloud 操作合同；IO 实际结束前不能释放 writer/缓存占用。进程内可继续完成已授权的固定操作，但进程重启不自动恢复写入。主 Agent 结束不触发新一轮推理。

## 7. 文件名映射

当前 delivery_files 直接用 artifact.relative_name，不能实现用户改名。给 files 交付请求增加可选的严格目标映射 `{artifact_id: relative_target}`；缺省保持原名字，手动多选合同不变。

工具只提交一个 artifact，并将 destination_path 设为目标父目录，relative_target 设为用户指定文件名，name 为空。映射应用于暂存树/目标变更表，不改 remote artifact、receive/material 的来源名、ID、size、SHA。

映射必须覆盖指定集合且无多余项；拒绝绝对路径、`..`、空组件、链接穿越、大小写/Unicode/Windows 规范化后的重复目标，沿用现有普通文件路径检查。源与目标均做独立验证。传输/复制实现、临时副本和模式沿用当前行为，不顺便做拷贝优化。

## 8. 正常与失败链

正常：用户要求报告保存到已有 `reports/` → cloud_result.query 得到固定 R/A → download(path, force=false) → permission.prepare/permit → 窄端口验证 binding→sid/project → DeliveryRepository 预留 → LocalReceives 下载、校验、发布 → LocalDeliveries files 预检/写入 → completed 回执 → status 返回实际绝对路径 → 原 Read/Shell 继续处理。

失败：目标已是不同内容 → 同样先固定来源/目标 → 原 review 发现内容差异 → prepared/conflict，目标未写 → 工具返回原因 → 模型问用户 → 用户选择覆盖 → 新 download(force=true) 复用缓存并重新权限/预检 → 原 approved 再查目标。若用户在预检后又修改目标，拒绝该旧观察，不因 force 绕过。

网络断开：receive 未 available 不进入目标写入；回执保留实际阶段。恢复同一 operation 时核对原 result、range、SHA 与目录身份；不能创建另一份结果或静默追加 `.part` 到用户目录。

## 9. 文件与验证范围

新增文件建议为 `tools/cloud_results.py`（工具 schema/适配）、`application/cloud_local/result_tool.py`（范围/投影）和 `result_downloads.py`（已有交付 producer 的编排），只在既有文件超过 500 行或职责不同处拆分，不建新服务。

修改前述 runtime 接线、`contracts/cloud_delivery.py`、`storage/cloud_delivery.py`、`deliveries.py`、`delivery_files.py`、必要的 permission 会话记忆分支，以及存储版本/备份校验。原表结构可复用，但新阶段/metadata 需要本机格式兼容门禁；使用实施时最新 schema 的下一版，不能踩并行记忆迁移版本。

S1 必须先跑失败测试证明：权限先于下载、单文件重命名有效、冲突零写、初次 force 可用、错误 result 无写入、服务重启只读 status、resume 幂等、阶段接入父删除保护。全部要求及标准构建见[验收矩阵](143bq-04-acceptance.md)。

## 9. 审查修订

冲突后的新目标和显式覆盖可以复用同一会话的完整产物，即使远端结果已轮换或绑定已退役。重试校验原目标观察结果，写入占用短暂失败后的同 ID 可继续。普通文件以 SHA 与大小判断是否已保存，目标身份与实际写入继续经现有文件权限和观察检查。状态投影已接收字节与失败阶段，预期路径错误反馈给模型。
