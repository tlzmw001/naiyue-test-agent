# Spec143BL-02：请求采样与压缩预算接线

日期：2026-11-16。状态：**S2已实现并通过本地验证；S3/S4另行推进**。前置：[S1 计量核心](143bl-01-calibration-core.md)完成并通过其验证；总体范围见[总 Spec](143bl-context-token-calibration.md)。

本片使实际 input usage 真正影响下一请求和所有压缩检查。用户已确认使用单一动态系数、单一校准估值；取消近期最大倍率、固定 5% 余量和两套预算数值。本片不增加新的压缩阈值，也不引入网络计数、分块摘要、服务端压缩或独立校准存储。

## 1. 结果与边界

完成后，同次请求的原始 E 与实际 A 形成最新有效比例 r=A/E，下一份完整请求使用 C=ceil(r×E_new)；无兼容样本时 C=E_new。显示与所有容量分支使用同一 C，不保留前缀基准或增量公式分支。每次模型请求和摘要请求独立冻结计量前态，完整返回后直接生成样本，随现有消息提交保存。

普通模型请求的 E/A 必须精确配对。逻辑 model_call_id 在 overflow 重试前后可以相同，不能拿它当一次实际请求的唯一身份；每次执行使用独立 sample_id，计量前态保存在该次 wrapper 的局部变量中。摘要、子 Agent 和普通 agent 样本不混用。

保留现有公开调用签名、工具执行、权限、流式结果和错误分类。内部规划函数参数可以随本片迁移；变更后的测试调用方一起迁移，不保留无消费者的旧入口或转发层。

## 2. 当前源码链与需要替换的位置

基线：`60108431ceeeaf100df008da4765b879505fc1d7` 加工作区改动；[Spec143BK](143bk-manual-compaction-after-model-failure.md)的失败模型头手动压缩行为已经在该提交中，不可回退。

| 当前文件/函数 | 已有职责 | 本片改变 |
|---|---|---|
| `request_preparation.prepare_request` | 固定 model/system/tools，调用主动压缩，返回图更新 | 继续拥有准备时点；将统一计量用于准备，不改变固定配置合同 |
| `context_compaction._compaction_steps` | 投影、触发、切点、摘要、后验、fallback | 所有预算分支由 S1 的 C 驱动，返回摘要携带校准记录 |
| `model_input.ModelInputMiddleware` | 投影 canonical 输入并调用 handler | 冻结本次请求、共享只读计量、完整返回后采样 |
| `context_observation._prepare_observation` | 独立粗估并发布 preflight | 最小改为消费本次计量的 C；快照 v2/UI 属 S3 |
| `context_summary_io.arun_summary_steps` | 执行 generator yield 的摘要 IO，再 send/throw | 维持当前执行协议，不增加另一层摘要调用 |
| `context_recovery.awrap_model_call` | 明确 overflow 后强制压缩并单次 retry | 沿当前链自然得到 retry 独立采样，不引入重放或累计 usage |
| `manual_compaction.compact_checkpoint` | 两步替换并读回确认 | summary metadata 随原必要提交生效；不建立旁路保存 |
| `call_observation._ModelSelectionMiddleware` | 固定请求、响应门禁、失败传播 | 保持门禁；未被接受响应的临时样本不能绕过门禁持久化 |

目前 `context_compaction.py` 有七类原始计量消费者：`_cut_at_complete_turn` 的后缀、初始输入、完整轮次候选、轮内候选、候选摘要输入、实际摘要输入、最终候选输入。原本的 `estimated <= window-output` fallback 与 `post_estimated >= estimated` 强制减少检查也依赖这些数值，必须同时迁移。

## 3. 消费 S1 的接口

以下名称及字段以 S1 为唯一 schema 定义，S2 不另定义倍率算法：

| 接口 | 本片调用者 | 输入/输出合同 |
|---|---|---|
| `measure_request(model, system_message, projected_messages, tools, *, purpose, response_format, calibration_history)` | 主请求、压缩原输入/候选/摘要 | 返回不可变 RequestMeasurement；budget 含 E/C、status、method、invalidation_reason |
| `complete_sample(measurement, response, *, sample_id)` | 主请求正常返回、摘要校验成功 | response 为唯一对应的 AIMessage；返回新 CalibrationEnvelope 或 None；不写 IO |
| `carry_compaction_state(agent_measurement, summary_envelope)` | 创建新 summary 消息 | 返回携带最新样本槽且 revision 增长的候选记录；无 summary usage 时传 None；计量身份不可用时允许返回 None |

`measurement.budget.raw_input_tokens` 是 E，`calibrated_input_tokens` 是唯一校准估值 C。C 是预测，不是供应商精确计数；本片不改变 S1 的 Fraction/整数取整规则。budget 可用时 `measurement.calibration_ratio` 为正 Fraction，未校准时为1且 method 明确标记未校准，不代表存在实际样本；budget 不可用时 ratio 为 None，消费者不能补1后继续当作已验证预算。

`calibration_history` 传 canonical 消息，不从序列化的摘要文字里寻找记录。`projected_messages` 必须已经经过当前 `prepare_model_messages`；工具图片 carrier 等适配后的实际框架输入纳入 E 和媒体分类，不建立内容前缀指纹。summary 单独拆出其 system 与 human 输入，tools=[]，purpose='summary'。

S1 记录按 agent/summary 两用途、text/image 两媒体各保留最近一份有效 Sample，最多四个样本槽；每个兼容请求只使用其中一份比例。最新完整 envelope 以 revision 选择，不能拼接历史副本找回已被替换的旧比例。method/reason 与损坏记录 high-water 恢复严格引用 S1；本片不新增样本数配置、滑动窗口或另一套首因规则。

现有 compaction 的 system_messages 在已核对生产/测试消费者中均为0或1条，映射为 None 或该条 SystemMessage，不能因接口单数而遗漏输入。原列表参数不为本片改名；出现多条真实消费者时须先明确规范化合同，不能悄悄只取第一条。

在 `contracts/run_context.py` 的 `ModelCallState` 新增 `context_measurement: RequestMeasurement | None = None`，只作为本次发送的只读观察通道。BudgetEstimate/RequestMeasurement定义迁到contracts/context_calibration.py，计量核心复用同一类型，调用状态引用采用TYPE_CHECKING，避免静态和运行时反向导入agent_runtime；该字段不是 checkpoint 字段，也不能作为 complete_sample 的输入来源。

### 3.1 非空 response_format 的完整传递

S1 对非空 response_format 禁用历史校准；这个条件必须在主动压缩时已知，不能只在最后 ModelInput 才发现。沿已有对象传递，不新增解析器或注册层：

1. `_build_runtime_agent` 已接收 response_format，将其作为新增内部关键字传给 `make_model_selection_middleware`；后者与 `_ModelSelectionMiddleware` 保存该 graph 固定值。
2. `PreparedRequest` 追加默认 None 的 response_format 字段，`_prepare` 构造时赋值；`prepare_request` 将其传给 `_acompact_messages`。
3. `_acompact_messages` 与 `_compaction_steps` 增加默认 None 的内部关键字，所有 agent 原输入/候选测量传该值；不改公开 build_runtime_agent 等入口签名。
4. `_begin_recovery` 从本次 `request.response_format` 传入，与 retry 原请求形态一致；手动压缩正式入口的形态固定为 None。
5. `ModelInput` 和观察层使用各自实际 ModelRequest.response_format。摘要本身是独立纯文本请求，明确 response_format=None，不继承主请求的结构化输出格式。

非空主请求继续使用原始 E，不应用旧 agent 倍率，不生成新的 agent 样本；保留既有 ToolStrategy 输出、ToolMessage 和 structured_response。摘要自己的既有执行/采样合同保持。未来自定义 middleware 动态改变格式的行为不作为本片新增支持范围，当前实际发送阶段仍按它看到的非空形态禁用校准。

## 4. 普通请求采样及观察时点

当前 wrapper 顺序保持：

```text
固定请求 / 响应门禁
  → overflow recovery
    → ModelInput
      → observe_model_request
        → 既有内层 middleware / ChatModel
```

`ModelInputMiddleware.awrap_model_call` 的步骤固定为：

1. 调用现有 `_project_request`。必要输入转换错误原样传播，不能放入可降级的采样 catch。
2. 为这次实际执行生成 `uuid4().hex` 的 sample_id。不要使用逻辑 call_id，也不新增 ID 注册表或生命周期事件。
3. 用已投影请求和 canonical history 调 `measure_request`，冻结局部 measurement。模型/路由/API/thinking 等身份按 S1 取得；实际 system/tools 进入本次 E，response_format 决定请求形态是否支持，不建立 system/tools 内容指纹。
4. 有正式 ModelCallState 时，在 handler 前将其 context_measurement 替换为本次 measurement；缺少运行观察上下文的独立入口仍可执行计量/采样，不为观察字段强制创建 RunScope。
5. 等待 handler。发生异常、停止或取消时直接传播，不从 partial callback、累计 usage 或旧响应补造新样本。
6. 正常返回后，从 ModelResponse.result 中选择唯一 AIMessage；零条或多条均不采样并诊断固定 reason。将局部 measurement 和该 AIMessage 交 `complete_sample`。
7. 返回有效 envelope 时写入该 AIMessage 独立 metadata 键；不改 canonical 输入，不调用 checkpointer，不改模型输出文本。handler 的原返回包装完整保留。

`ModelInput` 位于观察层外侧，因此 observation 的 `finally` 先执行，采样写 metadata 后执行。必须遵循：

- 观察层进入时局部捕获 context_measurement，记录发送前 E/C；结束时只补本次实际 usage A。
- 观察层不等待、读取或触发稍后才写入的校准 envelope，也不利用当前响应重新计算已经发送请求的 C。
- `context_measurement` 后来被 retry 覆盖，不影响旧观察的局部引用，也不影响外层各次 wrapper 的局部 measurement。
- 即使 snapshot 发布或用量异步落库失败，complete_sample 仍直接消费返回 AIMessage 并执行。已有观察失败隔离合同继续成立。
- sampler 元数据构造失败只诊断固定 reason/错误类型，不覆盖成功响应或主异常；下一次使用历史样本或明确冷启动。必要输入转换、配置验证和后续必要保存失败不得因此被吞掉。

本片同步迁移 `_prepare_observation` 的 preflight：原 `estimated_input_tokens` 保持 E，追加 `calibrated_input_tokens=C`；needs_compaction 使用 C 与 T，而非重新调用裸估算。S3 负责完整快照版本和界面，但 S2 收尾不得留下一套与执行不同的 preflight 判断。独立观察测试若没有 measurement，则用同一 measure_request 构造只读结果，不运行采样。

删除 `ModelInputMiddleware.wrap_model_call` 的旧同步 override，不为它另写校准算法。可达性依据是：正式图的 select_model 只有 `abefore_model`，当前 LangChain `factory.py` 为它注册 `RunnableCallable(sync=None, async=...)`；同步调用会先在这个准备节点报 `No synchronous function provided`，尚未到达 ModelInput。仓库生产、脚本和测试中也没有独立同步 ModelInput 消费者。保留实际 `awrap_model_call`，通过真实 async 图和既有入口验证接线，不能为了保留旧入口新造同步消费者。若实施时发现新增真实消费者，先更新本 Spec 的消费方与范围，不静默扩展产品同步能力。

## 5. 主动压缩的统一决策

在 `_compaction_steps` 中，必要的 `prepare_model_messages`、metadata 类型和正数验证顺序保持。完成配置验证后，对原输入测量一次并保留 `agent_measurement` 作为这次操作的固定基准。

令 W=window、O=max_output、R=compaction_reserve、S=summary_output、K=keep_recent：

- T=W−max(O,R)，C_original>T 才触发普通主动压缩；等于时不触发。
- B=W−O；C_original≤B 是原输入可发送的预算条件。
- force/manual 不因低于 T 跳过强制流程，但保留原手动 no_safe_cut/skipped 行为。
- 不再乘固定余量，不将 O、R 相加，不重复预留 thinking/output。

### 5.1 后缀与安全切点

`_cut_at_complete_turn` 目前从尾部逐消息累加，达到 K 后再回退到最近 HumanMessage 起点。迁移后直接从后往前枚举索引>0的 HumanMessage 轮次起点，对从该起点到末尾的完整后缀进行当前模型投影，再调用S1 measure_request：system=None、tools=[]、purpose='agent'、response_format沿本次主请求、calibration_history=固定原canonical消息。这样按后缀实际媒体选择原记录中的兼容槽，得到C_suffix；没有兼容样本则C_suffix=E_suffix，不可把原含图请求的比例机械套到纯文本后缀。计量不可用时走既有不可计量合同，不以默认倍率伪装成可用规划。第一个C_suffix达到或超过 K 的起点即完整轮次切点；若只有索引0的整段历史才足够，或没有足够后缀，则返回0，继续原有无完整切点/轮内安全切点分支。

对不含图片转换且原始计数可加的历史，这与“逐消息累加后退到Human起点”相同；校准和图片投影只是改变用于比较的数量。禁止对每个逐消息中间位置投影：它可能暂时起于 ToolMessage 或半个图片工具批次，并非有效请求候选。只在完整 Human 起点形成投影，之后仍按原配对校验处理真实历史错误，不能将错误伪装为少量 token。

后缀不含 system/tools，且没有独立实测输入，应用兼容历史比例仅用于保留量规划，不能把原请求 A 分摊到某条消息。图片 carrier 可能跨工具批次产生，候选后缀应保留配对消息并按现有验证重新投影；不能单独统计 carrier 后再计一次源图片。最终候选必须按完整输入和该候选媒体类别重新测量，不能把这个后缀规划数当发送预算证明。

切点的用户轮次/轮内规则、manual 不切最新用户轮次、有效与无效 tool call 的配对规则保持。保留本身符合 K 不代表整个输入可发送，后续必须验证完整候选。

### 5.2 完整候选规划

每个候选先构造 `SUMMARY_PREFIX` 占位消息＋保留后缀，再带本次固定 system/tools 测量整份候选。S1 从本次冻结记录选择与候选媒体兼容的最新 agent 比例，按完整候选 E 计算 C；没有兼容样本时冷启动。内容删改不触发前缀匹配分支，也不把旧 A 直接加到候选上。

规划值为 `C_placeholder + S`。S 已经是生成 token 上限，不再次乘字符倍率；这只是尚未有摘要正文时的选切点额度，不是最终输入保证。

同时对该候选的实际 `_summary_request` 进行独立 summary 测量。仅当 `C_summary + S ≤ W` 且 `C_placeholder + S ≤ B`，候选在预算层可进入摘要；原有结构校验仍需全部通过。预算未知不能把已经超限的原输入变成可发送。

候选搜索沿原有顺序有界遍历，不为找到可发送输入而重复调用摘要模型。无可用候选按原 reason 失败，不截断用户内容、工具输出或安全约束。

## 6. 摘要独立计量与后验提交

摘要仍由 `_compaction_steps` yield `(bound_model, summary_request)`，`arun_summary_steps` 负责一次 `ainvoke` 后 send/throw。不要为校准新增另一个摘要执行器或改变 generator 的网络异常通道。

发送前固定 `summary_measurement` 与 summary sample_id：

1. 从最终序列化结果拆出 summary system、人类消息，以原模型身份测量，purpose='summary'，calibration_history=原 canonical messages。
2. 优先使用 S1 的同用途、同媒体最新 summary 样本；无样本时仅同模型纯文本 agent 样本可作为 inherited 先验。继承倍率不等于 summary 自身实测，也不改写 agent 样本。两种用途各自使用一个最新比例，不相互累计或取最大值。
3. 校验 `C_summary+S≤W`。超限抛 `ContextCompactionError('summary_input_exceeds_budget')`，摘要请求计数为 0。
4. 调用 `bind_model_output_limit(model,S)` 后 yield；输出 cap 绑定不改变模型/上游/适配身份，不使本次 measurement 与实际执行错配。

返回顺序为：先保留原 `_record_summary_finished` 观察，再执行原 AIMessage、tool_calls、内容类型、stop_reason、空白/截断校验；全部通过后才用局部 summary_measurement 生成 summary envelope。观察数据失败不能作为跳过必要摘要校验的理由。

复用现有 `context_summary_model_started` / `context_summary_model_finished` 安全日志，前者增加该次 sample_id、raw_input_tokens、calibrated_input_tokens；保留旧 summary_input_tokens_estimate 的原始 E 含义。后者沿现有 usage 字段记录 A，并携带同一 sample_id。发送前预测直接来自冻结 measurement，完成时不回算。无需新增事件类型或观察 hook；`_record_summary_finished` 只记录事实，不能拥有 complete_sample 或其持久化。S4据此配对摘要同次 E/C/A，日志失败不影响直接采样。

新建 summary HumanMessage 后调用 `carry_compaction_state(agent_measurement, summary_envelope)`；返回有效记录时写其独立 metadata。无有效 summary usage 时第二参数传 None，仍继承原记录中的最新样本槽，不能因为一次 usage 缺失丢掉过去的有效测量。已有 summary_envelope 时以它为唯一基底，不将旧记录合并回去。身份不可用、carry 返回 None 时不写假记录，继续既有未校准预算合同；不能仅因没有派生记录把原本有效的压缩变成失败。

此时才对 `[summary,*preserved]` 完整投影并测量 C_post。其校准历史是候选消息；有 carry 记录时，其 revision 高于保留 AI，避免旧副本夺回优先级；没有有效身份时继续 C=E。可选采样/carry构造失败时，以原冻结历史完成后验计量，不能因记录未附成功丢失已有agent倍率。`C_post≤B` 才通过；force 还要求 `C_post<C_original`，否则为 `compaction_not_reducing`。两者都使用唯一校准值，不允许一个用 raw、另一个用 calibrated。

原请求、候选和 post 的 agent 比率均来自本次固定 agent_measurement 所带的同一份旧样本记录；summary 新 usage 只更新 summary 槽，不能用摘要样本替换 agent 比例来证明压缩有进展。候选保持相同媒体时使用相同比例；媒体变化时按 S1 从这份记录选择兼容槽或冷启动，并记录 method/media。C 仍可能高估或低估，严格小于检查只是估值下降，不宣称实测减少量。候选循环复用本次不可变记录，不反复读取共享可变状态；不新增缓存服务。

采样和继承都只写候选消息；不就地改旧 summary 或保留 AI 的 metadata。候选失败、取消或摘要异常时，不影响原消息中的 revision 或最新样本。

提交边界保持三个原路径：

| 路径 | 校准状态何时生效 | 失败保护 |
|---|---|---|
| 主动压缩 | `prepare_request` 返回消息替换更新，图完成必要提交 | 必要保存失败继续失败，不以观察已发 completed 代替提交 |
| 手动压缩 | `compact_checkpoint` 两步更新并读回确认 | `ManualCompactionCommitError` 原样保留；失败模型头未替换不能变 ready |
| overflow 恢复 | retry 成功且响应门禁通过，ExtendedModelResponse 一并提交 summary 与响应 | retry 失败/门禁拒绝不提交候选；不单独保存 summary 样本 |

## 7. 正常与失败实例

### 7.1 工具结果触发主动压缩

W=128000、O=16000、R=24000，因此 T=104000、B=112000。最近 agent/text 样本为 E=45000/A=90000，因此 r=2；本次完整已投影请求原始 E_new=53000。

`prepare_request → measure_request` 得到 C=ceil(2×53000)=106000，因此触发。候选 summary E=30000，继承倍率得到 C_summary=60000，S=2000，允许摘要。摘要返回并通过校验后，完整主请求候选 E=18000，继续采用这次操作固定的 agent 比例，C_post=36000≤112000；图提交后发主请求，假设返回 A=38000，则新的 agent/text 样本替换为 E=18000/A=38000。摘要返回的输入计数只更新 summary/text 槽。

验收必须同时断言调用顺序是 summary→agent、发送内容确实替换、消息中的最新 agent/text 样本对应 18000/38000，且 provider 收到的请求体不包含本项目校准 metadata。

### 7.2 校准后发现摘要也放不下

原输入触发压缩后，最终摘要 C_summary=127000、S=2000、W=128000。`_compaction_steps` 在 yield 前抛 summary_input_exceeds_budget。摘要调用 0 次、主调用 0 次；原 checkpoint/校准记录保持，compaction 进入明确失败终态。

该失败是预算策略拒绝，不宣称 provider 必然拒绝，因为 C 仍可能高估。本片不为该场景自动分块摘要。

### 7.3 overflow 的独立配对

第一次 agent 请求 I1 使用局部 measurement M1，provider 明确 overflow，没有新样本。恢复摘要 I2 使用 M2，只更新候选 summary 样本槽；retry I3 使用压缩后输入 M3，返回 A3，则样本必须为 E3/A3，不能为 E1/A3，也不能加 I2 的 usage。

原逻辑 call_id 可以贯穿 I1/I2/I3；sample_id 必须分别唯一。工具执行次数保持 1，不重放整个 Agent run。I3 再失败时没有 I4，候选 summary 不提交。

### 7.4 最新比例立即向上或向下更新

第一轮 E=1000，无样本时发送前 C=1000；完整返回 A=4000 后保存 r=4。第二轮完整 E=1500，发送前 C=6000；返回 A=5400 后该槽替换为 r=3.6。第三轮完整 E=2000，发送前 C=7200；旧比例4不再参与决策，也不存在另一个7400的预算值。

第三轮若返回 A=9000，下一请求改用 r=4.5；若返回 A=1600，下一请求改用 r=0.8。缺 usage 则保留上一有效 r=3.6。无固定最小倍率或近期最大值阻止向下更新，整个更新只影响后续请求，已发送快照保持原 C。

## 8. 降级与异常合同

| 情况 | 处理 |
|---|---|
| 缺少新 usage/无效完整样本 | 不替换样本槽；已有兼容历史继续用于下轮 |
| 首次总量测量 unavailable | 沿 Spec118 显式记录 unverified，普通请求可继续；force 明确失败 |
| 已知 C_original 超预算后候选测量 unavailable | 失败；不能返回原输入或当作 0 |
| 采样附 metadata 失败 | 保留模型成功/原异常，记录固定 reason；不改预算配置 |
| 摘要 transient/空白/截断 | 沿原 fallback 条件，且必须 C_original≤B；force 不允许 fallback |
| 无安全切点但原 C≤B | 普通软触发可沿原 no_safe_cut_within_send_budget 返回；manual/force保持原各自合同 |
| 必要输入投影或限额配置失败 | 原异常传播，不能降级成 calibration unavailable |
| 校准后 C 与当前实际 window 配置不符 | 不自动推断/修改窗口；记录数据供现场核验 |
| unsupported response_format/server-side request shape | 不建立新样本，不借历史倍率宣称该形态已准确覆盖 |

## 9. 文件与迁移范围

| 文件 | 具体修改 |
|---|---|
| `agent_runtime/model_input.py` | async 主链局部测量、实际 AI 响应采样；删除不可达旧同步 override，保留 handler 返回形状 |
| `contracts/run_context.py` | ModelCallState默认可空context_measurement；PreparedRequest默认可空response_format；无需改checkpoint schema |
| `agent_runtime/__init__.py`、`call_observation.py` | 已有graph response_format显式传入内部模型准备对象；公开build签名不变 |
| `agent_runtime/context_compaction.py` | 替换所有预算分支；summary 采样和候选状态继承 |
| 条件新增 `agent_runtime/context_compaction_inputs.py` | 就地替换后 compaction 文件物理行数>500才执行下述确定拆分；≤500不新增 |
| `agent_runtime/request_preparation.py` | 保持固定配置的计量入口；将 PreparedRequest.response_format 显式传入压缩规划 |
| `agent_runtime/context_recovery.py` | `_begin_recovery` 显式传当前 request.response_format；既有单次强制压缩/retry 算法不变 |
| `agent_runtime/context_observation.py` | preflight 消费本次 C，移除独立决策粗估；snapshot v2 下一片完成 |
| `tests/test_context_calibration_runtime.py` | 新增请求/响应配对、观察边界与真实 async 图接线测试 |
| `tests/test_context_calibration_compaction.py` | 新增摘要/切点/后验/降级矩阵 |
| 既有上下文/恢复/手动压缩测试 | 保留并扩展原断言；迁移已移出的私有 helper 导入 |

优先就地替换并复用 S1。若 `context_compaction.py` 最终物理行数>500，则将既有 SUMMARY_MARKER/SUMMARY_PREFIX/SUMMARY_SYSTEM、`_validate_removed_prefix`、`_serialize_text_history`、`_summary_request` 精确迁到 `context_compaction_inputs.py`，同步测试导入，不改变序列化文本或安全规则；≤500不拆。这是执行明确行数条件，不要求实施者重新选择模块方案。`context_summary_io.py` 继续只拥有摘要 IO 驱动，避免混入纯构造/规划职责。迁出后删除原定义，不加兼容转发。

`context_recovery.py` 必须同步实际 response_format 参数；`manual_compaction.py` 的正式入口保持 response_format=None，两者的算法均不新增路径，其既有行为用集成测试验收。未增加新锁；本片不得创建共享 mutable 校准对象。

## 10. TDD 与验收矩阵

先写以下行为测试并在未接线代码上确认预期红灯。缺模块/import 错误只证明接口未实现，不能替代“返回 usage 不影响下轮判断”的行为红灯。

| 用例 | 初始红灯原因 | 转绿后的必要断言 |
|---|---|---|
| `test_returned_usage_calibrates_next_preflight` | 第二请求仍使用原始字符估算 | 上轮 E/A、当前完整 E、C 和压缩调用次数全部一致 |
| `test_calibration_ratio_updates_both_up_and_down` | 当前没有直接反馈校准 | 4→3.6 后 C=7200，随后4.5或0.8立即用于下一请求；无近期最大值或下限1 |
| `test_each_attempt_pairs_own_measurement_and_usage` | 没有请求级样本或重试配错 | I1/I2/I3 sample_id 唯一，I3为E3/A3，summary不混入agent |
| `test_observation_failure_does_not_disable_sampling` | 采样尚不存在 | snapshot发布/用量持久化故障后仍生成下一轮可用样本，主结果不变 |
| `test_snapshot_uses_sent_measurement_before_sample_update` | preflight独立计数 | 发送前 C 不被响应 A 改写；retry不改旧局部快照 |
| `test_async_graph_reaches_input_calibration` | 正式 async 图的 ModelInput 只投影 | 经已有准备/门禁实际发出请求，返回样本被下一请求使用，异常身份保持；无新增同步入口 |
| `test_nonempty_response_format_disables_prepare_and_retry_calibration` | 主动压缩阶段没有格式事实 | prepare、overflow和retry均不复用旧agent倍率；既有ToolStrategy结果及配对ToolMessage保持 |
| `test_summary_guard_uses_calibrated_input` | 摘要只用raw通过 | 超预算在yield前失败，模型0次、原消息与revision不变 |
| `test_final_candidate_guard_uses_calibrated_input` | post仅用raw放行 | 摘要可完成但候选超预算时不提交、普通模型0次 |
| `test_compaction_carries_latest_samples_once` | summary无继承状态 | carry revision高于保留AI，读取最新记录而非并集；旧比例不能复活，样本槽不增加 |
| `test_summary_usage_missing_preserves_agent_sample` | 无继承状态 | None usage不清空agent槽，下一请求仍用最近有效比例 |
| `test_summary_usage_updates_only_summary_sample` | 用途未接线 | summary替换自身媒体槽，agent槽及其模型报告证据不变 |
| `test_compaction_without_identity_uses_uncalibrated_path` | 无可空carry合同 | 没有有效identity时不写假记录，C=E仍沿原预算门禁；不因派生metadata缺失破坏已成功摘要 |
| `test_keep_recent_uses_calibrated_projected_suffix` | K只按裸估算选择 | 完整Human起点后缀重新投影；image→text时选原text槽或冷启动，不能沿用image比例；工具配对和最终整请求保护保持 |
| `test_fallback_and_force_reduction_use_same_calibrated_value` | 边界仍比较raw | 软fallback只在C≤B；force后C未下降则不重试 |
| `test_cancel_or_commit_failure_keeps_calibration_state` | 候选未明确原子携带 | 无提前修改原消息，手动失败头和必要保存合同保持 |

普通路径还需显式断言事件顺序、模型/工具调用次数、必要消息更新和观察降级。故障注入用记录器记录故障，再在测试末断言，不能把断言放入会被生产观察隔离吞掉的 callback。并发/取消用 Event 或明确状态同步，不用极短 sleep。

复用现有 FakeMessagesListChatModel、ToolCapableFake、native_application_helpers 和 SDK MockTransport。无需改 conftest；若 fixture 必须补齐模型校准 metadata，使用 S1 正式 model factory 或在新测试文件局部构造，不能给所有测试偷偷注入伪真实样本。

实施后的命令如下，本次文档编写没有执行这些功能测试：

```bash
.venv/bin/python -m pytest -q tests/test_context_calibration_runtime.py tests/test_context_calibration_compaction.py
.venv/bin/python -m pytest -q tests/test_context_compaction.py tests/test_context_budget_degradation.py tests/test_context_overflow_recovery.py tests/test_manual_compaction.py tests/test_async_context_chain.py tests/test_context_compaction_fallback.py tests/test_context_operation_boundaries.py tests/test_runtime_middleware_contracts.py tests/test_model_request_context.py
.venv/bin/python -m compileall -q naiyue_test_agent
git diff --check
```

对生产目录 `rg -n 'count_tokens_approximately' naiyue_test_agent/agent_runtime`，逐个解释剩余调用：本片所有普通 Agent/compaction 决策只能走统一测量；原始组成统计允许保留。自动权限审查、长期记忆等独立用途不纳入本片。

## 11. L1/L2/L3 完成门禁

- L1：所需字段及测试存在；达到拆分条件时纯输入模块存在，否则不创建空文件；所有修改 Python 文件≤500行。
- L2：局部 measurement 由实际请求生成，实际 response input usage 产生样本；没有伪造、占位、裸倍率默认成功或窗口静默改写。
- L3：普通根/子请求、主动摘要、手动摘要、overflow retry 都经过其规定的计量路径；样本随现有必要提交保存并在下一请求被消费。SDK request fixture 证明校准 metadata 没有进入供应商输入。

专项全绿后按总 Spec/S4 运行项目要求的后端全量和标准构建，结果未完成前不能声称本片实现完成或提交。每片交接记录行为接线、实际命令与结果、已删除旧计量调用、保留调用方和唯一下一步。

本片通过只证明本机确定性链路与错误合同，不证明目标 GLM 的真实 usage 语义或摘要质量；真实模型验收由 S4 单独记录。

## 12. 本地实施记录（2026-11-16）

已接线ModelInput实际异步请求的measure→handler→complete；测量保存在wrapper局部，ModelCallState只提供观察引用。采样附在唯一返回AIMessage，原包装和外层响应门禁保持。PreparedRequest追加默认response_format，graph固定值沿prepare和overflow传递。删除旧同步ModelInput override；当前factory将select_model的before节点注册为sync=None/async=abefore_model，生产和脚本没有独立同步ModelInput消费者。

正常链：select_model.abefore_model→prepare_request→_compaction_steps用C选切点/规划→summary IO→校验/采样/carry→完整候选后验检查→图提交；实际发送经ModelInput→observe_model_request→模型，观察finally结束后才采样，外层_accept门禁通过后随原消息保存。失败链：summary输入超限在yield前拒绝；候选超限不提交；overflow原请求失败无样本→摘要候选→retry独立E/A→门禁通过一并提交，重试失败沿原异常路径且不重放工具。

全量架构测试发现原Spec的TYPE_CHECKING反向引用仍违反静态分层规则，故将S1现有两种只读数据类型原样迁到contracts/context_calibration.py；核心重新导入同一类型，无重复schema、转发函数或新抽象。没有新增执行器、数据库/锁/配置。context_compaction.py为477行，未触发条件拆分；保留summary纯构造/校验函数原位置。所有compaction决策粗估已迁移到measure_request；context_usage剩余计数用于v1快照/组成展示（S3迁移），权限审查计数是独立用途。preflight已经消费发送前冻结C，保留E字段。

TDD首批5项在旧主链确认行为红灯（缺采样、未校准摘要预算、未触发校准压缩），最小实现后转绿；后续增加边界回归共19项。旧故障注入迁移至context_calibration计数器或统一measure_request边界，保留异常身份/不提交断言；SimpleNamespace夹具补齐实际ModelRequest的response_format。旧“已知候选放不下仍调用摘要”用例依本片候选前置门禁收紧为summary调用0次，保留主请求0次/原checkpoint/失败事件断言。未改conftest。

summary采样/carry构造失败时后验仍使用原冻结历史里的agent比例，不允许记录附加失败让预算冷启动变小。真实SDK MockTransport证明下一请求正文未包含校准metadata；它不是供应商网络实测。

最终后端两分片3370＋3371＝6741项全部通过，0失败/错误/跳过。最终专项243项通过（含新增19项）；前端78文件681项、标准macos-arm64 wheel构建、10份生产文件包内源码一致性、compileall/pip check/diff检查通过。

第一轮全量因TYPE_CHECKING反向依赖失败，调整共享类型后重跑；后续分片1发现旧preflight注入入口，迁移夹具后该分片完整重跑，失败/中止结果不计通过。最终证据build/spec143bl-s2-final-backend-0.xml、build/spec143bl-s2-final-backend-1-recheck.xml、build/spec143bl-s2-regression.xml、build/spec143bl-s2-final-build.log、build/spec143bl-s2-verification.json。wheel包含当前并行工作区改动，非隔离发布产物；真实配置/依赖/数据库未改，未真实调用供应商或提交发布。

2026-11-16审查修复：初始切点只移除旧摘要时不构建无新历史的摘要请求，转入既有后续安全切点搜索；所有候选的配对校验、摘要输入预算和最终发送预算继续生效。自动与force路径两项回归先得到no_new_history_to_summarize红灯，再修复转绿；相关255项通过，标准macos-arm64 wheel构建成功，日志build/spec143bl-s2-cut-fix-build.log。未执行全量后端或真实供应商验证。
