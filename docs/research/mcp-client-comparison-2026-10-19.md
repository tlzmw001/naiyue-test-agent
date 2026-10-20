# MCP客户端路线与连接复用：第一轮对比

日期：2026-10-19。范围：设计研究、真实协议夹具、当前项目入口探针；未实现通用MCP产品功能，未修改项目依赖、真实配置或用户数据库。

2026-10-20状态：用户已认可会话内跨消息复用、保留内置Pydantic与默认保持/显式TTL建议；当前实施顺序以[Spec117第8节](../specs/117-mcp-client-integration.md#8-切片与独立验收)为准。本文保留第一轮实验与当时讨论证据，后续00已完成：复跑25＋10并新增54项同合同研究测试后选定SDK，当前结论见[00报告](mcp-client-selection-2026-10-20.md)。下文“尚未选择/下一实验”为第一轮时点记录，不覆盖00结果。

## 1. 用户确认与选择标准

用户接受SDK与LangChain两条路线通过实验再比较；不预设LangChain优先，也不因SDK需要更多代码而排除。选择标准按顺序为：当前项目接入合同是否清楚且可执行 → 资源/权限/错误/取消的所有权与失败路径 → 公开接口与升级维护 → 产品接线工作量 → 依赖和代码数量。允许为更顺畅的SDK接入补充必要设计与实现；尚未选择最终生产依赖。

库选择与连接生命周期是两个独立决策。库提供什么上下文管理器，不等于产品应在哪个范围保留状态。

## 2. 环境与可复现入口

- 项目现有LangChain 1.4.2、langchain-core 1.6.6、Python 3.12.14。
- 临时目标目录安装此前dry-run解析的35项研究依赖，包括mcp 2.3.0、fastmcp-slim 4.1.1。通过进程内sys.path使用，未安装进项目.venv。
- 服务夹具：build/research-mcp-20261019/server.py。它提供进程内state、显式工具错误、可取消等待、InputRequiredResult；HTTP端口由测试分配，不依赖外部账号/模型。
- 比较脚本：build/research-mcp-20261019/compare.py。
- 项目主链探针：build/research-mcp-20261019/test_project_boundary.py，复用当前native_application_helpers、build_async_local_application、权限审批与require_tool_permit；明确只包装一个固定已知Pydantic schema，不伪称任意JSON Schema动态适配完成。
- 原始输出：[比较结果](../../build/research-mcp-20261019/result.json)、[比较日志](../../build/research-mcp-20261019/run.log)、[项目探针日志](../../build/research-mcp-20261019/project-tests.log)。build与临时安装目录是本机研究产物，未纳入生产包或提交；若清理临时目录，须按同版本重建研究依赖。

执行命令：

```bash
.venv/bin/python build/research-mcp-20261019/compare.py
.venv/bin/python -m pytest build/research-mcp-20261019/test_project_boundary.py -q
```

实际结果：比较脚本25项断言通过，6.43秒；项目探针10 passed，5.14秒，1项LangChain beta警告。0次供应商请求，未连接用户业务服务。6.43秒是整组本机实验耗时，不是两库性能排名。

## 3. 两条路线的共同结果

| 场景 | 直接SDK | LangChain MCPAdapter |
| --- | --- | --- |
| stdio发现/调用、HTTP连接/调用 | 通过 | 通过 |
| 文本及结构化结果 | 探针显式提取content/structured_content | 复用转换后的ToolMessage与artifact |
| 工具主动返回is_error | 探针显式转ToolException | 默认已有ToolException子类；关闭局部handler后交项目链 |
| 拒绝未支持交互且只发送一次 | session.call_tool的allow_input_required=True显式识别 | 预建客户端自定义elicitation handler＋input_required_max_rounds=0，实际ask只调用1次 |
| 停止一个等待调用后继续使用同一客户端 | 通过 | 通过 |
| 显式关闭后stdio进程退出 | 通过 | 通过，含transport.close |
| 当前项目审批前/拒绝/无效入参 | 0次服务工具调用 | 0次服务工具调用 |
| 当前项目批准执行、工具错误、原call ID配对 | 各批准1次，错误保留 | 各批准1次，错误保留 |
| 当前同一编译图、两个聊天、A下一条消息继续 | A保持值、B空值 | A保持值、B空值 |

LangChain探针只使用公开构造器、model_copy、ainvoke、close，不通过修改私有sentinel实现策略；直接SDK同样没有自行实现协议。现代InputRequiredResult测试不等于legacy反向交互全部验证。

两种原始调用路径都不能替代项目的执行前完整JSON Schema验证：额外参数能够到达允许该参数的服务端；固定schema项目探针由当前Pydantic合同拒绝非法输入。不要把服务端最终校验当作本地授权前校验。

尚未比较完成：通用JSON Schema注册扩展、真实共享异步资源拥有任务、同会话并发/子实例/审批跨进程恢复、HTTP认证/断线/响应丢失/预算、完整多模态与五平台安装。当前不能依据35项通过就宣布任一库胜出或MCP产品已完成。

## 4. 生命周期实验：用业务语义决定范围

同一个进程内state工具，第一步set(A)，第二步get。模型/run边界由探针在两个调用之间明确划定，客户端是否关闭由候选生命周期控制；两库结果一致。

| 客户端实际生命周期 | 观察 | 决策含义 |
| --- | --- | --- |
| 每工具调用创建并关闭，stdio keep_alive=False | 第二步新PID，值为空 | 不满足任何跨调用进程内状态连续性 |
| 一次run内复用，run后关闭 | 同run读取A；下一次run值为空 | 支持一轮工具循环，不支持下一条用户消息继续该现场 |
| 同一聊天运行时复用，A/B分别创建 | A下一条消息读取A；B为空且PID不同 | 同时满足该夹具的聊天内连续、聊天间独立 |
| 整个应用共用一个客户端/进程 | B也读到A | 若业务要求聊天间独立，则范围过宽；若明确共享状态才适用 |
| HTTP指向同一进程全局状态，A/B客户端分开 | B仍读到A | 独立客户端不保证远端业务状态隔离，必须靠服务端身份/业务句柄/存储作用域 |

关键更正：FastMCP 4.1.1的StdioTransport默认keep_alive=True。实验中退出adapter上下文后PID仍存活，再进入仍得到同一PID与值；只有显式Client.close（包含transport.close）后退出。由此撤回“LC工具每次async with就意味着stdio每次重启”的概括。比较复用范围时必须固定真实进程/transport寿命，不能只看SDK ClientSession或Python上下文层数。

**核心判据：选择能覆盖所需状态连续性，同时不跨越身份与可变状态隔离边界的最小资源所有者。**

这不是实验能替用户决定的产品需求。若期望同一聊天的下一条消息继续外部工具现场、另一个聊天独立，则聊天运行时是合适默认；如果所有调用都携带可跨连接的显式句柄，可另评估更短寿命；若需要多个聊天共同操作同一现场，应明确共享，而非因URL相同自动合并。

本项目建议：应用级统一创建/关闭入口；按(session_id, agent实例身份, server配置身份)拥有客户端；单次调用借用。正常结束一轮、前置审批暂停、Web切换可见聊天不关闭；运行时删除/释放、子实例真正结束、应用关闭时回收。进程内存活期间延续，不承诺跨重启恢复外部状态。子实例当前是否有后续独立续跑，以已批准的子Agent合同为准，不由MCP顺带扩展。

共享配置/工具定义、共享客户端状态、共享HTTP底层连接池分别讨论。初版不建跨主体池，不把资源放入可序列化的消息/checkpoint，也不让全局ToolSpec闭包捕获一个有状态client。现有ToolRuntime提供执行主体身份；异步IO资源按应用的异步生命周期管理，不能仅因AgentSession持有逻辑身份就把异步上下文交给其同步工作线程关闭。

## 5. Pi最新版：实际所有权链

参考本机旧检出a69bef789确实无内置MCP；此前没有及时核对最新远端，这个结论不能用来描述当前Pi。

本次在独立临时目录检出官方main提交73f8de80bc87021375f8b525292ac07492253002，coding-agent与pi-mcp包版本1.3.0。没有更新用户现有Pi检出或安装扩展。Pi当前已有官方内置MCP扩展，也允许第三方替换；不是只存在社区桥接。

真实链：

```text
MCP扩展工厂的servers集合
  → session_start重新装配当前运行时的server项
  → startConnection/createConnection → 每server一个McpServerConnection
  → getClient：已连接则返回同一个McpClient；正在打开则共享opening Promise
  → 工具execute通过执行时getClient取得当前连接 → callTool
  → 普通turn结束不关闭
  → session_shutdown → 等待背景工作与所有connection.close
  → runtime new/resume/fork替换旧会话前，先abort并发出shutdown
```

源码定位（固定上述提交）：

- packages/coding-agent/src/extensions/mcp/index.ts：servers约281、createConnection约562、session_start1114、session_shutdown1252。
- packages/coding-agent/src/extensions/mcp/runtime.ts：getClient237、callTool246、withClient278、connectOnce355、close451。
- packages/coding-agent/src/extensions/mcp/tools.ts：createMcpToolDefinition312、execute337，执行时取连接，并传signal。
- packages/coding-agent/src/core/agent-session-runtime.ts：teardownCurrent167；new/resume/fork均有真实消费者；dispose404。

注意：raw网页会因空行渲染出现与本机文件不同的行号，链接以文件/提交为准。Pi的交互运行时通常有一个当前会话；切换运行时会关闭旧连接。本项目Web允许多个聊天同时存在，不能把“切换可见页面”照搬成销毁旧运行时。

最新版使用它自己的@earendil-works/pi-mcp独立客户端，不依赖官方MCP SDK；README声明其协议面为2025-11-25及更早兼容。借鉴职责与所有权，不将其选型或旧协议写法当作Python路线/现代协议事实。它对会话失效有单独分支：认为明确会话不存在时未执行，请求可在新会话重试一次；其他不确定工具失败不自动重放，不能概括成任何情况下绝不重试。

## 6. 第一轮结论与下一步

当前证据支持两库均为可行候选，尚无硬阻；SDK的额外适配代码可以接受，不能以包数量/代码行数先行淘汰。下一实验应保持同一产品行为，分别完成通用schema与真实异步拥有/关闭接线的最小研究适配，比较公开接口依赖、必要自有逻辑、正常与失败路径和可维护性，再只选一条生产路线。该研究不等于先做两套完整产品后端。

连接范围建议已有条件化依据：若采用聊天内连续、聊天间独立的产品语义，则应用管理、聊天/实例拥有、调用借用；HTTP服务端业务隔离另有责任。不要用参考项目采用了某方式代替这个需求判据。

研究过程边界：最初夹具裸dict返回没有结构化合同，先暴露structured_content为空；开启结构化后SDK明确拒绝裸dict，改为夹具真实StateInfo输出模型。项目探针首轮6绿4红，问题是研究调用误用deny而非现有reject，以及探针把原始binding.args误当已填默认的subject.args；按现有源码修正研究调用/适配断言后10绿，未修改产品、既有测试、断言目标或测试基础设施，不把这些失败归因于两库。

## 7. 内置工具与MCP的schema适配

用户已确认按会话保持连接；本节补充schema方案说明，尚未改动产品。Pi仍核对固定73f8de80。

Pi内置Read的readSchema使用TypeBox Type.Object/Type.String/Type.Optional/Type.Number，作为工具parameters。agent-loop.prepareToolCall先准备参数，再调用validateToolArguments，之后才进入beforeToolCall和execute；无效输入形成错误结果，不执行工具。validation.ts使用typebox/compile的Compile及Check，缓存validator，还先做optional null归一化和Value.Convert；普通JSON Schema另有coerceWithJsonSchema。因此不能描述成“Python jsonschema库”，也不能暗示完全无转换的原样校验。

MCP的inputSchema经toParameters补缺失type/properties后成为工具parameters，同样进入公共执行校验入口。采用相同描述体系不等于任意方言/远程引用都已证明兼容，也不意味着必须迁移本项目校验器。

本项目内置工具以Pydantic为源：例如Read严格整数offset、未知字段禁止、默认值及Python validators，ToolSpec.validate_input执行model_validate，再由prepare_permission_call取得model_dump作为权限subject。模型侧JSON Schema属于导出描述，不能反向生成新Pydantic来替代原模型，否则可能丢失Python校验/严格类型等语义。

建议保留两条输入合同：

```text
内置：原Pydantic模型 → model_validate → 已校验参数
外部：远端JSON Schema → JSON Schema validator → 已校验参数
                                         ↓
                     同一权限subject／审批／一次许可／工具执行主链
```

需要改动的实际耦合点：ToolSpec.input_schema类型；registry注册校验与validate_input；permission_rules直接访问model_fields；prepare_permission_call的model_dump和ValidationError合同；authorization非法输入分支；JSON Schema StructuredTool的隐藏Runtime注入/参数保留名。MCP默认只支持工具级规则，不能把远端path字段当本地路径，也不能默默接受尚不执行的args规则。

Spec117当前候选用RootModel包装已被JSON Schema验证的dict，以维持调用方model_dump合同；这只是已验证数据的返回封装，不是把远端schema翻译为Pydantic。JSON Schema失败须映射到当前“参数非法、未执行”的异常/反馈合同，不能只加validator却因上层只捕获ValidationError而走错分支。注册时检查schema本身，调用时检查入参；不自动填充远端default、不做字符串转数字、额外参数按原schema决定，不一律删除或禁止。

本轮本机小探针：原Read offset=1成功，offset='1'以int_type拒绝；Draft202012Validator对integer schema接受1、拒绝'1'及禁止的额外字段；带default的可选字段未传时仍保留为空dict，RootModel.model_dump亦为原dict。现有注册器专项命令.venv/bin/python -m pytest tests/test_tool_registry.py -q，实际22 passed in1.06s。仅证明当前内置合同与验证库语义，不是MCP通用接线验收。

## 8. Pi运行时与本项目对应

Pi的AgentSessionRuntime是具名类，持有当前AgentSession、cwd绑定的AgentSessionServices和创建工厂，管理new/resume/fork/quit时的整体替换与关闭。AgentSessionServices含cwd、modelRuntime、settingsManager、resourceLoader；AgentSession持有agent、sessionManager、扩展runner等活对象。这里的runtime指当前进程内可执行的对象组合，不是Python解释器，不是一次模型请求，也不是持久JSONL文件。

| Pi实际对象/职责 | 本项目对应，非一一映射 |
| --- | --- |
| AgentSessionRuntime：当前会话及服务的生命周期 | AsyncApplication的异步资源生命周期＋SessionService管理的某个AgentSession；本项目支持多会话并存 |
| Pi AgentSession：当前聊天执行/状态/扩展等 | 本项目AgentSession＋SessionExecution及部分服务职责 |
| Pi Agent / agent-loop：模型工具循环 | 本项目create_agent编译图及middleware；当前图由应用共享 |
| SessionManager与JSONL记录 | 本项目SessionState、SQLite/checkpoint/归档的部分职责；不是存储格式等同 |
| cwd绑定服务与MCP扩展的活连接 | 本项目Catalog/ToolRegistry/设置/资源服务，以及计划中的按会话异步MCP资源 |

本项目SessionService._sessions保存活AgentSession；AgentSession持有SessionState并借共享graph/catalog/registry；ActiveRun只属于一次运行，收尾后释放。SessionExecution是AgentSession的方法组成，不是又一个独立常驻runtime。工具参数ToolRuntime是框架注入的当前调用上下文，提供session_id等事实，不是MCP资源总容器。

因此根会话A执行run1→结束→run2，复用同一AgentSession与MCP所属连接；ActiveRun更换，编译图仍共享。不能将MCP存入ActiveRun或checkpoint，也不需要仅为连接复用另建每会话编译图。异步资源在应用正式loop管理，AgentSession的短同步状态锁/工作线程不负责随意退出AnyIO上下文。

## 9. 空闲回收：参考与建议边界

区分三种寿命：HTTP物理socket、MCP逻辑客户端/协议会话、stdio服务进程。HTTP空闲socket回收可委托底层pool；本轮核对研究环境httpx2.Limits公开签名有keepalive_expiry=5.0，其他最大连接数默认不同于HTTPX1，不能照搬。连接池过期不等于终止MCP会话，也不杀远端进程；在途SSE/订阅流不应当作空闲socket清理。

Pi官方内置MCP在已检查扩展/runtime源码中按session_shutdown等显式边界关闭，未找到同类idle TTL机制。对比第三方nicobailon/pi-mcp-adapter固定0a3f72be46e95a979942288cc82f40d9e190feb2：

- lifecycle.ts定期checkConnections，读取per-server或global idleTimeout；keepAliveServers跳过回收，0禁用。
- server-manager.ts.isIdle仅考虑已连接项；inFlight或activeProtocolOperations大于0则不回收，再比较lastUsedAt。
- direct-tools.ts在调用/审批所在流程递增inFlight，finally递减并touch；idle-close-in-use.test.ts覆盖等待审批、拒绝、取消与UI使用。这里只读源码与测试，未运行其依赖/测试。
- 文档给普通lazy服务10分钟默认、30秒检查及下次调用重建；有状态服务建议lazy-keep-alive或idleTimeout=0。这些是该扩展的选择，不是MCP协议要求或本项目已采用默认。

本项目建议先按需建立会话执行连接，仅看历史/切换前端不分配新执行连接；临时发现连接单独关闭。默认不自动关闭可能有状态的客户端/进程。明确可安全重建的服务再显式启用per-server idle_timeout_seconds，0不回收、正值接受其连接内状态可能重置；暂不加入LRU平台或根据readOnlyHint自动推断无状态。

若实施正值TTL，应同时满足：当前run不活动、无在途借用、无绑定的待审批/交互；从最后占用释放开始计时；健康ping不延长业务idle。回收与新借用在同一所有权同步边界内先转closing，新调用等待关闭完成后创建新一代，防止关闭正在借用或新建的资源。保留目录/配置不等于恢复业务状态，不能重放旧工具。无资源可用时不能为了LRU静默杀有状态会话。现有max_active_runs只约束执行中run，不约束空闲连接数。

按会话复用已确认；上述默认关闭TTL和可选TTL细节仍是建议，不作为用户已确认的配置。验证应使用可控时钟、借用/关闭事件、实际tools/call次数，覆盖run思考中、审批暂停、回收与新调用竞争、关闭失败、其他会话不受影响。

## 来源

- [Pi官方当前MCP说明](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/docs/mcp.md)
- [Pi MCP扩展](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/extensions/mcp/index.ts)、[连接运行时](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/extensions/mcp/runtime.ts)、[聊天运行时切换](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/agent-session-runtime.ts)
- [Pi独立MCP客户端](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/mcp/README.md)
- [LangChain连接](https://docs.langchain.com/oss/python/langchain/mcp/connections)、[工具适配](https://docs.langchain.com/oss/python/langchain/mcp/tools)、[Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Pi Read schema](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/coding-agent/src/core/tools/read.ts)、[公共参数校验](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/ai/src/utils/validation.ts)、[工具准备与执行](https://github.com/earendil-works/pi/blob/73f8de80bc87021375f8b525292ac07492253002/packages/agent/src/agent-loop.ts)
- [社区扩展空闲策略](https://github.com/nicobailon/pi-mcp-adapter/blob/0a3f72be46e95a979942288cc82f40d9e190feb2/docs/configuration.md)、[lifecycle](https://github.com/nicobailon/pi-mcp-adapter/blob/0a3f72be46e95a979942288cc82f40d9e190feb2/lifecycle.ts)、[isIdle与使用计数](https://github.com/nicobailon/pi-mcp-adapter/blob/0a3f72be46e95a979942288cc82f40d9e190feb2/server-manager.ts)
- [HTTPX资源池说明](https://www.python-httpx.org/advanced/resource-limits/)，本项目候选SDK使用httpx2，具体签名以研究安装源码为准。
