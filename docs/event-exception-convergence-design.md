# 事件与异常收敛设计讨论

日期：2026-10-11。状态：讨论稿，未批准执行；不改产品、公开API、线协议或数据库。承接[健壮性蓝图](agent-resilience-design.md)与[Spec080](specs/080-exception-failure-annotations.md)，实施前另锁定可执行Spec。

实施更新：方案已获用户批准，并按[Spec081](specs/081-event-exception-convergence.md)完成两片及续接修补，后续内部兼容清理见Spec082；下文是设计时事实快照。当前全量1038后端/27前端及构建通过，工程已本地提交bcbe6be，未推送。Web完全丢失通知后的待定回执只保证本进程不重复执行，不保证跨进程幂等；重试/熔断/备用能力仍是后续。

## 1. 当前证据与更正

- events.py注册27种事件，不是28种。Publisher维护run及model/tool、turn、control、context operation生命周期，跨事件规则与分发混杂；构造器还有字段校验。
- 无网络故障注入：同一假模型正常returned/completed；仅在accepted-model_call_finished发布点注入ValueError后，app.run抛ValueError并产生failed。观察可改变运行结果已复现；不是自然线上事故。
- CLI实际消费消息、模型开始、工具、审批、control、压缩/恢复；Web消费消息/工具/run与control_delivered。turn无专门UI分支，但日志监听读元数据，不能说绝对无消费者。
- 当前6个ContextVar：publisher、RunControl、turn/control/context观察、failure context。MessageStateObserver无独立绑定，入口显式传publisher。
- 内容事件带白名单JSON快照/增量，失败事件带安全码。JSON快照不是天然脱敏，日志另做元数据白名单。
- Pi a69bef789：core10种，turn_start/end；loop emit→Agent.processEvents更新状态→逐个await listener。core与AgentSession._emit无逐监听者try/catch，不能声称Pi隔离所有消费者。AgentSession在message_end保存，监听emit在保存前。
- AgentX3fcfa0e：TokenStream部分/完成/错误回调分别处理transport、保存、健康上报及hooks；TracingTokenStreamWrapper封装ThreadLocal。不能把所有回调都视作纯观察。
- 本项目SQLite/checkpoint保存是显式关键路径，不依赖可选订阅；不迁移为尽力而为的监听者。

## 2. 推荐职责与数据流

原异常E是失败/控制的调用合同，FailureDescription D是安全事实，事件是只读通知。恢复决策直接使用捕获点事实，不依赖事件消费或异常属性读取是否成功。

```text
执行：调用 → 事实/策略 → 结果或原异常 → App收尾与必要保存
              │                          │
观测：        └─调用/消息进度             └─终局通知
                              ↓
                  每run有序分发 → CLI / Web / 脱敏日志
```

普通观察故障只降级观察，不改成功/失败、stop/审批/manual_review/busy；必要保存、控制执行、队列/审批grant、实际消息门禁仍是关键路径。已恢复错误不提前发run failed；同一未恢复错误可分别报告调用failed和run failed，引用同一分类事实。ToolMessage回模型，事件不会回填模型消息。

## 3. 消费者清单

| 事件组 | 当前用途 | 建议 |
|---|---|---|
| run_started/finished | CLI/Web/日志，Web绑定run与终局 | 保留；App生产收尾后的终局 |
| message_delta/completed/revised | Web草稿与内容、CLI完整消息 | 保留；内容/诊断分开投影 |
| tool_call_started/finished | CLI/Web工具状态、日志 | 保留；dispatch不声称执行器实际进入 |
| model_call_started/finished | CLI开始提示、日志用量/原因 | 保留调用级观测，不伪造SDK内部attempt |
| approval_requested | CLI提醒；Web具体项来自应用接口 | 保留状态；决定与权限仍归App |
| control accepted/delivered/applied/discarded/outcome_unknown | CLI回执/交付/未确认；Web消费delivered | 保留有实际用途的语义，按kind区分 |
| control_delivery_started | 诊断/测试，无独立UI展示 | 诊断候选，本轮不删除 |
| compaction/context_recovery started/finished | CLI进度/恢复、日志 | 保留，由Runtime实际能力边界生产 |
| turn started/resumed/finished/suspended/unavailable | 日志/验证，无专门UI分支 | 诊断用途；评测需明确消费指标 |
| message_snapshot_unavailable | CLI/Web观察降级、日志 | 保留不完整状态 |
| context_messages_changed | 日志/验证，展示不删历史 | 诊断候选，不作为UI删除命令 |

未来评测是合法消费者，但尚未实现的评测不能为任意新事件背书。若以后过滤事件，必须处理统一sequence/游标，不能省略帧却让前端误报sequence_gap。当前不删除/改名27种事件，也不为追求10种而重写协议。

## 4. Publisher与校验

总线负责每run序号、时间/关联、有序分发、监听故障隔离。移出model/tool start-end、turn跨审批、control转换、context配对、唯一终局等序列规则，改用测试事件序列检查器。

检查器不能只作为抛异常的普通listener，否则隔离后测试可能误绿。收集后在测试末assert_valid，或记录违规后显式断言。生产者维护发事实所需状态，不再由总线重复建立五套执行状态机。

保留输入/反序列化格式、权限/容量、内容投影/日志脱敏，以及真实模型工具配对门禁；它们不是观察序列校验。第一片不删除所有__post_init__，分类后只搬迁跨事件规则。

只改publish不够：payload在publish前构造、message投影、各Observer的普通观察故障也需入口保护。失败记录固定诊断并标记incomplete/禁用本run相关观察器；不重跑模型/工具。保护不能包住真正的stream推进、结果、控制和关键IO。

真实GraphBubbleUp/GraphInterrupt、CancelledError、UserStop及进程控制保留；监听者不是审批/停止入口，不能通过抛框架异常获得改变图的权力。实施Spec分别限定监听调用和框架调用的catch范围，不以catch BaseException声称“永不影响”。

保持轻量同步分发，网络由现有有界async队列/Web消费。慢监听、重入、锁顺序和并发需测试；隔离异常不保证慢消费者零延迟。断流/溢出标观察不完整，从应用状态重同步，不伪造completed。不承诺总线必须缩到固定行数。

关键前提是消费者有独立完成依据。当前Web DeliveredRun.started由run_started监听完成，_produce成功路径未独立完成该future；如果只把发布故障隔离而无启动通知，可能悬挂等候。此为源码风险，尚未运行复现。第一片必须覆盖启动/终局通知丢失，以应用run身份、实际任务结果/状态提供有界降级依据；不能只改Publisher后宣称安全。具体接线在执行Spec锁定，公开签名保持。

## 5. 层级与payload

Runtime定义turn/model/tool/message及自己的压缩恢复事实；App定义run终局、控制接收/清理、审批待办和保存结果。按实际所有者划分，不为了仿照Pi把Runtime压缩搬到App。Runtime交付事实可经App薄适配成为会话通知，Runtime不认识App类。

共用envelope/发布协议可在标准库contracts边界；Runtime仅定义自己payload，App组合事件目录/序列化。统一投递不要求巨大共同_PAYLOADS表，不建设新事件运行时/插件注册平台。

授权本地CLI/Web得到不影响原消息的内容快照/增量；日志/指标得到白名单元数据/安全失败描述。不序列化整个SDK对象、原响应、异常正文或任意additional_kwargs。订阅内容意味着有内容访问权限，不把所有事件当可外发诊断。

## 6. 一次run的依赖传递

一个Runtime认识的run服务对象归组publisher、错误策略、RunControl和观察辅助对象；App装配后传下去。不是全局服务定位器，不持App/DB/UI对象供底层反向调用。

保留既有公开签名：用一个集中入口ContextVar桥接，make_invocation_context只读这一个对象，其后middleware从显式runtime.context读取；消除多套bind/getter及异常reporter/id表。不承诺全部ContextVar清零。thread/task复制、还原、session/run匹配必须验证。App控制输入仍经active_runs查找，不依赖输入线程隐式上下文。

停止/队列/grant是执行权威，Observer账本只跟踪事实。App根据实际结果/异常收尾，不根据事件是否收到决定成功或保存。

## 7. 框架custom stream

本机LangGraph1.2.12和官方[streaming](https://docs.langchain.com/oss/python/langgraph/streaming)提供custom/get_stream_writer；writer本身通过框架上下文取得。可作为以后工具增量的来源，经适配成为领域事件。

本轮不整体替换：App控制接收、图外启动、图返回后的保存/终局仍需应用投递。沿用当前messages/updates/values v2，不升级依赖或迁移v3。custom接入另验hook、sync/async、取消及重同步；本轮只核对能力，未做接线探针。

## 8. 收敛顺序与范围

建议一份共同设计/后续Spec、两片独立验收：

1. 观测不影响执行：移走生产序列状态机、保护观察入口、测试检查器，保留终局/CLI/Web/事件字段/断流降级。工具恢复/权限/存储模式不变。
2. 依赖与失败回传：集中run服务/单绑定，合并Spec080原异常标注，删除重复bind/reporter/id回查；业务类型、模型规则、控制保持，payload按生产所有者归组，不改线协议名。

实施涉及超过8文件，先完整Spec与兼容清单，不能把合并设计当一次大改。无需新服务、凭证、数据库迁移；回滚源码，不动会话数据。事件删除、协议改名、所有检查删除、审批/保存降级、重试/熔断新增不在本次收敛范围。

验收覆盖：成功发布失败、主异常+发布次错、payload/Observer故障、监听隔离、停止/审批、保存失败、跨会话并发/顺序、流式缺口/重同步；检查器确实让非法序列失败。第二片覆盖异常身份/cause/context、独立Runtime、集中上下文还原、同步/异步/stream/Web和线程传播。先红灯再实现，最终全量后端测试、前端测试/build、compileall/pip check/diff check。

## 9. 本轮验证边界

仅文档，无产品/测试/.env/依赖修改，无真实请求/提交push。故障注入输出normal returned/completed与injected ValueError/failed。相关基线命令`.venv/bin/python -m pytest -q tests/test_run_events.py tests/test_application_run_event_failures.py`为`27 passed in 0.57s`，不表示收敛已修复。文档检查用git diff --check；不报告全量测试或构建通过。
