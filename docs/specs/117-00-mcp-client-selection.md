# MCP-00：同合同客户端选型实验

日期：2026-10-20。用户已授权启动00；本片只完成研究、实验和单一依赖建议，不实施01—08，不修改项目依赖、真实配置或数据库，不提交/发布。

## 1. 当前数据流与缺口

当前应用工厂 → 共享编译图 → AgentSession/一次执行 → ToolSpec(Pydantic) → prepare_permission_call → 一次审批许可 → ExecutionAuthorizationMiddleware → 工具执行体 → ToolMessage。现有注册器不接受任意JSON Schema，非法参数分支依赖工具内部的Pydantic再次拒绝；不能直接插入JSON Schema工具。

第一轮25项协议/生命周期断言和10项当前应用固定schema探针已证明两库基本可行。本片先复制研究夹具到新的build目录复跑，保留旧证据，再补任意schema与跨任务资源所有权实验。研究适配不能冒充生产注册器接线。

## 2. 固定条件

- 当前LangChain 1.4.2/core 1.6.6不变；候选直接mcp 2.3.0，或LangChain MCPAdapter＋fastmcp-slim[client] 4.1.1。使用既有临时target依赖，不安装进项目环境。
- 同一个SDK服务端夹具，以公开list_tools/call_tool覆盖提供原始schema；使用真实stdio子进程和loopback Streamable HTTP。夹具数据仅用于确定性实验。
- 同一输入：nested object、oneOf、本地$defs/$ref、enum、default不填充、additionalProperties=false；本地采用Draft202012Validator。拒绝远程引用与保留runtime参数是客户端共同的注册责任。
- 同一输出：文本＋structured JSON；声明错误、交互请求、超出结果预算分别显式失败；不自动重放原调用。时间/输出预算先作为研究参数，不确定产品默认值。
- 同一资源边界：专门asyncio任务进入/退出客户端；其他任务借用；两个聊天独立；调用取消不关闭所属连接；应用式关闭由另一任务请求，取消在途调用并等待owner清理。默认不做TTL。

## 3. 新增研究文件与接口

`docs/research/mcp00/`保存可复查脚本，不属于生产包：

| 文件 | 职责/接口 |
|---|---|
| fixture.py | 同一真实MCP服务端，原始schema、结果、等待/副作用后不返回、审计 |
| clients.py | Candidate(route, audit, url)、call(name,args)、显式close；OwnedClient.start/call/close；不缓存到全局生产对象 |
| boundary.py | 研究SchemaSpec.validate_input、隐藏ToolRuntime工具、真实权限prepare/authorize；仅研究补非法JSON参数拦截 |
| http_budget.py | 两库共同的公开HTTPX2 transport扩展，JSON读取时预算与原始错误诊断；不改MCP协议 |
| test_selection.py | 两库×传输×schema/许可/资源/失败用例；记录审计次数、PID、任务和参数 |

正常链：模型形状ToolCall → 本地JSON Schema校验（不补default）→ 真实prepare_permission_call → 审批绑定与一次许可 → 编译ToolNode注入隐藏runtime → require_tool_permit → 按会话借用owner → 原SDK调用或LC工具调用 → 同一结果限制 → 配对ToolMessage。

失败链：非法参数 → 研究授权hook直接配对错误、远端0调用；审批未授予/重复使用 → 原PermissionMechanismError；server已记录副作用但无返回 → 取消/超时向上传播、远端只调用一次，不宣称未执行；关闭 → 先阻止新借用、取消在途调用、同owner任务退出上下文、显式关闭FastMCP transport、等待PID消失。

研究不修改生产registry/permissions/authorization。使用当前公开ToolNode和真实项目权限函数；该部分只能证明适配合同可行。生产注册、全应用资源装配及生命周期竞态完整实现分别留01/03。已有10项当前应用探针补足固定schema主链证据。

## 4. 验证与选型规则

先固定行为断言，再写最小研究适配并运行；这是探索性实验，记录失败原因，不将其称作产品TDD。每个候选运行同一矩阵。检查缺失schema、非法值/额外字段、默认值与许可/远端参数一致、单次授权、状态延续、并发借用、取消后继续、关闭在途、结果预算、工具错误/未支持交互及未知副作用不重放。

选择顺序：正确性/权限与资源归属 → 公开接口与策略可控性 → 必须自有逻辑及维护 → 发行成本。不以总运行时间作性能排名，不以包数自动淘汰；两库均通过时说明为什么某库更贴合当前合同，而非声称另一库不能做。输出固定版本、必要公开API、后续接线与明确未测范围。

运行新研究pytest、基线探针、脚本compileall、当前venv pip check、git diff --check和文档链接检查。没有修改生产代码，不把这些检查称作全量产品构建/测试。平台、OAuth/认证服务、断线重连矩阵、压缩后膨胀与恶意远端流量的解析前全链路预算均需独立证据；结果转换后限制不提供进程内存硬界。HTTP预算实验证明两库的公开transport扩展入口能在JSON解析前截断读取，未宣称压缩解码/单个下游chunk/stdio均有硬界。

实验中读取预算错误可能先令RPC收到Connection closed，再在owner退出时抛出底层ReadError（SDK包装为ExceptionGroup，LC展开）。研究适配保留读取时原始异常对象并向调用者重抛；关闭时只把已经向调用者报告过的同一异常对象记为清理诊断，其他关闭失败继续raise。新增独立失败注入断言校验两种分支；不能用异常文本匹配去吞掉新故障。该机制限研究owner；MCP-03须按实际连接失效/并发调用和保存合同接线。

## 5. 实际结果

已完成：[00选型报告](../research/mcp-client-selection-2026-10-20.md)。两库54项新增研究测试通过，23.59s；基线25项5.92s＋10项5.19s复跑通过，原注册器22项1.11s通过。选定官方mcp 2.3.0＋jsonschema 4.26.0；01先引入校验库、02再引入SDK，当前产品依赖不改。正常/失败链、读取预算发现、精确清理诊断与未测边界见报告。
