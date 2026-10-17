# Spec085：工具系统、权限审批与执行链统一设计

日期：2026-10-15，2026-10-16分片更新。状态：**085-01统一错误、085-02目录与依赖迁移、085-03A默认目录/Read设置、085-03B Ls/Write/Edit设置及085-04启动注册已实施，其余仍供审阅。** 当前已迁移的准确范围见[085-01](085-01-unified-tool-errors.md)、[085-02](085-02-tool-package-boundaries.md)、[085-03A](085-03-read-settings.md)、[085-03B](085-03b-file-tool-settings.md)和[085-04](085-04-tool-registration.md)；其余“新增/迁移后”仍为目标接口。第2节保留总改造前基线，实施按第15节逐片验证，不构成一次实现全部目标的授权。

本文是本轮讨论的统一入口；[文件与消费者清单](085-tool-system-file-map.md)是本 Spec 的组成部分。此前的[Spec084](084-unified-tool-error-feedback.md)、[权限讨论](../research/permission-sandbox-approval-2026-10-12.md)、[工具对照](../research/tool-registration-design-2026-10-15.md)、[Shell讨论](../research/bash-design-2026-10-15.md)、[沙箱边界](../research/tool-sandbox-boundary-2026-10-15.md)保留证据，不再并列充当实施合同。

## 1. 核对结果、范围与被替代的决定

已确认的主线可以合并，没有发现必须改变 Python + LangChain `create_agent` 路线的框架障碍。以下冲突按后续讨论覆盖早期候选，而非把两种方案同时实现：

| 旧记录/候选 | 本 Spec 采用的合同 |
|---|---|
| Bash 暂不进统一 ToolError；错误处理按启动工具名过滤 | 双 Shell 重设计后一起纳入；官方 ToolError 不设置静态名称过滤 |
| 工具副本、ToolFeedbackError、提示白名单、保留旧格式器 | 全部取消；原 BaseTool + 现有 ToolException + 一个 on_error 回调 |
| 显式 Ask、隐藏路径或 `.pem/.key` 一律硬拒 | 显式只有 Deny/Allow；默认策略保守 Allow，否则 Ask；支持范围和授权一致性另查 |
| 注册表启动后固定、每会话一整份注册表/Agent、独立版本平台 | App 共享注册表及图，会话保存活动名称，每次模型请求固定轻量工具集合 |
| 完整文件 Operations/VM 环境先于工具建设 | 本次只做已有多消费者需要的共享进程入口；文件继续原生 IO，完整沙箱另行设计 |
| WriteOutcomeUnknown 或普通超时一律锁会话 | 写提交未确认转具体消息；Shell 清理成功后的超时转消息；控制和真实机制故障继续原层处理 |
| Edit 模糊预算影响全部大文件编辑 | 精确路径保留；超预算只限制模糊匹配，不额外保证该分支的 Pi 归一化歧义检查 |

用户补充确认：本次支持文件/目录软链接按真实目标读写、修改真实文件并保留链接；允许引入 Pillow 做图片处理。完整 Windows 兼容性复盘在工具建设后进行。

本次包括八种内置工具、可信代码注册/会话启停、动态提示词、权限四模式/两阶段 Auto、整批 HITL、原串并行、标准图片与接口适配、必要配置/持久化/迁移清理。前端工具管理、模式快捷键、图片/diff 专用视图登记后续；已有文本、审批和停止界面仍须正常工作。

本次不做项目插件自动加载、工作区信任、MCP、动态替换/卸载、工具依赖图、后台任务平台、持久 Shell、模型配置热重载或任何沙箱后端。运行中登记新名称和启停已登记工具纳入；源文件改动后重启，首版不做 Python 模块热重载。无新增自定义 Hooks 加载器，可信工具作者通过 ToolSpec 的权限描述接入。

子Agent独立实施设计见[Spec086](086-subagent-foreground.md)及[文件地图](086-subagent-file-map.md)，状态供审阅、未实施。其已确认产品合同：通用subagent(agent, task)、共享可信SubAgentSpec、每次独立实例/消息/稳定agent_path、角色工具独立选择、父子共用会话规则/模式/session_allow、一次性批准绑定实例/批次/动作；仅一层、前台/同进程，同级按原父批规则并行。结束后续跑、多层委派和后台分别见TE10/TE11/TE12，不建占位服务。

内部人工审批保留：并行父批A出现新Ask只暂停A本批，B继续已获准工作且后续调用仍审查；B完成结果由框架任务保存，恢复A不能重执行B。原前置整批审批/串行顺序不变。同任务父工具入口可能重入，不能把“无新Agent实例”误解为“不需要恢复批次/许可”。Spec086新增框架探针验证了公开tasks.result、并行完成保留、串行后继挂起与相同内部call_id的多interrupt恢复；产品接线仍未做。

Spec086工程细节供审阅：沿现有RunContext/读取函数增加agent_path，不新增作用域层；原“审批等最外层图停止后开放提交”候选在086审阅中撤回，P4/P5已验证独立子运行状态/审批通道可使A不等B，086现以此细化供审阅接口；串行正常排队不再套60秒故障阈值；统一根事件流附子source；明确暂停停止/必要保存和复合工具异常边界。接口/迁移以086为准，其中取消execution.batch_wait_seconds草案；设计配置样例已移除旧项并补subagent输出预算，产品读取仍待实施。可配置max_model_calls用官方累计thread_limit跨同任务恢复，缺省不额外限轮数仍为工程建议。

停止方向已确认：拒绝动作、停子任务、停根分别处理；应用作废旧审批并驱动原图收尾，必要保存后才报告完成。准确方法、状态及竞争写入086，不能把方向认可或假模型探针当实现授权。定义/消息输入、真人来源、注册热新增及已确认逐项边界均集中到086，不再并列维护另一套子Agent方案。

**Claude审阅建议核对（2026-10-16；状态与候选分开）**

| 建议 | 当前状态/后续动作 |
|---|---|
| 动态工具参数错误可能混入runtime注入字段 | 记录为必须补入实施验收的回归场景；沿实际ToolNode参数错误格式化与动态工具接线核验，未预断定过滤位置或新增转换层 |
| 大Spec拆小实施 | 第15节已有切片顺序；每片实施前形成准确接口/修改范围和验收，不一次完成大提交 |
| Auto审查输入边界 | 第8节已按用户确认收口；不读工具结果/文件正文，保留动作参数与真实用户来源 |
| 双Shell默认策略保守 | 第13节已确认默认Ask、精确Allow和四模式，不加只读命令识别器 |
| 子Agent及作用域/权限/恢复/长任务 | 已确认产品合同及接口/恢复/停止工程设计集中到Spec086审阅；结束后续跑、多层派生和后台后排，不直接实施 |
| ToolMessage.artifact结果通道 | 已确认首版接受原生content_and_artifact；模型必要反馈在content，JSON详情随ToolMessage/checkpoint保存，前端专用展示后排，不承诺永久产物归档 |
| ToolSpec副作用注解 | 已确认side_effects=read_only/may_mutate/unknown，缺省unknown；权限准备保存声明、Auto消费，不能替代授权、改变串并行或触发自动重试 |
| 审查对象执行环境字段 | 已确认execution_environment取实际执行上下文；首版kind=host、platform=windows/macos/linux，随审查对象保存并绑定许可；Auto/审批说明/执行核对消费，不由模型填写，不代表已隔离 |

首轮工程默认建议见带注释的[配置样例](../examples/tool-runtime.example.toml)。图片/Edit 内部预算、100ms 排空、5s 收尾等在本文明确标为**工程默认建议**，不冒称此前已经确认的数值；它们可随本 Spec 审阅调整，不改变已确认机制。

## 2. 当前链路：本次从哪里改起

版本边界：本节保存085启动前基线；01—12已实施的当前入口见文末分片状态和各小Spec，不能将这里的旧Popen/Unknown/权限重复解析当作当前实现。

当前入口的事实：`application.__init__._assemble_application` 或异步/持久对应入口持有同一个 SessionService 和 compiled Agent。CLI/Web 分别创建内置 BaseTool 列表；模型目录只在启动加载。

```text
CLI/Web → LocalApplication.run / AsyncApplication.run
 → SessionService._run_steps（会话、busy、run/turn、资源准备）
 → invoke_agent_run / 异步 invocation → compiled Agent
 → 选模、项目指令、上下文预算/摘要/溢出恢复 → 主模型
 → ObservedHumanInTheLoopMiddleware.after_model（静态 interrupt_on）
 → ToolBatchMiddleware.after_model（按 metadata 决定串并行）
 → ToolNode → 批次 dispatch → _record_tool_call → BaseTool → 具体 IO
 → BaseTool.handle_tool_error → ToolMessage → 下一次主模型
```

当前 HITL 的审查早于工具参数正式校验。应用 `_pending_tool_approval` 再调用 review 解析目标；恢复时 HITL 与工具正文继续解析，因而存在重复完整决策。`file_permissions.py` 目前只接 Read 的同步策略，不能当作八工具通用权限。

当前 `read/ls/find/grep/write/edit` 各有提示白名单与最终转换器；Bash 用同步 Popen、两个采集线程和自己的固定环境。write/edit 的 WriteOutcomeUnknown、BashOutcomeUnknown 被应用、CLI、运行错误策略特殊捕获。`tool_batch.py` 和 `async_tool_batch.py` 已有协调算法，本次迁移复用；086补保存事实/恢复重建及中断挂起，不能原样宣称支持子Agent。

## 3. 职责与依赖：工具能力不再归运行层

```text
application：App/会话、配置加载、可信登记与活动选择、审批提交、必要保存
        ↓                    ↓
agent_runtime/tool_execution  tools：定义/注册契约、权限事实与规则、八工具及实际 IO
        ↓                    ↓
LangChain/LangGraph           原生文件 IO / 共享进程入口 → 当前宿主进程
model_access：模型目录、协议/图片能力、请求消息转换（运行层使用）
```

`tools` 可以使用 LangChain BaseTool/ToolRuntime，但不反向 import application 或 agent_runtime。工作区、已准备许可、停止检查通过现有 ToolRuntime.context 显式传入；stateless进程runner由工厂闭包持有，每次请求/输出消费者/control显式传入，运行状态全部在本次调用内。共享工具不得保存某会话的可变值。原有 RunServices → invocation_context 作用域桥接保留，不能为清理兼容而删除真实调用方；085-12没有为进程增加该字段或新ContextVar。

不新增 BaseTool 子类、统一 run(action_dict)、通用后端注册器或完整文件操作接口。共享进程服务有四个当前消费者：Bash、PowerShell、Grep 的 rg、Find 的 fd；图片、Edit 匹配/diff 分模块是为分清职责和满足单文件500行，完整清单见附录。

## 4. 关键数据与接口

| 数据/拟新增接口 | 生产者 → 消费者；边界 |
|---|---|
| `ToolSpec(tool, input_schema, execution_mode, permissions, side_effects='unknown', prompt_snippet='', prompt_guidelines=())` | 作者/内置集合 → 注册、模型、权限、调度；持原 BaseTool，名称/说明从它取得，不复制 |
| `ToolSpec.validate_input(args)` | 调用真实公开 Pydantic 模型 → 权限前置检查；保留 strict/extra/default，不从 tool_call_schema 重建弱化模型 |
| `ToolRegistry.register(spec)` / `get(name)` / `select(names)` | App 共用；先校验再原子登记，不允许重名覆盖；登记不自动启用 |
| `PermissionSubject` | ToolSpec 的 `permissions.prepare(args, context)` → 管线/展示/执行；工具名、操作、有效参数、工作区、逻辑与真实目标，不含文件正文；公共准备另附ToolSpec.side_effects和当前上下文execution_environment |
| `PreparedToolCall` | 权限节点 → HITL/应用/执行；JSON可保存的调用ID、名称、原参数、校验/准备状态、审查对象、决定及理由来源；准备失败仅存安全反馈与原因码，不存异常对象 |
| `ToolExecutionState` | 图 state 扩展；保存本次 AI 响应对应的工具名称、批次ID、模式和 PreparedToolCall 列表；不序列化工具对象/锁/模型 |
| `ExecutionPermit` / `OneUseApprovalGrants` | 已保存决定/人工批准 → 执行检查；绑定 call_id、名称、参数、工作区、目标及execution_environment；不能跨调用/会话重复消费 |
| `ProcessRequest` / `ProcessOutcome` | 工具适配器 → `HostProcessRunner.run` → 工具；明确 argv/cwd/env/期限/流消费，返回退出、采集与清理的独立事实 |

ToolSpec.permissions 是一个小的声明合同：普通自定义工具可用 `ToolPermission(default='ask')`，按名称/参数匹配；文件工具用 `FilePermission(path_field='path', operation='read'/'write'/'list'/'search')`，复用目标准备和默认策略。确有特殊资源才实现 prepare/default_decision，不能要求所有作者写新类。

用户2026-10-16确认副作用注解：side_effects只描述工具能力，read_only表示不主动修改所操作的业务资源，may_mutate表示可能创建/修改/删除或产生外部动作，unknown为未声明的缺省。Read/Ls/Find/Grep声明read_only，Write/Edit/Bash/PowerShell声明may_mutate；不逐命令识别只读，也不把内部诊断/输出归档混入业务资源分类。作者在ToolSpec填写，注册严格校验值域；公共权限准备从可信定义附加到审查对象并随PreparedToolCall保存，快审/详审读取同一声明，模型args不能覆盖。声明不保证实际行为，read_only仍可能读取敏感信息；不直接Allow/绕Deny，不改变execution_mode，不提供自动重试或恢复安全证明。自定义工具不填沿unknown，不新增注解类或分析器。

用户2026-10-16确认执行环境字段：execution_environment由应用接入的实际执行上下文提供，首版只支持kind=host、platform=windows/macos/linux；公共prepare_local_batch附加到PermissionSubject并随PreparedToolCall保存。workspace仍复用已有字段，解释器由所选工具/启动设置说明，不在共享ToolSpec保存会话可变环境，不接受模型args自报沙箱。Auto、人工审批说明和ExecutionAuthorization消费该事实；执行前核对许可所绑定的环境，恢复一致时不重跑Auto，缺失/不一致不得沿用旧许可或静默改用宿主，明确反馈需要重新审查。此字段不包含完整env、凭证或PATH；环境变量继承/覆盖/移除合同不变。当前仅准确标注宿主；未来沙箱实例/文件及网络限制待真实后端确定，不以sandbox布尔代替保证，不新建环境注册平台或类层次。

`execution_mode` 缺省 parallel，write/edit/bash/powershell 显式 sequential。注册检查名称唯一、真实描述/schema、模式、权限字段、handle_tool_error 未启用及response_format为content/content_and_artifact；不修改调用方对象补救配置。首版接受常规BaseTool文本/内容块，以及原生(content, artifact)结果；动态工具 `return_direct=True` 等改变图路由的能力不在承诺范围，登记时报错说明。

用户2026-10-16确认artifact结果合同：复用BaseTool原生response_format，不新增结果类；普通工具仍只返回content，需详情者返回(content, artifact)，由框架生成同ID ToolMessage。模型必需的成功/失败/不完整/范围/续读信息及图片仍放content；artifact约定为可保存的JSON数据，Edit先使用结构化diff/预算内patch/首个修改行等，不放进程/客户端/句柄或复制无限全文。详情沿现有图状态/checkpoint保存，当前事件/Web仍显示content，专用展示后排；不承诺退出当前上下文后永久可检索，不新建产物平台。大量记录仍走受管日志和引用；子Agent完整记录归属回其生命周期设计决定。ToolException→ToolError链不变，不要求失败携带artifact。

2026-10-16 artifact可行性复核（合同已确认；以下仅机制证据）：本机create_agent+假模型+内存SQLite探针确认content_and_artifact生成同调用ID的ToolMessage，artifact经checkpoint序列化往返保留；Chat Completions适配不发送artifact，当前token估算/文本摘要不读取artifact；消息快照和Web投影也均不展示。异步BaseTool结果配对通过。仅合成数据、无文件修改或模型请求，不代表产品已接线、永久产物归档或前端可展示；首次探针误用visibility='current'被既有校验拒绝，改用正式graph_values后五组断言全部通过，退出0。

轻量工具集合是 `tuple[ToolSpec, ...]` 原引用，存于本次运行上下文；图 state 只保存名称。首版定义登记后不可变且无 replace/unregister，故同进程审批恢复能按保存名称重新取得同一对象，不需要工具版本平台。会话活动变更只影响下一次模型请求，新登记不影响其他会话。

活动名称保留调用方顺序并检查重复，不使用无序set生成schema/prompt；准备时在注册表/会话相应锁下取得稳定引用，锁外运行模型/工具，不把长IO包进登记锁。

示意保存数据（不是当前输出）：

```python
tool_execution = {
    'batch_id': 'ai-17', 'tool_names': ['read', 'edit'], 'mode': 'Default',
    'calls': [{'id': 'c1', 'name': 'edit', 'args': {'path': '.agents/skills/x/SKILL.md', 'edits': [...]},
               'validation': 'valid', 'decision': 'ask', 'source': 'default',
               'subject': {'operation': 'write', 'workspace': '/work/project',
                           'side_effects': 'may_mutate',
                           'execution_environment': {'kind': 'host', 'platform': 'linux'},
                           'requested': '/work/project/.agents/skills/x/SKILL.md',
                           'resolved': '/shared/skills/x/SKILL.md'}}],
}
```

名称、参数、顺序与官方审批 action 必须一致；禁止批准后静默修改参数。人工只提供 approve/reject 和可选本会话“不再询问”，首版不支持 HITL edit decision。

## 5. 启动、注册与会话控制

1. `CLI/Web`默认读取工具设置并调用已实现create_builtin_tools；`build_*application` → 已实现prepare_tool_registry校验实际ToolSpec集合，后片再接权限规则。用户最新确认不增加CLI工具配置路径参数；已确认默认用户目录~/.naiyue-test-agent/，模型models.json、工具/未来权限settings.toml和会话数据同目录，详见已完成的[085-03A](085-03-read-settings.md)；模型--config可选覆盖，保留当前JSON/私有文件检查，不自动迁移真实配置。
2. `load_tool_settings`严格校验工具额度的未知键、类型/范围；缺失采用代码默认，预期读取/解析/校验失败记录安全诊断后整份回退默认。权限配置的`validate_permission_rules`仍按已确认合同校验工具/schema/路径字段，读取失败或非法在创建Agent前终止。已确认两类设置共用settings.toml；权限片接入后整文件语法错误无法确认Deny应失败，能解析但工具字段非法时只让工具额度回退，权限字段非法仍失败。当前03A/03B仅Read/Ls/Write/Edit额度已接入，不能把此时回退当作新权限已生效。不自动读取项目配置、AGENTS或.env。
3. 现有 ModelCatalog 加载一次；Auto 配置的 fast/detailed model_id 必须可解析，预算/超时等复用该目录中的独立模型条目，不另建凭证或 SDK 管理器。未配置两审查 ID 时 Auto 不可选择，其余模式可用。
4. App 持有 ToolRegistry、工具设置、权限规则和共享 compiled Agent；SessionService 保存活动名称、权限模式和内存 session_allow。图一次装配固定适配器。
5. 运行中 `app.register_tool(spec)` 先检查新定义与现有适用规则再发布；失败保留旧注册表。`set_active_tools(session_id, names)` 校验全部名称后替换该会话集合；不自动执行工具。

`create_builtin_tools(settings)`把同一只读设置传给各工厂：用Pydantic派生模型构造该App自己的公开约束及含runtime执行模型，生成描述和ToolSpec；执行也读取同一设置。不能启动改Field后仍用旧schema或执行时另读环境。内置八工具额度由本TOML覆盖；自定义工具用作者现有代码配置其schema/业务限制，不为任意插件新增配置加载平台。

八个`create_session_*_tool(settings)`工厂迁移后统一返回ToolSpec，`create_builtin_tools`只收集，不另写工具名→权限/模式映射。作者使用`@tool`得到BaseTool再组ToolSpec；底层IO helper可直接单测，统一消息/审批行为经真实Agent入口测试。此返回合同和旧固定workspace工厂清理一并迁移，不保留“两种返回值自动识别”。

旧环境额度入口不并存：NAIYUE_WRITE_MAX_BYTES→tools.write.max_input_bytes与tools.edit.max_input_bytes分别设置，NAIYUE_BASH_SHELL→tools.bash.executable，NAIYUE_BASH_OUTPUT_COMMAND_BYTES→Shell的max_archive_bytes，NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES→tool_output.max_workspace_archive_bytes，NAIYUE_TOOL_BATCH_WAIT_SECONDS已由090A删除，不另设正常排队期限。文档给迁移表，测试/脚本改用显式设置；不留“新TOML与旧env谁优先”的兼容解析。环境继承本身仍按第13节，不等于这些旧App配置键继续生效。

后端新增 `get_registered_tools/register_tool`、`get_active_tools/set_active_tools`、`get_permission_mode/set_permission_mode`。AsyncApplication有IO的写操作按现有命名提供aregister_tool/aset_active_tools/aset_permission_mode，纯内存查询可同步。LocalApplication、AsyncApplication、CLI 异步 facade 委托同一会话逻辑，不各写一份规则解析。读/写会话控制和必要持久化采用现有锁与失败合同，不能把保存失败当成功。

活动/模式可以在会话busy或等待审批时提交，按各自边界生效。持久会话先保存对应属性成功再发布内存值，失败维持旧选择并报错；run收尾不能用启动时整行旧快照覆盖期间已改变的属性。它们不修改本批state/人工决定，也不直接启动新的run。

自定义接入：把业务代码放在应用可导入的可信 Python 模块，用`@tool`定义BaseTool，再构造ToolSpec声明公开输入/权限/执行模式；启动时传给装配，或现有进程代码调用 register_tool，再对目标会话 set_active_tools。无须修改 runtime 的工具名分支；执行中具体失败 raise ToolException，其他异常交统一回调。网页首版不上传/执行任意 Python 文件。

普通无注入参数的工具可直接使用公开ReportInput；需要文件授权上下文的工具按项目现有继承方式加执行模型，避免runtime被strict/extra拒绝。下面是设计示意，`existing_preview_function`代表作者已有业务实现，不是本次新增项目函数：

```python
class ReportInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    filename: str

class SessionReportInput(ReportInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)
    runtime: ToolRuntime[dict, dict]

@tool(args_schema=SessionReportInput)
def preview_report(filename: str, runtime: ToolRuntime[dict, dict]) -> str:
    """Preview a report at an authorized path."""
    target = require_file_target(runtime, filename, operation='read')
    return existing_preview_function(target)  # 业务提示在产生处用ToolException表达

spec = ToolSpec(tool=preview_report, input_schema=ReportInput,
                permissions=FilePermission(path_field='filename', operation='read'))
app.register_tool(spec)
app.set_active_tools(session_id, [*app.get_active_tools(session_id), 'preview_report'])
```

工具源码变化、运行设置、模型目录变化需要重启 App；可信应用代码对现有进程调用登记/启停不需要重启。初始集合可以为空，仍保留 ToolNode，使后续动态登记可执行；空集合工具 schema/prompt 列表为空。

运行层通过装配注入两项会话读取：`resolve_active_tool_names(session_id)`供ToolSet准备集合，`resolve_permission_context(session_id)`供Permission一次取得当前mode/session_allow。实际实现复用SessionControlMixin/SessionService上的加锁只读方法，不新增单独控制对象；不能只在run开始复制一次，否则同一run的后续批次无法看到模式变化。它们复用现有选模的绑定方法注入模式，不把SessionService整体传给tools，不用共享全局变量。没有应用层的低层测试显式注入固定返回值。

## 6. 图接线与真实先后顺序

本机 LangChain1.4.2：before hook 正序、after_model 逆序；wrap 正序进入、逆序返回。不能只看 middleware 列表位置猜运行顺序。保留原响应门禁、turn、steering/follow-up、停止及上下文的相对合同，新增以下接线：

```text
主模型请求：StopModel → 选模 → ToolSetMiddleware.wrap_model_call
 → 现有溢出恢复 wrapper → ModelInputMiddleware → 请求预算/观察 → provider
模型响应：现有门禁/停止 → PermissionMiddleware.after_model
 → BatchApprovalMiddleware.after_model → ToolBatchMiddleware.after_model → tools
单次工具：ToolSetMiddleware.wrap_tool_call（核对本轮工具，override(tool=原对象)）
 → 批次 dispatch/领域观察/顺序放行 → 工具日志与调用 guard
 → 官方 ToolErrorMiddleware → ExecutionAuthorizationMiddleware → ToolNode/BaseTool
返回：ToolMessage → 工具日志 → 调度前进 → 领域结果事件 → 结果归并 → 下一次模型
```

注册列表中 ToolBatch 位于 BatchApproval 之前、Permission 之后的反方向：`[..., ToolBatch, ToolObservation, ToolError, ExecutionAuthorization, BatchApproval, Permission, ...]`，确保 after_model 实际为 Permission → Approval → Batch。ToolSet 在工具 wrapper 最外侧，模型图片转换在溢出恢复内侧。最终完整列表由 `_build_runtime_agent` 集中生成，必须用真实 create_agent 的轨迹测试验收，不靠注释担保。

互动运行的目标注册顺序如下；低层没有启用的可选能力不装对应项，不另建一套执行算法：

```text
StopModel, TurnBoundary, select_model,
reset_run_control, ToolSet,
ToolBatch, ToolObservation, ToolError, ExecutionAuthorization,
compact_before_new_run, inject_before_model, inject_at_natural_end,
recover_input_overflow, BatchApproval, Permission,
ModelInput, observe_model_request, StopBoundary
```

因此before_agent为reset→ToolSet准备→compaction，after_model为StopBoundary→Permission→Approval→Batch→TurnBoundary。工具wrapper只有ToolSet/Batch/ToolObservation/ToolError/Authorization参与；注册位置相同不代表每个hook都会执行。

具体装配为`create_agent(tools=[], middleware=固定适配器, ...)`：客户端工具定义由registry唯一持有，ToolSet每次提供模型tools并在执行时override(tool=...)；固定wrap_tool_call使空列表也有ToolNode。框架自己的response_format/结构化输出仍走原框架配置，不把它注册成可随意启停的用户工具。

`ToolSetMiddleware.prepare_tool_view` 在首个 before_agent、主动摘要之前准备第一份集合；before_model消费这份待用集合，后续工具循环的 before_model再准备新集合。`wrap_model_call`在选模后消费该集合，override(tools=原工具列表)，组合基础 prompt + 本 run 项目指令/Skill索引 + 本轮工具摘要/指导。不会逐轮向历史 messages 追加同一段。SDK 重试、溢出重试和当前工具批次固定这份集合；下一次主模型请求才读新活动集合。

现有主动摘要实际在 before_agent；其 `_arguments` 调用同一集合/prompt读取入口，摘要预算、恢复预算与最终请求均使用该集合。不能继续让 compaction 工厂捕获启动 tools，也不把摘要改成每个工具循环重新触发。审批恢复从保存名称恢复本批集合，不能让新run准备覆盖它。恢复时保留 canonical messages，图片适配后的请求副本不进入 checkpoint。

工具未知/本轮未活动时 ToolSet 给同 ID error 消息，正文不执行；该提前返回仍通过本组现有工具观察函数记录not_executed及安全原因，不能因位于最外层而丢失调用事件。这类协议拒绝和模型截断guard不伪装成工具内部异常。准备许可缺失或调用绑定不一致是运行机制故障；目标发生变化是具体的重新发起调用提示，不能以旧批准越权。

## 7. 权限规则格式与四模式

规则格式落于同一 TOML；默认不放任何隐私硬 Deny。以下是格式示意，不自动作为用户实际规则加载：

```toml
[permissions]
mode = "Default"
[[permissions.deny]]
tool = "read"
path = "${workspace}/private/**"
[[permissions.allow]]
tool = "bash"
args = { command = "python -m pytest -q" }
# 可省略的 Auto 配置使用已有模型目录 ID：
# [permissions.auto]
# fast_model_id = "review-fast"
# detailed_model_id = "review-detailed"
```

每条规则的 tool 是精确名称或 `*`；可有 path 与 args，多个字段为 AND，多条为 OR。args 为公开 schema 字段的 JSON 标量精确相等，不做命令前缀、正则或语义推断；Bash/PowerShell command 按原字符串精确匹配，timeout 不纳入默认“不再询问”命令规则。

path 只用于声明文件资源的工具，支持绝对路径或 `${workspace}` 前缀；路径组件 glob 支持 `*`、`?`、`**`，`*`不越目录，`**`可跨目录，使用独立于 Shell 的明确实现/测试，不直接把 fnmatch 当完整路径规范。内部按宿主规范化分隔符/盘符；POSIX区分大小写，Windows沿规范化路径比较，UNC/重解析点须实测。

显式 Deny 检查用户请求路径和解析后的真实路径任一命中；Allow 必须覆盖真实目标，不能只批准位于工作区的链接名来放行外部目标。新文件绑定现存父目录解析结果及剩余路径。路径解析不读取正文；不能确认真实目标时返回具体错误，不猜测批准对象。

`tool='*' + path` 仅匹配声明文件资源的工具；`* + args` 要求所有候选工具具有相应字段及一致可比较类型，否则配置非法。精确工具名未登记、字段不存在、非文件工具配置 path、未知占位符/模式语法均启动报错；动态登记也重新验证所有适用规则。

权限顺序：真实公开参数预检查 → 准备审查对象/能力前置事实 → 配置 Deny → 配置/会话 Allow → 工具默认策略 → 模式。Allow 可免默认 Ask，不能绕过工具能力和目标一致性；策略只返回 Allow/Ask，普通文件满足工作区内且无隐藏/识别到的私密特征才默认 Allow，其余 Ask。默认特征明确为逻辑/真实目标的工作区相对后代组件以`.`开头，或文件后缀不分大小写为`.pem/.key`；外部目标本来即Ask，不因名字普通免审。这些是Ask特征，不是硬拒表；项目指令和Skill已明确授权的资源可由可信会话资源范围提供受限Allow，显式Deny仍优先。不能仅凭发现了一个指向外部的Skill链接就自动授权其正文。

| 模式 | 剩余 Ask 的处理 | 规则已 Deny |
|---|---|---|
| Default | 交人工 | 拒绝 |
| NoAsk | 转拒绝消息 | 拒绝 |
| Auto | 快审；若 Ask 再详审；仍 Ask 交人工 | 拒绝 |
| BypassApprovals | 允许执行，不走人工/模型审批 | 拒绝 |

模式在权限节点开始处理当前工具批次时固定。等待审批时修改模式不影响该批；下一批工具调用才生效，可能仍属于同一用户输入。活动工具则在更早的模型请求准备时固定；两者生效边界不同，不能共用“立即刷新全部”的开关。

“不再询问”仅在用户批准该项后添加session_allow：归SessionService管理的该会话内存状态字段（现有_sessions[session_id]字典扩展），保存临时Allow规则，不写回配置文件、不写会话DB，不挂到共享Agent/BaseTool上。父子读取同一会话集合，权限节点开始新批次时取得规则快照。文件按用户实际同意的目标/操作范围，命令按工具名+精确command+会话工作区，其他工具按精确公开参数；同时核对已确认的执行环境，默认不扩大父目录/所有命令。会话Allow不跨会话、不覆盖Deny；同会话后续run可用，浏览器刷新不清后端内存，服务重启不恢复，即使重新打开同一持久会话也不恢复这些临时授权。

恢复API增加可选`remember_approved`审批行ID集合（086统一为approval_id，模型call_id仍只用于消息配对），仅允许其中项目本次确实approve；从已保存对象生成原工具/操作/精确目标规则，不能由界面传任意Allow文本。目录记忆只免同工具/同起点下次审查，不自动生成`/**`或给Read授权；递归隐藏/私密子目标仍按各自规则过滤。CLI/Web勾选入口后续接此后端能力。

## 8. Auto、批量暂停、恢复与执行许可

审批粒度审阅补充：下文整批暂停是此前已确认的设计，不能解释为框架只能批量。当前安装LangChain1.4.2的官方HITL在after_model整批interrupt；已发布1.4.6提供interrupt_mode='per_call'，在工具wrapper中逐调用中断、按interrupt ID独立恢复。为完整子Agent目标重新比较该官方路径；它不自动提供模型顺序串行或修复当前批次恢复。候选尚未获准替换本节链路，也未升级产品依赖；版本/参考调用链及底层独立恢复探针见[审批粒度复核](../research/permission-sandbox-approval-2026-10-12.md#审批粒度复核2026-10-15)。

拟新增 `AutoPermissionReviewer.review/areview`：复用 ModelCatalog 两个模型，由权限节点提供独立整理的审查输入。固定审查指令、已登记工具的副作用声明与可信执行环境单独提供；对话部分从现有图 messages 投影，只保留仍在上下文中的真实用户消息、历史工具调用的名称/参数，以及当前待审调用、目标准备事实和默认 Ask 原因；保留顺序并区分历史调用与待执行调用。2026-10-16用户确认另提供本run已加载的AGENTS.md或CLAUDE.md项目指令：沿既有AGENTS优先/CLAUDE回退和每run快照，保留纯指令后经既有resolve_permission_context传给审查输入，不二次读盘、不混入Skill索引。项目指令独立于人类消息，不能伪造用户来源、覆盖Deny或固定边界；完整指令无法容纳于审查预算时Ask。继续排除助手解释/思考、工具结果、普通文件正文、Skill索引/正文和模型生成摘要。write.content、edit 新文本及内联脚本属于调用参数，仍须作为待审数据保留，不能误当工具结果删除。真实用户来源由应用实际接收/投递入口识别，不能只看 HumanMessage 类型，也不能依赖可选观察器成功。批准旧批不重审，恢复run的项目指令刷新只供其后新批使用；详见[085-07](085-07-auto-permission-review.md)。

首版不另建独立审查历史、数据库或不受压缩影响的授权记录；复用已有图状态，压缩摘要不视为用户授权。历史按审查输入预算取舍，当前待审操作不得静默截断；信息不足或预算不足以完成审查时 Ask。上下文压缩可能丢失先前口头授权或限制，不能承诺恢复；长期确定性禁止仍用 Deny。快审/详审复用同一份已整理输入，不向主会话写入审查对话。每阶段只接受严格结构 `decision: allow|ask` 与有界理由；不使用新 Agent 或主模型循环。

审查观测先用安全结构化诊断：关联session/run/call_id、fast/detailed、model_id、有效decision/回退原因及可得usage；不记录审查正文/参数，不发purpose=agent的主模型事件，不新增领域事件类型。prepare/default_decision只做同步本地元数据和判断，异步权限hook沿现有run_blocking执行必要阻塞IO，模型IO只在areview中await；不在默认策略回调里偷偷请求模型。

快审 Allow 即结束；快审的有效 Ask 才调用详审。任一阶段超时/网络/解析错误、非法 Deny 等直接回退人工Ask并记录安全原因，不再追加审查请求；UserStop/外部取消继续原控制。直接调用 `BaseChatModel.ainvoke`，纳入现有 RunControl 的模型等待，单独标识 review 阶段和用量，不向主会话 messages 塞审查对话、不触发普通 turn 或错误冒充主模型响应。两条模型目录配置各自决定输出/思考/请求预算，不能修改共享模型对象切参数。Auto内部发生UserStop时，权限hook须委托从现有StopBoundary提取的`finish_stopped_batch`，配对尚未执行项并以run_stopped跳结束；它不在主模型wrapper内，不能假定StopModel会自动接住。外部CancelledError仍清理后传播。

`PermissionMiddleware.after_model/aafter_model` 为本批只准备一次有效调用与最终决定，保存 ToolExecutionState。无效 schema 调用不进入审查，也不提前生成 ToolMessage；真正参数错误仍在后面的 BaseTool/ToolNode 路径出现。若同批 B 需审批，A 参数错误会等 B 恢复后才反馈，这是保留批量时序的明确代价。

目标准备也不能把普通路径错误变成图异常：工具的prepare识别断链/解析IO等，保存`target_error`及已准备的具体安全提示，不进入审批；ExecutionAuthorization在执行阶段raise对应ToolException，交同一个ToolError生成消息。工具自定义prepare意外异常只留安全类型/通用提示，不序列化原正文；原因在准备处记录。已形成审查对象后的默认策略回调不可用/返回非法值，按Ask处理；必要Deny规则引擎、state绑定等运行机制失效仍上抛，不能降级Ask后绕过不可确认的Deny检查。

`BatchApprovalMiddleware` 读取已保存结果，为本批创建局部官方 HumanInTheLoopMiddleware，interrupt_on 仅列本批需人工的名称，when 再按 call_id 查对应 Ask。同名工具有 Allow/Ask 混合时也须逐 call 判断，不按工具名全部询问。不修改共享 middleware 的静态映射，不重写 interrupt/Command 协议。

```text
首次：main_model → Permission.prepare_local_batch → Auto(有需要才调用)
 → 保存本批结果 → BatchApproval → 官方 HITL interrupt → 本次 run suspended
界面：只展示 Ask 项，但同批所有工具执行数为0
恢复：SessionService._resume_steps → session_approval.prepare_resume
 → 核对全部决定/官方 actions/保存对象 → 构造本次一次性 grants
 → Command(resume=全部决定) → BatchApproval 重读已保存结果
 → 官方批准/拒绝消息 → ToolBatch.build_plan → ToolNode → main_model
```

HITL 恢复重入当前审批节点；权限前置节点已经完成，不因批准恢复再调 Auto。应用 `_pending_tool_approval` 改为消费保存的对象，取消重复 review/分类 IO。更改目标/参数要新工具调用，不能把用户批准过的对象偷偷替换。首版仍只保证同进程审批恢复，不增加重启后 pending 审批恢复/外部模型 exactly-once 保证。

`ExecutionAuthorizationMiddleware` 在进入具体工具前检查本批 call_id/name/参数/工作区与决定；Allow/AutoAllow 从已准备结果发放调用许可，人工批准消费通用 OneUseApprovalGrants。Deny/NoAsk拒绝给同 ID error，正文不执行；官方人工 reject 使用官方拒绝消息，不重复构造。标记为无效schema的调用不产生许可，执行阶段交框架原参数校验反馈，不能误报“许可丢失”或进入工具正文。

文件工具入口显式调用 `require_file_target`，拿到已授权真实路径并重验链接/父目录与必要目标身份；不重新跑规则/Auto。自定义文件工具复用此入口；任意可信 Python 自行绕开 helper 做 IO 无法靠登记自动隔离。身份变化只拒绝旧许可，提示重新提出调用；保留写提交前快照冲突检查。

审批完成后计划只看实际尚未有 ToolMessage 的调用：含 sequential 则全体按原顺序，否则有界并行。拒绝唯一 sequential 项后，剩余 parallel 可并行。同步 `_dispatch_tool_call` 不得再根据原 AI 中已拒绝的 serial 项认定 plan 缺失；该已发现问题必须先复现成回归测试再随迁移修复。

完整例子：用户要求修改一个链接到`/shared/skills/x/SKILL.md`的Skill；当前模式Default，主模型本次发出A=read(offset=0)、B=edit(上述链接)、C=grep(工作区普通目录)。以下是目标调用轨迹，不是当前运行输出：

1. `ToolSet.prepare_tool_view`固定read/edit/grep等活动定义，`wrap_model_call`把同一集合的schema/prompt交主模型；AI响应产生A/B/C和各自call_id。
2. `Permission.prepare_local_batch`校验A无效，不审查；`FilePermission.prepare`得到B的逻辑路径、外部真实目标和write操作，默认Ask；C准备获准的搜索起点，默认Allow。保存三项与本批Default。
3. `BatchApproval`仅把B交官方HITL；A尚未产生参数错误消息，C也没有启动rg。应用从保存对象展示B的真实目标，不再调用路径review。
4. 用户批准B。`session_approval.prepare_resume`核对决定与B已保存的原参数/工作区/真实目标绑定，不访问文件，生成一次性grant；图以Command恢复，HITL重读B已保存Ask，不重跑Permission/Auto。
5. `ToolBatch.after_model`看到B为sequential，建立A→B→C计划。A到框架校验，得到参数error消息且正文未跑；它是已配对普通结果，计划继续。
6. B经`ExecutionAuthorization`消费grant、`require_file_target`核对真实目标；`edit._edit→prepare_edit_plan→_copy_to_temp→_replace_temp`修改真实文件并保留链接，再返回diff/firstChangedLine。
7. C经同一许可检查、递归候选过滤、`HostProcessRunner.run(rg argv)`取得匹配与跳过范围；三条ToolMessage按原ID归并，下一次主模型解释结果。

失败变体：等待期间链接改指另一个文件，B的`require_file_target`拒绝旧许可→ToolException(目标已变化，请重新发起)→官方ToolError→同B的error消息，B文件正文不读取/写入；C仍可继续。若用户直接拒绝B，官方先给B的拒绝消息，A/C按剩余parallel计划运行。若B提交替换后结果未确认，则具体错误不宣称“未写入”，不会自动重试；这是另一种失败，不能误报成权限拒绝。

## 9. 统一错误：保留提示，分清控制与机制

`feedback.py::tool_error_feedback(error, request)` 是唯一最终回调，直接注入官方 `ToolErrorMiddleware(on_error=...)`，不传静态 tools 名称过滤，动态工具同样适用。工具保留“识别原因 → 准备具体提示 → raise ToolException from cause”；去掉 handle_tool_error 和 SAFE_* 提示白名单。

| 错误/结果 | 传播与影响 |
|---|---|
| 文件不存在、UTF-8错误、无匹配/歧义、写冲突、解释器/rg/fd不可用等 | 具体 ToolException → 原提示 → 官方同 ID/name error ToolMessage |
| 普通意外 Exception，包括执行后整理结果失败 | 安全类型+通用提示，保留内部 cause；不返回原始异常正文、参数、堆栈，不自动重试 |
| WriteOutcomeUnknown 原提交分支 | 改抛具体 ToolException，说明结果未确认、先检查再操作；删除旧类与单凭它锁会话的逻辑 |
| GraphBubbleUp/GraphInterrupt、UserStop、CancelledError | 保持暂停/停止/取消；不转普通工具反馈 |
| 调度、模型门禁、上下文、必要保存、真实进程收尾故障 | 原层处理；回调防御分支返回 None，不能吞成普通失败 |
| 截断、部分搜索/输出、跳过未授权子项 | 明确完整性及范围；按工具合同可为正常结果，不一律报 error |

进程共享层新增的 `ProcessCleanupError` 仅表示受管父进程或必要本端资源在收尾期限内未回收；它是替代 BashOutcomeUnknown 混合语义的**机制异常**，不是新模型提示类。运行错误策略、应用收尾阻断及日志有真实消费者，ToolError 必须放行；普通超时/采集不完整不得借用它。

工具日志仍有 tool_failed；领域事件 outcome=error_result；run 可以最终 completed。这三个字段回答“某次调用结果”“领域观察”“本次运行是否正常结束”三个问题，不是把失败统计改成成功。统一转换只记录一次安全诊断并保留原因/阶段，不机械叠加三层重复日志。

四条验收模拟：Read UTF-8 解码失败→原 INVALID_ENCODING；自定义 RuntimeError→通用消息；edit 替换结果未确认→具体消息且模型可继续；进程真正收尾失败→原异常上抛并阻止会话安全续跑。保留未配对工具、未确认保存、取消后未结束资源对应的人工核对状态，不能整块删除 manual_review_required。

## 10. 文件工具：路径、递归权限与读写

统一 `paths.prepare_file_subject` 解析相对/绝对/`~`及既有路径兼容形式，固定会话 root；规范化后的越界交权限 Ask，不把所有 `..`表达直接当恶意。NUL、循环/断链、无法形成目标、平台不支持的路径返回具体错误。模型不能改变会话 cwd。

软链接包含用户所说的软连接。Read/Write/Edit 支持文件链接和目录链接；审查真实目标，外部目标默认 Ask。写入在真实父目录创建临时文件、替换真实文件，保留链接本身；新建父目录发生在批准后，解析最近现存父级并在创建/提交前重验。Windows junction/reparse point 不只看 is_symlink，须纳入真实目标测试；不声称纯 Python 校验能彻底消除敌对并发换链的 TOCTOU。

目录递归批准只覆盖普通获准子范围；未授权隐藏/私密目标先跳过，不逐文件打断审批、不逐文件调 Auto；显式 Deny 始终过滤。结果必须给被跳过的目录/范围及原因和受限摘要，不能声称“已搜索全部”。目录链接目标须先核对授权并去环，不能让rg/fd无条件follow进入未批准外部子树。列名称不等于读正文；授权规则依据工具声明的 list/search/read 操作应用，不能把 Read 正文限制原样套给 Ls/Find。

| 工具 | 具体行为与修改边界 |
|---|---|
| Read | 文本默认/最大2000行、64KiB；先返回巨行前完整行；第一行已超限则给可容纳 UTF-8 前缀，标记部分行，不拆字符；补实际行范围、截断原因和下一页例子 |
| Read续读 | 保留1起始 offset/limit按行分页；本次不增加单行字符游标API，截断单行的剩余内容可由Shell等分段检查；专门行内续读单独待办，不声称 offset 能读到被截掉的半行 |
| Grep | rg 原生 glob/上下文/大小写；获准候选分组送 rg，不逐文件启动；取消旧2000文件/8MiB单文件/32MiB扫描候选上限与整文件UTF-8预检；结果/期限有界，跳过与超时明确 |
| Find | fd 原生 glob/smart case，文件+目录、流式发现，过滤后计数；缺 fd 明确报错，不下载/安装，不静默换成另一种语义 |
| Ls | 一层、排序、目录后缀；保留limit/预算，补目标/IO/不完整原因及合作停止；不加递归接口 |
| Write | 批准后自动创建父目录，1MiB默认输入限制而非目标文件上限；同真实目标跨会话 busy、快照/原子替换/失败清理保留 |
| Edit | 同次全部edits基于原文件，必须唯一且不重叠，保持大文件分块/临时替换；具体算法见第11节 |

Grep 有结果但超时→带不完整说明返回；没有结果且未完成→具体错误，不能等同“无匹配”。结构化子进程非零和 stderr 按 rg/fd 文档语义解析，不用 Shell 的所有非零即失败机械覆盖。真实外部目录、权限过滤、文件消失、循环链接和结果排序都须测试。

## 11. Edit 匹配、换行、diff 与预览

`edit.py::_scan_target` 的精确流式匹配、快照、非重叠检查优先保留；新增 `edit_matching` 处理 Pi 对照过的有限归一化：换行、行尾空白、NFKC、引号/破折号/空格变体等。不是编辑距离或语义相似。精确匹配零次才尝试模糊；多次直接歧义，不用模糊“挑一个”。

若同批需要模糊，按统一归一化坐标查找并检查相关重复/重叠；以有界窗口保存归一化到源字节/行的映射，逐块扫描，不整文件读入；必要的再扫描最多三遍，不能按每个 edit 扫完整文件。归一化缓冲超预算：已唯一的精确编辑继续可用，需模糊的本次整组不提交并给具体限制提示，不能部分提交。

原文件UTF-8 BOM保留；LF/CRLF用于匹配兼容，写回保留未修改原字节。替换内新行采用匹配区域的首种换行，无则用文件首种换行，再无则LF；混合换行不全文件重写。归一化只找坐标，不把原文件整体转成归一化文本。该混合换行规则是本 Spec 的工程选择，测试需逐字节断言。

`edit_diff.build_edit_feedback` 从已定位变化区生成局部 unified diff、firstChangedLine 和完整 patch。显示 diff/预览有独立有限预算；“完整 patch”只有确实完整且≤配置预算才提供可应用内容，超限明确 patch_omitted，不给截断 patch 冒充完整。提交已成功而后续预览格式化失败不声称文件未改，沿统一错误边界说明。

批准 Edit 包含执行所需内部读取，无须再申请 Read。人工批准前若要展示旧文件上下文，只有已具备读取许可才读取；否则展示用户给出的 old/new 参数。不会为了审批预览先读取未授权原文；首版后端计算结果复用，不新增前端专用diff页面。

工程默认建议：归一化缓冲1MiB、单个受影响行重建缓冲1MiB、预览/diff各64KiB、完整patch1MiB；独立键并逐项注释。源文件无全长限制；超限如何降级分别按上段，不将所有预算写成 max_file_size。

## 12. 图片、模型接口与上下文

Read 检测真实图片内容后用 Pillow 识别/缩放/必要转换，返回标准 text/image 内容块和 MIME/base64；不靠扩展名认定格式、不把二进制当UTF-8。建议首版支持 PNG/JPEG/WebP、GIF首帧、BMP转换；EXIF方向处理、透明度及格式转换失败要有确定结果。其他格式明确具体错误，不假装模型已看到图片。

工程默认建议：输入32MiB、解码40百万像素、最长边2000px、base64输出4.5MiB；均在read图片配置中注释，解码前/转换后都校验，Pillow炸弹警告不静默忽略。超限尝试有界缩放/重编码，仍不能满足则具体错误；不无界尝试质量参数。Pillow为本次唯一新增产品依赖，实施冻结兼容Python3.12的固定版本，不升级已有依赖。

模型配置新增 OpenAI类条目的必填 `api: chat_completions|responses` 和可覆盖的 `profile.image_inputs` 布尔值；框架profile只作默认，未知能力保守处理，用户显式配置为准。对应设置use_responses_api=False/True，不设auto、不猜URL/模型名、不为缺api保留旧配置兼容。实施须给迁移样例并补齐被授权修改的配置；真实`.env`仍需遵循用户配置修改授权边界。Anthropic/Ollama按原适配器能力处理，不虚构同名api值。

```text
read(image) → ToolMessage标准内容块 → canonical messages/checkpoint
 → 选中当前模型+profile/api → prepare_model_messages（仅请求副本）
   Responses/Anthropic：复用官方适配器支持的标准路径
   Chat Completions：ToolMessage保留文字/图像引用说明
     + 在本批所有配对ToolMessage之后放一个HumanMessage图片承载消息
 → 最终请求预算/观察 → provider
```

该 HumanMessage 是请求适配载体，不是用户新输入、不是新 turn，不落会话历史，不插到尚未配对工具结果之间。当前模型不支持图片时，请求副本以明确的图片未提供说明替代，canonical 图片仍保留供以后切模型；不默默把旧图删除或称已理解。

`context_compaction._validate_removed_prefix/_serialize_text_history` 必须支持内容块：摘要输入包含文本、图片存在及有限元数据，不把base64塞摘要文字；尚未压缩的图片保留原块。被历史摘要淘汰的图片明确不再携带，不能声称摘要保留像素。预算估计识别图片成本/有限未知值，不把base64字符数当普通文本token。

Web还必须修一个已有路径的实际约束：`DeliveredRun.observe`目前在完整事件wire_event后做帧大小检查，图片base64可能先超限，不能只依赖前端textContent忽略图片。新增`web.serialization.project_event_for_web`：只为Web消息快照产生文字投影副本，标明content_projection=text、重算该副本snapshot_bytes/sha256；DeliveredRun的缓冲/展示投影均消费副本，再检查帧大小。原领域事件、模型历史与checkpoint不变；保持原序号/ID/工具状态，无新增图片UI。文本本身超限仍沿现有明确降级，不扩大为无限帧。

ModelInputMiddleware 放在 overflow recovery 内侧；重试基于原 canonical 数据再次适配，避免双重追加载体或把适配消息提交 checkpoint。Skill/AGENTS仍按既有每个新run资源准备刷新，同一run内不悄悄重读全部文件；新工具提示按模型请求刷新，不混淆这两种生命周期。

## 13. Shell 与共享进程的完整生命周期

保留 bash，新增 powershell，模型参数均仅 command/timeout；不加任意 env/cwd/shell 参数。Windows可同时启用Git Bash和原生 PowerShell，优先pwsh7再回退5.1；macOS/Linux默认Bash，显式启用且安装pwsh可用PowerShell。不安装解释器、不静默切到WSL或另一种Shell；显式启用却缺解释器启动报错。

两种Shell的默认权限策略均返回Ask；工作区内启动、命令名称或看似只读不构成默认Allow。显式Deny优先，配置/会话精确Allow可免默认Ask，其余由Default/NoAsk/Auto/BypassApprovals处理；Bash与PowerShell按各自工具名匹配，timeout不纳入默认“不再询问”命令规则。首版不增加只读命令白名单、前缀匹配或Shell语法/副作用分析器；命令解析不能替代未来沙箱的实际资源限制。

`create_builtin_tools` 准备真实OS/解释器版本提示；`ShellAdapter.build_request` 生成 argv/cwd/env，`HostProcessRunner.run` 执行，`ShellAdapter.format_outcome` 决定普通返回或具体 ToolException。每次新非交互进程、cwd为会话root、stdin关闭，无TTY；一次command内cd/export有效，下次不延续，文件效果保留。

环境默认复制父进程，依次values覆盖、path_prepend合成、unset_env移除；Windows名称大小写及pathsep适配，不修改共享os.environ，不自动读项目.env。建议默认可见移除BASH_ENV与NAIYUE_WEB_TOKEN（PowerShell仅后者），用户可调整；保持其余业务环境继承，无TOKEN/KEY模糊黑名单。该少量默认移除是工程建议，不改写成已确认“所有父环境变量必下传”。

Bash非登录/非交互、不主动source rc/profile；PowerShell用NoProfile/NonInteractive/EncodedCommand。建议PowerShell默认尊重现有执行策略，可显式选择仅该子进程Bypass，企业策略仍有效；不修改用户/机器策略。这替代此前“默认Bypass”的未批准候选，避免在总Spec偷加系统策略绕过。

Shell默认合并stdout/stderr为一根管道，尾部2000行/64KiB；rg/fd保留结构化stdout与诊断stderr分流。共用进程生命周期不共用输出解析。Shell退出0只说明Shell进程结果；非零保留输出生成具体错误，不凭stderr/文字Error推断，不自动补exit $LASTEXITCODE或设置ErrorActionPreference=Stop。

输出增量解码、去ANSI、CRLF/独立CR转LF、其他不可见控制字符转可读转义；跨块状态有界。PowerShell进程内初始化UTF-8，5.1文本重定向默认编码显式适配、用户Encoding优先，7.4+原生二进制重定向保留其语义。外部程序可声明output_encoding；非法字节用替换并标记有损，原始归档独立保留。

协议回调只向有界采集队列交字节/退出事实；队列满用公开pipe transport回压，异步消费者负责解析/写盘，不能在事件循环callback内做无界磁盘IO或把异常丢给后台日志。工程默认建议未消费队列1MiB；消费者/读取故障必须传回runner并完成收尾，不能无限等待EOF或伪报完整输出。

超过尾部预算或需保留有损原始诊断时触发归档：已有前缀+后续流式保存；普通UTF-8只一份，需要时另产UTF-8文本视图供Read。每调用实际落盘16MiB、工作区跨Shell/会话128MiB；两份都计额度。磁盘/配额失败继续排管道、保留最新尾部、说明前缀/尾部/缺失区间，不标Full output。日志保留原所有权识别/手动清理，无TTL；旧受管日志有真实消费者，继续可回读/清理。

```text
获得执行许可 → 建立执行总deadline（不计人工等待）→ 启动前停止检查
 → public loop.subprocess_exec + SubprocessProtocol，立即持续采集
 → 分别接 process_exited / pipe_connection_lost / 本端资源关闭
 → 正常退出排空，或超时/停止终止受管组/树 → 有界回收 → 结束归档
 → ProcessOutcome → 具体反馈/控制传播/ProcessCleanupError
```

不使用communicate收无限输出，不先wait再读，不依赖私有_transport；公开管道transport允许在父退出但后代持管道时有界关闭。启动中取消也必须接管已创建进程，不能因spawn await被取消留下无人收尾对象。

Shell正常父退出：自然EOF即完整；否则按最后输出等短静默，工程默认建议100ms，持续有数据仍受剩余执行期限约束；主动关闭采集标不完整。正常退出不承诺自动杀所有后代。rg/fd要求完整结构化EOF或给不完整/失败，不能套100ms就报告搜索完整。

超时/用户停止在父仍运行时：POSIX独立session/进程组终止并回收直接子进程；Windows首版taskkill /T /F并等待结果/父回收/本端关闭。主动脱组和父先退等边界不保证任意后代全部结束；更强JobObject另立合同，不把进程管理称作沙箱。

收尾共用一份工程默认建议5s预算，不逐步骤续5s。清理确认后普通超时→具体错误消息、模型可继续；用户停止→同ID停止结果并停止后续模型/工具；真正任务CancelledError清理后原信号传播。受管父或必要资源未回收才抛ProcessCleanupError，保留主异常/收尾事实，不自动重试命令、换Shell或移除隔离。

## 14. 会话持久化、外部影响与 Windows 后续

活动名称与模式属于会话属性，需同步 describe、内存结构、保存和恢复；session_allow首版仅内存。会话库按实际消费者分片事务迁移：05已完成v1→v2增加active_tool_names_json，权限片再增加permission_mode；旧记录缺省Default及启动初始活动集合，不丢历史、不删库。恢复发现已保存工具缺失应给具体配置错误，不静默启用其他工具；受限pending恢复范围不扩大。

运行API与会话ID/事件配对保持；应用`build_*application(..., tools=...)`从裸BaseTool列表迁移为ToolSpec列表，低层`build_runtime_agent`的tools参数替换为tool_registry并显式注入上述两项会话读取，是明确的接入合同变化，需随本Spec批准并迁移仓库消费者，不保留旧自动包装层。直接BaseTool.invoke仍按LangChain原语义抛异常，统一error消息由Agent入口提供。CLI/Web复用内置集合；用户最新确认由统一活动工具选择控制Bash/后续PowerShell，取消--host-bash等Shell专用启动开关，随05活动集合接线删除；不保留旧开关与活动集合双重启用条件。04现行代码仍含该开关，当前可运行说明以README为准。

**不能因为新权限系统就删掉OS权限。** 本次删除的是旧工具策略metadata、重复审查、工具错误白名单/回调、被替代的Bash环境/线程执行及未知结果特判；凭证私密文件、日志所有权、SQLite独占/保存、Web认证和未配对副作用保护仍有真实消费者。

已发现的Windows静态阻点：`session_storage.py`顶层fcntl、O_NOFOLLOW/flock与POSIX私有位；模型目录私有位校验；write/edit的fchmod；日志mode/owner规则；原Bash的POSIX组清理。新进程方案解决其中一部分，不代表整个CLI/Web可直接在Windows运行。当前没有Windows实机证据。

工具建设后执行[后续清单的WIN1](../future-work.md#win1工具建设后全项目windows兼容性复盘)：检查安装/启动→模型配置→会话/锁/SQLite→工具/路径/编码/子树→Web/停止/重启恢复→脚本及文档。输出可复现阻点、必要平台实现、真实Win5.1/7与macOS/Linux回归及能否直接使用的结论；安全目标保持，不用跳过检查换取启动成功。

未来沙箱直接替换/扩展进程服务入口及明确的执行环境信息；工具准备数据、审批、输出协议可复用。原生Python文件工具和自定义任意IO目前仍在宿主；Bash规则不能限制命令内所有文件/网络资源。选定隔离后再定OS边界与额外授权协议，不预建全文件Operations或声称权限审批已经实现沙箱。

## 15. 实施顺序与验收入口

总 Spec 不等于一次大提交：每片先确认准确红灯、实现、回归、清理旧消费者，再进入下一片；若新证据改变合同先更新本文。文件/API迁移切片必须覆盖相应全链，不能交付两个长期并行入口。

| 顺序 | 交付切片 | 必须可观察的证据 |
|---|---|---|
| 1 | ToolSpec/Registry/settings、独立tools包、统一错误 | 原工具无副本；旧具体提示不变；意外异常模型可继续；动态名ToolError；控制/机制不被吞 |
| 2 | ToolSet/动态prompt、活动集合与空图动态工具 | 会话隔离；注册与启用分开；本批/重试/审批引用不变；停用名称猜不出执行；预算schema一致 |
| 3 | 权限/Auto/局部官方HITL/串并行、会话保存 | strict预查；Deny优先；四模式；快审Allow不详审；异常回退人工；恢复不重审；整批暂停；拒绝唯一serial回归；v1迁移 |
| 4 | 文件路径/软链接、递归搜索与Read/Edit能力 | 外部/隐藏Ask；Deny子范围不泄正文；换链旧许可失败；真实目标写入保留链接；有界大文件/模糊限制/完整patch |
| 5 | Pillow与三协议图片链、上下文 | 请求副本/历史区分；成批配对；切模型；超限/格式/像素；摘要不吞base64；恢复不重复图片载体 |
| 6 | 双Shell/共享原生异步进程/输出归档 | 正常/超时/停止/取消/父退管道留存/读失败/保存降级/清理故障；进程工具三系统样本、CLI/Web循环检查；应用Windows阻点在WIN1修后复验 |
| 7 | 迁移终检和WIN1兼容性复盘 | 无废弃内部消费者/兼容空壳；真实Windows端到端结论与平台补齐清单，单独修复其余阻点 |

2026-10-16分片：085-01/02/03A/03B/04/05/06/07/08及[085-09 Write/Ls](085-09-write-parents-and-ls-feedback.md)已实施。ToolSpec.permissions、前置规则校验、保存权限决定/官方整批HITL/通用grant与permit、四模式/会话切换/内存Allow和schema v3已接；真实目标软链接与递归过滤提前作为授权主链的必要消费者纳入06，文件高级能力仍后排。旧when/review/grant_factory及Read专用权限metadata已无消费者并删除。Auto共用快/详审模型及同一算法，纯项目指令与真实人类来源接线、停止/取消和恢复不重审已验证；08已接文本巨行/分页和权限前有限读取路径兼容，执行不重选；09已接授权后建父目录、Ls不完整/错误/停止反馈，Edit共用默认检查不变；[085-10 Edit](085-10-edit-matching-and-feedback.md)已接有限归一化/原字节映射、局部换行与有界diff/preview/完整patch和JSON artifact，保留分块/超预算精确能力；复制阶段生成反馈、替换前复核原subject，精确两遍/模糊三遍。全量1429/前端27及构建通过。[085-11图片](085-11-read-images.md)已接Pillow、四预算、显式api/profile、标准块与临时载体、摘要/预算和Web文字投影；新增57例，全量1486/前端27及构建通过，无真实供应商或Windows验证。[085-12共享进程](085-12-process-lifecycle.md)已实施：Bash使用同一async runner/同步薄桥、公开SubprocessProtocol/回压，分开退出/EOF/资源事实；普通超时回填、停止结束本轮、真正CleanupError保护，旧Unknown/线程/名字特判已移除。全量1514/前端27及构建通过，无Windows实机/沙箱证据。[085-13搜索](085-13-search-tools.md)已接rg获准分组/fd流式、协议/跳过预算、严格设置/schema、async与本次取消协调；复核已有kind防文件变目录，删除旧预读/发现/扫描阈值/同步薄转发。新增38例+1条进程回归，全量1553/前端27及构建通过，无Windows/Linux实机证据。[085-14双Shell](085-14-dual-shell.md)已接公共Shell/继承环境、PowerShell、合流尾部/原始与条件文本归档及共享预算，删除旧环境开关和双流算法；新增40例，全量1593/前端27及构建通过。真实Bash3.2.57已验，缺pwsh，Windows/Linux实机待验。[085-15本机终检/Windows复盘](085-15-final-audit-windows.md)已完成：四例诊断回归、全量1597/前端27及构建通过；实际清理/保留项及Windows四项阻点见报告，原机修复/验收尚未完成。下一步先讨论Windows安全文件/数据库锁，文件属性/路径授权及长命令排队独立修复。准确范围和验证以各小Spec为准。

2026-10-16后续决定：用户取消模型配置私密权限门槛，load_model_catalog已删除模式位拒绝，正常OS读取、普通文件/内容校验与脱敏保留；不新增配置ACL检查。新增十例、全量1607/前端27及构建通过，Windows后续范围仅保留会话/归档/锁及其他已列阻点。

同日后续决定：会话库/锁/附属文件也取消私密模式位门槛，保留文件类型/链接检查、实例独占与保存/恢复校验；不新增ACL机制。当前无已确认的多进程共享同库需求，Web多会话/前台子Agent均不因此新增应用进程，锁策略本轮保持。

SubAgent按[Spec086附录六片](086-subagent-file-map.md#f-六个实施切片与验证)衔接基础注册/权限片；不把子Agent一次塞入085工具迁移。每片复用现有测试，新增模块级行为测试清单见附录。最终命令至少 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`、`.venv/bin/python -m pip check`；Web线协议变化时加现有前端test/build。Windows使用相应`.venv\Scripts\python.exe`及原生PS测试，不能把macOS绿色当Windows证据。每片还做git diff --check、500行和全链消费者检查。

原总设计阶段只产出设计核对、文件地图和可解析配置样例。2026-10-16起085-01/02/03A/03B/04/05获准分片实施，实绩与验证见各小Spec；其余目标、Pillow/解释器、真实配置和Windows验收仍未实施，不以局部完成宣布总Spec完成。

## 16. 依据与证据边界

源码基线：本项目当前工作区（最近产品提交db87428）；LangChain1.4.2/core1.6.6/LangGraph1.2.12；Pi本地a69bef789，相关上游核对main5ab7fe74；AgentX3fcfa0e。Pi的工具prepare/execute、公共错误文本、原工具集合引用及Operations用于职责参照；AgentX的Registry→Provider→Executor→doExecute链用于注册/执行分工，不当作Python框架API或相同批量权限实现。

框架依据：[动态工具与middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[多模态消息](https://docs.langchain.com/oss/python/langchain/messages)；当前本机factory.py确认wrap使空集合仍有ToolNode、after_model逆序，tool_error.py确认无过滤适用动态名称、GraphBubbleUp继续传播、回调None重抛。动态覆盖/局部HITL/批准恢复不重审及父退出与管道EOF分离均有此前离线探针，尚非本文产品实现。

其他一手依据：[Python公开子进程协议](https://docs.python.org/3.12/library/asyncio-protocol.html)、[Windows asyncio限制](https://docs.python.org/3.12/library/asyncio-platforms.html)、[taskkill](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)、[PowerShell启动](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh)、[Pillow图像接口](https://pillow.readthedocs.io/en/stable/reference/Image.html)。Shell/沙箱参考版本、Claude两阶段审查与平台边界见对应研究记录；不对闭源内部或本项目生产效果作未验证声明。
