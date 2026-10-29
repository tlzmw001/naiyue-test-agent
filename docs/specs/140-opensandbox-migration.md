# Spec140：基于 OpenSandbox 的沙箱后端迁移

日期：2026-10-28。状态：**部署前提已变化，OpenSandbox 全量迁移待重新确认；以下原设计保留作为候选，不是当前可直接实施的计划。**

## 最新约束与选型复核

用户发现当前机器存在名称带 k8s 的运行中容器，并明确只能使用当前机器和现有 Docker；不能申请独立主机或默认借用集群资源。本轮以不升级/重启原 Docker、不改 kubelet/containerd/节点网络、不安装第二套 daemon、不碰其他业务容器为约束。名称只是 Kubernetes 归属线索，仍需只读核对标签及运维归属。

官方文档要求 Docker 20.10+；所查固定版初始化通过 docker.from_env 连接，未见直接拒绝 19.03 的版本门槛。这不证明不兼容，也不证明受支持。OpenSandbox 在 19.03 上只能作为单独获准的兼容性实验，不能依据普通 create/run 成功就启动生产迁移。Kubernetes 后端需集群 API 权限、CRD/controller/路由等部署，节点有 k8s 容器不代表这些条件已具备。

当前建议改为：保留既有 Docker/envd 业务合同，评估原生 OpenSSH 常驻数据通道、准备/核对聚合、全链路异步和独立控制入口；同时修复受信控制记录与工作负载同 UID 的问题，重新制作仅本项目使用的执行镜像。必须覆盖全部文件/进程/Git 路径，不以 Skills 本机回退或仅缓存代替。该方向尚未获得实施确认，不把本文 OpenSandbox 字段/接口直接套用到它。

原 Spec 中的取消、恢复、审批、Git、安全和证据分层要求继续作为评价标准；依赖 OpenSandbox 的接口、超时/暂停策略、镜像与迁移步骤暂不执行。当前替代提案及性能模型见 [Spec141](141-persistent-sandbox-performance.md)：首建 10 秒不再作为无条件承诺，先用真实链路验证分段预算，日常秒级目标保留。下一步为确认节点归属与允许的资源预算，再做受限性能/安全原型；不继续要求用户升级原机或获得当前不可用的环境。已授权的文档工作不构成部署/新镜像/完整产品代码修改授权。

此前设计目标：采用 OpenSandbox 官方方式，从源头消除高频远端 IO 的启动开销；恢复秒级可观察、可停止的交互；保留会话、审批和 Git 结果保护。原先可评估升级的部署分支已由上述最新约束取代。当前授权是设计与验收文档，不含安装、升级、配置修改、部署、资源删除、提交或发布。

本组文档共同构成一个 Spec：

- 本文：范围、当前链路、目标职责、实施切片及迁移门禁。
- [140A 接口与安全合同](140a-opensandbox-contracts.md)：身份、状态、配置、并发、恢复、文件与 Git、安全。
- [140B 验收手册](140b-opensandbox-acceptance.md)：现在可做的只读盘点、分阶段命令、性能目标、故障矩阵及证据格式。

## 1. 当前事实与问题

源码基线：`3759fea2e7eb0b0566e2f14fb6ed1af306ab4a82`；控制端已发布 0.5.5。公司已导入的镜像是 envd 0.5.3 / AITest Kit 0.6.0。当前工作树的未跟踪 `research/` 不属于本次修改范围。

### 1.1 正常调用链

```text
首次发送
  → application/sessions/execution_binding.py::_prepare_sandbox_session
  → InputService.prepare：冻结来源，生成输入 bundle
  → EnvironmentService.create：保存绑定、Docker 创建、envd 握手
  → InputService.install：传输、安装并确认输入
  → session_execution._prepare_resources / skills.prepare_run_resources
  → DockerFileSystem → EnvironmentService.request
  → DockerEnvironment.request → DockerCLI.run
  → Windows ssh → 跳板机 → 远端 docker exec → 一次性 Python request
  → 容器常驻 envd Unix socket → 返回文件或进程结果
  → create_agent → 工具调用 → 必要保存 → 前端/模型
```

`EnvironmentService.request` 和 `DockerEnvironment.request` 都会核对环境；当前核对涉及 daemon、容器与卷等远端请求。文件口的资源元信息、路径解析、列表、正文又分别调用。进程状态和输出也走同一传输路径，问题不限于 Skills。

### 1.2 失败与取消链

`DockerCLI` 失败转换为固定 `SandboxError`；操作服务保存 unknown，恢复查询原 operation_id。当前 `execution_use` 为整个 run 保持环境使用权，`EnvironmentActivity.manage` 与之互斥；前端 `SandboxManager` 又按 `busy/unsettled` 禁用管理。改连接不能单独解决取消和销毁入口不可用。

用户现场证据：Docker 探针约 1.67—1.81 秒；容器内 envd 的简单调用约 0.001 秒；`.agents/skills` 与 `.claude/skills` 各 10 项。它们支持“远端往返被放大”的定位，不是新后端的性能证明，也不能据此保证所有准备都在固定秒数内完成。

当前旧 envd、工作负载与 `/state` 同 UID 的恢复记录信任问题，是迁移验收项；不能把换成开源后端视为修复证据。

## 2. 原候选方案与边界（待重新确认）

原候选采用 **官方异步 Python SDK + Linux OpenSandbox Server + Docker 后端 + 官方 execd + server proxy**。公司链路用原生 OpenSSH 的长期本地端口转发；直连受信 HTTPS 的环境不需要 SSH。以下描述保留供比较，不代表最新部署约束下仍选定此方案。

```text
浏览器 / CLI
  → Naiyue 服务：会话 / 审批 / 任务 / Git / 受控交付
  → OpenSandbox 适配器 + 官方异步 SDK（use_server_proxy=True）
  → 长期 OpenSSH 隧道（只在需要跳板机时使用）
  → OpenSandbox Server：认证、Docker 生命周期、请求代理
       ├─ Docker daemon：创建、暂停、恢复、销毁
       └─ 官方 execd：命令、文件传输、状态、输出
            ├─ 普通工作负载：非特权身份
            └─ 固定业务辅助程序：文件复核 / Git 输入结果合同
```

服务代理让 Windows 只需连通一个服务入口。官方代理本身仍有服务端端点核对，不能宣称“每个请求零 Docker IO”；它消除了当前每次核对跨 SSH 启动远端 CLI 的成本。

本轮方案涉及 **超过 8 个文件，以及新增 1 个常驻服务 OpenSandbox Server**。不是小补丁。SDK 是用户选定平台的必要依赖；不重新用标准库实现其协议，不 fork 官方 Server/execd，不引入 AsyncSSH。

| 职责 | 归属 | 不能越界 |
|---|---|---|
| 创建/查询/暂停/恢复/销毁沙箱 | OpenSandbox Server | 不决定项目权限或本地 Git 交付 |
| 命令执行、流式输出、后台查询、传输 | 官方 SDK / execd | 不以 HTTP 成功替代业务成功 |
| 项目/会话、审批、工具名称及返回约定 | Naiyue 应用层 | 模型不能指定 server、sandbox ID、UID、控制凭证 |
| 输入冻结、结果验证、三锚点、受控交付 | 现有 Git 业务层及有限适配 | 不把沙箱内容视为可信控制记录 |
| 连接拥有者、超时、未知状态、取消协调 | Naiyue 适配与应用生命周期 | 不能重发可能已执行的写操作 |
| 出网限制、凭证隔离、资源上限 | 服务部署策略 + 应用显式校验 | 不继承 quickstart 的宽松默认值 |

### 2.1 最小完整交付与范围外

第一版支持本机或远端 Linux Docker，Windows/macOS/Linux 控制端；一个会话固定一个逻辑沙箱，后续轮次复用；Python/Git/Bash/AITest Kit 0.6.0；项目文件工具、Shell/argv、后台任务、Skills、输入与结果全链路。

不在本轮建设 Kubernetes、微虚机池、浏览器/桌面自动化、新 MCP 工具、密码/二次验证后台交互、通用插件后端平台。PowerShell 仅在镜像确实提供解释器时启用，不因控制端 Windows 自动注册为可用。模型继续留在控制端，供应商密钥不进入镜像。

取消自研 SSH 帧协议、逐请求 `docker exec` 数据面、控制端长期直接管理 Docker CLI 的新设计。已有内部入口只有实际消费者仍在时保留；切换完成后删除旧执行实现，保留历史数据解码与明确的迁移拒绝。

## 3. 关键决策

1. **官方能力优先，业务保护单独保留。** SDK 的文件 API 不承诺本项目审批后的目标身份复核，execd 状态不承诺现有 Git 一次捕获合同。140A 定义固定业务辅助程序，不另建网络服务或第二套通用进程运行时。
2. **全链路异步。** 会话装配、资源扫描、文件口及工具消费者一起迁移；本机阻塞磁盘/Git 可以保留有界 worker，网络 SDK 不经同步包装或线程池往返。
3. **手动销毁优先。** Docker 后端请求 `timeout=None`，不使用 SDK 默认 10 分钟寿命；拒绝平台强制自动到期而本应用不知情的配置。闲置只暂停，不自动删文件。官方 Docker pause 是冻结，UI 明确区别于任务停止。
4. **性能和可控性同时交付。** 即便输入大、命令长，也必须先显示阶段、已耗时和停止入口。20 个 Skills 是标准夹具之一，不是唯一优化对象。
5. **安全先于性能。** 服务 API 认证、执行端凭证不泄露、工作负载不能修改受保护记录、网络限制、路径复核必须先通过；失败不得以“先打通”为由开启生产模板。

最脆弱前提：所选官方版本及运行策略，能够在目标 Linux 上同时实现执行端身份隔离、受保护记录及所需网络限制。若 S0 实测不成立，后续生产接入停止，保留证据并重新讨论官方运行选项/上游补丁；不静默退回同 UID、不降低边界、不自动换回旧 SSH 运行路径。

## 4. 正常与失败的目标链路

### 4.1 新建、执行、取回

1. 首次发送保存 `request_id/session_id/binding_id`、选择的镜像与配置指纹，返回可观察的准备状态；本地快照生成持续报告数量/字节，未准备好不启动模型。
2. 连接拥有者建立或复用 SDK；适配器按唯一 binding 元数据创建沙箱，保存 provider sandbox ID。重复请求查询已保存记录，不重复创建。
3. 官方传输上传输入到受保护暂存区；固定辅助程序验证 bundle/ref/tree，独占安装，返回受保护回执；控制端保存 input_snapshot 后才进入 ready。
4. 资源准备一次集中获取项目说明和两类 Skills 的索引，正文懒加载；使用 run 快照，不跨会话混用缓存。
5. 工具通过已有审批，再由新文件/进程适配器执行；输出有界流向归档、前端及原模型工具消息，内部诊断不混入正文。
6. 结果捕获封住新任务并排空已知活动项，生成一次不可变结果；控制端下载到 quarantine，验证后导入应用任务引用。未取得本地必要保存前不得显示 ready 或允许交付。

### 4.2 请求已发出但回执丢失

本地先保留 intent 与 operation_id → 远端可能已执行 → 连接中断 → 任务标记 unknown，UI 解除全局等待但封住冲突写入 → 重连核对原 server/binding/sandbox → 有 provider execution ID 就查原命令；受保护业务操作按原 operation_id 查回执 → 不能确认时继续 unknown，不重跑 Shell、安装输入或重新捕获结果。日志故障不能覆盖这一状态。

### 4.3 运行中销毁

用户确认未回收结果可能丢失 → 短事务登记唯一管理 operation → 立即返回 accepted → 阻止新的 run/tool/输入/结果操作 → 发出取消并排空有界 IO → 官方删除原 sandbox → 查询确认 sandbox 与本应用拥有的资源已删除 → 保存 destroyed、释放额度。无法确认删除时保持 unknown/deleting，不显示成功；浏览器关闭不取消服务端管理动作。

## 5. 实施切片与退出门禁

| 切片 | 交付 | 完成条件；失败时处理 |
|---|---|---|
| S0 官方能力与部署验证 | 固定版本清单；独立验收探针；Windows 两跳 SDK、权限/网络、暂停/恢复、命令中断及记录寿命证据 | 140B 的 P0/P1/S0 通过；能力不满足即停止生产迁移 |
| S1 连接、身份与生命周期 | SDK owner、原生 SSH 隧道、异步 provider、绑定序列化、新管理操作；先写失败测试 | 创建丢回执不重复建；跨会话隔离；取消能穿过准备阶段；必要保存失败不显示成功 |
| S2 文件与资源链 | 异步 FileSystemPort 及 Host/沙箱消费者；路径辅助程序；批量资源发现 | 旧权限/TOCTOU/工具结果测试保留；20/200 Skills、普通读写和缓存失效验证 |
| S3 命令与后台任务 | 官方命令/输出/中断适配、并发预算、恢复/归档 | 真实进程树停止；ID 丢失不重放；输出游标不误当字节偏移；模型正确收到原输出 |
| S4 Git 与镜像 | 输入/结果辅助程序、受保护回执、新镜像构建、AITest 验收 | 三锚点、一次捕获、丢回执、归档续传、本机不改 HEAD/index；安全测试通过 |
| S5 UI、迁移与发布候选 | 阶段投影、异步管理、旧路径清理、完整端到端/性能/升级验收 | 140B 全部门禁通过，提交/构建/CI/安装绑定同一源码；发布另按明确授权 |

S1 即接入最小状态和取消，不等到 S5 才解决不可观察；S5 做完整产品呈现。每片先确定性红灯再实现，完成构建/回归后才进入下一片。S0 探针验证外部能力，不把它描述为产品功能的 TDD。

不对工期给无依据的天数估算。S0 是投入前置门禁；S2/S4 是主要兼容与安全成本，S5 涉及 UI/存储/平台回归。

## 6. 文件与接口影响地图

以下“新增”路径是实施目标，本轮不创建代码。每个新模块限一项当前职责，单文件不超过 500 行；同职责已有文件优先扩展。

| 位置 | 调整内容及消费者 |
|---|---|
| `contracts/environment.py`、`contracts/process.py` | 文件/资源异步合同；Host、沙箱及所有工具/装配调用方同步迁移；模型工具参数不变 |
| `sandbox_settings.py`、`sandbox_profiles.py`、`contracts/session_environment.py` | provider 配置、保存选择与指纹；不在加载配置时连网 |
| 新 `opensandbox_settings.py`、`contracts/opensandbox.py` | 官方连接字段、绑定/管理 DTO；不注册通用后端插件 |
| 新 `execution/opensandbox_{connection,tunnel,environment,files,processes}.py` | 连接拥有者/隧道、官方生命周期、文件与进程适配；分别由现有应用服务调用 |
| `application/environments/{service,api,activity,lifecycle,operations,inputs,results,tasks}.py` | 异步接线、短锁、业务状态、预算和恢复 |
| 新 `application/environments/management_jobs.py` | 用户已接受、可跨 UI 断开继续执行的管理动作；复用现有后台任务生命周期 |
| `application/sessions/{execution_binding,session_execution,session_context,skills,api}.py` | 首次准备、资源快照、工具与查询使用新异步链 |
| `storage/{environment_registry,sandbox_operations,registry_database}.py` 及相关 schema | 复用既有记录，更新 JSON 解码、约束、备份与升级；不得只改 dataclass |
| `tools/` 文件工具和资源入口 | 异步消费文件口，保留审批、输出、停止；沿真实调用者查全，不只改 DockerFileSystem |
| `service/environments.py`、`web/`、`frontend/src/{api.ts,sandboxManagementTypes.ts,components/SandboxManager.tsx,components/ComposerControls.tsx}` | 新增管理 operation API 与投影；旧公开 API 不改变成功语义 |
| 新 `naiyue_test_agent/sandbox_support/` | 受保护、固定子命令的文件与 Git 辅助程序；无监听端口、无通用 Shell RPC |
| `scripts/build_sandbox_image.py`、`sandbox/`、新 `release/opensandbox.json` | 新执行镜像/版本清单；先适配现有构建入口，不另建无验证发布捷径 |
| `pyproject.toml`、`requirements*.txt`、构建清单 | 只加入官方 SDK 所需固定依赖；Server 使用独立环境，不装进控制端或业务镜像；依赖冲突另报 |
| `tests/`、`frontend/src/*.test.*`、`scripts/verify_sandbox_*.py`、新增官方/组合探针 | 140B 定义的行为覆盖和公司链路报告；旧测试迁移保持断言 |
| `docs/lock-hierarchy.md`、发行/配置/SSH/交接文档 | 锁变更与实现同片更新；历史 Spec 标注替代，不伪改旧验收 |

## 7. 部署与旧数据迁移

以下是原候选部署条件，当前暂不成立：优先在独立 Linux VM/测试机完成 S0。Docker Engine 最低要求按官方文档为 20.10；最终版本由受支持 OS、厂商运行时、S0 实测确定并写入候选清单，不直接选择“最低”或 `latest`。用户最新约束不允许以升级原机作为本轮解决办法。

同系统多个 dockerd 需要隔离 data-root、exec-root、socket、bridge 和网络规则；官方标为实验性，不采用它作为本项目默认部署。多个 CLI 或 Python venv 不会隔离 Docker daemon。带 `tke` 的版本先确认运维归属，不能直接替换系统包。

先用隔离 data_dir、一次性测试项目和测试沙箱，不接管旧 envd 容器。正式切换前要求旧活动沙箱已取回结果、用户确认清理并核对完成；否则迁移检查拒绝启用新后端，不能把旧 binding 指向新容器。历史 destroyed 记录与已取回结果继续可读。

数据库迁移先离线备份并校验还原；备份与迁移后数据都保留。回退旧版本只使用切换前备份的独立副本，新产生的数据单独保存，不能用旧库覆盖新库或让旧程序写入新版 schema。未取得的新结果需另行恢复，不能承诺无损自动降级。

## 8. 已核对的外部依据与证据边界

上游源码基线：`opensandbox-group/OpenSandbox` 的 `release-1.2.0`，commit `62a64f0b48cdd6b410f36e53c74dc7559c87719a`。本轮 PyPI 元数据查询 `opensandbox` / `opensandbox-server` 均为 1.2.0；这只是存在与声明依赖证据，尚未安装、运行或核对下载包哈希。S0 必须锁定包哈希、execd/egress 镜像 digest 及 Docker/内核事实，不能把源码 tag 当作所有二进制版本相同的证明。

- [官方快速入门](https://open-sandbox.ai/getting-started/)：异步 SDK、server proxy。
- [官方架构](https://open-sandbox.ai/architecture/)及[执行端](https://open-sandbox.ai/architecture/data-plane/execd)：职责与执行接口。
- [固定版生命周期协议](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.2.0/specs/sandbox-lifecycle.yml)：manual cleanup、pause/resume、超时。
- [固定版执行协议](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.2.0/specs/execd-api.yaml)：命令 UID/GID、状态与输出；不自动证明路径 CAS 或业务幂等。
- [固定版代理实现](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.2.0/server/opensandbox_server/api/proxy.py)、[重试策略](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.2.0/sdks/sandbox/python/src/opensandbox/transport/retry.py)：连接及重试必须有应用预算。
- [安装要求](https://open-sandbox.ai/getting-started/installation)、[网络](https://open-sandbox.ai/architecture/network/single-host-network)、[出网控制](https://open-sandbox.ai/architecture/network/egress)：部署验证依据；官网可能领先固定版，以固定源码和实测为准。
- [Docker 多 daemon](https://docs.docker.com/reference/cli/dockerd/#run-multiple-daemons)、[live restore](https://docs.docker.com/engine/daemon/live-restore/)：多实例/跨版本升级不能承诺无影响。

参考项目核对：Pi `a69bef789` 的 `rpc-client.abort → rpc-mode abort → session.abort` 支持控制请求独立于长输出的职责拆分；AgentX `3fcfa0e` 的 `ChatSessionManager.interruptSession` 是运行标记与通知，不作为远端进程已停止的证明。本项目固定 LangChain 1.4.2；异步工具仍接 `create_agent` 的现有工具循环，不创建第二套 Agent runtime。具体注册行为以本机固定版本及 [LangChain 官方工具文档](https://docs.langchain.com/oss/python/langchain/tools)核验。

## 9. 本轮交付状态

仅新增本组设计/验收文档并同步交接及项目设计记录。尚无 OpenSandbox 产品接线、真实公司链路性能、UID 隔离、Docker 升级或新镜像验收结果。最新约束下先重新确认选型；S0 不再作为已获准的下一实施单元，不启动整体后端替换或旧容器迁移。
