# Spec143BM-S4：有限问题拆分、自动召回与整体验收

日期：2026-11-16。状态：总体方向已确认，详细实施规范；**尚未实现**。本片属于[总规范](143bm-memory-optimization.md)，以[总体方案](../长期记忆优化总体方案-2026-11-16.md)和用户确认的“失败跳过、不重试”为边界。

## 1. 用户能看到的结果

用户说“跑一下本地回归，另外看看 TC-99 的页面”时，自动召回分别查询这两件事。`TC-99` 只限制页面问题的查询，不阻止本地回归环境记忆进入提示。多个查询共享既有相关条目数和 token 预算，不把整段记忆正文全部注入。

本片不增加模型调用、Embedding、自动工具循环或查询失败重试。简单明确的查询、弱查询、权限和每轮提示快照沿用现有合同。超长且无法可靠分开的单句可以遗漏，不能为了命中而删除否定、适用条件或任意截词。

## 2. 当前主链与具体缺口

核对基线：`60108431ceeeaf100df008da4765b879505fc1d7` 加已有工作区改动。

```text
SessionPersistence.prepare_memory
  项目 -> MemoryJobs.prepare -> prepare_memory_parts
  普通聊天 personal_only ------> prepare_memory_parts
    -> _query_text：最后真实 HumanMessage 的末尾 max_query_chars
    -> MemoryInput(search, active) -> allowed -> MemoryAccess.query
    -> _search -> MemoryRepo.rank/current -> select
    -> _memory_index -> _render -> prompt parts
```

现有 `_query_text` 只取末尾 2000 字符。`prepare_memory_parts` 只执行一个全文查询；`MemoryAccess._search` 在当前查询内部融合扩展词和项目/个人库，`rank` 将原 query 与 expansion 的技术锚点共同作为必要条件。因此把全文保留在 query、把短句塞入 expansions，不能实现独立事项检索。

当前 `prepare_memory_parts` 同时被执行和预览调用，返回 `[{id,text,count}]`，不保存记忆；`SessionPersistence` / 请求准备负责实际轮次快照，审批恢复沿用原快照。项目目录只在现有首次目录时机准备，普通聊天不增加项目目录。

现有 `_read` 只返回 items，会丢失返回包的 `truncated`。本片须保留查询是否受限用于诊断；不因此增加全库扫描或自动补查。

## 3. 接口与内部数据

### 3.1 外部合同保持

- `MemoryInput` 的 action/query/expansions/status/limit 和工具调用签名保持；显式 search 仍由调用者提供查询，原 expansions 语义不变。
- `MemoryAccess.query(...)`、`prepare_memory_parts(...)` 的签名与成功返回形状保持。
- 自动查询仍走 `MemoryInput` 校验、当前 `allowed(request)` 和 `MemoryAccess.query`，不绕过权限入口。
- `memory` / `session_directory` 两类 prompt part、上下文 token 统计、预览和执行共用装配入口保持。
- 本片无数据库迁移，不更改 active/candidate/expired 的业务规则。

### 3.2 直接扩展现有 memory_context.py

该文件当前 136 行。先在现有模块内完成以下私有函数，不为单一消费者新建查询规划服务、缓存或注册层；实现后必须保持小于 500 行。

| 私有函数 | 输入 | 输出与消费者 |
| --- | --- | --- |
| `_query_plan(messages, settings)` | 当前消息与 MemorySettings | 查询计划；由 `prepare_memory_parts` 消费 |
| `_read_result(memory, session_id, project_id, *, allowed, **arguments)` | 与当前 `_read` 相同的可信身份和操作参数 | 有界结果包或带安全错误码的失败结果；供偏好读取和独立查询使用 |
| `_merge_related(query_results, *, seen, limit, rrf_k)` | 每条成功查询的有序 items、待注入偏好的 `(scope,id)→version` 映射 | 带查询归属的候选顺序及冲突身份；装配器排除冲突后执行最终 token 预算 |

`_query_plan` 返回普通内部字典，无 Pydantic 对外 schema：

| 字段 | 形状 | 说明 |
| --- | --- | --- |
| `queries` | `[{query_id, text, start, end}]` | `q1..qN` 为本次计划内标识，offset 指有限扫描投影；不是聊天归档证据位置 |
| `scan_truncated` | bool | 用户文字超过扫描上限 |
| `omissions` | `[{start,end,reason}]` | 原因只允许 `scan_limit/query_limit/query_chars/query_terms/protected_block/weak_query` |

结果包由程序建立为 `{query_id, ok, items, truncated, error_code}`。不得用 `items=[]` 混淆“成功零命中”和查询失败。内部记录不持久化，不进入记忆索引或跨会话恢复。

## 4. 确定性查询构造

### 4.1 选消息与扫描上限

1. 从后向前选最近一条真实用户 HumanMessage，继续排除 compaction summary、delegated_task、steering；助手/工具输出不参与。
2. 字符串直接读；文本块消息按原顺序读取 text/input_text 并保留段落分隔。只扫描最多 `recall_scan_chars`，以额外 1 字符探测截断，不先拼接无限正文。
3. 扫描前缀而不是先截成末尾 2000 字符。超出扫描上限的后文不推测、不绕回全文搜索；诊断标 `scan_truncated`。截断时，仅保留截断前已闭合的完整边界；末尾未闭合片段即使很短也整段省略，不能把缺少后半句限定的前缀当完整查询。
4. 完整输入归一化后属于现有 `WEAK_QUERIES`，则查询数为 0；偏好和允许的会话目录仍按原规则准备。

### 4.2 边界规则

按从左到右的有限状态扫描器识别完整段落、句子、列项；不调用模型或词性/实体服务。

- 非保护区的换行、`。！？!?；;` 是边界。英文句点只在后接空白/结尾且不位于技术词项内时作为边界。
- 识别位于段落开头或逗号之后的 `另外`、`此外`、`还有一个问题` 作为显式新事项起点；去掉连接词和相邻分隔符，正文原字词保持。
- 普通逗号不拆。例如“如果在 CI，先保持原样”是一段，不能只查“先保持原样”。不按“和、与、以及”拆分，因为它们可能连接同一事项的条件。
- 内联反引号、成对中文引号/ASCII 双引号、括号内的标点不作为边界。优先保护 `memory_lexical.LEXEMES` 识别的技术词项，包括路径、版本、编号和 URL。
- 围栏代码块作为一个保护区，不把大段代码/log逐行转成查询；记录 `protected_block`。块外的自然语言问题正常参与。未闭合保护区保守保持，不猜测其中边界；若超限则整段省略。
- 列项标号只在行首剥离，如 `1. `、`- `；不得删除 `TC-99` 或路径中的数字/连接符。
- 不删除否定词、时间限定、条件词，也不自动把“那个方案”改写成一个猜测出来的主题。

每段去掉首尾分隔符后，要求 `len(text) <= max_query_chars` 且 `len(lexical.tokens(text)) <= max_query_terms`。超限段整段跳过，继续检查下一个独立段；禁止任意截到长度上限后当作完整问题。全由符号组成或为弱查询的段跳过。

依原顺序选至多 `recall_query_limit` 个不同查询；只折叠首尾空白归一后的完全相同文本，不能 casefold 路径/技术锚点来去重。超限后的独立事项记录 `query_limit`。首片不承诺通用多意图理解。

### 4.3 三个必测例子

| 输入 | 查询计划 |
| --- | --- |
| `跑一下本地回归，另外看看 TC-99 的页面。` | `跑一下本地回归`；`看看 TC-99 的页面` |
| `如果在 CI，先保持原样。不要沿用本地代理。` | 两个完整句子，各自保留条件或否定 |
| `请检查 /repo/a.b/test.py，另外排查 TC-99。` | 路径不被句点切开；TC-99 只约束第二次查询 |

无明确边界的冗长单句仍可能受词法覆盖率影响而漏召回。这是首片限制，必须进入 S0 报告；不能通过降低全局覆盖阈值、丢掉锚点或新增推理调用隐式扩大本片。

## 5. 独立检索与结果合并

先计算 `K=min(related_limit,max_limit)`；K=0 时不发起相关查询，偏好/目录仍按原合同准备。否则，对每个计划查询顺序执行一次 `MemoryInput(action='search', status='active', query=单段, expansions=[], limit=K)`。scope 沿当前自动召回默认 both；普通聊天由原 personal_only 边界限制到个人库。所有 query 的 `allowed` 分别检查。

一条查询失败时只省略它，记录固定错误码；其他独立查询仍可执行。不重试失败查询，不替换成全量 list。成功结果受限可以保留已核验的索引项，同时标明截断；它只供前台相关提示，不被当作后台“无冲突”的证明。

设 Q 为实际查询数，K 为 `min(related_limit,max_limit)`，返回候选最多 Q×K；底层每次数据库排名继续受 candidate_limit 限制。默认 Q≤4、K≤8，不把 related_limit 解释成每查询最终注入额度。

合并规则：

1. `(scope,memory_id)` 作为身份；同 ID 不跨 workspace/personal 合并。偏好先保留条目与版本映射，等 related 汇总后再统一渲染；同 ID 同版本不重复写入 related，只有实际注入的偏好才计查询覆盖。
2. 同一身份在偏好读取和查询之间、或不同查询之间返回不同 version 时，保守从偏好与 related 两处都排除该身份，不额外读取重试，也不计覆盖；记录 `version_changed_between_queries`。每个查询内部的权威版本/正文 digest 复核仍由 MemoryAccess 执行。
3. 同一版本合并 `matched_query_ids` 与每条查询的排名，融合分数为 `sum(1/(rrf_k+rank))`；不比较跨库原始 BM25 数值。
4. 第一轮按 query 原顺序，为尚未覆盖的查询尝试一个可放入提示的最高排名结果；候选已代表多个查询时同时计覆盖。
5. 之后按融合分数补其余名额；并列按最早 query 顺序、scope、memory_id 稳定排序。
6. 遇到已选 ID、敏感条目或不能装入预算的完整索引项，尝试下一条已返回候选；不为补位额外查询，也不截断条目的条件字段。

`matched_query_ids` 仅供内部排序和诊断，不新增工具/API 响应字段。`duplicate_ids`、`needs_verification`、来源状态和 preview_complete 沿原结果语义保留，不将部分预览描述成完整正文。

## 6. 提示预算、生命周期与失败

保留当前装配优先级：用户偏好 → 本次允许的会话目录 → 相关记忆。总预算仍为 `injection_tokens`，包括数据包外壳；公平合并仅分配剩余相关记忆空间。目录已占满预算时允许相关记忆为空，记录预算省略，不顺带重排整个提示策略。

`_memory_index`、`_render` 的数据边界文字保留：历史记录不构成授权，需检查适用条件和当前文件。来源不可用但仍有效的记忆按 S2 合同保留来源状态；本片不读取原文补证、不续期、不转正、不写库。

正常链例：两个查询各命中一个 active 条目 → 权限/当前记录复核 → 合并为两项 → 总预算通过 → 相同 parts 进入本轮提示与其用量统计。

失败链例：q1 数据库 busy，q2 命中页面记忆 → q1 记录失败并省略，q2 正常注入 → 主 Agent 按当前用户问题执行。q1 不触发补查任务，后台记忆整理也不因本次召回失败重做。

继续保留 `AssertionError` 等测试检查器异常可传播的行为；捕获可预期读取/验证错误，不以宽泛空 catch 返回成功。模型已发送或本轮已冻结的提示不承诺在另一次遗忘后被撤回；后续实际写入仍重新检查来源与遗忘状态。

## 7. 配置与诊断

在 `MemorySettings` 增加两个可选字段，旧配置省略时采用默认值：

| 字段 | 默认 | 合法范围 | 目的 |
| --- | --- | --- | --- |
| `recall_query_limit` | 4 | 严格 int，1..8 | 单次自动召回的独立查询数 |
| `recall_scan_chars` | 16000 | 严格 int，2000..128000，且不小于 max_query_chars | 限制扫描工作量，避免先丢掉消息开头 |

这些是首版有界参数，不是实测最优值。其余 query 字符/词项、候选数、条目数、token、字段权重和 RRF 参数保持。无新增开关或双检索模式。

新字段沿 `AppSettings.memory` / `load_settings` 进入；配置非法沿现有 `memory_settings_invalid action=disabled` 降级，不改变全局启动策略。同步完整配置示例、简版示例与 `docs/memory-tool.md`，不修改用户真实 settings.toml。

本轮装配结束记录无正文的诊断：`query_count/read_failed_count/scan_truncated/query_omitted_count/returned_count/injected_count/uncovered_query_count/budget_omitted_count`。原因码包括上述计划省略原因和读取错误码，禁止记录 query、引文或私密正文。复用 logger，不新增观察回调或持久召回表。

正常仍返回原 parts 列表，无结果返回空列表；不为展示诊断额外生成“没有记忆”的占位提示。诊断日志不能改变主执行结果。

## 8. 文件范围与迁移清理

| 文件 | 修改职责 |
| --- | --- |
| `application/memory/memory_context.py` | 查询规划、保留读取状态、合并与装配；删除旧末尾截断单查询路径 |
| `memory_settings.py` | 两个新配置及约束 |
| `docs/examples/settings.example.toml`、`docs/examples/full/settings.full.toml`、`docs/memory-tool.md` | 示例与行为说明 |
| `tests/test_memory_query_plans.py`（新增） | 确定性边界与限额 |
| `tests/test_memory_context.py` | 多查询合并、权限与提示预算 |
| `tests/test_memory_context_usage.py`、`test_memory_approval_context.py`、`test_memory_scheduling.py` | 统计、同轮快照和目录时点回归 |
| `tests/test_memory_application.py` | 真实配置加载与非法值原降级合同 |

本片超过 8 个文件，但无新服务/依赖。`MemoryAccess`、公开工具、数据库与框架运行入口原则上不改；若实现声称必须改，先指出实际调用障碍并同步 Spec。保留现有词法选择器及其旧回归，不能用删除旧测试掩盖短查询退化。

执行和预览都调用新计划函数，旧 `_query_text` 及只丢回 items 的 `_read` 内部包装迁移所有消费者后删除。旧测试改为验证当前行为，不保留无调用方的旧单查询实现。

## 9. TDD 与验收

| 测试 ID | 场景 | 必须断言 |
| --- | --- | --- |
| R01 | 短查询与弱查询 | 短查询一次，弱查询零次，无额外模型调用 |
| R02 | 同一句“另外”双事项 | 两个独立请求，TC-99 不污染第一条 |
| R03 | 条件/否定/路径/编号 | 字面限定保留，普通逗号/路径句点不误切 |
| R04 | 文本块、代码围栏、未闭合引号 | 扫描有界，保护区不拆，不构造半段完整查询 |
| R05 | 2000 字符之前的主题与长尾 | 在扫描范围内的早期完整问题不因末尾截断消失 |
| R06 | query/scan/terms 上限与 K=0 | 截断末尾残句省略；查询数不超限；129词单段不发出；K=0零相关调用，K>0请求夹限 |
| R07 | 多查询融合/偏好重复 | 默认总 related≤8；相同 scope/id 去重，跨库同 ID 保留 |
| R08 | 事项覆盖与小 token 预算 | 能容纳时每事项有代表；不能容纳不切条件、不超总预算 |
| R09 | 第一个查询 denied/busy | 该项不读或只失败一次，其他独立查询可用，日志无正文 |
| R10 | 同 ID 在偏好/查询之间版本变化 | 两处均排除且不计覆盖，不读回重试，不混合匹配依据 |
| R11 | candidate/expired/forgotten/其他项目workspace | 不进入related；personal正文按权限跨项目可用，other_workspace来源无quote；无写库、转正或续期 |
| R12 | 预览、实际发送、审批恢复、普通聊天 | 共用 parts 形状，既有快照/目录/个人库边界保持 |

先确认 R02/R05 在旧链失败、其余用例的失败原因与目标一致，再实现；测试末尾显式核对调用次数、日志、状态和数据库未变。不得通过放宽覆盖断言或 skip 制造通过。

实施后在仓库根目录执行：

```bash
.venv/bin/python -m pytest tests/test_memory_query_plans.py tests/test_memory_context.py tests/test_memory_context_usage.py tests/test_memory_approval_context.py tests/test_memory_scheduling.py tests/test_memory_application.py -q
.venv/bin/python -m pytest tests/test_memory_readonly.py tests/test_memory_semantic_inputs.py tests/test_memory_personal.py tests/test_multi_project_memory.py -q
```

新测试文件由实施创建，目前不存在。合并前完成[总规范](143bm-memory-optimization.md)的全量测试/标准构建及 S0 留出评测；本片不通过新增宽松词法规则来单独追求召回。

## 10. 联合验收与回退

S4 的规划算法可在 S0 后独立开发，最终必须使用完成 S1–S3 的完整工作区做联合验收：源对话 → 提议/比较 → 实际保存 → 新用户问题召回。至少包含新值找旧值、更正后召回新版本、失败不重试、删除后来源状态及遗忘后禁止重建。

按 S0 对比旧检索集合、新留出集合和真实模型提取结果；报告按事项召回、误注入、DB 工作量、总提示 token，区别“未命中”和“命中但装不下”。短查询精度不能用平均长查询增益掩盖。

本片自身无数据迁移；回退算法只涉及代码与新增配置字段。若实际配置已写入新字段，旧版本可能按非法 memory 配置禁用记忆，回退须使用兼容的配置副本；本轮不改真实配置。S2 的数据库格式回退另遵其备份合同，不能用本片无迁移推断整个优化可无损回滚。

本次仅编写 Spec；尚无新测试、质量结果或构建通过声明。
