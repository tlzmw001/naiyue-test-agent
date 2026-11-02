# Spec143M：S04.1 固定任务Runner、必要历史与控制

日期2026-11-01。用户“继续”授权按S03.1收尾方案实施；总体143a/143f、拆分线143i、输入143l。只做单根运行/必要保存与投递/主动控制/固定身份，不提前完整根子审批、后台命令与恢复（S04.2），不注入供应商根密钥或提前实现模型转发（S05），不切A正式入口（S09）。无提交/push/发布/公司部署。

## 1. 当前源码与参考

当前主链`SessionService → bind_persistence → assemble_runtime → create_agent`、`RunLifecycle.arun → SessionPersistence.afinish → RunUnitOfWork.finish_run`具备固定task装配边界；模型结果返回前仍等待finally必要保存。`open_storage`拥有ProjectStore及独立AsyncSqliteSaver，checkpoint与业务事务不同连接，不能冒称跨连接原子。Coordinator已有独立历史台账与seq/digest幂等ACK，不能打开Runner DB替代投递。

Pi本机a69bef789的`agent-session-services.ts:createAgentSessionServices → createAgentSessionFromServices → sdk.createAgentSession`先固定cwd与资源，再构造AgentSession；`agent-session-runtime.ts:createAgentSessionRuntime`包装已有会话/服务。AgentX本机3fcfa0e的`AbstractMessageHandler.processChat → onCompleteResponse → MessageDomainService.saveMessageAndUpdateContext → sendEndMessage`把完成回调与保存/投递区分；该方法源码不能证明其双仓库跨写事务，不照搬其Java/LangChain4j API。借鉴服务/会话/运行职责，项目继续Python＋create_agent，不新建另一套循环。

已核对本机LangChain1.4.2/LangGraph1.2.12安装源码：本项目`agent_runtime._assemble_interactive_runtime`向create_agent传checkpointer和已有middleware，当前`durability=sync`由RunLifecycle调用。不会在Runner恢复时为每个消息重建整个全局Application/个人记忆服务。

## 2. 本片产品边界

Runner始终是受信镜像中的固定入口、任务容器的前台工作进程，不由Windows逐工具调度。Coordinator仍拥有容器/命令/所有者身份，不创建Agent图。Task拥有独立state/DB/checkpoint/outbox，执行根固定/workspace/project；普通本机继续现有host装配，A有消费者的代码暂不删。

S05前产品未配置模型时Runner如实报告waiting_model，run拒绝model_unavailable；不制造假回复/假数据。确定性模型仅由测试传入真实Runner内核，端到端使用独立受信测试镜像的根属启动器注入，产品wheel没有scripted/fake模式或任意模块加载开关。测试必须实际走现有Agent循环和Host工具，不能把SDK写文件当Agent执行。

生产固定启动脚本由独立模板构建从wheel复制到只读/opt/nyt/runner-entry.py，OpenSandbox显式启动该脚本。先等待S03唯一安装器生成的结构化安装回执，再启动Runner；不同时启动两个安装器。S03同一安装模块/回执沿用，初次安装和已有workspace的重建语义不变。先实测OpenSandbox前台工作进程退出/kill是否终止容器及后代；失败要修正本片Spec再实现，不用日志约定行推断。

## 3. 固定装配与必要提交

Runner显式校验bootstrap/task/project/input/release/generation/boot_id及私有state marker，固定执行根authority；marker/已确认DB丢失、身份错配不重建空状态冒充恢复。Coordinator在bootstrap发布前持久预留boot/state_epoch，首次握手核对该预留；state_epoch稳定跨容器代际。Runner只打开本任务DB；共享记忆/MCP/子Agent首片不装配，工具注册范围显式选定，现有权限/文件进程授权保留，不因容器隔离静默绕过审批。权限由显式CloudSettings.runner_permissions送入只读bootstrap，默认仍为现有Default，不自动开放Bash/write。

复用open_storage、SessionService、SessionPersistence和HostFileSystem/HostProcesses。只在真实双消费者位置抽出绑定Persistence/装配图的公共组合步骤；不复制整个host_assembly，不添加空插件注册层。SessionService当前接近500行，本片不往其中堆云端状态。

必要历史放在`RunUnitOfWork.finish_run`的同一SQLite事务：保存聊天/回复边界/会话状态时，同时写RunnerJournal事件与本次命令终局。采用storage层明确的可选journal对象；本机未绑定时原语义不变。直接在调用返回后另写outbox有丢失窗口，普通observer会吞错，均不采用。新SQL只在storage，业务层不构造SQL；checkpoint独立连接仍按原安全完成检查。

RunnerJournal在副作用前持久接收command_id/request_digest/run_id；重复ID同摘要查原状态，不重跑工具，不同摘要拒绝。进程崩溃留下accepted/running时视为unknown，不自动重放图或命令。已完成但ACK丢失可重发原历史及终局，不重新执行。

history_record保存受限的消息更新与run_finished结构化记录，带run/command/来源generation；epoch/seq在本任务DB中持久增长。collect_chat_updates的messages为完整视图，Journal在UOW同一事务、chat写入前读取旧body_hash/parent_message_id，采用现有归档的判等，只投递新增/改变记录；两者任一失败一并回滚，不能先独立提交Journal。单事件/单batch不得超出S01帧预算；超限导致必要保存失败，不丢字段/截断后宣称完整。usage/diagnostic仍为可降级观察，不升级为全部必要历史。outbox ACK后持久推进游标，断线补发保留原seq/digest。

## 4. 控制与完成事实

Runner主动WebSocket连接任务侧固定路由`/internal/cloud/v1/tasks/{id}/control`，鉴权在accept前；令牌只在Header/只读bootstrap，不能放query/日志。新增control:connect scope，仍绑定task/project/current generation；每个帧重新核对当前身份/连接fence。新连接替换旧连接，boot_id/state_epoch/连接ID不匹配及旧代晚到拒绝。JSON大小/深度/重复键按S01边界验证，发送按每连接异步门串行，不跨网络持CT1/DB。

同task并发attach在任何DB await前由owner-loop独占producer预留，第二个并发请求返回runner_connect_busy，后续重试可替换连接；调用方取消不释放仍在工作的producer。新run不能仅凭持久connection_id准入，必须与本进程当前连接及有效凭证相符。取得发送门后紧邻send再次认证/核对fence，Runner接收command时再核对自身有效期；最终Dispatch含run/fence的字节预算在持久占用前检查，统一发送处再防守。

bootstrap由Coordinator在tasks/<id>/control/<generation>创建，单独RO挂/run/nyt，文件私有；只包含任务令牌和配置，不含owner/OpenSandbox/模型根密钥。注册表只存token hash。现有TaskCredentials.scope显式扩展，代际推进继续吊销旧凭证。在线续期在有效期内通过已鉴权通道申请，更新bootstrap后回传；过期/断线不绕过认证。长期离线后的完整恢复随S04.2，当前可由受信环境refresh重新预备，不接受过期凭证自行延长权限。

owner新增`POST tasks/{id}/runs`：command_id、expected_revision/generation、message、model_id。按原回执优先/CAS/单根占用/限额准入；run命令与active_run/reclaim blocker同事务，发送前持久dispatched。HTTP取消不取消已接受producer。先返回接受事实，执行状态来自持久回执和历史；无模型/未连接/未ready不得进入运行。

Runner协议至少hello、command、accepted、query/state、finished、renew；均严格DTO。Coordinator对账只查原命令，不能因没ACK重新换ID发start。未知执行状态继续占用并阻止空闲/新run；真正静止从Runner必要保存/无待发历史的边界确认。停止/审批完整行为在S04.2，首片不注册占位审批接口。

完成顺序：Runner必要保存/outbox同事务完成→HTTP任务事件投递→Coordinator事务ACK→Runner持久ACK→发送finished→Coordinator核对已持久终局事件/epoch/seq/command/run/generation并完成owner回执，才清active_run和阻碍、记录静止时间。仅模型返回、WebSocket ACK、日志行或任务自称已发历史均不能独立标success。投递失败只重试投递，不重放已执行工具；必要保存失败保持unknown/阻塞。

## 5. 状态、退出及关闭

Task增加必要的runner_phase/boot/连接/state_epoch字段；普通S03环境backend_ready保留，只有受控Runner完成装配并握手、后端及输入就绪时task.lifecycle才ready。产品无模型时waiting_model，不误标Agent可运行。

Runner前台异常写结构化退出回执（task/gen/boot/稳定错误码，绝不写凭证/原异常正文）。SIGKILL可能没有回执，后端终止事实与控制断线分别记录；缺回执仍unknown，不猜执行成功。验证前台退出确实收束容器后代。首次root运行不支持自动恢复未知副作用；后续S04.2提供显式恢复/停止/审批。既有生命周期保护扩展active run、required_history、unknown；不能因WebSocket断线释放额度或换代重跑。

即使断线时没有active_run，detach也清除last_quiescent_at并添加runner_disconnected，不能按旧静止时点自动回收；新有效握手只能移除这一连接阻碍，不清除required_history/unknown。明确的完成记录仍须通过必要历史核对，不能以一次心跳清空执行阻碍。

关闭依次封口owner准入、断开/排空控制管理、排空inputs/lifecycle生产者与本地worker、关闭SDK/DB/根锁；Runner独立运行，普通Windows/UI断线不等于取消任务。每个资源失败均可观察，不覆盖原始运行/保存主异常。新增锁/发送门/预留及SQL事务顺序同步锁表。

## 6. 文件与验证

新增contracts/cloud_runner.py；cloud_runner装配/内核/连接/entry/bootstrap按职责分文件；storage/runner_journal.py；application/cloud/runner_control.py、runner_runs.py及对应storage/cloud模块；service/cloud控制/运行路由。扩展RunUnitOfWork、必要公共装配组合、现有cloud settings/task/credentials/lifecycle/template入口。所有新文件≤500行，不新依赖、不改真实.env，不为旧测试保留产品旁路。

先TDD：固定任务根与DB重开、工具循环/顺序/消息配对、同ID及摘要冲突、必要保存与outbox原子失败、ACK丢失不重跑、Coor停止仍可查历史、断线/旧代/抢连接/过期、结构化退出缺失、同名模块不能劫持、实际工具/Kit解释器及无根凭证。CLI/ASGI/真实SQLite＋确定性模型不是生产模型替身模式。必要异常在测试末显式断言，不能让observer吞掉检查。

使用唯一标记的本机专用Linux后端，先验证PID/后代门禁，再用最终源码wheel/镜像跑真实Runner进程与控制长连接、write→Bash→reply→保存→历史ACK、二轮复用、ACK/断线和kill未知边界。Windows/公司网络/供应商不由本机结果覆盖。安全/架构复核后完整后端两分片、前端、标准构建/wheel/Twine/源外安装。收束本轮资源并恢复VM停止，更新handoff后只讲S07.1，不自动跨片。

## 7. 实际接线与当前数据流

| 生产者/入口 | 数据与消费者 | 提交/失败边界 |
| --- | --- | --- |
| CloudLifecycle.start | TaskDirectories三数据挂载＋TaskBootstrap只读/run/nyt；Backend显式-I固定入口 | boot/epoch先CAS预留再原子发布；安装仍只有S03安装器，Runner等待结构化installed回执 |
| cloud_runner.entry.serve → open_runner | 固定根authority、private marker/DB、borrowed catalog → SessionService与共享assemble_session_runtime | 无catalog只waiting_model；不启动Application/ProjectHost/MemoryJobs；缺失已确认DB拒绝 |
| owner POST /runs → RunnerRuns.start/reserve | CAS/原ID/真实连接/有效token/最终帧预算 → 同事务active_run与dispatched回执 | HTTP取消不取消producer；原ID查询先于新准入，不因丢ACK换ID重跑 |
| RunnerClient.receiver → RunnerKernel.accept/_run | RunCommand → RunnerJournal.admit → 现有SessionService.arun/create_agent工具循环 | command先持久再副作用；断线不取消active_task；未完成重开unknown |
| SessionPersistence.afinish → RunUnitOfWork.finish_run | checkpoint安全检查、chat候选 → Journal差分/outbox＋原chat/会话/回复边界事务 | 任一必要保存失败回滚；主异常cause保留。saver连接独立，不声称跨连接原子 |
| RunnerClient.publisher → task /events | 原epoch/seq事件 → Coordinator自有CloudHistory事务ACK → Runner持久ACK | 丢ACK只补发同摘要事件；不重跑模型/工具；pending未清不发finished |
| task WS finished → RunnerRuns.finish | 对照Coordinator已确认的epoch/seq/run/command/source_generation终局 → owner回执/静止边界 | 缺历史/非静止/身份错配拒绝；unknown继续阻碍新run与回收 |

失败例：write已执行，必要history写入失败 → UOW回滚会话完成及outbox → RunnerJournal原命令unknown → Coordinator不收success。另一例：历史事务已提交但HTTP ACK丢失 → 原事件重发匹配digest → 原seq ACK → finished对账；文件工具只执行一次。

删除/替代项：Host原bind_persistence＋assemble_runtime组合由双消费者共享的assembly函数替代；Backend原sleep入口由固定Runner替代；HTTP局部JSON解析器由HTTP/WS/私有bootstrap共用有界解析替代。A正式消费者仍真实存在，按S09统一切换/删除，不保留第二个B runtime或产品假模型入口。未改依赖版本、真实.env/模型配置、A envd指纹源码及用户数据。

## 8. 下一片S07.1的讨论边界

目标是一个可验证的薄闭环：Windows/本地CLI导入I → 创建/启动task → 发单根任务 → 原ID查状态/历史 → 明确产物由Runner上传、Coordinator提交并认证下载。先只取一份明确文件，例如测试report.json；完整I→O代码差分、冲突交付、文件集合及Range留S07.2，S05前仍使用受信测试注入验证，产品如实提示无模型。

准备时先沿现有transfer的staging/流式IO/fsync/hash/持久commit及A结果交付的必要保存/unknown合同各追一条真实链，再定向核对OpenSandbox文件流和参考项目产物入口。复用可验证的传输/回执机制，不让Coordinator执行任务路径、不打开Runner DB，不为拿一份产物重建Git快照/bundle链。

需要在S07.1 Spec写清：产物只允许固定task范围的相对路径/文件类型/大小；任务凭证单独追加产物用途并绑定task/gen；流式原字节上传、长度/hash核对、私有staging和原子提交；提交前不提供下载URL；同ID重试与断线结果不重复执行；所有者下载鉴权及原子本地落盘。CLI只是同一HTTP/命令接口的消费者，不新增本机Agent循环。失败验证覆盖错hash、越界/符号链接、半上传、任务/代际串用及重复提交。


## 9. 本机实施与验收（2026-11-01）

本片已完成本机验收，无提交/push/发布/公司部署。实际证据见[verification.json](../../build/spec143-s041-20261101/verification.json)，源码/轮子与最终测试启动时冻结哈希一致；429个Python文件四目标逐字核对，最大修改文件305行。新增12个生产模块、4个测试文件；17个既有生产文件及1个测试文件有改动，旧6355项回归保留。

专项命令：

```bash
.venv/bin/python -m pytest -q tests/cloud tests/test_storage_dependency_direction.py tests/test_session_lifecycle_integration.py
```

实际结果：**237 passed**。完整CI两分片：**3284 + 3092 = 6376 passed**；本轮新增21项，收集清单与两分片JUnit按测试身份核对无缺失/重复/失败/skip。前端69文件568 passed，TypeScript/Vite通过（既有chunk-size提示）；标准四平台wheel/Twine、Mac源外安装、pip check、compileall、git diff --check通过。Windows/x64仅交叉构建，不视为原机Runner验收。

真实后端为专用Colima Linux ARM64/Docker29.5.2/OpenSandbox1.2.0，任务2CPU/2GiB、非root1000:1000，独立Runner/Test环境及Kit0.6.0。最终镜像的确定性模型只覆盖根属测试启动器，产品wheel没有fake入口；正式模板无模型报告waiting_model且拒绝run。实际write→Bash（测试解释器）→reply→必要保存→历史ACK、第二轮复用、同ID不重放、同名workspace模块不劫持、管理服务即时重启自动重连和历史保留均通过。两轮只有10个必要事件（首轮6消息+终局，次轮2新消息+终局），不全量重传旧消息。

641B小输入/热镜像单样本，从环境start请求到backend＋input＋Runner ready约2.52s；收到接受回执后确定性首轮完成约0.65s。不是p95、公司Windows网络、真实模型或大输入性能承诺。0真实供应商/业务请求。

故障门禁：HTTP历史ACK丢失与WS断线只重发原事件，模型/工具不重跑；journal/后续chat写入失败均整体回滚且保留cause、unknown阻碍重放；SIGKILL使前台/容器退出137，缺退出回执不猜成功，原历史可查、active_run保留、新run拒绝。正常SIGTERM生成runner_stopped/exit_code=0结构化回执。独立PID门禁还验证前台退出收束子进程；两任务仅固定四挂载，bootstrap/input只读，无owner/backend根密钥或Docker socket。

架构/本轮凭证边界专项已复核关闭：bootstrap先发布后CAS、普通/取消attach竞态、过期连接/发送排队、最终Dispatch预算；另补历史重复传输、SDK与server卷名约束不一致、主动关闭后的监听重绑、hello早到环境finish及空闲失联回收保护。测试检查移到SDK外部，避免SDK包装断言异常误作预期失败。S03此前未完成的自动审查不由本轮结论替代。

仅本轮容器、网络和精确防火墙规则已清理；临时DNS按原字节撤回，专用VM恢复停止，其他VM/default Docker context不变。fixture数据与镜像保留，真实配置/凭证/旧数据未改。现有A仍有真实消费者，按S09统一切换删除；本片未建设第二个B循环或兼容层。S04.2/S05及目标机原生新增门禁保留；下一步仅讨论第8节S07.1。
