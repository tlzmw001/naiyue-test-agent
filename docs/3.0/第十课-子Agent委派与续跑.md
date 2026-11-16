# 第十课 · 子 Agent 委派与续跑

对应场景10。四段及父子持久化补读已讲；用户已提交章末复述。下表保留用户理解与对应纠正；纠正后的再次复述、本人调试和独立故障判断尚未评估。

## 用户复述与对应纠正

| 用户复述 | 核对与补准 |
| --- | --- |
| SessionService 的属性保存子实例，AgentSession 拿到服务便能知道有哪些实例 | 必须区分角色目录与运行实例。SessionService._subagents 按角色名保存 spec 和编译图；当前活跃实例实际在 AS._runtime.run.subagent_instances，由 AS.subagents（SubagentRegistry）借用同一个 SessionRuntime 管理。AS 收到角色查询/委派等方法引用及依赖，不是靠持有整个 SessionService 管理实例。 |
| 这样可以跨会话查询 | 普通跨轮复用 AS，重建 AR/driver；已结束子实例的历史与登记依靠持久化查询，按原 session_id 归属。重启可读取已保存状态；另一个逻辑会话不能因此接用该实例。同一角色可供不同会话使用，不等于共享子线程。 |
| 创建异步任务、启动 driver，里面执行图的 Agent 循环 | 主线正确。driver 本身就是 create_task 返回的 asyncio.Task，驱动 _subagent_steps；编译图在角色注册时已创建，并非每次 driver 内新建。子图通过独立 thread_id 隔离执行历史，未创建新 AgentSession 或 OS 进程。 |
| 审批保存到子实例，向外暴露，批准后填回 Future | 主线正确。ApprovalBook 校验返回结果与 checkpoint 后，把 PendingToolApproval 挂在 instance['approval']；其 future 在内存中。子 driver await future，提交决定后 set_result 唤醒原 driver；原父运行仍占用中。审批恢复保持子线程和 driver，但更新子执行 run_id，不重发任务消息。 |
| 父停止时，在收尾额外查询子实例是否结束 | 还要补上主动控制：根停止当场标记父 AR，并向所属子 RC 传播信号；待审批子实例还要失效审批、唤醒 Future。父收尾等待子 driver 结束、确认无未完成审批/任务，再持久化并释放。单独停止子实例不设置父停止标志，也不停止兄弟实例。 |
| ready 可以恢复；审批或持久化前崩溃不恢复，留下说明 | 要区分恢复历史与恢复执行。子图已经写 checkpoint 也可能崩溃；历史仍可保留。正常确认完成的实例可在恢复父会话后，经新委派校验续跑。执行/子审批中崩溃不会复活原 driver、Future 或自动重放委派；父图对缺失委派结果补 result_unconfirmed 错误消息，子历史保留供检查。根图可恢复的审批与活跃子审批不是同一路恢复分支。 |
| 首次返回 agent_id，再用它找到已完成子图继续 | 主线正确。按 (session_id, agent_id) 查实例登记，校验角色/执行绑定、允许状态、确认 checkpoint 和干净图状态。新建本次 instance 字典、driver、RC、run_id，向原子线程追加新任务；不是复活旧 Task。正常跨轮要求 ready；同一父运行内也允许其已成功结束的 pending_parent 实例。 |

## 固定对象与身份地图

```text
SessionService
├─ _subagents[角色名] → {spec: SubAgentSpec, agent: 编译图}
└─ _sessions[session_id] → AS：AgentSession
   ├─ _runtime → SR：SessionRuntime
   │  └─ run → AR：ActiveRun（每次父运行新建）
   │     └─ subagent_instances[agent_path] → 本次 instance 字典
   │        ├─ agent_id / child_thread_id（续跑沿用）
   │        ├─ driver：Task → _subagent_steps(已注册图, instance)
   │        ├─ control：RunControl（新委派新建）
   │        └─ approval：PendingToolApproval → future
   ├─ subagents：SubagentRegistry ─┐
   ├─ approvals：ApprovalBook ─────┼→ 共享同一个 SR
   └─ controls：ControlQueue ─────┘
```

角色图按角色复用；逻辑子实例按 agent_id/子线程区分；每次委派执行另建 driver/控制对象。图调用的 RuntimeContext、应用 SessionRuntime 与 contextvars.Context() 是不同对象。Context() 为新 Task 提供独立上下文变量环境，能力通过应用显式装配的 scope 传入。

## 主链与关键边界

1. 注册：SessionService.register_subagent → build_subagent_runtime → 保存角色 spec/图。委派工具通过上下文中保存的方法引用进入 SessionService.ainvoke_subagent，再按 session_id/角色查找并直接调用 AS.subagents.ainvoke_subagent。
2. 准入：准备身份和实例 → subagent_records.admit 校验、版本条件写入 running → create_task(_subagent_steps(...), context=Context()) → await shield(driver)。shield 隔离外层取消的自动传播；外层取消后仍会请求子停止并等待结束，不把任务遗留后台。
3. 执行：独立子 RC/RunIdentity/RuntimeContext，借用文件与进程能力；任务消息进入 child_thread_id 对应的图。子图工具权限仍在执行边界检查；不因委派自动获得父批准。子实例不再递归委派。
4. 返回：子内部工具结果保留在子线程；最终 AI 文本经校验与预算裁剪形成报告，外层工具转换为父 ToolMessage。角色可并行委派，父图等待整批工具；单次 await 不代表父只能启动一个子实例。同一实例并发续跑拒绝而非排队。
5. 确认：子成功 settle → pending_parent；父事务保存归档并核对登记/归档/最新 checkpoint 一致后 → ready。子失败/停止另记状态；尚未提交图输入的占用可撤回到原登记。
6. 活跃审批：保存图中断 → 校验并登记 live_child → await future → 提交校验/批准凭证 → 唤醒原 driver → Command(resume=...) 进入原线程，不重新种入任务。拒绝走工具错误反馈，不等于停止。
7. 停止与收尾：ControlQueue 按 root/子 agent_path 定位 RC；审批等待另需唤醒。RunLifecycle 等待并检查所有所属子工作，再关闭控制窗口、保存和释放；已发生的工具副作用不因停止回滚。
8. 重启：保留历史不等于恢复旧任务。执行中崩溃的委派不会自动重放，也不会复活 live_child 审批。恢复流程配对父图缺失结果，保留可查的子历史；新委派能否续跑取决于登记和图状态的完整校验。

## 父子持久化关系

| 数据 | 当前 SQLite 路径 |
| --- | --- |
| 图状态/执行写入 | 共用 checkpoints/writes，以 thread_id 等键区分；子 SubagentCheckpointSaver 包装同一后端，不另建存储。 |
| 应用管理 | 父会话用 naiyue_sessions；子登记用 naiyue_subagent_instances，通过 session_id 归属、child_thread_id 指向子图。 |
| 聊天归档 | 共用 naiyue_chat_messages/naiyue_chat_sync，按 session_id/thread_id/checkpoint_ns 区分。父线程保存外层委派结果，子完整过程属于子线程。 |

父事务确认应用登记与归档，不表示图执行期间的所有 checkpoint 写入也与该事务一次提交。

## 当前源码与测试学习入口

- 装配/归属：application/sessions/agent_session.py、session_service.py；application/subagents/session_subagents.py。
- 图输入/报告：agent_runtime/subagents.py；续跑校验：application/subagents/subagent_records.py。
- 活跃审批/停止：application/sessions/session_approval.py、session_controls.py；父收尾：session_execution.py。
- 持久化：storage/subagent_sql.py、unit_of_work.py；崩溃配对：application/sessions/session_recovery.py。
- `tests/test_subagent_execution.py::test_parent_receives_only_report_and_artifact_child_uses_real_read`：模拟模型，检查真实读工具、子历史与共享 checkpoint 后端。
- `tests/test_subagent_continuation.py::test_same_instance_next_user_turn_keeps_history_new_controls_and_usage`：旧线程与新执行对象；同文件的无效续跑/忙实例用例观察拒绝边界。
- `tests/test_subagent_approval_resume.py::test_a_approved_and_finished_while_b_still_runs_whole_child_batch_waits`：模拟父子模型与等待事件，检查审批恢复不重启父调用，整批工具完成前父不继续。
- `tests/test_subagent_stop.py::test_stop_one_pending_child_keeps_sibling_running_and_decision_history`：事件同步，检查单子停止、审批失效、兄弟和父运行继续。
- `tests/test_subagent_restart.py::test_restart_pairs_root_delegation_without_replaying_child_or_reviving_approval`：模拟模型，真实进程退出、SQLite/文件副作用；检查子历史保留、父结果配对与无自动重放。

本课证据为当前源码及既有测试定义走读，未执行功能测试、构建、真实模型请求或用户本人单步调试。持久化分类、并行批次/权限细节及停止边界由助手讲解；用户本次未独立完整复述。下一课为场景11「跨会话记忆与来源校验」，等用户明确开始后再进入。
