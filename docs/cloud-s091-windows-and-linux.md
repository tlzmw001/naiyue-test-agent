# S09.1 Windows＋Linux 实机验收手册

产品源码基线：`de5f76aa87b54274fc4edffdc5423ca74382aac1`。本次只跑正式切换前的公司实机门禁。运行对象是随手册提供、按源码提交和SHA固定的候选；版本字符串仍可能是0.5.6，不能用`pip install -U naiyue-test-agent`替代候选安装。原S00 Linux/Windows三段通过继续有效，不重装Docker，不从头重跑S00。

候选界面已构建为`cloud-candidate`。门禁通过后，才由开发侧继续S09.1正式入口切换和必要A清理。本手册不会替用户删除旧A、更新全局配置或部署自动升级服务。

## 0. 两台机器的分工及执行边界

| 位置 | 本轮负责 |
| --- | --- |
| Windows | 独立候选venv/数据目录、本机主聊天与云端侧栏、正向SSH、反向业务隧道、结果接收/分析/代码交付 |
| Linux开发机 | 复用已安装Docker/OpenSandbox版本和专用bridge，准备新Runner模板，运行独立Coordinator，核对模型出站、容器隔离和持久性 |
| 当前开发侧 | 提供固定源码/两平台候选wheel/Kit/哈希；不远程执行公司验收，也不将本机测试当公司通过 |

先限定专属`S09_ROOT`、新控制容器名、镜像tag、监听端口和试验任务ID。只允许本轮资源；不做prune、不删除旧镜像/卷/用户库，不改原S00报告。修改sshd/全局Docker/系统代理/防火墙前，由当地AI给出精确diff、影响和恢复方法，用户确认后再做；不能用host任务网络、root任务、关闭TLS校验或放宽断言解决失败。

每个新PowerShell/SSH终端都需要重新填写本窗口用到的变量；变量不会跨窗口继承。端口或根目录改变后，必须同步两端配置。

普通协调器/任务UID使用同一个已批准的非root服务身份，使0600/0700文件可在停止、重建后继续访问。不要让Coordinator以sudo/root运行来绕过目录权限。可信OpenSandbox控制进程和显式构建步骤可使用已有Docker管理权限；Coordinator和任务不挂Docker socket。

## 1. 取得候选并核对身份

候选包含`candidate.json`、`SHA256SUMS`、`source.zip`、`wheels/windows-x86_64/`、`wheels/linux-x86_64/`、Kit wheel、候选核验脚本、本手册和一份待填验收汇总模板。`candidate.json.code_commit`是本次产品源码提交；手册提交可比它更新，但不能混用其他候选wheel/旧镜像。包内无真实key、代理凭证、owner token或SSH私钥。

Windows PowerShell中，将解压根目录和已有SSH别名填成实际值。Windows路径使用你真正的盘符，示例不能原样带占位文字执行：

```powershell
$Root = (Resolve-Path 'C:\你的独立目录\naiyue-s091').Path
$SshHost = '你的已有SSH别名'
Set-Location $Root
$Candidate = Get-Content .\candidate.json -Raw | ConvertFrom-Json
Get-Content .\SHA256SUMS | ForEach-Object {
  $Parts = $_ -split '  ', 2
  $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Root $Parts[1])).Hash.ToLower()
  if ($Actual -ne $Parts[0]) { throw "SHA mismatch: $($Parts[1])" }
}
$Candidate | Select-Object code_commit, python_files, kit_version
```

Linux也解压同一包到全新目录，执行`sha256sum -c SHA256SUMS`。不要让源代码目录、私密配置和cloud数据根混在一起。复制文件使用已批准的SSH/SFTP；不从聊天传凭证。

## 2. Linux盘点和独立Python环境

在Linux Bash填写变量。端口仅为本手册示例，先检查无冲突；改变时全链路同步。新root首次创建，已有同名目录先停止核对，不能直接覆盖。

```bash
export S09_ROOT="$HOME/naiyue-s091-实际候选短SHA"
export BUNDLE='/实际候选解压根'
export PY312='/已批准Python3.12解释器绝对路径'
export NET='原S00已验证的专用bridge名'
export BACKEND_PORT=14680
export OWNER_PORT=14690
export TASK_PORT=14691
export LOCAL_OWNER_PORT=14692
export S00_TOOLS_IMAGE='原S00实际tools镜像tag或ID'
export BASE_IMAGE='原S00已核对架构的python镜像@sha256:实际摘要'
export EXECD_IMAGE='原S00固定execd镜像@sha256:实际摘要'
export PACKAGE_INDEX='https://实际允许的包源/simple/'
"$PY312" -c 'import sys; assert sys.version_info >= (3,12,4); print(sys.version.split()[0])'
id
sudo docker version --format '{{.Server.Version}}'
sudo docker info --format 'ID={{.ID}} Architecture={{.Architecture}}'
sudo docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}'
ss -ltn
umask 077
mkdir -m 700 "$S09_ROOT"
mkdir -m 700 "$S09_ROOT/private" "$S09_ROOT/reports" "$S09_ROOT/backend-runtime" "$S09_ROOT/cloud-data" "$S09_ROOT/src"
unzip "$BUNDLE/source.zip" -d "$S09_ROOT/src"
"$PY312" -m venv "$S09_ROOT/venv"
export PY="$S09_ROOT/venv/bin/python"
"$PY" -m pip install "$BUNDLE"/wheels/linux-x86_64/*.whl
"$PY" -m pip install 'opensandbox==1.2.0' 'httpx-sse==0.4.3' 'python-dateutil==2.9.0.post0' 'six==1.17.0'
"$PY" "$BUNDLE/verify-candidate.py" --manifest "$BUNDLE/candidate.json"
export BACKEND_ID=$(sudo docker info --format '{{.ID}}')
export TASK_UID=$(id -u)
export TASK_GID=$(id -g)
export GATEWAY=$(sudo docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}')
```

宿主原先的Python3.10不能跑B产品。没有独立Python>=3.12.4时停在这里，让开发机AI按公司允许方式准备独立解释器；不要改`/usr/bin/python3`。如果开发机不是x86_64，停止并请求对应架构候选，不使用本包Linux x86_64 wheel。

## 3. Linux准备受信Runner镜像（包括AITest Kit）

先确认`TASK_UID/GID`非0。Kit必须使用包内0.6.0 wheel及冻结SHA，`/opt/nyt/runner`与`/opt/nyt/test`分离，不在任务启动时pip安装Agent。

模板构建调用真实`nyt cloud release prepare`。若普通`docker info`已可用，`DOCKER_EXECUTABLE`填实际docker绝对路径；否则在本轮私有目录使用现有sudo权限的窄wrapper，不新增docker组、不将socket交给Coordinator：

```bash
sudo -v
printf '#!/bin/sh\nexec sudo -n /usr/bin/docker "$@"\n' > "$S09_ROOT/private/docker-build"
chmod 700 "$S09_ROOT/private/docker-build"
export DOCKER_EXECUTABLE="$S09_ROOT/private/docker-build"
export TEMPLATE_ID='s091-test'
export IMAGE_TAG="nyt-s091:实际候选短SHA"
cd "$S09_ROOT/src"
"$PY" - <<'PY'
import hashlib,json,os
from pathlib import Path
from naiyue_test_agent.cloud_install.template import TemplateBuildSettings
root=Path(os.environ['S09_ROOT']); bundle=Path(os.environ['BUNDLE'])
wheel=next((bundle/'wheels/linux-x86_64').glob('*.whl'))
kit=next((bundle/'kit').glob('aitest_kit-*.whl'))
v=dict(wheel=str(wheel),wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),kit_wheel=str(kit),
 kit_profile=str(Path.cwd()/'release/sandbox-image.json'),test_lock=str(Path.cwd()/'release/cloud-test-requirements.txt'),
 base_image=os.environ['BASE_IMAGE'],template_id=os.environ['TEMPLATE_ID'],backend_id=os.environ['BACKEND_ID'],
 image_tag=os.environ['IMAGE_TAG'],docker_host='unix:///var/run/docker.sock',docker_executable=os.environ['DOCKER_EXECUTABLE'],
 package_index=os.environ['PACKAGE_INDEX'],uid=int(os.environ['TASK_UID']),gid=int(os.environ['TASK_GID']),
 output=str(root/'private/template-receipt.json'))
TemplateBuildSettings.model_validate(v)
with (root/'private/template-build.json').open('x') as f:json.dump(v,f)
PY
"$S09_ROOT/venv/bin/nyt" cloud release prepare 'de5f76aa87b54274fc4edffdc5423ca74382aac1' \
  --settings "$S09_ROOT/private/template-build.json" > "$S09_ROOT/reports/template-build.json"
```

`prepare`会校验wheel/Kit SHA、Docker backend ID、镜像架构，构建后以非root/无网络/无cap/no-new-privileges执行完整preflight，Kit CLI、doctor、pytest、本地报告、pip check及rg/fd均须通过；生成的TemplateReceipt才可登记模板，不能手写成功回执。

注意当前构建器使用host构建网络并显式清空代理build args，**不会继承S00那次容器代理修正**。apt和允许包源在该路径不可达时，保存安全错误类型并停止；先确认公司网络/CA/允许镜像源，不私改构建器、关闭TLS或把代理认证写镜像。构建tag/receipt已存在也不要覆盖，换本轮新身份并记录。

## 4. 新OpenSandbox控制实例与Coordinator

原S00控制服务可保留；新实例使用相同已批准版本/bridge和新端口。控制服务自己的SQLite放`backend-runtime`，**不能放cloud-data**，否则会污染B独占根。只把`cloud-data`加入allowed_host_paths。

```bash
"$PY" -m scripts.cloud_probe.prepare --output "$S09_ROOT/private/backend" \
  --network "$NET" --fixture-root "$S09_ROOT/backend-runtime" --port "$BACKEND_PORT" \
  --base-image "$BASE_IMAGE" --tools-image "$S00_TOOLS_IMAGE" --execd-image "$EXECD_IMAGE"
"$PY" - <<'PY'
import json,os
from pathlib import Path
p=Path(os.environ['S09_ROOT'])/'private/backend/server.toml'; s=p.read_text()
a='allowed_host_paths = ['+json.dumps(str(Path(os.environ['S09_ROOT'])/'backend-runtime'))+']'
b='allowed_host_paths = ['+json.dumps(str(Path(os.environ['S09_ROOT'])/'cloud-data'))+']'
assert s.count(a)==1
p.write_text(s.replace(a,b))
PY
export CONTROL='nyt-s091-control-实际候选短SHA'
sudo docker run -d --name "$CONTROL" --label nyt.spec=143-s091 --network host \
  --env-file "$S09_ROOT/private/backend/private.credentials" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount "type=bind,src=$S09_ROOT/backend-runtime,dst=$S09_ROOT/backend-runtime" \
  --mount "type=bind,src=$S09_ROOT/cloud-data,dst=$S09_ROOT/cloud-data" \
  --mount "type=bind,src=$S09_ROOT/private/backend/server.toml,dst=/s091/server.toml,readonly" \
  "$S00_TOOLS_IMAGE" opensandbox-server --config /s091/server.toml
```

本机限时检查loopback`/health`，再确认无key管理请求被拒绝。不要输出private.credentials/server.toml原文。正式Coordinator不得挂上面控制容器的socket。

在`private/models-cloud.json`准备真实模型配置，格式见源码`docs/examples/models.example.json`和`docs/cloud-models.md`；只放Coordinator，不能纳入输入、模板或任务环境。原生openai/anthropic的固定上游补到`model-proxy.json`；兼容/ollama沿模型配置base_url。示意：`{"config_path":"实际绝对路径/models-cloud.json","upstreams":{}}`，原生provider需按实际provider_id填完整映射。权限600，不打印key或完整配置。需要任务代理/包源变量时，另准备私有`tool-env.json`（按`docs/cloud-network-and-dependencies.md`的values结构），只填已核准的变量；宿主CA路径不会自动出现在任务里。

```bash
"$PY" - <<'PY'
import json,os,secrets,tomllib
from pathlib import Path
from naiyue_test_agent.application.cloud.settings import CloudSettings
root=Path(os.environ['S09_ROOT']); receipt=json.loads((root/'private/template-receipt.json').read_text())
backend=tomllib.loads((root/'private/backend/server.toml').read_text())
proxy=json.loads((root/'private/model-proxy.json').read_text())
v=dict(data_dir=str(root/'cloud-data'),protected_roots=[str(Path.home()/'.naiyue-test-agent')],
 owner_listen='127.0.0.1:'+os.environ['OWNER_PORT'],task_listen=os.environ['GATEWAY']+':'+os.environ['TASK_PORT'],
 owner_token=secrets.token_urlsafe(32),task_token_ttl_seconds=900,
 backend=dict(endpoint='http://127.0.0.1:'+os.environ['BACKEND_PORT'],backend_id=os.environ['BACKEND_ID'],api_key=backend['server']['api_key']),
 templates={receipt['template_id']:receipt},model_proxy=proxy,max_active_environments=2,max_retained_environments=10)
if (root/'private/tool-env.json').exists():v['tool_environment']=json.loads((root/'private/tool-env.json').read_text())
CloudSettings.model_validate(v)
with (root/'private/cloud.json').open('x') as f:json.dump(v,f)
PY
"$S09_ROOT/venv/bin/nyt" cloud serve --settings "$S09_ROOT/private/cloud.json"
```

此终端先保持前台；另开SSH查看监听和`cloud-data/cloud-root.json`。owner只监听loopback，task只监听专用bridge地址。允许任务网段到task端口及必需内部通道；其他任务/管理API/宿主服务仍拒绝。需要修改宿主策略时，按第0节先审批精确规则，不照抄通用iptables命令。需要常驻时使用公司允许的用户进程管理，启动命令/私密路径保持一致；不要把它接成自动升级服务。

## 5. Windows独立候选与正向连接

Windows创建全新venv/私有数据目录，Python要求同上。不要覆盖现有Agent venv或默认数据。可复用已有模型配置的只读私密副本，不更改原文件。

```powershell
py -3.12 -m venv "$Root\venv"
$Py = "$Root\venv\Scripts\python.exe"
$Nyt = "$Root\venv\Scripts\nyt.exe"
$Wheel = Get-ChildItem "$Root\wheels\windows-x86_64\*.whl" | Select-Object -ExpandProperty FullName
& $Py -m pip install $Wheel
& $Py "$Root\verify-candidate.py" --manifest "$Root\candidate.json"
New-Item -ItemType Directory "$Root\private", "$Root\local-data", "$Root\fixture", "$Root\reports" -ErrorAction Stop
```

在Windows主终端定义兼容Windows PowerShell5.1/PowerShell7的无BOM、新文件写入函数：

```powershell
function Write-Utf8New([string]$Path, [string]$Text) {
  if (Test-Path -LiteralPath $Path) { throw "Refuse to overwrite: $Path" }
  [IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}
```

在Linux另开终端生成两份私密连接配置，使用实际端口；Windows副本的endpoint指向本机正向端口，Host/Origin仍绑定开发机owner authority：

```bash
umask 077
export S09_ROOT="$HOME/naiyue-s091-实际候选短SHA"
export PY="$S09_ROOT/venv/bin/python"
export OWNER_PORT=$("$PY" -c 'import json,os;from pathlib import Path;print(json.loads((Path(os.environ["S09_ROOT"])/"private/cloud.json").read_text())["owner_listen"].rsplit(":",1)[1])')
export LOCAL_OWNER_PORT=14692
"$PY" - <<'PY'
import json,os
from pathlib import Path
r=Path(os.environ['S09_ROOT']); c=json.loads((r/'private/cloud.json').read_text()); i=json.loads((r/'cloud-data/cloud-root.json').read_text())
for name,port in [('linux',os.environ['OWNER_PORT']),('windows',os.environ['LOCAL_OWNER_PORT'])]:
 v=dict(endpoint='http://127.0.0.1:'+port,owner_authority=c['owner_listen'],owner_token=c['owner_token'],instance_id=i['instance_id'])
 with (r/'private'/('connection-'+name+'.json')).open('x') as f:json.dump(v,f)
PY
```

通过SSH/SFTP把`connection-windows.json`复制为Windows`private/connection.json`，保护NTFS权限，不进Git或证据包。Windows另一个PowerShell保持正向SSH：

```powershell
$SshHost = '你的已有SSH别名'
$OwnerPort = 14690
$LocalOwnerPort = 14692
ssh -N -T -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
  -L "127.0.0.1:${LocalOwnerPort}:127.0.0.1:${OwnerPort}" $SshHost
```

Windows主终端创建`private/profiles.json`与本轮settings；下列写入为新文件，存在时先停下来核对。

```powershell
$Profile = @{profiles=@(@{profile_id='s091';connection_file="$Root\private\connection.json";template_ids=@('s091-test');default_template='s091-test';tunnel='external'})}
if (Test-Path "$Root\private\profiles.json") { throw 'profiles already exists' }
Write-Utf8New "$Root\private\profiles.json" ($Profile | ConvertTo-Json -Depth 6)
$DataPath = (Join-Path $Root 'local-data').Replace('\','/')
$ProfilesPath = (Join-Path $Root 'private\profiles.json').Replace('\','/')
$SettingsText = @"
[storage]
data_dir = '$DataPath'
[cloud_tasks]
profiles_file = '$ProfilesPath'
"@
Write-Utf8New "$Root\private\settings.toml" $SettingsText
& $Nyt cloud project list --settings "$Root\private\connection.json"
```

上面的写入函数不产生BOM；不要改用PowerShell5.1的默认Out-File/Set-Content写私有JSON。模板ID必须与实际receipt一致。配置可用时project list返回真实列表或空列表，不以假任务补数据。

## 6. 最小Web＋CLI闭环（B07/B12/B20）

在`fixture`新建一个小Git项目，提交基线，随后留一处未提交改动。包含普通文本、一个小二进制、requirements、pytest测试、实际调用所需proto；只放允许上传的资料，不放key/完整业务配置。另建一个未跟踪且被.gitignore排除的outside.txt验证交付不误改；Git模式会包含所有跟踪文件，不能把普通已跟踪文件口头当成排除项。本轮第一次用新目录，不用真实业务仓库做覆盖实验。

用现有真实本机模型配置的私密副本`private/models-local.json`启动：

```powershell
& $Nyt --settings "$Root\private\settings.toml" --config "$Root\private\models-local.json" --workspace "$Root\fixture"
```

打开该命令给出的私有链接（含token，不截入报告）。加号菜单→云端任务→新增任务，选择s091和输入范围，先准备而不发消息。核对新建不调用模型；只看列表、切换任务不创建新容器；来源摘要/文件数/字节数为真实值。

第一条提示：在任务内读取基线，修改一个文件、新增一个pytest测试，执行测试，在工具提供的本轮产物目录生成报告与handoff。所有启用provider/API各至少一轮真实调用；记录安全request ID、状态和时长，不保存认证头或响应正文。任务必须ready并可观察模型→工具→输出→终局→结果available，waiting_model或只有SDK握手不能算通过。

复制真实task_id做同任务CLI核对：

```powershell
$TaskId = '侧栏实际task_id'
$Task = (& $Nyt cloud task show $TaskId --settings "$Root\private\connection.json" | ConvertFrom-Json)
& $Nyt cloud usage $TaskId --settings "$Root\private\connection.json"
$Request = @{command_id=[guid]::NewGuid().ToString();expected_revision=$Task.revision;expected_generation=$Task.generation;message='S09 CLI核验：只回复当前任务已收到CLI追加消息，不写文件';model_id='实际可用模型ID';thinking_level='model_default'}
Write-Utf8New "$Root\private\run.json" ($Request | ConvertTo-Json)
& $Nyt cloud run send $TaskId --settings "$Root\private\connection.json" --request "$Root\private\run.json"
```

CLI与Web应显示同一任务/历史。准入409先刷新task；提交回执丢失先按原command_id查询，不生成新ID重复执行。CLI当前没有`cloud result`子命令，完整结果在侧栏核对，不编造命令。

## 7. 逐项公司验收

| 门禁 | 操作及通过条件 |
| --- | --- |
| 真实模型出站 B07/B20 | 从最终Coordinator服务身份，经真实上游完成每种已启用provider/API的模型流和工具轮；记录DNS/TLS/代理、首块、较长空闲、停止取消。Windows能调模型不能代替Linux结果。 |
| 两任务隔离 B01/B17/B21/B22 | 新建第二任务；开发机AI按准确容器ID/metadata投影检查UID/GID、caps五组为0、NoNewPrivs=1、2CPU/内存/pids、四类精确挂载、无socket/真实模型key。先证明其他任务/管理服务监听真实存在，再从任务负向访问；不能把connection refused当鉴权通过。 |
| 下载依赖 | 任务在受控外网下用项目.venv安装requirements、pip check/import/pytest。Kit/pytest在实际选中解释器可用，rg/fd/git/bash可调用。不能改Runner环境、sudo/apt或把host解释器当任务解释器。 |
| HTTP＋真实RPC B02 | 依第8节建立业务中转，以实际proto和只读方法验证响应；正向连接、业务隧道分别断开/同端点恢复。RPC不以HTTP成功代替，不能用S00 Echo冒充业务服务。 |
| 工具失败与修复 B24 | 先运行确定缺少依赖/错误测试，侧栏能区分工具报错和Agent退出；追加提示后在原任务修复。failed/unknown须显式核对，不绕过恢复/静止保护。 |
| 后台续聊 B31 | 在工具内起受管mock服务，本轮结束后保持后台，追加消息继续使用同服务；fresh pending可superseded且旧轮无独立快照，捕获中则等待，不停止mock腾准入。 |
| 本机离线 B23 | 用无Windows业务依赖的任务做30秒纯本地工作；输入就绪且运行已持久受理后停止本轮Windows服务/正向隧道。Linux独立完成并自主保存。重开原数据/连接能看到原任务/结果，无重复run。Windows关机后依赖Windows中转的业务请求不保证继续成功。 |
| 文件结果 B09/B10/B27 | 修改/新增/删除/二进制/执行位按实际平台生成固定结果；预览不执行报告HTML；接收前后SHA一致。接收到主输入框仅放说明和引用，原草稿保持，不自动发起主模型。用户点击发送后模型真实读取本机材料，侧栏过程不进入主聊天/长期记忆。 |
| Windows交付 B11 | 先导出新目录；再独立验证创建分支与apply。未提交初始改动可带入分支，范围外文件、HEAD/index保持；CRLF、文件锁、reparse/路径冲突明确拒绝；partial沿原操作继续，不覆盖后来本机改动。 |
| 删除后离线交付 | 完整接收且本机I缓存存在后，明确确认删除本轮任务，再断云端连接；从本机已接收材料的受控交付入口验证导出/分支/apply，不依赖已脱敏的创建请求。CLI/API或当前UI不能进入的动作如实记入口限制，不伪报完成。 |
| 停止/释放/删除 B18/B26 | 停止本轮需保存且不删除任务；释放环境需确认容器已移除、workspace/state保留，重建新generation继续；删除只对本轮已确认task做预览/确认，墓碑与关联核销正确，其他任务及本机材料保持。 |
| 真容器故障 B19/B24 | 仅对已登记的本轮测试容器注入Runner退出/kill，核对旧容器和后代物理退出；无回执保持unknown/占额、不重放副作用，确认旧写入者消失后才能新代恢复。不得注入旧A或不属于本轮的容器。 |
| 关闭外网 | 按公司批准的实例网络策略，包源/业务中转/通用代理与IPv4/IPv6外部路径均拒绝，但控制/历史/结果/受限模型仍工作；不使用network=none，不伪造逐任务网络开关。策略变更针对本轮实例，先清楚记录影响与恢复。 |

本轮首批强制门禁为B02、真实B07/B20、B12、一次完整结果取回和断线保存，以及新模板/拓扑安全差集。其余表项属于同候选的重点回归；没有环境或资料的项目写pending，不能把S09.1删除A的前置门禁留给S11才首次验证。

## 8. 业务隧道配置边界

正向-L仅供Windows UI/CLI访问Coordinator；业务反向-R单独保持。RPC需要真实Windows可达IP/端口、proto、只读method、TLS/authority及认证方式，均在当地私密配置，不发送到聊天。已有S00隧道如仍适用可复用，但核对目的地，不能把fixture服务当业务服务。

Windows新PowerShell（先填变量，端口冲突时全链路改）：

```powershell
$SshHost = '你的已有SSH别名'
$HttpTarget = 'Windows实际可达HTTP目标'
$HttpTargetPort = 80
$RpcTarget = 'Windows实际可达RPC目标'
$RpcTargetPort = 50051
$HttpReversePort = 14681
$RpcReversePort = 14682
ssh -N -T -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
  -R "127.0.0.1:${HttpReversePort}:${HttpTarget}:${HttpTargetPort}" `
  -R "127.0.0.1:${RpcReversePort}:${RpcTarget}:${RpcTargetPort}" $SshHost
```

Linux新终端先重新设置实际S09_ROOT、PY、NET，并用`sudo docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}'`取得GATEWAY，进入`$S09_ROOT/src`。可复用S00已验证的TCP bridge入口；若需要本轮新入口，可用`$PY -m scripts.cloud_probe.network_cli bridge --bind-host "$GATEWAY" --http-remote-port 14681 --grpc-remote-port 14682 --output "$S09_ROOT/reports/business-bridge.json"`保持前台。该实现只是TCP转发，输出里的S00 fixture字样不等于业务已验证。容器使用输出的bridge地址，不能用容器127.0.0.1指宿主。TLS的SNI/authority/证书仍按原服务配置，不能加-k或关闭校验。HTTP虚拟主机Host也需正确保留。

依次保存在线→只停业务-R后失败→同端点重连后成功的三段结果。故障前先确认请求是否已到业务服务；未知副作用不自动重放。Windows睡眠会让依赖此隧道的业务调用失败，常驻进程不能阻止休眠；本次未测睡眠就写未测。

## 9. 性能和证据

每项记录pass/fail/pending、实际命令或UI动作、预期/实际结果、报告路径及安全错误码。候选SHA、Docker/backend/SDK/server、模板image ID、UID、网络、输入ID/文件数/字节/变更量都登记。不要只发成功截图。

本机已有capture探针的2CPU复验可由开发机AI在本轮真实任务容器内执行源码`scripts/profile_result_capture.py`，使用`/opt/nyt/runner/bin/python -I`和任务自身2CPU限制；在临时目录生成数据，输出cold＋20轮p50/p95/max。它只测freeze_result，不能当端到端耗时。

另外记录：首次输入准备、已缓存新任务（含I解包/全部核对）、同任务热态、上轮终局→可接受下一消息、上轮终局→结果available、结果接收/导出各自耗时。小夹具与真实中等项目分开，模型/业务耗时另列；输入默认4096文件/128MiB，输出4096文件/256MiB，大规模需显式配置并分档，不能暗增默认后比较。

交回的脱敏包建议包含：`summary.md`、`candidate.json`、版本/安全投影、上述门禁表、性能样本JSON、实际失败的安全错误码与最小复现、资源保留清单。私有config、cloud-root下完整数据库/提示词、owner链接、token、key、代理认证及业务正文不进入包；需要原始私密日志时先由当地AI脱敏。

## 10. 结束与后续切片

先保存报告，再列仍运行的本轮Coordinator/OpenSandbox控制实例、bridge入口、Windows正/反向隧道和试验任务。继续工作所需服务可保留并说明；清理时只关闭本轮准确进程/容器，任务删除走产品预览确认，不prune或递归删用户根。遇到失败停在该项，保留原command_id/result_id、现场和证据；不切正式入口、不删A来“推进进度”。

当前剩5单元：

| 单元 | 通过这次验收后仍要做什么 |
| --- | --- |
| S09.1 | 汇总真实门禁，再切正式Web/CLI入口并退出必要A运行消费者；全量回归、构建、包安装与入口再验 |
| S09.2 | 清理余留工具、脚本、构建辅助和当前文档，不清用户历史数据 |
| S10.1 | 显式版本升级、可信产物取得和模板预检，既有任务固定版本 |
| S10.2 | 自动预备、备份恢复和旧版本引用回收 |
| S11 | 完整组合矩阵、规模/并发/性能及交付收束 |

此次实机门禁不是“B全部完成”，也不要求在本次测试中验证尚未实现的自动升级/备份接口。


当前代码/命令依据：`cli/cloud.py`、`cli/cloud_tasks.py`、`cloud_install/template.py`、`application/cloud/settings.py`、`cloud_files/profiles.py`、`web/__main__.py`及143g验收矩阵。文档静态/配置结构验证不代表公司命令已执行；真实结果由Windows和开发机回传。
