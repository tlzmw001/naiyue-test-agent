# Spec085-04：ToolSpec、启动注册与统一内置集合

日期：2026-10-16。状态：**已授权实施并通过本机验证。** 接续已实施的[085-03B](085-03b-file-tool-settings.md)。本片交付启动时可用的唯一工具接入合同；运行中登记/活动切换、动态提示词与新权限管线在后片接入，不宣称静态编译图已经支持新增名称。

## 1. 当前问题与本片边界

现在CLI/Web分别拼BaseTool列表，串并行从metadata读，审批也有自己的metadata。自定义工具已经能通过列表传入；缺少的是一个明确的项目定义及统一登记入口。本片统一原工具引用、真实公开输入模型、执行模式和能力注解，仍由LangChain执行原BaseTool，不增加执行分发器或复制工具。

先搜后建：当前没有ToolSpec/ToolRegistry/builtin集合入口；已有schemas.py负责公开/执行模型，permissions.py负责现行审批，batch.py负责现行调度，均扩展真实消费者，不再建同职责包装。

本片**只迁移执行模式metadata**。新Deny/Allow、FilePermission/ToolPermission、PreparedToolCall等尚未实施，现有审批metadata及Read策略仍是实际生效的唯一权限入口，继续保留。本片不新增另一个无消费者的permissions字段；权限片接好其生产者和消费者时一起替换。prompt字段也在ToolSet真正消费时增加。最终ToolSpec合同仍以总Spec第4节为准；这是按消费者分片，不是保留两套权限接口或自动适配旧接入合同。

## 2. 数据与接口

```python
@dataclass(frozen=True)
class ToolSpec:
    tool: BaseTool
    input_schema: type[BaseModel]  # 作者真实公开模型，不能用弱化的tool_call_schema
    execution_mode: Literal['parallel', 'sequential'] = 'parallel'
    side_effects: Literal['read_only', 'may_mutate', 'unknown'] = 'unknown'

class ToolRegistry:
    def register(self, spec: ToolSpec) -> None: ...
    def get(self, name: str) -> ToolSpec: ...
    def select(self, names: Sequence[str]) -> tuple[ToolSpec, ...]: ...
    def names(self) -> tuple[str, ...]: ...

def create_builtin_tools(settings: ToolSettings, *, host_bash: bool) -> list[ToolSpec]: ...
```

以上省略号只表示拟议接口，产品文件不得交付占位实现。名称/说明从spec.tool取得，不另外保存可漂移副本。frozen固定Spec字段，不声称冻结第三方BaseTool；作者登记后不得修改工具名称、schema及执行合同。

register先校验再在短锁内登记，重名失败且旧项保持；get未知名明确报错；select保留调用者顺序、拒绝重复/未知名，锁内取原引用、锁外使用。不支持覆盖/卸载。首版公开App只提供get_registered_tools()供可信代码检查登记结果；运行中register_tool/set_active_tools留到动态工具片，避免登记成功但图不能执行的假功能。

登记校验：真实BaseTool；有效名称与非空描述；公开Pydantic模型；模式/副作用值域；handle_tool_error禁用；response_format仅content/content_and_artifact；return_direct=False。公开模型不得暴露runtime等注入字段，其普通字段、默认、约束及验证器须与执行模型同源。内置工具用真实继承关系保证，自定义作者用同一公开模型派生执行模型；不通过JSON schema重建而丢失strict/extra/validator。具体支持的继承/同模型规则由测试锁定，不新增自动修复或工具复制。

不在启动校验中执行业务工具，不调用validate_input构造假参数，不提前打开目标文件。未来权限节点才用公开模型校验真实调用。

内置声明：Read/Ls/Find/Grep为parallel、read_only；Write/Edit/Bash为sequential、may_mutate。当前没有PowerShell工厂，等双Shell片加入同一集合；不提前创建空工具。自定义默认parallel/unknown。side_effects只描述能力并可从App目录读取，不决定许可、重试或调度。

## 3. 文件、函数与删除项

| 文件（相对naiyue_test_agent） | 具体变化与调用方 |
|---|---|
| tools/registry.py（新） | 实现ToolSpec与ToolRegistry；启动App装配、低层Agent、调度及登记目录读取实际使用 |
| tools/builtin.py（新） | create_builtin_tools收集当前六文件工具及可选Bash；四项已支持配置传给工厂，Find/Grep/Bash仍用各自现有设置；CLI/Web共用 |
| tools/read.py、ls.py | 会话工厂返回ToolSpec；保留build_tool_schemas返回的public，执行模型仍传@tool；旧固定workspace工厂消费者迁移后删除 |
| tools/write.py、edit.py | 同样返回ToolSpec并显式模式/副作用；删除naiyue_execution_mode写入；IO预算/提交算法保持 |
| tools/find.py、grep.py、bash.py | 会话工厂返回ToolSpec并使用现有公开模型；不借本片改搜索、进程和权限；Find/Grep固定workspace工厂及其孤立schema消费者迁移后删除 |
| tools/__init__.py | 导出唯一新接入合同及内置集合，删旧固定workspace工厂导出，不加兼容别名 |
| application/__init__.py | build_local_application仍用tools关键字，但元素严格改为ToolSpec；在load_model_catalog前准备并校验registry；LocalApplication持有它并新增get_registered_tools；_assemble_application将同一registry交运行层 |
| application/async_application.py | build_async_local_application先登记验证再获取模型客户端；AsyncApplication持有registry并提供同一只读目录；保留事件循环/关闭合同 |
| application/persistent.py、async_persistent.py | 同一登记路径先于模型/DB资源获取；原checkpoint、恢复与必要清理保持，不增会话DB字段 |
| agent_runtime/__init__.py | build_runtime_agent/_assemble_interactive_runtime/_build_runtime_agent/build_configured_runtime_agent从tools改为tool_registry；从登记原引用构造启动BaseTool列表；原HITL规则读取这些原工具；删被登记验证替代的handle_tool_error循环 |
| agent_runtime/tool_execution/batch.py | 构造接registry，模式从spec.execution_mode取；删_MODE_KEY及metadata模式解析，不改整批串并行算法与WAIT配置 |
| application/cli.py、web/app.py | 用create_builtin_tools代替重复手写列表；原workspace/host-bash启用条件不变，传ToolSpec给应用 |

公共启动登记过程有同步/异步/持久多个真实入口，可在application/__init__.py新增prepare_tool_registry(specs)供它们共用；它只登记，不读配置、不造模型、不套执行器。工具设置继续由CLI/Web现有加载器读取，自定义SDK作者显式传冻结设置。

上下文压缩的make_context_compaction_middleware、请求观测及模型绑定仍接启动时的原BaseTool列表；本片数量与顺序固定。动态片再将其改为每次请求同源工具集合，不提前挂空ToolSet hook。现行approval_policies_for_tools仍供HITL/SessionService读取，保留其权限metadata校验；名称唯一由registry负责后，删除其重复的名称扫描，不减少非法权限声明检查。

## 4. 正常与失败的完整链路

启动示意：

```text
CLI/Web load_tool_settings
 → create_builtin_tools(settings, host_bash=既有开关)
 → create_session_*_tool：原BaseTool + 真实public模型 → ToolSpec
 → build_*application(tools=[ToolSpec, ...])
 → prepare_tool_registry：register每项
 → 准备模型/会话/checkpointer
 → _assemble_application(..., tool_registry=registry)
 → _assemble_interactive_runtime
 → create_agent(tools=[原spec.tool, ...])
```

例如模型提出Read和Write：仍先经过现有整批审批；ToolBatch从登记定义发现Write=sequential，按原顺序执行；Read/BaseTool校验其执行模型，Write调用原_write；ToolException经085-01公共ToolError生成原call_id错误，其他工具仍按既有合同推进。没有插入第二次执行校验、业务函数或异常转换层。

重复名字/非法模式/return_direct工具：register抛明确配置异常，Agent未创建、目标IO与模型请求均为零，不将装配故障当工具消息。一个已登记工具内部抛ToolException：还是原消息反馈，不把业务失败当登记失败。GraphInterrupt/停止/必要保存故障保持原边界。

## 5. 自定义代码与消费者迁移

例子（拟议使用，不代表本轮执行）：

```python
class ReportInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    text: str

@tool(args_schema=ReportInput)
def summarize_report(text: str) -> str:
    """Summarize already supplied report text without reading files."""
    return existing_function(text)

spec = ToolSpec(tool=summarize_report, input_schema=ReportInput,
                execution_mode='parallel', side_effects='read_only')
app = build_local_application(model_path, tools=[spec],
                              system_prompt='', response_format=None)
```

源码放应用可正常import的可信Python模块，启动列表传Spec；改Python源码仍需重启，网页不加载代码。现有自定义审批工具仍用当前实际ApprovalPolicy附到原BaseTool，新权限片会一次迁移该入口。登记并不自动授予文件权限或提供沙箱。

这是用户已在总Spec确认的接入合同迁移：不同时接受裸BaseTool和ToolSpec，不自动包装。全搜生产、scripts、tests的工厂/build_*调用：自定义测试用自己已有公开输入模型构造Spec；直接工具调用改spec.tool.invoke/ainvoke，统一错误仍经Agent。不能只断言旧函数存在，不能删旧行为或保留固定workspace工厂来照顾测试。

必须迁移的测试组：read/ls/find/grep/write/edit/host_bash、application_*、async_*、persistent_*、tool_approval_registration、tool_batch_scheduling、active_stop、tool_error_feedback及tool_feedback_helpers。现有helper若调整，只为让同一行为经过新唯一入口，不改变异常/顺序/事件断言；在实施记录解释。脚本按rg枚举真实消费者迁移，不执行实网探针。

## 6. 验收与下一步

先新红灯再最小实现：

1. 登记保留原对象identity；重复/未知/无效定义失败且旧集合不变；顺序稳定。
2. 输入模型strict/extra/默认/validator同源；runtime不公开；模型/export schema不充当校验模型。
3. 内置集合与CLI/Web接线；host-bash缺省关闭；Read/Ls/Write/Edit配置继续生效；原权限/审批不丢。
4. 模式metadata无消费者，Spec模式真正驱动混合批次顺序，纯并行批次仍并行；同ID消息、原错误与控制身份保持。
5. 同步/异步/持久应用接线；登记失败先于模型/DB获取；App目录返回真实登记定义；content_and_artifact真实往返不混入模型内容。
6. 迁移固定workspace工厂的全部测试/脚本并删除旧实现；grep确认无自动包装/旧模式键/旧公开工厂消费者；全量pytest、compileall、pip check、前端测试/构建、diff/文件行数通过。

本片不运行真实模型、不改真实配置/依赖/用户库、不提交发布。后片在同一ToolSpec/Registry上接活动名称、ToolSet/动态提示词与局部HITL，随后迁移新权限描述/保存许可；不为“启动注册”建一套以后废弃的独立执行系统。

## 7. 实施与验证（2026-10-16）

已实现第2—5节。实际生产新增registry.py/builtin.py共126行；七个会话工厂返回ToolSpec并保留真实公开模型，CLI/Web复用内置集合，四种App装配先准备registry/现行审批声明再获取模型。同步/异步App的get_registered_tools返回原Spec引用。低层运行入口统一tool_registry，ToolBatch模式由Spec提供；上下文预算和框架执行仍接原BaseTool。

登记规则具体落地：名称允许ASCII字母、数字、下划线、点、短横线且非空；公开模型必须是执行模型本身或其Pydantic基类，普通字段集合与框架公开字段一致，不得包含runtime；字段类型/默认/约束、模型配置（除执行需要的arbitrary_types_allowed）和validator必须一致。框架导出模型只用于判断字段可见性，不作参数校验器。没有调用业务函数或构造假参数来验证定义。

删除：四个固定workspace工厂及导出；write/edit/bash模式metadata写入；batch._MODE_KEY及旧模式解析；运行装配handle_tool_error重复检查；approval_policies_for_tools重复名称扫描。生产、tests、scripts无上述旧入口或旧模式键消费者，也无低层tools=旧参数。resolve_workspace_root仍供SessionService/恢复调用，旧审批metadata、Read策略、一次性grant、Bash Unknown保护及WAIT配置仍有真实消费者，保持到对应片迁移。

生产额外消费者test_agent/discount_case_design.py已迁空ToolRegistry；六脚本probe_configured_tool_loop、probe_tool_batch、probe_message_events、probe_message_state_boundaries、probe_async_application、probe_async_model已迁移，不执行实网。只使用框架create_agent的探索脚本保持BaseTool合同，没有把它们误迁为项目API。

测试迁移保留原事件/顺序/消息/异常/目标不变断言。共享tool_feedback_helpers现在接Spec并给真实运行图内存checkpointer/明确工作区；执行边界的无grant案例在HITL恢复后仍无grant，继续验证工具正文拒绝，正常批准由原App测试覆盖。带注入参数的自定义审批测试显式声明公开模型并用schemas派生执行模型。原async资源清理测试不能再用“非法模式”模拟模型获取后的故障，因此改在_assemble_application注入同一个ValueError，增强断言原异常identity、close已调用及安全清理诊断；非法定义零资源获取另有四入口测试。不修改conftest，不删除/skip/放宽旧用例，不在生产增加裸工具自动兼容。

TDD：新增test_tool_registry.py先24 failed（新接口缺失、旧启动先获取资源），最小实现后24 passed in1.04s。首次迁移定向202 passed/6个旧调用点未适配；首轮全量1170 passed/16个遗漏（测试装配构造、原工具属性和token预算BaseTool入口），逐项迁移后全量如下；这些不靠兼容壳或减少断言处理。

```text
.venv/bin/python -m pytest -q --tb=short --show-capture=no
1186 passed in 38.94s

.venv/bin/python -m compileall -q naiyue_test_agent tests scripts
exit 0
.venv/bin/python -m pip check
No broken requirements found.

# cwd=frontend
npm test -- --run
Test Files 9 passed (9); Tests 27 passed (27)
npm run build
tsc --noEmit + vite build: exit 0; 186 modules transformed

git diff --check
exit 0
```

修改生产文件均≤500行；输入schema原引用、运行事件、整批审批/串并行、停止/清理、sync/async及SQLite artifact往返均有当前测试。这里“先于DB”指异步持久入口自身获取DB之前；同步持久入口的storage由调用者已持有，本片不宣称阻止调用者先打开数据库。无真实模型/Windows/真实用户配置库验证，无依赖升级及提交/push；助手实现不记为用户独立掌握。下一步只审阅[085-05动态工具/提示词/名称审批](085-05-dynamic-tools.md)。
