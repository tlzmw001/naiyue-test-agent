# Lesson 50：四种调用接口与三类流输出

> 第三章第7主题，2026-10-12。确定性合同与等待/分片边界已核验；用户本人走读和掌握未评估。

## 两个维度

| 接口 | 调用方式 | 本课普通路径的输出 |
|---|---|---|
| invoke | 同步调用，调用方等待执行完成 | 最终状态字典 |
| ainvoke | await异步调用，当前协程等待完成 | 最终状态字典 |
| stream | for消费同步迭代器，每次取值可能等待 | 逐项流输出 |
| astream | async for消费异步迭代器 | 逐项流输出 |

stream与astream本课显式stream_mode=['updates','values']、version='v2'，并非逐字答案；生成器创建与消费执行分开，异步调用需要await或调度。流式不自动成为异步，异步不自动提供分片；外层async不证明模型/工具/存储每层都原生非阻塞。

## 实际数据合同

v2流项目形状：{'type':'updates'或'values','ns':(), 'data':...}。updates的data按节点名分组，只含本次写入；values的data为当时完整状态。messages模式则产生模型消息/分片及元数据，粒度依赖模型实现与路径，不把任意part当字符串。

当前四接口同任务测试：[test_agent_middleware_order.py](../../../tests/test_agent_middleware_order.py)::test_call_modes_share_loop_but_differ_in_output。参数invoke/ainvoke/stream/astream。同步FakeMessagesListChatModel与echo_probe；async默认线程回退，不是原生IO证据。为第三次重复的bind_tools提取该文件内ToolCapableFake一方法基类，前两组测试不改行为/断言，无conftest或生产变更。

实际updates节点顺序model→tools→model；values消息类型human→human,ai→human,ai,tool→human,ai,tool,ai；四参数最终回答done、工具只执行一次、call-1配对。

## 两项独立证据

- [test_async_model_middleware.py](../../../tests/test_async_model_middleware.py)::test_native_model_wait_does_not_block_other_coroutine：_generate禁止，_agenerate等待release Event；模型任务未完成时另一协程执行，再release后返回answer。证明该确定性原生异步模型等待边界，不扩张为所有IO/CPU或生产负载。
- [test_async_application.py](../../../tests/test_async_application.py)::test_delta_is_available_before_response_and_final_snapshot_calibrates：原生异步流假模型分两片输出hello，Event暂停中间；应用消费者看到he/provisional=True，任务未完成；释放后最终hello/provisional=False且只有一条最终消息。是应用集成边界，本节只看输入/输出合同，事件投影留第4章。

正式模型/工具wrapper异步路径为awrap_model_call/awrap_tool_call；上一节只实现同步wrapper的教学对象不能推断自动支持ainvoke。框架部分同步模型/工具可线程回退，线程工作能排空不等于强制终止；停止留第5章。

## 命令与观察

```bash
.venv/bin/python -m pytest -q 'tests/test_agent_middleware_order.py::test_call_modes_share_loop_but_differ_in_output[stream]'
```

先invoke、stream，再ainvoke、astream。参数测试入口calls=[]；for/async for当前帧看mode、part['type']、part['ns']、part['data']；测试帧看calls、parts，result只在完成后存在。流结果list化只用于测试断言，不代表生产必须缓存全部事件。

本轮整测试文件10 passed in0.21s；两个既有异步边界测试2 passed in0.74s；compileall该文件及diff检查退出0。没有真实供应商、端到端原生非阻塞或全量回归新证据。

源码核对：本机langchain1.4.2/langgraph1.2.12的Pregel四接口；BaseChatModel._agenerate使用run_in_executor回退，StructuredTool无coroutine时亦回退；应用async_message_observation消费messages/updates/values并把文本分片与完整消息确认分开。

当前下一步：本人走读四接口对照；第三章还可用已有测试综合串起装配、状态、hook、wrapper和context，随后再进入第4章，不因助手测试通过宣称用户掌握。
