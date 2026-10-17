# Lesson 59：IOCall与生命周期驱动

> 第五章第2主题，2026-10-13。同步next/send/throw主链，再对照异步执行分支。用户本人走读/复述/独立实现未评估。

## 数据与职责

IOCall保存call、args、kwargs、可选async_call与critical，是待执行操作的描述。构造IOCall不执行call；_run_steps是生成器，调用它先得到生成器对象，next才开始执行。流程选择操作、处理结果/异常并决定收尾，driver执行操作并回传，不承担会话策略。

```text
_run_steps --yield IOCall--> run_steps --调用操作--> 返回值或异常
_run_steps <--send(value) / throw(error)-- run_steps
```

next(steps)执行到首个yield并取出IOCall；pending.call(*args,**kwargs)执行实际操作；send(value)让暂停的yield表达式得到value，流程继续到下一yield，下一IOCall成为send返回值。yield两侧传递的是不同数据，不要混淆操作描述与执行结果。

## 同步正常与异常路径

无workspace/persistence/approval的正常应用测试：准备资源→执行Agent→检查待审批→收尾四个IOCall。return result之前仍执行finally中的收尾yield，收尾后恢复待完成的return；生成器通过StopIteration.value交出业务结果。driver最后执行操作的result可能是None，最终返回业务结果来自completed.value。

执行故障测试用monkeypatch把session_service.invoke_agent_run替换为fail；fail抛预创建RuntimeError，driver捕获后steps.throw(error)在流程暂停的yield处抛入同一异常。流程记录失败、裸raise，finally仍yield收尾IOCall；throw这次返回该IOCall。driver完成收尾再send(None)，原异常继续向外传播，由pytest.raises捕获，busy解除。throw不必立刻向外抛错，它可能先产生下一操作。

steps.close是退出driver时关闭生成器，不是替代消费收尾IOCall的通用方案。BaseException捕获还用于取消等信号，具体优先级留后续课。

## 异步对照与边界

arun_steps使用同一同步生成器和next/send/throw。没有async_call时await run_blocking(call,...)；有async_call时构造awaitable，critical=True用await_settled，False直接await。异步驱动改变操作的执行方式，不要求把流程生成器改成异步生成器。线程IO/settle/取消合同留第6主题，asyncio内部本课步过。

## 既有测试与阅读入口

- tests/test_application_run_events.py::test_success_events_follow_cleanup_without_changing_result：正常4次IO往返，实际Fake模型/Agent执行与内存saver。
- tests/test_application_run_event_failures.py::test_stage_failure_keeps_original_exception_and_cleans[execute]：执行边界注入同一个RuntimeError，未执行模型，断言原异常身份、失败stage与busy释放。
- tests/test_async_application.py::test_formal_arun_preserves_history_events_and_cleanup：异步正常历史、终局与busy合同，不覆盖本课全部取消/settle分支。
- 本次三例3 passed in0.78s；无新生产/测试代码或实网请求。
- 测试入口断点后F11进入run_steps，next/send/throw展开到_run_steps的yield前后；pending.call实际操作F10，观察者、模型、保存等内部看合同。异常测试展开fail确定raise位置，随后重点观察throw交出的_finish_run。
- driver帧WATCH：pending.call.__name__、pending.args、pending.kwargs；执行后result，捕获时error；StopIteration分支看completed.value。流程帧看instructions/resources/result/primary_error产生时点，不重新执行有副作用函数获取结果。

下一步第3主题一次run的准备、执行、结果处理和必要收尾；已讲driver仅引用，不重复展开。
