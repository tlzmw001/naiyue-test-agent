# Spec143BL-03：上下文校准的快照、恢复与界面

日期：2026-11-16。状态：**已按单系数方向修订的详细实施 Spec，尚未实施**。

前置：[总方案](143bl-context-token-calibration.md)、[S1 计量合同](143bl-01-calibration-core.md)、[S2 运行与压缩](143bl-02-runtime-compaction.md) 完成对应测试。后续：[S4 验证与交付](143bl-04-verification.md)。

本片将同一次请求的 E/C/A 如实投影到已有快照和上下文面板，并证明校准记录能沿现有消息持久化路径恢复。每个 purpose/media 使用最近有效样本的 r=A/E，统一计算 C=ceil(r×E_new)；显示、阈值和剩余预算消费同一个 C。不增加固定余量、第二种决策估值或样本窗口配置。

## 1. 范围与现状

本片修改现有上下文统计入口、读取校验和面板，不新增页面、事件类型、数据库表、公开 HTTP 路由或跨会话校准状态。

当前真实消费者：

| 当前位置 | 当前职责 | 本片要求 |
|---|---|---|
| `agent_runtime/context_usage.py` | 原始总量/分类估算；snapshot v1 写读与 16 KiB 限制 | 保留原始 E 与分组；新增有限校准投影；v2 写、v1/v2 读 |
| `agent_runtime/context_observation.py` | 执行前发布 running；finally 发布 returned/failed | 消费 S2 的本次固定 measurement，不能独立选择另一组校准样本 |
| `application/sessions/session_context.py` | 用当前 checkpoint 与当前配置只读预测下一请求 | 同一计量函数；不触发模型、压缩或状态写入 |
| `application/sessions/agent_session.py` | 短锁读取预览状态并验证未过时 | 将 thinking_level 纳入读取与复核 |
| `frontend/src/events.ts` | 接收 model_context_snapshot，当前只允许 version=1 | 同时接受 v1/v2，保持 call ID 与事件顺序检查 |
| `frontend/src/contextTypes.ts` | ContextEstimate/ContextSnapshot/ContextView 类型 | 增量类型，不把旧估算字段改成新语义 |
| `frontend/src/components/ContextPanel.tsx` | 入口占比、阈值提醒、原始分类和最近 usage | 当前值与预算比较统一使用 C，分类仍以 E 为分母 |

当前 `ContextTrigger` 使用 `>=` 标记阈值，而运行链以 `>` 触发。本片随 C 接入将提示统一为严格 `C > T`；C=T 时不提示已经触发自动压缩。

当前预览调用 `catalog.get(model_id)`，`snapshot_for_context()` 和 `context_snapshot_is_current()` 未包含 thinking_level。由于 S1 身份区分有效 thinking，必须同步：已有短锁快照增加 thinking_level，复核比较它；预览调用 `catalog.with_thinking(model_id, thinking_level)`。未挂载会话的历史入口使用 `SessionPersistence._read_history` 已提供的 thinking_level。无新锁、无跨 IO 持锁。

## 2. 对外数据合同

### 2.1 保留原字段

`estimated_input_tokens` 始终是原始 E，`estimator` 保持 `framework_approximate`。原始 groups、system children 和 groups_status 的校验不变：明细 available 时总和等于 E，而不是 C。

`ContextView` 顶层仍为 session_id/current/latest_request。current.basis 仍是 next_request、last_sent 或 unavailable。当前预览不包含草稿和未选入的待发送消息；最近请求快照包含该次已经发送的固定输入。

### 2.2 新增 calibration 投影

v2 的 estimate 必须包含 calibration 对象；v1 不含该字段，读取时不补造历史校准结果。当前只读预览同样返回该对象，但不需要额外的顶层 version。

| 字段 | 类型与允许值 | 含义 |
|---|---|---|
| method | S1 §6 的 method 枚举 | S1 给出的来源，展示层不能重新推断 |
| calibrated_input_tokens | 非负整数或 null | C；显示与预算使用同值，只在 unavailable 时为 null |
| invalidation_reason | null 或 S1 §6 的固定枚举 | 旧依据不可使用的原因，不包含原始错误文本 |

method/reason 的允许值与首因优先顺序以 [S1 §6](143bl-01-calibration-core.md#6-内部接口与所有权) 为唯一来源，不在本片扩展枚举或接受任意字符串。内部读取路径固定为 `RequestMeasurement.budget`；其 raw_input_tokens/status 映射原 E/status 字段，其余三字段映射此表。calibration 对象只接受表中三个字段。

配对规则：

- method=unavailable 时 E/C=null、status=unavailable、groups_status=unavailable、groups=[]，与 S1 budget 状态保持一致。
- method=uncalibrated 时 C=E。
- ratio_estimate/inherited_agent_ratio 时 E/C 均可用；r 可小于 1，因此 C 小于 E 合法，不能额外要求 C≥E。
- method=unavailable 不能在 UI 自动回退到一个看似可靠的 C；历史 v1 无校准字段则明确显示“历史原始估算”，可沿旧 E 展示。
- 输入值必须是实际 int，拒绝 bool、负数、NaN、Infinity、字符串数字；不允许额外字段塞入消息正文。

不将内部样本、identity、revision、sample_id 或模型连接信息放进公开 calibration 对象。校准记录与统计快照是两个独立 metadata 键：损坏校准记录由 S1 降级；损坏快照由 `read_snapshot` 拒绝，两者不互相覆盖。

### 2.3 ContextSnapshot v2

顶层字段集合保持精确：version、model_call_id、model_id、run_id、recorded_at、status、estimate、usage。新写 version=2；status 仍为 running/returned/failed；usage 仍为 input_tokens/output_tokens/cache_read_tokens/cache_write_tokens 或 null。

v2 的 estimate 字段集合固定为：status、estimated_input_tokens、image_count、context_window_tokens、max_output_tokens、compaction_reserve_tokens、trigger_threshold_tokens、context_window_source、max_output_source、compaction_reserve_source、groups、groups_status、estimator、calibration。来源字段仍只允许 config/default/unknown；groups 仍沿原 group/system children 形状。v1 保持原字段集合，其中 groups_status 可缺省；v1 不接受 calibration。

v2 唯一结构增量是 estimate.calibration。原 identity/time/usage 字段与数字类型/明细/16 KiB 校验继续执行，不提高事件体积上限。当前 read_snapshot 不校验 cache 分项和与 input 的大小关系；返回 usage 的异常 cache 分项由 normalize_model_usage 处理。本片不额外收紧 v1 历史 usage 的读取规则。

`read_snapshot` 根据 version 分支验证：v1 兼容原有 groups_status 缺省和 cache_write_tokens 缺省；v2 必须满足新增配对规则。不把无效 v2 当成旧 v1 剥掉字段后接受。旧记录无校准时按旧口径读取，不重新计算过去的 C。本组旧草案未发布实现，不为已取消的双估值草案新增兼容分支。

`ModelContextSnapshotData` 仍调用同一个 read_snapshot 作为写入事件边界的验证器；snapshot_json=null 的有界投递合同继续存在。

## 3. 数据生产时点与调用链

### 3.1 正常模型请求

1. S2 的 ModelInput 投影消息，产生本次 RequestMeasurement，在调用内层 handler 前赋给 `context.invocation.model_call.context_measurement`。
2. `observe_model_request.awrap_model_call` 进入后立即保存该对象的局部引用。该对象属于本次尝试，不在模型返回后重新读取可能已被 retry 替换的字段。
3. `estimate_context` 从该 measurement 取得 E/C，仅补原始分组和已有预算元数据；分组失败不丢弃可用总量和校准值。
4. `request_snapshot` 生成 version=2/running/usage=null，按原事件发布。
5. ChatModel 返回后，`finish_snapshot` 保留发送前的 estimate 原值，只更新 status 和本次 normalized usage；随后附在返回 AI 的 SNAPSHOT_KEY。
6. 内层观察完成后，外层 ModelInput 才将新校准样本附到同一 AI；图正常提交消息后两份 metadata 同时可恢复。

因此最近请求显示的是“事前预测＋事后测量”，不能用此次 A 反算一个新的 C 覆盖原预测。下一请求才使用新样本。

内部函数扩展：`estimate_context(model, system_message, messages, tools, *, measurement=None, response_format=None)`。原四参数调用保持可用；measurement 提供时直接消费，不二次挑样本。不提供时调用 S1 对同一投影输入测量，并传入有效 response_format，供独立统计测试使用。该函数不修改传入消息。

### 3.2 overflow 与观察失败

原请求失败，观察 finally 发布 failed/usage=null，S2 不从失败响应建立样本。恢复摘要有独立 purpose；重试重新进入 ModelInput，得到自己的 measurement。逻辑 model_call_id 沿用原关联合同，事件仍按原 sequence 覆盖为最近尝试；不新增第二组模型生命周期事件或调用 ID 映射注册层。

普通观察函数抛错时沿原诊断隔离；S2 直接采样不依赖本事件是否成功发布。原模型异常身份不得被快照错误覆盖。必要 checkpoint 保存错误仍由原必要操作链传播。

### 3.3 只读当前预览

`HistoryAPI.context → context_view → 当前会话短锁快照/aget_state → resources/system/tools 准备 → with_thinking → prepare_model_messages → S1 measure_request → estimate_context`。

预览的 measure_request 显式接收原 checkpoint 消息作为 calibration_history，投影后的消息用于原始 E 的计量；所得 measurement 直接交给 estimate_context，不由统计层再扫描另一份历史。无需为预览生成消息前缀指纹或独立比例。

整个过程只使用当前消息所带样本，没有远程计数、摘要、模型调用或数据库写入。预览只重估当前已有内容；不会把上次 A 直接展示为当前实测。

预览和无 measurement 的统计调用仍须传递当前有效 request shape；当前正式应用的 response_format=None 可以直接传 None。若装配入口已提供非空 response_format，必须沿 S2 的同一内部传参进入 measure_request，按 S1 降级，不能在预览时悄悄重置为 None。

结束时保持既有模型/工具视图/checkpoint/background notices 的过时检查，并加 thinking_level 检查。检查不通过则 current.basis=unavailable、estimate=null，不展示刚才旧配置下的校准值。

busy 时继续返回 last_sent 语义，由已交付的本轮快照覆盖；审批中、需要人工恢复或没有可信模型头时，不为了提供预估而放行执行或改图状态。

## 4. 恢复与存储验证

S1 的校准 envelope 和这里的 v2 snapshot 都沿现有 `BaseMessage.response_metadata` 保存。以下路径须实际回归，不预先增加 schema/table：

| 场景 | 当前路径/事实 | 必须证明 |
|---|---|---|
| 本机会话重开 | SessionPersistence 读取原图 checkpoint 消息 | 同一消息样本数值/版本保留；下一请求继续校准，不从累计 usage 表恢复 |
| 手动压缩成功/失败 | 原两步提交与确认，候选 summary 携带 S1 envelope | 成功后读到更高 revision 的记录与各用途/媒体最新样本；失败/取消时旧图和旧样本不变 |
| 最近任务重生成 | `conversation/session_regeneration.py` 从 source checkpoint 复制 prefix | 只使用被保留前缀样本，不能读取被替换分支后来的样本 |
| 根/子 Agent | 各自 checkpoint 消息；主面板忽略子 source | 子样本不进入根系数；子 usage 不覆盖主快照 |
| Runner 重启 | `cloud_runner/kernel.py` 使用 state/session.sqlite 与原 checkpointer | 续跑读回样本，bootstrap token 续期不失效稳定 profile |
| Runner 必要历史 | 原消息序列化成必要 history message/parts | 校准 metadata 可序列化且随必要历史保存；必要历史失败不伪造完成 |
| 只读旧记录 | 旧消息无校准字段、旧 snapshot v1 | 保留原始统计，冷启动；不补造历史模型测量 |

`cloud_runner/observation_encoding.py` 当前不透传 model_context_snapshot。本片不新增云端上下文面板或该事件的云端公开协议；Runner 的校准执行与本地持久恢复是必须验收项，云端观察 UI 的新增能力不在本片范围。

确认现有序列化即可保留字段时，保留原存储实现，只增加行为测试；只有发现实际字段过滤造成丢失时，才在对应现有转换点最小修复，并列出调用方。不能为写“影响文件”而无必要修改 SQL。

## 5. 前端显示与兼容

保留当前四个标签页和布局。

| 位置 | v2 | 旧 v1 |
|---|---|---|
| 当前占比与主数字 | C / window，文字“校准预估”或“未校准预估” | E / window，明确原始估算 |
| 达到压缩条件提示 | C > T；等于不触发 | 按历史 E > T 展示，标旧口径 |
| 剩余可用输入 | max(0, window−output−C) | max(0, window−output−E) |
| 输入构成条/各项百分比 | 原始 groups / E | 原实现 |
| 最近请求 | 同次发送前 E、C 和返回 A 分开 | 仅原始 E 和返回 A |
| 累计用量 | 原有计费统计不变 | 不变 |

不增加第二个预算检查数字或对应说明栏；同一 C 同时服务当前预估和阈值判断，但不能称实际用量或安全上界。未知值维持“未提供/预估暂不可用”，不填 0。`inherited_agent_ratio` 仅是弱先验；如进入可见记录，显示“沿用历史比例预估”，不称摘要实测或精确计数。图片说明保留原始估算仍有不确定性的事实。

`frontend/src/events.ts` 允许 version 1 或 2，仍拒绝不匹配的 model_call_id 和损坏 JSON；snapshot_json=null 清除本轮可选快照，不造成消息流失败。`ContextEstimate.calibration` 为可选字段以兼容旧输入；`ContextSnapshot.version` 改为 1|2。

后端生产者完成严格字段校验；前端至少校验 v2 数字可用性、method/status/C 的配对，不允许 malformed calibration 产生 NaN 占比或剩余预算。避免多个组件各写一套回退规则：在现有 ContextPanel 模块内复用单一展示值选择的小函数，v2 取 C、v1 取 E，供 ContextTrigger 与 Estimate 使用；不创建新的通用状态层。

## 6. 事件与诊断

沿用 model_context_preflight、model_request_context、model_context_budget、model_context_snapshot 及既有 compaction 事件。

- S2 已将 preflight.needs_compaction 改为 C>T；S3 保留这个单一事实，不再次计算独立阈值结果。
- 原 estimated_input_tokens 日志字段保持 E；追加 calibrated_input_tokens/method/fixed reason，与 v2 快照一致。
- model_context_budget.input_tokens 和 threshold_exceeded 仍描述此次返回 A 与 B 的比较，不能改成 C。
- 不把内部样本或身份指纹发给 UI；不增加事件驱动的预算调度。
- 快照超出既有 16 KiB 或 Web frame 预算时，继续可观察地省略统计 payload，保持序号和主体响应可用；采样 metadata 不依赖统计投递。

## 7. TDD 与影响文件

新增 `tests/test_context_calibration_projection.py`，用于数值协议与冻结时点；新增 `tests/test_context_calibration_recovery.py`，用于消息持久化/恢复，避免把已有大测试文件继续加到 500 行以上。

先红后绿覆盖：

1. v2 带单一 C 正常读回；v1 原样兼容；坏版本/多余字段/非法数字/不匹配 method/status/C 被拒绝。
2. E=20000、C=40000 时原始 groups 总和保持 20000；分组失败仍保留 C；向下校准 C<E 的合法值不被拒绝。
3. 发送前 C 与返回 A 不同，finish_snapshot 不覆写 C；下一请求使用最新样本，系数升降都立即反映在同一个 C。
4. observer 发布失败/投递省略/usage 落盘失败，S2 样本仍进入正常消息；主异常身份保持。
5. idle 预览不调用模型、不写图，不包含草稿；并发切 thinking 或模型后预览明确不可用。
6. 压缩后旧 A 不显示为当前实测；各用途/媒体最新样本随候选提交携带，summary usage 不改 agent 系数。
7. SQLite 关闭重开、regenerate 源 checkpoint、根/子隔离、Runner 重开与必要 history 分片保存。
8. 浏览器入口按 C>T 判断，C=T 不提示；同一 C 用于占比与剩余、E 用于分类；无第二个决策值。
9. legacy v1、无校准/无 usage、null 可选投递、损坏 v2 的 UI 降级均不伪造数字。
10. 最高可识别 revision 的坏记录不复活旧样本；新有效响应以更高 revision 恢复。恢复后不会被旧坏记录持续清空；四个用途/媒体样本槽的边界保持。

必须迁移的生产文件：context_usage.py、context_observation.py（只增投影，不重写 S2 判断）、session_context.py、agent_session.py、contextTypes.ts、events.ts、components/ContextPanel.tsx。现有 storage/Runner 历史路径先测试验证，不预设新表或替代链。

现有测试扩展：`tests/test_context_inspector.py`、`tests/test_model_request_context.py`、`tests/test_last_regeneration.py`、`tests/cloud/test_runner_kernel.py`、`tests/cloud/test_runner_history_archive.py`、`frontend/src/context-inspector.test.tsx`、`frontend/src/events.test.ts`。旧字段断言保持原含义；为校准新增断言，不能删掉必要安全/顺序检查。

定向命令（新增文件实施后执行）：

```bash
.venv/bin/python -m pytest -q tests/test_context_calibration_projection.py tests/test_context_calibration_recovery.py tests/test_context_inspector.py tests/test_model_request_context.py tests/test_last_regeneration.py tests/cloud/test_runner_kernel.py tests/cloud/test_runner_history_archive.py
npm --prefix frontend test -- --run src/context-inspector.test.tsx src/events.test.ts
npm --prefix frontend run build
git diff --check
```

## 8. 验收与交接

L1：新增字段、v1/v2 读取和前端消费确实存在。L2：不是占位结果，原始构成与校准总量独立，未知状态可检查。L3：实际主请求快照、HTTP 当前预览、SSE、重开与 Runner 都通过对应真实组件路径，不只测单个字典函数。

提交前全量与标准构建遵循 S4。记录新增测试的有效红灯、定向/全量最终结果、v1/v2 往返证据和两种视口截图。普通快照错误仍是观察故障；必要保存错误仍阻断。未进行真实模型验证时明确记录其缺口。

本片不新增锁或状态服务，不改变审批、工具执行、归档保留或数据恢复授权。完成后更新交接，唯一下一片为 S4 验证与交付；本 Spec 的存在不表示这些能力已经实现。
