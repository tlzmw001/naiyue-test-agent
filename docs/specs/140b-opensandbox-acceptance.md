# Spec140B：OpenSandbox 分阶段验收手册

日期：2026-10-28。状态：**验收计划，尚未执行 OpenSandbox 接入、性能、安全或 Docker 升级验收。**

**最新约束：只能使用当前机器和既有 Docker，机器上有疑似 Kubernetes 管理的运行容器。** 当前只执行 P0 只读盘点；P1 升级/独立环境与 OpenSandbox 安装分支暂停，后文是候选手册，不是当前部署指令。性能/恢复/交互的标准仍用于比较替代设计。不得因手册保留升级步骤而再次建议直接升级这台节点。

关联：[迁移设计](140-opensandbox-migration.md)、[接口与安全合同](140a-opensandbox-contracts.md)。使用顺序为 P0 → P1 → S0 → S1—S5 → 发布候选；某门禁失败就停在该层，不靠后续测试全绿覆盖它。

## 1. 验收分工与现在可以做什么

| 阶段 | 执行位置/责任 | 验收证明 |
|---|---|---|
| P0 现状盘点 | 用户/运维在现有开发机，只读 | 系统、Docker 管理归属、旧容器基线；不证明 OpenSandbox 可用 |
| P1 选定试验环境 | 用户/运维批准独立环境或升级窗口 | 能运行受支持 Docker；原业务基线保留 |
| S0 官方能力验证 | 开发者准备探针；用户在公司 Windows→跳板机→试验 Server 执行 | 原生 SSH、官方 SDK/Server/execd 的真实能力与限制 |
| S1—S5 产品迁移 | 开发者实现与自动化回归；用户验收公司 UI/CLI | Naiyue 真实调用链、恢复、安全、性能及交互 |
| 最终候选 | 开发者与用户 | 同一源码/包/镜像的测试、安装、公司业务与旧环境兼容证据 |

**现在只需要先做 P0。** 公司尚未安装新 Server、项目也尚未实现新后端时，不能运行后文的迁移验收命令。所有标为“实施交付后”的脚本是 Spec 要求的新增产物，本轮尚未存在。

## 2. P0：现有开发机只读盘点

在已经能操作 Docker 的 Linux 终端运行。下面 socket 是用户当前已验证的路径；若实际不同，先改变量。结果留在本机即可，对外只给选定字段，不能分享完整 `docker inspect`/环境变量/配置/认证信息。

```bash
NYT_DOCKER_HOST='unix:///var/run/docker.sock'
uname -r
cat /etc/os-release
docker --host "$NYT_DOCKER_HOST" version --format 'Client={{.Client.Version}} Server={{.Server.Version}} API={{.Server.APIVersion}}'
docker --host "$NYT_DOCKER_HOST" info --format 'OS={{.OperatingSystem}} Arch={{.Architecture}} Driver={{.Driver}} Cgroup={{.CgroupDriver}} Containers={{.Containers}} Running={{.ContainersRunning}}'
docker --host "$NYT_DOCKER_HOST" ps -a --no-trunc --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker --host "$NYT_DOCKER_HOST" volume ls
docker --host "$NYT_DOCKER_HOST" network ls
systemctl is-active docker containerd kubelet
```

`systemctl` 某项 inactive/not-found 不等于验收失败；这只是线索。由运维确认 Docker 是否归 TKE/Kubernetes/其他平台管理、能否有维护窗口，以及新 VM/测试机是否可用。不执行升级、restart、prune、rm，不查看/传出容器里的凭证。

Windows 控制端只确认实际 Python 和 OpenSSH；这些命令不创建连接：

```powershell
python --version
Get-Command ssh | Select-Object Source
ssh -V
```

P0 记录：OS/内核/架构、Docker Client/Server/API、运行时管理者、既有容器数量/业务负责人、卷与端口、控制端 Python/OpenSSH、可用维护窗口。旧截图不能代替升级前的新基线。

## 3. P1：部署选择与 Docker 升级验收

本节为原候选方案，当前不执行。若后续资源条件改变，独立 Linux VM/测试机可重新评估。OpenSandbox 的 Python Server 可以用独立 venv，Docker Engine 的隔离要靠独立 daemon/VM；不能把安装另一个 Docker CLI 当作隔离旧容器。

同机多个 dockerd 不是默认验收路线，官方标为实验性且可能影响网络规则。普通升级也不承诺容器不停机，跨 19.03 到新版本不能靠 live-restore 保证；带 tke 的运行时先取得平台方支持。

若选择原机升级，必须在执行前完成以下清单；这里不提供未经 OS/厂商核对的升级命令：

1. 运维确认目标 Docker/运行时包版本、兼容 OS/内核、维护窗口、旧容器停机预期与禁止修改的资源。
2. 对每个需要保留的旧容器登记：镜像 digest、卷/挂载、网络、发布端口、restart policy、运行用户、限制、健康检查，以及一个由业务负责人指定的代表性请求。
3. 备份应用数据、必要容器定义与主机配置，并在隔离环境验证至少一份实际还原。镜像 `save` 不包含卷数据，不能代替数据备份；不能复制活动数据库文件就称为一致备份。
4. 有条件先在克隆 VM 演练升级和还原。禁止新旧 daemon 同时打开同一 data-root；不能承诺旧二进制可直接读取升级后的元数据。
5. 升级前后分别执行 P0 同一命令，逐项比较；原运行中业务恢复至预期，原停止的业务不意外启动，端口/卷/网络身份保持或变化已审批。
6. 每个旧业务的代表性请求成功，写入型业务做批准的临时测试记录并读回；检查重启/错误日志，无丢数据和越界暴露。运维确认后才允许 OpenSandbox 创建试验资源。

任何旧业务失败，**Docker 升级验收失败**，与 OpenSandbox 的功能结果分别记录；先处理旧业务，不继续扩大新部署。回退按已验证的主机/数据恢复方案执行，不能直接降级包后反复启动同一存储目录。

## 4. S0：先验证官方能力，不接生产数据

### 4.1 准备与依赖

在批准的独立环境安装固定版本 Server，配置认证、回环/受控监听、Docker endpoint、执行组件镜像及日志；Windows 使用独立测试 Python 环境安装固定 SDK。起点为 `opensandbox-server==1.2.0` 和 `opensandbox==1.2.0`，记录所有下载哈希及依赖解析结果。Server 与控制端 Python 环境分开，不替换现有 Agent venv。

执行镜像用本次受控测试镜像 digest，含 Python/Git/Bash/AITest Kit 0.6.0；初始纯官方冒烟可以使用预拉取的固定 Python 镜像，但该结果不能替代业务镜像验收。执行组件/网络组件按对应官方配置固定 digest，不推断它们都叫 `:1.2.0`。

需操作者准备的非秘密值：Server origin、镜像 digest、SSH 别名、远端 Server 端口、批准的出网哨兵目标、报告目录。秘密只有 Server API key、已有 SSH 凭证及必要镜像仓库凭证：从受保护环境/现有认证配置读取，不放命令行、报告或 Git。健康检查通过不能代替认证检查。

### 4.2 一条手动隧道，验证单入口

在 Windows 新终端运行；使用之前已验证成功的主机别名。输入提示后的冒号是 `Read-Host` 提示，输入值后回车即可。此处只转发操作者指定的本机端口，不关闭已有 VS Code/SSH 会话。

```powershell
$NytSshExe = (Get-Command ssh).Source
$NytSshTarget = Read-Host '输入已验证的 SSH 主机别名'
[int]$NytLocalPort = Read-Host '输入本机未占用的验收端口'
[int]$NytServerPort = Read-Host '输入开发机上 OpenSandbox Server 的回环监听端口'
$NytForward = '127.0.0.1:{0}:127.0.0.1:{1}' -f $NytLocalPort, $NytServerPort
& $NytSshExe -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L $NytForward -- $NytSshTarget
```

这个窗口保持运行且没有命令输出是正常的。若用户 SSH 配置显式禁用了转发，需运维确认并处理，不能绕过限制。另一个 PowerShell 终端输入同一个本机端口后检查：

```powershell
[int]$NytLocalPort = Read-Host '输入刚才使用的本机验收端口'
$env:NYT_OSB_ENDPOINT = 'http://127.0.0.1:{0}' -f $NytLocalPort
Invoke-RestMethod -Uri ($env:NYT_OSB_ENDPOINT + '/health') -TimeoutSec 5
```

预期只证明隧道与健康接口可达。后续探针还必须验证：未认证请求被拒绝；正确 key 的生命周期/代理调用可用；浏览器和工作负载拿不到 key。试验结束 Ctrl+C 只关闭这个隧道。

### 4.3 S0 探针的实施交付合同

S0 必须新增 `scripts.verify_opensandbox`，直接使用官方 SDK，不能用 Naiyue 适配器或假服务冒充官方验证。脚本必须先有参数/身份/清理安全测试。交付后执行：

```powershell
python -m scripts.verify_opensandbox --suite contract --endpoint-env NYT_OSB_ENDPOINT --api-key-env NYT_OSB_API_KEY --image-env NYT_OSB_IMAGE --policy-fixture $env:NYT_OSB_POLICY_FIXTURE --allow-create-sandbox --output $env:NYT_OSB_REPORT
```

`NYT_OSB_API_KEY` 由安全方式预置，命令不打印值；`NYT_OSB_IMAGE` 为已批准 digest；policy fixture 只含批准哨兵的目标/预期 allow 或 deny/测试端口，不含秘密；report 为本机新文件，默认拒绝覆盖。

脚本必须：生成唯一 probe_id；创建前登记 intent；使用 metadata 标记资源归属；逐项输出真实开始/结束/失败，生成 JSON；只销毁本次已记录且核对一致的 sandbox/配套资源。不能仅按宽泛 label 批量删除；清理失败保留恢复信息和资源 ID，退出非零。故障注入只针对本次资源，绝不重启公司 daemon 或共享 Server。

| ID | 方法 | 必须满足 |
|---|---|---|
| O01 | Windows 两跳，SDK `use_server_proxy=True`，创建→写→读→运行→查询→销毁 | 只需一个服务入口；不用逐文件启动 SSH/Docker CLI；返回与副作用一致 |
| O02 | 同一 SDK 对象连续调用与 8 路 IO，另发状态/中断 | 复用连接；无串行线程池等待；管理请求不被数据连接耗尽 |
| O03 | Bash、字面 argv、中文/空格路径、stdout/stderr、非零退出、AITest 版本/help/doctor | 返回真实输出/退出状态；Linux 镜像缺 PowerShell 不误报 Bash 不可用 |
| O04 | 前台流、后台句柄、状态与日志游标；中断有子进程的命令 | 输出有序；非零退出可区分；停止后父子进程都不再产生副作用 |
| O05 | 关闭本次隧道，命令继续/重连；停止本次容器内 execd 做独立故障用例 | 记录 ID/日志保留的实际边界；没有终局证据不得报成功或重发命令 |
| O06 | `timeout=None`、pause/resume、Server 测试实例重启后查询 | 无自动到期；原 sandbox 与文件保留；pause 是冻结；管理记录能够重新核对 |
| O07 | 外部未认证客户端、工作负载读取环境/proc/私有目录、调用 execd、修改回执 | 控制凭证不可得、特权请求不可伪造、私有记录不可篡改；任何一项失败阻断 |
| O08 | restricted 策略对批准域名、直接 IP、IPv6、UDP、宿主机/兄弟沙箱哨兵 | 与 allow/deny 清单一致；包级限制生效，不能只验证 DNS |
| O09 | 查询实际 CPU/内存/PID/挂载/用户/capabilities/暴露端口，并尝试写系统目录 | 与模板强制策略一致；旧只读根盘约束未验证前阻断，不能只检查请求字段 |
| O10 | 镜像拉取失败、Server 不可达、401/403、端口占用、错误 host key | 有限时间失败、可解释、无明文 key、无本机回退、无多建/误删 |

O07 若当前固定版本无法在官方支持的运行配置下达成，停止后续生产接线，提交证据与需调整的权限/运行时方案供确认。不要把“接口存在”记录成“安全通过”。

## 5. S1—S5：产品行为验收矩阵

以下测试先在隔离服务与假模型环境做确定性验证，再在公司真实 Server 执行；真实模型验证单列，不用模型偶然没调用工具来通过用例。

| ID | 场景/操作 | 预期和证据 |
|---|---|---|
| A01 | 首次发送、重复提交同 request_id、并发准备同会话 | 一个会话/绑定/输入/沙箱；阶段连续；输入必要保存前无模型调用 |
| A02 | 创建已成功但回执丢失，或本地保存失败 | 保留 intent，按 metadata 核对原资源；不第二次 POST 创建，不误显示 ready |
| A03 | 保存后换配置、镜像、host key、server；跨会话传 sandbox ID | 原绑定不换地址/容器；拒绝不匹配身份，其他会话保持正常 |
| A04 | 20 个 Skills、同名两来源、坏元数据、超预算、命令改 Skill 后下轮读取 | 来源优先级与原合同相同；诊断真实；不使用过期缓存或本机替代内容 |
| A05 | Read/Ls/Find/Grep/Write/Edit；审批期间替换符号链接或父目录 | 正确读写；目标变化拒绝；工作区外/隐藏权限/链接/大小规则未放宽 |
| A06 | Shell/AITest/子 Agent、普通和后台命令，2 个并发长命令 | 在同一沙箱真实重叠运行、输出分别归档；一个 run 的约束不变 |
| A07 | 活动操作上限、运行/保留额度、10 倍 IO/Skills 负载 | 明确拒绝/背压，不无限堆 task/线程；暂停内存不被当作已释放 |
| A08 | snapshot/upload/resources/等模型/执行/收尾各阶段点停止 | UI 立即确认收到；未发出的操作不再发送；已发出的核对或中断，无假停止 |
| A09 | 一个输出流堵塞，另点管理、切会话、停止；桌面及窄屏 | 查看与控制可用，不整页禁用；停止不排在普通 IO 队列后 |
| A10 | 活动任务中明确销毁、重复销毁、关闭浏览器后重开 | 返回同一个管理 operation；后台继续；原资源确认缺失才显示 destroyed/释放额度 |
| A11 | 销毁/中断时断网，或子进程不退出 | unknown/deleting 可见；不报成功，不因上层 await 取消遗留未登记进程 |
| A12 | 回执丢失/SDK 超时/控制端重启；Shell 有一次计数副作用 | 查原 execution ID；不能确定就 unknown；计数不增加第二次 |
| A13 | 输出超过预算/断流/游标重放/官方日志过期 | 有界、无重复拼接、截断或缺失标记；没有终局不推断 exit 0 |
| A14 | Git 输入只含允许项、安装回执丢失、已有工作区改动 | 原 snapshot；重复请求不重新 checkout；未确认拒绝执行 |
| A15 | 结果捕获前有后台/unknown；捕获后断线、续传、伪造 bundle | 有活动时拒绝；一次捕获；同 result_id 续传；坏 SHA/ref/tree/祖先拒绝 |
| A16 | 取回后销毁沙箱，再看 diff/本地交付 | 已保存结果可用；未批准时本机 HEAD/index/工作文件保持；交付沿原确认 |
| A17 | 暂停/恢复、闲置、正常关服务/异常退出后重开 | 原沙箱/文件身份；暂停不冒充终止；无时间到期自动删数据 |
| A18 | 旧 envd 活动绑定、旧配置、新旧数据库/备份 | 阻止无确认迁移；历史可读；不接管旧容器、不覆盖新数据回退 |
| A19 | SDK/日志/事件投递故障，必要保存故障 | 观察失败不改原结果；必要保存失败阻止成功；敏感信息不入日志/模型 |
| A20 | 清理探针自己发生异常 | 原错误保留、清理失败独立可见；只列本次残留资源，不 prune/误删旧资源 |

### 5.1 验证命令与当前状态

已有回归/构建入口，实施后必须执行并保存真实结果；本轮文档修改不声称已运行这些产品测试：

```bash
python -m pytest -q tests
npm --prefix frontend test -- --run
npm --prefix frontend run build
python -m scripts.build_release --target windows-x86_64 --output build/opensandbox-candidate/windows
python -m scripts.build_release --target linux-x86_64 --output build/opensandbox-candidate/linux
```

同平台源码外安装沿现有 `scripts.verify_wheel_install`；其他支持平台沿当前 CI 四平台矩阵。PATH 中的 rg/fd 按发行工具链准备，不能因缺工具而 skip 测试。新镜像走现有 `scripts.build_sandbox_image` 扩展后的正式入口，产物清单记录全部固定版本/digest；不能把旧 envd wheel 的构建通过当作新镜像通过。

S5 新增 `scripts.verify_opensandbox_agent`，迁移/复用当前 `verify_sandbox_environment/inputs/sessions/processes/results/delivery/profiles` 中的行为断言，避免保留两套含义不同的验收。交付后命令为：

```powershell
python -m scripts.verify_opensandbox_agent --suite all --settings $env:NYT_OSB_SETTINGS --workspace $env:NYT_OSB_TEST_WORKSPACE --allow-create-sandbox --output $env:NYT_OSB_AGENT_REPORT
```

settings 是已批准的新配置；workspace 必须为探针初始化的一次性项目，不能指向真实公司仓库；初始 marker/目录安全测试必须拒绝误用。`--suite all` 包括 A01—A20 和下节性能；破坏性故障只作用于探针登记资源，清理策略同 S0。若某组需单独执行，suite 固定为 `behavior/security/performance/recovery`，总报告验证四组完整覆盖，不用缺项替代通过。

## 6. 秒级体验验收：先定义计时口径

以下数字是**设计验收目标，不是当前测量结果**。超标应记录失败并定位，不能临时放宽阈值后声称达标。

标准环境：镜像及 execd/egress 已预拉取；沙箱 2 CPU/2 GiB/PIDs 256；同一开发机最多 2 个运行沙箱；Windows→服务 HTTP 小请求 p95 往返 ≤100 ms、有效传输 ≥20 Mbps，磁盘与负载事实随报告保存。未满足前提仍记录真实用户耗时，但不能把条件外结果用于证明条件内 SLO。

夹具 F1：Git 可跟踪的普通文件共 671 个，其中 650 个正文各 8 KiB、20 个 SKILL.md 各 ≤4 KiB（两目录各 10 个）、1 个 AGENTS.md ≤4 KiB；总 bundle ≤8 MiB。内容确定性生成且记录文件清单 SHA，不含凭据、LFS、submodule、外部 symlink。F2：在 F1 基础上变为 200 个 Skills，测试扫描放大，不要求整个仓库仍为 671 文件。

连接先预热 5 次，再测 30 次，报告 p50/p95/max 与失败数；p95 取排序后第 `ceil(0.95*N)` 项。每个样本从用户可观察或接口明确定义的起点计时；失败和超时不能从报告删除。新建准备测 10 次独立沙箱，逐次核对销毁；十次数据不足以宣称总体稳定分布，须附全部样本。

| 指标 | 起止点 | 目标 |
|---|---|---|
| 点击后的反馈 | 点击发送/停止/管理 → 前端呈现已接受/阶段 | p95 ≤1 秒；网络不通时也不能无限无反馈 |
| 已就绪会话开始下一轮 | 发送已受理 → 资源准备完成、即将请求模型 | F1 p95 ≤3 秒；单独统计模型首 token，不混入 |
| Skills/说明装配 | 开始加载远端资源 → 完成本轮索引/说明 | F1 p95 ≤2 秒；F2 p95 ≤5 秒 |
| 小文件读取 | 工具进入实际执行 → 取得 ≤64 KiB 内容 | p95 ≤0.5 秒；不含人工审批等待 |
| 小文件写/改 | 审批通过 → 远端完成并保存回执 | p95 ≤1 秒；保留身份复核与必要保存 |
| 首段命令输出 | 工具获准提交 → 收到受控立即 flush 命令的首段输出 | p95 ≤2 秒；不能用普通业务命令计算/缓冲时间代替传输 |
| 新会话准备 | 首次发送受理 → snapshot/创建/安装/资源全部完成 | F1、预拉镜像，10 次均 ≤15 秒；每阶段分别计时 |
| 停止请求受理 | 用户点击 → 本地必要状态保存、控制请求登记 | p95 ≤1 秒；不等待任务结束才回包 |
| 实际进程结束 | 中断已发出 → 父子进程停止且核对 | 合作命令 ≤3 秒；拒绝 TERM 的用例 ≤stop_timeout+2 秒 |
| 管理/销毁 | 点击 → accepted；accepted → 原资源缺失确认 | 受理 ≤1 秒；正常链路删除 ≤20 秒；超过总预算进入 unknown |
| 并发时控制响应 | 8 路 IO或大输出期间读取状态/停止 | p95 ≤1 秒受理，其他会话可操作 |

额外统计：SSH 新建次数、SDK 请求类别/数量、服务端端点核对次数、worker 数、峰值内存、上传/下载字节。预热后 30 次小读取不得启动 30 个 SSH 或远端 Docker CLI；不能只看平均耗时而遗漏某次分钟级卡顿。

大仓库/首次拉镜像单独测，不套 F1 的 15 秒；必须显示真实阶段/计数、已耗时，每 5 秒至少有状态心跳或“仍等待该阶段”的时间更新，且控制入口可用。心跳不是虚构完成进度。

## 7. 镜像与真实业务验收

镜像必须在目标 Linux x86_64 实机运行：核对 digest/架构、Python/Git/Bash、`importlib.metadata.version('aitest-kit') == '0.6.0'`、`aitest --help`、`pip check`、一次独立夹具的 doctor/codegen/实际测试结果。只打印版本不能代表真实测试工作流成功。

固定业务任务由用户选一个可控的公司测试场景：明确输入、允许访问的目标、预期结果、最大运行时间和产物。不把未经批准的 URL、模型或凭证填进示例。先以假模型驱动全部必需工具完成系统验收，再单独用真实模型走一次；记录真实请求和结果，不把模型自述当成功证据。

前端人工步骤：首次发送看阶段 → 执行时展开工具看到输出 → 管理面板可打开/关闭/刷新 → 停止本轮并核对父子进程 → 后台任务单独停止 → 新一次运行中确认销毁 → 页面重开能看到管理结果 → 断网时明确 unknown → 结果取回后沙箱销毁仍能查看差异。桌面、窄屏与键盘焦点都验证，控制台不得有未解释错误。

## 8. 报告、判定与发布前门禁

每份报告包含：源码 commit、wheel SHA、镜像与 execd/egress digest、SDK/Server/Docker/内核/Python/OpenSSH、probe_id、用例 ID、预期/实际、起止时间、耗时、失败阶段/错误码、断线时是否可能已发送、资源前后清单、残留/清理状态、供应商请求数。只保存必要脱敏事实，不保存完整配置、密钥、命令正文或用户文件。

逐项状态只允许：`未执行/通过/失败/阻断`。空白结果不是通过；O07/O08、安全/恢复/必要保存任一失败阻断上线；性能未达标即“功能部分通过、性能失败”，不能报迁移完成。后端/前端测试、真实 Docker、公司链路、真实模型、业务结果、旧容器升级分别给结论。

证据保存在操作者指定的新报告目录；文档只引用报告位置和已完成事实。上线候选必须绑定同一源码、同一包与同一镜像，再做四平台安装检查。新镜像发布/离线 ZIP 与版本号另由用户确认，不能沿用已发布 0.5.3 tag 覆盖内容。

验收通过后清理仅本次测试资源，保留用户/运维要求的镜像和报告。正式迁移前另列旧沙箱结果、用户确认、备份及回退清单；探针有删除权不等于允许删除用户旧沙箱。

## 9. 本轮文档验证

本轮只检查文档存在、链接、命令对应当前或明确规划入口、用例覆盖、文件行数及 `git diff --check`；未安装 SDK/Server、未执行以上探针、未升级 Docker、未制作镜像。后续实际结果按对应切片补充，不提前填入通过记录。

2026-10-28 文档检查结果：3 份 Spec 均低于 500 行；7 个相对链接目标存在；Markdown 代码围栏成对、无行尾空白；O01—O10 与 A01—A20 共 30 项编号完整；`git diff --check` 返回 0。命令/构建入口分为当前已存在和明确规划两类；这些检查不证明后者已实现。
