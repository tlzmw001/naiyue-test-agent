# Spec117：MCP客户端接入与分片规划

日期：2026-10-19，当前更新：2026-10-20。状态：**MCP-00研究定案、01参数合同、02只读发现、03会话资源、04根Agent闭环、05子实例/恢复、06呈现、09运行中配置与07显式空闲回收已实现；08本机门禁已接入，用户已确认取消Intel并改为四平台，Linux/Windows原机证据未关闭**。用户已授权完成00/01/02/03/04/05/06/09/07，未来同时连接本地与远程MCP服务，当前没有指定业务server。01已引入jsonschema及必要传递依赖，02已引入mcp SDK与剩余依赖；真实配置不改；[00结果](../research/mcp-client-selection-2026-10-20.md)为当前选择依据。

分片修订：2026-10-20。用户认可会话复用、保留内置Pydantic、两客户端实验比较与分层回收建议，要求整理实施切片。第8节为当前推进顺序，替代初稿MCP-01—05及旧MCP-06编号；分片讨论之后用户已授权实施00/01/02/03/04/05/06/09/07，后续片仍按范围独立推进。生产依赖已由00实验定为官方SDK，不把选择藏到实施中。

**当前确认与历史实验**：按会话维持连接；应用管理、聊天/实例拥有、调用借用，跨消息复用，普通run结束不关闭。空闲策略采用默认保留、明确可重建服务才显式启用TTL的方向；TTL机制在MCP-07独立验收，不借回收暗改连续性承诺。SDK与LangChain已按同合同实验比较，选定直接SDK；接受必要的自有代码/设计。[第一轮对比报告](../research/mcp-client-comparison-2026-10-19.md)记录2026-10-19两库25项协议/生命周期断言、10项现有应用主链探针通过；00本轮另复跑25＋10，新增54项研究用例通过，选定mcp 2.3.0＋jsonschema 4.26.0。Pi参考为固定73f8de80；[01参数合同](117-01-json-schema-tool-contract.md)已接正式主链，新增60项/后端3064及本机wheel干净安装通过；[02配置与只读发现](117-02-mcp-discovery.md)、[03会话资源](117-03-mcp-session-resources.md)已接入，[04根Agent闭环](117-04-mcp-agent-tools.md)与[05子实例/恢复](117-05-mcp-children-and-recovery.md)已验收，[06呈现](117-06-mcp-presentation.md)及[09运行中配置](117-09-mcp-runtime-configuration.md)已接入，[07显式TTL](117-07-mcp-idle-reclamation.md)已完成本机实现/验收，[08](117-08-mcp-delivery-verification.md)已接标准安装MCP门禁；当前四平台发行范围已确认，全部平台交付仍需Linux双架构/Windows原机CI证据。

## 1. 目标与范围

让现有通用Agent发现并调用用户配置的外部MCP工具，继续使用本项目的ToolSpec、活动工具集合、权限/审批、批次、事件、停止和保存链。

首个可交付范围：stdio与Streamable HTTP、多个显式配置的server、tools/list与tools/call、文本/结构化JSON结果、静态凭证环境变量引用、CLI/Web/一层子Agent复用。

连接寿命与依赖选择独立决定。已确认同一逻辑会话内复用其server客户端，避免stdio每次调用重启；不同会话/子实例默认隔离。逻辑会话、单次run、SDK客户端、HTTP物理连接池和协议会话不是同一概念。应用配置、工具清单与本地授权分别由应用持有，客户端库负责协议与传输。空闲策略为默认保留、明确可重建服务显式TTL；07独立实施，不假设未来服务均无跨调用状态。

本片不建设本项目对外MCP server、服务市场/自动安装、通用插件框架、OAuth登录与凭证库、resources/prompts入口、sampling/roots/elicitation交互、长任务扩展、跨会话共享池、任意Python工具热加载或通用重试。浏览器自动化保持整个合并路线最后。此前取消的真实业务验收与接口测试闭环跟进不恢复；MCP组件自己的确定性验收仍须完成。

## 2. 当前事实与阻点

1. 当前安装langchain=1.4.2、langchain-core=1.6.6，02已安装mcp 2.3.0，未引入FastMCP。现有工具注册全部走tools/registry.py的ToolSpec。
2. MCP-01已扩展ToolSpec的JSON Schema分支，保留原Pydantic；prepare_permission_call仍用validate_input(...).model_dump(mode='json')。JSON校验器返回RootModel，错误配对未执行，规则仅工具级；模型侧在现有工具视图用完整function字典，执行侧仍用原工具。协议发现已由02的nyt mcp list接入；04/05已接根/子Agent与恢复，06补呈现，09补运行中配置。
3. 当前异步工厂build_async_local_application与open_async_persistent_application在装配运行图前建立注册表。CLI同步入口通过现有桥接使用正式异步应用，不另建同步MCP执行链。
4. 动态工具选择、根/子权限与审批、一次性执行许可、工具反馈和持久诊断已有消费者；这些继续作为执行权威。
5. MCP工具携带的readOnlyHint等是server声明，不能自动升级为本地授权或安全重放证明。

## 3. 选型与实际调研

**MCP-00决策：采用官方Python SDK mcp==2.3.0＋jsonschema==4.26.0，沿用LangChain 1.4.2/core 1.6.6。** 两库同合同实验均通过，选择SDK是为直接接入项目已有Runtime/权限/结果边界，接受自有薄转换与异步owner，不是LC能力被证伪。详见[00报告](../research/mcp-client-selection-2026-10-20.md)。 用户接受为更顺畅的SDK接入补充必要代码与设计。沿用当前LangChain/create_agent，不升级框架，不手写JSON-RPC、版本协商或SSE协议。jsonschema用于本项目执行前入参合同，两种路线都需要解决该合同，不能算成SDK路线独有优势。选择顺序是合同正确/所有权清晰、公开接口与维护性，再考虑代码量与依赖数量。

已核验的官方方案：LangChain 1.4.2已有langchain.mcp.MCPAdapter，依赖FastMCP，旧langchain-mcp-adapters仓库已归档。当前入口仍在维护并标为beta，旧仓库归档不等于LangChain放弃MCP。返回工具默认使用handle_tool_error，并默认接入执行中elicitation中断；现有ToolSpec禁止工具私设错误处理器，当前审批只支持自己的前置审批合同，不能直接复制quickstart。源码同时明确接受带自定义elicitation handler的预建客户端，并支持外层上下文保持连接；这些默认值属于需要适配的差异，不是已证明的硬阻点。

本机pip dry-run对比：mcp==2.3.0新增13个包；fastmcp-slim[client]==4.1.1新增35个；langchain[mcp]==1.4.2新增46个，未发现需替换当前已安装核心依赖。此数量只描述当前环境的解析结果，不是各平台最终依赖数。SDK方案仍新增依赖，实施时同步requirements、pyproject与当前四平台发行检查。

SDK路线真正的收益是直接控制协议调用入口、交互协商、结果/错误转换和资源归属，避免先采用通用适配器的策略再调整；代价是自己维护更多schema/内容/错误/元数据转换及升级回归。LangChain路线复用现成工具转换和FastMCP生命周期，代价是更大的依赖面与默认行为适配。包数只作发行成本参考，不能替代适配工作量和正确性证据。

第一轮有状态stdio/HTTP与当前应用固定schema探针两库均通过。本轮[00实验](117-00-mcp-client-selection.md)新增通用JSON Schema研究分支、真实权限/ToolNode、专门拥有任务/跨任务取消/关闭、当前应用组合和HTTP解析前JSON预算，54 passed in23.59s；基线25项5.92s＋10 passed in5.19s复跑通过。研究注册器/ExitStack不是产品接线，完整并发创建/应用关闭/根子恢复及预算保护仍留后片；只维护一个生产后端。

### 历史探针证据（2026-10-19）

- 只在系统临时目录安装13项研究依赖；项目.venv、依赖清单及真实配置未改。
- 官方SDK连接https://docs.langchain.com/mcp，发现3个工具；只调用search_docs_by_lang_chain，成功返回10个文本块、30259字符，协议2026-07-28。未调用submit_feedback，0次模型供应商请求。
- 临时MCPServer子进程通过stdio发现echo并回传固定测试标记，协议2026-07-28；退出上下文完成SDK子进程清理。合成echo仅为协议夹具，不是业务能力。
- 当前LangGraph编译图验证JSON Schema StructuredTool与隐藏ToolRuntime注入可共存；当前ToolRegistry随后按预期拒绝该schema，原因为Tool input schema must be the execution model or its public base。
- 直接调用未编译ToolNode的第一版探针因缺图runtime配置失败；改为标准编译图后通过。没有由该探针修改生产逻辑。

后续同日第一轮比较实际25项断言通过（6.43秒），当前应用固定schema探针10 passed（5.14秒、1项LangChain beta警告），0次供应商请求。上述只证明已测试入口，不代表通用MCP产品接线、Windows、认证服务或任意server验收；不把研究总耗时当库性能排名。

### 参考项目当前源码

- Pi旧检出a69bef789的usage确实无内置MCP，但这不是当前最新版事实。已在临时目录核对官方main 73f8de80（包1.3.0）：已有内置MCP扩展，session_start创建每server的McpServerConnection，getClient复用已连client，session_shutdown关闭；new/resume/fork替换运行时先shutdown。其独立@earendil-works/pi-mcp不依赖官方SDK，当前声明协议2025-11-25及旧版兼容。借鉴所有权，不直接搬选型/协议代码；固定源码链见对比报告。
- AgentX 3fcfa0e：AgentMessageHandler.provideTools→AgentToolManager.createToolProvider→McpUrlProviderService解析server URL→HttpMcpTransport/DefaultMcpClient→McpToolProvider。可参考server身份与工具提供者分离；它是Java/LangChain4j fork的旧SSE链，不能当作Python或新协议事实，也不照搬其请求/响应日志设置。
- 进一步追踪AgentX：AbstractMessageHandler.chat每次聊天请求创建provider；对应fork d110efc4的McpToolProvider将同一个McpClient交给DefaultToolExecutor，executeTool通过已有transport发送请求，未每次重建client。由此只能确认本次provider/运行内复用；在已追调用链没有找到生产侧配对close，不能宣称它已实现完整的跨聊天会话复用/清理。
- Claude Code公开文档：使用MCP TypeScript SDK；管理会话中的已连接server，结束/断开时回收stdio进程，远端掉线可重连，stdio不自动重连。可据此判断其生命周期不是逐工具调用强制重建；公开文档没有证明不同逻辑会话或子Agent共享同一个客户端，不能推测其私有对象池。配置scope只是配置可见范围，不是连接共享范围。

## 4. 配置与可见接口

沿用用户级settings.toml，新增mcp配置对象，不自动读取项目仓库中的MCP启动配置。默认servers为空；未启用时零MCP网络/进程调用，SDK延迟导入。模型不能新增server、修改URL/命令或注入凭证。

| 配置 | 合同与默认值 |
| --- | --- |
| mcp.servers.<id>.enabled | 默认false；id仅字母/数字/下划线，最多16字符 |
| transport | 明确为stdio或streamable_http；不按字符串猜测本地脚本，不支持旧独立HTTP+SSE入口 |
| allowed_tools | 显式非空远端工具名列表；只有列出的工具可注册，清单中的工具缺失则报错 |
| url / headers_env | HTTP专用；url禁止内嵌凭证；headers_env映射请求头到环境变量名，值不进模型、日志、历史或错误正文 |
| allow_insecure_http | 默认false；loopback HTTP可用于本地server，非loopback明文HTTP需显式开启；02保守禁止明文HTTP配置任意headers_env，避免猜测自定义认证头 |
| command / args / cwd / env | stdio专用；argv直接执行，不经Shell；env值仅支持完整${ENV_NAME}环境变量引用，不自动继承模型Key或Web访问token |
| connect_timeout_seconds | 默认15，严格正数；覆盖创建/协商/发现阶段 |
| call_timeout_seconds | 默认0，沿用当前工具不限执行时长的约定；正数启用本次调用总时限，始终响应停止 |
| read_timeout_seconds | 默认0，表示不额外设置协议读取超时；可显式配置正数，与总调用时限及连接/发现时限分开 |
| max_tools / max_schema_bytes | 每server默认64项/单工具65536字节；发现分页累计检查，重复游标或超限失败 |
| max_response_bytes | 02新增独立HTTP读取预算，默认1048576；JSON解析前字节/SSE单事件限制，压缩拒绝；不等同进程内存硬上限 |
| max_result_bytes | 默认65536；限制模型可见结果；完整输出复用现有tool_output归档与额度 |
| max_concurrency | 默认1，按server在当前应用实例限并发；不让server的annotations自动扩大并发 |

URL、端口、命令均来自配置；研究探针地址不写成产品默认。网络代理沿用已有network决策，但SDK使用httpx2，不能把当前httpx.AsyncClient直接传入；新增客户端构造时保留TLS验证、受控代理和同源重定向边界，配置说明同步扩大到MCP。

stdio的stderr进入有界、本地诊断输出，不直接转发SDK默认stderr或记录完整环境；stdout仅供协议解析。工具描述/结果都是外部数据，不作为本地权限规则、系统指令或配置写入来源。

工具名固定为mcp__<server_id>__<remote_tool>，总长不超过64；非法字符/超长名称明确拒绝，不静默改名制造冲突。server_id唯一；初版原始工具名限定40字符以内且符合现有工具名字符范围。原名、server、输入/输出schema与目录指纹随适配工具保留。

新增只读命令nyt mcp list：按配置发现清单并显示server、原名、注册名及安全错误；不执行工具、不发起OAuth、不打印凭证。未配allowed_tools时该命令可列候选，应用装配仍要求显式选择。

注册不等于启用：新会话默认仍只启用现有默认内建集合，MCP工具从已有工具菜单或CLI会话工具选择开启。子Agent只能使用角色显式列出的注册名；不能继承根会话新增MCP工具。

## 5. 现有注册合同的最小扩展

本节不是要求把内置工具改写为JSON Schema。内置工具仍以原Pydantic模型和Python validators为执行权威，模型侧JSON描述由其导出；MCP工具以远端inputSchema字典为参数结构合同，使用JSON Schema验证器。两种输入合同汇入同一个注册/选择/权限/审批/执行链，不将任意JSON Schema翻译成动态Pydantic，也不把内置模型转成JSON再反向生成，避免丢失严格类型、默认值与自定义校验。

实际要适配的点：registry.py的schema类型/注册检查/validate_input；permission_rules.py直接访问model_fields的参数规则；permissions.py的model_dump及ValidationError边界；authorization.py非法参数分支；MCP StructuredTool的隐藏Runtime注入与单次许可。注册阶段校验schema本身并创建validator；调用阶段校验参数。JSON Schema错误须进入当前“参数无效、未执行”合同，而非作为普通网络错误继续执行；错误信息保留必要字段路径，不暴露完整参数。支持范围与本地引用合同必须明确，不能把“通用”解释为接受任何方言/远程引用。

- ToolSpec.input_schema增量支持JSON Schema字典，已有Pydantic字段/validators/injected字段规则完整保留。validate_input继续返回可model_dump(mode='json')的BaseModel：JSON Schema校验后返回RootModel封装的JSON对象，调用方合同不变。
- 用标准jsonschema验证Draft2020-12及本地$defs/$ref，不自行把JSON Schema翻译成Pydantic。外部$ref禁止网络解析；不支持的声明方言在发现阶段拒绝。输入必须是有限JSON对象，不填充server默认值，不默默删除额外参数。
- JSON Schema StructuredTool的执行函数注入内部ToolRuntime参数；保留名与远端顶层参数冲突时发现即拒绝，模型伪造该参数也拒绝。不新增ContextVar或第二套运行上下文。
- 初版MCP权限配置支持工具级Allow/Deny和现有精确一次审批；针对MCP的args/path规则在启动时明确拒绝。内建工具现有按参/按路径规则不变，不能宽松接受却不执行。
- 当前授权层对validation='invalid'会把请求继续交给工具，依赖Pydantic再次拒绝；JSON Schema StructuredTool没有这层本地验证。必须在ExecutionAuthorizationMiddleware为JSON Schema无效输入直接返回配对的错误ToolMessage，绝不能继续进入MCP执行体；Pydantic旧分支保持。执行体仍先核对本次许可，不能绕过参数/授权链直接调用。
- 所有适配工具handle_tool_error=False，错误仍由现有ToolErrorMiddleware统一转换。execution_mode默认sequential、side_effects默认unknown；不依据远端hint自动Allow或自动重试。

## 6. 正常、审批与失败主链

```text
settings.mcp（可信配置）
  → SDK创建连接并发现tools（外部事实）
  → 名称/allowlist/schema预算校验 → 冻结目录与指纹
  → MCP ToolSpec → 现有注册表/活动工具集合
  → create_agent模型提出具体工具调用
  → JSON Schema参数校验 → 权限模式/Deny/Allow/前置审批
  → 一次执行许可 → 等待server槽位 → 获取本执行主体拥有的连接
  → tools/list复核目录身份 → 选定客户端库的受控单次调用入口
  → 文本/JSON结果、ToolMessage、原事件/历史/诊断
  → 释放本次调用占用；连接按已确认的所属生命周期关闭
```

审批subject包含server_id、remote_tool、配置身份与目录指纹、精确参数；不含凭证。04已按用户修订实现工具级会话记忆：完整工具名与身份匹配时其他合法参数也允许，Deny优先；一次许可仍精确绑定当前参数/调用/实例，不将记忆工具等同于整server授权。执行前重新校验许可及当前目录；同名工具的schema/描述/输出合同变化时拒绝本次执行，不沿用旧审批，也不自动换工具。这里的指纹只能检测目录变化，不能证明server实现未被替换。

执行端仍在本机发起调用，远端副作用发生在哪里必须在审批/工具来源中明确显示；不把原execution_environment='host'解释成远端工具也运行在宿主工作区。

| 分支 | 必须行为 |
| --- | --- |
| 参数非法/显式Deny/拒绝审批 | 不发送tools/call；按现有ToolMessage错误合同反馈 |
| 发现或连接失败 | 启用的server必须完整发现才完成应用装配；报出安全server id/阶段，不能静默加载半套工具；用户可禁用该server后启动内建工具 |
| is_error=true | 保留“工具执行返回错误”的事实；有界正文进入ToolException/现有反馈，普通日志不记录正文，不当成传输故障重试 |
| 认证/协议/超时/断连 | 安全类型/阶段日志与明确错误反馈；已发送调用可能执行，不能称未执行或自动重发 |
| InputRequiredResult/未支持交互 | 首版明确拒绝，不自动重复原调用；直接SDK候选用session.call_tool显式识别，LangChain候选须验证公开自定义handler与现代协议分支的行为 |
| legacy server反向请求 | 不声明sampling/roots/elicitation能力；不回传模型凭证或工作区根；未支持请求明确拒绝 |
| 停止/外部取消 | 沿现有run control取消本次等待；共享期内禁止关闭其他调用正在使用的资源，必要时将本主体连接标为失效并显式重连；不宣称远端已停止副作用或计费 |
| 重启审批 | 使用既有恢复链重新读取配置/目录并校验原subject；server缺失或指纹变化不得执行旧待审调用 |
| 必要保存失败 | 保持当前失败/阻断合同；MCP成功结果不能绕过必要保存，普通诊断失败不能覆盖原异常 |

结果首版支持文本与structured_content。结构化结果只有artifact而无可见正文时，生成有界JSON文本供模型消费并保留原结构；不伪造成功。音频/图片/资源链接/嵌入二进制明确报告当前适配限制，不隐式下载URI。长文本复用已有归档，前端仍能明确看到截断与完整输出位置。

## 7. 连接、状态与资源边界

原per-call方案只作为比较候选保留，不再作为首版默认。真正逐调用创建/关闭stdio进程会丢失进程内状态；HTTP也可能重复协商/认证。注意FastMCP的StdioTransport默认keep_alive=True，第一轮实验证明adapter上下文退出后进程可继续存活；显式close才回收。因此不能把每次async with等同于每次重启，必须检查transport/进程的真实寿命。

建议范围：同一逻辑会话与server在进程存活期间复用客户端；不同根会话/子实例不自动共享可变客户端、协议会话、认证/roots及回调状态。共享工具定义不等于共享客户端。HTTP客户端存活不代表固定占用一根TCP连接；2026-07-28版Streamable HTTP已移除协议会话，旧版仍可能有Mcp-Session-Id，按协商结果处理，不自行伪造远端状态。

用户已确认同会话跨用户消息保持，不限于一次Agent run。连接属于当前活跃的聊天运行时资源，不属于持久聊天记录或ActiveRun字段；不承诺应用重启后恢复外部进程内状态。重连不等于恢复业务状态，不能自动重放之前调用。

决策依据：选择满足所需状态连续性、且不跨越身份/可变状态隔离边界的最小所有者。若产品语义是“同一聊天下一条消息继续外部现场，另一聊天独立”，则run寿命过短、应用全共享过宽，聊天运行时是合适默认；这是有前提的产品建议，不是MCP规范强制。第一轮有状态夹具和真实应用主链已分别验证这两个反例。远程HTTP全局状态实验还证明独立客户端不能自动隔离服务端业务状态。

落位建议：应用级统一管理异步创建/关闭，按(session_id, agent实例, server配置身份)拥有客户端，调用只借用；不放入消息/checkpoint。普通run结束、审批暂停和Web可见聊天切换不销毁；运行时删除/释放、子实例真正结束或应用退出时关闭。Pi切换运行时会关闭旧会话，本项目多聊天Web不能把切换可见页当运行时销毁。初版不预建跨主体池；空闲释放若会破坏状态连续性，须显式定义，不能暗加TTL。

本项目应用注册表/编译图共享而会话并发，适配工具不能闭包捕获一个全局有状态client。执行时应使用现有ToolRuntime中的会话/执行主体身份定位所属客户端，不另建一套授权上下文。AnyIO资源应由进入它的拥有任务完成退出；当前应用aclose创建单独任务，这要求验证所有权/清理接线，不是必须采用per-call的理由。03已按该范围接专门owner任务、应用关闭/会话删除/子实例释放；idle正值仍留07，不预建通用池平台。

### 空闲回收方向与独立切片

先区分HTTP物理socket与MCP逻辑客户端/stdio进程。HTTP底层池的空闲socket过期可由库管理，保持上层会话归属与客户端对象；它不等同于发送协议会话终止或杀本地进程，也不主动终止在途SSE/订阅流。当前临时环境httpx2.Limits确认有keepalive_expiry参数，不照抄httpx1的全部默认连接数。

执行连接按需创建，默认不自动结束进程/协议会话；可安全重建的服务可按server显式启用idle_timeout_seconds正值，0表示不自动回收。正值同时意味着配置者接受该服务连接内状态可能重置，不能根据readOnlyHint推断可回收。MCP-02只定义默认0的配置合同，MCP-07才接受和执行正值；在MCP-07之前必须明确拒绝正值，不能加载后不执行。真实用户配置不在本轮修改。

07已按[实施Spec](117-07-mcp-idle-reclamation.md)接入：新增29项，后端3313/前端366、构建及本机wheel干净安装/TTL探针通过；默认0无回收定时器，新借用先登记使用权，已退休则等旧关闭后重建，启用中TTL变化沿09拒绝合同。

若启用回收，最小必要条件：无在途调用、无执行中的会话run、无绑定的待审批/交互；从最后一次占用释放开始计时；健康ping/普通Web订阅不算业务使用。回收判断和新调用借用必须原子协调，先标记closing再退出拥有者上下文；新调用等待旧关闭后重建，禁止借到已关闭资源或关闭新一代连接。失败可观测；重建不重放旧工具调用。用可控时钟、事件与调用次数验证，不用极短sleep。

参考：Pi官方内置扩展在已检查源码里按运行时shutdown关闭，无同类空闲TTL；社区nicobailon/pi-mcp-adapter固定0a3f72be有idle计时、inFlight/activeProtocolOperations保护及keep-alive豁免。它是第三方扩展，不是官方实现；只参考机制，不照搬其10分钟/30秒常数、UI心跳或全套管理器。详见对比报告补充。

HTTP读超时、总调用时限和SSE单事件上限分别设置。SDK当前默认SSE单事件上限1MiB；JSON响应增加httpx2读取预算保护，不只在解析完毕后截断。stdio SDK当前没有公开单行解析前字节限额，因此首版只运行用户显式信任的本地程序，不承诺抵御恶意stdio进程的内存消耗；沙箱与硬资源限额属于后续独立能力。

SDK协议协商/非执行方法的兼容处理与业务tools/call重放分开。不得因“兼容旧协议”而重复可能已经执行的工具请求；故障注入检查实际tools/call次数。

## 8. 切片与独立验收

本次将初稿的大切片重排为MCP-00—08，并在06后插入09运行中配置。00是选型实验，01—06形成基础产品，07是可独立验收的可选TTL能力，08是发行前交付检查。基础产品始终允许默认0、不回收；若版本不包含07，08必须确认正值配置被明确拒绝。实际发布另按用户授权执行，不因本计划自动commit/push/tag/PyPI。

固定依赖顺序：00 → 01 → 02 → 03 → 04 → 05 → 06 → 09。07依赖03—05并协调09启停；08以09及本次实际纳入的07为输入。默认按表顺序推进，一次一片，不并行修改同一注册/资源/审批合同。

```text
00选型定案 → 01参数合同 → 02只读发现 → 03会话资源拥有
                                           ↓
                                  04根Agent闭环（首个可用节点）
                                           ↓
                           05子实例/恢复 → 06现有CLI/Web呈现
                                           ↓
                            09运行中配置 → 07可选TTL → 08发行前完整验收
```

| 片号 | 可检查的交付 | 核心验收 |
| --- | --- | --- |
| MCP-00 客户端对比定案（已完成研究） | 在同一临时研究合同下补通用schema、正式异步loop内拥有任务/跨调用/关闭、错误与预算的对比；产出一个生产路线与固定依赖清单 | 两库同输入、同权限/取消/恢复边界；不使用私有字段达成合同；列出自有逻辑、清理路径和限制。若均可用，按接入顺畅、职责/维护性决策；不以行数/包数淘汰SDK。产品依赖/真实配置不改 |
| MCP-01 通用参数合同（已完成） | ToolSpec支持JSON Schema分支，保留原Pydantic；引入jsonschema及必要传递依赖；JSON Schema注册/入参验证、返回/错误适配、非法参数在授权前终止 | 真实项目入口注册合成JSON Schema工具；嵌套/数组/enum/联合/required/additionalProperties/$defs；保留省略、不自动填默认/转换；非法参数执行次数0；原内置字段/validators/Runtime隐藏与参数权限规则完整回归 |
| MCP-02 配置、传输与只读发现 | 引入00选定mcp及剩余传递依赖（jsonschema先在01落地）；HTTP/stdio显式配置、代理/凭证引用、工具目录/指纹/allowlist/命名及nyt mcp list | 本地真实stdio及loopback HTTP；现代/legacy按选定库支持面验证；分页/重复游标/非法schema/缺工具/失效凭证；list零tools/call、零模型请求，成功/失败均关闭临时资源；未启用MCP零进程/网络 |
| MCP-03 会话资源拥有 | 按(session_id, agent实例, server配置身份)建立执行连接；创建、借用、并发槽、取消、失效、最终关闭，接应用正式loop | A连续两次run保持进程状态，B独立；首次并发借用只创建1次；停止等待/调用中/连接中；已执行响应丢失不重发；关闭进行中到达的新调用；应用启动失败/退出、会话删除及子实例释放入口能回收；JSON/SSE预算在读取时生效 |
| MCP-04 根Agent完整调用 | 两个正式异步工厂发现并注册MCP ToolSpec；活动工具、权限subject、精确审批、许可复核、文本/JSON结果与原错误/保存链 | 固定模型经过真实arun/审批恢复：关闭/非法/Deny/拒绝为0次调用；批准恰好1次；改schema/配置旧许可失效；同根跨消息复用、双会话路由；is_error/超时/断连不混为成功，不重放。CLI/Web通过现有入口可进行根会话调用 |
| MCP-05 子实例与恢复边界 | 子角色显式工具名单、实例资源归属、根/子停止、待审批与SQLite恢复、必要保存/关闭合同 | 根A/根B/子实例不串连接与授权；子实例完成释放、审批暂停保留；临时SQLite重开，服务缺失/目录变化拒绝旧待审；进程重启不伪称外部状态恢复；必要保存失败不被MCP成功覆盖 |
| MCP-06 现有CLI/Web可见性 | 在既有工具菜单/来源字段/审批目标/结果卡呈现server与原工具、限制和诊断；同步序列化与接口消费者、配置说明 | 已配但未启用工具的选用、根子角色显示、待审批、停止、长结果/完整输出位置、无数据空态；凭证不进入API/日志/历史；无新配置管理页、自动安装或服务市场 |
| MCP-09 运行中配置（已完成） | 用户级mcp配置手动刷新，零配置启动后新增本地/远程服务、调整allowed_tools、enabled启停；沿用现有Web菜单与CLI chat | 按服务准备/原子发布、失败保持旧服务；名单更新复用连接；停用撤销未提交调用/旧审批、在途收尾，重新启用等待旧连接且重审；新增不自动扩大选择，详见[09 Spec](117-09-mcp-runtime-configuration.md) |
| MCP-07 显式空闲回收（已完成本机验收） | 默认idle_timeout_seconds=0；正值按server显式开启；最小业务占用/闲置计时/closing协调、按需重建 | 可控时钟验证边界前/后；run模型思考中、在途调用、审批暂停不回收；最后占用释放才计时；ping不延长业务idle；回收与新调用竞争、关闭失败、其他会话不受影响；重建不重放旧调用、不宣称恢复旧状态 |
| MCP-08 交付验收（本机门禁已接，跨平台待收尾） | 当前文档/正常与失败主链、全部正式入口、发行白名单/依赖约束、四平台wheel干净安装与回归证据 | 全量后端/前端/构建、pip check、打包与安装检查；MCP禁用路径零额外IO、现有工具/会话可用；实际纳入的TTL行为可验证；不把组件验收升级为任意业务server/真实模型效果或全协议覆盖 |

### 8.1 每片修改面与可运行验证

下列测试文件名是本次计划的目标，不表示文件已经存在；未建立前不报告命令已通过。每片先检查同职责既有测试，可在其上扩展；单文件接近500行再按真实职责拆分。

| 片号 | 实现区域与实际消费者 | 计划验证入口 |
| --- | --- | --- |
| 00 | docs/research/mcp00研究脚本与00报告、固定LangChain/MCP公开源码、本Spec第3节；仅研究消费者 | 新增54项通过；25项协议＋10项当前应用基线已复制复跑；原注册器22项回归通过，见00报告 |
| 01 | tools/registry.py、json_schema.py、permission_rules.py；agent_runtime/tool_execution/permissions.py、authorization.py、toolset.py；requirements/pyproject五项校验依赖 | 实际tests/test_json_schema_tools.py、test_json_schema_permissions.py新增60项；定向152、后端3064、前端347及构建/本机干净安装通过，见[01记录](117-01-json-schema-tool-contract.md#5-完成记录) |
| 02 | mcp_client/settings.py/connections.py/cli.py、settings.py及CLI分发；mcp_client/connections.py由list命令实际消费 | tests/test_mcp_settings.py、test_mcp_discovery.py；CLI集成以临时settings/stdout/stderr检查list和退出码，0次tools/call |
| 03 | mcp_client/connections.py；application异步资源创建/关闭、会话删除/子实例释放必要接线；客户端访问从调用身份定位，不能塞进ActiveRun或可序列化State | tests/test_mcp_lifecycle.py；真实进程、HTTP和事件同步；沿正式应用loop检查owner进入/退出、进程/流/任务无残留 |
| 04 | mcp_client/tools.py/results.py；async_application.py/async_persistent.py；现有权限/工具反馈及必要tool元数据 | tests/test_mcp_agent_integration.py、test_mcp_permissions.py；内存与临时SQLite工厂均覆盖，先读当前公开入口，不建平行Agent循环 |
| 05 | application/session_subagents.py、session_persistence.py及原子实例/保存消费者；沿已确认实例生命周期释放，不为MCP新增子实例续跑功能 | tests/test_mcp_subagent.py、test_mcp_recovery.py＋相关既有根子审批/停止/保存测试 |
| 06 | web/service.py、工具列表/审批接口投影与既有frontend工具/结果组件、CLI呈现、docs/examples/settings.example.toml及使用文档 | tests/test_mcp_presentation.py＋前端对应定向测试；最小浏览器检查现有菜单/审批/结果，不增加新设置页面 |
| 07 | mcp_client/settings.py/connections.py、当前会话占用/待审批查询、诊断；不另建心跳/通用LRU平台 | tests/test_mcp_idle.py；可注入单调时钟与借用/关闭事件，避免极短sleep；正值配置与实际回收全链验证 |
| 08 | pyproject.toml/requirements约束、现有scripts.build_release/release_artifacts/verify_wheel_install、README/MCP说明/Spec/handoff | 按第9节既有构建与安装流程；各平台结果独立记录，未运行平台标未验证；不执行发布 |

### 8.2 前后边界与停止点

- **00结束：路线可决策**。给出同合同证据、选定库及需要自有的最小逻辑；更新Spec依赖/接线后再改产品依赖。若证据显示两路线都有未解决合同问题，留在本片定位，不用产品实现掩盖。
- **02结束：只读可见**。用户能通过临时配置运行list查看工具和安全错误；还不能称Agent会调用MCP。
- **04结束：根会话最小闭环**。用户可在现有CLI/Web选择并调用MCP工具，权限/取消/状态延续已接线；子Agent/重启恢复/展示完整性按后片验收，不宣传为基础版全部完成。
- **06结束：基础客户端闭环**。根子、两入口、恢复边界、来源与结果可见；默认不启用空闲回收，进入08可以验证该范围的交付。
- **07结束：可选TTL闭环**。只对显式正值配置生效；未启用TTL的服务继续保持会话寿命。可在首次基础交付之后独立做，但未实施时不能假接受正值。
- **08结束：可交付证据齐备**。仅在各项实际运行后报告测试/构建/平台通过；发布目标、范围及授权独立处理。

每片完成先展示正常/失败调用链、实际命令与输出、限制及唯一下一步，用户对关键合同的理解与助手测试结果分开记录。片内按已确认目标连续执行；仅在路线/语义改变、真实配置/发布等既有边界处讨论，不逐文件重复确认。一片不等于固定一天，研究/跨层故障可分多次工作单元，不承诺日历工期。

### 8.3 有条件的后续能力

OAuth、resources/prompts、sampling/roots/elicitation交互、多模态、跨主体共享池、服务端目录通知自动刷新与任意Python工具热加载仍不纳入本批；有实际消费者后分别立项，不能把MCP-06误读为这些能力已排期。浏览器保持总路线最后，原第1/2类业务跟进继续不恢复。

正常路径显式断言工具事件、观察健康及清理；故障注入在测试末检查主异常/实际调用次数，避免诊断隔离吞掉测试检查器失败。不修改失败测试放宽断言。

## 9. 预计修改面与验证入口

整体会涉及超过8文件，预计生产/测试/文档合计约25—35文件；这是工作面估计，不是必须新建的文件数。按第8节拆片，优先扩展既有同职责入口，每份源文件不超过500行。没有新增独立常驻服务；stdio进程是用户配置的外部server，测试server只作为夹具。

| 文件/区域 | 计划责任 |
| --- | --- |
| 新naiyue_test_agent/mcp_client/settings.py | 配置、身份/命名、预算；由AppSettings聚合 |
| 新mcp_client/connections.py | 选定客户端库的创建/发现/主体所有权与关闭、代理、有限并发与停止；可选TTL随07接入，不是第二套协议栈 |
| 新mcp_client/tools.py、results.py、__init__.py | ToolSpec/许可复核/原名路由、内容与错误转换及最小导出 |
| tools/registry.py、permission_rules.py、agent_runtime/tool_execution/authorization.py | JSON Schema校验/规则限制及非法参数在执行前终止，原Pydantic合同保持 |
| settings.py | 加载mcp对象并安全报告配置失败；不修改用户真实settings.toml |
| application/async_application.py、async_persistent.py | 在现有建表/建图前发现并合并工具，两入口一致 |
| cli.py及新mcp_client/cli.py | nyt mcp list只读发现；保留nyt/nyt chat公开入口 |
| web/service.py、frontend现有工具/审批展示相关文件 | 显示server来源与有界结果；无新配置管理页面或自动下载 |
| pyproject.toml、requirements.txt及必要约束文件 | 固定新依赖，保留当前框架版本；实际修改需随方案获准 |
| tests/test_mcp_*.py、frontend相应定向测试 | 两传输夹具、授权、失败/停止、恢复、输出与生命周期回归 |
| docs/examples/settings.example.toml、MCP说明、handoff | 配置格式、支持范围、限制和实际验证；不写入密钥 |

计划验证命令：先逐片pytest tests/test_mcp_*.py；整合后按现有PATH临时加入打包fd/rg运行python -m pytest tests -q，npm --prefix frontend test -- --run，npm --prefix frontend run build，python -m pip check及git diff --check。最后用现有scripts.build_release、release_artifacts与verify_wheel_install验包；发布仍需独立授权。

回退：未启用mcp时保持原路径；移除新增server配置/停用MCP工具即可恢复内建集合，不迁移或删除用户数据库。已保存会话引用缺失工具时明确提示不可用，不静默更换同名工具。没有数据迁移或自动回滚外部副作用。

## 10. 外部依赖、风险与后续决定

生产依赖已定为mcp 2.3.0＋jsonschema 4.26.0，01先引入校验库、02再引入协议SDK（00不修改依赖）；没有必需云账户/新服务。stdio服务器所需的Python/Node/其他CLI由用户环境提供，Agent不自动安装；远程服务需要的token从指定环境变量读取，当前不要求提供账号或Key。

已撤回的脆弱前提是“工具不依赖隐藏的跨调用连接状态”。当前建议通过同主体复用降低这项限制；仍须明确状态作用域、身份/凭证、并发、取消、所有权/清理、断线重连与重启恢复边界。连接复用不保证远端业务状态持久，也不构成权限/租户隔离。其他风险：客户端库协议版本变化、外部目录漂移、stdio启动成本/内存边界、服务故障导致显式启动失败、多平台新依赖wheel可用性。用固定版本、目录复核、调用次数/关闭故障注入和发行矩阵验证，不以包装层掩盖问题。

用户已授权实施00/01/02/03/04/05：SDK/LC实验选定SDK，01已接正式参数/权限/模型描述合同；02已接用户配置、受控stdio/Streamable HTTP、分页目录快照/指纹及只读命令，见[02 Spec](117-02-mcp-discovery.md)和[使用说明](../mcp-client.md)。新增65项通过，当前后端3129/前端347及构建通过；本机wheel280文件、150份生产源码字节一致，标准源码外安装通过。[03会话资源](117-03-mcp-session-resources.md)已按用户确认接入：同任务owner与按主体借用、取消/失效/最终关闭，新增31项，当前后端3160/前端347及构建、本机wheel与源码外三轮状态探针通过。[04](117-04-mcp-agent-tools.md)已接两个正式工厂的发现/注册、默认活动集合、根Agent JSON Schema/审批/精确许可与单次SDK调用/有界结果归档。新增37项后端＋1项前端；最终后端3197、前端348及构建通过，本机wheel284文件/154份生产源码一致，标准安装与安装包根Agent三轮1/2/1通过。[05](117-05-mcp-children-and-recovery.md)已接按实例调用/审批/停止/释放及旧MCP待审取消后新调用；close取消竞态已复现并修复。新增34项后端＋1项前端，最终3231/349及构建通过，本机标准安装与子实例/冷待审安装专项通过。06已按授权接入CLI/Web安全来源、根子角色与审批范围、成功/错误/未执行/未知/不支持状态和真实归档事实；实时与历史同投影。新增22项后端＋9项前端，全量3253/358、TS/Vite及本机wheel标准安装通过，见[06 Spec](117-06-mcp-presentation.md)。07/08未实施；基础闭环已齐，建议下一片08基础版交付验收，07显式TTL可独立后续交付；先说明待确认，不发布。

## 来源

- [LangChain当前MCP入口](https://docs.langchain.com/oss/python/langchain/mcp)、[连接生命周期](https://docs.langchain.com/oss/python/langchain/mcp/connections)、[工具与交互行为](https://docs.langchain.com/oss/python/langchain/mcp/tools)。本机1.4.2源码核对adapter.py/tools.py/elicitation.py。
- [旧适配器仓库归档状态](https://github.com/langchain-ai/langchain-mcp-adapters)。
- [Pi最新版MCP扩展与固定源码链](../research/mcp-client-comparison-2026-10-19.md#5-pi最新版实际所有权链)。
- [Claude Code MCP文档](https://code.claude.com/docs/en/mcp)：客户端运行时、stdio退出、远端重连与动态更新；不是其私有源码或跨会话共享证明。
- [AgentX对应fork的McpToolProvider](https://github.com/lucky-aeon/langchain4j/blob/d110efc4fd9d9e0dd63b9907181873778a733181/langchain4j-mcp/src/main/java/dev/langchain4j/mcp/McpToolProvider.java)、[DefaultMcpClient](https://github.com/lucky-aeon/langchain4j/blob/d110efc4fd9d9e0dd63b9907181873778a733181/langchain4j-mcp/src/main/java/dev/langchain4j/mcp/client/DefaultMcpClient.java)。
- [官方Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[SDK文档](https://py.sdk.modelcontextprotocol.io/)。本轮检查PyPI mcp-2.3.0 wheel的client.py/session.py/stdio.py/streamable_http.py。
- [协议版本](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning)、[传输](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)、[Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[授权](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)。
- 参考源码：本机Pi a69bef789、AgentX 3fcfa0e；链路与采用边界见第3节。
