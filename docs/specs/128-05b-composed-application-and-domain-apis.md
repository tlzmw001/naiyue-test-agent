# Spec128-05B：组合应用与领域子API

> 版本边界（2026-10-24）：本文保留当时迁移的说明。当前终端已由 [R1-06](R1-06-cli-service-client.md) 改为 HTTP/SSE 客户端，旧 cli_async/app.call 拥有者桥已删除；默认 Web 的共享接入在 R1-07。

版本更正（2026-10-23）：原十片审查发现本片遗漏原承诺的app.memory；下文“不制造空memory API”不能代替范围取消。用户随后批准补齐，当前合同和主链转到[原第7片补齐Spec](128-07-memory-api.md)。下文九个API、测试数和范围处理保留为05B历史时点；原MemoryQuery/SessionPersistence记忆编排已由新MemoryAPI替代，不作为当前入口指引。

状态：用户在05A收尾和05B说明后批准“继续”，已完成本机验收（2026-10-23）。仅本片；06会话mixin和实际worktree管理独立，不提交/push/发布。基线是当前未提交的01—05A，保存于build/spec128-05b/baseline，不覆盖主目录并行成果。

版本边界：本文记录05B时点；领域API和适配器合同继续适用，06进一步把内部AgentSession行为迁到显式协作对象，当前会话调用入口见[06](128-06-session-collaborators.md)。

## 1. 当前问题与外部合同

05A已使磁盘/RAM共用仓储和必要保存，但AsyncApplication53方法、AsyncPersistentApplication23方法仍继承组合，CLI再镜像40方法。WebService34方法多数包含归属、错误映射、幂等或协议投影，不能按纯转发删除。

稳定外部合同：CLI命令/参数/文本交互，HTTP路径/请求响应/SSE/认证/幂等，SQLite schema18与项目/执行目录权限、两种profile、必要保存、取消和关闭顺序。两个实际使用的工厂调用形式保留。内部Python应用方法按已批准目标迁移，不为旧测试保留平铺兼容层。无配置、依赖、版本、真实库修改。

正常链：Web POST → 原归属/回执/准入 → app.runs.start/stream → SessionService/AgentSession → 原图/保存 → RunDelivery/SSE；CLI输入 → bridge.call(lambda app: app.runs.start(...)) → 原拥有者loop → 同一RunAPI。只有适配器决定协议和呈现。

失败链：重复回执仍先于busy判定；权限/目录无效继续原错误码，保存失败继续failed/blocked；关闭期间新入口拒绝，已进入operation的嵌套调用与必要IO继续收束；流断开/溢出不取消生产者。所有API共用同一生命周期所有权，不能各建run表或锁。

## 2. 对象和依赖

唯一Application组合领域API和ApplicationLifetime，不继承内存/持久类。Lifetime只持有loop、operation futures、生产者tasks及关闭需要的catalog/session resources/资源栈，提供check_open/operation/任务登记/aclose；不提供领域方法或动态service locator。

子API拿明确依赖（所需SessionService、graph、SessionPersistence或具体仓储），不能持有整个Application。既有业务算法直接迁到所属API，不留下指向旧大类的转发。跨领域需要同一个必要服务时显式注入，不能为了统一调用额外包resolver/registry。

| API | 当前功能 |
| --- | --- |
| sessions | create/create_workspace/list/list_workspace/is_loaded/status/prepare/restore/recover/delete/update_presentation/reorder；模型/推理/工具/权限选择及Skills/control_options |
| runs | start/compact/regenerate/regeneration_action；steer/follow_up/stop；根/子审批准备/提交/恢复/停止及stream/stream_approval/stream_stop_approval |
| history | messages/session/context/page/message/reasoning/file_change/subagents/subagent/chat_subagents/approval_seed |
| tasks | list/output/stop/preview_outputs/clear_outputs |
| observability | subscribe/configure_errors/usage/runs/events |
| models | default_id/ids/thinking_options |
| tools/subagents | 当前定义list/register |
| mcp | status/refresh |

记忆继续通过RuntimeContext.scope.capabilities.memory和现有memory jobs/tasks接入；本片没有已存在的独立管理API可迁移，不制造空memory子API或新增CRUD。个人memory跨项目与临时无项目memory规则不变。

重新生成放runs，因为它取得admission并开始真实执行；regenerate函数由接收整个application改为显式接收它实际需要的服务/graph/仓储，保持准备/激活/失败保存/幂等顺序。

## 3. 适配器与消费者迁移

CLI AsyncCliApplication保留线程/Runner、_submit、open、close，提供call(Callable[[Application], T|Awaitable[T]]) -> T，在loop上调用并等待结果。没有动态__getattr__代理、按字符串找能力或镜像一套子API类。所有CLI入口/显示/审批/后台/观测/MCP与测试替身迁到该入口；不删除输入线程和异步执行桥。

Web保留WebService/RunDelivery中的实际协议职责，迁移其调用到领域API。与新功能无关的Web方法不搬家、不新增一组Web子门面。必要只读投影和有界输出仍由Web负责。

实际脚本、测试、源码外安装worker与故障注入全部迁移；旧内部类/方法/导入删除。私有测试观察点迁到领域服务/Lifetime的真实所有者，保留事件顺序、异常对象身份、配对、busy、保存确认、关闭与权限断言，不用旧平铺API兼容测试。直接根控制/框架测试用其实际边界，不因只剩测试就恢复旧应用类。

## 4. 文件与验证

生产：application/core.py、lifetime.py、领域包api/run_api、小型目录API模块；assembly及两个工厂；conversation/session_regeneration；cli和web调用方。脚本/测试同步新入口，前端协议无需变化。

先补红灯：唯一Application/无继承及无旧平铺方法、两种profile同样的子API、本实例历史/重新生成；CLI call在owner loop且不镜像旧方法。随后覆盖跨API统一owner/关闭嵌套operation/活动流。继承原4737测试所有必要行为，按新入口迁移。

完成后：定向合同与失败边界；全量后端、前端测试/TS-Vite、标准Mac ARM64 wheel/Twine/源码外安装；AST/500行、compile/pip/diff、包源码字节一致和冻结无漂移。架构与安全复核聚焦相对05A差异，保留红灯及中途失败日志，证据不代表真实模型质量或其他原机证明。

## 5. 当前接线、删除项与行为测试迁移

`assembly.build_application`在原资源栈内完成Store/检查点、memory、catalog/MCP、SessionService、writer、SessionPersistence和graph装配，最后构造`core.Application`。两个工厂仅选择RAM/磁盘profile，保留参数与调用形式。Application只构造九个领域API和共享Lifetime，并提供aclose；各API显式获得所需graph、服务或仓储，不反向持有Application。

以保存一条新消息为例：CLI输入线程调用`bridge.call(lambda app: app.runs.start(sid, messages))`；call在owner loop执行回调并等待其返回的协程；RunAPI.start进入共享operation，再由SessionService找到AgentSession并取得本轮准入，沿既有LangChain/SessionPersistence/UOW保存。Web的RunDelivery在原认证/归属/回执检查之后调用同一RunAPI，订阅仍来自`app.observability.subscribe`。流消费者关闭只结束交付，生产任务仍登记在Lifetime并继续保存。

以关闭时已有查询为例：sessions.status进入operation并等待必要查询；aclose关闭新入口，调用stop_loaded_sessions并等待当前operation；原查询随后调用history.messages，作为同一个已准入任务的嵌套operation允许完成。此时另一个任务发起history.messages会明确抛RuntimeError；原任务结束后全部operation清空，资源栈再关闭。`test_domains_share_owner_and_close_drains_nested_cross_domain_operation`用三个Event同步，并检查新入口拒绝、已有查询结果、共享owner及最终关闭，没有依赖短sleep。

重新生成由RunAPI接收request_id、expected_reply_id和expected_conversation_revision。内部regenerate函数显式接收SessionService/graph/SessionPersistence和会话/档案/回复仓储，保持幂等、准备、激活和必要失败保存顺序；无整应用参数或影子存储。

删除AsyncApplication/AsyncPersistentApplication两个继承门面及其旧平铺方法、CLI领域镜像方法与root默认模型镜像。两个async工厂文件仍有实际启动消费者，继续保留；AsyncCliApplication保留线程/Runner/_submit/open/close，新增唯一call入口。SessionService和AgentSession的现有运行方法仍是底层真实消费者，留给06处理；本片不凭arun名称删除它们。

CLI回调不能在owner线程内再次同步等待call，否则会死锁；_submit在创建协程之前拒绝重入。真实chat中的默认模型读取与create_workspace放在同一个回调中。恢复崩溃测试里的子进程源码、安装worker和真实模型探针也迁到领域API；未发起供应商请求。

测试迁移保留原事件顺序、异常对象、权限/审批配对、数据库事实、保存与关闭断言。新增`tests/cli_portal_helpers.py`仅用于终端呈现测试：显式接收各领域替身，以call执行回调，没有默认业务结果或自动成功。真实桥的owner-loop、重入、run/审批/停止仍由正式对象测试。三项直接根控制测试改为显式组合RunAPI/Lifetime和原SessionService，继续在原图边界断言；不恢复已废弃的大门面。动态设置故障注入改到sessions子API。一次自动迁移误将Windows transport的_closing当成Application字段，已还原该测试字段，未改Windows产品处理器。

测试阶段区分：最初三个合同红灯为缺Application/旧CLI镜像存在，再转绿；owner线程重入单独先红后绿。共享owner和跨API关闭为实现后追加的确定性回归，不冒称其有独立红灯阶段。首轮全量因旧设置替身未触发同步事件而中断；第二轮23失败/4718通过暴露剩余动态调用/替身/子进程迁移点，修正后106项定向通过。失败日志保留在build/spec128-05b。

## 6. 验收结果与限制（2026-10-23）

| 范围 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| 新合同/崩溃恢复 | `.venv/bin/python -m pytest tests/test_composed_application_contract.py tests/test_restart_approval.py -q --tb=short` | 22 passed in3.61s |
| 最后消费者回归 | `build/spec128-05b/consumer-final.log` | 106 passed in6.83s |
| 后端全量 | `PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **4742 passed in343.03s**，exit0 |
| 前端 | `cd frontend` 后 `npm test -- --run` 与 `npm run build` | 50文件/423 passed，TypeScript/Vite成功；保留既有chunk体积提示 |
| 标准构建 | `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec128-05b/verified-dist` | wheel成功，370文件；240份Python/4前端资源/README元数据与源码一致 |
| 元数据/源码外安装 | `twine check` 与 `scripts.verify_wheel_install --wheel-dir build/spec128-05b/verified-dist --output build/spec128-05b/install-verified.json` | 全通过；CLI/Web、文件读写、审批恢复、重开、MCP、后台、子续跑、memory等固定场景，0供应商请求 |
| 结构/资源 | AST/生产500行、compileall、pip check、git diff --check、正逆冷导入、冻结SHA | 240生产模块通过，6模块两种顺序冷导入通过，840文件无漂移；113份运行/工具/存储/权限/会话底层与05A基线相同 |

新增五项合同由磁盘/RAM两例、真实CLI派发、CLI重入拒绝及跨API关闭组成；其余行为迁移保留既有检查。独立架构复核16项、安全复核19项及16项通过，无遗留发现；不计为新的全量结果。证据入口`build/spec128-05b/verification.json`、`review-findings.json`、`install-verified.json`和各日志。中途失败/中断日志保留，不替换最终通过证据。

本机验收仅为Mac ARM64与确定性模型/受控本地服务；未发起真实供应商推理，未运行远程CI或其他平台原机验收，不代表实际模型业务质量。原主目录并行成果、真实配置/用户库/依赖/版本未改，未提交/push/发布。06 AgentSession/SessionService mixin与真实worktree创建/附着/删除仍未实现。唯一下一步：向用户说明并确认06的协作对象、共享状态/锁、正常/失败链与验收，再实施。
