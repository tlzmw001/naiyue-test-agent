# Spec128-06：会话行为由 mixin 改为显式协作对象

状态：用户批准按05B收尾说明实施06，已完成本机验收（2026-10-23）。基线为v0.4.0隔离分支上的01—05B，保存在build/spec128-06/baseline。只做会话组合；实际worktree生命周期、观察器统一和发布独立。不改配置、依赖、版本、真实用户库，不提交/push/发布。

## 1. 当前链路与稳定合同

当前Application.runs → SessionService._admit → AgentSession.admit_run；AgentSession由Controls/Execution/Subagents/Approval四个mixin组成，方法隐式读取其他文件创建的_state/_run/_lock、persistence和资源。SessionService也继承ControlMixin和SubagentControls，实际注册/路由分布在三个文件。

稳定外部接口：两个应用工厂、05B领域API、SessionPort运行端口、CLI命令及owner-loop、HTTP/SSE认证/归属/幂等/错误映射。SQLite schema18、必要保存/UOW、执行目录和审批许可绑定、memory项目/个人范围、RAM能力限制不变。AgentSession/mixin不是承诺的外部Python合同；当前内部消费者及故障注入按真实所有者迁移，不保留测试专用旧入口。

正常例：runs.start → SessionService按原注册锁/容量/执行目录检查准入 → owner.lifecycle.admit_run写入唯一ActiveRun → lifecycle.arun准备资源并调用原LangChain → children登记、执行、等待审批/续跑 → lifecycle收束children和controls → SessionPersistence.afinish/UOW必要保存 → 释放run → 原终局事件。保存完成前busy不能提前消失。

失败例：根执行失败或取消 → 保留原异常 → controls标记并请求根/子停止 → 等子driver和审批future收束 → 清空控制窗口 → 必要保存 → 能释放时释放 → finish；后续清理/保存/观察失败按原合同处理，不能覆盖主异常。保存失败仍未确认；子实例未收束仍不能释放。过期run必须按对象身份拒绝，即使run_id相同。

## 2. 唯一状态与明确依赖

在现有session_state.py增加内部SessionRuntime数据句柄：session_id、SessionState、ActiveRun|None、原短Lock。它不保存到DB。AgentSession拥有这个句柄，四对象借用同一个实例，不能复制State/run/锁。require_run和require_subagent只检查持锁时的对象身份，不承担IO或执行。SessionState与ActiveRun仍分别表示持久事实和瞬态执行，不把run字段混入持久状态。

AgentSession保留实际使用的会话配置、快照和SessionPort查询职责及配置提交锁；执行/控制/审批/子行为迁到四对象。各对象构造函数显式接收runtime和实际使用的依赖，不接收整个AgentSession/SessionService/Application，不使用__getattr__代理、通用注册表或新的ContextVar。

| 对象 | 方法与依赖 |
| --- | --- |
| RunLifecycle（现session_execution.py） | root arun/approval resume、资源准备、失败检查、收尾；从AgentSession迁入admit/release/freeze/run快照/再生成切换；显式借用controls/approvals/subagents、persistence、registry、MCP/process/archive；动态模型和活动工具沿已有查询方法直接注入两个回调，保持原读取时机 |
| ControlQueue（现session_controls.py） | steer/follow_up/stop、边界消费、消息标记、close_control_window和队列日志；只拿runtime。停止仍在同一个短锁里选定根/子目标，出锁后才request/flush/wake |
| ApprovalBook（现session_approval.py） | 根pending/remember/review、根批次校验、子live approval登记/提交/释放；只拿runtime；复用现有纯校验/许可函数 |
| SubagentRegistry（现subagents/session_subagents.py） | 实例准备、driver、步骤、settle/assert/status/thread ids/list；显式借用controls/approvals、catalog/registry定义查询、persistence和实际资源，不拥有应用角色定义 |

复用现有模块而不平铺新文件。SessionRuntime用于四处共享的当前run所有权和三处身份校验，有真实重复职责；两个动态配置回调在root准备/压缩时有当前调用方，直接传参会冻结原可变选择，故显式注入现有绑定方法，不建设通用回调层。

SessionService现有注册/恢复/删除guard/运行端口路由/共享角色方法收回本类，删除两个mixin。仍是一份_sessions/_subagents和同一个注册锁；没有新会话路由框架。运行端口路由到owner.controls/approvals/subagents/lifecycle。应用级角色图仍由SessionService构建/注册，各会话借用。

子续跑subagent_records当前隐式读取owner私有字段，改为admit显式接收runtime/catalog/会话仓储/子仓储/result settings，settle只接收子仓储。执行绑定、版本、claim取消补偿、必要settle次序保持；不新增第五套持久化对象。

## 3. 锁、状态和迁移清理

保留注册锁→会话短锁的方向。同一个runtime锁内完成run/控制队列/子实例/审批状态的配对变更；driver等待、future唤醒、模型、资源发现、数据库IO及observer flush留在原锁外。配置选择的三个串行锁及先保存后提交内存保持。

RunLifecycle的记忆prompt快照延续原逻辑轮/审批恢复合同；不重新检索、不因对象拆分失去已准备内容。根子RunScope、执行目录、权限绑定仍由原生产者生产。close/stop对已运行后台任务的语义保持。

迁移Application的RunAPI/HistoryAPI、SessionPersistence/恢复/重新生成和SessionService等实际消费者。测试/脚本的_run/_state/_lock观察点迁到runtime，故障注入迁到具体协作对象；保留异常身份、事件顺序/配对、必要保存、busy和目录失效断言。删除四个旧mixin及两个service mixin、仅旧mixin使用的导入、测试专用owner转发，不保留双执行路径。保留有真实消费者的SessionPort/配置查询和纯函数。

## 4. 验证

先写并看到正确红灯：AgentSession/SessionService无mixin、四对象共享runtime且不持有完整owner、会话间隔离、Service控制路由、同ID旧run身份拒绝、控制关闭后仍需显式release。随后最小迁移，复用已有运行/停止/审批/子续跑/必要保存/重开/失败注入测试。

完成后核查真实接线和旧入口清理；定向与全量后端、前端423/TS-Vite、Mac ARM64标准wheel/Twine/源码外安装、AST/500行/compile/pip/diff、包源码一致和冻结无漂移。架构/安全独立复核及主代理对抗检查聚焦本片相对05B差异。分别记录红灯、既有测试迁移、最终结果；不把本地固定模型当实际业务质量证明。

测试迁移说明：审批通知和子实例捕获辅助函数原先检查owner is self，现在分别检查owner.approvals/subagents is self，保留同一应用/会话的对象身份过滤；没有添加自动成功或放宽事件断言。直接异步结构测试原先禁止整个模块的yield，SessionService收回原deletion_guard后，同模块已有合法同步contextmanager；现明确检查它只有一个yield，其余所有位置及全部async方法仍禁止yield/IOCall/arun_steps。两个仓储故障注入包装保留claim完成后、输入开始前的原位置，仅适配显式参数。

## 5. 当前对象、可追踪主链与删除项

实际对象由SessionService.create/install_restored构建，形状如下。runtime是当前run的唯一槽位；外部保存仍只映射SessionState的既有字段。

```text
AgentSession
  _runtime: SessionRuntime(session_id, state, run, lock)
  controls: ControlQueue(_runtime)
  approvals: ApprovalBook(_runtime)
  subagents: SubagentRegistry(_runtime, controls, approvals, 必需资源)
  lifecycle: RunLifecycle(_runtime, controls, approvals, subagents, 必需资源)
```

实际正常分支可从`test_session_lifecycle_integration.test_root_then_child_approval_keeps_owner_until_required_save[success]`走读：

1. 根工具已待审批，`app.runs.resume_approval`把决定传给SessionService，沿同一个注册锁和原校验顺序进入`session.lifecycle.admit_run/aresume_tool_approval`；新ActiveRun写入runtime.run，返回消息结果前保持所有权。
2. 根subagent工具通过原RunScope能力调用SessionService.ainvoke_subagent；共享角色字典选定graph后交给`session.subagents.ainvoke_subagent`。Registry在runtime锁下登记实例，records.admit校验保存目录/项目/角色/版本并必要保存claim，随后才开始driver。
3. 子checkpoint前置审批交给`session.approvals.register_live_approval`，返回PendingToolApproval及future。`app.runs.submit_subagent_approval`路由回同一Book，按批次/源run/实例校验，出锁后唤醒future；只恢复原interrupt，不重放已完成工具。
4. 子步骤结束后释放MCP/审批、必要settle并标记driver收束；根收尾依次stop（仅错误）、settle/check子实例、controls.close_control_window、SessionPersistence.afinish、lifecycle.release_run、observation.finish。测试在必要保存中用Event阻塞，明确检查busy仍为true、controls_open为false、子driver/future已完成且实例表已清空；保存完成才出现根completed终局并释放run。

失败分支由同一测试的save_failure/primary_error/cancel/close覆盖：模型主异常保留对象身份，取消等待已开始保存，关闭在子审批后停子并等待全部必要保存，保存失败不发布completed。较小入口`test_session_collaborators.test_stale_same_id_run_cannot_mutate_current_collaborators`在相同run_id下更换ActiveRun对象，旧对象不能关闭队列、释放当前run或安装pending审批。

删除项为四个AgentSession mixin和两个SessionService mixin；原模块因仍拥有纯函数或新的真实类继续保留。AgentSession仅保留15个实际使用的配置/只读快照方法，状态路径归一化后主体与05B相同；不为测试保留旧arun、控制、审批或子生命周期转发。Service原注册/共享角色/SessionPort方法直接属于本类，数据库和运行框架接口保持。测试类级故障注入分别指向RunLifecycle、ControlQueue、ApprovalBook、SubagentRegistry，脚本观察点指向runtime。

实现阶段曾由定向测试和安全复核发现`observe`随admit_run搬迁漏导入，已在源代码补齐；根审批通过和停止原失败分支均已转绿。首轮还补齐HumanMessage类型注解导入。红灯和中途失败日志保留，不将这些中途轮次计为最终通过。

## 6. 实际验收与限制（2026-10-23）

| 范围 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| 三项新合同 | `.venv/bin/python -m pytest tests/test_session_collaborators.py -q --tb=short` | red.log为3项预期失败；core-second.log为3 passed in0.84s |
| 会话/控制/审批/保存 | consumer-second.log | 126 passed in3.67s |
| 停止/子续跑/崩溃/失败边界 | failure-first.log | 96 passed in6.41s |
| 原生异步/领域结构/组合 | structure-final.log | 29 passed in4.43s |
| 后端全量 | `PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **4745 passed in344.19s**，exit0，backend-first.log |
| 前端 | frontend目录下`npm test -- --run`、`npm run build` | 50文件/423 passed；TS/Vite成功，既有chunk体积提示保留 |
| 标准wheel/元数据 | `scripts.build_release --target macos-arm64 --output build/spec128-06/verified-dist`，`twine check` | 成功，370文件；240 Python/4前端资源/README元数据与当前源码一致 |
| 全新源码外安装 | `scripts.verify_wheel_install --wheel-dir build/spec128-06/verified-dist --output build/spec128-06/install-verified.json` | CLI/Web、文件工具、审批/重开、后台任务、子Agent续跑、memory等固定场景全通过；0供应商请求 |
| 结构/冻结 | AST/500行、compileall、pip check、git diff --check、正逆冷导入 | 240模块通过；10模块两种顺序导入通过；841文件无漂移；141份底层/适配器/装配文件相对05B不变 |

独立架构专项20 passed in1.48s、安全复核关闭observe遗漏后31 passed in2.77s，未遗留发现；48个迁移方法中46个路由/状态路径归一化后AST相同，其余两项为等价持锁读取和显式records依赖；Service40方法与AgentSession保留15方法同样对照。SessionState、ActiveRun AST及schema18保持。三项新合同不替代原有正常/失败/并发/权限与持久化测试。

证据入口为build/spec128-06/verification.json、review-findings.json、artifact-verified.json与install-verified.json。中途core-first的HumanMessage导入错误和consumer-first的14失败/112通过保留；修正源代码、身份过滤与结构检查后定向/全量转绿，没有删测试、放宽业务断言或skip。

本片仅本机Mac ARM64、确定性模型和受控本地服务验证，未运行其他平台原机或远端CI，未验证真实供应商业务质量。前01—05B、主目录并行工作和真实配置/用户库/依赖/版本保持，未提交/push/发布。分片口径更正：本Spec的06合并了原十片中的8（控制/审批）、9（子实例管理）、10（运行生命周期）；代码已落地并完成组合验证，未按原三片分别收束和交付。对应关系见[原十片总表](128-project-execution-and-run-context.md#2-原定十片与实际实施记录)。实际worktree创建、附着、登记、合并后的会话与回收仍待独立功能Spec。唯一下一步：先分别核对原8/9/10的范围、说明和验收证据，再讨论worktree。Control/ContextOperation观察器统一仍是次要待选项，不随本片实施。
