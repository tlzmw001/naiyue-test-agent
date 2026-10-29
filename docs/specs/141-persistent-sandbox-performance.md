# Spec141：现有 Docker 上的常驻沙箱通道与性能预算

日期：2026-10-28。状态：**G0 工具与 G1 部分实测已完成；G1 因同 UID 安全失败及并发 IO 故障未通过。完整产品迁移未批准。** 设计、调用计数及实测边界以下文记录为准。

用户当前只能使用原开发机和 Docker 19.03 厂商版本，机器存在疑似 Kubernetes 容器。沿用 Docker 容器隔离与 envd，参考 OpenSandbox 的职责和接口粒度，不部署其完整平台。此前 [Spec140](140-opensandbox-migration.md) 保留为另一部署条件下的候选；本文是当前方向的性能设计依据。

## 1. 范围、约束与外部合同

- 不升级/重启 Docker，不改节点网络、kubelet、containerd，不加第二套 daemon，不操作其他业务容器。
- 控制端继续 Python + LangChain create_agent；Windows 原生 OpenSSH、已有 Host 别名、跳板机、严格主机校验保持。首版认证沿用当前 key/agent 合同，不把密码/MFA 支持包含在性能承诺中。
- 复用现有文件权限、审批 subject、路径/对象身份校验、操作日志、输出归档、Git 输入/结果/交付算法。性能优化不能绕过这些职责。
- 不新增远端宿主服务或 TCP 监听，不引入 AsyncSSH、Go runtime、OpenSandbox SDK；不实现集群调度、预热池、通用 RPC 平台或任意远程主机命令入口。
- 需要新版本项目执行镜像，继续包含 AITest Kit 0.6.0。镜像构建、部署和发行在之后的独立验收范围内；本轮没有改 release 清单或真实配置。
- 公开模型工具名称、参数、结果/审批语义保持。内部 FilePort/ProcessPort 调用迁移为原生异步；环境协议及能力握手需演进，不能把 Python 内部可 import 函数当成长期外部兼容合同。
- 本片超过 8 个文件，涉及控制端、envd、前端与镜像。减少运行时层次不等于改动面很小。

## 2. 已验证事实：开销在哪里

项目基线 `3759fea2e7eb0b0566e2f14fb6ed1af306ab4a82`。

普通只读调用：`DockerFileSystem._request → EnvironmentService.request → validate → DockerEnvironment.validate → DockerCLI`，之后 `DockerEnvironment.request` 再 validate，再启动一次 request 桥接程序。

SSH 模式一次完整 validate：daemon info 1 次；container ls/inspect 2 次；两个 volume 各 ls/inspect 2 次，共 7 条远端命令。一次普通只读 RPC 为 `7 + 7 + 1 = 15` 条。操作协调器的 `_rpc` 只经过一次完整核对，是 8 条，不能将所有路径都算成 15 条。

使用真实生产函数和合成 Docker 响应统计普通 RPC，使用临时目录中的两组各 10 个合法 Skill 调用真实发现函数，结果：

| 项目 | 实际调用计数 |
|---|---:|
| 普通只读资源 RPC 的 Docker 命令 | 15 |
| 20 个 Skill 的 resource_info | 64 |
| resolve_resource | 42 |
| list_resources | 2 |
| read_resource | 20 |
| 资源 RPC 合计 | 128 |
| 额外环境 validate | 1 |
| 按当前 SSH 路径折算远端命令 | 128 × 15 + 7 = 1,927 |

计数探针：`build/sandbox-transport-review-20261028/count_current_calls.py`；输出 `current-call-counts.json`。执行命令 `.venv/bin/python build/sandbox-transport-review-20261028/count_current_calls.py`，本轮 exit 0。未执行 SSH、Docker 或模型调用。临时 Skill 是明确的合成夹具，不是用户项目或线上计时证据。

用户现场已有的 1.67—1.81 秒是一次 SSH Docker info 的总时间，不是持久连接 RTT，也不代表每一种 Docker 命令耗时相同。容器内 hello/resource_info 约 0.001 秒也不代表 Git、搜索和全部业务执行时间。

准备另有独立开销：本地 Git 快照、Docker 创建/卷核对、输入 bundle 编码和上传、容器内 Git 校验/安装。当前 input_chunk 最大 1 MiB，每块确认后再发下一块，远端每块 flush/fsync；计入传输预算，不能只计算带宽。

命令输出另有独立放大：refresh 先 status，再最多 8 次 operation_output，每次均重复 Docker 核对。轮询参数 100 ms 不代表实际每 100 ms 获得结果。

## 3. 开源代码参照：采纳哪些机制

OpenSandbox 固定 `release-1.2.0` 所解析提交 `62a64f0b48cdd6b410f36e53c74dc7559c87719a`，不把浮动主分支文档当成本项目已实现事实。

| 源码及真实调用链 | 采用的机制 | 本项目保留的边界 |
|---|---|---|
| `FilesystemAdapter.read_file → read_bytes → adapter-owned AsyncClient.get → execd /files` | 复用客户端连接；普通文件访问不反复走生命周期核对 | 经原生 SSH 字节流到 UDS，不依赖容器网络 HTTP |
| `write_files → 一次 multipart upload`、`get_file_info(paths)` | 以业务操作批量获取文件信息，减少串行往返 | 非通用任意 batch；批量资源读取逐项校验、逐项结果 |
| `CommandAdapter.run → _execute_streaming_request → RunCommand → codeRunner.Execute` | 命令有独立身份；输出增量到达；前后台、查询、停止有明确接口 | 继续使用已有 operation_id/journal/归档，不另建第二套作业系统 |
| `get_background_command_logs(cursor) → SeekBackgroundCommandOutput` | 游标增量输出，避免重复下载完整日志 | 继续校验 offset、gap、终局输出完整性 |
| 普通请求 client 与 SSE client 分开；SSE POST 重试限制 | 长任务与连接读超时分开；写入不能盲目重发 | 明确任务 deadline、传输 deadline、停止确认；unknown 查询原 ID |
| `RunManagedCommand` 的取消/完成竞态处理 | 停止要等待并确认实际退出，不能仅发信号就报告已停止 | 保留本项目进程组与未确认状态合同 |

源码核对还发现 `RunCommand` 尾部有可配置 grace sleep，所查版本默认 200 ms；前台 Python 客户端读取到 EOF。不能看到“流式/常驻”就假设零固定开销，也不照搬该尾等待。上游行为有其上下文，本项目应独立测量。

参考链接：

- [OpenSandbox CommandAdapter](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/sdks/sandbox/python/src/opensandbox/adapters/command_adapter.py)
- [FilesystemAdapter](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/sdks/sandbox/python/src/opensandbox/adapters/filesystem_adapter.py)
- [RunCommand 与输出查询](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/web/controller/command.go)
- [受控命令取消](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/runtime/managed_command.go)
- [grace 默认值](https://github.com/opensandbox-group/OpenSandbox/blob/62a64f0b48cdd6b410f36e53c74dc7559c87719a/components/execd/pkg/flag/parser.go)

Pi 本地 `a69bef789bc95abf0acee16f7b4660b70b650bb9`：`createBashToolDefinition → ops.exec(onData, signal, timeout) → 完成输出/退出状态`，说明工具与执行 IO 可分开；本地实现仍 spawn 命令，不是持久 SSH 证明。AgentX 本地 `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`：`ContainerAppService.executeContainerCommand → canExecuteCommands → DockerService.executeCommand → execCreate/execStart → awaitCompletion`，聚合到最后返回；参考职责边界，不照搬阻塞等待或每条命令 docker exec。

本项目固定 LangChain 1.4.2 / core 1.6.6。已核对本地 `StructuredTool.ainvoke`：没有 coroutine 时仍回退 `run_in_executor`。因此只把最底层 SSH 改 async，保留同步工具包装，不能宣称已消除线程池等待。官方工具合同见 [LangChain Tools](https://docs.langchain.com/oss/python/langchain/tools)。本片不改模型—工具循环。

## 4. 建议架构：复用四项职责

```text
已有 EnvironmentService / OperationCoordinator
  ├─ 管理入口：绑定预留、创建/恢复/销毁、完整核对
  │    └─ 固定管理流程经独立 SSH 执行；不等待普通数据请求排空才受理取消
  └─ 按 binding/container/代次持有一个异步数据会话
       └─ 常驻原生 OpenSSH → 常驻 docker exec attach
            └─ 有界分发到已有 envd UDS
                 └─ 现有文件算法 / process worker / journal / Git 算法
```

### 4.1 连接与协议

- `EnvironmentService` 持有数据会话，不能像当前 `_engine()` 那样每次重新构造丢失连接状态。复用键包含 binding、完整 container ID、配置快照和 envd 启动代次；相同 Host 别名不等于同一沙箱身份。
- 一条常驻交互通道复用现有 JSONL、request_id、environment、session_id。新增 attach 入口保持 stdin/stdout，stdout 仅协议，stderr 独立消费并限制日志量。
- 帧读者按 request_id 完成对应 await；写者只保护一帧写入，不持锁等待响应。乱序响应允许，ID 重复、超限、错身份使通道失效，拒绝继续拼接不可信响应。
- attach 仅作转发和有界并发，不持久化业务状态，不执行模型命令。初版可对每个请求连接已有 envd UDS，省去 SSH/docker/Python 重建；是否进一步复用本地 UDS 由 G1 实测决定，不提前重写整个 envd 为 asyncio。
- 交互并发候选为 8 个普通 IO、2 个控制名额；大块传输最多 1 路。数值为原型预算，实施时进入正式配置及校验，不成为硬编码公网端口或隐藏策略。RPC 并发不等于 CPU 任务并发，已有 max_active_operations=16 也不保证 2 CPU 能同时快速运行 16 个计算任务。
- 大文件/镜像读取/大写入/Git bundle 使用按需独立通道，不能让一个多 MiB 帧挡住停止或小文件查询。复用同一协议和实现，不另造传输平台；空闲时不保持额外连接。初次准备尚无普通工具 IO，可直接在建好的主通道上传，减少一次 SSH 建连。
- 独立通道仍共享网络带宽；必须限制输出/上传速率、单帧和队列大小。交互帧候选上限 256 KiB（按编码后字节计），业务大消息按操作及预计响应上限路由到大块通道。不能为了帧上限偷偷降低现有 Write/Edit/图像读取能力。
- 对任务排队和发送设置 deadline。取消本地 await 不等于取消远端任务；关闭数据连接也不等于任务停止。

### 4.2 核对与管理

完整 daemon/container/volume/执行策略核对放在建会话、重连、生命周期变化和异常恢复。每次请求继续核对本地绑定、会话代次、审批 subject 和远端身份；不会删除文件对象的执行时校验。

低频后台完整核对可以保留，但不把其结果当作权限保证。应明确：租约期间的宿主管理员恶意修改不在容器控制进程可防御的范围内；生命周期变更和新代次禁止沿用旧租约。

准备阶段将固定管理流程聚合成一次 SSH 往返，远端本地调用现有 Docker CLI；不要求远端额外安装 Python/SDK。允许的宿主动作固定为本项目的 info/inspect/带归属标签的创建、启动及管理；生成参数按已有校验与 shell 引用，不接收模型任意命令。本地先持久预留确定的名称/标签，远端逐阶段回报；失败保留已创建资源的可查身份，不自动回滚删除。

把完整核对挪走、批量创建及减少重复 inspect 均须通过确定性计数和身份替换测试；不得拿 TTL 缓存替代身份核验。若 G1 显示远端 Docker create/inspect 本身就慢，常驻 SSH 无法消除该部分。

### 4.3 减少业务往返

| 真实消费者 | 当前行为 | 建议合同 |
|---|---|---|
| Read/Ls/Write/Edit/Find/Grep | prepare_target、verify_target、执行分别请求 | 审批前 prepare_target 保留；审批后的 verify 与执行在 envd 同一次操作中完成 |
| AGENTS/CLAUDE/Skills 准备 | 每个文件/目录多个逐项 RPC | 一次有界资源清单 + 按消息预算批量取正文；20 个常规 Skill 的预算为 2—3 个依赖往返 |
| 正常进程 refresh | status 与多个 output 请求分开 | 原 ID + output cursor 一次返回状态和最多一个有界输出块；有更多输出再继续读取 |
| 无输出的运行任务 | 控制端不断完整请求 | 有界等待“状态/输出变化”；保留 100 ms 的候选最大合并等待，终局立即返回，不额外 sleep |
| 输入 bundle | 每块新 SSH、反复核对，再写入确认 | 同一常驻流连续传输，初版保留 1 MiB chunk、offset/digest/fsync；先测出吞吐，不预先增加乱序窗口和重组逻辑 |

资源批量接口返回规范路径、对象身份、有限正文与逐项错误；YAML 解析、同名优先级、禁用规则仍由现有应用代码负责。envd 只负责 IO 与安全检查，不复刻第二套 Skills 规则。超过数量/字节预算应分页或明确诊断，不能伪装为完整扫描。

初版不依赖跨轮缓存来达标，避免工作负载改文件后使用旧指令。后续缓存必须证明失效机制，不能只信可能被工作负载修改的 mtime。

### 4.4 正常与失败链

正常 Read：工具审批准备产生 subject → 用户/权限机制授权 → 异步文件口携带 subject → 已建立会话发送 request_id → envd 执行时核验对象并读取 → 返回同 ID → 现有工具结果交给模型。此时不访问 Docker 管理接口。

写入丢回执：控制端先记录 operation_id/指纹 → envd 预留、执行、保存终局 → 连接断开 → 控制端将结果标为待核对 → 重建会话并核对身份 → 查询原 operation_id。查不到或远端记录不可确认就保持 unknown，禁止自动再次 Write/Edit/Shell。

取消：前端本地立即显示“正在停止” → 后端关闭该 run 的新提交准入 → 控制名额发送 stop → envd 停进程组并确认 → 保存结果 → 前端显示已停止。数据通道失效时，独立管理通道仍可在确认范围内停止整个沙箱；不能把整容器停止静默当成只取消单任务。

销毁：先受理独立管理操作并展示阶段；协调停止/未确认结果和已有交付保护，再按精确归属身份删除。run 的长期 activity.use 不能继续将管理入口整体拒绝；锁只保护短状态转换，跨 IO 使用预留状态。前端不因 run busy 禁用所有查看、停止和管理按钮。

### 4.5 安全与执行能力是前置条件

- envd、socket、恢复日志与工作负载分属受保护身份；工作负载不能读写 /state、伪造控制请求、继承控制 fd、信号干扰控制进程或修改控制程序。
- 工作负载降权用可靠进程创建机制，不能在多线程 envd 中通过修改进程全局 UID 或不安全的 preexec_fn 解决。envd 保留能力的最小集合与文件操作执行身份由 G1 Linux 探针证明；不得仅测 chmod 就通过。
- 保持只读根文件系统、资源上限、no-new-privileges、现有网络模式；不为兼容开启 privileged、host network 或挂载宿主 Docker socket 给工作负载。
- hello 返回实际能力和执行端版本，按所选 profile 要求验证 Bash、Python、Git、AITest。Linux 不默认要求 PowerShell；如 profile 依赖某解释器，准备时明确失败，不能先展示可用再让模型发现缺失。
- 能力探测必须使用真实任务将采用的 executable、PATH 和权限。当前“Shell interpreter unavailable”的现场根因尚未获得实机修复证明，连接性能改造不能自动视为修复该问题。

## 5. 性能模型与预算

以下是**设计估算，不是测试通过结果，也不把单项 p95 简单相加冒充端到端 p95**。

记：H=SSH+docker attach 建连成本；R=持久应用请求 RTT；L=本地/远端业务处理；Q=排队；W=编码后传输字节；U=有效字节吞吐；N=依赖链上的往返数。

`T_warm ≈ N×R + L + Q + W/U`。同一批可并发请求按依赖波次计算，不能简单拿总数除以并发度；写操作和 Git 安装仍有必要的顺序。

仅保持连接、不合并资源 RPC：20 个 Skill 的 128 次串行请求，在 R=100 ms 时网络下限就是 12.8 秒，尚未计磁盘和解析。因此该最小方案不满足目标。

### 5.1 已运行沙箱的敏感性估算

例子假设 U=20 Mbit/s=2,500,000 byte/s；小 Read 含审批准备共 2 个往返、64 KiB wire bytes、L+Q=80 ms；20 Skills 批量准备按 3 个往返、128 KiB wire bytes、L+Q=250 ms。人等审批的时间不计。

| 应用 RTT R | 小 Read 估算 | 20 Skills 资源准备估算 |
|---|---:|---:|
| 20 ms | 0.146 s | 0.362 s |
| 50 ms | 0.206 s | 0.452 s |
| 100 ms | 0.306 s | 0.602 s |
| 200 ms | 0.506 s | 0.902 s |
| 500 ms | 1.106 s | 1.802 s |

这些数值不能承诺为公司 p95。R 必须经两跳后的同一应用通道测量；ping、容器内 1 ms、SSH info 的 1.8 秒都不能替代它。大正文会增加 W；一次查询多个大文件时需按真实分页数重算 N。

### 5.2 首次准备的预算

测量夹具 F1：671 个文件、其中 20 个合法 Skill 每个不超过 4 KiB；真实 bundle 不超过 8 MiB；镜像已导入，2 CPU/2 GiB profile；不含镜像拉取、人工审批、模型响应。保留原输入安全检查。

本地快照 S 和远端管理 M 只在已预留绑定且可分别取消/核对时并行；否则预算必须相加。

`T_cold ≈ max(S, H_management + D) + H_attach + (4/3×B)/U + K×(R+F) + 2R + G + C + P`

其中 D=远端 Docker 创建/核对，B=原 bundle 字节，K=1 MiB 分块数，F=每块远端落盘确认，G=Git 安装，C=资源准备，P=控制端收尾。base64 的 4/3 膨胀必须计入。

| 阶段 | 示例预算/假设 |
|---|---:|
| 本地快照 S | 2.0 s，与远端管理并行 |
| SSH 管理 H_management + Docker D | 2.0 + 2.0 = 4.0 s |
| attach 建连/握手 H_attach | 2.0 s |
| 8 MiB bundle，20 Mbit/s，含 base64 | 4.474 s |
| 8 块串行确认，R=100 ms、F=20 ms | 0.960 s |
| begin/cache 的额外两个往返 | 0.200 s |
| Git 安装 G | 1.500 s |
| 资源准备 C | 0.700 s |
| 控制端收尾 P | 0.200 s |
| 总计 | **约 14.0 s** |

这已经推翻“无条件首建 10 秒”的承诺：8 MiB/20 Mbit/s 的预算余量很少。F1 首建目标可设 p95≤15 s，但只有 G0/G1 的真实分段数据支持时才进入完整实施；预估要留余量，不能拿该表直接宣称目标已可保证。若吞吐仅 5 Mbit/s，仅 base64 bundle 传输就约 17.9 s；如果本地 Git 快照要 30 s，整个首建至少 30 s，换传输无效。

已有沙箱后续轮次不应重新建容器或默认重复安装输入；只刷新当前工作区资源和必要身份事实。该路径是日常秒级体验的主要验收对象。

### 5.3 拟定产品验收目标

| 场景 | 目标及计时边界 |
|---|---|
| 用户点发送/取消/管理 | 本地反馈≤200 ms；后端受理 p95≤1 s，不能等任务结束才回应 |
| 运行沙箱的小 Read | p95≤500 ms，含两次机器往返，不含人等审批 |
| 20 Skills + 项目指令 | p95≤2 s，每轮真实校验，不依赖命中缓存 |
| 已运行会话发送到发出模型请求 | p95≤3 s；不包含供应商响应与模型推理时间 |
| 简单命令 | 启动回执 p95≤500 ms；已有输出的额外可见延迟 p95≤500 ms；脚本本身用时单独计 |
| F1 首次准备 | 暂定 p95≤15 s，按上节条件；首建 10 s 仅作为优化目标 |
| 高输出/8 个并发 IO 时取消 | 受理≤1 s，协作退出≤3 s；不协作按现有停止宽限并显示升级阶段 |

1/8 并发分别测量；更大工作区、10 倍 Skill、多个会话需单列，不把一个样本的成绩外推为任意规模保证。吞吐/RTT不满足预算时显示具体限制，不靠提高超时或隐藏阶段获得“通过”。

## 6. 先验证后改造：四个闸门

### G0：原机只读基线与性能下限

在用户已有目标容器内做受限 hello/小文件/有限数据读回探针，使用实际 Windows SSH 别名与跳板机。测量 20 次建连、至少 200 次持久应用往返、8 MiB 有界读写吞吐（写入需专属测试资源）、控制端本地快照耗时。报告 p50/p95/max、失败率、样本数、cold/warm 分类；20 次建连的 p95 仅筛查，不能当稳定生产统计。

先确认节点归属和允许资源预算。只读部分不创建/删除原容器；持久通道实验可先使用容器内一次性、固定功能的 Python 程序验证管道与 RTT，不安装服务。涉及写入和故障实验仅在获准的本项目一次性容器中进行。

G0 预筛：应用 RTT p95≤100 ms、有效吞吐≥20 Mbit/s、每次建连 p95≤2 s、F1 本地快照≤2 s。任一超预算就重新代入模型；如果目标已不可能满足，先说明并调整方案，停止完整迁移。阈值是本设计的预算，不是对公司环境的已知事实。

### G1：最小真实垂直原型

只做正式方案所需的最小 attach、请求分发、一个文件读和一个有界命令/停止；用现有 envd/journal 路径，不以 echo 服务代表完成。加上固定管理批处理和 F1 输入安装分段计时，以免只证明读取快。

必须验证：普通 IO 与控制请求并发、200 MiB 有界输出/大传输下的小读取、慢读端背压、连接断开、进程结束与取消竞态、工作负载无法访问控制状态。Linux 权限隔离与 Bash/AITest 有效配置在这里先验，不拖到最后验收。

G1 不通过就修改方案，不推进完整 UI/全工具迁移。测得的阶段上界代回首建模型，检查余量；不得把本地 loopback 成绩作为公司链路结果。

### G2：按链路迁移并持续预算检查

先控制端与 envd 通道，再文件/资源，再进程输出和停止，再准备/输入，再前端。每一片同时覆盖生产、测试、序列化、配置与当前文档，删除已替代且无实际消费者的旧内部包装。

每片 CI 加确定性的请求数/进程启动数断言；时间测试用可控时钟/延迟注入和事件同步，不靠紧 sleep。模拟 R=20/100/200 ms、吞吐上限和失败，检查 N、Q、buffer 上限，不把模拟数字当现场性能。

在途写入没有结果不得重发；输出成功但诊断失败不得改为执行失败；身份错配拒绝；缓存失效/目录变更、超大帧、孤儿子进程、并发销毁都保留明确断言。

### G3：完整产品与原机验收

后端全套、前端、标准控制端与 envd 构建、Linux 新镜像权限/能力验证通过后，在同一发行候选上复验真实公司链路。使用 F1 和用户真实工作区分别测量，至少 200 次热操作、分批冷创建测试；冷创建数量按共享机器获准预算控制，报告样本限制。

验证发送到模型请求、第一段工具输出、取消反馈、最终状态、销毁完整闭环。记录控制端 queue/send/receive/render 与远端 dispatch/start/complete 的各自单调时钟耗时；不直接相减未同步的跨机器绝对时间。

## 7. 实施影响及回退边界

| 文件/模块 | 必要修改 |
|---|---|
| `execution/docker_cli.py`、`docker_environment.py` | 管理流程聚合；完整核对移到连接边界；日常请求不再走一次性 DockerCLI |
| 新 `execution/sandbox_channel.py` | 唯一会话所有者、异步收发、有界 pending、断线与关闭 |
| `envd/__main__.py`、`protocol.py`、新 `envd/attach.py` | 常驻桥接、协商/代次、有限请求分发与协议错误 |
| `envd/dispatch.py`、`files.py`、资源 IO 小模块 | 执行前校验合并、有界资源批量读取，复用原安全算法 |
| `application/environments/service.py`、`activity.py`、`operations.py`、`inputs.py` | 生命周期所有权、取消准入、原 ID 恢复、状态与输出合并、异步输入 |
| `execution/docker_files.py`、`docker_processes.py`、文件工具/权限入口、会话资源准备 | 异步接线，消除网络等待使用共享线程池的路径；保留真正需要的本机阻塞 IO |
| `envd/processes.py`、进程创建/镜像文件 | 受控降权、解释器能力验证、日志/socket/Git 状态保护 |
| 前端沙箱管理与会话状态、服务管理接口 | 阶段进度、受理反馈、独立取消/销毁协调 |
| `docs/lock-hierarchy.md`、测试、构建/验收脚本 | 新锁与准入关系、请求数门禁、故障和性能证据 |

不立即引入多后端插件注册层、通用事件总线或第二份持久 operation 表。当前真实消费者只有本机 Docker 与 SSH Docker，两者共用同一字节协议。

传输升级不能直接沿用不支持 attach 的旧 envd 镜像。新控制端明确检测协议/能力，不静默回到慢路径。旧会话结果先完成核对/取回，不自动销毁/迁移已有卷；兼容读取旧操作状态与新执行路径分别验收。新旧协议不兼容时先停止迁移，不修改旧记录伪装可用。

回退保留旧控制端/旧镜像及原卷，不移动不可变标签，不降级已写入的新协议状态。每片发布前给出记录兼容范围、未完成操作清单与人工处理入口；原型不接用户正式会话，所以 G0/G1 失败没有用户状态迁移成本。

## 8. 本轮证据与唯一下一步

本节为开始原型前的设计记录：当时已完成固定版本开源调用链核对、当前生产路径计数与带条件性能模型；不证明实机秒级达标或安全问题已修复。后续获准的原型与本机镜像实验见第 9—10 节，不能继续将本节当作当前实施状态。

下一步建议先实施/运行 G0/G1 的受限性能与安全原型，再根据真实数字冻结完整接口与实现范围；不先做完所有模块后才第一次测公司链路。

## 9. G0/G1 原型实施边界（2026-10-28 获准）

原型放在 `scripts/sandbox_probe/`，不接产品启动入口、不改变 envd wheel 清单或真实配置。`transport` 负责异步子进程字节通道与在途请求；`worker` 是一次性注入容器进程的标准库桥接程序；`target` 只构造固定 Docker/SSH 命令并核对显式目标；`measure` 输出连接/真实 envd RTT/并发/吞吐分项；`exercise` 只在带专属 probe 标签的测试容器执行固定文件/进程/断线测试。原型与正式后端不能同时对外提供执行入口。

`ProbeChannel.request(operation, params)` 保留现有 envd 身份和 request_id；失败终止当前通道，写入未知不自动重发。默认 worker 只允许 hello、资源查询/受限读及内存吞吐探针，拒绝 submit/stop/write。`--exercise-owned` 必须再次核对容器专属标签和项目身份，不能对用户正常会话启用。stdout 仅协议，stderr 被持续消费并限额；报告只含统计、固定错误码及版本，不含 Host 别名、主机/IP、项目路径、文件正文或环境变量值。

本机复用已有 `nyt-r1-10` Colima 测试 VM，启动时不保存配置、不激活默认 Docker context、不改 SSH config。只创建本轮精确命名/标记的测试容器，清理前复核容器 ID 与标签；不使用全局 prune。公司端只提供明确参数的只读探针包，不自动连接或部署。

TDD 先验证帧上限、乱序分发、错误身份、断线/超时关闭、控制名额、环境过滤（含 PROGRAMDATA）、引用与目标校验、只读拒绝写操作、报告脱敏；再接真实 envd/journal 的 Linux 实验。原机网络数据未取得时，公司秒级验收保持未完成；已确认安全假设失败时阻止完整迁移，报告真实阻碍。

## 10. G0/G1 实验结果与阻断（2026-10-28）

### 10.1 实际实现及正常/失败链

`python probe.py → __main__.run → inspect_target → Target.worker_command → ProbeChannel → worker → 原 envd Unix socket → EnvironmentOperations/FileDispatcher`。inspect 核对运行状态、归属标签、完整容器 ID、环境及会话；报告不保存这些私有值。默认只读路径仍拒绝 operation_submit/stop 和 Git 输入变更。

每次连接只启动一个桥接进程，stdout 是 JSONL；request_id 分派乱序响应，普通 IO 最多 8 个在途，hello/status/stop 有 2 个独立名额。原型 worker **仍为每个请求打开一次旧 envd Unix socket**，没有将旧 envd 改为持久多路接入。此差异在并发实验中成为明确待查边界。

断线后不重发命令：`exercise` 在收到 submit 回执并观察 started 后关闭通道 → 重连 → 查询原 operation_id → 确认仍 running → stop → 检查 stopped 且 process_reaped。**没有注入 submit 丢回执**，不能把这个测试叫作丢回执恢复已通过。

协议/身份错误、超时、处理任务异常会结束通道；POSIX 按本轮自建进程组清理，并限时消费管道直到 EOF。Windows 强制回收不能证明 ProxyJump 后代已清理时记 `probe_cleanup_unconfirmed`，Windows 实机证据仍待提供。

`fixtures` 用生成数据调用现有 GitRepository/SnapshotBuilder，Git 初始化隔离继承的 GIT_DIR/WORK_TREE/INDEX_FILE；输入按 1 MiB 顺序发送，使用原 input_begin/chunk/cache/git_input 路径。只在专属标签容器允许这些操作。没有变更产品 API、依赖、envd 源码、release 清单或运行配置。

### 10.2 本机测量，不是公司验收

环境为本机 Colima Linux ARM64、Docker **29.5.2**，2 CPU/2 GiB 专属容器。使用标准构建脚本新建**本机候选** envd 0.5.4 + AITest Kit 0.6.0 镜像；公司仍用原 0.5.3 镜像。没有连接公司 Docker 19.03。

容器保持只读根、network none、cap-drop ALL、no-new-privileges；workspace/state 使用专属 **tmpfs**，所以 fsync/Git 时长只能验证数据路径，不代表公司磁盘/卷的性能。镜像下载/构建不计入准备时间。

以下是**第一轮完整成功样本**，原始记录 `build/spec141-probe/local-first-success.json`；后续并发复跑失败，不能从此表宣称稳定达标：

| 测项 | 实际结果 | 样本/边界 |
|---|---:|---|
| 建连 + hello + close | p95 91.803 ms | 20 次，本机 Docker |
| 常驻真实 envd 往返 | p95 0.637 ms | 200 次，不是 echo |
| 约 10 KiB 文件读取 | p95 0.794 ms | 20 次，无审批/模型时间 |
| 671 文件/20 Skills 快照 | 0.5947 s | 1 次，bundle 5,072,820 bytes |
| Docker 创建启动/就绪 | 0.1344 / 0.5965 s | 1 次 |
| 输入上传/校验/Git 安装 | 0.1097 / 0.1148 / 0.1608 s | 1 次，tmpfs，5 个 1 MiB 分块 |
| 固定命令 + 文件读回 | 0.0574 s | 1 次，真实 operation/journal |
| 确认停止 | 0.0099 s | 1 次，检查进程已回收 |
| 输出总量/保留尾部/单次取回 | 200 MiB / 8 MiB / 64 KiB | 首次观察值；不能证明所有负载都通过 |

Bash 与 AITest `--help` 走真实 envd 任务执行路径成功；这不能证明公司“解释器不可用”问题已修复。高输出测项随后增加输出增长重叠和保留限额显式检查；之后的实验未完成该阶段，因此不能将旧观察值升级为新门禁已通过。

### 10.3 两个阻断及停止规则

1. **安全失败已经实证**：真实任务 UID=1000 可用 r+b 打开 `/state/journal.sqlite`（没有写入字节）、连接控制 socket、读取 PID 1 环境。故 `migration_gate=blocked_security`。恢复日志不可作为不可信工作负载面前的独立可信事实来源。尚未实现 UID 隔离。
2. **并发失败尚未完成根因定位**：在 200 MiB 输出时执行 8 路小查询，后续三轮均返回 `probe_envd_io_failed`，最后一轮获得 Linux errno **107**。记录为 `local-run-final.log`、`local-3.json`、`local-4.json`；最后一份含受限 errno。按项目“连续三次失败停下”规则停止进一步试改。当前假设是旧 envd 的逐请求 socket 接入或桥接连接状态在并发下失效；源码默认 listen 队列为 5 是线索，**不是已证实根因**。不靠重发、降低断言或加长超时制造通过。

正常成功样本说明固定启动开销可以显著消除；这些失败则说明不能只套一层常驻 SSH 就发布。下个设计切片应先确定 envd 的持久接入/有界调度与 UID 隔离合同，再补故障复现，之后才进入完整工具/UI 迁移。端到端批量 Skills、审批合并、管理批处理、真实卷上的首建计时、取消/销毁 UI、丢回执与停止竞态仍未验收。

### 10.4 可交付探针与验证边界

独立包 `build/spec141-probe/sandbox-probe-spec141.zip`，使用说明 `scripts/sandbox_probe/README.md`。已校验 ZIP、解压后从源码树外运行入口；公司端默认只读，不需要升级控制端或更换镜像。产物与源码哈希见同目录 `probe-manifest.json`。

本轮新增回归覆盖只读准入、输入限额、响应乱序、身份错误、控制名额、超时/断线、背压、父进程先退出后代持管道、脱敏、Git 环境重定向与生成夹具。安全/架构审查发现的问题已补失败测试再修正；测试结果与标准构建记录见 `build/spec141-probe/verification.json`，该文件记录实际收尾结果，不以本节替代执行证据。

首次全量运行因测试资源 PATH 中缺 fd、localhost HTTP 环境未就绪而出现失败，已保留原日志；按仓库 CI 使用 native_assets/ci_shards、仅为测试进程指定本机直连后重验。不修改断言或真实环境配置。

每轮专属容器按完整 ID 和标签核对后删除；不 prune、不删除旧镜像/卷。没有提交、打标签、发布、远端部署或新增用户掌握证据。本节下一步的公司只读结果已在第 11 节取得；G1 未通过前不开始完整迁移。

## 11. 公司 SSH 只读结果与预算更新

来源是用户上传的两张 `probe-report.json` 照片，**未读取原 JSON 文件**。转录与计算记录在 `build/spec141-probe/company-g0-from-photos.json`。照片中的 worker SHA256 与当前本机原型逐字节哈希一致；报告显示 envd 0.5.3、SSH、existing_container_readonly、status=measured。

| 测项 | p50 / p95 / max 或总量 | 解释 |
|---|---|---|
| connect + hello + close，20次 | 1914.879 / 2056.481 / 2078.316 ms | 含关闭的整个连接周期，不是纯 SSH 握手 |
| 桥接 RTT，200次 | 46.512 / 51.946 / 61.507 ms | 不经过 envd 的基线 |
| 真实 envd RTT，200次 | 46.944 / 52.679 / 129.179 ms | 本次轻负载小请求表现 |
| resource_info，200次 | 47.791 / 53.776 / 132.765 ms | 不是文件正文读取 |
| 8路 resource_info，200次 | 88.022 / 97.467 / 97.765 ms | 普通 IO 并发，不代表8个代码任务 |
| 内存上传8MiB | 3.8473s；payload 17.443、JSON wire 23.352 Mbit/s | 已包含编解码、校验和分块调度开销 |
| 内存下载8MiB | 5.4693s；payload 12.270、JSON wire 16.438 Mbit/s | 不能当作磁盘读写或物理网络带宽 |

主采样通道请求/响应均为1057，stderr_bytes=0；这不包含全部连接采样请求，也不证明 envd 容器日志无错误。总82.503秒包括20次连接采样、多个200次往返序列和吞吐测试，不能解释成“一次沙箱准备82秒”。本次没有 sample_read；文件正文、审批链、Git、真实卷、安全和高输出均未测。

预筛保持原值：RTT通过；连接周期p95超2秒56.481ms，仍记未通过；上传按JSON wire指标通过，下载16.438低于20 Mbit/s，未通过。`not_measured/not_verified`表示未覆盖，不是失败。G0仅取得连接/轻负载/内存吞吐子集，**不是G0全部通过或公司完整验收通过**。一次成功不能消除第10节高输出并发失败和同UID阻断。

沿用第5节的Read两次依赖往返、64KiB JSON字节、L+Q=80ms；Skills三次依赖往返、128KiB JSON字节、L+Q=250ms。以本次R=52.679ms、下载有效JSON速率16.438Mbit/s作预算代理，分别约 **0.217s / 0.472s**。这是条件算术估算，使用分项p95不构成端到端p95；小消息与bulk传输不同，业务处理/排队仍是假设，批量接口尚未实现。仅常驻而仍串行128次RPC时，相同RTT代理下仅往返约6.743秒，继续说明必须合并业务RPC。

首次8MiB上传实测为3.8473秒，已经包含本探针base64等开销；若用payload速率算原始字节，不得再乘4/3。该测试为8路64KiB内存块，正式Git传输为串行1MiB落盘确认，不能直接照搬吞吐或再机械叠加一遍已包含的RTT。公司本地快照、Docker创建、Git校验/安装和落盘仍未知，不据此改写成“首次10/15秒已经有保证”。

当前建议保持常驻SSH与现有Docker方向、保留热操作秒级目标；下载预算按实际数据处理，大文件返回保持分块和有界输出。下一片先讨论并验证envd持久接入/有界调度与工作负载身份隔离；不是把本轮原型直接接正式会话。无需让用户重复运行同一轮连接探针，待新切片具备条件后再测真实Read/Skills/首建和停止路径。

## 12. G1 接入故障定位与下一切片（2026-10-28 继续授权）

本片仍限制为原型和本机专属实验，完整产品迁移、真实配置及发布清单保持不变。先修复桥接接入缺陷，再核对身份隔离方案，不能把前者通过写成整个 G1 通过。

### 12.1 已复现的接入根因

Linux/Python3.12.16 的受控 Unix socket（listen backlog=5，暂停 accept）在6个已排队连接后返回 EAGAIN。`asyncio` 的 `sock_connect` 把该分支作为等待可写处理，随后 SO_ERROR=0 就结束等待；此时 `getpeername` 仍返回 ENOTCONN(107)。原型直接将其交给 StreamWriter，写/关闭发生异常。独立实验三次一致，见 `build/spec141-probe/backlog-repro.json`。这不是三次失败猜测，而是同一已控制触发条件的复现。

真实 envd 高输出实验在发送前增加一次只读 `getpeername` 观察，将这个特定分支映射为 `probe_envd_backlog`；得到相同错误，见 `backlog-envd-before.json`。没有增加队列容量、重发请求或修改 envd。已有确定性回归通过注入“sock_connect 返回但 socket 未连接”重现当前 `probe_envd_io_failed`，红灯见 `connect-red.log`。

修复合同：桥接端先完成并验证 UDS 连接，之后才构建流并写入请求。只在确认尚未连接、尚未发送任何字节时关闭该 socket 并有界等待重新申请连接；沿用整个 RPC 的 deadline，取消/超时关闭正在申请的 socket。请求发送后任何断开均不重发。普通 IO 8名额、控制2名额、消息上限保持。修改仅为 `scripts/sandbox_probe/worker.py`、对应回归与本节；不是额外的一套业务重试策略。

验证依次检查：单条 mutating 请求最终只收到一次；持续拥塞/取消释放 socket；发送后断线不重复提交；真实 envd 的200MiB输出与8路小IO重叠、输出保留限额、恢复/停止；原只读与协议回归。Linux真实测试和跨平台确定性注入的证据分别报告。
