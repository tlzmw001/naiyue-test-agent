# Lesson 28：应用层记录运行失败

> 2026-10-10。已讲RunObservation初始化关键字段、failed123–148、finish163–170；SessionService失败捕获入口/_cleanup_run396–417/_finish_run419–457。取消、观察器发布器内部、IO步骤及持久化细节仍按后续章节读；不记全应用已掌握。

## 具体状态与链路

每次运行有RunObservation对象；stage初始prepare，status初始completed只是默认待结果字段，非提前发布成功；failure=None。SessionService开始模型/图执行前赋stage='execute'，正常结果处理前赋finalize。

示意执行时TimeoutError→SessionService catch保留primary_error→普通路径observation.failed(error)→status='failed'、failure=RunFailureData('execute','transient','timeout','TimeoutError')→原异常raise→finally收尾→最后finish发送终局。failed本身不是raise或run_finished发布入口。

## failed123–148逐行

- error参数BaseException使入口能接控制类异常，类型注解非自动转换；stage是仅关键字可选参数，默认None。显式stage='cleanup'等可指定阶段。
- 124–127：非Exception或GraphBubbleUp特殊框架控制异常，_suppress_terminal=True，context_operations.finalize(error)后return，不伪造普通completed/failed终局。不是忽略控制，原对象继续由会话层传播。异步CancelledError在会话层选择observation.cancelled而非此普通failed路径，活动UserStop也有控制语义，后续专门读，不泛称全部BaseException都是失败。
- 128普通执行异常将status改failed。
- 129–135分类转换try：WriteOutcomeUnknown/BashOutcomeUnknown显式tool_outcome_unknown，否则模型分类；self.failure存RunFailureData。stage or self.stage先用非空显式stage，否则当前对象阶段；这里非固定execute，阶段由应用执行顺序维护。
- 136–140诊断自身普通Exception→固定日志及受限诊断类型→unknown/unclassified回退；stage保持捕获阶段，error_type来自原error。与failure_data有类似逻辑，实际如何整理待完整链路后确定，不当场抽象。
- 141–144 self.turns.fail(self.failure)交失败数据给turn观察器；报告失败用BaseException捕获/固定日志受限类型，不让该报告阻断后续上下文处理。这是报告边界，不代表运行层会吞全部控制异常。
- 145–148 self.context_operations.finalize(error)结束活动上下文操作的观察；输入原异常不是模型输入，非重试/压缩。报告故障同样记录，函数成功返回None；self状态已修改。观察器算法/发布细节后续章复访。

原异常传播在SessionService catch，primary_error=error后记录，再裸raise；finally指向_finish_run/_afinish_run。当前只讲定位与先后，不把生成器IOCall、取消及持久化全链标全部已学。终局run_finished由RunObservation.finish产生；它在正常收尾流程末尾调用，不在failed里立即发送。

## 验证

```bash
.venv/bin/python -m pytest -q tests/test_application_run_event_failures.py::test_stage_failure_keeps_original_exception_and_cleans tests/test_application_run_event_failures.py::test_failure_diagnostic_fault_does_not_replace_primary tests/test_application_run_event_failures.py::test_process_control_is_not_converted_to_completed_or_failed
```

实际6 passed in 0.53s：prepare/execute/finalize三阶段失败保留原异常并释放busy，诊断抛异常/非法码两反例回退，控制异常不转普通成功或失败终局。假应用/本地确定性证据，无真实.env/网络/产品代码测试修改或全量构建声明。

## 掌握与下一步

- 已讲解，理解待反馈，独立实现/追问未评估。
- 用户答“更新观察状态”，正确识别failed职责；阶段题未答，不补记已掌握，独立实现/追问未评估。
- 下一步SessionService _cleanup_run396–417/_finish_run419–457和RunObservation.finish163–170；看主要/次要错误、busy释放/终局顺序，再汇总职责与整理Spec。

上述收尾阅读已在后续小段完成；当前下一步转为异常职责汇总和确定整理范围/Spec。

## 收尾第一段：主要错误与清理错误

本轮只展开SessionService419–429与最后456–457的错误优先级；后半持久化/会话字段释放/终局及_cleanup_run逐行留下一段，避免一次堆完所有清理代码。

模型执行TimeoutError后，外层catch写primary_error并failed/raise，finally进入_finish_run，异常传播暂缓直到finally完成。_finish_run接原对象，不重新从日志/模型回答解析失败。secondary=None记录运行本身无错误时收尾阶段出现的首错误，不是另一个模型请求。

- 421 review_required取当前session人工核对标志作初值；422–423调用_cleanup_run，成功用其返回快照更新局部review_required供后面终局，不在这里做权限审批。
- 424捕获普通清理Exception；425–427若primary_error为None，secondary=error并failed(stage='cleanup')把原本可成功的运行改成失败，后续继续其他收尾，最后若secondary非None则raise它。
- 428–429若已有primary_error，只记受限类型的session_cleanup_failed，不覆盖主要失败数据、不把secondary改为清理错；原异常仍等待外层finally结束继续传播。清理故障并非忽略，但当前这一路只进日志，不把全部次要错误做列表携带。
- Python finally语义：已有异常正在传播时finally正常结束→继续原异常；finally再抛新异常→新异常可能遮盖原异常，所以显式选择记录/保留首错误。本段不宣称所有后续异常均已被兜住。

两个合成场景的现有测试：运行准备先RuntimeError再清理ValueError→抛同一RuntimeError，阶段prepare；运行原先正常但清理ValueError→抛同一ValueError，阶段cleanup。命令：

```bash
.venv/bin/python -m pytest -q tests/test_application_run_event_failures.py::test_cleanup_failure_is_reported_without_masking_primary
```

实际2 passed in 0.85s，同时断言busy释放/终局失败/日志无模拟正文。模拟清理故障注入在记录丢弃消息的步骤，不能宣称任意cleanup中途点的完整故障矩阵。只改学习文档，无实网/真实.env/产品测试源码改动。

本轮检查：运行已经TimeoutError，清理再ValueError，应继续向外抛哪个？待答。下一步_cleanup_run字段与_finish_run430–457持久化/释放/终局，以及finish163–170，闭合本链再汇总。

## 收尾第二段：会话字段与持久化门禁

本轮读396–417与430–449；终局publish450–457/finish163–170下一段。用户继续，未答上一检查，不补记已掌握。

具体阶段：busy=True、run_id='r001'→关闭本轮控制/清队列/清提示词快照→无persistence可立即busy=False/run_id=None；有persistence保持忙直到finish尝试完成→finally释放。busy用于运行占用，持久化ready/blocked用于后续可否安全启动，不能混用。

- 397锁内修改共享会话；398 manual_review_required保存局部快照返回，后续终局不用重新从可能已被新run改动的会话读取。
- 399–400 turn.snapshot以pending_tool_approval是否非None作条件，当前实现仅pending且有state且phase为suspended/failed时返回有限延续字典，否则None，赋pending_turn。不是完整消息历史或checkpoint。
- 401–404先取两队列长度，再clear；未交付steering/follow-up在此run退出时丢弃并记录数量，不假装已注入模型。
- 405 controls.close只关闭当前控制登记并准备未交付/未知交付事实，不发模型调用；close_reason按control_exit、prepare_failed或run_+status，复杂条件拆开解释。真实flush发布稍后执行，锁内不主动消费所有订阅者。
- 406移除session_id到当前RunObservation的活动映射，pop缺失返回None；407关闭steering窗口；408复位本轮stop标志；409释放本轮项目指令快照None。关闭控制与取消远端请求不等价。
- 410–412无持久化时清busy/run_id；有持久化时此处故意保留忙，防新run抢先改变会话/持久元数据。413–416锁外记录剩余消息丢弃计数，417返回review_required快照；原审批/人工核对标志不在此重置。
- 430–432有持久化才调用finish，确认/保存结束状态与checkpoint关联；图checkpoint与应用元数据不同，存储算法第8章详细读。异步对应_afinish_run调用await persistence.afinish，共用_cleanup_run，不把同步IO语法直接说成原生异步。
- 433–438持久化失败仅在primary_error和secondary均None时成为首错误，failed(stage='finalize')；已有任一前错只记录次要保存故障，保留首错。与上一段同一错误优先级，不额外模型重试。
- 439–449 finally在有persistence时锁内释放busy/run_id，复位控制/快照、清两队列/移出active map；包括持久化失败后的释放，并补齐前清理半途出错可能留下字段，不宣称任意错误都不会影响收尾。

重要边界：保存失败释放运行占用，但persistence._finish_steps会登记blocked并抛SessionPersistenceError(session_save_unconfirmed)，后续运行被门禁阻止；不是busy=False就可继续，也不冒充保存成功。当前分类语义有未细分项，统一整理时按职责汇总，不在课堂改规则。

```bash
.venv/bin/python -m pytest -q tests/test_async_persistent.py::test_metadata_commit_wait_keeps_loop_responsive_and_busy tests/test_persistent_sessions.py::test_final_save_failure_has_no_completed_and_blocks_uncertain_state tests/test_persistent_sessions.py::test_primary_model_failure_survives_secondary_store_failure tests/test_application_run_events.py::test_success_events_follow_cleanup_without_changing_result
```

实际4 passed in 0.82s：事件同步阻塞提交期间busy且无finished、释放后完成；提交失败无completed/释放busy并阻止重跑；模型首异常不被保存次错替换；正常终局消费时字段已清理。假模型+临时SQLite/同步事件证据，无真实.env/模型网络/产品测试源码变化，不称全量构建或所有存储故障验证。

检查：保存失败后busy=False，是否就能正常开始下一次运行？待答。当前唯一下一步finish及终局交付故障450–457/163–170，再汇总异常职责与整理Spec。

## 收尾第三段：终局发布与发布故障

本轮读RunObservation.finish163–170、SessionService450–457及RunFinishedData58–72。用户继续，未答上一检查，不补记已掌握。

- controls.flush/context_operations.flush先尝试发布已准备的观察事实，再发运行终局；不是重跑控制、工具或压缩。订阅者/发布器内部详细机制第4章，不把flush简单称SDK流打印。
- _suppress_terminal=True则跳过普通run_finished，对应控制退出等特殊路径，不虚构成功/失败终局。False则构造RunFinishedData并publisher.publish。
- payload按位置传status、stop_mode、manual_review_required局部快照、failure；仅stopped取active，其余None。四种status：completed正常结束、stopped主动停止、interrupted审批等暂停、failed执行/收尾失败；completed不证明回答质量，failure仅failed时为RunFailureData。
- RunFinishedData.__post_init__校验状态成员、stop_mode与状态对应、人工核对严格bool，(status=='failed')!=isinstance(failure,RunFailureData)是两布尔必须一致：failed缺详情或非failed带详情都拒绝。
- publisher把payload放外层关联event，Web wire_event用asdict输出version1/关联字段/data；run_delivery识别run_finished取status/manual_review/failure。这里只定位消费者，SSE/投影完整算法留第9章。
- 发布正常后，primary_error仍由外层raise/finally继续传播；若secondary存在，_finish_run末尾raise它；事件发布不是异常替代，也不是进程永久退出。
- 450–455：finish过程普通异常且primary_error/secondary/observation.failure均None时裸raise该发布异常；否则受限日志run_event_publish_failed，保留既有失败。不保证发布失效时每个消费者收到终局，不递归尝试伪造新成功/失败事件。
- 发布器对普通订阅回调异常可隔离/本run停用，和发布入口生命周期/结构故障区别；本轮测试直接让publish(run_finished)抛异常验证主异常保留，不说任意消费者故障都必让app.run失败。
- snapshot使用本run局部review_required/私有observation/publisher关联，避免busy释放后新run改变session时污染旧终局；顺序per-run，不是全局跨run绝对不交错。

四正常payload及三拒绝字段组合合成探针通过，无网络。测试：

```bash
.venv/bin/python -m pytest -q tests/test_application_run_event_failures.py::test_terminal_publish_fault_does_not_replace_primary tests/test_run_events.py::test_faulty_observer_is_isolated_per_run_and_not_exposed_in_logs tests/test_application_run_events.py::test_old_terminal_uses_cleanup_snapshot_when_new_run_changes_session
```

实际3 passed in 0.52s：发布入口失败不替换主异常、普通observer故障隔离、旧终局不读新run人工核对状态。局部确定性证据，未新测首次发布故障全部场景/网络交付/生产压力，未改产品或测试源码，不称全量构建。

完整主链：模型/框架异常→模型分类/模型失败事件→应用保留primary_error并failed→finally清理→持久化结束状态→释放占用→flush观察/发run_finished→传播原异常或收尾首错。可恢复工具错误/Graph控制/取消另有专门路径，当前图不是全异常统一硬停。

本课核心主链已讲，用户理解待反馈/独立实现与追问未评估。当前下一步：异常职责汇总→明确最小整理范围并写Spec；不把学习顺序确认当外部重试/新异常基类授权。检查：已有TimeoutError时终局发布再ValueError，外部优先得到哪个？待答。
