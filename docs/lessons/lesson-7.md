# Lesson 7：为什么应用组合入口要只装配一次

状态：2026-09-30，已讲解组合入口 `build_local_application` 的具体数据流与核心行，以及 `LocalApplication.run → SessionService.run → invoke_agent_run` 的 ID 与消息流。用户先正确指出 `resolve_model` 接线点，随后用“Checkpoint 和 Reserve Model”回答第二轮历史与模型来源；按语境将后者理解为 `resolve_model`，记为引导后理解这两条职责线。尚无独立改动或面试追问证据，不标记为能独立实现或能应对追问。

## 一个具体运行例子

应用启动时传入模型配置路径、工具列表与提示词。`build_local_application` 读取一次 `ModelCatalog(default_model_id='A', models={'A': ..., 'B': ...})`，创建 `SessionService` 和带同一个 `InMemorySaver` 的 Agent。调用 `app.create_session('A')` 得到外部 `session_id=s1`；服务内部另存 `thread_id=t1`。随后 `app.run(s1, [('user', '第一问')])` 把 `s1` 和 `t1` 交给运行层；`create_agent` 在 `t1` 的 checkpoint 中保存消息。`app.select_model(s1, 'B')` 更新服务里的选型；下一次 `app.run(s1, [('user', '第二问')])` 仍用 `t1` 取回历史，而模型 middleware 调用同一个服务的 `resolve_model(s1)` 得到 B。这里的 ID 和消息是说明数据流的示意值；测试使用生成的 ID 和假模型。

## 当前代码地图与问题

| 入口 | 阅读重点 |
| --- | --- |
| `naiyue_test_agent/application/__init__.py:84-97` | 目录只加载一次；服务、默认模型、回调和 checkpointer 如何交给运行层；返回的对象持有什么。 |
| `naiyue_test_agent/application/__init__.py:66-81` | `LocalApplication.run` 为什么不让入口再传一个任意 Agent。 |
| `naiyue_test_agent/application/__init__.py:48-63` | 内部线程 ID、每次新 run ID 和 busy 清理。 |
| `naiyue_test_agent/agent_runtime/__init__.py:22-75` | 模型调用边界为何会调用 `resolve_model`，如何从本次 context 找 `session_id`。 |
| `tests/test_application_composition.py` | 加载计数、两次同会话历史、A→B、另一会话隔离与事件的断言。 |

待复习：`record_selection` 在这个组合入口是无副作用回调，选中的模型由现有 middleware 写 `model_selected` 日志；它不是会话选型的保存位置。会话选型只由 `SessionService.select_model` 修改。

已答理解检查：用户指出 `resolve_model`，并把 checkpoint 与模型解析对应到历史和本轮模型。精确机制是 `InMemorySaver` 持有各线程 checkpoint，复用 `thread_id` 定位历史；`SessionService` 存储 `model_id`，middleware 每次调用模型前通过 `resolve_model(session_id)` 获取模型对象。下一检查（尚未作答）：如果先执行 `app.select_model(s1, 'B')`，`thread_id` 会变吗？`resolve_model(s1)` 返回哪两个值？能独立实现与能应对追问均待验证。
