# Spec 023：同步运行中的 Follow-up

状态：2026-10-01；已按本 Spec 实施并完成本地确定性验证。前置为 [Spec 022](022-synchronous-steering.md)。本切片只增加同一次运行中的 Follow-up 队列与自然终局交付，不改工具调度、取消、异步、CLI 输入或持久化。

## 当前数据流和参照

当前 `LocalApplication.run → SessionService.run → invoke_agent_run → create_agent`。应用层已持有进程内 Steering 队列；运行层 `before_model` 只取 Steering，`after_agent` 在自然终局取剩余 Steering 或关窗。Pi `a69bef789` 的 `Agent.followUp()` 写独立 `PendingMessageQueue`；`runLoop` 内层在工具/Steering 处理后，外层只在本应结束时读取 Follow-up；队列默认 `one-at-a-time`。本项目保留这一语义，复用当前安装版 LangChain `1.4.2` 的 `@after_agent(can_jump_to=['model'])`，不自写模型—工具循环。AgentX `3fcfa0e` 的本次会话链路搜索没有对应的 Follow-up 队列事实，不借其推断此行为。官方[自定义 middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/custom)说明 node-style hook 的消息更新及 `jump_to='model'`。

## 行为与接口

- 新增 `LocalApplication.follow_up(session_id, text)` 与 `SessionService.follow_up`。只接受已有、正在运行且仍开放输入的会话，以及非空字符串；未知会话抛 `KeyError`、窗口关闭/未运行抛 `SessionBusyError`、无效文本抛 `ValueError`。返回只表示已入队；文本不进入日志。
- 会话新增独立 `follow_up_queue`。原 `steering_queue` 语义不变；`before_model` 仍只取 Steering，因此 Follow-up 不因工具完成而提前进入下一次模型输入。
- 自然终局的 `after_agent` 在同一锁内选择：若有 Steering，取出当前全部 Steering；否则若有 Follow-up，只取队头一条；都没有则关窗。选择结果以 `HumanMessage` 更新状态并 `jump_to='model'`；下一个自然终局再取下一条 Follow-up。两类队列同时非空时，以**该次取队列的时点**为准 Steering 优先；选择 Follow-up 之后才到达的新 Steering 可在随后的 `before_model` 注入，不追溯改写本次选择。
- 保留 `SessionService.finish_steering` 和 `build_steerable_runtime_agent` 的既有签名与原语义。新增组合回调和运行层装配入口，复用原 hook 逻辑而不复制一套循环；`build_local_application` 改为把新回调接入 Agent。应用层持有队列/顺序/窗口，运行层只接收“下一条要注入的字符串列表”，不导入应用层。
- 记录脱敏 `follow_up_accepted`、`follow_up_injected`、`follow_up_discarded`，仅包含事件名、`session_id`、`run_id`、数量。队列取出与模型成功分开；失败时仍在队列中的 Follow-up 清空并记 discarded，已取出的消息不重新排队，原异常与 `run_failed` 保持。续跑仍是同一次 `invoke`/run ID，终局只记一次 `run_completed`；框架递归上限或其他异常仍为失败，不把未完成变成正常回答。
- 当前只支持可信单进程入口。CLI 没有在运行中提交 Follow-up 的界面；真实模型、Web 授权、跨进程与跨重启交付均不由本切片测试证明。

## 影响文件和 TDD 验证

- `naiyue_test_agent/application/__init__.py`：会话队列、提交、自然终局仲裁、清理与组合接线。内部 `steering_open` 可改为共用输入窗口名，不改外部 API。
- `naiyue_test_agent/agent_runtime/__init__.py`：保留旧装配函数签名，新增交互式装配入口并复用已有两个 hook。
- `tests/test_application_follow_up.py`：先失败测试，后用假模型与 `threading.Event` 确认工具期间提交不会在工具后的模型调用前注入；自然结束后按 FIFO 一次一条续跑；Steering 优先；同一 run 事件和脱敏；关窗拒绝、失败清队列及会话隔离。再做框架递归上限的最小异常探针或测试，明确结果而不伪装完成。
- 本 Spec、`docs/handoff.md`、`docs/future-work.md` 与必要的总览/学习记录按实际结果更新。

验收以 `pytest` 红灯→最小实现→定向和全量测试、`compileall`、`pip check` 的实际输出为准。L1/L2/L3 分别核对存在、非占位逻辑、从应用组合到框架 hook 的接线。用户已理解上一切片 hook/reducer/jump 的解释；Follow-up 新代码的独立实现与面试追问尚无证据。

## 实施与验证结果

- TDD 红灯：修正测试夹具对空 `SystemMessage` 的消息数量假设后，`.venv/bin/python -m pytest -q tests/test_application_follow_up.py` 为 `5 failed in 0.62s`，均因 `LocalApplication.follow_up` 尚不存在。实现后加入关窗竞争及递归上限用例，定向运行 `7 passed in 0.57s`。
- 全量 `.venv/bin/python -m pytest -q`：`164 passed in 3.67s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`：退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。项目无独立构建命令。`git diff --check` 退出码 0；当前仓库文件尚未纳入 Git 跟踪，因此这项检查不等于对未跟踪文件的审查。
- L1/L2/L3：`LocalApplication.follow_up → SessionService.follow_up → follow_up_queue` 已接入；`build_local_application → build_interactive_runtime_agent → after_agent → SessionService.finish_pending_messages` 已通过真实装配的假模型用例验证。`before_model` 仍只消费 Steering。假模型证明两条 Follow-up 各得到一轮模型回答、Steering 在自然终局优先、工具后的模型输入尚无 Follow-up、同一 run ID 与会话隔离、失败丢弃剩余队列。已关闭窗口拒绝新消息；递归上限触发 `GraphRecursionError` 与 `run_failed`，无 `run_completed`。
- 这是进程内同步路径及确定性假模型证据。没有在线模型运行中输入、跨进程持久队列、Web 授权、活动取消或业务测试效果证据。`follow_up_accepted` 只表示入队，`follow_up_injected` 只表示从队列取出，都不等于模型成功回答；已取出后失败时 checkpoint 的部分状态及下次运行恢复/去重尚未验证。用户对前一切片的 hook 机制自述理解；本切片代码由助手实现，尚无用户独立编写或面试追问证据。
