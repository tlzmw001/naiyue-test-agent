# Spec100：真实供应商有界验收

2026-10-17：用户确认首轮使用已有网关配置的`heiyu-gpt-5.5`完成核心验收；图片用Pillow本地生成，不接生图API。聊天中提供的生图凭证不保存、不使用、不复制。此处仅记录用途边界，不记录凭证。最新指令“继续，直接做到完成验收”已授权实现并执行100B—E，保持8/18/24/10及总60次发送上限，不自动重试失败场景。

## 1. 目标、阶段与证据

验证当前适配器、工具、权限、子智能体、上下文与Web/持久化组合能消费真实模型响应。测试全绿、模型自述、协议payload检查和真实服务验收分别记录；网关型号标签不证明官方模型身份，也不证明网关兼容全部参数。

| 分片 | 场景 | 最大真实HTTP请求数 | 当前状态 |
|---|---|---:|---|
| 100A | 验收预算/SDK客户端接线、Pillow图片夹具、只读预检 | 0 | 本机实现与Mock接线验收完成 |
| 100B | 文本/流式、Read调用配对、声明的推理档位、图片 | 8 | 实际8次，修复/定向复验后3项通过；推理档位未配置 |
| 100C | 文件搜索/写/改/读、错误反馈、批准/拒绝、模式边界 | 18 | 实际17次，5通过，根多工具批准未通过 |
| 100D | Auto快审/详审及输入隔离，单子/兄弟审批与停止 | 24 | 实际17次，4通过，双子任务审批未过；另2次诊断 |
| 100E | Skill显式调用、compact、重开、Steering/Follow-up/停止、页面交付 | 10 | 实际10次，4通过；页面仅现有Mock/构建证据 |
| 合计 | 单网关、单底层模型，主/子/审查/摘要分别观察 | 60 | 已停止52次，completed_with_findings，16功能通过/2未过 |

Responses优先复验此前记录过的路径；Chat Completions及其他供应商另记覆盖，不能由协议名或同模型名推定通过。缺少图片/推理声明的项目显示未覆盖；真实快审不自然升级时，不声称详审升级已验。直测审查角色与完整Auto管线分别标识。单场景时间预算180秒，输出预算建议主1024、快审256、详审2048、摘要1024；支持能力确认后才纳入实际请求。金额需网关计价依据，不能由请求数直接推导。

## 2. 当前链与本轮范围

现有`probe_async_application`在LangChain回调数逻辑调用；`AutoPermissionReviewer.areview`显式使用空callbacks，摘要/子任务和SDK重试不能依靠根回调统一计数。`ModelCatalog._prepare_async_clients`已为当前模型创建并拥有同步/异步HTTP客户端，`aclose`统一关闭。`with_thinking`浅副本复用这些SDK资源；无需在生产运行层添加全局预算或改变工具默认期限。

100A只新增scripts下验收专用模块和聚焦测试，不改生产API、配置文件、数据库、依赖、前端或既有探针行为。本轮入口只提供preflight，不提供可误触发的live开关。原模型配置只读且不复制密钥到临时文件；默认沿`get_models_path`，早期项目配置必须显式传`--config`。完整配置合法性复用`load_model_catalog`，不复制一套配置验证器或为旧格式补默认api。

## 3. 对象、文件与合同

新增`scripts/provider_acceptance_budget.py`：

- `RequestBudget(limit, parent=None)`及`child(limit)`：同一总额度下的批次/场景共享一把锁。`reserve(model_id)`在HTTP发送前同时检查祖先额度，全部允许才一起加一。达到任一上限后再申请，抛`AcceptanceBudgetStop`并锁止整组；拒绝的请求不计已发送额度。不退款、不重试、不跨进程自动重置额度。模型标签限定安全有限字符。
- `AcceptanceBudgetStop(OpenAIError, GraphBubbleUp)`为验收控制信号：SDK原样传播OpenAIError，Auto原样传播官方GraphBubbleUp，LangChain按Exception正确聚合；必要资源仍由已有finally/应用收尾处理。不作为生产ToolException或新领域失败。初版裸BaseException在真实已安装LangChain的agenerate聚合分支被错误地当成功结果读generations，两个接线测试已复现；按当前SDK/框架控制分支调整继承，不修改依赖或产品代码。
- `guard_catalog(catalog, budget)`复用`_prepare_async_clients`，当前仅支持首轮ChatOpenAI兼容适配器，拒绝其他类型及重复接线。将进程内模型及实际根SDK客户端的max_retries设0，给拥有的同步/异步客户端加request hook，共享额度；不读取/记录HTTP头、地址、正文或图片。未来模型/协议必须先补对应接线测试，不声称当前已支持Anthropic/Ollama实网验收。
- snapshot只提供已占用额度、上限、阻断状态和有限模型标签计数。请求发出前占用意味着它是客户端发送尝试数，不等同供应商收到/计费次数；网关内部重试不可见。

新增`scripts/provider_acceptance_preflight.py`：

- `create_image_fixture(root)`用Pillow内置字体、本地随机ASCII码生成PNG，返回图片路径与预期答案给验收断言；答案不在文件名、文本任务或PNG元数据中，不下载字体/图片，不接生图服务。
- `preflight(config_path, model_id=None)`沿正式loader构造并检查声明，提取限定的接口/图片/推理档位信息；不invoke/ainvoke，不启动图、saver、工具或服务，不使用guard发送请求。配置异常映射固定reason，不输出原始异常、路径、URL或凭证。即使配置阻断也可检查Pillow夹具生成/解码，夹具放TemporaryDirectory中，退出清理。

新增`scripts/probe_provider_acceptance.py`：唯一CLI，只读预检，参数`--config`及`--model-id`。输出有限JSON摘要；就绪退出0，本地配置/依赖检查阻断退出2。`--help`和预检均零模型请求；不自动从旧路径双读、不写真实配置、不创建长期输出目录。默认配置不存在按阻断处理。

新增`tests/test_provider_acceptance_budget.py`与`tests/test_provider_acceptance_preflight.py`，可按职责拆分保持≤500行。现有`tests/test_model_access.py`的合成配置与写文件helper可复用，不改共享测试基础设施。SDK请求测试用真实已安装适配器、MockTransport/传输替身和合成凭证，不能连接真实供应商。

## 4. 正常、拒绝与零请求链

后续正常请求：验收控制器创建总60→批8→场景2额度→构造真实目录→guard_catalog→正式App/模型/审查/摘要入口→原SDK客户端request hook→祖先额度原子占用→HTTP发送→正常模型结果。不同调用源使用同一受保护目录；逻辑角色归属沿原事件和审查诊断，HTTP计数不从内容反推角色。

额度拒绝：并发线程/协程同时申请最后额度，仅一方成功；另一方在传输前抛控制信号，SDK不重试、Auto不降级后偷偷继续。总额度保留真实已占用数，下一组也阻断；控制器执行既有停止/settle/关闭，不以强杀或回滚掩盖已发生副作用。跨进程场景由后续控制器预分配互不重叠配额，不把进程内对象宣称跨进程共享计数。

本轮预检：CLI→正式配置loader→安全声明投影/固定错误→本地Pillow夹具生成解码→有限JSON。当前早期配置缺api时输出blocked/api_required_or_invalid，退出2，模型请求0；这不是供应商响应失败。合法合成配置输出ready，仅证明本地准备，不证明服务可用。

## 5. 验证与实施顺序

先写失败测试确认新模块/入口尚不存在，再实现。预算测试覆盖祖先额度、并发末额度、永久阻断/错误标签、实际SDK同步/异步HTTP接线、SDK原重试2被改为0、浅副本和空callbacks路径仍受控、重复接线拒绝。预检覆盖真实loader校验、缺api/非法配置固定错误、未知ID、默认/显式路径、无异常/凭证回显、Pillow像素及元数据、CLI无live开关及零网络调用。

完成运行定向与全量pytest、compileall/pip check、AST/≤500行及diff检查；前端无改动仍核对现有test/build。按check完成架构/安全复核。使用真实配置的预检只读，不调用供应商；不把预检成功或Mock SDK调用数当成实网证明。不提交push，除非用户另外要求。

README/handoff/future-work同步100A结果及下一步配置阻断；不改关键知识点、课堂掌握记录或原始模型配置。100B—E的实际场景控制器/断言在各片实施前列清文件与函数，不在100A创建空实现或占位live模式。

## 6. 100A本机实施记录

三个scripts模块均为真实实现，生产代码/依赖/配置未改。23项新测试先以缺模块收集错误确认红灯；其中后续协议/流式等边界为实现后补充验证，不能统称全部TDD。SDK接管后的客户端使用旧httpx，测试替身同时覆盖httpx/httpx2；503验证同时保留框架适配异常和原SDK异常cause，不放宽请求次数/错误身份断言。裸BaseException聚合问题由真实适配器测试复现并改验收控制信号，不修改框架。

当前23项定向通过；包含同步/异步、Responses/Chat Completions、流式一请求、浅副本、Auto空callbacks与详审额度拒绝、并发最后额度、配置固定错误及无凭证回显、图片解码/不覆盖及CLI/help零调用。架构独立复核23 passed in0.90s、安全复核19 passed in1.16s，无剩余确证发现；全量结果见handoff顶部。

最终全量后端2166 passed in89.68s，前端18文件96 passed，TypeScript/Vite204模块构建成功；345 Python AST、Python/前端≤500行、compileall、pip check、文档链接及tracked/untracked空白检查通过。仅新增3个scripts模块、2份测试与此Spec，修改README/future-work/handoff；无生产代码、依赖、环境配置或用户数据变更。

实际只读命令：`.venv/bin/python -m scripts.probe_provider_acceptance --config .env/models.json --model-id heiyu-gpt-5.5`，退出2，固定`blocked/api_required_or_invalid`，`http_requests=0`、`live_requests_enabled=false`，本地PNG 800×240解码ready。不是实网失败；原配置缺当前必填api。未复制或修改真实配置、凭证/用户库；没有生图API或模型供应商请求。SDK传输测试的请求对象和响应为合成Mock，不算真实验收。100B前须先明确当前模型接口和能力及具体场景，100A无真实执行入口，未提交/push。

## 7. 已授权的接口声明与首批范围（2026-10-17）

用户确认仅给现有模型补`"api": "responses"`并重跑零请求预检。只修改`.env/models.json`中`models.heiyu-gpt-5.5.api`，保留原文件排版，并在写前后对整个JSON做语义比对，确认其他字段值不变；无配置备份/凭证副本，不修改默认用户配置或生图设置。该文件已被Git忽略，无暂存/提交/push。

同一预检命令现退出0：`status=ready`、`api=responses`、`http_requests=0`、`live_requests_enabled=false`，PNG 800×240生成解码正常。`image_inputs=true`来自SDK有效档案，原文件未显式设置图片能力，尚不能证明网关视觉链路通过。推理档位为空、默认model_default，不试填low/high。原配置SDK重试仍为2；只在将来验收guard接线时置0，未修改真实请求策略。

此步未改程序源码；定向命令`.venv/bin/python -m pytest -q tests/test_provider_acceptance_budget.py tests/test_provider_acceptance_preflight.py tests/test_model_image_config.py --tb=short`实际39 passed in1.22s；前端TypeScript/Vite204模块构建成功。当前接口声明/图片准备为本地证据，非供应商验收。

100B首批拟使用三个独立临时会话：文本/流式随机值1次；Read合成文本并回答2次；Read本地PNG并回答像素随机值2次，计划5次、批次硬上限8次，各场景分别上限1/2/2。图像答案不进入任务正文/文件名；工具结果与调用ID、最终回答及保存状态分别断言。未声明的推理档位记未覆盖，不分配请求；剩余3次不自动重试或补测。场景控制器接正式应用和共享guard后先Mock验证，真实执行另明确启动；此处未新增live入口或发起真实请求。

## 8. 100B—E执行器（最新实施范围）

保留原CLI的零请求默认行为，另增显式执行入口`scripts/run_provider_acceptance.py`，必须传`--live`、模型配置、模型ID和新报告路径。`run_acceptance`一次进程持有总预算、按顺序创建批次/场景子预算；报告每场景立即保存，只有有限状态、断言名称、用量数值、耗时和安全异常类型，不保存请求正文、响应正文、图片、URL或凭证。任何场景失败停止该批及后继批，未执行场景明确标记；不得重启脚本获得新额度掩盖失败。报告路径独占创建，避免覆盖既有证据。

`scripts/provider_acceptance_cases.py`负责共用的`CaseEnvironment.open_app`、`run_turn`、调用/ToolMessage配对及正常终局断言。复用正式loader、内存中的选定目录、正式`open_async_persistent_application`及SQLite；只在探针装配上下文临时替换loader，退出还原。目录在建图前接guard，所有角色共享实际SDK客户端。仅内存限制输出，不改真实配置；临时目录退出清理，报告独立保留。每个场景180秒，停止和收尾沿正式App所有权，不强杀掩盖副作用。

`scripts/provider_acceptance_core.py`实现100B三个场景和100C文件/权限场景；`scripts/provider_acceptance_agents.py`实现100D Auto/子智能体；`scripts/provider_acceptance_context.py`实现100E Skill/压缩/重开与控制。超过500行按这三种场景职责拆分，不改产品API。

100C使用合成工作区和外部夹具：文件搜索写改读最多7次；错误反馈2次；含显式Allow目标及待批目标的批准2次；人工拒绝2次；NoAsk2次；Deny覆盖宽Allow2次，共最多17次（批次18）。100D直接验证审查角色与正式Auto路径分别记录，正常子任务、两个子任务分别审批、停止等待审批的子任务均只操作夹具；按场景硬额度控制，不把模型未产生预期并行调用解释为调度通过。100E只读Skill后按其随机指令回答、两轮历史手动摘要及重开、Steering/Follow-up/停止依靠正式事件在安全边界操作；本地Web交付另用现有确定性测试核对，正式App实网不冒充浏览器手工验收。

正常链：控制器→场景子额度→正式loader→guard→正式App建图→arun/astream_run/审批/子任务/压缩→HTTP hook计数→有限断言→报告。失败链：网络或断言异常→仅记录安全类别与已消耗次数→正式App停止/settle/close→报告失败并标记后继未执行；不输出供应商原异常正文。新增执行器测试先Mock验证额度、停止、脱敏、正式App配对与报告，再执行实网；不通过修改测试或重发获得通过。

### 8.1 首条实网后离线修正

首条请求文本正确、9个delta，原验收在`stream_deltas`失败；HTTP已发送1次，不重发或重置此记录。固定供应商版本的Responses流在起始空chunk给response ID，文本chunk的空ID被LangChain补run ID；聚合结果保留response ID。新增`tests/test_responses_stream_identity.py`在正式App可离线复现增量/最终消息分离。修复限定`agent_runtime/async_message_observation.py:_StreamState.delta`：按框架模型节点调用命名空间保存首chunk身份，空响应起始chunk同样参与身份确定，同一次调用的增量归同一消息；新的节点调用使用新身份，摘要/审查过滤保留。公共事件结构、保存消息ID、前端组件不改。

验收专用`scripts/provider_acceptance_scope.py:FixturePermission`包装原ToolSpec权限准备：原prepare产生审查对象后，requested/resolved都必须属于规范化临时root，Bash精确命令只允许`printf AUTO_OK`，越界抛GraphBubbleUp控制信号，早于Auto/Allow/人工批准执行。仍委托原default_decision，保留真实权限算法。实际审批前另核对工具、调用数量及预期参数；NoAsk/Deny须同时检查实际目标和具体拒绝原因，避免把普通工具错误误报成权限通过。兄弟审批先确认两实例同时待批，再分别提交。规范化root同时避免macOS的/var与/private/var别名影响Allow规则。

故障修正后只继续未执行项，保留失败记录与已使用1次额度；不自动重跑首条流式用例。后续正常流式调用可另外记录本次修复的实网覆盖，原失败不改写为成功。继续执行必须从已有有限报告导入历史计数并校验总/批/场景额度，已执行场景禁止再次发送；最终报告明确首次失败与后续证据，不能用新进程重置额度。

第二次显式续验在Read文本联合断言失败后停止，累计3次：配对、success及落库成立，流式1条已归并、0条未归并；报告未保存内容，无法区分正文附带分页说明还是最终回答差异，保留初次未通过，不追认。本地新增真实Read分页用例先红后绿，修正验收对合法完整单行分页说明的遗漏，分别验证正文与最终回答。基于用户“直接做到完成验收”的推进授权，修复后显式增加一次定向复验：流式1次、Read2次，原图片仍2次，100B合计最多8次，仍不重置预算、不增加上限、不自动循环重试。原两份失败报告保留；定向复验有独立名称/次数，新增场景只在显式continue-from且存在已诊断检查时开放，普通新运行仍计划5次。

100C实网的根批准场景未触发预期审批，保留`root_approval_requested`失败，不凭此推定产品授权失效，也不重复发起该场景。原报告缺少参数/权限分支细节，无法追认其原因；新增有限工具终局outcome/reason便于后续定位，不保存正文或路径。用户要求本轮完成验收，故增加显式`--continue-independent`：诊断后只收集尚未执行的独立场景，断言不满足保留finding但继续，失败场景不重发；网络、额度和fixture边界故障仍停止。以累计19次报告续接，既有已执行项全部跳过，预算和失败记录保留。最终completed_with_findings表示验收执行收尾，绝不等于所有场景通过。

### 8.2 Responses摘要内容边界

追踪摘要链发现`context_compaction._compaction_steps`只接受str，当前Responses适配器合法文本块列表被拒绝；新回归先复现`summary_invalid`。本次仅对正式metadata声明api=responses的摘要响应接受标准text块，已知reasoning/thinking块不写入摘要，未知/图片块、空结果、截断/工具调用仍拒绝。复用现有内容投影，保留非Responses/未知协议的原拒绝边界和旧回归，不为旧测试放宽未知内容。影响生产`context_compaction.py`、新增`tests/test_responses_summary_content.py`；无API签名或配置/依赖变动。手动压缩仍要求实际减少上下文，小型历史返回不压缩不视为供应商故障；实网验收夹具需包含可压缩的合成冗余，不拿短历史强求压缩。

100D兄弟调用在2次请求后返回两条工具错误，无子任务模型调用；未证明双待批，保留未覆盖。标准Responses双工具事件经已安装转换器的本地样本验证参数彼此独立，未复现原生转换器错误；不凭此推断实际网关完整协议正确。新增一次只读、不执行工具的`multicall_diagnostic`（100D内上限1）：沿同一guard SDK流收集原生已完成function_call与LangChain转换后参数是否相等、参数键/类型是否符合明确合成要求等有限事实，不保存正文或参数值。这是定位请求，非重发兄弟执行场景；原失败不撤销。后续只继续未执行的child_stop和100E。

只读诊断证实原生完成事件有2份参数对象，而当前转换器两份args均为{}。增补一次`nonstream_tool_diagnostic`（100D内上限1）：模型浅副本设置框架已有disable_streaming=tool_calling，用公开astream入口核对完整响应下两份参数是否准确；不执行任何工具、不改变生产默认/真实配置，不重发root_approve或child_siblings。总60、100D24上限保持，报告该验证为兼容候选证据，不能冒充批审批实网已通过。

## 9. 最终收尾证据

实际52次：100B=8、100C=17、100D=17、100E=10，总上限60。所有原定功能场景均已尝试，16通过、2项多工具批次未通过；两条初次失败及两次只读诊断另记，22回执保留原状态。完整响应只读实网得到1call，未满足2call，故未改变生产默认/真实配置、不宣称它已解决批审批。当前停止发送；下一步先讨论该兼容边界，再另定有界复验。最终有限JSON及详细结果见[验收报告](../research/provider-acceptance-2026-10-17.md)。

后端最终命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`实际 **2205 passed in89.83s**；日志`/tmp/naiyue-100-backend-final-complete.log`。最后删除未消费的验收记录字段后，聚焦`tests/test_provider_acceptance_*.py tests/test_responses_*.py`实际 **62 passed in2.76s**。前端`npm test -- --run`实际 **18文件96 passed**，`npm run build` TypeScript无错误/Vite **204模块成功**，日志`/tmp/naiyue-100-frontend-final.log`和`/tmp/naiyue-100-build-final.log`。

360 Python AST、412 Python/前端源文件≤500行、433本地文档链接、compileall/pip check、git diff及modified/untracked空白/凭证形状扫描通过，JSON场景/批次/总额度精确求和52。架构最终Mock59项、安全79项复核通过，无确证遗留代码发现；SDK诊断3个Mock为探索性诊断之后补充验证，不称TDD。两处生产修复有先红后绿证据；供应商质量、浏览器实网、原机平台及用户掌握程度不由这些本地检查推出。其他会话101/102设计文件及交接段落保留；无暂存、提交或push。

## 10. 流式最终参数修复（用户已授权，2026-10-17）

目标：保留流式，参考Pi在完成事件中核对最终参数；不关闭并行工具、不放宽参数/权限校验。先诊断实际事件，再冻结最小接入点、先红后绿实现、最后定向复验root_approve和child_siblings。原52次报告保持原样。

本次新增总额度9：只读事件诊断1、根批次批准2、兄弟子审批6；SDK重试0，场景失败不循环重发。新增账本保留诊断已用次数，关联原52次，最大累计61。阶段报告独占创建；只记录事件类型/顺序、调用序号、长度及相等判断，不保存正文、参数值、原始ID、URL或凭证。仍只操作临时合成工作区/数据库，不修改真实配置。

诊断扩展现有provider_acceptance_multicall，收集全部事件类型，按output_index/item_id/call_id关联参数；以SDK原生完成参数比较转换结果。需保留标准增量、多调用、最终参数独有、部分增量、交错、未知/非法参数、异常/停止与关闭、元数据/usage等离线回归。接入范围应局限model_access：在SDK事件进入现有LangChain转换器前补齐缺少的参数，继续委托官方请求、流式转换和回调；不复制整个框架流式执行器或修改全局共享转换器。具体接线由诊断和小探针确认后在本节补充。

预定影响：model_access的Responses适配及_build_model接线；现有诊断脚本、定向阶段执行器和对应测试；本Spec、验收报告、README/handoff的当前状态。Chat Completions/其他供应商、Agent图、权限/审批、工具代码、前端和依赖保持原合同。正常链最终完整args进入原参数校验和审批；真正非法JSON/对象或不一致身份必须明确失败，不猜测参数、不静默执行错误目标。

### 10.1 已确认事件与接入点

诊断新增1次，见[有限事件报告](../research/responses-final-args-diagnosis-2026-10-17.json)：两调用added.arguments长度0，接着arguments.done及output_item.done完整参数长度35/36；没有arguments.delta，旧转换器两次args={}。本次完整序列确认此前假设，原历史失败仍保留。

最小接线采用Responses专用ChatOpenAI子类，仅覆盖异步_astream：每次调用创建借用SDK客户端的局部视图，包装该请求返回的事件流，再完整委托super的请求、转换、超时、回调和错误处理。已安装ChatOpenAI._astream直接用super调用基类_astream_responses，故仅覆盖同名_astream_responses不会接通；公开astream回归已先红证明，实际入口选_astream。原共享SDK及HTTP客户端不变，关闭仍归ModelCatalog；不猴子补丁，不复制框架执行器。支持普通responses.create及with_raw_response/headers路径。

工具参数先按output_index及item/call身份收集，output_item.done以完整参数定稿（缺失时取arguments.done）；response.completed.output可补全尚未结束的调用。只向官方转换器交付一次带完整arguments的function_call开始项，避免官方字符串合并重复或交错索引错配。文字/推理/用量及其他事件照常即时透传；不等待整个响应完成才展示文字，现有UI无工具参数增量消费者，工具执行本来就等模型响应定稿。严格JSON对象校验，身份冲突/最终参数矛盾/未完成工具流明确失败；不使用宽松JSON修补，不执行未知目标。新增responses_stream.py承担这一个协议职责，_build_model只对api=responses选择子类；Chat Completions保持原类。

乱序完成回归先红确认：即使B先完成，工具最终交付仍按已声明output_index顺序；暂存已完成B，A定稿后依次交付A/B，避免改变串行工具的执行顺序。参数缓冲为单请求范围，不增加应用队列或审批状态。

### 10.2 实现与最终验收

已按上述接线实现。新增公开SDK/生命周期/两阶段账本22项回归；空参数8项、乱序完成2项和两类最终事件冲突1项均有本轮先红后绿记录。网络故障测试按已安装SDK的真实包装链精确断言OpenAIConnectionError→APIConnectionError→httpx.ReadError，不将底层HTTP异常误作公开合同。缺fd的既有搜索用例使用项目已记录的PATH执行，不修改或放宽测试。

根批次批准新增2次，兄弟子审批新增6次，均通过原场景断言；保留诊断已用1次，本次9/9，累计原52+9=61。根批次整批暂停/唯一Ask/批准配对成立；兄弟两者同时待批且一方在另一方等待期间完成，两个子结果及保存历史成立。根/子分别1/3条流式消息归并、未归并0。详见[补充报告](../research/responses-final-args-2026-10-17.md)及[续账JSON](../research/responses-final-args-validation-2026-10-17.json)。本次停止发送，不重复其他16项实网。

最终命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short`：**2227 passed in88.78s**，日志`/tmp/naiyue-100-final-args-backend-final.log`；聚焦146项通过。`cd frontend && npm test -- --run`：**18文件96 passed**；`npm run build`：TypeScript无错误、Vite204模块成功，日志`/tmp/naiyue-100-final-args-frontend.log`与`/tmp/naiyue-100-final-args-build.log`。365 Python AST、417源文件≤500行、446本地文档链接、compileall/pip check/diff及有限报告凭证形状/额度求和检查通过。

架构/安全专项复核共同发现的“两类最终事件矛盾未检查”已先红后绿补齐，复核均返回无确证遗留发现。模型接入仅异步Responses流路径新增适配，其他供应商/API、同步SDK对照、权限/图、前端、配置、依赖、DB schema未改；无Windows/Linux原机、浏览器实网手工操作或新增业务效果证据。其他会话101/102/M6讨论与文件保持；未暂存、提交或push。
