# Spec 014：进程内应用会话服务

状态：2026-09-30；已按 TDD 实施，本地验证通过。前置：[Spec 011](011-in-memory-agent-continuation.md) 的内存检查点与 [Spec 013](013-synchronous-run-events.md) 的同步运行入口。

## 当前数据流与问题

调用方直接提供 `session_id`、`thread_id` 和 `run_id` 给 `invoke_agent_run`。运行层只校验非空，既不能证明 `thread_id` 由可信会话映射而来，也不管理同一会话同时运行的竞争。配置驱动的选模 middleware 已经可以在每次模型调用前重新读取选择，但尚没有应用层持有该选择。`InMemorySaver` 只按 `thread_id` 管 Agent 状态，不保存会话归属或当前模型。

## 本切片接口与规则

- 新建 `application.SessionService(catalog: ModelCatalog)`，持有同一份已校验的模型目录快照和进程内会话表。应用组合处用 `service.resolve_model` 作为 `build_runtime_agent` 的选模回调，并给运行层传同一 `InMemorySaver`。服务不创建 `create_agent` 或业务策略，不让运行层导入应用层。
- `create_session(model_id: str) -> str` 先用目录校验模型 ID，成功后生成不可预测的外部 `session_id` 与独立内部 `thread_id`。验证失败不创建会话。服务不公开任意 `thread_id` 读取接口；该 ID 只由 `run` 传给运行层。
- `resolve_model(session_id) -> (model_id, BaseChatModel)` 给运行层的可信回调；未知会话明确失败。`select_model(session_id, model_id)` 先校验模型再提交；失败不得改变当前选择。忙碌会话也可切换，提交后的下一次模型调用边界生效；已经开始的模型调用不受影响。不在本切片记录持久的变更历史。
- `run(agent, session_id, messages)` 在锁内检查会话并设置 busy，生成唯一 `run_id`；释放锁后调用现有 `invoke_agent_run`，最后无论成功失败都清除 busy。相同会话的第二次并发运行抛 `SessionBusyError`，不会调用 agent；其他会话可以并行运行。返回原始 Agent 结果，异常原样传播，run ID 在运行事件中可查。
- 这只适用于可信的本地单进程调用；`session_id` 不是身份认证。未来 Web 入口须额外校验访问令牌和会话归属，不得把服务直接暴露成无认证 API。服务与 `InMemorySaver` 都会随进程退出丢失。

## 影响文件与验证

- 新增 `naiyue_test_agent/application/__init__.py`：最小会话服务和忙碌异常。既有公开 API 不改。
- 新增 `tests/test_application_session.py`：先写失败测试。真实 `build_runtime_agent` + 假模型 + `InMemorySaver` 验证同会话延续、切模型、不同会话隔离；用 `threading.Event` 同步阻塞 fake agent，验证同会话忙碌拦截、失败后释放，以及另一会话可运行。断言运行日志只包含 opaque run ID，不包含输入/回答/内部 thread ID。
- `docs/handoff.md`、`docs/implementation-roadmap.md`、`docs/future-work.md`：更新实证、限制和下一步。

验收：新测试先因模块不存在红灯；随后定向和全量 pytest、`compileall`、`pip check` 通过。L1/L2 检查实现真实存在；L3 测试确认应用服务确实调用运行入口且模型回调、检查点接线有效。Pi 对照为本地 `a69bef789` 的 `AgentSession.setModel` 与活跃运行状态；AgentX 对照为本地 `3fcfa0e` 的 `ConversationAppService.prepareEnvironmentWithModel`。迁移的是会话/请求职责边界，不复制其语言或运行时。

## 实际验证与证据边界

- 实施前基线 `.venv/bin/python -m pytest -q`：`89 passed in 3.92s`。新增测试后，`.venv/bin/python -m pytest -q tests/test_application_session.py` 因 `naiyue_test_agent.application` 不存在在收集阶段报 `ModuleNotFoundError`，符合预期红灯；最小实现后定向 `4 passed in 0.58s`。
- 完成后全量 `.venv/bin/python -m pytest -q`：`93 passed in 3.60s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。运行层、模型层和测试策略层均没有静态导入 `application`。
- L3：两个不同会话只看到各自的内存检查点历史；同一会话第二次调用只传新消息仍看到上一轮，A→B 切换后由 B 回答。另一测试用工具回填触发运行中的 A→B 切换，记录的模型选择为 `['A', 'B']`；并发测试用 Event 固定运行重叠，第二次同会话调用被拒绝、另一会话可运行、失败后 busy 清除，两个内部线程 ID 不同且不进入日志。
- 运行事件和模型事件只在当前进程的 Python 日志中；没有对外 CLI/Web 入口、授权、多进程同步、重启恢复、真实供应商跨模型历史兼容、用户可见的安全失败展示或持久的模型变更审计。`SessionService` 必须与同一模型目录快照、同一带 checkpointer 的 Agent 一起装配；未来入口应封装这一组合并加访问控制。本切片的 `session_id` 只是不易猜测的索引，不是权限凭证。
