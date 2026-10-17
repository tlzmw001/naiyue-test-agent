# Lesson 65：前五章全流程复盘与测试走读手册

> 第五章最后主题扩展为前五章综合复盘，2026-10-13。六组完整流程共用主图，逐组课堂展开；当前六组例子均已展开；2026-10-14用户先补回例6再接第六章。讲解范围收束，不自动记为本人调试或独立掌握。用户自述比之前理解更多，仍有细节未懂。

## 使用方式与范围

先沿每组的输入→装配/准入→模型/工具→状态/事件→返回或异常→收尾读一遍，再Debug对应现有测试。每个跳转确认三个问题：当前帧是谁、参数来自哪里、返回值/异常接下来给谁。示意ID用S1/T1/R1；真实ID为本次生成值，不用逐字比较。

项目代码、真实LangChain图、假模型和本地工具/临时SQLite组合成确定性集成；测试不证明真实模型的自主决策、业务效果或线上并发。配置加载在测试中被替换，不读取真实models.json或凭证。后续工具/权限/审批、压缩恢复、保存与Web包含改变主链的分支；本轮标出入口和合同，内部后续学。

## 总图：执行、观察与收尾

```text
配置/模型目录 → build_local_application → 会话服务+Agent图+checkpointer
创建session → session映射thread，模型选择/控制状态保存在应用会话表
run准入 → busy/run_id → prepare → bind RunServices → Agent执行
模型前hook → 选模wrapper → handler → AI响应 → 响应门禁/状态合入
  ├─ 有工具调用：调度工具→工具主体返回→ToolMessage→状态合入→下一模型循环
  └─ 准备自然结束：停止/steering/follow_up/结束选择
普通模型异常 → 描述并附在原异常 → 裸raise → 应用记录failed
普通结果 → 应用检查pending/停止结果 → 记录completed/interrupted/stopped
以上已准入路径 → finally → 清理共享状态/必要保存 → 尝试终局 → 返回/raise

观察旁路：模型/工具执行事实和图values/messages → observer/payload
 → per-run Publisher(seq/FIFO/失败隔离) → 日志/可信内容订阅/异步桥/投影
```

区别：Agent图创建不是执行；模型响应不是整个run结果；messages不是RuntimeEvent；模型回调不自行保存应用会话；描述异常不替换原异常；记录failed不等于执行完收尾；事件EOF不等于成功；busy释放不能消除审批/人工核对阻断。

## 代码地图与默认深度

| 位置 | 主链作用 | 本轮深度 |
|---|---|---|
| application.__init__.build_local_application/_assemble_application | 同一目录、服务、图与内存saver接线 | 读对象关系与注入回调 |
| SessionService._create_session/_run_steps | ID/准入/阶段/执行和finally | 关键行展开 |
| application.io_steps.run_steps/arun_steps | 操作执行，send/throw交回流程 | 首次往返展开，重复轮次引用 |
| RunObservation.bind + make_invocation_context | RunServices投影到显式context | 跟同一对象，不重讲Token内部 |
| agent_runtime._invoke_agent_run | input/context/config分工，消息观察执行入口 | 展开参数来源 |
| call_observation选模middleware._prepare/wrap/_accept/_failed | 选模型、handler、门禁、失败描述 | 主分支展开 |
| LangChain model/tools及add_messages | 调模型/工具、结果消息、更新状态 | 看关键调用与实际输入输出，调度内部步过 |
| message_observation/turn/control observers | values到消息/轮次事实 | 看对象和事件，哈希/JSON细节步过 |
| session_controls及inject hooks | 实际队列/模型前和自然结束边界 | 例2展开 |
| _finish_run/_cleanup_run | 主/次异常、必要保存、busy与终局 | 正常与失败各追一次 |
| AsyncApplication/RunControl/await_settled | 任务归属/用户停止/外部取消/已开始IO | 例4、5展开 |

## 例1：启动、工具循环、同会话换模与另一会话隔离

主测试：[test_application_composition.py](../../../tests/test_application_composition.py)的test_local_application_wires_one_catalog_one_checkpoint_and_session_events。无Event等待窗口，适合完整慢速单步。

配套事件测试：[test_application_message_events.py](../../../tests/test_application_message_events.py)的test_tool_loop_publishes_all_content_once_and_default_logs_are_metadata。两者分别关注装配/历史与完整消息事件，不把两个测试的正文混成同一实际运行。

### 输入与替身

测试目录含A/B；A预设AI工具调用read_probe(call-1)、A-one、A-other，B预设B-two。load_model_catalog被替换为返回这份ModelCatalog；read_probe实际执行但只返回合成字符串private-tool-result。create_agent不发模型请求。

| 运行 | 会话/thread | 本次输入 | 预期消息内容 |
|---|---|---|---|
| R1 | S1/T1，模型A | private-first | private-first、空AI工具调用、private-tool-result、A-one |
| R2 | S1/T1，模型B | private-second | R1四条历史、private-second、B-two |
| R3 | S2/T2，模型A | private-other | private-other、A-other |

### 逐阶段时间线

1. load_catalog收到Path('synthetic-models.json')，返回同一catalog；loaded_paths只有一次。真实配置解析/SDK构造本例被替换，第二章按合同回访。
2. build_local_application创建SessionService、一个InMemorySaver；_assemble_application注入resolve_model、控制队列和资源回调；_assemble_interactive_runtime/_build_runtime_agent注册middleware并create_agent。返回LocalApplication持有service/agent，不重新为每次run装配图。
3. 两次create_session各生成公开session_id与内部thread_id；记录model_id=A、busy=False、run_id=None。app不持有一个全局thread；历史在saver，应用会话表保存映射与控制状态。
4. app.run(S1, [('user','private-first')])进入service.run；_run_steps只是生成器对象，run_steps.next才实际执行准入。锁内检查阻断/busy，生成R1，建立RunObservation/每run Publisher及策略快照，busy=True、run_id=R1；随后锁外执行。
5. observation.start发布run_started；资源prepare操作得到instructions/resources，本例无工作区/应用持久化。驱动send结果，阶段切到execute。
6. observation.bind绑定同一RunServices；invoke_agent_run得到S1/T1/R1。input={'messages':[('user','private-first')]}；context含session/run和观察/策略/控制对象；config.configurable.thread_id=T1。context不是模型正文，config指定checkpoint历史。
7. invoke_with_message_observation先get_state(T1)、seed历史哈希，本例为空；再驱动agent.stream(updates/values,v2)。seed不删除Agent历史，也不重复发布旧历史。新输入由框架转换为HumanMessage、赋ID并合入状态，values观察发布human快照。
8. before_agent初始化run_stopped=False；模型前控制hook本例没有队列/停止。其他资源/预算hook只看当前合同，本例没有摘要调用或恢复分支。
9. 选模wrapper._prepare从context读S1，service.resolve_model查会话model_id=A、catalog取同一A对象；生成逻辑model_call_id、turn.begin、model_call_started；handler(request.override(model=A))进入内层wrapper/框架模型调用。
10. 模型A第一次实际输入是空SystemMessage加Human private-first。Fake._generate返回ChatResult，框架装成ModelResponse，其result含AIMessage(tool_calls=[read_probe,args={},id=call-1])。业务工具调用ID来自预设模型响应，AI消息ID由消息状态处理；两者用途不同。
11. _accept检查AI/结束原因/invalid_tool_calls等，把AI交turn observer并记录model_call_finished。响应被图合入后，完整AI快照可见。Fake未给finish_reason，本例stop_reason=unknown但合法工具调用仍按tool_calls路由；unknown停止原因与未知异常不同。
12. 框架选出未回填的call-1，进入ToolBatchMiddleware工具wrapper与_record_tool_call；工具主体read_probe返回str，框架包装成ToolMessage(tool_call_id=call-1,content=private-tool-result)，图再合入状态。tool结束事件先于对应ToolMessage状态可见；第一turn在AI与工具结果可见/ready后完成。
13. 普通工具结果回到模型循环入口，先经过before_model；再次resolve_model仍A，新的model_call_id/turn。第二次模型输入为System空+Human+AI工具调用+Tool结果；Fake返回A-one。合入完整AI消息，第二turn完成。
14. AI没有后续工具调用，after_agent自然结束检查两队列空，关闭输入窗口，不再跳模型。图最终返回四条消息，底层runtime返回这个result；with退出恢复RunServices绑定。
15. _run_steps切finalize，检查pending=None，observation.result记录completed，准备return。但finally先yield _finish_run；驱动执行清理：控制队列/活动登记/窗口/本次指令清理，内存分支busy=False/run_id=None；observation.finish尝试终局。驱动恢复流程，StopIteration.value携带业务result给调用方。
16. select_model(S1,'B')只更新本会话model_id；T1未变。R2重用图/saver/catalog，生成新R2；baseline已有四条历史，只送新Human输入，B获得历史+新输入并返回B-two。旧消息仍在结果，不在本轮重复发completed。
17. R3用S2/T2，因此只有自身历史；A-other是共享Fake模型的第三个预设响应，不代表历史串线。Fake响应游标与checkpoint历史不同。

### 本机只读观测：第一run完整事件序列

复用同样输入、增加只读Fake输入记录和事件订阅；本次实际首run16事件、后两run各8事件，两会话最后busy=False。以下序号是这条探针的输出，不是所有运行必须固定的序号。

| seq | 事件 | 本例事实 |
|---:|---|---|
| 1 | run_started | R1已准入 |
| 2 | message_completed | Human private-first |
| 3 | turn_started | 第一turn |
| 4 | model_call_started | A第一次逻辑调用 |
| 5 | model_call_finished | accepted，stop_reason=unknown |
| 6 | message_completed | AI工具调用消息 |
| 7 | tool_call_started | call-1 |
| 8 | tool_call_finished | success |
| 9 | message_completed | Tool结果 |
| 10 | turn_finished | 第一turn completed |
| 11 | turn_started | 第二turn |
| 12 | model_call_started | A第二次逻辑调用 |
| 13 | model_call_finished | accepted |
| 14 | message_completed | AI A-one |
| 15 | turn_finished | 第二turn completed |
| 16 | run_finished | completed |

模型输入观测含空SystemMessage，而图messages结果不包含它；系统prompt按模型请求装配，不是本例通过Human状态输入保存的消息。

### Debug定位

入口断点测试models定义；装配时F11看build_local_application对象接线，create_agent内部F10。create_session展开ID/状态。run_steps第一次next/send展开到_run_steps，实际模型/工具操作F10或按下列关键入口展开；重复driver轮次不重读每行。

| 当前帧/时点 | 有限观察 |
|---|---|
| 测试：app创建后 | loaded_paths；app._sessions._catalog is catalog |
| 测试：session创建后 | {sid:(r['thread_id'],r['model_id'],r['busy'],r['run_id']) for sid,r in app._sessions._sessions.items()} |
| _run_steps准入后 | session_id、thread_id、run_id、session['busy']、observation.stage |
| driver执行操作前后 | pending.call.__name__、pending.args、result（赋值后） |
| _invoke_agent_run | input；config；(context['session_id'],context['run_id'])；sorted(context) |
| 选模_prepare | model_id、call_id（产生后）；request.messages的type/content投影 |
| _accept | [(m.type,m.content) for m in response.result]；message.tool_calls |
| 工具read_probe | 无入参，主体返回private-tool-result；ToolMessage在框架包装后产生 |
| invoke_with_message_observation | part['type']，values时[(m.type,m.content) for m in latest['messages']] |
| _cleanup_run结束 | session['busy']、session['run_id']；thread_id仍保留 |
| 测试run返回后 | [(m.type,m.content) for m in first['messages']]，再看second/other |

result可能同时出现在driver、流程、框架不同帧，必须说明当前帧。返回前清理结果常为None，业务result由生成器return传出。WATCH只读取，不执行run/publish/send/throw等有副作用函数。

## 例2：同run工具执行中插话，再处理后续任务

入口：[test_application_turn_events.py](../../../tests/test_application_turn_events.py)::test_steering_and_follow_up_create_turns_when_delivered_not_when_queued。无Event窗口，适合完整单步。

输入Human initial；Fake依次返回AI(probe)、AI second、AI third。probe通过测试holder取得app/session，执行steer('steer')、follow_up('follow')，然后返回tool body。

1. 装配/准入/第一次模型响应完全沿例1；第一turn开始，AI提出probe，调度工具。
2. steer/follow_up检查正文、busy和steering_open；锁内分别向两deque追加正文，登记控制请求，锁外发布accepted；此时状态messages没有新Human，也没有新turn，不能把入队当交付。
3. probe返回tool body，框架ToolMessage先合入。下一模型入口的inject_before_model调用drain_steering，取出全部steering并清队列；prepare_control_messages按已选control配对消息ID。
4. hook返回{'messages':prepared}，reducer合入Human steer；values可见后control observer记录delivered。follow仍在队列，第二次模型输入不含follow，返回second。
5. 当前无待执行工具，inject_at_natural_end调用finish_run_boundary；停止优先，再全部steering，最后一次pop一条follow。本例取follow，返回messages+ jump_to='model'。
6. 框架跳模型循环入口，会经过before_model，不重跑before_agent；第三次模型收到Human follow并返回third。
7. 两队列空，原子关窗；应用结果/收尾按例1。最终Human顺序initial/steer/follow，3个turn、1个run。

关键状态依次为(steering=['steer'],follow=['follow'])→([],['follow'])→([],[])。自然结束检查的类别优先级不是跨类别全局提交时间FIFO；busy=True而steering_open=False意味着已关窗但还未释放运行资格。两种边界和工具主体必须展开，观察器与driver引用已学结论。

### 例2本次走读观测与关键跳转

同一现有测试本次1 passed in0.85s；另加只读Fake输入/会话队列/事件记录的内存探针，未修改实现。模型前阶段、整个图执行与应用结果返回分清：_run_steps只交出一次invoke_agent_run，内部图调用模型3次；second回复先留在图状态，after_agent可继续，应用此时仍处execute，只有图真正结束后才切finalize/收尾。

| 实际观测点 | steering_queue | follow_up_queue | busy/open |
|---|---|---|---|
| 第一次模型输入 | [] | [] | True/True |
| probe内两次提交后 | ['steer'] | ['follow'] | True/True |
| 第二次模型输入 | [] | ['follow'] | True/True |
| 第三次模型输入 | [] | [] | True/True |
| app.run返回 | [] | [] | False/False |

实际模型输入（省略每次都有的空SystemMessage）：第一次[Human initial]；第二次[Human initial, AI probe调用, Tool tool body, Human steer]；第三次在第二次输入后追加[AI second, Human follow]。最终状态追加AI third，共7条消息。

probe里steer/follow_up通过session_id查应用的_active_runs获取本run控制观察者；真实队列存字符串，ControlObserver._records存请求身份/阶段，两者分工。drain/finish选出字符串并登记select；prepare_control_messages生成带已分配ID的HumanMessage；hook返回messages更新由框架reducer合入，values路径先观察消息、再按Human ID确认control_delivered。

关键事件序号来自本探针：7 tool_started；8/9两类accepted；10 tool_finished；11 Tool消息可见；12第一turn结束；13 steering_delivery_started；14 Human steer可见；15 steering_delivered；16/17第二turn/model开始；19 AI second可见；20第二turn结束；21 follow_delivery_started；22 Human follow可见；23 follow_delivered；24/25第三turn/model开始；27 AI third可见；28第三turn结束；29 run_finished。delivered是状态可见合同，不代表模型已经理解；序号不是其他场景固定值。

_select_controls和prepare_control_messages看输入/输出即可，关键展开drain_steering、inject_before_model返回更新、finish_run_boundary的优先级和popleft、inject_at_natural_end的jump_to。当前hook帧messages是正文列表，prepared是HumanMessage列表；state消息与session队列不可混用。工具装配时仅创建工具对象，真正执行probe在首次AI响应调度之后。

## 例3：模型失败、描述回传与观察故障

主入口：[test_model_tool_subscriptions.py](../../../tests/test_model_tool_subscriptions.py)::test_model_failure_has_no_accepted_end_and_preserves_exception[timeout]。无等待窗口。失败在Fake._generate真正抛出，不是直接在应用入口注入。

1. 创建failure=TimeoutError('PRIVATE_TIMEOUT')，FailingModel._generate直接raise这个对象；装配/准入/资源/绑定/选模同例1，model_call_started已发布。
2. handler抛错，没有正常ModelResponse；选模wrapper except调用_failed。describe_failure_in_context使用context中的本run RuntimeErrorPolicy，按model_id委托目录的模型规则快照。
3. 默认模型describe_model_failure→resolve_failure→classify_model_failure按类型得到transient/timeout，构造FailureDescription(source=model,category=transient,reason=timeout,error_type=TimeoutError)。没有按异常正文猜测超时，没有决定重试。
4. attach_failure_description将(run_id,D)附在原异常；_failed发布模型失败终局，wrapper裸raise。异常对象、类型和原有cause保留。
5. stream执行退出，模型响应未成功合入，不产生普通成功AI消息。IOCall driver.throw将同一异常送回_run_steps的execute暂停点，应用保存primary_error。
6. observation.failed→_describe_failure先read_failure_description校验run/type，读到D，直接补stage=execute成为RunFailureData；不把同一异常再按模型规则重分。状态failed，但busy仍需收尾。
7. finally驱动_finish_run清理共享状态，尝试必要保存/终局；没有次要失败时调用者仍收到原TimeoutError，pytest断言caught.value is failure。metadata过滤订阅只看到run_started/model_started/model_finished(failed)/run_finished(failed)，序号可有被过滤消息/turn事件造成的间隔。

自定义分类单次调用入口：[test_failure_annotations.py](../../../tests/test_failure_annotations.py)::test_application_reads_annotation_and_classifies_model_once。它用VendorQuotaError+两参数classify规则，断言calls=[原异常]；应用读取同run描述，reason=vendor_quota，active登记清理。与默认timeout同一主链，只替换规则选择。

### 普通观察者故障分支

同文件test_midrun_observer_failure_does_not_change_model_or_tool_result：正常模型路径，两订阅者，一个在model_call_started抛RuntimeError。Publisher._deliver捕获普通观察故障、记录诊断、将这个订阅者标为本run失效；另一订阅者继续收到accepted与completed终局。业务result仍是PRIVATE_REPLY，坏订阅者只收到run_started/model_call_started两次。这与模型handler失败是两种责任。

必要收尾故障入口：[test_application_run_event_failures.py](../../../tests/test_application_run_event_failures.py)::test_cleanup_failure_is_reported_without_masking_primary[False/True]。无primary则cleanup错误阻止成功返回；有primary则记录次要错误并保留原异常。本例故障在清理尾部_record_steering，busy此前已释放，不能泛化所有清理失败必然释放。

关键观察：模型帧failure；_failed帧description；应用帧primary_error/observation.stage/failure/busy；_finish_run帧secondary；测试帧caught.value is failure。分类器/诊断错误不重讲9分支，引用第二章合同。

### 例3本次验证与单步地图

三个既有测试（timeout、应用读取已附描述、普通订阅者故障）本次3 passed in0.87s；同输入只读内存探针确认same_exception=True、描述run匹配、terminal=failed/execute/transient/timeout/TimeoutError、busy=False/run_id=None/active_runs=0。无模型网络请求或生产/测试实现修改。

完整事件本次为：1 run_started；2 message_completed（输入Human）；3 turn_started；4 model_call_started；5 model_call_finished（failed）；6 turn_finished（failed）；7 run_finished（failed）。测试只订阅四种调用元数据，过滤后序号为1/4/5/7，不能当作完整事件链；失败调用未产生成功AI回复。

入口断点放测试failure赋值行，选[timeout]，Fake._generate的raise failure必须读；框架handler内部可步过或步出回项目。随后展开wrap_model_call的except/_failed、describe_failure_in_context的描述附加、run_steps的steps.throw、_run_steps的except/finally、RunObservation._describe_failure的已附描述优先、_finish_run清理与终局。分类规则引用第二章，不重复逐行；message_observation异常finally的get_state只读取已保存部分状态，不重新调用模型。

E是同一个TimeoutError；D是FailureDescription，附在E.naiyue_failure=(run_id,D)；F是事件RunFailureData，补stage且不含source。步骤throw让异常在原yield处抛出，应用裸raise仍需执行finally；finally先yield清理IOCall，清理完成后driver.send恢复生成器，原异常再到调用者。failed记录结果，busy释放在共享状态清理中；本内存例释放早于run_finished投递，不把终局通知作为准入锁。

WATCH只在变量已产生的对应帧查看：模型failure类型；_failed的description四字段；应用primary_error/observation.stage/status/failure/session['busy']；_finish_run的secondary/session['busy']；测试caught.value is failure。不要在WATCH重跑分类、发布或执行函数。

普通订阅者故障测试的本fixture只有正常模型回复，名字虽含tool并不证明实际执行了工具。错误发生在broken回调，_deliver记录并禁用本run的该handler，健康消费者和模型执行继续，terminal=completed；与模型异常传播分开。

用户前两次追问已澄清：hook消息更新与jump_to/图边控制模型循环；事件用于消费与展示等实际功能，但普通观察结果不承担循环或权限决策。循环跳转有用户明确自述理解，例3本次仅已讲解，未记录用户独立debug或掌握。

## 例4：异步文本流、完整确认与消费者断开

流式入口：[test_async_application.py](../../../tests/test_async_application.py)::test_delta_is_available_before_response_and_final_snapshot_calibrates。断开入口：[test_async_stream_delivery.py](../../../tests/test_async_stream_delivery.py)::test_stream_disconnect_keeps_owned_run_and_persistence。

1. make_app替换目录，建立原生async Fake与AsyncApplication；acreate_session使用run_blocking进行会话创建。输入Human start，完整预设回复hello。
2. app.astream_run返回异步生成器，首次迭代启动_stream；先生成run_id，建立匹配session/run的AsyncEventStream，再create_task(produce)、登记app._tasks/done回调。
3. produce→_execute→sessions.arun→arun_steps→同一_run_steps；准入/prepare/上下文与图执行仍沿例1，操作由async_call或run_blocking执行。
4. 原生Fake._astream先yield 'he'再等待release；框架messages流片段到_StreamState.part/delta，只对主模型合法文本/ID递增index，发布message_delta(provisional=True)。
5. 同步Publisher调用桥入队，桥唤醒消费者；消费代码记录RuntimeEvent并调用StreamingMessageProjection，得到草稿content=he/provisional=True。测试等saw_delta确认消费者已经处理；测试变量task是consume Task，此时尚未done。Fake仍等release，生产执行也未结束。
6. release.set后Fake给出llo及必要元数据，完整AI进入graph values；MessageStateObserver发布完整快照，投影按同ID替换/确认为hello，避免he+hello重复。消息确认不代替保存确认。
7. 图自然结束→应用结果/必要收尾→run_finished；produce finally.stream.finish保留尾部队列、唤醒消费；读完后_stream await task确认生产未失败，finally.close退订清队列。
8. 断开分支使用另一个测试的saved回复与临时SQLite：先读run_started，Fake进入等待；iterator.aclose只关闭消费桥，不取消已登记producer。producer未done、会话仍busy；release后producer得到saved、完成保存，会话ready，订阅清空。

关键读取：_stream的Task登记、_StreamState.part的messages/values分流、投影输入输出、producer finish与consumer close。模型/SDK/事件循环调度/SQLite内部步过。entered/saw_delta/release是测试阶段信号，wait_for只是等待上限，不是业务流程；5秒协调区间不长期停断点。任务/迭代器操作不可在WATCH重执行。

### 例4本次验证与异步走读边界

两个既有入口本次2 passed in0.83s。前者验证首片段he在完整回复之前已到达消费投影，之后同一AI行变为hello/provisional=False并收到completed终局；后者使用tmp_path临时SQLite，验证消费迭代器关闭后producer未done、busy仍True，放行后结果saved、会话ready、订阅清空。无生产/测试实现、配置或实网修改，不把该断开测试记为重启恢复验证。

两个同名task不能混用：测试task=create_task(consume())负责消费；_stream局部task=create_task(produce())负责执行Agent、结果与收尾，并在app._tasks登记。调用astream_run只取得异步生成器，首次__anext__/async for驱动才进入主体、订阅和启动生产。正常消费时_stream读完桥队列后await生产task，以传播生产错误；异常退出/主动aclose从finally关闭桥，不走普通读完后的await，不自动取消producer。

数据路线：Fake._astream产出ChatGenerationChunk(AIMessageChunk('he',id=M))；框架messages part供_StreamState.delta生成RuntimeEvent(message_delta,index=1,text='he')；同步Publisher调用AsyncEventStream.__call__过滤session/run、入deque并唤醒；消费者__anext__取事件，projection建立draft。后续llo同ID/index=2追加，完整graph values触发快照事件，projection按(session_id,id)覆盖为完整hello，不能将hello再拼到hello之后。三份数据分别是框架消息片段、领域RuntimeEvent、显示dict，不把显示投影当Agent图状态或保存确认。

finish表示生产结束且保留尾部事件，close表示消费退出并退订/清空队列；__anext__先检查队列，再检查closed，所以正常finish后仍可读完队列。run_finished记录运行终局，message_completed只确认完整消息；本流式主例为内存应用，必要保存边界在断开例/第8章展开。

断点策略：测试入口可慢速准备；在create_task(consume())之后至release.set()之前的5秒协调段连续运行，先停在draft断言处查看有限字段，再放行并停在final断言处。生产链各关键函数结合源码阅读；确需逐行进入时选一处断点跑一遍，不在带wait_for的区间多处长停。不改测试时限或IDE配置。断开例同样先到producer取得之后查看状态，再单步aclose，放行后连续跑到ready断言。WATCH仅看当前帧已有的draft、final、事件类型/序号、done/busy，不调用__anext__/aclose/cancel等改变运行的函数。

本组已讲解，用户本人调试/理解仍待反馈；未因“继续”升级例3掌握证据。下一组例5停止、外部取消与settle。

## 例5：用户停止、外部取消与已开始线程工具

三个主入口在[test_active_stop.py](../../../tests/test_active_stop.py)：test_stop_before_model_sends_no_request、test_stop_cancels_model_without_fake_answer_and_can_start_again、test_external_cancel_remains_failed_and_is_not_user_stop。线程工具入口：[test_async_builtin_io.py](../../../tests/test_async_builtin_io.py)::test_sync_tool_runs_off_loop_and_cancel_waits_for_settlement。

1. 模型前停止测试在run_started回调request_stop，校验session/run/窗口后设stop_requested=True、关窗、RunControl.signal置位；本测试先在compact_before_new_run.abefore_agent→await_model的入口检查遇到UserStop，返回run_stopped/jump_to=end；inject_before_model未执行，没有模型请求/伪造AI。
2. 等待中停止测试先让原生Fake进入await release；选模wrapper已通过control.await_model登记模型调用Task。停止校验可选run_id，stale被拒绝；返回待发送正文，同run重复请求返回[]。
3. RunControl.request取得登记快照，安排各Task所属loop调用cancel；await_model捕获CancelledError，仅本run已request且等待者本身无取消请求时转UserStop，finally移除模型登记。
4. StopModelMiddleware显式捕获UserStop，返回空ModelResponse+Command(run_stopped=True)；后置边界跳end，应用记录stopped，必要收尾后终局stopped。第一轮无AI回答；放行release后新run消费尚未使用的next回复。
5. 外部取消测试不调用request_stop，直接cancel app.arun Task；CancelledError不变为UserStop，driver交回流程，应用记录failed/task_cancelled并设置manual_review_required，必要收尾后原取消仍到调用方。
6. 若取消时同步工具已在工作线程运行，工具wrapper.await_settled用shield保护内部操作，保存外层取消后继续等同一Task；测试检查暂时超时、busy=True、无终局；release后工具finished，再传播取消、清理busy、failed/人工核对。

普通工具返回、操作结束、run停止、run失败是不同事实。request_stop提交完成不等于run已结束；已开始操作不回滚，真实工具/必要保存失败仍可能failed。模型前停止入口无等待窗口可慢速单步；其他入口先运行协调段再观察，详见Lesson62/63。

### 例5本次验证与完整停止分支（2026-10-14）

本次五个既有实例5 passed in1.84s：模型前停止、模型等待中停止后再运行、外部模型取消、test_stop_drains_started_tool_but_skips_waiting_tool[True]、外部取消线程工具并等待settle。测试是本机Fake/合成工具，不含模型网络请求；无生产/测试实现或配置修改。

request_stop锁内验证busy、可选run目标、重复提交与输入窗口，设置stop_requested并关窗；retained是steering/follow-up正文快照，不立即从deque取走，也不自动重发。锁外active.stop_control.request设置运行私有signal，并仅安排登记的模型Task.cancel；此方法返回不等于停止完成。

模型前停止本测试实际由_ContextCompactionMiddleware.abefore_agent先调用await_model；requested已True，尚未创建操作Task就抛UserStop，abefore_agent接住并返回run_stopped/jump_to=end，不执行_acompact_messages或后续inject_before_model，也不调用模型。inject_before_model另有停止检查，但不是本例命中的入口。等待中停止由await_model在CancelledError分支检查requested且当前等待任务cancelling()==0，转UserStop；选模wrapper描述/记录失败调用后重抛，外层StopModelMiddleware接住UserStop返回空ModelResponse+Command。StopBoundaryMiddleware.after_model跳end，应用沿正常result路径记录stopped，清理后返回只有Human的结果。一次模型调用失败与run stopped可以同时成立；不伪造AI回答。Fake._next在等待之后才pop回复，因此下一run可以使用尚未消费的next。

外部task.cancel没有request_stop信号；CancelledError保持，经过arun_steps.throw回_run_steps，记录failed/task_cancelled、manual_review_required=True，finally驱动_afinish_run，清理后原取消到测试。当前合同即使模型等待中外部取消也要求人工核对，不能把它说成已经观察到工具副作用。

工具中用户停止（本次True顺序实例）：writing开始并等待，later尚未开始；request_stop不强杀writing线程，busy保持。放行writing后得到committed；later进入guarded时检测signal，返回对应ID的error ToolMessage、naiyue_not_executed=user_stop，later主体不执行；下一模型边界停止。两条工具结果配对而不再调用模型，run_stopped=True。

外部取消线程工具：实际同步工具由框架在工作线程执行，_ToolCallObservationMiddleware.awrap_tool_call通过await_settled保护已开始的handler。shield阻止等待者取消传入内部Task；保存外部取消对象，继续等同一Task，不重启工具。内部结束后重新抛取消，而非把正常工具返回当整个run成功。测试的wait_for(shield(task),1)预计TimeoutError只是证明运行尚未结束；task外层shield避免该检查再取消运行。release后finished=True、busy=False、terminal failed/需核对；无rollback保证，也不据此断言完整ToolMessage已提交。

单步优先模型前停止测试（没有等待期限），展开request_stop、_ContextCompactionMiddleware.abefore_agent、await_model的创建前检查、abefore_agent状态更新/跳转、应用result与清理。其他测试在5秒线程/Event协调区间连续运行，再看结果；线程工具不能期望F11从事件循环连续进入另一个线程，必要时单独工具入口断点且快速放行。关键WATCH在当前帧查看retained/run_stopped/消息type、control.requested/current_task().cancelling、取消分类/需核对、busy/finished/second/model.seen；不在WATCH调用cancel/request_stop/事件set/wait。

### 例5停止时点纠正与实测

用户调试发现模型前停止没有进入inject_before_model。本次直接执行上述两个既有测试并临时增加内存观测（不改测试/生产文件），断言通过：模型前测试轨迹为await_model_enter(abefore_agent,requested=True)→UserStop→abefore_agent返回run_stopped/end，inject_before_model调用数0。等待中测试取消轨迹为_cancel进入(done=False)→_cancel返回(done=False)→Fake._next收到CancelledError（预设回复尚余1条）→await_model向外抛UserStop。

精确时序：request_stop先置signal并用call_soon_threadsafe安排_cancel；事件循环执行_cancel时才调用Task.cancel；Task在随后恢复调度时收到CancelledError。本测试已暂停在await release.wait，取消直接打断等待，不等release.set，不执行其后的replies.pop。cancel是请求而非同步完成，异常清理/模型流关闭/图停止与应用必要收尾仍须执行；run终局和busy解除另有时点。

Python采用协作调度，长同步代码段不会被cancel逐行抢占；某个await若立即完成也不一定让出控制。已完成Task不被本_run_control._cancel取消；操作若明确屏蔽/处理取消可延后终结。模型本机Task退出不自动证明供应商远端计算中止。资料核对Python3.12 Task.cancel/Task Object官方文档：https://docs.python.org/3.12/library/asyncio-task.html#asyncio.Task.cancel 。本次仅修正课堂材料，无进度推进或行为变更。

本组已讲解；用户随后报告第六点settle理解，线程执行由框架适配、外层先收到取消而后传播已补充说明，其他独立能力未评估。用户随后要求先补回例6，见下方完整走读。

## 例6：同会话互斥、跨会话与旧run通知隔离

入口：[test_application_session.py](../../../tests/test_application_session.py)::test_same_session_busy_rejected_other_session_runs_and_failure_releases_gate。竞态入口：[test_control_races_and_failures.py](../../../tests/test_control_races_and_failures.py)::test_old_cleanup_control_notifications_keep_old_run_after_new_run_starts。

1. busy测试用BlockingAgent替代真实图；S1准入设busy并进入invoke，通过started告知主线程，然后等待release。seen记录真正进入invoke的session/thread。
2. 第二个S1请求在锁内检查busy=True抛SessionBusyError，尚未取得新run资格，不执行原run的finally清理；不进入invoke，所以seen没有第二条S1。
3. S2有独立状态，busy=False，准入并返回空messages。服务锁保护短暂状态修改，不持续包住Agent执行。
4. 放行S1后stub抛预设RuntimeError；应用记录失败、finally清理busy，工作线程捕获原对象。再次S1能够进入invoke并又抛同一个预设RuntimeError，验证恢复准入，不代表业务成功。
5. 旧通知竞态测试在内存清理尾部_record_steering处暂停旧run；在此之前共享状态已清理、busy已释放。主线程能启动新run并得到new回答。
6. 放行旧收尾的锁外通知；它使用旧observation/Publisher/control记录，discarded仍带旧run_id/reason=run_stopped；新run没有被混入旧控制事件，活动登记最终为空。

对先前“收尾后才释放busy”的口语总结补充精确边界：当前内存实现可在共享状态清理完成后释放busy，锁外诊断/终局通知可能稍后投递。因此不同run的事件可以交错，每个run自己的seq仍独立。不要把所有旧通知已投递作为准入条件；必要保存路径的释放位置另有合同（Lesson60/第8章）。

这两例含3/5秒线程协调窗口，连续运行并发段后看seen/worker_errors与events中的run_id；关键锁内准入、锁外通知引用源码阅读。它们不是高并发/多进程/租户安全证明。

### 例6本次验证与时间线（2026-10-14）

两个既有测试本次2 passed in0.84s；旧通知测试另有同输入只读观测（临时包装测试helper与app.run，未改文件/实现），原断言通过。新run准入前busy=False/run_id=None/steering队列空；新run返回时旧run终局尚未出现。关键到达顺序实测：old seq1 run_started→new seq1 run_started→new seq8 run_finished→old seq11 control_discarded→old seq12 run_finished。各自序号仍为old 1..12、new 1..8，不把合并到达流视为全局run结束顺序。

准入关键代码在_run_steps的with锁内：读本session→检查人工核对/审批/busy→生成run身份/observation→置busy/活动登记。检查和占用原子完成，执行图在锁外。拒绝发生在生命周期try之前，未取得资格的调用不执行活动run的finally清理。首测试用BlockingAgent验证应用准入：S1第一次进入后等待；重叠S1被拒绝未进入invoke；S2可进入；放行S1后同一RuntimeError到worker_errors，清理后再次S1可进入但stub仍抛同一异常，不能误读成第二次业务成功。

第二测试的blocked_app使用同步Fake模型，旧run在_generate等待；测试先入队steering再request_stop，放行模型后图停止，队列待丢弃。_cleanup_run锁内先control.close记录待投递事实、清队列/活动映射、释放内存busy，再锁外_record_steering；测试仅在这个日志调用前暂停旧线程，尚未到RunObservation.finish。新run得以创建独立observation/publisher并完成。旧线程放行后，用旧controls.flush与旧publisher投递discarded及run_finished，旧局部run_id不会因session当前run改变而变新。

RunEventPublisher在创建时保存session/run身份和自己的sequence；ControlObserver.close把旧请求结局放入自己的outbox，flush不重新查询当前_active_runs来选择publisher。session当前状态是准入数据，旧observation是旧通知归属，不能混用。正式消费者按session_id/run_id分流再看sequence；不能仅凭某session最后到达的run_finished覆盖它的新run状态。

两例都用内存会话。不同会话能运行不推断当前SQLite持久应用也允许多活动run，SessionPersistence另有单活动run限制，留第8章。此例释放资格前没有仍需等待的工具IO或必要保存，与上一组settle合同一致。

调试从测试入口准备，3/5秒线程协调段连续运行后看断言；不要在worker等待中长期停断点。首例重点展开_run_steps准入、_cleanup_run和测试seen/worker_errors/caught.value。第二例重点源码读pause_discard位置、close只登记/flush才投递、publisher固定身份；观察events的(run_id,sequence,event_type)，不在WATCH触发run、发布、Event.set/wait或join。

本组已讲解，用户本人调试/理解未评估；前五章主要机制与六组复盘讲解收束，下一节第六章工具注册/schema/结果。此前停止位置纠正、用户settle与循环跳转自述保留；不把绿色测试转成独立能力证据。

## 后续章节会打开哪些分支

| 位置 | 后续会展开 | 当前必须知道的合同 |
|---|---|---|
| 工具执行前/中 | 权限、参数、审批、中断恢复、并行批次 | 可能返回ToolMessage，也可能interrupt/raise；待审批会阻断普通新run |
| 模型调用前/异常后 | 预算、压缩、摘要、输入溢出恢复 | 可以替换上下文/重试或失败，内部摘要不等于普通主Agent turn |
| 应用必要收尾 | checkpoint/SQLite、恢复、保存失败 | 完整消息可见不代表保存；必要保存故障影响运行结果 |
| 外部消费/入口 | CLI、Web/SSE、回执、重同步、前端 | 消费者断开/溢出与producer生命周期分开，展示不是Agent输入状态 |

## 验证与课堂位置

本次六组选择共16个既有参数实例一起运行：16 passed in1.69s；例1另有同输入的只读观测输出（模型输入/事件16、8、8/idle）。无生产/测试实现、IDE/环境或实网变更。既有旧dirty/untracked工作保留。

材料已准备六组，不等于用户已经逐组完成。课堂已展开六组例子，例2循环跳转与例5第六点settle有用户自述理解，各组独立实现未评估。2026-10-14按用户要求补回例6后，前五章主要机制与复盘讲解收束，下一节进入第六章。疑问先定位阶段/栈帧/字段，再深入关键函数。
