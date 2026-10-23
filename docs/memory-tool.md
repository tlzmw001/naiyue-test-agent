# memory：项目与个人记忆的查询和维护

从0.4.0提供，设计与验收见Spec127。提供list/search/read、record/update/disable/forget及refresh_overview。概览/记忆整理在完整根用户轮正常保存后执行；达到配置阈值也可自动登记。真实新库为空时返回空结果，不注入示例或遍历旧会话自动填充。

## 入口与作用域

memory已加入CLI/Web共享内置工具，当前是parallel/may_mutate/default allow，仍遵守会话/角色工具集合和现有权限、审批及调用许可。需要持久化应用和可信workspace UUID/root绑定；纯内存或关闭memory时返回明确不可用。已有会话保持其保存的工具选择，可从原入口启用。

默认scope=both，查询当前workspace与personal；可指定其中之一，不能提交任意工作区、数据库路径或SQL。项目数据在当前sessions.sqlite的独立表中；个人库位于配置data_dir/personal/memory.sqlite，相同data_dir的多个工作区应用共享。首次需要个人读写时才打开，关闭总开关不删除数据。

```json
{"action":"list","scope":"workspace","limit":10}
{"action":"search","query":"pytest 测试类","scope":"both"}
{"action":"search","query":"代理","machine":"windows","status":"active"}
```

read使用返回的具体scope、memory_id及version；offset从0开始，以返回next_offset继续读取。version是期望当前版本，已更正时返回memory_version_changed，不静默改读新正文。

list/search默认仅active且未到期。用户明确查询时可选candidate/inactive/expired/all；all也不返回forgotten正文。可按kind及machine/module/target/subject筛选；未知条件保留needs_verification，明确不匹配的记录排除。读取不续期、不转正，不按当前TTL设置重新计算已保存expires_at。

## 检索、结果与预算

中文二元字组和完整技术标识符进入FTS5/BM25，各库分别排名，再以RRF合并；每个显式expansion必须满足原查询和自身技术锚点。完整词段匹配优先，否则按覆盖门槛回退。expansions最多5个，由调用方已有推理提出，工具不调用扩词或评分模型。

K由默认/单次limit决定，受max_limit约束。先取排序后的K条，再严格折叠相同范围、类型、状态/期限、条件、依据及标题正文的副本，保留duplicate_ids；不自动从后续弱结果补位，不合并代码缩进/换行差异。折叠后可能少于K。list逐条列真实条目，用next_cursor继续；search是有界top K，以truncated表明截断，需要更多时缩小查询或改用list，不提供假稳定搜索翻页。

结果含真实ID/version/scope、分类/状态、正文片段、条件、依据时间、采用TTL及截止时间和来源引用。只有当前项目的可见归档、消息/会话版本、角色、tool_call_id/reply_id及引文区间均匹配时，来源才available并允许read返回短引文。其他项目的个人记忆来源只给定位和other_workspace，不跨项目读取原文。来源不可用不等于整条记忆自动失效，也不代表已验证事实当前仍适用。

## 显式记录、候选与生命周期

以下是调用参数示意，不是已保存的数据或实际效果证据：

```json
{"action":"record","scope":"workspace","proposal":{"kind":"context","title":"pytest环境","body":"使用.venv运行pytest","subject":"pytest环境","basis":"user_declaration","intent":"activate","reason":"用户明确约定后续使用"},"sources":[{"current_user":true,"quote":"以后测试使用.venv"}]}
```

record/update要求完整proposal和最多8条来源。current_user只选择调用会话当前根thread由应用接收的真实用户输入；子任务文字不能伪装成用户声明。也可引用history返回的具体session_id/thread_id/message_id/revision/conversation_revision、quote和offset。写入前保存当前正式checkpoint的可见档案，再核对范围、版本、角色及引文；不运行/恢复目标会话。没有可信发生时间的旧档案拒绝作为新写入来源，可由用户在新轮重新确认。

basis为user_declaration/tool_observation/inference；模型可提出candidate或activate。明确持久用户声明，或成功工具直接支持且目标明确的可复用观察，在没有缺口/已知冲突时可首次active。推断、缺持久意图/条件/验证等保留candidate和gaps，不按时间、次数或confidence自动晋升。程序核对来源和确定性门禁，不能证明自然语言的语义蕴含；模型仍需对“是否值得记、是否支持”负责。已知敏感模式拒绝保存，不声称能识别所有秘密。

update、disable和forget使用查询返回的真实memory_id和expected_version，scope必须具体。update替换完整建议并补证/更正，同稳定ID递增版本；kind固定以免通过改类型绕TTL。候选转正、到期后恢复需真实新证据。已知同类型/subject/条件的active正文冲突降为candidate；完全相同已有记录拒绝重复创建。disable保留历史并退出默认查询；forget清记忆当前正文/条件/版本/引文/索引，只留最小墓碑和同scope库旧来源身份哈希抑制。抑制按已知消息版本，可能一并阻止同条消息中的其他候选；不做同义文本识别、不删除原会话或已有备份，也不承诺物理介质安全擦除。

候选TTL各类默认30天；active的user/rule/decision无类型TTL，其余如配置所列。明确deadline（来源给出的Unix秒时间）即使免类型TTL也遵守；与类型期限取早。普通读取、旧来源重提或改写不续期；新来源身份且真实时间晚于原依据，才以来源时间采用当前TTL策略。没有重新指定deadline的确认保留原明确截止时间。TTL配置不追溯既有记录，按类型可仅覆盖部分配置；正整数上限36500天，免TTL类型不能配置active类型TTL。

成功回执含saved/still_candidate、真实ID/version及缺口；并发旧版本报memory_conflict，不能覆盖更正/停用/遗忘。回执超预算时缩短文字并标truncated，已保存正文完整；read可读取当前gaps/reason/deadline_at，include_history显式返回最近最多5个版本的短正文和理由/缺口，history_truncated表明历史受条数或字节预算截断。新建/更新前检查最小详情预算，超限以memory_metadata_budget拒绝并回滚；保存后若人为调小预算，读取仍可能报memory_output_budget，需恢复足够预算，不裁改已保存正文。IO/来源必要保存失败是配对的工具错误，主Agent可继续，不能宣称已记住。

## 启动配置

以下是默认值示意；不会自动修改实际settings.toml。配置启动读取。

```toml
[memory]
enabled = true
auto_enabled = true
trigger_turns = 8
trigger_chars = 20000
# model_id = "已存在的模型ID" # 省略时冻结登记时的会话模型；不创建额外模型。
max_input_tokens = 16000
max_output_tokens = 4096
overview_chars = 1200
max_proposals = 8
max_batches = 3
task_timeout_seconds = 300
max_pending_tasks = 32
max_active_tasks = 1
injection_tokens = 2000
preference_limit = 3
related_limit = 8
directory_limit = 8
default_limit = 10
max_limit = 50
candidate_limit = 100
field_weights = [1, 1, 1]
fallback_coverage = 0.5
rrf_k = 60
max_query_chars = 2000
max_query_terms = 128
preview_chars = 300
read_chunk_chars = 4096
max_output_bytes = 16384
source_limit = 3
busy_timeout_ms = 2500

[memory.candidate_ttl_days]
user = 30
rule = 30
decision = 30
context = 30
procedure = 30
experience = 30
reference = 30

[memory.active_ttl_days]
context = 30
procedure = 180
experience = 365
reference = 90
```

default_limit必须<=max_limit<=candidate_limit，max_limit最大50；max_output_bytes至少4096。candidate_limit是每库/每个variant的候选上限，和返回条数K不同；max_query_terms限制分词后的查询复杂度。整包JSON含来源、元数据和游标计入输出预算，必要时少返回条目、缩短正文或减少来源，并标注截断。工具IO/FTS故障与真正无结果分开。

memory字段可解析但校验非法或model_id不存在时安全诊断并禁用本模块，普通Agent/history仍可运行；全局TOML语法/权限失败仍沿原启动失败规则；不输出完整配置或敏感正文。enabled=false关闭整个模块；auto_enabled=false只关闭自动整理，保留显式整理和查询。修改配置后重启，现有会话可沿工具选择启停memory。

## 概览与后台整理

```json
{"action":"refresh_overview"}
```

回执是accepted与task_id，表示登记成功。工具本身仍按原parallel调度返回，不在这一工具调用中等待会话结束或另调模型。当前根轮正常保存后，应用worker独立运行；审批等待保留请求，续跑正常完成再释放，停止/失败不释放。可用target_session_id选择同目录的保存会话；start_sequence/end_sequence可限定范围，范围请求在目标已有任务时明确拒绝，不静默丢弃边界。同会话普通请求可合并，保持首次模型和deadline；其他调用会话不能接管在途目标。

自动阈值是“自上次受理后新增完整用户轮达到trigger_turns，或可见用户/助手字符达到trigger_chars”。受理成功或合并才推进基线；队列满保留未受理材料，下次正常结束再检查。失败/超限不重复卡住旧段，后续新材料继续推进。旧会话第一次新轮前建立基线，不回放整个历史；首次输入可包含最近旧轮。

每批一次已配置语言模型输出overview={goal,progress,blockers,next_step}及至多8条proposals，不调用embedding、扩词、评分或格式修复模型。概览与每条记忆分别校验并提交03写入合同；成功概览不等于建议都已保存。相关记忆带真实ID，候选还带缺口；版本由程序在请求前冻结，不要求模型填写。模型可在新证据支持下提议同ID转正，CAS/来源/TTL等仍由代码校验。已配置deny规则同样约束内部list/search/read与写入。

[127-07后台合同](specs/127-07-memory-model-contract.md)将建议改为平铺的kind、subject、body、basis、intent、sources六个核心字段；scope及环境条件等按需提供。程序补齐新建/更新动作、默认标题、来源身份和预期版本，只接受最终实际发送的完整相关记忆ID。来源仅向模型展示本批source_key、角色、可见正文、发生时间、工具名称/结果和partial标记，仍按原档案还原精确引文。显式memory工具的record/update参数结构保持上文合同。

models.json中每个模型的profile可选memory_output：text（默认）、json_schema或function_calling，仅控制后台整理的返回通道。后两种使用现有LangChain/SDK结构化接口；须由人明确确认该模型及网关支持后配置。不根据模型名猜能力，协议构造失败不切换通道重发，超时/SDK既有重试保持。schema请求开销计入输入限额；过小配置会在请求前失败。函数返回只接受单个MemoryResult，作为返回值解析，不执行工具；所有通道继续由项目校验顶层及逐条建议。当前支持的SDK路径和限制见127-07，不代表已通过真实供应商兼容性验收。

等待目标数、实际并发、输入/输出预算、单批建议及总批数均受配置控制。总时限从首次登记开始，包含等待本轮、排队、模型及SDK既有重试；不额外增加项目重试。停止/关闭会等待实际模型或必要提交收尾，已提交成果保留。task_list/task_output/task_stop及现有CLI/Web后台面板可查看/停止，memory任务没有Shell退出码或输出归档；独立purpose/mode=memory的用量与诊断不混入活动根聊天事件。队列是进程内资源，不在重启时重放，保存的概览与记忆持久保留。

新用户轮最多注入3条偏好、8条相关记忆及会话首次8个同目录近期索引，共2000估算token。代码检索及限额，不额外请求模型决定注入；弱查询如“继续”不随机召回相关经验。每项只含短索引、真实版本/来源可用状态及详情工具入口，不自动续期或转正。历史记录不授予权限，当前指令和源码/配置优先。审批/同轮循环使用冻结快照；进程重启的审批恢复不重新全局检索，下一新用户轮重新准备。已知敏感模式的索引省略并记录安全诊断，不保证识别全部秘密。

## 个人库模式与项目备份

用户在02复核后确认个人共享库统一DELETE/FULL，短事务、有界busy等待，不自动切换WAL；这不改变项目会话库既有模式。已有非DELETE个人库返回memory_personal_mode_unavailable，不在运行中的其他使用者旁在线转换。未知格式返回memory_invalid_format且不改写；format2验证专属application_id/user_version及表/触发器结构。模式/格式问题须在全部使用者关闭后单独处理。

项目当前schema17，升级沿用迁移前快照、结构校验及离线整库还原；当前记录、来源、历史版本、检索索引及概览/触发进度一起保存。个人format1升级前，在BEGIN IMMEDIATE保留写权时经独立只读连接生成同目录memory.sqlite.format1-<id>.sqlite一致性私有快照，验证及fsync后才事务升级；已有当前版本成为历史基线，更早不可恢复版本不虚构。项目还原不回滚个人库。现有storage CLI不因此自动支持个人库备份/还原；个人库维护需要关闭全部使用者并使用一致性SQLite快照，不能在WAL/写事务活动时只复制主文件。

04—06两轮共48次真实配置模型调用，第二轮固定预期13/24满足。127-07精简后台合同后，[127-08](specs/127-08-memory-semantic-ab.md)使用新预算完成text/json_schema各12次真实请求：两组格式均12/12，原固定结构/状态预期为11/12和9/12，同候选ID转正均通过。共同e06缺可复现目标，仍保持候选；结构化组另外产生外部偏好候选和例行噪声，并有类型/语言偏差。默认保持text，旧样本回归不证明泛化，自动提取质量仍未整体关闭。累计72逻辑调用，本轮24 HTTP采集完整；历史HTTP次数不另行推定。需要仅使用显式维护时可将auto_enabled=false，实际配置不自动修改。

真实对比还发现Responses默认上限覆盖单次记忆上限，[127-09](specs/127-09-openai-output-limit.md)已在采样结束后修正为SDK规范字段绑定，覆盖记忆及摘要消费者；旧结果保留当时实际16384上限，不能冒充修复后的真实验证。修复后使用SDK/模拟HTTP回归及工程验收，未追加供应商调用。更早失败与边界见[04—06验收记录](specs/127-04-06-memory-completion.md)。
