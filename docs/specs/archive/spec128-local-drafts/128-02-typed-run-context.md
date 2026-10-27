# Spec128-02：按生命周期分层的运行上下文

日期：2026-10-22。状态：合同草案，尚未实施。与[128-01](128-01-project-execution-root.md)分别迁移和验收；不包含目录搬迁、门面组合或mixin重写。

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

回归覆盖源代码、脚本与测试的旧访问清理，不留无消费者兼容桥；保留实际需要的callback桥。测试fixture变更只能迁移正式类型构造，保留原行为、顺序和异常断言。完成后按项目标准运行全测试/构建；本草案尚无新实现验收结果。
