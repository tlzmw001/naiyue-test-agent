# 全量配置参考与逐项说明（当前开发分支）

## 143AQ统一连接配置（开发分支，未发布）

当前文件上传/下载操作、预算和Windows验收见[文件与连接指南](../../cloud-files-and-connections.md)。新任务不需要Git基线，创建后在对话加号添加文件；本轮output作为普通产物下载。

Windows新增 `connections.example.json`，复制到私有目录为connections.json。它取代新配置中的profiles.json、connection.json和remote-update.json；旧格式仍仅在读取边界支持，执行主链相同。不要把示例占位凭证或实例ID用于真实环境。

| 字段 | 说明 |
| --- | --- |
| `connections` | 全部开发机条目，1–64条。同文件每个connection_id和每个profile_id唯一。 |
| `connection_id` | 开发机连接身份。同条目profiles共享一个正向SSH及HTTP客户端，避免本机端口冲突。 |
| `connection` | 原connection.json内容，endpoint是Windows本机转发地址，owner_authority是Linux原Owner地址；instance_id沿用现有实例。凭证仅在私有文件中填写。 |
| `ssh` | 原profile中的tunnel、ssh_host、ssh_executable、connect_timeout_seconds、keepalive_seconds/count、reconnect_seconds、poll_seconds、shutdown_seconds。字段含义与下方原profile表相同；tunnel省略仍为external，应用管理连接须填owned。 |
| `profiles` | 每条含profile_id、network_id、template_ids、default_template。同连接多个network分别一项，只需配置自己获分配的ID；无反向入口可省略network_id。保留已有profile_id。 |
| `remote_update` | 可选；需要从Windows触发更新才填写remote_python、remote_settings（Linux绝对路径）和timeout_seconds；SSH别名复用ssh，不再重复写。 |

本机settings的 `[cloud_tasks]` 使用 `connections_file` 绝对路径。不能同时填写旧 `profiles_file`。升级时可填写 `network_state_file` 指向原profiles.networks.json；不指定则使用connections文件同目录的connections.networks.json。程序不自动移动/覆盖旧状态。

CLI统一用 `--settings C:/private/connections.json --profile development`，例如 `nyt cloud project list`；多profile不指定会明确拒绝，单profile可省略。CLI复用本机服务已建立的SSH连接，运行CLI前保持本机服务或外部隧道在线。

远程更新例：`nyt cloud release discover --remote C:/private/connections.json --profile development`。Linux的update.json只保留cloud_settings_file，不再需要connection_file；从cloud.json读取地址和凭证，从已存在且ready的cloud-root.json读取实例ID，不初始化新实例。旧版显式connection_file仍可读取，若同时配置cloud_settings_file，地址/实例必须一致；旧凭证字段仍以显式文件为准。

下面profiles/connection/remote-update独立文件说明供0.5.8迁移核对，不是新版本要求额外准备的文件。

本目录已包含143AO通用服务入口，尚未随新版本发布；已安装0.5.8请使用[对应标签的配置例子](https://github.com/tlzmw001/naiyue-test-agent/tree/v0.5.8/docs/examples/full)。通用入口迁移见末节及服务隧道手册。

这些是示意配置，不是可直接连接公司环境的部署材料。所有端口、bridge、路径、UID/GID、实例ID与凭证都须替换。JSON严格不支持注释；本说明与JSON并列提供，不能往JSON加 _comment 字段。真实配置请放仓库外私有目录；Linux文件0600、目录0700，Windows限制ACL。无真实密钥。

## 使用顺序

1. 本地settings.full.toml是从旧0.5.0参考迁移的完整场景示例，保留原逐项注释；其中Auto、代理、MCP和子Agent为示例选择，并非默认启用。先按需删去这些场景设置，再配置cloud_tasks。
2. 开发机准备已有OpenSandbox、非root身份、bridge；cloud.example.json复制为私有cloud.json。templates为空不是通过预检的模板，按 ../../cloud-s091-windows-and-linux.md 产生真实回执再登记。
3. 开发机models.json沿 ../models.example.json；新版本不改变模型配置格式，不把key放到Runner。
4. 将实际Owner身份填入connections.json对应条目的connection；Windows的settings只设置connections_file绝对路径。Linux更新器从cloud_settings_file引用的cloud.json读取连接，不再要求另一份connection文件。
5. external模式自行开SSH转发；owned添加ssh_host并预先配置SSH认证，不并行占用同一端口。
6. 只有需要更新/备份时才配置Linux update.json，并在Windows connections条目中填写可选remote_update；无需单独remote-update.json。Coordinator程序先独立升级，Runner更新不会重启Coordinator。维护标记跨重启保留，验证后显式leave。

旧settings删除整个sandbox（含git/ssh/network/lifecycle/profiles）、delivery和mcp.allow_sandbox。旧会话不兼容，旧数据不自动迁移。S11 R4到0.5.7没有新增必填字段。JSON里省略的可选值：profiles.ssh_host默认null（owned必填）、update.maintenance_receipt默认null（需时填真实预检回执路径）、download_proxy默认null（需时填私有代理，不提交）；resource_limits.max_logical_bytes默认null（未设逻辑字节上限）。

表格中的值均可在同名JSON定位。数值约束由真实Pydantic模型校验；秒、字节、数量不能互换；0仅在模型允许时使用。不要为对齐版本覆盖已有实例身份。

## profiles.example.json

| 字段 | 用途 |
| --- | --- |
| `profiles` (profiles) | 连接配置列表；profile_id必须唯一。 |
| `profile_id` (profiles[].profile_id) | 侧栏选择的连接标识。 |
| `connection_file` (profiles[].connection_file) | 私有Owner连接JSON绝对路径；Windows替换为C:/private/connection.json。 |
| `template_ids` (profiles[].template_ids) | 允许使用的真实模板ID清单，须已在Coordinator登记。 |
| `default_template` (profiles[].default_template) | 默认模板ID，必须在template_ids内。 |
| `tunnel` (profiles[].tunnel) | external复用人工SSH转发；owned由应用启动并管理转发进程。 |
| `ssh_executable` (profiles[].ssh_executable) | SSH可执行文件，默认从PATH查找。 |
| `connect_timeout_seconds` (profiles[].connect_timeout_seconds) | SSH建立连接最长秒数。 |
| `keepalive_seconds` (profiles[].keepalive_seconds) | SSH保活间隔秒数。 |
| `keepalive_count` (profiles[].keepalive_count) | 无响应保活次数上限。 |
| `poll_seconds` (profiles[].poll_seconds) | 连接检测轮询间隔秒数。 |
| `shutdown_seconds` (profiles[].shutdown_seconds) | 关闭受管SSH进程等待秒数。 |

## connection.example.json

| 字段 | 用途 |
| --- | --- |
| `endpoint` (endpoint) | 本地转发后的loopback URL；不是开发机桥接监听。 |
| `owner_authority` (owner_authority) | 开发机Owner的原始host:port，用于请求授权校验。 |
| `owner_token` (owner_token) | 私有Owner凭证，与开发机一致；示例字符串必须替换，禁止提交。 |
| `instance_id` (instance_id) | 现有Coordinator实例UUID；从真实实例获得，不随意重生成。 |
| `timeout_seconds` (timeout_seconds) | 请求或远程调用超时秒数，不代表后台操作停止。 |
| `io_bytes` (io_bytes) | 每次IO块字节数。 |
| `max_response_bytes` (max_response_bytes) | 单次Owner JSON响应上限，默认64MiB；manifest/selection预算已提高到32MiB，极端长路径清单可显式提高为128MiB。旧显式4MiB不会自动修改。此项不改变模型转发或WebSocket帧预算；文件正文走分段流。 |

## cloud.example.json

| 字段 | 用途 |
| --- | --- |
| `data_dir` (data_dir) | 开发机持久任务根；改路径不会自动搬迁数据。 |
| `protected_roots` (protected_roots) | 禁止作为任务数据根处理的受保护目录。 |
| `owner_listen` (owner_listen) | 必须loopback地址:端口，仅通过SSH访问。 |
| `task_listen` (task_listen) | 专用私有bridge地址:端口，须容器可达；禁止0.0.0.0。 |
| `owner_token` (owner_token) | 私有Owner凭证，与开发机一致；示例字符串必须替换，禁止提交。 |
| `task_token_ttl_seconds` (task_token_ttl_seconds) | Runner任务令牌有效秒数；不是容器存活TTL。 |
| `max_frame_bytes` (max_frame_bytes) | 控制协议单帧字节预算。 |
| `max_receipts` (max_receipts) | 命令回执数量预算。 |
| `history_batch_events` (history_batch_events) | 历史回传每批事件数量。 |
| `json_depth_limit` (json_depth_limit) | 入站JSON最大嵌套深度。 |
| `history_page_limit` (history_page_limit) | 历史API单页条目上限。 |
| `history_read_timeout_seconds` (history_read_timeout_seconds) | 历史读取超时秒数。 |
| `history_limits` (history_limits) | 任务历史存储和读取预算。 |
| `max_message_bytes` (history_limits.max_message_bytes) | history_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `max_pending_bytes` (history_limits.max_pending_bytes) | history_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `max_pending_messages` (history_limits.max_pending_messages) | history_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `max_parts` (history_limits.max_parts) | history_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `preview_chars` (history_limits.preview_chars) | history_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `detail_chars` (history_limits.detail_chars) | history_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `cursor_seconds` (history_limits.cursor_seconds) | history_limits.中的秒数预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `body_depth` (history_limits.body_depth) | history_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `observation_limits` (observation_limits) | 观察流快照/事件缓存预算。 |
| `queue_records` (observation_limits.queue_records) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `queue_bytes` (observation_limits.queue_bytes) | observation_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `record_bytes` (observation_limits.record_bytes) | observation_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `batch_records` (observation_limits.batch_records) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `flush_seconds` (observation_limits.flush_seconds) | observation_limits.中的秒数预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `text_chars` (observation_limits.text_chars) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `live_messages` (observation_limits.live_messages) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `live_threads` (observation_limits.live_threads) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `live_tools` (observation_limits.live_tools) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `state_bytes` (observation_limits.state_bytes) | observation_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `cached_tasks` (observation_limits.cached_tasks) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `replay_events` (observation_limits.replay_events) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `replay_bytes` (observation_limits.replay_bytes) | observation_limits.中的字节预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `subscribers` (observation_limits.subscribers) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `task_subscribers` (observation_limits.task_subscribers) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `heartbeat_seconds` (observation_limits.heartbeat_seconds) | observation_limits.中的秒数预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `send_timeout_seconds` (observation_limits.send_timeout_seconds) | observation_limits.中的秒数预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `stream_seconds` (observation_limits.stream_seconds) | 单次观察流最长秒数，到期需重连同步。 |
| `snapshot_attempts` (observation_limits.snapshot_attempts) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `snapshot_messages` (observation_limits.snapshot_messages) | observation_limits.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `cursor_seconds` (observation_limits.cursor_seconds) | observation_limits.中的秒数预算；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `backend` (backend) | 已有OpenSandbox后端连接，不是Owner接口。 |
| `endpoint` (backend.endpoint) | 本地转发后的loopback URL；不是开发机桥接监听。 |
| `backend_id` (backend.backend_id) | 实际后端身份，须与模板预检回执一致。 |
| `api_key` (backend.api_key) | 后端私有凭证；替换示例，不进入Runner。 |
| `request_timeout_seconds` (backend.request_timeout_seconds) | 网络单次请求最长秒数。 |
| `ready_timeout_seconds` (backend.ready_timeout_seconds) | 环境就绪探测超时秒数。 |
| `page_size` (backend.page_size) | 每次后端列表读取数量。 |
| `max_pages` (backend.max_pages) | 后端分页读取数量上限。 |
| `templates` (templates) | 真实TemplateReceipt字典。示例为空：仅可加载，不能执行任务；必须使用构建预检真实产物，禁止手写passed。 |
| `max_active_environments` (max_active_environments) | 运行环境上限；达到后准入拒绝。 |
| `max_retained_environments` (max_retained_environments) | 保留环境上限；与活动数量不同。 |
| `idle_stop_seconds` (idle_stop_seconds) | 空闲停止阈值秒数；0关闭空闲停止。 |
| `resource_limits` (resource_limits) | 后台维护软资源预算，不是宿主磁盘硬配额。 |
| `maintenance_interval_seconds` (resource_limits.maintenance_interval_seconds) | 维护扫描间隔秒数。 |
| `task_batch_size` (resource_limits.task_batch_size) | 每轮维护读取任务数。 |
| `scan_max_entries` (resource_limits.scan_max_entries) | 一次扫描最多目录项。 |
| `scan_max_depth` (resource_limits.scan_max_depth) | 目录扫描最大深度。 |
| `scan_timeout_seconds` (resource_limits.scan_timeout_seconds) | 普通资源扫描时间预算，秒。 |
| `cleanup_timeout_seconds` (resource_limits.cleanup_timeout_seconds) | 删除清场独立时间预算，秒；不再复用普通扫描的2秒。 |
| `scan_cache_seconds` (resource_limits.scan_cache_seconds) | 扫描结果缓存有效秒数；0禁用缓存。 |
| `min_free_bytes` (resource_limits.min_free_bytes) | 操作前要求的可用空间字节下限。 |
| `delete_drain_timeout_seconds` (delete_drain_timeout_seconds) | 删除排空控制等待秒数。 |
| `task_cpu` (task_cpu) | 每任务CPU份额，正整数。 |
| `task_memory` (task_memory) | 每任务内存，如2Gi/512Mi；不是字节整数。 |
| `input_limits` (input_limits) | 输入捕获/上传/缓存预算。 |
| `max_file_bytes` (input_limits.max_file_bytes) | 单个文件字节上限。 |
| `max_input_bytes` (input_limits.max_input_bytes) | 一次输入所有文件合计字节上限。 |
| `max_archive_bytes` (input_limits.max_archive_bytes) | 输入归档字节上限。 |
| `max_files` (input_limits.max_files) | 清单文件数量上限；接收指材料文件数，结果指变更文件数。 |
| `max_metadata_bytes` (input_limits.max_metadata_bytes) | 结构化元数据字节预算。 |
| `io_bytes` (input_limits.io_bytes) | 每次IO块字节数。 |
| `max_pending` (input_limits.max_pending) | 待处理输入数量上限。 |
| `max_cache_bytes` (input_limits.max_cache_bytes) | 受管缓存字节预算，不是用户保存目录配额。 |
| `gzip_level` (input_limits.gzip_level) | gzip压缩等级0..9，越高越耗CPU。 |
| `upload_timeout_seconds` (input_limits.upload_timeout_seconds) | 上传请求超时秒数。 |
| `chunk_bytes` (input_limits.chunk_bytes) | 上传块字节数。 |
| `max_segments` (input_limits.max_segments) | 分段数量上限。 |
| `staging_ttl_seconds` (input_limits.staging_ttl_seconds) | 临时输入过期秒数。 |
| `sweep_interval_seconds` (input_limits.sweep_interval_seconds) | 清扫检查间隔秒数。 |
| `git_executable` (input_limits.git_executable) | Git可执行文件路径或PATH中的名称。 |
| `git_timeout_seconds` (input_limits.git_timeout_seconds) | 单次Git子进程超时秒数。 |
| `max_discovery_entries` (input_limits.max_discovery_entries) | 输入发现最多遍历的目录项数量。 |
| `control_io_timeout_seconds` (control_io_timeout_seconds) | 控制连接发送IO超时秒数。 |
| `control_receive_timeout_seconds` (control_receive_timeout_seconds) | 控制连接接收超时秒数。 |
| `runner_permissions` (runner_permissions) | 云端工具权限；要求NoAsk，不等于允许访问宿主。 |
| `mode` (runner_permissions.mode) | runner_permissions.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `deny` (runner_permissions.deny) | runner_permissions.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `allow` (runner_permissions.allow) | runner_permissions.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `tool` (runner_permissions.allow[].tool) | runner_permissions.allow[].中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `args` (runner_permissions.allow[].args) | runner_permissions.allow[].中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `tool_environment` (tool_environment) | Runner工具环境设置；不可放模型密钥。 |
| `values` (tool_environment.values) | tool_environment.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `path_prepend` (tool_environment.path_prepend) | tool_environment.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `unset_env` (tool_environment.unset_env) | tool_environment.中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `subagents` (subagents) | 可选子Agent定义；沿当前SubAgentSpec结构。 |
| `artifact_limits` (artifact_limits) | 产物上传、读取与保留预算。 |
| `max_file_bytes` (artifact_limits.max_file_bytes) | 单个文件字节上限。 |
| `max_cache_bytes` (artifact_limits.max_cache_bytes) | 受管缓存字节预算，不是用户保存目录配额。 |
| `max_artifacts` (artifact_limits.max_artifacts) | 一次结果最多报告产物数量。 |
| `io_bytes` (artifact_limits.io_bytes) | 每次IO块字节数。 |
| `transfer_timeout_seconds` (artifact_limits.transfer_timeout_seconds) | 材料传输超时秒数。 |
| `result_limits` (result_limits) | 结果冻结、清单和租约预算。 |
| `output_directory` (result_limits.output_directory) | 报告产物目录，相对项目根；源码变更另外归入代码取回。 |
| `max_patch_bytes` (result_limits.max_patch_bytes) | 代码补丁字节预算。 |
| `max_metadata_bytes` (result_limits.max_metadata_bytes) | 结构化元数据字节预算。 |
| `max_files` (result_limits.max_files) | 清单文件数量上限；接收指材料文件数，结果指变更文件数。 |
| `max_artifacts` (result_limits.max_artifacts) | 一次结果最多报告产物数量。 |
| `max_result_bytes` (result_limits.max_result_bytes) | 一次结果字节总预算。 |
| `max_results` (result_limits.max_results) | 保留结果/接收记录数量预算。 |
| `result_lease_seconds` (result_limits.result_lease_seconds) | 结果读取租约有效秒数。 |
| `max_result_leases` (result_limits.max_result_leases) | 同时结果读取租约数量上限。 |
| `git_executable` (result_limits.git_executable) | Git可执行文件路径或PATH中的名称。 |
| `git_timeout_seconds` (result_limits.git_timeout_seconds) | 单次Git子进程超时秒数。 |
| `model_proxy` (model_proxy) | 仅Coordinator装配的模型代理配置。 |
| `config_path` (model_proxy.config_path) | 开发机私有models.json绝对路径；沿既有models示例配置。 |
| `upstreams` (model_proxy.upstreams) | 原生provider ID到固定上游根地址；兼容provider沿models base_url；不填写任意任务URL。 |
| `max_request_bytes` (model_proxy.max_request_bytes) | 模型请求体字节预算。 |
| `max_response_bytes` (model_proxy.max_response_bytes) | 单次响应字节上限。 |
| `error_bytes` (model_proxy.error_bytes) | 上游错误读取字节上限，原始错误不透传。 |
| `chunk_bytes` (model_proxy.chunk_bytes) | 上传块字节数。 |
| `per_task` (model_proxy.per_task) | 每任务模型并发上限。 |
| `total` (model_proxy.total) | 全实例模型并发上限。 |
| `body_timeout_seconds` (model_proxy.body_timeout_seconds) | 读取模型请求体超时秒数。 |
| `max_duration_seconds` (model_proxy.max_duration_seconds) | 单次模型请求最长秒数。 |

### 云端 MCP（143AY）

在 Linux 开发机 Coordinator 启动时 `--settings` 指向的文件中填写 `mcp`。
示例中的服务均为停用状态，替换实际地址并开启后才会连接。已有任务从 Windows 端
打开「云端任务 → MCP 工具 → 刷新工具」应用，正在运行的任务等该轮结束后更新。
本机 `settings.toml` 的 MCP 用于本机 Agent，云端 MCP 由这份 Coordinator 配置管理。

| 字段 | 用途 |
| --- | --- |
| `mcp` / `servers` | 可选。按服务 ID 配置，默认无服务，最多 8 项。ID 可用 1–16 个英文字母、数字或下划线。 |
| `enabled` | 控制整个服务。启用服务的有效工具全部交给云端主智能体。 |
| `url` | HTTP(S) Streamable HTTP 地址，保留真实域名和路径。禁止 URL 用户名、密码、查询参数和片段。 |
| `route` | `direct` 从 Runner 直连。 `windows_tunnel` 复用该任务绑定的 Windows SSH 反向入口。 |
| `network_slot` | 中转必填。用任务 `network_id` 下的端口入口 ID。Windows 连接页面应把该入口指向与 MCP URL 相同的主机、有效端口和 TLS 类型。 |
| `allow_insecure_http` | 默认 `false`。仅在明确使用非回环明文 HTTP 时设为 `true`。HTTPS 按 Runner 信任的 CA 校验证书。 |
| `headers` | 可选。在私有 `cloud.json` 直接填写每个请求头的完整值，如 `{"Authorization":"Bearer <token>"}`。同名头只能在 `headers` 和 `headers_env` 中选一处。保存后在任务内刷新 MCP 工具。 |
| `headers_env` | 可选。按服务填写「请求头 → Coordinator 进程环境变量名」，如 `{"Authorization":"LOGS_MCP_AUTHORIZATION"}`。变量值填写完整头值，例如 `Bearer <token>`。多个服务可使用不同变量，也可为同一个服务配置多个头。获准连接的明文 HTTP 会明文发送这些头。 |
| `connect_timeout_seconds` | 默认 15 秒，建立连接和发现目录的单服务上限。 |
| `call_timeout_seconds` | 默认 30 秒，单次调用上限。 |
| `idle_timeout_seconds` | 默认 300 秒，直连空闲连接回收。中转连接仅在同一轮内复用，每轮结束就关闭。每个 Runner 对每个服务默认单并发。 |

服务并行发现。刷新确认期限至少为 `control_receive_timeout_seconds`，也会覆盖最长的已启用服务连接超时与控制帧发送预算。真实 Windows 中转需保证该机的 SSH
反向连接与待测服务网络可用。关闭中转或目标变更后，下一轮会禁用失配的 MCP 服务。
开发机直连服务和原内置工具仍可使用。旧 Runner 模板显示不支持此功能。

配置文件认证头需要更新 Coordinator。Runner 需要已有的认证头能力。Coordinator 从私有
`cloud.json` 或启动环境取得头值，随刷新控制帧交给 Runner。凭证不写入配置快照。
修改环境变量后重启 Coordinator，再在原任务刷新工具；直接修改 `headers` 后可以直接刷新。
Windows 隧道入口仍匹配 MCP URL 的主机、端口和 TLS 类型；私有 CA 由 Runner 信任，可在 Runner
启动环境使用 `SSL_CERT_FILE` 指向受信 CA bundle。使用域名时证书须覆盖该域名，使用 IP 时须覆盖该 IP。

## update.example.json

| 字段 | 用途 |
| --- | --- |
| `work_dir` (work_dir) | 更新器持久日志、发行下载缓存根，需私有权限。 |
| `index` (index) | 可信索引HTTPS URL或绝对文件路径；示例固定0.5.8且需发布后可用。 |
| `trusted_prefixes` (trusted_prefixes) | 下载URL及重定向前缀白名单；仅保留实际可信资产来源。 |
| `platform` (platform) | 目标开发机架构linux-x86_64或linux-arm64，不填本地Windows架构。 |
| `build` (build) | 开发机模板构建参数。 |
| `template_id` (build.template_id) | 新模板ID，须与profile选择一致。 |
| `backend_id` (build.backend_id) | 实际后端身份，须与模板预检回执一致。 |
| `image_tag` (build.image_tag) | 本机构建镜像标签；最终使用预检后的不可变身份。 |
| `docker_host` (build.docker_host) | 已有Docker socket unix URL；须实际运维身份可访问。 |
| `docker_executable` (build.docker_executable) | Docker CLI程序路径。 |
| `package_index` (build.package_index) | 构建依赖使用的HTTPS包源，不带凭证。 |
| `uid` (build.uid) | 实际非root运行UID，须与后端和持久目录权限匹配。 |
| `gid` (build.gid) | 实际非root运行GID，不能照抄示例1000。 |
| `build_timeout_seconds` (build.build_timeout_seconds) | 构建最长秒数。 |
| `build_proxy_env` (build.build_proxy_env) | 代理环境变量名映射，例如HTTPS_PROXY映射NYT_BUILD_PROXY；值是变量名而非代理URL。 |
| `connection_file` (connection_file) | 私有Owner连接JSON绝对路径；Windows替换为C:/private/connection.json。 |
| `task_data_dir` (task_data_dir) | 实际Coordinator数据根，用于维护备份；不是work_dir。 |
| `cloud_settings_file` (cloud_settings_file) | 实际Coordinator配置路径。 |
| `recovery_dir` (recovery_dir) | 恢复目标根；恢复新根需同步后端allowed_host_paths。 |
| `channel` (channel) | 可信索引频道，默认stable；固定版本索引不会自动发现未来版本。 |
| `auto_activate` (auto_activate) | 完整构建预检成功后是否CAS激活新模板；不升级既有任务。 |
| `max_cache_bytes` (max_cache_bytes) | 受管缓存字节预算，不是用户保存目录配额。 |
| `min_free_bytes` (min_free_bytes) | 操作前要求的可用空间字节下限。 |
| `keep_backups` (keep_backups) | 保留有效备份数量下限。 |
| `gc_max_resources` (gc_max_resources) | 一次垃圾回收计划资源数量上限。 |
| `gc_backup_verify_bytes` (gc_backup_verify_bytes) | GC验证备份字节预算。 |
| `max_artifact_bytes` (max_artifact_bytes) | 单个发行附件下载字节预算。 |
| `max_index_bytes` (max_index_bytes) | 索引/清单读取字节预算。 |
| `request_timeout_seconds` (request_timeout_seconds) | 网络单次请求最长秒数。 |
| `io_bytes` (io_bytes) | 每次IO块字节数。 |
| `backup_max_bytes` (backup_max_bytes) | 一次备份字节预算。 |
| `backup_memory` (backup_memory) | 维护容器内存资源，如512m。 |
| `backup_cpus` (backup_cpus) | 维护容器CPU预算。 |
| `backup_pids` (backup_pids) | 维护容器进程数上限。 |
| `backup_max_files` (backup_max_files) | 备份最多文件数。 |

## remote-update.example.json

| 字段 | 用途 |
| --- | --- |
| `ssh_host` (ssh_host) | 已配置SSH别名；owned必填，事先配置密钥与主机指纹。 |
| `ssh_executable` (ssh_executable) | SSH可执行文件，默认从PATH查找。 |
| `remote_python` (remote_python) | 开发机运维venv的Python绝对路径；不能填本机解释器。 |
| `remote_settings` (remote_settings) | 开发机update.json绝对路径。 |
| `timeout_seconds` (timeout_seconds) | 请求或远程调用超时秒数，不代表后台操作停止。 |

## template-build.example.json

| 字段 | 用途 |
| --- | --- |
| `template_id` (template_id) | 新模板ID，须与profile选择一致。 |
| `backend_id` (backend_id) | 实际后端身份，须与模板预检回执一致。 |
| `image_tag` (image_tag) | 本机构建镜像标签；最终使用预检后的不可变身份。 |
| `docker_host` (docker_host) | 已有Docker socket unix URL；须实际运维身份可访问。 |
| `docker_executable` (docker_executable) | Docker CLI程序路径。 |
| `package_index` (package_index) | 构建依赖使用的HTTPS包源，不带凭证。 |
| `uid` (uid) | 实际非root运行UID，须与后端和持久目录权限匹配。 |
| `gid` (gid) | 实际非root运行GID，不能照抄示例1000。 |
| `build_timeout_seconds` (build_timeout_seconds) | 构建最长秒数。 |
| `build_proxy_env` (build_proxy_env) | 代理环境变量名映射，例如HTTPS_PROXY映射NYT_BUILD_PROXY；值是变量名而非代理URL。 |
| `wheel` (wheel) | 目标Linux平台wheel绝对路径，文件名须按实际资产替换。 |
| `wheel_sha256` (wheel_sha256) | 实际wheel的SHA256；全零仅占位，必须从已验证发行取得，不能直接构建。 |
| `kit_wheel` (kit_wheel) | 固定版本Kit wheel绝对路径。 |
| `kit_profile` (kit_profile) | 发行提供的工具链profile文件路径。 |
| `test_lock` (test_lock) | 发行提供的测试依赖锁文件路径。 |
| `base_image` (base_image) | 已核验基础镜像digest；示例全零不可使用，取正式manifest中的真实值。 |
| `output` (output) | 成功预检后写入真实模板回执的路径。 |
| `expected_runner_version` (expected_runner_version) | template-build中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `expected_runner_protocol` (expected_runner_protocol) | template-build中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `expected_state_schema` (expected_state_schema) | template-build中的数量或结构限制；示例值为当前模型默认值，调整会影响该子系统的拒绝/截断边界。 |
| `resume_existing` (resume_existing) | 仅显式恢复已有构建时启用，不猜测成功。 |

## 143AN 大项目与服务隧道配置增量

本节为0.5.8相对0.5.7的增量；本机、Coordinator及新Runner模板统一更新到0.5.8。旧配置显式写入的数值优先，不会被新版默认值覆盖。

- `input_limits`：未压缩总量/单文件均为2 GiB；归档2.5 GiB；32768文件、16 MiB元数据；本机缓存16 GiB；上传1800秒；发现500000目录项。仍逐块IO，不一次读2 GiB。缓存会保留历史输入，16 GiB不是自动清理目标，也不是磁盘预分配。
- 连接配置`max_response_bytes`默认64 MiB，覆盖完整输入包清单响应；仍使用有界流式读取。旧connection.json显式4 MiB需同步调整，见connection.example.json。Runner的input_wait_seconds默认1800秒。
- `result_limits`：结果工作区（包括未改动输入）总量3 GiB、65536文件、16 MiB元数据，避免旧256 MiB阻止大项目结束。代码patch仍64 MiB，报告/接收预算不随项目输入无限增大。修改一个巨大二进制文件仍可能触发补丁上限，应缩小修改或单独保存报告。
- `network_id`：profiles条目新增可选字段，指向Coordinator `networks`中的管理员分配项。省略时不绑定网络，不启动反向转发。不是用户认证字段。
- `ssh_host`：owned控制连接或配置network_id时必填，使用Windows用户SSH配置中的Host别名，不接受任意SSH命令。
- `reconnect_seconds`：连接异常后的后台检查间隔，默认5秒。SSH保活仍由keepalive_seconds/count控制。关闭网页不关连接，停止本机服务才回收受管SSH进程。
- `networks`：Coordinator新增可选映射，默认空；示例含alice/bob两个分配（本节通用入口属于143AO开发版，0.5.8仍为固定两项）。每项`bind_host`为容器可达的私有IPv4桥地址，`ports`为入口ID到端口的映射（1–64项），每个端口均可用于任意TCP协议，全部端点不得重复。禁止0.0.0.0、loopback、公网地址。它只是允许的转发分配，不替代SSH服务端授权和防火墙。
- 本机页面设置保存到profiles文件同目录的`<profiles文件名去扩展名>.networks.json`。保存已选目标、启停意图及原切换操作；不修改profiles.json、connection.json或settings.toml。文件由程序私有写入，不建议手工编辑；有未完成切换时用页面取消或重试。
- Coordinator在registry.meta保存切换状态；运行准入同事务检查。切换断线不会自动解锁，避免旧地址仍被使用。网络元信息随每轮命令更新，旧任务容器无需重建。

详细管理员/Windows操作、TLS使用及验收见[服务隧道手册](../../cloud-service-tunnels.md)。

### 通用服务入口（143AO）

Windows继续使用profiles中的network_id，不增加全局网络列表。页面和本机后端只允许本机配置的profile；管理员须给不同Windows分配不重复network_id。这是配置级可见性，不是Owner凭证级租户隔离。一个人可配置多个profile；owned控制连接须使用不同本机endpoint端口。

每个ports键为稳定入口ID（1–64字符，字母数字开头，其余允许点、下划线、横线），值为1024–65535整数；端口不能重复，空集合或超过64项拒绝。页面可填写入口name（可选，最多80字符）和address；支持http/https/grpc/grpcs/tcp/tls，tcp/tls必须显式端口，不接受路径、凭证、查询参数。旧http_port/grpc_port会读取为ports.http/ports.grpc，新旧字段不能混用。完整升级步骤见[服务隧道](../../cloud-service-tunnels.md#8-从058升级通用入口)。

## 143AQ预算与新字段（取代上面的0.5.8历史预算）

- Windows `cloud_tasks.input_limits` 和 Linux `input_limits`：单文件/每批20GiB、归档22GiB、100000文件、元数据32MiB、缓存64GiB。`max_downloads=64`限制共享不可变输入并发读，`min_free_bytes=268435456`为Runner展开文件后的保留空间。
- Linux `artifact_limits`：单文件20GiB、缓存64GiB、最多4096份、`segment_bytes=8388608`；`transfer_timeout_seconds`按一段上传计算，不限制整个大文件总时长。
- 两端 `result_limits`：每轮output总量20GiB、最多1000产物，新任务不扫描工作区，不生成patch；旧patch/Git字段只服务已有基线结果及仍使用的CLI接口。
- Windows `receive_limits`：单文件20GiB、1000文件、8MiB分段、缓存/交付保守预算128GiB；目标目录不是缓存目录。`transfer_timeout_seconds=120`按一段下载计。
- Windows `cloud_tasks.queue_poll_seconds=1.0`：单条待发送消息核对间隔。`download_link_seconds=300`：本机单文件浏览器授权寿命；`max_download_links=64`：有效链接数量上限。全部设置均有边界校验。
- Linux新Runner模板预检会生成 `file_transfer_version=1`。不要编辑旧回执的数字；必须真实构建并预检同源模板。旧0.5.8模板不接受新空任务。
- workflow验收JSON的connection_file现在可指向同一connections.json，多profile时增加profile_id；仍兼容原单connection文件。此脚本原有基线回归与新附件场景是不同验收，不把旧基线探针当作附件端到端证明。
