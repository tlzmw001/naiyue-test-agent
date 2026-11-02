# Spec143A：运行、身份、历史与记忆

2026-11-01用户确认的任务自动授权、外部网络开关与项目依赖合同见[143p](143p-s042a-task-capabilities.md)；该合同替代此前B逐工具审批计划，本机普通模式审批保留。

状态：总体合同，已实施范围以143j–143r各子Spec为准；S04.2b见143q，S05正式模型接线/原生转发及本机证据见[143r](143r-s05-model-relay.md)。真实供应商/公司出站仍独立待验。权限继承与同任务信任限制见第8节。

## 1. 进程与所有者

| 组件 | 拥有 | 不拥有 |
| --- | --- | --- |
| 本机主服务/连接模块 | 本机Agent/主会话/长期记忆、持久委派关联与结果接收；OpenSSH、私有连接配置、本地文件授权/缓存、侧栏API适配 | 云端任务执行循环、直接打开云端任务数据库、容器后端管理密钥 |
| Coordinator | 云端任务登记、所有者命令、命令回执、历史投影、准入、生命周期、委派编排、输入/结果、模型密钥 | 任意用户 Shell、直接打开任务SQLite、逐条文件/Shell工具实现 |
| Runner | 当前任务 Agent、工具、根/子会话、任务权限执行、checkpoint、后台命令与必要保存 | 其他任务目录、全局配置原文、OpenSandbox 管理权限 |
| OpenSandbox | 容器与执行基础设施 | 应用聊天历史、记忆规则、本地代码交付语义 |

复用 `application/sessions/session_service.py` 的运行与保存职责，拆出不依赖项目级全局数据库的装配入口；Runner 只接受启动时固定的一个 task_id。保留根/子 Agent 语义，不再通过“创建云端项目 Host”让 Coordinator 隐式运行图。

装配前置只读分析见[143i](143i-runner-assembly-spike.md)。S04.1固定task装配、单根运行和必要历史/控制的实施边界见[143m](143m-s041-runner.md)；复用SessionService与create_agent，不通过environments=None打开整个全局Host。S04.2b按143q接配置角色/子图、受管后台、停止及显式恢复；任务自动授权/依赖环境见143p，S05正式模型接线/受限转发见143r，真实供应商仍待独立验收。

拟新增模块按职责放入 `application/cloud/`（任务/命令/控制）、`cloud_runner/`（运行入口）、`service/cloud/`（路由/模型转发）、`storage/cloud/`（注册表）；沿用项目现有 FastAPI、SQLite、HTTP/SSE 和日志，不创建独立 RPC 框架或第二套 Agent runtime。文件达到 500 行前按实际职责拆分。

## 2. 身份与最小记录

云端 project_id 使用现有 UUID 格式；根 task_id 与根 session_id 同值，使用现有 32 位 hex。子 Agent 的 thread_id 与 task_id 分开。input_id 是内容清单 SHA-256；result_id、command_id 使用随机 UUID；不能从路径或标题推断权限。

```text
CloudTask {
  task_id, project_id, title, input_id,
  template_id, runner_release_id, state_schema,
  sandbox_id?, generation, revision,
  lifecycle, active_run_id?, created_at, updated_at,
  last_error_code?, result_ids[], last_quiescent_at?, reclaim_blockers[]
}
CommandReceipt {
  command_id, task_id, expected_generation, kind,
  request_digest, status: accepted|running|succeeded|failed|unknown,
  run_id?, error_code?, created_at, updated_at
}
```

`generation` 每次替换 Runner 单调递增；`revision` 对外控制请求做 CAS。外部响应不包含凭证、绝对宿主路径或原始供应商错误正文。必要事件批次携带task_id/当前投递generation/epoch，事件携带source_generation/run_id和单调seq；需要恢复的事件先持久化后投递。当前投递身份与历史来源分别核验，详143j第8节。

## 3. 状态、并发与恢复

生命周期：`creating → ready ↔ stopped`；准备失败进入 `failed`；容器/执行结果不能确认进入 `reconciling`；已确认丢失进入 `lost`；显式删除进入 `deleting → deleted`。`starting/stopping` 是带 command_id 的中间状态。单次运行的 running/interrupted/finished 与任务生命周期独立，网络连接状态也独立。

准入在 Coordinator 事务中预留任务/generation；不跨网络长期持有 SQLite 事务。Runner 再核对任务、generation 和单根运行独占。重复 command_id＋相同摘要返回原回执；同 ID 不同摘要 409。模型/工具调用不能因 HTTP 重试复制执行。

正常链：新run命令 → Coordinator持久登记accepted → 经第7节控制通道发给Runner → Runner持久登记command/run归属后ACK → 当前SessionService/图运行 → 保存checkpoint及可重发历史事件 → Coordinator事务写历史投影/确认序号 → 必要保存双方确认后登记命令终局 → SSE展示。Coordinator不是checkpoint的第二写入者；展示增量不等于持久保存完成。

故障链：提交后连接断 → 回执仍 accepted/unknown → 重连查询原 ID → Runner/持久记录对账。不能证明未开始时不得自动新建 run。Runner 崩溃后的在途外部工具标记 unknown/interrupted，复用现有“不可证明未执行就不重放”的恢复合同；下一轮显式续聊，不从最后模型请求自动重试整个图。

S04.2b将根运行终局与任务静止分开：根completed/stopped且必要历史确认即可结算本轮；受管后台仍在时保留background_commands阻止回收，允许新一轮查询/停止后台。静止心跳绑定last_run_id，不能解除在途根/控制/捕获/环境操作或unknown保护。正常stopped可以继续新输入，不强制走恢复。

停止/恢复走所有者control命令并保留独立回执、control_operation_id；以已确认control_finished历史结算，不以WS ACK结束。显式recover固定原run和Runner提供的checkpoint，只执行现有三阶段状态核对/配对，不重放模型或工具；控制重试沿原ID继续。任务上次Runner活动未确认时，同代重启不核销旧进程；显式acknowledge_unknown停止后，只有后端确认旧容器消失才允许新代启动，原run仍unknown直至显式恢复。完整合同、从未进入图的null checkpoint与提交后会话装配尾段见143q。

旧 generation 未确认停止前禁止新 Runner 写同一 state/workspace。仅令牌吊销不足以停止本地文件写入，必须先通过后端确认旧容器终止/失去挂载，再启新代；无法确认则停在 reconciling。恢复不能覆盖主异常或将必要保存失败降级为提示。

关闭浏览器、连接器或 SSH 不停止云端任务。显式“停止本轮”只停止当前根/子执行并保存；“停止环境”先请求停止、等待必要保存及受管命令收束，再停容器；“删除”另做资源与数据范围确认。强制终止必须如实显示可能存在未保存/未知操作。

持久任务显式使用后端`timeout=None`，不依赖Coordinator续租维持进程。自动空闲停止只针对已就绪且Runner报告静止边界的任务：无根/子运行、未收齐输出、受管后台命令、必要保存/历史投递未确认及未对账unknown；Coordinator还须核对无在途命令、捕获、换代或状态不确定。管理操作与新run准入互斥并在停前再次核对generation/revision，不能用一次心跳或last_activity时间替代这些条件。

静止边界后超过配置的idle_stop_seconds才可停止，保留workspace/state/历史；历史查询、SSE订阅不刷新执行活动。Runner失联、活动时间保存失败、状态缺口均登记reclaim_blockers并阻止自动停止；未知资源继续占额度。Coordinator停机期间不执行自动回收，恢复后先对账；不能用到期销毁解决不确定状态。显式强制停止另走已确认范围和风险提示，不承诺内存或未保存内容恢复。

## 4. 存储布局与数据库边界

全新 B 使用显式 `cloud.data_dir`，拒绝指向现有 A 数据根；不迁移/自动发现旧库。建议布局如下，实际根目录走配置：

```text
cloud-data/
  cloud.sqlite                    # 任务/命令/传输/版本；Coordinator单写
  history/<task_id>.sqlite         # 对外历史投影；Coordinator单写，任务不挂载
  inputs/<input_id>/              # 已验证 immutable 清单＋归档
  tasks/<task_id>/workspace/      # 当前代码，唯一任务可写
  tasks/<task_id>/state/          # Runner本地sessions/checkpoint/WAL/待确认事件
  results/<result_id>/            # 固定结果清单、patch、独立产物
  releases/<release_id>/          # 已验证运行产物/清单
  staging/<operation_id>/         # 未提交文件，不可被任务目录引用
```

每任务数据库持久化在容器外本机磁盘，仅挂载给该任务`/state`。Runner是应用层唯一写入者；同UID任务命令仍可能篡改它，不能据此宣称OS级单写保护。无需多个容器争写项目sessions.sqlite。SQLite用Linux本地磁盘WAL，活动库不放网络共享盘；容器停止/重建保留workspace/state。

Coordinator只读取自己拥有的history投影，不打开tasks下的SQLite、WAL/SHM或任务指定路径，不调用任务侧`open_storage`/saver.setup。`mode=ro/query_only`不能防止路径被换成链接/FIFO；前置resolve再open也不是保护。历史查询沿用既有分页/回复/用量算法，使用有界短事务、正文/条数/查询耗时限制；SSE不持有读事务。无需让宿主账号读取任务0600数据库或写其SHM。

Runner先持久保存可重发事件，再按epoch/seq提交；Coordinator校验绑定身份、序号连续性、事件枚举/长度和状态，在同一事务提交投影与接收游标后ACK。相同序号同摘要去重，摘要冲突/缺口拒绝并要求补齐；不因断线复制run。Runner只删除已确认的待发记录，未确认投递属于必要保存。checkpoint与事件保存不是跨库原子事务，失败须保留可恢复阶段，不将半完成包装为终局成功。Runner损坏且事件未同步时显示已确认上界与缺口，不向用户承诺未确认内容已保存。

epoch属于任务持久日志，不因连接/run/Runner换代自动重置；新代使用当前有效凭证补发旧outbox，保留原source_generation和同epoch/seq。拒绝旧代凭证/连接不等于丢弃旧来源历史；后者仅为数据，不取得当前控制权限。日志丢失/损坏需显式对账，不能换epoch隐藏缺口。S01先接真实持久台账/ACK/短分页，S04/S06再在该台账上接完整语义投影；不能将无消费者的聊天/审批接口当作S01交付。

此处必要事件指可恢复的命令归属、最终聊天/回复边界与运行状态；临时正文delta、用量/诊断观察按原有有界降级合同处理，缺失须可见，不因模型usage flush失败把已完成主任务改成失败。现有RunEventPublisher会隔离observer异常，不能仅注册一个HTTP/outbox observer承载必要保存；应在SessionPersistence/RunUnitOfWork必要提交位置显式接入并确认。B首版不建立逐工具审批状态/决定路由，任务内普通工具使用143p的固定NoAsk授权；本机审批链保留其实际消费者。

任务只提交执行观察；任务事件不能修改全局权限或产生所有者授权。工作区外的宿主/管理能力不提供通过工具审批临时扩权的入口；结果应用、部署和访问配置变化由所有者显式发起。Runner的工具结果/退出报告是该任务上报的数据，不能代替后端确认容器已停，也不是独立防篡改执行审计。

task state 是该任务的信任域：同容器命令可读取任务令牌，也可能篡改自己的代码/历史。该方案保护宿主、根凭证和其他任务，不宣称自己的 state 对本任务恶意命令防篡改。Coordinator 对任务上报的数据做身份、长度、枚举与状态校验，不让任务内容更新全局权限/路径。

## 5. 历史、checkpoint、记忆的区别

| 数据 | 保存位置/写入者 | 恢复与共享 |
| --- | --- | --- |
| 聊天档案/回复/用量 | Runner本地持久记录＋Coordinator独占投影 | 停容器读已确认投影，不启动Agent；未确认缺口可见 |
| 所有者命令/控制回执 | Coordinator的cloud.sqlite | 权威用户决定不由Runner自报；执行观察按来源记录 |
| LangGraph checkpoint/子图状态 | 每任务 state，Runner 写 | 固定 state schema 与 Runner 版本恢复；不重放未知副作用 |
| 工作区与输入 I | workspace 可写，input 不可变 | checkpoint 不替代文件快照；恢复聊天不自动回滚代码 |
| 个人/项目长期记忆 | 本机既有数据库/MemoryAccess | 仅本机整理；不向云端挂库或开放memory API，过程查看不参与整理 |
| 委派关联/结果接收 | 本机持久库＋Coordinator委派/命令回执 | 绑定来源project/session、用户操作ID与instance/task/result；仅实际工具查询记录真实run/tool_call，不伪造模型调用，断线对账/幂等接收，不迁移主会话checkpoint |
| 临时辅助上下文 | Spec142 后续定义 | 不借 B 迁移自动变成持久会话 |

按[143s](143s-local-session-cloud-delegation.md)，保留本机当前记忆条目/来源/禁用/遗忘与质量门禁，不迁移MemoryAccess到云端，不实现云端个人/项目记忆或自动整理。任务说明携带所需上下文；Runner可写交接文件随固定结果取回。143w将下载/分析草稿/代码应用分开：下载与未发送草稿不入聊天/记忆，用户确认发送分析要求后由只读材料工具真实读取并必要保存，才形成可引用来源；报告不伪造用户声明。查看过程、自动对账和侧栏补充指令不自动写入主聊天或记忆。

## 6. 模型密钥：参考事实与本稿选择

| 项目 | 可确认的证据 | 不得扩大成的结论 |
| --- | --- | --- |
| OpenHands SDK | [mediation.py](https://github.com/OpenHands/software-agent-sdk/blob/98849457621881ae2414719b82b8bc1cf535b4ab/openhands-agent-server/openhands/agent_server/docker_runtime/mediation.py) 的 materialize_secrets 将选定 SecretRegistry 值具化后转发 runtime | 不能说它保证同任务进程看不到选定密钥 |
| OpenSandbox | [Credential Vault](https://github.com/alibaba/OpenSandbox/blob/a6c0cfc509af6253f59a4d7b97b6ede21a5d797d/docs/guides/credential-vault.md) 用 egress 路径注入真实凭证，并有版本、DNS/nft、网络和 HTTPS 条件 | 不是普通容器默认行为；命名网络与 networkPolicy 的限制不能忽略 |
| ZCode | [zcodeAgentService.ts](https://github.com/zai-org/ZCode/blob/a60ba6a7e5a06742270ca605f5d2cc2766afa7e1/packages/services/src/zcode-agent/zcodeAgentService.ts) 存在解析 requestAuth 并响应 Agent 协议请求的路径 | 本轮证据不足以承诺其所有模式都不向 Agent 返回真实凭证 |
| Codex 云端 | [官方环境说明](https://learn.chatgpt.com/docs/environments/cloud-environments) 说明环境与秘密配置的产品合同 | 模型认证的内部隔离实现未公开，不作为我们的实现事实 |

本稿选择 Coordinator 内一个**受限模型 HTTP 转发入口**；不另外引入 MITM/Vault 系统。保留原生 SDK 请求/响应与流，不在代理重建 Agent 循环。成本是要覆盖当前模型协议、取消、错误与流式转发；不能用一个仅支持 OpenAI Chat 的网关宣称全部模型已迁移。

S05实际接口/配置以143r为准：显式model_proxy.config_path读取原模型JSON，原schema缺base_url的openai/anthropic由model_proxy.upstreams补唯一固定根地址；其他provider沿原地址。任务只拿无key/无上游的profile，ModelGrant固定准入task/gen/profile/model；body前鉴权与预留额度，出站前核当前代际/策略/状态。token轮换/吊销阻止新准入，已准入有界请求按原租约完成，停止取消仍传播到上游；不因重复鉴权旧token产生续期401。最终request不携带共享cookie。普通刷新只换同代token/期限，新代才可换profile。Runner实际catalog由bootstrap装配；无配置时保留waiting_model。

| 当前模型接入 | Runner 内部连接覆盖 | 真实凭证处理 |
| --- | --- | --- |
| OpenAI / compatible Chat 与 Responses | 内部 base_url＋Bearer 任务令牌；保持 API 模式 | Coordinator 替换认证后发固定上游 |
| Anthropic | 内部 base_url＋x-api-key 任务令牌；原生 messages/SSE | Coordinator 替换 key；保留协议版本与必要头 |
| Ollama | 内部 base_url＋SDK client_kwargs 任务认证头 | Coordinator 转发到配置的 Ollama 端点，不开放任意地址 |

这些是内部装配覆盖，不把任意URL或真实密钥塞进用户消息。真实key、OpenSandbox key、所有者token只由管理服务持有。任务令牌绑定task_id/generation、provider/model、用途和有效期；通过已鉴权控制通道提前续发，终止/代际更替吊销。初始令牌放Coordinator独占目录中的单任务文件，单独RO挂载；Runner可在本任务域持有后续令牌，Coordinator不向任务RW目录写续期文件。日志/argv/提示不包含它。Coordinator不可达时模型/记忆调用可能失败，不因无容器TTL而承诺任务能脱离控制面完成。

服务端显式检查令牌、任务状态、generation、允许的 endpoint/method/model、体积和并发上限；固定上游 allowlist，禁止请求指定上游、自动跨域重定向或透传用户 Authorization。流式逐段转发，断开传播取消；不缓存完整模型正文、不记录提示词/响应或认证头。保持原 SDK 重试归属，代理不叠加透明重试或第二份 token 计费。

任务内命令仍可能拿任务令牌调用允许的模型；令牌限制损失范围，不证明“只有 Agent 可以用”。同任务工具若需真实业务凭证，也属于该任务信任域；首版不增加通用秘密保险库。脱离项目需求的全网出站封锁/全协议凭证注入不纳入本期。

## 7. API 与传输合同

所有者API只在loopback监听，使用单用户bearer；任务API只在专用bridge地址监听且不注册所有者路由。可共享应用代码，不能共享一个包含全部路由的监听入口。权限在业务路由显式检查；Runner token不能列全局目录/管理其他任务，SSH不替代API认证。

| 拟定入口 | 输入与输出 |
| --- | --- |
| `GET /api/cloud/v1/instance` | 实例 ID、协议、能力、版本；不创建任务/检查点 |
| `GET/POST /api/cloud/v1/projects` | 云端逻辑项目目录；不包含 Windows 真实路径 |
| `POST /api/cloud/v1/tasks` | command_id、project_id、input_id、template_id → task/回执；无有效已提交输入拒绝 |
| `GET /api/cloud/v1/tasks/{id}` | 生命周期、版本、准备阶段、最后错误、当前 run；响应 revision |
| `POST /api/cloud/v1/tasks/{id}/commands` | command_id、revision/generation、start/stop_environment/capture/delete；返回回执 |
| `GET /api/cloud/v1/commands/{id}` | 原命令状态，供断线对账 |
| `GET /api/cloud/v1/tasks/{id}/history/events` | 所有者读取自有有界事件台账；epoch/after_seq/limit及已确认上界；不读取任务DB |
| 现有本机 scopes/session 消息与SSE；任务专属历史/控制适配 | 本机主会话继续使用现有Host；侧栏按本机持久关联访问目标instance/task，不把云端过程映射成本机主消息或复制Agent循环 |
| `GET /internal/cloud/v1/tasks/{id}/identity` | 当前任务凭证限定的身份与已确认游标；不返回token/路径 |
| `WS /internal/cloud/v1/tasks/{id}/control` | Runner主动连出；握手绑定generation/release/protocol，承载命令、ACK、令牌更新及心跳 |
| `POST /internal/cloud/v1/tasks/{id}/events` | Runner 事件批次＋epoch/seq，重复去重，缺口要求恢复 |
| `/internal/cloud/v1/tasks/{id}/model/{provider}/*` | 原生模型协议转发；独立鉴权与字段校验 |

文件传输与版本路由见配套文件，不把大文件放 JSON/SSE。通用会话路由可复用现有消息 DTO；A 的 execution_binding/bundle 字段明确删除，不承诺旧客户端兼容。客户端握手版本不符时先报升级要求，不能解析失败后继续发送。143j限定S01实际注册的子集，未到切片的路由不注册占位成功；能力发现仅返回已接线项。

事件订阅支持按 epoch/seq 补发；超过保留窗口返回需要 snapshot，客户端通过历史/状态重建。先获取快照上界再衔接事件，缺口不可静默跳过。控制 ACK 表示命令已接收，完成状态以持久回执/终局为准；SSE 失败不撤销已成功保存的任务。

控制通道使用现有FastAPI WebSocket能力，不引入独立RPC框架。帧携带kind/command_id/task_id/generation/run_id及有界payload；握手后任务身份由服务端绑定，不接受帧改写。当前代只允许一个有效连接，重连使旧连接失效但不创建新run/generation；队列有界、反压可见，大文件和模型正文不走此连接。HTTP事件提交仍负责第4节持久投递，不并行再造第二套事件ACK。

进程退出采用结构化终局/错误码和后端状态对账，不解析stdout/stderr约定行。正常退出先完成必要保存；Runner异常退出必须令任务容器及其后代收束。OpenSandbox覆盖容器entrypoint，不能仅写Runner是PID1就当已接线；S04实测Runner被kill后容器退出、无孤儿写入，再启下一代。无终局回执时登记interrupted/unknown，保留主异常与已确认输出。

## 8. A权限实现的继承清单与保证边界

核对当前源码而非仅引用旧提案：[141A](141a-envd-identity-boundary.md)第0节是已实现合同，后续同UID旧提案已撤回。B不保留envd，但以下行为测试须迁移到新消费者，不能随旧协议一并丢弃。

| 当前生产入口/回归 | 已解决的坑 | B处理与验收位置 |
| --- | --- | --- |
| `envd/identity.py:initialize_identity/TaskProcessRunner.run`；`test_envd_identity.py`、`test_envd_capabilities.py` | 同UID不能靠0700/0600隔离；降权、附加组、能力、FD、环境必须一并处理 | B不复制A的六项控制能力；任务仍非root/dropALL。根凭证/权威记录移到未挂载的Coordinator域；验证真实UID/组/五项cap/NoNewPrivs及无控制FD/环境，S00/S02/S04 |
| `envd/workspace.py:Workspace.open/check`；`test_envd_file_boundary.py` | resolve后再open的竞态、只检查最后一级、FIFO阻塞、硬链接/跨挂载别名、审批后父目录替换 | 宿主所有不可信文件摄取/备份/删除及连接器交付以固定根/父目录句柄处理；保留内部安全链接、拒绝越界/特殊类型；S03/S07/S08/S10 |
| `envd/file_mutations.py:mutate`；同上 | 暂存父目录可被任务替换、先改文件后伪报保存、rename/fsync结果未知 | 输入/结果提交放所有者私有staging；同文件系统原子安装并记录提交点，IO不确定保持unknown；S03/S07/S08 |
| `tools/processes.py:HostProcessRunner.run/_settle`；现有process/后台任务回归 | 启动被取消仍可能已有进程；发信号不等于退出；退出不等于输出收齐 | 复用受管句柄/进程组、等待回收/输出和主异常保留；不开放任意PID控制；Runner死后以容器状态收束，S04 |
| `agent_runtime/tool_execution/permissions.py`、`approval.py`、`session_approval.py` | 授权准备不是执行；路径/父目录subject复核、批次与根子审批归属 | 复用准备/执行复核与显式deny，B采用NoAsk固定能力、不迁移远端interrupt审批；批次Write后Read合法，不误把inode冻结为永久授权；S04/S06 |
| `environments/activity.py/lifecycle.py/operations.py`；lifecycle policy/races/completion回归 | 活动保存失败/unknown仍占额度；运行、未收齐输出、必要保存阻止空闲停止 | 按第3节迁移到任务状态和静止边界；只读观察不续活动，管理与准入互斥，S02/S04 |
| `envd/identity.py:trusted_git_executable`、`envd/git_inputs.py/git_results.py`；Git安全回归 | 不能用控制身份执行工作区可修改Git程序/配置，或把任务输出当可信完成 | 比较程序/依赖位于只读发行资源，原始Git配置/filters/hooks禁用；结果上传仍按不可信数据限额校验，S03/S07/S08 |

首版B明确以整个任务容器为同一信任域：Runner与任务命令同UID，可相互影响、读取任务令牌或篡改任务本地checkpoint。独立venv/环境过滤减少误破坏，不提供141A那种控制进程相对任务的OS防篡改保证；模型内审批也不能约束已获准Shell中的任意后续动作。Coordinator仅保证所有者命令、跨任务/根凭证/管理面边界，不把任务报告标为独立可信审计。若要求同任务恶意命令也无法影响Runner/审批执行，必须在S04前另行修订身份/部署方案并实测，不能静默增root/cap或宣称本Spec已满足。

正常文件例子：I中的`proto/current.proto → service.proto`根内链接可解析并逐字节核对。失败例子：任务把待摄取文件或其父目录换成指向Coordinator库的链接/FIFO，摄取作业拒绝且管理服务仍可响应其他任务；不得把路径检查后的字符串重新交给高权限open。新B纯内部工具对本任务state的访问不冒充跨域隔离，宿主摄取/本机交付则必须满足上述边界。
