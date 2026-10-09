# Spec 024：当前 turn 完成后优雅停止

状态：2026-10-01；实施前设计。只做单进程同步 `LocalApplication.run` 的安全边界停止，不中断活动模型请求、SDK 重试等待或已开始的工具，不做 Web/CLI 输入、异步取消或工具回滚。

## 当前数据流与源码依据

`LocalApplication.run → SessionService.run → invoke_agent_run → create_agent`；当前运行中可提交 Steering/Follow-up，`before_model` 取 Steering，`after_agent` 在自然终局取队列/关窗，终局仅有 `run_completed/run_failed`。Pi 本地 `a69bef789` 的 `agent-loop.ts` 在模型回复和当轮工具结果处理、`turn_end` 之后调用 `shouldStopAfterTurn`，若为真直接 `agent_end`，不再轮询 Steering/Follow-up；`Agent.abort()` 是另一个活动取消机制。AgentX 本地 `3fcfa0e` 的 `ChatSessionManager.interruptSession` 设置会话标志并完成 SSE，未找到模型/工具执行路径消费该标志的证据，不能把它当作本切片的取消实现。

本机 LangChain `1.4.2` 的 `create_agent`：工具节点完成后进入 `loop_entry_node`（首个 `before_model`）；无工具时进入 `after_agent`；`before_model(can_jump_to=['end'])` 的 `end_destination` 是 `after_agent` 节点（若已配置），不是直接绕开收尾。`after_model` 在模型返回后、工具路由前运行。已用本机最小探针确认自定义 `AgentState` 字段可由 `after_agent` 写入最终结果。以上先作为设计依据，仍需真实装配的假模型失败/通过测试验证。

## 本切片行为

- 增加 `LocalApplication.request_stop_after_turn(session_id)`，转给 `SessionService`。只在已有活跃且尚未关窗的会话接受；未知会话 `KeyError`，空闲/已结束窗口 `SessionBusyError`。重复请求在同一 run 内幂等，不再记第二条 accepted。接受后关闭新 Steering/Follow-up 接收窗口；返回表示已登记，不表示活动请求已取消。
- 请求若在首次模型调用开始前到达，仍让该模型 turn 完成；若模型要求工具，当前批次中已发出的工具调用照常完成。其后不再发下一次模型请求，也不再注入排队消息。模型没有工具时让当前回答完成后停止。若模型或工具失败，失败优先于停止，仍走 `run_failed` 并抛原异常。
- `SessionService` 每次 run 重置停止标志；停止与自然终局取队列使用同一锁仲裁。停止先于取队列时，未取消息留在队列，由 `finally` 清理并记原有 discarded 事件；已经注入框架消息但尚未处理的消息不承诺撤回，需单独检查 checkpoint 部分状态。
- 运行层在本次 run 首次模型响应前不因停止请求跳过模型；模型已返回后，在下一次 `before_model` 若观察到停止请求则 `jump_to='end'`，无工具时由 `after_agent` 直接处理。`after_agent` 写运行状态 `run_stopped=True`，使 `invoke_agent_run` 记录唯一终局 `run_stopped` 而非 `run_completed`；正常路径仍 `run_completed`，异常仍 `run_failed`。下一次普通 run 清除此状态，避免跨 checkpoint 误判。
- 当前 `build_interactive_runtime_agent` 保持原签名与调用可用；项目内部 `_assemble_interactive_runtime` 接收停止回调，`build_local_application` 使用它接线。`build_runtime_agent`、`build_configured_runtime_agent`、`build_steerable_runtime_agent` 和 `invoke_agent_run` 的现有调用签名不改变。返回字典新增布尔 `run_stopped` 仅限统一应用入口的交互式 Agent。
- 日志只记录 `session_id/run_id`、固定事件名和队列数量，绝不记录提示词、工具参数或结果。`stop_requested` 不等于 `run_stopped`。当前工具可能有已完成或结果未知的外部副作用，不能声称取消或回滚。

## 代码入口审计与清理边界

当前 `build_runtime_agent` 被测试、`test_agent` 使用；`build_configured_runtime_agent` 被模型配置测试和真实探针脚本使用；`build_interactive_runtime_agent` 被应用组合使用；`_build_runtime_agent` 是这些入口共用的私有装配；`build_steerable_runtime_agent` 目前只有自身定义和文档引用，是旧公开入口的兼容转发。它不是当前应用运行必经节点，但删除会破坏已有可导入 API，因此本切片不删除或改名。后续若要收敛公开入口，先列外部使用证据和迁移方案，作为独立 breaking-change 决策。

## 影响文件与 TDD

- `naiyue_test_agent/application/__init__.py`：停止标志、提交/关窗、边界仲裁、清理和装配回调。
- `naiyue_test_agent/agent_runtime/__init__.py`：节点式 hook、最小状态字段与终局事件。
- `tests/test_application_stop_after_turn.py`：先失败测试再最小实现。用 `threading.Event` 和假模型/工具验证无工具回答期间请求、工具期间请求、多工具当前批次完整结束后不发下一模型请求、Steering/Follow-up 未取队列丢弃、失败优先、重复请求和空闲拒绝、停止后下一 run 正常恢复。断言事件去重、脱敏与返回 `run_stopped`。
- 本 Spec、`docs/handoff.md`、`docs/future-work.md`、必要总览文档；不改 `.env`、依赖、旧函数签名或无关代码。

验收：基线全量测试先通过；新行为测试先因接口/终局缺失而红；实现后定向和全量 pytest、`compileall`、`pip check` 为绿。L1 文件存在、L2 非占位逻辑、L3 `build_local_application → Agent hooks → SessionService` 真实接线，均以确定性假模型验证。活动取消、真实供应商和业务质量不由此切片证明。

## 实施与实际验证

- 基线 `.venv/bin/python -m pytest -q`：`164 passed in 4.00s`。先写 5 个用例，定向红灯为 `5 failed in 0.91s`，共同原因是 `LocalApplication.request_stop_after_turn` 不存在。实现后补关窗竞争用例及旧装配入口兼容回归，定向一度 `18 passed in 0.65s`。继续核对“首次模型请求前停止”时，回归测试先以 `1 failed in 0.85s` 复现待取 Steering 被错误注入；只修改 `before_model` 的停止分支后，定向停止/Steering/Follow-up 为 `19 passed in 0.81s`。
- 最终 `.venv/bin/python -m pytest -q`：`172 passed in 3.75s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。项目没有单独构建命令。当前文件均未纳入 Git 跟踪，`git diff --check` 退出码 0 不能当作对未跟踪文件的差异审查。
- L1/L2/L3：`LocalApplication.request_stop_after_turn → SessionService` 在同锁内登记停止并关闭新输入窗口；统一应用装配把两个停止回调传入运行层内部装配。`before_agent` 在每次 run 重置两个状态字段，`after_model` 标记本 run 已有模型响应；`before_model` 在首次请求前遇停止时不取队列、仍执行当前模型，在已返回模型后的下一请求前遇停止则跳到终局。`after_agent` 在同锁内选择停止或队列消息并写入 `run_stopped`。运行入口据最终状态记 `run_stopped`，不会再记 `run_completed`。假模型证明当前回答/双工具批次完成后停止、待取队列被丢弃、模型失败优先、重复请求幂等、关窗拒绝和下一 run 状态重置；旧 `build_steerable_runtime_agent` 直接装配测试仍通过。
- 已接受停止后，已开始的同步模型和工具继续执行；本切片没有 API/工具活动取消、预算收敛或外部副作用回滚。`run_stopped` 表示当前 turn 安全边界已观测到停止请求，不等于任务业务成功。已交付到框架状态但未进入下一模型请求的消息在竞争边界可能留下部分 checkpoint 状态；恢复与去重尚需独立实验。代码由助手实现，用户尚未学习本切片核心代码或独立编写。
