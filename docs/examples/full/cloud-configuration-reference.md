# 0.5.7 全量配置参考与逐项说明

这些是示意配置，不是可直接连接公司环境的部署材料。所有端口、bridge、路径、UID/GID、实例ID与凭证都须替换。JSON严格不支持注释；本说明与JSON并列提供，不能往JSON加 _comment 字段。真实配置请放仓库外私有目录；Linux文件0600、目录0700，Windows限制ACL。无真实密钥。

## 使用顺序

1. 本地settings.full.toml是从旧0.5.0参考迁移的完整场景示例，保留原逐项注释；其中Auto、代理、MCP和子Agent为示例选择，并非默认启用。先按需删去这些场景设置，再配置cloud_tasks。
2. 开发机准备已有OpenSandbox、非root身份、bridge；cloud.example.json复制为私有cloud.json。templates为空不是通过预检的模板，按 ../../cloud-s091-windows-and-linux.md 产生真实回执再登记。
3. 开发机models.json沿 ../models.example.json；新版本不改变模型配置格式，不把key放到Runner。
4. 将实际Owner身份填入connection；Windows的profiles.connection_file改为C:/private/connection.json，settings中的profiles_file也须是本机绝对路径。开发机更新器使用另一份开发机路径的connection。
5. external模式自行开SSH转发；owned添加ssh_host并预先配置SSH认证，不并行占用同一端口。
6. 只有需要更新/备份时才配置update和remote-update；普通启动不要求这两个文件。Coordinator程序先独立升级，Runner更新不会重启Coordinator。维护标记跨重启保留，验证后显式leave。

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
| `max_response_bytes` (max_response_bytes) | 单次响应字节上限。 |

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

## update.example.json

| 字段 | 用途 |
| --- | --- |
| `work_dir` (work_dir) | 更新器持久日志、发行下载缓存根，需私有权限。 |
| `index` (index) | 可信索引HTTPS URL或绝对文件路径；示例固定0.5.7且需发布后可用。 |
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
