# 权限体系调研：沙箱、工具权限规则与审批策略

> 2026-10-15整合：当前合同与函数接线见[Spec085](../specs/085-tool-system-rebuild.md)。本稿下方包含早期explicitAsk/隐私硬Deny等已撤回候选，仅作调研历史；当前为Deny/Allow、保守默认Allow/Ask、四模式、整批暂停及保存决定后恢复不重审。没有新增自定义Hooks加载器或沙箱实现。

> 审批粒度正在随完整子Agent目标重新评估：官方逐调用入口已发布，不能把当前批量方案说成框架唯一能力；见文末[本轮复核](#审批粒度复核2026-10-15)。尚未改变原方案或产品依赖。

日期：2026-10-12。状态：调研稿，未批准实现，不改产品代码。

证据类型标注：**[源码]** 本机参考项目源码；**[文档]** 官方文档（2026-10 读取）；**[探针]** 本机运行结果；**[推断]** 设计判断。

参照版本：Pi `a69bef789`（本机）；Claude Code 官方文档（permissions / permission-modes，含 v2.1.2xx 说明）；Codex 官方 sandboxing 文档；Gemini CLI policy-engine 文档；OpenHands SDK security 文档；MCP 规范 2025-11-25。AgentX 初始调研只定位到 `container` 模块；2026-10-15补充核对本机 `3fcfa0e` 的工具装配→内置executor→doExecute/错误文本链，仍未核实与本项目等价的批量审批合同。

2026-10-15讨论校正（不代表批准实现）：

- 第7节是候选目标链，不能当作本项目当前执行顺序。当前HITL在`after_model`审查原始工具参数，早于ToolNode/BaseTool的正式参数校验。Bash的`when`已用`BashInput`预检查；Read的`when`只检查部分字段。本机假模型探针确认：同为`offset=0`，工作区内Read直接返回参数错误；外部普通路径Read先审批，批准后才返回同一参数错误，两者均未执行工具正文。
- 参数错误消息当前由`BaseTool.handle_validation_error`生成，不经过工具正文或`ToolException`。若保留批量审批，建议审批前复用工具真实输入模型做完整预检查，无效调用不入审批；正式错误消息仍复用现有框架路径。同批其他调用触发中断时，该错误结果可能等批次恢复后才产生。此建议尚未实现。
- 不能直接把`tool_call_schema`当作与执行完全等价的验证器：本机Read探针中它丢失了原模型的`strict=True`和`extra='forbid'`，接受字符串offset和未知字段。提前检查应复用真实输入约束，不另写弱化的参数规则。
- 显式规则最初讨论为`deny > ask > allow`；用户最新确认取消显式Ask，第一版只保留`deny > allow`，未命中再使用工具默认策略及模式。工具默认权限策略只返回allow或ask：文件操作仅在工作区内、且不涉及隐藏/已识别隐私等需确认特征时默认allow，其余有效且受支持的操作默认ask。明确禁止的目标由配置deny表达，隐藏/隐私特征本身不构成工具硬拒；显式或会话allow可以覆盖默认ask。工具能力、实际目标与授权一致性校验保留。四种模式名为`Default`、`NoAsk`、`Auto`、`BypassApprovals`；用户确认BypassApprovals只免审批，显式deny仍生效。“不再询问”仅增加本会话allow，不永久写配置。Auto快审/详审只能给出allow或ask，不替用户作最终deny。模式切换从下一批次生效，前端可立即展示新选择。Hooks和具体规则配置仍在讨论，旧候选不得当作已确认范围。
- 参考边界：Pi本轮核对的是官方`permission-gate.ts`、`protected-paths.ts`示例，前者用少数命令正则，后者只拦截write/edit路径子串，不是不可绕过的完整保护。Claude关键路径删除不能由allow规则或PreToolUse allow自动批准，但部分模式仍转人工询问，不能概括成所有模式一律拒绝。Codex区分沙箱与审批，Auto-review替换审批决策者，不自动放宽执行边界。来源：[Claude模式](https://code.claude.com/docs/en/permission-modes)、[Claude SDK顺序](https://code.claude.com/docs/en/agent-sdk/permissions)、[Codex沙箱](https://learn.chatgpt.com/docs/sandboxing)、[Codex Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)。
- 参数相关既有测试：`.venv/bin/python -m pytest -q tests/test_host_bash.py::test_invalid_bash_args_return_tool_error_without_approval tests/test_read_tool.py::test_read_rejects_invalid_pagination_parameters` → `13 passed in 0.63s`。仅验证当前行为；无产品/测试实现或环境配置更改，无真实模型请求。

### 审查重复与默认策略的进一步核对（仍为设计讨论）

- 批量暂停例子：模型同轮提出A（Read分页参数无效）和B（外部普通文件，需要人工审批）。若参数预检查只排除A的审批、不提前生成ToolMessage，HITL会在工具节点之前因B暂停整批；界面只询问B，A的错误结果等B被批准或拒绝并恢复后才生成，模型也要等本轮结果继续。这是所选批量时序，不是必须新增的安全要求。用户确认提前展示价值有限，本轮不提前构造/展示参数错误ToolMessage，保留既有框架反馈链。
- [Anthropic官方工程文章，2026-03-25](https://www.anthropic.com/engineering/claude-code-auto-mode)公开了两阶段分类器：第一阶段快速给二值判断，仅第一阶段标记可疑时进入详细推理；不是每条调用固定请求两次。它在静态规则及项目内普通操作之后才参与。拟议Auto可借鉴该结构，具体模型、预算和回退合同尚未确定，不能视为已经验证的效果。
- 当前Read外部普通文件的本机计数探针：首次运行调用`_read_policy_requires_approval`和`_review_read_policy`各一次；审批恢复又调用`_read_policy_requires_approval`及工具正文中的`_resolve_read_access`各一次，共四次权限解析。最后一次含执行前目标/授权检查；展示层重新分类和恢复时重跑完整决策可以合并。
- 本机LangChain1.4.2/LangGraph1.2.12的纯假模型探针确认：在HITL之前用独立middleware的`after_model`节点准备审查结果并返回图state，HITL的`when`仅查该结果；批准恢复时轨迹为`prepare_review → read_saved_review → read_saved_review → tool_body`，审查准备1次、结果读取2次、工具执行1次。无需改框架或手写主循环。拟议权限管线采用这种单次完整决策、下游消费既有结果的组织方式；不在`when`内调用审查模型，不因普通审批恢复重复模型审查。仅限本机内存checkpoint探针，不宣称崩溃后外部请求exactly-once。
- 拟议结果只绑定当前调用/批次中的工具名、参数、工作区和审查目标；审批展示及一次性grant消费同一事实。执行前仍核对实际目标和必要硬性限制；对象或授权上下文改变时，旧授权不可直接复用。图state接线、序列化及现有`review`消费者清理须在实施Spec明确，目前没有实现。
- 默认策略当前并不统一：Read外部普通文件ask，ls/find外部目录ask；grep外部单文件ask但外部整目录拒绝；write/edit外部文件直接拒绝；Bash有效命令全部ask。用户期望普通文件操作统一为工作区内默认allow、工作区外默认ask，适用allow可免默认询问；write/edit和grep目录范围需要实际补能力，不能只改权限规则。危险目标保护与参数/不存在/类型不支持等工具错误需分开，后者不应误称为不可豁免权限deny；Bash另议。
- 现有目标重验及混合参数错误/审批测试：`.venv/bin/python -m pytest -q tests/test_application_read_approval.py::test_changed_symlink_target_cannot_use_old_approval tests/test_host_bash.py::test_invalid_and_valid_bash_in_one_batch_only_valid_needs_approval` → `2 passed in 0.66s`。只证明现有合同，未实现新模式或分类器。

### 四类边界与审批模型调用候选

四类是职责归属，不增加四套异常类。schema参数错误仍由框架生成反馈；工具已有具体错误及意外执行异常沿Spec084转换。控制与运行机制故障仍归原层。

| 类别 | 当前事实与例子 | 设计方向 |
|---|---|---|
| 默认询问 | Read/grep外部普通单文件、ls/find外部目录；write/edit外部路径及grep外部目录当前尚不支持审批 | 默认策略只返回allow/ask；文件操作满足工作区内且无隐藏/已识别隐私等需确认特征才默认allow，其余默认ask；适用allow可免默认询问，后两项需补执行授权能力 |
| 权限禁止 | Read/grep正文及write/edit当前限制敏感/隐藏路径和.pem/.key；Read自定义策略可deny，策略失效时也拒绝；批准后目标改变不能复用旧授权 | 隐私特征政策迁到配置，默认可Ask，不继续作为工具硬拒；显式规则deny、人工拒绝与无效旧授权需分别表达 |
| 工具能力/支持范围 | Read/grep只支持UTF-8正文；Read超长单行、write/edit输入大小上限；write父目录须存在，edit只改已有文件且精确唯一不重叠；写入目前不支持符号链接 | 保留在工具中，allow/模式不会让工具获得未实现的能力；路径中`..`等当前表达限制与真实越权分开，不能仅因分类就删除防护 |
| 普通执行/输入语义失败 | 文件消失、目标类型不符、无rg、IO失败、非法glob/regex；edit无匹配/多匹配/重叠/无变化；写入忙、快照冲突、提交结果未确认及意外异常 | 由工具识别并准备具体提示，或ToolError提供通用提示；不增加人工审批来处理这些错误，不自动重试写操作 |

边界补充：ls/find列名称不等同读取正文，不能把Read正文策略直接套给名称展示。分页、结果截断、无匹配以及目录搜索按合同跳过个别文件通常是正常结果，保留现有成功语义。缺少会话工作区是执行前置配置问题。“策略不可用”须按下文区分可建议审查失败、必要规则故障和实际目标不明确，不再笼统等同权限deny。

审批模型接入的建议尚未批准实现：

- 仍由`create_agent`创建主Agent；运行层权限middleware在独立`after_model/aafter_model`节点准备决策。Auto只处理需自动审查的默认ask；规则已allow/deny及其他模式不调用审批模型。
- 应用装配从已有`ModelCatalog.get(model_id)`取得快审、详审模型并直接注入；两个ID可以配置为同一底层模型的不同thinking/输出预算。复用现有模型配置中的`thinking`、`max_output_tokens`、`request`，不引入另一份凭证/供应商加载器，不修改共享模型实例，不隐式随主模型切换。
- 快审直接`invoke/ainvoke`，结果只能为allow或ask；快审ask进入详审。详审建议使用LangChain模型级`with_structured_output(AutoReviewResult, include_raw=True)`及`invoke/ainvoke`，结果合同收窄为`decision=allow/ask`与简短`reason`，详审ask才转人工；撤回此前Auto返回deny的候选。非法deny输出不可成为拒绝决定，按无效审查结果转人工。原生格式及结构化工具调用仅供解析，不绑定实际业务工具，不创建第二个工具循环。供应商/网关的真实结构化输出支持仍须集成验证，不能仅据runner构造成功宣称兼容。
- 输入包含固定审查规则、来源可确认的用户指令、必要工具调用记录、待审工具的准确参数、可信工作区和规范化目标。不将主模型自己的授权解释或工具返回正文当成用户授权，不先读取尚未获准的外部文件；已丢失必要授权上下文时转人工。
- 模型返回后先校验再保存为本批次图state；HITL、审批展示和执行授权消费同一结果。审批模型响应不追加主会话`messages`。当前`async_message_observation._StreamState.delta`已只展示`langgraph_node='model'`的文本，独立审查节点可复用该过滤，接线时验证，不先新增一套展示通道。
- 审查网络/供应商故障、无效/截断结果转人工（无交互渠道不能执行）；只有规则deny、人工拒绝或模式拒绝才形成权限拒绝反馈。复用模型层安全故障描述；UserStop和外部CancelledError保留控制效果；审查等待接现有`RunControl.await_model`。图/保存等运行机制故障按原层处理，不变成模型审查拒绝。
- 不额外套应用层重试。现有模型SDK默认可重试2次，两个逻辑阶段不等于最多两次HTTP；审批专用模型的重试/超时须由对应既有request配置明确约束。
- 建议沿既定职责归组，把权限运行编排及模型审查放入`agent_runtime/tool_execution/`内的权限模块；工具描述/默认策略归`tools/permissions.py`。先用一个权限middleware及必要结果schema，不建立独立审批Agent包、全局审查缓存或额外SDK封装。类名、具体字段和旧review消费者清理待完整实施Spec收敛。

本轮离线证据：既有8个测试入口（含参数化）共`12 passed in 0.80s`，覆盖UTF-8、名称展示、rg缺失、外部grep目录、写入预算、edit匹配及冲突、三适配器输出预算。纯假模型探针验证`ModelCatalog.get → bind_model_output_limit → ainvoke`及短文本/JSON返回，无业务工具绑定；另确认ChatOpenAI/ChatAnthropic/ChatOllama可本机构造结构化输出runner，未发模型请求。探针里的16/128仅为合成预算，不是拟定产品默认值。没有新权限功能/模式/模型审查接线。

### 规则格式与现有架构接入（候选，尚未批准实现）

最新确认方向：用户同意取消显式Ask，第一版仅保留Deny和Allow，撤回前一轮显式Ask及其Auto特殊语义的候选。工具默认权限策略只保守识别可直接allow的操作，其余返回ask，不返回deny。文件操作的默认allow须满足工作区内且不涉及隐藏/已识别隐私等需确认特征；这些特征通过默认ask交模式处理，不是硬性禁止，不另建一套`defaults.ask`规则列表。具体工具及自定义工具怎样声明这些条件，后续结合注册设计确定；Bash仍另议。

1. 显式规则：先匹配deny，再匹配allow；会话allow沿用同一优先级，不能覆盖deny。
2. 未命中显式规则：使用工具的默认权限策略，仅返回allow或ask。工作区内普通文件操作可默认allow；外部目标、隐藏/已识别隐私目标及其他不能确定可直接放行的受支持操作默认ask。
3. 默认ask按模式处理：Default询问、NoAsk拒绝、Auto进入模型审查、BypassApprovals免审批放行；BypassApprovals仍受先前显式deny检查及工具能力/目标校验约束。Default保持默认策略，不会把默认allow变成ask；例如工作区内普通文件修改仍默认允许。
4. Auto快审allow直接放行，快审ask进入详审；详审allow放行，详审ask进入HITL。模型无deny输出权。

取消显式Ask的取舍：暂不提供“对工具默认允许的某类操作，仍通过一条显式规则强制每次人工审批”的覆盖能力，Auto也没有这种规则例外。用户已补齐隐藏/隐私目标的默认策略前提：默认allow范围保守，工作区内隐藏或已识别隐私目标也默认ask，所以Default可交人工、Auto可先审查。这里的“其余ask”限于权限默认判断；参数非法、工具不支持、执行IO失败、配置故障和控制信号仍走此前各自链路，不统一改成人工审批。

本节两项边界已由用户确认：完全免审批模式采用BypassApprovals，显式deny仍生效；模式切换仅影响下一批次，当前在途审查/挂起审批继续使用本批固定模式，前端可先显示新选择。后续批量审批及保留现有串行/并行规则也已确认，具体见下节；仍未批准实施。

条目建议统一为`tool`选择器、可选`path` glob和可选`args`精确条件；一条内条件AND，多条规则OR。不把任意JSON整体做正则。示意（不是实际配置文件，工具名有合成示例）：

```json
{
  "permissions": {
    "deny": [{"tool": "remove_resource", "args": {"environment": "production"}}],
    "allow": [{"tool": "read", "path": "${workspace}/docs/**"}]
  }
}
```

匹配候选合同：

- `tool`第一版仅精确规范工具名或`*`；`path`按路径段的`*`/`**`匹配规范化实际目标，展示仍包含用户输入路径。`${workspace}`/`${home}`为限定的可信占位，不执行表达式或任意环境变量替换。规则模板中的路径模式可以由用户增删，不把所有隐藏文件定义为私密。
- `args`匹配公开输入schema校验后的生效参数，含默认值，不允许拿注入的runtime作为模型参数参与匹配。配置中未知字段/参数名或不支持的条件要在装配验证时明确报错，不能静默忽略限制规则。
- 2026-10-15用户确认Shell命令条件首版仅完整command精确匹配，未命中交既定模式；不做前缀/glob/正则或复合命令拆分。`pytest -q`不匹配改参或追加命令，Bash/PowerShell按真实工具名匹配；这沿用args精确条件，不新增命令解析器。会话“不再询问”维持当前会话/工作区及精确命令范围，不能自动扩大到整个解释器或同类操作。
- 普通自定义工具可以直接按规范工具名及schema参数匹配；文件类工具注册时额外声明哪个输入字段是路径，例如`preview_file(filename)`声明路径参数为`filename`，由可信公共路径解析产出规则所用`path`。工具作者不重复编写规则解析器。未声明文件目标的工具不能声称受到文件路径规则保护，未知自定义工具默认ask；Bash间接访问与多目标工具的完整资源声明另行按真实能力设计。
- 显式allow和会话allow可覆盖默认ask，不能覆盖显式deny；保持单次批准/会话allow的现有作用域。规则allow只解决政策授权，不能跳过工具能力限制或实际目标与授权的一致性检查。图中只保存当前批次决定，参数/目标/政策上下文实质变化时旧决定失效。
- [参考事实] Pi本机核对的官方扩展用工具名及event.input自行判断，没有通用allow/deny配置schema。Claude用`Tool`或`Tool(specifier)`，路径规则和MCP规则有专门消费分支；参数级匹配并非所有自定义工具都自动支持。因此这里借鉴工具选择器与条件的职责，不宣称照搬了统一规则引擎。来源：[Claude权限](https://code.claude.com/docs/en/permissions)。

“无法确认授权”当前事实：`file_permissions.decide_file_access`调用现有Read策略回调时，回调抛异常或返回非法结果会记录错误并返回`deny/policy_unavailable`，不是Auto不确定。新链路应区分：已解析完整对象且必需显式规则检查成功后，默认建议/Auto不可用可转ask；规则配置加载/校验失败是配置故障，用户确认在Agent启动前抛异常终止，不转权限拒绝消息或人工询问；对象无法确定先返回参数/路径错误；批准后对象改变则旧授权失效，按新对象重新审查。权限配置校验须结合本次实际注册的工具、参数schema及资源声明，前置到创建图/执行Agent之前；具体入口待工具注册与自定义工具设计一起确定，当前不实现。

中间件接入方式：在应用装配时准备审查组件并注入权限middleware，再交给现有`create_agent(middleware=[...])`一次性注册；按after_model逆注册顺序，保证权限节点在HITL前执行。运行中不修改middleware列表、不手写主循环。权限middleware决定何时审，注入组件封装快审/详审如何调用，结果只回权限state。

关键对象区别：`compiled_agent.ainvoke`运行整张图；`BaseChatModel.ainvoke`只是一次模型请求。权限hook调用后者不会递归进入主图。现有`_ContextCompactionMiddleware.abefore_agent → arun_summary_steps → model.ainvoke`已采用类似子调用。二阶段组件建议放在既定工具执行权限模块中，先不建独立Agent包或可加载脚本平台。

本机纯假模型注入探针：真实`create_agent`注册异步审查hook，hook调用注入reviewer，reviewer直接调用独立假模型。轨迹为`main_model → permission_hook → review_model_subcall → read_saved_decision → [暂停/批准] → read_saved_decision → tool_execution → main_model`。断言主模型2次、审批模型1次、工具1次，主消息类型仍为human/ai/tool/ai，无审查模型的ask消息；没有真实模型请求，也没有产品接线。现有策略不可用两项测试`2 passed in 0.77s`只验证旧行为，不能当作新Ask降级已经实现。

### 会话模式切换与创建时接线的进一步说明

- 用户确认会话内可切换权限模式，类似现有模型切换；优先实现后端，前端显示和Shift+Tab切换记录为后续适配。切换只影响下一批次，当前在途审查/挂起审批不改用新模式；前端可立即展示新选择，并表达下一批次生效。会话保存用户当前选择，批次在开始权限审查时读取并固定本批模式；模式不写入共享middleware实例的可变全局字段。批次指同一条主模型响应的工具调用集合，不等同整个run或用户会话；同一run内下一轮工具批次即可读取新模式。
- 拟议装配顺序：先确定本次注册工具集合，完成模型/权限配置读取及交叉校验；从ModelCatalog取得快审/详审模型，准备审查组件；向权限middleware注入已校验策略、审查组件及会话模式读取入口；最后由create_agent一次性构建含权限hook和HITL的图。创建模型对象、组件和图不等于发送模型请求。
- 运行时：主model节点产生工具调用；权限aafter_model读取并固定本批会话模式、形成审查对象。显式规则或默认策略可直接解决时不调用审批模型；本批Auto下默认ask才await审查组件。组件调用BaseChatModel.ainvoke，快审ask才再调用详审。返回allow/ask后，权限hook把本批次结果及所用模式写入图state；HITL的when及UI消费已有结果，人工批准后进入工具执行，工具消息才回主循环。
- 图内权限节点在等待审查时尚未结束；两个审批模型请求是该节点内部的异步IO，不递归调用compiled_agent.ainvoke，不临时增加节点。审查组件只封装两阶段调用；权限middleware决定何时调用，不在各工具正文内重复模型请求。具体类名仍为候选，不引入另一套Agent循环/模型配置加载器。
- 直接模型子调用不会自动经过主Agent的选模、响应门禁等middleware；审查组件须显式使用已配置模型、审查结果校验、既有停止等待与安全失败处理。审查响应只用于权限结果，不追加主会话messages。现有摘要链`_ContextCompactionMiddleware.abefore_agent → _acompact_messages → arun_summary_steps → model.ainvoke`已经使用类似子调用机制。
- 普通人工审批恢复从HITL节点重跑，其前置权限节点已完成并保存结果，故无需重跑模型审查。参数/目标/必要授权上下文实质变化时旧结果不能直接套用；单纯切换会话模式不使本批结果失效，不改变本批when结果或用户决定配对。下一批次读取新模式。具体接线须写入后续实施Spec，不承诺崩溃/重试场景下外部模型请求exactly-once。
- 本轮补充离线探针（LangChain1.4.2/LangGraph1.2.12，纯假模型、内存checkpoint、无文件/网络IO）：创建后trace为空；节点为model/tools/HITL.after_model/PermissionProbe.after_model；首次`main_model → permission_hook → fast_review → detailed_review → read_saved_decision`后暂停；批准恢复仅追加`read_saved_decision → tool_execution → main_model`。断言主模型2次、快审1次、详审1次、工具1次，messages仍human/ai/tool/ai。该探针验证框架接入机制，不代表新权限功能已经实现。

### 批量审批与既有调度（方向已确认，尚未批准实现）

- 用户确认保留批量暂停：主模型同条响应给出A/B/C，权限结果为allow/ask/allow时，先暂停整批，A/C也不执行；审批请求只列B。多条ask则一次列出所有待确认项，逐项作approve/reject，收齐决定后一次恢复；不新增部分提交后局部恢复。
- 批量是等待及提交时序，授权决定仍逐调用独立：批准B后A/B/C进入已有调度；拒绝B则B正文不执行，产生绑定原tool_call_id的error ToolMessage，A/C继续进入调度。拒绝一条不等同取消整批或结束Agent，批次也不承诺事务回滚。结果完整回填后由主模型继续；控制/运行机制故障仍遵守既定边界。
- 用户确认沿用现有串行/并行，不单独重设计、不引入工具依赖图：本批实际待执行调用中有任一sequential工具，就按模型给出的顺序执行整个待执行批次；全部parallel才采用已有并发上限。序列位置不自动表达结果依赖；模型须先取得某调用结果才能构造的后续调用应留到下一轮。
- 必须保留先后顺序：权限审查（含必要Auto子调用）→HITL批量审批/恢复→批次调度计划→工具执行。当前注册列表中ToolBatchMiddleware位于HITL之前，after_model逆序执行使HITL先于计划生成；新权限middleware接入后也须验证同一顺序。审批期间不先执行其他工具。
- 用户例子A(parallel)、B(sequential且ask)、C(parallel)：等待B时执行数为0；批准B后整批A→B→C；不能先把A/C作为纯并行子批启动，再在批准后补B。已经运行的C可能早于B，事后串行执行B无法恢复原顺序。若B被拒绝并已有拒绝ToolMessage，B不再进入待执行计划；只剩parallel的A/C可以按原规则并行。
- 若等待B时会话Default切到Auto，界面可显示Auto/下一批次生效；B仍需按本批Default作人工决定。HITL恢复只读本批结果；当前批完成、主模型提出下一批工具调用时采用Auto，不需要等新会话或下一条用户消息。
- 依据：本机HITL在工具节点前一次收集需确认调用并interrupt；SessionService要求每个pending项对应一个approve/reject；ToolBatchMiddleware排除已有ToolMessage结果的调用。现有三项测试（独立批准/拒绝、拒绝首项后继续、批准首项后保持执行顺序）本轮`3 passed in 0.84s`，只验证当前行为，不代表新权限模式已接线。
- 官方文档默认batched模式与上述时序一致；新per_call模式要求LangChain>=1.4.6，本机1.4.2不直接具备该入口。保留批量是当前需求及既有接线下的建议，不宣称框架只能批量，也不为本轮升级依赖。来源：[LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。Pi本机a69bef789串行路径逐调用prepare/执行，并行路径先逐调用prepare再Promise.all执行，beforeToolCall可await扩展UI；不将它概括成与本项目相同的批量审批。AgentX本轮核对工具装配→内置executor→doExecute/错误文本，未核实与本项目等价的批量审批合同，不据此作批量能力结论。
- 本轮新增确认：既有混合批次/异步并发与串行测试`4 passed in 2.61s`。纯假模型合成探针通过正式AsyncApplication，A/C为parallel、B为需审批的sequential：批准/拒绝两种恢复前均零工具执行；批准后A→B→C且三条success；拒绝后仅A/C执行，B为error。无真实模型/业务IO，未新增产品或测试文件。
- 同步入口现有缺陷，留待实施修复：相同合成场景批准路径通过；拒绝唯一sequential的B后，`ToolBatchMiddleware.after_model`按未完成A/C正确不建串行计划，但`_dispatch_tool_call`的缺计划检查仍扫描原AIMessage全部调用，看到已拒绝B就误抛ToolBatchSchedulingError。最小内存对象探针再次复现pending=[A,C]/plan=None却抛错，根因已定位；正式异步入口两条路径通过，不能扩大成所有入口都通过。修复应统一待执行集合的判断并补对应回归，不更改调度规则，不用ToolError掩盖调度故障；用户要求继续讨论，当前只记录不修代码。

---

以下保留2026-10-12原始调研及当时的候选方案；涉及本项目显式Ask、管线和实现范围的旧建议，以文首2026-10-15讨论校正为准。

## 1. 结论先行

1. 业界成熟方案都把权限拆成**相互独立的几层**，而不是一个"审批弹窗"：隔离（技术上能做什么）、规则（静态策略）、决策者（人/分类器/自动）、授权生命周期（一次/会话/永久）、审计。本项目目前只有"决策者=人"和"一次性授权"两块，且做得扎实；缺隔离、规则语言、模式和审计。
2. **沙箱和审批是两个正交维度**（Codex 的核心设计，Claude Code 同样强调"权限规则不是安全边界，沙箱才是"）。只有审批时，被注入的提示可以诱导模型构造用户看不出风险的命令；沙箱在审批失误时仍然兜底。
3. 规则语言建议采用 **deny → ask → allow 固定顺序、任一来源的 deny 不可被覆盖**（Claude Code 语义），比 Gemini 的数值优先级更容易解释和审计。
4. macOS Seatbelt 在本机可用 **[探针]**：工作区内可写、外部写入/读 `~/.ssh`/网络均被拒，子进程继承限制。第一版沙箱可以不引入 Docker。
5. 推荐分 4 个切片推进（第 7 节），第一片是"规则引擎 + 模式 + 审计事件"，不依赖沙箱即可交付价值。

---

## 2. 分析框架

| 层 | 回答的问题 | 典型实现 |
|---|---|---|
| ① 隔离 | 即使决定放行，进程技术上最多能做什么？ | Seatbelt、bubblewrap、Landlock+seccomp、容器、微虚拟机 |
| ② 规则 | 对某个工具调用，静态策略怎么说？ | allow/ask/deny 规则、路径 glob、命令前缀、域名 |
| ③ 决策者 | 规则说"ask"时，谁来回答？ | 人工审批、分类器模型、规则全拒（无人值守） |
| ④ 授权生命周期 | 一次同意管多久、管多宽？ | 仅本次调用 / 本会话 / 永久写入规则 |
| ⑤ 审计 | 事后能否复盘谁、依据什么、放行了什么？ | 决策事件、规则来源、被拒原因 |

威胁模型（贯穿五层）：**致命三要素**——私有数据 + 不可信内容 + 对外通信同时存在时，提示注入可导致数据外泄。对测试 Agent 而言：私有数据=工作区、`.env`、凭证；不可信内容=被测系统响应、网页/文档；对外通信=Bash 网络、HTTP 工具。切断任一条腿即可大幅降险，最常见的是用网络白名单切断第三条。

---

## 3. 各参照实现

### 3.1 Pi：核心零权限，全部交给扩展

**[源码]** 调用链：

```
agent-loop.prepareToolCall (agent/src/agent-loop.ts:600-668)
  → 查找工具 → 参数准备与 schema 校验
  → config.beforeToolCall(...)            # 校验之后才进入权限钩子
      → AgentSession._installAgentToolHooks (coding-agent/src/core/agent-session.ts:485)
      → ExtensionRunner.emitToolCall (extensions/runner.ts:934)
          按扩展加载顺序依次调用 tool_call 处理器；第一个返回 block 的立即生效
  → block → 生成 isError 工具结果回给模型（可带 terminate 提示结束本批次）
  → 钩子抛异常 → prepareToolCall 的 catch 生成错误结果 → 工具不执行（fail-closed）
```

README 明确："No permission popups. Run in a container, or build your own confirmation flow with extensions."

官方示例扩展（`coding-agent/examples/extensions/`）：

| 示例 | 做法 | 可批评之处 **[推断]** |
|---|---|---|
| `permission-gate.ts` | 正则匹配 `rm -rf`、`sudo`、`chmod 777`，命中时弹 Yes/No；无 UI 时直接拒绝 | 黑名单正则可轻易绕过：`rm -r -f`、`/bin/rm`、`find -delete`、`python -c` |
| `protected-paths.ts` | `path.includes(".env")` 等子串匹配，拒绝 write/edit | 未做规范化和符号链接解析；`bash` 写同一文件不受限 |
| `sandbox/` | 用 `@anthropic-ai/sandbox-runtime` 替换内置 bash；配置分全局/项目两级合并；默认拒读 `~/.ssh`、`~/.aws`，只允许写 `.` 和 `/tmp`，网络按域名白名单 | **沙箱初始化失败时退回无沙箱 bash，只发一条通知**（fail-open） |

值得学的：钩子位置正确（参数校验之后、执行之前）；扩展异常即阻断；拒绝结果作为工具错误回给模型，让模型换方案；核心保持极简。不该照搬的：黑名单式规则、沙箱 fail-open。

### 3.2 Claude Code：规则 + 模式 + 沙箱 + 分类器，层次最完整

**[文档]** 要点：

- **规则语法**：`Bash(npm run test:*)`、`Read(./secrets/**)`、`WebFetch(domain:example.com)`、`mcp__server__tool`；支持 gitignore 风格的 `!` 例外，但例外只作用于同一来源。
- **评估顺序**："Rules are evaluated in order: deny, then ask, then allow. The first match in that order determines the outcome, and rule specificity doesn't change the order." 任何层级的 deny 都不能被其他层级的 allow 覆盖；托管（企业）配置优先级最高。
- **Bash 匹配**：会按 `&&`、`||`、`;`、`|` 等拆分复合命令，每个子命令单独匹配；会剥离 `timeout`、`nice`、`nohup` 等包装；`find -exec/-delete` 这类前缀规则无法自动放行。文档明确承认局限："a deny or ask rule ... isn't a security boundary around the program"，例如 `Bash(rm *)` 拦不住 `/bin/rm` 或 `bash -c 'rm ...'`，要靠沙箱。
- **模式**：`default`（Manual）、`acceptEdits`、`plan`、`auto`、`dontAsk`（凡需询问的一律拒绝，适合无人值守）、`bypassPermissions`（只建议在容器/虚拟机里用）。
- **Hook 与规则的关系**：PreToolUse hook 返回 allow 也不能越过 deny/ask 规则；hook 返回阻断则优先于 allow 规则。
- **与沙箱的关系**：沙箱开启且 `autoAllowBashIfSandboxed=true`（默认）时，沙箱内的 Bash 不再因"整个 Bash 需要询问"而弹窗，但内容级 ask 规则（如 `Bash(git push *)`）、deny 规则、对关键路径的删除仍然生效。
- **工作区信任**：项目 `.claude/settings.json` 里的 allow 规则属于"授予能力"，要等用户接受信任对话框才生效；deny/ask 只做限制，无需信任。
- **auto 模式分类器**：按固定顺序判定——显式规则 → 工作区内只读与编辑自动放行（受保护路径除外）→ 其余交给分类器。进入 auto 模式时会丢弃 `Bash(*)`、`Bash(python*)` 这类等于任意代码执行的宽泛 allow 规则。**分类器看不到工具结果**："Tool results are stripped from those requests, so hostile content in a file or web page can't manipulate the classifier directly." 另有服务端探针扫描进入的工具结果。连续被拦超过阈值会退回人工审批。
- **沙箱运行时**（已开源，`@anthropic-ai/sandbox-runtime`）**[文档]**：macOS 动态生成 Seatbelt profile，Linux 用 bubblewrap + seccomp；网络统一走宿主机上的 HTTP/SOCKS5 代理做域名白名单；读取规则是"先拒大范围再放行例外"，写入规则是"仅允许列出的路径"；会监听违规日志。README 列出的局限包括：无法处理证书固定/mTLS 客户端，`enableWeakerNetworkIsolation` 等选项会打开外泄通道。

### 3.3 Codex：沙箱模式 × 审批策略两个维度

**[文档]**

- 沙箱模式：`read-only`、`workspace-write`（默认）、`danger-full-access`。沙箱作用于它启动的所有命令，不只是内置文件操作。
- 审批策略：`on-request`（在沙箱内工作，需要越界时请求批准）、`never`；`untrusted` **已退役**。
- 平台机制：macOS Seatbelt；Linux/WSL2 需要 bubblewrap 和用户命名空间；Windows 有原生沙箱。
- 命令规则：支持按命令前缀写 allow / prompt / forbidden。
- 设计精髓：先在沙箱里尝试，越界才升级为审批。审批对象从"每一个命令"变成"每一次越界"，审批次数大幅下降，安全性却不降。

### 3.4 Gemini CLI：TOML 策略引擎 + 分层优先级

**[文档]**

- 规则字段：`toolName`（支持通配）、`commandPrefix` / `commandRegex`、`argsPattern`（对 JSON 化参数做正则匹配）、`decision`（`allow` / `deny` / `ask_user`）、`priority`（0–999），可用 `mcpName` 精确定位 MCP 工具。
- 分层：Default 1、Extension 2、Workspace 3（文档注明暂未启用）、User 4、Admin 5，最终优先级 = 层基数 + priority/1000，保证 Admin 永远压过下层。
- 规则可以限定在某些审批模式下生效（`default`、`autoEdit`、`plan`、`yolo`）。
- 没有参数条件的全局 deny 会把工具从模型可见列表里直接移除。
- 系统级策略目录要求严格的文件属主，防止被普通用户篡改。

和 Claude Code 对比 **[推断]**：Gemini 的数值优先级更灵活（同层内也能让更具体的 allow 胜出），但用户很难推断"这条命令最终会怎样"；Claude Code 的 deny 优先更保守、更容易解释和审计。本项目建议采用后者。

### 3.5 OpenHands：确认策略 + 风险分析器

**[文档]** 确认策略分三种：`AlwaysConfirm`、`NeverConfirm`、`ConfirmRisky`（只确认有风险的操作）。风险分析器有两类：一类是 LLM 分析器；另一类是确定性分析器，包括正则签名、`PolicyRail`（识别"下载后管道执行"这类组合威胁）和 `Ensemble`（取多个分析器中的最高风险）。风险分为 LOW / MEDIUM / HIGH / UNKNOWN 四级。可以学"多个分析器取最高风险"和"识别组合威胁"这两个思路。

### 3.6 MCP 规范：工具风险元数据

**[文档]** 工具可以声明 `annotations`。常用的风险提示有：`readOnlyHint`（默认 false）、`destructiveHint`（默认 true）、`idempotentHint`（默认 false）、`openWorldHint`（默认 true）。这几个字段名和默认值来自较早版本的规范，本轮读取的 2025-11-25 页面没有列出字段细节。规范要求："clients MUST consider tool annotations to be untrusted unless they come from trusted servers"。同时建议：保留人在回路，调用前向用户展示工具输入，并记录工具使用情况以便审计。

**[推断]** 本项目可以按这套字段给内置工具标风险，作为默认规则和 UI 展示的依据。第三方 MCP 工具的注解只能当参考，不能用来放宽权限。

---

## 4. 横向对比

| 维度 | Pi | Claude Code | Codex | Gemini CLI | OpenHands | **本项目** |
|---|---|---|---|---|---|---|
| OS 隔离 | 扩展示例（srt） | srt：Seatbelt / bwrap + 网络代理 | Seatbelt / bwrap / Win 原生 | Seatbelt / Docker | Docker 运行时 | **无**（宿主机执行，环境变量已清理） |
| 规则语言 | 无，扩展代码自定 | allow / ask / deny + 匹配符 | 命令前缀规则 | TOML + 优先级 | 分析器 | **无**，由各工具硬编码的分类函数决定 |
| 冲突处理 | 第一个 block 生效 | deny > ask > allow，跨来源 | — | 层级 + 数值优先级 | 取最高风险 | — |
| 模式 | 无 | 6 种 | 沙箱 × 审批组合 | 4 种 | 3 种确认策略 | **无** |
| 自动决策 | 无 | 分类器（看不到工具结果） | 自动审查 | — | LLM / 规则分析器 | 无 |
| 授权粒度 | 由扩展自定 | 本次 / 会话 / 永久写规则 | — | 审批向上传播 | — | **只有本次**，且精确绑定审查对象 |
| 钩子 | `tool_call` | PreToolUse，不能越过 deny | — | 扩展层策略 | 分析器 | 文件策略回调（仅 read） |
| 钩子出错 | fail-closed | — | — | — | — | 文件策略 fail-closed |
| 沙箱不可用时 | 示例 fail-open | — | — | — | — | — |

---

## 5. 本项目现状

**[源码]**

- **审批**：工具通过 `attach_approval` 声明 `ApprovalPolicy(when, description, review, grant_factory)`（`agent_runtime/tool_approval.py`），装配时转成 LangChain `HumanInTheLoopMiddleware` 的 `interrupt_on`（`agent_runtime/__init__.py:160-169`）。Web 支持逐项 approve / reject。
- **一次性精确授权**：`OneUseApprovalGrants.consume(tool_call_id, subject)` 要求调用 ID 和审查对象都一致，用一次即删除。这样能防止"审批的是 A，执行的是 B"，**这一点比多数参照实现更严格，应保留**。
- **文件工具**：read / ls / find / grep 由各自的分类函数返回 allow / ask / deny（工作区内放行，工作区外询问，非法路径拒绝），执行时用同一个分类函数重新判断。write / edit 只能操作工作区内的文件。read 支持可插拔的同步策略回调，回调出错时拒绝（`file_permissions.py`）。
- **Bash**：每条命令都要审批（`bash_requires_approval`）；在宿主机执行；环境变量只保留 `PATH`、`HOME=工作区`、`TMPDIR`、`LANG`，凭证不会经环境变量泄漏；有进程组超时终止。**但命令仍然可以读写用户能访问的任何文件，也能访问网络。**
- **缺失**：用户可配置的规则、模式（无人值守、只读规划等）、OS 沙箱、网络限制、"不再询问"、权限决策审计事件、工作区信任。

---

## 6. 本机探针：macOS Seatbelt

**[探针]** macOS 26.4，`/usr/bin/sandbox-exec` 存在（Apple 标记为弃用，但 Codex、Claude Code、srt 都在用）。本机没有 Docker。

Profile 核心内容：默认拒绝；允许进程执行和读文件；拒绝读 `~/.ssh`、`~/.aws`；只允许写工作区和 `/dev/null`；拒绝所有网络。

| 操作 | 结果 |
|---|---|
| 工作区内写文件并读回 | 成功 |
| 写工作区外的文件 | `Operation not permitted`，文件未生成 |
| `ls ~/.ssh` | `Operation not permitted` |
| `curl https://example.com` | `Could not resolve host`，网络被拒 |
| Python 子进程写工作区外的文件 | `PermissionError`，限制被子进程继承 |

边界：只验证了这 5 个场景。没有验证网络代理白名单、性能开销、pytest 和 HTTP 测试在沙箱里的兼容性，以及各类逃逸手段。

---

## 7. 推荐方案与切片

### 7.1 目标结构 **[推断]**

```
工具调用（参数已通过 schema 校验）
  → ① 规范化审查对象：路径解析、符号链接解析、命令拆分
  → ② 规则引擎：内置默认 + 用户 + 项目（仅在工作区受信任后生效）
        deny → ask → allow；没有规则命中时由当前模式决定
  → ③ 决策者
        allow → 执行（Bash 仍在沙箱里执行）
        deny  → 错误 ToolMessage，告诉模型原因和可选的替代方案
        ask   → 人工审批（HITL）；dontAsk 模式下直接拒绝
  → ④ 授权：一次（现有机制）/ 本会话 / 永久写入规则，始终绑定规范化后的审查对象
  → ⑤ 审计：发出 permission_decided 事件，记录决定、规则来源、审查对象摘要
  → ⑥ 执行层隔离：Seatbelt 沙箱，默认禁网，按白名单放行被测系统
        沙箱不可用时 fail-closed（不执行，或改为每条命令都要审批）
```

设计约束：

- 扩展钩子可以收紧权限，但不能越过 deny（Claude Code 语义）；钩子出错一律阻断（Pi 语义）。
- 沙箱内的 Bash 可以免整体询问，但内容级 ask 规则和关键路径删除仍然要询问（Claude Code 语义）。
- 越出沙箱时走审批升级，而不是静默失败（Codex 语义）。
- 工具按 MCP 风格标注风险元数据（只读 / 破坏性 / 幂等 / 开放世界），内置默认规则和 UI 展示都由这些元数据生成。
- 以后如果引入分类器，它只能看到用户消息和工具调用，不能看到工具结果。

### 7.2 切片建议

| 切片 | 内容 | 验收要点 | 依赖 |
|---|---|---|---|
| **P1 规则 + 模式 + 审计** | 规则文件格式、deny 优先的规则引擎、`manual` / `plan` / `dontAsk` 三种模式；现有各工具的分类函数迁移为内置默认规则；`permission_decided` 事件 | 规则冲突矩阵测试；`dontAsk` 下无人值守跑通且没有任何越权；现有审批测试不回归 | 无 |
| **P2 Bash 沙箱（macOS）** | 生成 Seatbelt profile，Bash 包进沙箱执行，默认禁网；沙箱不可用时 fail-closed；沙箱内 Bash 可以免整体询问 | 逃逸测试：写工作区外、读凭证、访问网络、通过子进程绕过、通过符号链接绕过；pytest 在沙箱内能跑 | P1 |
| **P3 网络白名单** | 本地代理加域名/端口白名单，只放行被测系统 | 白名单内可以访问，白名单外被拒；DNS 外泄测试 | P2 |
| **P4 授权生命周期与信任** | "本会话不再询问"、"永久写入规则"；项目里的 allow 规则需要工作区信任后才生效 | 复合命令按子命令分别保存规则；未信任的工作区里 allow 规则不生效 | P1 |
| 以后 | Linux bubblewrap 或容器、分类器自动审批、多用户策略（Cedar / OPA） | 评测证明有需要再做 | — |

需要做的选型：沙箱是**自己生成 Seatbelt profile**（纯 Python，可控，但网络代理要自己写），还是**调用 srt 命令行**（功能完整，但引入 Node 依赖、版本还在 0.0.x）。建议 P2 先自己生成最小 profile 并禁网，P3 时再评估是否改用 srt 的代理。

### 7.3 风险与开放问题

- `sandbox-exec` 被 Apple 标为弃用，未来 macOS 版本可能移除；要设计平台抽象层，并保留容器方案作为后备。
- 规则语言是对命令文本做匹配，有绕过面（见 3.2），所以必须和沙箱一起使用，不能单独宣称安全。
- 测试执行需要访问被测系统的网络，这和"默认禁网"冲突，必须等 P3 解决后才能无审批执行测试。
- 规则文件本身也是攻击面：项目目录里的规则可能来自不可信的仓库，所以需要 P4 的工作区信任机制。

---

## 8. 面试问答准备

1. **为什么有审批还要沙箱？** 审批依赖人或模型对命令文本的理解。文本可以变形（`/bin/rm`、`bash -c`），注入内容也可以诱导用户批准；沙箱在操作系统层面限制效果，审批出错时仍然兜底。
2. **deny 优先和数值优先级怎么选？** deny 优先可预测、可审计，代价是不能在 deny 范围里用更具体的 allow 开例外；数值优先级灵活，但最终结果难以推断。安全默认选前者。
3. **分类器自动审批安全吗？** 它是减少审批疲劳的手段，不是安全边界。关键设计是不让分类器看到工具结果，防止被注入内容直接操纵，同时还要靠沙箱和网络限制兜底。
4. **怎么防止"审批 A、执行 B"？** 授权绑定规范化后的审查对象和调用 ID，执行时用同一套规范化逻辑重新判断（本项目已实现）；还要处理符号链接和检查到使用之间的竞态。
5. **"不再询问"怎么保存才安全？** 复合命令按子命令分别保存规则，而不是保存整串命令；不能把能执行任意代码的宽泛规则（比如 `python*`）存为永久 allow；项目里的 allow 规则要经过工作区信任。
6. **沙箱初始化失败怎么办？** fail-closed：不执行，或者降级成每条命令都要审批，并且明确提示用户。Pi 的示例扩展是 fail-open，这是反面例子。
7. **用致命三要素分析测试 Agent？** 私有数据是工作区和凭证；不可信内容是被测系统的响应和外部文档；对外通信是 Bash 网络和 HTTP 工具。只放行被测系统的网络白名单，就切断了第三条腿。
8. **第三方 MCP 工具的风险注解能信吗？** 规范要求视为不可信，除非来自受信任的服务器。只能用来收紧权限或辅助展示，不能用来放宽权限。

---

## 来源

- Pi 本机源码：`packages/agent/src/agent-loop.ts`、`packages/coding-agent/src/core/agent-session.ts`、`src/core/extensions/runner.ts`、`examples/extensions/{permission-gate.ts,protected-paths.ts,sandbox/}`、`packages/coding-agent/README.md`
- [Claude Code: Configure permissions](https://code.claude.com/docs/en/permissions)
- [Claude Code: Choose a permission mode](https://code.claude.com/docs/en/permission-modes)
- [anthropic-experimental/sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)
- [Codex sandboxing](https://learn.chatgpt.com/docs/sandboxing)
- [Gemini CLI policy engine](https://geminicli.com/docs/reference/policy-engine)
- [OpenHands SDK security](https://docs.openhands.dev/sdk/guides/security)
- [MCP 2025-11-25: Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Codex CLI, Claude Code & Gemini CLI sandbox and approval modes compared](https://inventivehq.com/blog/ai-coding-cli-sandbox-approval-modes-compared)
- [Simon Willison: The lethal trifecta](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents)
- [Simon Willison: Auto mode for Claude Code](https://simonwillison.net/2026/Mar/24/auto-mode-for-claude-code/)

## 审批粒度复核（2026-10-15）

- 更正表述：本机langchain1.4.2官方HITL.after_model收集action_requests后一次interrupt；这是现用中间件版本/接线，不是LangGraph不能逐调用审批。此前本稿已记>=1.4.6入口，后续讨论未把该官方候选重新比较，不能据旧选择继续推导完整子Agent必须批量或禁止内部Ask。
- 当前官方文档推荐新Agent考虑per_call：interrupt_mode='per_call'从工具wrapper中逐调用暂停，按interrupt ID回答，可只回答一条，其他保持pending；不保证提示顺序或自动按模型顺序执行。[官方HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。
- PyPI元数据确认1.4.6已发布；内存读取正式wheel的HITL源码，确认after_model在per_call直接返回，wrap_tool_call/awrap_tool_call调用_resolve_per_call，由interrupt(value,response_schema=...)取得单项决定。未安装或执行该wheel。包要求langchain-core>=1.6.10,<2及langgraph>=1.2.12,<1.3；本机分别1.6.6/1.2.12，不能只改一个模式参数就宣称完成迁移。[正式元数据](https://pypi.org/pypi/langchain/1.4.6/json)。
- Claude公开Agent SDK通过单次工具的can_use_tool/canUseTool回调await用户决定；它是逐调用授权入口，不是整批decisions列表。CLI前台/后台子Agent都可在主会话呈现审批。公开资料未确认任意同批工具的严格调度顺序或全树暂停，不能据UI逐条弹窗推导这些内部合同。[回调](https://code.claude.com/docs/en/agent-sdk/user-input)、[子Agent](https://code.claude.com/docs/en/sub-agents#run-subagents-in-foreground-or-background)。
- Pi固定a69bef789：executeToolCallsSequential以for/await逐个prepareToolCall（await beforeToolCall→AgentSession事件→扩展UI）再执行/回填，然后下一项；executeToolCallsParallel先逐项prepare，保存尚未启动的执行函数，全部准备后Promise.all。因此串行路径是逐条审查并逐条执行；并行路径是逐条审查后一起执行，不能只说Pi永远逐条批准立即跑。
- AgentX固定3fcfa0e：内置executor.executeTools检查名称/参数/supportsTools后直接doExecute；本轮未查到等价逐调用人工审批/恢复合同。会话interrupt和工具上架审核不当作执行前审批证据。

底层机制一次性stdin探针：本机1.4.2/1.2.12，真实create_agent、假模型、InMemorySaver；A/B工具正文各调用官方interrupt，C不询问，仅内存计数，无网络/业务文件IO。实际输出：

| 阶段 | 已执行 | 仍待审批 |
|---|---|---|
| 首次调用 | C | A、B |
| 只按A的interrupt ID批准 | C、A | B |
| 再批准B | C、A、B | 无，主模型完成 |

断言全部通过、进程退出0；证明底层已能独立恢复，不代表1.4.6官方HITL或本项目串行调度已验收。审批粒度、执行次序、暂停范围是三个合同。下一步候选是官方per_call加可恢复调度的定向验证，保留原已确认批量方案供比较；不自行升级依赖、不把逐调用等同任意工具无序并行。

### 发布成熟度与批量子审批兼容性复核

PyPI正式wheel上传时间2026-10-14T17:27:47Z，功能PR41062于同日16:49:33Z合入；截至本轮约一天半，不能用“官方已发布”替代成熟运行证据。PR新增逐call/按ID、同名工具多次调用、非法答案、异步和重试等测试，但本轮未运行上游测试。PR亦说明多项恢复混入非法答案时存在底层checkpoint竞态边界，不能声称自行写wrapper即可规避。[PR41062](https://github.com/langchain-ai/langchain/pull/41062)、[正式元数据](https://pypi.org/pypi/langchain/1.4.6/json)。

新增一次性真实框架探针仍使用本机1.4.2与官方batched HITL：父Agent调用delegate，子Agent同批提出免审child_lookup和需审child_action；首次只产生child_action审批且两动作均未执行。以单项批量decisions批准后父子完成，父delegate入口2次，两实际动作各1次。证明批量本身支持嵌套审批，升级或自研逐调用不是子Agent成立的必要条件。

同一真实暂停结果传入当前SessionService._pending_tool_approval，复现RuntimeError('Tool approval actions do not match model calls')：当前函数拿父messages最后一条AI的delegate调用，核对子中断里的child_action，缺少来源作用域/子PreparedToolCall消费；同时只接受一个interrupt。结合注册研究第20节的批次计划丢失/中断记failed，当前阻点是嵌套识别、调度与授权恢复，而非批量语义。该探针只计内存，无网络/业务文件IO，退出0，不代表产品已适配。

自研最小候选是在现有版本中新增项目工具middleware：消费保存的权限结果，Ask时使用官方interrupt，approve/reject后执行或返回同ID消息，继续使用官方Command/checkpointer。只有approve/reject，不照搬新版本edit/respond/schema全套能力。仍需应用pending按来源/interrupt ID管理、部分决定与重复/过期请求处理、CLI/Web逐项提交和批次可恢复；这些接线不会因自行实现或升级而消失。回调一直await用户输入则会改变现有run suspended/resume合同，不作为默认候选。

讨论结论边界：当前建议先保持依赖，批量子审批完整适配与逐调用体验分别评估；是否改变已确认批量合同、是否自研尚待用户决定。工作量估计不作为实现授权或交付承诺。

### 单个前台子Agent批量审批正常场景（候选）

用户认可此正常路径：父批次只有已获准的通用subagent调用p1（agent='code_reviewer'、task为检查任务），子Agent同批Read(c1,Allow)/Bash(c2,Ask)；批准后子批次执行、子模型给摘要、父模型继续。保持1.4.2和官方批量HITL，不构成产品实施授权；本小节不声称已解决多父调用/多子并行/后台或跨重启恢复。此前review_agent仅为示例/探针名，不保留为通用工具名或兼容别名。

- 通用模型工具名采用subagent，界面显示SubAgent；agent参数选择子Agent定义，task承载任务。Pi本机a69bef789注册名为subagent、label为Subagent；Claude当前工具名为Agent；LangChain官方单一分发示例名为task。[Pi入口](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/examples/extensions/subagent/index.ts:472)、[Claude工具](https://code.claude.com/docs/en/tools-reference#agent-tool-behavior)、[LangChain单一分发](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents#single-dispatch-tool)。模型工具名沿项目现有小写命名，角色名不编码成多个固定工具。
- 每个新子实例有独立agent_path，建议结构为父agent_path/agent_name/instance_id，instance_id由父保存批次ID与父工具调用ID稳定派生；例如root/code_reviewer/b1-p1、root/test_runner/b1-p2、root/code_reviewer/b1-p3。同角色的两次新建也不同；同一实例审批恢复或后续继续沿用原路径，不随run_id改变。路径是逻辑身份，工作目录由workspace/cwd另行管理，不自动给每个Agent建不同工作区。
- 子图由create_agent创建并继承父checkpointer，调用保留框架config/namespace，显式context_schema=dict和子运行上下文；不能另起不相干thread。注册表共用，父子活动工具与图state各自独立。
- 子Permission.after_model一次准备并保存ToolExecutionState；HITL在子工具执行前暂停，Read/Bash都不启动。拟扩展现有ObservedHumanInTheLoopMiddleware._create_action_and_config：保留官方name/args/description及review_config，在action加入项目生成的naiyue_approval（agent_path、batch_id、tool_call_id、保存subject的必要投影）。已有方法已被项目覆写，不新增异常重包装或自写interrupt协议；这些元数据不能来自模型args或可选观察器。
- 应用collect_pending_approvals从真实result.__interrupt__逐组读取interrupt.id、官方actions和项目来源快照，保存pending；不再用父messages最后AI反推子调用、不重跑review。数据经过项目适配器生成，恢复后执行仍与子图自己的PreparedToolCall重验一致性，不让UI提交任意目标/参数。
- UI仍收齐本组approve/reject一次提交，仅增加来源展示。本例只有一个子审批组，原外部整批提交操作可保留；内部prepare_resume按保存对象构造Command(resume={interrupt_id:{decisions:[...]}})，并构造仅该agent_path可见的OneUseApprovalGrants。父入口取得同thread恢复命令，工具包装重入，再由框架恢复内部子HITL；应用不直接另开子run重放任务。
- 父subagent调用本例已经Allow，重入时核对同一未完成调用并复用保存决定，不重跑父Auto；子Bash正文此前未进入，批准后第一次消费人工grant。父工具包装在调用子图前只做身份/上下文准备，不做不可重放业务IO。父工具也先需人工批准等扩展情况另验收，不用此正常路径证明所有许可恢复均已实现。
- ToolBatch拟新增ensure_plan供after_model与dispatch复用：本例恢复时从父保存批次及未完成p1重建运行期AsyncBatchPlan，不依赖after_model再次执行；子批次在HITL通过后正常建计划。GraphInterrupt不得标普通failed。多父调用时已完成task的pending writes、顺序位置与其他等待者需另设计/探针，不能仅靠父messages重建就宣称全场景安全。
- 涉及目标文件/方法：子Agent工具包装及装配；tools权限/ToolExecutionState与许可；tool_execution/approval及现有ObservedHITL动作生成；application/session_approval的collect_pending_approvals/prepare_resume（总Spec已规划该拆分）；SessionService两条结果入口、resource_invocation恢复；ToolBatch/AsyncBatchPlan；Web/CLI审批来源字段和前端ApprovalPanel。正常路径不要求改为逐条提交。

此前一次性异步机制探针使用review_agent示例名、真实父子create_agent、官方1.4.2 batched HITL、子准备state、action元数据、按interrupt ID恢复、现有OneUseApprovalGrants；最后加入当前子ToolBatch（Bash显式sequential）和仅限父单调用的计划重建适配。实际断言：准备1次、父包装进入2次、父计划重建1次、lookup→command按序各1次、grant再次消费False、父完成，进程退出0。未启动真实Shell/读文件/模型服务；应用采集器/UI及通用调度恢复尚未实现。显式context_schema=dict属于探针与后续装配合同，不把机制验证表述为产品功能完成；命名调整只更新设计，不把旧探针说成已验证通用角色选择和新实例ID派生。
