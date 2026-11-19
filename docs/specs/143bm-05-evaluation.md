# Spec143BM-S5E：两维记忆的基线与效果评测

日期：2026-11-16；状态更新于 2026-11-17。**后台驱动和旧/新首轮真实校准已有结果，完整人工评分与效果验收仍未通过；前台驱动已由 S7 实现并完成首轮真实校准，五项评测问题待修。** 原协议及阶段记录保留，尚缺的评分/覆盖/成本、当前后台新批次和两入口 validation/holdout 统一归入 [S8 第 14 节](143bm-08-memory-tool-action-contract.md#14-s0s8-未完成事项统一对账与关闭标准)，不再另排 S5 收尾支线。下文“当前七类”“前台待实施”等是方案编写时的历史背景，实际状态以本段和 S8 为准。
实现合同：[S5 空间与时间两维记忆](143bm-05-space-time-memory.md)。旧协议：[S0](143bm-00-evaluation.md)。

## 1. 为什么先写 Spec，再做实现和评测

当前代码还是七类、user 优先三条、默认部分类型不过期。现在可以验证评分器或测旧链基线；不能用旧链的结果回答“两类＋个人长期约定预加载是否更好”。
正式顺序为：冻结新标签/评分器 → 保存可重建旧源码并测旧基线 → 实现 S5 → 同协议测候选 → 逐事实审阅和成本对照。
可以先做纯 prompt 小实验，但它不能替代真实保存、过期、类型变更、权限、预加载和最终回答的联合验证。本片优先复用已有工具完成闭环，不新建评测平台。

## 2. 已有能力和必须补齐的地方

| 现有入口 | 可复用 | S5 必须补齐 |
| --- | --- | --- |
| scripts/evaluate_memory_optimization.py | 源码 manifest、固定工程样本、报告与对照 | 新协议入口；不能把新旧产品行为差异自动视为测试回归 |
| scripts/probe_memory_semantics.py | 原模型配置、RequestBudget、真实 MemoryJobs、阶段输出、独立报告 | 新 suite、两类标签、旧项种子、后续提问和最终回答；前台单独入口 |
| scripts/memory_evaluation_cases.py | 文件哈希、family 分组、预算、独占创建报告 | format=2 的明确校验分支；保留 format=1 原验证，不放宽旧数据防线 |
| scripts/memory_recall_evaluation.py | 真实库、prepare_memory_parts、注入 ID、只读检查 | 两维组合、完整个人组、超预算、旧类→新类迁移后的注入 |
| scripts/memory_worker_evaluation.py | 受控 A/B 驱动真实后台与保存断言 | 时间类别更正、跨 kind 冲突、无重试合同 |
| scripts/review_memory_evaluation.py | 报告哈希、逐事实覆盖、危险激活阻断 | 正文匹配后再评范围/时间，不先以 kind 筛掉已保存事实；最终回答评分 |

旧 run1/run2 是 8 条校准的历史证据，不能覆盖、重新改标签后冒充原报告，也不能充当新留出集。当前八条抽取和八条召回校准不是完整 S0 通过证明。

## 3. 新协议与样本文件

新增固定合成资产目录 `tests/data/memory_space_time/`：fixtures.json、protocol.json、rubric.md；这些是计划路径，本文编写时不创建伪评测结果。
protocol 含 protocol_id、format=2、样本/评分规范哈希、split/family 划分、模型有效设置指纹、预算、门槛和报告 schema 版本。原 `memory_optimization/` 保留其冻结协议。

### 3.1 最小矩阵

真实模型数据先采用 24 个独立问题家族：calibration/validation/holdout 各 8 条。每个 split 覆盖以下事项，近重复变体不得分到不同 split：

| 事项 | 必须区分的错误 |
| --- | --- |
| 明确通用的协作方式 | 应为 personal＋convention；后续没有相同关键词时仍能遵守 |
| 只在某项目成立的抽象要求 | 不得因抽象/“以后”扩大为 personal |
| 持续项目选择 | workspace＋convention；不能挤入个人预加载 |
| 跨项目但有阶段性的个人限制 | personal＋context；不能因为是个人记忆变成长期 |
| 临时项目事实/否定与条件 | workspace＋context 或明确不应记忆；限定不能消失 |
| 阶段事实被新用户消息确认为长期 | 真实旧项＋同 ID 新版本；不可因改类另存冲突项 |
| 重复、部分确认、旧来源缺失 | 不无证续期、不恢复已清理来源、不凭缺失推断无旧项 |
| 多事项及无证概括 | 逐事实分母；没有新记忆也可能是正确结果，不能强制每例写入 |

每条包含 source、scope/time 标签、允许的旧种子、followup_query、必须/禁止出现的最终回答行为。前台写入使用同一事实定义，但独立记录入口与调用预算；不把两个入口结果混合成一个准确率。
离线矩阵另外覆盖实现 Spec 的 T01–T14，尤其是 0/1/4/超过 max_limit 的个人记录、刚好预算边界、读截断、权限、坏源、故障、混合版本及迁移；不花真实模型预算验证纯机械边界。

### 3.2 逐事实标签

每个 expected_fact 至少包含：fact_id、must_extract/must_activate/must_inject、must_not_activate、语义主张、正式来源位置、必要条件、允许 scope、允许 time_kind、预期关系/旧项、禁止扩大范围/延长时效、followup 的可观察要求。

- 首先人工或确定性事实标识把输出对应到事实，然后分别判正文、范围、时间、状态与注入；不能先按 expected kind 过滤输出，再把未匹配当“没证据”。
- 旧链没有新两类字段，时间分类准确率记 not_applicable；报告旧 kind 和实际期限。不能把七类转换成标签后宣称旧模型正确完成了两类分类。
- 新类政策检查和新旧共同业务指标分开。基线不满足新政策属于待比较行为，不是报告结构损坏；新候选违反新政策则不通过。
- 预算不足的 fallback 用例标明“不保证全量注入”；不能一边声明允许退回检索，一边给全部个人约定打 must_inject。预算充足的充分证据正例要有必须命中标签，防止“全部丢弃”取得零错误的虚假通过。
- 暂时不用出现的模型内部推理作为评分目标；评估可观察输出，如是否先陈述证据、是否保留 CI 条件、是否杜撰当前环境。
- 每个 seed 的创建材料显式进入 fixture。普通种子走真实生产准入/提交；故意畸形或旧格式边界只在离线 fixture 中构造并注明用途。

## 4. 旧基线与候选的隔离

1. 先改评测器和合成标签，不改生产算法；新评测器能够驱动旧链和新链，不能重写旧链 prompt/schema 来让它表现得像候选。
2. 保存 HEAD、相关 tracked/untracked 源码的安全快照及清单/哈希；当前工作区有未提交实现，只有 git SHA 或 diff 哈希不够。
3. 快照只含运行记忆链和评测必需的代码；不复制真实配置、凭证、用户库、日志或 node_modules。模型配置以既有路径只读使用，报告只保留安全参数和身份。
4. 重放旧链用隔离源码目录/独立进程，不切换或重置当前 dirty 工作区，不在正式生产链留下 baseline/new 开关。候选不能悄悄 import 当前修改后的模块作为“旧版本”。
5. 两边使用相同源消息、种子语义、后续问题、配置预算和模型有效参数；只有产品算法/类型合同/新预算政策允许不同并写进 manifest。
6. 同一原始事实的旧链种子用该旧 schema 的合理类型，新链种子用新 schema；映射只服务测试构造，单列迁移场景真实执行升级，不能拿种子适配代替迁移验证。
7. 供应商模型后端若无法固定，记录模型标识、采样日期、参数和不确定性，不将结果差异全归于代码。

## 5. 执行路径与预算

### 5.1 后台主批次

```text
临时项目/个人库＋真实消息准入
  → 可选旧种子 → MemoryJobs 一批 A/B → 真实存储结果
  → 新的后续提问 → 产品 prepare_memory_parts 与 compose_model_prompt 请求装配
  → 一次真实模型回答 → 保存注入材料/实际回答 → 逐事实审阅
```

测试配置 max_batches=1；自动触发由测试控制，不能让后续提问意外再启动后台整理。每例最多 A 一次、B 一次、后续回答一次，即≤3 次逻辑调用；零提议时 B=0。8 例一次完整批次≤24，repeat=1。
后续问题选择无需工具执行的解释/分析任务；若模型意外要求额外工具轮，按预算终止并记录未完成，不能悄悄加调用或编造最终答案。若该需求合理，应修订下一版协议而非补跑本例。

### 5.2 前台独立批次

以真实应用/Agent 工具执行链调用 memory record/update，确保必要来源保存和权限检查实际经过；不能直接调用 commit 冒充前台模型行为。
每例最多两次写入任务模型调用（提出工具调用、读取工具结果后结束）＋一次后续回答，合计≤3。由测试关闭自动后台整理，B 调用数应为 0。
改类限制用例预期明确拒绝且旧记录不变；不能给它同时标“前台必须改类成功”。若模型在允许调用数内不完成，记录失败，不换任务提示后补跑。

### 5.3 运行顺序

1. 评分器与离线硬边界先通过；保存旧 calibration 和可重建基线。
2. S5 实现后，先只跑后台 calibration：旧/新各≤24 次逻辑调用，完整对照≤48。
3. 审阅结果后决定是否修改实现。前台 calibration 是另一个独立完整批次，每个版本≤24，不与后台混算。
4. 有效方案才进入 validation；冻结代码/提示/设置后，隔离旧链与候选分别运行 holdout。
5. 两个入口、三个 split、两个版本全部各执行一次时，理论总上限 288 次逻辑调用；按阶段执行，不因为本文存在就一次全部发出。

所有调用使用用户此前配置的模型，通过现有模型配置解析，实施时核对有效 model_id，代码和协议不硬编码模型名/URL/凭证。不擅改采样参数、SDK 重试或真实配置。
业务失败不修复重试、不补跑失败子集。修订后运行新的完整批次，保留旧报告；计划的独立新实验不伪装成原任务恢复。
逻辑调用、实际传输 attempts、token 和耗时分别报告；缺少 attempt 观测记 unknown，不能记 0。

## 6. 评分和门槛

| 指标 | 计算与解释 |
| --- | --- |
| 内容支持率 | 被原始证据支持的保存主张 / 全部保存主张；未保存与保存错误分开 |
| 个人范围精度/召回 | 正确 personal / 实际 personal；应 personal 且保存正确 / 应 personal，不只看数量 |
| 时间分类准确率 | 新候选对应事实的正确 convention/context；漏提取另列，不偷换分母 |
| 错误长期化 | 阶段事实被保存成 convention 的绝对数＋相关分母 |
| 有效记忆召回 | 正确正文/范围/有效状态覆盖 must_activate / 全部 must_activate |
| 注入召回与干扰 | 必须出现的有效事实是否进入真实请求；不相关/过期/其他项目注入数量 |
| 后续任务行为 | 实际回答是否满足可观察要求、保留限定、避免杜撰；与只看注入分开 |
| 生命周期 | 同 ID/版本/TTL/失效/whole 变更与预期一致；读取写入计数 0 |
| 成本 | 每入口/阶段的调用、输入输出 token、耗时、读取次数、预加载大小/失败原因 |

工程门槛：T01–T14 和原有测试全部通过，构建通过；未知引用/权限越界/旧源复活/无证续期/漏接线等硬边界错误为 0。
语义安全门槛：本次观测中的无依据激活、范围扩大、错误覆盖、否定反转、错误长期化为 0；出现任一例就不宣称方案质量通过，保留报告分析。有限样本零错误不是全局零风险证明。
收益门槛：预算充足的关键个人约定必须成功注入；新候选的最终有效记忆召回和后续任务通过数不得低于同样本基线，所有新增失败逐例解释。8 条的首轮结果只支持继续/调整的判断，不作统计显著性或生产普适声明。
避免无记忆“取巧”：充分证据正例 must_activate/must_inject 的遗漏必须计入，不用低错误率掩盖全部拒绝。
成本门槛：不突破预先冻结的调用/注入/读取上限；报告候选相对基线的成本增减，不把耗时下降自动当成语义改善。

人工审阅必须覆盖每 case、每 expected_fact 和每危险新增主张，说明 reviewer、理由及对应输出。助手初审可以辅助定位，不能冒充用户人工确认；若未完成人工审阅，semantic_status 保持 pending。
格式/调用失败既保留失败率也保留可审阅材料，不能只对成功子集计算总体成功率。报告结构无效和“合法返回但业务错误”须分开。

## 7. 脚本合同与运行示例

复用原入口；已增加 `--suite space-time`（当前仅后台）。前台接线时再增加 `--entry background|foreground`；当前不得传入尚不存在的 --entry 参数。`--config/--model-id/--split/--repeat/--fixtures/--protocol/--output` 保持原意义。
新的 format=2 样本必须明确支持上述入口与≤24预算；原 suite 的固定平衡校验和二阶段预算不随新入口放宽。
前台驱动若会使原脚本超过 500 行，才拆出由该入口调用的 `scripts/memory_foreground_evaluation.py`；已有 MemoryJobs 驱动继续复用，不依赖 tests 中的产品替代实现。

以下后台命令已接线，当前不含前台驱动；`MEMORY_MODEL_CONFIG` 由操作者指向既有模型配置，不能填入或打印密钥：

```bash
.venv/bin/python -m scripts.probe_memory_semantics \
  --config "$MEMORY_MODEL_CONFIG" --suite space-time \
  --split calibration --repeat 1 \
  --fixtures tests/data/memory_space_time/fixtures.json \
  --protocol tests/data/memory_space_time/protocol.json \
  --output build/spec143bm-s5/candidate-background-calibration-01.json

.venv/bin/python -m scripts.review_memory_evaluation \
  --report build/spec143bm-s5/candidate-background-calibration-01.json \
  --reviews build/spec143bm-s5/candidate-background-calibration-01-reviews.json \
  --output build/spec143bm-s5/candidate-background-calibration-01-reviewed.json
```

基线命令在保存的旧源码环境执行，并用独立 baseline 文件名；不能在新生产代码上加一个“baseline”标签冒充旧算法。输出必须独占创建，不能覆盖报告或复用旧哈希。

## 8. 交付和下一步判断

交付必须包含：可重建旧快照清单、新旧 manifest、冻结样本/协议、离线结果、完整真实报告、逐事实审阅、注入材料与最终回答、费用/耗时/调用数和明确未完成项。
结果按三层陈述：工程是否正确接线；模型在本批如何判断；后续任务是否获得收益。不能将模型连接成功、active 记录增加或测试全绿替代后两层。
S5 通过不等于 S0 全 96 场景及其他历史待办已全部通过。发生退化时优先定位来源支持、scope、time、预算退回或普通检索，不靠加隐形类型、改标签或挑成功样本掩盖。
真实库采用与发布仍是后续独立操作；本协议不包含自动部署、清理旧记忆或改用户设置。


## 9. S5-0 首步执行记录（2026-11-16）

- 已建立24个独立family的合成样本（每split 8条）、正式证据位置、旧种子、后续问题和可观察回答要求；不是语义效果验收结果。
- `load_suite` 对format=2走独立严格校验，format=1原校验保留；新报告人工评分必须提供范围、时间、错误长期化和后续回答判断。
- 新测试先观察缺失资产失败；评分新增门禁单独观察“缺字段未拒绝”的预期失败，再实现并转绿。专项27项通过；记忆回归491项通过（19.06秒）；标准Mac wheel构建退出0、Twine通过，compileall及diff检查通过。
- `build/spec143bm-s5-preflight/baseline-source.tar.gz`和baseline-manifest.json保存1353份后端源码、测试、合成资产及依赖声明，归档内逐项哈希已核对。未包含用户配置/数据库/日志；运行所需解释器/已装依赖由独立环境提供，不宣称已完成隔离重放。
- 该快照在生产逻辑修改前取得；真实模型调用为0。评分器完整逐维指标和后台整理→后续真实请求/回答的驱动仍待接线，因此S5-0未完成，尚不能运行文中space-time命令。
- 当前样本内容已由作者用于资产校验，尚未运行留出结果或用其调参；一旦后续用留出失败指导修改，就必须更换留出集。


## 10. S5-0 后台接线（2026-11-16）

- 新增 `scripts/memory_space_time_probe.py`，由原语义探针实际调用；旧种子使用正式来源保存和MemoryInput提交，旧来源缺失只在临时合成库注入，不触碰用户库。
- 后台整理限定新消息范围，防止旧种子被当作本轮新确认；随后使用产品prepare_memory_parts、compose_model_prompt和一次直接模型调用观察回答。该驱动覆盖请求材料与模型回答，不覆盖完整交互host、流式UI或工具循环，不能称完整用户会话验收。
- 离线已验证A/B/回答恰为3次、个人记忆实际进入第三次请求、召回写入0、真实来源缺失及种子身份；范围/时间/回答评分按case汇总，原逐事实提取/有效/注入指标继续保留。
- v2旧链报告明确policy=baseline，时间分类可标null且分母为空；候选仍必须有布尔判断。评分是否匹配事实仍依赖人工，不自动将同字面视为正确概括。
- 隔离目录build/spec143bm-s5-baseline/replay-01/source来自冻结快照，只覆盖新评测脚本/测试；549份产品Python源码逐项哈希核对一致，实际模块路径和离线重放通过。不是在当前生产代码加baseline开关。
- 真实后台calibration使用此前配置的heiyu-gpt-5.5、repeat=1、8例≤24逻辑调用；报告独占创建，失败不补跑。有效配置仅只读加载，无供应商配置或用户库修改。

- 本轮真实结果：8/8执行，后台completed=3、partial=5，21逻辑调用，后续回答8份。02/04/06在comparison_query阶段失败，07在comparison阶段失败，08为批次失败；失败原因尚待离线定位，不猜测成模型业务拒绝。原报告退出1，未补跑。
- 01的personal/user有效记忆进入后续请求；03的pytest规则、05的临时网关已保存但未召回。其余失败/遗漏详见build/spec143bm-s5-baseline/triage.json（助手初审，不是人工业务验收）。本轮说明还需观察查询/装配，不能将退化全归于类型。
- 工程验证：38专项通过；记忆回归494通过，加最后两项旧种子测试通过；标准Mac构建及Twine通过；compileall/diff检查通过。真实模型业务通过与否不由工程结果替代。
- 数据/代码/调用证据见background-calibration-01.json、evaluation-input-manifest.json、replay-01/replay-manifest.json和verification.json。原报告保留，未重写评分或补跑失败案例。SDK实际attempts未完整观测，不将21逻辑调用等同21次HTTP请求。


## 11. 旧基线离线定位（2026-11-16）

复现命令：`.venv/bin/python -m scripts.diagnose_memory_space_time --report build/spec143bm-s5-baseline/background-calibration-01.json --output build/spec143bm-s5-baseline/offline-diagnosis-02.json`。输出文件须不存在；真实模型调用0，7例能用已解析阶段输出重放，08原失败响应未存不可精确重放。诊断模型输出是脚本重放，不是新真实采样或失败补跑。

| 现象 | 已验证根因 | 后续处理 |
| --- | --- | --- |
| 02/04/06显示comparison_query_failed | bind_extraction在查询前拒绝identity_terms的对象数组；Extraction要求字符串数组，实际为anchor/term对象。离线cause链为Pydantic string_type | S5实现时明确A同源schema与字符串/技术锚点要求；不自动把对象修成字符串、不增加修复调用 |
| 07部分确认更新失败 | B在partial补充中更改原正文和subject，编译门禁以partial_confirmation拒绝 | 保留保护；失败不是旧库丢失或源引文自动恢复，不能放宽断言 |
| 03/05成功存储但未注入 | 真实普通search返回0；现有feature覆盖率分别3/11≈0.2727和0.3，均低于fallback_coverage=0.5，且无完整字面匹配 | 作为检索限制保留对照，不在S5类型简化中降低全局阈值；时间/分类调整不能被宣称已修复这类召回 |
| 08批次解析失败 | 回调仅存parse_error_type，没有失败原文/错误码；无法区分JSON/schema/截断原因 | 本次只修诊断：以后保留阶段、固定错误码和经过敏感过滤的可见text，不保存推理块；不重新请求08 |

A文本提示中“object anchors”存在对象身份/JSON对象的歧义；其完整schema没有像B一样附在实际text模式请求中。该事实解释为什么需要完善表达，但单次样本不能证明加schema一定消除格式错误。Windows/ruff这类普通词也不能自动成为目前lexical.anchors允许的技术身份锚点，S5须明确可省略identity_terms，不能靠照抄普通名词绕过校验。
诊断脚本只接受标synthetic且suite=space-time的报告，不读用户库，不修改生产函数；临时patch仅用于观察异常cause链与真实查询返回数。旧报告及快照保持原样。


评测失败处理修正（2026-11-16）：后续回答的模型异常或意外工具请求保存为followup.status=failed，保留已完成A/B、任务、记忆、请求与注入材料，不补调用；整批structural_failures包含该失败。format=2在case总数及评分身份/逐事实覆盖完整时允许审阅partial报告，输出engineering_passed=false且overall保持失败，CLI非零退出；缺case仍拒绝，format=1旧门禁保持。异常只记录类型及固定错误码，不写provider异常正文。


S5-3首轮候选calibration（2026-11-17）：沿既有.env/models.json默认heiyu-gpt-5.5，8/8执行，24/24逻辑调用，无补跑。任务7 completed/1 partial，对比冻结旧基线3/5、21逻辑调用；合计case耗时271541ms对188700ms，仅为本轮观测不作代码因果归因。原候选报告structural_failures=2、退出1，完整保留。06为评测器漏登记显式旧种子原文的来源误报，新增回归先红后绿后离线重核来源通过，不改原报告、不请求模型；07 partial是模型在partial确认中扩写正文，被既有门禁拒绝，旧记忆保留。01个人convention注入1，其他7例注入0；02 A和实际B输入均为workspace，但B以缺跨项目依据误拒绝导致漏存（2026-11-17复核更正）；03/04/05/06/08虽有保存但未召回，不能宣称效果验收通过。完整人工评分、前台及留出集仍pending。证据build/spec143bm-s5-fixes/{candidate-background-calibration-01.json,comparison.json,input-manifest.json,probe-source-at-run.py,verification.json}。保存的执行时探针源码与输入清单哈希一致；之后仅修正已记录旧种子的评分输入，原始实验身份保留。

2026-11-17 评测审查修复合同：报告中的消息内容仅保留可见 text/input_text/output_text，递归移除 reasoning/thinking/签名及加密块；回调与followup统一通过safe_report处理。模型身份复用已构造适配器的校准身份，以白名单保存模型/API和参数指纹、SDK重试配置及本次MemorySettings指纹；未提供的配置记未知，不反填历史。报告绑定实际protocol/rubric哈希；源码manifest纳入实际评测驱动与模型配置消费者。新增回归先红后绿，不调用模型、不覆盖旧报告。召回策略优化仍按独立方案推进，本修复不降低覆盖阈值。

修复验证：5项新增回归（原3缺口先红后绿），记忆专项与构建结果见 build/spec143bm-report-fixes/verification.json。既有模型适配器身份只读验证通过；真实请求0。历史报告不回填、不覆盖，后续新批次才消费完整身份信息。
