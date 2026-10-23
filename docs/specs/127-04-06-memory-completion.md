# Spec127-04/05/06：概览、后台整理与自动上下文收尾

2026-10-22，用户授权合并04/05并完成模块验收。以127主Spec已确认合同为准，本文件明确实际入口与接线；不更换存储/模型/检索路线，不改真实配置、用户数据、依赖、版本或发布。

版本边界：本文件保留04—06及48次真实模型请求的历史证据；后台建议的record/update与proposal嵌套、向模型提供version等格式已由[127-07](127-07-memory-model-contract.md)替换为平铺建议和程序冻结版本。显式memory工具及03写入合同不变，不能用07离线回归覆盖本文件的语义失败记录。

## 范围与外部行为

04：memory.refresh_overview登记，返回accepted/task_id；本轮正常必要保存之后执行。应用自有worker独立调用一个已配置模型，每批一次JSON输出overview与proposals，分别校验/保存。task_list/output/stop和现有CLI/Web后台面板共用只读/停止查询接口；Shell的submit/foreground/输出归档拥有权不变。

05：正常完整根用户轮保存后，新增8轮或20000用户/助手可见字符触发，两个阈值可配置；停止/失败/审批等待不触发。登记/合并成功推进基线，排队满未接收不推进；失败/超限区间不反复补做。新会话从零累计，旧会话首次新增前取档案上界为基线，并保留最近旧完整轮作首次有界输入。新根轮最多3偏好/8相关/首次8其他会话索引，共2000估算token；沿唯一prompt_parts/compose_model_prompt与上下文预览/统计接入，同轮冻结，审批/压缩不重复召回。

06：确定性正常/故障/并发/关闭/恢复组合、固定模型语义样本、四平台构建及本机安装；真实语言模型与其他原机证据单独报告，不以fake回答冒充。无发布授权。

## 接线与数据

主链：SessionExecution准备资源→SessionPersistence记忆准备→MemoryJobs读取可信目录/MemoryAccess→prepare_memory_parts→本轮freeze_run_resources→正式模型装配。显式memory工具→原permit/role/busy范围→MemoryJobs登记当前会话（可显式指定同目录保存会话）→当前根run正常finish必要保存→MemoryJobs.after_finish解锁目标/自动阈值→独立worker→可见档案分段/旧概览/少量相关active及candidate→当前配置模型→JSON顶层校验→概览提交及逐条03写入校验→任务状态/用量/诊断。

失败链：根保存失败不释放本轮显式请求，不创建成功结果；正常保存后普通登记/召回/worker故障有安全诊断但不替换主Agent结果。后台来源改写/CAS/已遗忘来源拒绝相应建议，概览单独验证覆盖/版本，失败如实partial/failed。未处理段按受理上界跳过，后续新增继续；没有必须覆盖的恢复账本。

项目schema16→17，沿标准升级前备份/还原校验，个人format2不变。新增overview表（会话/thread/对话版本、程序分配version、四字段JSON、来源区间、生成时间、独立任务/来源run/批次）及progress表（首次/触发基线、已受理处理位置、已完成reply、首次目录标记）。旧分支概览标stale不注入，来源范围比当前档案新则拒绝；追加不视为重写。history目录/详情返回当前概览和真实保存状态，无模型调用/目标恢复。

worker不持数据库事务等待模型；应用并发1、等待目标32、同会话合并，300秒从首次登记计（含等待/SDK重试）、最多3批。分段保持消息sequence和原文offset，过长单条消息推进区间，不因一条过长永远停留；处理位置及来源信封由代码提供。每条proposal只可引用本批程序提供的source_key和精确短quote，再转成03强类型来源；工具/子报告不变成用户原话。普通敏感文本有界脱敏/省略；无reasoning/system/凭证配置/二进制进入模型。

输出schema：overview={goal,progress,blockers,next_step}整体<=1200chars；proposals最多8条，record/update、workspace/personal、03 proposal（正文<=800chars/别名<=5）及source_key/quote列表。可零条；顶层非法/截断不抢救，条目独立失败允许部分成功；不额外调用格式修复/评分模型。转正/TTL/遗忘/CAS仍用03生产入口，不允许模型提供可信时间或任意状态。

独立memory publisher只连接日志、ModelUsagePersistence/DiagnosticPersistence，不连接活动聊天DeliveredRun。mode/purpose=memory，origin_run_id与task_id/批次关联；既有model_call_started/finished及SDK尝试事件复用，未知用量不填0。查看/停止只委托任务owner，Shell与memory业务执行分开。

审批边界：显式登记遇到根审批等待仍保持待执行，续跑虽然run_id变化，但只在该调用会话本逻辑轮正常保存后释放，首次登记deadline不延长。当前会话对象只保留一份本轮有界memory/session_directory快照，审批续跑和手动压缩复用，下一次新用户轮替换；没有新检索层/跨会话缓存。重启后的审批恢复没有这份进程内快照，首版不重建检索或恢复后台任务，沿已有历史恢复入口继续；下一新用户轮正常准备。保存的概览/记忆不受进程退出影响。

## 配置和验收

MemorySettings增加实际消费的自动/阈值、model_id、输入/输出预算、概览/建议预算、并发/队列/总时限/批数和注入参数；非法模型ID安全禁用记忆模块，不回退调用默认模型。总开关false无读写准备/自动模型；仅auto_enabled=false允许显式整理。实际配置文件不修改，只更新示例文档。

先失败测试再实现；验证含：显式登记返回后才工作、根结束保存失败无后台调用、自动OR阈值/旧会话基线/满队列不丢新材料、分段3批/超时/失败跳过/合并、root事件隔离/停止关闭、03候选晋升/遗忘竞争、跨会话目录及按需历史、同轮冻结/弱查询/预算/预览和个人共享。所有新增组件都必须有真实调用方。迁移夹具和标准安装worker同步准确旧格式，不放宽既有断言。

## 首轮真实模型初验（2026-10-22）

用户授权只读使用`.env/models.json`（该路径中的.env是目录），未复制/修改配置，不读取passwd.md。固定12段×2，模型配置ID为heiyu-gpt-5.5，生产MemoryJobs/runner及临时项目/个人库，初始24逻辑调用上限已用完；无embedding/评分/格式修复请求。24任务completed、24概览generated、24模型开始/结束配对、供应商用量均有记录；HTTP尝试capture unavailable，不能由逻辑次数推出SDK实际尝试次数。

固定预期12/24满足、12/24不满足：分类错误5、明确约定过度candidate2、有限排障经验漏记2、旧候选未同ID补证2、多余候选1。e08临时选择、e09助手猜测、e10外部引文两次均未错误激活；不把这些有限反例扩大为通用质量保证。e12的演示鉴权占位符进入一次多余候选，不是真实凭证泄漏证据；仍按原discard期望计失败。e11缺旧状态的原样本按事前批准有限合成前置补足，报告映射和限制，不隐瞒补充。

原始报告`build/spec127-04-06/semantic-results.json`及逐项只读复核`semantic-review.json`保留全部失败。当前同路径离线复建确认e11整段长查询词项覆盖0.088235<0.5，related候选为空，模型未收到旧ID；原24报告未保存请求payload，不能把复建说成当时抓包。固定语义初验未通过，不能宣称自动记忆质量达标；不改冻结样本/期望、不把同样本修正回归当独立泛化证据。用户随后明确授权修正分类提示及候选上下文，并追加最多24次同样本验证；首轮报告保留，修正及回归独立记录。

## 06四条流程的确定性组合

`tests/test_memory_workflows.py`消费冻结workflows的w01—w04，固定主模型A与整理模型M分开，全部临时库和数据目录，走正式工具/图。w01：首轮短目录→history读取指定会话概览及真实长原文，无概览保持not_generated，另一会话run/checkpoint不变。w02：已保存环境/规则/流程→memory检索读回→Read当前合成Skill/资产/配置→真实Python生成并收集运行unittest产物→读取有不同run_id的本次报告，旧成功及规划不产生本次报告。w03：保留既有代理选择→读当前合成知识入口/源码/配置→本机loopback实际失败→修正当前端点并成功→引用正式失败/成功ToolMessage保存有限experience，原长期选择版本及正文保持。w04两个分支：A实际worker被事件阻塞，B正式工具更正/遗忘；A放行后旧CAS/抑制拒写、概览保留、task如实partial，新搜索核对更正或空结果，遗忘的正文/版本/来源/FTS不复活。root独立运行5 passed in4.47s。

这些固定编排证明正式机制、真实本机IO和错误边界；不是模型自主规划能力、真实Nacos、真实业务系统或其它平台原机质量证据。夹具两次失败分别由新归档合同要求数据目录在执行目录外、临时settings的Path类型不合引起，已只修本文件夹具，生产断言保持。

删除生命周期回归额外确认：memory删除guard在整个异步删除期保留登记占位，request在元数据await前后核对source/target，拒绝删除期间或读后交错登记；失败删除退出guard后允许重新登记。先复现2失败/6通过，再修最小reservation，生命周期35通过。普通目录首发标记失败也不得阻止failed根请求撤销；safe日志降级后不会被未来根轮错误释放。

## 经授权的提取修正与第二轮

用户明确批准再追加最多24逻辑调用，仍为原12段各2次，配置ID/冻结样本/评分保持。只修一般分类/作用域/证据提示及候选上下文：七类定义区分协作风格、规范、决定、环境、流程、经验和入口；环境选择与实际验证分别引证，用户报告不冒充工具验证；当前workspace已有代码绑定，不能因看不到原ID凭空保留范围缺口；不强迫凭空补命令或降激活门禁。

原search与全局FTS/覆盖率/排序不变。候选search不足时只补一页有界最新candidate；新增补位要求完整且全部available来源、同target/session/thread，不跟游标，不拉全库。按scope/id去重，搜索与补位共享candidate count；固定原version读完整正文时逐块重查本地来源，search既有跨会话命中不受新增本地过滤影响。完整request分别经过list/search/read deny，仍由输入总预算删可选相关内容，主来源不被挤掉。e11实际_prepare输入先红后绿，83项专项通过；不把这当成语义质量通过。

第二轮探针还保存safe_report脱敏后的合成model_inputs，让ID是否进入实际请求可直接核对，不记录配置/请求头或供应商异常正文；补观测先红1/13通过→14通过。首轮原始报告不回填这些新字段、不修改结果。第二轮仍只调用一个已配置模型，不增加评分/修复模型。同样本回归属于开发调试证据，不是独立泛化评测。

删除guard最后还加入进入时递增且forget后不清空的generation；登记在metadata await前后核对caller/target。真实SqliteSaver中删除已完成并退出guard后再恢复旧登记的两支先红，修正后原8＋新2独立10通过，最后检查到登记之间没有await。

## 第二轮实际结果与最后预算修正

第二轮24逻辑调用均返回，24概览保存、24调用配对；17任务completed、7任务partial。固定预期13/24满足、11/24不符：7次item缺少嵌套proposal或必填字段，由严格校验拒写；3次保守candidate；1次本应discard的内容成为规则记忆。e11两次实际捕获的model_inputs均含原候选ID/v2，模型输出两次均尝试update原ID，但因外层字段形状非法未提交；不能说已成功转正。七类误分不再是这轮主要失败，但整体13/24仍未通过质量初验。完整回归报告`semantic-regression-results.json`及对照`semantic-regression-summary.json`保留原评分，48次预算已用完，没有继续请求。

之后工程全量发现加长后的提示本身占1060估算token，挤掉1100小预算场景的来源，造成两个原覆盖测试失败；没有改测试预算/断言。将提示压为2643字符，保留七类/作用域/证据/安全及候选合同，并明确item envelope与嵌套proposal，1100预算恢复分段推进；原失败2项＋相关91通过。最后压短版的语义效果没有再调用模型验证：上面13/24是压短前回归，不能当成最终提示已达标；最终工程全量与包字节按压短后源码核验。

## 最终工程验收

最终源码冻结后 `.venv/bin/python build/spec127-04-06/run_backend.py` → **4562 passed in303.69s**，执行前后660份生产/脚本/测试源码哈希未变。较宽专项339/13.87s、七类/候选/预算相关91/2.01s、四流程5/4.47s；`npm --prefix frontend test -- --run` →50文件422通过，`npm --prefix frontend run build` →TS无错误/Vite331模块；研究116及既观察311合法检索顺序＋1可信范围拒绝对照一致，禁止0，不新增泛化声明。此前并行执行目录迁移期全量19失败/4515通过、扩长提示期2失败/4560通过的原日志保留；最终没有失败用例被skip、删除或放宽断言。

四目标沿标准build_release生成`build/spec127-04-06/delivery-wheels`：macos-arm64、linux-x86_64、linux-arm64、windows-x86_64。Twine四包通过，每包205个当前Python源码文件字节一致，README UTF-8元数据一致。标准verify_wheel_install在Mac ARM64源码外全新venv安装并运行现有门禁及新的memory组合，memory=passed、worker1调用/31输入token为离线夹具事实，冷重开0调用/0任务/0供应商；不把夹具计数当真实模型用量。其它三个目标完成构建，未跑本片原机安装。

660份Python AST/500行检查、compileall、pip check、git diff --check通过；示例配置已经真实load_settings核对，无实际配置/个人库改动，无依赖/版本/提交/push/发布。架构/安全独立发现已用红绿回归关闭，最后删除generation独立10通过；先前agent受额度影响的新增workflow验证由root接管完成，不伪称未完成独立复核已做。

实现及工程验收完成；语义质量验收仍未通过，模块不能按完整质量达标关闭。唯一未关闭项是自动提取的输出契约与语义可靠性；两轮48请求已用完，最后压短提示没有进一步真实效果证据。原始报告/比对/验收/范围在build/spec127-04-06，最终记录`verification.json`，源码差异`review-scope.diff`及scope.json注明共享文件仍含并行迁移，不是自动提交范围。没有新增用户独立掌握或最终人类验收证据。
