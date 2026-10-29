# Spec141A：envd 控制身份与工作区执行身份

日期：2026-10-29。状态：**第0节权限、文件和Git切片已实现并通过本机验收，证据见第8节；尚未发布/部署，公司验收和完整性能迁移仍待完成。**

父规格：[Spec141](141-persistent-sandbox-performance.md)。本片不升级公司 Docker，不挂载宿主 Docker socket，不开 TCP 端口，不部署远端服务，不包含完整异步工具/UI 迁移。最终需要新的 envd 镜像，旧镜像不因控制端更新自动获得权限隔离。

## 0. 当前建议与旧方案撤回

**当前建议：受保护的 envd 直接执行限定工作区的文件 IO；Shell/AITest/代码任务仍以 UID/GID1000、零能力运行。** 撤回下文第3至7节“同 UID 工作区执行者 + 控制进程仅三项能力”作为首选设计；这些节仅保留旧提案供查证，不是实施合同。第1至2节的原语实验仍有效，但不代表新方案验收。

原先“给控制进程跨账号文件访问能力就会破坏隔离”的表述过于绝对。控制进程与任务拥有不同权限本身是合理机制；真正需要证明的是任务不能获得这些能力、控制入口不可被任务调用、文件 API 不能被路径替换等输入诱导去操作控制状态。能力在进程层生效，并非自动只限定 `/workspace`，因此路径边界是启用能力的前置条件。

### 0.1 上游源码证据

本轮核对固定版本，不能将所有部署方式概括成同一权限模型：

- OpenSandbox `62a64f0b48cdd6b410f36e53c74dc7559c87719a`：普通 [DownloadFile](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/web/controller/filesystem_download.go) 由 execd 直接 `os.Open`；[上传](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/web/controller/filesystem_upload.go) 直接写文件，再经权限函数设置模式/所有者。普通文件链没有每次切换成任务 UID 的步骤；能否跨用户读取取决于 execd 的实际身份和运行能力，不能宣称它总是 root。
- 该版本的可选隔离会话另有 [ConfinedView](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/isolation/confined_view.go)，通过 `os.Root` 操作受限目录。可借鉴“常驻服务 + 限定文件视图”；不照搬其 bwrap 部署，该分支 Docker 服务会增加 SYS_ADMIN 并放宽 AppArmor/seccomp，不符合本项目当前约束。
- E2B runtime `b9941e251c126641bd2dd5b7c15099dc5c00a898`：[服务模板](https://github.com/e2b-dev/runtime/blob/b9941e251c126641bd2dd5b7c15099dc5c00a898/packages/orchestrator/pkg/template/build/core/rootfs/files/envd.service.tpl) 明确 User/Group=root；[下载](https://github.com/e2b-dev/runtime/blob/b9941e251c126641bd2dd5b7c15099dc5c00a898/packages/envd/internal/api/download.go) 解析用户/路径后由服务直接 `os.Open`；[上传](https://github.com/e2b-dev/runtime/blob/b9941e251c126641bd2dd5b7c15099dc5c00a898/packages/envd/internal/api/upload.go) 直接写并设置归属。这里的用户参数不等于文件接口以该用户身份执行。其 microVM 隔离与本项目共享宿主内核的 Docker 边界不同，不能据此宣称本项目恢复记录已安全。
- Pi `a69bef789bc95abf0acee16f7b4660b70b650bb9` 的 `read.ts` 默认 `ops.readFile → fsReadFile`，可替换远端操作；AgentX `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f` 的 `executeContainerCommand → DockerService.executeCommand → execCreate/execStart` 不提供这里所需的受保护文件视图。当前 LangChain1.4.2 的工具调用也不替应用决定 Linux 身份边界，因此本次不能从模型工具框架获得权限保证。

### 0.2 建议的真实路径与最小验证

正常 Read：现有授权/subject → 常驻通道 → envd 验证当前环境和请求 → 在固定工作区目录句柄下打开目标、核对对象身份/类型/预算 → 读取字节 → 返回现有工具结果。任务创建的0600文件保留权限；控制进程通过显式授予的文件能力访问，不递归 chmod，不为每次读取启动子进程。

失败例子：任务把 `/workspace/report.txt` 换成指向 `/state/journal.sqlite` 的软链接。接口必须拒绝该请求；不能先 `Path.resolve` 检查、随后重新按路径打开。现有 `EnvdFiles._safe → HostFileSystem` 不足以作为提权后的验收依据。优先参考已有 `git_snapshot.read_input` 的目录句柄、`dir_fd`、`O_NOFOLLOW` 和前后身份检查；工作区内链接的现有使用语义须保持或明确协商，不能悄悄全部禁止。越界链接、路径替换、特殊文件和硬链接风险均需确定性回归；不将新内核专用 API 设为公司环境的未验证前提。

Write/Edit：由受保护 envd 执行并观察必要写入/落盘步骤后，再记录原 operation_id 的终局；失败或证据不足保持失败/待核对。新文件归属 UID/GID1000，保留约定模式。这个 completed 证明该操作当时完成，不承诺任务之后不能再次修改文件。旧提案中同 UID 执行者的“成功”回执不能单独支撑这个承诺；仅校验 request_id/字段格式无法解决其可信性。

当前实施集合：SETUID/SETGID/KILL、DAC_OVERRIDE（跨账号文件访问）、CHOWN（创建结果归属）、FOWNER（任务拥有的sticky目录中替换文件）。它们只由控制身份持有；任务及后代的effective/permitted/inheritable/ambient能力清零，no-new-privileges禁止重新获得权限。第8节包含真实UID/0600/归属/停止与FOWNER开关对照证据。不增加SYS_ADMIN、SYS_PTRACE，不使用privileged或放宽现有seccomp/AppArmor。新策略已进入本机候选创建与inspect检查；公司现有运行配置未变。

Git输入/结果也须接线：受保护 state 中的对象/日志由可信控制路径维护；不能把当前 Git 调用整体改为 root 后在任务可修改的 `.git/config` 等环境中执行。现有工作区内容读取可复用受限文件入口；工作区安装、正式对象生成、所有者、落盘提交点和断线查询的具体交接仍须完成独立契约与真实夹具验证。未完成前 G1 不通过。

验收至少包括：0600读写及正确文件归属；任务不能访问控制 state/socket/环境或恢复控制身份；软链接与并发路径替换不能越界；任务伪造输出不能记成文件写入成功；真实 Git 输入/结果、停止与断线恢复；200MiB输出与8路IO并发。无需新增 IO 守护进程/内部协议、数据库或本机 Skill 副本。网络 RPC 次数预算保持父规格方案，但本轮未测新权限路径，不能声称已经达到秒级。

### 0.3 两层权限与链接处理的实现切片（已接线，验收见第8节）

第一层由操作系统执行：控制目录及其父级不可由任务修改，state为控制身份所有且0700，socket为0600；控制程序和启动配置位于只读镜像。任务创建必须统一设置UID/GID1000、清空附加组与全部能力、no-new-privileges、关闭控制FD并过滤环境；解释器探测、搜索程序和任务派生进程同样适用。降权或启动策略核对失败时拒绝准备，不能回退为root执行。能力值来自受信策略，不能由模型/RPC参数改变。任务直接访问 `/state`、调用socket、给控制进程发信号等均用真实进程验证拒绝。

第二层由envd文件入口执行，目标是使调用者无法借控制身份越界：

1. 启动并核对挂载后固定工作区根目录句柄；只接受注册工作区内的相对路径，或能够按路径组成部分映射到该根的绝对路径。不能用字符串前缀判定，`/workspace-other`不属于`/workspace`。
2. 逐级使用父目录句柄和`O_NOFOLLOW`打开目录/最终文件，后续操作继续使用这些句柄，不返回完整路径让下游重新打开。`O_NOFOLLOW`只管当前打开的最后一级，因此父目录也必须逐级处理；不能只在最终文件上加标志。
3. 遇到软链接时读取其链接文本，由受限解析逻辑处理；相对目标从所在父目录解析，绝对目标只能重新映射到注册工作区。`..`不能越过根，链接循环/过长链明确失败。工作区内正常链接可访问，跨工作区链接拒绝；不直接把链接目标交给不受限`open`。旧内核基线使用Python已有`dir_fd`/`readlink`/`O_NOFOLLOW`能力，不把Linux5.6新增的`openat2`作为部署前提。
4. 最终句柄核对文件类型、设备/inode及审批subject，再从同一句柄读取。审批保留既有路径/类型/父目录anchor语义，不把审批时文件inode冻结为新授权合同；同批Write之后Read/Edit可读取合法新版本。对象身份在每次实际打开前后核对。链接在打开前被替换时拒绝或重新完成受限解析；与审批目标不符时返回目标变化。打开后改名或替换路径不会把既有句柄转向新文件。句柄固定不意味着内容不可并发修改，仍保留既有变化检查、字节限制与冲突结果。
5. 写入、创建父目录和原子替换同样必须以固定父目录句柄执行；不能在读取安全后重新使用字符串路径提交。临时文件完整性、硬链接别名、目标替换与落盘/journal提交点另有确定性回归；该写入合同与Git交接未闭合前，不把控制进程文件能力接入生产。不得以一把Python锁声称能阻止任务直接修改文件系统。

必要例子：`/workspace/latest.txt → reports/result.txt`允许；`/workspace/latest.txt → /state/journal.sqlite`拒绝；父目录在检查后被替换为指向`/state`的链接不能使读取越界。竞态测试用事件停在“已检查/已打开”边界后再替换，不依赖极短sleep；分别检查拒绝、保留原句柄且不读控制内容，以及审批对象变化时明确失败。

接入覆盖Read/Write/Edit、资源/Skill/AGENTS读取、目录遍历和Git快照，禁止保留绕过受限入口的`Path.open`分支。需要外部进程的find/grep等必须走统一降权启动路径，不能继承控制能力。此切片仅修改envd侧的权限实现，本机文件工具外部行为保持；候选镜像验收前还须验证真实Git输入/结果与断线恢复，不能仅凭链接测试通过声称完整隔离成立。

### 0.4 实施中的具体布局与Git交接

工作卷挂载到受控制身份保护的父目录 `/work`，其中 `workspace/` 归任务账号、`.envd-stage/` 为控制身份0700；镜像只读的 `/workspace` 链接到 `/work/workspace`，用户路径不变。两目录位于同一挂载，允许用目录句柄进行原子rename；暂存文件即使已经chown给任务，任务也不能进入其父目录或替换该目录。原始旧布局卷不自动迁移/接管；启动和inspect严格核对新布局，既存公司容器不动。

控制socket所在 `/tmp` 必须是 UID/GID0、1777：任务可以创建自己的临时文件，但不能因为拥有整个父目录而删除root的0600 socket。真实最小容器已复现旧uid1000布局可unlink控制socket；新布局的真实任务必须验证拒绝unlink。容器不得使用host或其他容器的PID namespace，避免新增KILL能力越过自身进程空间。Docker inspect可能给capability添加`CAP_`前缀，核对时仅去此前缀后比较精确集合，不放宽能力清单。

文件Write/Edit把已固定句柄的原内容复制到控制暂存区，在该处复用既有匹配/反馈/原子写算法，提交前核对原对象与内容，设置最终归属后从可信暂存目录原子替换到已固定的工作区父目录。暂存只用于一次写操作，不新增Skill缓存或数据库。中断前未提交不得记成功；rename或父目录fsync结果不确定沿既有unknown合同处理。句柄不提供对任意并发任务写入的全局事务。

Git安装在可信暂存目录checkout已验证的私有对象库，核对HEAD后按不跟随链接的方式设置归属，再移入空工作区；输入安装沿原活动操作排他门执行。结果快照的工作区Git查询/导出命令全部降为任务身份；控制进程只接收有界bundle字节并在私有库中fetch/验证，不以控制身份直接fetch任务工作区。实际工作区文件仍通过句柄读取，正式结果摘要/祖先/路径/内容预算与记录由envd核对；任务侧Git输出不是可信完成回执。此交接须经真实恶意Git配置夹具和故障测试后验收。

控制Git可执行文件必须解析为镜像中root所有且文件/全部父目录不可被组或其他用户写入的真实路径，拒绝工作卷、状态卷、临时目录及伪文件系统中的程序。不能把任务可改写的`container_executable`以root执行。Find将已验证的公开目标映射到固定物理根执行原生程序，匹配结果仍由EnvdFiles规范回公开路径检查权限；解决原生fd输出物理路径与公开别名不一致的问题。

## 1. 为什么需要这一片

变更前的真实链为 `envd.main.serve → EnvironmentOperations → execute_process → HostProcessRunner`。旧镜像和容器均以1000:1000运行，工作负载与envd同UID；0600 socket和0700 state不能隔离相同身份。旧镜像真实任务曾成功打开journal、连接控制socket、读取PID1环境；第8节的新候选已验证这些访问被拒绝。

直接把 envd 改为 root、只给 SETUID/SETGID，再把任务降为1000，仍有两个实际问题：

1. `HostProcessRunner._terminate` 的进程组信号没有权限跨 UID 发送，停止失效。
2. `EnvdFiles.read_resource → Path.open` 无法读取工作负载创建的0600文件；Git 安装如果继续由控制身份执行，还会创建错误归属的工作区文件。

不能通过放宽 `/state` 让任务访问控制记录；控制身份可以拥有必要的文件访问能力，但必须配套限定工作区的接口。该修正及待验证范围见第0节。

## 2. 实测证据与限制

本机临时容器、现有候选镜像、只读根/network none/no-new-privileges；分别仅加 SETUID+SETGID 和再加 KILL，未变更镜像内容或真实配置。脚本 `build/spec141-probe/identity_primitives.py`，输出 `identity-two-caps.json`、`identity-three-caps.json`。

| 检查 | 两项能力 | 三项能力 |
|---|---|---|
| 子进程及其后代 UID/GID1000、附加组为空 | 通过 | 通过 |
| 子进程 effective/permitted/inheritable/ambient capability 为0 | 通过 | 通过 |
| 工作负载访问受保护文件/socket、PID1环境、给控制进程发信号0 | 均拒绝 | 均拒绝 |
| 工作负载恢复 UID0、继承控制 FD/环境变量 | 均拒绝 | 均拒绝 |
| 控制进程直接读任务0600文件 | 拒绝 | 拒绝 |
| UID1000子进程读自己的0600文件 | 通过 | 通过 |
| 控制进程直接停止自己的任务进程组 | **拒绝** | **成功并回收** |

两项能力实验用同 UID 的受控信号辅助进程回收自己的测试任务，三项能力实验直接发送 SIGTERM；没有给其他容器/主机进程发信号。文件为合成控制文件，**不是完整 envd/journal/Git 接线验收**。两个实验都不证明公司 Docker19.03、旧内核或容器逃逸防御已验收。

## 3. 旧提案：三项能力与同 UID 执行者（已撤回首选，勿实施）

| 进程/资源 | 身份与权限 | 责任 |
|---|---|---|
| envd 与 attach | 控制 UID0，drop ALL 后仅 SETUID、SETGID、KILL | 认证身份、调度、可信操作记录、受控降权、停止已拥有的进程组 |
| 工作区 IO 执行者 | UID/GID1000、附加组为空、capabilities全部清空 | 在工作区内执行已有文件算法和必要 Git 工作区操作 |
| Shell/AITest/代码任务及后代 | 同上 | 执行授权任务；退出后不得重新获取控制身份 |
| `/state`、控制 socket | 控制身份所有，0700/0600 | 保存 journal、正式 Git 输入/结果和控制记录 |
| `/workspace` | 工作负载身份所有 | 用户代码和可修改文件；本身不作为可信控制记录 |
| envd 程序、依赖与启动配置 | 镜像只读、工作负载不可写 | 避免修改下一次控制进程的代码 |

KILL 只由控制进程持有。实际停止仍沿已有受控进程句柄/进程组路径，不能暴露任意 PID 的 kill RPC。任务权限字段来自固定执行镜像/受信运行策略，不接受模型工具参数或普通 RPC 指定 UID/capability。

保留只读根、现有资源限额、no-new-privileges和网络策略；不增加 CHOWN/DAC_OVERRIDE/SYS_ADMIN/SYS_PTRACE，不使用 privileged/host PID/host network。新增策略须在镜像创建、inspect、恢复核对、测试、构建清单中同步，不能只改 Dockerfile。

替代方案是继续仅两项能力、每次停止另起 UID1000 信号辅助进程。它还需处理 PID复用、启动失败、等待和清理责任；当前已有进程组控制链，因此优先推荐三项能力。此选择尚待用户确认。

## 4. 旧提案的正常与失败调用链

正常命令：原控制端授权请求 → attach → envd核对身份/预留原operation_id → 通过标准进程创建参数设置 user/group/extra_groups、清理环境并关闭无关FD → UID1000任务 → envd采集有限输出、记录 OS退出/回收事实 → 返回原操作结果。解释器版本探测也必须用同一降权路径，不能 root 探测成功后让低权限任务失败。

停止：原operation_id → envd查自己持有的任务 → 对该进程组发信号 → 等待回收与输出收尾 → 记录终局。没有确认退出就保持 unconfirmed，不把信号发送成功当已停止；禁止原请求断线后自动重提任务。

正常文件读取：审批 subject → envd预留当前请求身份 → 向受控工作区执行者发固定文件操作 → 执行者以UID1000复用路径、对象身份和内容预算检查 → 仅返回该请求的数据 → envd装配原身份响应。0600文件保持原权限和所有者，不靠递归chmod修复访问。

失败：降权失败不得回退为控制身份执行；控制FD/环境泄漏、UID/capability不符时准备失败。工作区执行者断线时，已发送的Write/Edit标为结果待核对，不能自动重放；只读失败可明确返回错误。普通输出/观察失败不能伪造任务终局。

## 5. 旧提案的工作区 IO 与可信状态边界

仅降低 Shell/AITest 的 UID 不足以闭合安全合同。现有 `EnvdFiles`、`InputReceiver.install`、`ResultExporter.capture` 跨工作区和state访问，必须迁移真实消费者。

- 文件操作复用现有 `FileDispatcher/EnvdFiles` 算法，但在工作负载身份执行。为避免每读一个文件重新导入 Python/LangChain，优先验证一个**随 envd 存续的有界工作区执行者**；它只处理明确文件/Git工作区操作，不拥有 journal/socket/state。
- 该执行者与工作负载同 UID，不能当作新的可信控制端。其响应一律作为不可信工作区数据：固定操作、字段和字节预算，严格配对正在等待的请求；不得由其指定operation_id、会话身份、journal状态、宿主路径或触发其他管理命令。可信控制事实由 envd 根据自己拥有的请求和进程状态记录。
- IPC 不传控制socket、journal FD或控制凭证。只有当前操作需要的数据通过有限管道传输。任务关闭无关 FD，只有专门工作区执行者拥有其数据管道。
- Git 输入的正式缓存、安装记录和结果清单仍在受保护state；工作区Git不能再直接读取受保护 `objects.git` 路径。需通过有界输入对象/包流交付，工作区以1000创建文件。
- Git结果从可修改工作区获取；控制端必须自行核对输入基准、包/对象/路径预算和摘要。工作负载返回的清单、摘要或成功字符串不能直接成为可信正式结果。不把工作区中某一时点内容不变当成承诺。
- 此切片先用真实文件/Git夹具验证进程边界及必要数据交接，再决定最小代码结构；不预先建设通用 broker/plugin 平台。已有控制端会话库和envd journal继续使用，不增加第三份持久数据库或本机Skill副本。

## 6. 旧提案的影响范围与实施顺序

| 现有模块 | 必须同步的职责 |
|---|---|
| `envd/processes.py`、`tools/processes.py` | 受信降权参数、真实探测与命令同权限、停止与清理；本机工具现有行为保持 |
| `envd/__main__.py`、`operations.py`、`dispatch.py` | 控制/工作区请求归属、失败与操作记录、关闭顺序 |
| `envd/files.py` 及必要的工作区执行模块 | 重用文件算法；能力有限的数据通道；只读/变更失败边界 |
| `envd/git_inputs.py`、`git_results.py`、必要Git入口 | 正式state与工作区之间的受限数据流，文件归属与结果核对 |
| `sandbox/Dockerfile`、`docker_environment.py`、`sandbox_settings.py`及策略序列化 | 身份/能力创建与完整核对一致；具体配置变更需确认 |
| `release/envd.json`、构建/测试和`docs/lock-hierarchy.md` | 构建输入与独立镜像版本；锁/关闭顺序、故障注入和验收 |

先完成 G1 专属容器中的命令/停止/文件/Git路径，再迁正式控制端/UI。新的工作区执行者不能靠声明存在过关：必须分别验证源文件实现、实际启动、所有相关消费者接入；替代的内部旧路径同步迁移，当前公共模型工具合同保持。

## 7. 旧提案的验收与性能门禁

1. 先红后绿：真实任务不能读写journal/安装/结果记录，不能连socket、读控制环境、继承控制FD、发信号/恢复root；子孙进程同样检查。
2. 真实Bash/Python/AITest能力、文件0600读写、Git输入安装及结果取回成功；所有工作区文件归属正确。测试权限与生产降权入口一致。
3. 控制进程没有额外能力；降权失败、执行者被工作负载杀死/伪造响应、断线、停止竞态均有确定性断言。恢复不重复执行写入；旧镜像/旧状态明确拒绝或按批准兼容范围核对。
4. 与Spec141相同的200MiB输出+8路IO和控制名额压力；单次读/20Skills业务批量、进程启动/停止、Git落盘分别计时。
5. 常驻执行者热调用的新增本地调度预算候选为20ms，必须实测，不能用本轮简单Python子进程替代整个业务链。按公司RTT52.679ms的旧条件预算，即使增加20ms，Read/Skills仍有秒级余量；未验证业务处理/排队，不能承诺端到端p95。
6. 标准控制端/envd构建、全量相关测试后，再进行获准的公司候选镜像验收；不把本机Docker29.5.2结果冒充旧Docker19.03结果。

第3至7节的三项能力/同UID执行者方案已撤回，保持未实施。当前第0节方案的实现结果以下节为准。

## 8. 第0节方案的本机实施与验收（2026-10-29）

本次仅完成身份、受限文件和Git交接切片。新增`envd/identity.py`、`workspace.py`、`file_mutations.py`都由现有生产入口调用；旧EnvdFiles按完整路径检查后交给HostFileSystem重新打开的分支已替换。HostFileSystem和通用Read/Write/Edit算法仍有本机工具消费者，不删除；没有同UID常驻IO助手或第二套数据库。

正常链：`DockerEnvironment.create/verify_container → envd.serve.initialize_identity → EnvironmentOperations`核对控制策略并固定根；`DockerFiles.read → FileDispatcher.dispatch → EnvdFiles.read → Workspace.open/check`从同一FD读取。任务创建的0600文件仍保持0600。Write/Edit经原operation_id预留，`EnvironmentOperations._execute → FileDispatcher → file_mutations.mutate`复制到控制暂存区、执行既有算法、核对原内容/身份、chown/fsync/目录FD替换后返回结果，再写journal终局。Find/Grep只启动低权限原生进程。

失败链：越界软链接由`WorkspaceOutside → ResourceBoundaryError → env_resource_outside`返回；Skill/AGENTS发现层按既有合同记录诊断并跳过，不让一个坏链接终止整次发现。工具Read返回ToolException供模型处理。文件提交结果不确定保持unknown；丢失回执查询原ID，不能重放Write。底层必要IO错误保留诊断，普通观察不能伪造业务完成。

已复现并补回归：父目录在打开后被替换为外部链接、硬链接、循环链接/FIFO、前缀相同的外部目录、提交时父目录替换、内部链接正常Write/Edit、同批预审批Write→Read/Edit、延后创建父目录；实际原生fd在别名工作区的Find。原进程故障测试补真实临时工作区，使其满足根FD初始化前提，原异常/事件/不重放断言全部保留。无放宽断言或跳过失败测试。

最终证据目录`build/spec141-identity/`，汇总`verification.json`：

| 验证 | 实际结果 |
|---|---|
| 全量后端两个分片 | 2891 + 2984 = **5875 passed**，收集的5875个唯一用例均有通过记录 |
| 专项/收尾 | 115专项通过；最后60项身份/文件/进程回归通过 |
| 前端与构建 | 554前端测试通过；TS/Vite、标准macOS ARM64控制端wheel、Linux ARM64 envd wheel和镜像构建均退出0；Twine/pip check通过 |
| 候选产物 | 独立envd0.5.5、AITest Kit0.6.0；镜像62份执行源码与当前清单逐字节一致 |
| 真实权限 | UID/GID1000、附加组空、子进程同身份；有效/允许/继承/ambient能力0，no-new-privileges=1；恢复UID/GID0、访问state/socket/控制环境/FD、修改stage、移动工作区根、替换socket、给PID1发信号0均被拒绝 |
| 文件与Git | 0600文件可由envd读取/写回，归属仍1000；内部链接可读、越界链接拒绝；真实Find/Grep、671文件输入安装、Git结果导出成功，任务仓库fsmonitor钩子未被执行 |
| FOWNER必要性 | 无FOWNER不能删除任务所有sticky目录中的文件，有FOWNER可完成；见`fowner.json`。此前KILL开关实验见第2节 |
| 并发/恢复 | 200MiB输出与8路小IO并行通过，输出保留上限8MiB；断线查询原操作/停止、丢失进程及文件回执、正常关闭、envd重启unknown、控制端崩溃恢复通过；0供应商请求 |

镜像标签`naiyue-envd:spec141-identity-verified`，ID `sha256:1764a4d577e61803764e8c6f8a2408daaf00f8382720fdf0e5bc7e601e09485e`；执行源哈希`9e3bfc470852240abd1fe2cee3fab6a8d31c6614a4235235ef677b7a7104268f`。这是本地候选标签，不是发行tag。构建清单`release/envd.json`仅用于候选构建，真实配置和依赖未变。

复核命令（仓库根目录，原生fd/rg已按现有流程放入PATH；本机测试进程设置localhost直连，不修改用户代理配置）：

```bash
.venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec141-identity/backend-0-final.xml
.venv/bin/python -m scripts.ci_shards --index 1 --count 2 --output build/spec141-identity/backend-1-final.xml
npm --prefix frontend test -- --run
.venv/bin/python -m scripts.verify_sandbox_identity --context colima-nyt-r1-10 --image naiyue-envd:spec141-identity-verified --output build/spec141-identity/new-identity-report.json
.venv/bin/python -m scripts.verify_sandbox_processes --context colima-nyt-r1-10 --image naiyue-envd:spec141-identity-verified --output build/spec141-identity/new-recovery-report.json
```

最新单轮本机样本：40次小读p95 2.747ms；200MiB输出4.522s，同时8路小IO p95 2.398ms；停止57.8ms。数据来自Linux ARM64/Docker29.5.2及专属容器，不能外推为公司SSH完整任务时延。慢传输消费者、公司旧Docker/内核/amd64镜像验收仍未完成。

所有自建测试容器/卷已由自身清理逻辑移除，测试VM恢复为原来的停止状态，默认Docker context不变。公司资源、用户库、真实运行配置未动，未提交/发布。旧同UID镜像和旧挂载布局不能复用新权限保证，也不自动迁移。下一步进入Spec141正式常驻通道和控制端异步/UI控制迁移，然后再进行公司候选验收；不能把本次结果称为生产卡顿问题已解决。
