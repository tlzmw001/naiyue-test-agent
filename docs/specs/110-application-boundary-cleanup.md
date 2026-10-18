# Spec110：应用边界与持久化装配清理

日期：2026-10-18。状态：**R1—R9已实施并通过离线验收；用户已授权本批提交/push，结果以Git记录为准**。

用户先确认并记录单会话转发、公开durability、客户端准备和usage关闭归属，随后授权本批与补充R5—R9一起实施。本文是Spec105会话行为归属迁移之后的收敛项，沿用Spec104并发准入、Spec101冷历史查询和现有应用生命周期合同。第1—10节保留设计依据与实施合同；当前入口和实际验收见第11节。

已确认的四项：R1单会话路由收敛（第1—5节）、R2根图公开durability（第6节）、R3客户端准备方法（第7节）、R4 usage关闭归属（第8节）。用户随后授权一并实施本批清理及R5—R9（第10节）；两个应用工厂暂不抽_prepare_shared。

## 1. 当前问题与数据流

当前单会话控制链：CLI线程桥或Web的RunDelivery.control → AsyncApplication.steer(session_id, text) → SessionService.steer(session_id, text) → AgentSession.steer(text)。CLI与Web是两条入口；当前Web控制不经过CLI桥或WebService.steer。

105已让AgentSession拥有本会话状态和行为，但Service仍有多处按sid查找后同名转发。新增单会话操作会重复维护应用入口、Service路由和拥有者方法，阅读时也要多跳一层。

短方法数量不是删除清单：Service.steer/follow_up还有文本校验；AsyncApplication部分方法承担事件循环、操作生命周期和阻塞IO保护；AsyncPersistentApplication的13个短查询/操作方法均带生命周期保护。

## 2. 已确认的职责边界

| 位置 | 保留或调整 |
|---|---|
| AsyncApplication | 保留CLI/Web实际使用的公开接口；内部单会话操作受控取得AgentSession后调用其行为，保留原有检查、run_blocking、await_settled及_owned_operation |
| SessionService | 保留创建/装载/登记/移除、共享定义、原子准入/并发名额、登记状态与删除协调、共享角色解析及9个SessionPort路由；按实际消费者移除重复的单会话转发 |
| AgentSession | 继续拥有本会话控制、配置、审批及执行行为；原Service校验必须迁移到存续入口，保持异常合同 |
| SessionPort/Runtime | 仍仅声明已有9项能力；Runtime不得import application，也不得取得完整AgentSession或可变State |
| 持久化与冷查询 | 历史、聊天分页、用量、诊断等按现有存储入口查询，不因路由清理自动装载会话 |
| CLI/Web | 继续使用公开应用接口；CLI线程桥、HTTP权限/请求与回执检查按实际合同保留 |

Spec105第4节中“不加入通用get_session”约束的是Runtime/SessionPort边界，允许应用层内部受控查找拥有者。不得将内部查找变成对CLI/Web暴露的app.get_session API。

## 3. 内部查找与调用合同

可采用内部SessionService.require(session_id) → AgentSession，最终命名随现有代码确定。这不是对外支持接口，也不是SessionPort新增能力。

- 只在现有登记短锁内查找已装载对象，未知sid沿用现有异常；不创建、恢复或复制会话，不执行数据库IO。
- 查找返回引用不代表锁持续覆盖后续操作。会话状态锁、应用操作保护、删除协调和准入锁继续承担各自合同，不因取得对象而旁路。
- 消费者按当前操作取得对象；不新增拥有者缓存、服务定位器、动态__getattr__或按字符串分派的通用转发器。
- 应用内部单会话方法可以直接调用拥有者；arun/compact/审批恢复仍经Service原子准入，不能直接调用拥有者执行体绕过名额。
- 逐项迁移生产、持久化、context、测试和探针消费者；无消费者的旧Service方法同步删除，不保留仅供旧测试使用的别名。

## 4. 正常与失败链

正常例：运行中的会话A收到steering → 入口完成原检查 → 应用查找到A → A在自己的锁内检查接收窗口、入队并发原控制观察 → 原运行在下一个消费时点取出消息。消息、run_id、事件配对及对会话B的隔离保持。

输入失败例：空白/非字符串steering继续抛原ValueError；有效文本遇到关闭的接收窗口继续抛SessionBusyError。当前直接绕过Service会丢失文本校验，因此必须迁移校验；同时非法文本与未知sid出现时的检查顺序也须核对并保留。

协调失败例：启动A时全局名额已满 → Service._admit在原协调锁内拒绝 → 不安装run、不调用模型/工具、不消耗审批决定。仅删除查询转发不应改变这条链。

生命周期例：配置提交已进入阻塞IO时应用关闭 → _owned_operation继续登记该操作 → 等待已开始IO收尾 → 再关闭共享客户端/存储。不能为了消除短方法删除该保护，或让冷历史读取变成恢复执行。

## 5. 预计影响与验收

实施前先列出候选方法和所有实际消费者。预计涉及application/async_application.py、session_controls.py、session_approval.py、session_subagents.py及必要的session_context/session_persistence调用；Service登记与准入方法、公开应用工厂签名、HTTP/SSE、DB格式、依赖与配置保持现有合同。

验收要求：

1. 给出至少steer和配置修改两条迁移前后真实调用链，说明检查、锁、IO及状态由谁负责；减少的是重复路由，不以删除约20个方法为硬指标。
2. 为迁移行为补回归测试，覆盖合法控制、空白/非字符串、未知sid、接收窗口关闭、错误身份/顺序及跨会话隔离，测试先红再绿。
3. 复用并保持tests/test_application_steering.py、test_application_follow_up.py、test_session_port.py、test_active_run_lifecycle.py、test_cross_session_concurrency.py和相关Web/持久化测试的原有行为断言；实际受影响文件全部纳入。
4. 显式检查Runtime仍只依赖原SessionPort；无新的对象/State泄漏、旧接口别名或无用途转发。
5. 全量测试、项目既有构建、分层/行数检查及git diff --check通过后，记录删除项和因实际职责保留的入口。

## 6. R2：根图通过公开参数指定durability

现状：application/async_persistent.py从langgraph._internal._constants导入CONFIG_KEY_DURABILITY，以agent.with_config的configurable注入sync；子图agent_runtime/subagents.py已在调用时传durability='sync'。本机LangGraph 1.2.12的Pregel.ainvoke和astream均声明公开durability参数。

已确认调整：持久化根运行沿现有调用链明确传入durability='sync'，删除私有常量导入及仅为该配置存在的with_config。普通内存应用保持原默认行为，不新增配置项、代理图或通用调用包装器。[官方Durability说明](https://reference.langchain.com/python/langgraph/types/Durability)：sync要求完成当前步骤保存后才进入下一步骤。

正常链：持久化根普通/工作区运行 → ainvoke_agent_run或现有资源调用入口 → ainvoke_with_message_observation → agent.astream(..., durability='sync')。无publisher直调、观察不支持/基线读取故障降级的agent.ainvoke也必须收到同一参数；根审批恢复的Command调用沿同样规则传递。子图现有公开参数保持。

失败链：当前步骤保存阻塞时下一步骤不能提前执行；保存失败仍按原异常/必要保存合同传播。不能删掉配置后依赖框架默认async模式，也不能因流式成功忽略fallback或resume路径。

本会话离线框架探针：两个简单节点配InMemorySaver，分别以ainvoke/astream比较现有私有配置与公开参数，共4次执行；最终值和保存/节点轨迹相同，均在first的checkpoint保存后进入second，0次模型请求。这只验证框架参数路径，不代替应用迁移后的测试。

预计影响：application/async_persistent.py、session_execution.py、agent_runtime/async_invocation.py、resource_invocation.py及相关调用方/测试；复用async_message_observation.py已有durability透传。实施前核对全部实际消费者，不扩大公开应用工厂/HTTP接口。

验收：先以确定性同步信号阻塞checkpoint保存，验证下一节点尚未启动，再释放并完成；覆盖正常流式、无publisher与观察降级、根审批恢复、保存异常、根子SQLite/停止/重开，保留异常身份和busy收尾。不得用只检查最终checkpoint存在的断言替代顺序合同。

## 7. R3：客户端准备方法与工厂装配边界

现状：build_async_local_application和open_async_persistent_application均调用catalog._prepare_async_clients，scripts/provider_acceptance_budget.py也有实际调用。它创建并登记由目录拥有的SDK传输资源，是跨模块使用的准备能力，当前私有命名与消费者不符。

已确认调整：改为明确的方法prepare_async_clients，迁移两个工厂、脚本、测试和当前文档中的入口说明，删除旧方法名，不保留兼容别名。仍是同步准备函数，异步工厂继续await run_blocking(catalog.prepare_async_clients)；不顺便重写适配器内部SDK资源处理或增加新依赖。

正常链：工厂取得catalog → 准备并登记拥有的客户端 → 装配应用 → 应用关闭时catalog.aclose关闭这些客户端。失败链：准备部分成功后异常或取消，原有acquire_blocking/await_settled与工厂清理继续关闭已取得资源，原始异常优先，次要关闭故障继续记录。

两个工厂暂不抽_prepare_shared：只有两处重复，且持久化路径穿插storage取得/工作区绑定、saver.setup和有限写入器启动；显式保留各自资源取得与清理作用域。不新增SharedResources/通用工厂上下文，不为减少两处代码增加资源转交平台。

验收复用tests/test_async_resources.py的取得取消、部分准备/装配失败清理、原异常和双应用传输隔离，并验证所有实际调用方已迁移。公开工厂签名、配置及供应商重试保持。

## 8. R4：usage的关闭由应用显式引用

现状：AsyncPersistentApplication._close_resources通过self._persistence._usage.aclose关闭写入器；SessionPersistence在afinish借用同一写入器执行aflush_run。应用已直接持有diagnostics，两种写入器的关闭引用路径不一致。

已确认调整：工厂将同一个usage显式交给应用管理关闭，同时交给SessionPersistence供运行收尾flush；不复制写入器或订阅。应用不再穿透persistence私有字段。不增加get_usage、aclose_usage等只转发的访问方法。

正常链：停止接收并等待运行/已接受操作settle → 维持原关闭合同，排空usage和diagnostics → 工厂退出saver作用域 → 关闭storage。目录客户端关闭与原异常优先级沿现有合同保持，不因本项调整随意重排。

失败链：应用构造前失败仍由工厂已有异步上下文清理写入器；usage关闭失败仍执行diagnostics清理；应用/上下文重复调用aclose保持幂等；关闭次异常不覆盖原运行/调用异常。

预计影响：async_persistent.py构造/关闭接线及直接依赖旧私有路径的测试；SessionPersistence仍保留实际需要的flush引用。验收覆盖关闭时在途事件、订阅解除、整段退出、重复关闭及失败清理；保留tests/test_async_stream_delivery.py原断开不取消、必要保存和订阅配对断言，仅迁移观察入口。

## 9. 实施约束与当前状态

实施时逐项形成可审阅的最小切片，先补失败测试再最小修改，保留原事件/异常/审批/取消/保存/关闭合同，并按现有全量测试、构建及diff检查验收。不得将两个工厂共用函数或其他功能自动纳入。

## 10. R5—R9：本轮补充实施合同

- R5配置归属：新增项目级settings.py，顶层ToolSettings/load_tool_settings迁为AppSettings/load_settings；UsagePersistenceSettings/DiagnosticSettings归此模块，工具预算模型留tools/settings.py。迁移application、web、脚本与测试的实际消费者，删除旧导出，不保留别名。create_builtin_tools只接BuiltinToolSettings及实际需要的process/output/progress配置，工具包不反向导入顶层配置。settings.toml位置、字段、默认值、严格类型、权限启动失败与预算局部回退规则保持，配置文件和依赖不改。
- R6观察构造：RunObservation增加显式runtime_errors/application_errors构造参数，未传时才构造默认策略。根准入直接传入当前快照策略，子观察传已有instance['errors']并保持原应用策略，不再构造后覆盖。策略快照的生成时点及绑定对象身份保持。
- R7并发校验：应用普通/审批恢复及低层AsyncBatchPlan共用最小专用正整数校验，继续拒绝bool；校验函数放已有工具批次模块，不建通用校验框架。应用仍在准入前验证，低层仍在原位置验证，异常文本和优先级保持。
- R8执行命名：AgentSession内部协作入口_arun改名arun，Service两处调用及实际测试迁移，旧名称删除；aresume_tool_approval保持。应用公开运行接口、准入和收尾归属不变。
- R9装配字段：SessionService构造时声明_system_prompt及独立的装配完成状态；assemble_runtime成功后才标记完成，保留原_assembled的“开始装配/禁止后绑持久化”意义。acompact在准入前检查完成状态，未完成时抛明确RuntimeError，不占busy或发布运行事件；合法None/空提示词仍按原框架合同支持。

R5正常链：CLI/Web加载同一份AppSettings → 工厂分配应用配置、具体工具预算 → 原工具/运行；失败链：权限错误仍阻止启动，预算错误仅原范围回退并记录。R6—R9正常链：构造完整观察/准入 → 明确会话执行入口 → 原finally收尾；失败链：非法并发或未装配压缩在准入前拒绝，策略描述及原异常身份保持。

新增测试先验证新配置入口/依赖边界、根子策略构造与身份、非法并发准入前拒绝、执行入口与装配失败状态；既有配置/权限/工具/CLI/Web及运行生命周期断言全部迁移保留。并行会话的前端和Web修改原样保留，只在必要的配置导入/工具装配处做局部接线。完成后更新当前入口文档和本Spec实际结果，不改历史课堂叙述。

## 11. 实际实现与验收（2026-10-18）

### 当前生产入口

| 项目 | 已接线位置与真实行为 |
|---|---|
| R1单会话路由 | AsyncApplication → SessionService.require(sid) → AgentSession；配置IO仍在_owned_operation内经run_blocking执行。SessionPersistence.astatus、context_view和approval seed读取迁至拥有者。Service保留登记/删除协调、原子准入和原9项SessionPort，Runtime没有require能力 |
| R2公开durability | AgentSession.arun/aresume_tool_approval按实际persistence确定sync，经原资源调用入口/ainvoke_agent_run传到消息观察的astream和所有ainvoke降级；普通内存应用不传该参数。手动compact仍用原aupdate_state必要保存 |
| R3客户端准备 | ModelCatalog.prepare_async_clients由两个应用工厂及provider_acceptance_budget调用；同步准备仍经run_blocking，取消/部分初始化清理保持 |
| R4 usage归属 | 持久化工厂将同一个usage交给AsyncPersistentApplication和SessionPersistence；应用直接关闭自己借用的usage引用，Persistence仅在运行finish执行flush；上下文退出的幂等清理保持 |
| R5配置归属 | 新增naiyue_test_agent/settings.py的AppSettings/load_settings，应用/CLI/Web/脚本均消费该入口；工具包保留具体预算模型，create_builtin_tools仅接BuiltinToolSettings及process/output/progress。原TOML语义保持 |
| R6策略构造 | RunObservation在构造时接收runtime_errors/application_errors，未传时才构造默认。根准入传运行快照，子观察传已有instance['errors']，对象身份和快照时点保持 |
| R7并发校验 | 现有agent_runtime/tool_execution/async_batch.py的validate_tool_concurrency由普通/审批恢复准入前和独立工具批次入口复用，仍拒绝bool，低层call_id检查顺序保持 |
| R8执行入口 | AgentSession.arun是明确的内部协作方法，Service普通/compact两处调用和测试已迁移；公开App运行接口保持 |
| R9装配字段 | Service构造时声明_system_prompt=None和_runtime_ready=False；原_assembled仍标记装配开始，成功建图后才设置提示词/ready。未成功装配的compact在准入前报RuntimeError，不占busy、不发run事件 |

正常例：CLI或Web steering → App先校验非空文本 → require返回同一个已装载对象 → AgentSession在本会话锁内入队 → 原Runtime经SessionPort在控制时点消费，主/子和其他会话隔离保持。配置例：App.aset_permission_mode → _owned_operation → require → run_blocking(owner.set_permission_mode) → 原持久化写入 → 本会话内存提交；关闭仍等待已开始的操作。

失败例：空白文本与未知sid同时出现，仍优先ValueError；有效文本/未知sid仍KeyError。非法工具并发和未完成装配的compact在准入前拒绝。持久化根保存未完成时下一节点不启动；保存错误及停止继续进入原finally/必要保存流程，原异常身份、审批决定和busy释放合同由现有回归覆盖。

### 删除项与保留项

- 删除20个重复单会话路由：describe_session、get_thread_id、snapshot_for_context、approval_seed_identity、select_model、select_thinking、get/set_permission_mode、get/set_active_tools、steer、follow_up、request_stop、subagent_status、pending_tool_approvals、pending_approval_batches、pending_subagent_approvals、submit_subagent_approval、prepare_root_approval_submission、validate_root_approval_stop；删除仅承载审批路由的SessionApprovalControls。原_session查找迁为Service.require，不保留旧别名。
- 删除ToolSettings/load_tool_settings旧聚合入口和工具包导出、_prepare_async_clients、AgentSession._arun、CONFIG_KEY_DURABILITY私有导入和专用with_config；生产/脚本无残余调用，测试仅保留明确验证旧名称缺席的断言。
- 保留公开AsyncApplication/持久化应用、CLI线程桥、应用关闭保护、跨会话准入/名额、9方法SessionPort、冷档案查询、既有saver/storage作用域。两个工厂继续显式装配，没有_prepare_shared、动态代理或新的服务/资源管理平台。
- tests/conftest.py只将自动配置发现的隔离目标迁至新settings模块，继续阻止读取开发机配置；旧入口测试/CLI工具集合替身按新输入接线迁移，原顺序、结果、异常和状态断言保持。

### 验证命令与实际结果

| 验证 | 命令 | 实际结果 |
|---|---|---|
| 新增边界与durability | `.venv/bin/python -m pytest -q tests/test_application_boundary_cleanup.py tests/test_root_durability.py` | 40 passed；首批24红/8绿，durability另8项先红后绿 |
| 后端全量 | `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2865 passed in119.83s；日志/tmp/naiyue-110-backend-final.log |
| 前端当前工作树全量 | `npm --prefix frontend test -- --run` | 35文件280 passed；日志/tmp/naiyue-110-frontend.log |
| 现有构建 | `npm --prefix frontend run build` | TypeScript成功，Vite523模块成功；日志/tmp/naiyue-110-build.log |
| Python语法/依赖 | `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`；`.venv/bin/python -m pip check` | 成功；No broken requirements found |
| 结构与差异 | AST、生产/测试/脚本500行、拥有者类型注解、工具包依赖及`git diff --check` | 438份Python通过，无超过500行文件；旧生产/脚本入口残余为0 |

首次全量发现run_blocking持有的4个旧方法引用及旧测试入口/替身，已按真实消费者迁移。未带既有fd路径时发现搜索工具环境缺失；核对原交接后使用已有~/.pi/agent/bin/fd 10.5.0，仅给测试命令设置PATH。没有安装程序、修改真实配置、放宽断言、跳过测试或更改工具语义。

Deep架构与安全两项独立只读审查均无确认发现，分别实际复跑91项/136项通过。主代理补做逆向检查：不接受bool、未知sid/非法文本优先级、未装配/装配失败后compact不占名额、两个独立应用资源及关闭、冷历史不装载、主子保存隔离；对应确定性测试和既有全量均通过。

证据边界：0次真实供应商请求，未执行实网验收或更改用户SQLite，未提交/push。前端结果覆盖并行Spec103当前成果；本批未实现其UI功能，既有Vite大chunk提示保留。当前主链已更新，课堂进度和用户掌握程度不作新增声明。
