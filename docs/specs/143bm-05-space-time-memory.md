# Spec143BM-S5：空间与时间两维记忆

2026-11-17 输出合同修订：[S6 程序装配动作](143bm-06-program-owned-memory-actions.md) 已获用户确认。B 不再重抄部分确认的旧正文或 A 的 kind；其余空间/时间、证据、CAS、TTL 和失败不重试约定保持。

日期：2026-11-16；状态更新于 2026-11-17。**S5-1/S5-2 已接入当前代码，阶段工程验证通过；S5-3 首轮后台真实校准已有结果，效果尚未验收。** 当前待办统一进入 [S8 第 14 节](143bm-08-memory-tool-action-contract.md#14-s0s8-未完成事项统一对账与关闭标准)，包含 S6 修复后的新后台批次及前台/独立留出验证；历史结果详见配套评测协议文末。

入口：[总索引](143bm-memory-optimization.md)。配套：[S5 评测与基线协议](143bm-05-evaluation.md)。
核对基线：HEAD `8c5a6f07c9976aac8f232b07f99a9481a23e37e4` 加当前未提交的 S0–S4；HEAD 单独不能重建当前方案。
实施基于已冻结的旧链快照；仅验证临时库，不修改真实配置或升级真实用户库。

## 1. 用一个正常例子和一个失败例子说明目标

用户说：“以后无论在哪个项目，分析问题都先核对事实，再列假设和验证办法。本项目这周临时走测试网关 B。”

后台沿现有触发条件启动，A 提出两条记忆，分别引用完整的原文限定：

| 正文 | 空间 scope | 时间 kind | 默认有效期 |
| --- | --- | --- | --- |
| 分析问题先核对事实，再列假设和验证办法 | personal | convention | 90 天 |
| 本项目本周临时使用测试网关 B | workspace | context | 30 天，上下文中的“本周”仍必须保留 |

B 对照引用和旧项审核；代码核对身份、来源、版本、权限与生命周期后保存。有效期是可召回的上限，不得把正文中的更短适用期限抹掉；已有显式 deadline 继续取更早者，模型不得编造可信时间戳。“本周”须结合该证据的时间理解，不能在下周召回时自动变成下周。

下一轮用户问“分析这次测试失败”：个人长期约定在完整装得下时预先注入；项目网关事实通过任务检索进入上下文。无需用户再次说“先列假设”。

失败例子：原话是“仅在这个项目中先列假设”，A 却提出 personal。B 应判 unsupported 并跳过；不会改存 candidate，也不追加修复调用。若 A/B 都误判，真实引用检查仍不能证明语义正确；这是待测的模型风险，不能写成代码已保证拦截。

## 2. 已确认的业务规则

1. 空间只有 personal、workspace；both 只是读取选择器，不能成为存储范围。
2. 时间只有 convention（长期约定）和 context（阶段事实）。取消独立 user/preference 类型以及其他语义细分。
3. 长期约定默认 90 天，阶段事实默认 30 天；原“偏好默认不过期”的例外取消。三个月按 90×86400 秒执行，不做自然月运算。
4. 阶段性约定也属于 context；稳定习惯、持续选择可归 convention，不另外区分规则、决策或经验。
5. “内容抽象”“要求模型如何思考”“没有项目字段”都不是 personal 的证明；来源仍须支持跨项目。
6. 在项目会话中，范围不明确但内容本身有充分依据，A 使用 workspace；缺少持续性依据时，有价值的当前事实可用 context。无依据的内容直接不提出，不能用短 TTL 挽救错误事实。
7. 普通聊天仍沿 personal_only 边界；没有项目身份时不得伪造 workspace，也不能把本来项目限定的内容自动放宽到 personal。
8. 默认召回仍为个人＋当前项目；其他项目 workspace 记录不可读。正文可跨项目召回不等于源引文跨项目可展开。
9. 删除固定“三条偏好”选择。有效个人长期约定完整装得下时预先加载；装不下时只执行正常任务检索。
10. 失败跳过、不重试、不补跑、不把失败材料存 candidate。受支持但冲突未解的候选仍沿既有状态合同处理。

不新增重要性分数、置顶标签、偏好子类、自动画像总结、额外召回模型、向量依赖或管理 UI。后台轮数/字数触发、工具结果删减与证据输入规则不因 S5 改变。

## 3. 当前实现与本片改动

| 当前代码事实 | S5 改动 |
| --- | --- |
| `tools/memory.py`、`memory_writes.py` 接受七种 kind | 当前请求与当前记录只接受 convention/context |
| `memory_settings.py` 中 user/rule/decision 的 active TTL 缺省为不过期 | 两类 active TTL 都有明确默认值，不因 scope 改变 |
| `memory_context.py` 对 kind=user 做 list，默认最多 3 条 | 查询 personal＋convention 的有界完整集合；取消按更新时间取固定条数的注入行为 |
| list 返回正文预览，且可能受条数/输出字节限制 | 不能据第一页预览宣称已取得全部个人约定；必要的逐条 read 也有明确上限 |
| B 编译和 commit 都禁止变更 kind | 只允许后台 whole＋独立新证据的受控时间类型变更 |
| `MemoryWriter.matches` 把 kind 计入同事项冲突匹配 | 时间类别不再作为事项身份；变更 kind 不能绕开重复/冲突门禁 |
| 前台 record/update 经公共提交门禁，不经后台 B | 本片保留该链路并单独评测，不默认为已具有双模型审核 |

来源核对入口：`memory_job_runner.drive`、`bind_extraction`、`compile_comparison_decisions`、`MemoryAccess.commit_comparison/query`、`memory_mutations.commit`、`prepare_memory_parts`。

## 4. 接口、状态和责任

### 4.1 对外合同

- 保留 `MemoryInput.kind` 和 `MemoryProposal.kind` 字段名；新枚举为 `Literal['convention','context']`，缺省的读取 kind 仍为 None。
- action、scope、来源结构、ID、版本和返回分页语义保持；`MemoryAPI.query`、`MemoryAccess.query`、`prepare_memory_parts` 函数签名不变。
- 枚举收窄是本次已批准分类调整的可见变化。工具 schema、描述、测试、探针和当前文档必须一起切换；不把旧枚举悄悄作为新写入别名。
- 实际消费者：模型工具调用、后台编译器、运行前上下文装配、评测与发布 smoke。历史 snapshot 可保留当时的 kind，不等于新请求仍支持旧枚举。
- 不新增 `scope_evidence`、`importance`、`is_preference` 或 `always_inject` 字段。复用正式 sources；A/B 必须分别核对内容、空间和持续性。

### 4.2 配置

| 配置 | 新合同 |
| --- | --- |
| active_ttl_days | 仅 convention/context；默认 90/30，值继续是有界正整数，拒绝 bool |
| candidate_ttl_days | 仅 convention/context；默认均 30；候选不自动注入 |
| personal_convention_tokens | 新增，默认 800，合法整数 0..8000；0 禁用预加载；有效值为与 injection_tokens 的较小值 |
| injection_tokens | 沿用总预算，默认 2000；包含个人约定、相关记忆、目录及提示包裹 |
| preference_limit | 删除运行时消费和最终设置字段；仅配置读取边界可作一次明确兼容转换，见下文 |

800 是首版实验初值，不是测量得到的最优值。配置经 `load_settings → MemorySettings → prepare_memory_parts` 实际生效，不只声明字段。

旧 preference_limit 的外部配置确有消费者，允许在读取边界窄化转换：新字段未提供时，旧值 0 转为新预算 0，旧正值采用新默认预算并记录弃用诊断；同时提供时新字段优先。校验旧值原有类型/范围，不能把非法旧值吞掉。转换后运行对象不保留 preference_limit，生产分支不再按它挑条数。

旧 TTL 字典中若存在不再支持的 kind，不能猜测多个自定义期限如何合并；沿 `memory_settings_invalid` 明确禁用记忆并提示迁移字段，应用其他能力照常。示例与升级说明列出新配置，不能擅改真实 settings/.env。无 memory 配置的用户采用新默认。

### 4.3 状态不变

active/candidate/inactive/forgotten 与派生 expired 沿原合同。expired 不是新增持久状态。读取、预加载、普通命中、duplicate 和部分确认均不延长有效期。

## 5. 写入主链和时间类型变更

```text
归档保存成功 → 原轮数/字数触发 → build_batch/model_batch
  → A：两类提议＋scope＋E 引用 → bind_extraction
  → query_plan/comparison_candidates：同范围查同事项，不以 kind 隔离
  → B：正文、范围、持续性、旧项关系、whole/partial/unknown
  → compile_comparison_decisions → MemoryInput＋程序拥有的确认范围
  → commit_comparison → 来源重验/CAS/冲突/TTL → 单库事务
```

| 数据 | 生产者与形状 | 消费者与约束 |
| --- | --- | --- |
| A 提议 | 新 kind＋scope＋正文＋正式 E 引用 | binder 保持来源唯一映射，不让模型提供可信 ID/时间 |
| B 决定 | 现有 Decision，claim.kind 使用新枚举 | B 不能改 A 的 scope/kind 来救回错误提议；可选择符合新证据的不同旧 kind 目标 |
| 新旧时间差异 | target.kind 与 claim.kind 不同 | 编译器和 commit 双重检查，不能只改 prompt |
| 保存结果 | 同一 ID 的下一 version、真实 sources、到期时刻 | 后续读取/索引/审计；不能只改数据库字段而漏更新快照或 FTS |

时间变更例子：“这周试用 pytest”（context）后来变成“本项目以后统一 pytest”（convention）。允许对同一 ID 更新，而不是因 kind 不同另存重复项。

变更必须同时满足：后台受控比较入口；relation=correction；confirmation_scope=whole；有晚于旧 evidence_at 且从未用于该记忆确认的真实用户消息；新引用独立支持完整的新正文、范围与时间类别；目标版本仍匹配。阶段事实转长期和长期缩短为阶段均采用该合同。

- B 的新 claim.kind 仍须与它消费的 A 提议一致；取消的只是“目标旧项 kind 必须相同”这一无条件限制。
- partial/unknown 必须保留旧 kind、正文和条件；无新消息、只有旧记忆/概览、重复证据均不能改变 kind 或续期。
- 首版时间类别变更要求真实用户新确认；不凭一次工具成功观测提升为长期约定。
- scope 不能原地变更，不新增跨库搬移或跨库事务。
- 新 expiry 由本次有效确认时间与新类 TTL 计算，并与已有显式 deadline 取更早者；不能在变更 kind 时清除旧截止限制。
- 精确同事项冲突键为 scope/workspace_id/subject/machine/module/target，不再含 kind。同正文新增仍 duplicate；异正文的已知冲突沿已有 candidate 规则处理，不把分类换名当成解冲突。

### 前台直接写入

`memory tool → MemoryAPI.query → 必要来源保存 → MemoryAccess.query → commit` 保留；不增加前台 B，不自动转交/重放成后台任务。
record 及同 kind update 继续允许；不同 kind 的直接 update 返回明确的 `memory_kind_change_requires_comparison`，不写库、不另存候选。需要时间类别变更时，由后续独立的后台整理依据真实新消息完成。
前台工具描述使用同样的空间/时间证据要求。真实引用但语义错误仍可能通过确定性门禁，必须作为前台模型评测风险单列；测试不得伪造“代码能理解全局适用”的保证。

## 6. 个人长期约定预加载

### 6.1 有界取得完整集合

优先复用公共 MemoryInput、权限检查和 `_read_result`，在 `memory_context.py` 增加一个被 `prepare_memory_parts` 实际调用的内部帮助函数；不建立新的仓储/缓存/注册层。若文件将超过 500 行，再按装配职责拆分并记录调用方。

1. disabled、总预算 0 或个人预算 0 时，不读取个人预加载集合。
2. 经 allowed 检查，发一次 `list(scope=personal,kind=convention,status=active,limit=max_limit)`；max_limit 是 I/O 完整性上限，不是“选最重要 N 条”的产品规则。
3. list 非成功、有更多页或输出截断时，判集合不完整，本轮预加载整体为空；不追 cursor，不拿第一页冒充全部。
4. 每条候选再经 `read(scope=personal,id,version,status=active)` 权限检查，至多读取一页；最多 max_limit 次 read。复核 ID/version/scope/kind/status/到期，使用完整正文及全部条件。
5. 单条 read 失败、正文不完整、版本变化或输出无法完整表达时，本轮预加载整体放弃，不补查。敏感条目不注入并明确记录排除，不能输出敏感正文/引用到日志。
6. 单次 read 已由生产返回标明 complete；不得为了凑完整绕过 read_chunk_chars/max_output_bytes。用户将 read_chunk_chars 配小可能导致预加载退回普通检索，属于明确限制。

内部结果至少区分 enabled/complete/omitted_reason/rows/conflicted_ids，不新增公开 API 响应字段。完整集合按 memory_id 稳定排序，仅用于提示稳定性；不按近期、长度或编号挑子集。
来源原文不可用不自动淘汰仍有效的记忆正文；继续标注 source_status，不拿保存 quote 冒充可展开的原文。

### 6.2 装配与退回规则

预加载条目包含 ID/version、scope/kind、完整正文、必要条件、evidence_at、到期和来源可用状态；不加载旧版本、原始引文或历史工具输出。内部提示 JSON 将 preferences 改为 personal_conventions；通用外层 part.id=memory 保持。
普通 related 的索引也带上已有 evidence_at，提示“本周/本月”等相对限定以该证据时点为参照，不以召回时刻或 updated_at 重解释。该字段只描述来源时点，不是对事实仍适用的证明；本片不新增自然语言日期解析引擎。若仅有正文期限而无结构化 deadline，不能宣称数据库已按正文期限精确失效，后续回答评测必须覆盖这一限制。

1. 用现有 token 估算器计算整组及包裹开销；整组≤有效个人预算才接纳，不能逐条塞到装满为止。
2. 正常 S4 查询计划始终执行，默认≤4 个 search；超预算不触发一套额外查询。个人预加载与普通检索均为只读。
3. 整组超预算、集合不完整或读取失败时，personal_conventions=[]；普通检索继续使用 personal＋当前 workspace。不会回退“最近三条”，也不额外请模型精简画像。
4. 与 S4 的不同版本冲突统一处理：同 scope/id 出现不一致 version，从预加载与 related 都排除该 ID，不重读；其余完整条目可保留，记录冲突导致的明确省略。
5. 只有实际注入的个人记录才加入去重集合；整组放弃后不能把这些 ID 从普通结果误删。
6. 个人约定在自身预算内优先，目录及相关记忆沿 S4 次序在剩余总预算内装配；完整包裹仍须≤injection_tokens。普通 related_limit 不限制已接纳的个人集合，但仍限制 related 数量。
7. 相关结果的原有 preview_complete/detail 语义保持，不能将相关预览宣称成完整的个人长期约定。

弱输入“继续”：完整且装得下的个人约定仍可注入；个人组放弃时，普通查询数为 0，不凭空补主题，允许没有记忆。这是已接受的尽力而为限制，不是任务失败或重试理由。

### 6.3 可观测性和只读性

扩展既有 memory_recall 诊断：personal_preload_status、reason、listed/read/admitted/injected_count、估算 tokens、允许预算、version_conflict_count。原因使用固定枚举 disabled/empty/list_incomplete/read_failed/permission_denied/body_incomplete/budget/estimate_failed/version_changed/sensitive。
不输出正文、引用或模型凭证。日志失败仍沿 safe_log 隔离，不改变已准备的上下文。预加载不得修改 updated_at、TTL、访问次数或任意记忆行。
读取是逐次合法快照，不承诺跨整个装配过程的数据库原子快照；记录读取后仍可能被其他任务修改，继续保留历史数据提示和版本身份，不增加跨 IO 长锁。

## 7. 新旧数据与配置迁移

### 7.1 格式与机械映射

当前项目 schema=24、个人 user_version=3、记忆 format=3。预计分别升级到 25/4/4；实施前检查是否被其他切片占用。
数据库当前 kind CHECK 约束须真正改为两类，不能只在模型输出层改名。新建库和旧库升级最终结构一致。

| 旧 kind | 新 kind | 说明 |
| --- | --- | --- |
| user/rule/decision | convention | 继承旧系统已标为持续偏好/规则/选择的分类结果 |
| context/procedure/experience/reference | context | 其他旧类保守缩短为阶段信息，不在迁移时重新推理 |

该映射是迁移政策，不是对旧内容重新作语义认证；旧 scope、正文及限定不能被推广。旧分类本来就错的记录仍可能错，须在副本评估中展示该限制。

### 7.2 时间、版本和冲突

- 迁移默认期限按本格式规定的 convention=90/context=30、candidate=30 天；以旧 evidence_at 计算，不能以升级时刻计算。已有 expires_at/deadline_at 更早则保留更早值。
- 不更改 created_at、updated_at、evidence_at、last_confirmed_at，不补 user_origin/tool_success/verified_at，不因迁移刷新确认。
- 非 forgotten 当前记录追加一个迁移版本，来源引用复制但证据身份不变；保留原历史 snapshot_json，不把历史七类改写成模型当时输出了两类。新快照记录迁移后的当前状态；旧版本不能再作为 CAS 当前版本提交。
- 不复活 inactive/expired/candidate/forgotten。forgotten 保留清空内容和 suppression，不创建新的正文历史或来源。
- 同一新冲突键下，不同旧类合并后若出现多个 active 且正文不同，不能根据新旧/长度选胜者：按现有精确 known_conflict 的保守规则，将受影响当前记录转 candidate，追加 `legacy_kind_merge_conflict` gap，候选到期仍不晚于原 expiry；历史快照保留。
- 同正文重复不删除记录；沿现有精确重复读取合同处理。上述 conflict 是结构化保守判定，不能宣称它证明语义矛盾。
- FTS/触发器与当前 ID/version/digest 同步重建；历史来源、消息抑制和源项目所有权保持。迁移必须事务化、重复打开幂等，错误回滚并可观察。

### 7.3 操作边界

实现阶段只对新临时库、固定旧格式 fixture 和用户库的获准副本验证；不打开真实库触发迁移。本 Spec 不授权真实用户数据删除、升级或还原。
实际采用前停掉共享库的旧进程，沿现有备份入口保存一致备份。旧进程不能与新格式写入混用；新格式写入后不承诺直接退代码无损使用。
数据映射、历史记录保留和真实升级的影响须在交付时明确展示，不把测试副本升级成功当作真实迁移完成。

## 8. 分片、修改文件和 L3 接线

| 顺序 | 小切片 | 完成条件 |
| --- | --- | --- |
| S5-0 | 冻结新标签/评分器和可重建旧基线 | 生产算法不改；离线评分验证；取得旧链 calibration；不覆盖 run1/run2 |
| S5-1 | 先独立修正A实际请求的schema/锚点表达并验证预算，再切换两类合同、TTL、写入和迁移 | 新库/旧库一致；后台时间变更、前台限制、跨 kind 冲突、无续期均有回归 |
| S5-2 | 有界完整个人预加载 | 无三条选择；完整性/预算/冲突/权限/弱输入接线；只读与调用数可验证 |
| S5-3 | 新旧对照、语义审阅与最终回答观察 | 同数据/模型/协议；报告内容、范围、时间、注入和成本，未过门槛不宣称成功 |

S5-1/S5-2 实施完成后才能进行代表新方案的真实评测。可以提前评旧方案和验证评分器，不能把 prompt 单测称为新模块效果。

| 文件范围 | 必须同步的责任 |
| --- | --- |
| tools/memory.py、memory_writes.py | 新枚举、工具描述、直接改类错误合同 |
| memory_settings.py、settings.py | TTL/预算、旧计数项入口转换、非法旧 TTL 诊断 |
| application/memory/memory_job_runner.py、memory_proposals.py、memory_comparison.py | A/B schema 与提示；A文本模式附同源schema并计入输入预算，明确identity_terms为字符串技术锚点数组且可省略；新旧 kind 的 whole 变更条件 |
| application/memory/memory_mutations.py、storage/memory_sql/writes.py | 提交二次门禁、时间变更、冲突键不含 kind |
| application/memory/memory_context.py | 完整预加载、同版本去重、整组预算退回与日志 |
| storage/memory_sql/memory_schema.py 及必要的新迁移模块 | 当前两类 CHECK、旧格式升级、快照/FTS/来源一致性；避免让旧版本校验失效 |
| storage/migrations.py、backup_schema.py、personal_database.py、memory_personal_upgrade.py | 项目/个人格式升级、备份与版本识别 |
| scripts/probe_memory_semantics.py、memory_*evaluation*.py、review_memory_evaluation.py | 复用并版本化评测，详见配套协议 |
| tests/test_memory_*.py、旧库 fixtures、tests/data/ | 红灯用例、版本化合成样本、旧格式保留与新格式验收 |
| docs/memory-tool.md、配置示例、发布 smoke、相关设置测试 | 当前用户入口一致；不修改真实环境配置 |

具体函数先搜后改；不因“统一设计”新建通用框架。初版不新增锁；锁作用域/嵌套若改变，先同步 lock-hierarchy 并补确定性并发测试。
迁移后删除生产七类定义、旧 preference 读取和旧 kind 恒等门禁的失效分支；只保留旧格式升级、历史展示、基线适配与明确配置兼容的真实消费者。

## 9. 必须先红后绿的确定性验收

| 编号 | 行为与断言 |
| --- | --- |
| T01 | 两种 kind 读写/序列化通过；七类新请求拒绝；scope 与 kind 不绑定 |
| T02 | convention/context 默认 TTL 精确为 90/30 天；personal/workspace 相同；读/duplicate 不续期 |
| T03 | 合法 whole＋新用户证据使同 ID context→convention→context，版本/来源/expiry 一致 |
| T04 | 部分确认、旧消息、工具单次观测、直接工具改类、跨 scope、CAS 失败均不改变旧记录 |
| T05 | 换 kind 新增同事项不能绕过 duplicate/known_conflict；不同明确条件仍按原合同处理 |
| T06 | 个人 convention 4 条且装得下时全部预加载；workspace convention、personal context 不混入该组 |
| T07 | 整组超预算为 0 条预加载；相关搜索仍发生且已放弃组的 ID 可命中，不补查询、不随机选择 |
| T08 | 多页/字节截断/单条 body 不完整/权限拒绝/read 失败不伪装完整；每轮≤1 list＋max_limit read＋原 Q search |
| T09 | “继续”时完整组可注入；组放弃且 Q=0 时允许无记忆；零预算无个人读取 |
| T10 | 版本冲突从两组排除；同 ID 同版本只注入一次；正文条件完整、敏感项不泄露 |
| T11 | 总包裹预算有界；目录和相关项仍受原限制；全部读取前后 DB 写入计数、时间、TTL 不变 |
| T12 | 全部旧 kind/状态/空库/过期/自定义旧 expiry/合并冲突/多 span/遗忘抑制迁移；备份可读、异常回滚、幂等 |
| T13 | 配置省略/旧计数0与正值/新旧同时给/非法旧 TTL/布尔值；实际运行消费者读取新预算 |
| T14 | 后台 A/B 失败、查询失败、提交失败仍无重试无候选救回；前台路径未被误计为两阶段 |

TDD 只针对实现：先观察预期失败，再最小实现及回归。文档编写不伪称 TDD。
模型语义误判不属于可用 deterministic assert 证明不存在的错误；人工注入“两个模型都错误认可”的结果可用于展示剩余风险，不能把该探针说成真实模型错误率。

## 10. 工程验证和交付

专项：`.venv/bin/python -m pytest -q tests/test_memory*.py tests/test_probe_memory_semantics.py tests/test_release_memory_smoke.py`，新增迁移/配置用例按实际文件补入，不能遗漏因新命名未被 glob 包含的文件。
完整后端、受影响前端和标准 `scripts.build_release --target macos-arm64`、Twine/wheel 检查沿总规范第 9 节执行。结果必须来自同一候选源码，记录真实命令与退出码；本机验证不是发布或其他平台验收。
评测命令、数据版本、人工评分与成本按配套协议。工程全绿、真实调用完成、业务效果通过分别报告。
收尾同步 handoff 顶部、当前索引和真实待办；不追加课堂掌握结论，不提交/发布/修改用户库，除非另获授权。

## 11. 实现取舍与已知限制

- 本次已确认业务方向包括 90/30 天和取消不过期偏好例外；不再将该问题作为实施阻塞反复确认。
- 本文将原讨论未展开的落地选择具体化为：后台可受控改时间类别、前台不增 B、旧类机械映射和保守合并冲突处理、800 token 首版个人预算。这些是待代码验证的实现合同，不是已测得的最佳值。
- 个人组过大时，“继续”可能失去通用约定；记录实际遗漏后再决定是否优化，不提前增加画像或置顶机制。
- 三个月失效后，原通用协作习惯可能不再出现；仅凭读取不能续期，这是主动接受的时间策略。
- 迁移不能修复旧分类/旧 scope 的语义错误；真实来源检查不能证明概括无误。前台与后台风险分开评测。
- 若首轮评测显示简化导致错误范围、错误长期化或漏掉关键协作约定，应先定位具体失败环节；不能通过增加隐形子类或改评分标签制造通过。


实施次序补充（2026-11-16）：A输出合同是S5-1的独立准备单元，先保持七类与旧库格式完成，避免把已定位的格式失败和后续分类迁移混为一次收益。两类切换不能让前台继续以kind=user构造非法请求；与S5-2的替换接线需同一可运行候选验证。准备单元通过不等于S5-1完整交付。


S5-1准备单元执行记录（2026-11-16）：`memory_job_runner._prepare` 的真实A请求附ExtractionResult同源JSON schema；identity_terms明确字符串技术锚点而非JSON对象，Windows/ruff等普通词可以省略。已有batch预算计算消费完整system/schema，无新增调用或自动修复。两项接线/预算回归先红后绿；两个小预算批次测试仅按schema新增开销补足设置，原部分推进/遗漏/终止状态断言全部保留，并新增真正预算不足时在读归档和模型调用前失败的验证。此为新增请求材料后的测试场景迁移，不改变生产预算上限或放宽断言。七类/旧TTL/旧数据库格式暂时仍在；两类、迁移及个人预加载尚未实施，不能报告S5-1整体完成。


S5-2先行接线（2026-11-16）：普通索引已暴露原有evidence_at，提示相对日期以该时点解释且不证明当前适用；读取不续期不写库。完整个人convention集合尚未实现，依赖S5-1两类合同与存储迁移，不以旧user类别适配冒充完成。


## 12. S5-1/S5-2当前实现接线（2026-11-16）

当前请求只接受convention/context；默认active 90/30、candidate均30。`load_settings`在入口转换合法旧preference_limit并移除运行字段，非法旧TTL/计数继续禁用memory；示例配置已迁移，真实配置未改。

正常写入：A/B的同源schema已随Kind改变；`compile_comparison_decisions`要求不同kind目标仅direct＋correction＋whole＋较新真实用户证据，`memory_mutations.commit`再次检查whole/真实新用户消息/历史消息身份，CAS、截止限制及源核验保持。前台无confirmation_scope所以不同kind直接拒绝。`MemoryWriter.matches`同事项匹配去掉kind，新类不能逃避duplicate/known_conflict。

正常加载：`prepare_memory_parts → _personal_conventions → _read_result → MemoryAccess.query`，一次personal/convention/active list，至多max_limit次单页read；按ID稳定排序，完整正文经组预算接纳；普通S4查询始终按原计划执行，去重只使用实际接纳ID。阶段事实和项目约定不混入个人组。索引保留evidence_at和完整性/条件/到期及来源状态。

失败加载：list分页/截断、read失败/正文不完整整体放弃个人组，普通查询保持；组超预算也不抽取子集。跨查询版本冲突从两组剔除，弱查询且个人组放弃时可为空。所有查询走原权限入口；无业务重试、额外模型或读续期。safe_log记录个人完整性、读取数、预算及省略原因，不输出正文。

升级：`migrations.initialize_store`的25步和`initialize_personal`的format4分支实际调用`memory_time_schema.upgrade`，沿已有备份与事务。重建当前表两类CHECK与FTS触发器，保存旧版本及原来源，按旧evidence_at和更早expiry/deadline算新上限；非forgotten追加当前版本，合并冲突降候选；forgotten不新增正文或来源历史。旧格式schema创建/检查及迁移映射保留为真实历史版本消费者；不是新请求别名。源码不存在七类生产请求或preference_limit运行分支。

测试迁移：旧分类/TTL/最近三条属于被批准改变的产品行为，当前测试改为两类期限、完整个人组及普通项目检索。工作流测试以ID检查全部三条事实，避免两条context共用同一个kind工具调用ID。安装烟测使用明确跨项目原文的personal约定，继续验证弱输入的真实注入。历史库helper增加真实CHECK降级，以免仅降版本号伪造旧库；旧格式和冻结真实基线保持独立。

工程结果引用`build/spec143bm-s5-implementation/verification.json`；真实模型请求0。工程测试不证明分类语义正确，S5-3真实校准、前台评测和人工逐事实评分尚未进行。


审查修正（2026-11-17）：迁移开始固定time.time()判断时刻，只将按新期限计算后仍未到期的active记录纳入合并冲突。历史冲突测试固定时钟为0，保持其“双方仍有效”的原场景和所有降级断言；新增原先到期/因新TTL到期两个回归均证明不会拖累有效新项。预加载收到memory_version_changed或读回身份不一致时保存(scope,id)到conflicted_ids，普通检索与预加载统一剔除，组放弃仍不补读；普通预算/IO退回不扩大ID剔除。新增list→并发更新→read拒绝→search新版本回归验证零注入该ID。3项新增先红后绿，专项50、记忆548项通过。真实模型新旧对照另见评测规范的独立实验记录。
