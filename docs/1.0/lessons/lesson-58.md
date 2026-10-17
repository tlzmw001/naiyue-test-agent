# Lesson 58：session、thread、run与busy准入

> 第五章第1主题，2026-10-13。两条同步确定性测试；已讲解，用户本人调试/复述/独立实现未评估。

## 职责与正常链

SessionService._sessions[session_id]保存thread_id、model_id、busy、活动run_id及控制/审批状态，不直接保存messages。thread_id是LangGraph/checkpointer状态键，不是操作系统线程。创建会话时session/thread各生成ID，当前一会话绑定一thread；每次普通run生成新run_id。

```text
S1 → T1 → R1、R2
S2 → T2 → R3
create_session → _create_session → run → run_steps驱动_run_steps
  → 锁内准入/标记busy → invoke_agent_run → finally/_finish_run → _cleanup_run
```

同一Agent/内存saver供多个会话使用；同thread延续消息历史，换模型不换thread，另一个thread隔离历史。invoke入口context携带session/run，config.configurable.thread_id指定历史。Fake模型响应游标与会话历史是不同状态；无重启/持久化恢复证据。

## busy分支

| 情况 | 结果 |
|---|---|
| idle且其他条件满足 | 同一锁内检查busy并设True、记录run，锁外执行 |
| 同会话busy | 准入处raise SessionBusyError，未进入Agent，也不执行原活动run的收尾 |
| 另一会话idle | 独立准入；服务锁没有持续包住Agent执行 |
| 已准入run抛普通RuntimeError | 记录原异常，finally执行收尾，调用方仍收到同一异常对象 |
| 本课无持久化的清理 | _cleanup_run置busy=False、run_id=None；thread保留 |

准入还检查manual_review_required/pending_tool_approval；解除busy不是所有情况下都能再次运行。本课普通RuntimeError不触发这两种阻断。持久化释放busy路径另讲，不能泛化内存分支。

## 阻塞测试时序

BlockingAgent替代实际Agent，seen记录进入invoke的session/thread。worker执行S1并通过started告知已进入invoke，等待release；main拒绝第二个S1请求并成功执行S2，再release并join；worker抛预设failure，经收尾由run_first收集。最后再次S1进入invoke并抛同一failure，证明准入恢复，不证明新一次业务成功。

## 阅读与测试入口

- tests/test_application_session.py::test_sessions_continue_history_switch_models_and_isolate_threads：首课前半运行，1 passed in0.70s，验证历史/切模型/隔离/不同runID。
- tests/test_application_session.py::test_same_session_busy_rejected_other_session_runs_and_failure_releases_gate：后半运行，1 passed in0.76s，验证busy/跨会话/失败释放/原异常身份。
- 必须展开：_create_session状态写入；_run_steps锁内busy检查/写入；BlockingAgent.invoke与run_first异常收集；_cleanup_run内存释放条件。
- 输入输出：invoke_agent_run的context/config、_finish_run、run_steps驱动（第2节展开）。步过fake/模型/框架/日志与观察器。
- 阻塞测试wait/join上限3秒，不适合在并发窗口长时间单步。worker.start至join连续运行，可在assert not worker.is_alive处设临时断点；准入/清理关键源码单独阅读。不改超时或IDE配置来掩盖失败。
- 测试帧WATCH：{sid: (row['busy'], row['run_id'], row['thread_id']) for sid,row in service._sessions.items()}、seen、worker_errors。最后again调用前seen只有S1/S2两项；_run_steps帧看session_id和session['busy']。

无生产/测试代码修改、无实网请求。下一步第2节IOCall与next/send/throw同步驱动，再对照异步驱动。
