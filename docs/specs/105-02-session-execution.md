# Spec105-02：单会话主任务执行与控制

日期：2026-10-18。状态：用户已确认并实施，验收通过，结果见第7节。本片依赖已完成的[105-01](105-01-agent-session-state.md)，不提前实施105-03子实例迁移或104跨会话并发。

版本指引：本文保留本片实施时点；当前对象/入口及组合收尾见[105-05](105-05-session-lifecycle-acceptance.md)，State瞬时字段和独立观察/子实例Map已由ActiveRun替代，不按历史字段恢复旧实现。

## 1. 当前链与目标

改前：AsyncApplication → SessionService._admit → AgentSession.admit_run；Service._arun/aresume_tool_approval执行共享图，Service._active_runs保存观察，Service._afinish_run收尾和保存。State已由AgentSession唯一拥有。

目标：Service负责定位与准入，AgentSession负责本会话普通运行、compact、根审批恢复、资源冻结、失败、控制与finish。AgentSession._current_run为瞬时RunObservation引用，不进入SessionState/SQLite，不复制客户端、根图、工具注册表或消息。

## 2. 文件和方法

涉及8个生产文件（含说明调整），加测试/Spec超过8文件；按本片基线核对，不覆盖前序未提交工作。

| 文件 | 变更 |
|---|---|
| application/agent_session.py | 继承内部SessionExecution；初始化_current_run；admit_run原子安装busy/run/observation；close_control_window及release_run按run身份清理 |
| 新增application/session_execution.py | 同一AgentSession的执行部分，不构造独立Executor。迁入_arun、aresume_tool_approval、_prepare_resources、_on_failure、_areview_partial_state、_cleanup_run、_afinish_run和审批日志 |
| application/session_service.py | 保留_admit和公开arun/acompact/aresume_tool_approval入口；准入后直接await对象执行。删除执行实现、_active_runs及无消费者导入 |
| application/session_controls.py | 对象内部取得当前control；steer/follow_up/drain/finish边界负责记录与锁外flush。服务保留参数校验及路由，删除_control。prepare_stop返回根停止token供既有子协调使用 |
| application/session_compaction.py | 现有唯一调用方的装配逻辑迁入对象compact分支，删除薄模块 |
| application/session_approval.py | announce_approval从静态方法移为同文件函数；根、子共用原格式化/投递逻辑，无新协议 |
| application/session_subagents.py | 只改上述函数调用；子实例/driver/审批仍在服务，105-03迁移 |
| application/run_events.py | 校正拥有者说明，观察及事件协议不改 |

SessionExecution._arun(agent, messages, *, run_id, observation, subagents, max_concurrency=4, compact=False, system_prompt=None)接收已准入运行；不再反调_admit。aresume_tool_approval(agent, decisions, *, run_id, observation, subagents, max_concurrency=4, remember_approved=None, stop=False)同理。self已表示会话，无需重复传sid/session对象。

## 3. 过渡协作与锁

105-03前，执行方法的subagents参数借用当前SessionService，只调用ainvoke_subagent、_request_scope_stop、settle_subagent_drivers、assert_subagents_settled、get_registered_subagents五个既有行为。不存self._service，不读取其私有Map/锁，不新增协作服务/通用callback容器。105-03迁移实际子行为后同片删除该参数。测试可用只提供五行为的协作者确认边界。

Runtime RunServices仍只接原有ainvoke_subagent回调，禁止装入整个Service/AgentSession。compact接服务直接提供的既有system_prompt，实际执行时读取当前模型、工具、冻结prompt及共享角色定义。

锁方向继续为持久化协调 → 服务登记/准入 → 会话短状态锁；不反向持锁调用。admit_run校验失败不安装观察、不发开始事件。队列与当前control在同一短锁内配对；buffer接受/选择仍为原内部记录，外部flush、日志、停止token.request、future唤醒、子收尾和IO都在状态锁外。

## 4. 正常调用链与数据

示意：session-a/thread-a/run-a，用户消息“读取README”。Service._admit创建RunObservation及错误策略，在登记锁内调用A.admit_run。A在状态锁内校验manual_review/pending/busy/decisions，设置busy=True、run_id=run-a及_current_run=该观察。

```text
Web → AsyncApplication.arun → Service.arun/_admit → A._arun
  → observation.start → persistence.start → A._prepare_resources/freeze_run_resources
  → 既有Runtime异步入口 → 共享图(thread-a) → 模型/工具/回填
  → A.replace_pending_approval → 原审批通知 → observation.result
  → A._afinish_run → 原服务子驱动settle/assert → A._cleanup_run
  → persistence.afinish → A.release_run → 局部observation.finish
```

控制：Service.steer校验文本并定位A → A.steer，在锁内取得当前control、更新本会话队列、记录接受；锁外日志/flush。Stop的A.prepare_stop校验run和窗口并返回根token，服务继续操作原子实例Map，锁外request/唤醒。根暂停收尾保存pending后空闲；批准时同thread、新run/observation，原一次性grant/session_allow及恢复协议保持。子审批不结束父run。

| 阶段 | busy | _current_run |
|---|---|---|
| 准备/执行 | True | 本次观察 |
| 控制窗口已关闭、必要保存中 | True | None |
| 收尾完成 | False | None |

观察引用清除沿原_active_runs删除时点；执行局部引用保留至终局，供保存和投递使用。release/close须校验当前run，旧finish不得清理新run。无持久化时按原清理时点释放busy，有持久化时保存完成/失败保护后释放。

## 5. 失败链和影响

例如ProcessCleanupError E由图上抛：A._on_failure记录E并按原规则核对部分工具状态/标人工核对；A._afinish_run请求停止并等待本会话子执行、关闭控制、必要保存。若再有保存F，E仍是主异常，F记录并沿用持久阻断；仅F失败则F对外。CancelledError、GraphBubbleUp、ManualCompactionCommitError及观察故障的原合同保持，无自动重放/重试。

公开AsyncApplication/Service入口签名、SessionPort、HTTP/SSE、DB schema8、工作区身份、checkpoint/聊天档案和配置不变。不升级依赖、不访问真实模型/用户库、不开放并发。普通运行与审批恢复保留可见独立路径，共用原资源/失败/收尾方法，不新增策略解释器。

## 6. 测试、迁移与验收

先新增正确失败的行为测试：观察和busy原子安装且会话独立；无效恢复无指针/事件；控制从对象取当前观察且外部回调不持状态锁；事件同步阻塞保存时busy=True/current=None，重复启动拒绝；旧run清理不影响新run；受限子协作者可完成根执行并保持原异常。沿用普通/compact/根恢复、子待批/父停止、原异常与次异常、SQLite重开等现有行为测试。

将测试/脚本对Service执行函数、_cleanup_run、_record_steering、announce_approval、_active_runs的实际消费者迁至新拥有者，不保留重导出/别名，不放宽原顺序、配对和异常断言。native lifecycle AST检查迁移到SessionExecution，Service公开入口仍为async。无需修改conftest或公共测试helper。

定向：.venv/bin/python -m pytest -q tests/test_agent_session_execution.py tests/test_agent_session.py tests/test_application_run_events.py tests/test_application_run_event_failures.py tests/test_control_races_and_failures.py tests/test_async_persistent.py tests/test_subagent_approval_resume.py --tb=short。

最终：PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q；frontend内npm test -- --run及npm run build；compileall、pip check、500行/旧入口/分层检查、文档链接与git diff --check。本片代码回退不涉及数据迁移；不提交或push。

## 7. 实际实现与验收（2026-10-18）

已按合同落地8个生产文件、18个测试文件（含新建tests/test_agent_session_execution.py），未改conftest或测试helper。State外新增的_current_run在admit_run同锁安装，在close_control_window/release_run按run身份清除。Service._active_runs和旧执行方法已删除；session_compaction.py的原装配合入执行compact分支，未留重导出/兼容别名。根/子审批通知使用同文件announce_approval函数。

实际普通链：Service.arun/_admit → A.admit_run → A._arun → persistence.start → A._prepare_resources → 原Runtime/共享图 → A.replace_pending_approval/原通知 → A._afinish_run → 原服务子settle/assert → A._cleanup_run → persistence.afinish → A.release_run → 局部observation.finish。compact和根恢复分别进入其原执行段，one-use grant/session_allow及原turn恢复保持。

实际控制链：Service校验文本并定位A → A.steer/follow_up，锁内将队列与_current_run.controls配对，锁外日志及flush；根Stop由A.prepare_stop返回对应停止token，服务按原子实例Map协调子停止/唤醒，锁外request。工具/模型/文件和数据库IO不占状态锁。必要保存被事件阻塞时_current_run已None但busy仍True，新准入明确拒绝，保存后才解除并发占用。

失败链继续由A._on_failure核对原部分工具状态，A._afinish_run停止/settle、清理、必要保存；原运行异常对象及次级保存错误优先级由现有故障注入测试保持。无保存成功的伪完成，无自动重试。旧run调用cleanup/release会拒绝且保留新运行引用；无效恢复没有指针、开始事件或Allow副作用。

- 新测试初次因测试fixture import路径拼写错误未收集，修正到现有fixture后才进入正式红灯；该收集错误不计TDD证据。正式红灯命令：.venv/bin/python -m pytest -q tests/test_agent_session_execution.py --tb=short，7 failed in0.69s，均为计划新增拥有者/方法或新控制签名缺失，见/tmp/naiyue-10502-red.log。迁移后7 passed in0.65s。
- 定向组合：119 passed in2.17s，见/tmp/naiyue-10502-focused.log。初次118通过/1失败为遗漏旧故障注入位置，迁至当前执行模块后原断言通过；没有回添旧入口。
- 全后端：PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q，2434 passed in100.55s（0:01:40），见/tmp/naiyue-10502-backend.log。
- 前端：frontend内npm test -- --run，26 files/186 passed，见/tmp/naiyue-10502-frontend.log。npm run build的TypeScript检查及Vite210模块成功，见/tmp/naiyue-10502-build.log。
- compileall及pip check成功（No broken requirements found）；393份生产/脚本/测试Python AST解析且均不超500行，Runtime反向application导入为0。独立架构、安全只读复核未发现本片引入的确证问题；主审另核对迟到旧run、保存等待、停止和子协作组合。

12份变更文档及README合计683个本地链接目标、86个标题锚点检查通过；其中修正future-work原有一处指向project-plan第16节的过期锚点，未改业务。git diff --check通过。脚本无旧入口消费者需迁移，因此本片没有脚本代码变更。

以上是本机确定性/假模型及临时SQLite证据，不代表真实供应商或Windows原机验收。未改配置、用户数据库、依赖或前端行为；没有提交push。

## 8. 105-02收尾时的下一片：105-03

本节保留105-02收尾时点；[105-03现已实施验收](105-03-session-subagents.md#8-实际实现与验收2026-10-18)，以下为当时计划：105-03将Service._subagent_instances[(sid,path)]改为各AgentSession._subagent_instances[path]的唯一实例表；迁移prepare_subagent_instance/ainvoke_subagent/_subagent_steps、live审批注册/提交/释放、scope停止及settle/assert。共享角色定义和角色图仍归服务，模型/工具/权限的SessionPort继续按sid/path路由，不复制图或客户端。

下一片要具体讲清共享角色如何传给会话、子审批如何同锁验证父run与本实例、锁外future唤醒/driver收尾，以及根finish如何直接调用本会话行为并删除本片临时subagents参数。当前一层、前台、批量审批、不同path、兄弟失败和独立审批恢复语义保持。105-03尚未写实施分片或实施，不在本片假装子实例已归对象。
