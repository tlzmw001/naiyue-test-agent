# Spec 069：最小持久会话设计起点

状态：2026-10-08，用户已确认SQLite＋官方checkpoint方案，并修正CLI为默认持久、显式临时、无ID resume显示会话选择。[Spec070](070-persistent-session-implementation.md)现已实施并完成781例/独立进程与有界真实模型验证。本文下方保留设计期间的建议与尚未实施表述，它们属于当时快照；当前实现/限制以Spec070和最新handoff为准，不扩大为完整崩溃恢复或生产成熟度声明。

## 事件与消息阶段的完成边界

Spec062–068已接入本地同步运行的run/模型/工具/完整消息/turn/控制/审批/上下文观测以及CLI消费。完整消息及时显示不等于token增量；状态可读不等于磁盘保存。正式异步、活动取消、慢消费者隔离/背压、事件持久交付和重启恢复仍未完成。遗留问题继续见future-work。

消息是模型协作协议和对话状态：Human提出请求，AI提出工具调用，Tool以tool_call_id回填结果，下一次模型读取历史；摘要改变后续实际上下文。事件是运行过程/结果事实：用来显示、通知控制请求交付/终局、诊断和评测；它不是模型下一次输入本身。工具错误是否回流、SDK是否重试、摘要是否回退由对应执行逻辑决定，事件通知其事实，不统一等待UI决定。

## 本轮核对的参考源码

- Pi a69bef789 agent.ts processEvents：message_start/update更新streamingMessage，message_end写入state.messages，工具事件更新pendingToolCalls，之后await listeners。其事件在这条链中参与内部状态维护。
- Pi AgentSession 的message_end分支调用SessionManager.appendMessage；后者生成id/parentId条目，经_appendEntry/_persist写JSONL。首次写入有hasAssistant/flushed条件；UI监听通知在保存处理前，不把已显示message_end说成磁盘确认。失败处理handleRunFailure会构造error/aborted助手消息及结束事件，但不是所有异常/监听故障都能无条件转换。
- AgentX 3fcfa0e AbstractMessageHandler：onCompleteResponse先保存AI并更新context，再发送结束通知，另调用模型/会话完成和计费hook；工具完成另有保存/通知链。说明显示、保存、评测可以分职责，不照搬Java写法或原错误正文。
- 本项目仍由LangGraph reducer/checkpointer拥有图状态，应用SessionService拥有session映射/模型/workspace/审批/控制。当前应用统一装配仍是InMemorySaver，当前langgraph1.2.12；本机尚无langgraph.checkpoint.sqlite模块。官方文档将checkpointer用于thread状态，store用于跨thread应用数据；选型前继续核对实际saver源码/依赖和同步线程兼容性。

## 建议的首片用户行为

先实现“完成一轮普通对话 → 正常退出 → 新进程选择同一会话 → 继续提问”，验证模型确实读取旧消息、工具结果和已有摘要。先解决已结束会话的正常重启延续，再按独立切片扩展暂停审批、异常退出、分支与历史树。

首片不能因为暂不完整支持审批/失败恢复就抹掉安全状态：遇到挂起审批、未完成运行或副作用待核对时，必须识别并明确阻断不安全继续，不能默认为普通可用会话、自动批准或重跑工具。具体恢复/阻断协议需在实施Spec中确定。

## 数据所有权与保存边界

| 数据 | 所有者/恢复用途 | 设计方向 |
| --- | --- | --- |
| session_id、thread_id映射、model_id、workspace | 应用，找到正确模型/工作区与图线程 | 持久应用元数据；只存模型ID，不存模型对象/API key |
| Human/AI/Tool消息、摘要、图状态及待执行节点 | LangGraph，恢复实际上下文和执行位置 | 使用框架持久checkpointer，不手写对话JSON代替完整checkpoint |
| 待审批、pending_turn、人工核对/未完成运行标记 | 应用与图的恢复一致性边界 | 必须明确如何匹配checkpoint/校验/阻断；不是把内存dict直接dump |
| busy锁、worker、发布器、回调、当前stop flag及内存控制队列 | 进程内执行资源 | 不原样序列化；崩溃后的未完成状态另有持久标记，不直接清零放行 |
| run事件与完整展示历史 | 展示/诊断/评测 | 与运行状态分开；首片不建立事件回放运行时，后续持久轨迹/全历史展示单独定范围 |

建议分两条关键保存路径：应用元数据写入；LangGraph checkpoint写入。事件消费者观察这两条路径的结果，不承担唯一关键保存。当前可选observer故障会被隔离，这种失败策略不适合作为会话唯一存储。

## 下一步要确定的设计

先比较Pi JSONL条目与LangGraph持久saver在当前项目的接线成本；结合本地单用户/未来SaaS给出存储建议，不能直接宣布使用SQLite或安装包。随后明确创建/选择会话的CLI行为、应用元数据与checkpoint的关联/写入顺序、模型/工作区不可用、正常退出与崩溃恢复的区别、存储失败终局和验证方式，再制定可执行实施Spec。

关键验收必须跨两个独立进程，不能用同一个app的两次run冒充持久恢复；加入写入失败、缺失/不匹配checkpoint及受限会话不自动重放的反例。核心调用学习仍待用户参与，不把助手设计记为用户独立掌握。

官方资料：https://docs.langchain.com/oss/python/langgraph/persistence 、https://docs.langchain.com/oss/python/langgraph/interrupts 。本轮为源码/文档设计核对，没有新功能测试、TDD红灯、构建或真实模型请求。

## 首选存储方案（2026-10-08，用户已确认）

选择本地SQLite：应用用标准库sqlite3维护会话元数据；框架使用官方SqliteSaver保存checkpoints/writes，二者共用一个数据库文件、分别管理表/连接/事务。不自写checkpoint序列化或将全部26种事件回放为运行时。官方包当前未安装，实施前单独锁定与现有langgraph1.2.12/checkpoint4.2.0兼容的版本并运行小探针，不升级既有依赖；候选选型批准不代表依赖/运行行为已经验证。

对照：Pi用JSONL SessionEntry，id/parentId组织树，恢复文件并构建当前分支上下文；优点是可读、便携和自然组织分支。我们已有LangGraph checkpoint，仅把聊天内容存JSONL仍缺调度/interrupt/pending writes，完整照搬会重复实现框架状态。AgentX用PostgreSQL/MyBatis，Session/Message/Context分离，保存消息并更新activeMessages；核对的MessageDomainService没有在该方法上看到@Transactional，不推断整个事务边界。借鉴职责分离，当前单用户CLI不引入其服务端数据库部署成本。

SQLite适用前提明确限制为本地、轻量、单CLI进程、同库单活动run；同进程可以管理多会话，不改变一个run内既有工具并行/串行规则。官方当前main源码有check_same_thread=False与内部锁，但将SqliteSaver定位为轻量同步用途，不是多线程吞吐方案；首片仍须测试CLI worker与框架写入线程。不允许两个CLI抢同一数据库的运行权，使用实例独占机制并验证第二进程在模型调用前被拒绝。实现方法在实施Spec定稿，锁不代替数据库事务。

持久入口明确选durability='sync'，本机Pregel默认'async'、sync会等待checkpoint future后开始下一步；invoke同步与保存策略不同。sync不代表外部工具副作用与SQLite事务原子，也不宣称任意掉电无损。

### 建议恢复协议

1. 创建会话时先提交session_id/thread_id/model_id/workspace及初始ready状态；空会话checkpoint可为空，有过成功run的会话必须有确认关联。模型切换写入元数据成功后再更新选择，不存API key/模型对象。
2. 每次run进入图之前，提交running与本次run_id。该提交失败则不调用模型/工具；模型、工具和图执行仍走原路径，框架保存checkpoint。事件消费者不负责关键保存。
3. 正常completed或after_turn stopped后，确认可读取已写checkpoint、图没有待执行节点/interrupt且没有manual review；提交ready和checkpoint_id/namespace，之后才发布成功/stopped终局。待批/失败/unknown分别保留受限状态，首片重启不自动恢复执行；同进程现有错误/审批行为继续保留。
4. 正常退出关闭自己的存储资源。恢复时读取元数据，核对模型ID、工作区、记录的checkpoint与实际最新状态；仅ready且关联一致的已结束会话允许新输入，session/thread不变，新run重新生成ID。缺配置模型、缺工作区、缺/错checkpoint明确失败，不默认切模型/工作区或打印空历史冒充恢复。
5. 重启遇running、interrupted、failed或review_required，首片允许列出但阻断自动续跑。metadata/checkpoint来自独立提交，即使同一DB也不跨调用原子；最后确认提交失败或崩溃残留running，即使图已结束也不能擅自放行。保留可读状态供后续恢复切片处理，不回滚已发生工具副作用。

首片关键例：图已生成回答/写checkpoint，但应用ready写入失败 → 调用失败，CLI说明保存未确认，已显示回答保持；磁盘running使新进程阻断。这样宁可暂不能自动恢复，也不把不一致状态当普通可用会话。存储失败阶段沿现有prepare/execute/finalize/cleanup分类，不要求先增加通用事件类型。

### 入口、保存位置与后续

用户最新确认：CLI默认创建并保存新会话，无需开启持久参数；新增--no-session使用原内存会话、退出后不能恢复。--session-db只覆盖数据库位置，不决定开关，默认用户私有目录~/.naiyue-test-agent/sessions.sqlite；不修改.env。去掉原建议的--list-sessions，--resume不带ID显示按更新时间逆序的会话索引和选择入口，--resume <session_id>直接恢复。应用已有内存入口及公开签名保持，CLI默认变化由用户明确要求。

列表是一会话一行，编号、完整session ID、更新时间、workspace、model ID、恢复状态；不是每个会话的完整聊天历史，不为列表自动调用模型生成标题。首片显示所选数据库内全部保存会话，不偷偷按当前工作区过滤；按真实时间倒序，时间相同时以ID确定顺序。不存在的标题/摘要不造数据。选中后使用保存工作区，若另给--workspace必须一致，否则拒绝；受限状态仍可列出但不能自动恢复执行。

TTY中--resume展示编号列表，输入编号或完整ID恢复，空行/EOF取消并退出；非法选择说明后继续选择，不创建空会话。无历史时明确显示暂无可恢复会话并退出，无模型请求。非TTY的无ID--resume只列索引、提示用带ID形式并退出，不把管道中的业务提示词当选择输入。不带--resume的普通启动创建新会话，默认持久不等于自动续上次；选择/恢复不重新执行历史工具或发模型请求。

--no-session与--resume互斥；--no-session与显式--session-db同时出现也报参数冲突，避免静默忽略。临时模式仍支持同进程checkpoint、多轮/Steering/Follow-up/审批/停止，且不打开会话DB或获取其实例锁；write/edit/Bash的业务文件副作用照常按原规则执行，不宣称工具不会落盘。持久模式数据库不可写/锁不可得时明确失败，不悄悄退化为临时模式。

目录/数据库权限0700/0600及SQLite附属文件须验证，不以路径/权限声称沙箱。创建后展示session ID，恢复保持session/thread不变而新run换ID。首片只验收新输入继续正确历史，不同时做完整聊天历史浏览/预览、箭头TUI、命名/标题生成、分支或--continue。临时入口对照Pi --no-session；Claude Code --resume无参为picker，其--no-session-persistence在官方CLI说明中限print模式，本项目不照搬此限制。

正常完成对话、工具历史、压缩摘要、model切换、会话隔离和两个独立进程验收优先；同时用故障注入验证保存失败、running残留、受限恢复、第二CLI占用及关联错误，不能只有happy path。不依靠SQLite自动承诺工具exactly-once。

完整展示历史与当前活跃上下文仍分开：checkpoints保存图状态，不直接等于Pi可读会话树或完整聊天展示索引；首片只验收继续对话，重启历史浏览/分支另片，不为远期UI提前写完整事件仓库。异步阶段换匹配的AsyncSqliteSaver或按SaaS并发改Postgres saver与元数据仓库，模型消息/工具/runtime策略不因数据库选择而重写；数据迁移/异步连接/取消仍需测试，不能称零成本切换。

源码证据：官方当前main SqliteSaver的get_tuple/put/put_writes/cursor/异步NotImplemented与线程锁；https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py 。当前main不代表待安装版本已适配；本轮没有安装包、数据库写入、产品实现或运行测试。

## JSONL与checkpoint兼容性澄清（2026-10-08）

用户认可方案的完整性，但仍询问纯JSONL是否能保持框架checkpoint一致，以及SQLite是否大众方案；这不是实现授权，存储选择仍待确认。

此段是当时讨论快照：后续用户已明确选择推荐SQLite方案，最新CLI契约见上文。无需重新询问SQLite或持久默认的授权。

结论：JSONL可以实现完整LangGraph持久saver，框架不强制SQLite。当前本机BaseCheckpointSaver约定get_tuple/list/put/put_writes等操作，Checkpoint包含channel_values/channel_versions/versions_seen，CheckpointTuple还含metadata/parent_config/pending_writes；只要完整实现本项目所需的接口和恢复语义，可以继续使用create_agent/框架checkpoint、不改Agent循环。但该JSONL saver属于我们自定义的存储后端，不是直接使用官方SqliteSaver。

必须区分两种JSONL：只保存对话及会话元数据，在正常结束边界重新构建新执行上下文，可以支持“继续聊天”；完整saver还保存图恢复状态和待写入结果，才能保留框架审批/部分执行/历史checkpoint等语义。前者不是不能用，也不是不能恢复消息；它不能直接等同于原生checkpoint恢复。

完整JSONL后端需自己处理框架类型序列化、按thread/namespace/checkpoint查询与父子关系、pending writes幂等/去重、记录持久提交/文件锁、尾部半行或损坏处理、格式版本及跨进程/审批/失败恢复测试。框架serializer可能产生带类型二进制，JSONL外层可读不代表payload天然是简洁对话文本。上述成本属于文件适配器，不是LangGraph本身无法支持JSONL。

Claude Code官方sessions文档明确本地transcript默认JSONL、支持从transcript恢复，并说明条目包含message/tool use/metadata且内部格式会随版本变化；只据此确认产品功能/文件格式，不推断其全部内部恢复实现或使用LangGraph。Pi则有已核对的SessionManager JSONL条目→分支路径→模型上下文重建源码。来源：https://code.claude.com/docs/en/sessions#where-transcripts-are-stored 。

SQLite在LangGraph生态是官方提供的本地轻量同步选择，不能据此说“所有Agent多数使用SQLite”；目前没有代表性统计。保持推荐的依据是本项目已选LangGraph、学习预算和复用维护成本，而不是大众程度。若用户优先可读/可携带会话格式，可以另行评估完整JSONL saver；目前不实现。将来如需导出，SQLite为唯一恢复来源、JSONL作导出/展示产物也是可选项，未授权前不在首片增加双写。
