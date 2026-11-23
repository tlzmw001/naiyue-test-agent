# Spec143BO-S3：后台提出与按主题整理

日期：2026-11-17；实施更新：2026-11-18。状态：已实现并通过本机组件/整体验收。依赖：[S1](143bo-01-storage-and-new-generation.md)、[S2](143bo-02-wiki-tools-and-scope.md)。不建设第二套任务队列，不全库读入模型，不升级检索算法。

## 1. 任务形态

保留 MemoryJobs 的触发、任务归属、停止、并发、model_id、超时、进度和概览状态。同一任务的逻辑阶段为 A（从本批新来源提出）与 B（读取主题目录/页面后整理）。

普通自动/refresh_overview：A → 概览独立保存 → B → 提交。显式 organize：直接 B，只能整理指定范围内既有页面，不伪造新用户证据或续期。两者使用相同任务设施，后者不意味着后台失败自动生成后续任务。

organize 的 page_ids 是既有页面的可写集合：可以读同范围目录避免重名/重复，也可以建立同范围新页完成拆分；不能顺带改写未被指定的既有页。前台返回的 accepted/task_id 仅代表受理，最终状态通过既有任务查询取得。

## 2. A 输入和输出

输入沿现有完整消息/自然单元分批、E 编号、重叠只读上下文、工具结果裁剪；附三个大主题定义及范围规则。上一概览只用于连续理解，不能作新证据。

输出 overview={goal,progress,blockers,next_step}（沿 1,200 字符）及最多 8 个 proposals：

~~~json
{
  "matter": "方案讲解方式",
  "topic": "collaboration",
  "scope": "personal",
  "body": "讲解技术方案时先给具体例子，再解释原理。",
  "basis": "user_declaration",
  "intent": "activate",
  "reason": "用户陈述其一般学习习惯",
  "sources": [{"evidence_id": "E12"}],
  "scope_evidence_ids": ["E12"]
}
~~~

示例中的 body 是允许的概括，E12 是实际输入的符号引用；模型不提供可信时间/ID/来源核验标记。正文≤800 字符并满足既有字节限制，保留事项对象与条件；不输出 kind，也不把一次试验、任务进度填成长期记忆。

程序先绑定 E、origin、用户/工具来源、限定完整性和 scope 来源关联。没有新正式证据的提议不能刷新旧项。坏提议局部跳过；合法概览独立保存，不因 B 失败回滚。

A 的项目提议（含省略 scope 时的默认 workspace）只用 sources 绑定事实。提示模型将 scope_evidence_ids 留空；本地收到其他值时统一清空，再送入 B。个人提议仍须给出非空、唯一、属于本条 sources 且来自真实用户的范围证据。

A 的请求提示使用本任务的概览字符数和提议数量上限，发送给模型的 schema 同步声明提议上限。合法信封里的提议列表超限时记录 `memory_proposal_limit`，跳过该列表，独立校验并保存概览。任务报告保留提议超限与 Wiki 整理、提交失败原因。概览的 coverage.skipped 只记录来源覆盖情况，保存新概览及读取已有概览时排除上述三种任务原因，保留来源缺口和历史计数格式。其他信封错误仍按 A 整体失败处理。提议绑定失败保留固定诊断码，区分字段、个人范围证据和上下文完整性。日志只记录诊断码，来源核验仍以本批证据为准。

## 3. B 的按需阅读协议

复用既有模型输出适配、严格 JSON/schema 和模型配置；不新建具备 Bash/网页/任意工具权限的 Agent。

B 每次返回一个显式步骤：

- step=read：requests 为 1..4 个合法只读 Wiki 请求；程序检查范围和参数后顺序/有界执行，结果回填。
- step=finish：返回本工作单元的完整 decisions、必要 page_changes/links；提交前按下表检查可修参数，实际事务分组由程序统一计算。

可用读取为 list_pages、read_page、read（指定已经合法发现的条目及来源/历史投影）；范围被绑定到本组 personal 或当前 workspace。程序选择包含未过期 active＋candidate 的整理投影，包括仅含 candidate 的页，不让前台默认过滤造成遗漏。所有结果受 S2 的 2,000 token 分页和现有累计 comparison_material_bytes 限制。每次实际模型输入含提示/schema/已读结果，仍受 16,000 输入上限及所用模型更小窗口约束。

自动整理与 `refresh_overview` 的 B 单元已有 `(scope, topic)`。模型侧 `list_pages` 只提供 `action` 和可选 `cursor`，组织器校验输出后，取本单元主题填入交给 `Ledger.read` 的请求。两个主题单元可发送相同的模型请求，仍各自查询各自的目录。自动 list_pages 回显当前 scope 或 topic 时，程序核对等值后移除回显；冲突值按非法形状结束本组，概览与独立成功组照常保留。显式 `organize` 以指定页集合创建单元，可用原 `list_pages.topic` 选择读取主题，页面写权限继续由原账本校验。前台 Wiki 工具和 `finish.page_changes.topic` 合同仍沿用原定义。

合法翻页是推理流程的下一步，不是失败重试。重复完全相同且无进度的读请求、伪造/失效游标、必要读取失败、整包超预算或截止时间到达：结束相关组、报告原因，不自动压缩材料、不改查全库、不用“没找到”新建。

不设“后台固定两次调用”的旧承诺；记录真实 logical_calls/SDK attempts/token/耗时。沿原 300 秒任务期限、批数、输入/输出和累计读取量控制执行；不增加人为真实评测调用次数配额。

前台 memory/history 继续走普通工具校验反馈，由主模型看到配对错误后决定是否重新调用。前台项目写入在准备许可与执行前均清空不适用的 scope_source_indexes。B 的 supplement/confirm 进入正文校验前丢弃不消费的 proposal。add 必须给出 destination；提示和字段说明列明合法目标，缺失时按依赖组报告 memory_destination_required，保留独立组的提交。后台修正按下面的合同执行。

### 3.1 写入前的一次参数修正

每个 B 工作单元（自动的 scope/topic 或显式 organize 的页集合）共享一次反馈机会。沿用相同模型、输出协议、已读投影、期限和输入预算。正常调用数不变。A 的概览与提议不会重跑。

| 发生位置 | 程序动作 |
| --- | --- |
| B 完整回包的 JSON 语法错误，或 read/finish 在读取或写入前缺字段、类型/枚举错误 | 回传固定的格式说明、可信的字段路径，要求按合同重交。单次 read 中的全部参数先完成结构校验。 |
| finish 的新增/移动等缺目标页、新 N 未定义或重复定义、遗漏或重复消费 P、已选 P 的 E 引用错误、合并漏引某条新提议的证据、旧目标、新正文或合并参与项缺失，移动误带新提议，关联引用未定义的新页 | 从当前工作单元生成带决定序号的字段说明，一次提示多项；模型重交完整 finish。只引用已有材料，证据不足仍可选 unsupported。 |
| 实际读取层需要分页或版本已变、未发现/无权目标、无进展、来源与完整确认门禁、敏感内容、协议拒绝/截断、预算/超时/停止、网络或事务失败 | 保持现有处理。正常分页继续使用签名游标；到达写入事务后不进行模型修参。 |
| 修正仍不可用 | 对已有合法结构的原 finish 沿用原分组与事务校验，独立正确组可落库。无可分组计划时结束本单元。停止/超时继续遵循原控制。 |

反馈中只使用固定提示与字段位置；不回显错误的值、原始模型输出或异常正文。finish 的修正发生在收集所有单元结果和提交事务之前。只允许补交 finish，无需重新读已经读取的页。结构错误的 read 可补交有效步骤，之后继续正常读写循环。

实现入口为 memory_organization.organize；模型结果的静态检查集中在 memory_organization_feedback，存储、来源和权限仍由 Ledger 与 commit_group 验证。专项检验正常零额外调用、跨 read/finish 共用一次、二次失败的独立组和拒绝路径。

## 4. 完整性账本

程序为本轮持有只读账本：已完整遍历的范围/大主题目录及 catalog_revision、已完整读取的 page_id/version/成员版本和最早到期点、合法 O/P 引用映射。该账本是一个任务内的局部数据，不是持久候选队列或跨会话缓存。

- 新建主题页前，必须完整看过对应大主题/范围目录；不能把第一页未见误当成不存在。
- 比较并写入既有页前，必须读完该页整理视图的所有成员正文，包括未过期 candidate。必要来源按需展开；程序继承已核验来源，不强迫模型重读跨项目不可访问原文。
- 只能引用账本中的 O 条目和已绑定 P 提议，不能填写任意旧库 ID。
- B 选错主题或误判同义性仍属于语义评测风险；账本不能证明模型选对了材料。

完整目录或相关页过大而预算不足时跳过该组，不新建“临时主题”绕过查重；保留已成功的独立组和概览。30 条满页不必然适配 16,000 token 后台材料窗口，需在评测记录实际不足率。第一版接受此可观察限制，不引入更复杂检索补救。

## 5. 最终决定与程序职责

| 决定 | 模型需要决定 | 程序负责 |
| --- | --- | --- |
| add | 新内容、目标已有/新页、E 来源 | 新 ID、范围/来源/时钟、页容量、事务 |
| duplicate | 与哪个 O 重复 | 保持旧正文/来源/时钟，不制造新记录 |
| supplement | 对哪个 O 的局部/未知确认、E 来源 | 仅附来源，继承旧正文/条件/范围/期限；忽略不消费的新正文诊断字段 |
| confirm | 对哪个 O 的完整重新确认、直接 E 来源 | 保留旧正文/条件，以较新完整证据更新时钟；不让模型重抄旧正文 |
| correction | 被更正 O、完整新内容、充分新证据 | 同 ID/CAS 更新，充分确认才更新时钟 |
| merge | 同范围 O/P 成员及合并后的必要语义 | 复制必要来源、保护范围依据、退休重复成员、原子成员更新 |
| unresolved_conflict | 有真实依据但互相冲突的长期提议 | 保持旧项，依既有合同形成 candidate，不进入普通页面 |
| unsupported | 不值得长期保留/依据不足 | 不保存提议，不造 candidate |
| move/split | O/P 分配到哪些同范围同级页面 | 原 ID/来源/期限不变，页版本、容量和链接更新 |

只在确有语义更正或合并时需要新正文。duplicate、仅附来源、原文不变的完整确认和成员移动不要求模型重抄旧正文/旧 scope/旧时间。旧来源全部由程序从所选 O 关联，禁止重引入“旧引文编号抄错导致补证失败”的合同。confirm 必须读过完整目标且有新的充分直接证据，不能以重复读取记忆或模型自述续期；状态边界与 S2 一致。

page_changes 只包含页元数据和成员映射；新页使用任务内 N 编号，由程序分配 ID。同页名不是同身份；页面范围由组固定。merge 必须指定保留的已读 O（涉及已有成员时），避免模型自行决定数据库 ID。关联目标须为本次已授权发现的现存页或同组 N 页；提交和读取均再次过滤目标权限/存在性，不接受任意跨项目目标。

自动退休只用于已被合法合并/更正替代的条目，不因“看起来不重要”删除仍有效的独立事实。用户明确 forget 继续走显式动作和抑制合同。候选/不确定内容不通过短 TTL 挽救。

## 6. 时钟与容量

纯组织（移动、拆页、改标题/简介）不改变条目的 evidence_at/ttl_days/expires_at。合并旧条目没有新的完整确认时，结果到期时间取所依赖成员中最早者；不能用最新一条的时钟刷新全部事实。

新完整确认可更新相应内容，但不能使未确认的附带条件续期；必要时拆成独立记忆。partial/unknown 始终仅补来源。原文中的更短期限/条件保留，默认 30 天不是覆盖原意的许可。

每个结果页在同一事务内检查未过期 active＋candidate≤30。模型通过整合或新同级页分配保证容量；返回仍超额则整组失败。不会自动删旧项腾位、自动追加整理调用或保存失败 P 等下次恢复。

## 7. 连通组、事务与失败

同一输入批次的 A 提议先按 scope/topic 分成 B 阅读工作单元，organize 则用指定页集合形成单元。先收集该批全部 B 结果，再根据全部 P、已涉及的 O 和页面写目标建立相连组，最后逐组提交；不得边收到一条决定边写入后再发现同批依赖。失败单元已触及的页面/条目从阅读账本保留为失败依赖。

任一决定即使后来校验失败，其已经发现的关联仍参与隔离。不能把同一旧项的一个失败提议删掉，让另一个提议走 add 绕过 CAS。不同输入批次依实际最新版本重新读取/校验，不承诺整任务多批原子性。

同库一组同时提交记忆、来源、版本、页成员、元数据、链接。跨 scope/cross-project 的组直接拒绝，无跨库写事务。版本、来源归档或有效期变化时整组回滚；失败不会重读再比较或另建新 ID。

| 故障 | 结果 |
| --- | --- |
| A 整体失败 | 本批无新概览/提议，记录失败 |
| A 提议数量超限 | 跳过整份提议列表，合法概览独立保存 |
| A 单条坏提议 | 跳过该条，概览和独立好提议继续 |
| B 必要读失败或没有可分组的输出 | 本组零写入；有可分组的原 finish 时按 3.1 保留独立组 |
| 一组 CAS/范围/容量失败 | 本组零写入，已提交独立组保持 |
| 停止/超时/进程退出 | 结束未提交工作，不恢复候选、不重跑 |
| GC 在读后删除目标 | 提交失败，不把缺失目标视为 new |
| 观察事件投递失败 | 记录诊断，不覆盖已提交结果或原业务异常 |

新来源的以后一次任务是新工作，不恢复失败任务的内存提议。B 在写入前允许 3.1 规定的一次参数修正。事务提交失败结束该依赖组。SDK 传输尝试次数单列观测。

## 8. 文件和接线

- memory_job_runner.py、memory_jobs.py：A/B 分阶段调度，增加显式组织任务，保留已有任务/概览生命周期。
- memory_proposals.py、memory_batches.py：topic/scope E 绑定，移除 kind，保留完整来源边界。
- 新 memory_organization.py、memory_organization_reads.py：有界 read/finish 驱动和完整性账本；不复制 model_access 供应商调用。
- memory_comparison.py 中仍相同的成组/CAS 编译责任可以迁移或扩展；最终已替代的查询组装和孤立包装在 S6 删除。
- model_access/memory_output.py：继续统一 profile/schema/输出限制，必要时仅扩展输出协议声明。
- tests/test_memory_wiki_extraction.py、test_memory_wiki_organization.py、test_memory_wiki_worker.py。

## 9. 验证

假模型脚本按真实 read/finish 协议运行，检查实际读请求、回填、SQL 副作用和任务终局。先复现 W07、W11、目录未读完误 new、版本冲突绕过和 self-reference，再最小实现。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_extraction.py tests/test_memory_wiki_organization.py tests/test_memory_wiki_worker.py tests/test_memory_job_runner.py tests/test_memory_overview_progress.py -q
~~~

正常链覆盖真实 MemoryJobs → 保存后释放 → A → 概览保存 → B 按页读取 → 同库提交。故障注入在测试末显式检查失败次数、零写入/局部成功、源身份和日志；不能让运行时的异常隔离吞掉测试断言后伪装通过。

## 10. 实施主链与边界（2026-11-18）

实际主链：MemoryJobs.request 接受显式v2请求，或隔离Wiki服务以wiki=True沿原轮数/字符触发refresh；after_finish必要保存后_release固定范围→memory_job_runner.drive。普通任务_batch复用原批处理/概览覆盖规则，以WikiExtractionResult替换旧kind协议；overview独立_commit后交memory_organization.organize。organize请求跳过A，沿同一槽位/停止/截止机制直接进入B。

memory_wiki_protocol声明A和B schema，沿prepare_memory_output与严格原始JSON解析支持text/json_schema/function_calling，不复制供应商调用。A坏条目在bind_extraction局部拒绝；overview独立验证，敏感提议不会使合法overview丢失。B每次read或finish，读结果累计进入本工作单元输入，按实际输入窗口、comparison_material_bytes及原任务deadline限制，不设额外调用次数。现有model调用/HTTP attempts观察保持，memory_stage新增organize并关联真实invocation_id，无提示词入日志。

memory_organization_reads.Ledger持有本任务scope/topic、合法发现页、完整目录、页版本/成员与O引用、catalog/generation及到期快照。后台read_page使用active+candidate；每次完整JSON（含O映射）再次检查2000估算token/16KiB。分段完成条目可返回O引用，提交仍要求整页完整；引用不会随翻页丢失。显式来源/history读取必须针对合法已发现O，不能打开其他项目归档。重复无进展、失效游标、错范围或预算不足结束单元。

B全部finish/失败依赖先收集，再按读取触及页连接成组；失败单元保留触及集合。同库组由memory_organization_commit.commit_group持D1、既有来源→目标事务，并用Staged收集所有改变；最后一次WikiWriter.apply验证源/目标页CAS、来源、容量并保存。Staged.matches同时检查本组已计划记录，避免两次add绕过精确查重。组内任何异常由外层事务回滚，没有早到决定的单独提交。独立组成功后的已知catalog增量只更新剩余本任务快照，不吸收外部改版。

补证/确认/更正/新增复用S2 commit门禁；O正文、条件、旧来源均由程序继承。pure duplicate/unsupported零写入、零续期；move/split保留ID、期限、来源；merge继承全部必要来源，只有被替代成员退休。第一版对结构化条件或basis不同的合并明确拒绝，避免丢失限定；可保留为独立项或拆页。无新whole证据的合并取最早expiry并保留候选/gaps；新的whole合并复用replace的真实新来源、权限和冲突门禁，仍保留各成员更短deadline。必要来源超过8整组失败，不丢来源凑上限。

显式organize仅改指定既有页，可读同范围目录并建立新同级页；发现但未授权修改的旧页不能成为写目标。页元数据、成员和关联一次提交；无任意删除/forget分支。停止时排空必要提交以保留真实成功计数；事务失败不重试、不回退保存A，也不把失败提议持久化为候选。

正常例子与测试：真实MemoryJobs→根完成→A→概览→B目录→N页add；旧页complete后supplement/confirm/correction；两主题独立成功；真实工具调用→后台实际完成。失败例子：坏E、敏感提议、未发现页、重复读取、目录未完误新建、页CAS改版、deny、错scope、两次重复add、拆页结果超容量、等待模型时停止，断言终局和SQL零写入/概览保留。

默认运行时仍使用原格式到S6；wiki=True为明确组件选择，不根据数据库形状猜合同。旧比较器仍有旧默认消费者，S6统一切换后清理。本片替换S2的memory_wiki_worker_unavailable执行拒绝分支，旧S2测试迁移为真实执行合同并保留受理/根等待/副作用断言。S4上下文导航、S5 GC、S6真实迁移/默认切换不提前实施；无真实模型质量声明。

## 11. 最终验收

新增29项确定性测试：A协议/证据、B账本/分页/候选、成组新增/更新/合并/移动/回滚、真实后台正常/失败/停止、organize模型调用观察。S2三个旧占位断言已迁移到实际后台执行，保留根等待、必要副作用和真实工具接线验证；未保留旧执行拒绝包装。新单元初始观察模块缺失红灯；追加敏感提议隔离、同组重复add、organize日志先复现失败后修正。新增合并来源测试还修正了内部来源对象规范化，实际正常/失败断言保留。

- 专项命令：`.venv/bin/python -m pytest -q tests/test_memory_wiki_*.py tests/test_memory_model_observation.py tests/test_memory_job_runner.py tests/test_memory_jobs.py tests/test_storage_dependency_direction.py`，163 passed in 9.72s。
- 全后端递归文件清单分四组，1665/1911/1754/1909通过，共7239，0失败/跳过；清单、日志和退出码在build/spec143bo-s3-final。
- 标准macos-arm64构建退出0、Twine PASSED，compileall/pip check/diff检查通过。
- 冻结1394个Python文件无漂移；wheel内564个应用Python文件与最终源码逐字节一致。
- 首轮开发环境缺fd，33项native find测试失败；未改产品/断言或安装系统依赖，沿项目scripts/native_assets.py准备校验固定版本到隔离test-assets，仅为最终测试进程设置PATH。首轮清单仅含顶层，最终清单改为递归包含cloud等全部测试目录并完整重跑。

机器证据：[verification.json](../../build/spec143bo-s3-final/verification.json)。无真实供应商调用/业务质量评测、用户数据库迁移或真实配置改动；未提交发布。默认启动切换归S6，旧默认组件消费者继续保留；下一片S4上下文导航与历史预算。

### S3 审查修复（2026-11-18）

merge 对每个实际消费的新 P 独立复用 S2 record 的来源/basis/范围/有效期门禁，临时编译不产生SQL写入；无whole确认时合并expiry包含新P期限，候选/gaps不得借旧O清除。页元数据创建投影到record、修改与链接投影到update，并检查写权限。B阅读单元返回后按P/O/页面写目标计算决定连通组，失败单元保留已触及页面依赖，避免一个主题的无关坏决定回滚独立好决定。回归包含真实MemoryJobs同主题独立成功和失败工具来源。

S6反馈：B读取schema移除scope，由账本绑定工作单元范围；显式错误范围仍拒绝。add/unresolved_conflict复用单条未改写P且省略E时，代码继承原绑定来源；新正文不适用该省略规则。来源、范围、期限和容量门禁保持。
