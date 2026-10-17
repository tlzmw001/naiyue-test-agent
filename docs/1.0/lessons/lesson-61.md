# Lesson 61：steering与follow_up的实际交付

> 第五章第4主题，2026-10-13。第四章control观察只引用结果；本课讲实际队列、hook、消息更新与输入窗口。用户本人走读/理解未评估。

## 合同与数据流

steer/follow_up都要求非空文本、会话busy且steering_open；锁内向本会话对应deque追加正文、登记观察请求，锁外发布记录。调用接受不改变已经发出的模型请求。steering在下一次模型调用前可取出；follow_up只在准备自然结束的边界取出。正常继续沿用本次session/thread/run，不由队列自动创建新run。

```text
steer → steering_queue → before_model inject_before_model → drain_steering
  → prepare_control_messages → {'messages': HumanMessage列表} → 状态reducer → 模型
follow_up → follow_up_queue → after_agent inject_at_natural_end → finish_run_boundary
  → prepare_control_messages → {'messages': ..., 'jump_to': 'model'} → 下一模型循环
```

before_model先检查停止，再一次取出全部steering。自然结束的finish_run_boundary在同一锁内选择stop→全部steering→一条follow_up→关窗并自然完成；类别内FIFO，类别间不是按提交时间全局FIFO。priority是本次边界选择，不能当作对所有未来请求的全局排序保证。

返回messages是图状态更新，第三章已讲add_messages；prepare_control_messages关联第四章请求/消息ID。当前LangChain factory的after_agent model_destination是loop_entry_node，有before_model时该入口是首个before_model hook；jump回模型循环会经过before_model，不重跑before_agent。after_agent在本项目可继续图执行，不是finally或应用run收尾。

## 适合完整单步的现有测试

tests/test_application_turn_events.py::test_steering_and_follow_up_create_turns_when_delivered_not_when_queued：probe工具内用holder['app']/holder['session']提交steer与follow，直接返回tool body，没有Event等待超时。测试fake先返回probe调用，再返回second，最后third。

```text
Human initial → AI工具调用 → probe内两类入队 → Tool tool body
 → before_model取steer → Human steer → AI second
 → after_agent取follow并跳转 → Human follow → AI third
 → 两队列为空关窗 → 应用收尾
```

测试断言3个turn、1个run；两类入队时仍只有第一个turn_started。工具完成后steer加入，follow等当前任务无继续工具调用、准备自然结束才加入。holder只是测试闭包中的应用/会话引用，不是项目运行机制。

## 分支与窗口

| 情况 | 结果 |
|---|---|
| 工具执行中接受steering | 工具结果回填后，下一模型入口加入 |
| 工具执行中接受follow_up | 紧接工具的模型调用不加入，等自然结束检查点 |
| 正文回答生成中接受steering | 当前调用先返回，再在自然结束边界加入并继续 |
| 自然结束时两类都有 | 全部steering优先，follow_up留队，一条一条执行 |
| 两队列空 | 锁内steering_open=False；随后可能仍busy直到收尾 |
| 普通模型失败且输入尚在队列 | 按第3节清理/丢弃，保留原异常 |

窗口判定与队列选择共用服务锁，避免关窗后仍接受输入。busy=False之外还有审批/人工核对准入合同，引用Lesson58；停止留下一课。

## 验证与阅读深度

- 主单步入口上述turn测试：1 passed in0.51s。
- steering工具后FIFO/正文期间继续，follow_up FIFO/工具后延迟/steering优先/失败丢弃/关窗共7个既有代表性测试：7 passed in0.87s。每条分支对应tests/test_application_steering.py或test_application_follow_up.py中的同名行为测试；这7例有3秒等待窗口，连续运行并发段后查看inputs/calls/outcomes。
- 必须展开SessionControlMixin.steer/follow_up的准入与append、drain_steering、finish_run_boundary；运行层inject_before_model/inject_at_natural_end的返回更新与jump。步过日志/观察器、模型/框架、已学IOCall与准备/收尾。
- 测试入口断点；probe函数生成/装配先步过，真正执行probe时展开两个控制调用。service方法帧WATCH：list(session['steering_queue'])、list(session['follow_up_queue'])、session['busy']、session['steering_open']；hook帧看messages、prepared、stopped，测试帧结果形成后看[(m.type,m.content) for m in result['messages']]。

无生产/测试实现、配置或实网变更。下一步第5主题用户停止与外部取消。
