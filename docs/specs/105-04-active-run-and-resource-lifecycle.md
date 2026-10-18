# 105-04：ActiveRun、持久化装配及关闭

状态：用户已批准并实施，验收通过。承接105-03，不提前实施104或106。

版本指引：本文保留本片实施时点；当前对象/入口及组合收尾见[105-05](105-05-session-lifecycle-acceptance.md)，State瞬时字段和独立观察/子实例Map已由ActiveRun替代，不按历史字段恢复旧实现。

## 1. 目标与边界

SessionState保留10项会话事实：thread_id、model_id、workspace_root、active_tool_names、permission_mode、thinking_level、session_allow、pending_tool_approval、pending_turn、manual_review_required。新增同文件ActiveRun，拥有run_id、RunObservation、冻结资源、控制开关、停止、两条队列和本次子实例。AgentSession仅有一个_run引用，busy由是否存在推导。删除旧瞬时State字段、_current_run及独立子实例Map，不设兼容属性。

HTTP/SSE、公开工厂/业务方法、SessionPort、SQLite schema8和恢复协议不变。不新增配置、依赖、沙箱或并发许可。

## 2. 所有者及具体方法

- session_state.py：定义ActiveRun；agent_session.py：admit_run返回记录，freeze/replace/remember/mark/close/release按对象身份校验，外部停止仍校验run_id。close_control_window关闭控制且保留_run；release_run在必要收尾后移除。
- session_controls.py、session_approval.py、session_subagents.py：消费所属_run，不复制其可变事实；空闲查询返回空集合/未停止。子任务必须settle再丢弃记录；收尾失败不能遗失仍运行的driver。
- session_execution.py：_arun、aresume_tool_approval、_afinish_run传同一记录；finish快照由AgentSession生产，传给SessionPersistence.afinish，不回读协调者。
- session_service.py：_admit返回(session,run)；bind_persistence只允许启动时绑定一次；assemble_runtime接管原共享装配；stop_loaded_sessions锁内取得Actor引用元组、锁外停止，避免新建回滚后按旧ID查找失败。共享角色/图、订阅仍由Service拥有，SessionPort仍路由。
- application/__init__.py：删除_assemble_application；内存/持久化工厂调用assemble_runtime。AsyncApplication明确接收system_prompt，持久化子类明确接收storage/persistence。
- session_persistence.py：明确借用catalog/registry/permission_settings；历史/恢复IO移入现有类。session_recovery.py保留分类、配对、恢复记录、审批校验算法，审批校验显式接收配置和注册表。仍是END→tools→END三次更新。
- session_context.py：context_view显式接收sessions、agent、catalog、registry、system_prompt及可选冷历史，不接受整个App。
- async_application.py、async_persistent.py：_owned_operation登记完成信号，覆盖执行和共享IO；_tasks仅存本应用创建的流式producer。aclose拒绝新入口，请求停止，等操作和producer，再关catalog；工厂随后退出saver并关storage。

不新增通用Manager/Executor/ContextVar。原有内部消费者随片迁移；旧测试/探针不构成保留旧实现的理由。状态锁内不IO/await/外部投递，锁方向仍为持久化协调→Service→AgentSession。

## 3. 正常数据与调用链

示意：session-a/thread-a发送问题，产生run-a。

```text
app.arun → owned_operation(完成信号)
 → service._admit → A.admit_run → ActiveRun(run_id=run-a, observation=...)
 → A._arun(run) → persistence.start → 冻结资源 → 根图/子图
 → 子driver settle/assert → A.close_control_window(run)
     _run仍存在，busy=True，controls_open=False
 → persistence.afinish(sid,rid,graph,observation,finish_snapshot)
 → A.release_run(run) → _run=None → 终局观察 → 操作完成信号
```

finish_snapshot只含thread_id/manual_review_required。根待批及pending_turn是会话事实，根run收尾后保留；审批恢复新建ActiveRun。新建/冷装载/恢复均不复活旧队列、观察或子实例。旧记录即使run_id与新记录相同，也不能关闭或释放新记录。

## 4. 失败与关闭合同

保存失败仍标记持久化blocked，原执行异常优先，次要保存/观察失败记录诊断。子driver未settle时不释放根记录。无持久化路径也先完整收尾再释放。

_owned_operation在第一个await前登记Future→调用方Task，用于检测自关闭；关闭只等Future，不gather外部Task。Future完成表示settled，业务异常仍返回原调用方。嵌套调用保留上层操作，closing期间已接纳操作可完成必要嵌套步骤，新入口拒绝。关闭本身由单一_close_task承担，重复close等待同一任务，调用方取消不打断必要关闭。控制入口仍可用于停止和收尾。

例：配置保存阻塞时调用aclose，先等其成功或失败，再关共享客户端；配置调用返回后调用方自行等待其他事件，不阻挡应用关闭。持久化工厂严格app→saver→storage，原异常与次要关闭错误优先级保持。

## 5. 验收

先红灯验证ActiveRun分离/身份、保存期间占用、关闭等待操作而非外部Task、重复/取消关闭。迁移现有根/子审批、停止、失败与恢复测试，不缩减断言。再运行完整后端、前端、构建、compileall、依赖检查、500行、当前文档链接和差异检查。真实SQLite重开属于离线验证，不声明真实供应商或Windows实机通过。

## 6. 后续关系

105-05做根/子/压缩/取消/保存失败/真实SQLite重开组合验收及地图收尾；104再去掉现有全局运行门禁，默认10个根run名额。106 E1订阅范围归104-02、R1离线429隔离归104-03、P1仅保留测量基线，本片不改订阅/重试/限流。


## 7. 实施与边界验证（2026-10-18）

13个生产文件完成上述迁移；旧State瞬时字段、独立观察/子实例表、_assemble_application及恢复IO外部包装均删除。原内部测试消费者迁至记录/拥有者；原public业务API、SessionPort、DB schema8和三次恢复更新保持。测试装配观察改为patch实际Runtime入口；没有更改conftest或共享测试helper。

新增生命周期测试覆盖State/Run默认隔离、同ID旧记录、关闭期间占用、未settle子句柄保留、启动绑定、自关闭、重复/取消关闭和真实SQLite五类IO的成功/失败。另有关闭快照与新建保存回滚重叠的确定性回归：先复现KeyError和遗漏catalog关闭，再用Actor引用快照修复。旧清理失败回归要求没有活跃子工作时仍释放占用：保留原清理异常、失败持久状态，补控制关闭；仍有子句柄时禁止释放。

最初两项新测试因缺ActiveRun/旧三元准入红灯，启动后绑定和关闭回滚两个边界分别先红后绿。关闭测试第一轮缺失入口导致等待，停止该次测试并定向取得红灯；没有把该次未完成运行当作通过。最终完整验证见第9节。

已额外记录一项既有测试稳定性问题：`test_application_cli.py::test_tty_input_after_acceptance_window_closes_starts_next_run`使用的ClosingApp没有request_stop，退出恰好撞上第二个worker仍存活时会AttributeError。本片未修改该文件（与分片前字节一致），该场景绕过真实AsyncApplication/ActiveRun。定向CLI及新生命周期34项复验通过；全量失败记录保留在`/tmp/naiyue-10504-backend-final.log`，最终再复跑完整集。105-05应补齐该测试场景的确定性生命周期，不在生产层新增兼容旧替身的分支。

## 8. 当前源码追踪入口

| 数据或动作 | 生产与消费位置 |
|---|---|
| 10项会话事实、本次运行记录 | [session_state.py](../../naiyue_test_agent/application/session_state.py)，由[AgentSession](../../naiyue_test_agent/application/agent_session.py)创建/持有；_require_run按对象身份验证 |
| 准入、共享图和启动绑定 | [SessionService](../../naiyue_test_agent/application/session_service.py)：_admit→admit_run；assemble_runtime构建根图并登记角色；bind_persistence只在装配/装载前执行 |
| 运行、资源准备和必要保存 | [SessionExecution](../../naiyue_test_agent/application/session_execution.py)：_arun/aresume_tool_approval→_afinish_run；run.observation保持同一对象；finish_snapshot由Actor生产并传入afinish |
| 保存和恢复IO | [SessionPersistence](../../naiyue_test_agent/application/session_persistence.py)：afinish、_read_history、_recover_checkpoint；[session_recovery.py](../../naiyue_test_agent/application/session_recovery.py)只保留恢复分类/记录/配对及显式权限校验算法 |
| 有界上下文预览 | [context_view](../../naiyue_test_agent/application/session_context.py)显式借用图/注册表/catalog/基础提示词；冷历史由App准备，仍只读，不执行模型或更新图 |
| 操作完成信号及共享资源关闭 | [AsyncApplication](../../naiyue_test_agent/application/async_application.py)：_owned_operation→_close_resources→catalog.aclose；[持久化工厂](../../naiyue_test_agent/application/async_persistent.py)之后退出官方saver并关闭storage |
| 新行为与真实IO边界 | [test_active_run_lifecycle.py](../../tests/test_active_run_lifecycle.py)，保存期间占用继续由[test_agent_session_execution.py](../../tests/test_agent_session_execution.py)验证 |

示意输入为`arun(session-a, [('user', '检查代码')])`，本片不改变messages格式。Service创建`ActiveRun(run_id='run-a', observation=...)`，Actor.freeze_run_resources把本次prompt_parts和project_instructions写入该记录。保存时只传`{'thread_id': 'thread-a', 'manual_review_required': False}`这份限定快照，不序列化ActiveRun。根审批暂停后保留的是State.pending_tool_approval/pending_turn；用户恢复时重新准入，生成新记录，不复活旧控制队列或子driver。


## 9. 最终验收结果

- `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`：**2456 passed in 101.10s (0:01:41)**，完整日志`/tmp/naiyue-10504-backend-verified.log`。
- frontend目录`npm test -- --run`：**26文件、186 passed**，`/tmp/naiyue-10504-frontend.log`；`npm run build`：TypeScript零错误、Vite **210模块**构建成功，`/tmp/naiyue-10504-build.log`。
- 新增生命周期18项通过；重点根执行/持久化/Runtime入口/SessionPort/Web管理88项通过，CLI及生命周期34项复验通过。原事件、异常身份和恢复断言保持；未放宽断言、skip或添加测试兼容生产分支。
- `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、pip check、395份Python AST及500行上限、Runtime反向application导入0、已删除内部入口实际消费者0、`git diff --check`通过。当前变更文档699本地目标/71标题锚点通过。
- 架构/安全专审完成；安全审查发现的关闭与新建回滚竞态先复现，再用Actor快照闭合，定向25项复验通过。只记录一个已有CLI替身时序待办到105-05，未修改其生产路径。

本片修改13个生产文件、31个测试文件（含新增test_active_run_lifecycle.py），10份docs及README；没有新增生产.py、依赖、配置、DB迁移或供应商请求。公开应用业务API、SessionPort、HTTP/SSE、schema8与恢复语义保持；未提交push，原有脏树保留。本机离线/临时SQLite证据不等同Windows实机或真实供应商证明，不新增用户掌握声明。

下一片105-05只做现有链组合验收、必要消费者/测试稳定性整理和当前地图收尾；先展示精确方案，确认后实施。104仍未实施；105五片中尚余105-05。
