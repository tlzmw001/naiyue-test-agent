# Spec143BO-S1：Wiki 存储、索引与新代记忆

日期：2026-11-17；实施更新：2026-11-18。状态：S1 存储组件及隔离迁移已实现并验证，默认启动未切换。依赖：[总规范](143bo-memory-wiki.md)、[S0](143bo-00-contracts-and-evaluation.md)。本片在临时库实现和验证；真实开库迁移接线属于 S6 的整体切换。

## 1. 小目标与范围

把“工作约定 → 后台任务规则 → 多条记忆”保存为关系数据；读目录不读正文，读一页不扫描整个 Wiki。保留个人库与当前项目库边界。用户已选择旧记忆全部不再使用，因此不需要遗留内容的分类模型、兼容召回或“待整理”产品入口。

复用 MemoryRepo、MemoryWriter、来源快照、版本和参数化事务。新增 memory_wiki_schema.py 与 memory_wiki_queries.py，是因为主题结构/分页与已有来源/概览职责不同；已有单文件行数允许时直接扩展，不为压行数拆空转发。

## 2. 格式与旧代隔离

当前项目 schema=25、个人 memory format/user_version=4。目标项目 schema=26、个人 format/user_version=5；实施若版本被其他已合入功能占用，先更新本 Spec 和备份识别再修改代码。

迁移顺序：

1. 沿当前所有者/关闭条件与备份流程验证旧格式，生成可校验备份。禁止旧进程混写。
2. 同库事务内把旧记忆正文、来源、版本表隔离为固定 legacy143bm 表名；移除其写入触发器和派生检索入口，不再注册任何产品查询这些表。
3. 建立空的新代当前表及 Wiki 表；旧记忆 ID 不复用，新库无自动映射。
4. 复制原忘记抑制的最小来源哈希到新抑制表，保持删除意愿；不复制旧正文、quote、标签或积极记忆。
5. 原聊天、checkpoint、会话概览、目录已读标记保持。现有会话的自动提取起点推进到切换时已保存根历史末尾，以真实归档统计建立触发基线，不补扫停用前旧历史。
6. 原已受理任务不跨进程恢复；旧待执行 memory_actions_v1 调用由 S2 拒绝。提交格式标记和新代身份，事务任一步失败整体回滚。

legacy143bm 表仅供关闭态备份/后续显式维护，不供模型或普通管理 API。新代 GC 不清这些表；其物理删除留给用户计划中的整体清理重构。这是明确的数据保留边界，不是仍在使用旧记忆。

用户以后显式要求重新整理某段历史，是从原始合法来源产生新提议，不能读旧记忆表来回填，不能把旧发生时间改为当前时间。自动任务只处理切换后的新范围及必要的只读重叠上下文。

## 3. 新代数据结构

固定标识均由程序生成。范围、项目身份、时间、核验属性不由模型直接写入数据库。

| 表 | 字段/约束 |
| --- | --- |
| naiyue_memory_info | 原格式信息＋generation_id、catalog_revision、started_at；版本递增由事务维护 |
| naiyue_memory_pages | page_id PK、scope、workspace_id、topic、title、description、version、created_at、updated_at；topic 仅三种；workspace 必须本库项目身份、personal 的 workspace_id 为空 |
| naiyue_memories | 原稳定 ID/版本/状态/正文/条件/来源时钟等，去掉 kind；新增 page_id FK；所有新记录均按长期记忆合同进入 |
| naiyue_memory_page_links | from_page_id、to_scope、to_page_id、description；唯一三元组；from 属于本库，target 由业务层校验，跨库目标不伪造 SQLite FK |
| naiyue_memory_sources | 原来源字段；增加 supports_scope 布尔值，程序根据绑定的用户范围证据设置 |
| naiyue_memory_versions | memory_id/version PK、snapshot_json、superseded_at；当前版本为空，替换时程序设置退休时间 |
| naiyue_memory_suppression | 继承原最小消息身份哈希，不保存可还原正文 |

主题页 title≤160 字符且≤480 UTF-8 字节，description≤300 字符且≤900 字节；关联说明≤160 字符；每页最多 8 个出站关联。沿用记忆正文≤3,200 UTF-8 字节、最多 8 条必要来源的既有存储边界。它们是防异常载荷限制，不替代 2,000 token 输出分页。

不保存另一份整页 Markdown。页面 description 是导航简介，不复制全部正文或项目来源原文。失效条目不参与页面渲染；目录/简介不能继续陈述已经失效的事实，只描述该页用途。

状态沿用 active/candidate/inactive/forgotten。candidate 是“已具备真实依据但冲突未解”的保存状态，不是阶段性记忆类型，更不是失败兜底；默认前台页面只读 active。小主题 30 条同时计入未过期 active＋candidate，避免通过候选绕容量。

## 4. 范围、成员和版本不变量

- 每条当前记忆只有一个主 page_id；页面范围和成员范围完全相同。
- 页面重命名不改 ID；页面元数据、成员、相关链接变化均递增 page.version，并递增该库 catalog_revision。
- 整理不能把 workspace 页移到 personal，也不能跨项目链接。personal 页只能链接 personal。
- 事务提交后，每个结果页的未过期 active＋candidate 数≤当前容量；配置调低导致已有页超额时，允许不增量的更新/退休与可修复至上限的拆分。
- 普通读的到期过滤使用调用当时的 now，不等待 GC 更新 status。
- 不把“读取过”“原记录 exists”当成整个整理期间稳定；最终页版本、条目版本、相关有效期都须复核。

## 5. 真实查询与索引

| 访问模式 | SQL/索引合同 |
| --- | --- |
| 大主题目录 | 只选 page_id/topic/title/description/version；按 topic、title、page_id 游标查；索引覆盖相同前缀 |
| 页内有效记忆 | page_id、status、expires_at 过滤；按 expires_at,memory_id 排序；复合索引 (page_id,status,expires_at,memory_id) |
| 按 ID 读取/更新 | memory_id PK、page_id PK、期望版本；先核验调用范围 |
| 关联 | (from_page_id,to_scope,to_page_id)；目标 metadata 批量按 ID 读取，缺失/越权目标不输出 |
| 来源 | 沿用 (memory_id,memory_version,source_id)，页内多条记录批量查，不逐条 N+1 |
| 到期清理 | (expires_at,memory_id)，旧版本 (superseded_at,memory_id,version) |
| 空页判定 | 利用页成员索引 EXISTS，不扫描全部正文计数 |

普通目录只展示至少有一条当前可见 active 成员的页；后台整理目录额外包含未过期 candidate，避免只有冲突项的页被误认为不存在。投影由受控调用入口选择并绑定游标，不由任意模型字段扩大。冷空页不占导航位置；精确定位仍能核验存在的页，不因页暂空而创建内容替身。

个人和项目目录在应用层合并有界 metadata，不跨项目打开其他数据库。按访问模式准备 SQL，不用一个带所有可选 OR 条件的通用查询包揽目录/正文/清理。所有参数绑定；表/列名来自固定代码。

SQL 的 LIMIT 是内部 IO 批量（沿用最大 50 条），不是挑选最重要 50 项的语义规则；cursor/has_more 保证余下目录可遍历。页内总条目上限 30，过期历史另由到期索引过滤和 GC 管理。

## 6. 来源与版本清理准备

当前版本保留支持正文及个人范围的最小必要证据副本；原聊天消失不伪造可读状态。scope 证据不能因取“最近 8 条”而被挤掉；必要证据合计超过现有上限时整组拒绝，不能丢掉门禁依据。

合并在目标当前版本中复制必要依据，不让有效记录依赖待删除旧记录的正文/版本外键。旧被替代版本保留至 superseded_at＋清理缓冲，届时可分批删；仍需的依据已在当前版本中。

原始证据的 message identity、span identity 和 user_origin 保持不同职责。新代清理版本后不能让同一旧消息变成“未用过的新确认”：是否可续期以真实发生时间严格晚于 evidence_at 为必要条件，并保留当前版本中必要身份。

## 7. 正常与失败链

正常：S2 的同库 plan → MemoryRepo 写事务 → 复核页面和条目版本 → 更新成员/来源/版本 → 复核≤30 → 更新页/catalog revision → commit → 回执。

失败：第二个写入者使某页版本变化 → 整个相连变更组回滚 → memory_conflict → 原数据保持。不能改用新的 memory_id 绕过 CAS。源库与目标个人库沿既有 project→personal 顺序；不建立两库写事务。

GC 与续期并发：GC 在自己的事务内按当前 expires_at 复核；更新先提交则跳过，GC 先删则旧 ID 更新失败，不自动创建替身。

## 8. 修改文件

- storage/memory.py、memory_sql/writes.py、sources.py：当前仓储、版本、来源和同库提交。
- 新 memory_sql/memory_wiki_schema.py、memory_wiki_queries.py：新结构和限定查询。
- storage/migrations.py、personal_database.py、backup_schema.py：格式、备份和开库识别，真实入口 S6 接线。
- memory_sql/memory_schema.py：按旧/新版本分别验证；旧验证仅用于迁移/备份，不保留旧运行查询。
- tests/test_memory_wiki_storage.py、test_memory_wiki_migration.py、test_memory_wiki_query_plans.py。

## 9. 验证

先红后绿：旧数据渗入、跨范围成员、31 条、CAS 冲突、scope 证据丢失、迁移中断和清理竞态。迁移还检查原 history/overview 字节与关键行身份保持，旧忘记抑制继续有效。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_storage.py tests/test_memory_wiki_migration.py tests/test_memory_wiki_query_plans.py -q
~~~

用临时磁盘 SQLite 构造 2,000/20,000 新代条目及大量过期数据，记录 EXPLAIN QUERY PLAN、查询次数、取出行/字节和耗时分布；不逐字匹配 SQLite 不承诺稳定的完整计划文本，不用极紧毫秒断言制造脆弱测试。

不把 S1 组件测试当成真实库迁移或整体产品接线完成。最终 L3 调用链由 S2/S3/S5/S6 验证。

## 10. S1 实施接口边界（2026-11-18）

S1 新增 MemoryRepo.prepare_wiki(now) 显式准备入口；它复用当前 Database 所有者/短锁及既有备份器，事务内调用 Wiki 格式迁移。新建库也先使用已有空格式再显式准备，不增加默认开库分支。CURRENT_SCHEMA 仍为 25、个人默认仍为 4，真实启动/备份新版识别在 S6 接线时同步改为 26/5。显式准备不是生产双轨开关。

MemoryRepo.wiki_edit(scope,workspace_id,now,capacity) 在既有单库事务内提供 WikiWriter；其 apply(pages,memories,links,external_targets) 消费程序编译的组，先核对全部期望版本，savepoint 保证调用方即使捕获错误也不会提交半组。新页/条目 ID 由程序构造函数生成，模型局部 N 引用由 S2/S3 编译。现有 MemoryWriter 的来源/抑制读取复用，新格式 save 独立，旧写入链到 S6 才迁移删除。

queries 提供目录/成员的稳定键分页、批量来源以及本库关联；SQL 层检查所属库身份。跨库链接目标仅接受应用层在合法范围读取的 metadata 快照，读取时还须重新过滤，仓储不为此打开其他项目。S1 代码只能保证结构/版本/范围/容量，不对来源语义作推断；完整来源绑定和续期策略在 S2/S3。

更新计划默认 require_unexpired=true，提交时旧目标已过期即拒绝。S2 的新完整确认分支在完成来源/充分确认门禁后可由程序指定 false，允许对尚未清理的旧 active 记录确认；该参数不对模型公开。已删除目标仍按 ID/期望版本冲突拒绝，不能转为新增。

## 11. 实施接线和验证记录

实际入口：MemoryRepo.prepare_wiki → 原备份器 → Database.transaction → memory_wiki_migration.upgrade → memory_wiki_schema.create/check。项目迁移单库提交 schema26、个人提交 format/user_version5；默认 CURRENT_SCHEMA25/个人4 保持，正常启动回归已覆盖。新版校验由独立 Wiki schema 模块完成，backup_schema/migrations/personal_database 的正式启动识别在 S6 同步，不提前扩展默认可读格式。

当前可运行组件链：MemoryRepo.wiki_edit → WikiWriter.apply → 同库 savepoint＋SQL 提交；wiki_pages/wiki_members/wiki_sources/wiki_links → 限定查询。标题、成员、链接变化递增页版本和 catalog revision；来源批量读取且拒绝旧格式。程序提供已绑定来源，仓储检查数量、来源属性、范围证据继承和结构；真实出处核验/续期语义门禁仍由 S2/S3 接入，不把组件通过当作模型效果通过。

新增三个存储专项文件共 22 项通过；初始入口缺失红灯后实现，并针对旧格式来源泄漏、提交前到期、缺失索引校验分别观察失败后修复。迁移演练检查了原对话/概览、忘记抑制、触发起点、可读备份、异常身份及事务回滚。新共享 helper 仅为这三个测试提供临时计划/来源数据，未修改 conftest 或原测试基础设施；旧归档 helper 的 checkpoint 引用在新迁移测试中通过真实 Saver 表补齐，没有放宽备份校验。

记忆相关回归 700 通过；全后端按测试文件分四组，共 7,156 通过（1,647＋1,892＋1,734＋1,883），0 失败/跳过。测试 PATH 使用本次已校验 wheel 的 fd/rg。标准 Mac 构建/Twine、compileall/pip check 通过，冻结源码摘要一致；命令/退出码及文件清单见 build/spec143bo-s1/verification.json、backend-shards.json 和各日志。

临时磁盘库 2,000/20,000 条有效记忆，分别另含 500/5,000 条过期成员和 100 个历史版本：固定页读取均为 30 条，60 条来源用 1 次 SELECT 取得，成员查询命中 wiki_members 索引。20 次热读取 P95 分别约 0.348/0.351ms；Python 峰值分配约 60KB，不含 SQLite native 内存。原始汇总见 build/spec143bo-s1/performance.json；这是仓储局部测量，未包含渲染、模型、真实用户库及完整应用门禁，不作为整体性能承诺。

旧 MemoryWriter/旧格式校验仍由当前 memory_mutations、默认项目/个人启动及备份消费，不能在 S1 删除。新增 Wiki 入口当前由隔离测试消费，未接模型工具、MemoryJobs 或正常启动；S2/S3 接入组件、S6 切换后再按实际消费者删除旧运行路径。未调用真实模型、未操作用户数据库或真实配置，未提交发布。唯一下一片为 S2。

### S1 审查修复（2026-11-18）

批量来源读取将 user_origin/tool_success/supports_scope 恢复为布尔值，避免合法个人来源往返后被严格门禁误拒。WikiWriter 标记保存点回滚失败；MemoryRepo.wiki_edit 在正常退出但该标记存在时强制抛错，由既有外层事务执行整笔回滚，即使调用方已捕获原业务错误也不能提交半组。未捕获的原异常仍沿原路径传播。无重试或 candidate 兜底。新增 test_memory_wiki_storage_review_fixes.py，两项先红后绿，S1 专项 24 通过；本轮完整验证结果见 handoff 顶部。
