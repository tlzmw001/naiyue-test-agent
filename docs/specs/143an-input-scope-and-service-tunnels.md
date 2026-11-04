# 143AN 大项目输入与个人服务隧道

状态：2026-11-04 五个分片代码与本机验证完成；Windows/公司端到端验收待用户执行。未提交、发布、部署或修改真实配置。

## 目标与边界

每台 Windows 的本机服务管理两个服务槽位（HTTP/HTTPS、gRPC），管理员预分配独立的远端端口；共享 SSH 账号、Coordinator 和容器池。任务持久绑定网络配置 ID，仅用于正确路由，不宣称租户权限隔离。目标地址在本机页面修改，非运行时热应用，无须重启本机服务或重建容器。一个槽位切换影响所有关联任务，不保留旧任务旧目标。

创建任务支持非 Git 项目和目录勾选，Git 缩小范围仍保留 Git 来源回执。默认未压缩输入总量提高到 2 GiB。不得初始化用户 Git 仓库、静默上传整个项目、绕过安全排除或修改真实配置。保留现有独立源捕获、上传、创建、环境启动事务及结果取回合同。Spec142 四份草稿不动。

## 当前数据流与缺口

CloudTaskCreate.prepare → POST cloud-sources → LocalTaskDomain.capture → select_git/capture_cached → source_handle → ManualCloudTasks.submit → prepare/upload/task:create → TaskBootstrap → Runner。

非 Git 目录 select_git 首个 rev-parse 抛 input_git_failed，UI 错误映射落入连接提示。selection 仅接收具体文件，目录不展开，反斜杠和 glob 拒绝。git include 是额外包含而非范围筛选，不能用于缩小范围。ProfileConnections 按需拥有 -L 连接，进程退出后下一次使用重建；尚无反向隧道、页面目标配置和任务网络绑定。Runner 环境为 Coordinator 全局设置。

## 分片与验收

### 1. 输入预算

默认 max_input_bytes=2147483648（2 GiB）、max_file_bytes=2147483648、max_archive_bytes=2684354560（2.5 GiB，含 tar/压缩开销）、max_files=32768、max_metadata_bytes=16777216、max_cache_bytes=17179869184（16 GiB）、upload_timeout_seconds=1800、max_discovery_entries=500000；分块仍8 MiB，IO缓冲仍256 KiB，不扩大内存读取块。单文件可占满总预算。同步客户端、Coordinator、Runner共享 InputLimits 与示例。审计缓存临时预留、接收、解包及 Runner 等待限制。明确磁盘峰值和旧显式配置仍优先。

### 2. 来源检测与范围选择

新增本机鉴权 API：GET cloud-source-tree（project/session/path/cursor，单目录分页），POST cloud-source-preview（同来源参数）。返回 Git 能力/不可用原因、真实目录项、预览文件数/字节/预算问题。后端逐层安全检查，路径限制沿用 input_path；手动输入只规范化 Windows 分隔符，不支持绝对路径、..或glob。目录递归有 discovery 上限，禁止沿目录符号链接递归。捕获时重新选择及校验，预览不是写入授权或快照。

LocalSourceRequest 增加可选 paths 范围；旧 selection 具体文件合同保留。Git scope 按文件/目录前缀过滤 Git 列表，独立于 include；SourcePolicy/来源指纹记录 scope。默认 Git 全部；非 Git 默认自选。UI提供文件/目录勾选、手动路径、预览、明确错误；预览成功后才可提交，编辑使预览失效。Git 不可用有明确原因，不自动初始化或回退。

正常：选择 src、tests → 预览 → 捕获同一范围 → source_handle → 现有创建链。失败：不存在/非便携/越界/超限 → 本机报错，0云端创建。

### 3. 本机隧道管理

每个 profile 可配置一个 network_id 和两个管理员分配的槽位地址（反向绑定到开发机专用容器桥地址，不能绑定公网/0.0.0.0）；管理员在 sshd 明确允许相应监听，容器访问这些地址。采用 OpenSSH 标准 -R，不引入依赖。端口唯一性由管理员分配及 SSH ExitOnForwardFailure 校验；页面不能修改管理员分配端口。

本机状态独立持久保存，不能覆盖 profiles/connection/settings 原文件。页面配置 host/port/tls，HTTP 可填 URL；拒绝凭证、查询串/片段和 shell 注入。一个槽位一个受管SSH进程，关闭网页不影响服务，连接丢失重试，主动停止持久保持；配置缺省不创建连接。不修改全机ssh、Docker、路由、防火墙配置。

状态区分 desired/applied/revision、进程存活和目标TCP可达；TCP探测不冒充HTTP/gRPC/TLS业务成功。失败只记录安全码/异常类，不记录凭证。超时、关停回收和Windows无终端创建有测试。

### 4. 任务绑定与安全切换

Coordinator 管理员配置 network_id→容器可达的HTTP/gRPC入口。CreateTask/CloudTask 持久绑定 network_id；本机从profile确定，不接受模型随意指定。关联状态保存registry.meta，不新增独立数据库。

切换分两步：本机先持久化切换意图→prepare（确认所有关联任务没有活动run、后台命令、待恢复或控制/环境/产物操作，原子写switching）→关闭旧进程/建立新转发→finish（目标信息和revision，解除switching）。失败保留待核对状态，原操作可重试，不能用超时自动猜成功。run:start准入在同一registry事务检查switching和目标版本，避免检查后又开始run。网络元信息随每轮命令提供给Runner，更新后旧容器下轮也能得到新目标；明确连接端点、原域名/TLS server name及HTTP base path，保持证书校验。运行中不重定向连接，不自动终止用户任务。

### 5. 页面、示例与组合验证

云端任务页“服务连接”居中弹窗：profile选择、两个槽位、地址、TLS、TCP测试、保存应用/断开、状态和影响说明。使用现有CSS变量/Modal、键盘与窄屏布局，不新增UI依赖。创建页范围选择复用现有风格。

详细配置样例包括两用户不同network_id/端口、共享SSH别名、ssh-key预配置、GatewayPorts clientspecified/PermitListen及桥地址限定、Windows服务启动/退出/断线/换地址、HTTPS Host/SNI与gRPC authority示例。只编辑仓库示例，不修改用户配置。不声称两个端口可以同时维持两套不同HTTP目标。

## 影响范围与验证

涉及超过8个文件：contracts/cloud_inputs/cloud_sources/cloud_local_tasks/cloud_tasks/cloud_runner；cloud_files/sources与新增范围/隧道模块；application/cloud_local、cloud/runner_runs及storage/cloud/lifecycle；两个service路由；前端创建/服务连接组件/API/type；配置示例、lock-hierarchy、handoff和回归测试。

数据流：页面→本机鉴权API→本机隧道管理器→SSH；页面→本机管理器→Coordinator网络事务→任务run准入→Runner命令上下文。没有模型参与路由决策。

每片TDD先红后绿。验证非Git、目录递归/排除/越界/符号链接/预算、Git scope回执、新分支来源；双用户网络路由、切换与run竞争、后台阻塞、断线重试、主动停止、进程启动失败、重启后恢复原切换、旧容器下轮更新、前端失效预览及状态。定向测试后完整pytest、npm test -- --run、npm run build、标准wheel构建、git diff --check。Windows/公司SSH/真实容器/真实HTTPS与gRPC由原机验收，不以模拟测试替代。

不自动提交/push/发布；回滚源码不会删除用户项目、云端任务或网络状态。新增字段有缺省值，已配置任务不能因回滚重定向到其他用户。

## 实现细化

- 结果冻结统计全工作区，故同步ResultLimits默认max_result_bytes=3GiB、max_files=65536、max_metadata_bytes=16MiB；patch/报告预算保持，2GiB输入不代表允许2GiB补丁。Runner输入等待默认1800秒。
- 页面的Git scope独立字段最终用LocalSourceRequest.paths传入，SourcePolicy.scope记录；老selection具体文件请求仍可用。
- 隧道未完成切换支持cancel：本机先持久cancelling，关闭新进程，再请求云端取消原操作，云端恢复旧目标并递增revision。丢失commit ACK时先核对云端已提交事实，不把已经成功的切换当未完成回滚。取消/重试幂等；状态恢复后旧目标由supervisor重新连接。
- 两个业务端口采用SSH精确绑定私有容器桥地址，需要管理员允许GatewayPorts clientspecified和精确PermitListen；不另外加一个常驻relay服务。SSH进程由本机服务拥有，HTTP和gRPC分开，控制连接沿用已有ProfileConnections。

- 完整InputPackage包含manifest和selection，CloudConnection.max_response_bytes同步默认64MiB；旧connection.json显式4MiB须手动对齐。Runner等待输入默认1800秒，不更改WS帧预算。
- 最终review补齐：首次意图持久化失败回退内存，不允许后台执行未落盘意图；pending切换与两个槽位各自隔离失败，其他槽位持续恢复；关闭一个进程失败仍尝试回收另一个。
- 来源浏览/预览复用delivery_paths.parent固定目录身份：POSIX使用dir_fd，Windows持有拒绝删除的目录句柄；不在校验后重新解析可替换目录。回归分别验证两平台的安全机制。
- 目录选择支持排除子文件；被排除目录内的子项须先重新选中目录，再取消不需要的文件。选择“全部Git文件”清空排除项，避免隐藏过滤。

## 最终本机验证（2026-11-04）

五片均接入真实入口，非占位。全量后端6425 passed（529.05秒，0失败/错误/跳过）；最终定向35 passed包含全量收集后新增的同Runner连续两轮路由更新用例；前端74文件613 passed、生产构建0错误。macOS ARM64 / Windows x86_64 / Linux x86_64标准wheel、Twine、522份Python源码及Web资源一致性验证通过；Mac源码外安装烟测复用已有依赖。compileall、diff检查与代码文件500行限制通过。

2GiB零填充文件以tar捕获，输入2147483648字节、归档2147491840字节，validate_package通过；5.93秒仅是本地探针，不是公司网络门禁。真实组件在模拟API夹具下检查1440/375布局；临时夹具与Vite已关闭。

验证命令、早轮失败原因及最终日志见[报告](../../build/input-network-20261104/report.md)和[结构化证据](../../build/input-network-20261104/verification.json)。配置/Windows步骤见[手册](../cloud-service-tunnels.md)。真实Windows SSH、公司容器网络、双用户与HTTPS/gRPC端到端仍待原机验收；本轮没有发布安装包。

## 0.5.8发布前兼容复核

用户授权正式0.5.8标签发布，取代0.5.7候选附件方案。Windows范围比较按平台normcase处理，保留Linux大小写区分；无network_id的命令省略services=None，保持现有0.5.7严格Runner可接受。两项回归先红后绿。

正式四平台安装流程新增release_network_smoke：临时中文/空格目录浏览、输入捕获/归档校验、侧车文件原子替换、真实本机子进程管道/关闭；有Git时验证本机路径大小写语义。该步骤不连接真实SSH，不能替代公司网络验收。新脚本由release_smoke调用、verify_wheel_install核验结果，并随附加包交付。

发布全量回归额外复现Updater读取与原子替换并发：状态文件在open与lstat间更换，原安全校验拒绝身份不一致。限定cloud_install.store.read最多三次重做完整私有文件/句柄校验，仅重试unsafe_store_file EINVAL并记录安全码，其他错误原样传播；不改变共享文件安全原语。5项确定性回归保留符号链接/硬链接/权限拒绝，替换竞态先红后绿，Updater与文件专项25通过。无网络绑定的task视图同时省略network_id=None；用0.5.7真实模型验证旧客户端/Runner可接受。
