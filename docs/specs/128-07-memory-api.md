# Spec128 原第7片补齐：app.memory

状态：用户已批准，完成本机验收（2026-10-23），未提交/push/发布。编号对应原十片，不改变已完成01—06的实施记录。

## 1. 当前问题与范围

Application已组合各领域API，但缺少原计划承诺的memory。现有memory工具经过逐调用许可后，根消息、根审批恢复和子Agent把能力绑定到SessionPersistence.aquery_memory。该方法同时负责调用者检查、来源保存、记忆查询/修改及整理任务登记，领域职责仍藏在持久化服务里。

本片把既有行为移到真实的MemoryAPI，不新增闲置会话管理、HTTP/CLI/UI入口、请求动作、数据库或配置。复用现有MemoryInput，保留个人记忆跨项目、来源脱敏、项目身份校验、临时会话无项目记忆、任务释放和错误合同。

## 2. 接口和实际调用链

Application.memory为与其他API共享ApplicationLifetime的MemoryAPI。query(*, scope: RunScope, request: dict, tool_call_id: str | None = None) -> dict是当前运行能力入口，输入仍只有既有八种MemoryInput动作；没有任意project_id参数。原MemoryQuery协议同步改为显式传RunScope。该入口不接受闲置或已结束运行的scope。

装配链：assembly取得现有MemoryAccess/MemoryJobs及graph → Application构造MemoryAPI → SessionService将同一对象传给新建和恢复的AgentSession → RunLifecycle/SubagentRegistry把该对象的query绑定到RunCapabilities.memory。每个工具调用仍在复制的RuntimeContext中验证许可；不将许可、模型调用准备结果放入RunScope。API不持有整个Application、不自行打开或关闭资源。

正常写入：memory工具校验MemoryInput及许可 → scope.capabilities.memory(scope=scope, request=...) → MemoryAPI.query进入owner.operation → AgentSession在短状态锁内核对当前session/run/role、publisher身份、Project/ExecutionRoot和活动memory工具 → 完整执行目录检查 → MemoryAPI取得SessionPersistence.history_lock提供的原每会话共享锁 → await SessionPersistence.save_memory_sources从正式graph读取并保存来源 → 同一锁内执行既有MemoryAccess.query → 返回原结果 → 工具反馈。记忆仓储内部的来源版本/事务/项目校验继续生效。锁只由原字典生产，不新增锁容器；来源保存为直接async方法，不使用生成器传输。

读取走同一身份和目录检查后执行既有查询。refresh_overview仍只登记MemoryJobs.request，origin_run_id为当前根run，返回accepted/task_id。根必要保存成功后由原持久化完成路径释放任务；任务list/output/stop仍属于app.tasks，记忆上下文准备仍在原运行资源准备路径。

失败例：旧run的scope在新run中再次调用 → 当前run/publisher不匹配 → memory_scope_mismatch，来源不保存、记忆不写。其他会话/子Agent身份不匹配、角色无memory同样拒绝。执行目录失效保留execution_root_unavailable及原因。RAM或禁用记忆时API存在但无MemoryAccess/Jobs，显式memory_unavailable，不创建身份或个人记忆库。来源必要保存失败保留memory_source_save_failed，禁止继续写入；MemoryReadError继续转为可配对的工具错误。取消后已开始IO必须排空再释放锁；aclose拒绝新入口并等待已进入的操作结束后关闭现有资源。

## 3. 影响文件与清理

新增application/memory/api.py；修改core/assembly、sessions的AgentSession/SessionService/RunLifecycle/SessionPersistence、SubagentRegistry、contracts/run_ports、tools/memory。SessionPersistence仅保留save_memory_sources实际来源保存、history_lock共享锁、提示准备/必要最终保存/任务释放；删除aquery_memory、bind_memory_agent及旧_memory/_memory_agent字段。原_history_lock生产者及全部消费者统一迁到history_lock，无重复兼容入口。

测试和release_memory_smoke的私有资源观察点迁到app.memory._access，保留原异常、权限、顺序和存储断言；不修改通用测试基础设施。当前文档同步新主链，历史05B说明标记被本片替代。

## 4. 验收

先补失败测试确认API缺失；随后核对两种profile同形API及共享owner、根/子Agent/根与子审批恢复的正式工具链命中同一API、陈旧/外来/停用scope和无效请求拒绝、来源保存与写入共用history锁、保存失败无写入、关闭/取消排空。沿用真实SQLite写入/重开、来源事务、个人记忆和整理任务释放的既有回归，不把模块存在当成接线完成。

定向测试通过后运行全量后端、前端/TS/Vite、标准Mac ARM64 wheel、Twine和源码外干净安装。核对包源码一致、冷导入、compileall、500行和diff检查。只报告本机确定性测试和安装证据，不代表真实模型质量或其他平台实机验证。本片完成后只讲原第5片两项持久回归测试缺口，等待用户批准再实施。

## 5. 当前实现与测试边界

生产装配和四处scope能力绑定已迁移。AgentSession.validate_memory_scope在单一runtime锁内读取当前run；根用完整RunIdentity和当前publisher，子实例还核对本次子run及parent/root身份、执行阶段、settled状态、活动工具和publisher_view。锁内无IO；MemoryAPI拿到已确定的根run_id后做目录验证、请求校验和实际操作。原SessionPersistence.aquery_memory/bind_memory_agent/_memory/_memory_agent已删除；prepare_memory、最终保存后的MemoryJobs释放与统一任务查询有实际消费者，继续保留。全量首轮发现新增asynccontextmanager违反原直接async结构检查，最终改为API直接持有同一history_lock并await保存/写入；原结构测试原样保留，未豁免yield。

新增test_memory_api/test_memory_api_persistence共21项：四种正式根/子/审批链、陈旧身份和schema拒绝、两种不可用配置、真实来源/写入锁与重开恢复、必要保存失败不写、取消/关闭排空。原子API合同的两种profile和共享owner断言新增memory；原记忆、临时资源及安装smoke的私有观察点迁到真实资源所有者app.memory._access，没有改conftest或通用helper，也没有降低原断言。

红灯先有15项因API不存在失败；首次测试采集曾误用不存在的SubAgentSpec导入，修测试导入后才记录正式红灯。实现后专项暴露新测试构造问题：ask不是权限配置字段，应沿现有ToolPermission声明；无效identity的scope先触发独立background notice一致性校验，应移除测试用notice再测API守卫；运行前提示准备会合法查询个人库，故存储禁止探针只包围被拒绝的API调用，直接断言零查询。未改变产品行为迎合这些构造错误。其余6项是实现后增加的回归，不冒称各自有独立红灯。

定向结果：新合同21 passed in2.26s；既有记忆/context/临时/会话协作者/子审批340 passed in10.82s（后者执行时新合同为19项，随后补的两项另验）。全量和构建结果待下面最终验收记录。

首轮全量为1 failed/4798 passed in343.59s，仅原test_native_session_lifecycle拒绝SessionPersistence中的yield；最小修源后，新21合同加原5生命周期检查合计26 passed in2.02s。原结构测试与本轮基线字节一致，未放宽断言。首轮wheel/安装也已通过，但它们对应中途版本，最终证据必须引用重新构建的verified-final-dist及新的源码冻结，不能混用。

## 6. 最终验收（2026-10-23）

| 范围 | 命令与实际结果 |
| --- | --- |
| 新API/持久化/原生命周期合同 | `.venv/bin/python -m pytest tests/test_memory_api.py tests/test_memory_api_persistence.py tests/test_native_session_lifecycle.py -q --tb=short` → **26 passed in2.02s** |
| 全后端 | `PATH="/Users/zmw/naiyue-test-agent/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` → **4799 passed in343.33s**，exit0 |
| 前端 | `npm test -- --run` → **50文件423 passed in4.87s**；`npm run build` → TS/Vite exit0，保留既有chunk大小提示；前端源码未改 |
| 最终wheel/Twine | `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec128-07-memory-api/verified-final-dist`及Twine通过；372文件，版本仍是基线0.4.0，仅本地候选 |
| 源码外全新安装 | `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec128-07-memory-api/verified-final-dist --output build/spec128-07-memory-api/install-final.json` exit0；正式CLI/Web/审批/SQLite/存储维护/子实例/memory固定场景通过，0供应商 |
| 字节/结构 | wheel内242 Python、4前端资源及README与当前源一致，850冻结源无漂移；AST/500行、compileall、pip check、diff及8个独立进程冷导入通过；原生命周期结构测试字节未改 |
| 独立增量复核 | 架构最终8项、安全最终4项通过，当前19/16生产文件哈希与复核一致，无遗留发现；调整前21/45项仅保留为历史专项证据，不相加 |

最终wheel SHA256：`655b1c87ca4efb21de4a67463eff8adf960643384d7190cf7dccded8fb6b1ff5`。完整证据位于`build/spec128-07-memory-api/verification.json`、artifact-verified.json、install-final.json及两份review JSON；红灯、首轮全量失败、中途wheel与安装报告另名保留。

L1：两种profile都有MemoryAPI且共用owner。L2：真实SQLite来源保存、记忆修改/读取/重开、MemoryJobs登记和任务管理可验证。L3：根/子/两类审批能力都绑定同一API，恢复安装的会话也取得同一对象；旧编排入口删除。程序接口只服务已有运行能力，无闲置会话管理或新增协议。个人记忆跨项目、临时无项目、任务必要保存后的释放与ExecutionRoot错误合同保持。

这是本机确定性测试、打包和安装证据，不代表用户掌握、真实模型质量或其他平台实机验证。无配置/依赖/版本/用户库修改，未提交/push/发布。原第5片两类持久回归缺口仍待用户确认后补齐；本轮只讲解下一片，不提前实施或进入worktree管理。
