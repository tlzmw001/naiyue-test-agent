# S00 开发机 AI 交接任务单

当前接续说明（2026-11-01）：用户已报告原Linux及Windows三段通过，下文保留原执行任务单；不要从头重装或重跑全部项目。先归档四份JSON/脱敏代理修正与资源清单，再按[143h第8节](specs/143h-s00-backend-gate.md#8-s00补充门禁与下一步未执行)执行新增差集。正式权限继承见[143a第8节](specs/143a-runtime-and-state.md#8-a权限实现的继承清单与保证边界)。仍不开始S01或操作旧A资源，防火墙/监听/账号等配置变更先列目标、范围和不可触碰项；原复验ZIP不覆盖重打。

日期：2026-11-01。直接交给新开发机上的AI执行。目标仅是完成S00环境与复验，不开发B产品、不进入S01、不迁移或清理旧A数据。

## 1. 当前事实与工作目录

- 用户在Windows通过SSH操作新Linux开发机；当前Mac侧AI无法连接它。Windows上的操作需要用户或Windows侧AI执行，Linux侧不能代称已经验证。
- 截图确认：Ubuntu22.04.5/jammy、x86_64、kernel5.15.0-170-generic、宿主Python3.10.12、用户UID1001、约60GB空闲磁盘/123GiB总内存。
- Docker客户端和服务端29.9.2，containerd2.4.2，Docker/containerd服务active，storage driver为overlayfs；sudo已可用。盘点当时0个运行容器、1个停止容器，不能删除/重启它来做实验。
- 复验包已上传、校验、解压到 `~/naiyue-cloud-s00`，用户已确认这一步完成。ZIP SHA256：`2835107ba9844069d9ae151c3b3e2fc03f2bb75307166d62cda581a2f549be02`。
- `experiment/` 与 `runtime/` 已按指导准备，仍需核对实际文件。不要重跑prepare覆盖现有随机API key；该命令也会拒绝已有输出目录。
- 当前卡点：工具镜像构建的 `pip install -c /wheels/constraints.txt /wheels/*.whl`，处理两个本地wheel后，下载`attrs`出现`[Errno 101] Network is unreachable`并重试。
- 宿主与构建网络的对照结果尚未提供；代理地址/端口/认证方式尚未确认。不能把“之前能访问PyPI”当本次构建内连通证据。
- 工具镜像尚无构建成功证据，任务镜像、OpenSandbox控制服务和远端S00门禁也尚未确认成功。先盘点当前实际状态，接续执行。

先阅读包内已有文件：`docs/cloud-s00-local-and-windows.md`、`docs/specs/143h-s00-backend-gate.md`、`scripts/verify_cloud_backend.py`、`scripts/cloud_probe/{prepare,scenario,checks,network_cli,task}.py`。本任务单补充当前进度与边界；不要从头盲目重装。

宿主准备/解压使用标准库，先沿用Python3.10，不升级Ubuntu系统Python；真正后端探针在Python3.12工具容器内运行。旧手册中宿主必须3.12的表述不作为升级要求；若准备脚本确有兼容错误，先给实际错误。

## 2. 执行范围

连续完成已授权的S00排查、专用配置、构建、启动与验证，不逐条请求确认。可以操作本次新建的实验目录、镜像、专用网络和容器；对已有同名资源先核对归属。

以下边界必须保留：

- 不重装/升级Docker或containerd，不重启整机/全局Docker服务，不删除已有容器/卷/镜像，不做prune/reset。
- 不擅自修改全局daemon.json、systemd、sshd、防火墙、路由或现有用户配置。若确实必须改，先列目标文件、最小diff、影响和恢复办法，再请用户批准；优先只配置本次S00。
- 不关闭TLS验证、不使用`--trusted-host`掩盖证书问题；需要公司CA时用已批准CA并记录信任配置。
- 不删/改弱`checks.py`与`scenario.py`断言，不调成root/privileged、不保留capabilities、不用host网络替代任务bridge，不绕过mount检查制造通过。
- 不接入真实模型key/Agent，不向任务注入OpenSandbox管理key、宿主SSH key或全局凭证。
- 不打印完整env、代理认证URL、`experiment/server.toml`、`private.credentials`或完整容器inspect。诊断只保留必要字段与脱敏信息。
- 三次假设验证仍失败时停止该问题，报告尝试和证据，不叠加未知配置。

可信控制容器按原方案使用root、host网络和Docker socket管理任务；任务容器必须独立非root/bridge/无socket。这两个角色的权限不能混为一谈。

## 3. 第一阶段：定位并修正构建网络/代理

1. 结束仍在重试的本次构建；只停止该构建，不重启Docker。记录失败阶段与原错误。
2. 对同一`https://pypi.org/simple/attrs/`分别检查宿主HTTPS、BuildKit默认网络、所选代理通路，使用有界超时。核对DNS、IPv4/IPv6、路由与代理是否相关，用结果选择单一修正。
3. 查清当前真正可用的代理在哪里：Linux宿主、Windows还是公司网络服务；不能猜端口。容器中的127.0.0.1指向容器自身，不能用它表示宿主或Windows代理。
4. 区分Docker daemon拉镜像、BuildKit RUN联网、控制容器内部API、任务运行时联网。镜像能pull不证明pip能联网；构建代理有效不证明未来任务网络有效。
5. 优先将构建代理作为本次build参数配置；不要为构建把代理写成最终镜像ENV或全局下发给所有容器。代理有认证时使用安全的本地凭证/BuildKit secret方式，不把认证URL写进聊天、脚本、日志、层或版本库。
6. 注意原命令使用`sudo docker`：普通用户的shell变量、Docker客户端配置可能与sudo后的环境不同。仅看变量是否设置和实际连通结果，避免打印凭证。
7. 若代理只监听宿主loopback，先解决构建环境到代理的受控可达性。host构建网络只能作为明确记录的构建阶段诊断/选择，不能据此改变任务运行网络，也不能把它当作已修好任务bridge出网的证明。
8. 本轮运行时S00主要使用内网/loopback/中转，未必需要给任务注入公网代理。不要把构建代理自动留在控制/任务镜像中。若运行时确需代理，localhost、控制API、实际bridge入口/中转应按客户端行为验证绕过；NO_PROXY跨客户端无统一语义，不能只看配置存在。
9. 修正后先用最小请求证明构建网络成功，再重建原工具镜像。保持`packages/constraints.txt`与SDK/server1.2.0，不跳过依赖、不改latest、不去掉版本约束。
10. 记录修正了哪些S00文件/启动参数和前后测试。若修改了包内Python探针，必须提供相对路径diff及原因，不能再称其为未修改候选；优先保持探针源不变。

参考：[Docker代理配置](https://docs.docker.com/engine/cli/proxy/)、[构建代理参数](https://docs.docker.com/build/building/variables/#proxy-arguments)、[BuildKit秘密挂载](https://docs.docker.com/build/building/secrets/)。Docker支持构建代理参数与运行环境代理分别配置；使用ENV会把配置带入镜像。

## 4. 第二阶段：完成镜像构建

在已解压目录恢复非秘密变量；SSH重连后不能假定旧shell变量仍存在：

```bash
cd "$HOME/naiyue-cloud-s00"
ROOT=$(pwd -P)
NET=nyt-s00-work
CONTROL=nyt-s00-control
TOOLS=nyt-s00-tools:test
TASK=nyt-s00-task:test
PORT=14380
```

核对`experiment/server.toml`中的网络/端口/数据根与这些值一致，只输出非秘密投影。文件若缺失，按prepare的真实参数补生成；若已存在，不重新生成随机key。任务容器UID1000与宿主用户UID1001不同是本候选的预期设计，目录权限由探针准备。

固定镜像：

- Python基础：`python@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337`。
- execd：`opensandbox/execd@sha256:6cf7dba2f21f0b536e100563d841ac58a9f31c2b0a081b7ac76796a24d6f47e2`。
- 目标平台linux/amd64；SDK/server1.2.0，工具依赖按已交付constraints冻结。

以下build命令按第一阶段已验证的方式加入本次代理参数/secret；不要照抄不存在的代理值：

```bash
sudo docker build --progress=plain -f experiment/Dockerfile.tools -t "$TOOLS" .
# 只有工具镜像成功后执行下一条
sudo docker build --progress=plain -f experiment/Dockerfile.task -t "$TASK" .
sudo docker image inspect "$TOOLS" "$TASK" --format '{{.RepoTags}} OS={{.Os}} Arch={{.Architecture}} User={{.Config.User}}'
```

两镜像均为linux/amd64，任务User必须1000:1000。核对最终镜像无构建代理认证/根API key残留，只输出是否存在，不打印值。工具镜像默认root符合可信控制端角色。

## 5. 第三阶段：启动专用网络与控制服务

核对端口14380未占用、同名网络/容器归属、runtime目录存在。已有同名资源不盲目删除或重复创建；不属于本次时选择新名字并同步S00配置/变量。

```bash
sudo docker network create --driver bridge --opt com.docker.network.bridge.enable_icc=false --label nyt.spec=143-s00 "$NET"

sudo docker run -d --name "$CONTROL" --label nyt.spec=143-s00 \
  --network host \
  --env-file "$ROOT/experiment/private.credentials" \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  --mount "type=bind,src=$ROOT/runtime,dst=$ROOT/runtime" \
  --mount "type=bind,src=$ROOT/experiment/server.toml,dst=/s00/server.toml,readonly" \
  --mount "type=bind,src=$ROOT,dst=/repo,readonly" \
  -w /repo "$TOOLS" opensandbox-server --config /s00/server.toml
```

等待实际就绪，有界检查loopback `/health`；核对`/v1/sandboxes`无key拒绝、有正确key可访问。正确key只从容器内已有`NYT_S00_API_KEY`读取，请求头为`OPEN-SANDBOX-API-KEY`，不要echo出来。健康检查与控制API保持直连，不发到公网代理。

服务只监听127.0.0.1:14380，不暴露公网，不更改宿主已有服务。诊断日志保留阶段/错误类型，输出前脱敏。

## 6. 第四阶段：执行Linux后端门禁

```bash
IMAGE_ID=$(sudo docker image inspect "$TASK" --format '{{.Id}}')
sudo docker exec "$CONTROL" python -m scripts.verify_cloud_backend \
  --domain "127.0.0.1:$PORT" --api-key-env NYT_S00_API_KEY \
  --image "$TASK" --image-id "$IMAGE_ID" --network "$NET" \
  --fixture-root "$ROOT/runtime" --output "$ROOT/runtime/backend-local.json"
sudo docker cp "$CONTROL:$ROOT/runtime/backend-local.json" ./backend-local.json
```

报告路径必须全新；已有报告保留，重试换后缀。通过标准：命令退出0、`status=local_backend_passed`、`phase=completed`、`remaining_container_ids=[]`、`cleanup_errors=[]`，且具名门禁均通过。

门禁覆盖：UID/GID1000、NoNewPrivs、五组cap为0、资源限额、精确挂载与只读输入、跨任务文件/peer网络/管理端口/socket拒绝；HTTP和proto-gRPC unary/bidi在Linux夹具上的在线/断开/同端点恢复；续租、pause/resume、kill后持久目录重建。

Linux门禁通过后先保留控制服务/专用网络供Windows阶段使用。报告字段`company_acceptance=pending`是脚本预置合同，不修改成passed；阶段结果在最终汇总中单独判断。

## 7. 第五阶段：Windows真实反向SSH中转

Linux侧AI先完成前四阶段，再把需要用户在Windows执行的命令整理出来。必须由Windows或Windows侧AI启动以下服务/SSH，不能在Linux代跑后声称Windows已验。

Windows在同一复验包解压目录使用独立环境：

```powershell
py -3.12 -m venv .venv-s00
.\.venv-s00\Scripts\python -m pip install grpcio==1.85.1 protobuf==7.36.2
.\.venv-s00\Scripts\python -m scripts.cloud_probe.network_cli fixture --output windows-fixture.json
```

保持夹具运行。若py启动器不可用，用已确认的Python3.12实际路径，不修改原Agent环境。新PowerShell读取真实端口并使用已能登录的新开发机SSH目标：

```powershell
$Target = "已有的新开发机SSH别名"
$f = Get-Content .\windows-fixture.json | ConvertFrom-Json
$HttpPort = ([uri]$f.http_url).Port
$RpcPort = [int]($f.grpc_target.Split(":")[-1])
ssh -N -o ControlMaster=no -o ControlPath=none -o ControlPersist=no -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R "127.0.0.1:14381:127.0.0.1:$HttpPort" -R "127.0.0.1:14382:127.0.0.1:$RpcPort" $Target
```

14381/14382为可配置的Linux loopback测试端口，先检查冲突；改变后同步Linux入口参数。默认`-R`只监听loopback，不改GatewayPorts或放宽sshd。不复用后台ControlPersist：本机已经发现前台退出后旧转发仍存活的情况。

Linux配置bridge入口：

```bash
GATEWAY=$(sudo docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}')
sudo docker exec -d "$CONTROL" python -m scripts.cloud_probe.network_cli bridge \
  --bind-host "$GATEWAY" --http-remote-port 14381 --grpc-remote-port 14382 \
  --output "$ROOT/runtime/windows-bridge.json"
sudo docker exec "$CONTROL" cat "$ROOT/runtime/windows-bridge.json"
```

等待实际JSON生成，不把`-d`返回0当作就绪。gateway必须现场查询；入口仅绑定专用私有bridge地址。实际链路是任务→bridge入口→Linux loopback反向端口→SSH→Windows loopback夹具。容器127.0.0.1不能代指宿主。

从Linux输出取`http_url/grpc_target`，从Windows夹具文件取`http_sha256`。这些是夹具数据，不是业务凭证：

```bash
HTTP_URL='实际windows-bridge.json中的http_url'
RPC_TARGET='实际windows-bridge.json中的grpc_target'
HTTP_SHA='实际windows-fixture.json中的http_sha256'
sudo docker exec "$CONTROL" python -m scripts.verify_cloud_backend \
  --domain "127.0.0.1:$PORT" --api-key-env NYT_S00_API_KEY \
  --image "$TASK" --image-id "$IMAGE_ID" --network "$NET" \
  --fixture-root "$ROOT/runtime" \
  --external-http-url "$HTTP_URL" --external-http-sha256 "$HTTP_SHA" \
  --external-grpc-target "$RPC_TARGET" --external-expect online \
  --output "$ROOT/runtime/windows-online.json"
```

依次完成三次：

1. Windows隧道在线：`online`，保存windows-online.json，HTTP/unary/bidi均true。
2. 仅停止这条Windows SSH隧道，保留夹具和Linux入口：改`--external-expect offline`、新文件windows-offline.json，三项均false。
3. 用相同端口重建隧道：改回online、新文件windows-reconnected.json，三项恢复true。

三份报告退出码均应0，endpoint_sha256一致、剩余容器/清理错误为空。每次探针会创建自己的新测试容器，不能要求恢复原进程内存。不能通过重开Windows夹具改变HTTP标记/端口后省略重新核对。

## 8. 第六阶段：真实服务、报告与收尾

有已批准的只读HTTP/RPC测试资料后，从真正任务容器经同样中转访问预发布服务，记录实际连通/响应判断。不要改线上数据，不把业务请求/凭证写进报告。使用实际proto与对应客户端，并正确处理TLS/SNI/authority；不关闭证书校验。

包内外部RPC探针固定调用`s00.Echo`，**不能仅把external-grpc-target换成真实业务RPC地址**。真实RPC要单独使用业务proto/客户端。若资料未提供，明确记为待验，不编造通过、不要求用户发送敏感资料到聊天。

结果输出一份脱敏summary与原始探针JSON，至少包含：

- 实际系统/架构/Docker/内核、SDK/server/execd/镜像ID和包版本。
- 原网络故障的证据、根因结论、具体代理/网络修正、改动文件及必要diff（无秘密）。
- 两镜像构建退出码和身份检查；Linux后端各门禁结果。
- Windows三段中转表、三个endpoint摘要、三份报告路径；真实HTTP/RPC另列结果。
- 本轮创建/保留/清理的资源、仍在运行的进程和未完成项。
- 本次限制：不是B产品、没有Agent/模型运行；Windows睡眠未实测就不宣称睡眠恢复通过；跨任务/凭证边界仅按具体探针证据说明。

全部验证结束后，先保存报告，再按ID/label核对本次资源并清理控制容器和测试网络；SDK负责清理其所属任务容器，清理失败记录并对账。不要删除原来那个停止容器、用户目录、镜像缓存或Docker全局资源。Windows只关闭本次夹具/隧道。若还在等Windows配合，保留必要服务并报告清楚，不提前清理导致后续无法测试。

最后回复用户：哪些通过、哪些失败/待验、证据在哪里、唯一下一步。不要因为镜像构建成功或Linux本地夹具成功就关闭整个S00，也不要开始S01。
