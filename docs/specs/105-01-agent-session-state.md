# Spec105-01：单会话状态与受控操作

日期：2026-10-18。状态：用户2026-10-18授权；已实现并完成本片验收。第1—6节保留实施合同，实际结果和下一片见第7—8节。依赖[Spec105总设计](105-session-ownership-refactor.md)。用户已批准本片，保持四层依赖方向；不扩大到105-02或104。

架构复核：AgentSession属于application层，只编排会话事实与行为；模型适配仍在model_access，图/工具执行接线仍在agent_runtime，测试策略仍在test_agent。Runtime仅依赖SessionPort，不导入AgentSession。A.select_model与B.select_model绑定同一类方法，self指向不同实例，独立的是State/锁而非方法代码。

版本指引：本文保留本片实施时点；当前对象/入口及组合收尾见[105-05](105-05-session-lifecycle-acceptance.md)，State瞬时字段和独立观察/子实例Map已由ActiveRun替代，不按历史字段恢复旧实现。

## 1. 本片目标

使一个AgentSession实际拥有一个SessionState及其读写操作。SessionService继续管理sid集合和编排运行，但不再直接修改某个会话的State。第一片不把运行IO、当前RunObservation或子实例一并换主人：它们分别在105-02/03迁移，当前只有一份，按原身份关联。

这片是有行为的状态归属迁移：配置写入、运行准入状态转换、根待批替换、临时Allow、控制队列、资源冻结、人工核对和释放busy均进入对象。不能仅包一层.state或__getattr__，再让服务继续任意修改里面字段。

## 2. 当前链路与真实数据

当前SessionService._sessions[sid]直接保存SessionState。新建在SessionControlMixin._create_session构造，恢复在install_restored_session构造。SessionService、controls、approval、subagents通过同一服务锁读写它；其他组件已经使用受控快照。

示意会话A：session_id=session-a，thread_id=thread-a，workspace_root为已规范化Path，model_id为当前目录内实际选项；active_tool_names为注册名字元组，permission_mode=Default，busy=False、run_id=None，队列各自为空。示意身份不是实际模型/运行记录。

改后_sessions[session-a]保存AgentSession；其session_id只对应A，_state仍为原SessionState实例。模型目录、工具注册表、权限设置和持久化实现作为借用引用传入；不新建同类客户端或库。不会复制一份state到Service以便同步。

## 3. 文件与类/方法

原计划检查8个生产文件及相关测试/探针；实际修改6个生产文件，persistence/context继续消费同名受控入口，无需为接线而改文件；范围限定为State的拥有和访问，不改图/工具/HTTP协议。实施前逐项搜索实际消费者，已有行为优先迁移，不重新设计算法。

### 3.1 新增application/agent_session.py

新增AgentSession，构造入参为session_id、SessionState及本片实际需要的catalog/tool_registry/permission_settings/persistence引用。_state与状态短锁归该对象；配置提交锁也归该对象，不从协调者取得可变State或锁。

按消费者提供以下操作；已有名字在对象内去掉冗余session_id参数，service按sid路由：

| 操作 | 输入与输出/副作用 | 实际消费者 |
|---|---|---|
| describe_session、get_thread_id、is_busy | 形成既有字段形状的限定结果/返回thread字符串；is_busy给协调者作准入、删除和分类策略变更检查 | CLI/Web描述、图/历史定位、服务协调 |
| snapshot_for_finish、snapshot_for_context、context_snapshot_is_current、approval_seed_identity | 按现有内容复制/复核；不返回State | persistence、context预览、审批准入 |
| snapshot_for_run | 取thread_id/workspace_root/model_id/busy/run_id/stop_requested/steering_open及必要pending/pending_turn副本，不含观察对象/子任务 | 旧主执行编排、子实例准备及审批身份核对 |
| select_model、select_thinking、set_active_tools、set_permission_mode | 校验→原storage对应写入→内存提交；根模型变更重置thinking | AsyncApplication原设置入口 |
| resolve_model、get_active_tools、get_permission_mode、resolve_permission_context、resolve_prompt_parts | 根配置/冻结资源快照；列表和嵌套Allow不暴露可变引用 | Service/SessionPort根路径；子路径仍由现有实例逻辑选取 |
| admit_run | 接mode/run_id/observation和现有审批参数；原子校验并安装busy/run/控制窗口；运行事实由snapshot_for_run提供 | Service._admit |
| freeze_run_resources | 接run_id及准备好的prompt_parts/project_instructions；冻结到本次run | Service._prepare_resources |
| replace_pending_approval、remember_approved_rules | 接本run身份与已准备pending/rules；替换根待批、按原去重合并临时Allow | 普通/审批恢复、子审批提交 |
| mark_manual_review | 标记人工核对并清空不再可信的根待批 | Service._on_failure |
| pending_tool_approvals、prepare_root_approval_submission、validate_root_approval_stop | 复制根待批/按原批次和来源run校验输入，不消费或执行工具 | Service根审批路由 |
| steer、follow_up、is_stop_requested、drain_steering、finish_run_boundary、prepare_control_messages | 接文本或既有观察/控制依赖；按原优先级操作本会话队列/turn | Service控制路由与SessionPort |
| prepare_stop | 校验本会话busy/run并设置根停止及队列状态，返回原停止操作所需的限定事实 | Service._request_scope_stop仍负责关联旧观察Map及全局子实例 |
| close_control_window、release_run | 前者关闭控制/留pending_turn/清队列与冻结资源；后者在原收尾时点清busy/run | Service._cleanup_run/_afinish_run |

admit_run只完成原子状态转换，避免生成无人消费的快照；Service._admit仍返回对象/run_id/observation，执行入口通过snapshot_for_run一次取得thread_id、workspace_root和根pending副本，不加通用RunRequest/策略类。配置快照和控制窗口操作承担具体分支，不按18个字段做18个setter。若文件接近500行，队列/停止操作迁移到现有session_controls.py中AgentSession内部部分；不引入独立状态服务或只转发的对象。

admit_run保留现有顺序：manual_review → 根pending与mode关系 → busy → 审批decisions/remember校验 → 安装run。无效审批不得产生开始事件、数据库running或临时Allow。RunObservation仍在Service按当前分类策略构造/登记，不写入State。

### 3.2 修改application/session_service.py

- _sessions类型改为sid → AgentSession。公共入口按sid取到对象；registry锁仅用于登记、定位及跨会话准入协调。
- select_model/resolve_model/resolve_prompt_parts等调用对象中对应实现，不保留旧字段读写块。
- _admit在服务短锁内调用对象admit_run，再安装现有_active_runs[sid]；返回对象、run_id、observation。校验与安装仍是同一次准入，不拆成两个可交错请求。
- _arun/aresume读取准入返回的限定事实；_prepare_resources仍在锁外做IO，完成后调用freeze_run_resources。
- 根待批、临时Allow和失败人工核对分别调用对应操作；_cleanup_run关闭本会话控制窗口，保留当前观察Map移除时点；_afinish_run在原保存结束时点release_run。
- configure_error_classifiers按对象的受控运行状态判断，不拿_state。不新增通用“执行某回调访问state”入口。

本片仍保留Service中的异步编排，因为105-02才整体转移它；不是新增第二套执行方法。记录中必须明确“State已移交，执行拥有者尚未移交”。

### 3.3 修改application/session_controls.py

- _create_session和install_restored_session构造AgentSession包住SessionState；使用现有dataclass默认值，恢复只安装经校验的持久字段和pending副本。
- describe/snapshot/config/队列等原入口按sid定位后调用对象，删除已搬走的实现。内部单会话实现可作为AgentSession的文件拆分，但不得继续依赖Service._sessions。
- deletion_guard在现有persistence→service锁顺序下检查目标对象空闲，保持删除/恢复预留原子边界；不对外暴露会话锁。
- remove_session按原合同移除对象与当前全局子实例；对子实例Map的归属不在本片提前改变。
- _request_scope_stop继续在现有协调边界检查/标记旧子实例、使审批失效，调用对象prepare_stop处理根State；future唤醒和外部flush在锁外，保留返回的待发内容。

### 3.4 修改application/session_approval.py及session_subagents.py

根待批读取/校验移入对象；子实例逻辑仍在Service当前mixin中。需要本会话run/stop/模型/工作区等事实时，使用已有配置/运行快照，不能读取._state；接受子审批的session_allow合并调用remember_approved_rules，整次校验与状态提交仍在原协调边界内。

不改变子实例身份算法、许可绑定、审批future、单驱动、批次恢复或停止语义。根pending深副本用于计算，写回必须走本run的replace_pending_approval，不在读出的快照里就地修改并期待自动生效。

### 3.5 其余接线

- session_state.py：保留字段/default_factory与数据职责，更新owner说明，不加映射兼容协议。
- session_persistence.py：_install_session仍调用服务安装入口；若构造/受控快照签名内部调整，迁移实际调用，不穿透对象。_active/_recovering单值门禁继续保留。
- session_context.py：使用原受控context快照/复核，必要时同步内部调用；不把预览时拿到的对象引用当执行授权。
- AsyncApplication、SessionPort、存储schema、图调用消息与事件格式保持；测试/探针若读写旧内部State，改用真实业务操作/受控快照，保留原语义断言，不保留测试专用兼容。

## 4. 三条改前改后调用链

### 4.1 新建，然后切换模型

改前：AsyncApplication.acreate_session → Service.create_session/_create_session → 新State入Map → storage.create；aselect_model → Service.select_model → catalog验证 → storage.select_model → Service改State。

改后：创建入口 → Service生成sid/thread → AgentSession(SessionState)入Map → 取限定创建事实写storage.create；aselect_model → Service按sid取A → A.select_model → catalog验证 → storage.select_model → A提交model_id并重置thinking。

storage.create失败仍移除刚创建对象并raise原异常；没有半成功的可选会话。配置SQL失败时A内存保持旧值，B不变；不发送成功确认。不在短状态锁内等待SQL。

### 4.2 普通运行与收尾

改前：Service._admit直接改State → _arun使用该State → _cleanup_run改队列/资源 → persistence.afinish → 解除State.busy。

改后：Service._admit → A.admit_run(mode=message，run_id=run-a，observation=本次对象)登记busy，再由snapshot_for_run取得thread-a/root/pending=None → Service._arun照旧调用共享图 → A.replace_pending_approval或mark_manual_review → A.close_control_window → persistence.afinish读取A的保存快照 → A.release_run。

有持久化时，close_control_window之后仍busy=True，直到必要保存结束；无持久化路径沿原收尾完成时点释放。不能按_active_runs已移除而提前把A当空闲。B有自己的State/队列；本片不因此取消Web/库全局门禁或宣称多会话并发已开放。

### 4.3 服务重开与根审批恢复

改前：persistence.arestore/_install_session校验保存配置/审批 → Service.install_restored_session新State → aresume_tool_approval直接读写State。

改后：同一恢复IO → Service.install_restored_session → AgentSession(SessionState持久字段＋pending副本) → 公开应用恢复入口 → Service._admit → A.admit_run(mode=approval_resume)在原子边界校验decisions → 现有prepare_resume生成一次性许可/临时规则 → A.remember_approved_rules → 原共享图恢复 → A.replace_pending_approval → 原保存收尾。

恢复后busy=False、run_id=None、控制队列空、冻结资源空；不把旧任务或future装回内存。一次性许可与本会话Allow仍不同：前者绑定本调用，本会话规则只在当前服务内该会话有效，不写配置。092的崩溃恢复操作和101档案补录在本片保持原算法。

## 5. 锁与失败边界

登记锁→对象短状态锁；不得反向取得。当前由服务关联State/观察/子实例的一次原子操作，迁移为在同一外层登记锁内执行必要对象操作与映射变更；不能读一份快照、放锁后凭旧事实接受审批。配置选择使用对象自身操作锁串行化DB提交，状态锁只保护前后内存读取/提交。对象内已持有状态锁的操作直接构造必要返回值，不能再调用会取得同一锁的公开快照方法；不换RLock掩盖嵌套。

release_run、replace_pending_approval等使用本次run身份，防止旧请求改到新运行；对外已有异常/拒绝类别保持，不把内部实现错误当普通工具错误吞掉。状态快照只复制需要的可变字段，不深复制task/future/模型，也不泄漏session_allow或pending引用。

关键风险是把旧外层锁拆开后产生检查/更新空窗，而不是新增一个dataclass。测试用Event/Barrier控制交错，检查无效审批、重复准入、配置提交失败和保存中busy；不用极短sleep猜时序。

## 6. 验收合同与命令

先新增tests/test_agent_session.py，确认对象行为测试按预期红灯，再迁移实现。原测试优先复用，完整集合按真实接线增加，不能仅验证类存在。

1. 新建/恢复均使用AgentSession；两个会话State/队列/Allow独立，返回嵌套快照修改不回流。
2. 模型/推理/工具/权限设置校验与成功确认保持；storage各项失败不提交内存，不影响另一会话。
3. run/pending/compact准入及无效decisions顺序保持；同会话重复请求只接受一次，无额外开始事件/SQL/工具副作用。
4. 普通run、根审批恢复、Stop/Steer/Follow-up及子审批当前链均通过；状态移交不减少原run/batch/path/许可断言。
5. 清理后、保存尚未完成时busy仍真；保存失败阻断与原异常/次异常优先级保持；finish不误清另一会话。
6. 当前Service及其mixin不再直接取得/写_state；SessionState仅由对象行为更新，存储显式映射不变。无__getattr__/双写/同步执行兼容。

定向命令：.venv/bin/python -m pytest -q tests/test_agent_session.py tests/test_session_state.py tests/test_session_port.py tests/test_async_persistent.py tests/test_subagent_approval_resume.py tests/test_active_stop_approval.py --tb=short。

收尾执行完整后端：PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q；前端在frontend内npm test -- --run和npm run build；Python编译/依赖、500行、git diff --check和文档链接检查。此为实施前合同：当时尚未创建新测试，不能提前报告通过；现已按第7节落地。

实际验收记录应分别写当前对象/state实际接线、生产行为结果与尚未迁移的执行/子实例归属。完成后只进入105-02设计，不直接开放104、不改真实配置/用户库、不发送供应商请求。

实施前红灯：.venv/bin/python -m pytest -q tests/test_agent_session.py --tb=short，10 failed in1.14s；均为计划新增模块尚未存在，日志/tmp/naiyue-10501-red.log。控制队列状态操作按500行约束放入现有session_controls.py的AgentSessionControls内部部分，服务保留协调/记录/flush，避免持有登记锁投递外部观察。

## 7. 实际实现与验收（2026-10-18）

本片代码范围为6个生产文件、12个测试文件（其中新增AgentSession和其专测各一份），无新依赖、配置、数据库schema或HTTP/SSE字段。session_persistence.py/session_context.py继续使用既有受控入口，未作无用途修改；脚本无旧直接State消费者需要迁移。当前编排/观察Map与全局子实例仍由Service拥有，不标成105全量收尾。

AgentSession位于application，持有唯一State、短状态锁与本会话配置提交锁。模型目录/工具表/权限设置/持久化引用借用现有对象。根图、角色图和客户端仍共享。AgentSessionControls是同一个实例的内部文件拆分；Service只按sid协调，不取得._state；移除其旧配置选择锁和无用途_busy_error别名，单一使用的快照转发已内联。类方法定义只有一份，新增测试核对两个实例type相同、select_model实现相同，同时其模型选择、Allow和快照独立。

admit_run只作原子转换，不生成无人使用的返回数据；Service._admit保留对象/run_id/observation三元组，普通/恢复执行通过受控快照取事实。aresume仅取一次pending快照；资源准备使用不含大pending正文的context快照。队列选择/缓冲接受在锁内，日志与flush在服务登记锁外，保留当前观察拥有者与事件序号。

实际正常链：AsyncApplication.arun → Service._admit → AgentSession.admit_run → Service._arun → AgentSession.freeze_run_resources → 原共享图/thread → AgentSession.replace_pending_approval → close_control_window → SessionPersistence.afinish读取受控快照 → release_run → 原终局投递。持久化时关闭控制窗口后仍busy，保存结束才释放。

配置失败链：aselect_model → Service按sid路由 → AgentSession.select_model验证/写storage → IO抛原错误 → 内存保持旧选择，另一会话不变；状态短锁没有覆盖配置IO。当前错误类别、模型切换重置推理、根/子权限取值和审批配对保持。

旧测试迁移保留原断言：State dataclass默认值仍在数据单元核对，应用状态通过拥有者快照/操作核验；直接写busy、Allow、人工核对和控制队列的注入改成真实状态转换。清理异常测试原先在控制窗口已关闭后强塞队列，现在在真实_cleanup_run完成后抛同一异常，保留原异常身份、prepare/cleanup阶段、终局和释放busy断言。未新增测试专用生产setter、没有修改conftest或测试helper。

红灯：10 failed in1.14s，新模块缺失。首次新专测转绿10 passed in0.86s；原链专项31 passed in1.42s。接口迁移首次组合142 passed/2 failed（遗留快照属性读取及关闭队列注入），完成同等断言迁移后144 passed in4.65s。

最终命令及实际结果：

- PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q → 2425 passed in99.33s；日志/tmp/naiyue-10501-backend-final.log。
- npm --prefix frontend test -- --run → 26文件、186 passed；日志/tmp/naiyue-10501-frontend.log。
- npm --prefix frontend run build → TypeScript通过、Vite210模块成功；日志/tmp/naiyue-10501-build.log。
- .venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples → 0；.venv/bin/python -m pip check → No broken requirements found。
- 393份Python AST全部通过且每文件≤500行；Service及其协调mixin无._state穿透，Runtime无application import；git diff --check通过；10份文档643个本地链接目标及新验收锚点通过。
- check专项按本轮开始前快照隔离既有脏树，架构/安全各一位只读审查，均无确证新发现。根复核覆盖旧run写入、重复准入、可变快照、慢配置IO与根/子审批/保存的组合；不以此替代实网/Windows证据。

未发送真实供应商请求、未改真实配置/用户库，未提交/push；原有未提交工作保留。本轮为助手实施和验证，不新增用户已掌握声明。

## 8. 唯一下一步：先讲105-02

本节保留105-01收尾时点；[105-02现已实施验收](105-02-session-execution.md#7-实际实现与验收2026-10-18)，当前入口以该Spec为准。当时目标是把主运行编排及当前RunObservation一起归AgentSession：将Service._arun、aresume_tool_approval、_prepare_resources、_on_failure、_areview_partial_state、_cleanup_run、_afinish_run迁到application/session_execution.py的内部执行部分，移除Service._active_runs。普通run、compact、根审批恢复各自执行段仍可直接阅读；共享图/thread、错误优先级、子任务settle和必要保存顺序保持。

Service保留登记/准入协调和共享分类策略准备，SessionPort仍由Service按sid路由。子实例及其审批的全局Map继续等105-03整体转移，不能在105-02复制一份。新片需先讲清当前观察取得/关闭、控制投递锁边界、全局准入与对象准入原子边界，再获确认实施；本轮没有新增session_execution.py或实施104。
