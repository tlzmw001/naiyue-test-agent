# 104-02：Web多会话任务与会话范围订阅

日期：2026-10-18。状态：**已实施并验收**。依赖[104-01](104-01-application-concurrency.md)，接入[106 E1](106-concurrency-events-rate-limits-and-observation.md#3-e1按会话选择事件订阅者)。104-03组合验收已完成，见该片实际结果；本片未增加真实供应商请求。

## 1. 当前链和交付边界

当前HTTP → WebService.start → RunDelivery._launch，在唯一active处拒绝其他会话；控制、live_child审批、恢复/删除和关闭也仍用全局active。后端Service/Actor/SQLite已按会话并发，Web没有接通。DeliveredRun及前端watch已有session/run过滤、取消显示订阅和每会话草稿，优先复用。

本片把active改active_by_session，runs仍按run_id、latest仍按sid。前者只拥有HTTP producer/交付，不成为执行额度或审批权威状态；额度仍由Actor的ActiveRun经Service准入。共享图/客户端/工作区/schema8/权限管线与HTTP路径、请求/事件载荷不变；不新增生产模块、Manager、供应商限流/重试、数据库表或配置。不修改真实配置/用户库或其他进行中改动，不提交push。

## 2. 文件与接口

| 文件/对象 | 修改内容 |
|---|---|
| web/run_delivery.py / RunDelivery | active_by_session；_launch/_produce/control/forget_session/close按目标归属；原登记锁不覆盖等待job.started或运行时长。明确额度拒绝清临时pending及订阅；结果未知保留防重放回执 |
| web/service.py / WebService | delete_session/recover仅检查目标活动job，保留登记锁、工作区/状态/恢复ID/action检查 |
| application/run_events.py / ApplicationEventSubscriptions | subscribe(handler, *, session_id=None)；沿原token/锁记录范围，publisher按原注册顺序选择全局+本sid，_log_event一次 |
| application/session_service.py、async_application.py | subscribe_events(handler, *, session_id=None)转交可选范围；现有全局消费者及AsyncEventStream调用保持，不增加无用途CLI转发 |
| web/errors.py | RunCapacityExceededError → 409/run_capacity_exceeded |
| frontend/src/api.ts | 区分当前会话忙和总额度满，保留草稿/审批决定，不自动重发 |
| frontend/src/useConversation.ts（仅如回归证实需要） | 复用当前选择代次/取消/SSE/run校验；不得让旧会话响应覆盖新会话，不引入所有会话后台订阅 |
| 测试、当前地图与交接 | 新增会话订阅、Web并发/审批/故障、前端切换及满额用例；迁移真实旧内部消费者和全局互斥断言，保留幂等/权限/异常/保存断言 |

## 3. 正常调用链与数据

示意A运行，B提交：WebService校验B → _launch先查成功/待定回执，拒绝B自身活动job → 建job_b并只订阅B → active_by_session[B]=job_b，创建_produce → 退出登记锁后等待job.started。

producer调用application.arun(B) → Service._admit → B.admit_run安装ActiveRun → B._arun发布run_started → DeliveredRun.observe得到run-b并安排_ready → register写runs[run-b]、latest[B]和成功回执，started future完成 → HTTP202。持久化start/图执行和202交付可交错；202仅为已接纳，不是保存成功。必要保存后Actor释放，producer只清除仍指向job_b的B活动映射；A保持。

```python
active_by_session = {'A': job_a, 'B': job_b}
runs = {'run-a': job_a, 'run-b': job_b}
latest = {'A': 'run-a', 'B': 'run-b'}
```

原start_lock继续协调Web启动、恢复和删除的检查/登记，避免同sid管理操作交错；_launch的首次等待和同request_id待定等待均放锁外。恢复/删除仍可在管理请求内持登记锁完成IO，不宣称这些管理请求本片互相并行；其他已经执行中的Agent及控制不等待此锁。

## 4. 拒绝、控制与关闭

- 额度满：应用准入抛RunCapacityExceededError、没有run_id时，清本job待定回执/活动占位并退订，HTTP409；不改latest、不产生成功回执，不执行模型/工具或消费授权。稍后同request_id同body可再申请。不用错误字符串或单凭缺少run_started推断未执行；其他未知/已开始错误仍保留待定/成功防重放。
- 同sid重复任务仍拒绝；重复成功请求优先返回原回执。HTTP等待者取消不取消producer，其他会话仍可启动；未知观察故障也不持登记锁等待整个run。
- control通过run_id查job，再核对active_by_session[job.session_id] is job及现有状态，停止/Steer/Follow-up不串。live_child按sid查父job并核对origin_run_id，在原驱动恢复、不占新根名额；paused_root仍走新任务和原104-01准入。
- 根审批满额：原批次、决定、session_allow和画面保持；满额纯停止仍可完成。A运行时恢复/删除空闲B，只清理B所属任务/回执/历史，不删工作区文件。
- 关闭先禁止新Web任务，保留全部活动producer引用，调用原application.aclose停止根/子并等必要保存，再结算所有Web任务。应用关闭报错或取消仍完成Web收尾，主异常优先，次异常日志可见。观察退订失败按可选观察降级，不妨碍归属清理。

## 5. 订阅与前端合同

全局subscribe_events(handler)仍消费所有运行；Web用subscribe_events(job.observe, session_id=sid)。publisher创建时沿原登记顺序快照全局+匹配sid，发布不反复扫描注册表；原FIFO/重入/故障隔离及中途退订不改变已有publisher的语义保持。子事件沿根sid，source仍描述子路径；DeliveredRun的身份/序号/缺口验证保留，订阅不承担HTTP授权。

前端只订阅当前会话；切换A→B取消A的显示订阅，A后端继续。切回读取A的latest及历史，运行中接续SSE，已结束展示结果。满额不清草稿/审批选择；发送、停止和子审批始终面向目标run。新增API错误文案；不另造后台总线、主题或布局。

## 6. 验证计划

先红灯：范围订阅/原注册顺序与快照/故障隔离，A/B真实HTTP均202且到各自模型屏障；额度409无调用且同request_id可重试。随后覆盖等待者取消/无观察启动等待不挡B、同sid互斥、SSE身份与连续序号、A停止/保存失败不影响B、根/子审批、空闲恢复/删除、全部关闭及异常优先级。

前端用当前hook/组件和事件可控的API替身验证会话切换、旧事件丢弃、草稿、停止路由及满额审批决定。已有Web/App/订阅测试保持；无conftest变更。专项后跑全量后端、前端、构建、compileall/pip check、500行/分层/链接/diff；浏览器只用临时离线服务。如发现合同必要修正先同步本Spec。

104-03再将HTTP并发、根子审批/停止/保存、重开恢复、文件busy、离线429及幂等组成完整场景，不用本片绿灯代替全部组合验收或原机/供应商证据。

## 7. 实际结果（2026-10-18）

L1/L2/L3：六个Python生产文件与frontend/src/api.ts已接线；没有新增生产类/模块、配置/依赖/schema。RunDelivery.active已删除，所有生产消费者改active_by_session；scope可选参数由AsyncApplication→SessionService→ApplicationEventSubscriptions传递，Web真实job使用本sid，原全局消费者保持。前端useConversation无需修改，原草稿/选择代次/SSE取消与回切正确。

首次13项红灯确认无范围订阅、第二会话被全局active拒绝和started等待仍占登记锁；接线后通过。其余新增组合补证合计后端22项、前端3项。旧快速完成局部应用替身迁移真实范围订阅合同，原回执/结果/异常断言不减；一个旧compact测试的全局忙提示迁为本次已批准的当前会话文案，草稿/调用断言保持。原生产同步/单活动兼容分支没有保留。第一次Web回归遗漏既有rg/fd路径导致文件工具1项失败，按项目PATH复验通过；没有修改工具逻辑。没有conftest或共享测试设施变更。

| 验证命令/范围 | 实际结果 |
|---|---|
| 四份新后端测试 | 架构/安全审查定向复跑22 passed in1.71s |
| Web、应用事件、观察隔离回归 | 203 passed in22.54s；/tmp/naiyue-10402-web-regression-final.log |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | **2512 passed in107.93s (0:01:47)**；/tmp/naiyue-10402-backend.log |
| frontend目录 `npm test -- --run` | **27 files / 189 passed**；/tmp/naiyue-10402-frontend-final.log |
| frontend目录 `npm run build` | **TypeScript/Vite210 modules，exit0**；/tmp/naiyue-10402-build-final.log |
| compileall、pip check、AST/500行、分层 | 403份Python、67份前端源文件通过；Runtime反向导入Application为0，依赖完整 |

架构复核覆盖活动归属/快速完成/未知观察/关闭/FIFO与快照，无确认遗留；安全复核覆盖容量/审批/权限/错误/关闭及上述22项实际复跑，无确认遗留。文档链接及diff检查通过；无真实供应商、用户库或真实配置修改，无提交push。

## 8. 浏览器证据与限制

实际构建产物接临时Uvicorn/SQLite和原生异步离线模型，IAB页面1280×720：A、B分别发送并同时运行；保留两份不同草稿切换，C满额提示且原草稿保留；返回A停止，B仍运行且B草稿可见；返回C原草稿仍在，释放名额后重发成功；最后停止B/C。模型账本仅3次合成调用，拒绝C时只有A/B两次，未请求供应商。临时服务已正常退出，标签关闭。

证据截图：[满额与原草稿](/tmp/naiyue-10402-browser/capacity.png)、[A停后B继续](/tmp/naiyue-10402-browser/b-after-a-stop.png)、[C重发接纳](/tmp/naiyue-10402-browser/c-retry.png)。独立Playwright连接器未连接，改用IAB实际页面验证，不安装/修改扩展。控制台检查时无error，看到两条已有stream_reader_detach_failed警告（不是整场警告总数统计）；frontend/src/sse.ts与本片前基线逐字节一致，reader.cancel失败有日志且releaseLock继续。这是保留观察限制，不宣称无告警或已修复它，也不把未做的375px/Windows/Linux测试写成已做。

## 9. 唯一下一步

后续[104-03组合验收](104-03-concurrency-acceptance.md#6-实际结果与104收尾)已完成真实TCP多根/根子审批、临时库重开及106 R1离线限流隔离。本片浏览器证据仍单独保留，不重复扩大实网请求预算。
