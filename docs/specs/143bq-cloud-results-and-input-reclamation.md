# Spec143BQ：主 Agent 取回云端产物与完整删任务后的共享输入回收

日期：2026-11-18。状态：**主链已接通**。实现边界和验收证据见第 9 节。

## 1. 已确认的结果

1. 主 Agent 按对话指令查询关联云端任务、把结果文件保存到指定本机位置，再用现有文件/进程工具处理。
2. 只增加一个模型工具 `cloud_result`，动作是 `query / download / status`。不拆成多个工具，不在云端完成后自动唤醒主 Agent。
3. `download.force` 默认 `false`。工具报告冲突，模型询问用户；用户可选择强制覆盖或改名。用户已明确要求覆盖时允许第一次调用就 `force=true`，不要求先制造一次冲突，也不增加冲突解决动作或确认票据。
4. 新工具可用后退出“交给主 Agent 分析”的专用草稿/材料注入流程；结果窗口的文件取回操作只保留“保存到本地”，保留列表、选择和预览。
5. 源码与其他文件使用同一产物通道。用户选择今后让云端 Agent 把变更文件打包后取回，或按指令 Git 推送远程；退出旧“应用代码／创建分支”流程，不为历史“基线＋补丁”增加转换链路。
6. 完整删除云端任务后，回收该任务曾使用、且没有其他任务、附件、在途上传/关联、读取或必要恢复依赖的 Coordinator 共享输入包。
7. 删除前持久固定候选 ID；回收可中断核对、幂等重试；实际删除后才释放容量；任务删除与缓存回收分别报告，失败原因可见。
8. 释放容器环境、本机移除关联不触发此回收。从未关联任务的已提交包不进入本次回收候选；无 TTL、全局垃圾扫描或新的独立回收工具。
9. 本机与 Coordinator 配套升级，旧客户端明确提示升级后才能继续云端写操作。
10. 云端成果保持最新结果轮换；Runner outbox 仍随完整删除任务清理；其余云端策略与拷贝实现保持。

## 2. 最小方案与切片

| 切片 | 可检查的交付物 | 依赖 |
|---|---|---|
| [S1：单工具与本机保存](143bq-01-result-tool-and-save.md) | 主会话查询、单文件下载/改名/覆盖、真实路径和可恢复回执 | 复用现有 receive、files delivery、文件权限 |
| [S2：窗口与旧分析/代码取回退出](143bq-02-result-ui-and-analysis-retirement.md) | 手动保存保持；旧专用分析、应用/分支入口退出；历史仍可查看 | S1 闭环通过 |
| [S3：完整删除与共享输入回收](143bq-03-task-input-reclamation.md) | 原删除回执上的候选、依赖保护、实际删除、独立报告与重试；协议升级 | S2 先移除旧补丁恢复消费者 |
| [验收矩阵](143bq-04-acceptance.md) | 分片红绿证据、全链路故障注入、迁移和平台验证 | 全部切片 |

实现影响超过 8 个文件，涉及工具、运行接线、本机云端应用、Coordinator、存储和前端。按三个行为切片推进，不新增服务、通用资源回收框架或第二套文件复制器。

S1 每次 `download` 下载一个产物到一个完整文件路径；多文件由主 Agent 连续调用同一工具。这个技术收敛不限制现有手动多选保存，也避免为新工具增加多目标权限/部分覆盖决策机制。S3 的输入占用记录只服务本次已有竞争窗口，不扩展为通用租约平台。

## 3. 当前真实数据流

```text
Runner .nyt-output/<run_id>/普通文件
  → result_snapshot.capture → 固定 result/artifact 元数据和字节
  → Coordinator 结果存储、最新结果轮换、下载 lease
  → CloudResults → CloudResultReceive → LocalReceives
  → 本机校验缓存 + receive/material 记录
      ├─ CloudFilesDelivery → LocalDeliveries → delivery_files → 用户目录
      └─ analysis-draft → 主会话草稿/material_refs → cloud_result_read

旧 input_id 任务另有：工作区 → manifest + patch → 基线还原 → apply/branch/export

完整删除：确认预览 → 删除预留 → 停止/排空 → task_cleanup
  → 墓碑 + 成功回执 + 本机关联退役
  当前没有共享 inputs 回收。
```

源码事实：普通产物通道不限制 `.py`、`.zip`、报告等扩展名，但只接收实际写入当轮产物目录的文件；工作区改动不会自动变成普通产物。旧 patch 只能说明变更，不能冒充完整源码文件。

已核对的主要入口：

| 职责 | 当前来源 |
|---|---|
| 产物捕获/安全枚举 | [result_snapshot.py](../../naiyue_test_agent/cloud_runner/result_snapshot.py)、[result_scan.py](../../naiyue_test_agent/cloud_runner/result_scan.py) |
| 接收、范围校验、结果租约、完整性 | [receives.py](../../naiyue_test_agent/application/cloud_local/receives.py)、[cloud_receive.py](../../naiyue_test_agent/storage/cloud_receive.py) |
| 目标预检、持续写入、回执 | [deliveries.py](../../naiyue_test_agent/application/cloud_local/deliveries.py)、[delivery_files.py](../../naiyue_test_agent/application/cloud_local/delivery_files.py)、[cloud_delivery.py](../../naiyue_test_agent/storage/cloud_delivery.py) |
| 当前模型材料读取 | [cloud_materials.py](../../naiyue_test_agent/tools/cloud_materials.py) |
| 完整删任务与成功后脱敏 | [deletions.py](../../naiyue_test_agent/application/cloud/deletions.py)、[storage/cloud/deletions.py](../../naiyue_test_agent/storage/cloud/deletions.py) |
| 上传/附件关联 | [inputs.py](../../naiyue_test_agent/application/cloud/inputs.py)、[storage/cloud/inputs.py](../../naiyue_test_agent/storage/cloud/inputs.py)、[attachments.py](../../naiyue_test_agent/application/cloud_local/attachments.py) |
| 上传命中快路径 | [input_upload.py](../../naiyue_test_agent/cloud_files/input_upload.py)：先 GET 已存在包，命中后不创建 transfer |

## 4. 新主链与职责

```text
用户指令 → 主 Agent 选择任务、文件、目标路径、是否 force
  → cloud_result：严格参数 + 当前运行身份 + 现有权限
  → 本机云端窄端口：验证来源会话/项目/绑定，固定 result/artifact
  → 原 receive：下载和校验 → 原 files delivery：预检和写入
  → 结构化回执：operation_id、状态、真实路径、冲突/失败原因
  → 主 Agent 查询 status 后用现有工具处理文件

用户完整删除任务 → 原删除命令（先固定候选）
  → 原停止/排空/目录删除/墓碑 → task_deletion=succeeded
  → 逐候选核对依赖 → 退役标记 → 受管文件删除 → 容量核销
  → input_cleanup 独立回执 → 本机删除记录仍可查询和重试
```

模型负责理解“哪个任务、哪个文件、保存哪里”和向用户解释冲突；程序负责身份、权限、路径、完整性、依赖竞争、持久回执和真实容量。工具结果中的云端文件名和报告正文属于外部数据，不作为新的执行指令。

正常/失败的具体函数链、数据形状与异常传播分别写在 S1 和 S3。状态权威分别是 Coordinator 任务/结果、原 receive、原 delivery 和原删除命令回执；前端与模型不得自行合成“已完成”。

## 5. 对外合同与兼容范围

| 接口/消费者 | 本次决定 |
|---|---|
| 主 Agent 的工具目录 | 新增一个 `cloud_result`，随后移除 `cloud_result_read`；主会话作用域内可用 |
| 结果窗口 | 保留普通产物预览和手动保存；退出专用分析、旧代码交付、独立浏览器下载按钮 |
| 原普通产物 receive / files delivery / 字节读取 | 保留实际消费者与现有完整性语义，扩展目标文件名映射供 S1 使用 |
| 历史消息中的 material_refs、已完成交付回执 | 保持读取展示，不重写历史、不删除已保存文件 |
| 旧 apply/branch/export 及 analysis-draft 写入口 | 按 S2 显式退役，旧请求返回能力已退出；不保留可执行的兼容包装 |
| Coordinator owner 协议 | v1 → v2；旧客户端写操作在执行前拒绝并显示升级信息 |
| Runner 协议/结果捕获/最新结果策略 | 保持现状，未授权自动 Git 推送或改变成果保留时间 |

旧代码取回退出是已确认的产品变更；因此仅有历史 manifest/patch 的结果不再承诺一键还原源码。对应普通产物仍可保存，用户也可查看历史记录。已经在进行中的旧写入必须先停止准入并核对结果，不能删除恢复材料后假装原操作成功。

## 6. 范围外与限制

- 不新增云端任务创建/发送/删除的模型工具，不扩展跨会话搜索、任务共享或后台唤醒。
- 不递归抓取云端工作区，不自动打包源码、不自动推送 Git、不增加凭证配置；由云端 Agent 按用户指令使用已有能力完成。
- 不自动解压下载包；主 Agent 在文件实际保存后使用已有工具处理。文件通道沿用当前普通文件权限/模式语义，不等同于 Git checkout。
- 首期工具接入现有可信本机项目的来源主会话和 Host 文件执行环境；普通无目录聊天、临时会话、子 Agent、Runner 与不匹配的远端执行环境不获得宿主写入能力。
- 新工具不改变结果轮换；查询后结果被轮换且本机没有完整缓存时，明确报结果不可用，不静默下载另一轮。
- 不对已提交但从未关联的输入做清扫，不给失败回收增加定时重试。仍有依赖的包可以继续保留到后续完整删除或用户对原回执重试。

## 7. 参照与证据边界

本轮以工作区源码为准。Pi 源码 `a69bef789` 的工具执行链是 agent-loop 的 `executePreparedToolCall` → `tool.execute` → 文件工具 → ToolResultMessage；失败回填到模型。AgentX `3fcfa0e` 的 `AbstractBuiltInToolProvider` 工具装配/执行链负责参数校验后进入具体工具，具体应用服务再校验领域输入。这里只复用“窄入口、明确结果与应用层校验”的职责，不迁移两者运行时。

本机环境核对：LangChain `1.4.2`、langchain-core `1.6.6`、LangGraph `1.2.12`。沿用项目 `StructuredTool.from_function`、`ToolRuntime` 和 typed RunContext，不新建 Agent 或会话。官方 [LangChain tools 文档](https://docs.langchain.com/oss/python/langchain/tools)说明运行上下文与工具结果回填；本项目权限和持久化仍以当前源码为准。

本轮旧行为基线：下载/材料/删除/读取/附件 8 个测试文件 **41 passed in 2.61s**，日志见 [baseline-tests.log](../../build/spec143bq-design/baseline-tests.log)。Spec 结构检查见 [document-validation.json](../../build/spec143bq-design/document-validation.json)。这些证据都不证明新增能力已实现；没有真实云端、模型效果或 Windows 实机验收结论。

## 8. 讨论收束

需要用户决定的两处分歧已收束：采用普通产物/Git 的未来代码取回方式；接受本机与 Coordinator 配套升级。无新增业务选择阻塞 Spec。

技术上必须守住的实施门槛：单文件改名不能改动源 artifact 身份；旧代码链需按实际消费者退役；上传 cache hit 也须先建立保护；删除成功后仍能看到和重试清理回执；升级不能让旧进程忽略新的保护记录。各项已落入子 Spec 与验收矩阵，不能只写在工具描述中。

## 9. 实施状态

- S1 已接入默认主会话工具和文件权限。下载回执沿用交付库原 `preparing` 阶段和私有计划元数据，避免为本功能更改项目数据库版本。测试覆盖主 Agent 保存、路径、冲突、覆盖、拒绝绕过、会话许可及中断恢复。
- S2 已移除旧分析草稿链、材料模型工具、浏览器票据和旧代码交付执行分支。旧分析及代码写入口返回 410。历史回执仍可读，旧交付记录可查看已写范围及本机证据。普通产物不再依赖本机 Git 基线。低层验证代码仍用于旧数据回归。
- S3 已接入 owner v2、Coordinator 格式 3、跨请求占用、候选代、实际删除、容量核销和独立清理回执。删除记录可核对或重试，GET 不执行物理清理。Runner 协议沿用原版本。
- 对无法核对 v1 历史恢复依赖的升级前输入包，迁移登记代号并标为受保护，删除记录显示 `input_legacy_use_unverified`。部署方核对完整历史来源并登记必要占用后，使用 [S3 的历史来源核对](143bq-03-task-input-reclamation.md#12-审查修订)记录证据。Coordinator 仅在原任务删除和显式重试时检查固定候选，待实际依赖解除后回收。
- 本机验证日志见 `build/spec143bq-implementation/`。环境升级和实际删除均由临时测试实例验证，未对用户现有实例执行升级或删除。

本机全仓最终验证：后端两片 3,502＋3,805＝7,307 项通过，错误、失败和跳过均为 0。前端 78 文件 681 项通过，macOS arm64 构建、Twine 与 571 个应用 Python 文件字节对照通过。


## 10. 审查修订

主 Agent 可在冲突后使用已核验的本机产物另存或按指令覆盖。原操作 ID 可恢复暂时忙碌的下载。回执展示下载进度及明确的失败步骤。旧代码导出的恢复保护按原目的目录检查，停在预检的旧记录释放进行中保护。

附件取消先登记待解除，失去应答时沿原操作重试。完整删除已成功时，其原回执保留待解除占用入口。CLI 创建任务的明确拒绝释放已取得的占用。包元数据访问和传输缓存核验持有读预留。仍有物理回收待完成时，整实例备份保持未就绪。删除记录核对断线时继续展示已知成功和本次失败原因。

日志位于 `build/spec143bq-review-fixes/`。已发布 v1 界面使用原有文案，兼容升级顺序见 [云端更新说明](../cloud-updates.md)。

本机最终复验后端两片 3,513＋3,812＝7,325 项通过，错误、失败与跳过均为 0。前端 78 文件 682 项通过。macOS arm64 标准构建、Twine 和 571 个应用模块的包内源码对照全部通过。
