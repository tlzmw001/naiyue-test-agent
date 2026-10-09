# Lesson 6：一次工具调用怎样形成安全事件

状态：2026-09-30 用户反馈“这块我是能理解的”，记为对工具事件切片的**自述理解**。此前已用成功、抛异常和错误 `ToolMessage` 的具体数据流讲过整体机制；本章的每一行尚未由用户逐行追踪或独立复写。本切片由助手实现，测试通过不等于用户已能独立编写或回答面试追问。

## 从一个具体例子开始

示意输入：本次 `run_id=r1`；模型返回 `AIMessage(tool_calls=[{'name': 'read_probe', 'args': {'value': 'secret'}, 'id': 'c1'}])`。工具返回 `ToolMessage(status='success', content='private-result', tool_call_id='c1')`。框架先把调用交给项目的 `_record_tool_call`，随后由工具 handler 执行，最后把 `ToolMessage` 回填到消息状态，再进入下一次模型调用。

项目只记录 `tool_started(r1, c1, read_probe)` 和 `tool_completed(r1, c1, read_probe)`；`args.value` 与 `ToolMessage.content` 不进入事件字段。若工具抛异常，第二个事件为 `tool_failed`，原异常继续向上，运行层记 `run_failed`。若框架返回 `ToolMessage(status='error')`，同样记 `tool_failed`，但错误结果仍交回模型，Agent 后续可能正常结束；工具失败和整次运行失败不是同一个判断。

## 当前代码入口

| 入口 | 阅读重点 |
| --- | --- |
| `naiyue_test_agent/agent_runtime/__init__.py:83-111` | `@wrap_tool_call`、`request.tool_call`、`request.runtime.context`、`handler(request)` 及两条失败路径 |
| `naiyue_test_agent/agent_runtime/__init__.py:122-133` | middleware 被 `build_runtime_agent` 真正传给 `create_agent` |
| `naiyue_test_agent/agent_runtime/__init__.py:164-185` | 同一个 run 的开始/终局事件；`thread_id` 仅进框架配置 |
| `tests/test_agent_runtime_tool_events.py` | 假模型成功、原异常、错误 `ToolMessage` 与直接 invoke 的确定性断言 |

后续学习检查：对于 `ToolMessage(status='error')`，为什么事件是 `tool_failed`，但运行终局仍可能是 `run_completed`？回答时应追 `handler` 的返回、消息回填和下一次模型调用。当前没有该题的用户作答或独立代码证据；“能独立实现”和“能应对追问”均待验证。
