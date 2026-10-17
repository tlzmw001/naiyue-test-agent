# Lesson 57：turn、控制与上下文过程事件总图

> 第四章第6主题/收尾，2026-10-13。观察事实与执行政策分开；当前测试由助手核验，用户本节及delta本人走读/复述未评估。发布器保持用户已确认实现。

## 三类问题

| 事件组 | 记录什么 | 负责对象与执行实现边界 |
|---|---|---|
| turn_started/finished/suspended/resumed | 一轮主模型响应及该轮工具批次的观察状态 | TurnObserver；模型/工具执行在框架/runtime，审批实现第6章 |
| control_accepted/delivery_started/delivered/applied/discarded/outcome_unknown | 请求被接受、开始交付、消息可见或实际动作生效、未交付/未确认 | ControlObserver记录；真实队列/stop/审批由应用与runtime执行，第5/6章 |
| compaction_started/finished、context_recovery_started/finished | 上下文操作开始及结果/状态替换确认 | ContextOperationObserver记录；切点/摘要/恢复算法第7章 |

这些是按场景插入的观察，不是每个run固定执行全部事件的业务流程。发送事件不实施对应操作，观察完成也不替代必要保存或业务质量验证。

## 普通turn主线

```text
run_started
  turn_started T1 → 主模型响应M1 → 本轮工具批次及消息可读 → turn_finished T1
  turn_started T2 → 主模型最终回答M2及消息可读 → turn_finished T2
run_finished
```

省略同组模型/工具/完整消息/delta事件，非完整日志。一条无工具文本回答只有一个turn；多个工具属于该响应的同一turn。可恢复错误工具结果仍可完成turn，error_result_count保留失败事实。turn_finished不代表整个run已收尾。

审批暂停分支：R1中T1 suspended，R2恢复时turn_id仍T1、origin_run_id仍R1，事件外层run_id为R2；之后才开始新turn。已有tests/test_application_turn_events.py::test_approval_pauses_and_resumes_same_turn_then_starts_next[approve/reject]证明该合同，本轮不重跑也不深入审批策略。

## 控制事实分支

Steering/Follow-up：accept→select/delivery_started→生成带真实ID的HumanMessage→观察匹配ID/角色→delivered；仅同正文不能证明请求交付。已选择但没确认可读→outcome_unknown；尚在队列、run关闭→discarded。Stop/Approval用applied记录相应真实结果/审批hook边界，accepted不等于已生效。控制故障与执行异常合同引用第二章。

## 上下文事实分支

Compaction：started→准备预期summaryID/保留消息顺序→观察替换→applied。看到旧summary或准备动作返回不够；终局未见期待结构→unconfirmed。压缩还可skipped/fallback/failed，恢复可recovered/failed/unconfirmed，按实际路径决定。applied表示本次期望替换结构可读，不证明摘要事实保真/估算精度/持久保存。

## 可独立运行入口

| 主题 | 用例 |
|---|---|
| 一轮纯文本 | tests/test_application_turn_events.py::test_text_response_is_one_turn_inside_run_and_finishes_after_message |
| 工具批次→最终回答两轮 | 同文件test_one_tool_batch_and_final_answer_are_two_turns[sequential]，parallel按需 |
| 同正文两个控制请求 | tests/test_control_observation.py::test_start_gate_and_same_text_have_distinct_control_and_message_ids |
| 选中未确认/尚未取出 | 同文件test_selected_not_visible_is_unknown_and_queued_is_discarded |
| 预期摘要替换确认 | tests/test_context_operation_observer.py::test_only_expected_summary_and_retained_order_confirm_replacement |
| 替换未确认 | 同文件test_unconfirmed_state_is_diagnostic_not_a_fabricated_applied_result |

本轮以上6例6 passed in0.82s，分别为框架/应用确定性集成与观察器单元。无新生产/测试实现、模型网络或保存质量证据；测试辅助CheckedPublisher的额外跨事件校验不作为生产Publisher行为。

走读优先工具turn的sequential参数，入口barrier=...；测试末first/second形成后看data.turn_id/model_call_id/assistant_message_id/tool_call_ids/tool_message_ids及sequence。控制单元看accepted/delivered的control_id/message_id配对，不深入队列算法；上下文单元观察old-summary不触发applied、new-summary+kept匹配才触发。复杂执行内部暂用合同定位，留后续章。

## 第四章收尾与下一步

执行事实→观察器/payload→统一RuntimeEvent→有序Publisher→订阅快照/异步桥→消息投影。分清回调交付、图状态可读、动作生效、最终运行与必要保存；各层观察可以降级，不能替代真实执行结果。

六主题主要讲解范围收束，用户已有publisher订阅主链走读自述、完整消息概念自述，其他能力不自动验收。下一步第五章session/thread/run归属与busy准入；第五章尚未开课，不提前建Lesson58。
