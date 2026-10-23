# Spec127-01：history只读工具

2026-10-22。用户批准按已说明的01范围实施，完成后说明02，不自动开发02。仅当前工作目录的已保存归档；无模型调用、历史补录/恢复、自动概览/注入、记忆库迁移、全目录正文索引或会话分支。本片不提交/push/发布，不改真实配置/用户库，保留并行文档/发行/学习/worktree修改。

## 1. 当前链路与最小改动

现有SessionStorage拥有归档SQLite/连接锁；chat_history在必要保存时整理真实档案，chat_queries为Web返回含reasoning/body_json的分页，不能直接作为模型输入。本片新增history_queries/history_projection，仅投影human/ai/tool的普通可见文本，复用SessionStorage连接/事务和runtime工具许可。旧Web/API不变。

数据链：当前主/子Agent→history工具校验许可/参数→可信运行上下文的aquery_history→SessionPersistence校验调用者身份/工作区/活动角色→SessionStorage短只读事务→history_queries校验目标/父子归属→可见文本SQL投影→分页/预算→配对ToolMessage→下一正常模型调用。

复用RunServices→RunObservation.bind→invocation_context传递一个已有持久化对象的窄async回调；实际消费者仅history工具，纯内存应用无此能力时明确unavailable。不新增全局服务、应用对象入工具、额外ContextVar或SessionPort抽象。主/子共用回调，仍按各自工具活动集合和本次许可检查。

## 2. 工具接口与来源

工具history，parallel/read_only、return_direct=false、默认allow；服从现有deny/ask和启停。注册到CLI/Web共享builtin入口，不强行修改旧会话工具集合或角色配置。

参数为严格schema，额外字段拒绝；action为list_sessions/search/read。list_sessions只取limit/cursor；search必须完整session_id、非空query，可选返回的agent_id；read必须session_id，可选agent_id，sequence为空时读最近消息页，有sequence时必须revision/conversation_revision并按offset分段。limit默认10、最大50（可配置更小上限），cursor只能用于同动作/调用者/目标/查询/版本。

list_sessions按updated_at降序/session_id升序，分页排除当前调用会话；返回真实ID/title/state/updated_at、overview=null及not_generated、保存覆盖。search/read目标仍可为当前会话。没有标题/概览不编造。列表在每次查询读取当前活动顺序，不宣称跨页全目录不可变快照。

search先选会话，默认根thread；agent_id只接受该session真实登记的子实例且thread/path一致，不允许任意thread/库路径。首期是可见文本字面关键词共同匹配（ASCII不区分大小写），不是记忆语义/FTS重排；单批最多search_batch_messages个消息，命中至多limit，保留继续游标。空命中且有未扫描消息必须has_more=true，不能当作整个会话无答案。

read页按当前选定thread的sequence从新到旧取，返回页内按时间正序；read单条的offset只针对投影后的可见文本，不针对body_json。提供真实message_id/sequence/revision/conversation_revision、类型、父消息/工具call ID、工具名/status和短正文/分段入口。任一版本改变返回history_cursor_stale。追加保留页首次upper，旧页不会混入后续消息；重生成的thread或conversation_revision变化失效。

游标是程序生成的有界结构编码，逐项核对调用会话、目标session/agent、动作/query、上界/当前位置和当前版本；它不是授权令牌，解码后仍重查数据库归属，不能靠匹配ID绕过权限。目录游标绑定当前工作区及调用会话。

## 3. 可见投影、范围与失败

只查kind=message且message_type为human/ai/tool，排除context_summary、system和内部image carrier。content为字符串或数组中的字符串/text块才读取；reasoning/thinking/image/binary、additional_kwargs、artifact、工具参数不进入输出或命中条件。只保留明确白名单元数据。普通正文不宣称已完成任意敏感文本识别；不新增复制凭证配置/原始调用参数的通道。

SQL只提取可见文本与指定字符片段；不把整份body_json读进Python再截断。SQLite内部JSON解析仍有成本，不宣称读取任意巨型JSON为恒定耗时。查询/输出都有上限，命中摘录位于真实命中位置，隐藏字段不能命中。

边界复核补充：游标以固定长度SHA-256查询指纹绑定query，避免长查询放大每页输出；包括空命中页在内，最终整包JSON必须满足字节预算。SQLite原生length/substr/lower对嵌入NUL的处理不满足本工具的字符合同，因此在现有归档连接注册仅用于可见文本的长度、切片和ASCII小写函数；字符串与文本数组都须保留NUL前后全文，按Unicode字符offset精确重构。

调用者来自可信runtime，不来自工具参数；校验其实际会话绑定workspace_root、活动根/子身份和history工具选择。目标workspace必须与调用者相同。来源/目标检查和查询在同一连接的短读事务内；不持锁await模型/网络。目标运行中只读已保存内容，归档缺失/落后显示coverage，不aprepare/恢复另会话；必要保存的现有生命周期保持。

无结果是ok空列表；缺归档如实标注覆盖；目标不存在/范围不符/游标过期/无持久化返回明确工具错误。SQLite失败记录安全error_type并转为配对业务错误，不伪造空结果；许可/机制异常与取消继续按原合同传播。run_blocking沿用有主人的IO排空，不提前关闭资源。

## 4. 配置、文件与验证

新增tools.history预算，启动读取：default_limit=10/max_limit=50、search_batch_messages=200、preview_chars=300、read_chunk_chars=4096、max_output_bytes=16384（至少4096）、max_query_chars=2000。参数越过配置上限返回错误；输出不足时少给条目/缩短正文，游标不能越过未返回项或未读文本。保持现有load_settings的工具预算校验/回退，不修改真实settings或并行全量示例文件。

新增tools/history.py、tools/history_settings.py、application/history_projection.py、application/history_queries.py及三类测试；扩展builtin/settings、RunServices/RunObservation/invocation_context、SessionExecution/SessionPersistence/SessionStorage。十余文件先Spec/TDD，每文件≤500行。增加配置文档独立段，不改公开旧API签名、依赖、版本或现有Web历史格式。

TDD覆盖：真实临时SQLite目录/搜索/分页/大消息分段，字符串/文本数组/隐藏reasoning和artifact；归属/父子/错误与SQL注入，追加稳定/重生成/消息改写游标；空页继续、输出预算/游标不漏项、无DB写入/graph调用；真实create_agent工具调用/许可/禁用/失败后继续与子角色；注册/schema/load_settings。测试使用现有helpers，不改conftest，不用紧sleep。

确定性正常链由固定模型先建立旧会话，再在新会话实际调用list/search/read并验证下一次模型输入；失败链注入SQLite/过期/跨范围，检查错误ToolMessage配对和最终run结果。全后端tests＋研究测试、前端tests/build、compile/pip/500行/diff；按现有build_release和verify_wheel_install做本机标准候选验证，其他原机/真实供应商/业务质量另验。完成后说明02的SQLite/来源/版本/TTL/FTS与memory只读方案，不实现02。

## 5. 已接入主链与本片边界

正常例：旧会话必要保存时已有归档，新会话模型调用history。`create_builtin_tools`注册工厂；`SessionExecution`或`SessionSubagents._subagent_steps`通过`RunObservation.bind`把持久化对象的`aquery_history`随`RunServices`交给`make_invocation_context`。工具execute从框架收到隐藏runtime，公开参数经严格schema规范化，再核对当前batch/call/role许可；模型不能提供回调、来源身份或任意数据库路径。

`SessionPersistence.aquery_history`核对实际调用会话正在运行、workspace和该角色的活动工具；`run_blocking`进入`SessionStorage.read_history_tool`。同一锁及短读事务内，`history_queries.query`读取真实归属，`_scope`校验目标根/子thread，再调用`history_projection.page/message`。SQL只选白名单可见正文及元数据，字符函数保留NUL；结果是有界dict，经工具序列化成JSON字符串，由原工具管线生成原call ID配对的ToolMessage，进入下一正常模型请求。测试断言list/search/read三个配对及5次正常模型调用（含建立旧会话），没有history内部模型调用。

失败例：SQLite读取失败→`SessionStorage`记录安全错误类型并raise固定`history_read_failed`→`SessionPersistence`转为ToolException→原工具管线返回error ToolMessage→固定模型继续并正常结束run。目标范围、旧游标、无持久化均有明确错误；许可机制错误保留原合同，不伪造成功。冷目标读取测试禁止aprepare，并检查未装载目标会话、未改归档元数据；子角色实际搜索旧根会话、根会话读取旧子实例均有独立断言。

新功能27项确定性测试含6项schema参数分支；两次边界红灯记录在`build/spec127-01/red.log`、`child-red.log`，复核新增3项失败在`boundary-red.log`。定向最终27 passed；七份旧工具全集断言仅补history及其parallel/read_only声明，保留全部原名称、顺序和能力检查，不放宽为包含判断、不改conftest/helper。首轮全量38失败中还包括未将fd放入本次PATH；用已有macOS原生构建目录按CI方式准备后，38项全部通过，完整重验另记下节。使用说明见[history工具](../history-tool.md)。

本片不写新DB表、不生成概览、不注入其他会话正文、不实现memory；现有Web历史API保持。history普通正文是历史资料，不是当前规则或环境可用性的证明。纯内存应用明确unavailable。无真实供应商、跨平台原机或业务效果证据。

## 6. 最终验收（2026-10-22）

| 命令/检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -q`，本次进程PATH前置已有`build/release-native-0ff7df8/bin` | **4267 passed in301.05s**，exit0；含本片27项，日志`build/spec127-01/backend-final.log` |
| `npm --prefix frontend test -- --run` | 50文件、**415 passed**，exit0 |
| `npm --prefix frontend run build` | TypeScript/Vite成功、331模块，exit0；保留现有chunk大小提示，无构建错误 |
| `.venv/bin/python -m pytest docs/research/memory00 -q` | **116 passed in0.93s**，研究证据单列，不冒充产品记忆效果 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec127-01/final-wheels` | 标准wheel构建成功，exit0；保留原0.3.1版本，仅私有候选，未覆盖公开包 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec127-01/final-wheels --output build/spec127-01/final-install-smoke.json` | 源码外全新venv标准产品smoke通过，exit0；provider_requests=0 |
| Twine、包内13个本片生产文件逐字节及README的UTF-8内容核对 | 全部通过；最终wheel SHA256 `af4378e8d98ad7df2946d2f0b40d11e313f08a444bcae614c1fecdc8b27c472b` |
| AST/单文件500行/compileall/pip check/diff、当前文档链接 | 通过；本片22个生产/测试文件最大458行 |

最终候选与首轮候选分目录保留；最终标准安装日志`final-install-smoke.log`，汇总`verification.json`位于`build/spec127-01/`。安全复核无高置信发现；架构复核2项已修正并再次通过27项定向验证，无待处理阻断。以上不代表Windows/Linux原机、真实模型历史利用效果或公开发布已验。

唯一下一步：先说明并确认02的SQLite/来源/版本/TTL/FTS和memory只读实现合同，保留00b检索基线。首期精确展示折叠是否自动补位仍是02前的产品选择；本片没有提前固化它，也未启动02。
