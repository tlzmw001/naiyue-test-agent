# Lesson 5：应用会话怎样发起一次运行

> 本章只读 `application.SessionService` 的会话创建与同步 `run()`。2026-09-30 开始；不修改功能代码。

## 最小调用链

```text
测试/未来应用入口
  → SessionService.create_session(model_id)
      → ModelCatalog.get(model_id)：先校验模型
      → 保存 session_id → {内部 thread_id, model_id, busy}
  → SessionService.run(agent, session_id, messages)
      → invoke_agent_run(agent, ..., session_id, thread_id, run_id)
      → agent.invoke(...) / LangGraph checkpointer
```

## 当前代码地图

| 入口 | 本章作用 |
| --- | --- |
| `naiyue_test_agent/application/__init__.py:17-33` | 持有目录和会话表，校验模型后创建会话 |
| `naiyue_test_agent/application/__init__.py:45-61` | 检查同会话 busy，生成 run ID，调用运行层并清理 busy |
| `naiyue_test_agent/agent_runtime/__init__.py:164-185` | 记录同步 run 开始/终局，把消息与 ID 交给 LangChain Agent；当前行号已按 Spec 015 更新 |
| `tests/test_application_session.py:20-48` | 假模型的两次同会话运行、切换与另一会话隔离 |

## 第一段：创建会话

`create_session('A')` 先调用 `ModelCatalog.get('A')`；找不到就抛 `ModelConfigError`，后面的加锁和会话表写入均不会执行。成功后生成外部 `session_id` 与另一个内部 `thread_id`，保存所选模型 A 和 `busy=False`，只返回外部 ID。课堂中的 `s1/t1` 是解释用示意值，不是实际 UUID 或模型请求输出。

理解检查：用户回答“不会增加记录，因为在第一步 get 的时候就抛异常，然后结束”。与真实执行顺序一致，记录为**讲解后正确判断**；尚无独立实现或面试追问证据。

## 第二段：一次同步运行

按 `tests/test_application_session.py:39` 的 `service.run(agent, first_session, [('user', 'private-first')])` 阅读。`run` 在锁内取得会话记录，busy 时抛 `SessionBusyError`；否则把 busy 设为 True，取该会话的内部 thread ID 并生成本次 run ID。退出锁后调用 `invoke_agent_run`；无论其返回还是抛异常，`finally` 都重新加锁把 busy 清为 False。锁不跨模型/工具调用持有，因此其他会话的运行与已提交的模型切换不会被这个锁长期挡住。

理解检查：用户回答模型调用失败时“异常，false”，即原异常继续传给调用方、`finally` 清理后 `busy=False`。与代码路径一致，记录为**讲解后正确判断**；尚无独立实现或面试追问证据。

## 第三段：运行层怎样把参数交给 Agent

输入示意：`messages=[('user', 'private-first')]`，应用层生成的三个 ID 分别记作 `session_id=s1`、`thread_id=t1`、`run_id=r1`。`invoke_agent_run` 先检查 ID 非空，再记录 `run_started(r1)`；`agent.invoke` 的 input 包含本次消息，`context` 包含 `s1/r1`，`config.configurable.thread_id` 包含 `t1`。成功记录 `run_completed(r1)` 并返回原结果；失败记录 `run_failed(r1)` 后原样抛出。Spec 015 后，这些 run 事件同时含 `session_id=s1`，但仍不含内部 `thread_id=t1`。`s1/t1/r1` 均为解释用示意值，真实 ID 由 `uuid4().hex` 生成。

本段已讲解，用户理解反馈待收集。下一段沿 `context.session_id` 进入 middleware 和 `SessionService.resolve_model`，解释 A/B 选型；新增工具事件的代码地图见 [Lesson 6](lesson-6.md)。不把本章已读代码等同于独立实现。
