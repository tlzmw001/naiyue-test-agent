# Spec143BM-S0：冻结基线、分阶段评测与正确性门槛

日期：2026-11-16。状态：用户授权编写详细 Spec；**评测资产及新探针尚未实现，本轮未请求真实模型**。所属[总规范](143bm-memory-optimization.md)。

## 1. 本片要回答的问题

优化后保存更少，可能是去掉了错误记忆，也可能是漏掉了正确事实。保存更多，也可能只是多存了重复或不受证据支持的内容。本片先定义事实标签、实验分母和失败归因，再修改算法。

核心原则按用户确认冻结：允许整理失败、允许遗漏，错误记忆尤其错误覆盖的代价更高。不得通过降低来源/权限门禁、把失败内容转成 candidate 或反复调用模型提高表面完成率。

本片交付的是可重复的基线和评测工具，不是生产效果结论，也不授权改真实用户库。正式数据全部为明确标注的合成样例；实际用户材料只有经另行明确授权并脱敏后才能加入新版本样本集。

## 2. 已有能力与复用边界

已核对当前仓库：

- `docs/research/memory00/` 有冻结 fixtures/protocol、哈希检查、SQLite 词法实验、分校准/验证/留出的既有机制；旧研究实现不被生产导入。
- 00c/00d 的历史报告已记录部分质量门槛未过。不能用新脚本重新命名旧分数来宣称已通过，也不改变它们的固定答案。
- `scripts/probe_memory_semantics.py` 通过 `MemoryJobs.request → after_finish → worker` 执行真实后台链，创建临时 ProjectStore/个人库，并使用真实模型目录和 RequestBudget。
- 原语义探针固定 12 条样例、最多 24 次逻辑调用，结构检查与人工语义评价分开；`tests/test_probe_memory_semantics.py` 已验证预算在模型执行前生效。
- 生产自动召回入口是 `prepare_memory_parts`，后台查询由 S3 的受控比较入口提供。新的检索质量验收必须走这些入口，不能只测 ResearchIndex。

### 本片的选择

保留旧 00–00d 数据、协议和实现，不再扩张旧实验分支。新增当前产品链的合成数据集和离线评测入口；复用已有真实语义探针的配置加载、临时库、RequestBudget、事件配对和安全报告能力。它们是两种不同执行责任，不另建评测平台或引入依赖。

当前语义脚本 343 行；扩展前先拆出其样本/评分逻辑，确保每个文件小于 500 行。新内部模块须有下述明确消费者，不能只为将来扩展搭框架。

## 3. 资产与固定样本

新增 `tests/data/memory_optimization/`，不放到会忽略新文件的 research 目录。实施创建以下文件：

| 文件 | 内容 | 消费者 |
| --- | --- | --- |
| `fixtures.json` | 明确 synthetic=true 的固定输入和人工答案 | 离线评测、语义探针、分片回归 |
| `protocol.json` | 数据哈希、分组、参数、指标和调用预算 | 两个评测入口 |
| `rubric.md` | 主张/范围/时间/证据的人工标注规则 | 审阅者与结果评分 |

首版共 96 个场景，四套各 24 条：

| 套件 | 执行方式 | 覆盖内容 |
| --- | --- | --- |
| `input` | 无模型；真实归档＋批次/绑定函数 | 完整轮、长消息、配对、重复句、多引用、截断、版本变化、敏感输入 |
| `comparison` | 无模型；预置提议与受控 B 返回，走真实读写 | 新值找旧值、重复、更正、并存、过期、缺证据、部分确认、失败跳过 |
| `recall` | 无模型；真实库＋prepare_memory_parts | 短问、长问、多事项、无关编号、否定/条件、权限、预算、跨语种同义 |
| `semantic` | 真实模型；真实 MemoryJobs 链＋临时库 | 原文值得记的事实、噪声、推断、相反概括、旧项关系与保存结果 |

每套为 8 calibration、8 validation、8 holdout。按独立问题家族 `family_id` 分组；同一事实的长短改写、近重复或同一来源变体全部属于同一 split，不能把题目换几个字放入留出集。每套至少覆盖表中全部类型，样本名说明预期分支。

`semantic` 的每条正常输入限制为一个来源批次，测试配置 `max_batches=1`；单轮极长/多批等边界由无模型 input/comparison 套件验证，避免真实采样预算不可预测。旧语义 12 条单独做历史回归，不混入“新留出集”。

## 4. 样本 schema 与标签

根字段：`format=1`、`synthetic=true`、`cases`。每个 case 包含以下结构：

| 字段 | 约束 |
| --- | --- |
| `case_id/family_id/suite/split` | 唯一身份；suite/split 使用第 3 节枚举 |
| `context` | 合成 project/session/thread、权限、允许 scope、当前运行模式；不用真实文件路径 |
| `sources` | 原文 ID、role、text、reply/tool 配对、来源版本、相对发生时间及可见状态 |
| `old_memories` | 人工种子别名、scope/kind/status/body/条件/证据与到期偏移；用生产提交入口建立可行旧态 |
| `query` | recall 输入，其他套件可为 null |
| `expected_facts` | 值得提取的原子事实，每条含 fact_id、支持来源位置、适用条件、允许类型/状态集合 |
| `expected_relations` | 应比较旧种子别名；沿 S3 的 new/duplicate/supplement/correction/different_conditions/unresolved_conflict/unsupported 七种关系 |
| `forbidden` | 禁止出现的激活、错误覆盖、跨范围读取、来源复活、无证续期等明确断言 |
| `fault` | 无或固定故障注入点，仅用于无模型套件 |

`expected_facts` 的提取标签为 `must_extract | optional`，另有显式布尔 `must_activate/must_not_activate`，两者不能同时为真；optional 不计必提取分母。必须自动可用的充分证据事实标 must_activate；推断、未解决冲突、正文条件仍触发硬冲突、过期旧项仅部分补证均不标 must_activate，必要时标 must_not_activate。每条事实同时标注允许状态、支持程度、范围与时间，不以字符串完全相等判定语义正确。程序只做 ID/版本/来源位置/状态等确定性判断；人工审阅主张是否被支持。

来源时间采用相对于测试基准时刻的偏移，保持“旧→新”与到期关系。离线用注入的固定基准；真实探针以每条开始时刻为基准并在报告记录，避免样本随日历推移自动过期。模型不能自报可信来源属性，种子仍通过生产用户准入和 ToolMessage 状态构造。

若为了构造旧候选需要补充前置材料，必须作为 fixture 的显式 setup 保存，并记录其哈希与来源，禁止在执行器中悄悄猜一份旧记忆正文。基准实现不支持的新输入格式由适配器从同一 sources 投影，不改变事实和标签。

## 5. 冻结和运行顺序

1. 在修改生产算法前编写样本/标注规则，检查所有 case 的必提取事实和禁止项可从原文解释。
2. 计算 fixtures/protocol/评分器及相关生产源码 SHA-256，记录 HEAD、工作区相关差异清单、Python/SQLite/依赖版本与非敏感有效参数。协议不能包含自身哈希循环。
3. 先运行旧生产链的 calibration/validation 基线，保存逐 case 的输入、实际结果、阶段输出、指标和错误；明确已知失败，不要求新回归先在旧链全绿。改算法前固定整个样本集及旧源码指纹，但不读取 holdout 的运行结果来指导调参。
4. calibration 只用于实现调试与允许参数选择；validation 用于冻结最终配置与提示词；冻结后在隔离旧版本上取得 holdout 基线，再执行候选 holdout 的完整验收流程。留出集的固定重复属于同一次验收协议，不能逐条看结果后改算法再继续。
5. 留出失败后修订算法，原留出结果保留并改称回归集；再建立未参与修订的新版本留出集。不得仍称原集合为独立留出。

基准和候选报告必须使用同一 fixture 版本与相同模型/profile、既有采样设置。实现更换输出 schema 的影响本身属于候选变化，必须在 manifest 记录。若供应商版本不可固定，报告采样日期和模型标识，不能把不同日期差异全归于代码。

基线源码保留提交和相关工作区差异指纹；存在影响记忆链的未提交差异时，另保存经敏感检查的补丁或隔离源码快照，只有哈希不足以重建旧链。需要重跑旧链时在隔离检出中恢复该版本；不在生产代码中长期维护 baseline/new 两套运行开关。实验报告可存合成输入输出，不得复制真实配置、凭证或用户归档。

## 6. 分阶段指标与分母

| 指标 | 定义 | 防止混淆 |
| --- | --- | --- |
| A 提议召回 | A 中被人工判为对应事实的必提取事实数 / 原文 must_extract 事实数 | 分母不只含 A 已经提出来的内容 |
| 提议证据绑定率 | 成功绑定且位置正确的提议数 / 需要绑定的结构有效提议数 | 格式、来源不可用和语义不支持分别记原因 |
| 旧项召回 | 检索比较集合覆盖应比较旧别名数 / 标签中的应比较旧别名数 | 另报未找到、查询失败和预算省略 |
| B 决策正确率 | 关系/目标/范围均符合标签的决定数 / 有可评审 B 输出的决定数 | 另报所有应判断组的完成率，不能只报成功子集 |
| 最终有效记忆召回 | 最终可用记录覆盖的 must_activate 事实数 / 全部 must_activate 事实数 | 可用须主张正确、active、评测时未过期且范围允许；duplicate 沿用有效旧项也计覆盖，candidate 单列 |
| 错误新增/错误覆盖 | 无依据激活、范围扩大、错替旧项的绝对数及分母 | 允许遗漏不能被偷换成允许错误覆盖 |
| 重复新增 | 本应 duplicate/update 却新增同义 ID 的次数 / 应合并案例数 | 不用正文 exact duplicate 通过证明语义唯一 |
| 前台事实召回 | 进入最终 parts 的相关事实数 / 当前问题应召回事实数 | 分开“查到”和“预算内注入” |
| 误注入 | 无关或禁止事实进入 parts 的数目 / 实际注入数 | 空输出精确率为 null，不填 100% |

同时记录 active/candidate/discard/failed/skipped/unattempted 的实际数量。比较已完成但因当前结构条件不足命中 known_conflict 而保留 candidate，归为“保守未激活”；这不算运行失败，也不能谎称条件不同的事实已经全部 active。

无答案场景单列正确为空比例；每查询 Recall@K、实际返回精确率和按事项覆盖并列报告，不能平均掉特定安全反例。源 ID/数据库真实 ID 用报告映射连接，随机生成 ID 不影响评分。

## 7. 接受门槛与停止条件

确定性工程门槛：所有分片的硬门禁用例通过；其他项目 workspace 记忆或越权来源引文注入、forgotten/过期自动注入、假编号、陈旧覆盖、失败补跑、无新证据续期等禁止项计数为 0。允许 personal 正文沿原权限跨项目召回，其他项目来源标 other_workspace 且不含 quote。测试必须检查真实数据库副作用与调用次数。

语义样本门槛在调参前固定：

1. 留出集和独立重复中的关键禁止项必须为 0：错误覆盖、无依据激活、否定反转、范围扩大。零仅说明本集合未观察到，不能称绝对无幻觉。
2. 最终正确有效事实召回、前台事实召回和实际注入精确率，均不得低于同标签基线；核心定向场景“新值找到旧值”“无关编号不阻断另一事项”必须改善已复现的失败。
3. 没有输出时精确率为 null；正常必生效事实全部跳过不能通过召回门槛。若准确性改善而正确召回下降，报告为未达既定推广门槛，不能自行以“更安全”为由改标签或宣布全通过。
4. 硬边界通过但语义门槛未过时，可以报告工程实现完成、效果验收未通过；默认不宣称优化已经达到预期。下一轮优化由真实错误归因决定，不为追分引入失败重试。

成本门槛使用明确的系统上限，不预设未经测量的延迟百分比：每批 A≤1、B≤1，失败无新增逻辑调用；前台模型调用为 0；query/材料/输出/任务时间遵 S1/S3/S4。逐 case 报告 DB 操作数、逻辑调用、SDK 尝试、输入/输出 token、排队及阶段耗时、预算中止率。

候选相对基线的 token/耗时增量必须给出；不能把跳过更多内容造成的耗时下降当成性能提升。P50/P95 附样本量，不以这组本机数据声明生产 SLA。

## 8. 评测程序和可执行入口合同

### 8.1 无模型评测

新增 `scripts/evaluate_memory_optimization.py`：只在临时 SQLite 上调用真实生产函数，绝不导入模型目录或读取默认用户配置。支持：

- `--stage baseline|evaluate`、`--split calibration|validation|holdout|all`。
- `--fixtures`、`--protocol` 有明确的默认样本路径；evaluate 必须传 `--baseline`。
- `--output` 必填，文件已存在或为符号链接时拒绝覆盖。
- `baseline` 输出未改算法的实际结果，即使存在已知质量失败也如实保存；结构/执行错误仍非零退出。
- `evaluate` 校验数据/标签哈希一致、关联代码指纹存在，打印逐套件门槛结果；未通过返回非零。

`input/comparison/recall` 的样本读取、临时库种子和评分能力放在 `scripts/memory_evaluation_cases.py`；测试导入它，脚本不依赖 tests 的 conftest，也不另建职责重复的 helper。此模块有离线入口、真实语义入口和测试三个消费者；模型加载与生产流程编排仍在各自入口，不建立通用执行框架。

对尚未实现的 S1/S3 私有函数，S0 基线适配器调用当前公开/已有生产入口并记录不具备的观察项为 unsupported；S1/S3接线后使用当前实际入口。旧链没有 B，B 指标标 N/A，只用两版共有的最终事实指标比较，不把缺少 B 算成旧链语义失败。候选应有的阶段缺少观测则算未完成，不能用 unsupported 豁免门槛；不能用伪实现预测新算法表现。

### 8.2 真实语义探针

扩展 `scripts/probe_memory_semantics.py`，保留默认旧套件的 12 条样本与断言，新增 `--suite optimization`、`--split calibration|validation|holdout`、`--fixtures`、`--protocol`。optimization 模式每次只跑选定 8 条，`--repeat` 仍为 1 或 2；切入双阶段时默认由 2 改为 1，保持单次预算内可完整运行，命令也显式使用 1。

沿现有临时库、模型目录、RequestBudget 与事件配对，不新增供应商。S1/S3改变 A/B schema 后，观测回调按明确阶段解析；不能继续把 B 当作含 overview 的 A 输出。旧固定 12 条样本也走当前生产协议，不为旧探针保留废弃模型合同。

每次执行最多 24 次逻辑调用，**发起前**按当前算法的计划 worst case 检查两种套件：optimization 每 case 最多 2 次，8 条×2次重复=32，必须拒绝；8 条×1次重复最多16，允许。旧12条改走双阶段时，repeat=1 最多24，repeat=2 最多48须拒绝。不能跑到一半耗尽才声称完整。网络/解析失败不自动补跑；SDK 尝试记录但不作为程序新增重试入口。

每个新语义 case 做两次独立重复，分开运行同 split 的 repeat=1，记录不同实验 run_id。这是冻结评测样本的重复观测，不是自动重试失败产品任务；不能只重做失败 case 并挑最好一次。

单一模型/profile 对 24 条语义样本的两次完整重复：旧单阶段最多48次、候选两阶段最多96次，共最多144次逻辑调用；旧12条回归另算预算，不静默并入。24 次是单次执行上限，不是整轮总成本。本轮仅定义预算，后续真实请求在单独明确的采样执行范围内开展；配置路径由操作者指定，输出不含配置正文。

## 9. 报告与退出状态

报告包含 `manifest`、样本/协议哈希、参数、代码指纹、run_id、模型标识、执行模式、逐 case 来源映射与结果、分阶段指标、调用预算和失败原因。无模型报告必须明确 `provider_calls=0`。

真实报告初始为 `completed_needs_human_review` 或 `incomplete_or_structural_failure`；人工判定另存 `review.json`，字段为 case_id/run_id/fact_id、verdict、依据与审阅者标识。评分器验证覆盖所有语义输出后才生成语义门槛结果，不用另一个 LLM 给自己打分。

所有文件排他创建，`allow_nan=False`，缺失数据为 null。原始报告不改写；修订标注需要新 fixture/protocol 版本和解释，原结果继续保留。敏感检查命中时报告替换为脱敏标记，安全失败本身可见；不能把真实秘密放进合成例子。

## 10. TDD 与具体文件范围

| 测试 ID | 行为 | 核对点 |
| --- | --- | --- |
| E01 | fixture/protocol 被改或split泄漏 | 执行前拒绝，不能到评分后才警告 |
| E02 | 输出已存在/符号链接 | 不覆盖旧证据 |
| E03 | 空返回、无答案、部分失败 | 分母与 null 正确，不把失败算零事实成功 |
| E04 | 同事实不同正文/同正文不同条件 | 人工语义标签与程序确定性判断分开 |
| E05 | 模型预算不足 | 发起请求前拒绝，观测调用次数为0 |
| E06 | 一次失败与重复试验 | 产品任务不重试；完整实验重复有独立run_id，不挑成功样本 |
| E07 | 临时库与真实配置 | 用户库不访问、不写；配置敏感字段不进报告 |
| E08 | current worker与前台真实入口 | 证明调用经过生产函数，验证数据库副作用与A/B事件配对 |
| E09 | A/B阶段输出 | 分别解析，无overview的B不误报A结构错 |
| E10 | 人工审阅缺项 | 只能报告待审阅，不能产出语义通过 |

文件范围：新增上述三个数据文件、`scripts/evaluate_memory_optimization.py`、`scripts/memory_evaluation_cases.py`、`tests/test_memory_optimization_evaluation.py`；扩展 `scripts/probe_memory_semantics.py` 与 `tests/test_probe_memory_semantics.py`。旧 research 数据不改，生产代码不导入这些脚本。

S0 先写评测器失败测试，再实现工具。S1–S4 的行为红灯各自在实施时运行，不能把本轮文档审阅称为已经完成 TDD。

## 11. 执行命令与完成标准

以下是实施后的入口合同；新增脚本/数据目前不存在。本轮不会执行真实采样。

```bash
.venv/bin/python -m pytest tests/test_memory_optimization_evaluation.py tests/test_probe_memory_semantics.py -q
.venv/bin/python -m pytest docs/research/memory00 -q
.venv/bin/python -m scripts.evaluate_memory_optimization --stage baseline --split calibration --output build/memory-143bm-baseline-calibration.json
.venv/bin/python -m scripts.evaluate_memory_optimization --stage evaluate --split calibration --baseline build/memory-143bm-baseline-calibration.json --output build/memory-143bm-candidate-calibration.json
```

真实语义采样命令使用已有、显式设置的 `MEMORY_MODEL_CONFIG` 路径；该变量仅指文件，不回显内容，不在 Spec 写入用户实际路径或凭证：

```bash
.venv/bin/python scripts/probe_memory_semantics.py --config "$MEMORY_MODEL_CONFIG" --suite optimization --split calibration --repeat 1 --output build/memory-143bm-semantic-calibration-r1.json
```

validation/holdout 和第二次重复分别使用相应 split 与新输出名；同一输出文件不复用。请求预算、模型/profile和运行清单写入协议后再执行，不由 shell 循环无限尝试。calibration/validation 基线在算法修改前；holdout 基线按第5节在冻结后从隔离旧版本取得。`--split all` 只用于留出已经揭盲后的历史回归，不能绕过这一顺序。

S0 完成须交付：冻结数据与协议、评分器测试结果、旧生产链基线报告、待改善案例清单。真实基线尚未执行时明确标 `semantic_pending`，不能称已取得业务基线。S4 最终验收引用相同版本的基线与留出结果，报告程序/模型/业务三个层面的实际证据。
