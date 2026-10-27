# R1-14A：通过 OpenSSH 执行远端 Docker

2026-10-27，用户确认「用 ssh 路线吧」。Windows 控制端不安装 Docker CLI，复用用户的 OpenSSH 和 `.ssh/config`；Linux 目标已有 Docker。先实现 key 认证和跳板机通道，普通账户密码的输入、取消及重认证单独在 14B 处理。本片不修改公司 SSH 配置、Docker 服务或现有四个容器，不连接尚未提供给当前执行环境的公司主机，不提交、push 或发布。

2026-10-27后续边界：按用户授权，镜像已改用独立执行端安装包，当前构建/58源45依赖/验证及109.34MiB新交付见[R1-14A-01](R1-14A-01-envd-runtime-package.md)。下文“公司离线镜像准备”和末尾127MiB整包镜像结果保留其历史时点；后续验收使用[SSH说明的新runtime包](../remote-docker-ssh.md#32-051-release-离线镜像附件)，不能沿用旧整包内容哈希或旧Windows包。SSH连接与恢复合同保持。

## 当前链路与最小改动

开发基线为已发布标签 `v0.5.0`，独立工作树 `ssh-remote-docker`／分支 `codex/ssh-remote-docker`。最初在保留的旧开发树完成红灯与专项验证后发现版本仍为 0.4.0，已仅迁移本片明确文件到新基线；旧树、主目录和其未提交内容保留。最终交付证据只认新基线重新执行的验证，不把旧树结果当作新基线全量通过。

`SessionAPI.create_sandbox → 保存 SandboxProfileSelection → EnvironmentService._create → DockerCLI.daemon/json/run → HostProcessRunner → 本机 docker`。后续 DockerEnvironment 的资源核对、envd RPC、Git 输入/结果以及后台任务共用 DockerCLI；不为 SSH 复制文件、进程或恢复算法。

本片只增加 DockerCLI 的 SSH 执行分支和有类型的连接配置，保留本机分支。没有新 SDK、线程池、锁、服务或数据库 DDL。连接事实随原 settings JSON 和配置指纹保存；配置刷新只影响未来选择。不是让模型生成 SSH 命令，命令参数仍只来自确定性的提供方。

## 配置与身份

新增可选 `[sandbox.ssh]`：

- `host`：必填，OpenSSH Host 别名，允许中文；账户、目标地址、ProxyJump/ProxyCommand 和 key 仍由用户的默认 OpenSSH 配置解析。仅接受字母、数字、下划线、点和连字符，不接受选项、URL、用户信息或命令片段。
- `executable = "ssh"`：Windows/本机 OpenSSH 可执行程序，可显式使用含空格的绝对路径。
- `docker_executable = "docker"`：Linux 上的 Docker CLI，可配置绝对路径；不是 Windows 程序。
- `socket_path = "/var/run/docker.sock"`：目标机 Unix socket，必须是绝对 POSIX 路径且无父级跳转。
- `connect_timeout_seconds = 15`：单次建连预算；整个操作仍受原 `rpc_timeout_seconds` 限制。

SSH 配置存在时不读取本机 Docker context、不查找本机 docker，不回退本机；`sandbox.context` 必须保持默认值，拒绝同时指定另一 context。未配置 SSH 时保持原行为，直接配置 SSH context 仍不开放。两种传输分别保存，不隐式互相转换。

新 `ssh=None` 必须在序列化时省略，保证既有本机 settings 和 profile revision 完全不变。使用修改前实际生成的旧 revision 做回归。SSH 选择的完整非秘密字段参与 revision，旧会话从保存值重新装配。OpenSSH 文件由用户维护，内容变化不能悄悄获得新环境：每次执行原有 daemon ID、原容器 ID、标签、镜像、卷及策略核对；重新指向其他 daemon 时明确拒绝。

不保存 SSH 密码、私钥或私钥口令；不向容器挂载凭证。配置中没有密码字段。连接事实不是模型工具参数。SSH 段只保存在控制端注册记录/选择快照中，构造及核对 `NYT_SANDBOX_POLICY` 时必须排除它，保持 envd 原执行策略的形状，不要求既有同版本镜像理解控制端 SSH 配置。`known_hosts` 和两跳密钥必须由用户事先准备，不能在后台关闭主机身份核对。

## 正常与失败调用链

目标连接固定禁用 SSH agent 转发和 Host 配置中额外的本地/远端端口转发，避免复用 VS Code Host 时顺带创建其他转发。命令行上的 BatchMode / StrictHostKeyChecking 不声称覆盖跳板机子连接，跳板机自身配置和 known_hosts 必须预先核对。

正常：配置加载（不联网）→ 默认/显式模板选择及保存 → DockerCLI 在本机找到 ssh → `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=... -- Host <远端命令>` → OpenSSH 按用户配置穿过跳板机 → Linux Docker CLI 显式 `--host unix://...` → 原 daemon/资源核对 → envd/容器操作 → 原保存和投影。

远端命令固定使用 `exec`，所有实际参数用 POSIX shell 引用规则编码，避免空格、引号、`$()`、反引号和换行改变 argv。禁用 PTY；stdin 使用原临时文件描述符，stdout/stderr 使用原字节通道和预算。不得经过 PowerShell 文本管道；不能把 SSH 成功、输出最后一行或退出码零替代完整 JSON/协议身份验证。SSH host 来自可信配置；目标登录 shell 必须兼容 POSIX。本机与跳板机的认证、转发和主机身份条件先由用户直接 SSH 核对。

失败：缺少本机 SSH 在启动前报 `env_ssh_unavailable`。连接/主机身份/认证/远端 Docker 失败沿现有有界错误分类；读取为 `env_unreachable`，可能已经执行的写入为 `env_operation_unknown`。后者继续查原 operation_id，不重放、不重建、不自动改路径。daemon ID 改变报 `env_binding_mismatch`，在任何资源修改前失败。SSH 的原始 stderr、主机配置和凭据不进入错误正文或日志；日志只记固定事件、退出事实及异常类型。

密码认证不是启用 `BatchMode=no` 就算完成。14B 需要独立处理两跳凭证交互和后台重连。本片要求已准备好的 key/ssh-agent，失败时明确拒绝。

## 影响文件与验收

生产：sandbox_settings.py、execution/docker_cli.py、execution/docker_environment.py；按现有规则同步配置/指纹/环境存储消费者，不改公开方法签名。新增专项测试；README、设置示例、交接和总方案同步当前主链。已有框架工具、Web/CLI 创建接口和数据库 JSON 字段复用，不新增前端入口或依赖。

先运行失败测试，再最小实现：

1. SSH TOML 严格加载、非法 alias/socket/配置冲突/密码字段拒绝；配置与选择 JSON round trip，旧本机 revision 不变。
2. 没有本机 Docker 时 SSH 路线照常装配；argv 固定选项、Linux socket 和特殊字符参数无损；输入输出经过真实 HostProcessRunner 字节管道。
3. daemon 校验不查 context；错误 daemon/非 Linux/无有效身份拒绝；本机 unix/npipe 合同保持。
4. 缺 SSH、非零退出、超时、输出超预算、原异常分类和日志脱敏；写入只执行一次，不自动重试。
5. 服务创建保存 SSH 快照，刷新到另一 host 后旧准备/会话及重开保留原选择，新会话采用新选择；沿用原 envd 确定性服务测试。

本机专项和全量 Python、前端回归/构建、标准 wheel/Twine/包内容检查分别记录命令和结果。Windows 原机、真实两跳和 Tencent Docker 19.03 的端到端验收必须由公司开发机执行结果证明；本机替身不作为其证据。公司验收先只读连接，再在明确的临时项目中准备新镜像/自有容器，按原绑定核对后清理；不得重启 daemon、全局 prune 或操作已有容器。

## 状态

### 公司离线镜像准备（2026-10-27，已授权）

公司本机镜像只有明确的 Python 3.10 基础镜像，无可用 Python 3.12 内部镜像。用户允许离线传输，并明确授权在当前 ARM Mac 安装 QEMU 及必要依赖，创建独立 `nyt-r1-14-amd64` Colima 环境（x86_64、2 CPU、4 GiB 内存、20 GiB 虚拟磁盘上限），沿现有 `scripts.build_sandbox_image` 与 `sandbox/Dockerfile` 构建完整 Linux x86_64 镜像。不更改原 ARM64 环境或默认 Docker context，不转发 SSH agent，不挂载用户目录；应用源码、依赖锁定、真实应用/SSH/Docker 配置及公司既有服务保持。

正常准备链：核对现有 Linux x86_64 wheel 哈希 → 创建专用构建 VM → 核对显式 Docker context/daemon/架构 → 标准镜像构建 → 原 Docker/envd 真实探针 → 导出单架构 Docker 镜像归档及 SHA-256/镜像 ID → 用户传到公司开发机，在确认目标 socket 后导入、核对原镜像及基础探针，再由 Windows 开始正式 SSH 任务验收。普通 Python 基础镜像和 wheel 不是完整离线镜像；镜像名称、实际大小和哈希只在产物存在后记录。

本机首次 x86_64 标准构建在开始 Docker pull/build 前明确失败：`scripts.build_sandbox_image` 把 daemon 的 `x86_64/amd64` 映射为 `x64`，与当前发布包实际 `linux-x86_64` 不一致。最小修复限于该映射，保留架构拒绝；在既有 release artifact 测试内复用 wheel 夹具生成真实可检查的两种 Linux 包，先验证 x86 两个别名失败、ARM 两个别名成功及交叉架构拒绝，再修正映射并重新执行标准构建。不改变发布平台名称、公开命令参数或任何应用运行配置。

失败边界：下载/安装/VM 启动/构建/探针失败则保留日志和阶段状态，不伪造归档，不继续公司部署；同一阻碍连续三次失败停止并报告。目标此前截图为 TencentOS Server 3.1/EL8、Docker 19.03，用户本轮称 CentOS；用户只读确认内核为 `5.4.119-19.0009.40`、过滤库为 `libseccomp-2.5.2-1.tl3.x86_64`。容器与宿主共享内核；本机新 Docker 成功不证明公司旧 Docker/runc/seccomp 兼容。不得关闭 seccomp、启用 privileged、改宿主 Docker 或替换既有镜像来掩盖失败。若镜像基线确需改变，先说明证据与范围，不顺便升级项目依赖。公司导入和执行由用户分步核对，当前授权仅覆盖本机准备与验证。

14A 代码接线与本机验证完成，14B 普通密码和公司原机完整验收尚未完成。用户已确认显式 `/var/run/docker.sock` 的 Windows SSH 只读连接仍返回原服务端；这是用户执行结果，未通过本机工具连接公司主机。

新增后端测试 42 项（新文件 39 项、既有策略测试扩充 3 项），前端固定提示 1 项。先见 12 项缺功能失败；控制端策略泄漏补充 1＋3 项失败，固定转发约束和前端提示分别先红后绿。原本机策略/指纹、SSH 字节通道、配置刷新/服务重开及不重放均有确定性覆盖。两位只读复核无剩余阻塞；跳板机自身策略仍为明确的公司验收条件。

新基线实际命令（PATH 已按既有 native-assets 流程准备本机 fd/rg）：

```bash
.venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/r1-14a/backend-0.xml
# 271 文件，2576 passed in 261.24s
.venv/bin/python -m scripts.ci_shards --index 1 --count 2 --output build/r1-14a/backend-1.xml
# 271 文件，3068 passed in 369.82s
.venv/bin/python -m pytest --collect-only -q tests
# 5644 tests collected；两片互斥且并集为完整文件集。
cd frontend
npm test -- --run
# 68 文件，552 passed
npm run build
# TypeScript/Vite 通过，保留既有大 chunk 提示，0 错误。
```

标准 scripts.build_release 分别构建 macos-arm64、windows-x86_64、linux-x86_64，Twine 全过；Mac scripts.verify_wheel_install 源码外干净安装通过。首次 Mac 构建因本轮 node_modules 符号链接触发现有 release_source 门禁，确认后仅将本轮自建链接替换为本地依赖副本，重新按标准流程构建成功，未放宽门禁或重装依赖。失败日志保留；三个最终包的 Python、Web 和 README 与当前源码核对，完整报告为 `build/r1-14a/verification.json`。Windows/Linux 包的构建和内容检查不等于原机运行验收。

上述 SSH 应用实现阶段没有新项目依赖、DDL、锁或版本改动，没有真实模型请求、公司网络操作或 Git 发布。随后按用户授权完成本机离线镜像准备：QEMU11.1.2/匹配的Lima guest agent、独立x86_64 VM；标准镜像构建脚本仅修正一行x86别名，既有夹具和测试函数AST未改，2失败/4通过红灯后相关56项通过。首次初始化的缺guest agent、Docker组旧SSH会话及Colima0.10.3跳过DNS已按日志定位，仅在新环境内恢复；不升级项目依赖或修改旧环境。

真实环境/会话/交付三脚本24项、完整镜像默认/19.03官方seccomp14项、归档重导入后交付检查7项通过，应用内容哈希 `8b215e33ed8525893b07cea5e64b1692106ca1b009bfa3ffd7adbfe636a248b6` 与已验三平台wheel一致。Docker legacy manifest.json、仅linux/amd64、10个gzip层及config ID已核对，ZIP六文件/CRC/各文件SHA通过。ZIP为 `build/r1-14a/offline-image/naiyue-r1-14a-company-linux-amd64.zip`，133,232,207字节，SHA-256 `423dbf4d3ba0934a91ff615edf7667f144fe02eb9de66e398e23cca9f73a5682`。报告 `build/r1-14a/offline-image/verification.json`；本机内核6.8/Docker29.5.2，不能代替公司实际内核5.4/Docker19.03。两VM停止、测试容器/卷0、原ARM配置/默认context/真实SSH及Docker配置保持，0供应商请求。项目未提交/push/发布，真实公司主机未由工具连接。

当前唯一下一步以14A-01为准：传输执行端专用runtime新ZIP，在公司原机核对、导入并执行7项check.sh，通过后安装同源Windows完整应用开发快照，使用原key链路完成真实任务与恢复验收，再独立讨论14B。此前整包镜像只保留对照。
