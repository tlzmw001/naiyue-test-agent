# Lesson 14：当前 turn 后停止的代码地图

状态：2026-10-01，已按双工具运行中的停止请求讲解调用链；用户理解检查、独立实现及面试追问证据仍待收集。对应 [Spec 024](../specs/024-stop-after-current-turn.md)。

## 一个可复述的输入

模型已请求 `first_tool` 和 `second_tool`，两个工具都已开始但尚未返回。此时应用调用 `app.request_stop_after_turn(session_id)`，并已有待交付 Steering `S1` 与 Follow-up `F1`。停止请求只将该会话的 `stop_requested` 置真、关闭新输入窗口；两个工具仍按当前批次完成。LangGraph 把两个 `ToolMessage` 写入状态后进入下一次 `before_model`；它看到本次 run 已有模型响应且停止标志为真，返回 `jump_to='end'`，跳到 `after_agent`。应用边界返回 `(True, [])`，hook 写入 `run_stopped=True`，最终入口记录 `run_stopped`。模型没有第二次请求；剩余队列由 `SessionService.run` 的 `finally` 清理并记录 discarded。

若在首次模型请求前登记停止，`before_model` 不注入已排队 Steering，但仍允许当前首次模型调用；有工具时照常完成该批次。若模型/工具抛异常，运行记录 `run_failed` 并传播原异常，不产生 `run_stopped`。

## 代码入口

1. `application.LocalApplication.request_stop_after_turn → SessionService.request_stop_after_turn`：验证活跃/开放窗口，在同锁内设置停止和关窗；重复请求幂等。`stop_requested` 日志不含正文。
2. `application.build_local_application → agent_runtime._assemble_interactive_runtime`：把应用服务的 `is_stop_requested` 和 `finish_run_boundary` 回调交给运行层；运行层不导入应用层。
3. `InteractiveRunState`：`model_turn_seen` 让首次模型调用与之后的模型调用区别开；`run_stopped` 给调用方明确的终局字段。`before_agent` 每次运行重置，避免 checkpoint 把上次停止误带入下一次。
4. `after_model` 在模型响应已经返回时写 `model_turn_seen=True`。`before_model` 在后续模型请求前检查停止；`jump_to='end'` 在当前编译图指向 `after_agent`，由它完成停止/队列边界仲裁。
5. `finish_run_boundary` 在一把锁内按停止 → Steering → Follow-up → 自然完成选一路。停止时不取消息；队列尚待取的部分由 `run` 的 `finally` 清掉。
6. `invoke_agent_run` 根据最终结果的 `run_stopped` 选择唯一技术终局 `run_stopped` 或 `run_completed`；异常分支仍 `run_failed`。

## 新增参数与三处状态的归属

`build_local_application` 把 `sessions.is_stop_requested` 和 `sessions.finish_run_boundary` 作为**绑定方法**传给 `_assemble_interactive_runtime`。这里传的是将来可调用的函数，而不是此时读取到的布尔值/元组。装配函数用前者在下一次 `before_model` 查询停止标志，用后者在 `after_agent` 原子决定停止、Steering、Follow-up 或自然完成；两个参数必须同时提供。旧公开装配入口保持原签名，不传这两个参数。

| 值 | 谁持有、何时产生 | 用处 |
| --- | --- | --- |
| `session_id` | 应用层创建会话时产生，贯穿多个 run | 查应用层当前会话、队列、模型选择及停止标志 |
| `thread_id` | 应用层创建会话时产生，贯穿多个 run | `config.configurable.thread_id` 指定 LangGraph checkpoint 历史 |
| `run_id` | 每次 `SessionService.run` 新建 | 关联这次运行的日志事件，结束后清空 |
| `stop_requested` | 应用层会话字典，停止请求时设真 | 运行层通过回调读取；不是模型消息或图状态 |
| `model_turn_seen` | `InteractiveRunState` 的图状态，每次 `before_agent` 清零、`after_model` 置真 | 区分首个模型请求与模型响应后将发起的下一请求 |
| `run_stopped` | `InteractiveRunState` 的图状态，每次 `before_agent` 清零、停止时 `after_agent` 置真 | 返回给应用入口，用于选择 `run_stopped` 终局事件 |

`agent.invoke({'messages': ...}, context={'session_id': ..., 'run_id': ...}, config={'configurable': {'thread_id': ...}})` 的三个位置不能混用：`messages` 是本次输入并与图消息状态合并；`context` 是本次调用供 hook 查询的运行上下文；`thread_id` 选定 checkpoint 线程。`state` 是框架传给 hook 的当前图状态，`runtime` 是框架传入并装有 `context` 的对象；`_state` / `_runtime` 表示该 hook 暂时不用这两个参数。

## 从模型调用到停止的一次数据流

假设 `app.run(sid, [('user', 'initial')])` 已开始，第一轮模型返回两个工具调用，工具正在运行，应用层队列已有 Steering `S1` 和 Follow-up `F1`。`after_model` 已把 `model_turn_seen` 设为 `True`。另一线程调用 `app.request_stop_after_turn(sid)`；应用层同锁设 `stop_requested=True`、关闭新消息入口，返回只表示登记成功。两个工具返回后，框架把两条 `ToolMessage` 合入图状态，重新经过 `before_model`。此时回调读到 `True`，图状态也读到 `model_turn_seen=True`，于是返回 `{'jump_to':'end'}`；当前图的 `end_destination` 是 `after_agent`，不再调用模型，也不会执行 `drain_steering`。`after_agent` 从边界回调得到 `(True, [])`，返回 `{'run_stopped':True}`；运行入口记录 `run_stopped`。应用层 `finally` 清除还在队列里的 `S1/F1`，重置 busy 与停止标志。若模型或工具抛异常，入口记录 `run_failed` 并传播异常，`finally` 仍清理；这不是成功停止。

首次模型前收到停止的分支：`model_turn_seen=False`，所以第一次 `before_model` 返回 `None`，不调用 `drain_steering`，框架继续执行第一次模型请求。无工具时在 `after_agent` 停止；有工具时该批次完成，下一次 `before_model` 才跳过模型。停止不撤回此前已经合入图状态的消息，也不取消进行中的模型请求、SDK 重试或工具副作用。

## 理解检查与证据边界

请按“模型已发出两个工具调用，工具期间收到停止”判断：工具执行次数、后续模型请求次数、`S1/F1` 是否进入模型上下文、最终事件；再比较“模型请求之前就收到停止”的差别。为什么 `after_model` 不能直接在工具调用消息返回时终止？

`.venv/bin/python -m pytest -q tests/test_application_stop_after_turn.py` 是定向复核命令；阶段全量 `172 passed in 3.75s` 见 [Spec 024](../specs/024-stop-after-current-turn.md)。这些是同步假模型/工具证据，不证明在线模型取消、外部副作用回滚或用户已掌握代码。
