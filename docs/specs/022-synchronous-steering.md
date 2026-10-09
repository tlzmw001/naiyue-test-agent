# Spec 022：同步运行中的 Steering

状态：2026-10-01；已按 TDD 实施。范围仅是同一次 `invoke` 运行中插入用户消息，不包含 follow-up、停止、异步或工具调度。

## 当前数据流与设计依据

`LocalApplication.run → SessionService.run → invoke_agent_run → agent.invoke`。应用层持有会话 `busy` 与私有 `thread_id`；运行层组装 `create_agent`，框架处理模型—工具—模型循环。当前活跃运行没有提交新用户消息的入口。Pi 本地 `a69bef789` 的 `Agent.steer` 入队，`agent-loop.ts` 在工具回合后轮询 steering，并在下一次模型请求前加入上下文。本项目沿用这个时机，不复制 Pi 的循环。LangChain 1.4.2 的 `before_model` 位于每次模型请求前；`after_agent(can_jump_to=['model'])` 的本机探针显示自然结束前可追加消息并续跑。官方[自定义 middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/custom)描述节点式状态更新与跳转；仍以本机测试为本切片依据。

## 契约与边界

- 新增 `LocalApplication.steer(session_id, text)`，转给 `SessionService.steer`。只接受已存在且正在运行、尚未关闭接收窗口的会话和非空字符串；未知会话抛 `KeyError`，未运行/已关闭抛 `SessionBusyError`，无效文本抛 `ValueError`。它返回表示**已入队**，不表示模型已看见或最终成功。现有公开方法签名不变。
- 每次 `run` 开始清空上次临时队列并打开本次接收窗口；同会话仍只有一个运行。`steer`、队列取出、自然结束时关窗，均在同一锁内完成。先于关窗提交的消息必须被本次取走；关窗之后提交的消息明确拒绝，不自动转成下一次普通输入。
- 运行层新增接收两个应用回调的装配入口，保留原 `build_runtime_agent` 签名。`before_model` 取出 FIFO 队列，在下一次模型调用前加入 `HumanMessage`；`after_agent` 在即将自然结束时原子地取出剩余队列，有消息则追加并跳回 `model`，无消息则关闭接收窗口并结束。同一批消息只消费一次；自然结束续跑仍属于原 run ID，不生成第二个 `run_started`。
- 应用日志只记录 `session_id/run_id`、事件名与消息数量，绝不记录文本。运行失败时未取出的消息清空并记 `steering_discarded`；已经注入但后续失败的消息不重排，失败通过原异常和 `run_failed` 呈现。调用方须区分 accepted、injected 与 run completed。
- 本切片使用可信进程内入口；未来 Web 的身份/归属检查另做。同步模型请求不能被 steering 抢占；提交后只在下一安全边界生效。框架 `recursion_limit` 可终止无界续跑，届时仍走失败路径。

## 影响文件与验证

- `naiyue_test_agent/application/__init__.py`：会话队列、原子提交/取出/关窗、装配接线、脱敏事件。
- `naiyue_test_agent/agent_runtime/__init__.py`：新增两处 hook 和新装配入口；原 API 不变。
- `tests/test_application_steering.py`：先红灯，后用 `threading.Event` 和假模型验证工具回填后注入、自然结束前续跑、FIFO 与同一 run、拒绝窗口、失败清队列及不泄露文本；不使用定时睡眠。
- 本 Spec、`docs/handoff.md`、`docs/future-work.md` 与必要的总览文档更新实际证据。

验收：定向测试先因新接口不存在而失败；实现后定向与全量 pytest、`compileall`、`pip check` 通过；L1 文件、L2 真实逻辑、L3 应用组合到框架 hook 的执行证据均可查。真实在线模型、跨进程/持久化、流式、取消和业务质量不由假模型测试证明。

## 实际结果与限制

- 基线 `.venv/bin/python -m pytest -q` 为 `153 passed in 3.86s`。先写 3 个运行中提交/自然结束续跑/失败清理测试；定向运行得到预期的 `3 failed in 0.81s`，均因缺少 `LocalApplication.steer`。最小实现后 `3 passed in 0.53s`；随后加“关窗后 busy 尚在时拒绝”并发测试，定向 `4 passed in 0.54s`。
- `LocalApplication → SessionService` 的队列与锁已接入 `build_steerable_runtime_agent`，后者在 `create_agent` 装配 `before_model` 和 `after_agent`。假模型测试实际得到 `human → ai(tool call) → tool → human(steer 1) → human(steer 2) → ai`，自然结束前提交实际得到 `human → ai → human(steer) → ai`，两者都在一个 `run_started/run_completed` 对中。失败时待取消息产生脱敏 `steering_discarded` 并释放 busy。旧 `build_runtime_agent` 的签名和不带 steering 的装配仍保留。
- 最终全量 pytest、编译及依赖结果见 [交接记录](../handoff.md)。这些结果只证明同步假模型与进程内线程场景；SDK 在网络中途的真正响应中断、跨进程消息提交、Web 权限、持久队列均未验证。`steering_injected` 表示应用队列已交给 hook 用于状态更新；它与模型成功响应是不同事实。手工用 `SessionService.run` 装配 Agent 时，调用方必须接入该会话服务的两个 hook 回调，否则没有 steering 交付保证；统一 `build_local_application` 已完成接线。
