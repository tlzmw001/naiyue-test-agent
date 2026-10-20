# MCP-01：JSON Schema工具参数合同

日期：2026-10-20。状态：已实现并完成本机验证。用户已批准方案并要求继续实现。本片只扩展正式工具合同，不连接MCP服务、不引入mcp SDK、不修改真实配置/数据库、不提交发布。当前路线见[Spec117](117-mcp-client-integration.md)，客户端选型见[00结果](../research/mcp-client-selection-2026-10-20.md)。

## 1. 当前链与修改原因

应用工厂prepare_tool_registry → ToolRegistry.register → 共享create_agent图 → PermissionMiddleware.prepare_local_batch → prepare_permission_call → 一次审批许可 → ExecutionAuthorizationMiddleware → ToolNode/工具执行 → ToolMessage/观察/必要保存。

实施前ToolSpec只接受Pydantic，validate_input返回BaseModel，权限准备调用model_dump。规则校验直接访问model_fields。JSON字典schema在LangChain工具执行时不再验证；原授权invalid分支继续交框架依靠Pydantic拒绝，因此不能直接放行JSON工具。

## 2. 本片合同

- ToolSpec.input_schema增量接受dict。现有构造参数/方法签名及Pydantic模型身份、默认值、严格类型、validators和隐藏字段处理保持。
- 新tools/json_schema.py只供注册器和权限准备使用：验证schema、缓存独立副本/validator、校验参数、提供安全的JsonToolInputError；不是新的运行时/工具平台。
- 根schema必须为显式object；没有$schema时固定Draft2020-12，声明其他方言拒绝。支持标准嵌套/数组/联合/enum/required/additionalProperties和文档内部引用。format仅注解，default不填充。
- 使用jsonschema公共API与referencing公共Registry/Resource/resolver；只允许片段引用，禁止外部加载，内部引用必须可解析。不把default/enum/example中的普通数据当schema遍历；无法评估或无进展循环引用显式拒绝/失败，不执行工具。
- schema与参数必须是有限JSON（字符串键、有限数、无循环Python容器）；参数根为对象，不转换字符串数值、不删字段、不注入默认。已校验参数以RootModel包装，保留model_dump(mode='json')合同。
- JSON执行schema必须与公开schema一致。注册器为JSON合同和工具导出保留独立副本；注册后如果工具/公开schema被改动，模型视图、参数准备和执行分派均拒绝继续按旧合同授权（包括待批恢复）；同一已修改Spec不能在另一注册器重注册并替换已有合同。不改变已注册Pydantic工具的身份语义。
- 首版JSON工具使用显式runtime:ToolRuntime（含参数化形式）及**业务参数的异步StructuredTool执行入口。runtime/config/run_manager属于顶层保留参数（后两者被框架执行入口占用）；顶层声明冲突或模型传入时拒绝，嵌套同名业务字段可用。Runtime不进入公开schema，工具执行体继续require_tool_permit。
- JSON工具只接受ToolPermission及其子类，不接受本地文件路径权限。显式JSON工具args/path规则拒绝；全局带args规则命中JSON工具时拒绝。既有通配文件path规则仍只匹配文件工具，不改变原语义；工具级通配Allow/Deny按现有匹配执行。
- JSON参数无效时prepare_permission_call保存安全validation_feedback（仅错误类别/必要字段路径，不含输入值）；原validation='invalid'保持。授权层仅对该JSON反馈返回配对error ToolMessage，naiyue_not_executed=invalid_arguments；原Pydantic分支继续由框架反馈。非法参数不申请审批、不消费许可；与其他待批调用同批时保持整批审批顺序。
- 不新增事件或DB字段。validation_feedback是现有tool_execution.calls内可序列化事实，随既有checkpoint保存/恢复；观察消费已有not_executed字段，测试确认事件结果。既有审批恢复只接受整批输入都有效的保存批次，含invalid的混合批次继续明确拒绝恢复（不改变原Pydantic/恢复语义）；测试分别验证反馈checkpoint往返、混合批拒绝、合法批恢复后仅执行一次。
- 模型侧在既有ToolSetMiddleware._inputs中将JSON工具投影为标准function描述字典（name/description/parameters），参数schema完整复制。执行侧仍从同一ToolSpec选择原StructuredTool，不新增运行循环。当前LangChain对BaseTool的转换会改写引用、无法处理具名锚点/字面数据中的$ref，因此不让框架重新解释已确定的JSON合同；使用公开ModelRequest.tools字典入口，验证两家公开格式转换及既有观察schema_status。
- 文本、content_and_artifact结果和ToolException继续原处理；网络/MCP协议结果转换留后片，不在01新建MCP结果后端。

## 3. 正常与失败链

正常：JSONTool Spec → 注册时schema/隐藏Runtime/规则检查 → 固定模型合法参数 → 本地validator → RootModel/model_dump → subject.args → Allow或原审批 → 精确许可 → ToolNode注入Runtime → 工具核对许可 → 调用一次 → 原ID ToolMessage与事件。

失败：非法类型/非有限JSON/保留参数 → 安全JsonToolInputError → prepare保存invalid＋反馈 → 不进入审批列表 → 现有批次准入后授权层返回错误/未执行 → 工具计数0。Schema非法/外部引用/规则不支持在注册或工厂准备时失败，不获取模型或数据库资源，不留下半注册项。意外程序异常/许可机制错误继续原传播，不伪装成参数错误。

## 4. 修改面与TDD

生产：tools/registry.py、tools/json_schema.py、tools/permission_rules.py、agent_runtime/tool_execution/permissions.py、authorization.py，以及tool_execution/toolset.py模型侧JSON工具描述投影。正式链测试暴露原BaseTool的schema统计降级及供应商转换改写；最终复用工具选择层的模型字典入口和观察层既有dict分支，不新增观察机制，也不保留临时BaseTool/JSON统计专用分支。依赖：requirements.txt、pyproject.toml引入jsonschema 4.26.0及解析确认的新增传递依赖，不升级既有包。测试：tests/test_json_schema_tools.py（注册/Schema/JSON）、tests/test_json_schema_permissions.py（正式应用/审批/许可/事件/恢复），扩展现有测试仅在必要时。文档：本Spec、117状态、tool-authoring、handoff与future-work，保留并行学习改动。约12—15文件，无新服务。

先确认正常JSON工具当前注册失败、JSON invalid授权当前未阻止执行的红灯，再写最小实现并扩展到完整矩阵。使用既有native_application_helpers/假模型，不修改conftest或建立平行Agent循环。

验证：新增测试＋既有注册/权限/错误测试；全部tests；pip check、compileall/AST/500行、依赖清单一致性、git diff --check；既有scripts.build_release本机macos-arm64 wheel构建及scripts.verify_wheel_install干净安装。前端无变更，但正式构建仍使用现有前端构建流程。0次真实模型或MCP业务请求。

## 5. 完成记录

实际接入6个生产文件（注册器、新JSON合同、权限规则、权限准备、授权、工具集合），两份依赖、两个测试及本片文档。无占位模块；缓存由注册器创建，模型/参数/执行分派均消费，两个正式异步应用工厂通过既有注册入口使用。没有新增执行循环、ContextVar、MCP资源或存储schema。

| 本轮命令 | 实际结果 |
|---|---|
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests/test_json_schema_tools.py tests/test_json_schema_permissions.py tests/test_tool_registry.py tests/test_permission_rules.py tests/test_permission_boundaries.py tests/test_tool_error_feedback.py tests/test_model_request_context.py tests/test_runtime_middleware_contracts.py -q` | 152 passed in2.52s，其中本片新增60项 |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 最终3064 passed in128.58s；首轮3062后补冻结合同两项红灯并修复，最终全量重新运行 |
| `cd frontend`后`npm test -- --run` | 44文件347 passed，4.15s |
| `cd frontend`后`npm run build` | TypeScript/Vite成功；保留原chunk>500kB提示 |
| `.venv/bin/python -m compileall -q naiyue_test_agent`、AST/500行、`pip check` | 145生产Python文件通过；No broken requirements found. |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp01-20261020/wheel-final` | 成功，275文件；原生资源/归档检查通过 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp01-20261020/wheel-final --output build/mcp01-20261020/install-final.json` | 源码目录外新venv安装成功，原生工具/frontend API/Find-Grep/审批恢复/SQLite重开/CLI全passed，provider_requests=0 |

最终wheel逐份Python源码字节与当前145文件一致，新增五项依赖全部进入METADATA；安装SHA与构建SHA相同。版本仍0.1.1，是未发布的本地验证产物，不冒充PyPI新版本。项目环境未安装mcp SDK。依赖解析增加jsonschema4.26.0、attrs26.1.0、jsonschema-specifications2025.9.1、referencing0.37.0、rpds-py2026.9.1；既有版本未替换。两清单原有6项快照差异与HEAD相同，本片只同步新依赖，不擅自扩改旧快照。为运行既有构建命令补装了已声明的dev工具build1.3.0及pyproject_hooks1.3.3，没有改构建脚本/生产构建路线。

原始证据：[初始两项红灯](../../build/mcp01-20261020/red.log)、[真实模型绑定红灯](../../build/mcp01-20261020/model-binding-red.log)、[冻结合同红灯](../../build/mcp01-20261020/frozen-contract-red.log)、[定向最终](../../build/mcp01-20261020/focused-final3.log)、[后端最终](../../build/mcp01-20261020/backend-final.log)、[前端测试](../../build/mcp01-20261020/frontend-tests.log)、[前端构建](../../build/mcp01-20261020/frontend-build.log)、[最终wheel构建](../../build/mcp01-20261020/wheel-final.log)、[最终干净安装](../../build/mcp01-20261020/install-final.json)、[新增测试计数](../../build/mcp01-20261020/new-tests-final-collected.log)。build目录是本机证据，不属于生产包。

实现中还先复现了无效JSON BaseTool导致模型schema统计降级；最终使用现有模型描述字典入口及观察层既有dict分支，移除临时观察专用分支。额外验证OpenAI/Anthropic公开工具格式转换保持原schema；不把转换成功升级为供应商接受任意JSON Schema的实网证据。早期SQLite新测试误用ready恢复和continue动作，按已有源码改为restore_approval并分别验证合法批恢复、含invalid混合批拒绝及反馈字段完整往返；未改旧测试或恢复算法。首轮Find测试漏原生PATH失败，补环境后6项通过，最终全量始终使用已知PATH。

限制：没有真实MCP server或模型供应商请求；没有Windows/Linux原机或五平台新依赖安装证据；不支持本片范围外的MCP args/path规则，format仅注解；未覆盖恶意schema的CPU/内存硬限或任意供应商参数合同。无真实配置/用户数据库、学习记录修改，无commit/push/发布。唯一下一片为02配置/受控传输/只读发现；先讲02方案，等待用户确认。
