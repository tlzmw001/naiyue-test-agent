# Windows 经跳板机使用 Linux Docker

0.5.2 使用本机 OpenSSH 直接执行远端 Docker 命令。Windows 不需要 Docker CLI、Docker Desktop 或 WSL；容器和卷位于 Linux 开发机，Agent、会话库、模型调用和结果审查仍在 Windows。这里的应用实现已接线，真实 Windows／公司跳板机／Tencent Docker 的端到端验收需要目标机器运行结果，不能由本机确定性测试代替。普通账户密码的后台交互属于 14B，当前要求已准备好两跳 key 或本机 ssh-agent。

## 1. 现有机器与操作范围

本轮用户提供的开发机为 TencentOS Server 3.1／x86_64，Docker 客户端 19.03.9、服务端 19.3.9-tke.1／API 1.40，已有四个长期运行的容器。`systemctl is-active docker` 为 inactive，但客户端能够访问运行的 daemon；不能因此启动第二份 Docker 或重启现有服务。

不重装 Docker、不改 daemon 配置、不全局 prune、不停止或删除已有容器。后续创建只使用验收项目的新绑定和带所有权标签的资源；销毁仍须独立确认原容器和两个卷。

## 2. 在 Windows 只读检查连接

在 Windows 本地 PowerShell 中执行，不是在 VS Code 的远程终端中：

```powershell
Get-Command ssh -All | Select-Object Source
ssh -V
$DevHost = Read-Host '输入现有 SSH 配置中的开发机 Host 别名'
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -- $DevHost "docker --host unix:///var/run/docker.sock version"
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -- $DevHost "docker --host unix:///var/run/docker.sock info --format '{{.OSType}} {{.Architecture}}'"
```

预期能够无交互地显示远端 Client／Server 及 Linux 架构。缺少 key、私钥口令未由 agent 解锁、主机身份未核对、转发被禁止或 socket 无访问权，都应先解决连接准备，不在应用中关闭安全校验。不要发送私钥、密码或完整带凭证的配置/日志。

Windows OpenSSH 默认读取 `%USERPROFILE%\.ssh\config`。使用原来 VS Code 成功使用的 `Host` 别名，才能保留 HostName、User、Port、IdentityFile、ProxyJump/ProxyCommand。跳板机仍由用户配置；不把私钥复制到跳板机，不要求在跳板机安装 Docker。

**两跳边界**：以上命令行的 BatchMode、StrictHostKeyChecking 和转发选项约束目标连接，不能声称自动覆盖跳板机子连接或自定义 ProxyCommand。跳板机自身的 Host 配置、key、known_hosts 和无交互认证必须单独核对。自定义 ProxyCommand 是用户维护的本机可执行配置，不接受模型生成的内容。目标登录 shell 必须兼容 POSIX 远端命令。

## 3. 准备兼容的 Linux x64 镜像

目标机需要包含协议兼容的 envd、Python、Git、fd/rg 和项目运行工具的 Linux x64 镜像；普通 Python/Node 镜像不能直接成为应用沙箱。当前使用独立的 `naiyue-test-agent-envd` 执行端wheel，不把完整Agent、模型供应商SDK、Web/CLI或聊天/记忆业务模块放入基础镜像。镜像由具备Docker构建条件的机器通过 `scripts.build_envd` 和既有 `sandbox/Dockerfile` 构建，再通过公司允许的仓库或离线导入送到目标daemon。执行端仍复用58个必要源文件及45个现有锁定依赖，包含当前工具实际需要的LangChain/LangGraph；不复制另一套文件/进程算法。

开发构建入口如下（在项目源码目录、有可用且已核对的本机Docker context的构建机执行；输出目录选择新的空目录）：

```bash
python -m scripts.build_envd --target linux-x86_64 --output build/envd-linux
python -m scripts.build_sandbox_image --wheel-dir build/envd-linux --base-image python:3.12-slim-bookworm --tag naiyue-envd:runtime --context your-build-context --output build/envd-image.json
```

`--base-image`、`--tag`、`--context`按实际构建环境指定；这里的context是构建机自己的Docker context。完整Windows应用仍按原方式安装/配置，它与执行端包分别安装在独立环境，不在同一Python环境混装。基础执行镜像的源码/依赖缺失检查用于本次基础包验收；业务镜像可按用途增加项目依赖，没有把“禁止业务SDK”新增为运行权限规则。当前改动见[执行端打包Spec](specs/R1-14A-01-envd-runtime-package.md)。

Windows 缺少 Docker CLI 时不要运行仅支持本机 Docker CLI 的 `scripts.build_sandbox_image`。该准备流程在有构建条件的机器上执行；不因此要求开发机宿主安装并运行整套 Agent。已有 Mac ARM64 验收镜像不能代替公司 x86_64 镜像。具体公司仓库、基础镜像与项目依赖由实际可访问条件确定，不在文档里填内部地址或凭证。

SSH 段不会进入容器的 `NYT_SANDBOX_POLICY`，envd 协议仍为 2，没有为连接方式新增容器凭证或进程协议。

### 3.1 公司离线镜像与宿主兼容性

本轮用户确认没有可用的内部 Python 3.12 基础镜像，现有本机缓存为 `python:3.10-slim`，因此准备包含全部执行端依赖的离线镜像。经用户授权，在 Mac 新建独立 x86_64 构建环境，按标准 Linux wheel/Dockerfile 流程构建；2026-10-27已生成并重导入验证下述执行端专用归档，不要求 Windows 安装 Docker 或替换开发机现有镜像。

用户只读返回：宿主内核 `5.4.119-19.0009.40`、`libseccomp-2.5.2-1.tl3.x86_64`；此前系统截图为 TencentOS Server 3.1/EL8，Docker 为 19.3.9-tke.1。容器带自己的用户空间和 Python，不要求基础镜像与宿主同一发行版；架构、共享内核、Docker/runc/seccomp 兼容性仍需实际验证。使用 Docker 19.03 官方 seccomp 规则的本机预检只能发现部分不兼容，不能替代公司原机检查。

公司导入前核对归档 SHA-256、目标 Unix socket 和新镜像标签；导入后核对镜像 ID、`linux/amd64`、Python 版本、线程/子进程、SQLite/临时文件、Unix socket 和 envd。保留原镜像和全部既有服务，不修改宿主系统包、daemon 配置或安全规则。若出现 `operation not permitted` 等错误，保留完整失败阶段并定位；不以 `privileged`、关闭 seccomp 或取消应用安全策略作为通过手段。首次实际任务仍使用专用 Git 验收项目。

### 3.2 历史 0.5.1 Release 离线镜像附件

本节保留上版的归档信息；Agent 0.5.2 的兼容使用方式见 3.3 节。

历史交付为[0.5.1 Release附件](https://github.com/tlzmw001/naiyue-test-agent/releases/download/v0.5.1/naiyue-envd-0.5.1-linux-amd64-offline.zip)：`naiyue-envd-0.5.1-linux-amd64-offline.zip`，114,655,266字节（109.34MiB），同页提供`.sha256`。ZIP的SHA-256为：

```text
9fd924ba0eb385807be2515b01ca1321ef7fc2b7892b8e8d6c495be44d7e1c2f
```

[仓库归档清单](../release/offline/0.5.1-linux-amd64.json)记录平台、镜像ID、tar/ZIP校验值、协议和执行源码哈希。ZIP不进入Git历史；旧0.5.0开发快照仅保留历史对照，0.5.1须使用本附件。

在Windows解压，将整个company-kit目录传到公司Linux新目录。在VS Code的Linux远程终端进入该目录，先运行`sha256sum -c SHA256SUMS`；确认socket仍为原daemon，新标签`naiyue-envd:0.5.1-amd64`不存在或身份与清单相符，再执行：

```bash
docker --host unix:///var/run/docker.sock image load --input naiyue-envd-0.5.1-linux-amd64.tar
bash check.sh
```

导入新增镜像并使用磁盘/I/O，不停止已有容器。check.sh先核对文件、平台和归档的两个确定镜像ID（旧Docker的config ID／新Docker的platform manifest ID），再创建一个1CPU/512MiB/64PID、非root、只读根、禁网、无宿主挂载/端口映射的临时容器；结束自动删除。程序核对7项能力、0.5.1元数据/58源哈希/45依赖及控制端缺失、Python和fd/rg。预期末尾JSON中`passed=true`、7项cases全为passed、`source_files=58`、`dependencies=45`、`controller_and_model_sdks_absent=true`。

用户回传原机结果后再继续Windows安装：正常退出原服务后安装完整`naiyue-test-agent==0.5.1`，保持原模型/应用设置/数据目录。0.5.0正式版没有`nyt service`命令，不能把关闭网页当作旧服务已退出；退出方式不明确时先核对原进程，不强制中断活动写入。0.5.1支持`nyt service stop`。0.5.1 控制端当时要求执行镜像同版本；旧会话仍冻结原镜像和连接，不因模板刷新换绑，不能通过覆盖旧镜像/删卷绕过版本不符。

同源执行包、默认与Docker19.03官方seccomp预检及发布构建/安装记录见[0.5.1说明](releases/0.5.1.md)和Actions。本机为kernel6.8/Docker29.5.2；公司kernel5.4/Docker19.03、Windows跳板机、实际网络策略仍需原机证明。基础包构成检查不限制业务镜像以后按需增加项目依赖。

### 3.3 Agent 0.5.2 复用已发布的 0.5.1 镜像

Windows `ctime` 误报发生在控制端准备 Git 输入快照时。Agent 0.5.2 在执行协议兼容的前提下复用上方的 `naiyue-envd:0.5.1-amd64`；本次无需重新导入或替换已核对的镜像，也不提供 0.5.2 镜像附件。若目标机尚未导入镜像，按 3.2 节的 ZIP 校验、导入及 `check.sh` 步骤操作。

让旧服务的活动任务完成，使用 `nyt service stop` 正常退出，再在 Windows 现有环境安装 `naiyue-test-agent==0.5.2`。保留原模型、应用设置和数据目录，环境模板继续指向已验证的 0.5.1 镜像。已有会话仍冻结原镜像和连接。将来 Agent 若依赖旧 envd 没有的必需能力，须明确协商能力或升级执行协议和镜像；公司 Docker 19.03、两跳 SSH 与实际网络策略仍需原机验收。

### 3.4 本机候选：envd 0.5.3 + AITest Kit 0.6.0

针对 0.5.2 试用发现的三个问题，本机另构建了 `naiyue-envd:0.5.3-aitest-0.6.0-amd64`。镜像沿原执行端构建链安装 envd 0.5.3 wheel，同时安装经 SHA-256 校验的 PyPI `aitest-kit` 0.6.0 基础 wheel 及其 CLI 依赖；不包含测试工作区、凭据、Console 服务或完整 Agent。Agent 0.5.2 与 envd 0.5.3 均使用协议 2；现有 0.5.1 镜像和旧会话不被替换。

本机 tar 位于 `build/fix-052-aitest-053/naiyue-envd-0.5.3-aitest-kit-0.6.0-linux-amd64.tar`，SHA-256 为 `277e323ac61924012a91877954d0da72fd316c6dfc309e3808f468b1aedf69ef`。这是本机候选产物，尚未作为 GitHub Release 附件发布。传到公司 Linux 新目录后，先核对 tar SHA、原 Docker socket 和新标签是否已有其他镜像，再执行 `docker --host unix:///var/run/docker.sock image load --input <tar文件>`；新建会话的模板可在原机验收通过后指向新标签，已有会话保持原绑定。镜像导入只新增镜像，不要求重新制作；公司 Docker 19.03/内核/SSH 原机能否正常运行仍须实测。

本机验证命令与结果记录在 [Spec 136](specs/136-envd-image-aitest-kit.md) 和交接记录；导入后可用只读、禁网、可写 `/tmp` 的临时容器运行 `sandbox/check_runtime.py --aitest-version 0.6.0`，核对 envd、`aitest --help`、源码哈希和依赖。AITest 的真实接口用例需要用户自己的 workspace 和环境配置，本镜像构建未执行此类业务用例。

## 4. 配置控制端

下面是配置格式示例，Host 与镜像必须替换成已验证的实际值；这些内容不包含密码。编辑已有 settings 文件时保留其他设置，不覆盖整份文件。

```toml
[sandbox]
default_profile = "python-tests"
rpc_timeout_seconds = 60
cpus = 2.0
memory_bytes = 2147483648
pids_limit = 256

[sandbox.ssh]
host = "company-dev"
executable = "ssh"
docker_executable = "/usr/bin/docker"
socket_path = "/var/run/docker.sock"
connect_timeout_seconds = 15

[sandbox.profiles.python-tests]
name = "公司开发机 Python 测试"
image = "naiyue-envd:0.5.1-amd64"

[sandbox.network]
mode = "external"

[sandbox.lifecycle]
max_running = 2
max_retained = 10
idle_stop_seconds = 1800
sweep_seconds = 30
```

`executable` 是 Windows 上的 SSH 程序，可填其绝对路径；`docker_executable` 和 `socket_path` 是 Linux 上的路径。启用 SSH 后不要另外指定本机 Docker context，context 必须保持未设置或默认 default。没有 SSH 段时仍使用原本机 Docker 路线；连接失败不会切回本机。

默认 settings 位于 `%USERPROFILE%\.naiyue-test-agent\settings.toml`，也可以用 `nyt --settings "D:/Naiyue/settings.toml"` 指定。服务只从其启动时固定的文件热加载 sandbox；使用其他路径连接同一服务会明确拒绝。升级代码后须正常退出旧服务再使用新代码启动，不能把关闭网页或启动终端当作已关闭常驻服务；保留已有配置和数据目录。

## 5. 新任务与已有会话

服务使用新实现与所选配置后，在输入框上沿环境列表点击刷新，选择该模板；选择仅保存草稿，首次发送才创建容器和传入 Git 快照。第一轮使用专门验收的 Git 项目，不直接处理真实业务代码。未跟踪输入仍需明确选择。

资源登记、进程输出、取回改动、审查和本机交付继续走现有入口；不把文件自动写回 Windows 源项目。变更 Host、镜像或 socket 后刷新，只影响未来新会话；已有会话持有原连接快照，不跟随模板换绑。用户修改 SSH 配置使同一别名指向其他 daemon 时，原 daemon ID 校验明确拒绝。

停止容器保留文件和双卷；空闲停止、数量额度及正常服务退出规则沿用既有实现。SSH 断开或超时后，写入可能已经发生，继续核对原操作 ID，不重复创建或重放。不要用重发相同自然语言任务代替操作恢复。

## 6. 验收证据

先记录只读连接输出，再验证新建/文件/前后台命令/审批/取回/停启/服务重开原绑定，最后明确清理本轮自有资源。网络 external 必须从容器内验证公司允许和禁止的目标，不能由宿主联网推断；none 单独新建环境验证回环以外不可用。记录首次/增量输入与 SSH 调用耗时。

本机确定性测试只证明配置、原始字节、错误分类、身份检查和保存恢复的接线；未完成公司原机矩阵前，不称 R1-14 远端整体验收完成。密码认证不通过关闭 BatchMode、写明文 password 或让模型输入凭证实现。

## 0.5.3：含 AITest Kit 的新执行镜像

[0.5.3 Release](https://github.com/tlzmw001/naiyue-test-agent/releases/tag/v0.5.3) 提供 envd 0.5.3 + AITest Kit 0.6.0 的 Linux/amd64 离线 ZIP 及外部 SHA256。按包内 README 核对原 daemon、内部 SHA 与镜像身份，导入后执行同包 check.sh。新标签为 `naiyue-envd:0.5.3-aitest-0.6.0-reviewed-amd64`，只用于明确选择它的新会话，旧环境绑定保持。控制端升级命令和本版修复见 [0.5.3说明](releases/0.5.3.md)；归档身份见 `release/offline/0.5.3-linux-amd64.json`。公司原机检查仍独立于本机验收。
