# Lesson 49：显式context与动态选模

> 第三章第6主题，2026-10-12。已有机制只补本章调用点；助手验证测试与请求身份，用户复述/本人走读未评估。

## 数据来源与边界

调用方invoke传context={'session_id':'s1'}→框架Runtime.context→模型节点将runtime放入ModelRequest→选模middleware读取request.runtime.context→resolve_model(s1)→handler(request.override(model=实际模型))。

messages是模型会话内容；本例context供运行代码查找会话选择，没有自动成为模型消息。RunContext为TypedDict，不能把字段声明当非空值检查，_prepare显式验证字符串。与第二章RunServices的ContextVar绑定不同，本例上下文由调用方显式传入，不依赖服务绑定。

## 当前分支与已有测试

代码入口：[tests/test_agent_runtime.py](../../../tests/test_agent_runtime.py)。均无参数化选择。

| 场景 | 用例 | 结果与边界 |
|---|---|---|
| 同图、不同会话 | test_model_selection_is_isolated_by_session | s1→A、s2→B，按会话查找；未证明并发/历史隔离 |
| 同调用、选择被工具改动 | test_switches_model_after_tool_result_in_same_run | context仍s1，每次模型调用重新解析，A→工具→B；不创建第二Agent |
| 缺少会话ID | test_missing_session_id_fails_before_model_call | _prepare抛ValueError，resolve_model未执行，未进入模型handler |

正式应用的SessionService.resolve_model读取会话model_id并从catalog取对象；select_model负责修改选择。运行层接收回调，不import应用服务。首个测试selected字典是合成会话表，不是正式存储；源码入口application/session_service.py167/176、agent_runtime/call_observation.py55/170。

## 请求修改的实际观察

同一切换测试临时观察：两次都是新请求、runtime对象保持；第一次新/原请求model同为A，第二次新请求为B、原请求仍A。override创建新ModelRequest，不修改会话或原请求；默认模型仍是每轮初始请求的模型，wrapper按当前选择覆盖。观察断言通过，原测试断言通过。

## 命令与走读

```bash
.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_model_selection_is_isolated_by_session tests/test_agent_runtime.py::test_switches_model_after_tool_result_in_same_run tests/test_agent_runtime.py::test_missing_session_id_fails_before_model_call
```

本轮3 passed in0.76s，额外上下文/请求身份断言PASS；无新生产/测试实现、真实供应商或并发证据。

先选不同会话用例，入口selected=...断点。测试帧看selected、selections；_prepare帧看request.runtime.context、session_id，解析后看model_id及model is request.model。override后的新请求在下游handler帧观察，不能在调试表达式里重新执行handler/resolve_model产生记录或请求。

第三章六个主体机制已提供讲解材料，用户掌握仍待本人表现；后续主题区分invoke/ainvoke/stream/astream的执行与输出，再按需综合走读。
