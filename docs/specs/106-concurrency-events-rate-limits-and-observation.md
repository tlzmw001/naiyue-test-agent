# Spec106：并发下的事件路由、供应商限流与消息观察开销

日期：2026-10-18。状态：**E1已随104-02实施；R1离线部分已随104-03完成；P1仅保留基线**。

用户授权将三项核查和Pi对照整理为新Spec，交给正在推进会话架构/并发的另一会话阅读。本轮授权是文档记录，不是实现、真实供应商压测或新增重试的授权。本文不覆盖另一会话正在进行的修改。

当前进展：[104-02范围订阅及Web验收](104-02-web-session-concurrency.md#7-实际结果2026-10-18)已完成，R1离线隔离已在[104-03](104-03-concurrency-acceptance.md#6-实际结果与104收尾)完成，真实429/HTTP尝试仍待另行授权，P1保留测量基线。下文成稿时源码/探针与建议为历史依据，不覆盖当前两片实现。

## 1. 阅读结论与放置位置

| 编号 | 核查结论 | 建议动作 | 与现有分片的关系 |
|---|---|---|---|
| E1 事件订阅范围 | 全局订阅快照导致多余跨会话回调；其他session在job取锁前返回，没有跨会话锁内投影 | 按会话选择订阅者，保留真实全局观察者和现有事件合同 | 已纳入104-02合同并实施验收 |
| R1 供应商限流 | 根任务额度不能限制全部模型请求；是否先遇到429没有实网并发证据 | 104-03已完成离线限流故障隔离；后续有界实网报告统计429/尝试数 | 不在104预建模型限流器或恢复项目层重试；真实请求另定预算 |
| P1 消息观察CPU | 每个values快照会对全体消息重新JSON序列化/哈希，合成探针约5.5ms/次 | 保留基线，在并发验收观察循环延迟；有瓶颈证据后单独优化 | 不作为105/104前置；增量识别的正确性方案须另行讨论 |

既定顺序保持：[Spec105会话行为归属](105-session-ownership-refactor.md)收尾 → [Spec104跨会话并发](104-cross-session-concurrency.md)逐片实施。106是补充核查与验收要求，不是插入105之前的新架构阶段。

成稿时最新handoff已记录105-01/02/03验收，104仍标为未实施；这些是另一会话的交接状态，不是本稿重新验证的结果。另一会话持续修改中，实施者须重读最新handoff、104/105及实际代码；以下函数位置是核对入口，不是要求恢复旧owner。本文未新增任何功能已完成或用户已掌握的声明。

105-04实施接线已引用本Spec的放置位置，见[105-04第6节](105-04-active-run-and-resource-lifecycle.md#6-后续关系)；该时点E1/R1/P1尚未实施；现E1及R1离线部分已完成，P1保持待办。ActiveRun是根运行占用，应用操作完成信号是关闭等待，两者都不是模型请求限流器。

105-05当前主链及组合收尾见[105-05](105-05-session-lifecycle-acceptance.md)。它不实施本Spec的订阅、限流或观察优化；104-01先在现有Service/Actor/Persistence边界增加准入和会话协调。

## 2. 当前源码依据与证据边界

| 入口 | 核对事实 |
|---|---|
| [ApplicationEventSubscriptions](../../naiyue_test_agent/application/run_events.py)的subscribe/publisher | 所有handler登记在一个字典；publisher创建时复制当时全部handler并加_log_event |
| [RunDelivery._launch](../../naiyue_test_agent/web/run_delivery.py) | 每个job通过application.subscribe_events(job.observe)登记；当前仍有单active Web门禁 |
| [DeliveredRun.observe](../../naiyue_test_agent/web/delivered_run.py) | 先按session_id返回，再取job锁，锁内继续核对run_id和sequence |
| [RunEventPublisher](../../naiyue_test_agent/agent_runtime/events.py) | 保存本run的handler快照，串行交付，隔离失败观察者；AgentEventPublisher委托根publisher |
| [ModelCatalog.get](../../naiyue_test_agent/model_access/__init__.py) | 返回配置中对应模型对象；SDK重试默认2，配置可覆盖 |
| [AutoPermissionReviewer](../../naiyue_test_agent/agent_runtime/tool_execution/permissions.py) | 从同一catalog获取快审/详审模型，普通审查异常按现有合同降为Ask |
| [MessageStateObserver](../../naiyue_test_agent/agent_runtime/message_observation.py) | 逐条_snapshot，再与已存哈希比较；只避免重复发布，不避免重复快照计算 |
| [_StreamState.part/state](../../naiyue_test_agent/agent_runtime/async_message_observation.py) | values进入完整消息观察，messages进入文字delta；失败时还可观察recovery_view |

本轮只有源码核对和本机合成探针：没有启动多个真实Web模型任务、没有供应商请求、没有读取或修改真实模型配置、没有修改生产代码/测试。微基准不证明线上性能，也不证明104已实现。

## 3. E1：按会话选择事件订阅者

### 3.1 现状链与两处修正

```text
job A/B/... → application.subscribe_events(job.observe)
            → Service内ApplicationEventSubscriptions的全局handler集合
Service准入 → publisher(session_id, run_id)快照当时全部handler
运行发布   → 逐个调用handler
job.observe → 其他session立即返回 → 本session取锁并核对run/序号后投影
```

原问题的方向成立，但不能称为每个事件都做N次锁内处理：

- 其他session的过滤在job锁之前；不会为这些job复制正文、序列化Web事件或更新投影。
- publisher的handler集合是创建时快照，不是每次publish动态查询。后登记的job不会进入旧publisher；取消订阅也不移除已经建立的run快照。
- 三个订阅者A/B/C依次登记并各建一个publisher，然后各发一条run_started的合成结果：A收到A/B/C，B收到B/C，C只收到C。共6次业务handler调用，不是9次；仍证明存在不必要的跨会话回调。
- 当前Web单active门禁限制了同时活跃job，104放开后这项开销才会随重叠订阅增加。复杂度描述为单事件O(快照handler数)，不是N份完整投影。

### 3.2 建议合同与接口

目标链：

```text
publisher(A) → _log_event + 全局订阅者 + A的订阅者
publisher(B) → _log_event + 全局订阅者 + B的订阅者
根/子事件   → 同一根session范围 → job仍核对run_id、source、sequence
```

建议沿现有入口增加可选关键字，不替换为强制session参数：

```python
# 建议接口；须在104-02实施合同确认后实现。
application.subscribe_events(handler, *, session_id: str | None = None)

# 当前全局观察场景继续显式使用原调用方式。
application.subscribe_events(handler)

# Web job只登记当前会话。
application.subscribe_events(job.observe, session_id=session_id)
```

内部ApplicationEventSubscriptions可沿现有锁/token保存全局及按sid的handler集合；publisher只快照全局＋目标会话。没有必要新增总线、路由服务、后台队列或每会话应用实例。公开接口扩展须由实施片确认，本稿本身不修改签名。

必须保持：

1. CLI等现有全局消费者的真实用途；_log_event每个事件只执行一次。
2. run开始时的订阅快照语义；中途新订阅/退订只影响后续publisher，除非另行明确修改合同。
3. 同步轻量回调、FIFO/重入顺序、每run观察故障隔离及安全诊断；不顺便改为异步投递。
4. 子Agent事件使用根session路由，source仍表达子路径/子run；不能用子thread替代根session筛选。
5. DeliveredRun原身份/序号/缺口校验保留；订阅筛选不承担HTTP授权或必要保存。
6. 成功、准入拒绝、失败、取消等job收尾均退订；空会话订阅桶随最后一次退订清理，不新增持久缓存。

正常例：A/B各自登记会话观察者→A发布根/子事件→只调用全局及A消费者→A job完成原投影。失败例：A观察者抛异常→原合同记录并禁用本run该观察者→B的运行/订阅与A的必要保存不受其覆盖。

### 3.3 预计文件与验收

预计涉及run_events.py、SessionService/AsyncApplication订阅接线、web/run_delivery.py及相关测试；实际位置服从105收尾后的owner。CLI桥如需暴露新参数须核对真实调用，不增加无用途转发。不改事件载荷、存储表、RunServices、模型循环或权限规则。

- A事件不调用B的handler；测试统计调用次数，不能只检查B投影最终为空。
- 全局handler收到A/B，匹配sid handler收到本会话根/子事件；两种范围均恰好按注册交付。
- A/B并行期间退订A不删除B；旧publisher与新publisher分别满足快照合同。
- 同一sid下一run不残留已结束job订阅；准入拒绝/取消也无残留。
- 一个handler故障不覆盖主异常或必要保存；FIFO、sequence、缺口与原异常身份回归保持。
- HTTP/SSE并发交付验证正常/审批/停止/失败归属，优先复用104真实图和临时SQLite夹具。

已有快照合同见[应用事件测试](../../tests/test_application_run_events.py)的test_stop_busy_gate_and_active_subscription_snapshot。Spec104当前4.3仅要求消费者过滤，实施前需由负责会话补充订阅端路由范围，不能只把设计图改名当成接线。

## 4. R1：供应商请求容量与429验收

### 4.1 当前可确认与不可确认的事实

- max_active_runs=10是104拟定的根执行额度，不能解释为全应用最多10个模型请求；子Agent、Auto审查及摘要可能增加实际请求。
- 同model_id从catalog取相同模型对象；角色可以配置不同model_id。共用客户端本身不是429根因，额度可能按供应商账号、项目、模型类或其组合共享；另建客户端不能自动获得新额度。
- 并发数、请求速率和token速率是不同指标。单一模型并发Semaphore也不能保证满足RPM/TPM限制。
- 代码默认sdk_max_retries=2，模型配置可覆盖；项目层重试延期不等于没有SDK重试。未读取真实配置，不能声称本机实际请求都使用2次重试。
- 429也不能一律解释为短暂拥塞或可重试余额。需在后续设计中区分临时限流和配额/账务限制。
- 目前没有本项目10根并发实网样本，不能断言第一瓶颈必然是429。

供应商事实参照：[Claude官方限流说明](https://platform.claude.com/docs/en/api/rate-limits)描述RPM/ITPM/OTPM、部分模型共享额度和Retry-After；该供应商的具体规则不能直接替代当前网关合同。

### 4.2 104-03离线验收

沿既有SDK错误分类和Auto降级合同注入确定性限流异常，覆盖：

- A根模型限流失败，原异常/描述沿现有链传播，B仍完成；A必要清理后释放名额，不能把模型错误伪装成成功ToolMessage。
- A子模型限流按既有子失败报告合同回父任务，其他会话不被停止；不新增自动重跑工具。
- Auto快审/详审不可用时沿现有Ask回退；不得因限流默认允许工具。
- SDK等待或模型调用期间停止可用，清理/保存与名额释放顺序保持；不为验收引入新的产品重试路径。
- 错误可观察，但不记录原始供应商正文、全量响应头、凭证或任务内容。

104-03已完成上述离线真实图/临时SQLite验收：OpenAIRateLimitError（当前适配器的SDK/框架边界类型）及ModelRateLimitError按根/子、Auto快/详审分别注入，另验证模型等待中停止；B均保持自己的运行/保存。详见[实际结果](104-03-concurrency-acceptance.md#6-实际结果与104收尾)。无供应商压测、项目重试或限流器。

### 4.3 后续有界实网验收

在[Spec100](100-real-provider-acceptance.md)后续扩展或独立获批验收中，先确定目标模型/网关、并发梯度、每组请求次数、SDK重试设置、总发送预算及停止条件，再执行。历史61次请求与报告保持，不重置旧账本。

报告至少区分：

| 指标 | 目的 |
|---|---|
| 根任务/子模型/Auto/摘要的逻辑调用数 | 看调用来源；不能仅按Web根任务数估算负载 |
| 实际HTTP发送/尝试数 | 看SDK内部重试；最终成功也可能曾收到429 |
| 429次数、最终失败数、退避/耗时 | 分开记录恢复和耗尽；没有观测能力则明确标为未知，不能写0 |
| 模型标识与请求时并发量、可获得的供应商token用量 | 对照发生条件，区分实测用量与本地估算 |
| Retry-After等必要安全摘要 | 仅保留获准白名单的解析值；不整份保存响应头/正文 |

优先扩展已有有界验收器的实际请求观测，不在104新建完整追踪平台。正常例为所有请求成功并有真实尝试计数；失败例为出现429后按获批停止/重试预算记录结果，保留已完成工具事实，不自动重放整个业务任务。

### 4.4 限流器的延期边界

本Spec不引入新配置、Semaphore、限流队列、每模型客户端副本或项目层重试。若实测出现可重复的容量问题，再讨论共享额度究竟按model_id还是provider/account划分，以及等待/拒绝/公平性合同。

未来若加请求额度，应覆盖实际根/子/Auto/摘要请求边界，并在流结束、失败或取消清理后释放；不能持有整次根任务额度替代模型请求管理。未验证的RPM/TPM保证不作承诺。

## 5. P1：完整消息观察的CPU基线与优化约束

### 5.1 当前正常与失败链

```text
graph values → _StreamState.part → state → MessageStateObserver.observe
             → 每条消息_snapshot：JSON → UTF-8 → SHA256
             → 与旧哈希比较 → 只发布新增/修订 + 必要的上下文移除事件
graph messages → delta：不走上述完整历史快照链
执行异常 → 在允许的合同内读取recovery_view并观察，原异常优先
```

快照已去除非展示元数据并保留稳定消息事实；哈希缓存只避免重复发布，未缓存序列化工作。此处读取的是当前图上下文，不是Spec101独立聊天归档的所有历史；归档懒加载不自动减少本段成本。压缩后上下文减少可能降低此成本，但不能为此删除可浏览历史。

### 5.2 本机合成结果与复现方式

前一轮探针使用600条HumanMessage，每条content为4096个ASCII字符，稳定ID；先seed，再预热5次，计时40次完全未改动的observe。publisher只计数，测量不含Web投影、实际模型/工具、保存或网络。

| 项目 | 实际结果 |
|---|---:|
| 消息数 | 600 |
| 完整投影总字节 | 2,503,090 |
| 单次中位数 | 5.508ms |
| P95（最近秩） | 6.038ms |
| 40次不变历史观察的发布数 | 0 |

该结果与用户约5.9ms接近。10份类似values任务集中到达时，同一事件循环上可能累计约55ms同步处理；这是按微基准相加的推算，不是已测得的10会话用户延迟，也不是每个token都增加55ms。真实Tool/AI/多模态形状、快照频率和其他同步工作须分别测量。

可在仓库根运行以下零模型请求探针；每次运行数值可变，不作为极紧时间阈值的单元测试：

```sh
.venv/bin/python - <<'PY'
import statistics
import time
from langchain_core.messages import HumanMessage
from naiyue_test_agent.agent_runtime.message_observation import MessageStateObserver, _snapshot

class Counter:
    def __init__(self):
        self.calls = 0
    def publish(self, *args):
        self.calls += 1

messages = [HumanMessage(id=f'm-{i}', content='x' * 4096) for i in range(600)]
publisher = Counter()
observer = MessageStateObserver(publisher)
observer.seed(messages)
for _ in range(5):
    observer.observe(messages)
times = []
for _ in range(40):
    start = time.perf_counter()
    observer.observe(messages)
    times.append((time.perf_counter() - start) * 1000)
print('bytes', sum(_snapshot(m, 'graph_values').snapshot_bytes for m in messages))
print('median_ms', statistics.median(times), 'p95_ms', sorted(times)[37])
assert publisher.calls == 0
messages[0].content = 'in-place revision'
observer.observe(messages)
assert publisher.calls == 1
print('unchanged emissions=0; same-ID in-place revision emitted')
PY
```

### 5.3 为什么不能只跳过已见ID/同一对象

现有[消息观察测试](../../tests/test_message_observation.py)明确覆盖同一ID换内容、同一对象嵌套内容/工具参数原地修改、深拷贝同内容不重复、压缩移除、不可投影后恢复正常等行为；[故障观察测试](../../tests/test_message_observation_failures.py)覆盖recovery_view。前一轮探针也确认原地改同ID内容会产生一条修订。

因此，不能以“ID在缓存中”或“Python对象身份未变”直接跳过正文检查。只看到ID并不知道哪些ID已修改；必须先说明变更信息由谁生产、是否完整。

未来候选为：使用可信updates/显式变更边界确定候选消息，仅对候选构造正文快照；values保留必要ID顺序/移除确认，无法证明候选完整时走保守全量回退。该候选尚未验证框架、中间件、审批和恢复路径的覆盖，不是获批实现方案。

启用优化前必须保留：首次baseline、同ID修订、原地修改、相同内容去重、文字草稿最终确认、压缩/状态移除、未知类型/无ID诊断、恢复视图及原异常优先。不能删去这些断言换取更快结果。

### 5.4 何时进入实现

105/104正常推进，本项先作为基线。并发组合验收收集values处理次数、单次耗时和事件循环延迟；用实际短/长上下文及根/子路径判断用户可见影响，再确定优化目标和代表性样本。

不为此立即加缓存平台、逐part线程卸载、消息版本字段或框架私有补丁。若要引入版本/dirty信息，先补生产者与消费者合同及正常/失败链，再独立审批实施范围。

## 6. Pi对照：可借鉴的机制与差异

本机Pi核对版本：`a69bef789`。以下是该检出版本源码事实，不代表所有Pi版本或宿主扩展。SDK与实验性server是分别核验的调用链，不能声称server一定由SDK AgentSession实现；本轮未安装/运行Pi测试。

### 6.1 事件范围

- SDK：Agent持有实例listeners；AgentSession通过agent.subscribe(_handleAgentEvent)接本实例，再遍历自身_eventListeners。两处subscribe都属于实例，不先遍历全部会话。
- server：LiveSessionManager按id持有live runtime；runtime.subscribe闭包绑定live；progress/snapshot只发送给live.connections。server全局列表通知是另一类消息，不能与会话进度混为一谈。
- 可迁移点：在订阅/发布边界明确会话范围。保持项目的同步轻量通知、故障隔离及快照合同；Pi Agent核心会await监听者，而本项目普通观察不可改变必要执行结果，不照搬它的等待语义。

源码：[Agent.subscribe/processEvents](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent.ts:250)、[AgentSession事件与订阅](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/agent-session.ts:567)、[server会话绑定/投递](/Users/zmw/naiyue-personal-workspace/pi/packages/server/src/sessions.ts:224)。

### 6.2 供应商请求与重试

- OpenAI Responses等适配器将SDK内置maxRetries设0，再通过retryProviderRequest执行配置允许的请求重试；包装器识别429、解析Retry-After并提供可取消退避。包装器本身缺省重试0，SDK装配从settings传provider retry参数，不能把“有包装器”解释为每次必重试。
- AgentSession还有会话级可配置重试，_prepareRetry进行指数退避；错误识别排除部分配额/账务错误，不能把所有429都当成临时限流。
- 在核对的SDK、Agent及server链路未看到共享按模型并发准入器；不能借此断言全部插件/宿主都无限制，也不能认为Pi已经解决供应商额度问题。
- 本项目采用现有SDK重试与独立的根执行额度；不因Pi有外层重试就在104恢复已延期机制。

源码：[Responses请求包装](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/api/openai-responses.ts:146)、[provider重试](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/utils/provider-retry.ts:105)、[SDK传参](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/sdk.ts:312)、[会话退避](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/agent-session.ts:2811)、[错误识别](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/utils/retry.ts:223)。

### 6.3 消息变更来源

- agent-loop直接生产当前消息的message_update和最终message_end；Agent.processEvents按具体事件更新流消息/消息列表。
- AgentSession._handleAgentEvent在message_end调用sessionManager.appendMessage；正常JSONL追加序列化新增条目，不经过本项目这种全历史JSON＋SHA256的变更推断链。
- 首次落盘仍可能遍历已有条目，模型请求也会整理上下文；不声称Pi所有历史操作都O(1)或没有CPU开销。
- 可迁移点是“生产端给明确变更”。本项目继续使用create_agent/LangGraph，不能为省观察成本重写循环或假设values等价于一条具体消息事件。

源码：[循环具体消息事件](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent-loop.ts:314)、[会话保存入口](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/agent-session.ts:650)、[追加/首次持久化](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:1015)。

另按项目规则核对AgentX本机`3fcfa0e`：ConversationAppService.chat → handler.chat获得本次SseEmitter，再由ChatSessionManager按sessionId登记。它支持会话范围的职责对照，但该链不证明其全局事件或模型限流与本项目一致；本Spec不引入其Java运行时或追踪平台。

## 7. 交给另一会话的执行边界

1. 先完成已确认的105工作，重读本Spec与最新104/105；不要回退正在迁移的AgentSession归属。
2. 在104-02实施合同中纳入E1的真实接口、文件、正常/失败调用链与回归断言，用户确认后实施。105若已提前处理同一订阅职责，复用该结果，不再建第二条事件链。
3. 将R1离线故障隔离纳入104-03；实际429观测放后续获批的有界实网验收，保留SDK/逻辑尝试区别。
4. P1保留为性能待办；先看并发后的代表性证据，再讨论增量观察方案。
5. 不自动增加模型请求限流器、项目层重试、工具重放、数据库迁移、配置项或新依赖。

实施片仍须TDD、完整行为回归、前端构建及接线/边界检查；性能阈值不写成依赖机器速度的脆弱单测。文档阶段只做来源/链接/结构与diff检查，不声称构建或功能验收通过。

## 8. 本稿文档验证

2026-10-18：25个本地链接及带行号目标、成对代码围栏、Python探针语法检查通过。原样执行第5.2节探针：快照2,503,090字节，中位5.489ms、P95 5.996ms，不变历史发布0、同ID原地修订发布1；与前轮基线分开保留。git diff --check通过。本轮只新增本Spec并补handoff/future-work短索引，未修改104/105、产品、测试、依赖或真实配置，未请求供应商、提交或push；未运行整库测试/构建。
