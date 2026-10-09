# Spec 011：Agent 运行层的同进程连续消息

状态：2026-09-30；已按 TDD 实施，本地验证通过。

## 当前数据流与问题

`build_runtime_agent` / `build_configured_runtime_agent` 装配 `create_agent`，`RunContext.session_id` 供 middleware 在每次模型调用前选择模型。当前没有 checkpointer；调用方第二次 `invoke` 只提交新消息时，框架没有上次运行的消息状态。现有测试已证明一次 `invoke` 的工具回合后可从 A 切到 B，但未证明跨 `invoke` 的连续会话。

## 本切片契约

- 运行层装配函数可接收由调用方创建、持有的可选 checkpointer，并原样传给 LangChain `create_agent`。省略时沿用现有无检查点行为；已有调用方不需改动。
- 确定性测试用 `InMemorySaver`，同一个 agent 实例、同一个 `configurable.thread_id` 连续执行两次 `invoke`。第二次只传新用户消息，模型输入和输出状态都应包含前一次人类消息、AI 消息与新消息。两次调用之间把该会话的选模从 A 改为 B，第二次由 B 回答，历史不因换模型丢失。
- 不同 `thread_id` 的历史互不串入；`session_id` 仅作为本切片可信测试调用方传给选模 middleware，不自动等同于 `thread_id`。实际入口须由应用层校验会话身份并提供内部 `thread_id`，不能开放任意客户端值读取历史。
- 本次不创建会话服务、UI、数据库，也不定义同一会话的并发和运行中切换协议。现有一次 `invoke` 内的工具回合切换行为保持不变；本切片只验证两次调用之间的切换。

## 影响文件与验证

- `naiyue_test_agent/agent_runtime/__init__.py`：两个装配函数添加可选 checkpointer 透传；不改变已有必需参数与返回类型。本 Spec 实施时位于 `agent_runtime.py`，随后按 Spec 012 原样移入同名包。
- `tests/test_agent_runtime.py`：先写同线程连续输入、换模型及不同线程隔离的失败测试，再做最小实现。
- `tests/test_model_access.py`：验证配置驱动装配路径把同一 checkpointer 接给 Agent；不读取或改动真实 `.env`。
- `docs/handoff.md`：记录红灯、绿灯、学习证据与遗留边界。

验收：定向测试先因缺少 `checkpointer` 参数红灯，实施后定向和全量 pytest 通过；`compileall` 无错误，`pip check` 无依赖冲突。假模型与内存检查点只证明当前进程的框架接线，不证明重启恢复、并发一致性、真实供应商跨模型历史兼容或权限隔离。

## 实际验证

- 实施前基线 `.venv/bin/python -m pytest -q`：`82 passed in 4.07s`。
- 新增两条定向测试后，同一命令指定测试节点输出 `2 failed in 0.67s`；两条均因装配函数不接受 `checkpointer` 参数失败，符合预期红灯。
- 最小实现为两个装配函数的可选参数透传，既有调用保持无检查点。首次绿灯检查暴露测试遗漏：本机 LangChain 会把空 `system_prompt` 作为 `SystemMessage(content='')` 送给模型；据实际输入修正测试中的消息形状断言后，定向测试 `2 passed in 0.53s`。此修正没有放宽历史、隔离或选模断言。
- 全量 `.venv/bin/python -m pytest -q`：`84 passed in 3.57s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`：退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`；三个相关代码文件分别为 119、360、482 行，未超过 500 行。
- 观测到第二次 B 模型实际输入含 `SystemMessage`、第一次人类消息与 A 的回答、第二次人类消息；不同线程只见自己的消息。未通过本切片验证应用层任意 `thread_id` 访问拦截、同会话并发、重启恢复或真实供应商兼容。
