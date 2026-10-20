# MCP-00：客户端选型结果

日期：2026-10-20。**选定直接使用官方Python SDK `mcp==2.3.0`，本地参数合同显式依赖`jsonschema==4.26.0`，沿用LangChain 1.4.2/core 1.6.6。** 不引入生产FastMCP/`langchain[mcp]`后端。两条路线都通过本轮同合同实验，没有证明LangChain不可用；这是对当前工具/权限/资源合同的项目选择。

本片完成研究与决策，没有修改产品代码、项目依赖、真实配置或数据库。MCP-01—08仍未实现。[实验Spec](../specs/117-00-mcp-client-selection.md)、[可复查脚本与复现说明](mcp00/README.md)、[总规划](../specs/117-mcp-client-integration.md)分别记录合同、实验和后续接线。

## 1. 为什么选SDK

本项目已有注册表、权限准备、一次许可、错误处理、共享编译图和会话执行链。实验表明，无论哪条路线，都必须自行增加JSON Schema合同、隐藏ToolRuntime注入、按主体取得客户端、参数非法时终止、资源/预算与结果策略。LC现成工具不能直接绕过这些边界。

| 决策项 | SDK 2.3.0 | LangChain MCPAdapter＋FastMCP 4.1.1 |
|---|---|---|
| 同一通用schema/真实权限/ToolNode | 通过 | 通过；返回schema与夹具原始schema一致 |
| 共享图执行时按会话借用 | 自有工具包装取得owner后，直接调用session | 自有工具包装取得owner后，再调用该owner绑定的LC工具 |
| 未支持现代交互 | session.call_tool(...,allow_input_required=True)返回原始类型，项目明确拒绝 | 预建客户端自定义handler＋input_required_max_rounds=0，抛轮数耗尽异常；原调用只发送一次 |
| 原始结果/声明错误 | 直接读取CallToolResult，再进入项目结果/错误策略 | 先经FastMCP与LC转换；关闭工具自己的handle_tool_error，再交项目错误链 |
| 资源复用与清理 | 自己持有异步owner，明确同任务进入/退出 | FastMCP已有背景session管理与重入；本实验统一放进owner。其成熟管理是优势，不能声称LC必须自行重写 |
| 读取预算 | 公开Transport上下文＋streamable_http_client(http_client=...) | 公开StreamableHttpTransport(httpx_client_factory=...)；同样可做 |
| 必须维护的额外逻辑 | schema/工具/结果/错误/元数据转换、owner、发现/权限接线 | 相同项目合同＋默认交互/错误策略调整＋工具包装/转换后策略；可复用LC多模态等转换，但首版不用 |

**决定性取舍**：首版只要文本/structured JSON，不启用执行中交互；还必须动态按会话选择资源。SDK的原始调用直接进入项目合同，无需先取得一组绑定特定客户端的可执行LC工具，再外包一层按主体路由。这样权限、未支持交互、错误和结果的归属更直观。代价是项目自己维护这些薄转换与owner；用户已明确接受必要的代码和设计。本实验没有证据表明某库更快或代码更少，不作性能排名。

两库共同通过意味着选型不是硬能力淘汰。若未来需要大量多模态转换、交互恢复或FastMCP客户端扩展，可用明确需求重新评估；当前只维护一个生产后端，不预建双后端开关。

## 2. 本轮实际证据

项目HEAD：`1d34b6fa5c3a34309e6f1786d91d66f3ea73d966`，Python 3.12.14、macOS arm64。研究依赖沿用已存在的临时target，未装入项目venv。版本与关键源文件SHA见[环境记录](../../build/mcp00-20261020/environment.json)。原研究文件复制到新的baseline目录后复跑，未覆盖2026-10-19证据。

| 命令 | 本轮结果 |
|---|---|
| `.venv/bin/python build/research-mcp-20261020-baseline/compare.py` | 25项断言通过，5.92s |
| `.venv/bin/python -m pytest build/research-mcp-20261020-baseline/test_project_boundary.py -q` | 10 passed in 5.19s，1项LC beta警告 |
| `.venv/bin/python -m pytest docs/research/mcp00/test_selection.py -q --basetemp=build/mcp00-20261020/final` | 54 passed in 23.59s，1项LC beta警告 |
| `.venv/bin/python -m pytest tests/test_tool_registry.py -q` | 22 passed in 1.11s |
| `.venv/bin/python -m pip check` | No broken requirements found. |
| `.venv/bin/python -m compileall -q docs/research/mcp00` | 退出码0；5个研究Python文件AST/500行检查通过 |
| `git diff --check`及新增研究文件空白/本地链接检查 | 通过；23个本地链接目标存在 |

原始输出：[基线协议结果](../../build/research-mcp-20261020-baseline/result.json)、[基线应用日志](../../build/research-mcp-20261020-baseline/pytest.log)、[新增矩阵日志](../../build/mcp00-20261020/final.log)、[逐类计数](../../build/mcp00-20261020/summary.json)、[原注册器回归](../../build/mcp00-20261020/registry.log)。这些build文件是本机证据；持久研究源代码在本文同级mcp00目录。

新增54项包括：通用schema/审批/隐藏Runtime 24项；跨任务并行借用/取消/关闭4项；声明错误/交互/结果预算/未知副作用不重放16项；两个stdio owner隔离2项；保留名/远程引用拒绝2项；HTTP解析前JSON读取预算2项；当前AsyncApplication共享图/跨run与两个owner组合2项；清理错误身份与新故障传播2项。矩阵主要采用两库×stdio/HTTP相同输入和断言。0次模型供应商请求，没有用户业务server或真实凭证请求。

## 3. 正常链与两种失败链

研究输入是夹具数据`{"selection":{"kind":"count","value":3}}`，声明的可选note带default但未传入。不是用户业务样本。

```text
远端原始inputSchema（oneOf＋本地$defs/$ref）
 → 本地Draft202012Validator：不把"3"转3、不补note默认值
 → 当前prepare_permission_call：subject.args保持原输入
 → 当前permission_binding＋OneUseApprovalGrants
 → 编译ToolNode的研究授权hook＋当前ExecutionAuthorizationMiddleware
 → 隐藏ToolRuntime → 当前require_tool_permit
 → OwnedClient.call → 对应SDK session或该owner的LC工具
 → 文本/structured结果限制 → 原outer-call配对ToolMessage
```

非法类型或模型伪造runtime参数：prepare得到invalid → 研究hook直接返回错误ToolMessage → 远端调用数0。拒绝时为0；批准时恰好1次；重复使用已消费许可抛原PermissionMechanismError，计数仍为1。生产授权层尚没有这个JSON invalid拦截；它必须在01正式实现。研究SchemaSpec不是生产ToolSpec，暂把JSON校验错误映射Pydantic异常只是为了调用现有权限函数，不作为01的最终接口设计。

资源链：单一owner任务进入上下文并完成发现 → 不同任务并行借用 → 取消一个等待调用 → 下一调用继续成功 → 另一关闭任务禁止新借用、取消在途、通知owner → 原owner任务退出上下文 → LC再显式close → stdio PID消失。两个stdio owner PID不同；真实AsyncApplication固定schema工具也验证A/B不同PID、A下一run仍为同一PID、共享graph未更换。基线有状态set/get另验证跨消息值延续。

应用关闭正式接线尚未完成：组合实验由研究ExitStack显式组织app.aclose和owner关闭，不能说现有app.aclose已经管理MCP。连接首次并发创建、启动失败与关闭竞态、根子实例真实释放等完整合同留03/05。

未知结果链：夹具先写committed审计，再保持无响应 → 审计事件同步后触发调用超时/取消 → 远端原调用计数保持1 → 同连接后续echo成功。只能说明本测试没有重放、调用方停止等待；不能把超时解释为副作用未发生，也不能保证任意远端服务已停止计算。这不是任意网络断线/会话失效矩阵的替代。

## 4. 读取预算发现的真实边界

两库都能通过公开HTTPX2 transport扩展，在application/json的字节被SDK JSON解析前拒绝超过研究阈值6144字节的响应；同一8192字符夹具结果仅调用一次。正常小结果先成功。阈值只用于研究，不是生产默认配置。

实际失败形状：RPC等待者可能只收到MCPError(Connection closed)，owner关闭时SDK抛包含原ReadError的ExceptionGroup，LC展开为ReadError。run2两项红灯暴露这一点，不能把finally关闭再抛同一错误视为无关细节。研究源代码保留读取时原异常对象，向调用者重抛该对象并关联RPC错误；关闭时只有所有叶异常都与已经报告过的对象逐一相同，才记录明确诊断而不再次覆盖调用错误。出现额外清理故障仍raise，故障注入断言已验证。两库都需要项目定义这个合同，SDK不能免除此责任。

这是公开扩展可行性证据，不是完整恶意服务防护：未验证压缩解码后的体积、任意下游单chunk硬界、SSE超限端到端或stdio解析前硬限。SDK的SSE单事件上限公开可设；stdio目前没有同类公开单行预算。03必须把解码/结果/在途任务/失效与清理诊断写成实际合同，不能把本轮解析后4096字节限制当作网络硬界。

## 5. 固定依赖与公开入口

01先引入参数校验必需的`jsonschema==4.26.0`及其传递依赖，02再增加`mcp==2.3.0`及剩余依赖，沿用已有httpx2/anyio/LangChain；不在本片安装。按当前发布方式同步requirements与pyproject全部新增传递依赖，并按平台重新解析/检查，不把本机dry-run升级成五平台证明。

当前13项新增版本快照：mcp 2.3.0、mcp-types 2.3.0、jsonschema 4.26.0、attrs 26.1.0、jsonschema-specifications 2025.9.1、PyJWT 2.15.1、cryptography 50.0.2、cffi 2.1.1、python-multipart 0.0.32、referencing 0.37.0、rpds-py 2026.9.1、sse-starlette 3.5.0、pycparser 3.11。数量是已有dry-run快照，当前实验只核实实际使用版本，未重新运行跨平台解析；LC比较版本fastmcp-slim 4.1.1留研究，不纳入生产。

| 后续用途 | 选择的公开入口/自有责任 |
|---|---|
| 客户端/协议协商 | mcp.client.client.Client，SDK处理协商；项目显式拥有上下文 |
| stdio | StdioServerParameters＋Client；受控stderr/命令/env由配置层准备 |
| HTTP | streamable_http_client(http_client=...,max_sse_event_size=...)作为Transport传Client；HTTPX2公开transport扩展负责读取策略 |
| 目录 | list_tools/必要分页API；项目限制页数/数量/大小、校验schema、冻结allowlist与指纹 |
| 单次调用 | client.session.call_tool(...,allow_input_required=True)；明确识别InputRequiredResult并拒绝，不使用高层自动交互循环 |
| 结果 | CallToolResult/TextContent/structured_content/is_error；自有有界文本/JSON转换与项目ToolException，不携带原始凭证/响应进入日志 |
| 参数 | Draft202012Validator；保留内置Pydantic，禁止外部引用，发现时拒绝不支持合同 |
| 生命周期 | 单一拥有任务＋按主体借用；同任务enter/exit、并发创建与关闭屏障、故障诊断由03实现 |

SDK高层Client.call_tool除了自动交互，还包含现代HEADER_MISMATCH重新发现后重发分支；选低层公开session入口可让项目明确决定不自动重放。FastMCP当前call_tool也可限制交互轮数；没有证据证明其在本轮故障场景自动重发。任何后续安全重试必须单独确认已证明未执行的条件。

## 6. 接线顺序与停止点

01：只做JSON Schema注册/参数合同及现有授权层的非法输入终止，保护所有内置Pydantic行为。02：引入所选SDK及剩余依赖，配置、受控传输、目录/指纹和只读发现。03：App正式loop管理按(session_id,agent实例,server配置身份)的owner，按现有关闭/停止合同接入；不能把研究ExitStack当成产品装配。04才把根Agent端到端调用接上。其余顺序保持Spec117。

本轮没有运行全量后端/前端构建，因为没有产品修改；研究脚本编译、原注册器回归和依赖检查属于本片验证，不代表MCP产品构建、任意server验收、Windows/Linux原机或真实模型业务效果。00结束后的唯一下一片是01；尚未自动启动。

## 一手资料与固定参考

- [Python SDK v2官方文档](https://py.sdk.modelcontextprotocol.io/)、[客户端](https://py.sdk.modelcontextprotocol.io/client/)、[传输](https://py.sdk.modelcontextprotocol.io/client/transports/)。实现细节以本轮固定2.3.0源码与环境SHA为准。
- [LangChain连接文档](https://docs.langchain.com/oss/python/langchain/mcp/connections)、[工具文档](https://docs.langchain.com/oss/python/langchain/mcp/tools)。本轮固定1.4.2源码；重入与外层保持连接均可用，beta不是不可用结论。
- Pi73f8de80与AgentX3fcfa0e相关生产字段/消费/关闭链沿用并核对[第一轮报告](mcp-client-comparison-2026-10-19.md#5-pi最新版实际所有权链)记录；借鉴会话所有权，不用其不同语言/协议版本替代本次Python实验。
