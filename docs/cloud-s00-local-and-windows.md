# S00：本机原型与 Windows 复验手册

对应[Spec143H](specs/143h-s00-backend-gate.md)。本期只验证OpenSandbox后端/隔离/中转；不启动产品Agent、不调用真实模型、不迁移A，不向现有Agent安装OpenSandbox依赖。下列安装/创建/清理命令需要在明确选择的专用测试目标上执行，不能直接指向原预发布开发机。

## 1. 已测与待测

本机环境：Mac客户端＋专用Colima Linux ARM64/Docker29.5.2/kernel6.8；server/SDK公开PyPI1.2.0、execd1.1.0。任务USER1000:1000、drop ALL、NoNewPrivs、bridge ICC=false、2CPU/2GiB/256PID。通过SDK创建任务，Docker只读inspect核对生效策略，不走A的envd/SandboxChannel。

`local_backend_passed`只表示相应报告列出的本机门禁。Windows/OpenSSH、目标AMD64/实际Docker与内核、公司网络策略/HTTP/RPC仍须复验，报告保留`company_acceptance=pending`。容器就绪时间不包含Agent/依赖/项目输入准备，不能当B性能承诺。

已发现的版本边界：SDK1.2.0支持argv命令字段，execd1.1.0拒绝该字段；探针明确采用双方支持的command文本接口，所有固定argv经`shlex.join`转义。这只影响S00探针控制调用，B的任务内部工具仍按143在本地执行。不要临时改execd/latest或放宽cap绕过问题。

## 2. 复验包与前置条件

复验包仅包含探针源文件、echo.proto、两个公开wheel及SHA来源、工具依赖版本约束与本手册；不含本机secret、真实配置、SSH key、会话数据或Docker镜像。首次构建需访问Docker镜像仓库及PyPI；已知新机能下载PyPI/GitHub，不代表Docker仓库已验证。若拉镜像不可达，先记录结果，再另做对应架构的离线镜像包，不使用本机ARM镜像替代AMD64。

Windows负责上传/SSH和反向隧道；后端探针在目标Linux上的专用可信工具容器运行。目标需Docker和Python3.12用于准备文件，Docker安装按[官方对应发行版说明](https://docs.docker.com/engine/install/)及公司规则执行。本机29.5.2通过不证明所有版本均通过；实际版本写入报告。目标发行版/权限未确认前不执行安装脚本。

Windows PowerShell传输示例，替换已经确认的新开发机SSH别名；不要发送密码或密钥：

```powershell
$Target = "已确认的新开发机SSH别名"
Get-FileHash .\naiyue-cloud-s00-kit.zip -Algorithm SHA256
scp .\naiyue-cloud-s00-kit.zip "${Target}:~/naiyue-cloud-s00-kit.zip"
ssh $Target
```

## 3. Linux后端准备与门禁

以下Bash从专用新目录执行。资源名和端口为本手册的显式测试配置，可改；重跑选择新目录/资源名，不覆盖既有配置或删除旧资源。

```bash
set -euo pipefail
test ! -e ~/naiyue-cloud-s00
python3 -m zipfile -e ~/naiyue-cloud-s00-kit.zip ~/naiyue-cloud-s00
cd ~/naiyue-cloud-s00
ROOT=$(pwd -P)
NET=nyt-s00-work
CONTROL=nyt-s00-control
TOOLS=nyt-s00-tools:test
TASK=nyt-s00-task:test
PORT=14380
python3 -m scripts.cloud_probe.prepare --output experiment --network "$NET" --port "$PORT" --fixture-root "$ROOT/runtime" --tools-image "$TOOLS" --execd-image opensandbox/execd@sha256:6cf7dba2f21f0b536e100563d841ac58a9f31c2b0a081b7ac76796a24d6f47e2 --base-image python@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
mkdir runtime
python3 -c 'import hashlib,json,pathlib; p=pathlib.Path("packages"); records=json.loads((p/"sources.json").read_text()); raise SystemExit(not all(hashlib.sha256((p/r["filename"]).read_bytes()).hexdigest()==r["digests"]["sha256"] for r in records))'
docker network create --driver bridge --opt com.docker.network.bridge.enable_icc=false --label nyt.spec=143-s00 "$NET"
docker build -f experiment/Dockerfile.tools -t "$TOOLS" .
docker build -f experiment/Dockerfile.task -t "$TASK" .
docker run -d --name "$CONTROL" --label nyt.spec=143-s00 --network host --env-file "$ROOT/experiment/private.credentials" --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock --mount "type=bind,src=$ROOT/runtime,dst=$ROOT/runtime" --mount "type=bind,src=$ROOT/experiment/server.toml,dst=/s00/server.toml,readonly" --mount "type=bind,src=$ROOT,dst=/repo,readonly" -w /repo "$TOOLS" opensandbox-server --config /s00/server.toml
IMAGE_ID=$(docker image inspect "$TASK" --format '{{.Id}}')
docker exec "$CONTROL" python -m scripts.verify_cloud_backend --domain "127.0.0.1:$PORT" --api-key-env NYT_S00_API_KEY --image "$TASK" --image-id "$IMAGE_ID" --network "$NET" --fixture-root "$ROOT/runtime" --output "$ROOT/runtime/backend-local.json"
docker cp "$CONTROL:$ROOT/runtime/backend-local.json" ./backend-local.json
```

控制面使用host网络和Docker socket，它是可信编排端；任务必须独立bridge、无socket与全局挂载。server只监听loopback、有随机API key；不要将管理端口发布到公网。`private.credentials/server.toml`只在新的experiment目录生成，权限0600，不上传报告、不提交Git。

成功要求exitcode0、`local_backend_passed`、所有具名门禁通过、`remaining_container_ids=[]`及`cleanup_errors=[]`。创建ACK未知/清理失败时不要执行`docker rm $(docker ps...)`；保存报告中的run_id与已知ID，对账后只处理本次资源。

## 4. Windows实际SSH中转复验

这一步使用无业务数据的HTTP/proto-gRPC夹具，验证真实Windows→SSH→Linux→容器路径。真实预发布proto/认证/TLS/authority后续用独立测试验证；本工具的外部gRPC检查仅支持包内`s00.Echo`协议。

先在Windows解压同一包，创建独立夹具环境。不会往当前Agent环境安装依赖：

```powershell
py -3.12 -m venv .venv-s00
.\.venv-s00\Scripts\python -m pip install grpcio==1.85.1 protobuf==7.36.2
.\.venv-s00\Scripts\python -m scripts.cloud_probe.network_cli fixture --output windows-fixture.json
```

保持窗口运行。它仅监听Windows loopback，输出随机HTTP/RPC端口和HTTP内容SHA；数据是随机夹具标记。新PowerShell读取JSON并发起两个反向转发，下面14381/14382为选择的Linux loopback测试端口：

```powershell
$Target = "已确认的新开发机SSH别名"
$f = Get-Content .\windows-fixture.json | ConvertFrom-Json
$HttpPort = ([uri]$f.http_url).Port
$RpcPort = [int]($f.grpc_target.Split(":")[-1])
ssh -N -o ControlMaster=no -o ControlPath=none -o ControlPersist=no -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R "127.0.0.1:14381:127.0.0.1:$HttpPort" -R "127.0.0.1:14382:127.0.0.1:$RpcPort" $Target
```

不要将`-R`改成公网通配监听。上述命令关闭SSH多路复用，确保本次窗口拥有隧道；本机已发现ControlPersist会令前台退出后转发仍存活，探针如实拒绝这种“断线”。Linux宿主loopback端口不能用容器自身127.0.0.1访问；在Linux先查专用bridge gateway，再启动专属入口。入口只绑定该私有gateway，夹具不含业务凭证；正式业务部署还须验证源网络访问控制，不能把本夹具当生产网关。

```bash
GATEWAY=$(docker network inspect "$NET" --format '{{(index .IPAM.Config 0).Gateway}}')
docker exec -d "$CONTROL" python -m scripts.cloud_probe.network_cli bridge --bind-host "$GATEWAY" --http-remote-port 14381 --grpc-remote-port 14382 --output "$ROOT/runtime/windows-bridge.json"
docker exec "$CONTROL" cat "$ROOT/runtime/windows-bridge.json"
```

从输出取得`http_url/grpc_target`；从Windows夹具文件取得`http_sha256`，以下变量填这些实际值。无需发送业务内容或API key：

```bash
HTTP_URL='填windows-bridge.json的http_url'
RPC_TARGET='填windows-bridge.json的grpc_target'
HTTP_SHA='填windows-fixture.json的http_sha256'
docker exec "$CONTROL" python -m scripts.verify_cloud_backend --domain "127.0.0.1:$PORT" --api-key-env NYT_S00_API_KEY --image "$TASK" --image-id "$IMAGE_ID" --network "$NET" --fixture-root "$ROOT/runtime" --external-http-url "$HTTP_URL" --external-http-sha256 "$HTTP_SHA" --external-grpc-target "$RPC_TARGET" --external-expect online --output "$ROOT/runtime/windows-online.json"
```

下一步只Ctrl+C停止Windows的SSH转发窗口，保持Windows夹具与Linux入口运行；同一命令改`--external-expect offline`、输出新文件`windows-offline.json`。然后重建相同SSH转发，改回online、输出`windows-reconnected.json`。三份报告应分别为HTTP/unary/bidi全true→全false→全true，`endpoint_sha256`相同；失败也应exitcode非0且保留阶段。不要只测“连接拒绝”就宣称通路已验证。

Windows休眠导致中转中断属于预期依赖失败；上述停止隧道只是可控故障注入，不能冒充真实休眠测试。需要时另记录睡眠/唤醒行为。首次公司验收仍保留真实HTTP/RPC、业务依赖和权限范围的独立结果。

## 5. 保存结果与收尾

用`docker cp`取回上述三个报告，再从Windows用scp下载。只分享backend JSON、三份中转JSON和错误类型/阶段；不分享experiment目录、完整docker inspect/env、真实设置、SSH配置或凭证。

探针已通过SDK清理本次任务容器；确认报告没有剩余资源后，检查控制面与网络的label均为`nyt.spec=143-s00`，再仅停止/删除自己创建的控制面和测试网络。数据目录/镜像保留供查证，不自动删除：

```bash
docker inspect "$CONTROL" --format '{{json .Config.Labels}}'
docker network inspect "$NET" --format '{{json .Labels}}'
docker rm -f "$CONTROL"
docker network rm "$NET"
```

若名称/label与本次不符，停止清理并核对。真实开发机首次创建、安装、配置和删除范围须先确认；本手册不授权操作其他容器、修改sshd/防火墙或关闭公司限制。
