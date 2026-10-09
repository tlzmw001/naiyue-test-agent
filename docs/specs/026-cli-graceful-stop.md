# Spec 026：CLI 接入当前 turn 后优雅停止

状态：2026-10-01；已按 TDD 实施。复用 [Spec 024](024-stop-after-current-turn.md) 的应用停止信号，不实现活动取消、工具回滚、异步或流式输出。

## 当前链路与问题

交互式 CLI 的主线程读取输入，工作线程执行 `LocalApplication.run`。应用层已经有 `request_stop_after_turn(session_id)`，接受时在会话锁内设置 `stop_requested=True` 并关闭新消息窗口；运行层在当前模型回复和已发出的工具批次后返回 `run_stopped=True`，失败则抛异常并记 `run_failed`。CLI 目前只有 `:quit` 等工作线程结束，没有发送停止信号的命令，也不会展示 `run_stopped` 与自然完成的区别。

## 本切片契约

- 在 TTY 中用 `:stop` 请求当前 turn 后停止。只调用现有 `app.request_stop_after_turn(session_id)`，不把命令文本交给模型。接受后显示“已请求在当前 turn 后停止”，这是请求回执，不是完成回执。重复请求交给应用层幂等处理。
- 空闲时 `:stop` 不创建运行，提示当前没有运行。工作线程还活着但应用层拒绝请求（例如刚启动尚未开窗或已进入收尾）时，提示本次停止未被接受，不声称成功，也不自动开启下一轮。此竞争窗口保留为已知限制；界面不把 `worker.is_alive()` 当成应用层已接受的证明。
- 成功运行且返回 `run_stopped=True` 时，在本次可展示模型文本后显示“已在当前 turn 后停止”。自然完成不显示该提示；停止请求后若模型或工具失败，只显示现有安全失败类型，不显示停止成功。
- `:quit`/EOF 保持等待当前运行结束的语义，不发停止信号。非 TTY 输入仍逐轮同步；若读到 `:stop` 时没有运行，提示空闲。其他输入、选模和 Steering/Follow-up 规则不变。当前 CLI 不提供活动取消，正在进行的请求与工具仍可能持续。

## 文件与验证

- `naiyue_test_agent/application/cli.py`：增加命令分流和停止终局显示，不改变下层公开接口。
- `tests/test_application_cli.py`：先写失败测试，再用假模型和 `threading.Event` 验证运行中接受停止、当前回答返回、待取消息不再续跑、失败优先、空闲与关窗拒绝；测试不依赖短暂 sleep。
- README、`docs/handoff.md`、`docs/future-work.md` 和必要的总体决策记录同步现状与限制。不改 `.env`、依赖、Web 或运行层。

验收：基线测试通过；新行为测试先以预期原因红灯；实现后定向及全量 pytest、`compileall`、`pip check` 通过。L1 文件与命令存在，L2 有真实请求/结果分支，L3 从 CLI 到现有应用停止信号及 `run_stopped` 经确定性测试证明。真实网关时序、活动取消和业务效果另行验证。

## 实施与实际结果

- 基线 `.venv/bin/python -m pytest -q`：`175 passed`（上一切片结果；本切片开始前已核对基线）。新增 4 个测试后，`.venv/bin/python -m pytest -q tests/test_application_cli.py -k stop` 为 `4 failed, 10 deselected`，均因 `:stop` 进入旧的“未知命令”分支而失败。最小接线后，定向 CLI `14 passed in 0.57s`。
- 完成后 `.venv/bin/python -m pytest -q`：`179 passed in 3.86s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。项目无独立构建目标。
- L1/L2/L3：CLI 命令实际调用 `app.request_stop_after_turn(session_id)`；应用层在会话锁内登记标志，运行层返回 `run_stopped=True` 时 CLI 显示停止终局。确定性假模型让当前调用停在 `Event`，提交 Steering 再提交停止，证实已排队文本不再引发下一模型调用；模型失败则只显示失败，应用拒绝请求时不显示接受回执。无真实网关中途停止探针，本测试也不证明活动取消或所有竞态时序。
- 启动边界仍可能出现 `worker.is_alive()` 但应用尚未设置 `busy=True`，此时应用拒绝停止并提示未接受；未来若要求此请求必达，应单独设计启动确认或预登记机制，并覆盖退出竞争。当前不把拒绝解释成停止成功。
