# Spec143BM-03：按提议检索旧记忆与批量比较

版本边界：2026-11-17 的 [S6 程序装配动作](143bm-06-program-owned-memory-actions.md) 替代本文中 B 必须重写完整 claim、重抄 kind、partial 正文不等即拒绝的输出要求。证据、权限、CAS 与生命周期保护继续有效；本文其余实施记录保留历史语境。

日期：2026-11-16；状态更新于 2026-11-17。**两阶段主链已实现，有阶段工程验证和历史真实校准；完整关系判断、旧项召回和质量验收仍未关闭，统一转入 [S8 第 14 节](143bm-08-memory-tool-action-contract.md#14-s0s8-未完成事项统一对账与关闭标准)。** 下文阶段进度保留历史语境，不另排 S3/S0 收尾支线。初稿源码基线：`60108431ceeeaf100df008da4765b879505fc1d7`。

总入口：[Spec143BM](143bm-memory-optimization.md)。前置：[S1 输入与证据编号](143bm-01-conversation-evidence-input.md)、[S2 证据保存与生命周期](143bm-02-evidence-lifecycle.md)。总体依据：[长期记忆优化总体方案](../长期记忆优化总体方案-2026-11-16.md)。

本片使后台先提出有证据的事实，再按每条事实寻找旧项，最后集中判断新增、更正、补证或冲突。用户已确认失败直接跳过；不新增恢复包、重试、继续入口、提交回执或临时正文保留期。当前实施不修改真实配置或用户数据库。

## 1. 要交付的行为

用户说“以后本项目本地回归用 `.venv`，CI 保持原样”。已有 M17：“本地回归使用系统 Python”。

1. A 根据完整对话提出 P1，事项为“本地回归的 Python 执行环境”，引用 S1 的 E12。
2. 程序绑定 E12 的实际原文；查询事项时不要求旧项也包含新值 `.venv`，找到 M17。
3. B 同时看到 P1、E12、M17 的正文/条件/状态/证据，建议明确更正同一事项。
4. 程序按 M17 的已读版本提交一次更新，保留 ID；来源、时间、权限或版本不符则跳过。
5. A 的概览独立保存。B 失败不回滚成功概览，也不能绕过 B 新增 active 或 candidate。

这里的 P1 只存在于一次任务的内存；candidate 是通过正常业务判断后入库的不确定主张，不能用来收容运行失败。
另一条新事实明确针对 CI 时可以分别保留；若区别仅在正文、现有 known_conflict 仍命中，新项保留 candidate。并存不保证两项都 active。

不承诺词法检索找全同义主张，不承诺并发语义唯一性，不修改显式 `memory` 工具动作及载荷合同。

## 2. 当前调用链与替换点

| 当前位置 | 当前行为 | 本片结果 |
| --- | --- | --- |
| `memory_job_runner._prepare/_related` | 先拼用户全文查询 active/candidate，把旧项塞回同次模型输入 | A 不读旧项；有效提议绑定后才执行事项查询 |
| `memory_job_runner.PROMPT/_messages` | 一次调用同时做概览、提议和更新目标选择 | 分为 A 提取和 B 比较两份明确提示 |
| `memory_batches.response_schema/parse_model_result/proposal_request` | 平面提议、手抄引文、模型可选择旧 ID | S1 负责编号绑定；S3 负责 A/B schema、关系和提交映射 |
| `model_access.memory_output.prepare_memory_output` | 冻结模型上的 text/json_schema/function_calling 适配 | A/B 分别传自己的 schema；协议与输出预算规则不变 |
| `MemoryAccess.query/_search` | 公共请求、全查询锚点和覆盖率筛选 | 公共语义保持；新增受控内部比较读取，不靠 payload 开关 |
| `memory_mutations.commit` | 来源回查、CAS、状态、TTL、抑制与单库提交 | 消费 S2 内部 `confirmation_scope`，保留原硬门禁 |
| `memory_overview_store.save_overview` | 保存概览与覆盖信息 | 补齐旧范围不能覆盖较新概览的事务门禁 |

实现前复查实际消费者；源文件路径统一以 `naiyue_test_agent/` 为前缀。runner 目前已接近 500 行，按下面实际职责拆出模块，不保留并行单阶段后台路径。

## 3. 内存数据及 A 输出合同

### 3.1 输入由谁生成

S1 的 `build_batch(...) -> MemoryBatch` 提供 `units/evidence_map/next_position/history_version/skipped/coverage`。
`EvidenceUnit`、编号资格和位置规则以 S1 为唯一权威，本片不重新计算 offset 或将模型文本当原文。
`origin='overlap'` 可作合法辅助引用，但每个提议至少引用一个 `origin='new'` 的可引用片段。
`context_only`、硬切不完整片段及不存在的 evidence_id 不能作为写入依据。

A 输入固定为：上一有效概览及覆盖、本批 `model_batch(batch)` 投影、程序给出的当前项目/允许归属说明。
A 不收到旧记忆列表，也不把概览当证据。历史、工具输出和引文始终标为不可信数据，不是指令或执行授权。

### 3.2 A 的 JSON

根对象只允许 `overview` 和 `proposals`；overview 固定 `goal/progress/blockers/next_step` 四个字符串，总长不超过 `overview_chars`。
`proposals` 长度不超过 `max_proposals`；每项严格校验，额外字段禁止，不能由模型提交 action/version/时间/可信角色。

| 字段 | 类型与上限 | 语义 |
| --- | --- | --- |
| `matter` | 非空字符串，最多 128 字符 | 稳定事项，如“本地回归的 Python 执行环境”，不把新值当事项身份 |
| `identity_terms` | 字符串数组，最多 4 项，每项最多 64 字符 | 可选对象身份，如 TC-99；程序核对它确实出现在所引证据的词法锚点中 |
| `kind/subject/body/basis/intent` | kind/basis/intent沿现有枚举；subject/body为字符串，subject≤128，body≤800字符且符合MemoryProposal字节限制 | 独立主张及建议状态；kind 按现有业务分类 |
| `sources` | 1～8 个 `{evidence_id: string}` | 只引用本批编号；不提供 quote、offset 或 source_key |
| `scope` | `workspace/personal`，缺省 workspace | 待程序权限及 B 依据复核；缺少条件不代表跨项目通用 |
| `title/reason` | 可选，沿用 160/300 字符上限 | 缺省 title=subject；reason 由程序提供固定说明 |
| `machine/module/target` | 可选/null，沿用 128 字符上限 | 原文明确的条件；未知留 null，不把 CI 塞入 machine |
| `aliases/gaps` | 可选，分别最多 5/8 项，每项≤128 字符 | 非空白短文本；gaps 是该主张的真实缺口 |

A 不输出 proposal_id。程序按本批数组位置生成 `P1...P8`，坏项仍占原位置，不能重排后错误关联日志。
不同项可能是同一事实，不能仅凭 subject 不同就认为不同；A 不负责指定 memory_id。

### 3.3 局部校验与证据绑定

先严格解析完整 JSON：拒绝重复键、NaN、非对象根或非数组 proposals；整体不能解析则本批 A 失败，不抢救半截状态。
JSON 可解析时，overview 与每条 proposal 分别校验。合法 overview 可保存，单条提议错误不能抹掉它或其他合法提议。
根字段未知或 proposals 超过上限属于整体输出合同错误，不截取前 8 条后声称成功。

S1 `bind_proposal_evidence(proposal, batch)` 返回 `BoundProposal{proposal,evidence_ids,sources,evidence,context_units,context_complete}`，只绑定、不写库。
S3 追加 `proposal_id/matter/identity_terms/scope`；`sources` 保持现有 `MemorySource` 形状，后续仍须按 S2 回查。
context_units 由 S1 从本批已展示材料选择紧邻限定及工具配对信息，服从 MemoryBatch.context_char_limit（来自 batch_overlap_chars，默认600）；不补读或截半句，不自动变为新 sources。
context_complete=false 必须送给 B；若结论依赖缺失限定，该提议跳过，不能仅因 E 编号有效就当作支持充分。
若结论必须依赖 context_units 中 A 尚未正式引用的限定，也跳过该提议；B 不把上下文自动升级为新来源，不回调 A 补引用。
提议的敏感内容、来源范围、编号资格和归属权限在查询前校验；新证据版本/遗忘状态在提交事务再次核对。
个人范围必须有明确跨项目适用依据；B 不确认这一点就拒绝该个人提议，不自行改成另一个范围补救。

## 4. 后台比较查询：少量查询，不把新值设为必需词

### 4.1 固定查询计划

每个有效提议至多两次查询；在执行前一次性生成计划，失败后不更换查询重试。

1. 主查询为 `matter`，词法 OR 匹配，不设技术锚点硬要求；新值意外进入 matter 也不能成为全局 AND 条件。
2. 只有合法 `identity_terms` 非空时，再查 `matter + identity_terms`，仅这些对象身份作为必需锚点。
3. 对两个结果按既有 RRF 融合、按 `(scope,memory_id,version)` 去重；跨版本不能合成同一快照。
4. 不执行“最近 candidate 列表补足”，不因为零命中再用整个聊天或模型改写查询。

两个查询都不对 subject/kind/machine/module/target 做相等过滤：这些可能是待更正值，也可能未知。
已知条件保留给 B 比较，不能用新值的精确筛选把旧值提前排除。归属 scope 和项目访问边界始终是硬条件。
只搜索本提议最终目标 scope；项目和个人记忆属于不同归属，不能把一个范围的旧项更新成另一个范围。

查询候选为当前可访问的 `active/candidate` 现行版本，包含其中已过期者；排除 inactive、forgotten 和历史版本。
结果显式带 `expired` 布尔值、原状态和原到期时间，命中不改变状态。前台/public search 的默认过期过滤保持。
后台比较不使用前台全文覆盖率门槛；仍保留 FTS、版本/digest 一致性、明确身份锚点和参数化 SQL。

### 4.2 唯一内部读取入口

新增 `MemoryAccess.comparison_candidates(source_session_id, project_id, *, plan, allowed, now=None)`，仅后台 runner 调用。
`plan` 是程序创建的内部 QueryPlan，含 scope、两条以内查询、必需身份锚点与限额，不能从 MemoryInput 附加字段构造。
该入口复用 `query` 的会话/项目绑定、关闭状态、personal_only 和 `_stores` 校验；必要时提取直接共享的绑定函数。
不复制一套较弱的 scope 判断，不新增通用 resolver、注册表或任意 SQL policy 回调。

每个查询先以对应现有 `MemoryInput(action='search', scope=..., status=...)` 检查 `allowed`，active/candidate/expired 三种状态都检查。
读取每个旧项前再检查其现有 `MemoryInput(action='read', scope, status, memory_id, version)`；任一必要权限被拒，该提议失败。
内部 SQL 可一次筛选三类记录，不因此跳过三个权限条件；禁止改用 status=all 绕过针对 inactive/expired 的规则。
`allowed` 仅传 `MemoryJobs.allowed`，不接收模型函数；保留现有 deny 规则的完整规范化请求载荷。
复用该既有函数作为直接参数，是因为逐条 read 权限须在候选 ID 实际产生后检查；不新增回调注册层或可配置权限策略。

底层增加固定用途 `rank_comparison`，共享 tokenizer、FTS 权重和版本/digest 门禁；公开 `rank` 锚点/覆盖语义不变。
读取出的正文必须完整且满足现有正文上限；旧证据用 S2 的同一可见性/状态投影，不直接读取原表 quote 绕开会话删除。
内部每条旧项最多消费 8 个已存来源片段；公共 `source_limit` 的展示默认不变，不给工具增加 override 参数。
原证据合法缺失是 `unavailable` 状态，可携旧正文参与比较；读取异常、正文截断、版本变化不能伪装成证据缺失。

### 4.3 检索结果与完整比较组

内部返回 `ProposalMatches{proposal_id, rows, limited, reason}`；每行至少有下面这些程序产生的字段：

`old_ref, scope, memory_id, version, kind, status, expired, title, body, conditions, aliases, gaps, basis, reason, evidence_at, last_confirmed_at, expires_at, deadline_at, sources`。

`old_ref` 为本批 `O1...`，来源引用为 `O1:S1...`；真实 ID/版本由程序映射，不允许模型创造目标。
同一 ID 查询到不同版本、候选上限触达、读取不完整或任何查询错误，都使关联提议 `limited/failed`；不以空数组继续新增。
既有 `has_more/truncated` 信号必须保留。`limited=false, rows=[]` 仅表示本次有限查询没有命中，不证明全库无同义记忆。

提议是图的一侧，查到的旧 ID 是另一侧；共享任一旧 ID 的提议组成一个连通组。没有旧项的提议各自成组。
完整组指本次已发现、需要共同处理的材料，不是全库语义完整性；有失败提议已知关联某旧 ID 时，其整组跳过。
按最小 proposal 序号稳定装组；若整组超过旧项数、字节或 token 上限，整组跳过，再尝试其他独立组。
已经决定跳过的组本任务不再执行；不能截掉一条已知冲突项，或拆开共享 M17 的 P1/P2 各自更新。

## 5. B 输入、输出及提交映射

### 5.1 一次批量复核

至少有一组可处理提议时调用一次 B，即使所有组都没有旧项；零有效组不调用 B。
B 输入包含 `proposals/groups/new_evidence/old_memories`。新证据按 ID 去重；旧项和证据只存一份，用引用连接。
每个新提议附完整原文、S1 context_units/context_complete、角色、程序认定来源和原始时间；不能只给 A 自己的摘要让 B 自证。
旧证据 unavailable 必须明确显示原因；概览、另一条记忆正文和 B 自己的判断不能替代新来源。
B 检查事项/主张/对象身份是否忠于新证据，条件是否相同，是否独立新确认，以及是否足以覆盖旧项整个主张。
同模型第二次调用不是语义正确性的保证；固定模型评测按 S0 分别统计 A 漏提、检索漏项和 B 误判。

### 5.2 严格输出 schema

根对象只有 `groups`。每个已输入 group_id 恰好出现一次；组中 `decisions` 必须完整覆盖其 proposal_ids，每个 P 恰好一次。
一项决定可消费多个同义提议；不能重复消费 P，也不能引用其他组的 P/O/E。整体 JSON 不可解析则整次 B 失败。
局部组 schema/引用不合法时跳过该组，其他可独立验证的组仍可提交；不追加格式修复调用。

| 字段 | 合同 |
| --- | --- |
| `proposal_ids` | 本组非空 P 列表；最多 8 个，不能重复 |
| `relation` | `new/duplicate/supplement/correction/different_conditions/unresolved_conflict/unsupported` |
| `target_old_ref` | `supplement/correction/duplicate` 必须指定本组 O；其他关系必须 null |
| `support` | `direct/inference/unsupported`；unsupported 必须丢弃，不转存 candidate |
| `confirmation_scope` | `whole/partial/unknown`；表示新证据支持整个旧项还是部分/未知，供 S2 内部时间门禁 |
| `claim` | 保存关系提供完整主张对象；duplicate/unsupported 为 null；kind/scope 只能沿输入确定值 |
| `new_evidence_ids` | 1～8 个本决定所消费 P 引用过的 E；拒绝决定可为空；不能引用旧证据代替新确认 |
| `old_evidence_refs` | 最多 8 个实际提供的 `O:S`，只解释比较依据，不进入新来源或 freshness |
| `reason/gaps` | reason≤300，gaps≤8 项且每项≤128；内部理由为数据，不写完整引文到日志 |

`claim` 使用 A 的 kind/subject/body/basis/intent/title/reason/machine/module/target/aliases/gaps 字段；body 沿现有 MemoryProposal 的3200字符/3200 UTF-8字节上限，允许完整保留较长旧正文，不强行压成A的800字符短提议。
由多条 P 合并时须兼容 kind/scope；不得通过合并删除重要条件或引入未被新证据支持的事实。
schema 不开放 action、memory_id、expected_version、deadline、tags、来源身份或任何可信时间字段。
模型输出另一个范围、类型或数组外 ID，不纠正后继续写入，直接拒绝受影响组。

### 5.3 关系到业务动作

| B 关系 | 程序动作与必要条件 |
| --- | --- |
| new | 本次查询完整且无相关旧项，包含已有命中经 B 判定均不相关的情况；证据独立支持后 record，inference 只能 candidate |
| duplicate | 同一主张/已有来源，无写入、不续期；有真正新确认应判 supplement |
| supplement | 同 ID update；whole 且 fresh 才可能续期；partial/unknown 仅语义字段完全相同的补证 |
| correction | 同 ID update；whole、同 kind/scope、支持新完整正文且本次更正来源时间严格晚于 old.evidence_at |
| different_conditions | 条件明确不同才 record；条件未知不是“不同”，不能用来绕过旧项 |
| unresolved_conflict | 正常完成比较且新主张确有依据时 record candidate，并固定加入 unresolved_conflict 缺口 |
| unsupported | 丢弃，不写 active/candidate；记录固定原因 |

有旧项但 B 认为均不相关时可以 relation=new，须在 reason 说明；不能因为发生查询/读取错误获得这个分支。
现有 writer.matches 的精确冲突门禁继续生效；正文限定虽不同但结构条件仍冲突时，最终可降为带 known_conflict 的 candidate。
不通过改写 subject 避开该门禁，也不增加“B 已批准所以忽略冲突”的参数；S0 记为保守未激活，后续按实际证据决定是否需要条件字段。
未知适用条件保持在正文/gaps；不能用 null 表示所有环境通用，也不能凭更近的处理时间推翻明确旧约定。
同一旧 ID 在本次 B 结果中最多一个写决定；多条必须先合成一个完整决定，重复目标使所在组无效。
补证/更正时旧 tags、deadline 沿用；省略的可选 metadata 保持旧值，显式空 aliases/gaps 才表示经支持的清空。
同义 subject 不需字面相等；相同 subject 也不能跳过条件和证据比较。

## 6. 提交、原始时间与失败传播

### 6.1 唯一受控写入入口

`compile_comparison_decisions(...)` 只构造现有 `MemoryInput(record/update, scope, proposal, sources, memory_id, expected_version)`。
memory_id/expected_version 来自本批 O 的真实快照；record 不带二者。新 sources 只由 S1/S2 绑定后的 E 转换。
先验证整个组的引用、目标唯一性、kind/scope/时序和结果字段，再开始该组第一项写入。

runner 对每项完整规范化 request 执行 `service.allowed(request)`，然后调用：

```python
MemoryAccess.commit_comparison(source_session_id, project_id, request, *, confirmation_scope)
```

此方法是应用内部入口，不注册为工具，不增加公共 action 或 payload 字段；再次复用 query 的绑定校验与 `_mutate` 单库事务。
`_mutate(..., confirmation_scope=...)` 传给 S2 `commit(..., *, confirmation_scope=None)`；普通公开 query 路径仍传 None，维持原合同。
禁止把确认范围藏入 request JSON、tags 或 reason 并在下层解析为权限；公开额外字段仍被严格拒绝。
每项提交沿既有 `edit_with_sources -> resolve -> suppressed -> CAS -> writer.save`；不另建旁路写表。

### 6.2 时间与复合旧项

S2 的消息身份用于 fresh 与遗忘抑制，span 身份只用于保留同消息不同引文；换 E 编号、换 offset 或 overlap 不制造新确认。
fresh 检查该记忆全部历史版本已知消息身份，不能把曾被当前8条来源上限挤出的旧消息重新当新来源。
occurred_at 使用原归档时间，不用模型生成时间、B 完成时间或本次入库时间证明“更新”。
只有 `confirmation_scope=whole` 且存在比旧 evidence_at 晚、与 basis 匹配的独立消息来源，后台才允许刷新证据时间/确认时间/TTL。
更正还要求与 basis 对应的真实用户声明/成功工具证据晚于旧 evidence_at；不能附一条较新的无关工具结果替旧声明过时序门禁。
partial/unknown 时，要求 body/kind/subject/machine/module/target 均与旧值相同；否则跳过 `memory_partial_confirmation`。
仅补证保持全部旧语义字段、status、evidence_at、last_confirmed_at、ttl_days、expires_at；不能使过期或非 active 旧项整体转正。
仅补证不能用新 span 挤掉原当前证据；合并去重后超过8条则 `memory_source_limit`，整项零写入，不暗中删来源。
例如旧项同时写“本地用系统 Python；CI 用镜像 X”，新证据只确认本地：不把整个复合项改写或续期，本片不自动拆分旧复合记忆。
whole 的语义判断仍来自 B；程序能校验枚举、引用、原时序和目标，不能证明语义完整性，必须以负例评测约束。

CAS、来源更正、权限变化、遗忘、敏感内容、类型变化或写入异常都跳过本项/关联组；不重读后再比较，不改成 record，不降级保存 candidate。
独立单库成功保留；若同组多项先有成功后有失败，不跨库回滚已确认结果，也不重放。后续共享失败目标的决定不可继续。
沿现有 `_commit/await_settled` 等待必要保存结束再计数，停止不能把已成功的保存误报为未发生。

### 6.3 概览与批次结束

A 合法概览在 B 前尝试一次保存，使用 S1 的真实读过范围和遗漏标记；失败不阻止独立证据提议进入比较。
save_overview 事务内核对分支/history_version；当前概览已覆盖较后位置时，较旧范围的结果拒绝覆盖并记录固定原因。
不能把“当前 upper 取较大值”贴到只总结较旧范围的正文上；局部来源/重叠不自动补齐历史缺口。
旧范围显式整理仍可产生合法记忆，但概览缺口不由该副作用自动消除；不新增自动重建概览调用。
来源批次完成或失败后均前进到本批已准备的 next_position，失败范围记入 skipped；不因下一批重新执行失败 A/B。
A 前连批次边界都无法确定的准备故障结束任务；已知边界且后续可独立理解时，剩余预算内可处理新批次。
全局停止/超时立即结束，任务触发基线沿现有 accept 前移规则，不新增持久成功游标。

## 7. 正常与失败调用链

正常：`MemoryJobs -> drive -> prepare A/S1 build_batch -> A invoke -> parse/bind -> save_overview -> comparison_candidates -> group/pack -> B invoke -> validate/compile -> allowed -> commit_comparison -> S2 commit -> writer.save`。

其中 prepare/bind/compile 不写记忆；A/B 只返回建议；概览和每次记忆写入各自是必要保存，日志/用量展示是观察。
模型输入仅在任务内存存活，任务终结后释放，不保存供恢复的模型响应、提议包或证据副本。

失败例：P1 查到 M17，B 判断更正；在提交前 M17 从 v2 变 v3。`commit_comparison -> commit` 抛 memory_conflict。
runner 记录本组版本冲突并跳过，A 的概览及其他独立成功项保留；没有第 2 次 B、没有新增替代项，也不影响已结束的主 Agent 结果。

另例：一组读旧正文失败、另一组正常且无旧项。前组直接跳过，B 只复核后组；若整次 B 超时，两组均不能以候选兜底。

## 8. 预算、模型适配与诊断

默认值是本片实现及 S0 评测起点，不读取或修改用户真实配置；配置变更仍按项目授权规则执行。

| 限额 | 首版值/配置归属 | 达限处理 |
| --- | --- | --- |
| A/B 每次输入/输出 | 复用 max_input_tokens=16000、max_output_tokens=4096，并服从模型更小上限 | schema/system 计入，发送前失败则跳过，不静默换协议 |
| 每批提议/任务批次/时限 | 复用 max_proposals=8、max_batches=3、task_timeout_seconds=300 | 超额不加调用；时限含来源等待和槽位等待 |
| 每提议查询 | 新 comparison_queries=2，允许 1～2；第二条只在有身份词且配置为2时生成 | 计划外不补查；不能失败后换词 |
| 每条查询长度/词数 | 新 comparison_query_chars=256（32～512）、comparison_query_terms=64（8～128），同时不超过公共上限 | 超限提议跳过，不截去必要身份/条件 |
| 每提议旧项/整次 B 旧项 | 每提议最多 min(related_limit,max_limit)，整次 B 去重后最多 related_limit，默认均8 | has_more 或整组超限则跳过，related_limit=0 不解释成已查无旧项 |
| SQL 候选 | 复用 candidate_limit=100 与现有 RRF/权重 | 触达且有更多结果时 limited；不全量扫描 |
| B 材料 UTF-8 | 新 comparison_material_bytes=65536（4096～262144），包含提议/原文/旧项，不含重复共享项 | 与 token 取更严格者，整组装入或整组跳过 |
| 每条旧证据 | 最多8个已存 span，每个quote≤300字符，沿 S2 合同 | 必要证据缺失可标 unavailable；读取/输出截断不装作完整 |

每批 A 最多一次、B 最多一次；没有有效提议或所有组被拒绝时 B=0。3 批最多6次逻辑调用，不是每个提议各调用一次。
不新增业务重试或格式修复；既有 SDK 传输重试配置保持，实际 attempts 与逻辑 calls 分开统计并受既有任务期限约束。
任务最大计划输出为 `2 * max_batches * max_output_tokens`，输入为相应上限之和；实际不足不补足额度，不提高现有300秒默认。
A/B 使用任务已冻结的同一 model/model_id；分别调用 prepare_memory_output，schema name 固定 `MemoryExtractionResult`/`MemoryComparisonResult`。
保留 text/json_schema/function_calling 三协议、严格本地解析、原生异步及同步模型线程分支；不得静默换模型、协议或另开 Agent。
迁移现有 `_content` 的固定 MemoryResult 名称校验为调用方传入预期 schema 名称，raw/解析工具调用必须同名同ID且仅一项；保留 length/refusal/error/invalid_tool_calls 拒绝。

沿既有 memory 观察加入 `memory_stage=extract|compare`、task_id/batch；不将其写入用户对话或当模型可信输入。
现有 ModelUsageCallback 在 start 收到这些 metadata 时，写固定关联日志 `task_id/batch/memory_stage/invocation_id`；只关联现有调用与usage/HTTP-attempt事件，不增加公开事件字段。
按该 invocation_id 汇总 A/B 的实际用量和attempts，不能把结构化输出 wrapper 的 run_id 当成底层模型调用ID；关联缺失记未知。
统计 A/B逻辑调用、SDK attempts、输入/输出 usage、查询数、候选数、组数、跳过原因及等待/调用/保存耗时；缺失 usage 为未知。
`saved/overview_saved/failed/skipped` 保持可对应实际结果，duplicate/unsupported 属已判断无写入，运行失败不能混为“无记忆”。
固定原因至少包括 query_limited、comparison_budget、comparison_output_invalid、target_version_changed、partial_confirmation、source_invalid、permission_denied。
日志只含阶段、身份、计数和固定错误码，不包含完整提议、私密引文、提示或模型正文；观察投递失败不能覆盖必要提交结果/原异常。

## 9. TDD 验收矩阵

先编写以下行为失败测试，确认失败来自现有单阶段/查询/时间行为，再实现最小闭环；迁移测试 helper 必须说明双响应输入的原因。

| 编号 | 输入/故障 | 必须断言 |
| --- | --- | --- |
| T01 | 新值 `.venv`，旧值系统 Python | A无旧项；事项查询命中旧值；B输出同ID一次更新 |
| T02 | 同事实不同subject、同subject不同条件 | 前者比较，后者不误覆盖；纯正文条件仍命中硬冲突时candidate，并存不保证全active |
| T03 | 无旧项、零提议、全提议无效 | 分别 A+B、仅A、仅A；不按提议数倍增 |
| T04 | active/candidate/expired/inactive/forgotten | 前三种进入比较且状态清楚；后两种排除；public默认仍排过期 |
| T05 | 查询故障/候选截断/正文不全/读取中版本变 | 关联组跳过；无备用新增/候选、无改词补查 |
| T06 | P1/P2共享M17；B两次写M17/跨组引用 | 整组校验失败，零写入；独立组可保留 |
| T07 | 一组超字节/token/旧项限额，另组完整 | 前组整组跳过；B只含后组；无第2次B |
| T08 | A JSON破损/合法概览加坏提议/B结构错误 | 全局与局部失败边界准确；概览/记忆独立成功 |
| T09 | 真引用配反义结论、限定仅在未引用context、临时变长期 | 必须依赖未引用限定则skip；硬门禁测试与S0真实模型负例分别报告 |
| T10 | 旧证据unavailable，新明确更正/仅“试试” | 可支持的独立新证据路径成立；不可因旧证据缺失放宽 |
| T11 | 同消息换span/overlap/较旧来源重整 | 不制造fresh、不续期、不反向更正当前决定 |
| T12 | 复合旧项仅部分确认 | 相同正文可补span且日期全保持；改正文拒绝；过期不转正 |
| T13 | 全新whole确认/原kind不同/模型伪造scope | whole+fresh按S2续期；跨kind/scope拒绝且不降级record |
| T14 | B返回后改版本/删除来源/遗忘/权限拒绝 | 当前事务门禁阻止写入；不重比、不重放 |
| T15 | 一项已保存后另一项失败/停止中保存完成 | 成功保留且计数准确；无跨库回滚/回执/恢复任务 |
| T16 | 较新概览已到100，本批仅30～40 | 原概览不被旧正文覆盖；合法记忆可独立保存 |
| T17 | A失败边界已知后有独立新批/全局超时 | 新批可前进；失败批不重跑；超时不追加任何模型调用 |
| T18 | 三输出协议、原生异步/同步、模型更小context | A/B各自schema及预算生效，实际请求计数正确，无协议fallback |
| T19 | 公共payload注入comparison/confirmation字段 | MemoryInput拒绝；公开query/update原签名语义无回归 |
| T20 | 私密材料、usage缺失、观察异常 | 日志无正文；未知不填0；保存结果/主异常身份保持 |

聚焦新增 `tests/test_memory_proposal_comparison.py`、`tests/test_memory_comparison_queries.py`，并扩展已有 runner、structured_output、lifecycle、overview_progress、task_diagnostics 测试。
外部模型只在 S0 指定的固定语料/次数下独立评测；单元测试通过不等于提议召回或语义判定质量通过。

## 10. 影响文件、清理与验证命令

新增 `application/memory/memory_proposals.py`：A/B严格schema、纯解析及决定编译；新增 `memory_comparison.py`：查询计划、分组、预算、B输入和编排。
runner保留任务/槽位/期限/概览/必要提交归属；不把存储事务放入模型模块。各文件≤500行，超限按实际职责继续拆分。
修改 `memory_batches.py` 消费S1绑定；`memory_access.py` 和 `storage/memory_sql/memory_queries.py` 接入内部读取/提交；`memory_mutations.py` 的确认范围由S2实现。
修改 `memory_settings.py` 与现有配置参考/解析测试，新增限额不得只定义不消费；不修改用户实际settings/.env。
修改 `memory_job_runner.py`、`memory_overview_store.py`、必要任务结果投影/观察消费及上述测试；公开字段确需扩展时同步消费者，不私自改变工具签名。
`agent_runtime/model_usage_observation.py` 仅消费已有调用 metadata 记录阶段关联日志；扩展 `test_memory_model_observation.py`，现有usage存储/公开schema保持。

删除被替换的 `_related/_local_candidate`、旧单次PROMPT/related挤压A预算分支、后台手抄引文提议适配及旧单阶段schema。
S1 的 `bind_proposal_evidence` 和公共精确quote写入保持；不删除工具公开来源校验，不为旧测试保留废弃后台包装。
迁移 flat_contract/structured_output/probe 等实际调用方到A/B；保留原结构拒绝、协议、来源、时间和独立成功断言。
结束时用 rg 列出旧符号剩余引用，区分历史Spec与真实消费者；不顺带修改前台S4语义或模型传输重试配置。

```bash
.venv/bin/python -m pytest tests/test_memory_proposal_comparison.py tests/test_memory_comparison_queries.py tests/test_memory_job_runner.py tests/test_memory_structured_output.py tests/test_memory_lifecycle.py tests/test_memory_overview_progress.py tests/test_memory_task_diagnostics.py -q
.venv/bin/python -m compileall -q naiyue_test_agent
git diff --check
```

全量与发行构建使用[总索引的标准验证入口](143bm-memory-optimization.md)：`scripts.native_assets` 准备工具，`scripts.ci_shards` 跑后端全量，随后按其参数运行 `scripts.build_release` 与 `scripts.verify_wheel_install`；不用裸 `python -m build` 或手工复制模块替代。
缺少本机 fd/rg 或标准构建准备时如实报告环境条件，不删断言或跳过用例。
以上是实施时必须运行的命令，本次文档编写未运行产品测试/构建/真实模型。完成记录须分别列失败复现、聚焦/全量/构建实际结果、L1文件/L2实现/L3接线及S0质量成本报告。


## 11. 实施进度（2026-11-16，尚未完成）

本轮先实现 comparison_candidates / rank_comparison 和 commit_comparison 内部入口。抽取原query绑定校验由三入口直接共享，公开payload与检索过滤不变。内部固定SQL使用词法OR及显式身份锚点，包含active/candidate中已过期者，排除inactive/forgotten；逐查询检查三类状态权限、逐ID检查read权限，保留版本/digest、来源可见性及候选截断门禁。related_limit=0直接失败，不伪装成无旧项。来源最多8段经现有source_reference读取，不绕过归档门禁。

三项核心行为先红（入口不存在）再绿，另补身份、停用、read拒绝及零限额边界。记忆与探针相关401项通过，标准macos-arm64构建退出0（build/spec143bm-s3/reads-tests.log、reads-build.log）。尚未运行后端全量或真实模型。A/B schema、分组/材料预算、runner接线、S0旧项矩阵和旧路径迁移均未完成；新内部入口当前只有测试消费者，不宣称L3后台接线或S3完成。未新增锁/改变锁顺序；未改真实配置或用户库，未提交发布。


## 12. 两阶段实施与迁移（2026-11-16）

memory_proposals负责A/B严格本地合同、局部overview验证及完整组决定编译；memory_comparison负责固定事项查询、连通组与整组预算；memory_model_json仅共享原有严格原始响应解析。runner已实际调用比较读取和commit_comparison，A不再读旧项，每批一次A、最多一次B。无效组可跳过而独立组提交；普通失败在已知批次边界前进，停止/全局超时仍立即结束，必要保存沿既有await_settled计数。

来源在比较前回查当前归档与消息级抑制，最终提交再原子回查；比较阶段完整原文的版本/digest、来源可读状态与程序核验属性随旧材料送入B。新旧时序不足的更正被拒；whole变更正文在最终commit还必须存在匹配basis的fresh，公开None分支保持原行为。D1入口扩展及预检分段事务顺序已同步lock-hierarchy，无新增锁。

旧_related/_local_candidate、单阶段PROMPT、MemoryResult模型schema、parse_model_result与proposal_request生产入口已删除，S1 binder和公共精确引用仍保留。原纯适配测试通过测试专用compile_request构造A绑定与B快照，保留字段、版本、来源、metadata及拒绝断言；worker测试在调用点显式准备staged_replies，真实SDK测试为每种协议显式响应A/B两次，未放宽保存、权限或事件配对断言。旧最近候选补足测试改为事项读取/来源不可读/无补查/权限/版本/预算合同，不保留废弃fallback给旧测试。

probe按memory_stage解析A/B，不再将B误当含overview的A；两个套件按每例最坏2次在读取配置前预检，默认repeat=1、max_batches=1；无真实请求。回调写task/batch/stage/invocation安全关联日志，继续原usage/HTTP-attempt事件，不新增公开事件字段。比较准备日志记录实际查询数、候选/组数和耗时；未知模型usage继续为未知。

新增comparison_s3.json是8条显式旧态、时间与受控决定的calibration矩阵，由真实drive提交并断言副作用、TTL及两次调用，哈希冻结在protocol revision3。它补充S0工程材料，不能冒称采样A/B判断准确率或完整S0。旧96条controlled-proposal入口保持公共写入适配，仍能在隔离旧源码运行；B标N/A表示本入口未采样B，与S3是否接线分开。


最终工程验证：后端八片6843项（0失败/错误/跳过）；记忆/迁移/评测专项542项；前端78文件688项；标准macos-arm64构建、Twine、源外安装烟测、compileall与diff检查通过。最终wheel中21份相关源码逐字节匹配；安装烟测实际两次后台模型调用、独立usage/任务、重开及跨会话召回通过，真实provider_requests=0。报告build/spec143bm-s3/verification.json及final-*；早期迁移红灯和首轮6841报告保留但不作为最终版本验证。

隔离旧版12693a1只覆盖当前评测脚本与合成数据后，revision3校准仍可执行：受控公共提议旧21/24、当前22/24，逐例无回退；八条semantic未执行，B未采样，evaluate按已知反例返回1。该对照包含S2片段收益，不归因于B；不宣称S0业务门槛通过。S3工程主链已接线，真实判断质量、S0完整矩阵/人工评分和S4仍待完成。未提交、发布或修改真实配置/用户库。


## 13. S3审查修复（2026-11-16）

修复五项边界：比较读取返回后立即记录全部已发现旧ID，跨提议版本不一致仍保留失败关联，完整连通组跳过；B路由先校验group_id字符串类型，数组/对象等局部坏组不阻止独立合法组；更新不显式重复设置旧deadline，由提交层沿用旧值，避免工具补证被误当用户新设截止期；可选target先继承再检查更正时序；比较预算拒绝记录memory_comparison_budget失败诊断，任务按已有规则标为partial/failed，不新增重试。

新增tests/test_memory_s3_review_fixes.py：先观察6例预期失败，再修复转绿；另补whole/partial两种真实SQLite工具补证与截止期保留测试。原预算两例改为精确断言固定失败原因，保留独立组与整组跳过断言。未改公开payload、锁顺序、DB结构或真实配置。

验证：新增8项、相关550项通过；最终工作区后端四片1856+1533+1764+1705=6858项，0失败/错误/跳过；前端688项、标准Mac构建、Twine、源外安装、compileall与diff检查通过。三个修改生产模块与wheel逐字节一致，provider_requests=0。证据build/spec143bm-s3-fixes/verification.json及同目录日志/XML。全量与产物包含保留的并行工作区改动，不作隔离发布声明。未提交发布，真实模型语义质量仍待S0。

## 15. 首轮真实校准后的输出合同修正（2026-11-16）

沿原权限/范围语义做最小表达修正：A明确缺少跨项目声明时使用workspace，kind=user
不自动等于personal，持续时间不等于跨项目适用。B文本通道在实际SystemMessage中附本地
ComparisonResult.model_json_schema（同程序校验源），明确scope由程序继承且不得复制到claim。
生成消息函数也用于比较材料token预算，schema占用不得绕过cap。保留extra=forbid、
不删字段抢救返回、不重试。先验证实际消息/本地schema一致，再跑确定性回归及同模型完整
8条calibration新run_id；旧采样和标签不改，不针对失败案例单独补跑。

最终工程验证：当前工作区后端四片1825+1759+1759+1569=6912项通过，0失败/错误/跳过；记忆470项通过；标准Mac构建/Twine、compileall/diff及2生产文件wheel一致通过。证据build/spec143bm-semantic-calibration/verification.json与fix-backend-*.xml。包含保留并行工作区内容，不作为隔离发布声明。
