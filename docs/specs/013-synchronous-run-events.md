# Spec 013：同步 Agent 运行事件的最小关联链

状态：2026-09-30；已按 TDD 实施，本地验证通过。

## 当前数据流与问题

上层目前直接调用 `agent.invoke(input, context={'session_id': ...}, config={'configurable': {'thread_id': ...}})`。运行层 middleware 在模型成功时记录 `model_call_usage`，模型 handler 失败时记录 `model_call_failed`，但两类日志缺少统一 run ID，也没有可观察的运行开始和终局状态。`record_selection` 是独立回调，只表示选模尝试。未来取消、消息插入和应用入口需要能将同一次运行的事件关联起来。

## 本切片契约

- 新增同步入口 `invoke_agent_run(agent, messages, *, session_id, thread_id, run_id)`。调用方负责提供经过应用层校验的会话 ID、内部线程 ID 和每次运行唯一的 run ID；运行层只检查它们为非空字符串，不自行创建会话或随机 ID。
- 入口按顺序记录结构化 `run_started`，调用已有 `agent.invoke`（只传本次 `messages`、`context.session_id/run_id` 和 `configurable.thread_id`），成功记录 `run_completed` 并返回原结果。若 `invoke` 抛 `Exception`，记录一次 `run_failed`，以裸 `raise` 传播同一个异常对象；不把错误变成普通 AI 消息。事件只含固定事件名与 run ID，不写输入、回答、异常正文、凭证或内部线程 ID。
- `RunContext.run_id` 为可选字段。经新入口调用时，现有模型选型、成功用量和失败事件的结构化字段附加相同 run ID；旧调用方未提供 run ID 时，原有事件字段及行为不变。选模日志仅表示尝试，成功用量仅表示 LangChain 响应报告的数据，终局失败是一次 run 的结果，不等同于每次 SDK 尝试。
- 本切片只覆盖同步 `invoke` 和模型调用边界。不添加工具事件、持久轨迹、取消/中断状态、异步/流式路径、应用会话映射或 UI。同步入口不能代替未来的实际安全会话服务；未经授权的 `thread_id` 不可直接进入 Web 入口。

## 影响文件与验证

- `naiyue_test_agent/agent_runtime/__init__.py`：扩展可选运行 context 字段、在现有模型日志中关联 run ID，并新增同步运行入口；不改变已有公开函数签名。
- `tests/test_agent_runtime.py`：先写成功、模型失败、重复运行及非法 ID 的失败测试，再做最小实现。断言事件顺序、关联字段、同一异常对象传播、旧路径兼容以及日志不含敏感正文。
- `docs/handoff.md`、`docs/implementation-roadmap.md`、`docs/future-work.md`：记录结果和未覆盖边界。

验收：先确认新测试按预期红灯；定向和全量 pytest、`compileall`、`pip check` 通过。这里只把结构化 Python 日志作为首版可观察事件，不宣称已有数据库轨迹、工具 span 或用户可见的安全失败展示。

## 实际验证

- 基线 `.venv/bin/python -m pytest -q`：`84 passed in 3.59s`。
- 新增 5 个参数化后的定向测试先输出 `5 failed in 0.60s`，预期红灯均是模块缺少 `invoke_agent_run`。
- 最小实现后同组定向测试输出 `5 passed in 0.53s`；再加强完整事件顺序断言后，最终全量 `.venv/bin/python -m pytest -q` 输出 `89 passed in 3.80s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。运行层 153 行、相关测试文件 475 行，未超过 500 行。
- 同一 `thread_id` 两次运行的结构化事件按 `run-one`、`run-two` 分组；模型选型和用量记录带同一 ID，历史消息延续。合成模型失败得到 `run_started`、`model_call_failed`、`run_failed`，原异常对象透传，没有成功用量事件；日志正文不含合成提示词、回答、异常正文或内部线程 ID。原来直接 `agent.invoke` 的测试仍通过，未给其已有事件强加 run ID。
- 新入口只由确定性测试接线；当前正式应用入口尚不存在，旧诊断脚本和结构化折扣策略仍直接 `invoke`，故不能声称所有项目运行已经统一接入这些事件。没有工具事件、持久日志存储、异步/流式取消或真实供应商运行结果。

后续扩展（2026-09-30）：[Spec 015](015-synchronous-tool-events.md) 在保持 `invoke_agent_run` 签名、原异常传播与原历史验证不变的前提下，为 `run_event` 增加应用层传入的 `session_id`，并新增同步工具边界日志。以上原始验证结果只对应 Spec 013 实施当时的字段形状；当前字段以 Spec 015 的新增测试为准。
