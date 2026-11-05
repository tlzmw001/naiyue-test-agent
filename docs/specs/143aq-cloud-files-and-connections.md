# Spec143AQ：云端对话附件、直接产物下载与统一连接配置

状态：2026-11-04 用户批准方向及按片连续实施；本文件先记录合同，完成状态以末尾清单和验证证据为准。取代新任务上的 Git 基线、差集交付设计；不表示已发布。真实部署配置、用户目录、历史数据、Spec142 四份草稿不修改，不包含提交、push、发布或部署。

## 1. 目标和外部行为

1. 创建任务只选连接、环境、network ID，不要求文件；创建不调用模型。
2. 云端对话输入框的加号菜单提供添加文件、文件夹。目录浏览包含隐藏文件，选中的普通文件不按扩展名、gitignore、内容或是否 Git 项目过滤。压缩包作为普通文件传输，是否解包由用户提示和 Agent 决定。
3. 当前轮运行中可上传下一轮材料。上传到 Agent 工作区之外，上传本身不发消息、不修改当前工作区。提示词与明确选择的附件集合一起提交。
4. 运行时点击“本轮结束后发送”登记一条持久待发送消息；空闲时显示“发送”。首版每任务最多一条待发送消息，取消后可重新编辑。不点击发送不会自动运行。
5. 当前轮正常结束、任务可准入、全部附件完整时提交下一轮。停止、失败、审批/恢复阻塞、状态未知不触发下一轮，保留材料并显示暂停原因。不可把 WS 断开或 HTTP ACK 当作运行结束。
6. Agent 可自由使用输入文件、修改代码、整理交付文件；继续提供 `.nyt-output/<root_run_id>` 约定。程序仅发布 output 中明确收集的普通文件，不扫描整个工作区、不生成 diff、不替用户合并或建分支。
7. 产物显示名称、大小、状态、下载和保存到本地入口。浏览器下载遵从浏览器保存行为；本地保存使用目录浏览器（可建目录），同内容可复用，不同内容同名须明确确认覆盖。主 Agent 可通过同一产物标识下载、读取和分析。
8. Windows 同一 `connections.json` 配置所有连接、SSH、可用模板及 network ID，不再要求额外 connection/profile/remote-update 文件。Linux `cloud.json` 为实例配置来源，`update.json` 引用它；models/settings 保留各自职责。页面可变网络状态由程序保存，不算手工配置。

不新增直接容器 SSH/SFTP 服务、多租户认证、网络占用租约、自动代码合并、自动解压、文件语义分类、无限多条待发送消息。复用现有 SSH 控制通道访问 HTTP 文件 API。共享 SSH/Owner 凭证的权限边界保持，个人 network 列表不是强租户隔离。

## 2. 当前主链与需要解除的依赖

改造前（0.5.8）：`CloudTaskCreate → LocalTaskDomain.capture → capture_cached → ManualCloudTasks.create → preparation.prepare(upload → task:create → environment:start)`；`CreateTask.input_id` 和本地 binding.input_id 必填，Runner 的 `install_input` 只允许安装一次。`CloudTaskDetail.send → ManualMessage → command_dispatch → runner_runs` 只在无 active_run 时发送，TaskDraft 只有文本/模型。

改造前结果：`task_output_directory → result_snapshot.freeze_result → baseline/tree/patch → ResultBundle → 本地 receive/delivery`；结果包含输入基线，报告和代码交付共用完整快照。该依赖是本次退出目标，不能只隐藏按钮。

改造前连接：`settings.cloud_tasks.profiles_file → ProfileBook → CloudProfile.connection_file → CloudConnection`；Updater/CLI另读 connection_file，network 状态路径由 profiles_file 推导。统一配置必须同步这些消费者、启动/重连、CLI、测试、例子和状态文件定位。

新链：

```text
Windows 页面 → 本地服务/持久草稿 → Coordinator 暂存与附件引用
                    │                      │
               SSH 受管连接          已确认的消息 + 附件清单
                                           ↓
                              Runner 分批安装 → Agent 运行
                                           ↓
                             output 冻结 → Coordinator 产物
                                           ↓
                            浏览器 / 本地保存 / 主 Agent 分析
```

正常例：无文件创建任务→环境 ready→上传资料→点击发送→所有片段校验通过→原 command_id 持久准入→Runner 安装到本批独立目录→将路径/文件清单和提示词交给 Agent→output 产物冻结后发布→下载核对长度/摘要。

失败例：当前 run 期间上传大文件，用户登记下一条消息；上传中断后只续传已确认偏移，不让 Runner 接触不完整材料；当前 run 失败时待发送项暂停，即使上传后来完成也不自动调用模型。发送回执丢失时查询同一 command_id，不重建命令。

## 3. 网络与配置合同

### 3.1 网络输入

前端增加类型 HTTP / gRPC / TCP、TLS 开关、地址输入。地址支持 host:port（IPv6 使用方括号），HTTP 可省略端口；完整 http/https/grpc/grpcs/tcp/tls URL 粘贴时同步类型和 TLS。发往现有 API 的仍是 canonical `ServiceTarget.address`，端口转发不解释业务协议。非法凭证、查询参数、非支持协议和路径仍走现有校验；HTTP 路径继续保留，gRPC/TCP 不接受路径。不改变 NetworkEndpoints 端口用途。

### 3.2 单文件连接

`connections.json` 顶层 `connections` 列表。每条包括 `connection_id`、`connection`（现有 CloudConnection 字段）、`ssh`（现有 host/executable/保活/重连设置）、`profiles`（profile_id、network_id、template_ids、default_template）、可选 `remote_update`（remote_python、remote_settings、timeout_seconds）。一条 connection 可有多个 profile，凭证/SSH 仅写一次；profile_id 全文件唯一，保留原 binding 引用的 ID。单个 profile 仍绑定一个 network，UI据 connection 分组选择，内部复用既有 profile 消费模型。

本地 settings 增加 `connections_file`；与旧 `profiles_file` 同时填写拒绝，不能静默选择。0.5.8 已发布文件作为读取边界兼容，不增加第二套执行逻辑；新文档和最小示例只要求 connections_file。保留既有 profile_id 和显式状态文件位置以复用网络状态，不自动覆盖/删除旧配置或状态。CLI `--settings connections.json --profile ID` 选择同一配置，只有一项可省略 profile，多项必须明确选择；旧单连接 CLI 文件继续在边界支持。

`connection_id` 是正向SSH/HTTP租约键，同一条目下的多个profile并发连接只启动一次；反向入口仍按profile/network管理。`network_state_file`可显式指向原状态文件，默认连接配置文件同目录的同stem.networks.json。CLI复用本机服务或外部已建立的正向隧道，不另起争抢端口的SSH进程。

Linux UpdateSettings 不再强制 connection_file；从 cloud_settings_file 读取 Owner 地址/凭证，从已存在 cloud-root.json 读取 instance_id，禁止创建新实例或通过未认证网络发现身份。旧显式 connection_file 与导出信息不一致必须报错，不能连接错误实例。离线恢复仍有根身份和锁保护。Windows远程更新复用对应条目的 SSH 设置，无需额外 remote-update.json。

上述旧配置一致性指endpoint/owner_authority/instance_id；凭证在旧格式中仍取显式connection_file（保留已发布读取行为）。新配置只有cloud.json一份凭证来源。

私有读取仍验证路径、文件权限、大小；公开 profile 投影和错误日志不得含凭证。JSON不支持注释，提供完整示例与字段说明；TOML延续逐字段注释。

## 4. 文件和消息合同

输入预算默认每批总量/单文件 20 GiB（21474836480字节），100000文件，元数据32MiB，8MiB分段，256KiB IO块，归档上限22GiB，累计缓存默认64GiB，独立配置，需覆盖冻结副本+归档+元数据的临时占用。上传/下载使用有界流，磁盘不足在占用前拒绝；累计任务占用受现有资源预算控制，不能把每批限制当作无限磁盘承诺。

只对显式选择范围传输，不隐藏 `.env`、点文件、Git目录或普通扩展名文件；仍拒绝穿越/绝对归档路径、特殊设备、管道、套接字及逃逸链接。首版附件只支持普通文件/目录，符号链接和Windows junction/reparse项明确提示不支持，不静默漏传。文件名不能因安全校验被静默改名。保留源变更校验、身份/哈希和分段续传。

新增本地附件记录以 attachment_id 标识，字段含 session_id、binding_id、input_id、display_name、size、phase、transfer_id、error_code、created_at。生命周期 uploading→ready→attached；failed/cancelled 可观察。Owner关联以task记录中的attachments映射持久保存（每任务最多1024批，一条消息最多128批）；以 task_id/input_id 持久保存，任务令牌只能取已授予本任务的包。空任务 input_id 可空，必须同步任务/本地binding/Bootstrap/Runner/恢复/存储与校验，禁止伪造“空文件”输入。

TaskDraft 和 ManualMessage 增加 attachment_ids。提交时冻结文本、模型、附件集合和 command_id；一个附件只能引用当前会话/任务已经拥有的记录，上传未完成不准入。待发送项持久在本地任务库，与任务/会话级联清理；本地服务关闭不保证继续运行，恢复后先核对云端状态。浏览器关闭不取消已登记项。

附件安装路径为 `.nyt-input/<attachment_id>/`，独立批次不得覆盖前一批或工作区其他路径。Runner在启动本轮前完成安装/完整性校验并记录安装回执，重启按原ID续接；Agent上下文含本轮文件路径与描述，不将文件正文自动塞入提示词。多个同名顶层选择冲突在上传前展示，需用户调整选择，不猜测覆盖。

安装先在 /workspace/project 外、同一 /workspace 挂载上的私有staging解包校验，再固定工作区与.nyt-input的nofollow目录句柄，按dir_fd无覆盖发布。已有批次校验回执及目录身份，不合并写入；父目录被替换或变成symlink时拒绝。Runner与Shell目前共用UID，0700和随机目录不是同UID防篡改隔离；本片保护路径约束、完整性和幂等恢复，不新增UID隔离系统。任务输入引用同时进入删除、备份/恢复和资源统计，以同一持久关联为事实，不只增加下载鉴权表。

本地API沿现有session/binding鉴权边界增加附件列表、上传登记、进度/取消，以及单条 queued-message 创建/读取/取消；使用 UUID command_id 和版本比较。上传与排队分别持久化，取消队列不取消已开始运行。公开状态收敛为上传中、待发送、等待本轮结束、发送中、已发送、需处理；详细错误仅日志与结构化诊断。

## 5. 结果合同

保留 task_output_directory 工具，说明可放任意希望交付的普通文件或压缩包。从 output 冻结不可变副本、保存 artifact_id/task_id/root_run_id/相对名称/大小/SHA256；确认完成发布前不显示下载成功。产物在释放环境后仍可下载，删除任务及显式回收按引用清理。

默认单产物20GiB，单轮产物总量20GiB，数量1000；下载流式、Range/ETag、校验和取消继续复用现有能力。对用户显示系统生成的本机认证下载入口，不把Owner token拼进URL，不允许任意路径代理下载。浏览器先通过已认证POST取得5分钟的单文件链接和仅该路径可用的HttpOnly/SameSite=Strict Cookie（上限64个），GET再次核对会话/任务归属和服务实例；服务重启链接失效。URL不携带凭证，复制链接不会自动授权别的浏览器或主Agent；主Agent使用已有材料工具和认证API。链接中的产物身份必须再验证本机会话/任务归属；本机链接只保证本机使用，不宣称公网分享。

现有Runner产物上传是整文件从0重试、整体120秒，不能直接用于20GiB。S6将冻结副本按8MiB分段上传，持久偏移/分段摘要，重试核对原artifact_id后续传，提交时验证完整大小/摘要。传输超时按分段IO计，整个任务不由120秒截断。Runner/Coordinator产物缓存64GiB，本机receive预算128GiB，覆盖20GiB文件保存时缓存、临时副本、目标副本和覆盖备份的保守空间预留。

输入最多64个并发共享读者，最后一个物理关闭后才解除清理排他保护。Runner按manifest展开量及256MiB保留空间预检；Coordinator按附件归档加展开总量做保守软准入，重复引用也按此预算预检，不宣称硬磁盘配额。删除任务核销附件引用并关闭本任务读取租约；全局已提交输入包遵循既有共享保留合同，不因取消一个附件或删除一个任务直接删除。本片不新增自动输入GC，预算耗尽应调整预算或按离线维护处理，不能删除仍有引用的包。

本机服务崩溃后的uploading记录在重新读取时转为需重试；显式重试按原ID和已确认偏移继续。队列在来源项目重新打开后核对云端状态；浏览器关闭不取消已在服务运行的worker。

浏览器、CLI和主Agent统一调用产物入口；本地保存复用目录选择和临时文件/原子落盘，覆盖须绑定目标版本避免确认后覆盖新变化。主Agent分析只需产物标识→受权下载→普通read，不再要求代码差集应用或同步基线。旧产物可读取其已有冻结文件，不能重算旧工作区补丁；新任务不生成补丁。旧接口退出和历史只读边界在最后一片按实际消费者核对。

## 6. 分片顺序、影响面与验证

本工作跨越前端、本地服务、Coordinator、Runner和CLI，超过8文件；不新增服务、依赖或密钥。每片先行为失败测试→最小实现→定向回归→构建；最后跑全量。发生接口/存储或锁变化同步contracts/serialization/schema/migration/lock-hierarchy；新文件不超过500行。

| 片 | 交付 | 主要影响面 | 核心验收 |
|---|---|---|---|
| S1 | 类型/TLS/裸地址网络表单 | CloudNetworks、网络展示辅助与前端测试 | HTTP默认端口、gRPC裸地址、TLS、URL粘贴、无效输入、已有目标回填 |
| S2 | 单文件配置及全部消费者 | cloud_files/profiles/configuration、cloud_task_settings、cloud_local/tasks/networks、cloud_install/settings/worker/offline、cli、例子 | 多连接/多network、凭证不泄露、歧义拒绝、旧profile保持、Updater身份读取、CLI一致 |
| S3 | 无输入创建/启动及无基线output基础 | contracts、storage、preparation、Bootstrap、input_install、CloudTaskCreate、results预留和冻结 | 无文件就绪/首条文本运行并保存output、恢复身份、旧任务读取、失败不启动模型 |
| S4 | 20GiB追加上传与分批安装 | cloud_inputs、capture/archive/input_upload、Owner输入授权、Runner安装、本地附件库/API/浏览器 | 隐藏文件、链接拒绝、并行上传不改运行工作区、续传、预算、同名批次、错误隔离 |
| S5 | 附件消息与单项队列 | TaskDraft/ManualMessage、command_dispatch/runner_runs、持久队列、CloudTaskDetail/加号菜单 | 正常下一轮、失败暂停、取消、重启核对、重复提交只运行一次、刷新不丢材料 |
| S6 | 直接产物与主Agent分析，退出diff | result_snapshot/result_tool、artifacts/results、本地下载/保存、CloudResults/分析工具 | output任意普通文件、空output、环境释放后下载、范围请求、同名确认、鉴权、无需Git |
| S7 | 全链路清理/文档/打包检查 | 旧source/delivery主链及其测试、examples、README、handoff、锁表 | 无旧主链消费者、迁移映射齐全、包含新模块、Windows静态/确定性回归和原机清单 |

验证命令：按CI为进程PATH加入项目native fd/rg后，`.venv/bin/python -m pytest -q tests`；`npm --prefix frontend test -- --run`；`npm --prefix frontend run build`；项目现有wheel构建命令依据pyproject/构建脚本执行。20GiB能力的预算/分段边界用稀疏文件或受控流验证，完整20GiB真实网络速度/公司SSH/TLS/gRPC和Windows实机保留给最终用户验收，不以macOS测试替代。

S3引入受信模板的附件模式能力版本（缺失为0，新构建为1），新空任务在模板选择/创建/环境启动验证版本1，发送/Runner握手保持一致；旧0.5.8模板显示需要更新环境，不接收新合同。旧任务仍按已有模式读取和退出，不将旧Runner解释成支持nullable输入/附件命令。S4扩展的RunCommand附件仅在同一能力门禁后准入。

实现期不自动升级现网，不改真实settings/.env、不删历史任务/输入/产物、不发布。代码回退可恢复旧程序；新任务记录/新配置不保证旧版可读取，切换前按现有备份流程操作，禁止旧版混写。

## 7. 执行记录

- [x] 源码主链和已批准产品边界梳理，写入本Spec。
- [x] S1 网络表单：新增行为红灯后7项定向通过，TS/Vite构建通过（2026-11-04）。
- [x] S2 单文件配置。
- [x] S3 空任务。
- [x] S4 追加上传。
- [x] S5 下一轮消息。
- [x] S6 产物下载和分析。
- [x] S7 迁移清理及全量验证。

### 最终证据与迁移核对（2026-11-05）

- 后端：进程PATH使用项目固定native工具后，`.venv/bin/python -m pytest -q tests`：**6540 passed in 519.15s**。
- 前端：`npm --prefix frontend test -- --run`：**76 files / 633 tests passed**；`npm --prefix frontend run build`：TS/Vite通过，只有既有大chunk提示，无构建错误。
- 打包：项目标准 `scripts.build_release --target macos-arm64`、Twine及源码/前端包内容核验，详情见 `build/spec143aq/report.md`。版本未提升，构建仅为本机验证，不覆盖已发布0.5.8。
- 架构/安全只读复核发现的队列CAS、固定命令恢复、unknown暂停、恢复后目录身份、共享读取最后关闭/重复关闭及无输入产物保存均有回归保护。
- 删除：无页面消费者的 `CloudSourcePicker` 与旧source查询前端包装；目录选择测试迁移到普通附件窗口，保留选择、链接拒绝和失败重试断言。
- 保留：Owner/CLI项目输入接口仍有 `cli/cloud_inputs.py` 等消费者；历史非空input_id结果仍有既有冻结结果读取/交付消费者。新空任务经明确分支进入output-only，不把兼容读取解释成新任务继续依赖Git。
- 新组件存在/实质/接线：连接配置由ManualCloudTasks和CLI/Updater读取；附件API登记后由run custody固定授权，Runner安装后才调用Agent；队列由本地任务域恢复/驱动；下载入口由服务注册并由结果按钮调用。对应行为见empty_tasks、task/local_attachments、cloud_message_queue、attachment_install、browser_download_link和前端CloudAttachments/CloudTasks回归。
- 未验证：Playwright扩展未连接，因此未取得实际视觉截图；Windows原机、公司SSH/TLS/gRPC以及真实20GiB传输/磁盘峰值必须按[手册](../cloud-files-and-connections.md)另验。
