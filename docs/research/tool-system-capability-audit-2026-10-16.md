# 工具系统能力盘点：等待、取消、文件并发与后续边界

日期：2026-10-16。状态：源码盘点、临时框架/重启探针及既有测试复核，不是新功能实现。用户已确认取消正常串行排队的固定期限、文件冲突保留busy、工具运行进度属于必需能力；当前排队期限/进度产品尚未修改。本报告其余建议不自动构成实现授权。

## 1. 范围与证据

核对本项目当前工作树的八个内置工具、ToolSpec/Registry、活动工具、权限/审批、批次、运行停止、共享进程、输出及Spec085/086；不将整个Agent产品的所有可选能力都视为工具缺陷。

- Pi本机完整版本 a69bef789bc95abf0acee16f7b4660b70b650bb9；主线为 packages/agent/src/agent.ts → agent-loop.ts → packages/coding-agent/src/core/tools/。本报告不声称该本机版本是上游最新。
- AgentX本机完整版本 3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f；核对 BuiltInToolRegistry.createToolsForAgent → AbstractBuiltInToolProvider.createToolExecutor/executeTools → doExecute，以及 AgentToolManager.createToolProvider 的MCP装配。没有相同本地文件提交或嵌套HITL契约的直接证据。
- 当前框架版本及SubAgent探针边界沿[Spec086](../specs/086-subagent-foreground.md)。官方线程取消边界见[Python Future.cancel](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel)，等待取消的行为见[asyncio.wait_for](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for)。
- 本轮复跑九份既有测试：批次同步/异步、Write、Edit、进程生命周期/收尾、活动停止/故障和动态工具，134 passed in 5.76s。无新增测试、无全量或前端构建复跑，无真实供应商、Windows/Linux实机或任意恶意工具的硬终止验证。

## 2. 取消排队期限并不等于保证所有工具不会卡住

### 2.1 Pi实际如何处理

1. Agent.runWithLifecycle 创建 AbortController；Agent.abort 设置其信号；agent-loop.executePreparedToolCall 把同一信号交给 tool.execute。循环自身不对任意工具执行统一硬终止。
2. Bash createLocalBashOperations.exec 将取消接到 killProcessTree；只有传入 timeout 才设置执行计时器，省略时没有默认执行超时。waitForChildProcess 在父进程退出后处理管道EOF/静默，避免安静后代持管道造成一直等close；持续输出会延后静默结束。
3. Read/Ls的abort监听器可以拒绝外层Promise，但其未使用可取消接口的底层文件系统操作不因此被强制停止。
4. Write/Edit刻意不以abort监听器提前释放文件队列；在await前后检查信号，已经开始的文件IO仍须收尾。底层IO一直不返回，队列也可能一直等待。
5. 自定义工具若忽略信号、永不resolve，或者同步死循环阻塞事件循环，核心循环没有自动修复该问题的保证。

直接源码：[Agent取消](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent.ts)、[公共执行](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent-loop.ts)、[Bash](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/bash.ts)、[子进程退出/管道](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/utils/child-process.ts)、[Write](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/write.ts)。

AgentX的上述内置执行器直接调用doExecute并转换抛出的异常，不能因此证明不返回的调用会被终止；其MCP传输明确配置一小时timeout，不能推广成所有工具或线程的硬终止上限。

### 2.2 本项目目前的处理与限制

    request_stop
      → RunControl.signal置位；拥有的模型Task可取消
      → 进程工具：HostProcessRunner检测停止/执行期限，终止并确认收尾
      → 已开始的普通工具：保留其真实结果并等待在途操作结束
      → 后续排队工具：异步取得执行槽后再次检查停止，不启动工具正文
      → 不再追加下一次模型请求；必要保存后确认停止终局

- Bash/PowerShell默认执行60秒、最大300秒；Grep默认20秒、Find默认15秒；共同进程收尾总预算默认5秒。都来自当前settings.py，能配置；人工审批/正常排队不应计入命令执行时间。
- HostProcessRunner将执行期限、进程退出、管道关闭、消费者完成分开；必要收尾不确认则ProcessCleanupError上抛，不能以超时反馈伪装已经释放资源。
- RunControl并不取消任意工具线程；await_settled刻意等待已启动操作，不会通过清空busy让旧线程在背后继续写入。
- Ls已有循环间停止检查。Read跳过大量行、Edit多遍扫描/复制、Write分阶段操作目前没有等价的内部停止检查；它们遵守工具入口放行后等待收尾的现有合同，停止响应粒度仍可改善。增加检查只能在系统调用之间生效，不能中断不返回的内核IO。
- 同步批次入口尚未与异步取得槽后的停止guard对齐，Spec086已经列出；不能将正式异步CLI/Web证据推广为所有同步SDK分支完全相同。
- 自定义工具的SDK超时、合作停止及清理责任尚未形成集中作者说明。当前并不存在可自动保证任意同进程工具终止的ToolSpec字段。只加一个计时器，不能安全杀掉已执行的Python线程；异步代码吞掉取消或阻塞事件循环同样不能由外层期限自动解决。

建议：移除排队期限、保留各执行/清理期限；补清自定义工具合同。需要硬终止不可信任意代码时，另行使用独立进程/执行隔离，不把“取消等待”写成“动作已停止”。暂不新增通用看门狗、全工具进程化或统一强制timeout字段。

## 3. 文件写入排队之前是否遗漏

此前研究稿[18.2同目标并发](tool-registration-design-2026-10-15.md#182-功能候选及取舍)已明确记录Pi的realpath mutation queue，并提出沿用本项目busy；[Spec085第10节](../specs/085-tool-system-rebuild.md#10-文件工具路径递归权限与读写)及[文件地图](../specs/085-tool-system-file-map.md)保留该选择。因此这是已记录的行为取舍，并非这次才发现Pi有该机制；但此前没有把三种不同保护集中向用户说明，不应要求用户从研究稿推导其影响。

| 机制 | 约束的对象 | 本项目当前实现 | 不能保证什么 |
|---|---|---|---|
| 同批串行 | 一个模型响应中的工具调用顺序 | 含sequential则整批按模型顺序；不同批各有计划 | 不排所有会话/子实例对同一文件的写入 |
| 同真实目标占用 | 同一进程内Write/Edit正在修改同一规范目标 | write._reserve_target共享集合；第二条抛ToolException busy；Edit也使用它 | 不自动等候、合并或重试；不覆盖其他进程、Shell或外部编辑器 |
| 修改期间内容变化检查 | Edit扫描、构造、提交时的原文件 | 摘要/stat/目标核对、临时文件和原子替换 | 不构成跨进程文件事务，不保证任意敌对换链场景零竞态 |

Pi的队列仅在当前JavaScript进程内以realpath（不存在时退规范路径）为键；第二个操作等待，前者finally释放后执行。它不自动合并修改，也不把文件名队列变成跨进程锁。

具体例子：A正在Edit同一文件，B想Write整文件。Pi让B等A后覆盖；本项目立即给B一个busy错误，模型应重新读取并判断是否还需写。两者都能避免受管理操作同时进入修改区间，但后写整体内容是否覆盖前者，是不同的行为选择。

需要特别纠正的边界：Write的正式调用默认是整文件覆盖，并未绑定先前Read的版本。其内部expected_content没有生产调用方，不应据此声称Write具备Edit同等级的原文版本检查。busy也只检查时间重叠，不能防止不重叠的旧内容覆盖。该无消费者内部参数列为后续清理候选，本轮不删除。

用户在后续讨论中已确认继续busy，不增加目标队列。若未来另有同目标等待需求，可局部调整_reserve_target及其调用/取消测试，不需要修改ToolSpec、权限准备、模型循环或增加整个工具调度平台。排队也不会替代子Agent任务的写入范围安排、版本核对或分离工作区。

## 4. 能力矩阵

“已接线”指当前源码消费者存在；本轮134项仅复核其中相关分支，不把矩阵每行当成新增实测证据。

### 4.1 已接线的基础能力

| 能力 | 当前入口/合同 | 当前边界 |
|---|---|---|
| 工具定义、公开参数与注入字段 | tools/registry.py的ToolSpec及validate_input；schemas.py | 严格公开模型与执行模型同源；参数错不回显runtime；不兼容旧多套格式 |
| 代码注册、会话活动工具、提示词随集合调整 | ToolRegistry.register；App.register_tool/aregister_tool；ToolSetMiddleware | 新注册不自动启用；本次模型使用的定义固定到其批次；不做文件插件自动加载 |
| 八个内置定义 | create_builtin_tools：Read/Ls/Find/Grep/Write/Edit/Bash/PowerShell | CLI/Web默认活动六文件工具；Shell需选择；缺解释器/rg/fd明确反馈 |
| 权限规则及四模式 | permission_rules.py、PermissionMiddleware、session_controls.py | Deny优先，Allow可覆盖默认Ask；Default/NoAsk/Auto/BypassApprovals；无显式Ask规则 |
| Auto快/详审、来源限制 | AutoPermissionReviewer、collect_human_messages | 真人消息/项目指令/当前调用；不交工具结果或文件正文；无效审查回人工 |
| 审批与一次授权 | BatchApprovalMiddleware、OneUseApprovalGrants、ExecutionAuthorization | 本Agent本批审批前零执行；恢复不重跑Auto；许可绑定身份/参数/真实目标/环境 |
| 会话“不再询问”及模式切换 | session_allow、set_permission_mode | 内存会话范围，不写配置或数据库；切换只影响后续批次 |
| 批次串并行与并发上限 | batch.py、async_batch.py | 无sequential的批次有限并发；存在sequential则整批串行；正常排队期限仍待移除 |
| 普通错误与控制异常分工 | ToolException、ToolErrorMiddleware、feedback.py | 普通错误同ID回填；停止/中断/必要保存/真正机制故障保留控制效果 |
| 真实目标、文件/目录软链接 | paths.py、FilePermission、require_file_target | 审查真实目标；批准后写真实文件保留链接；Windows路径比较已接，原机待验 |
| 递归候选过滤与不完整报告 | candidate_access、Grep/Find/Ls | 未授权隐藏/隐私跳过，Deny始终过滤，结果说明未搜索范围；不是Shell沙箱 |
| Read分页、巨行与图片 | read.py、images.py、model_access/messages.py | 2000行/64KiB、完整行优先、UTF-8前缀；Pillow预算及两API图片适配；无行内游标 |
| Write建父目录、真实提交 | write._write/_replace_temp | 授权后建目录；同目标busy、临时写/fsync/原子替换；提交未知反馈，不自动回滚 |
| Edit大文件与有限模糊匹配 | edit.py、edit_matching.py、edit_diff.py | 同原文多替换、分块、唯一/重叠检查、换行/BOM保留、有限归一化、预算内diff/patch/artifact |
| Ls一层列名 | ls._list_directory | 排序/预算/原因/部分结果/停止；为排序仍先枚举名称，不是流式常量内存目录索引 |
| Grep/Find原生搜索 | grep.py、find.py、search_protocol.py | rg/fd、过滤后计数、有限输出及执行期限；不静默下载/替换搜索语义 |
| 双Shell与进程生命周期 | ShellAdapter、HostProcessRunner、ProcessProtocol | 每调用新进程、继承/覆盖/移除环境、无交互TTY；退出/EOF/消费者/取消分别确认 |
| 输出预算、归档和回读 | OutputAccumulator、ShellOutputArchive、Read | 合流尾部、截断标记、原始归档/必要文本视图、共享配额；并非无限保存 |
| 工具结果与持久状态 | 原生ToolMessage content及JSON artifact；官方checkpoint | 模型必要反馈放content；artifact不自动进入模型或前端详情 |
| 安全完成会话的保存和重启续聊 | 工作区UUID/SQLite、SessionStorage | 同库单服务；不同工作区可开不同服务；不支持任意副作用中途崩溃后自动重放 |

### 4.2 尚未提供、取舍或验证缺口

| 项目 | 状态及当前建议 | 对架构的影响 |
|---|---|---|
| 正常串行等待无固定期限 | 用户本轮已确认；产品仍待删除60秒/env及更新测试 | batch.py/async_batch.py局部合同，保留机制校验；不改ToolSpec/权限 |
| 同目标写入排队 | 用户已确认继续busy，不增加目标队列 | 未来可局部扩展目标占用算法，不需改核心图 |
| 文件工具长操作中途停止粒度 | Read/Edit/Write没有完整阶段检查；现状是等待已放行工具结束 | 可复用现有RunControl直接传到循环/提交边界；会影响停止语义和测试，不预先新增取消框架 |
| 自定义工具的超时/停止/清理作者合同 | 执行能力有，集中责任说明不够明确 | 文档/示例首先补齐；网络SDK与循环自行配合；任意代码硬终止需进程边界 |
| 前台一层SubAgent及内部人工审批 | 已讨论、Spec086供审阅未实施；已跑框架探针不等于产品接线 | 既定作用域读取、子驱动、审批来源/提交、停止/保存、事件路由迁移，不能称085原样零修改 |
| 全树总并发/token/费用硬预算 | 当前只有每批max_concurrency；086模型轮数上限可选，不含重试/Auto等所有请求 | 若将来明确需要全树限额，应用所有者协调资源；不能把本批限额宣传成全树限额 |
| 工具运行中部分输出/进度 | 用户已确认必需；Pi Bash有onUpdate，当前产品尚未接运行进度；已验证官方stream_writer/custom在工具返回前可交付 | 扩展事件合同/有界投递/消费者；保留最终ToolMessage合同，不新增ToolSpec回调字段，不是纯CSS修改 |
| 工具/模式选择、快捷键、图片/diff专用UI | 已排后续；后端活动集合/模式与artifact已具备 | 前端与必要HTTP管理端点适配，不改工具IO；与上一项运行中输出区分 |
| 单行内部续读 | 明确后排；巨行前缀有提示，余文目前用Shell | 若需要新增Read游标参数和测试，局部schema变化 |
| 非UTF-8文本 | future-work L8已记录；当前严格UTF-8，Pi使用UTF-8替换解码并不等于可靠多编码支持 | 确定编码契约后改Read及分页；不静默猜测/替换文件内容 |
| 工作区插件/MCP工具加载/远程IO后端 | 首版明确代码注册；Pi的Operations和扩展系统不是本项目已接能力 | 可信装配可产ToolSpec；第三方加载的信任、资源及生命周期另定，不预建通用Operations |
| 后台Shell/后台子Agent、实例续跑、多层委派 | 分别已后排；当前Shell前台/086首版前台单层且每次新实例 | 任务持有者、跨轮次结果/审批和退出合同会扩展；不承诺加开关即支持 |
| 原生沙箱及任意代码硬终止 | 未实施；当前宿主进程组与执行环境字段不是隔离保证 | 先接进程服务；若要求Python文件工具也受OS隔离，须另外定义其执行位置/文件IO接口 |
| 跨进程同目标文件协调 | 没有；内存busy/队列都不能覆盖外部编辑器、Shell及其他服务 | 需求出现后选文件锁/版本检查/工作区隔离；不默认加入全局锁服务 |
| Windows/Linux及真实供应商验收 | 088/089代码与mock已接；无Windows/Linux原机，无真实PowerShell/新图片供应商验证 | 以平台复盘验收为准，不能由macOS假分支推导已交付 |
| 待审批跨重启恢复、崩溃后副作用恰好一次 | 当前恢复入口只接受ready；interrupted/running均阻止，图checkpoint可能仍存在，详见第9节实测 | 纯前置审批恢复与已执行副作用恢复须分别设计；工具队列不解决此问题 |

## 5. 建议固定的职责与下一步

    ToolSpec/Registry：定义、参数、工具声明
        → 本批工具/模式/权限准备：审查对象与已保存决定
        → 官方批量HITL：暂停/恢复
        → 批次调度：顺序与槽位
        → 工具执行：文件目标占用或进程生命周期
        → 同ID ToolMessage：content给模型，artifact留非模型消费者
        → 应用：停止所有权、事件投递和必要保存

本轮没有证据要求推翻ToolSpec、统一ToolError、权限准备或官方批量HITL。文件队列、取消检查点、进度事件是不同层的能力，不能因为某项未采用就重新造一个总调度框架。

现阶段建议：先审阅本矩阵中的未覆盖项；已确认的60秒移除按局部切片实施；文件busy保持原合同；继续讨论循环间停止、作者合同及新增进度能力；然后沿Spec086六片实施。未来功能只能在其所属边界扩展，确需改变已有合同须明确列出迁移和证据，不能口头承诺以后零架构调整。

这份盘点覆盖当前明确目标及参考项目的相关工具能力，不是对任意未来产品需求的完整性证明。本轮未改生产/测试/配置/依赖，未提交或推送。

## 6. 文件工具中途停止：供讨论的最小方案

复用RunControl.requested，通过已有ToolRuntime/context显式传至IO helper；不增加第二套取消token、统一硬期限或新异常体系。沿Ls现有“具体工具反馈＋原停止标志结束run”的方式，不仅抛一个普通错误而继续模型。

- Read在跳过行/读取下一块之前检查；已获准并完成的前缀可保留，标不完整/用户停止。图片只能在读入、解码、缩放等阶段之间检查，不返回半张图片。已完成的有效结果保留。
- Edit在精确/归一化扫描、复制块之间及提交前检查；提交前停止可删本次临时文件、保持原文件不变。仍须确认关闭/清理，而非先释放目标占用。
- Write在创建各级父目录、准备临时文件、提交前检查；新建目录可能已经存在于磁盘，不递归撤销。
- 最后一次检查后进入replace视为提交区间：停止不抢断它，等待真实结果；成功保留成功、失败/结果未知保留原反馈。不能提交已完成后又报“文件未修改”。
- 一个系统调用不返回时，循环间检查也无法执行；界面显示stopping/等待收尾，不伪报已终止或提前允许同目标新写入。

这会改变已放行长文件工具的停止响应粒度，需独立小Spec和阶段/真实结果测试；当前仍是讨论，不将新行为写成已实现。

## 7. 自定义工具：供讨论的作者责任

1. 代码注册继续ToolSpec，不强制新增timeout/cancel/cleanup三个回调。真正异步SDK使用异步工具；阻塞客户端不能直接放进async def堵住事件循环。
2. 每个外部IO由该工具的可信设置提供有限SDK超时；需要总执行预算时由工具实现，执行预算从获准开始执行计，不包含排队/人工等待，重试不能不断重置总预算。外层结束等待不代表线程/远端动作已取消。
3. 循环、分页、阶段切换检查已有RunControl；支持取消的异步调用响应取消并清理，CancelledError保持传播。用户停止和业务错误保持区别，已经产生的真实结果不能改写为未执行。
4. 在finally/上下文管理中关闭自有文件、客户端、事务和进程；清理失败可观测，不能覆盖原异常或谎报清理成功。命令工具优先复用HostProcessRunner，已有ProcessCleanupError保护不变。不允许工具偷偷遗留无所有者的后台任务。
5. 普通可反馈失败使用ToolException；动作结果不确定时说明不确定、不自动重放。有无法安全释放的特殊资源时须明确该工具的失败合同，不能假设统一错误转换自动解决资源归属。
6. 长任务提供真实阶段/计数/输出；无总量就留空，不伪造百分比。进度通道与最终结果分离，发过进度不算完成。

## 8. 每批并发、全树并发与进度

现有max_concurrency限制本次图调用/本批工具的并发，用户对此理解正确。“全树总并发”只描述未来根Agent及其所有子实例合起来的资源数，当前没有另一个已实现的全树开关。

例如父同批运行子A和B，每个子自己的批次限4；A可运行4个工具，B也可运行4个，合起来最多8个实际子工具，父另有两个委派调用等待结果。没有任何单批违反4。示例是未来多子场景推演，当前没有SubAgent产品实测。

建议首版仍用每批上限，不因这个术语新增全树调度器。将来有实际总资源压力再单独限定具体资源；若所有父委派与子工具争同一有限信号量，会有父占槽等子、子拿不到槽的死锁风险，不能直接把本批信号量全局化。

用户确认进度是必需能力，目标是让长等待可见并可主动停止：

    权限准备/审批 → queued（待执行槽）
        → running（真正进入执行）
        → 阶段/真实计数/有界输出更新
        → stopping（已接受停止、等待资源收尾）
        → 最终工具结果及run终局

前置审批另标waiting_approval。必须区分当前dispatch开始事件与工具正文真正开始，不能让排队项从第一秒就显示正在执行。

优先复用官方ToolRuntime.stream_writer/custom → 现有图流消费者 → 现有RunEventPublisher → Web/SSE；同步消费者同样接线。无需新增ToolSpec回调字段。候选进度字段为调用身份、阶段、completed/total/unit及受限message/output；子来源按086的agent_path等身份归属，total未知时不报百分比，Edit各遍扫描分别显示阶段。

Shell已由consumer持续收到字节，可用现有解码/尾部快照生成更新；文件工具在阶段/块处理时更新。节流/合并/单条及缓存总预算需配置和验证，避免进度淹没当前有界事件缓存；不把进度逐条追加进模型messages/checkpoint，也不把心跳伪装成进展。输出静默不证明卡死，界面可显示已运行时间和距最后真实更新时长，用户请求停止后仍按资源合同收尾。

最小框架探针：当前安装版本create_agent＋同步ToolRuntime工具，先stream_writer发记录，再用线程Event阻塞其完成；astream(custom/values,v2)收到记录时断言工具未完成，然后释放Event。退出0：tool_progress_probe=PASS，emitted_before_tool_finish=True。这证明官方传输可用，不表示本项目消费者或UI已经接好。

## 9. 待审批重启、图位置与副作用是三件事

当前SessionPersistence._restore_steps首先要求row.state等于ready，否则session_recovery_blocked。正常审批挂起finish保存interrupted；若在必要元数据finish前进程退出，row可能仍是running。不存在自动回退到上一轮ready、把当前节点跳过或重新执行所有调用的产品逻辑。Web能列元数据，但重新打开该会话/提交旧审批目前409，不能误称已可只读展开完整会话。

官方持久checkpoint可以保留messages、tool_execution已准备调用、next和task.interrupts。它保存图状态/调度位置，不保存整个Python调用栈或保证任意副作用恰好一次；节点恢复可能从节点开头重入。见[官方interrupt持久/恢复合同](https://docs.langchain.com/oss/python/langgraph/interrupts)。

本轮真实双进程临时探针：假模型提出一个工作区外Read，进入正式审批；子进程os._exit(0)，父进程新建应用并读取同一临时SQLite：

| 强制退出位置 | 元数据state | checkpoint | next | interrupts | prepared calls | 应用restore |
|---|---|---|---|---:|---:|---|
| 待审批已保存、run已返回后 | interrupted | 存在 | BatchApprovalMiddleware.after_model | 1 | 1 | session_recovery_blocked |
| 图已暂停、元数据finish尚未提交 | running | 存在 | BatchApprovalMiddleware.after_model | 1 | 1 | session_recovery_blocked |

这是本机合成Read/正式图/SQLite强退探针，没有执行待审工具，不是执行中Write的崩溃保证。临时数据均在TemporaryDirectory，不修改真实库。另复跑持久化/异步/Web重启拦截/活动停止/Ls五组，49 passed in1.95s。

纯“前置整批审批已持久化且本批尚未开始”原则上可按官方能力补重启恢复；还须重建应用pending审批/身份/上下文，核对当前注册工具、规则/环境和真实目标，旧一次授权不能自动复用，不能只删ready判断。根前置审批与独立子实例/兄弟活动状态的恢复复杂度也不同，需要独立验收。

已经批准并执行的操作是另一类问题：例如文件替换成功但相应工具结果checkpoint尚未确认，重启后图可能仍显示该调用待执行。不能由这个图位置推断动作没做，也不能保证重放一次安全。纯待审批恢复不必因此一并实现全部副作用恢复；当前两类产品能力均未交付。

## 10. “自定义工具合同”如何落实

用户继续要求明确：哪些是强制注册门槛，哪些只是行为规范。当前以及建议必须区分以下三类，不把文档要求伪装成编译器可证明的保证。

| 类别 | 如何落实 | 例子/实际边界 |
|---|---|---|
| 工具结构 | ToolRegistry.register → _validate_spec运行时拒绝非法登记 | ToolSpec/BaseTool、名称/唯一性、公开schema与注入字段、execution_mode/side_effects枚举、权限接口、结果/路由合同；Python不以编译检查证明其正确 |
| 框架拥有的执行机制 | 批次/权限/ToolError/RunControl/共享进程/进度通道 | 框架控制自己拥有的调用路径；复用HostProcessRunner才能获得它的进程超时与回收行为；不能自动管住工具自行创建的任意线程/客户端 |
| 工具实现的行为 | 开发文档、可信配置/工厂示例、该工具测试 | SDK是否真的传timeout、循环是否观察停止、finally是否关闭正确资源；注册时不扫描代码关键词，不执行工具做危险的注册自测 |

本报告第7节提出的超时/取消/清理主要属于第三类，不是新增三个必须实现的方法。工具不响应停止仍可能注册成功，不能声称其已满足行为约定。建议不强制新增生命周期回调或“能力已验证”标志；作者使用现有runtime信号/通道和共享runner，文档与测试验证实际行为。

本轮真实注册探针：一个没有timeout/取消/清理声明的合成BaseTool成功登记；非法execution_mode被拒绝；未执行工具正文。退出0：registration_probe=PASS，lifecycle_callbacks_not_required=True，invalid_execution_mode_rejected=True。

Pi同类边界：extensions/types.ts的ToolDefinition约束name/parameters/execute等形状，execute接signal/onUpdate；extensions/loader.ts::registerTool登记定义并刷新工具。它没有在这段注册链证明函数会响应signal或资源一定释放。不能据类型声明把行为保证说成注册前已验证。

## 11. 服务重启、会话可用性与参考项目的恢复策略

### 11.1 当前服务没有因受限会话禁止重启

Web启动装配持久应用，/sessions列元数据；访问特定会话时WebService.session才调用arestore_session。running/interrupted等受限状态在该入口拒绝，正常服务仍可启动并创建新会话。前述“恢复被阻止”指该旧会话，不是全服务。前提是数据库可读、当前配置/锁等启动条件正常；本节不讨论数据库损坏。

当前缺口仍影响用户：受限会话不能展开完整历史或继续沟通。以前只建议“先恢复纯待审”的范围偏窄，不能作为长期产品体验。

### 11.2 Pi：恢复对话，模型输入补缺失结果，不重放原工具

核对本机a69bef789：

    AgentSession的message_end → SessionManager.appendMessage/_persist
      → 重启loadEntriesFromFile（跳过空行/非法JSON行，校验头）
      → buildSessionContext重建所选分支消息
      → createAgentSession将existingSession.messages交回Agent
      → 新请求进入provider适配
      → transformMessages补缺少配对结果的工具调用

补齐内容是“No result provided”，isError=true，沿原toolCallId；不是伪造执行成功，不执行旧工具。这是发给模型的消息投影，原始会话文件不因此自动重写。模型仍需判断下一步；该短提示也没有证明动作实际未发生。

源码：session-manager.ts::loadEntriesFromFile/buildSessionContext/_persist；agent-session.ts的message_end处理；sdk.ts:372；[transform-messages.ts:158](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/api/transform-messages.ts:158)，已追到anthropic-messages/openai-completions适配器调用，不只读取注释。

本轮直接通过Node原生TypeScript支持导入实际transformMessages，测试末尾孤立调用、新user跟在孤立调用之后两组：均补同ID error；输出“No result provided”；原数组未改变。退出0，无工具动作/网络/安装/参考仓库修改；不是Pi全产品强退测试。文件读取/持久链结论为源码证据；不将appendFileSync称为任意断电完整持久保证。

### 11.3 Claude Code：官方明确默认不中断后重跑旧工具

2026-10-16查阅[Manage sessions / What a resumed session restores](https://code.claude.com/docs/en/sessions#what-a-resumed-session-restores)：默认恢复崩溃前仍在运行的工具，不使它自动完成或再次执行；模型看到结果记录前中断标记，并被要求先检查是否已经生效。该说明明确排除启用了CLAUDE_CODE_RESUME_INTERRUPTED_TURN的路径；[环境变量文档](https://code.claude.com/docs/en/env-vars)另说明该开关可让SDK在中途结束后自动继续模型轮次，本报告不从此推断任意副作用重放保证。

[How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works#work-with-sessions)说明消息/工具调用/结果写本地JSONL；[SDK会话文档](https://code.claude.com/docs/en/agent-sdk/sessions)明确会话恢复保存对话，不恢复文件系统。本轮依据官方产品合同，没有反编译或本机真实Claude崩溃验收，不能虚构其内部修复函数/存储事务。

AgentX3fcfa0e的ConversationAppService.setupContextAndHistory从数据库活跃消息ID重建历史；AbstractMessageHandler保存用户消息，完成回调保存助手/工具展示消息。它是对话恢复参照，没有本项目相同前置HITL与副作用精确恢复证据，不据此断言其任意崩溃保证。

### 11.4 对本项目的修订建议（供讨论，尚未实施/确认）

1. 服务启动与单会话故障隔离；数据库正常时，不因某个旧run未完成而拒绝其他会话或新会话。
2. 可读取的旧会话应允许展示已保存历史、说明中断事实并继续沟通；不能把state不是ready永久等同于整段对话不可用。
3. 确认停在已持久化的前置审批、本批尚未放行：可重建待审对象/展示并由用户决定，不沿用旧一次授权。此处沿用第9节的规则/工具/目标复核要求。
4. 执行中断且没有可确认结果：保留已保存真实结果，对其余缺结果调用生成同ID错误，明确“上次运行在记录结果前中断，动作可能已生效，先核查再重试”。无法证明未开始的，不写“未执行”。
5. 结束旧执行路径的自动调度，用户继续时进入新的run；不能直接invoke(None)让框架重放待执行工具节点。完成消息配对/恢复说明与必要保存后，模型可核查文件/远端操作并重新提出工具调用，继续走当前权限规则。
6. 历史修复不回滚磁盘，不保证旧宿主命令已终止，不自动再次执行写入/请求。保留原checkpoint/故障事实以便核查；不能简单将running改ready或删除未完成调用。

该建议以恢复可用性为目标，不要求先做任意副作用恰好一次。具体如何建立可继续的图状态、保留原checkpoint及修复配对，须独立小Spec和当前框架探针验证；本轮不选择未经验证的update_state/新thread接法，也不修改旧恢复门禁。

本轮既有注册/持久化/Web重启拦截测试命令：

    .venv/bin/python -m pytest -q tests/test_tool_registry.py tests/test_persistent_sessions.py tests/test_web_tools.py::test_approval_request_validation_auth_and_restart_block

实际45 passed in1.27s。仅验证现有合同，不能将新恢复建议宣称已接线；未跑新的全量、前端构建或真实供应商请求。

## 12. 实现前Spec与可重复框架探针

用户确认前轮讨论已清楚，要求说明实现、写Spec后审阅；本轮不实现产品。已拆为[090排队/停止/作者约定](../specs/090-tool-wait-stop-and-author-contract.md)、[091工具进度](../specs/091-tool-progress.md)、[092重启恢复](../specs/092-session-restart-recovery.md)，按090A/B、091A/B、092A/B/C七片推进，状态均为供审阅。

确定接法：官方update_state/aupdate_state(None, as_node=END)合入完成pending writes并清任务；补缺结果和清临时运行状态时以现有tools state writer写入，再END结束后续路由，最终元数据确认。三步不执行工具节点、不invoke(None)。每步用checkpoint metadata中的JSON字符串记录来源/recovery_id/phase，和checkpoint同次保存，无需改schema；下一轮由该marker幂等收尾。

证据脚本[probe_session_recovery_design.py](../examples/probe_session_recovery_design.py)使用现有测试假模型/装配、临时真实文件与SQLite，无真实模型/配置/网络：

```bash
PYTHONPATH=tests:. .venv/bin/python docs/examples/probe_session_recovery_design.py
```

实际退出0，三个断言组：

```text
continue_probe=PASS reopened=True marker_persisted=True continuation=True old_tool_calls=0
pending_writes_probe=PASS async=True saved_success_retained=True original_checkpoint_unchanged=True no_replay=True
approval_transport_probe=PASS reopened=True mixed_sequential_batch=True no_early_tools=True new_grants=True
```

第一组实际create_agent前置审批暂停→关闭库→新App清任务/配对→再关闭库→恢复ready→新输入，原Read计数0。第二组AsyncSqliteSaver保存并行成功/失败任务，关闭重开后END保留成功结果，原始checkpoint内容相等、计数不增加。第三组手工重建内存pending以单独验证现有恢复运输：Read allow、Write ask、含sequential的整批初始动作0，重开后批准各执行1次；pending_turn=None沿现有approval_retry分支。它**没有实现新API、当前权限复核和产品崩溃恢复**，不能当作这些验收完成。

探针发现并已反映到Spec：SQLite metadata只透传标量，字典会被丢掉，因此用JSON字符串；显式历史checkpoint查询与最新查询对pending writes的投影不同，不能用next完全相等判断旧checkpoint是否保留，需比较实际保存内容。源码参考当前Pregel.bulk_update_state/abulk_update_state及checkpoint.base.get_checkpoint_metadata，不改框架内部表/版本。

目前仍需实施测试覆盖：恢复中各持久阶段强退/保存故障、当前Deny/目标/工具变化、同ID幂等配对、Web/CLI入口和重入准入、完整事件及Windows原机。仅写文档和探针，没有产品/正式测试/真实用户配置/依赖/数据库文件变更。

本轮另复跑现有注册/持久化/Web重启门禁测试（第11节同一命令）：**45 passed in1.26s**。三份新Spec各114/104/190行，探针200行，AST/本地链接/`git diff --check`通过。未重跑全量或前端构建，因为没有产品/前端代码修改；不把旧全量结果当本轮新功能验收。

## 13. Bash/Grep/Find执行期限对照（讨论，未实施）

用户在090A实施前要求重新核对执行期限，090A尚未获本轮实施授权。本节只记录来源和候选，**不修改090/091的已写方案、实际默认值或产品代码**。当前下一步讨论期限是否可选及默认值，不直接实施。

### 13.1 Pi：本地已核对a69bef789完整相关链

`AgentSession工具登记 → wrapToolDefinition → agent-loop.executePreparedToolCall → definition.execute(signal,onUpdate)`未加统一执行倒计时。明确限定默认本地操作，不替任意扩展/远端执行器保证行为。

| 工具 | 执行期限与到期/停止 | 进度和其他限制 |
|---|---|---|
| Bash | bashSchema.timeout可选；resolveTimeoutMs(undefined)返回undefined，createLocalBashOperations.exec仅在提供正数时setTimeout，默认无期限；到期killProcessTree并形成含已收集输出的超时错误；abort同样请求杀进程树 | onData→OutputAccumulator→节流onUpdate；输出预算不等于执行期限。显式timeout拒绝0/负数/非有限数，并受JS计时器最大值限制 |
| Grep | 默认spawn rg，没有执行计时器/模型timeout字段；AbortSignal回调child.kill，close后处理取消；达到匹配上限可主动停止并返回受限结果 | 当前_onUpdate未使用；默认100个匹配、文本预算仍在。不能说Pi搜索已具完整扫描进度 |
| Find | 默认spawn fd，--max-results约束结果数；没有执行计时器/模型timeout字段；abort请求child.kill并拒绝等待结果 | 当前_onUpdate未使用；默认1000结果/文本预算仍在，自定义glob分支的实际后台取消需作者配合 |

关键位置：[bash.ts:28](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/bash.ts:28)、[grep.ts:223](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/grep.ts:223)、[find.ts:137](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/find.ts:137)、[agent-loop.ts:670](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent-loop.ts:670)、tool-definition-wrapper.ts。源码事实没有转述为真实进程故障测试。Bash的waitForChildProcess在父退出后另有100ms输出静默处理，不是运行100ms即超时；rg/fd缺失时ensureTool下载也有网络超时，与搜索本体期限分别说明。

### 13.2 Claude：当前官方合同与无法确认的部分

2026-10-16核对[Tools reference](https://code.claude.com/docs/en/tools-reference#timeout-and-output-limits)和[环境变量](https://code.claude.com/docs/en/env-vars)：前台Bash/PowerShell默认120000ms，模型可指定timeout，默认配置下上限600000ms，有效上限取BASH_MAX_TIMEOUT_MS与DEFAULT较大值。数值可配置，官方未声明填0就是禁用。

[前台到后台](https://code.claude.com/docs/en/tools-reference#foreground-commands-that-move-to-the-background)明确：通常到期转后台继续（sleep开头例外），不是直接杀命令；禁用后台或bare模式则到期停止。后台本地交互会话可无期限，无人值守有独立期限，当前文档注明2.1.285/2.1.288版本边界。不据此把项目首版前台工具暗改为后台命令。

Find按功能对应Claude的Glob或Bash中的find，不是假定有同名独立Find。官方当前Glob/Grep说明与官方发布的[@anthropic-ai/claude-agent-sdk@0.3.301输入schema](https://app.unpkg.com/@anthropic-ai/claude-agent-sdk@0.3.301/files/sdk-tools.d.ts)表明GlobInput/GrepInput没有模型timeout字段；**这不证明内部没有期限**。本轮未取得当前闭源执行器的确切搜索期限/禁用开关，不能统一宣称20秒、15秒或无限。当前英文工具说明还区分部分平台使用Bash内find/grep的路径，其期限应按Bash讨论。

较旧[官方韩文环境变量页](https://code.claude.com/docs/ko/env-vars)列CLAUDE_CODE_GLOB_TIMEOUT_SECONDS=20秒、WSL60秒；当前英文页未列该项，故只能作旧文档证据，不确定地推广为当前版本合同。官方issue#16043有2.0.76用户报告约10秒被SIGTERM及误报无结果，系原始问题报告而非当前源码/验收，不能拿来确定新版本时间值。

### 13.3 本项目事实与供讨论建议

本轮调用load_tool_settings，仅投影时间数值，实际为Bash/PowerShell default60/max300/probe5，Grep20、Find15，process.cleanup5秒。settings.py均要求正数；当前填0不代表无限，普通预算校验失败会日志并回退默认。搜索deadline与ProcessRequest.timeout_seconds贯通共享HostProcessRunner，外层不加总期限也不能绕过内部deadline。

候选建议：三个工具都支持可关闭的执行预算；Bash/PowerShell默认不限时，允许模型显式选择正数timeout；Grep/Find暂保留当前有限默认，提供用户配置0关闭。0是应用设置语义，进入执行层统一为None，不传浮点infinity/巨大数字；搜索不因此新增模型timeout参数。此建议是默认交互/后台无人值守取舍，不是框架强制，也没有数据证明20/15是普遍合理阈值。最终默认值仍由用户确认。

去期限需同时覆盖settings/公开描述→工厂→搜索check_search/stream_records→ProcessRequest→runner启动/监视等待，而非只放宽配置校验。必须保持停止可达（包括无输出/启动中）、实际进程与consumer settle及必要保存；当前Find候选路径检查有同步文件IO，需验证慢IO不会阻塞停止入口，固定期限本身同样不保证这类IO即时可中止。

进度/停止使用户可观察并干预，不能自动证明任务有进展。无匹配搜索可能长时间零输出；用户离开则无限任务可一直等，因此执行期限保留可选有价值。总输出/结果/归档预算、解释器探测和进程清理期限不随执行预算关闭；不加“无输出即卡死”的新看门狗，不因本次研究提前做后台平台。

本轮只读参考/本项目源码、官方文档和公开SDK类型，未运行Pi/Claude真实任务或升级/安装它们；无产品/正式测试/真实配置/依赖改动。当前尚不声称可配置无限已实现，不改写090A为含所有执行期限迁移的大切片。

## 14. 后续用户决定与090A实施

用户明确覆盖13.3候选：四进程工具默认都不限时，公开timeout省略或0不设期限，正数指定本次期限；另列090C，安排进度界面后实施。该合同已写入Spec090，尚未改执行器/四工具参数/真实配置。本轮获授权的是090A，不继续讨论或实施090C的接口细节。

090A仅改现有batch.py/async_batch.py，删除等待期限及旧env读取，同步放行前补RunControl检查。新增8例先7失败/1通过，修改后相关39通过；全量1718 passed in51.86s，前端27及构建成功，compileall/pip check、284 Python AST/105生产文件行数及diff检查通过。原旧env拒绝断言迁成4组无影响装配，重复调用/计划丢失/异常唤醒/正常错误继续/审批及顺序断言保留。

实际图max_concurrency=1/2验证先settle已开始动作，再配对排队调用的user_stop、零执行，无额外模型请求和遗留图任务；事件健康检查在测试末执行，不藏在被隔离听众里。完整入口与结果见[Spec090第6节](../specs/090-tool-wait-stop-and-author-contract.md#6-090a实施与验证结果)。当时下一步讲解并审阅090B；其后实施结果见第15节。无提交/push或Windows原机/真实模型新证据。

## 15. 090B实施

按用户批准补文件工具安全点及[作者约定](../tool-authoring.md)，不改执行期限/进度/恢复。一个tools/control.py被Read/图片/Write/Edit/归一化扫描直接调用，复用原RunControl及ToolException；Write父目录catch范围收窄，停止不冒充目录变化。Ls保持授权部分结果，注册仅增加结构/生命周期边界说明。

提交前检查、replace进入后真实结果、句柄/清理settle再释放busy均用真实文件/事件同步验证；真实fsync错误cause不被停止覆盖，临时删除失败诊断可见且不谎称已删除。新增35例；首批文件23例21失败/2通过、正式图3失败，最小实现后26通过，追加边界再回归。最终全量1753 passed in51.50s，前端27及构建成功，compileall/pip check、287 AST/106生产文件行数及diff检查通过，详情见[Spec090第7节](../specs/090-tool-wait-stop-and-author-contract.md#7-090b实施与验证结果)。

正式async图/SQLite覆盖同ID error、后序未执行、无多余模型请求、stopped/ready保存与重开续聊，正常观察无降级。文档示例已实际注册/图调用；不据此保证任意自定义SDK可硬取消。未做Windows原机或真实模型新请求，未改真实配置/数据库/依赖、未提交/push。当时下一片审阅091A，实施结果见第16节；090C默认不限时仍未实施。

## 16. 091A进度后端实施

按用户确认的职责调整，仅新增tools/progress.py，ToolProgressObserver并入已有tool_execution/observation.py。应用创建每run对象，经原RunServices/context传入；工具只调用官方writer，运行层校验身份/终结后发原Publisher，不持有展示快照。41新增例覆盖八内置真实IO点、未结束即接收、排队、预算/身份/迟到、普通观察失败与控制异常，以及四种应用启动设置；作者示例真实图返回3并报告bytes=8。

原090A并发1停止回归暴露同步custom订阅占满LangGraph1.2.12同一executor，经15秒有界faulthandler及框架源码确认；首次全量因此终止，不计为通过。最终同步dispatch以公开request.override/dataclasses.replace注入官方ToolRuntime.stream_writer，保持同步updates/values；异步仍custom。无框架补丁/依赖升级/新线程池/并发上限调整；原和新增并发1/2回归通过。5个旧断言迁移新事件数/序号且保留原身份/顺序/状态/内容，不为通过保留旧事件分支。

全量1794 passed in51.78s（/tmp/naiyue-091a-pytest.log）、相关102 passed in3.07s、前端27及构建成功；compileall/pip check、291 AST/107生产文件行数、配置样例/作者代码/链接/diff检查通过，详见[Spec091第7节](../specs/091-tool-progress.md#7-091a实施与验收记录)。091B界面/090C执行期限/092恢复未实施，下一片只审阅091B。无真实模型/Windows原机新证据，无真实配置/数据库/依赖修改或提交/push。
