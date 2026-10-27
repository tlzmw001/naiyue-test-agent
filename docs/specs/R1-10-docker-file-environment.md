# R1-10：Docker/envd 文件通道

> 版本边界：下文保留本片当时使用完整应用wheel的设计与验收记录。2026-10-27起新镜像构建改用[执行端专用wheel](R1-14A-01-envd-runtime-package.md)及`scripts.build_envd`；不要把历史`build_release`产物作为当前镜像输入。历史证据和既有镜像协议不因此改写。

2026-10-25 用户批准实施10，完成后只讲11。保留01–09 dirty，基线 build/r1-10/baseline.json；不提交/push/发布，不修改真实应用配置/用户库/依赖。另获用户明确授权准备本机Docker：Homebrew Colima+Docker CLI/buildx，专用 nyt-r1-10 profile（4CPU/6GiB/40GiB稀疏盘），不挂载主目录、不转发SSH agent、不激活默认context；测试仅管理带本片所有权标记的自建资源。

## 1. 当前链与本片外部边界

08/09：准入冻结Host绑定→RunScope.files/processes→工具→Host后端。EnvironmentRef已支持sandbox，但没有登记、提供方或通道。10提供应用内部 app.environments 的 create/list/get/start/stop/destroy/recover/files，与真实FileSystemPort接通；不开放HTTP/CLI/UI执行位置选择、不重绑定已有会话、不改会话schema。完整会话选择/Git输入在12，沙箱进程和恢复在11，SSH远端在14。没有配置Docker、不开沙箱时不得连接Docker或加载模型。

正常：已登记可信项目/所属会话→应用准备环境→核对daemon/Linux/image→取得该binding生命周期锁、注册表存唯一会话预留及确定资源名称→创建独立命名卷/容器→验证实际ID、labels、只读根/挂载/资源/网络→启动envd并握手→running→files能力交给正式RuntimeContext→原工具审批/许可→Docker文件端口→exec stdio client→容器Unix socket中的envd→现有安全IO算法→有界结果返回。失败：容器消失或context被改指向其他daemon→env_expired/env_binding_mismatch；连接失败→env_unreachable；均不回退Host。

## 2. 配置、数据与所有权

AppSettings.sandbox（默认无image，纯本机可用）：docker_executable、context（显式本机context）、image、cpus、memory_bytes、pids_limit、rpc_timeout_seconds、stop_timeout_seconds、max_message_bytes、network.mode=external|none。严格校验，未知/非法网络设置失败，不能改成默认联网。固定容器内部root=/workspace和协议socket是镜像协议，不是模型参数。冻结创建时设置和镜像实际ID；修改全局配置不改变旧环境。

复用projects.sqlite及Database短锁/事务，注册表版本2→3新增execution_environments仓储表（binding_id/project_id/session_id唯一/生命周期/JSON记录）；保留项目行/操作和revision，项目库schema19不动。记录daemon实际ID、context、容器实际ID/预留名、卷名、项目/会话、镜像ID、环境描述和冻结配置，不存连接/锁/凭据。项目可信/有效及会话归属由应用核验。project_local不由此改成sandbox。

创建前预留名称和记录；不明创建结果保留记录及可核对资源，不盲目重试、删除或重建。recover只核对原名称、labels及完整安全配置后采纳实际ID；不存在不创建替代容器。destroy只操作核验归属的原资源，保留墓碑，不能凭名称删除不属于应用的容器/卷。服务关闭只结算应用操作，不自动删/停沙箱。正常退出的进程处理在11。

## 3. Docker后端与协议

采用标准Docker CLI和原HostProcessRunner（受控字面argv，有界stdout/stderr、超时及既有清理），不增加SDK依赖，不依赖服务cwd或终端当前context。每次明确context，核对daemon身份；只接受本机Unix/npipe端点与Linux daemon，SSH在14验收后开放。CLI错误不输出环境/凭据/原始provider body。返回有类型SandboxError(code)，文件工具边界转ToolException；写入通道断开/超时为env_operation_unknown，不自动重发。

镜像包含本项目标准Linux wheel及同一锁定依赖/rg/fd/envd。build_sandbox_image复用标准build_release产物，不复制宿主工作目录/环境文件。base image通过显式构建参数提供，构建报告记录实际base/image ID；应用按实际image ID创建。本片真实验证版本记录报告，不使用不带兼容核对的任意镜像。

容器只读根文件系统、非root用户、drop capabilities、no-new-privileges、pids/CPU/memory限制；只挂该会话代码命名卷及临时tmpfs，不挂Docker socket、宿主凭据、宿主可写项目。external使用Docker隔离网络并由外部策略控制出站；none用network none，实际inspect不符拒绝。Docker控制通道不走容器出站。磁盘仅有可用性检查，不宣称通用卷配额。

envd协议版本1：长度受限JSONL，request_id/environment/session_id/operation/params；响应回显同一身份、版本、request_id、ok/result或可观察error。无pickle、任意方法名、Shell字符串或端口暴露。Docker exec启动字面Python client，转发到容器内唯一Unix socket；长驻envd线程处理请求，共享原Write/Edit忙碌表。协议及工具返回均有界，资源bytes显式base64；不把Python runtime/许可/锁/连接序列化。搜索仅传必要审批subject、规则、session_allow、身份，容器内重建用于候选过滤的上下文；不发送模型消息/凭据/观察器。

## 4. 文件语义与取消边界

DockerFileSystem实现FileSystemPort全方法。控制层仅PurePosixPath组合，不在宿主stat/resolve远端路径；envd使用现有Host文件算法在容器本地核验路径、链接/anchor、读取、Write/Edit摘要/原子替换和反馈。所有资源/目标要求在代码根内（链接解析后也核对），不访问宿主或其他容器。已有工具许可绑定完整sandbox EnvironmentRef；缺能力/跨会话/跨环境拒绝。

envd内部文件能力描述当前sandbox，直接复用原安全IO/搜索实现及候选权限；不是把远端目录当宿主Path。Read图像/分页、Write/Edit返回二元结果、Ls/Find/Grep截断和Deny/私有候选过滤保留。协议会话不含publisher；进度只报告本机请求阶段，不伪造远端流式进度。发送前检查停止；已发送的必要IO必须结算，写结果不可核对则明确unknown，不宣称没写。持续取消/operation_id/输出恢复由11补齐，不因取消自动重试。

## 5. 锁与接线

环境生命周期按binding短全局字典锁取得独立RLock，在worker线程持有该环境生命周期锁跨Docker IO；不在事件循环等待线程锁。仓储事务只持Database短锁，离开事务后才调用Docker；不持DB锁跨IO/等待生命周期锁。文件RPC可并发，容器内原文件目标锁保持；显式destroy竞争可能使结果未知，绝不换容器。新envd socketserver线程及原目标/输出锁复用需写入lock-hierarchy。

Application添加environments子API，无旧门面转发；服务工厂构造不做Docker IO，操作受现有owner.operation生命周期保护。环境仓储借用ProjectService短锁获取注册表，只在worker初始化/访问。镜像/daemon状态和文件IO不在项目注册写锁里。字段同步检查配置load、序列化/DB映射、测试及镜像包内容。

## 6. TDD和验收

先红：sandbox配置严格校验/非法模式失败；版本2注册表迁移保留事实；服务懒装配；同会话预留唯一；错误daemon/labels/资源/网络/镜像/协议/响应ID拒绝；销毁和不可达区别；创建未知不重试；跨会话拒绝；Write/Edit/资源/搜索端口调用和协议边界。使用确定性fake transport及本机envd真实文件算法测试；不以替身证明Docker隔离。

真实Docker：标准Linux镜像→两个独立容器同名文件不串；正式工具Read→Write/Edit→Read、图片/AGENTS/Skills、Ls/Find/Grep规则、卷持久化stop/start、外部策略与none、实际资源/挂载核对、销毁后不回退、重启应用登记仍在；测试只删除本片自建标记资源。失败不skip或伪报，通过条件/实际版本写报告。

最后架构/安全复核、全后端回归、显式TS/Vite、标准Mac wheel/Twine/源外安装、冻结输入/资源一致性、compileall/diff检查；真实供应商请求为0。报告当前正常/失败主链、剩余11/12/14边界。

内部传输细化：原HostProcessRunner新增可选stdin_fd（默认仍DEVNULL，Shell/AITest不提供），DockerCLI用有界临时输入文件并等待既有spawn/IO/进程组收尾后关闭。避免为exec通道复制Popen/线程计时器/无限stderr暂存；不改变公开工具schema或给模型stdin入口。对应先红后绿测试 test_process_transport_input。

评审收紧的合同：Find/Grep 的只读目标/环境校验在线程执行，直接使用工具层已有 asyncio.to_thread，不反向导入运行时层；资源越界使用 ResourceBoundaryError 保留 Skills 诊断及 AGENTS 错误包装，真正环境失效仍阻断；Write/Edit 的响应 envelope 和二元反馈均须合法，已完成写入但响应超预算也报 env_operation_unknown，不返回普通大小错误或假成功。销毁校验归属，恢复和执行额外核对运行策略，确保本应用可以清理自己已失效的资源。

## 7. 当前可追踪主链与本机准备

- 配置生产者：settings.load_settings → AppSettings.sandbox；应用只保存配置，未调用 environments 时不探测 Docker。EnvironmentAPI.create 在 owner.operation 内先用 HostManager.list_sessions 核对真实会话归属，再在线程调用 EnvironmentService.create。
- 登记数据：SandboxRecord 保存项目/会话 UUID、binding ID、daemon/container/image ID、卷名和创建设置快照。EnvironmentRegistry 将其 JSON 写入原注册表；每次返回重新解析，调用者修改返回的 settings 不影响库内快照。
- 正常文件例子：files(binding_id, project_id, session_id) → DockerFileSystem → RunScope.capabilities.files / environment → 正式 Write 工具与许可 → prepare_target / verify_target / write RPC → DockerCLI 的原生受控进程 → envd request client → Unix socket FileDispatcher → EnvdFiles 的原安全 IO → `[文本, artifact]`。这里复用同一工具和 middleware，并非另写一套绕过许可的文件命令。
- 失败例子：原容器 stop 后旧 port 的 validate 报 env_stopped；destroy 后记录保留 destroyed、报 env_expired，既不重建容器也不访问宿主同名文件。写请求发送后响应断开/错误身份/坏反馈/超预算，返回 env_operation_unknown，不自动再写。
- 资源发现：prepare_run_resources → DockerFileSystem.resource_* → envd；AGENTS 和项目 .agents/skills 从 `/workspace` 内读取。根外链接保留拒绝/诊断；全局用户 Skill 的原配置语义不在本片变更。

2026-10-25 经用户授权用 Homebrew 安装 Colima、Docker CLI、buildx；创建专用 `nyt-r1-10` profile 和 `colima-nyt-r1-10` context（4 CPU、6 GiB RAM、40 GiB 稀疏盘，VZ、Linux ARM64）。使用 `--mount none --ssh-agent=false --ssh-config=false --activate=false`，未挂载用户主目录、未改默认 context、未修改真实应用设置、未注入凭据。当前 default context 仍为 `default`。buildx 仅补标准 CLI plugin 符号链接。

该 VM 初次启动的 `/etc/resolv.conf` 指向不存在的 systemd resolver，实际拉取镜像失败；已在本片专用 VM 内改为使用本机当时已有 DNS `223.6.6.6`，没有改 macOS 系统 DNS。此地址仅是本机准备记录，不是产品默认配置或跨环境建议。安装/启动/版本/拉取的日志保存在 `build/r1-10/docker-*.txt`。本地可用 `colima -p nyt-r1-10 status` 查看、`colima -p nyt-r1-10 start` 启动、`colima -p nyt-r1-10 stop` 关闭；执行 Docker 操作显式指定 `--context colima-nyt-r1-10`，无需改变默认 context。

真实验收使用标准 Linux ARM64 wheel 构建镜像，基础镜像通过 RepoDigest 固定，实际 image/daemon ID 和 wheel SHA 见 `build/r1-10/image-verified.json`。仅生成的临时应用目录和带本片 UUID labels 的容器/卷用于验收，探针结束经 environments.destroy 核验归属后清理，Colima VM 与构建镜像保留供后续分片使用。`external` 验证网卡/创建策略及旧配置冻结，不证明任何公司网络白名单；`none` 验证只有 loopback。真实模型请求为 0。

## 8. 最终验收记录

- 新增39项确定性测试（8个文件）；配置、仓储、协议、实际文件算法、生命周期竞争、线程响应性、传输 FD、坏写反馈及超预算写入均保留红灯证据。最后核心命令覆盖 `test_sandbox_*`、`test_envd_*`、`test_docker_environment`、`test_process_transport_input` 及导入/注册迁移约束：**75 passed in 6.15s**，日志 `build/r1-10/core-verified.txt`。
- 完整后端480文件、5204项。对 `groups-verified.json` 的两组文件调用 `pytest.main(['-q','--tb=short',*files])`，分别 **2602 passed / 227.09s**、**2602 passed / 208.13s**；两组文件互斥、并集等于正式 tests 收集清单。可全量复跑 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests`。首轮候选回归发现工具层反向导入后，修正源码并完整重跑，不修改或跳过架构断言。
- `npm test -- --run`（frontend）实际56文件/**459 passed**；`npm run build`（frontend）TS/Vite 退出0。前端源码未改，仅重跑回归与标准构建。
- `python -m scripts.build_release --target macos-arm64 --output build/r1-10/wheel-verified` 及 `--target linux-arm64 --output build/r1-10/linux-wheel-verified` 标准构建成功；`python -m twine check build/r1-10/wheel-verified/*.whl build/r1-10/linux-wheel-verified/*.whl` 均 PASSED；`python -m scripts.verify_wheel_install --wheel-dir build/r1-10/wheel-verified --output build/r1-10/install-verified.json` 源外全新虚拟环境安装/产品 smoke 通过，含存储升级/备份/还原、history/memory、审批/子 Agent、CLI/后台任务，0供应商请求。
- 两个 wheel 的315 Python源、4 Web资源均与当前源码及本轮 dist 一致。1012项冻结输入与构建/测试后源码一致，具体 SHA 与核对见 `artifact-check.json`、`frozen-verified.json`。compileall、pip check、git diff --check 均通过；本片生产 Python 文件均≤500行，未改依赖版本。
- `.venv/bin/python -m scripts.build_sandbox_image --wheel-dir build/r1-10/linux-wheel-verified --base-image python:3.12-slim-bookworm --tag naiyue-envd:r1-10 --context colima-nyt-r1-10 --output build/r1-10/image-verified.json` 构建成功，最终镜像 **sha256:9d589ce9fe8a7d8752649f5d9e2ab207962ade7468a7a6e175aecf2c790574d5**；构建实际以报告中的 base RepoDigest 为 FROM。
- `.venv/bin/python -m scripts.verify_sandbox_environment --context colima-nyt-r1-10 --image sha256:9d589ce9fe8a7d8752649f5d9e2ab207962ade7468a7a6e175aecf2c790574d5 --output build/r1-10/real-docker-verified.json` 退出0：六文件工具、图片读取、两个独立卷、AGENTS/Skills、权限过滤、none、external与旧配置冻结、stop/start、应用重开、destroy无回退均 passed。模型为固定离线响应，但图/middleware/文件工具/容器IO均是真实调用；不将其当成真实模型或业务效果证据。探针结束 container/volume 列表为空，默认 context 仍 default，见 `docker-cleanup-verified.json`。
- 架构与安全独立只读复核的发现均已关闭，最后专项分别37/23通过。构建只有既有 Vite 大 chunk 提醒和 Docker 必填 BASE_IMAGE 的 lint 提醒，无构建错误。汇总 `build/r1-10/verification.json`；仅验证 Mac ARM64 控制端及本机 Linux ARM64 Docker，未宣称 Windows原机、远端SSH或公司防火墙验收。

本片交付后只有环境/文件后端可由内部 API 使用；完整沙箱对话的 Shell/后台进程、operation_id/未知结果核对与输出恢复在11，Git输入及新会话Host/Docker选择在12，受控交付13、远端14。不提交/push/发布，不自动替换既有会话绑定；下一步只讲解并确认11。
