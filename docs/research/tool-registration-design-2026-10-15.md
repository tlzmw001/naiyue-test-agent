# 工具系统目标设计：共享登记、会话活动集合与动态请求

> 2026-10-15整合：统一实施设计见[Spec085](../specs/085-tool-system-rebuild.md)及文件附录。本稿保留讨论顺序/参考证据；旧“必要文件/进程IO接点”、符号链接/Pillow待定、Edit超预算连带拒绝等候选不再作为当前决定。本次已确认按真实目标读写软链接、引入Pillow、保留精确编辑而只限制超预算模糊匹配；单行专用续读/同名替换卸载仍不列本次范围。产品尚未实施。

日期：2026-10-15。状态：设计讨论稿，未批准实现。用户要求先讨论注册架构，再比较现有工具与Pi的功能差距；当前不写产品/测试/配置、不加载项目插件、不进行真实模型请求。

最新需求覆盖首轮候选限制：用户明确需要运行中注册工具、更新活动工具，以及自动调整相关提示词；允许分期实施，但本轮须讨论整体目标架构。撤回“注册表只在启动时准备、创建后保持不变”及“动态提示词暂不需要”的候选。模型配置目录继续按启动快照加载，用户接受暂不热重载；不能据此推断Agent图创建后所有请求参数都不可变。

最新复核方向已获用户认可（2026-10-15；仅设计认可，未授权实现）：App继续复用编译图；工具定义注册表也由App共享，各会话单独保存活动名称。此前“每会话必须有注册表”的理由不足，撤回该默认建议。Pi确有工具列表/系统提示词随轮次固定和刷新机制，但没有独立ToolSetSnapshot类；本项目需要保留相同的一致性职责，不据此强制新增快照管理器、版本存储或持久缓存。固定审批适配入口按本批已保存决定调用局部官方HITL的方向沿用；具体接口、字段及首期替换/移除范围继续讨论。

收尾复核：[Operations与沙箱边界](tool-sandbox-boundary-2026-10-15.md)已核对原粘贴方案及Pi上游main5ab7fe74。Gondolin为微型VM扩展示例，不能与Bash的OS沙箱混同。鉴于用户明确后续沙箱场景，现建议重新审视下文早期“暂不加operations”的候选：当前只提取必要文件/进程IO接点并接宿主适配，后续环境按会话/批次取得；不预建完整VM平台。此为本轮新建议，尚未批准实现，其他已确认主架构不重开。

## 1. 本轮边界

- 工具来源第一期仅可信Python代码；目标后端包括运行中登记/替换/停用及活动工具切换。项目目录扫描、模块自动import、插件/MCP加载属于后续来源适配，项目代码加载须先有工作区信任。
- 继续使用LangChain create_agent/ToolNode执行工具；沿用已确认ToolException/ToolError反馈、权限模式、批量审批及串行/并行合同。
- 动态工具提示词已是本次目标。具体工具功能差距仍按read/ls/find/grep/write/edit/bash另行核对；不因此提前实现图片、通用执行后端或插件平台。

## 2. Pi源码事实

本机Pi a69bef789，相关源码本轮无未提交修改；以下是源码事实，没有运行Pi或请求供应商。

- ToolDefinition定义name/label/description/TypeBox parameters/execute，另有可选executionMode、promptSnippet、promptGuidelines及渲染字段。不是只有一个执行函数，也没有本项目所需的统一文件权限字段。
- 两个入口：SDK的customTools: ToolDefinition[]直接传到AgentSession；扩展的pi.registerTool(definition)写入extension.tools并触发refreshTools。因此代码传入工具与项目扩展发现是两个不同入口，不必先建设插件加载才能支持自定义工具。
- AgentSession._refreshToolRegistry合并内置定义、扩展定义及SDK customTools，维护定义和可执行工具的映射；wrapRegisteredTools → wrapToolDefinition生成AgentTool，执行时才通过runner.createContext取得扩展上下文。
- setActiveToolsByName把选中的AgentTool放进agent.state.tools；streamAssistantResponse使用context.tools装配模型请求，工具调用则由prepareToolCall查找/校验，再由executePreparedToolCall调用tool.execute，转回原ToolDefinition.execute。
- Agent.createContextSnapshot复制tools/messages的顶层数组，保存systemPrompt；AgentSession._installAgentNextTurnRefresh在一轮工具完成后提供新的tools.slice()及提示词。runLoop在本轮模型调用和工具执行中使用同一个currentContext，到prepareNextTurn返回后才切换。工具对象本身没有深复制；源码支持轻量的每轮视图，不支持“Pi没有这个机制”的说法。
- AgentSession拥有Agent及_toolRegistry；registry中的工具经过绑定ExtensionRunner的包装，内置定义也会捕获会话cwd等依赖。这解释其会话归属，但不证明其他运行时必须复制同样的对象归属。
- 去重/覆盖语义须分层：同一扩展的Map.set可覆盖同名项；跨扩展getAllRegisteredTools保留首个同名定义；AgentSession合并内置与自定义时自定义可覆盖内置。不能把Pi概括为“所有重名启动报错”。本项目建议继续重名即报错。
- 项目资源加载有独立信任阶段：loadProjectTrustExtensions先禁用项目信任进行bootstrap，reload等待resolveProjectTrust，再按信任状态解析资源。这里只借鉴“注册工具与项目代码加载分开”，不复制其完整资源体系。

调用链：

```text
SDK customTools / 扩展registerTool
  → AgentSession._refreshToolRegistry
  → wrapRegisteredTools → wrapToolDefinition
  → setActiveToolsByName → agent.state.tools
  → 模型收到工具定义，返回toolCall
  → prepareToolCall → executePreparedToolCall
  → 包装后的AgentTool.execute → 原ToolDefinition.execute
```

源码入口（根目录/Users/zmw/naiyue-personal-workspace/pi）：

- packages/coding-agent/src/core/extensions/types.ts:449、:1558
- packages/coding-agent/src/core/extensions/loader.ts:289、runner.ts:451
- packages/coding-agent/src/core/agent-session.ts:939、:2588
- packages/coding-agent/src/core/agent-session.ts:540、packages/agent/src/agent.ts:437、packages/agent/src/agent-loop.ts:193、:229、:301、:607
- packages/coding-agent/src/core/tools/tool-definition-wrapper.ts:5
- packages/coding-agent/src/core/sdk.ts:75、:393
- packages/coding-agent/src/core/resource-loader.ts:380
- packages/agent/src/agent-loop.ts:281、:600、:670

## 3. AgentX源码事实

本机AgentX 3fcfa0e，相关源码本轮无未提交修改；核对内置工具提供者这条链，不将它等同MCP或任意用户代码插件体系。

- BuiltInTool注解是Spring组件；BuiltInToolRegistry.initialize通过ApplicationContext发现已注册的提供者，按enabled及priority收集。
- BuiltInToolRegistry自身也是Spring @Component，默认共享；createToolsForAgent(agent)从共享提供者集合为指定Agent构造适用工具。此处的Agent不是本项目的会话ID，不能说其已实现完全相同的“共享注册表＋会话活动集合”，但说明定义/提供者共享与按上下文选择并不矛盾。
- provider.defineTools(agent)返回ToolDefinition；该对象携带名称、说明、参数，可转换为LangChain4j ToolSpecification。
- createToolsForAgent(agent)调用provider.supports及createTools，为当前Agent得到Map<ToolSpecification, ToolExecutor>。
- AbstractMessageHandler.buildStreamingAgent把映射交给AiServices.tools；外部ToolProvider走另一个入口。
- AbstractBuiltInToolProvider.createToolExecutor的lambda把实际请求转给executeTools，再解析参数、检查支持范围并调用具体doExecute。RagBuiltInToolProvider是已核对的实际提供者。
- 注册器对提供者创建失败会记录并继续；这里不采用该行为作为本项目合同。本项目要先知道完整实际工具集合、再校验权限规则，注册非法应明确终止装配。

调用链：

```text
Spring中的@BuiltInTool提供者
  → BuiltInToolRegistry.initialize
  → createToolsForAgent(agent)
  → provider.defineTools / createTools
  → ToolDefinition.toSpecification + ToolExecutor
  → AiServices.tools
  → ToolExecutor → executeTools → 具体doExecute
```

源码位于/Users/zmw/AgentX/AgentX/src/main/java/org/xhy/application/conversation/service/message：

- builtin/BuiltInToolRegistry.java:40、:104
- builtin/ToolDefinition.java:36
- builtin/AbstractBuiltInToolProvider.java:39、:83、:117
- builtin/rag/RagBuiltInToolProvider.java:24、:39、:85
- AbstractMessageHandler.java:470

## 4. 本项目已有什么

- BaseTool已经保存name、description、args_schema及func/coroutine；@tool可创建它。create_agent把本地工具交ToolNode并从tools_by_name向模型提供工具，不需要项目另写执行分发器。
- Spec046已实现attach_approval/ApprovalPolicy、approval_policies_for_tools及一次性grant；合成自定义工具无需增加runtime工具名分支就能暂停/恢复。原附件“注册时没有校验”不符合当前代码：重复名称、非法审批metadata已经在装配时拒绝。
- 执行模式、审批描述、Read权限回调分散在metadata不同键及多个入口；CLI与Web分别拼同一组内置工具。这里的改进目标是一个明确的项目接入契约与一致装配，而不是从零新增自定义工具能力。
- 当前具体工具位于agent_runtime/tools；已认可向独立tools包归组，注册描述也应跟工具能力一起归组。本轮不实际搬迁。

## 5. ToolSpec与注册表：修正后的目标职责

### ToolSpec：持有原工具对象及项目需要的描述

| 信息 | 来源/消费者 | 推荐边界 |
|---|---|---|
| tool | 原BaseTool；交给create_agent/ToolNode | 名称、说明、执行函数从它取得，不复制工具，不另存可漂移的同名字段 |
| input_schema | 真实公开输入模型；权限前置参数检查及规则字段校验 | 普通工具可复用原args_schema；含runtime等注入字段时明确给出公开基础模型引用，不从tool_call_schema重建弱化约束 |
| execution_mode | 工具作者声明；现有ToolBatchMiddleware | parallel/sequential，集中校验；是否要求每个自定义工具显式填写待本节确认，不擅自改变现有默认规则 |
| permissions | 工具的资源/审查对象描述及默认策略；统一权限管线 | 普通工具可先只按工具名/参数审查，默认Ask；文件工具声明路径参数及操作，复用公共路径准备和保守Allow/Ask策略 |
| prompt_snippet | 工具作者提供，可选；动态提示词装配 | 工具可用性的一行摘要；只在工具处于活动集合时加入 |
| prompt_guidelines | 工具作者提供，可选；动态提示词装配 | 使用指导条目；按本轮活动集合重新组合，不逐轮向历史消息累计追加 |

字段名、构造入口均为候选。Python工具的input_schema保存现有Pydantic类引用；例如ReadInput是公开约束来源，SessionReadInput继承它补runtime，两个模型不重复编写业务约束。运行层经ToolSpec.validate_input(args)取得生效参数，不直接绑死在Pydantic API；未来外部来源适配器也须提供公开schema与相应验证入口，不为MCP等来源重写整个调用管线。注册能检查定义与引用是否合法，但不能凭JSON schema证明任意自定义验证器/执行函数语义等价。

实际调用方：请求准备使用活动工具及提示词元数据；权限middleware使用输入与权限描述；批次middleware使用执行模式；框架使用原BaseTool。普通自定义工具不增加独立错误格式器、异常子类或风险注解；具体错误用ToolException，意外异常走统一ToolError。Bash过渡例外随Bash设计收口，不扩大成另一套自定义错误机制。注册后的定义按合同视为不可变；变更通过显式替换提交新ToolSpec/工具对象，不能原地改旧BaseTool的名称、schema、描述或执行函数。

### ToolRegistry：App共享定义，会话独立选择（复核后建议）

- App拥有一份可信代码工具定义注册表，各会话只保存独立的活动名称集合。共享已登记定义不意味着全部会话自动启用新工具；登记和对某会话启用是两个明确操作。
- 工作区、权限模式、单次授权及会话资源仍按会话/运行取得，不能放进共享工具对象的可变字段。现有ToolRuntime/调用上下文优先承担依赖传递；若某工具确需独占客户端，再按该资源实际生命周期绑定，不为全部工具复制注册表或预建工厂平台。
- 统一登记ToolSpec并检查名称唯一、描述/输入/权限引用有效、执行模式合法，以及已确认错误反馈合同。register遇到同名报错；replace是明确的变更操作，不借注册静默覆盖。
- 启动时结合初始实际集合校验Allow/Deny配置，错误抛异常终止；运行中登记时对新定义及现有适用规则做同等校验，验证成功后一次发布。失败保留旧集合，不半登记；未命中规则仍按默认Ask/既定模式处理。
- 注册表提供定义查询与工具视图，不执行工具；框架适配器把选中BaseTool交回ToolNode。注册、是否活动、是否获准执行是不同状态；登记工具不绕过权限，未在本轮视图中的名称不能仅凭模型猜出就执行。
- 内置工具提供共享集合入口，CLI/Web复用；暂不为沙箱/远程执行预建通用后端平台，执行函数/工厂仍可注入必要IO操作。

建议登记控制入口由App拥有：register_tool(spec)、replace_tool(spec)、unregister_tool(name)；会话选择入口为set_active_tools(session_id, names)、get_active_tools(session_id)，另提供get_registered_tools()。未知活动名称报错；这些是可信应用控制操作，不自动作为主模型可调用的工具暴露。具体首期是否包含同名替换/移除、字段和签名仍待确认；用户已明确要求的是运行中登记及活动集合切换，不把探针覆盖的热替换自动升级成首期必做项。

正常例子（示意，尚未实现）：用户代码定义preview_report(filename)，用@tool保留名称/说明/执行函数；登记执行模式parallel、公开输入模型、文件路径参数filename和read操作。注册后自动进入同一权限管线、批量审批、调度、ToolError，无需修改通用runtime、CLI/Web各自的工具名分支。

```text
可信应用代码提供内置与自定义ToolSpec
  → ToolRegistry校验、收集本次实际集合
  → 权限配置结合工具与schema校验
  → create_agent一次注册固定的工具适配/权限/审批/调度hook
  → 每轮请求准备活动工具视图，提供BaseTool及对应提示词
  → 主模型提出调用
  → 参数/目标准备 → Deny/Allow → 默认策略与模式
  → 需要时整批审批 → 现有调度 → ToolNode执行原工具
  → 统一反馈 → 主模型继续
```

失败例子：重复登记read或路径参数拼成filenmae，启动装配报错；模型调用缺少filename则走既定参数错误反馈，不转人工；实际读取失败由工具准备ToolException提示，意外异常交统一ToolError。注册声明不自动约束任意Python内部所有IO；第一版代码提供者须可信，文件工具仍按实际目标与授权检查执行，不宣称ToolSpec提供沙箱。

### 职责布局与迁移要求

- 候选把ToolSpec和ToolRegistry集中在tools/registry.py，复用工具包的内置集合入口；权限描述归已讨论的tools/permissions.py，运行编排归agent_runtime/tool_execution。最终导入布局须核对Bash及资源依赖，避免runtime为读取描述而加载具体内置工具。
- 如果采用ToolSpec作为唯一项目注册合同，同一份执行/权限信息不能同时由metadata与ToolSpec维护。旧attach/读取/校验路径及消费者必须全链路迁移清理；不为历史测试留下第二套内部注册方式。
- 应用装配入口拟接收ToolSpec初始集合；App处理共享登记，SessionService处理会话活动选择。LangChain create_agent仍使用框架的BaseTool请求接口。项目内部旧metadata入口随批准后的合同迁移清理；公开入口的行为变更须在实施Spec明确，不为候选文档擅自改签名。
- execution_mode拟保留当前缺省parallel的行为；需要串行的工具显式声明sequential。调度规则不变，改的是模式与工具定义的读取来源。此缺省为建议，最终字段合同仍须确认。

## 6. 每轮工具视图：动态工具的一致性边界

保留轻量的本轮工具集合：从共享定义表和本会话活动名称选择ToolSpec引用，并以该集合生成提示词。模型schema、工具提示词、权限对象、审批、调度、实际执行和预算估算消费同一集合；BaseTool不复制。是否使用一个小数据类由实施Spec按消费者决定，不先强制新增ToolSetSnapshot类、注册表修订体系或独立管理器。

- 本轮模型请求准备开始时固定视图；首轮准备早于现有自动摘要/预算步骤，后续轮在before_model准备。首轮before_model复用已准备视图；模型重试/溢出恢复及当前工具批次不换视图，避免模型见到一个版本、执行另一个版本。
- 运行中登记更新App定义表；某会话的启用/停用更新该会话选择，下一份工具视图生效，不改变已发送模型请求、当前审批和已开始工具。若最终纳入替换/移除，等待审批时原引用必须保留，不能把旧调用换成新实现；若首期不支持替换，就不引入对应版本管理。
- Pi已确认每轮上下文快照、下一轮刷新、同一上下文供模型/工具执行；本项目额外有checkpoint审批暂停，需要使本批引用在暂停期间有效，但不能声称Pi已提供完全相同的持久化恢复合同。
- 权限模式仍沿既定合同在本批权限审查开始时固定；工具视图更早在模型请求准备时固定。两者边界各自明确，不将会话模式切换改成当前批次生效。
- 停用工具只影响后续请求，不删除历史ToolMessage；取消当前调用继续使用既有停止控制，不通过注册表变更隐式取消。
- checkpoint/DB保存活动名称及恢复确实需要的可序列化标识，工具函数/客户端/模型类留在运行时。不先建设完整版本存储；若纳入定义替换，跨重启缺少原定义时不能静默执行同名新实现。当前受限恢复合同不因动态工具而自动扩大。

正常例子：App登记read/find/preview_report；会话S只启用read/find。S启用preview_report后，下一轮模型schema与提示词同时出现它，权限/批次执行从同一集合取得定义；其他会话保持自己的活动集合。v1审批期间替换为v2的探针只是额外能力的可行性证据，是否纳入首期另定。

Agent归属比较：Pi Agent本身持有消息、工具、模型及活动运行状态，Session持有它便于切换/释放；本项目App持有编译图，消息/checkpoint由thread_id隔离，模型、工作区及控制由SessionService按会话解析。当前建议保留App共享图，不能在共享middleware实例字段中保存某会话的活动工具/审批状态。改成每会话编译图不会自动解决图创建后新增工具、动态提示词或HITL名称映射，仍需动态适配或重建图；没有当前证据需要此迁移。

## 7. 固定框架适配与必须同步的消费者

### ToolSetMiddleware（候选名）

一个固定注册的middleware承担工具视图准备及框架接入，不创建第二个Agent循环：

- before_agent/before_model准备或复用本轮视图；图state仅记录可序列化引用。
- wrap_model_call/awrap_model_call把本轮BaseTool列表及按活动集合构造的system_message交给handler。提示词组成为基础指令＋项目指令＋本轮工具片段/指导，重新装配而非累计追加。
- wrap_tool_call/awrap_tool_call按本轮视图确认名称并通过request.override(tool=原BaseTool)交框架handler；ToolNode继续负责注入、执行与消息配对。未知/不活动名称返回未执行的工具错误，不从当前最新注册表临时捞工具绕过本轮视图。
- 与现有选模/项目提示词/预算门禁的顺序一起明确：预算须看到最终tools与system_message；不能重复追加项目指令或工具指导。

### 审批复用

现有HumanInTheLoopMiddleware的interrupt_on是创建实例时的名称映射；未在其中的名称默认不询问，不能只让模型收到新工具。建议保留独立权限准备节点与审批节点，审批适配入口按已存本批视图/决定准备局部配置，并调用官方HITL的aafter_model/after_model。不修改共享实例的interrupt_on，不动态向图插入middleware，不重写interrupt/拒绝消息/恢复协议。只有approve/reject，继续复用原应用决定与grant合同。

具体例子：图创建时只有read；之后App登记preview_report并对会话S启用。主模型返回c1=read(workspace/a.md)、c2=preview_report(outside/r.txt)。前置权限节点保存c1=Allow、c2=Ask；Auto审查如有需要在此完成。固定审批入口按本批名称构造局部官方HITL对象，when通过tool_call_id读取已保存结果，仅c2需要人工。官方hook产生包含c2的批量interrupt，整个工具批次零执行；批准恢复后该审批入口从头重入、重建同样的局部配置，官方interrupt取得恢复决定，随后才进入调度和ToolNode。前置权限节点已完成并被checkpoint记录，不随审批节点重入，故Auto不重复调用。同名两次调用可有不同决定，不能只按工具名保存Allow/Ask。

局部HITL对象只是一次hook调用的配置载体，不创建Agent、不编译图、不注册新节点，也不调用审批模型。固定图中的适配节点负责把动态批次接到官方实现；其继承/组合写法与状态schema须在实施Spec明确。该接法已有本机假模型探针，尚未替换现有产品审批链。

### 其他消费者

| 位置 | 当前固定事实 | 目标来源 |
|---|---|---|
| ToolBatchMiddleware | __init__保存工具名/执行模式表 | 审批后本批视图中的待执行定义，串行/并行算法不变 |
| 权限/审批展示、grant | 启动时收集ApprovalPolicy映射 | 本批视图和已保存审查结果；恢复不重跑Auto |
| 官方ToolErrorMiddleware | 原Spec084按启动工具名筛选 | 动态普通工具使用tools=None及统一回调；Bash过渡排除随Bash设计收口 |
| context_compaction | 工厂捕获固定tools/system_prompt | 同一次请求准备好的视图及组合提示词；摘要算法/触发合同保持 |
| context_observation/recovery | 已消费request.tools/system_message | 保证在动态装配之后看到准确请求；溢出重试保留视图 |
| 会话存储/恢复/界面 | 模型ID/工作区等 | 同步工具选择与来源标识，运行时对象不序列化 |

空工具图的候选也须更新：固定注册工具wrapper会使create_agent即使tools=[]也创建tools节点，这是运行中新工具的执行入口。旧Spec084“空工具不额外创建ToolNode”前提被新需求覆盖；当前活动集合为空时不向模型发送工具定义，普通文本轮不执行工具节点。静态图节点存在不等于工具已经启用或执行。

## 8. 推荐分期与稳定接口

以下为目标提案，不是实施授权：

1. 核心后端：统一ToolSpec、App共享登记/会话活动集合、轻量本轮工具集合、动态schema与提示词，以及可信代码运行中登记；同名替换/移除是否首期纳入另定。权限/审批/调度/错误/上下文预算一起迁到该事实来源。保留既定权限、批量等待和串并行规则，修复已登记同步调度缺陷；同步会话字段的序列化/存储合同。
2. 用户入口：CLI/Web列出已注册/活动工具，切换集合、展示下一轮生效；模式切换展示及Shift+Tab按已记录需求适配。后端接口沿用第一期，不再发明前端专用工具执行路径。
3. 来源扩展：完成工作区信任后接项目插件；MCP等来源通过适配器形成同一ToolSpec并登记。资源创建/关闭归来源适配器，停用不提前关闭仍被在途视图引用的客户端；实际关闭/取消沿既有IO收尾合同。此期不改变主模型—工具循环，也不把自动执行任意项目Python作为信任实现。

第一期涉及tools、agent_runtime、application、存储及测试，明显超过8个文件，属于跨模块迁移，实施前须合并本轮所有已确认设计写完整Spec。没有新增框架、服务、SDK、账号或凭证；不创建空插件包或未接线的平台。稳定边界是ToolSpec描述/输入验证、共享登记/会话选择、本轮集合与框架适配，后续来源接入沿这些入口扩展；不承诺所有未知未来需求都零修改。

动态普通客户端工具与图控制能力分开：本轮已验证普通工具执行、审批与错误反馈；框架ToolStrategy结构化终局和return_direct的静态路由存在额外约束，不把它们自动当作新工具注册已支持的能力。遇到具体需求再验证控制协议，不为其预建新循环。

## 9. 验证与限制

- `.venv/bin/python -m pytest -q tests/test_tool_approval_registration.py` → `4 passed in 0.80s`，只证明既有自定义工具/审批/重复名等合同，不代表ToolSpec/ToolRegistry已实现。
- 只构造BaseTool的本机探针确认：工具已保存name/description/真实模型/异步函数，runtime不在公开工具schema中。
- 进一步执行探针发现：把不含runtime的严格extra=forbid模型直接作为执行args_schema，ToolNode注入runtime后会触发参数错误，工具正文不执行；不能凭schema构造成功就宣称执行接线通过。
- 复用项目既有“公开基础模型→含runtime的执行模型”继承结构后，纯假模型create_agent/ToolNode执行通过，可信上下文与调用ID注入、正文只执行一次、公开模型拒绝错类型及未知字段。该探针使用未参数化ToolRuntime时有Pydantic context类型序列化警告；正式项目已有ToolRuntime[dict, dict]注解，本轮未修改产品或为此新增包装机制。
- 没有真实供应商请求、项目插件加载或新注册模块；参考项目源码提供组织方式证据，不证明本项目已获得对应能力。

最新动态可行性探针（纯假模型、真实create_agent/ToolNode、内存checkpoint，无产品/测试文件变更）：

- 同一张图创建时tools=[]，之后才登记late_probe；wrap_model_call提供它，wrap_tool_call通过override(tool=...)交回ToolNode执行，图对象未重建。
- 每轮同一视图生成schema和工具指导；停用后下一次模型请求两者同时消失；早先探针按会话定义/选择模拟隔离，B猜出A的工具名只得到未执行的error ToolMessage。这证明执行边界可行，不证明注册表必须归会话；共享登记方案仍须验证各会话活动集合隔离。
- 会话A的v1工具等待审批时替换为v2并停用；批准后仅v1执行，下一轮使用新活动集合。执行记录仅[('v1','A')]，绑定名称轨迹[['late_probe'],[],[],[]]。
- 官方ToolErrorMiddleware(tools=None)捕获后注册工具的ToolException，保留具体提示并让主模型继续。
- 固定HITL适配节点逐批创建局部官方HITL配置并调用其公开hook，后注册工具正常中断/批准/执行；轨迹prepare_review_once→read_saved_decision→[暂停]→read_saved_decision→tool_execution，前置审查1次，无共享interrupt_on修改。
- 上述探针分别证明当前框架接口可行，不等于本项目所有动态消费者、并发、持久恢复或真实供应商已验收。重试、停止、模型输入预算及跨会话并发仍须在实施Spec列为集成验收。

官方参考：[LangChain Tools](https://docs.langchain.com/oss/python/langchain/tools)、[Custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)。本机版本LangChain1.4.2/LangGraph1.2.12，执行/注入细节以本机源码与探针为准。

## 10. 注册与启用的界面参照（本轮复核）

- Pi：开发者用customTools/registerTool提供定义；CLI --tools选择活动名称。仓库examples/extensions/tools.ts提供可安装的/tools选择器，逐工具enabled/disabled并把选择存到会话分支；不是默认内置命令。底层仍调用getAllTools/setActiveTools，界面不重新注册执行函数。
- Claude Code CLI：claude mcp add配置工具来源，/mcp查看状态、认证和启停服务器；当前文档的启停选择按项目保存，不能描述为每会话逐工具开关。Desktop对local/SSH会话可从输入框+→Connectors添加连接，设置页管理；插件另有安装/启停入口。[MCP官方文档](https://code.claude.com/docs/en/mcp)、[Desktop官方文档](https://code.claude.com/docs/en/desktop)。
- ChatGPT：当前官方资料介绍Plugins目录安装/连接外部服务，在聊天中描述需求或用@选定插件；部分账号入口仍叫Apps。安装指南要求安装后开启新聊天，不能拿它证明当前聊天里可任意热注册Python工具；公开界面也不能证明服务端Agent/注册表归属。[Plugins](https://learn.chatgpt.com/docs/plugins)、[Build plugins](https://learn.chatgpt.com/docs/build-plugins)。
- 本项目建议：第一期代码登记，界面只展示可用工具及来源/说明，并为本会话勾选活动工具；下一轮生效，未启用不等于Deny，启用也不等于本次获准执行。后续支持插件/MCP时再加来源管理/连接页面，由适配器登记ToolSpec；当前不增加在网页输入Python代码的功能。界面形态为建议，不是已实现或用户已确认。

## 11. Read功能对照与图片链路讨论

用户2026-10-15确认/认可的方向，仍未授权实现：

- 有需要时补路径兼容；具体包括哪些输入形式仍逐项确定，不把Pi所有平台兼容无条件纳入。
- 出现超长行时先返回前面已经取得的完整行；用户进一步提出起始/唯一一行超长时返回约64KiB内容，方向待细化。UTF-8字符不能从多字节中间切断，且现有行offset不能继续同一行的剩余部分；是否只取前缀或增加行内续读能力尚未确认。
- 补必要的截断/续读信息，改善文件不存在、非普通文件、读取失败和权限拒绝等具体原因反馈；统一ToolException/ToolError方向不变。
- Read专用前端展示后排；本轮优先讨论后端图片通路，不实现。

Pi源码事实（a69bef789）：read.ts识别图片MIME→读取二进制→processImage处理格式/尺寸→返回text+image内容块；image块持data(Base64)/mimeType。Agent Loop把它放进同toolCallId的toolResult.content，convertToLlm保留该消息，再由供应商适配器转换请求。图片数据不是放入普通文本，也不是交给Read做OCR。

- OpenAI Responses：openai-responses-shared.ts:76的convertToolResultOutput把图片变成input_image，作为function_call_output.output数组的一部分；按model.input判断图片能力。
- Anthropic：anthropic-messages.ts的convertContentBlocks/convertToolResult将图片变成base64 source，放进user消息中的tool_result.content。
- Chat Completions：openai-completions.ts:1329保留文本tool结果，并在连续工具结果之后增加承载图片的user消息；不是直接把图片塞进原生tool消息。
- Pi缩放实现默认最大2000×2000、Base64负载目标4.5MiB；这是Pi当前默认值，不直接成为本项目已确认配置。摘要serializeConversation只提取文字，不再次把原图交给摘要模型；图片预算则使用粗略估计。

本机框架验证：LangChain1.4.2/core1.6.6/openai1.6.6/anthropic1.7.5/LangGraph1.2.12。工具直接返回标准text+image(Base64/mime_type)列表，真实create_agent/ToolNode保持ToolMessage内容与tool_call_id，第二次假模型输入及checkpoint仍有图片；当前应用及middleware正常短链也通过。没有产品/测试文件变更，无真实供应商请求。

- 本机ChatOpenAI Responses payload得到function_call_output.output=[input_text,input_image]；ChatAnthropic得到tool_result.content=[text,image]。这只证明本地转换，不证明任意模型/网关支持视觉。
- 本机ChatOpenAI Chat Completions payload仍将[text,image_url]放在tool角色；官方该角色schema只允许文本，不能据此宣称已端到端支持。建议请求准备时采用Pi式兼容转换，不修改图和持久历史；供应商格式转换归模型接入职责，Read只返回统一内容块。兼容生成的图片消息不应成为会话中的新用户turn。
- 后续官方能力复核：LangChain已有标准多模态工具返回及ChatOpenAI的use_responses_api=True原生Responses路径，该路径无需自写图片工具结果拆分。当前langchain-core1.6.6的convert_to_openai_messages实测仅把图片块转换为image_url，仍只输出一条tool消息；当前Chat Completions默认路径没有Pi式自动拆分。用户后续已确认支持Chat Completions图片兼容，具体接线与配置见第12节；Responses/Anthropic仍复用框架原生转换，不能把仅支持Chat Completions的网关直接强切Responses。
- 当前context_compaction._validate_removed_prefix拒绝非str内容；实际对带图片ToolMessage的历史调用已复现unsupported_summary_content。图片后端须同时调整前缀校验和摘要序列化；可参考Pi只摘要文字/必要图片说明，不能声称保存全部视觉细节或把Base64作为普通摘要文本。
- 现有count_tokens_approximately已识别图片、不按Base64字符长度计数，默认每图85tokens；Pi约1200tokens。这两者均为估计，图片预算需在现有上下文入口明确口径，不另建预算系统。
- ToolMessage/checkpoint本身可保存图片块；正式异步SQLite恢复、事件大小和模型切换仍需集成验收，不提前新增图片存储平台或数据库列。
- Web现有文本投影保留文字说明、忽略图像，仅影响界面展示，不会从后端模型历史删除图像；可以先保持此展示，图片预览/专用卡片后做。
- 图片格式识别、独立的图片大小/尺寸限制及必要缩放属于Read工具职责；不沿用文本64KiB切分图片。项目venv当前没有Pillow：基础编码可用标准库；若要对齐Pi缩放/格式转换，需要另行确定图像处理依赖，本轮未安装。

建议影响位置：当前agent_runtime/tools/read.py（迁移后tools/read.py）；model_access及现有模型请求准备入口（模型能力、必要的协议兼容）；context_observation/context_compaction（图片预算、压缩）；既有checkpoint/事件/恢复集成验证。无需新Read工具名称、第二个Agent循环、图像专用审批或把所有工具返回值改成图片结构。只有图片分支返回多模态列表，普通文本仍可返回str。

官方对照：[LangChain多模态工具返回](https://docs.langchain.com/oss/python/langchain/tools#return-multimodal-content)、[标准消息内容块](https://docs.langchain.com/oss/python/langchain/messages#multimodal)、[OpenAI函数结果](https://developers.openai.com/api/docs/guides/function-calling)、[Chat Completions schema](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。图片读取的具体格式/缩放默认值及超长单行续读接口继续讨论；未知模型能力策略见已确认的第12节。

## 12. 已确认：模型接口、图片能力与请求适配

用户2026-10-15确认按下列方案设计，仍未授权实现：

- 每条openai/openai_compatible模型配置显式填写api，值为chat_completions或responses，不提供auto；创建ChatOpenAI时分别设置use_responses_api=False/True。现有模型配置在实施时补齐，当前不修改.env。anthropic/ollama继续由现有适配器类型确定原生接口，不要求多余的api配置。接口选择与图片能力分别判断，服务地址、账号及网关实际支持仍须核验。
- 配置开放profile.image_inputs布尔字段，复用框架的model.profile，不新增能力类或能力注册表。启动装配时将显式配置覆盖到框架默认profile，保留其他已有字段；未配置则使用框架资料，仍无记录则按能力未知处理，本次不发送图片并说明原因。配置装配完成后不在运行中修改共享模型profile，不引入模型配置热重载。
- Read/自定义工具返回统一文字和图片块，ToolNode/checkpoint保存原始ToolMessage。协议适配位于模型接入职责，不在Read内判断供应商；Responses、Anthropic复用框架原生转换。Chat Completions保留文字工具结果及tool_call_id，在同组所有工具结果之后追加承载图片的user消息，注明图片来源；该消息只用于请求，不变成持久历史中的用户turn。
- 对不支持图片/能力未知的模型，仅在请求中保留文字并明确图像未发送，不删除原始历史图像，不暗中调用OCR或另一个视觉模型。未被压缩移除的历史图片，在切换为视觉模型后可重新参与请求。
- 新增一个固定ModelInputMiddleware，同时提供同步/异步hook；位于现有超限恢复中间件内侧、请求观测/预算统计外侧。恢复层持有原始消息，超限压缩后重试会再次适配；压缩结果写回状态时不得混入临时图片承载消息，也不得重复追加图片。
- 建议新增model_access/messages.py，集中prepare_model_messages和薄框架适配；修改model_access/__init__.py的load_model_catalog/_build_model、agent_runtime/__init__.py装配、Read图片分支、context_compaction的前缀校验/文字序列化，以及context_observation中的图片预算与安全诊断。保持模型目录/会话选模主接口、权限审批和工具调度职责；验证同批配对、切模、超限恢复、异步SQLite/checkpoint与事件尺寸。
- 读取损坏图片等执行错误走具体ToolException→统一ToolError；图片能力不足走请求文字说明；配置与服务实际能力不符导致的模型请求失败由既有模型错误链处理，不自动换协议重发。

已核验的依据：Pi Model.api/input、openai-completions.ts连续工具结果后的图片消息，以及本机框架profile/use_responses_api。离线探针中gpt-5.5的image_inputs=True；经ChatOpenAI创建的glm-5.3/glm-5.3-flash无内置能力记录；显式False/True分别得到messages/input请求结构。均无真实模型请求。

GLM官方资料边界：glm-5.3当前仅文本输入；glm-5.3-flash/flashx支持图片，并非整个GLM系列能力一致。[GLM-5.3](https://docs.z.ai/guides/llm/glm-5.3)、[GLM-5.3-Flash/FlashX](https://docs.z.ai/guides/vlm/glm-5.3-flash)、[LangChain model profiles](https://docs.langchain.com/oss/python/langchain/models#model-profiles)。此处记录文档事实，不等于本项目已完成这些服务的端到端验收。

## 13. Read之外的只读工具盘点：Grep、Find、Ls

2026-10-15按用户要求继续盘点，排除Bash/Write/Edit；本节保留当时的源码事实与候选，后续Grep执行/递归授权已在第14—15节获确认，Find/Ls的新增改动继续讨论，不实施。项目当前独立工具共七个，排除上述三个后为Read/Grep/Find/Ls。Read已讨论的方向见第11—12节，本节聚焦其余三个。Pi依据仍为本地a69bef789的core/tools/grep.ts、find.ts、ls.ts、truncate.ts与utils/tools-manager.ts；项目依据为当前agent_runtime/tools同名模块。

| 项目 | 本项目现状 | Pi现状 | 意义/建议，尚未确认 |
|---|---|---|---|
| Grep主要参数 | pattern/path/glob/ignoreCase/literal/context/limit；默认100匹配、50KiB输出 | 参数相同，默认100匹配、50KiB输出 | 核心功能已具备，不增加新搜索工具 |
| Grep执行 | rg枚举候选→逐文件完整严格UTF-8/控制字节检查→每文件一次rg搜索 | 一次rg针对目标搜索并流式收集；需要context时再读相应文件 | 项目存在额外IO/进程开销，尚无性能基准；不能为提速绕过逐目标权限 |
| Grep额外预算 | 最多2000候选、单文件8MiB、累计32MiB；搜索阶段20秒，枚举/单进程另有超时；即使只请求1个结果也先检查候选数 | 已读工具代码没有对应候选数/文件大小/累计扫描量门槛，按匹配数停止子进程并限制输出 | 工作区增大时可能在搜索前整体失败；需讨论预算口径与部分结果/失败反馈，不能将扫描预算与输出截断混同 |
| Grep范围 | 当前隐藏/凭证文件沿用Read硬编码拒绝；只支持外部单文件审批，外部目录不开放 | --hidden，遵守rg忽略规则，无本项目同款工作区审批限制 | 新权限管线实施时需迁移这些旧限制；递归扫描不能只审核根目录而绕过内部目标Deny |
| Grep裁行/上限 | 每行2000字符；context最多10、limit最多1000；文本尾注 | 每行500字符；schema未给同款context/limit最大值；details另存原因 | 字符数/参数上限不是必然缺陷；优先说明触发的具体限制和可操作下一步 |
| Find搜索后端 | rg --files --hidden枚举文件，再用自写fnmatchcase/分段**匹配；先取完候选再截输出 | 默认fd --glob --hidden --max-results；也可由自定义glob操作替代 | 项目只返回文件；Pi默认路径可含目录并保留/。是否对齐目录发现、是否改用fd需另定，不预装依赖 |
| Find匹配语义 | 固定大小写敏感，不支持大括号选择；Grep glob复用同一实现 | Find沿用fd glob及默认smart case；Grep glob直接交rg | 自写glob与参考后端语义有差距；建议优先补清楚范围/错误行为，避免错误报告为无匹配 |
| Find限制/反馈 | 默认1000、最多2000结果、50KiB；候选输出上限8MiB；通用truncated尾注 | 默认1000、50KiB；fd侧提前限制结果；details区分结果上限/字节截断 | 建议细分原因和下一步操作，不因此立即增加分页接口 |
| Ls主体功能 | 默认500/最多2000、50KiB；隐藏名、大小写不敏感排序、目录/标记、空目录、条目stat失败跳过 | 同样默认500/50KiB、隐藏名、排序、目录/标记、空目录、跳过无法stat项 | 主体已接近Pi，优先接共同改动，不添加递归树/大小/时间/分页等Pi也未提供的功能 |
| 公共路径与错误 | Path相对/绝对路径；缺失/类型/权限原因有合并提示；工具各自ToolException/白名单 | 共享resolveToCwd支持~、@及Unicode空格等；更具体错误由公共执行边界转换 | 接第11节路径兼容和Spec084已确认错误统一；权限原因/工具能力/执行错误应分清 |
| 停止 | 三工具主体为同步函数，框架异步路径回落线程；内部未接本run停止信号 | Find/Grep有AbortSignal并kill子进程；Ls为异步IO/中止返回 | 调度可并行不等于运行中可中止。Find/Grep的子进程停止值得补，Ls普通IO的即时取消不作过度承诺 |
| 操作替换/展示 | 无内置I/O operations注入；统一文本展示 | Find/Ls有operations；Grep只替换目录判断/上下文读文件，rg仍本地spawn；各有details与专用renderer | 自定义工具注册不等于内置I/O可替换；当前无远程后端需求，不预建operations平台；专用界面后排 |
| 外部命令可用性 | Find/Grep只查PATH中的rg，缺失给出具体错误 | ensureTool查找fd/rg；非离线且平台支持时可下载，失败再报错 | 自动下载是产品/依赖管理选择，不能默认为功能缺陷或擅自引入 |

本轮本机证据：既有tests/test_grep_tool.py、test_find_tool.py、test_ls_tool.py共49 passed in1.07s；未改测试。临时合成目录探针确认：(1)Find pattern=*.{py,ts}与Grep glob=*.{py,ts}都返回无匹配，而rg原生glob找到两文件；(2)Find不能按目录名找到assets；(3)小写readme.md不匹配README.md；(4)2001个合格候选文件下，即使Grep limit=1也先返回search budget错误。未执行Pi的fd（本机PATH无fd）、未安装/下载工具，无模型请求。

阶段安排：Grep的扫描预算/执行方向及递归授权已由第14—15节收敛，接下来讨论Find的glob/大小写/目录合同；Ls仅补共同的路径/错误/截断信息。设计认可不代表授权实施，未确认的具体默认值和后端选型仍须后续确定。

## 14. 已确认：Grep扫描预算与执行方向

用户2026-10-15要求先按以下方向记录，仍未授权实现；递归目录授权的已确认合同见第15节。

- 取消2000候选文件、单文件8MiB、累计扫描32MiB这三项预先拒绝门槛；取消每个文件搜索前的整文件严格UTF-8/控制字符预检查。改为处理实际返回的匹配/上下文，无法正常表示时给具体反馈，不新增任意编码自动识别承诺。
- 候选流式处理，按本次许可范围过滤后对文件分组调用rg，取够结果即可停止候选与搜索；不再先收齐全项目文件或每文件启动一次搜索，也不为全局文件名排序阻塞早停。内部文件分组不改变外层整批工具审批、串并行与停止配对合同，不逐文件重新调用Auto。
- Grep glob采用rg原生语义，不再复用简化的_glob_matches作为其过滤语义来源。可使用现有rg -C上下文能力，达到匹配数上限时须取得最后一条所需上下文后再停，不必照搬Pi的额外整文件上下文读取。
- 保留总超时及用户停止，20秒作为首版总超时默认值，枚举、过滤、搜索和必要上下文共用截止时间，不给各阶段重新发一份完整预算。需要停止正在运行的搜索子进程，控制信号不转换为普通ToolException。
- 暂时保留默认100匹配、50KiB正文、每行2000字符，以及现有context/limit参数上限。输出尾注区分匹配上限、字节上限、长行裁剪及搜索不完整；到达匹配上限仅表示可能不完整，不保证后面一定还有结果。
- 正常无匹配是成功结果；输出上限/裁行返回正常部分结果和说明。总超时已有结果时返回明确不完整的部分结果；总超时尚无结果时用具体ToolException说明搜索未完成，不能宣称无匹配。非法正则、依赖缺失、真实进程/IO失败沿统一ToolError，不因已取得部分结果而静默吞错。
- 核心影响为重新组织_grep_files、替换_search_file的逐文件执行、移除_check_text预检查、接入运行停止；不新增工具名称、Agent循环或搜索结果类。完整实施Spec仍需覆盖递归授权、过滤、异步进程清理及现有消费者迁移。

依据是第13节源码盘点和临时目录探针，以及Pi grep.ts的单进程流式匹配/上限中止/AbortSignal链。此处是设计认可，非性能基准或新功能运行证据；没有新增产品/测试/配置或真实模型请求。

## 15. 已确认：递归目录授权与未搜索范围反馈

用户2026-10-15确认按下列方案记录，特别确认未授权的隐藏/隐私目标先跳过，并明确告诉模型哪些范围没有搜索。此为设计认可，尚未授权实现。

- “批准目录”明确表示批准当前Grep调用对该规范化目录树的递归正文搜索，受本次pattern/glob等参数、既有忽略规则及工具能力限制；审批展示须说明包含隐藏/默认需确认的隐私路径，显式Deny仍排除。不能只显示“访问目录”却隐含授予所有读取工具、写入或会话永久权限。
- 在该已批准范围内，普通、隐藏及默认判为Ask的隐私路径均已获得本次许可，不再次运行默认Ask/Auto/HITL。隐藏/隐私特征不是硬性Deny；若要禁止，用户通过适用于本次Grep的Deny规则表达。
- Deny作用于工具/操作及实际子目标，优先于配置Allow、会话Allow和本次批准。根目标/整个Grep已被Deny时拒绝本次调用，不提供可覆盖它的审批；仅部分子目标Deny时在交给rg前排除，目录分支能剪枝就不继续枚举，不为一个被排除子目标拒绝其他可搜索目标，也不在读正文后才过滤输出。
- 子目标不在规范化获准范围内（如符号链接实际指向目录外）时不能继承目录批准；第一版递归不跟随目录链接，实际目标超范围的候选跳过并说明，不在运行中的工具内补第二轮审批。显式新目标须作为新调用进入前置管线。
- 一次批准绑定当前调用ID、工具、规范化根、递归范围及参数；不等同工作区变更，不给Read/其他工具附带授权。“本会话不再询问”才生成界面明确展示范围的会话Allow，例如grep对该目录树的后续搜索；仍不可覆盖Deny或自动授予写权限。
- 现有一次性grant在Grep开始执行时核对并消费一次，本次执行局部持有已准许范围；不能在每个候选文件上重复consume。Auto也审查同一个完整递归范围，allow后不按每个子文件再次调用模型；执行阶段的实际子目标/Deny过滤不等于重跑审批。
- 执行中仅做实际范围和已加载规则的子目标匹配；不逐文件重跑整条审批管线，不重读配置、不调用模型进行隐私识别。隐私默认判断使用路径等已知特征，不能靠先读取正文才决定能否读取，也不保证识别全部敏感内容。
- 尚无明确递归许可的工作区普通搜索，不得把根路径默认Allow当作所有子目标都已获准。只搜索已Allow子目标，跳过仍需Ask的隐藏/隐私目标并说明范围不完整；确需这些目标时由主模型提出更明确的新调用走审批。不增加执行中HITL，不改变整批零执行等待的合同。
- 返回提示要区分“可搜索范围内未匹配”“因规则/未授权排除了部分范围”“没有任何可搜索目标”；不能把完整目录未搜索完写成整个目录无匹配。对已枚举且未获授权的范围，向模型列出可定位的相对文件路径/目录前缀及原因，不能只给“跳过若干文件”的数量；被剪枝目录只说明整个前缀未搜索，不进入目录枚举其内容。范围说明仍受输出预算约束，若列不全须明确说明未搜索范围清单本身也不完整，不能暗示已穷尽。不读取被排除正文；命中Deny目标的名称展示按对应操作规则处理，不额外泄露禁止展示的信息。

参考边界：Pi grep.ts使用--hidden，但permission-gate.ts仅处理少数Bash模式、protected-paths.ts仅拦截write/edit，未提供上述递归授权/逐子目标Deny合同。Claude官方Read规则适用内置Grep/Glob的说明和符号链接规则可作职责参照，但不是本项目已具备同等保证的证据：[文件规则](https://code.claude.com/docs/en/permissions#read-and-edit)、[符号链接](https://code.claude.com/docs/en/permissions#symlinks)。

## 16. 已确认：Find功能与fd后端

用户2026-10-15确认按下列方案记录，含fd外部命令依赖和匹配语义变更；仅设计认可，尚未授权实现、安装或下载。

- 采用Pi同款fd默认后端，支持文件及目录名称发现，目录结果保留/，结果以搜索根相对路径展示并保留既有特殊字符转义。保留pattern/path/limit三个参数，不新增文件类型、正则模式、分页或远程operations参数。
- pattern保持glob含义，交fd的globset引擎解析，支持*、?、**、字符类及大括号选择；非法模式返回具体ToolException，而非无匹配。沿Pi的路径模式转换：不含/按basename匹配，含/开启full-path，非绝对且无**/前缀的模式加**/。这是fd绝对路径匹配的适配，不能宣称严格锚定搜索根的相对匹配；如src/**/*.py也可能匹配搜索树内更深位置的src目录，返回相对路径与匹配锚点是不同职责。
- 沿fd默认smart case：全小写模式忽略大小写，含大写字符则区分。尊重.gitignore和fd支持的忽略文件；与Pi一样在非Git目录设置no-require-git。不把fd的.fdignore和rg的.rgignore描述为相同规则集合。
- 使用原生异步子进程流式接收，不先收齐所有候选；范围及可表达的目录排除条件在遍历前准备，流式结果再次核对实际目标及适用规则。limit统计权限过滤后实际返回的条目，因此不直接照抄Pi的--max-results=limit，达到返回条数/字节预算后主动停止。早停不承诺全项目文件名排序。
- 暂保留默认1000、最大2000条及50KiB正文；取消当前8MiB候选输出总量门槛。沿当前15秒作为首版整次Find超时默认值，接用户停止与进程清理。上限/超时的部分结果和真实进程错误分别处理，沿第14节已讨论的返回语义，不吞非零退出/IO错误，不把未完成搜索宣称为无匹配。
- Find列路径与Grep/Read读取正文是不同操作；复用规则优先级、审批范围、过滤与未搜索范围说明的机制，但使用Find自己的名称枚举权限判断。获得名称枚举许可不授予正文读取；适用于Find的Deny排除名称/目录分支，仍需Ask且未授权的枚举范围跳过并向模型具体说明。不能把Read正文硬编码限制直接复制到Find。
- 新增外部命令依赖fd（可识别发行版命令fdfind），第一版使用已安装命令，缺失时给明确ToolException；安装属于后续实施/环境准备范围，本轮不安装，也不默认自动下载或回退到rg以隐藏语义变化。无需新Python库、Agent循环或结果类。
- 迁移预计改造find.py的_find_files/搜索进程和错误反馈，撤除自写_glob_matches和仅服务旧链的_validate_glob/_discover_paths；当前Grep也引用这三者，须等Grep改用其新入口后按真实消费者一起清理，不能先删除造成断链。现有输入、名称转义、权限/配对和输出预算行为测试应迁移保留，Find目录/大小写/glob变更需要新增行为验收。

依据：本地Pi find.ts参数/命令构造/路径转换/停止链，fd官方源码build_pattern_regex使用globset::GlobBuilder及smart-case实现，[fd文档](https://github.com/sharkdp/fd)、[globset语法](https://docs.rs/globset/latest/globset/#syntax)。本机PATH仍无fd，本节匹配结果属于源码/文档依据下的设计预期，不是本机fd运行证据；未安装/下载、未改实现或测试。

## 17. 已确认：Ls功能及反馈完善

用户2026-10-15确认按下列方案记录；仅设计认可，尚未实施。源码对照为当前agent_runtime/tools/ls.py和Pi a69bef789的core/tools/ls.ts。

- 主体保持：只枚举目标目录的直接条目、不递归、不读正文、不应用.gitignore等搜索忽略规则；显示获准的隐藏名称、目录加/、按名称大小写不敏感排序，普通/控制字符名称沿现有转义。保留path/limit，默认500、最多2000及50KiB正文，不增加大小/时间/type/分页/递归参数，不换fd或rg，不新增依赖。
- 两边都是先获取目标目录全部直接名称再排序，limit控制返回项而非文件系统只枚举limit项；保留此排序语义，不机械套用Grep/Find的无全局排序早停。Python当前casefold+原名稳定排序与Pi的toLowerCase().localeCompare并非逐字相同算法，不为完全相同Unicode/locale顺序增加依赖。
- 接已确认共享路径准备和ToolException/ToolError。把路径不存在、非目录、操作系统无权枚举及其他IO原因分别反馈，不能由classify_ls_target合并成权限Deny。工具已知失败准备具体提示，意外异常交统一处理；删除旧工具错误白名单/兜底回调时保留或迁移实际需要的参数校验反馈。
- 使用Ls自身的直接名称枚举权限：默认/显式许可名称可显示，Read/Grep正文权限不直接套用；适用于Ls的Deny排除条目，仍需Ask而未获授权的名称目标跳过并明确相对路径/前缀和原因。一次批准只授权当前Ls调用的一层列举，不授予递归搜索/正文读取/后续工具许可；列出符号链接名称不等于授权进入实际目标。
- 数量上限统计权限过滤后实际可返回项。截断原因分为返回条数、字节预算；条数限制可建议增大limit（不超过2000），字节限制建议选择具体子目录/使用Find定位。未展示范围/跳过清单仍受预算限制，列不全时明确说明；不增加新结果类或专用展示功能来承载这些提示。
- 个别条目消失/无法stat继续跳过，保留日志并补结果中的具体跳过/不完整说明。只有确实枚举到零条目才称空目录；存在条目但全部被权限过滤时说明无可展示条目；全部因状态读取失败无法确认时返回具体ToolException。无需为此增加重试、第二次扫描或恢复机制。
- 保留Python文件系统IO及既有线程等待收尾，补运行停止检查点，不另启子进程、不为Ls引入异步文件系统依赖。正在进行的普通系统调用需要返回后才能结束收尾，不承诺立即中断；Pi的AbortSignal处理仅拒绝Promise，没有取消底层readdir/stat，也不能说其内核IO已被即时停止。当前无新增Ls硬超时需求，不机械复用Grep/Find子进程超时。
- Pi额外提供LsOperations和专用renderer；本轮无远程后端需求，不新增operations注入层，前端专用卡片后排。共享ToolSpec/权限/错误接线沿既定范围处理，不为Ls另建框架。
- 主要改造现有_list_directory及路径/权限准备消费者；classify_ls_target当前还被Find/Grep引用，待新入口接好后一起迁移清理，不能先删造成断链。现有名称/排序/输出/配对测试保留，补已明确的新行为验证。

本轮证据：既有tests/test_ls_tool.py为14 passed in1.29s。纯临时目录+Mock条目状态失败探针确认：唯一条目is_dir抛PermissionError时，当前返回status=success/content=(empty directory)，并记录ls_entry_stat_failed；未修改实现或测试。Pi对照仅为源码同类分支，没有进行Pi故障注入。文档变更另做diff检查，不代表新功能完成。

## 18. 已确认：Write/Edit完善方向与待细化边界

用户2026-10-15确认按本节及18.4的最终建议记录：父目录、换行、保留分块的有限归一化匹配，以及修改行号/局部diff/完整patch/复用计算的预览纳入设计；符号链接仍待定。其他原有能力与共享迁移按本节方向保留，细节见下；未授权实现。对照本项目write.py/edit.py、Spec038—040，以及Pi a69bef789的write.ts/edit.ts/edit-diff.ts/file-mutation-queue.ts和agent-loop.ts。AgentX 3fcfa0e再次核对BuiltInToolRegistry.createToolsForAgent→AbstractBuiltInToolProvider.createToolExecutor/executeTools→doExecute/异常字符串；这条已核对的内置链没有给出同等本地文件写入/编辑契约，不用它推断文件提交保证。

### 18.1 已有主体及应保留的能力

- Write与Pi都是path/content，创建或整文件覆盖UTF-8文件，允许空内容。当前默认NAIYUE_WRITE_MAX_BYTES=1MiB，按实际UTF-8字节计算；Pi工具正文未设置同等大小门槛，其成功文案content.length也不是严格的UTF-8字节数。候选保留现有可配置输入预算和准确字节数，不为对齐Pi去掉预算。
- Edit与当前Pi都已经是path/edits[{oldText,newText}]，所有编辑匹配同一份原文，要求旧片段非空、唯一且区间不重叠，支持删除。不能把“新增多处编辑”当成本轮差距。
- 我们的Edit已按Spec040分块扫描/增量UTF-8验证、记录原文摘要与状态，再分块构造临时结果；不设固定原文件/结果大小上限。1MiB限制替换输入累计字节数，不是原文件大小；64KiB是内部块大小，不是写入上限。Pi使用readFile/完整字符串/writeFile。候选保留现有有界内存路径，不为移植匹配逻辑退回整文件加载。
- 当前Write/Edit同目录临时写、保留普通权限位、flush/fsync、os.replace；Edit另有两遍内容/状态核对。Pi默认操作直接fs.writeFile，没有同等的临时文件提交和外部变化检查。候选保留已有保护，不扩展为跨进程事务、断电持久或自动回滚保证。

### 18.2 功能候选及取舍

- Write自动创建缺失父目录：用户已确认补齐。先形成并批准准确写入目标及必要建目录范围，之后才mkdir/临时写/提交；Deny仍约束涉及范围，不能用允许目标写入跳过其他适用限制。父目录创建本身是副作用，后续失败不承诺自动删除新建目录。无需单独增加mkdir工具或参数。
- Edit换行兼容：用户已确认补齐LF/CRLF匹配及新文本按文件换行风格写入。BOM当前已经保留；当前不做换行归一匹配，输入LF多行文本匹配不到CRLF原文，新文本带LF可造成混合换行。在匹配视图处理换行，保留到原始字节区间的对应关系，未触及字节继续原样复制；不照抄Pi的全文件换行恢复而连带改动混合换行文件的其他区域。混合文件新增行采用什么风格在落Spec前明确。
- Pi有限模糊匹配已确认纳入：精确失败后使用NFKC、逐行尾部空白、Unicode引号/横线/空格归一；不是拼写相似度或语义猜测，也不等于忽略所有空白/缩进。归一后的重复仍拒绝，不能任取首个；它会按受影响整行重建，可能改变同一受影响行中替换片段之外的字符，其他行保留。当前实现仍仅精确字节匹配；原“只补换行”的候选已被18.4最终建议替代。保留分块并明确资源边界，具体预算及精确路径降级语义在Spec细化。
- Edit多项错误补edits[index]及具体原因，沿现有ToolException；不回显完整旧/新正文。建议按最终内容判断“无变化”，不能只检查每项oldText==newText；当前原文ab，a→空、b→ab会报告成功但结果仍为ab，Pi最终baseContent==newContent会报无变化。
- 同目标并发：Pi共享realpath键mutation queue，Write/Edit同文件依次等待；我们共享目标占用，跨会话同时调用后者返回busy，同批已由sequential维持原序。候选继续busy，不引入跨会话队列；队列会改变后写覆盖行为，当前没有必须等待的场景证据。不同目标仍保留既有调度能力。
- 路径和符号链接分别处理：共享规范化与授权后外部路径能力应按既定权限改造接入，隐藏/隐私特征移出工具硬拒。Pi的直接文件IO可跟随链接，我们当前拒绝任意已有链接组件；不能以一个Allow配置宣称这项能力已获得。候选暂保留“不支持通过符号链接写入”的具体工具能力错误；若要跟随，应明确审查/写入实际目标且保留链接本身，与os.replace替换链接节点的语义区分。该扩展尚未定案。
- Pi有参数修复：字符串化edits、单对象edits及旧oldText/newText会转换成规范数组。候选不增加旧格式兼容，沿当前严格schema及参数错误反馈。未有实际模型失败证据时不同时开放多套输入表达。
- Pi Edit成功正文是短确认，diff/patch/firstChangedLine放details并由renderer使用，还有调用前预览；Write有源码高亮。已确认Edit修改行号、局部diff、完整时可用的patch及复用计算的预览纳入，按18.4接线；前端具体样式沿既有安排后排，不提前建设专用结果存储平台。Write高亮未新增实施安排。
- 停止：Pi在每次await后检查AbortSignal，并持有目标队列直到在途IO收尾，不会取消已完成的写入。我们已有外围await_settled；候选在Edit扫描/复制块边界及Write提交前补合作式停止检查，仍等待在途IO，不强停线程，不承诺撤销已提交结果。

### 18.3 共享接线、调用链与影响

权限沿既定ToolSpec/准备节点/HITL：Write/Edit需要新增自己的权限描述与授权执行能力，目前工厂仅声明sequential，没有外部写审批。外部、隐藏或隐私目标未命中Allow时默认Ask；显式Deny拒绝；批准的是这次规范目标和准确写入/编辑参数。执行消费已保存许可并核对目标，不能再重跑Auto或把路径重新归类为workspace-only。

正常例子：模型write(path=reports/new/case.md,content=...)→共享参数/路径准备→Deny/Allow/默认策略/本批模式→需要则整批暂停→批准恢复→既有串行调度→工具创建获准父目录→同目标占用/临时文件写入→replace→同调用ID成功ToolMessage→模型继续。Edit对应为目标占用→分块匹配/全部校验→分块构造→变化核对→replace，不按edits顺序逐项提交。

失败例子：Edit的edits[1]无匹配/不唯一/重叠→具体ToolException→统一ToolError生成同ID的error消息→模型可修改调用，正式目标不被本次替换。若失败在replace边界、结果不能确认，沿已确认Spec084转为“File write outcome is unknown; inspect the file before another write.”，不自动重试、不因这个错误锁整个会话；不能把它描述成目标一定没改。运行/保存机制故障和控制信号继续由原层处理。

预计改动现有_write/_target_in_workspace/_replace_temp、_scan_target/_copy_to_temp/_edit及两个工厂，接共享路径/权限/ToolSpec/ToolError；按Spec084同步清理旧白名单、WriteOutcomeUnknown与应用/CLI专用消费者。调度仍sequential，不改主Agent循环、不新增编辑工具、异常类或operations接口。若后续为匹配职责或500行上限拆模块，再根据实际消费者分组，不预先平铺新文件。前端审批内容展示及工具专用diff继续单独排期，后端必须保留准确参数与授权配对。

本轮证据：既有tests/test_write_tool.py、test_edit_tool.py、test_application_write.py、test_application_write_cli.py、test_application_edit_cli.py合计66 passed in1.48s，验证的是当前实现（含现有结果未知会话隔离），不是候选设计。临时目录探针使用与应用一致的resolve后根路径，确认缺失父目录报错、LF多行在CRLF文件上无匹配、新插入LF造成混合换行、两项最终抵消仍报告成功。Pi结论来自源码，没有运行Pi新行为或真实供应商请求；未改产品代码、测试、配置或依赖。

### 18.4 模糊匹配、修改详情与符号链接的进一步讨论

用户确认自动父目录和换行兼容；经本轮解释后，又确认按以下有限归一化匹配和修改详情建议记录。这里是设计认可，尚未实施；缓冲预算、混合换行新增行风格及预览接入的具体时序在最终Spec明确。符号链接目前仍仅解释，未确认启用。

- Pi细节：fuzzyFindText先精确后归一化；applyEditsToNormalizedContent若任一项采用模糊匹配，整批在归一化视图上重新定位；countOccurrences即使精确命中也按归一化文本计数。因此“每项精确命中就立即写入、只给失败项做字符替换”不是完整Pi语义。Pi按受影响行重建，可能归一化同一受影响行的其他字符，不可误称只修改oldText覆盖的原始字节。
- 保留分块可行，难点是归一化后长度/字节位置变化、跨块组合字符、行尾空白需看到换行或后继非空白才可决定、不同编辑统一匹配坐标和唯一性/重叠判定、按原始行范围写回。只需保留活动匹配窗口和候选/行范围，不需要给全文件每个字符保存一张内存映射表；_scan_target的匹配元信息需要扩充，_copy_to_temp保留未变区间复制和原文变化检查。
- 成本估计基于结构而非已测性能：最直观的实现为精确扫描、归一化校验/回退扫描、构造提交，当前两遍原文读取变为最多三遍；按此结构若完整扫描三遍，原文读量约从2N到3N，不能据此承诺总耗时只增加50%。可将两种匹配状态放同一遍扫描以省IO，但会增加实现状态，不必先做。归一化和局部修改区间处理是主要新增算法，Agent循环、审批和模型调用次数不变。
- Unicode边界需真实处理：逐64KiB块各自NFKC再拼接不等价于整体NFKC。本机标准库探针验证e+组合重音、韩文Jamo及组合符重排均能产生差异；[UAX15的连接性质](https://www.unicode.org/reports/tr15/#Concatenation)和[流式缓冲](https://www.unicode.org/reports/tr15/#Stream_Safe_Text_Format)确认该边界，极端组合序列可要求很大缓冲。逐整行读入也可能因单行等于整个大文件而失去原有界内存承诺。建议保留有限缓冲、正常代码/文档对齐Pi规则，无法在预算内正确完成的模糊匹配明确反馈，不插字符/截断/猜匹配；具体缓冲边界和精确路径是否可继续需落Spec前确认。不为未见的极端输入自建通用Unicode引擎，也不声称任意Unicode与Pi完全等价。
- 首个修改行成本低：扫描匹配同时记录行号，结合前面编辑新增/删除行数得到新文件位置；删除发生在文件末尾时要约定位置含义。局部diff和unified patch成本中等：已知编辑范围向前后扩展上下文、合并相邻范围、用标准库difflib比较局部旧/新内容、补全文件行号；不把两份大文件read().splitlines()后做全局diff。局部结果与Pi全局diff的分组/最短表示可能不同，但应准确表达最终变化。普通超长行/大修改导致展示超预算时截断展示且说明，patch必须完整才标可用，不能截断后仍称可应用patch；详情不可用不能覆盖已确认的写入成功。
- 预览复用同一匹配/修改计算入口，只计算不提交；执行时仍核对文件是否变化，不能永久相信先前预览。这是文件状态复核，不是再次跑权限管线或Auto。Pi的computeEditsDiff本身会读文件，不能照抄到本项目的未授权展示路径：未获准读取目标时仅展示参数中的oldText/newText并注明未核对原文件；真实上下文预览须有读取许可。具体权限与预览接入时序仍需落Spec。
- 当前框架有response_format=content_and_artifact，短说明放ToolMessage.content，diff/patch/first_changed_line等可放artifact；现有message_observation._snapshot和web.serialization.message_view均不投影artifact，前端MessageRow/ToolCall也无详情字段。因此支持界面必须同步消息快照/恢复投影、Web序列化与前端消费者，不能只声明工具返回字段。复用现有checkpoint消息，无需先建专门结果表/存储平台。本机合成工具探针确认同调用ID的content/artifact产生及JsonPlusSerializer往返，不代表完整保存/前端已接通。
- 符号链接是路径指向另一路径的文件系统对象。直接写链接路径通常改变真实目标；os.replace(临时文件,链接路径)会替换链接节点，真实目标不随之改变。纯临时目录探针已确认这两个不同结果。若支持，应按真实目标审查权限、在真实目标同目录临时写并替换真实目标，保留链接且执行前检查解析目标一致；当前不支持属于实现能力，不能宣称是框架做不到或永远Deny。用户目前只要求解释，尚未确认启用。

复杂度判断：首个修改行低；局部diff/patch中等；复用计算入口的后端预览中等，界面适配额外跨层；分块下Pi归一化匹配中等偏高，主要集中在匹配器及测试，而非重构Agent。用户已认可纳入有限模糊匹配和修改详情，资源边界与预览权限时序继续细化；没有给出未经探针支撑的精确工期/代码行数。仍不实现，不新增依赖。

## 19. Bash设计起点：现状、Pi与候选主链（待讨论）

2026-10-15用户要求开始Bash设计，尚未确认下列候选。先讨论执行/结果/控制主线，再细化命令规则、环境/预算、输出展示；不实现，不把Spec042—045的历史限制当作本轮不可更改的目标。

- 当前链：严格command/timeout参数→每条有效命令HITL→grant按调用ID/命令/root消费→同步_run_host_command→Popen(shell,-c,command)，工作区cwd、最小环境、stdin关闭、独立进程组→双线程采集stdout/stderr→每流末尾4096字节及按需工作区归档→退出0成功，非零ToolException；超时/用户停止/采集错误或不关闭统一BashOutcomeUnknown→应用manual_review_required，整会话阻断。工具sequential；CLI/Web主入口已是异步，但工具本体仍同步。
- Pi a69bef789：createBashToolDefinition→createLocalBashOperations.exec→spawn→stdout/stderr回调共用OutputAccumulator→waitForChildProcess→执行结果/抛错→agent-loop.executePreparedToolCall统一错误结果。timeout可省略且没有默认超时（上限约2147483.647秒，允许非整数），与我们默认60/最大300秒不同；2000行/50KiB合并尾部、按需系统临时文件、UTF-8增量解码、onUpdate限频约100ms。默认继承进程环境并可附PI会话变量，有operations/shellPath/spawnHook/commandPrefix扩展点。
- Pi超时在终止处理后抛包含部分输出/原因的错误，由公共循环转isError工具结果，不设置本项目这种副作用核对锁。用户abort也会形成工具中止反馈，但同一AbortSignal仍传后续运行，不能据“工具错误变消息”推断用户停止后仍正常继续。Pi的killProcessTree在POSIX尝试killpg后回退kill pid，不验证任意逃离组的后代；waitForChildProcess在父进程退出后等待EOF或输出空闲宽限，不承诺所有后台进程退出。exitCode=null且非timeout/abort的默认工具分支会正常返回，不能照搬为本项目的信号终止成功语义。
- AgentX 3fcfa0e：本轮核对WebTerminalService.createTerminalSession→TerminalSession.start→Docker execCreateCmd/execStartCmd→Frame回传WebSocket。它是交互式容器终端，不是已核实的模型Bash工具/ToolMessage链，不据此引入Docker或声称同等审批/超时合同。

### 19.1 候选结果与控制语义

| 情况 | 建议 |
|---|---|
| 参数非法、工作区/授权无效、shell未启动 | 原参数错误路径或具体ToolException，不启动命令 |
| 正常退出0 | 返回退出码、有界输出及完整性/归档信息 |
| 非零退出/已确认被信号终止 | 具体ToolException，统一ToolError生成同ID错误消息，模型可继续，不自动重试 |
| 命令超时且受管理进程已收尾 | 部分输出＋超时说明＋可能已产生副作用/先核对再决定，转错误消息；不单凭超时锁会话 |
| 输出采集失败/不完整，但进程已收尾 | 明确输出不完整，保留已知退出状态和部分输出/归档信息，具体错误消息；不伪称无输出或未执行 |
| 输出归档配额/目录/写入失败，仍能持续采集尾部 | 沿既有降级，归档不完整单独说明，不覆盖已知进程退出语义 |
| 用户主动停止 | 终止受管理进程并收尾、保存同ID中止结果和部分输出；后续未开始工具走既有stop结果，本轮不再正常调用主模型，下一次用户输入可继续会话 |
| 外部CancelledError/GraphBubbleUp及实际无法回收受管理进程 | 控制/执行机制故障保留上抛；不能由通用ToolError掩盖仍在运行的受管命令。BashOutcomeUnknown若保留应收窄为这种真正收尾未确认，不再包办超时/输出/用户停止 |

这里区分进程退出、输出采集完整、归档完整三件事；不新增模型自动重试或副作用核对平台。Bash纳入既定ToolError时移除旧handle_tool_error，更新Spec084当前排除Bash的声明及应用/CLI/观察/错误分类消费者，但保留真正取消、未配对、必要保存故障等既有边界。

### 19.2 其他候选与待定细节

- 权限：接共享Deny/Allow/默认策略/四模式；Bash默认Ask，显式Allow和模式可处理Ask，不新增“所有命令必须人工”的第二道旧grant门。保存并执行同一命令/cwd/有效timeout，整批审批及sequential保持。命令级规则能拒绝匹配到的命令，不能从cwd或字符串匹配推断任意Python脚本/子命令只访问获准文件；文件Deny若要求约束一切Bash运行访问，需要单独的执行隔离，Pi本体同样未给出该保证。本轮不自动加沙箱。
- 执行：建议以标准库asyncio.create_subprocess_exec作为一个异步核心；后续已选stdout/stderr合并单管道分块排空，统一终止/等待/归档收尾，不是简单communicate()缓存全文。进程创建成功后的采集准备失败也须进入收尾，读取任务异常必须被主调用观察，避免旧后台线程异常无法被ToolError捕获。已有同步SDK实际消费者仍在，迁移以同一核心的薄同步桥接处理，不留两套进程算法；不扩大为整个应用同步入口迁移。[Python3.12子进程文档](https://docs.python.org/3.12/library/asyncio-subprocess.html)。
- 超时：建议保留有界执行，默认先沿60秒/最大300秒，改由可信启动配置提供；不默认照搬Pi无限等待。命令timeout可覆盖默认但受配置上限约束；具体配置形状和长测试预算待讨论，不新增后台任务管理。
- 配置与运行错误分开：可信Bash配置的格式/额度/注册合同应在创建Agent前验证，失败明确终止装配；运行时shell消失/无权启动等才是具体工具执行反馈，不把无效配置交给模型反复重试。
- 输出：后续选择合并stdout/stderr、2000行/64KiB尾部及行/字节截断与回读范围提示，保留原始字节归档与必要UTF-8文本视图。16MiB单命令/128MiB工作区归档默认及不完整原因、手动清理沿用；不照搬Pi无同等配额的系统临时文件。额度按工具配置，最终正文、归档、流式展示预算分别说明。
- 环境：用户已确认默认继承父进程环境、支持显式覆盖/移除，撤回业务变量白名单候选。当前代码的固定PATH、HOME/TMPDIR=工作区和硬编码locale在实施时迁移为正常继承或显式设置，不视作沙箱。模型不新增可任意注入env/cwd的参数；少量启动控制/Agent私有变量的确切处理随Shell清单收口。
- 展示和扩展：运行中输出可以复用原生工具流/现有事件通道，须明确预算、节流和消息配对，不反复追加最终ToolMessage。前端专用显示按既有安排细化。无当前远程后端需求时不先建operations平台/spawnHook/命令前缀接口；本期不建设交互终端、持久shell状态或后台task_id，Windows双Shell已确认，具体适配随Shell设计收口。

2026-10-15后续校正：用户明确Windows首版同时支持Bash和原生PowerShell，Windows不再列为笼统后排。后续输出/运行设置/子进程/跨平台讨论集中在[Shell工具设计](bash-design-2026-10-15.md)，包括独立bash/powershell工具候选、合并输出与日志、环境及平台分支；此处早期“保留双流”仍为候选，以后续讨论为准。Claude官方已确认有Bash/PowerShell两个独立工具，不能再暗示双Shell只是本项目额外能力。

正常例子：模型bash(command='python -m pytest -q',timeout=120)→本批参数/权限准备→需要则整批审批→授权后串行调度→执行/采集→退出1→ToolException包含失败摘要→ToolError回填同ID error→模型决定读日志/修改。超时例子在回收后产生明确超时反馈；用户停止例子则回填中止/后项未执行，走现有停止边界结束，不把“反馈结果”理解为“强制继续模型”。

当前证据：进入Bash讨论时既有tests/test_host_bash.py、test_bash_output.py、test_active_stop_bash.py合计37 passed in10.56s，覆盖当前实现（含超时/停止会话阻断），不是候选语义完成，也不是每轮文档讨论都重跑。源码核对无真实模型/外部服务请求，无产品/测试/配置/依赖变更。

后续用户确认Shell与Read对齐2000行/64KiB、各工具额度分组配置并逐键注释、每次新建Shell，以及默认继承父环境并支持显式覆盖/移除；其他部分按建议方向继续。详见[Shell设计](bash-design-2026-10-15.md)、[配置样例](../examples/tool-runtime.example.toml)与[第10节收口清单](bash-design-2026-10-15.md#10-bash一节收口清单已定方向待讨论行为与验证)。命令授权已确认仅完整command精确匹配，其余交模式；子进程/管道收尾、PowerShell错误退出码、编码/文本与隐式启动候选见Shell设计第11节，尚待认可，环境继承主体不再开题。图片/Edit额度归各自章节，所有细节收束后写总Spec统一实施。

## 20. 完整子Agent目标与嵌套审批恢复探针（2026-10-15）

用户希望后续具备完整子Agent能力，不能将内部Ask一律拒绝作为最终能力限制；具体作用域接口、前后台及恢复边界仍在讨论，不构成本轮实现授权。Claude当前官方文档支持前台审批透传、后台在主会话提示并标来源；这是产品行为证据，不证明其内部使用LangGraph。[Claude子Agent](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background)。

本机一次性stdin探针：LangChain1.4.2、LangGraph1.2.12、prebuilt1.1.0；真实create_agent父子调用、官方HITL、父InMemorySaver、FakeMessagesListChatModel及仅内存计数的工具，无模型/网络/文件副作用。首次均观察到中断且动作未执行，再以同thread的Command(resume=approve)、新的run上下文恢复。实际结果：

| 路径 | 恢复结果 | 父工具入口次数 | 实际动作次数 |
|---|---|---|---|
| 官方嵌套，同步，不加项目批次 | completed | 2 | 1 |
| 加当前项目同步sequential批次 | ToolBatchSchedulingError | 1 | 0 |
| 官方嵌套，异步，不加项目批次 | completed | 2 | 1 |
| 加当前项目异步parallel批次 | ToolBatchSchedulingError | 1 | 0 |

探针进程退出0；错误行tool_batch_plan_missing对应预期复现，并非产品已修复。ToolBatch.after_model只将协调对象放runtime.context；恢复工具任务时未重建，且现有except BaseException把中断也记failed。官方恢复会重入父调用节点和子中断节点；因此父工具入口2次不能解释为实际动作重复，也不能据一次内存计数证明任意IO exactly-once。[官方重入合同](https://docs.langchain.com/oss/python/langgraph/interrupts#using-with-subgraphs-called-as-functions)。

设计须继续明确：共享定义但父子运行设置/状态独立；可保存的批次事实与运行期锁/协调器分开；恢复发放/延续许可而非重复消费；GraphInterrupt保留控制效果；审批按来源及interrupt ID准确路由。已完成动作不能因父工具重入盲目重放，Auto决定不能仅因恢复重算。PreparedToolCall/ExecutionPermit尚未实现，本探针不证明其已安全。

原整批审批保证针对每个Agent执行前已知的当前批次；子Agent运行后才发现的新Ask，不能倒推出父批次其他并行工具此前从未执行。用户后续确认：并行父批中A内部新Ask暂停A当前批，B继续已获准工作；父等结果齐备，B已完成结果须保留、恢复A不重执行B。原串并行/前置整批审批保持；准确审批展示/提交与一般恢复时序留小Spec验证。长期任务/人工等待也不能直接触发现有60秒排队失败；不以强制标parallel替代恢复与调度设计。

### 20.1 子任务预算跨审批恢复复核（2026-10-16）

本机1.4.2的ModelCallLimitState把run_model_call_count标为UntrackedValue，不保存checkpoint；thread_model_call_count才累计保存。真实create_agent＋官方HITL＋FakeMessagesListChatModel＋内存工具/MemorySaver探针：两次批准恢复后，run_limit=2仍实际完成3次主模型响应；thread_limit=2则在第三次调用前返回官方limit说明，累计计数保持2。两组断言通过，退出0，无模型请求/文件修改。首次探针误从过滤后的公开结果读取私有计数触发KeyError；改从saver的channel_values读取后确认上述结果。

因此不能直接用单次框架invoke的run_limit作为包含审批恢复的完整子任务预算。早期“累计计数加任务起始基线”候选用于同实例跨任务续跑；用户现确认本次不做续跑，首版不建设该基线，可优先验证独立实例累计限制。审批恢复仍不清零，精确接线/默认额度/结束反馈待生命周期设计确认，不因本探针创建新middleware或升级框架。[官方模型调用限制](https://docs.langchain.com/oss/python/langchain/middleware/built-in#model-call-limit)。

继续已完成实例也不同于恢复一次interrupt：官方per-invocation仅隔离同次调用，per-thread可积累跨调用历史，但同一持久子图并发会有命名空间冲突。角色定义共享不代表实例共享历史；agent_id到checkpoint定位、同实例单活动执行及不同实例并发须在一般调度/恢复中验证。[官方持久作用域](https://docs.langchain.com/oss/python/langgraph/use-subgraphs#persistence)。

用户2026-10-16确认：架构可支撑以后再做即可，本次结束后续跑后排以节省工作量；每次调用新实例，稳定身份/独立状态/必要checkpoint和同任务审批恢复保留。上段的完成后实例定位、再入/保留期属于未来工作，不再算本次必做项；一般父子审批/批次恢复仍须验证，不因延期续跑一并取消。

继续核验的候选接法：独立per-invocation子图复用官方ModelCallLimitMiddleware(thread_limit=N, exit_behavior='error')；子调用边界只将ModelCallLimitExceededError转具体ToolException，再由公共错误机制反馈父模型，不按计数字段或AI提示文本猜超限。第N次已正常完成应保留成功；限制的是后续主模型响应，不是同次响应内工具数量，也不是包含Auto/压缩或SDK重试的全部请求费用上限。额度/停止/部分结果合同仍待用户确认。

新增一次性异步stdin探针（1.4.2，父InMemorySaver、共享compiled子图、假模型和内存动作）：预算2时，第2次直接最终回答返回success；单子任务经两次HITL批准后仅2次响应、2次动作、typed超限反馈父ToolMessage.error；同一父批次两个子任务分别经审批，各2次响应/2次动作/独立超限。3组断言通过、退出0，检查父call_id配对和父最终继续。探针通过原生BaseTool.handle_tool_error验证反馈，不声称总Spec官方ToolError及项目调度已经接线；未运行真实模型/进程或修改产品代码。中间件顺序[HITL, ModelCallLimit]使逆序after_model先保存计数再暂停。

### 20.2 待审批子任务停止的最小接法（2026-10-16；候选）

Claude Code的角色maxTurns未公布统一默认数字；Python Agent SDK查询级max_turns缺省None，官方自建编排示例MAX_SUBAGENT_TURNS为15。两者不是同一产品字段，不能宣称CLI默认15。当前建议轮数保护可配/可关闭，默认不额外限轮数属于工程候选。[SDK默认](https://code.claude.com/docs/en/agent-sdk/python#claudeagentoptions)、[官方编排示例](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-effort-example)。

当前request_stop只接受busy会话，不能直接停止已暂停审批。已有ObservedHumanInTheLoopMiddleware.after_model在调用官方HITL前检查RunControl，命中则委托StopBoundary配对未开始工具并跳结束。应用认领停止、阻止旧审批提交后，按interrupt ID唤醒原父图；重入子审批节点时由停止guard先结束，不向官方decisions新增stop类型，不发批准许可、不重跑Auto。清除pending和报告stopped须晚于必要保存；失败保留停止处理中/失败事实，不能只删UI记录。用户已认可总体方向，讨论先到此；具体方法、字段、竞争和一般恢复接线留小Spec审阅，当前不实施。

一次性异步stdin探针复用现有ObservedHITL/RunControl/StopBoundary，父InMemorySaver、假模型、Read/Bash内存工具，无项目批次协调。子Read+Bash整批暂停后，按ID的应用控制唤醒值仅供停止guard截断、不送官方审批解析：仅停子任务时子模型1次/父模型2次；整轮停止另加最小父before_model guard时父模型仅1次。两组均动作0次、子调用2项停止配对、父subagent错误配对、父最终next/tasks为空，断言通过/退出0。未接应用停止API、保存/事件/UI、并发竞态或一般批次恢复；不能报告产品停止能力已完成。

### 20.3 前后台与首版层数（2026-10-16）

Pi a69bef789自带subagent示例的execute等待runSingleAgent；parallel分支await mapWithConcurrencyLimit再返回汇总，核心又await tool.execute后才进入下一次父模型，因此是多个子进程可并行、父模型仍等待。进程/流式更新/async语法都不等同后台委派。LangChain官方默认工具包装是等待结果；后台示例另有启动job_id/状态/结果及完成通知职责。[官方同步与后台](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents#sync-vs-async)。

Claude当前官方文档同时支持前后台：交互默认fork mode使普通委派走后台，关闭该模式时也可后台或按结果依赖前台；禁用后台等配置有例外，完成通过后续turn通知、待审批请求显示子来源。[官方行为](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background)。这是当前文档证据，不是其内部源码验证。用户后续确认本次仅一层且仅前台，沿同进程异步调用，不做后台任务API/完成通知；多实例暂停/继续方向也已认可，具体时序/恢复留小Spec验证，当前不实施。后台本可由同进程任务实现，并非必须新进程，此处延期是范围选择。
