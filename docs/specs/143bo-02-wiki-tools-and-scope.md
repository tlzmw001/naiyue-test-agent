# Spec143BO-S2：Wiki 工具、分页和统一范围门禁

日期：2026-11-17；实施更新：2026-11-18。状态：S2-1—S2-3 工具组件已实现并通过S2整体验收；正式默认入口切换归S6，后台Wiki执行器归S3。依赖：[S1](143bo-01-storage-and-new-generation.md)。目标是模型可以发现、完整读取及显式保存新代记忆，读动作零模型调用、零续期。所有操作均是现有 `memory` 工具的 `request.action`，不注册 list_topics/read_page 等独立工具。

## 1. 正常和失败例子

正常：memory.list_topics → list_pages(scope=workspace,topic=working_agreements) → read_page → record/update → 返回真实保存的 ID、版本、页面和期限。首条记忆可以在同一事务中建立主题页，不要求提前创建空页。

失败：模型在 30 条页上直接 record → memory_topic_full、零写入、返回该页读取和 organize 入口。后台不会自动接管这条失败提议；调用方可以明确请求先整理，但不能把“已受理整理”说成“记忆已经保存”。

## 2. 对外动作合同

工具名仍为 memory，参数仍是 {request:{action,...}}；动作签名升级为 memory_actions_v2。旧参数中 kind 和类型 TTL 消失，不悄悄忽略它们。规范化动作仍在许可中精确绑定；旧审批/待执行调用安全拒绝。

| action | 模型填写字段 | 返回与副作用 |
| --- | --- | --- |
| list_topics | scope=both/personal/workspace | 固定三主题的合法入口；无虚构记忆/数量 |
| list_pages | scope、topic、cursor? | 标题/简介/页 ID/版本/读取入口，2,000 token 内分页 |
| read_page | 具体 scope、page_id、version?、cursor? | 页面 metadata、记忆正文及关联的有界视图，分页，不续期 |
| list/search/read | 原 ID/查询/状态/分页字段，去掉 kind | 仅当前新代；显式词法辅助和条目来源/历史读取仍有消费者 |
| record | 具体 scope、destination、proposal、sources、scope_source_indexes? | 原子建立或使用页面，提交一条记忆 |
| update | 具体 scope、memory_id、expected_version、change、sources、scope_source_indexes? | 局部补证、完整重新确认或替换正文，均走相同门禁 |
| disable/forget | 原具体 scope、ID/期望版本、sources、reason | 保持来源与权限要求；forget 才建立抑制 |
| refresh_overview | 原 target_session_id/start_sequence/end_sequence | 继续登记从合法历史提取的任务，返回 accepted/task_id |
| organize | 具体 scope、page_ids（1..8） | 登记只整理已存在页面的 B 阶段任务，不能续期或读取旧代 |

停用动作单独验证用户指令，并保存停用原因与版本。停用继续保留旧正文的证据，不将停用指令并入正文证据，因此已有 8 条来源仍可以停用。遗忘继续删除原证据并建立抑制。

前台读页与后台整理展开关联链接时，按目标页的 `read_page` 参数检查拒绝规则，再查目标库。没有读取权限的目标不出现在链接结果中。SQLite 完整性异常导致写入回滚时，记录失败事件与异常类型，日志不包含数据库异常正文。

destination 为互斥结构：{page_id,expected_page_version} 或 {new_page:{topic,title,description}}。不能同时提供两种。创建新页与写入在同一事务；普通 update 不偷偷搬页，复杂移动交 organize。

change 为三个互斥分支：{mode:"supplement"}、{mode:"confirm"}、{mode:"replace",proposal:{...}}。supplement 表示局部/未知范围的直接补证，confirm 表示对现有完整内容的重新确认；两者均不接收 body/title/conditions/expiry，由程序继承旧内容。只有 replace 提交新正文，不要求重复 scope 来源原句或旧引用文本。工具说明先引导选择动作；未声明字段仍按 schema 拒绝，不设计前台任意载荷修复器。

proposal 沿用 title/body/subject/条件/basis/intent/gaps/reason/显式 deadline 的既有约束，移除 kind。长期价值由提示和语义评测判断；不能通过程序字段检查宣称已经证明长期价值。

scope_source_indexes 指向本次 sources 的零基位置，最多 8 个、去重且不得越界。后台用 E 编号表达同一关联，再由 binder 转为索引；不让模型重写第二份 MemorySource。workspace 必须为空；新建 personal 必须非空且所指来源为真实用户。替换 personal 正文时可以继承已有同事项的范围依据，但新的内容和限制仍需本次真实用户来源支持；不能用继承旧依据免除语义复核。

## 3. 分页返回

统一新增返回字段：generation_id、items/content、has_more、next_cursor、complete、budget={estimated_tokens,max_tokens,bytes,max_bytes}。read_page 还返回 scope/page_id/version 和每条记忆的 memory_id/version/conditions/evidence_at/expires_at。

content 是代码生成的 Markdown；ID/版本/游标等结构字段用于程序和工具继续读取。正文只保留一份，不在 items 中再重复整段 Markdown；items 只携带导航/定位与完整性 metadata。

完整工具 JSON、Markdown、来源状态、分页入口和包裹说明均计入 2,000 token；旧 16 KiB 字节上限继续兜底，先到者生效。预算是统一估算值，不是供应商精确账单；整请求仍经过现有校准和上下文检查。

游标绑定 generation、调用可见范围、topic/page、排序键、页面/catalog 版本、读取投影、条目内 offset 和该视图最早到期点。游标不是授权，解码后必须重新检查范围和版本；不接受任意数据库路径/项目 ID/SQL。

目录和页成员使用稳定键游标，不使用深 OFFSET。版本变化或视图中记录已到期使继续读取失去一致性时，返回 memory_cursor_stale，调用方重新取得当前页；不静默把两版拼成完整页面。

通常在条目边界分页。单条无法装入时，在自然边界分段，返回 entry_complete=false、条目版本、继续位置，并重复必要条件和到期信息。所有后续页必须推进；不能返回空页却给同一个 cursor。若必要 metadata 与最小内容片段仍装不下，返回有界 memory_budget_too_small，不伪造完整页或无进展游标。

工具提示要求前台在依据旧正文修改前读完相关条目；这是模型操作要求，不宣称普通前台 CAS 检查证明了完整阅读或理解。后台 S3 有任务内阅读账本，必须证明相关页面及必要条目已读完，才能提交涉及这些旧项的决定。

公开 list_pages/read_page 默认 active 投影；后台受控整理入口使用 active＋candidate 投影。两者共用查询/分页实现但分别绑定权限、投影与游标；不能将后台隐藏候选投影误注入普通页面。已有显式 list/read 的状态查询权限保持。

## 4. 范围语义与代码校验

固定语义标准见总规范。代码检查真实用户身份、现有 E/来源绑定、范围一致、所有者、权限、ID/版本和容量；不做“出现所有项目关键字即 personal”的规则。

以下三件事分别处理：

1. 原文确实存在：归档、版本、位置、引文一致性检查。
2. 来源身份可信：user_origin 由真实根用户输入标记导出，子任务、AI 和工具不能冒充。
3. 原文支持个人范围和新正文：模型语义判断＋固定评测；程序不冒充语义证明器。

用户转述第三方材料或局部否定要求由 A/主模型/B 看上下文识别。必要限定被裁切或仅在不可引用 context 中时，不构造扩大后的记忆。

新 personal 不接受工具结果作为唯一范围依据。补证必须保留已有 supports_scope 记录；旧个人正文可跨项目使用，不开放来源项目的原始会话读取。新范围确认不得绑定为已不可验证的“新来源”。

memory/history 返回、会话概览、压缩摘要和 AI 自述不能作为重新激活/续期的独立工具观测证据；程序按已知生产者/消息 kind 拦截这种自我引用，防止一次读取把旧内容变成刚发生的事实。用户重新明确确认则按真实新用户消息及 whole/partial 语义处理。

## 5. 直接写入与生命周期

MemoryAPI.query 继续在根/子 Agent/审批恢复处检查当前 RunScope 与执行绑定。写入先等真实来源归档保存，再进入 MemoryAccess/统一提交器；Wiki 新动作也必须可被权限规则拒绝。

新条目 evidence_at 来自能支持正文的真实来源发生时间，ttl_days 取当前配置；expires_at=evidence_at+ttl_days×86400，与真实用户指定的更早 deadline 取最小。过期证据不能靠写入时钟变成新 active。

同事项精确冲突沿既有 candidate 合同：真实有据但冲突未解可以保存 candidate，默认页面不展示；失败、坏引用、范围不明或来源缺失不能降级保存 candidate。candidate 也属于长期价值明确的提议，默认 TTL 同样 30 天。

supplement 不换正文/条件/范围/TTL/到期时间。confirm 保持正文和条件，由新的充分确认更新 evidence_at，并采用当前配置 TTL；不要求模型逐字重抄正文。confirm/replace 的续期都要求证据严格晚于原 evidence_at 且支持完整内容；不能仅凭 mode 字段证明语义充分。不完整确认只能选择 supplement。个人通用内容的确认须有相应真实用户依据，历史/工具自我引用不得续期。

confirm 不自动激活 inactive/candidate/forgotten；这些状态沿显式激活或忘记抑制合同另行处理。自然到期但尚未清理的 active 可由真正的新完整确认恢复有效；已物理清理的 ID 返回不存在，不自动创建替身。原明确截止条件保留，不能因默认 TTL 更新延长用户原先规定的期限。

现有精确重复检查继续拒绝相同正文的无意义新建；不新增模糊查重算法。直接写入页面合法性和最终容量在写事务内验证，不把前置 COUNT 当成并发保证。

## 6. 目录、关联和渲染的权限

- 先筛范围再组装标题/简介/计数/链接，不是先渲染再删正文。
- `scope=both` 的读请求先按同一动作与选择条件分别检查具体范围。只打开获准的库；没有获准范围时返回权限失败。分页快照只绑定实际可见的库。
- 个人页面不能引用项目私有页；项目页面只能引用自身项目或个人页。
- 同名页不代表同一身份；合并范围目录保留 scope 和稳定 ID。
- 缺失/过期目标不生成可读假链接；真实来源不可读只报告 unavailable/other_workspace，不伪造 quote。
- 标题、简介或 Markdown 链接不是执行指令；历史内容保持不可信包裹。
- 没有记忆则返回空数据与合法工具入口，不生成示例条目。
- 当前项目中的「以后」本身不表示跨项目。前台工具说明要求个人范围有用户表达的一般偏好或跨项目要求。用户只确认旧约定的一部分时用 supplement，未确认其余部分不构成替换的依据。

## 7. 模块和配置

扩展 tools/memory_actions.py、memory.py、memory_writes.py 及 permission_rules.py；新增 action contract 时同步 permissions.py 中批准绑定、只读/写操作识别、scope 规则及测试。

扩展 application/memory/api.py、memory_access.py、memory_mutations.py、memory_sources.py。新增 memory_wiki_render.py 承担页格式，和一个明确服务 Wiki 导航/目录/正文及 history 的输出预算函数；因为已有至少四处同职责，不建立通用插件平台。

预算函数输入为已格式化条目、固定 envelope、token/byte 上限，输出有限结果与 continuation；不拥有 SQL、权限或模型。条目只格式化一次、批量取所需字段，最后做整包检查，避免每加一项重渲染所有正文。

旧文本 search 的评分/索引保持；去掉 kind 过滤。目录不 SELECT 整条正文，不读取来源原文和所有历史版本；正文需要的来源 metadata 批量获取，避免 N+1 和大联表乘积。

## 8. 验证

新增 tests/test_memory_wiki_tools.py、test_memory_wiki_scope.py、test_memory_wiki_pagination.py；迁移原动作/审批/多项目测试，保留顺序、来源、异常身份和副作用断言。

必测：旧许可拒绝、新动作 deny、空目录及仅有 candidate 的整理目录、2,000 边界/CJK/emoji/长引文、完整翻页、游标错范围/错代/过期/改版、单条分段及预算不足无进展、1→30→31、两个并发写入、后台/直接写入门禁一致、source-only 不接新正文、confirm 保正文且仅新完整证据续期、个人来源缺失、跨项目标题泄漏、自我引用续期。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_tools.py tests/test_memory_wiki_scope.py tests/test_memory_wiki_pagination.py tests/test_memory_action_contract.py tests/test_memory_approval_context.py tests/test_multi_project_memory.py -q
~~~

验收要证明 registered tool → permit → API → MemoryAccess → Repo → ToolMessage 的真实接线；只测 helper 不足以通过本片。

## 9. 实施单元及当前边界

以下S2-1/S2-2段落记录各单元当时边界，当前状态以S2-3及最终验收为准。按三个连续单元落实原合同：S2-1 参数/权限，S2-2 查询/分页/渲染，S2-3 来源绑定/生命周期/写入与完整 API 接线。默认工具与数据库启动切换仍属于 S6，不能在项目25/个人4上提前执行 Wiki SQL。

S2-1 新增 memory_wiki_actions.py：去掉 kind 的提议、互斥 destination、supplement/confirm/replace 分支及有界 scope_source_indexes；来源位置索引只是关联，不证明真实身份或语义，后两者分别由 S2-3 binder 和模型判断。v2 spec 工厂复用现有 memory 的运行时执行函数，工具名称仍为 memory。现有默认注册仍消费 v1 工厂；隔离测试显式选择 v2 工厂，不在一个 registry 同时注册两个 memory。v1 组件目前仍有旧默认运行消费者，S6 切换后再删除。

权限准备根据实际 spec 写入 v1/v2 marker；规则投影按 marker 解析，不改写许可中的原参数。v2 执行拒绝 v1 许可，不能把旧审批当作新动作审批。真实 create_agent 组件验证了注册工具→权限准备→执行许可→能力端口，API 在该测试中使用观测替身；这不证明 API/Repo 主链、分页或写入已完成。

新增13项测试，其中初始9项在实现前观察失败，覆盖契约、参数拒绝、单 memory 注册、OpenAI/Anthropic schema、v2 deny 及旧许可拒绝。完整 S2 验收仍须完成第8节所列剩余行为，不将本单元绿色测试当作 S2 完成。

### S2-2 实施边界

新增 memory_wiki_reads/render：实际链为注册 memory(v2)→既有许可→MemoryAPI.query→query_wiki（重新检查调用会话/项目）→Repo.wiki_view→ToolMessage。工具从实际许可合同向能力端口传入可选 argument_contract（旧调用默认v1）；API按v1/v2明确验证与分派，不根据正文或用户字段猜数据库格式。v2的全部动作接入新代，v1仍由默认启动消费者使用至S6。测试使用隔离显式准备的新代库，正式默认入口仍未切换。

wiki_view 单事务返回 generation/catalog、最早有效到期点及最大50行目录或成员；目录只取metadata，正文来源metadata一次有界批量查询，不取quote/旧版本。来源原文状态明确 not_checked/other_workspace，不把已保存快照冒充原会话仍可读；精确出处读取保留给显式read动作。跨库关联再次按合法当前目标metadata过滤，空页/到期目标不输出。

游标用 MemoryAccess 生命周期的32字节随机密钥HMAC签名，防止任意修改定位/完整性声明；不作为授权，重启Access后失效。绑定会话/项目、scope/动作/主题或页面/投影、generation/catalog版本、稳定键及条目偏移，继续读取重新核验。全库最早active到期及catalog改版采用保守失效，可能因同库其他页变化要求重读，但不会静默混版。后台candidate投影只能由受控内部读取器选择，普通参数拒绝该字段。

wiki_read_tokens已接MemorySettings，默认2000。完整JSON返回（含游标、metadata、Markdown围栏及budget自身）按实际工具序列化计算字节，token估算取LangChain近似计数与UTF8字节/3的较大值；它是保守统一估算，不是供应商精确计费。字节上限取配置与16KiB较小者。已格式化条目只保存一份，逐项组合并检查完整envelope；单条超长二分选择可容纳片段后优先就近句/行边界，不丢字符。连最小metadata和一个字符都无法容纳则明确报memory_budget_too_small。

初始4条读取行为测试先观察入口缺失红灯；Markdown包裹另观察失败后接入。新增11项验证了65页目录遍历、Unicode正文完整还原、只读不续期、错范围/调用者/generation/改版/到期/篡改游标、候选投影隔离、来源状态、关联过滤、真实工具到真实仓储读取。后续S2-3仍须补写入、旧ID检索迁移、刷新/整理任务及完整统一门禁；本单元不冒充这些已完成。


### S2-3 实施与验收边界（历史：S3已替换下述执行拒绝分支）

v2完整工具链：memory._create_memory_tool核对许可→MemoryAPI.query校验RunScope/执行绑定并按argument_contract验证→写入先save_memory_sources→MemoryAccess.query_wiki校验当前会话/项目→wiki_edit_with_sources复用原project源→personal目标事务顺序→memory_wiki_mutations.commit→WikiWriter.apply→有界回执。全部模型动作仍在一个memory工具内。

新配置default_ttl_days=30、wiki_topic_max_memories=30已接MemorySettings；新建/完整确认使用当前TTL，补证保留旧值。resolve_wiki按原sources位置绑定scope索引，再去重并保留scope标记；不让去重改变索引语义。程序核查真实消息、跨度、身份；工具观测必须配对真实生产调用，memory/history及压缩摘要/AI自述不作为独立新依据。模型仍承担长期价值、完整确认/限定和个人范围语义，代码不冒充语义证明。

supplement继承旧正文/条件/范围/TTL/期限；confirm仅对active且有严格较新完整直接依据续期，可恢复尚未清理的自然到期active，保留旧截止条件；replace要求新直接依据，同ID/CAS，个人内容另需真实新用户来源并保留必要旧范围依据。必要来源超过8整组拒绝；forget不为删除指令再占第9个积极依据槽，先核实用户授权，在同一事务删除所有版本/来源、清空正文并记录旧新最小身份抑制。记录回执只回显身份和期限，不重复大正文；正文通过read读取。

list/search/read复用原SQL词法评分、RRF、锚点和去重策略，仅v2去掉kind条件并检查新代owner。list稳定键游标，read支持原offset及新增签名cursor（两者不能混用）；sources批量读取，read按原出处可见投影核验，原文消失/其他项目不伪造quote；旧版本沿原有界历史预览。读取结果仍受统一整包预算，必要时明确标记sources/history截断。当前来源核验不是给personal开放来源项目原始历史。读取前后复核generation/catalog，跨进程改版不静默拼接结果。

organize和v2 refresh_overview复用MemoryJobs的受理、队列上限、根回复完成后释放、任务查询、停止和关闭设施；organize先核验1..8个本范围既有页及代/页版本，释放前再核验，不推进历史提取起点。不同任务合同不合并。S2只承诺受理，真正A/B执行属于S3：当前隔离组件若启动这些任务，明确失败为memory_wiki_worker_unavailable，零模型请求、零写入、不重试，不交旧kind执行器处理；S6正式启用前S3会替换此安全拒绝分支。accepted绝不等于已经整理保存。

原worker测试fixture由手写SimpleNamespace任务迁移到真实MemoryJob，保留原输入/断言/故障注入，使用新字段的实际默认值；未新增为测试替身服务的生产兼容分支。新代与旧代默认运行仍各有真实消费者，S6才统一删除旧工厂/格式分支；未迁移用户数据。


## 10. 最终验收（2026-11-18）

S2工具组件已完成：单memory v2注册→精确许可→运行绑定→必要来源归档→新代查询/统一提交→仓储事务→有界ToolMessage。新提交器与记录投影独立于旧kind实现，是因为旧默认入口/格式仍有消费者；词法算法和来源核验继续复用，S6切换后清理旧工厂/提交路径。S3会使用统一Wiki提交器并验证后台调用链，S2不宣称后台B已经执行；history的2000 token接入属于S4，当前统一预算已供Wiki目录/正文/ID读写使用。

验证命令与结果：

- `.venv/bin/python -m pytest -q tests/test_storage_dependency_direction.py tests/test_memory_wiki_*.py tests/test_memory_action_contract.py tests/test_memory_approval_context.py tests/test_multi_project_memory.py`：128通过。
- 全后端按测试文件分四组，各1647/1899/1762/1897通过，共7205，0失败/跳过；文件清单及退出码见build/spec143bo-s2-final-fixed/backend-shards.json和backend-results.json。
- `PYTHONPATH=. .venv/bin/python scripts/build_release.py --target macos-arm64 --output build/spec143bo-s2-final-fixed/release`：退出0；Twine、compileall、pip check与diff检查通过。
- 1386个Python源码/脚本/测试冻结摘要保持，wheel内560个应用Python文件与最终源码逐字节一致。
- 额外真实仓储探针：同页29条后并发两写，一次saved一次memory_conflict；第31条memory_topic_full，最后条数和页版本均30。初次探针因macOS临时路径未resolve被安全目录检查拒绝，修正探针路径后通过，未修改安全检查。

实现中修复了新页metadata敏感值绕过、八条必要来源时遗忘指令不应占第九个积极依据槽、应用层SQL依赖越界；均观察失败后修正并通过回归。共享worker夹具改用真实任务对象，保留旧断言。首次全后端SQL检查失败的原日志保留在build/spec143bo-s2-final，最终四组在修复后的同一冻结源码完整重跑。

最终机器可读证据：[verification.json](../../build/spec143bo-s2-final-fixed/verification.json)，专项/构建/全量日志在同目录。无真实模型效果评测、用户库迁移、真实配置改写、提交或发布；正式默认工具/格式切换仍归S6。下一片为S3后台提出与按页整理。

## 11. S2 审查修复（2026-11-18）

五项回归先红：confirm 冲突结果必须写入状态/gaps；来源抑制不阻断同一用户命令的多次disable/forget；both目录先读完达到SQL批次上限的前一范围，不能提前跨库推进游标；显式candidate/all查询的到期快照包含候选；无项目个人整理按无执行目录身份受理，根完成释放不更新项目历史进度。原积极写入抑制、真实用户、范围及CAS门禁保留。

新增 tests/test_memory_wiki_s2_fixes.py，五项失败后最小修复；普通聊天全链测试另暴露项目tick错误并修正。S3执行、S6正式默认切换边界保持。

修复后验证：`.venv/bin/python -m pytest -q tests/test_memory*.py tests/test_multi_project_memory.py tests/test_storage_dependency_direction.py`：759 passed in 33.05s。标准构建`PYTHONPATH=. .venv/bin/python scripts/build_release.py --target macos-arm64 --output build/spec143bo-s2-review-fixes-final/release`退出0，Twine检查PASSED，diff检查通过。首次复用已有输出目录被构建器拒绝，改用上述新目录完成，未覆盖产物。本轮未跑全后端或真实模型评测。
