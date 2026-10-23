# Spec127-02：记忆存储、检索与只读工具

2026-10-22。用户批准02，完成后说明03，不实施03。沿用127/01：Python与既有Agent，不新增模型、Embedding、依赖、后台整理、自动注入、UI页面、发布或实际配置修改。保留01与并行发行/学习/worktree修改。K默认10、最大50，可配置更小；排序后先取K再严格原文折叠，不自动补位，作为本片获批合同。

## 当前链与修改

01已有history→可信RunServices窄回调→SessionPersistence→归档只读事务。02以同一路径增加独立memory_query能力，不合并history与memory的语义。应用拥有MemoryAccess及懒打开个人连接，关闭时先排空Agent操作再关闭个人连接；原项目连接仍由SessionStorage拥有，不新增ContextVar或服务注册平台。

项目当前schema14，顺延15；在现有迁移前快照/失败反馈链里增加记忆表，并同步storage_backup_schema的15结构检查、还原和迁移测试。项目journal模式不变。个人data_dir/personal/memory.sqlite是独立format1，不能复用项目独占文件锁；首次打开用短BEGIN IMMEDIATE防双进程初始化，只有完全空schema且user_version/application_id均为0才视为初始文件；format1设置并核对专属application_id与user_version=1，未知格式拒绝且不改写，失败有安全日志。复核证实混合运行时可相互切模式后，用户明确选择首版个人库统一DELETE/FULL；不自动启用WAL，已有WAL文件明确不可用，不在其他使用者存续时在线切换。此约束不改变项目会话库的既有模式。显式busy_timeout有界，不持事务await。

个人连接只在memory查询需要时打开。没有可信项目UUID/root绑定、无持久化或memory关闭时明确不可用，不能因为普通测试/启动而创建真实个人库。项目和个人分别短读事务，合并前重新读取有贡献的ID，检查当前版本/摘要/状态；不承诺跨库原子快照。

## 本片数据与职责

新建共用格式的naiyue_memory_info、naiyue_memories、naiyue_memory_sources及FTS5表/触发器。当前记录含memory_id/version、scope/workspace_id、七类kind、active/candidate/inactive/forgotten状态、title/body/aliases/tags、machine/module/target/subject条件、basis、created/updated/evidence/last_confirmed时间、采用TTL天数及expires_at。时间存UTC epoch，查询用真实当前时间过滤；不会用当前TTL配置重算历史expires_at。

来源保存真实workspace/session/thread/message及revision/conversation_revision、reply/tool调用、来源角色/时间和有界可见引文区间。当前作用域来源读取时核验档案可见投影、版本、tool_call_id/reply_id，再返回引文；其他项目来源仅给定位及不可用原因，不跨项目查原文。来源不可用不伪造验证成功。记忆正文属于已保存事实描述，不提升为当前规则。

SQLite触发器在同一事务维护当前记录的词项索引/version/digest；forgotten不进索引。本片没有对外写入工具或供模型绕过语义校验的底层写入API，临时测试直接写表作为夹具并验证触发器。历史正文版本、遗忘抑制与写入/转正校验留03，有实际消费者后再创建，不提前堆空表。02的版本合同是当前version及来源版本，旧详情请求不能静默返回新正文。

## memory工具与预算

CLI/Web共享builtin注册，02声明parallel/read_only/default allow；03增加实际写入时同步改may_mutate。继续检查活动角色/批次调用许可和规范化参数。memory参数不接受库路径、workspace_id、调用者身份或SQL。

- list：scope=both/workspace/personal，status默认active，支持显式candidate/inactive/expired/all、kind和四个条件、limit及受控游标。按updated_at降序、scope/ID升序；游标绑定调用会话/项目及查询指纹，不能越过未返回条目，不承诺不可变目录。
- search：非空query，可有最多5个显式expansions；按同一scope/status/条件检索，返回短正文、匹配依据和详情入口。是有界top K，明确truncated，不提供假稳定搜索翻页，需更多时缩小/改写查询或list。
- read：真实memory_id及具体scope，version可指定期望当前版本；offset按正文Unicode字符分段。默认active，显式历史状态仍不可返回forgotten正文。返回当前来源及适用条件；读取不续期、不改变状态。

只增加本片真正消费的memory配置：enabled、default_limit=10/max_limit=50、candidate_limit=100（每库每variant）、field_weights=[1,1,1]、fallback_coverage=0.5、rrf_k=60、max_query_chars=2000/max_query_terms=128、preview_chars=300/read_chunk_chars=4096、max_output_bytes=16384（>=4096）、source_limit=3、busy_timeout_ms=2500。TOML中memory节非法时安全诊断并禁用模块，其他Agent/history可用；不添加04/05尚无消费者的触发/模型字段，TTL配置在03写入时接。关闭不删数据。

## 检索算法（均为代码）

原query与显式扩展分别构造中文二元字组、保留大小写的技术锚点、普通英文casefold词项，再hex编码避免unicode61拆开标识符。超过复杂度上限明确错误，不静默丢技术词。每个variant都要求原query锚点及自己的锚点成立；SQL在scope/status/期限/条件/版本及digest过滤后按本库BM25取有界候选，再以RRF合并库/variant顺序。仅有贡献的ID重查权威值，陈旧贡献不得提分。

对每个候选计算合法variant的完整词段与词项覆盖；存在完整词段匹配时只保留该层，否则覆盖>=1/2回退，保留RRF顺序。先截K，再以scope/workspace/kind/状态/截止时间/四条件/basis/标题/正文的严格原文键折叠，保留重复ID，不合并跨条件/缩进/换行差异、不补位。工具没有二次模型评分；主Agent正常判断是否读详情/来源。算法索引不是适用性、因果或事实正确性保证。

## 正常与失败链、文件、验收

正常：固定模型请求memory.search→工具许可→SessionPersistence校验当前busy/角色/root→MemoryAccess在可信binding下选库→MemoryQueries短事务过滤/排名→重查贡献ID→分页/预算/来源投影→原call ID的ToolMessage→下一正常模型请求。空库返回真实空结果，不造示例记忆。单次IO/FTS错误记录error_type并转明确ToolException，统一工具层配对错误后模型继续；机制错误/取消遵循原合同。不存在目标图prepare/恢复和内部模型调用。

新增memory_settings、application/memory_schema/lexical/queries/access/personal及tools/memory；扩展现有设置、builtin、RunServices/bind/context、根子调用、持久化装配和schema/备份验证。每个模块有当前读取/索引/生命周期消费者，<=500行。03前无实际用户写入，生产新库可为空。

TDD：先缺模块/接口红灯，再迁移备份及快照/还原、个人多连接/忙等待/旧运行时/未知格式/危险路径；状态/期限/条件、来源可见性/跨项目引用、当前版本/旧FTS、中文/技术词/扩展锚点、完整优先/覆盖/RRF、可配置K及去重不补位、预算/分段/游标；正式根/子工具许可/关闭/IO失败后继续、重开零内部模型。配置临时data_dir，绝不访问实际个人库。不改conftest/helper或放宽旧断言；工具全集断言只增加memory及其能力。

完整后端/独立研究、前端tests/build、compile/pip/AST/500行/diff，Deep安全/架构复核；按既有标准构建/安装做本机Mac候选验收。固定离线数据比较生产候选与00b检索，不把已观察样本当新独立泛化。无真实供应商/业务质量/其他原机或公开发布证据。收尾同步主Spec及handoff/plan/todo自己的段落，并只说明03方案。

## 实际主链与验证（2026-10-22）

正常入口：`create_builtin_tools`注册memory；持久化装配创建无IO的`MemoryAccess`并随`SessionPersistence`拥有。根/子`RunObservation.bind`→RunServices.memory_query→make_invocation_context.aquery_memory。工具规范化MemoryInput并核对batch/call/role/参数许可；`SessionPersistence.aquery_memory`核对当前busy、角色选择及root后，run_blocking进入`MemoryAccess.query`。它从真实绑定取得UUID/root，选择当前项目连接或懒打开个人format1连接，短事务查询`memory_queries`，权威版本/digest重验后经`memory_results`做有界正文/来源投影，再返回原call ID配对ToolMessage。正式图测试实际执行list→search→read、重开及子角色，分别检查结果、下一正常模型输入、配对和模型次数；没有memory内部模型调用。

来源使用history可见投影核验role、revision、conversation_revision、tool_call_id、reply_id及引文范围；跨项目个人来源只定位、不取引文。SQLite/FTS故障→安全错误类型日志→MemoryReadError→ToolException→配对error ToolMessage→Agent下一正常调用并结束；格式/模式分别反馈memory_invalid_format、memory_personal_mode_unavailable。无持久化、未绑定、关闭或未选用均不能创建个人文件。应用先排空操作、关闭个人连接，finally仍尝试释放项目存储；主异常不被后续关闭异常覆盖。

项目14→15沿原备份/迁移/快照及还原。测试基础设施改动仅为准确模拟旧格式：共享legacy_store_schema删除新增记忆结构，storage_backup_fixture默认使用CURRENT_SCHEMA；固定历史阶段不改，当前目标用CURRENT_SCHEMA，覆盖扩展到14→15及15快照。安装worker的临时降级夹具同步；不删/skip断言，不改conftest或放宽异常/保存/恢复检查。

TDD证据`build/spec127-02/`：red.log为初始缺模块；cleanup-red.log证明关闭失败会跳过项目释放；review-red.log含未知view/header初始化、混合模式和来源关联6项失败；lifecycle-red.log证明all查询误合生命周期；personal-feedback-red.log证明安全错误码被泛化。对应源代码修正后本片**43项**通过，日志memory-final.log。架构2项、安全1项发现均已复核关闭；用户明确选择统一DELETE/FULL，当前无待处理阻断。

| 验证命令/检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests -q`，本次进程PATH前置已有原生工具`build/release-native-0ff7df8/bin` | **4314 passed in300.74s**，exit0；43项专项包含其中，日志backend-final.log |
| `npm --prefix frontend test -- --run` | **50文件、415 passed**，exit0，frontend-tests.log |
| `npm --prefix frontend run build` | TypeScript/Vite成功、331模块，exit0；原chunk大小提示保留，frontend-build.log |
| `.venv/bin/python -m pytest docs/research/memory00 -q` | **116 passed in1.51s**，研究证据单列 |
| `.venv/bin/python build/spec127-02/compare_retrieval.py` | 三批已观察312项全部一致：311合法查询按00b＋取K后严格折叠排序一致，1缺可信上下文拒绝一致；禁止0，不是新独立泛化证据 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec127-02/wheels`、Twine及包内源码/README核对 | 标准构建与检查通过；本片20个生产文件逐字节一致，README的UTF-8内容一致；保留0.3.1版本，仅私有候选 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec127-02/wheels --output build/spec127-02/install-smoke.json` | 源码外全新venv标准安装通过，storage_roundtrip=passed，provider_requests=0 |
| AST/500行/compileall/pip check/diff与当前文档链接 | 通过；本片51个生产/测试/验收文件最大458行 |

首轮全量16失败/4289通过，均为未同步的旧格式夹具或当前schema断言；保留原行为断言并同步后16项复验通过，最终全量结果如上。包与安装报告、完整逐query对照、structure/verification.json均在build/spec127-02。本片未改实际配置/用户库/依赖/版本、未提交/push/发布，保留01及并行文档/发行/学习/worktree工作。测试与本机安装不等于真实模型记忆利用效果或用户独立掌握。

唯一下一步：说明03显式record/update/disable/forget、可信来源提交、CAS、候选转正及遗忘抑制方案；03—06尚未实施。03实际扩展写入时再接TTL策略、历史版本/抑制表和对应个人库升级/一致性备份，不以只读阶段冒充完整记忆系统。
