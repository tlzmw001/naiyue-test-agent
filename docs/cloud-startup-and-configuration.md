# 0.5.7：两端启动与配置迁移

对比基准是正式标签v0.5.6。已经运行S11 R4候选的用户，直接看第1—3节和第5节；R4到0.5.7没有新增必填配置。下面变量必须填写实际路径/端口，不自动修改真实文件。发布包不包含凭证或完整依赖wheelhouse。

## 1. 升级前区分两种部署

- 正式0.5.6旧sandbox：先用旧CLI正常停止本地共享服务，再升级；旧sandbox会话/配置不兼容。云端Coordinator、Runner模板与连接文件是新链路，首次准备见[首次部署手册](cloud-s091-windows-and-linux.md)第2—5节。保留旧数据、镜像、卷，不自动转换或删除。
- 已有B云端部署（S11/R4）：保留原cloud根、Owner身份、profile、connection和后端。先维护排空，正常停Coordinator，再安装0.5.7并重启；新Runner模板另行构建预检。已存在任务仍固定旧模板，需显式upgrade。
- `nyt cloud release update`更新Runner发行，不安装/重启Coordinator。发行v0.5.7默认要求Coordinator至少0.5.7；先更新管理程序，再做Runner更新。禁止在运行中替换venv文件。

## 2. Linux开发机：管理程序与云端任务

继续使用已批准的Docker/OpenSandbox后端、bridge与非root服务身份。后端已在运行则复用，不重新创建数据根或重装Docker。Coordinator本身不挂Docker socket；构建模板的运维命令可以使用已有受控Docker权限。

以下变量填写原部署的真实值，`CLOUD_SETTINGS`是Coordinator JSON，不是本机settings.toml或Updater JSON：

```bash
export CLOUD_VENV='/实际路径/云端venv'
export CLOUD_SETTINGS='/实际私密目录/cloud.json'
# 确认旧Coordinator已按原部署方式正常停止后安装：
"$CLOUD_VENV/bin/python" -m pip install --upgrade 'naiyue-test-agent[cloud]==0.5.7'
"$CLOUD_VENV/bin/nyt" --version
"$CLOUD_VENV/bin/nyt" cloud serve --settings "$CLOUD_SETTINGS"
```

最后一条命令持续前台运行；常驻时使用公司现有进程管理器运行同一命令。没有`nyt cloud service start`。Owner继续绑定loopback，task监听专用bridge地址，不能把Owner开放为0.0.0.0。

首次模板准备使用附加包的Kit与工具链，按首次部署手册生成真实TemplateReceipt；不能手写预检通过回执。已有Updater配置时，在更新其可信index后执行：

```bash
export UPDATE_JSON='/实际私密目录/update.json'
"$CLOUD_VENV/bin/nyt" cloud release check v0.5.7 --settings "$UPDATE_JSON"
export UPDATE_ID=$("$CLOUD_VENV/bin/python" -c 'import uuid; print(uuid.uuid4())')
"$CLOUD_VENV/bin/nyt" cloud release update v0.5.7 --command-id "$UPDATE_ID" --settings "$UPDATE_JSON"
"$CLOUD_VENV/bin/nyt" cloud release status "$UPDATE_ID" --settings "$UPDATE_JSON"
```

保存UPDATE_ID，断线后查询/恢复原ID，不能重新生成ID猜测成功。新模板激活只影响新任务，已有任务按[更新手册](cloud-updates.md)显式upgrade。维护enter/leave、备份恢复按该手册执行；恢复到新根时还需同步OpenSandbox的allowed_host_paths。

维护标记跨Coordinator重启保留。确认新Coordinator与目标发行/模板正常后，必须退出维护，否则新任务/新一轮运行仍被拒绝：

```bash
export LEAVE_ID=$("$CLOUD_VENV/bin/python" -c 'import uuid; print(uuid.uuid4())')
"$CLOUD_VENV/bin/nyt" cloud release maintenance leave --command-id "$LEAVE_ID" --settings "$UPDATE_JSON"
```

## 3. 本地Windows：共享服务与连接

先在原环境设置实际路径，并用**旧版**CLI停旧服务，确认收尾后再安装。若一直使用默认配置，可以省略对应路径参数。

```powershell
$LocalSettings = 'C:\实际私密目录\settings.toml'
$LocalModels = 'C:\实际私密目录\models.json'
$Workspace = 'C:\实际项目目录'
$OldNyt = 'C:\原venv\Scripts\nyt.exe'
& $OldNyt service stop --settings $LocalSettings --config $LocalModels
```

完成第4节配置迁移后，在选定venv安装并启动：

```powershell
$Py = 'C:\选定venv\Scripts\python.exe'
$Nyt = 'C:\选定venv\Scripts\nyt.exe'
& $Py -m pip install --upgrade 'naiyue-test-agent==0.5.7'
& $Nyt --version
& $Nyt --settings $LocalSettings --config $LocalModels --workspace $Workspace
```

`nyt`自动启动或复用共享服务并打印浏览器地址，没有`nyt service start`。安装wheel不需要运行npm；wheel已包含前端、rg/fd。Python仍需>=3.12.4。

如果profile使用`tunnel="external"`，在另一个终端保留正向转发；先把下列变量填为connection/cloud.json对应的真实值：

```powershell
$SshHost = '已配置的SSH别名'
$LocalOwnerPort = [int](Read-Host 'connection.endpoint的本地端口')
$OwnerPort = [int](Read-Host 'cloud.json的Owner端口')
ssh -N -T -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
  -L "127.0.0.1:${LocalOwnerPort}:127.0.0.1:${OwnerPort}" $SshHost
```

`owned`模式由应用建立同一类转发，必须配置ssh_host，不另开占用相同端口的external转发。SSH alias/密钥/host key须事先配置；产品不修改SSH文件。

日常只读检查、日志与正常停止：

```powershell
$ConnectionJson = 'C:\实际私密目录\connection.json'
& $Nyt cloud project list --settings $ConnectionJson
& $Nyt service logs --settings $LocalSettings --follow
& $Nyt service stop --settings $LocalSettings --config $LocalModels
```

logs与stop是独立操作，不要复制后把正在使用的服务立即停掉。记忆后台详细日志用界面的task_id查询；物理位置为配置data_dir下的`service/service.log`。本地升级不会自动启动/更新开发机服务。

## 4. 正式0.5.6 → 0.5.7配置差异

### 删除：旧settings.toml中的配置

| 删除项 | 范围 |
| --- | --- |
| `[sandbox]` | docker_executable、context、image/default_profile、cpus、memory_bytes、pids_limit、RPC/stop/message/output/operation/poll预算等整节 |
| `[sandbox.ssh]` | host、executable、docker_executable、socket_path、connect_timeout_seconds整节 |
| `[sandbox.network]`、`[sandbox.lifecycle]`、`[sandbox.profiles.*]` | 全部删除；旧external/none网络模式或image字符串不能直接转换成云端配置 |
| `[delivery]` | max_commits、max_operations_per_project整节 |
| `[mcp.servers.<id>].allow_sandbox` | 所有服务中的该字段删除；不表示云端任务可调用本机MCP |

新加载器会拒绝这些旧字段，不能仅追加cloud_tasks后保留旧节。这里只修改配置，不删除用户数据或旧环境资源。

### 新增：本地settings.toml的cloud_tasks

最小配置：

```toml
[cloud_tasks]
profiles_file = 'C:/实际私密目录/profiles.json'
```

不配置profiles_file时，纯本机功能仍可用。直接字段默认值如下；无需为了升级把所有默认值抄入真实配置。

| 字段 | 默认 |
| --- | --- |
| profiles_file | 未设置；启用云端时填绝对路径 |
| max_preparations / max_commands / max_controls | 4 / 16 / 4 |
| runner_wait_seconds / runner_poll_seconds | 15 / 0.1 |
| max_observers | 8 |
| stream_seconds / stream_send_timeout_seconds | 1800 / 15 |
| input_limits / receive_limits / result_limits | 各自默认对象 |

输入默认单文件64MiB、总量128MiB、归档160MiB、4096文件、缓存512MiB；接收缓存512MiB/并发4/单文件64MiB；结果默认目录`.nyt-output`、单结果256MiB、最多64结果/4096变化文件/30产物。具体预算以[CloudTaskSettings](../naiyue_test_agent/cloud_task_settings.py)及其三个合同类为准。

### 新增：本地profiles.json和connection.json

profiles顶层为`{"profiles":[...]}`；每条必填profile_id、connection_file绝对路径、template_ids、default_template。tunnel默认external；owned额外填写ssh_host。其余默认：ssh_executable=ssh、connect_timeout_seconds=15、keepalive_seconds=15、keepalive_count=3、poll_seconds=0.1、shutdown_seconds=5。

connection必填endpoint、owner_authority、owner_token、instance_id；默认timeout_seconds=60、io_bytes=262144、max_response_bytes=4194304。endpoint是本地转发地址，owner_authority是开发机Owner原authority，不能把两者混写。token与instance_id取自已有实际部署，不能从例子复制或随意重生成。

### 新增：开发机cloud.json

这是独立Coordinator配置，不嵌入本地settings.toml。必填data_dir、owner_listen、task_listen、owner_token、task_token_ttl_seconds；实际执行还需要backend.endpoint/backend_id/api_key、真实templates回执、model_proxy.config_path。原生OpenAI/Anthropic provider按ID配置model_proxy.upstreams；兼容/ollama沿模型配置base_url。

| 原概念 | 新位置与默认 |
| --- | --- |
| sandbox.cpus | task_cpu=2，整数 |
| sandbox.memory_bytes | task_memory="2Gi"，不再是bytes数值 |
| lifecycle.max_running / max_retained | max_active_environments=2 / max_retained_environments=10 |
| lifecycle.idle_stop_seconds | idle_stop_seconds=1800 |
| SSH、Docker、镜像集中在本地sandbox | 本地profile/connection与开发机backend/模板构建各司其职 |
| 删除清场预算 | resource_limits.cleanup_timeout_seconds=30；扫描scan_timeout_seconds仍为2秒 |

其他可配置面包括protected_roots、resource_limits、history_limits、observation_limits、input_limits、artifact_limits、result_limits、runner_permissions、tool_environment和subagents；详见[CloudSettings](../naiyue_test_agent/application/cloud/settings.py)。凭证只留在相应私有配置，模型key不放入Runner镜像或输入包。

### 可选新增：更新与备份配置

普通启动不要求Updater配置。Linux UpdateSettings必填work_dir、index、trusted_prefixes、platform、build、connection_file；已有任务升级/备份恢复另用task_data_dir、cloud_settings_file、recovery_dir、maintenance_receipt。

build必填template_id、backend_id、image_tag、docker_host、package_index、uid、gid；默认docker_executable=docker、build_timeout_seconds=900、build_proxy_env={}。build_proxy_env的值是环境变量**名字**，不是代理URL或凭证。

Windows RemoteUpdateSettings必填ssh_host、remote_python、remote_settings；默认ssh_executable=ssh、timeout_seconds=60。配置后可以从Windows执行`nyt cloud release check/update/status ... --remote REMOTE_JSON`，实际构建/安装准备仍在开发机完成。

UpdateSettings默认channel=stable、auto_activate=true、max_cache_bytes=2GiB、min_free_bytes=256MiB、keep_backups=3、gc_max_resources=256、gc_backup_verify_bytes=8GiB；max_artifact_bytes=256MiB、max_index_bytes=1MiB、request_timeout_seconds=60、io_bytes=256KiB；backup_max_bytes=4GiB、backup_memory=512m、backup_cpus=1、backup_pids=64、backup_max_files=100000；download_proxy默认未设置。完整约束见[更新配置](../naiyue_test_agent/cloud_install/settings.py)与[更新手册](cloud-updates.md)。

本版固定索引地址为`https://github.com/tlzmw001/naiyue-test-agent/releases/download/v0.5.7/index.json`；它固定0.5.7，不承诺自动发现未来版本。trusted_prefixes须明确允许该仓库发行路径及实际GitHub资产重定向域，不能放开任意URL。离线方式把index、manifest及其引用文件放在同一受保护目录，index填绝对文件路径。

### 保持不变

models.json格式与受控示例相对v0.5.6无变化；memory设置未新增字段或改默认值；storage.data_dir仍是已有配置。官方受控示例为docs/examples/settings.example.toml及models.example.json，个人settings.full.toml不是版本对比基准。

## 5. S11 R4候选 → 正式0.5.7

无新增必填配置；独立30秒清场预算已经包含在R4中。主要新增是记忆状态/日志收敛与正式版本身份。保留已验收的配置路径、后端和实例身份，完成本机/Coordinator升级与同版Runner模板准备。公司网络及未测性能指标仍按原验收记录，不因版本发布自动视为通过。
