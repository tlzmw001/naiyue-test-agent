# Spec085-02：工具目录归属与显式依赖

版本边界：下文保留该片实施时点；Bash旧Unknown/私有runner/线程及名字保护已在[085-12](085-12-process-lifecycle.md)迁移删除。当前下一步为[085-13搜索](085-13-search-tools.md)审阅，不能据本片历史下一步判断现状。

日期：2026-10-16。状态：**用户授权后已实施并验证。** 接续已完成的[085-01](085-01-unified-tool-errors.md)，属于[总Spec085第1片](085-tool-system-rebuild.md#15-实施顺序与验收入口)的第二小步。本片经用户审阅并明确要求“实现吧”后完成；下文保留实施合同，实际结果见第8节。

## 1. 本步解决什么

当前read/write等能力放在agent_runtime/tools，工具又从运行层取权限描述；Bash直接读取RunServices。与此同时call_observation.py同时包含模型、工具和HITL观察。先按已确认职责迁移，让后续ToolSpec/Registry与权限适配在准确位置接入。

目标：独立tools包拥有具体能力、工具权限事实及现有授权凭据；agent_runtime/tool_execution拥有调度、观察、HITL和错误转换。工具可依赖LangChain ToolRuntime/BaseTool，但不反向import本项目agent_runtime/application。应用同时装配两者。

这一步只调整模块归属和Bash停止依赖的传入方式。工具参数、工厂返回BaseTool、IO算法、权限判断、整批审批、串并行、085-01错误反馈、会话存储及Web线协议保持；ToolSpec/settings/动态工具和新权限管线不提前实现。

```text
application / web
  ├─ tools：工厂、文件IO、现有Bash、权限声明/一次性许可
  └─ agent_runtime：图、模型、控制与tool_execution适配
        └─ ToolRuntime.context：将本次运行的可信依赖显式交给工具
```

## 2. 文件准确迁到哪里

下表均相对naiyue_test_agent；旧位置在同一步迁完消费者后删除，不留import转发包。

| 当前 | 目标 | 本步处理 |
|---|---|---|
| agent_runtime/tools/__init__.py | tools/__init__.py | 导出当前真实工具工厂；暂不导出未实现的ToolSpec/Registry |
| agent_runtime/tools/read.py、ls.py、find.py、grep.py、write.py、edit.py、bash.py | tools下同名7文件 | 原函数/类迁移；工具内改相对import；Bash另按第3节去运行层依赖 |
| agent_runtime/tools/bash_output.py | tools/output.py | 按总Spec确定的最终文件名迁；原归档/preview/clear代码及受管文件格式保持 |
| agent_runtime/tool_approval.py + file_permissions.py | tools/permissions.py | 合并现有声明、review、匹配回调和OneUseApprovalGrants；约165行，不新增legacy模块或重写策略 |
| agent_runtime/tool_batch.py | agent_runtime/tool_execution/batch.py | 移动ToolBatchMiddleware/_SequentialPlan，修import，原算法保持 |
| agent_runtime/async_tool_batch.py | agent_runtime/tool_execution/async_batch.py | 移动AsyncBatchPlan，保持取消/顺序/并发/失败语义 |
| call_observation.py中的工具部分 | agent_runtime/tool_execution/observation.py | 移_prepare_tool_call、_ToolCallObservationMiddleware、_record_tool_call、tool_guard_reason、observe/aobserve_tool_dispatch |
| call_observation.py中的ObservedHumanInTheLoopMiddleware | agent_runtime/tool_execution/approval.py | 只搬当前HITL wrapper；类名、hook、停止guard、拒绝观察保持，不提前改为新的BatchApprovalMiddleware |

既有tool_execution/feedback.py继续使用，Bash异常改从tools.bash导入。tool_execution/__init__.py只导出装配实际使用的适配项，不再导出具体文件工具。

call_observation.py保留make_model_selection_middleware、_ModelSelectionMiddleware，以及event_label/token_count/_emit这些已有公共观察小函数。模型、工具观察和HITL共用原函数，不复制、不新建utils文件；新observation/approval可向该模块读取它们，模型模块不反向导入新工具观察，避免循环。logger沿用原运行层日志名，事件名/载荷及middleware.name保持。

tools/permissions.py此阶段仍有ApprovalPolicy、ContextualApprovalPolicy、ApprovalReview、attach_approval、approval_policies_for_tools、review_path、FileAccessRequest/Decision、attach_file_permission_policy、decide_file_access及OneUseApprovalGrants。它们是正在使用的生产逻辑，不是旧接口兼容层；真正的新权限片替代后再删除。现有ReadOnlyApprovalGrants/BashApprovalGrants有实际消费者，本步不顺带改其语义。

resolve_workspace_root与_has_disallowed_hidden_part暂仍在tools/read.py，Write/Edit保留现有共享关系；paths.py的真实目标/软链接能力由文件片引入，不提前抽新类或增加Operations。

## 3. 唯一需要改参数传递的地方：Bash停止对象

当前_run_host_command内部做：

```python
services = current_run_services()
control = services.control if services is not None else None
```

搬迁后若照抄，会使独立tools反向依赖runtime。已有make_invocation_context已把同一个control放进context['_run_control']，直接复用这条接线。

拟实现的变化：

```python
# tools/bash.py：现有工具函数内
exit_code, stdout, stderr, truncated, archived = _run_host_command(
    command, root, timeout or _DEFAULT_TIMEOUT,
    control=context.get('_run_control'),
)

```

同文件原私有函数的签名改为`_run_host_command(command, root, timeout, *, control=None)`，原类型注解保留；删获取services/control的两行，原control.requested/control.signal检查及后续进程正文保持。工具不import RunControl具体类、不新建控制对象或ContextVar。该函数当前只有Bash工具一个生产调用方，直接传参足够，不新增端口类、回调工厂或后端注册器。

RunServices及bind/current_run_services继续服务应用/运行层；make_invocation_context仍负责来源匹配、跨线程上下文和现有字段投影，不删除它们，不改停止、环境变量、Popen或双流线程算法。

## 4. 装配、正常/失败链如何变化

当前_build_runtime_agent从runtime根模块取ToolBatchMiddleware，并从call_observation取模型/工具/HITL适配。改为：模型仍从call_observation取；batch、工具观察、HITL分别从tool_execution的对应模块取，原middleware列表顺序保持。

```text
App已有装配
 → create_session_read_tool：现在位于tools/read.py，仍返回原BaseTool
 → compiled Agent
 → tool_execution.batch：原批次计划
 → tool_execution.observation：原guard/日志/await_settled
 → 官方ToolError + 原feedback回调
 → tools.read._read：原IO
 → 原ToolMessage/观察 → 下一模型请求
```

Read非法UTF-8仍由_read抛具体ToolException→公共error消息；意外普通异常仍按085-01转换，计划可继续。外部目录仍由当前ObservedHITL暂停，SessionService经同一review/一次性grant恢复；只改其定义import位置，不新增授权或重复执行。

```text
用户停止Bash
 → SessionService请求本run的原RunControl
 → 已有RunServices → make_invocation_context → ToolRuntime.context
 → 工具显式把同一个control传给_run_host_command
 → 原signal/进程组终止/采集收尾
 → 原BashOutcomeUnknown及部分输出/会话保护
```

停止时不能换control、丢signal或改成普通ToolException。调度错误、HITL、取消、保存故障继续原所属层传播；本步不修已记录“拒绝唯一sequential后同步plan缺失”的权限片问题，也不移除60秒排队配置。

移动class后其__module__会变化，但类名/middleware.name及节点名保持；不设置伪旧module来兼容。现有可恢复ready会话主要保存消息/图值，仍以真实SQLite回归证明可读；没有新增DB schema/日志格式迁移，不能据静态判断跳过持久化验证。运行中/待审批任务本就不支持跨版本重启恢复。

## 5. 消费者及删除边界

当前AST静态统计命中旧import或待迁工具观察符号：22生产文件、41测试文件、3脚本；另有output/async_batch等被移动文件本身。因此涉及超过8生产文件，主体是import与模块归属迁移，不能只搬7个工具就宣称完成。

- 生产：runtime装配/errors/feedback/call_observation；application的内存/异步/持久装配、SessionService、CLI、skills、session_persistence；web/app工具装配。全部改新路径，App公开run/resume签名不变。
- 测试：工具、权限注册、审批恢复、Bash输出/停止、批次、事件、分类、持久化、CLI及测试helper同步改import和monkeypatch真实定义处。模型观察的monkeypatch仍指call_observation；工具观察注入指新observation，不保留旧名字转发。
- 脚本：probe_live_workspace_tools.py、probe_message_state_boundaries.py、probe_skill_loading.py改新import，运行真实模型仍需另行授权，本次迁源码不执行实网探针。
- 文档：README当前入口、learning-roadmap当前源码表、handoff和Spec文件地图同步新路径；历史lesson/Spec保留当时实现并加明确版本指引，不批量重写学习记录或关键知识点。

迁移完成删除旧agent_runtime/tools目录、tool_approval.py、file_permissions.py、tool_batch.py、async_tool_batch.py，以及call_observation已迁职责的旧定义。根__init__因实际装配仍import_record_tool_call，不把“仍可从根访问”误判为必须新建兼容包装；消费者直接迁到实际新入口。

保留有真实消费者的固定workspace工厂和审批metadata到对应注册/权限片；新旧模块不双轨。受管输出目录、owner标记、文件名、旧日志读取/清理均保持，不搬用户文件或删除数据。

## 6. 验收与实施顺序

新增tests/test_tool_package_boundaries.py：

1. 新tools包/各子模块可以导入；独立Python进程import工具包后不加载本项目agent_runtime/application，AST同时检查绝对/相对import方向。
2. 工具返回schema/name、现有model-visible参数、middleware.name与实际调用轨迹保持；模型/工具/HITL分别只有一个真实装配入口。
3. Bash函数传给runner的control与context原对象同一；复用真实批准/运行/停止测试确认进程结束、部分输出、未重复执行及未知状态保护。
4. 旧生产入口文件移除，生产/scripts/tests不再导入旧路径；不只断言模块文件存在，不提供转发壳让旧测试通过。

扩展既有test_layered_failure_contract的分层导入检查，并保留085-01全部27测试、原工具IO/审批/串并行/控制/事件/保存断言。纯模块重定位不改变业务预期；新依赖边界先写测试观察失败，再搬迁接线，不因迁移删行为测试或放宽断言。

实施顺序：新边界失败测试→移动工具并合并权限描述、显式传Bash control→移动batch/工具观察/HITL并按原顺序装配→同步全部消费者→删除旧路径与无消费者导入→定向/全量验证。导入暂时断裂时先恢复正确接线，构建红灯下不叠加后片功能。

获准后至少执行：

```sh
.venv/bin/python -m pytest -q tests/test_tool_package_boundaries.py tests/test_layered_failure_contract.py tests/test_tool_error_feedback.py tests/test_async_builtin_io.py tests/test_active_stop_bash.py tests/test_host_bash.py tests/test_tool_batch_scheduling.py tests/test_async_tool_batch.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

另搜旧import/monkeypatch/导出，核对全部修改生产文件≤500行及当前文档链接。不改变依赖/真实配置/数据库，不请求外部模型或提交发布；Windows执行另行验收。实际执行记录见第8节。

## 7. 核对依据

本机已完成085-01的工作区是当前事实，1097/前端27是上一片验证结果，不当作本片通过。本轮追踪了工具→旧权限描述、Bash→RunServices→context、runtime装配→batch/观察/HITL及应用/脚本/测试消费者。

Pi a69bef789的createReadToolDefinition将能力定义及execute放工具模块，agent-loop.executePreparedToolCall负责执行边界；AgentX 3fcfa0e的内置Provider分define/create与execute/doExecute。这只支持按职责分工，不要求复刻其目录或Java/TS实现。当前LangChain源码确认默认middleware.name取类名，本步类名保持；不升级框架、不改已验证hook机制。

## 8. 实施结果与当前入口

2026-10-16按用户授权完成上述迁移。新tools包10个文件（含合并permissions），tool_execution承接batch/async_batch/observation/approval，原feedback接新异常位置；旧tools目录和四个旧runtime模块已移除，无转发壳。call_observation只保留模型与原公共观察函数。Bash从context取同一个control显式传给runner，RunServices/投影/收尾算法保持。

生产/测试/三个脚本全部导入当前真实定义。测试基础设施tool_feedback_helpers的故障注入目标迁到新observation，仍制造ToolError外的dispatch失败，保留原错误身份和清理断言；没有改conftest。分层测试扩展为解析绝对及相对import，并在独立进程确认tools不会导入本项目runtime/application。旧classifier测试迁移时去掉重复import，将重复的未知结果样本改为含私密部分输出并断言分类不泄漏。

红灯：新增边界文件先4 failed（新入口缺失/旧入口尚存）。搬迁中回归发现合并时漏dataclass装饰器及模型失败路径仍需GraphBubbleUp导入，补回原语义后定向全绿。没有用修改业务预期、跳过测试或恢复旧入口消除失败。

| 当前验证命令 | 实际结果 |
|---|---|
| 第6节8个文件定向pytest命令 | 98 passed in14.27s |
| .venv/bin/python -m pytest -q --tb=short | 1101 passed in37.15s |
| .venv/bin/python -m pytest -q tests/test_classifier_contract.py tests/test_tool_package_boundaries.py --tb=short | 最后测试样本整理后24 passed in0.87s；其后仅文件末尾空行清理，运行逻辑与全量验证相同 |
| .venv/bin/python -m compileall -q naiyue_test_agent scripts tests | 退出0 |
| .venv/bin/python -m pip check | No broken requirements found. |
| npm --prefix frontend test -- --run | 9 files /27 passed |
| npm --prefix frontend run build | TypeScript/Vite退出0，186 modules transformed |
| git diff --check | 清除拆分遗留的末尾空行后退出0 |

补充静态校验退出0：生产/scripts/tests无旧入口import，全部生产Python文件≤500行；合并前后的权限类/函数AST逐项相同。EOF清理后重新compileall退出0。

定向与全量包含真实本地Bash进程停止/部分输出、七工具异步IO、批次串并行、批准/拒绝、错误回流及SQLite会话往返。它们是本机确定性/临时目录验证，不是实网模型、真实用户库或Windows保证。新增边界和原行为测试验证实际调用与返回；未把仅存在文件当接线完成。

README、当前学习源码表、课堂版本指引及交接同步新路径；未追加用户掌握记录。下一步审阅[085-03A工具设置与Read接线](085-03-read-settings.md)，该片未实现。未改真实配置、依赖或用户数据，无提交/push。
