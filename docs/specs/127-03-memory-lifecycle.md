# Spec127-03：显式记忆写入与生命周期

2026-10-22，已接线，最终验收见文末。承接127主Spec及02。仅实现主Agent/已获工具许可的子Agent显式record/update/disable/forget；04后台模型整理、05自动触发/注入未接入。不增加模型、依赖、UI、公开维护端点。

## 当前链与接口

现有：工具permit→SessionPersistence.aquery_memory→MemoryAccess.query→当前工作目录/个人库→只读投影。扩展同一入口；工具改parallel/may_mutate、默认allow，角色/permit/deny继续生效。依赖前次ID/version的修改须后续调用。

保留现有扁平公共读参数，增加严格proposal与sources结构。action判别校验禁止读写字段混用；受现有build_tool_schemas(BaseModel)约束，采用同一BaseModel的操作校验而不改通用工具注册层。record要求具体scope、proposal、来源；update另需memory_id/expected_version，整条建议替换；disable/forget需ID/expected_version、来源及reason。禁止模型提供版本、来源角色/时间、TTL或任意存储状态。read额外include_history仅用于显式查看版本历史，默认不变。

proposal：kind、title（UTF8≤480字节）、body（≤3200字节）、aliases/tags（各≤8项）、machine/module/target/subject、basis、intent(candidate/activate)、gaps、reason、可选明确来源截止时间。subject必填，标明具体事实的归属；不是自动同义词聚类。source：current_user或具体session/thread/message/revision/conversation_revision、quote≤300字符/offset。原文/身份由代码核对，不接受模型填role/evidence_at。每次≤8来源。

正常：用户“以后此项目pytest用.venv”→模型memory(record)引用current_user→保存正式checkpoint的可见档案→可信范围/原文校验→模型建议语义+代码门禁→短事务写当前记录、来源、版本历史、FTS→返回saved及真实ID/version。记录仍可能为candidate，返回still_candidate和gaps。

失败：必要档案保存失败→memory工具error，未写记忆，Agent可继续；旧版本并发提交→memory_conflict，不覆盖更新；遗忘后旧来源重提→memory_source_suppressed。业务故障走已有ToolException配对，许可/机制异常保持原边界。日志只记录错误类型/代码。

## 来源与生效门禁

写入前应用从当前正式图收集/保存可见档案，不恢复/运行目标会话；同会话历史锁串行化。图绑定发生在既有应用装配后。schema16给可见消息档案新增occurred_at，取首次包含该内容的官方checkpoint.created_at，旧行缺失不使用本次整理时间补填；未知时间拒绝该来源，用户可重新确认。当前用户快捷来源只选调用会话当前根thread真实human_input，子任务文本不充当用户原话。历史引用限本工作目录，逐一校验会话/分支/消息版本、可见原文区间及时间；排除system/摘要/图像载体。工具观察保留真实tool角色及成功状态。

首次activate：user_declaration必须有根用户原话；tool_observation必须有成功工具直接输出并有明确target；inference仅candidate。激活要求无gaps、reason非空；其他条件语义由当前模型提出，代码不能证明语义。识别出的同scope/kind/subject/条件不同正文active冲突降candidate并说明缺口；完全相同已有记录返回duplicate。敏感内容拒绝已知密钥/鉴权/密码模式和凭证URL；不声称穷尽所有秘密或验证自然语言蕴含。

update同ID CAS，kind固定，不能靠换类型绕TTL。补证/转正复用门禁，新旧来源合并；只有真实新来源身份且时间晚于原evidence_at才按该时间重新计算期限；仅改写/重提不续期。旧候选需新来源才能晋升，到期active也需新证据才能恢复。旧证据不因新增一条无关更新而变新，语义有效性仍依赖模型。disable保留历史；forget清当前正文/标题/别名/标签/条件/reason/gaps、所有版本正文/引用、来源与FTS，只留ID/version/scope/kind/时刻及旧来源身份哈希抑制。原会话及既有备份不删除；抑制按消息版本，可能同时抑制同条消息其他建议，不是语义匹配，也不承诺物理安全擦除。

## TTL与存储升级

MemorySettings增加candidate_ttl_days（七类分别默认30）、active_ttl_days（context30/procedure180/experience365/reference90）。user/rule/decision不接受类型TTL。明确来源deadline与类型TTL取早；保存ttl_days/expires_at，读取不改写，配置变更不追溯。

项目schema15→16：既有迁移前标准备份、短事务新增occurred_at、记忆gaps/reason/deadline_at、版本表、来源抑制表及format2；旧schema15备份仍按format1验证。个人format1→2：DELETE/FULL不变，BEGIN IMMEDIATE保留写权，独立只读连接backup到独占创建的同目录私有备份，校验旧格式/完整性并fsync后才DDL升级；失败回滚保留旧库与备份。升级把现有非forgotten当前版本流式保存为历史基线，不伪造更早版本。新库直接format2，不制造迁移备份。旧程序format1校验会拒绝新库。用户库/真实配置不访问。

每次修改单库短事务；项目来源锁持有到目标提交，防止本应用来源版本变化；个人库也使用BEGIN IMMEDIATE/CAS，跨进程busy有界。没有跨库全有或全无；一次显式调用只有一个scope。版本history有界返回元信息和短正文，不返回失效来源引文；遗忘无历史正文可读。所有SQL标识符来自固定程序字段映射，值参数化。

## 验收

TDD：临时真实SQLite，候选/首次active、补证/并发CAS、TTL不续与新证据、来源改写/跨目录/子任务不冒充、敏感拒绝、冲突、停用/遗忘及旧来源抑制、故障回滚、个人双连接竞争/格式备份升级、正式create_agent配对继续和重开保存。同步旧schema fixtures/标准安装探针，使历史文件不冒充新格式。全量pytest、前端test/build、研究实验、标准MacARM64候选与隔离安装；其他平台/真实模型质量不在本片证明范围。

当前read投影gaps/reason/deadline_at；显式历史最多5条并用第6条检测截断，预算不足先减少历史，必要元数据仍放不下则明确报错。写回执在提交事务内有界裁剪并标truncated，不丢实际正文。

## 当前实际主链与证据边界

1. `tools/memory.py::execute`从真实ToolRuntime取得session/root/role，核验原permit与标准化完整参数；模型参数不能换workspace或提供可信时间/角色。
2. `SessionPersistence.aquery_memory`复核busy、工作目录及角色工具集合；写动作在该会话历史锁内，`collect_chat_updates`→`SessionStorage.save_chat_updates`保存正式checkpoint档案。graph只在既有应用装配后`bind_memory_agent`绑定，工具不会自行运行/restore图。
3. `MemoryAccess.query`检查可信workspace绑定与调用会话；`_mutate`保持项目来源锁并在目标库BEGIN IMMEDIATE。个人操作保持项目读快照＋个人写事务，不跨库写入记忆。
4. `memory_sources.resolve`从档案可见投影核验具体quote/version/原生角色/发生时间；历史非当前根thread必须对应已登记的规范子实例。`memory_mutations.commit`先CAS，再核对来源抑制/敏感/转正条件/已知冲突，计算实际TTL与明确deadline。
5. `check_metadata`在新建/更新提交前验证最小read详情可装入当前字节预算，过大报memory_metadata_budget并回滚；`_save`以固定字段/绑定值保存当前行、快照和来源，原事务触发器更新FTS；`fit_mutation`在同事务内裁短成功回执，退出事务才返回。
6. 失败链由必要源保存、来源/版本/预算等业务失败返回原配对ToolMessage(error)；模型可继续。正式图测试分别检查tool_call_id、status、后续最终AI回复、模型调用次数及DB没有误写，不把工具反馈等同成功。取消/权限机制边界不改原执行器。

关键确定性入口：`test_memory_lifecycle.py`（8项），`test_memory_lifecycle_storage.py`（33项），`test_memory_write_runtime.py`（2项），`test_memory_lifecycle_readback.py`（3项）。关键行为已观察预期红灯后转绿；存储/并发/策略补充测试与全部回归共同验证。本片新增46项，既有四组迁移矩阵随schema16扩展4项；旧测试只同步当前may_mutate/版本合同与历史schema结构，不放宽原行为。测试夹具增加remove_memory_lifecycle是为了将schema16准确还原为15及更早，标准源码外安装的schema12探针同样移除occurred_at并断言升级为未知时间，不伪造来源时刻。

独立架构/安全复核的敏感转义、回执预算、旧版本历史基线、候选读回和最小详情预算问题，均经确定性回归后复核关闭。已有当前库若在后续人为调小输出预算，仍可能需要恢复原预算才能读取长元数据；不追溯裁改保存数据。来源角色/原文匹配不证明语义；敏感筛查只覆盖已知形状，正式模型效果留后续固定样本验收。

## 最终本机验收

- 后端：`.venv/bin/python build/spec127-03/run_backend.py` → **4364 passed in 311.87s**；脚本仅为本机验证进程临时加入已有build/release-native-0ff7df8/bin，不改系统或项目配置。本片memory专项89通过，其中新增46项。
- `npm --prefix frontend test -- --run` → 50文件415通过；`npm --prefix frontend run build` → TypeScript/Vite通过，331模块；保留既有非阻断的包体大小提示。
- `.venv/bin/python -m pytest docs/research/memory00 -q` → 116通过。冻结对照312场景为311合法顺序一致＋1可信范围拒绝一致，禁止结果0；这是已观察样本，不是新泛化证明。
- `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec127-03/final-wheels`、Twine、17个本片生产文件字节与README UTF8比对、源码外全新venv实际安装烟测通过；供应商0。wheel SHA256 `f5270570f64a0811bca3803a5aead35e2668ceb8d4b12c48331ab12e0a5fd266`。首次重建因旧输出已存在被标准构建脚本拒绝，改用全新final-wheels目录后完成；没有覆盖旧候选。
- AST/≤500行、compileall、pip check、git diff --check通过。独立架构/安全复核发现全部关闭。收尾新增读回及元数据门禁后，两个在途旧快照测试主动中止，不计通过；以上最终全量和最终wheel均使用冻结源码。

原始证据在build/spec127-03：verification.json、scope.json、review-scope.diff、backend-final.log、memory-final.log、frontend-tests.log、frontend-build.log、research.log、retrieval-comparison.json、wheel-source.json、install-smoke-final.json。单元/正式图fake模型证据与真实语言模型质量分开；本片没有模型供应商调用、其他原机或业务质量通过声明。未修改真实配置/用户库/依赖/版本，无提交/push/发布，保留01/02及并行修改。用户独立掌握证据未新增；唯一下一步说明04显式后台概览＋建议双输出及查看/停止/用途观测方案，未实施04。
