# Spec143BO-S4：上下文导航和历史读取预算

日期：2026-11-17。状态：已实施，本机验证记录见第9节。依赖：[S2](143bo-02-wiki-tools-and-scope.md)。本片替换自动记忆注入，不改变历史归档和当前会话压缩的职责。

## 1. 正常例子

新项目会话收到系统/项目规则、Skills/工具说明、最多 2,000 token Wiki 入口、最多 2,000 token 其他会话目录/概览。用户问“继续上次的测试约定”，模型选择历史或 Wiki 入口，分页读取具体内容；不用把所有个人或项目记忆预先放入。

首次目录材料超过 2,000 token 时，只返回预算内完整条目和继续读取入口。后续通过 history.list_sessions/read 找到其余会话；不能说未自动注入的历史已经丢失。

## 2. 当前主链

SessionExecution.prepare → SessionPersistence.prepare_memory → MemoryJobs.prepare → memory_context.prepare_memory_parts → run.prompt_parts 冻结 → compose_model_prompt → SystemMessage。

当前 memory_context 同时做个人约定完整预加载、按用户问题拆词法查询、最多 8 条 related 和最多 8 项 session_directory，合用 injection_tokens=2,000。本片删除前两种自动选记忆方式，保留合法来源/范围包裹与每轮冻结机制。

当前目录由目录 seen/attempt 标记控制，通常首次符合条件的会话请求尝试；不能在本片悄悄变成每轮重发。当前会话的后台 overview 不作为另一份全量摘要叠加，其他会话概览使用已保存的最新版本与覆盖信息。

## 3. 新自动装配合同

| part | 内容 | 预算/时机 |
| --- | --- | --- |
| memory | 三个大主题的范围化入口、读取说明；必要时少量明确 metadata | wiki_navigation_tokens=2,000，每轮按当前有效性准备后冻结 |
| session_directory | 当前项目其他会话的标题/状态/概览短预览/时间/覆盖和读取入口 | session_directory_tokens=2,000，沿首次尝试语义 |

两个预算独立，包裹说明也分别计数，实际和可以大于旧 2,000，但不超过新 4,000。闲置额度保持闲置，不自动补正文/随机记忆。两个配置都为 0 时返回空 parts，不影响显式工具。

根导航提供大主题入口，不把该主题所有小页标题自动塞进启动上下文。小主题目录由 list_pages 按需返回，范围内装得下就全部展示，装不下明确分页。

无数据不生成“示例记忆”；可以返回工具本身支持的分类入口，但不能假称该类已有记录或捏造计数。准确总量需要扫描时不为展示强制 COUNT；省略 total_count，而不是填 0。

其他会话概览沿当前四字段 goal/progress/blockers/next_step、完整概览≤1,200 字符和自动预览≤300 字符的限制；最多 8 个近期会话仍是候选 IO 上界，最终按 2,000 token 截定。其余通过 history 翻页，不是随机记忆挑选。

历史目录和 memory 工具权限分别核验：新装配不能仅因允许 memory 就泄露被禁止的 history；也不能把 history 存在与 memory.enabled 强行绑定。Wiki 关闭后，合法 history 仍能按原能力读取，目录 seen 状态仍由现有持久仓储管理。

新的实际责任为 SessionExecution.prepare → SessionPersistence.prepare_memory 分别准备 Wiki 与历史目录 → 冻结原有 prompt parts。目录的 eligible/used 短期状态从 MemoryJobs 移到 SessionPersistence，在其现有短锁下更新；SQL 的 directory_seen 和目录查询继续复用现有仓储。SessionService.resolve_prompt_parts 通知该所有者发生真实尝试，必要会话保存成功后独立持久化 seen，再通知 MemoryJobs；不依赖后台实例存在。预览不改变任一标记。提取调度仍只归 MemoryJobs，不能为复用目录重新启用被关闭的记忆。

目录只在有真实项目身份、history 已启用且其相关读取权限允许时准备。新 session_directory.py 可承接纯查询/预算装配，使已接近 500 行的 SessionPersistence 不继续膨胀；状态随既有所有者传递，不新增目录服务注册层。标记持久化失败独立记录，不改变已成功运行结果；本进程 used 状态保留，重启后可能再次提供目录，不能谎称严格 exactly-once。

## 4. 历史工具 2,000 token

HistoryInput 动作与必需字段保持，HistorySettings 新增 max_output_tokens=2,000。list_sessions/search/read 以及概览、包裹、来源 metadata 的整个返回 JSON 都计入预算，与 max_output_bytes=16,384、read_chunk_chars=4,096 等既有限制取先到者。

复用 S2 输出预算责任，history 的 SQL、会话/子线程/版本/权限仍由原模块处理。不能通过新增 budget helper 把两个领域的整条处理链合并。

长 overview/消息正文按合法片段输出并标记 complete/continuation；一次返回的概览只是预览时不得标成完整。read 的原 sequence/revision/offset 继续定位消息；完整概览使用 overview_only=true 的独立投影和既有 cursor 分页，不增加另一套公开 offset。

为避免重复在每个消息分页中附带整份概览：第一页附预算内概览与明确详情入口；后续消息页只有概览版本/定位，不重复正文。overview_only 仅允许 action=read 且给出 session_id，与 sequence/revision/conversation_revision/agent_id/query 和非零 offset 互斥；后续以 next_cursor 继续。游标绑定目标会话、根历史身份、概览版本/覆盖与片段位置；概览更新后返回 history_cursor_stale，不能拼接两版摘要。这是参数兼容扩展，原合法请求仍可由新 schema 解析，不伪造原来没有的概览。

工具 schema、许可/参数校验、服务/API 映射、归档 read、脚本和测试同片同步。若完整 overview 本来装得下，则一次返回；不额外调用模型生成摘要。

## 5. 整体窗口与压缩

基础规则、项目规则、Skills 目录、工具定义、子 Agent 角色、后台通知/云端材料、当前消息、工具结果、当前会话压缩摘要、memory/session_directory 全部计入最终请求。

沿 context_calibration.measure_request 和 context_compaction：阈值=window−max(output,reserve)，当前代码默认 window=128,000/output=16,384/reserve=16,384，不能将默认值当成用户配置。当前压缩摘要的默认输出上限 2,048 不改。

2,000 是一次按需工具返回上限，不是所有已读内容总和上限。连续读取会累计占用消息窗口，依现有机制压缩；本片不建立记忆专用摘要/向量索引/缓存，不静默截掉当前用户请求或项目规则。

读后到期的记忆可能仍在旧消息中；过期规则保证不再从新导航/工具返回为有效，不承诺撤回过去发送内容。每个页面保留 evidence_at/expires_at 与不可信数据说明，派生摘要/工具返回不得成为续期证据。

## 6. 性能与错误

导航只查询有界 metadata，先范围过滤；读正文时才加载正文，证据原文按需。每条格式化一次、最终统一序列化，避免对累计整包反复渲染。请求与上下文预览共用同一装配函数，预览零写入、零模型调用、不得消耗目录已读标记。

页面目录/历史目录失败分别记录并独立降级；memory 失败不隐去已合法取得的历史。budget 耗尽、权限拒绝、游标变化和没有数据分开计数。结果未知不能宣称完整空集合。

SystemMessage 部分计量与总请求计量保持真实；新增/改变 part id 时同步 context_usage.PARTS、前端显示和序列化消费者。优先保留 memory/session_directory ID，不为改名扩散 UI 变更。

## 7. 文件

- application/memory/memory_context.py：改为 Wiki 导航，不保留旧自动检索备用分支。
- application/memory/memory_jobs.py、sessions/session_persistence.py、session_service.py、session_execution.py、session_context.py，以及新增 sessions/session_directory.py：拆清 Wiki/历史权限与目录生命周期，运行和预览同源；同步 lock-hierarchy 的实际所有者变化。
- tools/history.py、tools/history_settings.py、storage/conversation_sql/history_queries.py/history_projection.py：结果 token 分页及概览独立投影。
- agent_runtime/context_usage.py、必要的参数/服务映射：计量和当前上下文预览接线。
- tests/test_memory_wiki_context.py、test_history_token_budget.py、现有 history/context/permission 测试。

## 8. 验证

先写失败用例：两个自动组各 1,800 token 时均保留；其中一组达到上限不挤掉另一组；全文读过 2,000 还能继续；历史概览有/无/过期覆盖和只有原文的会话；首次 attempt/重启/预览不误消费；关闭 memory 但允许 history；拒绝 history 不泄漏标题；普通聊天不见项目目录。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_context.py tests/test_history_token_budget.py tests/test_memory_context.py tests/test_memory_overview_progress.py -q
~~~

通过真实请求装配的假模型检查 SystemMessage、messages 和 tool schema，并检查读取不写库/不续期。不把静态截字符串测试当作最终模型请求预算接线。

## 9. 实施边界与当前接线（2026-11-18）

Wiki实例的MemoryJobs.prepare接memory_wiki_context.prepare_wiki_parts，仅读取list_topics元数据并提供list_pages入口，不检索正文。SessionPersistence.prepare_memory独立装配session_directory，目录状态归其现有短锁；resolve_prompt_parts标记真实尝试，必要保存成功后mark_directory，失败只记录诊断；预览不写。memory关闭且history启用时目录仍可用，逐项目/工具/动作/会话过滤，两个预算不借用。当前memory/session_directory part ID保留，现有计量与UI继续消费。

HistorySettings.max_output_tokens约束整个JSON（含游标），与字节预算取先到者，使用UTF8保守估算防止中文低估。history的overview_only扩展经原request字典传到归档query，独立分页返回概览文本及版本/覆盖；游标绑定来源、目标、根线程历史身份和概览快照。普通消息首页只带概览预览，后续页不重复正文。

默认产品工具/后台版本统一切换仍归S6。为保持这个已确认的版本边界，旧默认MemoryJobs的自动检索/目录与旧memory_context消费者暂留至S6，不作为Wiki失败后的备用分支；Wiki实例没有调用旧检索。S6须同步删除旧上下文实现和旧J1目录状态。普通个人聊天的默认旧代注入也保留至S6一起切换，避免旧代写入尚在使用时提前改读新代。

本片没有真实模型业务质量结论，不修改用户真实配置/数据库。验证证据见build/spec143bo-s4。

审查补充：目录权限使用HistoryInput补齐默认值后检查，避免agent_id=None规则遗漏；memory关闭时仍从Host向SessionPersistence传递真实MemorySettings，不重建默认配置。概览覆盖中的skipped明细可达4096字节，普通及概览投影提供skipped_count/skipped_details_omitted，保留版本、覆盖上下界及快照绑定，不让不可裁剪明细堵住历史分页；complete只表示本次概览正文片段已读完。完整遗漏列表仍保留在数据库和任务诊断，本片不新建遗漏明细工具。

验证结果：模块/配置868通过、最终受影响路径32通过；全tests扫描7217通过/34失败，修正原生工具PATH及普通聊天默认代边界后34失败项全部重跑通过。标准Mac构建/Twine、568个打包Python逐字节匹配、compileall/pip check/diff通过。证据：[verification.json](../../build/spec143bo-s4/verification.json)。这不是一次全量全绿结果，原始与补跑日志均保留。
