# Lesson 37：用户停止由异常转换为stopped结果

> 2026-10-10。本课只追原生异步模型等待中用户request_stop、且没有活动工具的路径。Lesson36门禁题未答，保留未确认。停止所有工具/线程、外部取消、Graph控制仍非本课覆盖。

## 一条数据流

request_stop(session)→记录会话停止信号/RunControl.request→取消登记的本地模型task→await_model识别用户停止产生UserStop→模型观察捕获/生成描述/报告/裸raise→外层StopModelMiddleware捕获UserStop并返回图控制结果→StopBoundaryMiddleware安全结束→App收到result.run_stopped=True→status=stopped→收尾后run_finished(stopped)。UserStop没有沿普通失败路径最终抛给App调用者。

## 各对象与状态

- 用户停止不是SDK网络故障；RunControl.await_model在收到停止信号且调用方未被外部取消的条件下，将被取消的模型子任务CancelledError转换为UserStop。UserStop继承BaseException，项目边界显式捕获，普通except Exception不会自动捕获。
- Policy保护分类为source=runtime/category=cancelled/reason=user_stop/error_type=UserStop，绕过自定义分类器；描述报告只是保存，不自动设置App failure。
- 模型调用事件model_call_finished.status=failed，failure={stage:execute,category:cancelled,reason:user_stop,error_type:UserStop}，表示该调用未正常完成。
- StopModelMiddleware.catch UserStop返回ExtendedModelResponse(ModelResponse(result=[]),Command(update={run_stopped:True}))，不伪造AIMessage。此处还有turn观察的固定CancelledError字段记录，本课仅定位、不记turn事件已逐行通读。
- 本例无待执行工具，StopBoundary在after_model看到停止信号，返回messages=[]/run_stopped=True/jump_to=end，停止图继续调用模型；详细待工具协议补齐算法留工具/控制章。
- SessionService走正常结果分支observation.result，pending=None/result.run_stopped=True→status=stopped，未走observation.failed，failure仍None。收尾后RunFinishedData(status=stopped,stop_mode=active,manual_review_required=False,failure=None)，busy=False。
- 模型调用failed不等于run终局failed；描述/事件提供事实，控制处理中途捕获并转换结果决定最终运行状态。停止请求回执不等于已经收尾/释放busy。

## 验证

使用现有tests/test_async_application.py的fake原生异步模型与make_app、MonkeyPatch.context，不读真实配置；Event同步等模型进入等待，再request_stop，真实图和App收尾后打印模型与run事件。首次仅打印误用ModelCallFinishedData.outcome（实际字段status）触发探针AttributeError；修正实验字段后全部断言通过，无产品缺陷/源码修复。

实际输出：model call status=failed / failure execute,cancelled,user_stop,UserStop / result.run_stopped=True / terminal stopped,active,False,None / message types=[human] / busy=False。没有模型正常回答，也未将UserStop抛给app.arun调用者。

```bash
.venv/bin/python -m pytest -q tests/test_active_stop.py::test_stop_cancels_model_without_fake_answer_and_can_start_again
```

实际`1 passed in 0.51s`，停止等待、无伪回答、安全checkpoint/清理、同会话后续可运行。fake模型、本地证据，不宣称供应商服务端计算取消或任意工具线程强杀。本课只改学习文档，无产品/测试/.env/依赖修改、不提交push，未做全量构建。

## 理解与下一步

- 已讲解本例，理解待反馈；独立追踪/实现/追问未评估。
- 检查：本例模型调用事件status=failed且reason=user_stop，运行终局应是failed还是stopped？待答。
- 唯一下一步对照外部task.cancel的保留CancelledError/failed路径，再补普通运行分类与App未报告回退；一次一个数据流。
