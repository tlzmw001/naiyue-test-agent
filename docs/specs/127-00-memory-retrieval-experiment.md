# Spec127-00：固定样本与无Embedding检索实验

2026-10-22。状态：00完成，离线结果及本机回归见第4节。主合同见[Spec127](127-memory-and-session-overviews.md)；只实现研究脚本/样本/测试，不接生产工具、配置或存储。

后续边界：用户追问精度后授权[00b](127-00b-memory-retrieval-tuning.md)深入实验。本文及原结果保留为历史基线，3/2/1＋宽松OR不再视为产品最终参数；当前下一步及候选见00b结果评审，01尚未实施。

## 1. 输入、输出和实验控制

交付路径为docs/research/memory00/；包含固定JSON样本、独立标准库检索原型、离线benchmark、确定性测试及README。输出写入显式指定的build证据目录，拒绝覆盖已存在结果；SQLite使用:memory:临时库，不读取真实会话或模型配置。研究文件不会被生产模块导入。

固定样本包括12段语义提取材料、24条检索查询（12 calibration、12 holdout）及4个完整流程。每条query给query/可选expansions、可信workspace/machine/target、relevant_ids/required_ids/forbidden_ids、是否无答案。正文/别名/查询及状态过滤三路线完全相同。语义样本本轮只校验结构/来源/期望约束，不调用模型也不声称提取质量通过。

三路线：substring按原始空白分隔词项作受控字面匹配；unicode61使用原始字段与安全引用的词项；adapted将连续中文二元字组及ASCII词项/原始技术标识符编码成FTS安全词再BM25。索引词不得覆盖展示正文，输入不被当FTS表达式/SQL执行，英文技术标识符和数值不丢失。技术锚点精确匹配、范围/状态/期限/已知条件过滤及跨库合并属于共同规则。

项目/个人各一个临时库、各自排名；共同使用倒数名次融合（RRF），不跨库比较BM25原始值。query与显式expansions分别执行、保留原query及variant各自技术锚点、去重合并，总结果上限8。未知条件可返回待核对，明确失配排除；过期/停用/遗忘/其他workspace禁止进入结果，过滤在库内limit前；FTS候选及最终返回分别核对权威版本/正文指纹，不能只用同ID关联旧索引与新正文。

初始参数集合在首次benchmark前固定：字段权重(1,1,1)、(3,2,1)、(5,3,1)，RRF常数60，库内候选上限50，展示前8。不针对某条query指定权重/词典/分支；每路线仅根据calibration选一组权重并冻结，同分优先标题/别名高于正文，再取较小权重。随后评估holdout用于路线选择，因此该集合是验证集，不宣称是最终选择后的独立测试集。原文子串基线不是带模型多次改词的Claude运行效果。

输出JSON包括输入/代码哈希、运行时/SQLite/FTS能力、所选权重/冻结指纹、每路线calibration与holdout逐条实际结果、Recall@8/Precision@8/必命中与禁止结果/无答案、每次查询时间/文本字节，以及诊断/失败。失败须非零退出并保留原因，不能把golden当实际结果或过滤掉失败query。README另记采用方案/失败案例和限制。

## 2. 测试与执行顺序

1. 先写固定数据及行为测试，保存红灯命令与实际失败；之后写最小研究实现，不先写产品模块。
2. 测试中文两字、技术标识符边界/0与0.5、跨库融合、在limit前过滤、未知条件、无答案/FTS注入、陈旧索引权威重验、纯标准库及调参不读取holdout标签。
3. calibration选择参数，生成冻结指纹，再评估holdout；报告每个失败/漏召回，不为好看修改留出标签或数据。
4. 若失败来自实现合同错误，用独立回归定位修复；若策略需要看holdout改动，该集合从此不再算留出，必须如实标明，不把修订成绩当独立验证。
5. 运行研究测试、重跑结果可重复检查、语法/500行/文档链接/diff。生产代码未改时不伪称产品功能完成；全量既有测试按当前工作树执行，结果与本片专项区分。

正常例：查询“虚拟环境”，workspace与personal分别检索可用条目→按共同名次融合→返回原文及来源/条件，Windows/过期项不挤占Mac前8。失败例：FTS词项包含引号/OR/SQL片段→受控引用为数据，不能执行语法；索引残留已遗忘ID→当前权威状态排除。无结果与SQLite故障分开，后者明确失败。

## 3. 00验收与后续

00完成条件：主Spec有真实接线位置和字段/失败合同；12/24/4固定集合校验通过；离线研究代码有正确红灯/绿灯证据；三路线完整报告及冻结/留出界限；选择一个满足关键案例的最小后续方案，保留局限；不声称模型语义/业务/跨平台效果已验证。

01唯一下一步为history只读工具：先讲准备如何注册/传可信范围/投影原文，再按用户下一步指令推进。不自动进入memory存储/写入/后台模型或发布。

## 4. 实际交付与验证（2026-10-22）

已交付主Spec及本片Spec、40条合成记忆/12段语义/24查询/4流程、4个研究Python文件、协议、逐query摘要和[实验报告](../research/memory00/检索实验结果.md)。研究实现被benchmark/测试实际调用，生产模块不导入。固定输入SHA-256为0c46d61aa5bcd6ae03ca00df8377cacff69fd830d037f1b217240286004a31ec，未改标签/数据改善成绩。

选择适配FTS/BM25、字段3/2/1、RRF60：留出Recall@8=100%、必命中10/10、禁止0、无答案2/2；返回精确率81.25%，h05仅1/8相关且首条无关、c02遗漏m23等完整保留。主Spec已同步这一候选索引路线及限制。留出参与选型，不是独立最终测试；没有在看过留出后调整策略。

| 验证命令/动作 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest docs/research/memory00 -q` | 57 passed in0.11s；初始行为红灯4 failed、模块条件/指标红灯6 failed，审阅边界回归红灯10 failed/44 passed后再修复。 |
| `.venv/bin/python -m docs.research.memory00.benchmark --stage calibrate --output build/spec127-00-ukypsq2_/frozen-v2.json` | exit0，三路线均选3/2/1，数据/协议/代码哈希冻结。 |
| `.venv/bin/python -m docs.research.memory00.benchmark --stage evaluate --frozen build/spec127-00-ukypsq2_/frozen-v2.json --output build/spec127-00-ukypsq2_/result-v2.json` | exit0、status=passed、recommended_method=adapted；三路线完整成功/失败结果均保存。 |
| 同一冻结文件复跑，输出`repeat-v2.json` | 36/36条排名和质量指标一致，不比较计时。 |
| CLI故障探针：已有输出/结果符号链接/冻结代码指纹被改 | 均exit1且有具体诊断，原结果未改变，不产生伪成功文件。 |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 4228 passed in298.75s。首次无fd的PATH为32 failed/4196 passed，单测明确复现fd不可用；复用既有与release/native-tools.json相符的fd10.5.0/rg15.2.0后完整重跑通过，未改产品/测试/环境文件。 |
| `npm --prefix frontend test -- --run` | 50文件、415 passed，5.11s。 |
| `npm --prefix frontend run build` | tsc无错误、Vite331模块成功；保留大于500kB chunk的提示，本片不调整前端产物策略。 |
| `.venv/bin/python -m pip check` | No broken requirements found。 |
| `.venv/bin/python -m compileall -q docs/research/memory00`、AST/标准库导入/JSON/本片500行/链接/空白及`git diff --check` | 全部通过；最终结构检查覆盖本片及同步文档。 |

架构/安全两项只读审阅完成：架构初始两项已用独立回归修正，复核无新增实质发现；安全无行动项。审阅和故障探针只验证本片研究边界，不替代后续真实存储/权限/模型语义验证。

原始证据在build/spec127-00-ukypsq2_；可随版本保留的72条逐query结果及代码/输入/冻结哈希见[results-summary.json](../research/memory00/results-summary.json)。语义样本12段和产品流程4条未执行，供应商请求0；未改生产/前端源、真实配置/用户库/依赖/版本，未构建发行wheel或提交/push/发布。并行学习、发布文档及worktree研究保留，无新增用户掌握证据。下一片仍为01准备说明。
