# Spec119：调用观测补齐

日期：2026-10-20。基线：HEAD `c5ca965` 加当前工作树。状态：**119-00规范已确认；119-01/02/03已完成本机离线与安装验收，纯观测阶段收尾**。

用户已确认本阶段聚焦观测，原04任务次数硬预算、05任务时间硬预算移出本轮。本文中的计数和耗时均为观察事实，不参与执行准入或停止决策。00仅写文档；后续切片先说明具体改动，经用户同意再实现。

实施顺序（2026-10-20对齐）：**Spec118已完成本机离线验收，用户已授权并推进119-01/02/03**。第2节保留01开始前的基线；01结果见12节，02门禁/接线见14/15节，03合同及结果见16/17节。

## 1. 目标、范围与验收问题

围绕现有一次根run及其子Agent，回答三个问题：模型执行了几次、SDK准备发送了几次HTTP请求、耗时分别在哪里。复用现有模型用量和运行诊断，不新建追踪服务、任务管理器或另一套Agent循环。

本阶段交付：

1. 区分框架模型执行、SDK HTTP尝试、工具dispatch和根run，保留现有身份与根子归属。
2. 给模型执行、工具dispatch、根run增加有明确起止边界的单调时钟耗时。
3. 观察模型客户端HTTP请求开始和响应头返回，看到SDK重试带来的多次尝试。
4. 通过原CLI和Web查看；明确未知值、未闭合、采集缺口和诊断保留范围。

本阶段不做：任务次数/时间配额、超限停止、跨审批剩余额度保存与恢复、费用估算、项目侧新增重试、统一网络追踪、MCP协议内部请求计量、首token延迟、响应体逐块网络耗时。现有用量累计继续工作，但不为本功能新建跨多run的任务身份。

现有模型/工具/MCP超时、子Agent调用限制、SDK重试参数、停止与取消、权限审批、收尾和必要保存语义保持。Spec118的输入token预算与本Spec的调用观测是不同职责。

## 2. 当前系统事实

以下为119-01开始前、Spec118完成后的源码边界；01新增的实现与验收见第11节及后续结果，不把本节缺口当作当前状态。

| 能力 | 当前生产者与消费者 | 已有事实及缺口 |
|---|---|---|
| 模型执行 | `ModelUsageCallback` → `RunEventPublisher` → 用量/诊断订阅 | 单套`model_call_started/finished`按invocation_id记录框架执行、用途、状态及usage；没有通用执行耗时 |
| 逻辑请求准备 | `_ModelSelectionMiddleware.abefore_model` → prepared/turn/context直接消费者 | 预留model_call_id，固定配置/工具/提示词并检查压缩；不另发布逻辑模型调用事件，逻辑ID不能与callback执行ID混算 |
| 模型用量 | `ModelUsagePersistence` → `SessionStorage` → `model_usage_storage.read_summary` | 独立SQLite事实，根子/模型/用途分组，final/partial/unavailable，采集完整性；不是SDK请求尝试账目 |
| 工具 | `aobserve_tool_dispatch`及审批结果观察 → 运行事件/诊断 | 已有call ID、boundary、outcome；started不证明工具函数或外部IO已执行 |
| 根run | `RunObservation.start/result/failed/finish`及应用执行拥有者 | started/finished及终局；必要清理和保存仍由执行链负责 |
| 有限诊断 | `project_diagnostic_event` → `DiagnosticPersistence` → 工作区SQLite | 白名单、容量/TTL、根run及子事件、有界查询；不是永久完整档案 |
| 客户端 | `catalog_lifecycle.prepare_owned_clients`及catalog生命周期 | OpenAI使用httpx，Anthropic使用httpx2，Ollama持有自身httpx客户端；当前未统一观察HTTP尝试 |
| 展示 | Web用量页、diagnostics API；CLI事件展示 | Web已有用量/诊断只读查询；CLI尚无同等用量/诊断查询命令，03需要补入口 |

已有公开Web路由：`GET /api/sessions/{session_id}/usage`、`GET /api/sessions/{session_id}/diagnostics`、`GET /api/sessions/{session_id}/diagnostics/{run_id}`。继续使用原认证、会话归属校验、分页与错误合同，不绕过应用层直接查库。

模型用量的四项计数、缓存口径、缺失值及重启未确认合同继承[Spec108](108-persistent-model-usage.md)；有限诊断继承[Spec109](109-web-replay-and-persistent-diagnostics.md)。

### 2.1 与Spec118的事件迁移关系

[Spec118](118-request-preparation-and-context-budget.md)第6节已确认将callback来源的执行事实统一命名为`model_call_started/finished`，移除现有逻辑调用事件的重复发布。2026-10-20用户已确认第11节剩余两项，授权118分片实现；当前118-01/02/03已完成本机离线验收，前置已满足；用户已授权119-01/02/03，各片现状以本文顶部及实际验收章节为准。

本Spec以**callback执行ID与事实来源**作为计量权威，不以事件名字猜语义：

- 118先落地并验收，119-01只在迁移后的callback来源`model_call_finished`载荷追加`duration_ms`；不恢复旧逻辑事件，不发布`model_invocation_*`兼容别名。
- 新`model_call_*`继续用`invocation_id`配对。turn、context_snapshot及压缩/恢复中的`model_call_id`仍是逻辑关联，不能将它当作框架执行ID或HTTP尝试的父ID。
- 118负责事件改名及消费者迁移；119在迁移后同步新增耗时字段的日志、序列化、存储、查询和测试。开工前重新核对真实代码，不沿迁移前的事件名接线。
- 存量SQLite中的`invocation_id`仍表示框架执行身份；不因展示名称变化重写历史ID，也不增加第三套模型执行事件。
- 新`model_call_finished returned`表示框架返回，不表示项目门禁accepted。门禁拒绝时保留这一条returned及其耗时，由原turn/run失败表达拒绝，不补第二条模型failed结束或重复入账。
- 历史诊断的旧`model_call_*`仍可能表达逻辑accepted/failed，旧`model_invocation_*`表达框架执行；读取时保留原协议事实，不能仅按同名事件重解释、重新配对或累计历史模型耗时。

对齐后的01仅扩展三类既有终态：`model_call_finished`、`tool_call_finished`、`run_finished`。模型计时从callback开始到callback终结；118提前的turn开始和请求准备不并入模型耗时，仍包含于根run耗时。摘要、Auto与主模型各自按独立invocation_id计时。

## 3. 身份与计数口径

| 观察对象 | 身份及作用域 | 加一次的时点 | 不能据此推断 |
|---|---|---|---|
| 根run | 外层`session_id/run_id` | 应用发布`run_started` | 完整业务任务、跨审批连续执行 |
| 模型执行 | callback `run_id`转换的`invocation_id` | `on_chat_model_start` | SDK只发了一次HTTP、供应商已接受或计费 |
| SDK HTTP尝试 | 新`attempt_id`，归属一个`invocation_id` | 该模型客户端的request hook实际进入 | 网络字节已发出、服务器已处理 |
| 工具dispatch | `(run_id, source, tool_call_id, boundary)` | 对应boundary的开始事实 | 工具函数进入、产生了外部副作用 |

外层`run_id`沿用根运行；子Agent由`source.agent_path/agent_id/agent_run_id/parent_run_id/parent_tool_call_id`表达。不能把子callback ID当成根run ID。并发事件按身份配对，不依赖相邻位置。

用途沿用`agent/summary/auto_fast/auto_detailed`和未知；根/子由现有source区分。一次项目逻辑请求发生输入溢出恢复时，首次模型、摘要模型、重发模型分别计为三次callback执行；一次callback内部SDK总共尝试三次（含初次），模型计数仍为一，HTTP尝试计数为三。

工具的`approval_resolution`与`dispatch`分开统计和展示；同一个tool_call_id跨审批run出现不全局去重。拒绝、前批失败等`not_executed`保持原outcome，不算作工具执行成功。HTTP重定向/认证重发若再次经过request hook，也计作新的HTTP尝试，不能把所有额外尝试都命名为“SDK重试”。

会话累计模型调用继续由已有用量记录提供。初版HTTP尝试数只描述**保留的run诊断中观测到的开始事实**；不新增永久会话HTTP总账，不承诺从已淘汰诊断恢复完整历史。

## 4. 119-01：耗时合同

### 4.1 统一数据形状

在相应终态数据类追加可空字段`duration_ms: int | None = None`。只接受非bool、非负、SQLite有符号64位范围内的整数；单位为毫秒，单调时钟差截取到整毫秒。小于1毫秒可真实得到0；缺失必须为None。

开始时在已有在途观察记录中保存`monotonic_ns()`，结束时计算差值；UTC timestamp继续用于排序和人类阅读，不用两个UTC时间相减充当准确运行耗时。时钟读取/字段校验失败只损失观测，仍尽力发布原终态及安全诊断。

不新增独立计时服务。模型复用callback的在途记录，工具在原dispatch wrapper的局部变量计时，根run由已有`RunObservation`保存起点。新增字段使用默认值，已有调用签名和原事件字段含义保持。

### 4.2 起止边界

| 对象 | 起点 → 终点 | 包含及限制 |
|---|---|---|
| 模型执行 | callback开始 → callback返回/失败/取消终结 | 包含适配器与SDK执行、SDK重试及退避、流式消费；不等同网络传输耗时，不包含开始callback之前的提示词准备 |
| 工具dispatch | `aobserve_tool_dispatch`入口 → 既有完成事实边界 | 包含该wrapper内的调度/门禁/执行/必要等待；不称为工具函数CPU耗时；工具返回error结果也有耗时 |
| 根run | `RunObservation.start` → 应用完成既有必要结算后发布`run_finished` | 包含准备、模型/工具及收尾；不包含应用准入前等待，也不包含审批中断结束后用户离线等待 |

模型取消缺少框架error回调时，复用当前task done兜底终结；只按现有原子移除结束一次，其耗时止于实际观察到终结，不伪称供应商停止时刻。进程崩溃、仅有开始或缺少可信起点时，保留未确认状态和空耗时。

`GraphBubbleUp`等原来没有dispatch完成事件的控制路径不为补时长而伪造终态；审批结果合成的`approval_resolution`初版不计时，字段为空。根run未发布可信终局时同样不补造完成耗时。

并行模型/工具时长相加可以大于run耗时；子调用耗时也嵌在根run中。UI分别标注“模型累计调用耗时”“工具调度耗时”“run耗时”，不求和成任务实际墙钟时间，不计算排队/CPU/网络占比。

### 4.3 保存与查询

- 模型：已有`naiyue_model_invocations`新增可空`duration_ms`，沿当前schema迁移入口执行；存量行保持NULL，不由timestamp回填。start/end幂等检查覆盖新字段，不将缺失结束写成0。
- 模型汇总：现有total及模型/用途/根子bucket追加`duration`，结构为`{known_sum_ms: 十进制字符串或null, reported_calls: 整数, unknown_calls: 整数}`；unknown包含当前进行中、历史缺字段及未确认调用。known_sum不代表完整总时长。
- 工具与run：耗时进入原诊断白名单和有限事件payload；不新增工具用量账表。保留范围和缺口继续由现有run诊断摘要表达。
- 同步有限日志字段、运行事件JSON序列化、存储读写和类型定义；不向日志写请求/响应正文。
- 无持久存储时保留实时事件展示；历史查询按原“不可用”合同返回，不临时生成伪历史。

## 5. 119-02：模型SDK HTTP尝试

### 5.1 采集边界与明确限制

优先使用模型客户端的公开request/response hooks，复用catalog现有客户端拥有和关闭路径，分别适配httpx/httpx2及Ollama客户端。保留既有hooks、代理、TLS、timeout、连接池、流式行为和SDK重试参数；不导入会改重试参数的验收脚本，不patch SDK私有重试函数，不增加依赖。

[HTTPX官方hook合同](https://www.python-httpx.org/advanced/event-hooks/)规定request hook在发送网络前执行，response hook在是否读取响应体的决定之前执行；AsyncClient要求异步hook。因此这里观察的是**请求尝试开始与响应头返回**，而非完整HTTP成功。httpx2须用当前安装版本的实际源码和离线探针验证同等行为，不能把httpx文档自动当作另一依赖的证据。

仅覆盖已绑定本项目模型执行上下文的模型客户端请求；MCP、Web搜索/抓取、业务测试HTTP、模型列表发现等不混入。模型执行上下文缺失时记录安全采集诊断，不用“最近一次调用”或共享client对象猜归属。

### 5.2 新增有限事实

沿现有运行事件通道新增以下事件，不建另一条持久化管线：

| 事件 | data字段 | 语义 |
|---|---|---|
| `model_http_attempt_started` | `attempt_id`, `invocation_id` | 当前request hook观察到一次发送尝试；model/purpose从已关联的模型执行事实取得，不重复选模 |
| `model_http_attempt_finished` | `attempt_id`, `invocation_id`, `status`, `http_status`, `response_headers_ms` | `status=response_headers`时已取得响应头，HTTP码为100—599整数，耗时为开始到该hook；`status=unconfirmed`时未观察到响应头，后二者为空 |

attempt_id独立生成；外层session/run/source取开始时固定的publisher。response_headers_ms与duration_ms同样校验，但前者绝不命名为完整请求耗时。HTTP 429/503也属于“收到响应头”；200后流式失败仍保留200响应头事实，模型终态为failed/partial，不改成returned。

公开hooks没有统一的网络异常结束hook。模型终结时，对已知开始但未闭合的尝试标为unconfirmed，不能仅凭模型最终异常把每次尝试标成timeout/failed。异常类型和传播仍由已有模型执行终态表达；若需要每一次底层网络异常的精确分类，另行提出有实际证据的扩展，不在初版包装响应流或改SDK。

started/finished按attempt_id幂等配对；先完成一次HTTP尝试再由SDK决定是否重试。本功能不会发起重试、重放请求、读取响应体、调用raise_for_status或修改返回对象的业务字段。

### 5.3 关联方式及02实施前的探针门禁

现有RunServices只提供run/根子publisher，不能唯一表示同一run内并发模型执行；全局共享catalog也不能承载“当前调用ID”。118计划复用invocation context保存请求准备事实，该记录与callback执行身份不是同一层：一个逻辑准备/恢复流程仍可能产生主模型、摘要、重发等多个invocation_id。02先核对118落地后的上下文可达性与生命周期，能准确承载实际invocation_id则优先复用；不能直接把prepared记录里的逻辑model_call_id作为HTTP父ID。

直接给catalog传调用参数无法覆盖SDK内部重试。若已有上下文无法到达HTTP hooks，实际消费者才需要一个**窄的、仅用于观察的调用上下文**：callback开始绑定invocation_id和publisher，HTTP hook读取，模型结束及task兜底清理。新增机制仍须通过下列传播探针证明必要性与正确性；01不新增ContextVar。

优先验证当前`run_inline=True` callback与真实SDK请求链能否安全传递task-local上下文，再决定具体私有实现。不得只因有ContextVar就假定新task、流式迭代器或线程必然继承正确。02开工先跑离线探针，覆盖普通/流式、摘要/Auto、根子并发、同client跨会话、取消和callback缺口；验证通过后把实际绑定/释放方式及影响文件补入02实施记录，再写最小生产实现。探针失败则报告具体不成立的传播边界，先调整本节，不扩大为通用上下文框架。

约束已经确定：绑定只用于关联，不是执行必要条件；缺失/歧义拒绝错误关联。不得新增模型执行循环或改变公开API来强迫传播，不为同一run另建全量上下文服务。观察辅助状态必须有界，复用诊断已有容量约束和缺口表达，不保留Request/Response、body、凭证或无限尝试列表；结束、取消和关闭均释放已持有的有限记录。

### 5.4 保存与数量解释

尝试事件进入现有有限诊断白名单及有界存储。run查询按开始事实计数、按attempt_id配对，分别显示收到响应头和未确认；不以结束事件数当请求次数。没有采集能力的历史run显示不可用，不显示0；采集丢失/淘汰后明确“已记录”及缺口，不能宣称完整实际请求数。

本阶段不新增永久HTTP明细表、跨run HTTP累计表或供应商request-id。总量只在保留的诊断范围内汇总；模型用量页的长期模型次数和这些有限尝试数具有不同保留范围，不能直接相减计算全会话重试次数。

## 6. 正常与失败的完整链

### 6.1 正常例子：一次模型执行中两次HTTP尝试

以下是验收示意数据，不是本次真实供应商结果。

```text
用户提交 → SessionExecution创建根run R1 → RunObservation.start
  → 原图/请求准备 → ModelUsageCallback开始I1（model_id=M1，purpose=agent）
  → SDK request hook：attempt A1 → response hook：503，response_headers_ms=120
  → SDK按原策略退避/重试
  → SDK request hook：attempt A2 → response hook：200，response_headers_ms=80
  → SDK/适配器消费完整响应 → callback结束I1：returned，duration_ms=900
  → 原响应门禁/工具循环/收尾/必要保存 → run_finished：completed，duration_ms=1100
观察支线：原publisher → 用量/诊断订阅 → 各自存储 → 原应用查询 → CLI/Web
```

结果：模型执行1次，已记录HTTP尝试2次；模型耗时900ms，run耗时1100ms。120+80不是完整模型耗时，HTTP 503不直接决定最终模型或run失败。usage仅由callback实际返回数据规范化，不从两次HTTP推算。

若随后调工具T1，`aobserve_tool_dispatch`沿原dispatch→handler→ToolMessage路径产生现有outcome并附duration；若工具内部调用MCP，仍只观察该工具dispatch耗时，不把MCP RPC当作模型HTTP尝试。子Agent走同一callback/hook合同，publisher携带子source。

### 6.2 失败例子：200响应头之后流式断开

```text
R2 → I2开始 → A3开始 → A3收到200响应头，response_headers_ms=60
  → 消费流时抛出原异常E
  → 现有callback失败/兜底：I2 failed，usage partial或unavailable，duration_ms=400
  → 原响应错误链和应用必要收尾 → 原run失败/控制终局
  → 可选观察投递/写库；失败仅记录安全诊断，不替换E
```

返回/raise合同：成功原结果保持；业务失败继续传播同一个异常对象、args及原错误分类；停止/CancelledError/BaseException沿原控制合同传播，不能被普通观察异常隔离当作成功。HTTP响应头事实不能升级为模型成功，必要保存失败也不能作为普通观察故障忽略。

补充失败分支：

| 场景 | 记录及降级 | 执行合同 |
|---|---|---|
| 连接失败，没有response hook | 已开始尝试在模型终结时标unconfirmed，响应码/头耗时为空；模型保留真实错误类型 | SDK自行决定原有限重试；本功能不重放 |
| 用户取消，框架未回调结束 | 当前task done兜底最多结束一次模型事实，已知未闭合尝试未确认 | 原停止、清理及必要保存继续 |
| dispatch前失败/审批未执行 | 原boundary/outcome保留，可信dispatch终态可附耗时 | 不虚构工具函数执行或外部副作用 |
| 时钟/事件构造/日志/订阅异常 | 有限错误类型诊断；尽力保留其他可用字段，耗时可空 | 不覆盖结果或原异常，不改变调度 |
| 队列满/数据库只读/诊断关闭 | 沿现有capture缺口及错误日志；无可用历史时明确不可用 | 可选观察写入失败不使Agent失败 |
| 进程退出/历史缺开始或结束 | 原started/unconfirmed/采集缺口；无法确认的耗时为空 | 不补造完成、成功或0耗时 |

## 7. 119-03：查询与展示

复用原应用只读查询、SQLite连接与Web页面，不新增独立观测站点。已有API只追加有限字段，不删除原字段或改变鉴权/分页含义。

- Web用量页：在已有模型次数、用途和usage旁展示模型累计调用耗时及有/无耗时的调用数。未知显示空白或明确不可用，不填示例数据。
- Web诊断查询与展示：在原run列表/事件明细提供run耗时、工具dispatch耗时、HTTP尝试开始/响应头状态和耗时；保留根子身份及capture/保留范围说明。新字段经过原白名单、JSON和前端类型全链路接线。
- CLI：在现有`nyt chat`命令分派中增加只读`:usage`和`:diagnostics [run_id]`入口，桥接已有异步应用查询。无run_id列当前会话最近run；有run_id读该run有界明细，不新增第二套统计算法；精确命令帮助/分页呈现于03实施前说明。
- CLI实时事件展示沿用原消费者，不逐token打印HTTP信息；查询失败提供有限错误提示，不触发重新运行或修改会话。
- 诊断容量/TTL沿用当前配置，无新增配置文件修改要求；删除会话仍沿已有拥有者清理其事实，不能遗留新表或旁路缓存。

初版不画平均延迟/百分位、费用、剩余额度、任务进度预测或“预算耗尽”。若某run没有HTTP采集能力标记或已有缺口，无法区分“未采集”和“确实零次”时，宁可显示未知。02须增加有限的采集能力/缺口标记，03消费该事实，不从“查不到事件”猜0。

## 8. 文件范围与接线验收

以下为各切片拟影响的现有入口，不是00已经修改的文件清单。生产、测试和前端合计超过8份，按01—03分别实现/验证，避免一次跨全链大改。

| 切片 | 生产接点 | 同步消费者及验证接点 |
|---|---|---|
| 01 | `agent_runtime/model_usage_observation.py`、`events.py`、`tool_execution/observation.py`；`contracts/session_events.py`；`application/run_events.py`、`model_usage_storage.py`及当前schema入口 | 用量持久化/查询、诊断投影/存储、事件序列化；现有模型用量、tool/run、诊断及重启测试 |
| 02 | `model_access/catalog_lifecycle.py`及Ollama已有客户端拥有入口；callback观察和事件定义 | 诊断投影/查询、catalog关闭、现有SDK夹具；必要的窄观察辅助模块须说明职责与实际调用方 |
| 03 | 原应用查询/CLI异步桥和分派、`web/service.py`及原API | `frontend/src/usageTypes.ts`、`components/SessionUsage.tsx`及原诊断展示；CLI/Web/API/前端测试 |

新增字段必须同时核对生产者、事件payload注册、JSON、有限日志、诊断白名单、SQLite迁移/映射、查询、CLI/Web类型和测试。单文件超过500行时按现有职责拆分，不仅为行数增加转发层。公开方法不做breaking change，有限查询保留原边界校验和参数化SQL。

普通观测故障沿项目已有`observe/emit_event`合同处理：隔离普通Exception、输出安全诊断，不吞控制BaseException。执行和必要保存由原拥有者负责；不得因观察失败改变模型/工具选择或审批结果。

## 9. 分片与确定性验收

| 分片 | 交付 | 必须看到的证据 |
|---|---|---|
| 00，已完成 | 口径、现状、数据流、事件/存储范围、分片与排除项 | 文档结构、相对链接、差异检查；无产品实现 |
| 前置：Spec118 | 统一请求准备及单套callback模型事件 | 118待审边界已确认、实现与回归验收完成；新事件按invocation_id配对，旧事件不双发，消费者迁移完成 |
| 01 | 模型/工具dispatch/run耗时、模型持久字段和汇总、诊断字段 | 失败测试→最小实现→定向/全量回归与构建；真实主链事件和查询字段均接线 |
| 02 | HTTP尝试、可信模型归属、收到响应头与未知、采集能力/缺口 | 先完成传播探针，再TDD；503→503→200=模型1/尝试3；流式失败、取消、并发不串线；请求数/参数与加观察前一致 |
| 03 | 原CLI/Web只读查询和展示 | 实际页面/CLI消费真实离线运行事实；API身份/分页、未知/历史字段、捕获缺口、桌面/窄屏、构建和回归 |

01重点测试：控制时钟给出确定耗时；小于1ms与None区分；UTC跳变不影响单调耗时；returned/failed/cancelled及task兜底最多一次；摘要/Auto/根子归属；tool error_result/not_executed/GraphBubbleUp；run准备失败和必要保存后终结；旧库NULL迁移、重复/冲突事件、新旧查询；并行时长不被当墙钟总时间。

02重点测试：当前OpenAI/Anthropic/Ollama真实适配器接离线传输，覆盖同步客户端和异步主链；SDK有限重试仍由SDK持有；响应头200后body失败；无响应头网络错误不伪造精确attempt异常；重定向额外尝试；同模型跨run及根子交错、摘要/Auto；观察缺失不误归属；取消/关闭释放有限状态，已有hooks/代理/TLS/timeout/客户端关闭保持。

所有片均须做故障注入：时钟、hook、publisher、诊断投影、队列和存储故障不会覆盖业务结果/原异常或停止语义；测试末显式断言采集故障及主链结果，不能将测试检查器失败吞进观察隔离。无极紧sleep窗口，使用可控时钟、事件同步或状态轮询。

复用测试入口：`tests/test_model_usage.py`、`test_model_usage_paths.py`、`test_model_usage_storage.py`、`test_model_usage_queries.py`、`test_model_usage_restart.py`、`test_diagnostic_storage.py`、`test_diagnostic_application.py`、`test_web_model_usage.py`、`test_web_diagnostics.py`。新增测试名在各片实现时确定，不把计划中的断言报告为已通过。

实现完成后按项目既有命令执行受影响测试、全量后端/前端回归、TS/Vite及构建、分层/500行和`git diff --check`；涉及安装接点时走原wheel安装验证，不另建构建方式。离线夹具证明合同和接线；真实供应商、外部MCP和生产业务效果分别报告，不能互相替代。

## 10. 参照、决策依据与00证据边界

已有项目调用链核验使用以下固定版本，后续切片若更新参照应重新核对，不能称为持续最新：

- Pi `73f8de80bc87021375f8b525292ac07492253002`：`AgentSession.getSessionStats`消费会话条目中的usage；`_prepareRetry`处理项目可见的恢复/重试流程。借鉴“保留事实、区分累计和当前上下文”；不能据其auto_retry事件认定已覆盖供应商SDK全部HTTP尝试。
- AgentX `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`：`AbstractMessageHandler`记录开始时间，经LLM调用后由`buildModelCallInfo`组装调用信息，失败分支记录延迟。借鉴调用出口保留耗时/状态；不由局部链推断其具有统一任务预算或逐HTTP尝试账目。
- 本项目LangChain 1.4.2/core 1.6.6现有callback是根子、摘要、Auto共同执行观察点；不替换已确认的`create_agent`路线。HTTP公开hooks的边界见第5节官方来源；SDK重试/流式具体覆盖由02安装版本探针证明。

上述参照支持职责划分，本项目采用“已有callback＋客户端公开hooks＋现有两类存储”的最小路径；不是照搬其他项目运行时，也不是它们已经实现本文全部能力的声明。

00阶段历史边界：当时只新增本Spec并同步排期/交接，仅文档验证。2026-10-20用户随后确认118剩余决定并授权其分片实现；当前先完成118的独立实现/验收，之后才推进119-01，不能把118授权视为119已实施。当时119尚无产品改动；本片01后续授权及实际实现见第11节以后。不改依赖、真实配置或用户数据库，不提交/push/发布。

00文档验证：Python结构检查确认10个顺序章节、3个本地引用及2份索引链接、范围/状态/正常失败链均通过；`git diff --check`退出0。新文件使用`git diff --no-index --check /dev/null`检查，无空白错误输出；该模式可用退出1表示文件差异，不能将它误记为退出0。仅文档证据，不替代119-01—03的功能测试与构建。

## 11. 119-01实施接点（用户授权继续）

本片只做第4节耗时。正常链：已有callback/dispatch/RunObservation开始时记录单调起点→原执行/必要收尾→原终态追加duration_ms→原publisher/asdict序列化→模型用量或有限诊断→原只读API。失败链：计时的普通Exception只记录安全诊断并使duration_ms=None，继续发布原终态；原业务异常/取消/GraphBubbleUp及必要保存保持，不伪造控制路径的结束。

新增窄的contracts/timing.py纯函数模块：start_timer/elapsed_ms和duration字段校验，只有三个实际计时消费者（callback、dispatch、RunObservation）及三个终态payload校验消费者。三处重复的时钟隔离/64位范围规则集中于此，不新增服务、ContextVar、队列或配置。callback在原_active元组追加起点，原pop一次保证结束一次；工具用局部起点；RunObservation起点属于当前实例，子source沿既有实例独立记录，不与根run时长相加。approval_resolution保持None。

SQLite当前schema12，按原SessionStorage初始化顺序完成9/10/11/12表迁移后再调用model_usage_storage的duration迁移到13；不得在usage的旧initialize中提前升版本而跳过诊断/回复表。仅给naiyue_model_invocations加可空列与非负整数约束；旧行NULL，不按timestamp回填。save_event终态幂等字段包含duration_ms；read_summary沿原Python整数累计，所有bucket追加duration={known_sum_ms:str|null,reported_calls:int,unknown_calls:int}。诊断投影保留可空duration_ms，根run列表摘要也保留，历史无字段只读显示None、不写库。

影响events、contracts/session_events、model_usage_observation、tool_execution/observation、application/run_events/model_usage_storage/session_storage/diagnostic_persistence/diagnostic_storage，以及frontend usageTypes与通用RuntimeEvent可选字段；asdict原序列化直接携新字段。01只同步类型，不提前增加03页面/命令。旧schema夹具若从新库降级构造，必须移除13列以保持真实旧结构；修改测试helper时明确说明，不放宽断言。

先按控制时钟/字段/失败/迁移/幂等/汇总写红灯，再最小实现；验证实际主链成功、失败、取消、摘要/Auto/根子和必要保存后的根终态；完整回归、标准构建及本机干净安装。仅操作临时测试库/候选，不打开或迁移真实用户库、不升级依赖、不commit/push/发布，02/03保持未实现。


## 12. 119-01实际接线与验收（2026-10-20）

### 12.1 当前主链

| 事实 | 数据生产与结束位置 | 消费与状态边界 |
|---|---|---|
| 模型 | ModelUsageCallback.on_chat_model_start将单调起点追加到原_active记录；_finish按invocation_id原子pop后计算duration_ms | callback返回/失败/取消及原task done兜底共用一次结束；不包含118请求准备，不改变returned与门禁accepted的区别；原ModelUsagePersistence→SessionStorage.save_model_usage_event→SQLite列→read_model_usage/aget_model_usage |
| 工具 | aobserve_tool_dispatch局部起点，原dispatch/handler完成或异常出口追加耗时 | 仍保留success/error_result/not_executed/exception/outcome_unknown及原异常身份；GraphBubbleUp仍只有原开始事实；approval_resolution为None；原publisher→有限日志/诊断及asdict实时JSON |
| run | RunObservation.start记录实例起点，SessionExecution._afinish_run完成子任务结算/控制清理/必要持久化后调用finish | 根与子RunObservation实例各计自身时长；根终局不包含审批中断后的用户等待；原有限诊断的summary_json/事件保留duration_ms，历史缺字段只读返回None |

正常例：SessionExecution.arun→start→模型callback/工具dispatch→observation.result→finally _afinish_run→settle_subagent_drivers/_cleanup_run→SessionPersistence.afinish→release_run→RunObservation.finish。受控测试在afinish处等待，等待期间无run_finished且调用未返回；实际保存结束后将单调时钟推进到86.9ms，根终态得到86ms。

失败例：模型抛原TimeoutError E→原_on_failure→_afinish_run→必要保存→run_finished failed→向外仍抛E；保存同时失败时仍不替换E。仅保存失败继续沿既有合同抛session_save_unconfirmed，__cause__仍为底层保存异常，不能当成可选观察故障。时钟普通故障则安全记录duration_observation_failed、duration_ms=None；工具原业务异常及取消身份保持。观察不会决定停止、审批或重试。

### 12.2 存储、旧入口与兼容

SessionStorage按原顺序初始化usage9、diagnostics10、reply11及regeneration12后，再调用model_usage_storage.migrate_duration到13；没有提前跳版本。新增可空INTEGER列，旧记录保持NULL；同终态重复保存不变化、duration冲突沿usage_terminal_conflict拒绝。根/子/模型/用途/未知模型全部使用原bucket累加，duration只累计已知值，Python任意精度总和返回十进制字符串；进行中/历史缺失/崩溃未确认仍计unknown_calls。

没有新增事件类型、ContextVar或计时服务，没有平行旧计时入口要保留；contracts/timing.py是三处真实计时/三类终态校验的窄公共函数。原压缩专用summary_duration_ms职责保持，不拿它代替callback事实。既有asdict序列化无需新增包装，CLI/Web展示未提前实施，只同步了前端可选类型。

测试基础设施仅修正legacy_store_schema.remove_reply_schema：从当前库构造v10以前夹具时移除13的duration列，再保留原reply剥离步骤；四个迁移测试的目标版本改为13，迁移数据/异常/状态断言保留。不在生产迁移中容忍“版本旧、列已存在”的不真实夹具。

### 12.3 本机证据与限制

新增49项后端测试；首批43项在旧实现上观察到预期红灯，随后补6项保存/故障/历史组合验证，并加强现有task done、原生SDK根子/摘要/Auto及SQLite/API查询断言。定向命令：

```bash
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests/test_call_durations.py tests/test_duration_storage.py tests/test_model_invocation_boundaries.py tests/test_model_usage_paths.py tests/test_model_usage_persistence_paths.py tests/test_model_usage_storage.py tests/test_diagnostic_storage.py tests/test_session_thinking.py tests/test_chat_history_boundaries.py --tb=short
```

实际结果：123 passed in 2.35s（build/spec119-01/focused-final.log）。首轮定向回归发现旧schema目标/夹具需要同步；新增保存故障用例最初将已有包装合同误写成底层异常直接返回，追踪SessionPersistence.afinish后修正为严格检查包装消息和原cause，没有修改生产异常链或放宽业务断言。

前端46文件367项通过；npm --prefix frontend run build的TS/Vite成功（321模块，原chunk大小提示仍存在）；标准macos-arm64 wheel构建成功，289个归档条目，159份生产Python源码逐字节一致且AST/500行检查通过。compileall、pip check、git diff --check通过。源外临时干净环境安装后native tools/frontend API/find-grep/审批恢复/SQLite重开/CLI通过；MCP合成工具8次、进程全部退出、0次供应商请求。10场景消息/图状态/保存边界探针通过。架构、安全及主审对抗复核无发现。证据集中于build/spec119-01；全量后端命令PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests --tb=short实际结果为**3406 passed in 220.57s (0:03:40)**（backend-full.log），本片完成本机验收。

119-01收尾时的下一片约定（02现状见14/15节）：先验证当前run_inline callback到真实SDK request/response hooks的执行身份传播，再按同客户端跨会话/根子并发/流式/取消及SDK重试探针结果确定绑定和清理接点。初版只记尝试开始、收到响应头或未确认；保留原重试/网络配置/关闭语义，不计响应体完整传输。119-03展示和硬预算保持未实施。本片未改依赖、真实配置或用户库，未提交/push/tag/发布；本机离线与安装证据不替代真实供应商、其他平台原机、业务效果或用户掌握证据。


## 13. 119-02具体方案（已实施）

### 13.1 本轮核对与设计前提

方案讨论时仅源码/官方文档核对，未运行传播探针或修改产品；后续授权后的实际证据见第14/15节。当前安装LangChain1.4.2/core1.6.6、OpenAI3.19.2、Anthropic1.9.0、Ollama0.6.3、httpx0.28.1/httpx2 2.13.1。ModelUsageCallback已run_inline=True；RunServices只有run级服务，118的prepared记录只有逻辑请求事实，两者均不能唯一标识并发SDK执行。

本机core的AsyncCallbackManager.on_chat_model_start顺序await inline handlers，但agenerate随后通过gather调用实际模型；on_llm_end/on_llm_error的shielded会copy_context并创建task。因此开始、请求和结束不保证同task；禁止把开始时的ContextVar token拿到结束task强行reset，或把共享客户端上的“最后一次调用”当当前身份。这是源码事实，具体传播正确性仍须探针证明。

当前catalog_lifecycle已拥有OpenAI的httpx与Anthropic的httpx2同步/异步客户端；Ollama登记SDK内部两份httpx客户端并统一关闭。优先在这些已拥有的实例上追加公开hooks，保留原hooks次序、网络参数及关闭拥有者，不按请求重建客户端、不改共享池。httpx2当前源码同样在send_single_request前后await request/response hooks；这不是用httpx文档代替另一个库的运行证据。

### 13.2 实施顺序与接点

1. **先完成离线传播门禁。** 使用真实已安装适配器与MockTransport，覆盖OpenAI Chat Completions/Responses、Anthropic及Ollama，普通/流式、摘要/Auto、根子交错、同客户端跨会话、同步SDK回退及取消/task done缺回调。断言hook读到的执行ID与callback ID一致，结束后继承的旧上下文失效；不向供应商发请求，不改超时/重试参数。探针不成立则停在门禁报告真实分支和失败条件，不发布无法保证归属的事件；通过后先把实际绑定/清理接点补入本Spec再TDD实现。
2. **加入窄的调用观察上下文与hooks。** 建议新增model_access/http_observation.py，只负责本次执行的轻量记录、task-local绑定、同步/异步HTTP hooks和有限待闭合记录；不import运行层或应用层。Runtime的callback仍生产事件payload，通过当前调用的观察函数交给下层，不另建总线。记录只有执行ID、开始时固定的发布归属、单调起点/尝试ID及关闭状态，不持有请求/响应对象、正文、凭证或应用对象。原_active的同一pop终结关闭记录，继承副本读到closed后拒绝关联；清理不依赖跨context reset。HTTP request.extensions可保存观察私有的有限配对标记，每次request hook生成新attempt_id，包括SDK重试复用/复制Request及重定向；不写网络header或业务payload。结束/取消/关闭排空待闭合记录，释放发布归属引用；上限复用当前诊断max_pending_events，满时只标采集缺口，不改变模型执行。
3. **接两类事实与既有诊断。** 沿第5.2节新增model_http_attempt_started/finished，结束只用response_headers或unconfirmed。收到响应头的尝试立即结束，模型终结时对剩余已知尝试标unconfirmed，之后才发布原model_call_finished，按身份至多结束一次。同步事件注册、有限日志、诊断白名单/summary_json、JSON、前端类型和只读查询，不新建表或HTTP会话总账，schema保持13。新增有限采集标记：run_started追加可空http_observation_version（本版本1），model_call_finished追加可空http_capture_status（complete/incomplete/unavailable）；诊断摘要依开始事实维护已记录尝试/响应头/未确认计数及完整性。旧run无版本标记为不可用；未支持的模型、缺失归属、观察限额或诊断本身缺口不能显示成可信0；明细分页不改变整run计数。complete仅描述采集边界完整，不证明请求到达供应商或响应体完整。
4. **故障/组合回归与安装。** 验证原请求次数/参数/重试配置/结果和原异常身份，hooks普通故障仅安全诊断与观测降级；停止/取消与必要保存仍由原拥有者处理。运行新增定向测试、全后端/前端、TS/Vite、compile/pip/diff/500行及标准wheel/源外本机安装；0供应商请求。03页面/CLI显示、本轮硬预算及项目侧重试保持未实施。

调用关系：应用装配传入既有诊断限额 → catalog准备并挂hooks / SessionService安装callback → callback开始实际I并绑定有限记录 → SDK公开hook生成A与响应头事实 → callback或task done关闭剩余A/失效记录 → 原publisher/有限诊断 → 原只读API。catalog关闭继续由原aclose_models拥有；观察辅助模块不关闭借用客户端，不控制运行。

影响超过8份文件：catalog_lifecycle及catalog准备入口、model_usage_observation/events、新窄辅助模块、contracts/session_events、run_events、diagnostic_persistence/diagnostic_storage、异步两装配入口/SessionService及前端类型；测试复用当前原生SDK/根子/摘要/Auto、诊断/关闭夹具。必要公开查询仅追加字段，原方法签名/鉴权/分页含义保持，新增payload字段带默认值。使用现有依赖和诊断配置；不需要新增账号、凭证、MCP服务或真实环境配置。

### 13.3 验收例子与回退

- 正常503→503→200：同一I，三个不同A，三条response_headers事实；原模型returned/usage保持，SDK总调用数和入参不因观察变化。重定向额外发送也按新A记录，不一概称为重试。
- 200响应头后流式断开：A仍为response_headers/200，I为原failed/partial或unavailable，向外原异常E；不把HTTP头返回升格为模型成功。
- 没有响应头的连接失败/取消：已知A在I终结时unconfirmed，http_status/response_headers_ms=None；原SDK决定重试/取消，不从最终E猜各次网络错误。callback缺口沿原task done关闭一次。
- 两会话共用客户端与根子交错：归属按各开始记录固定的session/run/source/I；晚到response、重复hook、关闭和过期上下文均不得写入其他执行。不依事件相邻位置配对。
- 历史/零次/采集缺口：有本版本标记且完整、确实无模型请求的run可显示已记录0；无标记或有缺口保持不可用/部分记录。诊断淘汰后不承诺全会话尝试累计。

本片无新SQLite迁移，回退时移除hooks/新观察绑定并保留既有诊断JSON；旧读者忽略未知事件/字段，不能重解释为旧模型事实。传播门禁失败只停本片探针，不改用户模型配置来“让测试通过”。确认后按上述顺序连续执行，只有前提不成立或涉及新语义才返回讨论。

### 13.4 当前参照边界

Pi本轮本地HEAD为a69bef789bc95abf0acee16f7b4660b70b650bb9（不同于第10节历史固定版本）。实际链为agent-loop.streamAssistantResponse→streamFunction→api/openai-completions的client.chat.completions.create(...).withResponse→retryProviderRequest→onResponse/流事件。该适配器显式将SDK maxRetries设0，外层provider-retry决定重试，onResponse位于该重试函数返回之后；不能据它推断每次HTTP尝试均已观测。本项目本轮保留SDK原重试，不迁移其重试策略。

AgentX本轮HEAD仍3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f：AbstractMessageHandler.processSyncChat→syncClient.chat→buildModelCallInfo(callTime)→TracingMessageHandler.onModelCallCompleted→traceCollector.recordModelCall；失败沿原catch/reportCallResult/onChatError。这条链记录模型级耗时与结果，不能单凭它证明SDK每次HTTP尝试。借鉴分清观察边界，不移植Java运行时/业务正文日志。

官方参照为[HTTPX公开hooks](https://www.python-httpx.org/advanced/event-hooks/)及[LangChain run_inline合同](https://reference.langchain.com/python/langchain-core/callbacks/base/BaseCallbackHandler/run_inline)；task/context和httpx2具体顺序以上述本机固定版本源码为准，方案讨论时传播门禁尚无运行通过证据，当前门禁结果见第14节。


## 14. 119-02传播门禁与实施接点（用户已授权）

14项离线探针通过（propagation-probe-final.log，14 passed in1.03s）：真实OpenAI Chat Completions/Responses、Anthropic/httpx2、Ollama，ainvoke/astream，同步SDK回退，三种摘要/Auto标签下同客户端跨会话/子来源交错，以及缺结束回调的task done。开始记录随请求上下文传播，shielded结束来自不同task、闭合共享记录后原task读取到closed；原_active清空。首两轮是新夹具错误读取publisher身份属性，改为核对发布归属对象与实际事件后通过，不是生产缺陷或传播失败。该探针不是产品TDD红灯；后续功能测试另先观察缺实现红灯。

实际接线采用窄HttpCallObservation记录及每个已拥有HTTP客户端的观察token，ContextVar只保存当前调用记录。callback按实际invocation_id创建，原_active一并拥有；同一pop终结记录后再发布模型结束。记录闭合清空emit/客户端引用和待闭合尝试，跨task不reset token；缺失或同task多输入/重入导致的歧义均停止关联并标incomplete。事件payload仍由Runtime构造，下层不导入运行/应用层。HTTP clients在原catalog准备路径挂公开hooks，当前模型的实际客户端token在调用开始时核对，避免运行中被替换为无hook客户端后把未采集误报为0。

每个token用弱引用跟踪当前调用，原aclose_models在关闭客户端前只结束对应观察状态、不代替关闭真实客户端；正常调用闭合主动移除弱登记，闭合记录不保留publisher。每次request hook写入私有extensions配对标记，response按该标记找记录、验证token并原子移除尝试；不持有Request/Response或正文。计数限额来自settings.diagnostics.max_pending_events，在两种异步装配→SessionService→callback的内部可选关键字传递；外部应用/查询签名不变。支持集合和限额只决定观察，普通故障安全诊断并降级，不改变业务结果/原异常/停止或必要保存。

诊断summary_json追加http_attempts={status:complete|partial|unavailable,observed_attempts:int|null,response_headers:int|null,unconfirmed:int|null}；run_started的http_observation_version=1提供采集版本，模型结束http_capture_status给出缺口。仅对实际保存的开始/结束事件递增，重复sequence沿原逻辑忽略，分页返回同一run摘要。根子统一计入根run已有诊断归属，source保留；无历史标记为unavailable，缺终局/采集缺口/缺模型采集状态则partial，不从没有事件猜0。SQLite无新表/迁移，schema13保持。先补字段/双hooks/幂等/限额/取消/关闭/诊断红灯，再最小实现和原生SDK组合验收。


14节补充：故障注入发现完全缺失callback时，只有HTTP缺归属日志不足以让run查询区分“未采集”和0。复用同一个HTTP ContextVar，在RunObservation.bind内放入弱引用的run缺口回报方法，调用记录继承该弱引用；普通无归属/准备失败时只标记该run的观察缺口。run_finished追加可空http_capture_status供原诊断降级；不新增事件/ContextVar/总线，闭合调用仍不强持有publisher。根/子run各自报告，原run Started版本合同和必要保存顺序保持。该分支已有缺字段红灯后再接线。另原SDK/适配器本来将流ReadError包装为OpenAIConnectionError；用无观察对照严格核对原异常cause链，不修改此必要分类行为。


14节复核补充：安全复核复现Request复制旧extensions且新调用限额已满/无上下文/禁用时，旧标记可使B的响应关闭A的尝试。新增三个独立Context的回归均先红灯（red-copied-marker.log）；每次request hook现先清除私有旧标记，再尝试为本次请求写入新标记，未采集时保持无标记。hook注册失败也已有红灯（red-install-hooks.log），注册自身普通故障安全降级，catalog创建与原模型调用保持。上述修改后必须重建最终候选并重新完整回归，早期wheel/全量结果不作为最终源码验收。


## 15. 119-02实际主链与验收（2026-10-20）

### 15.1 已接线的正常与失败链

应用两个正式异步装配入口读取现有diagnostics.max_pending_events→SessionService内部可选关键字→install_model_usage_callback.configure_http。原catalog.prepare_async_clients→prepare_owned_clients给OpenAI/httpx、Anthropic/httpx2、Ollama已有同步/异步客户端追加公开hooks；不新建每调用连接，不改变原网络/重试配置。callback弱引用当前配置模型，开始时读取其实际客户端token；替换成无hook客户端会得到unavailable，而非可信零。

RunObservation.bind在同一个HTTP ContextVar放入弱run缺口回报；ModelUsageCallback开始实际I→_http_call生成HttpCallObservation并绑定到继承上下文→原_active持有其生命周期。SDK request hook先清除Request复制来的旧私有标记，再生成新A，保存单调起点并发model_http_attempt_started；响应hook按本次extensions中的弱owner/A/token找记录、原子移除并发response_headers终态。Scope内待闭合尝试按每个invocation的已配置限额保存，收到头立即移除；溢出只标缺口。Runtime构造payload，下层不依赖Runtime/Application。

原callback _finish按I一次pop→计算01模型耗时→关闭观察记录、剩余A发unconfirmed→原model_call_finished带http_capture_status。shielded回调可在另一task完成，原task继承副本读到closed后失效；release无论正常/观察关闭失败都清除pending/emit/token引用。客户端token只弱登记当前scope，原aclose_models关闭真实客户端之前先结束其观察记录，真实关闭失败仍沿原必要资源合同返回。

真实离线SDK组合：主Agent→Auto fast/detailed→工具→子Agent→主回复→下一消息→手动摘要，8次callback与8次HTTP开始/响应头逐个按I/source配对；原模型用量、工具副作用次数、上下文摘要及根子归属断言保留。持久组合沿原DiagnosticPersistence→SessionStorage→summary_json/有限事件→aget/list_diagnostic_*读取，SQLite重开与模型用量读写保留。

失败例：先收到200头再读流抛E，头事实保持200/response_headers，模型failed，SDK/适配器原包装及cause链保持。完全缺callback时hook不猜I、不造attempt，通过弱run回报使run_finished携incomplete；缺归属、pending限额、准备/关闭/注册故障均安全降级，结果或原异常不变。原权限/停止/取消、GraphBubbleUp及必要保存时点不由本机制处理。

### 15.2 查询、保留与迁移收尾

新增两类HTTP事件后当前注册类型32；原4类上下文操作集合及模型callback事件合同保持。RunStartedData追加可空http_observation_version；ModelInvocationFinishedData与RunFinishedData追加可空http_capture_status；新HTTP payload严格校验ID、有限状态、100—599整数状态码和可空非bool非负int64头耗时。日志/JSON/诊断白名单/前端可选类型已同步，日志不含URL/header/body/凭证。

诊断http_attempts只按实际保存的事件递增，重复sequence不重复计数，pending model计数是summary内部有限状态，查询时不向外暴露；分页返回相同整run摘要。完整零、历史unavailable、缺模型结束/采集缺口partial分开；unconfirmed表示未见响应头，不伪装精确网络失败。计时为None不填0。没有新表或新schema，13保持；HTTP不写入永久会话用量总账，不把有限诊断淘汰后的空缺当成0。

没有旧HTTP观察入口要删除：本片新增一个下层窄模块，有callback/hooks/catalog关闭及弱run缺口的实际消费者；原模型usage/summary_duration_ms/必要执行与保存链保留。测试基础设施增加离线SDK夹具，替换的是测试传输，保留实际已拥有客户端、hooks/配置/关闭路径。增强原EventSequenceChecker的HTTP/I配对与模型结束前闭合约束，正常观察缺口在测试末明确拒绝。两个既有SDK组合测试为其原本借用的MockTransport显式挂hooks后保留原断言并增加HTTP断言，未全局改变旧夹具；旧上下文事件测试精确总数30→32，原操作集合不放宽。

### 15.3 验证记录与下一片

新增69项后端（14项传播探针、55项功能/故障/组合）。首41项功能测试在旧实现上预期红灯；准备/关闭隔离、缺callback的run缺口、hook注册失败和复制标记的独立Context分别补红灯再修。Probe夹具最初误读publisher属性、流失败用例最初误写SDK原包装预期，已按实际公开/源码合同严格修正，不改变生产SDK语义或放宽原断言。测试末核对事件/归属/缺口，观察隔离不会吞掉测试断言。

最近定向86 passed in3.91s；此前广定向234 passed in4.91s。首轮全量3534 passed/1 failed是旧registry总数仍30，已精确同步32；最终全量**3539 passed in224.45s**。该全量包含并行Spec120，不能把相对119-01的总数增长全部归于本片。前端46文件367 passed，TS/Vite321模块成功；160份生产Python的AST/500行、compileall/pip check/diff通过。最终标准Mac ARM64 wheel290个归档条目，源码字节/最终测试启动指纹一致；源外干净安装的CLI、Web API、原生工具、审批恢复、SQLite重开、MCP新增/根子复用/启停/TTL/冷历史通过，8次MCP合成调用、所有进程退出、0次供应商请求。消息/图状态/保存边界探针10场景通过；架构无发现，安全复核发现1项复制标记污染并有3项红灯修复、最终复核无新增发现。证据build/spec119-02。

复现命令与最终报告：

```sh
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec119-02/wheels-final
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec119-02/wheels-final --output build/spec119-02/install-final.json
```

[后端全量日志](../../build/spec119-02/backend-final.log)、[前端测试日志](../../build/spec119-02/frontend-tests.log)、[构建日志](../../build/spec119-02/frontend-build.log)、[安装报告](../../build/spec119-02/install-final.json)、[源码一致性](../../build/spec119-02/source-final-check.json)、[复核记录](../../build/spec119-02/review.json)。最终wheel SHA256：`913b51ae93dba00b020d531f03af3805e049d181d1b4ce3dfe673979f81cea8d`；application SHA256：`df227bf393753ab81d1fc557d535284ab3f96b10da61bfdd91e5a75562f76573`。

119-02收尾时的下一片约定（03现状见16/17节）：119-03先说明原CLI/Web的只读查询与显示：模型累计调用耗时、run/工具耗时、已记录HTTP尝试/响应头/未确认、历史未知及采集缺口。当时前端只同步类型；不把只有API的诊断能力描述成已有完整页面。先核对实际入口与分页呈现，再经用户确认实施。本片不提交/push/tag/发布，不升级依赖或改真实配置/用户库，保留并行Spec120与学习改动；离线SDK/本机安装不代替真实供应商、其他平台原机或业务效果。

## 16. 119-03实施合同（2026-10-20，用户已确认）

### 16.1 范围与接口

沿用原会话侧栏及CSS Modules/现有主题token，新增第四个“运行诊断”标签；不增加顶层工具按钮。会话累计总览、用途/根子/模型分组显示duration.known_sum_ms、reported_calls、unknown_calls；字符串大整数不转浮点。显示“已记录模型调用耗时”，并说明并发调用耗时之和不等于run墙钟时间。None/旧缺字段显示未提供，真实0保留0。

新增SessionDiagnostics组件与diagnosticTypes，只读消费现有GET usage、diagnostics列表与run明细API。列表显示时间/身份/状态/run耗时、HTTP已记录数量/响应头/未确认；明细按原sequence显示模型/工具/HTTP及其他已保存有限事件，保留source和关联ID。HTTP摘要取后端整run结果，不按当前页推算；收到200与模型failed可同时展示。事件跨页不猜丢失，不重建另一套配对/统计服务。

CLI在原chat分派新增:usage [--after-model <model_id>]、:diagnostics [--before <cursor>]、:diagnostics <run_id> [--after <sequence>]；拒绝不兼容、重复、非法游标参数。下一页打印可复制命令，shell词元使用标准库安全引用。AsyncCliApplication新增三个只读桥接，沿原loop调用aget_model_usage/alist_diagnostic_runs/aget_diagnostic_events；模型页20、诊断页50是有界查询默认值，Web继续消费其既有分页配置。--no-session明确查询不可用；不新增临时统计、配置、依赖、事件、表或schema迁移。cli.py仅新增分派，不超过500行；解析/格式化置于cli_observation.py。

### 16.2 正常与失败链

正常：页面点击标签→WebApi携原鉴权与session/run/游标→原WebService作用域校验→原AsyncPersistentApplication只读查询→原SQLite返回有界页/整run摘要→页面显示；CLI→现有_submit→相同应用查询→文本显示。默认不拉全，不自动轮询；用户刷新及当前根run新终态触发一次查询，非本会话/token更新不触发。刷新当前明细回到该run第一页；新run完成时保留选中run，列表刷新回首页。

失败：读取异常/错误身份/返回旧会话或旧run→当前查询显示有限错误及刷新入口，取消旧请求且忽略晚到结果，不影响任务/审批/保存。diagnostic_run_not_found明确“该运行诊断不可用”，不推断不存在调用；历史HTTP unavailable不填0，partial标“采集不完整”，完整零保留。查询原模型不调用、不加载冷图，不使用必要执行状态推算观察结果。诊断仅保存有限字段，无URL/header/body/凭证；React文本转义，CLI控制字符和换行转义，不直接打印原异常体或未筛选payload。

### 16.3 文件与验证

本片涉及超过8份生产/测试/文档文件：CLI原分派/异步桥及新cli_observation，前端App/ContextPanel/SessionUsage/WebApi、诊断类型/组件/样式及共享时间格式化；新CLI/前端/API组合测试和原侧栏四标签键盘测试。后端查询及存储语义保持。新小模块都有当前直接消费者；不存在待保留的旧诊断页面入口。

先写失败测试，再最小实现。覆盖正常/空/0/旧值/大整数、HTTP partial/unavailable/200后模型失败、分页摘要不变、旧session/run结果及取消、读失败重试/隐私、CLI解析与no-session、异步桥冷读、在途查询不停止Agent。最终跑后端全量、前端全量/TS/Vite、生产AST/500行/compile/pip/diff；离线真实SDK→SQLite→API/CLI→页面组合、1440桌面/375窄屏与键盘/溢出检查、Mac ARM64标准wheel/源外干净安装。实施中不改真实配置、用户库、依赖或并行Spec120/课堂文件，不提交或发布。

## 17. 119-03实际接线与验收（2026-10-20）

### 17.1 当前主链与职责

CLI在原chat输入驱动的未知命令分支之前分派两个只读命令，使用原display.write；原AsyncCliApplication._submit在既有loop调用三个原应用查询，SessionStorage仍拥有SQL连接/读事务。cli.py当前500行，新增cli_observation专责词元/参数校验及有限文本格式化，没有另建执行、模型调用、存储或统计入口。解析拒绝重复/不匹配参数、非法游标；未持久化时明确不可用。查询结果先完整检查session/run/每条事件身份并准备输出，再展示；原错误只记安全类型，返回有限提示。CLI换行/ESC及Unicode控制字符转义，下一页命令通过shlex.join引用，标识不会因恰与状态/用途同名而被翻译。

App→原ContextPanel增加第四个标签，保持原入口/标题/颜色/字体；Home/End/方向键动态遍历四项。会话累计SessionUsage消费后端原duration字段，总览及所有既有分组显示耗时与覆盖/未知次数；共享formatDuration只做整数显示，BigInt保持大数字符串精度。旧字段缺失不填0，模型并行合计不宣称run墙钟耗时。

SessionDiagnostics在标签打开时挂载→WebApi携原鉴权/身份/游标→原WebService校验工作区/session→AsyncPersistentApplication原只读方法→原SQLite有界页与summary。列表/明细一次一页替换，不拉全/累积缓存/轮询；HTTP摘要直接读整run值，不按当前页重算。选中run/页/revision/session构成查询身份，cleanup中Abort并用active防晚到；响应summary及events身份也校验。新根终态只刷新一次，明细保留选中run并回第一页；其他会话/token更新不刷新。前端不逐token打印尝试，也不读取未筛选payload正文。原鉴权、API参数、SQL与存储/schema13保持。

正常组合沿实际SDK→callback/hooks→原用量/诊断写入→SQLite→API/CLI/页面。失败组合收到200之后模型failed仍分开显示；无历史/不可用/partial分别展示，缺记录不猜无调用，读失败只影响当前面板。诊断保留/页边界不会被当执行配对失败；原模型/工具/审批/停止/保存消费者均保留，无旧诊断页面需要迁移删除。新增诊断组件/类型/样式、共享时间格式化和CLI模块均有当前直接消费者，包内确认新Python模块。

### 17.2 测试与本机结果

新增29项后端（CLI26＋SDK/查询组合3）和18项前端（耗时3＋诊断15）。先确认25项CLI/3项耗时预期红灯，诊断测试在缺组件处红灯；标识恰与状态/用途同名时错误翻译的CLI/Web两个回归另经红灯后修复。其他补充组合不冒称逐项TDD。原会话累计键盘测试迁移为四标签，仍检查End/方向键/懒加载；缓存未知值与新增耗时未知值各自定点断言，未放宽为任意一个未知。新CLI明细测试补齐真实event身份字段，保持严格身份校验。

定向后端68 passed in3.21s，最终CLI/真实SDK/Web组合31 passed in2.54s；最终全后端**3568 passed in228.03s**，前端**48文件385 passed**，TS/Vite324模块构建成功（保留既有大chunk提示）。161份生产Python的AST/500行、compileall、pip check及diff通过。原消息/图状态/保存边界探针10场景通过。架构、安全专项与本片对抗复核无遗留发现。

真实离线SDK的503/连接错误→重试→成功两组保持1次模型执行、3次HTTP尝试，事件逐页读时整run摘要不变；CLI在既有异步桥实际运行后查询，不再调用模型、不写库。Web组合初次失败源于旧测试catalog的MockTransport被新catalog拥有客户端替代，最小探针证实；只把离线传输装到应用实际拥有的客户端，hooks/配置/关闭不变，原断言严格保留，组合随后通过。临时UI脚本初次缺create_builtin_tools必要参数已按现有签名修正，不改产品。

本机浏览器由Playwright扩展不可用切换到Codex内置浏览器完成。临时正式应用真实执行两次根run、3次离线SDK请求及一次Read，从原SQLite/API读取页面；1440×1000和375×812无页面/诊断内容横向溢出，四标签键盘导航及翻页通过，console warning/error为0；已恢复视口、关闭临时页面和服务。截图不构成真实供应商/业务效果证明，实际供应商请求0。

复现命令：

```sh
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec119-03/wheels-final-current
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec119-03/wheels-final-current --output build/spec119-03/install-final-current.json
```

证据：[后端全量](../../build/spec119-03/backend-full.log)、[前端全量](../../build/spec119-03/frontend-full.log)、[构建](../../build/spec119-03/frontend-build.log)、[组合测试](../../build/spec119-03/integration-final.log)、[CLI实际输出](../../build/spec119-03/cli-output.txt)、[UI检查](../../build/spec119-03/ui-check.json)、[桌面明细](../../build/spec119-03/desktop-events.png)、[窄屏明细](../../build/spec119-03/mobile-events.png)、[窄屏累计](../../build/spec119-03/mobile-usage.png)、[复核](../../build/spec119-03/review.json)。

最终Mac ARM64 wheel291个归档条目、161份生产Python逐字节匹配最终测试启动指纹，前端资源与最新README元数据也一致，见[源码一致性](../../build/spec119-03/source-final-check.json)。[源外干净安装](../../build/spec119-03/install-final-current.json)的CLI、Web API、原生工具、Find/Grep、审批恢复、SQLite重开、配置和MCP全部通过：8次合成MCP调用、全部进程退出、0供应商请求。更新README后重新生成并验证最终候选，前一个仅用于早期验收，不混作最终产物；最终wheel SHA256为`ad939e5b7d9f43509148ee57496bca9f167cedbfaa9fa55aed6dbf9e0f2650d9`，application SHA256为`655cb467fdf27971e148f8d7045799b541bb6d2d4e929ebc08066d7d72364588`。构建/安装均退出0，未向PyPI或Git远端发送发布操作。

### 17.3 阶段收尾

119纯观测00—03收尾，04/05硬预算仍在本轮之外。HTTP仍为有限run诊断，不新增任务身份或永久会话HTTP总账，不由观测控制执行。按当前future-work顺序，下一事项为“安全工具重试”，先讨论只读/幂等/未提交证据及重放边界，经用户确认后另写Spec实施。本片未改依赖、真实配置或用户库，保留前序118/119、并行Spec120及学习改动，未提交/push/tag/发布。真实供应商、其他平台原机、生产业务效果及用户独立掌握证据分别验证。
