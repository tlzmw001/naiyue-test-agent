# Spec143BM-S2：证据身份、保存与生命周期

状态：详细实施 Spec，待实施；2026-11-16。本轮只写文档，未改产品、真实配置或用户数据库。
源码基线：`60108431ceeeaf100df008da4765b879505fc1d7`。
总入口：[143BM](143bm-memory-optimization.md)；设计依据：[总体方案](../长期记忆优化总体方案-2026-11-16.md)。
输入协作：[S1](143bm-01-conversation-evidence-input.md)；后台提交消费者：[S3](143bm-03-proposal-comparison.md)。

## 1. 要解决什么

同一消息可以提供两段不同证据，但它仍然只发生过一次：保存两段，不能变成两次确认。
新证据明确更正当前决定时，可以更新旧记忆；旧来源暂不可读不应堵死这种更正。
同时，保存了引文字节不等于以后在任何项目、任何删除状态下都可以展示它。

本片完成以下变化：

- 按片段保留引用，继续按消息来源识别新确认和遗忘抑制。
- 保存程序已经核实的来源属性，使它们不再随本次调用结束而丢失。
- 后台只确认复合旧记忆的一部分时，不让其余旧内容一并续期。
- 明确来源缺失、改写、跨项目、删除会话和遗忘的结果，并迁移两类 SQLite 库。

不新增聊天保留期、自动清理服务、删除原因表、可读快照回退、持久提议、恢复账本、回执或重试。
不改变现有 kind、TTL 配置、项目/个人访问范围；本片不增加模型调用。

## 2. 当前数据流与确切缺口

公开工具：`memory` → `MemoryAPI.query` → 保存本次来源 → `MemoryAccess.query` → `_mutate` → `commit`。
`MemoryRepo.edit_with_sources` 在同库开启写事务；个人记忆则先持有项目来源读事务，再开启个人库写事务。
`memory_sources.resolve` 从归档校验版本、可见原文和位置，并计算 `user_origin/tool_success`。
`MemoryWriter.save` 写当前项、版本快照、来源行，FTS 由已有触发器同步。

当前问题与保留项：

| 当前源码 | 本片处理 |
| --- | --- |
| `identity()` 不含位置，resolve 和 merge 会把同消息两段折叠 | 引入片段身份；原消息 hash 算法保持 |
| 来源表已有 quote、起止位置和原始时间，未保存 user_origin/tool_success | 增加可空的历史核验属性，旧行不伪造为已核验 |
| 更新从 fresh 来源计算整条记忆时间 | 后台增加明确的完整确认/仅补证分支 |
| `source_reference` 必须回查当前项目归档 | 保留；新增保存字段不能绕过读取限制 |
| forget 删除该 ID 全部版本/来源，并登记消息 hash | 保留并覆盖新增字段、多 span 和在途后台提交 |
| 删除会话只删除聊天等会话数据，独立记忆仍存在 | 保留正文与引用不可展开的现有语义 |

## 3. 接口与跨片合同

### 3.1 公开接口保持

`MemoryInput` 的 action、字段、校验和 `MemoryAPI.query(*, scope, request, tool_call_id=None)` 签名不变。
`MemorySource` 继续接受 current_user 或完整保存身份，加 `quote`（1–300 字符）和 `offset`；每次最多 8 条。
模型不能提交时间、角色、user_origin、tool_success、verified_at 或本片内部确认策略；extra 字段继续拒绝。

实际消费者包括根 Agent、子 Agent、审批恢复的 memory 工具能力、`app.host.memory` 应用 API、后台 runner、
`prepare_memory_parts` 自动注入、工具 JSON 反馈及 `scripts/release_memory_smoke.py`。
没有发现独立记忆编辑 HTTP API 或前端 MemorySource 类型；前端通过通用工具输出展示结果。
实施时须重新搜索调用方，不能由“目前没有类型”推断以后也没有消费者。

### 3.2 S1 的编号绑定

S1 将本批 `sources=[{"evidence_id":"E1"}]` 转换为现有 `MemorySource` 形状；编号不跨批持久化，不进入公开工具 schema。
S2 `resolve` 仍在真正写事务所见的来源快照内重新核对；S1 已验证不免除提交时核验。
位置是现有可见文本投影的 Python 字符偏移，不是 UTF-8 字节、原始 JSON 或模型压缩文本的位置。
一个超过 300 字符的完整依据必须由 S1 绑定多个真实片段；不得在 S2 静默截 quote。
S2 只保存最终显式选择的 sources，不把 B 看见的 context 自动变成证据；结论依赖的限定必须被正式引用，否则由 S3 跳过。

### 3.3 S3 的内部提交

S3 负责新增唯一后台入口：

```python
MemoryAccess.commit_comparison(source_session_id, project_id, request, *, confirmation_scope)
```

它不是公共工具/API action；复用现有来源、项目绑定与权限准入，不能直接把任意模型 JSON 交给 writer。
S3 将 B 的枚举输出核对为 `whole | partial | unknown`，并校验实际提供过的旧 ID/版本。
S2 给现有内部 `_mutate`、`commit` 增加 keyword-only `confirmation_scope=None`，只随受控调用传递。
公开 `MemoryAccess.query` 固定使用 None，保留完整正文＋sources 的既有合同；额外字段不能夹带在 request 中。
S2 不再引入通用 policy/resolver 类；S3 是该参数唯一新增生产调用方。

`whole` 是 B 的语义判断，不是程序证明；程序只能验证枚举、旧目标、来源、时间和执行对应分支。
公开 update 也不能由程序证明引文支持全文；不得把本片后台保护宣传成所有入口的语义真实性保证。

## 4. 身份与持久字段

### 4.1 消息身份与片段身份

原 `identity(row)` 保持原字节算法：对以下有序数组执行现有 JSON 序列化和 SHA-256：

```text
source_workspace_id, session_id, thread_id, message_id, revision, conversation_revision
```

它用于抑制以及“这条消息是否曾用于该记忆”；不得加入 quote、正文摘要、位置或时间。
新增内部 `span_identity(row)` 返回 `(identity(row), start_offset, end_offset)`，用于 resolve、merge 的引用去重。
不新增持久 span hash；已有字段足以确定身份。同位置同版本只保留一份，后一次已核验属性覆盖旧属性。
同消息不同位置必须均可保存；换位置、重新核验、换标题都不是新确认。

fresh 的条件：消息身份未出现在该记忆任一已保存版本的来源中，且 `occurred_at > old.evidence_at`。
通过 writer 的按 memory_id＋完整消息身份查询核对历史，不全量扫描其他记忆；查询参数全部绑定。
这防止某来源被当前版本的数量上限挤掉后，又被当成首次出现；原 suppression hash 不迁移、不重算。
新记录的初始 evidence_at 仍来自有效来源时间，不从提交时间开始计时。

### 4.2 来源表的新增列

仅在 `naiyue_memory_sources` 增加以下四列；当前项和 `snapshot_json` 不复制证据正文。

| 列 | SQLite 类型/约束 | 新写入来源 | 旧来源 |
| --- | --- | --- | --- |
| evidence_format | INTEGER，NULL 或 1 | 固定 1，由程序赋值 | NULL |
| user_origin | INTEGER，NULL 或 0/1 | 根 human 且归档 admission 标记为真 | NULL |
| tool_success | INTEGER，NULL 或 0/1 | 可见工具状态确为 success；其他来源为 0 | NULL |
| verified_at | REAL，可空 | 本次提交校验使用的有限 now | NULL |

四列必须全部 NULL，或全部非 NULL 且 evidence_format=1；用显式 IS NULL/IS NOT NULL 的 CHECK，避免 SQLite 三值逻辑放行半组数据。
布尔值由 Python bool 转 0/1；读取仅把 0/1 转 bool，NULL 不得转 False。finite/time 校验继续在程序执行。
原 occurred_at、role、reply/tool_call ID、quote、位置不改含义；verified_at 不替代发生时间，不刷新 TTL。
不存完整聊天、原始工具参数、隐藏思考、模型 confidence 或未经程序核验的“成功”标签。

写入字典、`SOURCE_FIELDS`、参数化 INSERT、仓储读取、备用快照结构校验必须同步。
新 schema 的字段存在不表示旧行已补齐。普通读取没有写副作用；不在查询时偷偷回填或调用模型。
旧片段以后经正常 record/update 再次提交并回查成功，才可在新记忆版本中具有这四个属性。
旧历史来源行保留 NULL；重验证不能让同消息变 fresh，也不更改旧版本的时间。

## 5. 提交、完整确认与证据数量

正常新声明：“以后本地回归用 .venv，CI 保持原样”。S1 绑定两个必要 span，S3 判定旧 M17 的本地主张被明确更正。
S2 校验新来源、消息级抑制和 M17 expected_version；`whole` 且 fresh 时按现有 kind/TTL/deadline 计算新日期，保存同一 ID 的下一版本。
M17 的旧来源不可读不拦截这次更新；但不得用旧正文冒充本次新声明的来源。

| 操作 | 时间与状态规则 |
| --- | --- |
| record | 初始时间来自已核验来源；沿用用户/工具/inference 激活门禁 |
| 后台 update，whole 且 fresh | 按现有 TTL 规则重新确认；仍不能越过固定 deadline |
| 后台 update，whole 但无 fresh | 沿用无新证据分支，不续期；过期/非 active 不能据此整体转正 |
| 后台 update，partial/unknown | 仅允许补证；保留旧语义字段、status 和所有有效期字段 |
| 公开 update，confirmation_scope=None | 继续既有完整正文＋sources 合同，时间仍只来自真实 fresh 消息 |
| disable/forget | 仍需本次真实用户来源；不使用后台确认策略 |

后台 whole 的续期/转正还要求 fresh 来源与 basis 匹配：user_declaration 必须有 fresh.user_origin，
tool_observation 必须有 fresh.tool_success 且 target 已给出；inference 继续不能直接激活。
后台新确认时间从这些匹配来源计算，不让一条较新的工具结果替一条较旧的用户声明抬高确认时间。
不满足时按无新确认处理；更正关系由 S3 拒绝本项，不能换成新增绕过。语义上是否相关仍由 B 判断。
该检查在 commit 最终回查后执行；S3 的提前检查不能代替它。公开 None 分支不借本片暗改既有行为。

仅补证时，proposal 的 body、kind、subject、machine、module、target 必须与旧项一致；否则抛 `memory_partial_confirmation`。
成功仅补证只更新 version、updated_at、必要的操作说明及来源；保留旧正文、标题、标签/别名、basis、gaps、
status、evidence_at、last_confirmed_at、ttl_days、expires_at、deadline_at。不能夹带改条件、延长截止日或转正。
过期 active 仍然过期，candidate 仍是 candidate；新发现的局部事实需要完整独立提议，由 S3 另作决定。

例：旧正文同时包含“本地系统 Python；CI Python 3.10”，用户只确认本地 .venv。
B 不能因看见 fresh 就给全文新期限；partial 更新不同正文被拒。本片不自动拆旧记录、不替用户补确认 CI。
若 B 错把部分确认判成 whole，程序不能从字符串校验发现其语义错误，必须由 S0/S3 负例评测揭示。

来源最多仍为每版本 8 个 span，按以下规则处理而非随意截断本次依据：

- 先对本次 sources 按 span 去重；任一必要新 span 都不能因排序而被丢弃。
- record/whole/公开完整更新：本次支持完整正文的全部来源优先，余量才按原发生时间容纳旧来源；旧版本仍保留旧证据。
- partial/unknown：保留原当前版本全部来源，加入新 span；合并超过 8 个则 `memory_source_limit`，整项不写。
- 不创建额外新版本来绕过数量门禁；失败由 S3 记跳过，本片无重试。

## 6. 读取、删除、遗忘

### 6.1 读取合同

公开 `sources[]` 仍使用既有 `available`、`reason` 和 detail quote/offset；本片不增加公开返回字段。
新四列只用于持久事实和内部比较材料，不能直接序列化整个数据库行给模型或前端。
S3 需要旧证据时也经当前来源读取门禁；不得直接取 writer.sources 中的 quote 绕过它。

| 当前情况 | available / reason | quote |
| --- | --- | --- |
| 同项目原文、身份、版本、位置均匹配 | true / null | 仅 action=read 的 detail 返回 |
| 其他项目来源 | false / other_workspace | 不返回；不跨库查该项目原文 |
| 会话或原文查不到 | false / source_unavailable | 不返回，不能猜删除原因 |
| 原文改写、版本/可见性/位置不一致 | false / source_changed_or_not_visible | 不返回，不用旧快照替代 |

旧格式但原归档可读的来源仍可当前核对为 available；这不凭空补出当年保存的 user_origin/tool_success。
新格式但原归档不可读的来源仍不可展开；“当时已验证”与“现在允许读”是两件事。
不新增 retained_snapshot/retention_deleted 成功分支。未来真有聊天保留期，必须由另一个获批方案记录处置原因后再讨论回退。
active 正文仍按状态、有效期和权限参加召回；自动索引继续带 source_status，不能把 unavailable 解释为重新验证。

### 6.2 删除会话

沿用 `SessionPersistence.adelete` → memory/background deletion_guard → history_lock → `RunUnitOfWork.delete`。
在途整理未清理完时拒绝删除；不新增删除并取消任务的分支。会话归档删除后，独立记忆正文及已存来源行保留。
后续 read 返回来源不可用，无 quote；本片不承诺来源表字节已物理擦除。没有持久草稿，故不增加跨库提议清理。

### 6.3 遗忘与失败

forget 在目标记忆库同一事务中：取该 ID 全部版本来源 → 按消息身份去重登记 suppression →
删除全部来源行/版本快照 → 当前项正文、元数据清空并标 forgotten → 既有 FTS 触发器移除投影。
新增来源属性和同消息多个 span 随来源行一起删除；hash 不含正文，抑制仍按目标库现有范围执行。
本轮内存提议随后提交时仍查 forgotten/CAS/suppression；禁止把 update 拒绝转成 record 来绕过结果。
同消息其他事实也可能被保守抑制；用户未来的新消息可以成为新依据。
遗忘不重写原聊天/会话概览，不承诺语义上永久屏蔽所有相近内容；删除会话与遗忘不是互相替代的操作。

失败链示例：personal 提议在 B 后原消息被改写 → 项目来源快照 resolve 抛 memory_source_changed →
个人目标事务回滚 → S3 记录本项跳过，其余独立项可继续；没有个人记忆半条写入，也没有落盘提议可恢复。
来源失败、来源越界、抑制、CAS、数量上限等都保留固定错误码；日志只记阶段/身份/错误码，不写引文或模型正文。

## 7. 数据迁移与兼容的实际边界

### 7.1 版本与模块

基线项目 `CURRENT_SCHEMA=23`，个人 `PRAGMA user_version=2`，两类记忆 `format_version=2`。
本片占用项目 schema **24**、个人 user_version **3**、记忆 format **3**；实施前若已被另一获批迁移占用，先同步本 Spec 编号。
新增 `storage/memory_sql/memory_evidence_schema.py`：四列 DDL、upgrade、项目 24 步骤；不塞进超过职责边界的历史生命周期模块。
project migrations 注册第 24 步；新建库也经过这一步。DDL 和版本标记在同一 BEGIN IMMEDIATE 内完成。
`memory_schema.check(version=...)` 必须分别支持 1/2/3：原 REQUIRED 保持旧字段，3 才加 evidence REQUIRED，2/3 均检查 lifecycle。
新增索引 `naiyue_memory_source_message(memory_id,source_workspace_id,session_id,thread_id,message_id,revision,conversation_revision)`，
供最多8个本次消息的历史存在性查询使用；无正文索引、无新持久 hash。format3 检查列、该索引及四属性组的数据不变量。
`backup_schema.check_snapshot` 按 project>=24 选 format3，16–23 仍选2，15选1；不能让旧快照被新必需列误拒。
当前 format3 打开时也校验新增列/组约束需要的结构，损坏不降级、不重建空库。

### 7.2 两类升级

项目复用既有实例锁、`initialize_store` 和 `create_backup`：DDL 前 SQLite backup、integrity/结构/hash/manifest 校验和 fsync 成功，才执行迁移。
不直接复制仍有 WAL 的数据库文件。备份失败不得开始 DDL；DDL 失败回滚当前步骤并保留既有 StorageStartupError 诊断。

个人复用 `initialize_personal` 的 DELETE journal、FULL synchronous、BEGIN IMMEDIATE。
把私有 `backup_format1(path)` 替换为 `backup_format(path, *, version)`，供同一真实调用方按原格式 1 或 2 备份；删除无消费者旧包装。
备份仍通过独立只读连接的 SQLite backup（不能用当前写事务连接备份），验证 integrity、application_id、user_version 和对应 memory format，再 fsync 文件/目录。
format1 原库先备份1，再在同一升级事务内执行2和3；format2 原库先备份2，再升级3；新空库直接最终建成3。
任一步失败保留原 user_version/format、数据和明确错误；不捕获错误后继续提供一个部分升级库。

所有旧来源四列均为 NULL；旧 active/candidate/status、全部来源和版本正文、原始日期、FTS、suppression 保持。
不扫描历史调用模型，不自动回填可信属性，不续期或转正。仅按明确旧格式转换结构。

### 7.3 旧程序与回退

现有项目程序启动只接纳 SUPPORTED_SCHEMAS，到24后旧23程序会在打开时拒绝；个人旧程序只接纳 user_version1/2，到3后再次打开会拒绝。
但已经打开的旧个人库连接没有逐事务格式门禁，不能声称升级会自动禁止它继续写。
实施/部署前须停掉共享 data_dir 的所有旧写者，关闭应用，再用新版本升级；不在本片引入跨版本在线迁移平台。
两类库各有一致性快照；没有跨库持久进度或新事务协议，不承诺自动成组恢复。一次升级演练记录相关库的备份身份和时点。

尚未写入新数据时可在停机状态恢复升级前匹配备份和旧程序；已有新格式写入后，不承诺无损代码回退。
项目按既有维护/restore 入口验证并恢复；个人当前无统一 restore 命令，需停掉所有持有者后验证专用快照，再由明确维护步骤恢复。
不得伪造个人库“已有一键恢复”能力；需恢复时先确认目标库与升级后数据处理范围，保留当前库供核对，不执行静默覆盖。

## 8. 文件与全链路核对

| 文件/区域 | 必要变更或核对 |
| --- | --- |
| application/memory/memory_sources.py | span_identity、resolve 属性；消息 hash 不变 |
| application/memory/memory_mutations.py | 分片合并、历史来源 fresh、后台确认策略、forget全版本 |
| application/memory/memory_access.py | S3负责内部入口；S2只接其事务参数，公开路径保持 |
| storage/memory_sql/writes.py | SOURCE_FIELDS、历史消息身份查询、参数化写入 |
| storage/memory_sql/memory_evidence_schema.py | 新增format3/schema24迁移职责 |
| memory_schema.py、migrations.py、backup_schema.py | 分版本结构校验、新旧快照兼容 |
| personal_database.py、memory_personal_upgrade.py | format1/2→3备份/升级，删除旧私有包装 |
| storage/memory_sql/sources.py、conversation.py | 保留原文读取门禁，禁止新列整行透传 |
| memory_results.py、memory_context.py、MemoryAPI、tools/memory*.py | 有界输出、source_status、公开严格schema和根/子/恢复调用回归 |
| session_persistence.py、unit_of_work.py、memory_jobs.py | 删除互斥、记忆正文保留/quote不可读；预期无需改行为 |
| frontend/src工具反馈与types、release_memory_smoke.py | 搜索确认真实消费者；不新增无调用方展示字段 |
| docs/lock-hierarchy.md | 原项目来源→个人目标顺序不改；若实现新增嵌套必须同步表和反向路径测试 |

测试文件随下节新增/扩展；单文件不得超过500行，按真实职责拆，不能借本片顺便重构前台或运行框架。
迁移收尾检查旧 helper、源码导入、测试、backup/restore、安装烟测及当前文档，不能为历史私有调用保留重复路径。

## 9. TDD 与验收

先在真实临时 SQLite 库写失败测试，记录失败确因缺少本片行为，再实现；不修改真实用户库。

| 用例 | 必须断言 |
| --- | --- |
| 同消息两个span、重复句不同位置 | 两段均保存；相同span仅一份；重开仍存在 |
| 换span/重新校验/旧版本曾使用来源 | 不变fresh，不续期，verified_at不改变occurred_at |
| 四属性来源 | 根用户/子代理human/工具成功失败区分；model额外字段拒绝；后台旧用户证据＋新无关工具不构成新用户确认 |
| 历史来源全部缺失＋新明确更正 | 新来源支持whole时同ID更新；旧来源仍不可读 |
| 复合旧项部分确认 | 不同正文partial拒绝；相同正文补证日期/status全保持；expired不转正 |
| 多span超8 | 不截必要证据；partial保留旧来源失败时整项零写入 |
| 删除会话、来源改写、跨项目personal | 正文按原规则可读，quote缺席，准确reason；无snapshot回退 |
| forget全部版本 | 当前/历史/来源/FTS均无内容；旧消息任意span受抑制，在途提交拒绝 |
| 公开接口回归 | 原payload与签名有效；公开full update仍按原fresh规则；内部字段无法注入 |
| 项目23→24、个人1/2→3、新库3 | NULL legacy、日期不变、结构和FTS正确、可重开、迁移幂等 |
| 备份失败、DDL中途失败 | 不进入DDL或完整回滚；原库及快照可验证，固定错误与日志存在 |
| 旧程序启动/备份恢复 | 旧版本拒绝新格式；旧格式快照仍可验证；不声称旧存活连接受保护 |

拟新增 `tests/test_memory_evidence_storage.py`，其余沿用真实已有文件；测试失败修生产逻辑，不放宽原断言。
并发用事件/事务门闩注入，覆盖个人源快照、目标失败回滚和forget提交顺序，不用极短sleep赌时序。

```bash
.venv/bin/python -m pytest tests/test_memory_evidence_storage.py tests/test_memory_lifecycle.py tests/test_memory_lifecycle_storage.py tests/test_memory_lifecycle_readback.py tests/test_memory_readonly.py -q
.venv/bin/python -m pytest tests/test_storage_migrations.py tests/test_storage_backup.py tests/test_storage_backup_validation.py tests/test_storage_backup_files.py tests/test_memory_api.py tests/test_memory_api_persistence.py tests/test_memory_context.py -q
git diff --check
```

专项通过后，完整验证统一执行[总索引第9节](143bm-memory-optimization.md#9-工程与业务验收)：
`native_assets → 配套PATH下ci_shards → 前端测试/构建 → build_release → wheel检查/安装验证`。
不得在准备原生资产前直接跑裸全量 pytest；非macOS ARM64按现有构建器选择本机目标并记录，不改构建体系。
交付记录命令、红绿结果、库版本、快照验证与安装结果；本文件列出的是未来验收，未声称已运行。
模型对whole/partial的判断质量交S0/S3固定负例实测；本片确定性测试通过不证明语义完整性或用户已掌握。
