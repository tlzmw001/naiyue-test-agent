# Spec 074：上下文模型链的异步接线

状态：2026-10-08，用户已确认实施。接项目prompt、主动摘要和明确输入溢出的一次恢复；沿用create_agent，不改变压缩算法、配置或CLI默认路径。

后续进度：[Spec075](075-integrated-async-application.md)已按用户要求合并完成工具、应用、异步存储和正式流式消费，共36例新测试/全量882例及真实两进程探针。本文后续分片记录是当时计划，最新唯一下一步为活动取消设计、随后Web；CLI仍保留原同步入口。

## 当前链路与范围

交互图before_agent调用_compact_messages，其内部绑定摘要输出上限后同步invoke；project prompt和recover_input_overflow只有同步wrap_model_call。Spec073已提供异步选模/门禁/请求观测，但这些节点仍阻止或阻塞完整上下文模型链。

目标链：异步before_agent → 共用切点/预算流程 → await摘要 → 共用验证/回退/状态替换 → 异步project prompt → await模型handler；若SDK明确拒绝输入，则异步强制压缩同一实际请求并await一次重发，ExtendedModelResponse/Command仍由框架应用。

Pi a69bef789的compact→generateSummaryWithUsage→await completeSummarization传入signal，区分准备、模型等待、摘要验证。AgentX3fcfa0e的SummarizeTokenOverflowStrategy.generateSummary另负责摘要请求与结果，再供上下文组装；Java实现不作为Python异步事实。核对本机LangChain1.4.2 factory的abefore_agent/awrap_model_call注册和LangGraph1.2.12流式状态格式；官方参考：https://docs.langchain.com/oss/python/langchain/middleware/custom 、https://docs.langchain.com/oss/python/langgraph/streaming 。不新增实验事件API。

## 实现契约

- project_prompt工厂签名/名称保持，双路径共用system message装配；同步resolve回调仍是可信快速内存查询，文件发现加载仍由应用负责。
- _compact_messages原签名保留，新增私有_acompact_messages。原算法在唯一摘要IO点yield绑定模型与summary_request；同步/异步执行器分别invoke/await ainvoke，再send响应或throw原异常回同一流程。仅拆摘要IO，不创建完整Agent运行时/通用工作流引擎。
- 切点、工具配对、前次摘要、输入/输出上限、发送复核、force必须减少输入、软阈值跳过与已知临时失败回退均共用，摘要最多一请求。摘要异常不得在执行器静默吞掉；执行器finally关闭内部流程。
- 主动压缩middleware实现before_agent/abefore_agent，名称compact_before_new_run保持。参数与摘要流程状态按调用局部存储，不用实例字段保存session/run/cutoff。
- CancelledError在摘要等待处记录安全失败耗时，在外层结束压缩观察后抛原异常；不得转成ContextCompactionError或走摘要失败回退。图状态替换仍仅在完整摘要验证成功后返回。
- recovery提供同步/异步wrap；第一请求只识别既有SDK明确输入拒绝，其他异常身份保持。共用恢复开始/重发请求构造/成功Command/失败记录，异步只改变等待。强制摘要失败或第二请求再失败均上抛，无第三请求，不重跑Agent/tools。
- 异步恢复取消在摘要/重发时关闭恢复观察并抛CancelledError，不转成模型回答，不增加用户活动停止模式。同步异常与审批控制行为保持。
- astream用values/updates、version=v2验证状态替换/最终消息；模型文本delta、草稿校准、SSE/Web、异步工具/审批/SQLite/应用run均不在本片。直接调用交互图不宣称application已异步。

## 文件与兼容

修改agent_runtime/project_prompt.py、context_compaction.py、context_recovery.py；新增内部context_summary_io.py隔离摘要IO驱动，防止原400行模块超过500。新增tests/test_async_context_chain.py与必要的边界测试文件，扩展既有scripts/probe_async_model.py的可选上下文验证。旧公开导入/工厂/名称/同步测试保持；若文件上限要求移动序列化helper，保留原导入并在本Spec记录，不删除旧代码入口。

更新交接/计划/待办/README与Spec073/074，不修改.env/真实配置/依赖/关键知识点，不提交、部署或迁移用户DB。

## TDD与验收

先观察预期红灯：原生异步模型同步_generate直接失败；摘要Event等待中同loop协程运行；invoke/stream均获得相同规范化结果，主动压缩更新checkpoint且保留旧历史可读；小上下文无摘要、项目prompt和当前选模一致；合法临时失败仅在发送上界内回退、超界拒绝；摘要空/length、非法工具结果、过大摘要、工具配对和前次摘要沿原规则。

明确溢出下原模型最多两次、摘要一次，错误响应不提交压缩历史；第二请求失败/未识别SDK错误/无法切点不能自动重试。摘要取消和重发取消都传播，未返回成功摘要更新；验证安全日志/观察结束与无额外请求，不声称真实供应商取消成功。

运行新定向pytest、旧上下文/事件/模型测试、全量pytest、compileall、pip check、≤500/空白/Markdown检查。现有真模型授权允许一有界上下文探针：合成历史、只调整探针内存预算，无工具/文件/持久库；最多摘要+回答两逻辑请求、参数化父截止，输出仅统计。真实故障/取消/溢出恢复仍用确定性测试，不制造供应商错误或扩大生产效果。

## 下一片

本片通过后，工具wrapper/HITL观察与异步串并行批次、工具IO按独立Spec接线；随后异步application/AsyncSqliteSaver/元数据与消息观察、正式流式消费及最小Web。用户after_turn停止保持，活动取消逐边界另验收。

## 实施与验证（2026-10-08）

已实现并接线：project prompt和overflow recovery变为同名双路径middleware；主动压缩提供before_agent/abefore_agent。_compaction_steps保留同一切点/预算/摘要校验/失败回退算法，唯一摘要IO交context_summary_io执行，再回填响应或原异常；没有复制两套压缩策略。context_compaction448行，未移动原序列化helper。取消摘要记录失败耗时/结束观察并抛CancelledError；恢复摘要/重发取消保持原信号，无第三次请求。

TDD证据：首批实际图11例全部红灯，包含同步_generate误调用和缺awrap；接线后相关65通过/1失败。失败测试误以为多模式流最后一条必为values，无项目middleware的原生after_agent探针确认最后可为updates，修正新测试为最后values的完整状态与checkpoint精确比较。边界补测4失败/13通过，其中重发请求构造阶段两例是真实回归，修复阶段标记后转绿；另外两例是错误注入假设，独立验证现有分类器与原生框架后修正测试边界并保留未知连接错误不能回退的测试，没有扩大产品回退规则。新增可选探针功能2例先红后绿。其余追加边界通过，不称全部34例都先红。

- 新测试`.venv/bin/python -m pytest -q tests/test_async_context_chain.py tests/test_async_context_boundaries.py --tb=short` → `34 passed in 0.68s`。
- 相关旧上下文/事件/恢复/回退合并验证曾得`118 passed in 1.12s`，随后补充的新场景纳入最终全量；一次误写不存在的mid_turn测试路径，命令未执行测试，rg确认后立即纠正，非产品故障。
- 最终`.venv/bin/python -m pytest -q` → `846 passed in 19.11s`（此前812，新增34）。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → exit 0；`.venv/bin/python -m pip check` → `No broken requirements found.`；git diff --check exit0，未跟踪文件另显式结构检查。无独立应用打包目标。
- `.venv/bin/python -m scripts.probe_async_model --with-context --timeout-seconds 120` → `async_context_probe=PASS logical_calls=2 native_adapter=True messages=3 state_stream=verified model_event_pairing=verified memory_checkpoint=verified`。现有默认模型、最多摘要+回答两逻辑调用，真实摘要保留随机合成事实并供后续回答，125秒父截止；仅探针内存副本调整触发/保留预算，摘要输出上限沿原配置。无工具/用户文件/持久库/配置修改，输出无正文凭证。不计SDK HTTP尝试/费用，不代表上下文质量评测或真实故障/取消覆盖。

覆盖正常跳过、当前选模/项目prompt、旧checkpoint可读、主动和强制摘要、前次摘要、旧工具配对、后置预算与非法摘要、临时/未知失败、force禁止回退、无安全切点、错误重发响应不提交、第二请求失败/取消无第三次请求、取消信号与安全观察。astream只验证values/updates和状态提交，没有token增量/浏览器显示证明。

已知限制：本机LangChain core的agenerate把子任务自行抛出的CancelledError作为gather结果后按普通生成结果读取，独立注入得到AttributeError；这不同于外层Task.cancel，本片只验证后者，不升级依赖或通过猜测AttributeError还原取消。raw httpx.ConnectError仍是unknown，不冒充已适配SDK连接异常。CPU序列化/token估算、日志/观察订阅者、可信回调仍同步；正式异步应用清理/事件消费、工具/审批、SQLite、增量/活动取消仍待下一片。

README/计划/待办/交接同步；未改.env/依赖/关键知识点，未提交部署。新增核心代码尚未逐行学习，独立实现与面试追问没有新证据。
