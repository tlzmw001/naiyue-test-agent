# Spec140A：OpenSandbox 接入与业务合同

日期：2026-10-28。状态：候选合同，因部署约束变化暂不实施。用户当前只能使用原机器和既有 Docker，见[主 Spec 最新约束](140-opensandbox-migration.md)。与[验收手册](140b-opensandbox-acceptance.md)一起保留作为评价参照。以下 OpenSandbox 字段/接口不是已批准实施方案或 0.5.5 已有配置，不能直接复制到现有配置，也不能自动套用到替代后端。

## 1. 版本、连接与凭证

控制端使用 `opensandbox==1.2.0` 作为 S0 验证起点；Server 独立安装 `opensandbox-server==1.2.0`。发布使用 S0 最终确认的包哈希与执行镜像 digest，不从源码 tag 推断 execd/egress 镜像标签。依赖解析冲突只报告并回到本 Spec，不顺便升级控制端现有依赖。

一个 Naiyue 服务实例在自己的 asyncio loop 内拥有 SDK 资源，按冻结连接身份复用；不跨 loop 共用客户端。普通数据请求使用受限连接池，管理/中断保留独立连接额度，不能被大文件或长期流占满。退出先收尾任务与管理 operation，再关闭 SDK 和本服务创建的 SSH 子进程。

SSH 只转发到配置的 Server 地址，绑定本机回环，不做动态代理，不转发 agent；保留系统 OpenSSH 配置、StrictHostKeyChecking、当前 PROGRAMDATA 等必需环境。`-N`、`ExitOnForwardFailure` 与保活由隧道实现统一生成；不能继承旧数据命令的 `ClearAllForwardings=yes` 后又期望 `-L` 生效。停止只作用于本服务拥有并核对身份的 SSH 进程，不影响 VS Code 的连接。暂不支持后台密码/二次验证弹窗。

Server API key 仅控制端持有；OpenSandbox Server 及其 Docker 权限是可信部署层。浏览器、模型参数、业务镜像、工作负载 env、日志和业务记录不能包含原始 key；绑定只保存凭证引用名。`use_server_proxy=True` 为本后端固定策略，任意代理目标、docker socket 和 UID 不作为模型工具参数开放。

## 2. 新配置合同

新配置与旧 Docker/envd 配置不能混用。保留现有 profile 名称与镜像选择、Git 预算和应用并发预算的职责；配置只在显式加载/刷新后用于新绑定，已有绑定不自动改目的地。Host 模式无需配置 OpenSandbox。

| 拟新增字段 | 默认/单位/生命周期 | 校验及用途 |
|---|---|---|
| `sandbox.backend` | 启用新沙箱时必填 `opensandbox` | 旧活动绑定不能靠补这个值迁移；未启用沙箱时允许缺省 |
| `sandbox.opensandbox.server_id` | 必填，非秘密的 UUID；冻结到 binding | 应用配置身份，不冒充官方提供的加密 Server ID；结合端点、SSH/TLS 和远端 metadata 核对 |
| `sandbox.opensandbox.endpoint` | 必填，HTTP(S) origin；无内嵌凭证/查询/fragment | 直连用 HTTPS；明文 HTTP 仅允许本机回环或下述 SSH 保护的远端回环目标 |
| `sandbox.opensandbox.api_key_env` | 必填，环境变量名 | 加载引用，调用时从服务的安全环境获取；缺失拒绝，不把值落入 binding |
| `sandbox.opensandbox.connection_timeout_seconds` | 15 秒；每次建连接 | 有界建立 SSH/TCP/TLS；不是命令执行超时 |
| `sandbox.opensandbox.request_timeout_seconds` | 10 秒；普通有限 HTTP 请求 | 流式命令由执行/空闲/总期限另控，不能套 10 秒杀长任务 |
| `sandbox.opensandbox.prepare_timeout_seconds` | 60 秒；一次准备的总预算 | 超时结束当前等待并保存可恢复阶段，不删除输入或自动再创建 |
| `sandbox.opensandbox.management_timeout_seconds` | 30 秒；一次后台管理尝试 | 超过则 unknown；已有任务执行超时继续使用原工具合同 |
| `sandbox.opensandbox.max_io_concurrency` | 8；每 binding 正常 IO | 有界并发，不决定 Shell 内部线程数；管理另保留 2 个连接额度 |
| `sandbox.opensandbox.ssh.host` | 可选块内必填，OpenSSH 别名 | 复用旧别名校验；与旧 `sandbox.ssh`、Docker context 互斥 |
| `sandbox.opensandbox.ssh.executable` | `ssh` | 可执行文件名/绝对路径；禁止 shell 字符串拼接 |
| `sandbox.opensandbox.ssh.local_port` | 0，表示本机选择可用端口 | 只绑回环；占用冲突在受限次数内重选，SDK 仅用实际就绪的地址 |
| `sandbox.opensandbox.ssh.keepalive_seconds` | 15 秒 | 心跳；保活失败最多 3 次后视为连接丢失，不能等同业务失败 |
| `sandbox.opensandbox.network_policy` | `restricted` 或 `external`，默认 restricted | restricted 为默认拒绝的包级出网策略；external 也保留管理端点与凭证隔离 |
| `sandbox.opensandbox.allowed_egress` | 默认 `[]`，类型 `list[str]`；模板冻结 | 每项为批准的 FQDN、通配域名、IP 或 CIDR，映射官方 allow target；不接受任意 JSON 策略，空集合不得退化为全部允许 |

SSH 模式下 `endpoint` 指向开发机上 Server 的回环 origin，其端口是 `-L` 的远端目标；控制端 SDK 使用隧道建立后得到的本机 origin，不再让用户手工填写第二个服务地址。直连模式无 `ssh` 块。端口/地址均从配置读取，不从聊天截图写入产品默认值。

`allowed_egress` 只适用于 restricted；external 与非空 allow 列表同时出现时拒绝配置，避免让使用者误以为它仍是白名单模式。目标不得是空字符串或含认证信息的 URL；平台级管理端点禁访规则始终高于业务 allow。

沿用当前已核对默认：CPU 2、内存 2 GiB、PIDs 256、活动操作 16、运行沙箱 2、保留沙箱 10、闲置 1800 秒。它们是应用预算/资源请求，S0 必须证明服务端实际强制执行；官方接口缺少对应能力时不得仅保留配置外观。`rpc_timeout_seconds`、旧帧大小、Docker executable/context 不能假装仍控制新 SDK；迁移校验提示旧字段无效。

旧版还要求只读根文件系统。固定版 Docker [container_ops.py](https://github.com/opensandbox-group/OpenSandbox/blob/release-1.2.0/server/opensandbox_server/services/docker/container_ops.py) 的 host config 接线已看到 capabilities/no-new-privileges/PID 限制，但未看到 `read_only` 参数；本 Spec **不批准取消该约束**。S0 必须核对官方可用的挂载/运行配置并做实际写入探针；若不能保持，记录为安全兼容阻断，提交具体差异后决定官方扩展或策略调整，不能默认接受可写系统目录。受保护目录/工作目录的可写挂载及删除归属随 S0 验证模板固定，禁止用 broad host bind 临时绕过。

Server 配置单独归部署管理：API 认证、监听范围、Docker endpoint、execd/egress digest、安全运行参数、持久目录与日志。拟新增 `release/opensandbox.json` 只记录版本/哈希/架构/能力测试报告引用，不包含用户 endpoint、key 或 SSH 别名。修改真实配置及清单须在实施阶段按用户已批准范围执行。

## 3. 身份、存储与必要保存

| 数据 | 来源与形状 | 消费者/保存边界 |
|---|---|---|
| 环境身份 | 原 binding/project/session；新增 `provider=opensandbox`、`provider_record_version=1`、server_id、provider_sandbox_id、image digest、profile revision、policy fingerprint | 既有 `execution_environments.record_json` 的版本化解码；会话选择、端点解析、恢复、审查 |
| 管理动作 | operation_id、binding_id、action、request fingerprint、state、phase、error_code、时间 | 复用 `sandbox_operations`，kind=`environment_action`；intent 先于远端副作用保存 |
| 工具执行 | 原 operation_id/fingerprint；新增 provider_execution_id（可空）、状态、游标、观察到的退出码 | 原任务/输出归档；收到官方 init/后台句柄立即必要保存 |
| 业务回执 | operation_id、kind、输入指纹、绑定、结果摘要、状态、生成物哈希 | 140A §7 的受保护 store；控制端验证关联后合并，不读取工作区自行伪造的 JSON |
| 准备/管理进度 | binding_id、operation_id、phase、已完成数量/字节、开始时间、有限错误码 | 原观察/状态投影；不另建与操作事实冲突的事件数据库 |

原来的 daemon_id/container_name/volume_name 不再伪填成 provider ID。旧版本记录走明确解码分支，只服务历史展示/迁移检查；新记录写入、列表额度 SQL、序列化、备份/还原、会话重开、CLI/Web 类型和测试必须一起更新。schema 版本在实际迁移切片按仓库当前版本顺增，禁止预占未知版本号。

必要保存失败：禁止提交下一次远端写入、禁止标记 ready/completed。观察日志失败：安全诊断后保留原返回/异常。控制端在发送后崩溃，重开依赖保存的 intent 和原标识核对；不能把记录缺失当作远端没执行。

## 4. 生命周期、管理 API 与交互

新沙箱状态：`creating → preparing → running ↔ paused`；管理中用 `pausing/resuming/deleting`；不可确认用 `unknown`；确认删除为 `destroyed`。`ready` 是输入/能力准备状态，不等于容器进程 running。

创建使用 `timeout=None`，要求查询返回无自动过期事实；不打开官方自动暂停/自动唤醒插件与本应用 idle watcher 同时管控。应用 idle watcher 无活动 run/后台/审批/unknown/未归档输出才允许 pause。正常停服在有界排空后 pause；故障退出不会承诺自动暂停或删除，重启必须核对。

Docker pause 不释放容器内存，也不是结束进程。paused 释放应用“可运行数量”名额，但仍计保留数量及宿主机资源容量；不能通过大量 paused 容器超售内存。新 UI 用“暂停沙箱/恢复沙箱”；“停止任务”调用命令中断，不把两者混淆。

为保持现有公开端点成功合同，新增接口，而非让旧同步 manage 静默变成 accepted：

- `POST /api/v1/environments/{binding_id}/operations`：body 为 `request_id`、`action`（pause/resume/reconcile/destroy）、`confirm_destroy`。认证、项目/会话归属、请求指纹、必要保存通过后返回 **202**，body 含 operation_id/state/phase。
- `GET /api/v1/environments/{binding_id}/operations/{operation_id}`：返回该动作当前事实、错误码和可核对状态。所有查询在服务认证和绑定归属检查后执行。
- 相同 request_id/相同 body 返回同一 operation；相同 ID/不同 body 拒绝。旧管理路径不修改原含义；对不适配的新 provider 明确返回固定 unsupported 错误，新 UI 只调用新路径。
- 已有 run/task 停止入口保持；在准备阶段也登记 control，而非等待 prepare 返回后才允许按停止。

| 用户动作 | 处理合同 |
|---|---|
| 打开管理、刷新、查看日志、切换会话 | 活动任务期间可用；返回登记状态+最后核对时间，不强制等待远端慢查询 |
| 停止本轮 | 先立即记录取消，随后中断本轮拥有的命令；成功反馈须有终止证据，保留已有“跨轮后台”归属合同 |
| 停止单个后台任务 | 只作用于该 operation，不误杀其他任务或 VS Code |
| 暂停 | 活动/unknown 项存在时返回明确原因，入口可点击；不把冻结当作取消 |
| 销毁 | 展示未回收结果和受影响任务，明确确认后登记后台动作；封口→中断→删除→核对，失败可追踪 |
| 断网 | 显示正在核对/状态未知、已耗时和最近阶段；其余会话与只读 UI 可用 |

阶段值固定：`connecting/creating/snapshotting/uploading/installing/loading_resources/ready/interrupting/deleting/reconciling`。只展示真实计数，不生成假百分比；首次发送尚无 run_id 时也按 binding/prepare operation 显示进度。模型请求、工具执行继续用原运行事件，不能把“等待模型”和“沙箱准备”混为同一文案。

## 5. 并发、锁、超时与输出

一个会话仍只接受一个 Agent run；run 内根 Agent/子 Agent 可以有多个任务，共享 binding 预算。`max_active_operations` 统计实际执行中的命令及受保护变更；达到上限明确拒绝，不无限排队。进程的子进程/线程还受整容器 CPU/内存/PID 限制。

每 binding 只在短锁内进行状态检查、配额预留、操作登记和封口；先释放锁，再 await SDK。已存在 operation 保存使用原 registry 短事务。跨 IO 的“活动计数/独占预留”不等于持有 mutex；取消与销毁能设置封口并发出 interrupt，但结果捕获/输入安装等待其条件成立，不能越过独占保护。

新增/改变锁时，同片更新 [lock-hierarchy](../lock-hierarchy.md)，明确查状态不反向等待运行锁、通知回调不重入管理、取消不等数据连接额度。并发测试使用 Event/屏障，禁止用紧 sleep 窗口判断谁先执行。

SDK 重试策略在接入层显式关闭一般自动重试，第一版只由业务层决定：安全只读查询最多重试 1 次且共享原 deadline；修改类请求只有证明未发送才允许再发。已有 operation 的“恢复”是查询，不是重新发送原命令。SSE 断开不是执行成功；终局事件/退出状态缺失保持 unknown。

输出增量按官方具体 cursor 语义保存；不能把日志行游标当旧 envd byte offset。前台 SSE 若无可靠续传标识，重连不拼接猜测的重叠片段，明确记录输出可能不完整。后台日志只在官方保留期内可追查；控制端持续归档，超期或 execd 重启导致记录丢失时维持 unknown，不承诺永久 exactly-once。

单帧、单操作、总输出仍受现有预算保护；超量保留明确截断标记。大文件传输与输出消费不得阻塞停止请求。SDK/SSH 重连使用相同冻结连接与 binding；服务地址/镜像/身份不符直接拒绝，不回退本机。

## 6. 文件口、Skills 与审批

`FileSystemPort` 的资源/文件方法统一异步消费，保留 `prepare_target` 在审批前，实际执行时把目标复核与 read/write/edit 合并在一次受控执行内；不得仅在控制端先 stat，审批后直接上传覆盖。Host 路径同步 IO 可用已有有界 worker；迁移所有当前调用者，不保留仅供旧链路的同步网络包装。

这些 Python port 是内部开发合同，迁移调用方与测试后可替换；对外模型工具参数、用户 HTTP/CLI 已承诺的行为不因此改变。内部可 import 不构成保留第二套旧执行链的理由。

优先使用官方 API 传输、查询命令；严格文件目标复核由固定辅助程序复用当前路径/身份/字节预算逻辑。它只接受封闭的操作集合及验证过的参数，合并执行时再次检查符号链接、父目录身份、目标类型/链接数、来源大小和预期内容指纹。Windows Host 既有行为独立保留。

辅助程序源码/入口位于工作负载不可写目录；无任意 module、Python 代码、可执行文件名或 Shell 参数入口。小型只读元数据参数可以字面 argv；正文/编辑内容通过官方文件传输写入私有暂存文件后引用，不能把敏感正文或令牌放到命令行。一次受控文件操作不再做整容器多次远端核对。

资源准备：每次 run 用一次受限批量辅助调用取得 AGENTS 及两类 Skills 的索引/必要元信息，沿用当前默认来源与同名区分；Skill 正文由 Read 懒加载。默认仅 run 内缓存，key 包含 binding/input_snapshot/路径与内容身份；写入、命令可能改文件、重连或新 run 后重新核对，第一版不跨 run 复用未经校验的快照。

隐藏文件、只读权限、未知 Skill 格式与超预算仍按原规则产生诊断/拒绝，不为了速度放宽。扫描在沙箱已安装的输入内进行，不静默切成本机 Skills。

## 7. 受保护业务辅助程序与 Git

必要性：官方 API 的通用文件/命令合同不能替代本项目目标复核、输入一次安装、结果一次捕获。这里只保留这些业务语义，不再维护通用 envd socket、传输帧、进程调度、状态轮询协议或第二个后台服务。

候选执行边界：官方 execd 为受信控制进程；普通 Shell/argv 明确降到工作负载 UID/GID，不能接受模型指定 UID。固定辅助程序由控制端通过 execd 以受信身份调用，私有状态目录及暂存区只归受信身份。辅助程序不能执行用户 Git hooks/filter、加载工作区 Python 包或接受任意脚本；解释器隔离模式与固定路径不可由工作区覆盖。具体可用权限组合由 S0 证明，不能默认授予 privileged、SYS_ADMIN 或关闭 seccomp。

业务回执只在该私有目录保存，按原 operation_id+payload fingerprint 排他预留。成功前写入 started；产物先固定并计算哈希，再原子发布回执。中间崩溃后只查询/核对，不能看到 started 就重新执行；重复同 ID/不同内容拒绝。持久记录仍不提供不可实现的跨数据库/文件系统原子事务承诺。

| 操作 | 保留合同 | 失败处理 |
|---|---|---|
| 输入 | 本地 `original_head/input_snapshot`、忽略/凭证/链接/预算规则；有限 bundle、受保护暂存、校验后独占安装 | 安装前后回执丢失查原 ID；未确认不能开始模型工具，不重新 checkout 覆盖工作负载改动 |
| 文件修改 | 审批对象与实际目标一致；受保护回执、内容指纹与一次变更 | 不确定时查询回执与目标；不能把当前内容恰好相同当作充分执行证明 |
| 结果 | `original_head/input_snapshot/output_commit`；无活动/unknown 任务；独立 index；一次不可变 bundle | 按原 result_id 恢复下载；记录缺失不得重新捕获成另一份同 ID 结果 |
| 结果导入 | 控制端 quarantine 验证 SHA/ref/fsck/tree/祖先关系，再 CAS 导入应用 ref | 未验证/未必要保存不显示 ready；不能改用户 HEAD/index/工作文件 |
| 交付 | 延续当前本地合入/建分支/丢弃引用及确认 | 不新增 push/PR、凭证转发或自动 checkout |

控制端记录是决策事实来源；受保护远端回执是核对证据；沙箱工作区/Git/普通日志是待验证内容。若 execd 凭证可被工作负载拿到并调用特权 API，以上边界整体不成立：S0 阻断，不允许仅以目录 chmod 宣布通过。

## 8. 安全与网络门禁

- Server 必须开启 API key 认证；服务监听范围及每沙箱端口映射都要核对。server proxy 不会自动关闭容器直连端口。
- 工作负载不得拥有 Docker socket、主机目录、Server key、SSH key/agent，不能读取 execd 控制 token、受信进程环境/状态/暂存正文，不能伪造特权请求或发信号破坏受信控制进程。
- 受信 UID 与工作负载 UID 分离；补充组、能力、`/proc`、环境继承、setuid 路径、可写入口均纳入测试。选官方配置能实现的最小权限，失败时不上生产。
- restricted 模板使用官方包级 `dns+nft` 默认拒绝；仅 DNS 过滤不算通过。验证直接 IP、IPv6、UDP/DNS、宿主机和兄弟沙箱；网络策略管理凭证不能给工作负载。
- 原 `network.mode=none` 不静默映射成 bridge/external。旧模板迁移需要明确接受“允许受控管理链路、禁止工作负载出网”的新语义；无此确认就拒绝启用该模板。
- external 模板允许业务网络也不允许读取控制凭证、主机 Docker API或修改网络策略。测试只访问操作者批准的哨兵地址，不扫描公司网段。
- Server 拥有 Docker 管理权限，只绑定授权环境，不能给工作负载挂同一 socket；容器边界仍依赖实际运行时，不宣称 Docker 等同微虚机隔离。

## 9. 可观测性与错误归属

模型工具结果：原命令 stdout/stderr、退出码、文件错误、安全限长错误提示；拒绝/unknown 明确说明不能自动重试。不得把内部地址、认证头和原始 SDK 异常对象直接给模型。

安全日志：固定 phase/code/provider、binding/operation/run/tool 关联、耗时/数量、连接重建原因、底层错误类别；不得记录 key、请求/响应全文、命令正文、文件内容或完整用户路径。日志故障保留主异常；必要状态保存故障是业务失败，不能按普通观察故障降级。

前端诊断：准备阶段独立于模型 HTTP 尝试计数；保留最近进度、终局和 unknown 原因；“正在处理”不能成为唯一信息。SDK 的 ready/health、命令接受、退出码、业务验证分别显示，不能合并成一个成功标记。
