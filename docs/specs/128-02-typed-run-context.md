# Spec128-02：按生命周期分层的运行上下文

交付编排说明：本节点从已保存合同和验收文档整理，移除了后续目录迁移注记；不是原始完整文档历史的精确快照。

日期：2026-10-22。状态：已完成本机验收，未提交或发布。与[128-01](128-01-project-execution-root.md)分别迁移和验收；不包含目录搬迁、门面组合或mixin重写。

## 1. 当前问题与目标

应用RunObservation绑定RunServices，make_invocation_context再将字段复制为扁平字符串键，工具和middleware读取；RunContext漏声明实际能力，且Callable缺少精确签名。memory当前包含显式写入，不能再把新接口命名为仅只读能力。

目标是让身份、能力、观察和各类临时数据拥有明确类型及生命周期，保留create_agent、现有工具JSON参数、事件、异常、权限与保存合同。本机既有LangChain1.4.2/LangGraph1.2.12已支持泛型ToolRuntime和dataclass context，不升级依赖、不新增服务容器或框架。

## 2. 三层合同与必要内部时点

| 用户确认的生命周期 | 数据 | 规则 |
| --- | --- | --- |
| 整个run | session/run/agent与父根关联、项目/执行身份、能力、观察引用、控制/错误策略 | 类型化只读；父子分别构造，不能只换agent_path复用根对象 |
| 每次模型调用 | 准备结果、工具视图、角色描述、模型call身份与批次计划 | 显式临时对象；新轮重置，不存在middleware或共享RunScope里 |
| 每次工具调用 | 执行许可、许可规则/模式及必要局部评审数据 | 继续复制context后注入；同批工具不共享可变许可 |

第二层须区分一次图invoke/resume、模型轮次和工具批次的实际时点，不新增产品层级：审批恢复可能跳过before_model而直接重建待执行工具批次，所以临时容器允许model_call为空。批次计划属于本次invoke的当前批，同批共享一份，不同批/根子/会话不能共享。

“只读”指身份与依赖引用不能重新绑定；observer、RunControl和一次性grant仍按各自合同记录/消费内部状态，不能复制后丢失身份或计数。

## 3. 拟定类型与访问

```text
RuntimeContext
  scope: RunScope（冻结）
    identity: RunIdentity（冻结）
    capabilities: RunCapabilities（冻结，精确Protocol/Callable）
    observation: RunObservationPorts（冻结引用）
    archive_root（应用派生的冻结工具输出归档位置，与执行目录分开）
    control / errors / 当前run只读输入
  invocation: InvocationState（本次图调用独占）
    model_call: ModelCallState | None
    tool_batch: AsyncBatchPlan | None
    child_model_failure: 当前子调用错误记录 | None
  invocation_inputs: 审批grant、只读资源根、并发上限等明确字段
  authorization: 当前工具调用的局部许可 | None
```

类型名称是拟定接口，不表示文件已存在。运行身份只携带实际需要的项目/执行信息，不向工具传整个Project数据库对象或Application。能力对象只有当前消费者需要的subagent/process/history/memory接口；观察对象只提供既有publisher/observer。

本片采用dataclass外层，工具以 `ToolRuntime[RuntimeContext]` 声明类型，通过明确属性访问。逐工具用 `replace(context, authorization=...)` 再 `replace(runtime, context=local)`，保留原来的复制后注入合同。不可用模拟所有旧字符串键的动态代理隐藏迁移；原TypedDict及旧字段读取在消费者迁移后删除。不是深复制整个context：同批plan与只读scope保留身份，只有当前工具的authorization不同。

## 4. 现有字段迁移清单

| 现有字段 | 归属 |
| --- | --- |
| session_id/run_id/agent_path/parent_tool_call_id/parent_run_id/root_run_id | RunIdentity |
| workspace_root | 128-01执行身份映射；不再兼作项目数据范围 |
| archive_root | RunScope的冻结归档位置输入，由应用按既有data_dir及项目/临时会话归属派生，不新增用户必填配置；与ExecutionRoot/Shell cwd分开，根/子、审批恢复及MCP消费者同步迁移。后台任务接收后保存到自身记录，不依赖已结束run的scope |
| ainvoke_subagent/alist_subagents/_process_tasks/_task_queries/aquery_history/aquery_memory | RunCapabilities；task_queries是并行memory04—06加入的统一任务查询能力 |
| _run_event_publisher/_turn_observer/_control_observer/_context_observer/_tool_progress_observer | RunObservationPorts |
| _run_control/_failure_policy | RunScope的执行控制与错误策略；不能算普通观察 |
| _background_notices | 根run精确scope下的冻结快照；是否已进入模型提示的确认属于model call |
| _naiyue_model_call_id/_naiyue_model_id/_naiyue_prepared_request | ModelCallState |
| _naiyue_tool_view/_naiyue_subagent_roles | 当前请求准备的固定视图 |
| _naiyue_tool_batch_plan | 当前invoke中的批次状态，按批重建 |
| approval_grants/read_resource_roots/_max_concurrency/_human_review_messages | 本次invoke的明确输入 |
| _subagent_new_delegation/_subagent_model_id/_subagent_model_failure | 子调用输入或临时状态，不外泄到父scope |
| _execution_permit/_permission_rules/_session_allow/_permission_mode/_review_call_id | 每工具/评审局部context |

实施前再搜索全部读写和正式注册hook，不能仅迁移表内已发现字段。由框架回调读取的能力也属于消费者，不以静态直接调用数判断可删除。

## 5. 正常与失败链

正常：应用从可信session/child构造scope→bind→创建独立invoke临时状态→before_model开始新ModelCallState→准备固定模型/工具/提示→主模型→许可/审批→创建批次plan→逐工具复制context并注入许可→ToolMessage回填→下一轮新ModelCallState→必要保存/收尾→reset绑定。

准备失败：新轮先清除旧准备状态再执行；失败不能让后续消费者读上一轮prepared。普通观察失败按既有诊断降级，执行/控制/必要保存异常不被隔离掉，原异常身份保持。

审批：首次permission→interrupt时尚未创建plan；恢复建立新的invoke状态，model_call可以为空，再根据保存的tool_execution names/calls完成approval→plan→tools。本批不重复主模型、不重复消费批准、不从上一run借plan。

溢出恢复：在同一个模型wrapper内重试内层handler，不重新走before_model；保留外层逻辑call身份和已固定模型/工具。SDK用量invocation_id仍逐实际调用产生，内部summary/Auto不得改外层准备状态。

并发：同批工具共享唯一AsyncBatchPlan，每个工具持有独立授权context；两个session可共享编译图，但scope、invoke状态和plan分别属于自己的运行。子调用有独立run/control/publisher/observer，退出后恢复父绑定。

后台通知：只有确认该轮prepared实际包含通知且符合现有成功消费条件才能ack；持有scope.notices不能直接证明已送达模型。

## 6. ContextVar与接线约束

当前仍有SDK用量callback、子checkpoint错误标注、MCP借用范围校验、运行内禁止关闭自身和子执行/审批恢复等消费者没有ToolRuntime参数。单个ContextVar可以作为窄桥保留并绑定同一类型化scope；不能仅因工具改为属性访问就删除。

同一个scope从应用进入显式context和callback桥，避免重新维护一份扁平字段副本。保留token/reset、异常退出和嵌套父子恢复；独立后台任务仍用现有干净Context，不继承结束run的publisher或控制对象。

scope可信身份不依赖publisher是否存在。observer必须与目标publisher对象对应，不能只比较文本ID后接入外来observer。缺能力要有明确不可用反馈，类型本身不是工具授权。

## 7. 改动与验证

预计修改run_services/invocation_context/运行类型声明、run_events/执行与子执行绑定、model selection/request preparation/usage callback/summary/recovery、工具批次/许可/Auto、history/memory/subagent/process工具及MCP能力消费者。不改依赖、工具公开schema、HTTP协议、存储业务含义或CLI线程桥。

优先迁移现有测试并保留断言：

- run_services：嵌套reset、跨session/run拒绝、独立线程隔离、精确根通知scope；补冻结引用和能力签名契约。
- request_preparation：多轮重置、准备失败、摘要期间配置改变只影响下一轮、停止与恢复。
- tool_batch_scheduling：共享图的两个会话各有plan，同批共享、批次完成不重放。
- runtime_middleware_contracts：审批前不执行，恢复可无model prepared，恢复后正确建plan和消费许可。
- subagent_batch_resume/scopes：同名工具call_id不串批准、父子恢复、并发上限、原始人类输入和错误身份。
- history/memory：沿正式工具许可与应用当前状态重验，覆盖当前写入能力和写前来源保存。
- 后台通知、MCP、关闭、用量与checkpoint故障：所有ContextVar实际消费者保持归属与生命周期。

补直接隔离断言：两个并行工具共享同批plan但各自看到独立permit，共享context没有permit；不同根/子/恢复invoke不共享临时容器。只检查最终输出不足以证明这些边界。

回归覆盖源代码、脚本与测试的旧访问清理，不留无消费者兼容桥；保留实际需要的callback桥。测试fixture变更只能迁移正式类型构造，保留原行为、顺序和异常断言。完成后按项目标准运行全测试/构建；当前实现与验收结果见第9、10节。

## 8. 实施合同（2026-10-23，用户已允许）

沿用01分支和现有未提交成果，先保存01源码快照至build/spec128-02/baseline，单独核对02差异。共享dataclass合同放contracts/run_context.py，能力Protocol放contracts/run_ports.py，不从tools导入应用装配或runtime建图入口。RunServices由RunScope替换，run_scope.py保留唯一ContextVar及token/reset；旧run_services模块和RunContext TypedDict在消费者迁完后删除，不提供字符串兼容代理。

RunIdentity保存session/run/agent及父根关联、project_id和ExecutionRoot；execution_path为派生属性。RunScope冻结identity/capabilities/observation/control/errors/archive_root/notices引用。make_invocation_context直接复用已绑定scope并检查session/run/agent/path匹配；无应用绑定的底层调用创建明确的最小scope。外来scope身份直接拒绝，不依赖publisher存在与否；观察器引用必须与publisher对象配对。

RuntimeContext冻结外层引用；InvocationState每次invoke/resume新建，包含当前ModelCallState、AsyncBatchPlan和子模型失败。ModelCallState每轮先重建后准备，工具视图/角色属于当前模型轮；准备完成的PreparedRequest具有明确字段。审批恢复允许model_call为空，批次从保存tool_execution重建。InvocationInputs冻结本次grants/读取根/并发/人类评审消息/子调用输入。ToolAuthorization冻结局部permit/规则/模式/remembered grants/评审call引用，授权middleware以replace复制外层context，不深复制scope、plan或一次性grants。

生产工具统一ToolRuntime[RuntimeContext]及属性访问；工具参数JSON、公开HTTP、DB、CLI桥、框架hook顺序不变。应用自身执行目录校验直接传已知ExecutionRoot/project_id，不为调用校验器伪造字典context。测试/探针迁移到正式类型构造，保留原断言；本片不引入静态检查器依赖。最终检查旧键/导入/包装及所有callback实际消费者，再跑完整测试/构建/源码外安装。

## 9. 当前实现与实际主链（2026-10-23）

02已接入当前分支，验收结果见下节；第3节是设计时的类型形状，实际字段名为 `RuntimeContext.inputs`。共享数据合同在 `contracts/run_context.py`；能力、控制、错误策略和批次接口在 `contracts/run_ports.py`，观察接口在 `contracts/run_observation_ports.py`。接口声明不导入运行层或应用层；原类仍在各自负责层实现行为，没有另建观察投递或控制执行框架。

以会话S、运行R、执行目录E的一轮工具请求为例：

```text
AgentSession.arun
  → RunObservation.bind(identity=RunIdentity(S,R,project_id=P,execution_root=E))
  → ainvoke_agent_run → make_invocation_context（同一个scope；独立InvocationState）
  → _ModelSelectionMiddleware.abefore_model（先新建ModelCallState，清空旧plan）
  → prepare_request（固定model/tools/prompt，完成后保存PreparedRequest）
  → permission/approval → ToolBatchMiddleware.aafter_model（本批新建AsyncBatchPlan）
  → ExecutionAuthorizationMiddleware（消费grant；replace产生局部ToolAuthorization）
  → 具体工具（读取scope能力和执行身份）→ ToolMessage → 下一模型轮
  → 原有保存及观察收尾 → ContextVar token/reset
```

`RunScope`、身份、能力、观察器组和工具授权均冻结外层引用；控制器、能力服务和观察器仍按自身职责维护内部状态。两个工具的context外壳和授权不同，同批次的scope、InvocationState和AsyncBatchPlan保留对象身份；不同会话及子调用不共享InvocationState。请求准备失败发生在新建ModelCallState之后，不会重新使用上一轮prepared或plan。

审批恢复由 `aresume_tool_approval` 进入新的invoke：新context起初没有model_call和plan，直接使用已保存的tool_execution重建本批工具选择及计划，授权阶段继续消费同一个一次性grant对象；本批工具执行前不重做主模型请求。子调用 `ainvoke_subagent_step` 使用自己的完整RunIdentity和scope，独立输入与临时状态，退出后恢复父绑定。评审人类消息明确为 `tuple[HumanReviewMessage,...]`，每项为sequence/id/content记录；`prepare_review_input`显式复制为list，预算裁剪不修改输入。

无ToolRuntime的SDK用量回调、MCP范围检查、子checkpoint错误和运行内关闭保护沿 `run_scope.py` 唯一桥读取同一个scope。独立后台任务继续使用干净Context；后台管理器直接校验已经给定的ExecutionRoot和归档路径，不为校验函数伪造工具context。错误策略按声明的端口调用，仅在None时采用默认策略，不静默丢弃合法端口。已保存审批的重验使用root_from_row保留原execution_root_kind及project_id，不能从路径重新构造Local；未登记worktree的恢复重验明确拒绝且不写库或安装会话。

正式工具为 `ToolRuntime[RuntimeContext]`。Pydantic隐藏参数只接受已构造的RuntimeContext实例，不重建服务/控制器/观察器，也不接受旧字典；序列化不遍历这些活对象。`build_tool_schemas`在内部dump保留runtime字段名并将其值转为None，框架仍使用原始验证实例调用工具，公开输入schema不出现runtime。自生成schema和正式工具schema均有实例身份测试。

已删除RunServices及旧run_services模块、RunContext TypedDict、扁平键的复制和读取。保留的run_scope桥有上述真实消费者。测试共用 `runtime_context_helpers.py` 明确构造正式类型，不接受旧字符串键或提供映射兼容；修改夹具保留权限、异常身份、顺序、停止、事件和结果断言。跨身份旧行为从忽略外来观察器改为在invoke入口明确拒绝，符合第8节合同。

本片未改变依赖、版本、公开工具参数、HTTP/存储协议、CLI事件循环派发或Web幂等/错误映射；未实施领域搬包、仓储拆分、应用门面组合和会话mixin替换。01的执行目录失效、个人记忆、临时会话与外部归档合同仍适用。没有新增产品worktree创建/登记/附着能力。

## 10. 验收记录

最终后端命令：

```bash
PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" \
.venv/bin/python -m pytest tests -q --tb=short --maxfail=10
```

实际结果：**4686 passed in305.92s**。前端 `npm test -- --run` 为50文件423项通过，`npm run build` 的TypeScript/Vite通过。新回归直接核对同批plan与scope引用、逐工具许可隔离、两个同时运行会话的隔离、审批恢复无model_call、准备失败清空旧状态、评审输入裁剪、端口错误策略和恢复审批保留执行目录kind。原有序列/异常/权限/数据及存储回归保持；此前失败日志保留。

Mac ARM64标准wheel、Twine与源码外干净安装通过；211份Python源码和4份前端资源与当前源码逐字节一致，wheel不包含旧run_services模块。安装覆盖CLI/Web、原生工具、Read→Edit→Read、审批恢复、SQLite/历史重开、MCP（8次合成调用及进程退出）、后台任务、子实例续跑和memory后台主链；0真实供应商请求。Python语法、生产文件500行限制、compileall、pip check和git diff --check通过，801份冻结源码无漂移。

只读架构/安全复核与确定性测试发现的人类评审数据形状、能力签名、观察端口、错误策略及恢复身份映射问题均已修正；结果不等于整体安全或真实业务质量证明。未做其他平台原机或真实模型验收，未新增静态检查器依赖。

证据目录为 `build/spec128-02/`：总记录verification.json、backend-verified.log、frontend-tests.log、frontend-build.log、artifact-verification.json、install-verified.json及review-findings.json。01源码快照保存在baseline，02差异见changes-vs-01.diff；01原证据保留在 `build/spec128-01/`。未提交/push/发布。唯一下一步：讲解并确认03领域分包与CLI适配器迁移，批准前不开始实现。
