# Spec143Z：S07.2 固定结果、自主捕获和按需读取

2026-11-02用户授权实施；产物交互按本轮反馈修正：新建时没有可选文件，不要求预填文件名。每轮由运行时提供专用产物目录，Agent决定报告/交接说明名称，结束后自动收集，再展示真实文件列表。本片不做S08本机受控接收/分析草稿/项目应用，不提交或部署，不改真实配置/用户数据。

## 1. 当前事实与参照

现有S07.1提供单文件私有spool、task-scoped prepare/PUT/commit、容器外immutable发布、固定FD下载/TaskIO/删除排空。运行终局以Coordinator已接收必要历史核验，LifecycleRepository.finish提交task＋run receipt。尚无完整结果、代码patch、自动结果保护或Range。

本片借鉴本地固定源码DeepSeek Harness deliverables/workspace-changes git.ts的私有object/index/tree职责，但不采用其git add --ignore-errors或临时diff缓存作为持久交付。沿A GitRepository复用有界进程/stdin/output_stream、禁系统/用户配置和hooks；不复活SnapshotBuilder/ResultExporter/bundle/ref链。输入capture只能复用source_handle/identity/check_links与稳定扫描保护，不能整函数用于O，因为原selection.paths固定且policy_sha绑定完整原selection。

## 2. 产物、代码与证据边界

Runner提供只读task_output_directory工具，返回本轮root_run_id对应的专用相对目录，系统提示指引报告/日志/handoff.md写入该目录。默认根由ResultLimits.output_directory配置，按run隔离；不以文件名猜报告，不把上一轮目录混入本轮。代码O扫描排除整个产物根，报告目录另按实际普通文件递归冻结，不跟随目录链接，不读取任务私有state。

I是已校验输入manifest/selection/archive；O绑定同一input_id和原selection hash，纳入原I条目变更/删除及隔离工作区新文件。manual selection只限制宿主导入文件，O范围明确为本任务隔离workspace（并非宿主源目录）；Git来源按冻结policy/ignore/exclude筛选新增，原I已纳入条目不因ignore丢失。依赖、缓存、.git和已知凭证继续排除；危险类型/越界链接、路径或扫描变化明确失败。Windows不可表达的安全Linux相对路径可在结果中展示并标记，S08交付再拒绝，不静默省略。

稳定冻结先写Runner私有spool，fstat/长度/hash/类型/目录成员复核后fsync并原子发布；私有Git只消费冻结普通文件/链接目标原始字节，hash-object --no-filters批量、私有index的-z index-info、write-tree、diff --binary --full-index --no-renames --no-ext-diff --no-textconv，patch直接写有界spool。禁止git add真实workspace，不读任务Git index/config，不生成commit/bundle。I/O清单差集用于列表；代码patch与报告分开。

available只证明所有必需字节、固定来源/槽位、规范清单和引用完整，未证明业务测试成功，也不单凭独立摘要声称patch应用后等于O。S08在可信I上隔离apply、重算完整O和变化集合后才允许本机交付。本片结果预览清楚标注未应用校验。

## 3. 持久身份、终局和自主推进

Owner Run准入同事务预留root命令及一个result命令（command_id=result_id），固定task/project/input/source_run/source_generation，quota需容纳两条，避免终局才无记录额度。RunCommand携带只读ResultPlan；直接内核测试/调用没有owner结果计划时无发布授权，不新增产品双模式或禁用开关。task.result_ids索引结果，pending_result_id在运行终局核验与原run回执同事务落盘，result_pending阻止空闲释放；仅未开始捕获的fresh pending允许新run同事务supersede，见143ab。

结果源边界是可信run_finished(epoch,seq,status)。recover先于原finished时，使用已核验control_finished/recovery边界，source_run不变、outcome=unconfirmed，不能伪造原completed或产生第二份结果。Runs.finish、Actions.finish/activity、bind/detach/环境收尾保留pending；等待后台静止不占artifact_operation_id，可正常stop。真正只读捕获由固定result/boundary的begin原子预留artifact_operation_id；不许新run/控制/环境同时修改。

自主工作放在已经常驻的Runner publisher：先投递必要历史/终局，后推进结果，UI/Windows无须追加capture。结果计划与Runner journal/spool持久化，重连/Coordinator重启只查询同一result并推进原文件，不调用runs.start。Owner终局尚未确认则等待，不以HTTP或WS ACK替代必要保存。失败轮只在本轮安全静止时读取部分材料，捕获后恢复原blocked状态，不清执行恢复保护。

状态：waiting_terminal → pending → capturing → verifying → available；确定失败为failed并保留pending保护，未知为unknown。O完整发布后只能对账/重传同spool；完整O尚未形成时可用户显式重试同结果、另记attempt和实际captured_at。自动断线重试不能重扫未知现场。明确放弃只允许没有进行中capture的pending/failed，固定CAS，不改业务结论；进行中/unknown先核对，不越过活跃捕获让下一轮写入。

## 4. 复用字节通道与接口

ResultRecord公开result_id/source_run/generation/terminal边界/outcome/phase、manifest文件、patch文件、真实artifacts和受控错误，不提供主机路径。O manifest与patch各一个artifact，报告各一个；不对每个代码改动建立文件对象。

Runner冻结后向task-scoped POST /results/{id}/prepare一次封存文件槽位：artifact_id/role/relative_name/size/SHA及来源全匹配。之后复用artifacts prepare/PUT/commit；_authorize对result:capture走独立严格槽位校验，不能简单放宽operation或跨结果复用文件。所有必需slot available、O清单合法、selection/input/hash/预算核对后才原子commit结果，解除本轮pending/artifact slot；失败不隐式成功。沿现有TaskIO和producers排空，删除沿任务专有artifacts目录及原command记录清理/脱敏，无新的任意路径读入口。

Owner/固定binding代理提供结果分页索引、元数据、变化列表和有界patch/报告文本预览；二进制仅元数据/下载，不渲染HTML。原ID retry/abandon显式动作，权限/CAS/实例边界同既有命令。候选侧栏展示运行结论、结果状态、真实文件差异/产物及按需下载；不出现本机分析/应用占位按钮。

ArtifactDownload扩展单Range＋If-Range强ETag：206精确长度/Content-Range，非法/多段/空文件范围/越界416含bytes */size；If-Range不匹配回200完整字节。完整GET仍核全文件hash；Range只证明固定文件身份/区间，连接器全文件拼接后才验证完整SHA。跨结果file_id拒绝。浏览器下载需认证本机代理，保存到浏览器下载目标；S08本机接收目录另有台账，不混为一个成功状态。

## 5. 实施与验证

内部按可验证单元推进：a frozen O/原始Git patch/本轮产物/DTO；b 持久result计划/终局保护/Runner自主发布/严格复用上传及重试；c Range/有界查询/候选UI/删除与组合验收。本轮授权范围为完整S07.2，内部单元不另算产品分片。

先红灯覆盖新增/修改/删除/二进制/执行位/链接、忽略规则冻结/输入原路径保留/目录或源变化、patch预算/无原仓库写入；终局与新run原子互斥、后台stop、blocked部分结果、恢复边界、spool/journal/上传/commit各ACK丢失、Coordinator重开、本机离线；未登记slot/跨结果引用、错input/manifest/hash、捕获失败后重试/放弃；Range/If-Range/取消/删除竞争。旧协议测试迁移保留原行为断言，并增加结果保护/明确放弃或真实结果完成边界；不靠隐藏禁用开关绕过新能力。若测试fixture只造虚拟I，补可信真实输入与明确Runner输入路径用于新结果，不放宽生产校验。

最终全量后端/前端、默认/候选构建、四目标标准wheel/Twine/源外安装，浏览器桌面/375px真实临时API闭环，独立架构/安全复核、diff与源码漂移核对。真实Docker/Windows/公司环境需各自证据，不冒充已完成。锁层级/生命周期/删除/当前handoff同片同步；唯一下一片S08.1。

## 6. 当前实现主链与读取边界

正常例子：本机绑定请求 → `command_dispatch.execute`冻结原run命令 → `RunnerRuns.reserve`原子预留root/result两条回执 → `RunCommand.result_plan` → `Kernel.accept`将计划写入Runner state/result-plans → Agent用`task_output_directory`取得本轮目录并按普通文件工具写入 → Runner journal必要保存/事件ACK → `RunnerRuns.finish`将原终局、result_pending、回执同事务提交 → 原publisher读固定结果、begin → `freeze_result`发布完整spool → prepare封存精确槽位 → 共用`upload_record`的原始字节通道 → commit检查I/O清单及所有产物available → 原子解除pending。侧栏的观察、预览、下载都不会再运行模型或写入主聊天/记忆。

失败例子：结果prepare/PUT/commit响应丢失 → publisher查询同一result_id；已有完整spool只核对/重传同bundle，commit已成功则不降级。扫描前已持久started_attempt而重启后无完整spool → 本轮保存失败、保留result_failed保护；只有显式retry增加attempt后才允许重新读现场，不能声称还是原停止瞬间。原run经过recover时journal当前状态可能变recovered，已核验failed/interrupted终局seq与来源继续保留，不伪造completed。来源/目录变化、patch/产物/元数据预算失败均不发布部分结果；失败轮可先保存部分代码/报告，但业务失败与文件available分开。

结果记录仍在可信commands表（operation=result:capture），分页SQL在CloudRegistry.result_commands；任务目录/SQLite不会被Coordinator打开。Runner state只由Runner打开；`state/results/{result_id}`是完整冻结文件，task的workspace/state在容器释放后保留。Coordinator的artifacts/artifact-transfers持有独立文件和身份；任务删除沿既有封口/TaskIO排空/固定身份清理/回执脱敏，无第二套回收目录。默认保留结果数量64（最新available＋在途轮换允许一份重叠，非终身轮数上限）、产物30/轮、patch64MiB、O总量256MiB等均由ResultLimits配置；ArtifactLimits的单文件/总空间/总数量更严格时继续生效，额度失败不会静默丢文件。

原I显式包含的安全缓存路径必须进入O；固定缓存排除只过滤新增。产物根与I原路径冲突在run准入明确拒绝。冻结全局ignore、仓库exclude、目录.gitignore按Git优先级重建在私有比较目录，当前工作区修改ignore不能反过来改变原输入policy。安全Linux代码路径保留，结果变化列表标明Windows是否可表达；报告文件沿既有artifact相对路径约束，非法名称明确失败。

实际API均在task范围：owner `GET /api/cloud/v1/tasks/{task_id}/results`及`/{result_id}`、`/changes`、`/files/{file_id}/preview|bytes`；`POST /{result_id}/retry|abandon`固定command_id/result revision。任务侧相同result范围提供GET与begin/prepare/commit/fail，只有artifacts:write当前token及精确槽位可上传。列表/变化offset分页，预览以字节cursor、UTF-8字符边界、有界页返回；二进制仅下载。HTML只显示文本。Range/If-Range沿同一个下载FD，完整GET验证SHA，单片段不冒充全文件校验。

本机`/api/client/v1/cloud-tasks/{binding_id}/results/...`按project/session/retained binding/instance借用连接，浏览器不选择远端URL或提交owner凭证。retry/abandon沿既有本机冻结命令台账，查询原receipt后才显式原ID重发；run:start未知仍不自动重放。浏览器下载走受限Queue(1) producer/DownloadLease，manager关闭先主动取消排空，即使浏览器停在header后也归还连接；未开始就取消也释放额度。关闭下载不停止远端任务。

候选CloudResults只展示真实索引、代码差异和已封存产物；切结果/文件/卸载后旧分页与预览不能串入新结果。下载核对长度和完整SHA再交浏览器保存，不写用户项目。S08.1才创建本机受控结果目录、下载台账和材料引用，S08.2才做patch隔离apply/完整O验证及项目交付。默认正式入口仍待S09切换，不推出A/B双模式产品开关。

## 7. 本机验证收束（2026-11-02）

后端最终两片3271＋3514＝6785 passed，原6757精确测试节点全保留、新增28、0失败/跳过；前端72文件588 passed，原584保留＋4。首轮全量发现application层直接构造结果分页SQL，已移入CloudRegistry，原架构测试未改并全量重跑。UTF-8/原始二进制/执行位、输入原缓存路径、全局ignore、槽位预算、根终局/后台stop、重连/恢复、bundle重试、prepare/PUT/commit ACK丢失、缺spool attempt保护、跨结果拒绝、Range/下载取消、下载背压关机与producer未开始取消、旧结果分页迟到均有确定性回归。原测试fixture仅增加真实I缓存路径/必要结果保存边界，原业务断言保留。

真实本机SDK调用受控模型HTTP夹具：工具取得本轮输出目录、写代码/交接报告，Windows/UI无后续命令时自动available；受控Backend释放及Coordinator重开后固定结果仍能下载。桌面1440和375窄屏真实浏览器经过本机认证代理预览/下载，完整SHA/字节核对、HTML纯文本、主草稿保持、云端只读观察段0写命令和页面0错误。这里不声称真实Docker/Windows/公司业务或真实供应商通过。

默认/候选TS/Vite构建、4目标标准wheel/Twine、Mac源码外新venv安装/产品自检、compileall/diff check通过；每包511生产Python源逐字节一致。独立架构/安全复核已闭合。完整命令/结果在[verification.json](../../build/spec143-s072-20261102/verification.json)、[review.md](../../build/spec143-s072-20261102/review.md)及同目录日志；未提交/push/部署/构建真实镜像，未改真实配置/用户数据，原S06.4改动和Spec142四稿保留。下一片仅S08.1，用户此前确认分析请求先入输入框、点击发送才启动本机Agent。

## 7. 143ab修正后的执行边界

正常续聊与捕获共用CT1/CAS：fresh pending在新run提交成功时转superseded并记录替代run；begin先成功则下一消息等待。失败/重试/unknown不自动抛弃已开始的快照。available之后Runner先持久cleanup proof，清本轮spool再清plan；未知/在途保留，清理失败不挡其他结果发布。Coordinator保留最新和短期接收引用，删除旧结果先deleting，字节删除成功后才deleted及释放额度；墓碑仍保留原执行结论。

I只首次验证/建私有树；O每轮全内容SHA256/blob哈希，已知基线内容不再写副本，变化对象放临时ODB，最终发布文件才fsync。缓存不是结果信任边界，mtime/size不用于跳过O内容；同大小同mtime的变化仍必须被发现。详细成本、局限和本机测量见143ab。
