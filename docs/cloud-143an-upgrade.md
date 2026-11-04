# 0.5.8：Linux / Windows 升级、配置与新增功能

适用于已有0.5.7云端部署。0.5.8提供2GiB输入、项目范围浏览与预览、两个受管服务反向隧道。以下路径、SSH别名、用户和端口均为示例，按当前部署修改；不要覆盖真实配置整份照抄，也不要重新生成Owner token、instance_id或数据根。

## 1. 哪些包需要更新

| 位置 | 更新内容 | 目标版本 |
|---|---|---|
| Windows本机venv | naiyue-test-agent（含前端、fd/rg） | **0.5.8** |
| Linux Coordinator venv | naiyue-test-agent[cloud] | **0.5.8** |
| Linux独立Updater/模板构建venv（若与Coordinator分开） | naiyue-test-agent[cloud] | **0.5.8** |
| 新任务Runner镜像 | 用0.5.8 Linux wheel重新构建、预检、登记并激活 | **0.5.8** |
| AITest Kit | 保持，不需要版本升级 | **0.6.0** |
| Python、Docker、OpenSandbox、Node | 本次没有要求版本升级；Windows不需要Node或Docker | 沿用已验收版本 |

安装新版Coordinator不会自动升级Runner镜像。已有任务保留原模板和原network_id；无网络绑定的0.5.7任务可继续运行，本次已修正空services字段的旧Runner兼容问题。需要两个个人服务隧道的任务，应在0.5.8模板激活、profile配置network_id后**新建**；仅升级旧任务模板不会补写network_id。

附件入口：[v0.5.8 Release](https://github.com/tlzmw001/naiyue-test-agent/releases/tag/v0.5.8)。`naiyue-cloud-0.5.8.zip`包含四平台Agent wheel、Kit、固定工具链、配置例子、验收脚本和本指南；不是完整依赖wheelhouse，不包含Python解释器、公司镜像或凭证。正常联网安装可直接使用PyPI；受限网络可使用附件wheel并沿用公司批准的依赖包源。

## 2. Linux开发机：更新管理程序

先安排所有使用者停止运行中的测试和后台服务，按原运维流程进入维护并核对排空状态；不要在运行中的venv直接替换包。若已配置Updater，可用旧CLI执行maintenance enter/status，待排空后再按现有systemd/前台方式正常停止Coordinator。已有数据目录、容器和镜像保持。

下面变量只用于本次终端，不会修改配置：

```bash
export CLOUD_VENV='/实际路径/coordinator-venv'
export CLOUD_SETTINGS='/实际私密目录/cloud.json'
export UPDATE_JSON='/实际私密目录/update.json'
# 确认旧Coordinator已经正常停止后：
"$CLOUD_VENV/bin/python" -m pip install --upgrade 'naiyue-test-agent[cloud]==0.5.8'
"$CLOUD_VENV/bin/nyt" --version
```

独立Updater的venv也执行同版本安装；同一venv则无需重复。离线wheel替代方式：

```bash
"$CLOUD_VENV/bin/python" -m pip install '/解压目录/wheels/naiyue_test_agent-0.5.8-py3-none-manylinux_2_17_x86_64.whl[cloud]'
```

ARM64开发机使用aarch64 wheel。安装依赖仍需可达公司包源或已有wheelhouse。不要使用Windows wheel构建Linux Runner。

## 3. Linux开发机：修改配置和SSH授权

### 3.1 Coordinator的cloud.json

增加`networks`，每台Windows分配不同network_id和两个端口。例中的172.18.0.1必须换成**现有任务容器可以访问的开发机私有桥地址**，不能用127.0.0.1或0.0.0.0。两个端口不能与Coordinator、其他用户或其他服务冲突。

```json
"networks": {
  "alice": {"bind_host":"172.18.0.1","http_port":21001,"grpc_port":21002},
  "bob":   {"bind_host":"172.18.0.1","http_port":21003,"grpc_port":21004}
}
```

如旧配置显式填写预算，修改对应字段；省略时会使用0.5.8默认值：

```json
"input_limits": {
  "max_file_bytes":2147483648,
  "max_input_bytes":2147483648,
  "max_archive_bytes":2684354560,
  "max_files":32768,
  "max_metadata_bytes":16777216,
  "max_cache_bytes":17179869184,
  "upload_timeout_seconds":1800,
  "max_discovery_entries":500000
},
"result_limits": {
  "max_result_bytes":3221225472,
  "max_files":65536,
  "max_metadata_bytes":16777216
}
```

这是合并到原对象的片段，保留其他自定义字段。2GiB指未压缩输入总量；结果工作区统计未改动输入，故扩大到3GiB。代码补丁仍64MiB，报告/接收缓存预算不变。磁盘峰值包括输入副本、归档、容器工作区和Git对象，不能只预留2GiB。

### 3.2 Linux端connection.json

若显式配置了`max_response_bytes:4194304`，改为`67108864`（64MiB），以接收大文件清单。保留endpoint、owner_authority、owner_token和instance_id原值。

### 3.3 SSH服务端

管理员一次性审查并合并以下规则；多人可共用SSH用户名，但每台电脑使用自己的network_id和端口。推荐各自密钥便于撤销。

```text
Match User nyt-shared
    AllowTcpForwarding yes
    GatewayPorts clientspecified
    PermitListen 172.18.0.1:21001 172.18.0.1:21002 172.18.0.1:21003 172.18.0.1:21004
    PermitOpen 127.0.0.1:18701
```

`18701`替换为原Owner API端口；其他已有SSH转发用途由管理员合并允许项。运行`sshd -t`检查后再按公司流程重载sshd，不要覆盖整份sshd配置。按公司网络策略仅允许容器网络访问这些桥接端口。共享SSH和Owner凭证不是恶意用户间的强权限隔离；本功能保证任务使用配置好的个人入口，不增加租户鉴权。

完成上述配置后按原方式启动Coordinator：

```bash
"$CLOUD_VENV/bin/nyt" cloud serve --settings "$CLOUD_SETTINGS"
```

该命令前台运行；已有进程管理器继续管理同一命令。Owner保持回环监听，真实配置/数据根不变。

## 4. Linux开发机：更新Runner模板

在原`update.json`中调整：

- `index`：`https://github.com/tlzmw001/naiyue-test-agent/releases/download/v0.5.8/index.json`
- `trusted_prefixes`：允许上述v0.5.8下载目录和`https://release-assets.githubusercontent.com/`；需要保留旧发行恢复能力时，保留已有旧前缀。
- `build.image_tag`：建议使用新标签，例如`nyt-runner:0.5.8`。
- 本文采用`auto_activate:true`，预检后自动激活给新任务；若保留false，update完成仅表示构建/登记ready，仍需按cloud-updates.md第4节显式activate，并核对当前激活模板。
- `build.template_id`、backend、Docker入口、UID/GID、公司包源、数据根、connection_file等沿用已验收实际值；`channel`仍为`stable`。

执行check后确认目标版本，再受理更新并保存原操作ID：

```bash
"$CLOUD_VENV/bin/nyt" cloud release check v0.5.8 --settings "$UPDATE_JSON"
export UPDATE_ID=$("$CLOUD_VENV/bin/python" -c 'import uuid; print(uuid.uuid4())')
"$CLOUD_VENV/bin/nyt" cloud release update v0.5.8 --command-id "$UPDATE_ID" --settings "$UPDATE_JSON"
"$CLOUD_VENV/bin/nyt" cloud release status "$UPDATE_ID" --settings "$UPDATE_JSON"
```

如果Updater位于独立venv，改用该venv的nyt。更新是后台操作，命令受理不等于构建已完成；反复status直到completed，失败/中断使用原UPDATE_ID排查/resume。新模板预检必须确认Runner 0.5.8、Kit 0.6.0、非root及原生工具通过。直接使用template-build配置时，wheel指向0.5.8 Linux包、wheel_sha256取SHA256SUMS，并将expected_runner_version设为0.5.8；不能手写成功回执。

新模板激活后只影响新任务。确认管理程序和模板都正常后，再退出维护：

```bash
export LEAVE_ID=$("$CLOUD_VENV/bin/python" -c 'import uuid; print(uuid.uuid4())')
"$CLOUD_VENV/bin/nyt" cloud release maintenance leave --command-id "$LEAVE_ID" --settings "$UPDATE_JSON"
```

日常可从Windows使用已配置的RemoteUpdateSettings触发同样的update/status（将`--settings`换成`--remote`），但本次首次更新Coordinator程序/sshd配置仍按管理员的现有部署方式操作。

## 5. Windows：停止旧服务并安装

先用旧venv中的CLI正常停止本机服务；配置路径按当前实际值填写。默认路径部署可省略对应参数。

```powershell
$LocalSettings = 'C:\实际私密目录\settings.toml'
$LocalModels = 'C:\实际私密目录\models.json'
$Py = 'C:\当前venv\Scripts\python.exe'
$Nyt = 'C:\当前venv\Scripts\nyt.exe'
& $Nyt service stop --settings $LocalSettings --config $LocalModels
& $Py -m pip install --upgrade 'naiyue-test-agent==0.5.8'
& $Nyt --version
```

使用附件wheel时，将安装行替换为：

```powershell
& $Py -m pip install 'C:\解压目录\wheels\naiyue_test_agent-0.5.8-py3-none-win_amd64.whl'
```

不用npm构建前端；新wheel已包含前端和原生工具。安装在新venv时继续使用原settings/models/data路径，不要复制或删除原数据根。

## 6. Windows：配置三个文件

### 6.1 settings.toml

保留原`cloud_tasks.profiles_file`。显式旧预算按下列值修改；没有写过这些字段时可以继续省略，使用默认值。

```toml
[cloud_tasks.input_limits]
max_file_bytes = 2147483648
max_input_bytes = 2147483648
max_archive_bytes = 2684354560
max_files = 32768
max_metadata_bytes = 16777216
max_cache_bytes = 17179869184
upload_timeout_seconds = 1800
max_discovery_entries = 500000

[cloud_tasks.result_limits]
max_result_bytes = 3221225472
max_files = 65536
max_metadata_bytes = 16777216
```

不要重复追加同名TOML节；在原节内修改。其他配置保持。完整注释见`docs/examples/full/settings.full.toml`。

### 6.2 connection.json

只将显式旧`max_response_bytes`改为`67108864`。原endpoint本地端口、owner_authority、Owner token和instance_id保持，不要从示例拷贝凭证。

### 6.3 profiles.json

在本人实际使用的profile中增加/修改这些字段：

```json
"tunnel":"owned",
"ssh_host":"nyt-dev",
"network_id":"alice",
"reconnect_seconds":5
```

profile_id、connection_file、template_ids、default_template保持与原配置/已激活模板一致。第二位用户填写自己的`bob`，不能照抄alice。`ssh_host`是本人`%USERPROFILE%\.ssh\config`中的Host别名；用户名可以共同使用开发机账号。首次SSH指纹核对、无密码BatchMode登录详见[服务隧道手册](cloud-service-tunnels.md)。

若原来用手工`ssh -L`、tunnel=external，切为owned前请正常关闭那个手工转发，避免本机Owner端口被占用。此后无需每天手工开正向/反向隧道。只有修改待测服务地址时在页面操作；profiles/connection/预算修改需要重启本机服务才读取。

启动：

```powershell
$Workspace = 'C:\实际项目目录'
& $Nyt --settings $LocalSettings --config $LocalModels --workspace $Workspace
```

版本输出应为0.5.8。浏览器强刷一次。新网络状态自动保存为profiles文件旁的`*.networks.json`，不要手工编辑或删除未完成切换状态。

## 7. 新功能怎么点击

### 7.1 配置HTTP和gRPC入口

1. 打开项目会话，展开**云端任务列表**，点击**服务连接**。中央弹出配置窗口。
2. 选择刚配置network_id的云端连接；看到管理员分配的HTTP、gRPC容器入口。
3. HTTP填写`http://域名:端口`或`https://域名:端口/基础路径`；gRPC填写`grpc://域名:端口`或`grpcs://域名:端口`。
4. 点**测试HTTP端口 / 测试gRPC端口**。这里只确认本机到目标TCP可达；不等于业务请求或证书验证成功。
5. 点**应用HTTP配置 / 应用gRPC配置**，等待“隧道已建立”。至少启用一个入口后再启动绑定网络的任务。
6. 点关闭即可；关闭弹窗/网页不会停止隧道。停止整个本机服务或电脑离线会断开，服务重启后自动恢复启用状态。

换地址：先让关联任务停止运行及后台服务，回到同一窗口修改地址并应用；无需重启本机服务或重建已经绑定的容器。下一轮使用新目标。两个端口同时提供一套HTTP目标和一套gRPC目标，不能让同一network_id的旧任务继续用旧HTTP目标。一个入口失败不阻止另一个重连。

主动停用用**断开HTTP / 断开gRPC**，重启后仍保持断开。切换未完成时用**重试原切换 / 取消切换**，不通过删除状态文件强行解除。TLS证书仍严格校验，Agent使用原域名/authority及给定连接入口；程序不是全局透明代理。

### 7.2 非Git与大项目范围选择

1. 云端任务列表 → **新增任务**，选择云端连接和模板。
2. 非Git项目会显示说明并禁用Git来源，使用**选择项目文件和目录**。
3. Git项目可保留**项目当前Git状态**；取消**导入全部Git项目文件**后选择子范围，仍保留Git来源回执。
4. 勾选目录表示递归选择；点**打开**浏览目录并取消不需要的文件。被整体排除的子目录须先在上级重新选中，再调整其中的文件。
5. 也可展开**已选路径 / 手动填写**，每行填写一个项目相对路径，例如`src`、`tests`、`api\proto`；不支持盘符、绝对路径、`..`和通配符。
6. 点**预览导入范围**，核对文件数、大小和上限；没有预算错误后点击**导入并准备任务**。改动选择后必须重新预览。

安全排除继续生效，不能上传.env、私钥、.git及被禁止的依赖缓存。2GiB允许上限不代表应上传所有文件；优先选择本次任务实际需要的目录。

## 8. 本次Windows验收重点

- 中文/空格项目目录、实际Src但手填src、选择目录再排除文件，预览和导入成功。
- 两台Windows共用SSH账号但不同network_id，容器分别访问各自服务，校验实际响应内容。
- HTTP/HTTPS、明文/TLS gRPC分别验证，错误证书必须失败。
- 空闲切换目标、HTTP失败但gRPC恢复、关闭网页保持、主动断开后重启不重连。
- SSH端口占用、断网、VPN切换、未完成切换取消/恢复；不得误报成功或出现多余终端窗口。

四平台CI的文件/进程检查不等于公司SSH端到端通过。包内可再次运行`python -m scripts.release_network_smoke`检查本机临时目录、归档和子进程；输出real_ssh=not_tested是明确范围，不是失败。
