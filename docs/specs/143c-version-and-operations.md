# Spec143C：版本、网络与运维

2026-11-01用户确认的任务自动授权、外部网络开关与项目依赖合同见[143p](143p-s042a-task-capabilities.md)；该合同替代此前B逐工具审批计划，本机普通模式审批保留。

状态：2026-11-01审阅后修订的拟实现合同；总入口见 [Spec143](143-cloud-task-b.md)。本轮不修改真实配置、Docker或SSH；当前产物能力与新增实机门禁分开记录在143h。

## 1. 不可变发行单元

沿用当前标准 wheel/frontend/native-assets 构建链，不建立另一份手工复制源码的发布体系。B 的发行清单绑定源提交、包 SHA、Runner 镜像及协议；PyPI/GitHub 可下载是已确认机器条件，不代表任意 latest 都可以直接替换。

```text
CloudRelease {
  release_id, agent_version, source_commit,
  wheels: [{platform, url, size, sha256}],
  coordinator_protocol, runner_protocol, state_schema,
  runner_build: {base_image_digest, wheel_sha256, template_manifest_sha256},
  test_toolchain: {manifest_sha256, aitest_kit_version, aitest_kit_wheel_sha256},
  supported_templates, min_connector_protocol
}
```

开发机直接下载经过明确选择渠道的 GitHub Release/PyPI 产物；校验来源、大小、SHA 和预期版本，不从任务消息读取下载 URL。哈希只能证明与可信发布清单一致，不宣称它单独能抵御发布账户被攻破。首版复用现有可信官方发行渠道，不另建签名平台。

Runner 镜像优先由开发机用已验证 Linux wheel＋固定 digest 基础镜像构建并缓存；不要求用户先部署私有镜像仓库/GHCR 凭证。依赖层与模板稳定，Agent wheel 更新只重建后部层。生成镜像 digest 后写本机发行记录；任务按 digest 固定，不能长期只存可移动 tag。

本地构建登记实际image ID（sha256）及构建输入SHA；仓库拉取另登记repo@manifest digest，不能把没有RepoDigests的本地镜像误判为构建失败，或将两种digest混为同一字段。镜像身份须与S02真实后端创建/inspect证据核对。

模板描述 Linux 架构、非 root UID/GID、系统依赖、Bash/可选 PowerShell路径、Python/工具版本、所需 native-assets、资源限额与网络。不得把 Windows 工具 executable 原样注入 Linux。模板预备运行 `command -v`/版本/实际小命令和 Agent 导入预检，缺必要命令就 unavailable，不等首条业务任务才发现。

Runner解释器、venv、程序及全部父目录位于镜像内任务用户不可写的位置，使用绝对解释器加`-I`启动，禁用工作区/PYTHONPATH/user-site注入；测试项目使用独立venv和PATH。业务命令不能pip升级Runner依赖；任务Shell与AITest使用项目.venv/bin优先的统一PATH。工具子进程以环境allowlist、关闭控制FD的方式启动，真实探测命令也走同一执行身份。镜像可写层不等于全部只读，必须用任务身份验证Runner路径不能被替换。

常用依赖由受信模板预装并固定版本。项目额外依赖由用户或Agent在工作区.venv中显式pip安装，按143p继承部署网络/显式代理和包源；不按每个安装命令审批，不自动扫描requirements、执行setup或提权修改系统/Runner。Shell与AITest每次选择同一个Python；新venv须显式安装所需Kit/pytest。首次依赖准备单列耗时；热态不重装。CRLF脚本/缺解释器等预检按143b第3节明确失败。

### 1.1 测试镜像必须包含AITest Kit

标准测试模板必须预装`aitest-kit`及其完整依赖、pytest、Python、Git、Bash和Agent需要的rg/fd；HTTP/RPC模板还须包含所选测试客户端依赖。Python distribution名`aitest-kit`、导入名`aitest_kit`、命令`aitest`和Agent工具`aitest_kit`是四个不同检查对象，不能只在工具列表注册一个名字便声称镜像具备能力。

初始版本沿用当前受控来源`release/sandbox-image.json.aitest_kit`：0.6.0及wheel SHA-256 `419559056a1d16b695d9b1e91a7f0d15ddc389996a369207bd3dcf8afada2331`；该值是当前发行清单事实，不是永久协议常量或升级latest的许可。B构建读取唯一清单并写入template/release回执，不复制A的envd入口/root/cap/旧布局。Agent和Kit可分别固定版本；更新Agent不会自动升级Kit，变更Kit必须更新同一清单并回归。

Runner venv与测试执行venv分开，均不依靠工作区同名模块导入。模板明确AITestSettings.executable及实际测试Python；Kit通过自己的解释器执行pytest，因此Kit/pytest和项目所需依赖必须位于选定测试环境，不能只给Runner venv装Kit再让另一Python运行测试。显式创建项目venv时须按依赖清单安装Kit/pytest；AITest通过所选python -I -m aitest_kit.cli调用，Shell环境同步，缺包保留真实错误，不修改Windows全局工具设置。

S02首次模板准入即验Kit，S04再经实际HostProcesses/aitest_kit工具复验，S10每次更新预检重复核对：包版本/import、`aitest --help`、`aitest doctor`，以及无业务凭证的本地HTTP或纯本地测试工作区完成实际pytest/报告生成；若测试模板承担代码生成，再覆盖固定Markdown/proto样本的生成/收集。命令不支持的version开关用包metadata验证，不凭空要求`aitest --version`。无命令/解释器不匹配/依赖冲突/非零退出均使模板unavailable，不等真实任务才发现。每个venv运行pip check，记录工具版本、解释器和报告hash，不宣称本地样本证明真实预发布通过。

## 2. 简单更新路径

用户体验：“发现更新 → 自动下载/校验/构建和预检 → 新任务使用新版本”。自动预备默认可开启；兼容版本在成功预检后自动激活给**新任务**。更新失败保留当前 active release，显示具体失败阶段，不阻塞既有任务。

S10先以显式更新命令完成相同下载/校验/预备/激活链，再接自动发现；两者共用唯一实现。自动预备与新任务激活仍属本期需求，不因先做显式入口而取消。按143s，主聊天与云端任务侧栏由本机同一份Web资源提供，随本机产品发行；Coordinator仅提供任务API/事件，不部署第二份主聊天UI。Windows连接模块保持文件/SSH/任务API适配；本机与Coordinator/Runner握手核对协议/能力，不因补丁版本不同直接假定兼容。

### 2.1 构建权限与登记链

镜像准备由开发机上部署时授权的独立更新命令执行，不由普通Coordinator进程或任务容器持有Docker socket。拟开发机本地入口`nyt cloud release prepare <release_id> --settings <cloud-settings-path>`：更新进程读取可信渠道/模板清单、取得发布锁，下载校验wheel/Kit/锁定依赖，在专用上下文构建与预检，再向loopback所有者API提交准备回执；此settings是开发机的云端服务配置，不是Windows SSH连接profile。只接受发行ID/允许模板，不接收任务提供的Dockerfile、路径、shell或下载URL；不建立新的网络构建服务。

Updater可使用Docker构建/inspect权限，该权限只授予受信安装账号或预先授权的受控运维作业；OpenSandbox管理进程仍负责容器，Coordinator只持其管理API。自动模式由部署时配置的受控调度作业调用同一更新命令，固定渠道、命令和权限，Coordinator仅展示进度/接收回执，不通过任意subprocess参数调用特权Docker。启用调度/安装账号/防火墙属于明确部署操作，不由聊天请求自动授权。

更新进程不直接写活动cloud.sqlite或共享state。它在非任务可写的私有staging生成BuildReceipt（release/template/源提交、wheel与Kit SHA、依赖清单、base image ID/repo digest、结果image ID、预检结果、后端实例身份）；经所有者鉴权API提交元数据。Coordinator核对已选择的可信发行、构建输入、后端身份及预检，再事务登记ready/active；任务token不能登记或激活发行。SDK无镜像构建能力不意味着给Coordinator补docker socket；构建回执与实际容器镜像核验必须在S10实测。

断线后更新进程按原command_id/receipt摘要查询，镜像已存在但未登记时重新核对并补登记，不盲目重建/重激活。依赖/基础层可缓存，模板改变才重建相应层；构建占用受运维资源限制，不能为更新抢光运行任务资源。代理或下载凭证不得放Dockerfile/持久ARG/ENV/日志，真实模型/所有者凭证不进入构建上下文。失败保留旧active版本，未被任务引用的构建残留按显式范围清理。

| 更新对象 | 激活时机 | 存量任务 |
| --- | --- | --- |
| 同协议/状态 schema 的 Runner | 已下载镜像、模板预检通过后新任务使用 | 继续固定原镜像；后续显式停止后选择升级 |
| Coordinator 应用 | 进入 drain、不接收新操作，所有运行/必要保存/传输已完成后重启 | 首版不热换管理进程，不主动杀任务；有活动作业则延后 |
| 协议/schema 不兼容更新 | UI 明示维护要求、完成备份与显式维护操作 | 不自动迁移运行中任务，不以旧版本能启动作为兼容证明 |
| Windows 连接器 | 沿用现有包升级方式，连接握手核对协议 | 旧客户端不兼容时只读升级提示，禁止继续发控制/交付命令 |

单个旧任务升级：先静止并必要保存 → 备份 state 和可恢复 workspace 清单 → 验证新 schema/迁移 → 新 generation 在原 workspace/state 启动 → 冒烟/续聊成功后完成。若版本不支持直接迁移，旧任务可继续固定旧版本；不为此保留 A 兼容层。首版只实现同 schema 的显式任务换代，跨 schema 数据迁移由当次版本 Spec 决定，不能自动猜测。

活动 release 和被任务引用的旧 image/wheel 不回收；当前一次未完成更新的 staging 不被任务引用。记录 downloading/verifying/building/preflight/ready/active/failed，command_id去重；重启按阶段对账，不重复激活。缺盘、hash错误、下载断线、构建失败均有可见诊断且不切 active 指针。

“回滚”指对新任务重新选择已验证旧发行；已有新 schema 写入后不得直接把旧程序指回同库。真实部署、数据恢复、删除旧镜像都需要事先确认目标与范围，本 Spec 不是远程执行授权。

## 3. OpenSandbox 接入要求

源码核验固定 `a6c0cfc509af6253f59a4d7b97b6ede21a5d797d`，见[版本记录](../../research/cloud-b-runtime-and-transfer-2026-11-01/source-versions.json)。研究源码号不是自动选取可部署产物的许可；S00 要记录实际安装的 server/SDK/执行组件版本、包 SHA、镜像 digest、Docker/kernel/架构，并用同套产物跑通门禁。冻结后不在后续切片随意升级依赖。

使用 Docker 后端的创建/状态/停止/销毁、任务挂载与端点能力；不用它替代应用会话数据库，也不在 SDK 外重复编写 DockerCLI＋SSH exec 容器管理链。任务内部工具直接复用 HostFiles/HostProcesses，无需重新套 OpenSandbox 远程文件/命令 API。

OpenSandbox管理服务独占Docker socket；正式Coordinator非root且不挂socket，使用仅本实例配置的管理API。`allowed_host_paths`只允许任务挂载根、已提交输入和专用单任务令牌根，不包括cloud.sqlite/history/releases等管理数据；不把整个cloud.data_dir列白名单。此白名单是后端实例范围，不是每task的授权；Coordinator仍显式限制当前task/input的路径和RO/RW用途。用真实管理凭证验证白名单外路径/特权策略请求拒绝，不将API key描述成天然细粒度容器权限。

必须显式配置 bridge 网络，拒绝默认 host 网络；首选独立命名 bridge、关闭容器间直接通信，并用宿主规则/专用入口放行任务到控制面和 Windows 中转的必要流量。源码当前对命名网络拒绝 `networkPolicy`；本期不同时承诺此模式下的 OpenSandbox egress policy/Vault。跨任务端口、宿主服务和转发入口的隔离以 S00 实际负向探针为准，不能只看配置名称。

所有者API和OpenSandbox管理API只绑loopback；任务入口只绑专用bridge地址且不注册所有者路由，见143a第7节。实际Docker模式不支持`secureAccess`，不能将execd视为默认有鉴权；必须显式设置`docker.publish_host=127.0.0.1`并核对每个任务实际发布端口、容器IP端口和管理代理入口的可达性。B业务工具不使用execd API，后端健康检查仍可能依赖它；不擅自删除组件或改上游。

loopback只限制网络暴露，不提供宿主多账号鉴权。部署必须确认宿主能访问这些无鉴权端口的账号属于受信运维范围；若存在不可信本机账号，需另行隔离发布端点或增加受保护入口并实测，未解决时S00不通过。不把仅bind到127.0.0.1描述为任意本机用户都无法访问。

宿主其他服务和非授权内网目的地不因ICC=false自动隔离。按最终Docker防火墙后端设计INPUT及转发/出站规则，仅放行已配置的任务API、DNS、模型/依赖所需出站和业务中转；实际允许范围写入部署记录，不宣称通用全网封锁。规则部署需确认范围；无规则证据时不得标网络门禁完整通过。负向探针覆盖gateway的现存其他服务、宿主其他地址/任务端口，正向控制先证明监听存在；不扫描公司无关网段。

Runner 非 root、禁止 privileged、drop all capabilities、no-new-privileges；seccomp/AppArmor沿部署支持的受控配置验证。只挂自身 workspace/state、该输入 RO及必要令牌文件；无 Docker socket、宿主根目录、共享数据根、SSH 私钥或根模型凭证。Coordinator/OpenSandbox 管理接口不暴露给任务；task-scoped API/model 入口单独鉴权。

UID/GID来自专用服务身份/模板配置，不固定为1000；S00的root执行器能chown不构成正式权限证据。首版可映射专用Coordinator服务账号的数值UID/GID，以免引入chown特权服务；两者仍以独立PID/挂载/网络域隔离，管理数据根不挂进任务，不能将相同UID当作同容器进程隔离。部署前核对账号占用、umask、父目录所有者和0700/0600写入后重开/清理；不借共享组或chmod全树解决权限。若改为不同UID，必须先验证0600/目录访问及清理方案，同组/setgid不是自动读写保证。

任务ID父目录及初始令牌父目录由Coordinator拥有且任务不能挂载或重命名；令牌单文件RO挂载、文件权限只授予本任务执行身份。Runner和命令的同任务信任边界按143a第8节，不复制A的root及SETUID/SETGID/KILL/DAC_OVERRIDE/CHOWN/FOWNER能力。宿主不以高权限解析任务Git配置或打开任务数据库。

如果 OpenSandbox 的实际产物不支持这些必要边界、桥网络导致生命周期不可用，S00失败并修订方案；不将 cap/网络保护静默放宽。普通 Docker 提供的是宿主内核共享隔离，不能称其等价硬件 VM 对抗任意内核漏洞。

持久任务创建显式传`timeout=None`，禁止省略后落入SDK默认十分钟TTL；实机核对expires_at为空及后端重启后不恢复到期销毁。原S00续租/pause-resume仅保留能力实测记录，不构成B产品必须有暂停/续租状态。自动空闲停止按143a第3节的静止/运行/必要保存/unknown保护执行；Coordinator不可用期间不靠后端定时销毁任务。确认provider kill后才可用原持久目录重建新代；仅已保存状态可恢复。

S04.2b内部操作见[143q第6节](143q-s042b-run-control.md#6-内部cli与核对顺序)：control stop收束根子和受管后台，控制必要保存确认后才结算；显式acknowledge_unknown环境停止保留原run/持久目录。后端确认旧容器不存在后才允许acknowledge_unknown启动新代，再按Runner描述显式recover。自动回收不能带此标志，任务上报quiet不替代物理停止证据；控制/捕获/必要历史/unknown继续占资源和阻止回收。

### 3.1 两种部署网络状态

用户确认“关闭网络”指关闭外部网络，保留Coordinator任务控制/必要保存及S05受限模型入口；不能用network=none切断Agent。首版同一实例采用同一部署策略：受控外部网络允许策略范围内的依赖下载和Windows业务中转；关闭外部网络则拒绝这些出口及其他IPv4/IPv6路径。网络由管理员在专用bridge/宿主防火墙与代理实施，无逐任务选择器或自动修改系统规则。代理/包源通过显式tool_environment进入工具，不能以删除HTTP_PROXY冒充断网。最终容器正负验收和配置例子见[网络与依赖说明](../cloud-network-and-dependencies.md)。

## 4. Windows 中转与私有访问

Windows→开发机的本地SSH转发供UI/API，Windows主动反向转发供预发布业务中转。大文件传输另用独立SSH TCP连接，不与控制/SSE共用ControlMaster；不能只开第二个channel就认为没有队头阻塞。控制与业务中转可按授权独立管理，任何一条断开都报告其真实状态；吞吐限速为配置项，按143g验证控制延迟。

用户已报告目标Linux门禁及Windows在线/断开/恢复三段通过；现有截图可核对Linux完整摘要和外部断开，在线外部字段/恢复原始报告仍待归档。当前夹具不证明真实预发布proto、TLS/authority、持续流/并发及公司批准范围，详见143h；不要求无理由重跑已通过的同一夹具。

普通 SSH `-R` 默认只在宿主 loopback 监听，容器 bridge 不能直接把自身127.0.0.1当宿主。首版采用专用 bridge 宿主地址上的受控 TCP 入口接到 loopback reverse port；入口仅允许任务网络源和配置的服务端口，禁止监听公网通配地址。具体地址、端口、服务映射来自配置，部署时检验 sshd转发策略及防火墙。若需公司批准中转，实际部署前明确批准范围。

保持原协议字节，不将 gRPC 转成 HTTP JSON。proto、目标端口、TLS/SNI/authority由测试配置明确提供；TLS证书不匹配时不能直接关闭验证制造成功。DNS、注册发现、多连接/双向流与客户端证书仍需相应真实用例，不能因为单次HTTP成功就推断所有RPC都成功。

Windows 休眠/断网会令反向通道不可用；后台服务/计划任务可以实现登录后启动、进程保活、SSH keepalive与有界退避重连，不能让处于睡眠的网络栈继续工作。任务可继续做不依赖预发布的工作；调用失败明确显示业务依赖不可达，不自动重放可能已产生副作用的测试。

真实模型请求从Coordinator所在网络出站，不能沿用A中Windows直连成功的结论。S00补充对每个实际启用provider/API端点验证DNS、TLS、认证、代理和SSE首块/空闲/取消；真实请求只记录provider/API标识、版本、请求ID和状态，不保存key/正文/私网URL。若模型只能经Windows出站，必须如实记录该在线依赖，不能继续承诺Windows离线仍可调用模型；修订部署前提后再推进。

首版连接器可以管理已授权的 OpenSSH 隧道；系统级 Windows 常驻服务、禁用睡眠和 sshd防火墙修改属于独立安装/配置操作，不由应用静默修改。手机后续直连Coordinator可查看/控制云端任务，但Windows业务中转仍是在线依赖；Windows关闭时本机主Agent不响应，不能据此承诺手机继续本机主聊天。

## 5. 配置、容量与可观测性

拟配置归属：`cloud.data_dir/owner_listen/task_listen/owner_token`、`cloud.backend`、`cloud.templates`、`cloud.releases`、`cloud.limits`、`cloud.relay`、`cloud.model_proxy`，以及已接线的`cloud.tool_environment`。Windows单独`cloud.connections`保存SSH alias、本地绑定、独立传输连接及实例身份；凭证沿现有安全配置通道，Linux模板不接收Windows工具路径。S01实现严格未知字段/范围验证，本轮不改真实设置。

初始建议限额：2个活动容器、10个保留任务环境；每任务2CPU/2GiB/256 PID，资源与业务规模实测后调配置。额度是资源准入，不把停止任务/删除历史混为一谈；排队状态明确显示，首版不引入复杂调度器。上传单文件/输入展开大小/文件数/结果大小/磁盘低水位、SSE与模型正文上限均由配置约束，越限在副作用前拒绝，不无限缓存。

creating/starting/stopping/reconciling及存在未确认资源的任务保留活动预留，确认停止才释放活动额度；删除确认才释放保留额度，不能靠断线或时间自动释放。磁盘低水位拒绝新建/导入并显示待处理资源，已有任务必要保存失败可见；不承诺只靠预检低水位就能硬限制任务写盘，实际硬配额支持情况另作部署证据。输入/结果按引用和预算回收；长任务不能成为无上限创建新容器的理由。

日志关联 instance/task/generation/run/command/transfer/result/release ID、阶段、耗时、字节量和稳定错误码。不记录模型正文、文件内容、Authorization、密钥或私有服务地址；错误正文先分类/限长脱敏。用户下载诊断包是显式动作。

阶段指标至少区分 capture、hash、upload、verify、container_create/start、input_install、runner_ready、first_run、result_capture、download、local_verify/apply。墙钟与并发子项累计分开，不能把20个并行Git进程时间简单相加当总耗时。

备份分两域：Coordinator对自有registry/history/shared库复用一致性SQLite备份；任务checkpoint由在线Runner在静止边界生成有界一致性备份并上传，离线/损坏任务须在旧写入者已停后由仅挂该任务目录的隔离维护作业处理。管理进程不因备份而直接打开不可信SQLite；无一致性证据的state不能标可恢复，不能只复制主DB而漏WAL。

workspace/state文件备份、隔离维护作业和删除均固定已授权任务根/目录句柄，不跟随替换链接/硬链接别名或特殊文件；类型不支持有可见诊断，不静默跳过后声称完整。必要时停任务后再处理，失败保留记录/额度。备份还覆盖输入/结果引用与版本；恢复到离线新根验证身份/引用，再显式切换，不在运行中覆盖原目录，不提供一键清空真实数据。
