# Spec141B：常驻沙箱连接、异步数据面与可中断交互

状态：2026-10-29 已完成本机实施与验收；承接已完成本机验证的 [141A](141a-envd-identity-boundary.md)。本片不发布、不部署、不升级公司 Docker，不修改真实用户配置。

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

唯一下一步：以本候选合同安排公司原机验收，取得真实链路/磁盘/Windows子进程回收和完整业务延迟后再决定正式版本发布。
