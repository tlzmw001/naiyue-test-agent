# Lesson 15：CLI 运行中输入如何进入两条队列

日期：2026-10-01。状态：入口与入队机制已讲解；用户理解、独立实现及面试追问仍待其回答/练习验证。对应 [Spec 025](../specs/025-interactive-cli-messages.md)。

## 一条确定性测试数据流

测试 [test_tty_queues_steering_and_follow_up_during_model_call](../../tests/test_application_cli.py) 给假模型三个预置回答：`first answer`、`steered answer`、`followed answer`。第一次 `_generate` 用 `Event` 等待；这里的等待由测试控制，不代表真实模型调用可以暂停。

1. CLI 主线程读到 `initial`，调用 `start_run('initial')`。TTY 分支创建工作线程，`Thread.start()` 后主线程返回输入循环；工作线程调用 `_run_and_print → app.run`。这里没有调用 `ainvoke`，同步模型请求只阻塞工作线程。
2. `SessionService.run` 在锁内将当前会话设为 `busy=True`、`steering_open=True`，生成本次 `run_id`，然后调用 `agent.invoke`。假模型发出 `entered` 事件并等待。
3. 主线程读到 `private steering`，看到工作线程仍活跃，调用 `app.steer`。应用服务在同一把锁中复查 `busy/steering_open`，成功才把文本加进 `steering_queue`。CLI 打印“Steering 已排队”只表示接受。
4. 主线程读到 `:follow private follow-up`，调用 `app.follow_up`，同样通过会话锁复查后写入独立的 `follow_up_queue`。它不会在工具后的普通模型调用前被当成 Steering。
5. 测试释放模型后，第一次回复为 `first answer`；自然结束边界先取 Steering 并跳回模型，得到 `steered answer`；下一次自然结束取 Follow-up，再得 `followed answer`。三次回复属于同一 `run_id`。`:quit` 时 CLI 等工作线程完成后退出。

如果普通输入到达时 `worker.is_alive()` 为真，但应用服务的接收窗口已经关闭，`app.steer` 会抛 `SessionBusyError`。CLI 等旧线程完成后，将刚输入的文本作为下一次普通运行提交；`is_alive()` 是初步判断，应用层同锁检查才是入队是否成立的依据。

## 关于 `worker.join()` 的追问

用户能按主线描述“首次输入启动工作线程、运行中普通输入进 Steering、显式命令进 Follow-up”，但把下一次 CLI 输入循环误认为要等 `run()` 完成，并猜测线程结束时 `worker` 会自动变成 `None`。已针对这两点纠正：`Thread.start()` 后主线程立即继续 `while`；`worker` 仍引用同一个 `Thread` 对象，结束后只是 `worker.is_alive()` 变为 `False`，下次 `start_run` 才覆盖变量。

竞争时序：主线程先看到 `worker.is_alive() == True`；随后旧 run 的接收窗口关闭，`app.steer()` 在锁内拒绝并抛 `SessionBusyError`；旧线程可能还在完成 `finally` 清理或打印。`worker.join()` 会阻塞**主线程**直到旧工作线程彻底结束，然后代码继续执行 `start_run(line)`，把刚才没能入队的输入作为下一次普通运行提交。`join()` 不停止线程、不重新运行任务，也不把变量设为 `None`。若直接启动新 run，旧会话可能仍是 `busy=True`，新调用会被 `SessionService.run` 拒绝；即使已清理，也可能与旧回复的打印交错。`join()` 同样用于 `:follow` 的关窗竞争和退出时等待。

这是当前实现的解释；用户对竞争路径的掌握仍待其后续回答，不能因讲解自动认定已独立理解。

用户随后能自行复述：在 CLI 试图提交 Steering 时若收到 `SessionBusyError`，同一条输入会等待旧工作线程完成，再走普通 `start_run`。记录为该竞争分支的**自述理解**；还没有独立代码改动或面试追问证据。“该分支保留输入”不等于模型一定成功处理：新运行也可能失败，已接受的队列消息在失败/停止时可能被丢弃，进程崩溃与重启交付未实现。

真实网关手工交互得到 `SECOND` 和 `THIRD`，没有独立 `FIRST`：那次 Steering 到达得更早，在**第一次** `before_model` 前已经进入上下文。确定性测试和真实网关这两种时序都符合“下一次模型调用前注入”，不能把 Steering 解释成“总会先得到当前问题的独立回答”。

## 本次理解检查

模型 API 已经开始处理 `initial`，此时 CLI 接受一条 Steering。它是否能改写正在进行的那次 API 请求？若不能，这条消息最早在哪个边界进入模型上下文？

正确方向：不能改写已发出的请求；等当前模型返回、已发出的工具调用完成后，在下一次模型请求前或自然结束续跑边界加入。理解状态需根据用户回答另行记录，不能因测试通过自动升级。

## 完整数据流：模型请求已发出后收到两类输入

以下 `S1/T1/R1/H0` 是讲解用的示意 ID，不是本机运行记录。假设同一会话上一轮已有历史 `[H_old, A_old]`；本次模型请求已发出，再从 CLI 输入普通文本 `S` 和 `:follow F`。模型依次返回 `A0/A1/A2`，不请求工具。生产代码入口是 `application/cli.py`、`application/__init__.py`、`agent_runtime/__init__.py`。

| 时间 | 调用与消息去向 | 应用队列与标志 | 图状态消息与标志 |
| --- | --- | --- | --- |
| 启动 | `create_session` 得到公开 `S1` 和内部稳定 `T1` | `busy=False`、`steering_open=False`、`stop_requested=False`，两队列空，`run_id=None` | 本次图运行尚未开始 |
| 输入 `H0` | CLI 构造 `HumanMessage(id=H0)`；TTY 工作线程调用 `app.run` | `SessionService.run` 在锁内设 `busy=True`、`steering_open=True`、`stop_requested=False`，清队列，生成本次 `R1` | `agent.invoke` 用 `thread_id=T1` 读历史，输入追加 `H0`；`before_agent` 重置 `model_turn_seen=False`、`run_stopped=False` |
| 首次模型调用 | `before_model` 调 `drain_steering(S1)`，此时空，返回 `None`；模型开始处理 | 两队列仍空 | 模型看到历史及 `H0`；现有请求已发出，之后入队不会改写它 |
| 运行中输入 `S`、`:follow F` | CLI 分别调用 `app.steer(S1,S)`、`app.follow_up(S1,F)` | 同一把锁确认 `busy/open` 后得到 `steering_queue=['S']`、`follow_up_queue=['F']`；记录 accepted 事件 | **尚无** `HumanMessage(S/F)`；图消息仍等待模型返回 |
| 模型返回 `A0` | 框架收下第一条 `AIMessage`；`after_model` 标记已完成模型回合 | 队列仍为 `['S']`、`['F']` | 消息为 `[..., H0, A0]`；`model_turn_seen=True`、`run_stopped=False` |
| 第一次自然结束边界 | `after_agent` 调 `finish_run_boundary(S1)`，先取全部 Steering；返回 `(False,['S'])` | Steering 清空，Follow-up 仍为 `['F']`，接收窗口仍开；记录 steering_injected | hook 返回 `{'messages':[HumanMessage('S')], 'jump_to':'model'}`；框架通过 `add_messages` 合并，形成 `[..., H0,A0,H_S]`，再调模型 |
| 模型返回 `A1` | 第二次 `after_agent` 取队首一条 Follow-up；返回 `(False,['F'])` | 两队列空，窗口仍开；记录 follow_up_injected | 合并 `H_F` 并跳回模型；消息为 `[...,H0,A0,H_S,A1,H_F]` |
| 模型返回 `A2` | 第三次 `after_agent` 发现两队列都空，返回 `None`，图自然结束 | 在锁内设 `steering_open=False`；此时 `busy` 可能短暂仍为 True | 消息为 `[...,H0,A0,H_S,A1,H_F,A2]`；`run_stopped=False` |
| 返回与清理 | `invoke_agent_run` 记录一次 `run_completed` 并返回；`SessionService.run` 的 `finally` 清理；CLI 打印结果 | `busy=False`、`steering_open=False`、`stop_requested=False`、`run_id=None`；`S1/T1` 保留供下次运行 | checkpoint 保留消息；CLI 从本次 `H0.id` 后取出 `A0/A1/A2` 的非空文本，各打印一次 |

入队、取出和模型成功是三个不同事实：`*_accepted` 只表示字符串进入队列；`*_injected` 表示 hook 已取出并返回状态更新；只有本次运行实际完成才有 `run_completed`。`R1` 在 Steering/Follow-up 续跑中不变；下一条独立 CLI 消息启动新 `run` 才生成新 `run_id`，`S1/T1` 仍沿用。测试里的 `entered/release` 是 `threading.Event` 同步信号，只为固定假模型时序，产品运行没有这两个标志。

若 `A0` 包含工具调用，LangChain 先执行工具并回填 `ToolMessage`；工具之后进入下一次模型请求前的 `before_model`，在那里可取出待处理 Steering，合并 `H_S`。Follow-up **不在** `before_model` 取，只等到后来真正的自然结束边界。如果 Steering 赶在首次 `before_model` 前入队，它会被首次模型请求带走，因此不保证先出现独立的 `A0`。

当前 CLI 的 `:quit` 只是 `worker.join()`，没有设置 `stop_requested`。`stop_requested` 仅由应用的 `request_stop_after_turn` 置位；它会关闭输入窗口，后续 hook 在安全边界停止并使 `run_stopped=True`，未取出的队列由 `finally` 清理。该停止功能目前还没有 CLI 命令。
