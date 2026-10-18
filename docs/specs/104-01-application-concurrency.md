# 104-01：根运行准入与按会话持久化协调

日期：2026-10-18。状态：**已实施并验收**。承接105五片收尾和[104总方案](104-cross-session-concurrency.md)。本文件保留实施合同，实际结果见第8节。

## 1. 本片交付和不变边界

同一应用内不同会话可在原共享图、模型目录、工具注册表和SQLite上同时运行；同一会话仍只允许一个根运行。默认最多10个占用中的根运行，满额立即拒绝、不排队。普通run、compact、根审批恢复均计入；父run等待子审批时仍占一个名额，子run不另算根名额。

busy持续到子任务结算和必要保存完成，直接复用Actor.is_busy/_run，不统计_owned_operation（同一调用可有嵌套操作）、controls_open、Web连接或SDK请求。它与单批工具max_concurrency、供应商限流分别是不同约束。

本片保持公开业务方法、SessionPort、运行/审批协议、schema8、工作区绑定和数据库进程锁。Web的单active门禁留104-02，浏览器完整多会话并发也到104-02才交付。不新增Semaphore、排队器、ExecutionManager、数据库工作队列、重试或模型限流器。

## 2. 文件、类与方法

| 文件 | 拟修改内容 |
|---|---|
| tools/settings.py | ToolSettings增加严格正整数max_active_runs，默认10。load_tool_settings允许该顶层key；仅该预算非法时警告并回退10，不丢失其他合法设置，也不放松权限配置失败时终止启动的规则 |
| docs/examples/settings.example.toml | 顶层max_active_runs=10及注释，说明根运行/保存占用、启动时读取、不排队、不限制所有模型请求；不改真实配置 |
| application/errors.py、application/__init__.py | 新增并导出RunCapacityExceededError，与同会话SessionBusyError区分 |
| application/session_service.py | __init__接启动预算；_admit在原登记锁内统计各Actor.is_busy，再将capacity_available交给目标admit_run，在同一临界区完成检查和安装 |
| application/agent_session.py | admit_run增加内部已确定事实capacity_available；保留现有manual_review/pending/busy/审批决定校验优先级，额度不足在安装ActiveRun和任何恢复观察动作前抛新错误 |
| application/session_persistence.py | _active由单tuple改dict[sid,rid]，_recovering由单sid改set[sid]；start/afinish/arecover/arestore/_delete/_install_session/_read_history及状态查询按本sid核对和释放；复用原锁、history锁和事务 |
| application/session_storage.py | 组合验收发现普通BEGIN先读后写可与官方saver交错产生SQLITE_BUSY_SNAPSHOT；运行期save_chat_updates/reorder/delete/finish_run写事务改BEGIN IMMEDIATE，在读取前申请写入权，不新增业务队列/重试或改变schema |
| application/async_application.py、application/async_persistent.py | 两工厂将已校验的settings.max_active_runs注入同一Service；关闭继续复用105结果，不另建并发任务管理器 |
| 新增tests/test_cross_session_concurrency.py、test_cross_session_persistence.py、test_cross_session_approvals.py；迁移test_session_recovery.py | 真实图、临时SQLite、按sid分配事件屏障的离线模型；旧全局互斥断言迁为同sid断言，保留异常、保存、事件和审批断言 |

共9个生产文件、配置样例及测试/文档，本合同已获用户确认。没有新增生产.py。HTTP容量错误映射和Web任务索引归104-02，不在本片顺便接UI。

## 3. 准入顺序及数据

示意max_active_runs=2。A已运行，B提出新任务：

```text
app.arun(B, messages) → Service.arun → _admit
  登记锁内：统计A.busy=True、B.busy=False → 1/2 → capacity_available=True
  → B.admit_run：校验本会话 → 检查额度 → 安装ActiveRun(run-b)
  → 退出登记锁 → B._arun
  → Persistence.start(B,run-b)
      _active={'A':'run-a','B':'run-b'}
  → B的原共享图，context携带B的session/thread/run
  → B.finish保存 → 仅移除_active[B] → B.release_run
```

第三个C到来时：

```text
登记锁内统计2/2 → capacity_available=False
  → C先校验本会话是否合法，再抛RunCapacityExceededError
  → C._run仍为None，不发run_started，不mark_running，不调用模型/工具或写checkpoint
```

错误后保留C草稿由上层负责、保留待审批和session_allow事实；不消费一次性授权，不进入后台队列。A结束后由调用方显式再次提交。统计和安装不能分两次获取锁；释放仍由Actor在必要收尾后完成，没有第二套计数器需要同步。

同会话重入保留原优先级：如resume期间旧pending尚存在，普通message可能先报SessionPendingApprovalError；再次resume报SessionBusyError。不能为了额度功能改变这些已验证的原因。

根前置审批保存结束后释放名额，批准后的恢复重新申请；live_child提交只唤醒当前父run的future，不申请名额。结束已暂停的根审批通过现有stop=True路径豁免总量检查，同会话合法性/busy检查仍保留；该短暂收尾可占用busy，但不执行新模型或实际工具。普通停止、Steer/Follow-up同样不等待执行名额。

## 4. 持久化和失败链

改前start把整库的_active当单值，故A活动时B被挡住。改后start只检查sid是否活动、恢复或受阻；短锁内先预留该条映射，再在锁外mark_running；失败按(sid,rid)身份撤销且原异常上抛。afinish仍以(sid,rid)核对所有者，保存/失败标记/释放只作用于本条。_blocked已按sid存储，继续使用。

恢复/冷装载/删除B在原短锁中预留B，不因A活动而拒绝；同B的竞争仍被活动、恢复和history锁阻止。_read_history按目标sid描述可恢复性，三次END→tools→END及所有消息配对/审批本地再校验不改。

例：A根模型失败→A停止/结算自己的子任务→A.afinish保存→释放A占用→原异常回调用方；B继续。A的元数据保存失败则保留A未确认/blocked状态，错误终局后释放A；测试只对A注入写故障，证明健康B可保存。整份SQLite不可用属于共享基础设施故障，不能用这个测试宣称B仍能保存。

锁顺序保持Persistence协调→Service登记→Actor状态；Service锁内没有await、文件IO或外部事件投递。原SQLite/官方saver短操作协调继续存在，本片开放的是模型/工具等待阶段的重叠，不承诺SQL写操作同时执行。

## 5. 验证计划

1. 配置缺省10、合法小额及非法类型/零/负数回退；合法Deny不能随预算回退丢失，非法权限仍启动失败；两工厂真实注入。
2. 真实图+SQLite中A/B分别到达事件屏障，证明执行重叠；thread、消息、事件、审批和停止归属分别检查。
3. 默认10个根任务均进入，第11个拒绝且无模型/工具/事件/checkpoint写；同会话重入及compact/审批恢复也核对。保存被挡住时仍满额，保存后下一次提交可进入。
4. 根审批释放/恢复重新准入；子待批父占用；满额时纯停止根审批仍可完成，其他任务继续。
5. A错误、停止、取消或局部保存失败不释放B的映射；A运行时可恢复/删除空闲B，同sid仍拒绝；关闭等待全部已接纳运行及必要保存。

先为未实现行为写失败测试，再最小接线；新测试用事件按sid控制，避免共享Fake.replies顺序决定A/B结果。完成后全量后端、前端、构建及分层/500行/文档检查。无真实供应商请求，Windows/Linux原机另验。

## 6. 参照及与106的关系

本轮重读本地Pi a69bef789bc95abf0acee16f7b4660b70b650bb9：server的executeCommand→requireAttached(sessionId)→runOperation→live.runtime.prompt，liveSessions和operationCount按会话持有；独立SDK的AgentSession.prompt按本会话isStreaming拒绝/进入Steer或Follow-up。这两条是不同接口路径，不能拼成已接通的同一生产适配器。未见这些入口内的全服务根额度；默认10是本项目已确认的决定。

重读AgentX 3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f：ConversationAppService.chat准备ChatContext后调用handler.chat，再由ChatSessionManager按sid登记SSE/中断信息；SSE完成移除不能替代本项目保存后释放根占用。这里参考按sid路由，不移植其交付完成判断。

参考仅为本地源码事实，不声称运行了它们或验证当前发行版。Python/create_agent继续沿用；本片不改框架图。106 E1会话范围订阅仍随104-02，R1离线429隔离随104-03，P1保留测量基线；根运行预算不等于模型请求限流。

## 7. 实施中定位的SQLite事务边界

子审批与其他根任务共同关闭时复现元数据保存database is locked。双连接固定顺序回归再次复现：连接A普通BEGIN读取会话，连接B提交写入，A再UPDATE无法升级旧快照。SQLite官方[隔离说明](https://www.sqlite.org/isolation.html)明确此为SQLITE_BUSY_SNAPSHOT，已确定要写入的事务可用BEGIN IMMEDIATE避免。修复限定四个运行期显式写事务，锁只覆盖原事务，不跨模型、工具、图或await；迁移/启动绑定事务不变。保留SQLite默认等待和错误可观察性，不加项目重试。这是本片并发保存正确性所需修正，先补确定性红灯再修改。

补充锁边界：固定顺序探针证实start持Persistence锁等待SQLite写入时，事件循环中其他会话获取同锁会挡住官方saver提交，直到busy timeout。start改用现有_active预留，不增加状态集合：短锁检查/预留→锁外mark_running→失败短锁按sid/rid撤销。准入Actor已busy，执行链await start成功才进图，正常预留沿原afinish释放。新增事件屏障红灯验证等待IO期间协调锁可用及失败只撤销本sid，保持原异常身份；不改变运行/恢复协议。

## 8. 实际实施与验收（2026-10-18）

L1/L2/L3：ToolSettings.max_active_runs已由两工厂传给同一Service；Service._admit原登记锁内统计Actor.is_busy并交已确定的capacity_available，Actor.admit_run校验后安装ActiveRun。Persistence._active/_recovering的生产消费者全部迁为目标sid，旧单值消费者为0。没有新增生产.py/调度器/重试/依赖或schema；9个生产文件、3份新测试和1份恢复测试迁移，配置仅改样例。

真实主链：app.arun(B)→Service._admit（例如A占用时1/2）→B.admit_run安装run-b→B._arun→线程Persistence.start短锁预留B/run-b、锁外mark_running→共享图B/thread→B._afinish_run结算自己的子实例、关闭控制、Persistence.afinish归档/保存并删除B映射→B.release_run。A始终保留自己的映射/历史/控制。满额C在admit_run中抛RunCapacityExceededError，无run_started、DB running、checkpoint或模型/工具副作用；决定与session_allow不变。

新测试共26项：默认10/自定义2及两工厂、非法预算保留Deny、额度拒绝和同会话优先级、历史/事件归属、活动compact占用、根审批重入/满额纯停止、子审批父占用/无需额外额度、单会话错误/取消/停止/保存失败、保存占用、冷会话恢复/装载/删除及所有根/子关闭。额度配置/旧全局持久化门禁先红后绿；SQLite事务升级与start锁等待有独立确定性红灯；其他组合用例为接线补证。新测试分页接口漏传必填参数及关闭后误用公开查询，已按实际合同修正测试调用，没有放宽业务断言或修改测试基础设施。

| 验证 | 实际结果 |
|---|---|
| 三份新并发测试＋test_session_recovery.py、test_async_persistent.py、test_session_lifecycle_integration.py | 63 passed in 3.21s |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | **2490 passed in 101.93s (0:01:41)**；/tmp/naiyue-10401-backend-final.log |
| frontend目录 `npm test -- --run` | **26 files / 186 passed**；/tmp/naiyue-10401-frontend.log |
| frontend目录 `npm run build` | **TypeScript/Vite 210 modules，exit 0**；/tmp/naiyue-10401-build.log |
| compileall / pip check / AST及500行 / 分层依赖 | 399份Python通过，生产Runtime反向导入Application为0，无损坏依赖 |

旧恢复测试中的全局互斥断言已迁为同sid互斥；新测试反向证明其他sid可运行/恢复/删除，配对、异常身份及三次恢复更新断言保持。SQLite基线独立临时探针确认为SQLITE_BUSY_SNAPSHOT，修复后固定顺序回归通过。架构复核发现的start持锁等待问题已闭合，原探针事件循环锁等待从181ms降至0ms；该数值是单个合成边界探针，不是吞吐性能结论。安全复核无确认遗留。当前地图/交接/待办及本地链接、diff检查同步。

证据限本机原生异步假模型、真实工具/图/临时SQLite，没有真实供应商调用、用户库/真实配置修改、Windows/Linux原机验收或提交push。Web单活动门禁仍在，HTTP/SSE容量码与按会话事件订阅归104-02；组合HTTP/429隔离归104-03，不把本片标为完整浏览器并发交付。

旧事项盘点已同步[future-work](../future-work.md#进入103前的已记录事项盘点2026-10-18)：101/102/105已收尾；104还剩两片；M6主动缓存、持久用量、持久诊断/交付预算仍需讨论实施合同；checkpoint修剪及两种项目重试延后。103其余UI、原机和PyPI等保持独立范围。
