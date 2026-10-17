# Lesson 60：一次run从准备到收尾

> 第五章第3主题，2026-10-13。IOCall驱动引用Lesson59；本节讲阶段职责、状态/事件与必要收尾。用户本人走读/理解未评估。

## 主链与数据

```text
准入（锁内busy/run记录，Lesson58）
 → prepare：start事件、可选持久化start、prepare_run_resources
 → execute：bind RunServices、invoke_agent_run或带资源入口
 → finalize：检查pending审批、保存pending/公开请求、observation.result
 → return结果或raise原异常（都要先经过finally）
 → _finish_run：_cleanup_run → 可选persistence.finish → observation.finish
```

prepare产出instructions/resources，指令保存在本次会话字段，资源随执行参数传递；execute产出消息result；finalize检查_pending_tool_approval，pending不为空则会话记录审批阻断并标记interrupted，内部策略留第6章。finalize是结果处理及保存故障可用的stage标签，finally是Python必经控制结构，两者不同。

stage定位故障发生位置，status描述运行结果；observation.result或failed记录状态，不释放busy，最终run_finished由收尾末端尝试发布。RunObservation的初始completed也不是执行成功证据。

## 分支与收尾

| 分支 | 合同 |
|---|---|
| 正常完成 | 结果处理→finally收尾→终局→调用者得到result |
| prepare/execute/finalize普通异常 | primary_error保留原对象、failed记录对应stage、finally收尾、原异常继续传播 |
| 先前无异常、cleanup失败 | 保存secondary，failed(stage=cleanup)，继续后续收尾，最终raise secondary，原成功return不成立 |
| 已有primary、cleanup也失败 | 记录清理故障，保留原失败描述与原异常，继续收尾 |

_cleanup_run清控制队列、关闭输入窗口、移除active run、清本次指令，并快照必要pending turn；内存分支直接busy=False/run_id=None。持久化分支先保留busy，persistence.finish尝试后在finally释放标记；必要保存失败会影响终局并传播，不作为观察故障忽略，存储一致性内部留第8章。busy=False不能消除审批/人工核对阻断。

_finish_run的普通Exception处理先保留primary，否则保留第一个secondary；必要保存次要故障记录而不覆盖既有错误。BaseException控制退出、外部取消、停止内部留后续主题。本课不宣称所有清理故障都能释放busy。

## 测试与阅读深度

- tests/test_application_run_events.py::test_success_events_follow_cleanup_without_changing_result：事件回调保存会话状态，run_started时busy=True，run_finished时busy=False/run_id=None；仅验证当前内存分支。
- tests/test_application_run_event_failures.py::test_stage_failure_keeps_original_exception_and_cleans[prepare/execute/finalize]：分别替换prepare_run_resources/invoke_agent_run/_pending_tool_approval；原异常身份、stage、busy与安全诊断断言保留。
- 同文件test_cleanup_failure_is_reported_without_masking_primary[False/True]：队列留项使_cleanup_run尾部调用_record_steering，在此注入ValueError；此前队列/内存busy已清理。False返回cleanup异常，True保留prepare主异常；不能泛化为在_cleanup_run开头抛错也必然释放busy。
- 本次以上6例6 passed in0.81s；无生产/测试代码、配置或实网更改，不包含真实持久化验证。
- 必须展开：_run_steps的stage切换、结果/pending分流、except/finally；_finish_run的primary/secondary判断；_cleanup_run状态修改。模型/工具/保存内部、观察器/发布器和已学driver按合同步过。
- 测试入口默认单步；优先[finalize]复访结果已产生但结果处理失败。流程帧看observation.stage/status/failure、primary_error、session['busy']；收尾帧看primary_error/secondary及busy；成功测试帧看states的busy/run_id投影。

下一步第4主题steering/follow_up真实队列、交付边界和优先顺序；第四章control观察结果只引用。
