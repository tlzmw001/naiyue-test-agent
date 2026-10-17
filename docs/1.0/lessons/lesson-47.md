# Lesson 47：middleware hook注册与普通工具循环顺序

> 第三章第4个主题，2026-10-12。已准备讲解材料并验证顺序测试；用户复述、本人走读和独立实现尚无证据。

## 目的与真实入口

区分Python方法实现、create_agent识别/注册hook，以及invoke通过图执行hook三个时点。复用框架的标准循环，不创建教学运行时。

新增确定性契约测试：[test_agent_middleware_order.py](../../../tests/test_agent_middleware_order.py)，用两组TraceMiddleware A/B、一个只继承默认实现的AgentMiddleware、假模型和echo_probe记录普通循环。没有修改生产代码；本次是补教学可观察边界，不称为新功能TDD。

数据：用户Echo ping→AI调用echo_probe(value=ping, id=call-1)→工具返回echo: ping→AI回答done。trace项为(event, 当前消息type列表)，工具主体项为(tool, None)。

## 创建时的注册分支

当前create_agent比较实例类上解析到的hook实现与AgentMiddleware默认实现；同步或异步实现不同则收集，并为before/after hook创建图节点。继承的非默认实现也可被识别，不要求在最末子类直接重写。

| 情况 | 本测试证据 |
|---|---|
| A/B提供四种同步hook | agent.nodes含A/B.before_agent、before_model、after_model、after_agent |
| AgentMiddleware只提供默认方法 | 对应四种节点均不存在 |
| 仅完成create_agent | trace仍空，无hook或模型/工具执行 |

Python方法覆盖提供可识别的方法；框架收集、建节点与连边负责执行时点。_record是普通辅助方法，不因定义在middleware里就成为hook。

## 普通完整轨迹

```text
A.before_agent
B.before_agent
  A.before_model
  B.before_model
  model
  B.after_model
  A.after_model
tool
  A.before_model
  B.before_model
  model
  B.after_model
  A.after_model
B.after_agent
A.after_agent
```

每个middleware对象本例before_agent/after_agent各1次，before_model/after_model各2次。普通before按注册顺序，after反序。工具后回loop_entry(before_model)，不回初始entry(before_agent)。不将after_agent当finally；异常、审批、jump/停止、after_agent续跑会改变到达路径和次数，留对应分支展开。

## hook看到的状态

| A的hook | 两次／一次实际消息type |
|---|---|
| before_agent | human |
| before_model | human；human,ai,tool |
| after_model | human,ai；human,ai,tool,ai |
| after_agent | human,ai,tool,ai |

模型返回更新先合入messages，after_model再读取状态。hook本例隐式return None，只记录轨迹，不提交更新或跳转。若要改状态须按框架合同返回更新，本节不通过原地修改示例暗示正确写法。

## 测试与走读

```bash
.venv/bin/python -m pytest -q tests/test_agent_middleware_order.py::test_hooks_are_registered_and_ordered_across_two_model_calls
```

最终本轮命令测试文件：1 passed in0.12s。compileall该测试退出0，git diff --check退出0。测试末独立断言注册节点、创建无副作用、15项轨迹、before/after消息状态、工具结果配对与最终回答；它验证正常框架行为，不证明真实模型选择、异常收尾或用户掌握。

入口断点：测试首行trace=[]。创建后看trace和sorted(agent.nodes)，invoke期间在TraceMiddleware._record当前帧看self.label、hook、trace与[(m.type,m.content) for m in state['messages']]。测试帧返回后看[event for event,_ in trace]及result的消息投影。

## 源码位置与下一步

本机langchain1.4.2 factory.py1111起收集hook，1549起注册节点，1646起区分entry/loop_entry/loop_exit/exit，1746起按顺序/逆序连边。依赖具体版本，不凭API名称推断顺序。

之前两次续课已讲create_agent与invoke、add_messages追加/同ID替换及消息ID/调用ID区别；要点补在Lesson46，均不自动记为用户理解。当前下一步本人走读本测试；后续第5主题展开wrap_model_call/wrap_tool_call的handler进入/返回。
