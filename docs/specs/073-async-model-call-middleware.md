# Spec 073：选模与响应门禁的原生异步入口

状态：2026-10-08，按已确认Spec071顺序实施的第一片异步基础。仅扩展现有模型调用middleware；不宣称应用/CLI/工具/持久化已全面异步。

后续进度：[Spec074](074-async-context-model-chain.md)已接项目prompt、摘要和一次输入溢出恢复的异步路径，并验证values/updates状态流；全量846例与真实摘要→回答通过。本文末尾的“下一步上下文”是本片收尾时点，现在唯一下一步转为工具异步接线；仍无异步应用/SQLite或文本增量。

## 当前调用链与源码依据

`build_runtime_agent` → `create_agent` → 同步model节点 → `select_model.wrap_model_call` → handler → 模型响应 → 归一/校验/调用事件。同一图的`ainvoke`进入amodel节点，框架组合`awrap_model_call`并await底层绑定模型的ainvoke。现在项目只提供同步装饰器，继承的awrap_model_call抛NotImplementedError。

核对本机langchain1.4.2、langgraph1.2.12、sqlite saver3.1.1。factory.py收集双路径hook，amodel_node等待异步handler；AgentMiddleware基类明确要求提供awrap_model_call。官方说明：https://docs.langchain.com/oss/python/langchain/middleware/custom 。流式模式另见 https://docs.langchain.com/oss/python/langgraph/streaming ，本片不选择实验事件API或实现token delta。

Pi a69bef789：agent-loop.ts的streamAssistantResponse在准备上下文后await streamFunction再for-await消费，executePreparedToolCall另外await tool.execute并传signal；模型IO与工具IO分别拥有异步边界。AgentX3fcfa0e：AbstractMessageHandler.processChat注册TokenStream的partial/complete/error处理并交MessageTransport；Java回调机制可参考职责，不能等同本项目Pythonawait。

## 范围与接口

- 保持`make_model_selection_middleware(resolve_model, record_selection)`及既有工厂/同步公开API签名；返回实现wrap_model_call与awrap_model_call的AgentMiddleware实例，名称保持select_model。
- 实际图红灯确认末端`observe_model_request`同样只有同步wrap；因此本片同步补齐它的双路径，请求结构/预算日志生成与finally记录共用，名称和全局middleware导入保持。不能绕开观测来让异步测试通过。
- 两种路径共享选模、调用开始、结束原因归一、usage/缓存观测、响应门禁、成功/失败事件；只在模型handler处同步调用或await。每次调用的model_id/call_id保存在局部变量，不能用middleware实例字段保存运行状态。
- request.override仍在原模型调用try内，构造请求失败也关闭已开始的调用事件并抛原异常；不能因拆prepare把它移出原异常边界。
- 原有error/invalid_tool_calls/缺有效调用/缺AI响应拒绝保持；普通异常仍抛原异常，未知日志安全字段保持。GraphBubbleUp继续抛出，不伪报普通模型失败终局。
- 异步Task取消保留CancelledError；模型调用结束事件记录failed与固定cancelled/task_cancelled原因，不转为AIMessage，不称为用户after_turn停止或供应商服务器已取消。事件与回调仍须受信任、快速、同步；慢消费者异步交付另片。
- 框架编译图的原生ainvoke用于本片验证，InMemorySaver保留thread历史。没有新的application异步方法，没有改变CLI默认同步路径、SQLite schema/配置或依赖。选模回调仍是本地快速同步查询。

第一片验收链：测试协程 → 现有build_runtime_agent（无工具）→ 框架ainvoke → awrap_model_call → 原生异步模型 → 共用门禁/事件 → 返回完整AIMessage与内存checkpoint。同步模型入口在测试中直接报错，避免线程托管同步调用冒充原生模型异步。

## 实现与TDD验收

生产修改call_observation.py与context_observation.py，新增tests/test_async_model_middleware.py和scripts/probe_async_model.py；更新Spec/交接/计划/待办/README。若超500行才按职责拆分，不提前创建新运行时或应用服务。

先失败测试：实际编译图可await原生模型；模型等待时同event loop可执行其他协程；同图按会话/下一次请求换模型、历史隔离；响应error/invalid/missing calls拒绝；普通异常身份和安全失败事件；未知原因与usage保留；取消原异常传播与调用事件配对；GraphBubbleUp不误报普通失败。使用asyncio.Event同步，不依赖紧sleep或新pytest插件。

验证定向pytest、全部pytest、compileall、pip check、文件≤500与文档结构。先用原生异步假模型确认调度/门禁与真实框架接线，再按用户已授权的真实验证做一逻辑请求的有界在线探针：只发送随机合成文本，无工具/用户文件/持久库，内存checkpoint与模型调用事件配对，输出统计不含正文或凭证；父进程硬时限与协程时限由探针参数决定。真实SDK重试取消、工具并发和应用终局另作独立证据。

## 后续接线顺序

1. 模型边界：本片双路径选模/门禁/调用事件。
2. 上下文与工具边界：项目prompt、摘要/溢出恢复等待、工具wrapper/HITL观察、异步串并行批次及有界工具IO；共用原策略，不复制运行时。
3. 应用与存储边界：新增异步应用运行/审批恢复入口，共用会话控制；AsyncSqliteSaver与元数据IO、完整消息观察、清理/终局一致性一起验收。继续保留同步入口。
4. 流式投影与最小Web：明确delta/最终消息/持久确认、订阅/背压/断开；应用拥有任务，UI消费事件并提交控制。活动取消按模型/摘要/子进程/副作用逐项验证，没有证据时保持after_turn。

本片可独立回归，后续不依赖新增业务配置或凭证。不修改.env/关键知识点，不提交、部署或迁移用户数据。

## 实施与实际证据（2026-10-08）

本片已实施：选模工厂返回双路径middleware，prepare/accept/failed共用规则，每调用数据局部保存；请求观测同样提供双路径，finally保持失败和取消时的安全日志。未改公开工厂签名、名称、模型配置/依赖和应用/CLI入口。

TDD：12个新用例最初全部失败，基类awrap_model_call缺实现；选模补齐后实际图9失败、直接hook与同步回归通过，定位到末端请求观测仍缺awrap，先更新本Spec再补接线。接通后只有一条新测试错误假设失败：system_prompt=''仍由框架发送空SystemMessage。独立无项目middleware的create_agent探针确认这个原有事实后，将预期修正为精确四条(type, content)，未修改产品去迎合测试、未放宽历史/隔离断言。

收尾检查发现拆prepare把request.override移到try外，会在构造请求失败时漏记call结束。新增同步/异步两例先红（2 failed），移回原try并保持原异常后定向67例通过；最后全量812例通过。真实成功探针在此修正之前执行，同一轮只补回异常路径；未重复付费成功请求或扩大真实取消证明。

- 定向`.venv/bin/python -m pytest -q tests/test_async_model_middleware.py tests/test_agent_runtime_response_gate.py tests/test_agent_runtime.py tests/test_model_tool_subscriptions.py tests/test_model_request_context.py tests/test_model_limits.py` → `67 passed in 0.86s`。
- 全量`.venv/bin/python -m pytest -q` → `812 passed in 18.94s`，此前798例、新增14例。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → exit 0；`.venv/bin/python -m pip check` → `No broken requirements found.`；git diff --check exit 0。项目尚无独立打包目标，未跟踪文件另显式检查存在/行数/空白/文档结构。
- `.venv/bin/python -m scripts.probe_async_model` → `async_model_probe=PASS logical_calls=1 native_adapter=True messages=2 model_event_pairing=verified memory_checkpoint=verified`。现有配置默认模型、一逻辑请求、随机合成文本、无工具/持久库；父时限默认95秒、协程90秒，未输出正文或凭证。不统计SDK HTTP尝试/实际费用，也不证明真实取消、流式delta、工具或应用终局。

新增确定性验证：原生_agenerate等待Event，_generate直接报错；等待中同loop协程可运行、完整结果与aget_state一致、下一请求换模型/会话历史隔离、error和无效/混合调用拒绝、原异常身份、未知原因/usage/缓存读取量、安全事件配对、取消传播、缺AI及GraphBubbleUp保持。取消模型call记failed/cancelled，未定义新的用户停止模式。

已知限制：同步选模回调/日志/事件消费仍可能阻塞；token统计和状态操作也非无限负载非阻塞保证。低层无工具运行可ainvoke，不代表统一application可以ainvoke；摘要/恢复、项目提示middleware、工具调度/HITL观察与SQLite仍须接线。唯一下一步：上下文模型链的异步接线Spec（项目prompt、摘要和最多一次溢出恢复，共用切点/预算/回退算法），工具调度和应用/存储随后分别验收。
