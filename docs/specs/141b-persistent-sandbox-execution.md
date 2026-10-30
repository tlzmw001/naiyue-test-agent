# Spec141B：常驻沙箱连接、异步数据面与可中断交互

状态：2026-10-29 已完成首轮本机实施；后续复核确认可靠性和入口确认缺陷，第10节修正范围已获用户授权，已实施源码与专项验证，最终构建门禁待清单授权。第9节为原候选正常路径证据，不代表异常隔离、长期运行或公司验收已通过。承接 [141A](141a-envd-identity-boundary.md)。本片不发布、不部署、不升级公司 Docker，不修改真实用户配置。

## 1. 问题与范围

现有 `DockerFileSystem → EnvironmentService.request → validate → DockerEnvironment.request → validate → DockerCLI.run` 为每次文件请求反复建立 SSH / docker exec / Python 进程。20 个 Skill 的探针统计为 128 次资源 RPC；公司常驻通道单次 p95 52.679 ms，即使仅保留连接也仍有约 6.7 秒串行等待。需同时减少连接建立和依赖轮次。

批准范围：常驻原生 OpenSSH 数据通道、本机 Docker 同协议、会话级核验、异步文件/进程调用、批量资源读取、状态/输出合并、准备阶段进度、停止与管理独立通路。保留公开工具参数、审批、operation_id、Git输入/结果和不重放未知写入的合同。141A权限边界保持。

## 2. 多会话连接归属

- `EnvironmentService` 拥有连接管理器，连接键包括 binding、完整 container_id、session、daemon 和冻结设置。SSH Host 别名不作为沙箱身份。
- 一个沙箱一条主通道，必要时一个大块数据通道；主通道普通 IO 8 槽、控制 2 槽，结果下载大块 1 槽。每个通道只有一个 stdout 读取者；按 request_id 分派回复，写入串行且有界。单请求取消只丢弃其迟到回复，不取消其他请求；协议/身份错误关闭该通道。
- 同一键并发建连合并为一个任务；建连发起者取消不撤销其他等待者的连接。连接失败只影响该沙箱。下一次调用可以重新核验并建连，但不会重发已交给旧连接的写操作。
- 总连接上限随现有 `lifecycle.max_retained` 有界，每个 binding 最多两条；同时建连数使用明确传输配置约束。不同 Host/沙箱不共用请求表、取消标记、operation锁或输出游标。
- 共享服务关闭负责停止接收请求、收集/停止操作、关闭所有通道并回收子进程；关闭浏览器页签不关闭服务或后台任务。stop/destroy/recover 先使对应连接失效，其他 binding 继续可用。
- 空闲连接可关闭，但容器和任务不因此停止。重连必须重新核验 Docker/卷/策略并握手；envd 每次启动有新的 generation。每帧身份绑定当前连接，不接受旧 generation 的回执。

## 3. 协议和安全边界

`python -I -m naiyue_test_agent.envd attach` 是持久 stdio↔受保护 Unix socket 桥。无需开 TCP 端口，不增加服务器依赖，不改 SSH 配置解析。原生 OpenSSH 继续使用 BatchMode、严格主机校验、禁用 agent/端口转发和 PROGRAMDATA 环境保留。

协议层和进程启动层分开：传输负责可回收字节流，协议负责请求身份、限额和错误分类。握手声明能力和 daemon generation。JSON 消息保持有限总大小；大内容分片，帧有界，读写均有超时和背压，stderr 独立排空且仅记录安全诊断。无无限请求ID集合、无无限排队或输出缓存。

envd 是事实来源，控制状态不可被 UID1000 工作负载访问。桥只接受受控请求，工作负载无法连接控制 socket。SSH 建连的完整 Docker 核验作为同一连接的 JSON 前导：远端等待固定14字节 ACK，控制端验证 daemon/容器/双卷/策略成功才允许 exec attach。前导有消息大小和时间限制，失败关闭原进程。创建预检合并 daemon+image，受控 shell 批次检查双卷 labels 后创建；单独保存新容器完整ID并核对策略后才启动。Docker命令均逐参数转义，独立赋值保留失败退出码；失联不重放创建。

文件执行在一次 RPC 内重新校验审批 subject 与路径；不能因减少控制端核验去掉 envd 检查。prepare_target 保持在审批之前。

## 4. 正常与失败调用链

正常 Read：工具审批准备 → `prepare_target` → 用户/规则批准 → 原有 permit 身份检查 → 一次远端 read 内校验+读取 → 工具结果回填模型。首次请求建立连接并完整核验；后续不启动新的 SSH/Docker/Python。

断线 Write：先保存原 operation_id → submit → 断线标记 unknown → 重连完整核验 → 查询同一 operation_id。查不到保留未知，不自动重做。一个沙箱失败不得取消其他沙箱已提交的任务。

停止：用户请求立即显示“正在停止” → 独立控制槽发送 operation_stop → 确认终态/必要输出 → 完成。数据连接不可用时，管理通路仍能核对、停止整个容器；未确认时显示未确认，不伪装成功。

同一 operation 的停止不能超越尚未完成的提交：提交及其必要本地保存保持独立 custody，取消等待者也必须排空，然后释放 submission Event；stop 等此 Event，但不等输出收集锁。丢回执后，旧 UDS 请求可能已经排队、尚未写入日志，新通道查询“未找到”仍不证明未执行：保留 unknown/stop_unconfirmed，不重放，允许独立停止/销毁容器。不承诺仅靠控制端取消即可保证任务以后绝不启动；本片不引入新的持久停止 tombstone 合同。

销毁：用户确认既有数据风险 → 关闭新操作准入 → 请求停止当前运行/准备 → 核对精确容器/卷 → 沿现有交付保护销毁。不会通过一把跨网络大锁锁住其他会话。

## 5. 资源批量读取

envd 提供有限资源枚举/信息及批量正文读取；应用仍负责 Skill 元数据、来源优先级、禁用规则和提示词。常见20项预期2–3依赖轮次。返回逐项错误和明确分页/截断，不把不完整目录当完整成功。不新增本机 Skill 文件副本或持久缓存。

## 6. 异步与交互

高频网络路径原生 async，不用每请求占线程等待 SSH。同步本地文件/数据库算法按现有有界执行方式保留；迁移实际消费者并删除仅服务旧数据通路的包装。CLI/Web共享同一连接所有权，跨事件循环使用必须显式路由，不能把 asyncio 锁/流拿到另一个 loop。

进程查询合并状态+有界输出，保留 offset/gap/归档及未知结果语义；控制请求不能等待200MiB输出全部读完。准备事件表达快照、容器、连接、已确认上传字节、安装输入、指令/Skills、模型请求。前端按钮按动作状态开放：查看、停止、管理不被全局 busy 一起禁用。

## 7. 实施与验证顺序

1. 传输门禁：多沙箱/同沙箱并发建连、乱序回执、取消、EOF、慢读者、背压、异常关闭、Windows子进程回收。
2. 接线主数据通道，明确旧镜像缺少能力的错误；生命周期/关闭接线和锁表同步。
3. 文件 async 和资源批量读取；20 Skill 调用次数与相同元数据/错误行为断言。
4. 操作状态/输出/停止；丢回执、重启、多个会话、高输出公平性。
5. 准备阶段和 UI 控制；忙碌时管理/停止、错误与重试。
6. 全量测试、标准控制端/envd构建、候选本机容器端到端；公司环境另行验收。

每步先失败测试再实现。新增生产模块按职责拆分且单文件≤500行；执行源清单随候选更新，不发布镜像。关键文件涉及 execution 的 transport/channel/registry、envd attach/dispatch、application environments/sessions、工具权限/文件入口及前端 SandboxManager/Composer/事件消费。具体接口随每步当前调用链写入本节后再实现。

验收目标：暖 Read p95≤500ms，20项资源≤2s，暖发送到首个模型HTTP请求≤3s（不含模型耗时/人工审批）；本地取消反馈≤200ms、控制接收p95≤1s。冷启动671文件/≤8MiB、预装镜像目标≤15s，必须按阶段实测，公司磁盘未知不能承诺。现有公司数据仅是预算输入，不是本片达标证据。

## 8. 实施证据

资源接线合同：`resource_index` 接收有限 `paths` 和 `directories[{path,leaf}]`，只返回文件事实、规范路径和目录项，不读取正文。每个目录最多256项，超额返回该目录明确的 `resource_limit`，不把部分列表用于提示词。`resource_read_batch` 最多32个规范路径，每项最多64KiB，并受整条消息预算约束；逐项返回 bytes 或有限错误码。读取时在已打开描述符上再次比较规范路径，目录在扫描后换成链接时拒绝读取。控制端只为本次准备创建内存视图，复用既有指令/Skill解析及诊断规则；选中的路径一次批量取回正文，大目录/低消息预算用明确诊断，不静默截断。

文件审批：同步本地权限准备可以返回字典；远端路径准备返回 awaitable，权限中间件和恢复审批入口等待其完成后才作规则决策。已发请求保留回执收集；未发请求取消后不得晚到提交。

准备交互合同：新增可选 `wait_for_ready`（默认 true，Web 首次发送使用 false），创建先保存原 session/binding 身份并返回，服务拥有唯一准备任务。状态只保存有限阶段、已确认上传字节和错误码，不保存正文；按现有保留额度限制同时准备，完成记录可淘汰，持久事实仍从数据库与远端日志核对。前端选中原会话后轮询阶段，准备成功继续原消息，失败/取消保留草稿。准备期间仍可打开管理面板。

显式中止管理使用可选 `interrupt=true`，仅 stop/destroy 接受；旧默认仍拒绝忙碌资源。先封闭该 binding 新使用权，再通知当前 Agent 停止及准备任务检查点取消，排空已开始的本地/创建 IO 后执行原精确资源管理；不持有全局锁等待。输入分片之间、提交安装之前及安装轮询检查取消，已提交安装不重放。任务取消或网络响应丢失不撤销服务持有的管理动作；未收到确认显示“正在停止/待核对”，不报成功。其他 binding 独立管理，关闭面板不取消管理请求。已有结果捕获通过原 binding 锁排空后才删除资源。

本机实施证据见第9节；公司原机与实际供应商请求仍按独立验收边界处理。


管理往返预算：健康首建远端管理路径是预检、双卷/容器创建、启动、核验前导+常驻attach，共4次SSH认证；输入上传使用已建立主通道，不为首次上传另建bulk连接。按公司旧连接p95约2.056秒，认证部分条件预算约8.2秒，再加本机快照、确认上传和安装。此处为设计估算，不作为公司冷启动≤15秒的证据；公司链路和磁盘需实际采样。

迁移验证边界：高频文件/进程为async；本地Git输入/结果/交付和有限生命周期保留明确同步worker入口。Delivery.execute/acknowledge唯一生产入口复用既有每项目delivery gate，不再调用已删除的operation RLock。旧故障注入迁到async准备/通道边界，保留原异常/顺序/状态断言；默认transport配置不进入旧profile指纹，旧本机和SSH会话记录仍可反序列化。

运行阶段：`run_preparation` 是只含枚举phase的观察事件（resources/model_request），Web运行快照与SSE同步消费；实际model_call_started和tool_call_started更新显示为请求模型/执行工具。阶段投递失败沿既有观察隔离诊断，不改变资源准备或模型结果；不把“准备模型请求”显示成已经收到HTTP响应。诊断仅保存phase，不保存指令/Skill正文。


## 9. 本机验收与当前主链（2026-10-29）

完整机器证据：[verification.json](../../build/spec141b/verification.json)。控制端源码仍0.5.5，候选产物用于验证，不覆盖已发布版本。未提交、打标签、发布或部署公司环境。

### 当前主链

- 新建：Web `createSelected(wait_for_ready=false)` → SessionAPI.create_sandbox → S1内保存原会话身份 → SandboxPreparations唯一后台任务持原host使用权 → snapshot/container/connection/upload/install阶段 → 原input_id必要保存 → 前端原会话轮询，成功后保存原用户设置并继续原消息。轮询失败保留草稿和待保存设置，不锁死后续新建。
- Read：prepare_permission_call等待DockerFileSystem.prepare_target → 原审批subject → read工具原生async → EnvironmentService.arequest → SandboxTransport主通道 → 单一FrameStream writer/request_id分派 → 原生SSH与docker exec attach → 受保护Unix socket → envd检查generation/身份/路径/subject并读取。暖连接不重复SSH核验或进程启动。
- 中止：全局管理入口显式interrupt只允许stop/destroy → 封闭对应binding新使用权 → 当前Agent停止信号与准备检查点 → 排空已发创建/本地IO → DKR2内精确资源停止/销毁。其他binding没有共享IO锁。默认管理仍拒绝忙碌，销毁仍须确认。
- 断线：已发送变更回执由通道托管；未确认按原operation_id查询，unknown不重放。发送前取消不得晚到提交；同ID stop等待提交保存但绕过输出收集。首次归档创建与身份保存同一个取消排空边界，避免重查误停。
- 指令/Skills：resource_index一次枚举事实 → 本机选择有效来源/安全候选 → resource_read_batch按冻结预算读取正文 → 本次内存视图复用原解析，不落本机Skill正文副本。

### 门禁实际结果

- 后端完整两片：2817 + 3120 = **5937 passed**，0错误/失败/skip；逐项与5937收集节点核对，无缺失/交集。命令 `python -m scripts.ci_shards --index 0/1 --count 2 --output ...`，原始结果 `build/spec141b/final-{0,1}.xml`。其中1条警告来自故意重复ZIP METADATA的夹具。
- 前端68文件 **559 passed**；TypeScript/Vite及标准 `scripts.build_release --target macos-arm64` 通过。标准 `scripts.build_envd --target linux-arm64` 和 `scripts.build_sandbox_image` 通过，AITest Kit **0.6.0**；373份控制端Python源码及65份envd执行源码一致，源外安装/运行时启动关闭、Twine、compileall、pip check、diff检查通过。Vite保留既有包体积提示，无构建错误。
- 本机生产路径：`python -m scripts.verify_sandbox_persistent --context ... --image ... --output ...`；原生SSH使用专用VM、严格主机校验和不共享ControlMaster的临时客户端。两个沙箱并发、200MiB完整收集、小IO公平性、停止、原绑定重连与另一会话继续可读通过。模型为离线夹具，供应商请求0。
- [文件与生命周期门禁](../../build/spec141b/environment-accepted.json)：六个文件工具/图片/审批过滤、卷隔离、禁网、停启、重开、销毁无回退、冻结策略通过。
- 浏览器界面夹具实测忙碌时打开管理、首项等待停止确认时另一项按钮可用、面板可关闭。未借夹具动作操作真实用户容器。安全/架构独立复核发现的准备删除/Host关闭、轮询设置丢失、ACK顺序、shell退出码与归档取消竞态已关闭。

| 指标 | 本机原生SSH实际值 | 采样边界 |
|---|---:|---|
| 671文件首建 | 1.965 / 1.915秒 | 两个样本，不是p95 |
| 完整Read（目标准备+读取） | p95 2.969ms | 40次，两次RPC |
| 20 Skill+项目指令 | p95 7.625ms | 20次，两次RPC |
| 两沙箱并发小读 | p95 32.5ms | 80次 |
| 200MiB输出期间小读 | p95 21.904ms | 40次，输出完整209715200字节 |
| 停止确认 | 61ms | 单样本 |

本机Linux ARM64/Docker29.5.2和低延迟VM SSH不能替代公司Windows/Docker19.03/两跳网络证据；真实供应商首HTTP延迟未测。公司的4次首建SSH认证和约53ms RPC条件预算见上节，需原机验证。候选需要新envd镜像，旧0.5.3镜像缺少attach与141A边界，不能直接获得本轮能力；缺失入口有明确env_attach_unavailable诊断。

候选镜像 `naiyue-envd:spec141b-persistent-accepted`，ID `sha256:ce4e97ec82cac7128b86e67b06a806c6027b90f328b92a015427d62f6acb5d38`。验收创建的容器/双卷均已删除，专用VM恢复Stopped；默认context仍default，原有镜像/真实配置/公司资源保持。

保留入口：同步Git输入/结果/关闭通过明确runtime桥调用同一async操作算法；有限管理CLI仍在worker。低层单次 `DockerEnvironment.request` / envd request用于现行独立权限/协议诊断脚本，应用热路径不使用它。DockerFileSystem旧同步单项资源包装及旧operation RLock已移除；对应旧探针、测试故障注入已迁移，事件测试保留旧业务断言并明确增加两条准备阶段的序号。

该时点的下一步已由第10节可靠性修正取代。公司原机验收须在修正及本机回归完成后进行。

## 10. 复核后的修正范围与设计边界（2026-10-29）

用户已授权修复两轮审查中确认的缺陷。已完成复核、最小复现、实现与本机最终回归（第10.10节）；不新增外部服务或依赖。第9节的原候选结果不代表本轮修正验证。

### 10.1 已确认问题与实现约束

- 请求超时必须区分排队、字节流发送、业务执行和连接活性；尚未发送的排队超时不得关闭健康通道。已发送业务请求超时也不能自动等同连接死亡。协议/身份错误、底层读写失效和EOF仍按通道故障处理。
- 单请求取消和迟到回复均有界管理；Find/Grep取消须进入envd实际搜索控制，不能仅取消attach等待。保留工具既有执行时限语义，不用固定RPC时限偷偷覆盖“无限执行”。
- 控制请求允许有界排队；未发送、远端明确拒绝、已发送未确认须保留区别。容量拒绝不得把已知运行任务强制改为unknown/stale并停止采集。
- 补常驻连接保活与独立探活。空闲后的写前探活只能降低黑洞风险，不能替代operation_id恢复或保证零未知写入。主通道随运行沙箱保持，不为追求空闲释放频繁重新认证。
- 清理失败允许重新核实和重试；确认原进程退出才解除binding封锁。不能缓存同一失败清理task永久拒绝recover，也不能无条件放开仍存活的旧通道。
- 请求预检、响应编码和帧预算采用一致的可逆路径编码。展示文本与机器路径分开；单项发送前编码/预算错误不得拖垮其他请求，半帧/管道故障不得伪装为单项可恢复错误。
- 事件循环上的touch、仓储句柄取得及同步数据库/文件工作完整移入现有worker边界。必要提交身份、回执、终态和归档offset仍保持持久保存及取消排空。先不把last_used改成易失内存事实。
- 两条用户销毁入口均在应用边界要求明确confirm_destroy；HTTP、SessionAPI及EnvironmentAPI不得靠UI确认。原会话绑定、忙碌、归属和交付保护保留。
- ResultService失败后的诊断保存隔离secondary异常并保留原错误；必要发布/完成保存不得被当作可选诊断忽略。ToolException诊断仅记录有限安全码和类型，不保存任意工具正文。
- watcher完成删除自身登记，避免旧task回调删除同key新task；activity持久化恢复后允许清除相应uncertain，并保持并发时序和空闲回收安全。
- 有输出积压立即继续有限采集，无事实变化不重复UPDATE；保留stale恢复、错误码清除等真实状态变化。长轮询和输出journal批量提交不作为本轮正确性修复的前提。

### 10.2 用户已确认：切换会话后仍自动发送

场景：用户在A点发送，沙箱准备未完成时切换到B；A准备完成后仍自动执行原消息。不能要求用户回A再点一次发送。

发送责任必须绑定原project/session/binding、原request_id、消息及图片身份和提交设置，而不是读取当前选中会话。服务端接受后拥有“等待原环境→重新核对原会话/权限/准入→调用既有运行入口”的责任；前端负责观察、切换和明确停止。沿现有CommandReceipts和运行所有权接线，不建立通用任务队列平台。

- 同一发送请求重复到达复用原受理结果，不能重复启动；准备失败保存明确原因，不能在另一会话发送。
- 切换到B后，A完成不得抢回选中会话、覆盖B状态、清空B草稿/图片或使用B的模型和权限设置。
- A显式取消待发送、停止准备或销毁环境，须使其待发送责任终结；取消与准备完成竞争不能晚到启动。
- 等待环境与正式Agent运行分开表达，准备期间不虚构run_id或模型请求；真正执行时重新走原准入。
- 服务重启沿既有结果核对和不盲目重发合同处理；本轮不额外承诺自动恢复并重放待发送动作。

正常链：原会话身份创建→固定发送意图被服务受理→原沙箱准备→运行准入→Agent与工具→原会话结果；观察者切换不改变执行归属。失败链：准备/准入失败→原发送任务记录失败原因→不启动Agent→前端在原会话展示并保留可处理输入。

### 10.3 职责收口与保留范围

| 当前职责 | 修正后的边界 |
|---|---|
| SandboxChannel/FrameStream | 请求身份、字节流、背压、取消/迟到回复和连接活性；不裁决远端业务是否执行 |
| SandboxOperationService | 原operation_id、提交责任、最后已知远端事实、结果核对和归档；网络观察失败与业务未知分开 |
| envd | 权限/路径执行校验、实际进程/文件操作和持久日志；控制端失去观察不等于任务停止 |
| 会话提交与准备 | 固定原会话的待发送责任和准备检查点；复用既有运行准入和所有者 |
| Web | 当前会话投影、可切换导航、原请求状态及明确停止；不以全局busy托管后台发送 |

已确认可清理：SandboxTransport.avalidate、EnvironmentService.request同步包装、SandboxOperationService.file_call同步包装、SandboxSessionSetup.json_value、envd verify_target协议分支。保留afile_call/_file_call实际算法及EnvdFiles.verify_target内部安全校验。

DockerProcesses.validate由Shell/AITest权限准备经ProcessPort动态调用，必须保留。EnvironmentAPI旧方法、read_resource和EnvironmentOperations.wait仍有正式验证脚本消费者；如收口须迁移实际消费者并保留原断言，不能直接删除。G0/G1旧探针支持旧envd0.5.3的版本边界明确保留，不能把新协议探针冒充旧镜像验收。

### 10.4 暂不改变的行为与验收

输出journal仍按现有FULL持久性合同；公司真实state卷提交延迟、锁等待和停止延迟测量后，再决定WAL或合并提交。文件执行等待预算不等于撤销副作用；不靠关闭SSH或取消Python await宣称已停止fsync。14B密码/MFA独立，不更换AsyncSSH，不升级现有Docker；减少首建SSH认证在可靠性修复之后按公司分阶段成绩决定。

现有复现：`build/spec141b-review/channel_probe.py`、`sqlite_probe.py`、`followup_probe.py`及`frontend-probe.test.ts`；编码/真实attach与会话销毁复现见`build/spec141b-review-encoding/`。这些探针证实现存缺陷，不是修复回归通过。落地时迁入正式测试，覆盖排队与执行超时、迟到回复、取消、第三个并发stop、清理重试、非UTF8路径往返、慢DB/锁争用、所有用户销毁入口、secondary保存失败、watcher/uncertain收尾和A/B会话自动发送隔离。

修正完成后再跑完整后端/前端、标准控制端/envd构建、源外安装和两沙箱并发/高输出本机门禁；执行源码变动须通过独立构建清单门禁。真实配置、公司资源与正式发布均不在本次授权内。公司原机结果独立报告，不能把小IO秒级目标解释成大输出全部传完或模型回答都在一秒内完成。

### 10.5 单请求结束后的迟到回复：接收责任有界保留

用户指出的风险成立：移除单请求超时中的通道_fail后，若finally仍直接删除pending，合法迟到回执会在SandboxChannel._read被当成未知ID再次关闭健康通道。超时、取消、迟到回复必须作为同一合同实现。

1. 区分未发送、发送了部分请求帧、完整请求已发出三种状态。未发送取消/排队超时不生成墓碑；部分帧发送失败不能当成普通业务超时继续使用通道；完整请求发出后结束本地等待，须保留对应接收责任。
2. 在同一通道owner loop内，原子地将已放弃等待的ID从pending移到有界retired登记（或等价托管结构），不得有ID既不在pending也不在retired的接收窗口。登记仅存ID/原操作关联及有限状态，不保存消息正文或无限历史。
3. 迟到回复仍校验原通道绑定、generation、request_id、协议和分帧offset/大小；只有确实由该通道发出、且登记为retired的ID可以按迟到回复消费。任意未知ID、错代次/错身份、已结算后再次重复的回复，仍是协议错误。
4. 原ID的完整终结回复到达并完成处理后，才释放其retired额度。取消请求的“已收到/已受理”回执不是原请求已终结的证据，不能据此提前释放。若引入替代终结确认，必须保证该ID已排队/在途的回复帧也已结算且此后不会再有该ID数据。
5. 不因TTL到期或LRU淘汰直接忘记尚未结算的ID。业务执行可能无固定时限，时间过去不证明回复不会再到。通道完全关闭并结束原接收者后，才可以清除该通道全部登记；新通道不继承旧ID。
6. 普通已发送pending与retired共用有界接收责任额度，沿现有transport.pending_requests约束；发送前预留，超时只在两种登记间转移，不能到超时后才发现无墓碑空间。容量耗尽时拒绝尚未发出的新普通请求并给出明确容量诊断，已在途请求继续，控制/探活维持独立有界准入；不静默丢墓碑、扩容或重放操作。
7. 分帧层的部分消息数、总缓存字节和废弃响应排空同样有界；不能只限制retired字典而让迟到大响应形成无界缓存。控制帧不能被一个废弃大回复长期阻挡。
8. 墓碑只解决通道接收责任，不改变变更操作的必要保存与原operation_id恢复：未确认写入仍按原操作核对，不能把丢弃迟到正文当成操作未发生或允许重试。

必须以事件/可控时钟覆盖：A完整发出后超时、B正常完成、A迟到终结、C继续完成且通道不重建；对应的主动取消路径；取消确认先到/原回复后到；超时与回复同一调度边界；未发送取消无墓碑；错代次/未知ID/重复终结仍拒绝；额度用尽及容量释放；时间推进后更晚回复仍可排空；部分帧和大迟到回复不绕过预算。

### 10.6 连接清理：固定批次、先核实、总预算封顶

用户指出的风险成立：不能把失败task永久复用改成每次get都重启清理。清理失败状态、当前清理task和binding封锁是不同事实。

- 实施策略默认建议为：每批最多2次终止尝试（首次+一次重试），共享10秒绝对总预算；作为传输层策略显式配置，不修改用户真实设置。预算覆盖关闭输入、等待自然退出、存活核实、终止helper、回收和管道收尾，不为每一轮、每条lane、每个并发等待者重新计时。
- 同一binding同一批清理single-flight，main/bulk统一封口。并发调用只能加入本批；普通get不得因上一批失败自动开新批。预算/次数耗尽后返回env_connection_cleanup_unconfirmed，保留原资源所有权和可检查的封锁原因；没有后台无限taskkill循环。
- 每次发出终止动作前，都先通过已持有的原Process/系统进程句柄核实，给事件循环处理已到达退出通知的机会。不能仅凭taskkill退出码、进程名称或“某PID存在”判断；不得扫描后杀所有ssh进程。Windows需防止旧进程已退出后按复用PID误杀无关进程。
- 原进程已退出时，不再对它重复终止；继续核实本次拥有的子进程/进程组和管道收尾。根进程退出不自动证明整个资源树已清理；缺少归属证据、权限不足或收尾无法确认，仍返回未确认。
- taskkill非零后重新核实原对象：若此时已确认退出且其余受管资源收尾完成，接受为成功；原对象仍存活或无法核实，才在剩余额度内重试。helper自身退出/回收也受同一个总预算约束。
- 超预算时结束本批等待，不能在finally无界等待helper或重新获取中的连接。仍未结算的获取/清理资源继续保持所有权和发布封锁，不因调用方离开而丢失引用或宣布清理成功。
- 只有明确recover/再次清理动作可以开启新批有限尝试；先重新核实原资源，已退出则直接完成。失败task记录可被新批替换，但未确认封锁只能在实际清理确认后解除。重新开放前不得创建替代main/bulk通道，其他binding照常服务。

必须覆盖：进程在taskkill之前/期间自行退出；helper非零但原对象已退出；第一轮失败、第二轮确认；两轮均失败后不出现第三轮；总预算耗尽；多等待者不扩大次数/时间；只退出父进程但管道/受管子进程未确认；核实失败不猜成功；普通get不重启失败清理；显式recover可新批核实并恢复；A清理慢不阻止B正常请求。Windows原生进程树验收单独进行，本机替身测试不作原机证明。

### 10.7 远端只读搜索取消的接线

attach 在接收到 find/grep 后，先以原 request_id 调用受保护 UDS 的 request_register，登记有限 OperationControl；实际搜索带内部 registered 标记，消费同一控制对象。request_cancel 等登记完成后设置该对象的 Event，控制回执不代表原搜索已终结；原搜索响应仍沿原 ID 返回。登记不存在的 registered 搜索明确拒绝，不能重新启动。attach 结束及搜索收尾调用 request_release：置取消，删除尚未执行的登记，已执行登记由搜索 finally 删除；因此取消先到、请求后到也不会漏取消。登记和执行共用 transport.pending_requests 上限，不持久保存正文。直接诊断的未登记搜索仍有相同有界控制对象。

搜索自身的 timeout 进入工具算法；UDS 连接/发送有界，等待无时限搜索结果不套固定60秒。原数据通道的独立 transport_hello 槽、应用心跳与 SSH ServerAlive 判断连接活性；attach 在策略规定的空闲预算后退出并请求取消其已登记搜索。只读取消不用于证明变更操作已撤销。

### 10.8 待准备消息的受理接口

现有 POST sessions/{id}/runs 增加可选 wait_for_environment（默认false）。仅原会话确在准备时启用：WebService先核对原scope/session，冻结binding、会话设置及conversation_revision，规范化图片后交给既有RunDelivery producer。立即返回 {session_id,request_id,status:"waiting_environment"}；此时没有run_id。同一request_id复用原受理回执、同会话第二条新请求拒绝，受现有max_receipts及图片并发预算限制。RunAPI.start的内部可选等待参数进入原环境使用权和已有准备task，绝不重新发起创建；准备结束后核对原身份/设置、检查取消，再走现有_execute准入。

GET session增加有限pending_submission投影（request_id/status/error_code，失败或取消可返回原text供恢复输入）；真实运行仍由原latest_run_id/live观察。POST sessions/{id}/submissions/{request_id}/stop只停止该原请求，start_lock内标记取消后在锁外排空；若已开始则沿原run stop。停止准备/销毁通过activity管理封口及原PreparationProgress取消位阻止晚到准入。服务关闭先取消等待消息，再排空现有运行；HTTP断开和浏览器切换不取消。

前端创建只等待原session身份确认，不再轮询准备。保存该会话的所选设置后提交wait_for_environment请求，受理后释放全局发送状态。草稿/图片操作均按原sid进行，A的回执与后续同步不得改写当前B；准备中可导航、管理和明确取消待发送。普通3秒同步比较execution及pending_submission事实，自动发现准备完成和真实运行。


### 10.9 修正实现检查点（2026-10-29）

- pending与retired共用各组有限接收责任，不按TTL忘记未结算ID；health独立槽。业务超时不判死，部分帧/身份/EOF故障仍关闭；native SSH ServerAlive使用传输策略。Find/Grep的本地RunControl在现有poll预算内取消请求，原ID迟到回执排空。
- 搜索注册现在由保持打开的受保护UDS连接拥有，而非仅靠两个RPC的先后。原EnvdRequestHandler在ACK写失败或UDS EOF的finally释放登记/设置原control，因此attach异常退出同样能取消；执行搜索等待不套固定RPC期限。正式租约测试和真实socketpair独立复核均通过。
- Stop正常/取消托管回执都先必要保存再传播结果/取消；后续poll失败保留stopped与输出未收完事实。提交托管返回明确拒绝时保存known failed，不重放。临时状态观察失败仅标stale，watcher有界退避继续；有积压不先睡。touch在worker，旧完成不清除较新失败，已存时间单调不回退。
- 等待job内部预留精确run身份仅用于事件关联，准入之前不对外展示run_id。冻结模型/工具/权限/思考设置在最终runtime锁内再次比较。等待图片借既有pending_images保存queued/run_id=None，禁止删除或重复选入；最终准入同锁验证并消费，取消/失败转retained，既有认证图片下载/恢复入口可用。start_receipt先于旧pending ID校验，重复请求仍返回原受理结果。
- 测试替身迁移只增加close(*,deadline=None)接口；保留业务、顺序、身份和输出断言。旧watcher“遇stale退出”测试按新合同迁为首次stale、后续恢复终态，再次登记可用；旧前端准备轮询测试迁为服务端受理与普通状态发现。未skip失败或放宽断言。

以下为2026-10-29的历史检查点，现已由第10.10节最终结果覆盖。前端563通过与TS/Vite构建通过；真实浏览器夹具确认准备中管理可用、A/B草稿隔离、取消及恢复原输入。标准控制端wheel构建、隔离源外安装、compileall/pip check/diff检查通过。后端全量预检5943通过、40失败；6项连接替身接口已修正并通过18项针对回归，34项是未获授权更新的envd清单门禁。最终全量、envd wheel及候选镜像/本机SSH双沙箱验收尚未完成，不引用第9节旧候选替代。本次尚未修改release/envd.json，候选清单位于build/spec141b-reliability/envd.manifest.proposed.json。公司Windows/Docker19.03/两跳与真实模型证据仍独立。


### 10.10 最终本机验收（2026-10-30）

用户“可以继续”授权后，仅将`release/envd.json`的执行源码哈希同步为`7447998cbafe4db2d956117f0c370b58be7ff1bf85f5014711372480d3f4b5c2`；独立envd版本仍为候选0.5.5，65份源码清单及依赖不变，真实运行设置不变。此前34项哈希门禁拒绝已解除。最终报告：[verification.json](../../build/spec141b-reliability/verification.json)。

| 验证 | 实际结果 |
|---|---|
| 后端完整两片 | 3098 + 2887 = **5985通过**；JUnit与当前collect逐节点一致，无遗漏、重复、失败或skip |
| envd产物门禁专项 | **39通过**；唯一warning来自刻意构造重复ZIP元数据的拒绝用例 |
| 前端与构建 | **69文件/563通过**；TypeScript及Vite构建退出0（原有bundle体积提示仍在） |
| 标准产物 | Mac ARM64控制端与Linux ARM64 envd wheel构建、Twine、控制端源外安装通过；373份控制源、65份执行源和4份Web内容与当前源码/构建逐字节一致 |
| 镜像 | `naiyue-envd:spec141b-reliability-20261030`，包含AITest Kit **0.6.0**；安装后65源哈希及pip check通过 |
| 真实文件/隔离 | Read/Write/Edit/Ls/Find/Grep、权限过滤、图片读取、分卷、network none/冻结external策略、停启/重开/销毁拒绝回退全部通过 |
| 真实故障注入 | 非UTF8 Skills路径往返且沿用原通道；实际fd运行后取消，原通道继续；SIGKILL attach后UDS租约取消fd，A可重连且B仍正常 |
| UI及待发送 | 真实浏览器夹具确认管理可用、A/B切换、B草稿保护、取消与恢复；正式认证HTTP回归确认原会话自动准入、取消/销毁/设置变化不晚到启动，图片/重复请求合同保留 |

使用当前生产入口的双沙箱验收（671文件、Agents/Claude各10个Skills、0供应商请求）：

| 场景 | 本机Docker | 本机原生OpenSSH |
|---|---:|---:|
| 两个独立会话首次准备 | 2.327 / 1.969 秒 | 1.822 / 1.808 秒 |
| 完整Read p95（含准备目标/执行） | 2.716 ms | 2.778 ms |
| 20个Skills准备 p95 | 7.338 ms | 7.533 ms |
| 两沙箱并发小读取 p95 | 41.447 ms | 44.179 ms |
| 采集200 MiB期间小读取 p95 | 20.633 ms | 19.019 ms |
| 停止任务 | 54 ms | 57 ms |

高输出两侧均完整归档209715200字节。A停止再启动/重连期间B继续正常。另一个真实镜像故障探针在30000文件中先确认原生fd已启动并暂停，再发取消/强杀attach，避免用极紧sleep冒充确定性控制：取消到进程退出并恢复读取142ms，attach强杀后搜索退出及A重连311ms；这些为本机观测值，非公司SLO。工作负载UID1000，不能读写/state；同UID控制状态问题沿141A的权限边界保持。

复跑入口（输出目录内保留完整命令和日志；源码开发需将仓库已有fd/rg资产目录加入PATH）：

```sh
PATH="$PWD/build/spec141-probe/test-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/spec141b-reliability/final-0.xml
PATH="$PWD/build/spec141-probe/test-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index 1 --count 2 --output build/spec141b-reliability/final-1.xml
(cd frontend && npm test -- --run && npm run build)
.venv/bin/python -m scripts.verify_sandbox_persistent --context colima-nyt-r1-10 --image naiyue-envd:spec141b-reliability-20261030 --output build/spec141b-reliability/persistent-local-final.json
.venv/bin/python -m scripts.verify_sandbox_environment --context colima-nyt-r1-10 --image naiyue-envd:spec141b-reliability-20261030 --output build/spec141b-reliability/environment-final.json
```

原生SSH使用本机专用VM的已核对公钥、独立临时配置和`ControlMaster no`；VM重启会改端口/密钥，须重新从本机Colima控制核对，不能直接复用历史端口。完整参数见`persistent-ssh-final.txt`对应报告和本目录临时ssh配置。故障探针为`build/spec141b-reliability/runtime-fault-gate.py`，仅作用于它新建的临时注册库/容器/卷；不是外部只读探针，也不会操作公司资源。

当前证据为本机Linux ARM64/Docker29.5.2/本机原生SSH；公司Windows、amd64、Docker19.03、两跳/VPN和真实模型业务效果仍需原机验收。第10.4节取舍保持：journal FULL，不提前切WAL/批量提交；密码/MFA和主机级SSH复用独立。未提交、打标签、发布或部署；旧0.5.3镜像不能提供本轮attach/权限合同，需要配套新候选镜像。临时容器/卷已清理，专用VM恢复Stopped，默认context仍为default，已有镜像/真实配置/公司资源保持。


### 10.11 公司平台候选制作范围（2026-10-30）

用户“继续吧”授权制作同源公司平台候选。沿第10.10节已通过的源码与前端资产，用现有scripts.build_release生成Windows x64控制端、scripts.build_envd生成Linux amd64执行包，再经scripts.build_sandbox_image生成含AITest Kit 0.6.0的新独立标签；版本保持候选0.5.5，不冒充已发布的0.5.5。产物存入build/spec141b-company-20261030/，保存输入源码快照/产物SHA及平台检查。仅启动原有专用本机amd64构建VM，不修改真实设置、现有公司Docker及任何公司资源，不提交/打标签/发布。

复用现有company-kit的归档/compatibility/check.sh方式并按本候选身份收紧校验；离线tar必须单平台、含Docker legacy manifest、层编码兼容19.03。新标签不存在才构建，导出后用另一个候选标签重导入，核对配置/层身份并运行同一检查器；不通过删除原镜像验证导入。检查器先验证包/架构/镜像身份，再仅启动受限临时容器；实际envd权限/文件/取消/SSH仍需独立的完整验收，兼容性小探针不冒充最终业务证明。

Windows wheel可在本机构建并检查平台资产/源码/依赖，但本机没有Windows执行环境，Windows实际安装、两跳链路和公司Docker19.03必须在目标机验收。给出保留models/settings/数据、先正常停旧服务、显式本地wheel安装及专用新会话步骤；本轮不自动改用户环境配置，不迁移旧卷。


### 10.12 公司平台候选本机制品验收结果（2026-10-30）

[候选报告](../../build/spec141b-company-20261030/verification.json)。产品源码、4份前端资产、依赖及构建输入共382项与第10.11节快照一致；未改真实配置或正式版本。Windows x64控制端和Linux amd64 envd标准wheel构建/Twine通过，控制端application_sha256与373份控制源相同、执行包execution_sha256与65份执行源相同。本轮产物回归78通过，唯一warning为刻意重复ZIP元数据；同源码上轮完整后端5985/前端563结果仍独立保存，本轮未重复全量。

两份最终交付：

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| Windows x64候选ZIP | 4243134 | 1cda8f956ed7a9a37b92d0ce7365f43a7f9caaf8a8d8a86a93ab8d413adf8b6a |
| Linux amd64离线镜像ZIP | 136056669 | 6c6c31024609d5fe13c7f1bd78b690a8999f17360d51fea2b78a75e8bcdaba59 |

文件位于build/spec141b-company-20261030/。新镜像标签naiyue-envd:spec141b-company-20261030-amd64，含envd候选0.5.5、AITest Kit 0.6.0；版本号不足以区分公开旧0.5.5，Windows包内verify-install.py按实际安装源码/Web/原生资产逐项校验。Windows包只含候选wheel及验收工具/说明，不含Python或完整依赖wheelhouse；需原0.5.5环境依赖齐全，显式本地wheel安装不改依赖。

离线tar单平台linux/amd64、含legacy manifest、11层全gzip；每层解压摘要与config.rootfs.diff_ids一致。为避免覆盖原tag，以另一个候选tag重导入经典manifest/config/layers，重新保存后确认config原字节SHA和全部层身份一致；containerd在重导入后生成另一平台manifest ID，因此check.sh只接受清单记录并已核对的构建manifest、重导入manifest与config身份，不接受任意tag替换。默认镜像、重导入镜像、[Moby v19.03.9官方seccomp规则](https://raw.githubusercontent.com/moby/moby/v19.03.9/profiles/seccomp/default.json)下同一compatibility.py均7项通过；规则来源及SHA保存seccomp-source.json。这些检查不证明公司daemon/内核已经通过。两个ZIP CRC/逐项内容SHA与staging一致。

随Windows包交付的独立快速门禁复用当前项目验收主链，保留671文件、20Skills、两个独立沙箱，输出缩为8MiB；实际在本机amd64 QEMU通过，完整归档8388608字节，A停启/重连时B正常，0供应商请求。暖Read p95 21.550ms、20Skills p95 98.971ms、停止78ms；QEMU冷准备26.582/16.945秒，不作为公司原生x86_64性能结论。此前公司吞吐约12Mbit/s时200MiB仅传输即可能超过两分钟，因此快速交互和大输出压力分开验收，原200MiB压力门禁保留。

临时容器/卷回到0，既有镜像tag保留；标准构建pull更新的本机python基础tag已恢复到快照身份。VM临时交付目录已移除，专用amd64 VM恢复Stopped，默认context仍default。未提交/发布/公司部署。**唯一下一步：用户先在公司Linux核对ZIP/原daemon/新tag、导入并跑check.sh；7项通过后，在Windows安装核对候选并运行独立双沙箱快速门禁，再用专用新会话验证UI和真实业务。公司任何失败先保留阶段与原始报告，不升级Docker或放宽安全规则。**
