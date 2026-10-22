# Spec125：主 Agent 后台命令生命周期

2026-10-21，实施设计及结果。用户已确认行为边界及默认值，并逐片授权实施125-01—06；六片已完成本机验收，06另纳入统一辅助侧栏和上下文快捷入口。与并行长期记忆调研、学习记录分开。

## 1. 目标、范围和外部合同

场景：第1轮启动被测服务并检查就绪，第2轮继续调用这个服务；或提交较长测试，在下一轮查询结果。一次Agent运行结束不等于命令退出。

首期承诺：

- Bash/PowerShell显式后台，以及主Agent前台命令超过等待阈值自动转后台；原进程、输出累计和执行截止时间不重置。
- 应用持有注册表，任务按工作区/会话归属；应用总览可查看和停止，模型仅操作本会话任务。
- 日志固定落在`<workspace_root>/naiyue-agent-output/`，只控制单次调用/单任务容量；移除工作区累计归档额度，不自动删除日志。
- 任务完成不新建模型运行；下一次用户输入触发的主Agent运行获得简短动态系统状态提示，模型按需查询正文。
- 子Agent仅前台命令，不允许显式后台或自动转换；退出应用时停止受管进程并等待必要收尾。

实际消费者：模型使用Shell和三个任务工具；CLI/Web直接调用应用管理接口，不通过模型执行查询/停止。沿用Python＋LangChain create_agent、已有审批与本机进程执行器，无新服务、账户、密钥或依赖。

首期不包含后台子Agent、PTY/stdin、定时任务、自动唤醒模型、沙箱、独立worker、应用重启后接管进程、跨重启任务历史。任务/已结束状态留在本次应用注册表，日志留磁盘；不新增SQLite表或预占schema版本。

应用崩溃可能留下旧进程，重启不能按旧PID猜测归属或自动重放。可查询保证受本次应用生命周期及文件仍可读取的边界约束，不把日志持久化说成进程恢复。

## 2. 实施前主链与为什么需要改动

实施前源码基线`5841cdf`，langchain1.4.2/langgraph1.2.12/checkpoint4.2.0/sqlite3.1.1。

`tools/shell.py::execute → require_tool_permit → ShellAdapter.build_request → execute_process_request → HostProcessRunner.run → ProcessProtocol.deliver → OutputAccumulator/ShellOutputArchive → 确认进程/管道/consumer/归档收尾 → format_process_outcome → ToolMessage`。

- `timeout`目前是模型可选参数，省略/null/0不限时；执行器在开始执行前建立唯一deadline，审批/排队不计入。
- Shell前台返回前等待进程与输出收尾。`AsyncApplication._stream`中的create_task仍属于原Agent运行，不是后台命令。
- 当前短普通输出不落盘，长/变换输出才归档；每调用16MiB，工作区128MiB。触限只停止归档，不停止命令。
- `_GLOBAL_LOCK`保护目录/清理快照；每次归档写入扫描目录计算总量。移除总量后，写入不再扫描全部历史文件；目录所有权/链接校验继续保留。
- 共享归档器实际消费者是Shell、aitest_kit和MCP结果；Web入口负责装配。web_search/web_fetch不使用此归档器，不改其请求期限或结果预算。
- `RunControl`、ToolProgress、stream_writer、执行许可属于原run。后台worker不能长期持有整个runtime或继承旧RunServices。

## 3. 已确认配置和时间规则

新增`[shell_tasks]`，由AppSettings加载并在CLI/Web装配中传给真实消费者；不修改用户真实settings.toml。设置在应用重启后生效。

当前01—04已接入下表五个字段；执行/日志/等待字段由双Shell消费，通知条数/字节字段经同一AppSettings和临时/持久构建器传到manager，不由模型传参。

| 配置 | 默认 | 合同 |
| --- | --- | --- |
| `foreground_wait_seconds` | 120 | 严格正整数秒；仅主Agent前台等待到期时转换 |
| `max_execution_seconds` | 0 | 严格非负整数秒；未配/0没有应用执行上限 |
| `max_background_archive_bytes` | 67108864 | 单后台任务64MiB；严格正整数；raw/text合计 |
| `max_notice_items` | 20 | 每批终态提示最大条数；严格正整数，04接入 |
| `max_notice_bytes` | 8192 | 整个终态提示段UTF-8最大字节数，含标题/查询说明；严格正整数，04接入 |

模型`timeout`仍为严格非负整数秒或null：

| 应用上限C | 模型参数M | 有效执行期限 |
| --- | --- | --- |
| 未配/0 | 未传/null/0 | 不限时 |
| 未配/0 | 正数 | M |
| 正数 | 未传/null/0 | C |
| 正数 | 正数 | min(C,M) |

配置字段类型/数值非法时明确报错，不能静默丢弃较短上限后回退成不限时。模型非法参数继续走现有工具校验反馈。

前台等待阈值F与执行期限T独立，但共用执行器开始本次执行的计时原点，不包含审批/串行排队。启动期间仍受总deadline/用户停止约束；F先到而spawn尚未确认时可交给后台并返回starting，不能伪造running。

- 前台无执行期限：到F仍运行则转换。
- T<F：到T终止；T>F：到F转换，仍在原T终止；T=F时执行期限优先。
- 显式background=true不启动前台等待计时。
- 进程自然退出或已收到停止时不再转换；转换不是重跑，不延长deadline，不把原0解释成绕过已配置上限。
- 仅Bash/PowerShell采用本配置；Find/Grep、aitest_kit、模型HTTP、解释器探测和必要清理期限保持各自合同。

## 4. 最小职责与拥有权

```text
CLI/Web应用装配
  └─ SessionService持有一个BackgroundProcessManager
       ├─ 本次应用的操作句柄/任务注册表/状态版本
       ├─ 每个操作独立的停止控制、worker、输出采集
       └─ 会话通知待提示集合

主Agent Shell → 当前许可校验 → 已授权进程计划 → 管理器
                                               ↓
                                 现有HostProcessRunner
                                               ↓
                                 受管输出＋进程收尾结果

任务工具 / CLI / Web → 同一管理器查询与停止
下一轮请求准备 → 只读状态快照 → 现有SystemMessage组装/预算
```

应用拥有是生命周期责任，不另造全局单例。管理器由SessionService构造/持有，AsyncApplication统一驱动关闭；不在每次工具工厂中另建，不由浏览器连接持有。

通过现有RunServices/invocation_context传窄的命令任务能力：提交/前台等待、列表、输出、停止和通知回执。协议定义在tools侧；工具不导入application，runtime不拿整个App/DB/UI对象。不新增ContextVar或任意服务定位器。

比较直接传参：底层runner/输出继续显式传参；框架注入的Shell工具运行时才能确定sid/run/call，因此复用既有受作用域保护的RunServices投影。单个窄端口服务三个任务工具与Shell四个实际消费者，不新增通用插件注册平台。

主Agent可转换的前台操作从启动起也由应用跟踪，但尚未转换时不出现在后台列表。工具调用自己等待完成或F，并在等待期间投递原run进度；worker只更新自己的采集对象，不持有原stream_writer。转换后停止向原run报告进度。

worker在空的contextvars.Context中创建，只持有复制的argv/cwd/env、执行策略、独立控制和采集对象；不携带一次性许可、原runtime或历史消息。许可在原调用内验证并消费后，冻结一次执行计划；不得借此开放后续任意执行。

后台交接成功后不占原根run名额，原工具串行门禁在工具返回时释放；不为后台另加队列或未经讨论的全局活动任务数上限。注册表状态保留本次应用周期，查询/提示有界；后台进程与后续工具共享工作目录，不能把原工具串行模式解释为整个后台生命周期的文件隔离。

HostProcessRunner保留唯一spawn/watch/settle/terminate实现。补充内部启动确认信号供管理器区分starting/running，不复制进程算法。现有execute_process_request继续服务子Agent/aitest_kit；共享输出采集在同一模块内收敛，原来确有消费者的前台包装继续保留。

## 5. 任务数据与工具接口

以下接口已于02接入；03仅为前台增加自动转换，不另造后台工具：

```text
bash(command, timeout=None, background=False)
powershell(command, timeout=None, background=False)
task_list(limit=20, before=None)
task_output(task_id)
task_stop(task_id)
```

列表按本会话过滤、分页（最多100条），只返回状态/有限命令预览等元数据，不扫描日志。输出首期返回有界规范化尾部和归档路径，沿当前Shell行数/字节预算；不额外建设流式全文分页器。详细UTF-8归档继续由Read按现有页读取；非UTF-8/raw有明确说明。

任务记录至少包含：随机task_id、workspace_root、session_id、origin_run_id、tool_call_id、Shell、命令、状态、开始/结束时间、有效执行期限、exit_code、termination、output_complete、archive_complete、cleanup_confirmed和归档引用。env/凭据不进入公开记录。

状态`starting → running → stopping → finished`，以及无法确认终局的`lost`。`finished`再通过termination区分exited/timeout/user_stop/app_close/output_limit/start_failed；退出码单独保存，不用“提交成功”替代命令成功。

同一`(origin_run_id, tool_call_id)`在本次应用重复进入时核对冻结请求身份并返回既有操作；身份冲突拒绝。新run的重新生成仍是新执行，不声称跨崩溃exactly-once。

显式后台登记并取得应用拥有权后可返回task_id＋starting；真实spawn确认后才running。原调用恰好快速结束则返回真实终态。调用结果只产生一次ToolMessage，后续task_output是新的工具调用与配对结果。

三个任务工具沿现有ToolSpec登记：list/output只读、默认allow；stop有副作用、默认ask，沿现有权限模式与规则。工具实现必须检查许可、可信root身份和记录的workspace/session，task_id或PID本身不是授权。子Agent显式后台请求在spawn前拒绝，自动转换入口同样检查身份。

任务工具随内建集合登记，用户仍可通过当前活动工具选择启停；后台回执必须说明可用查询方式。用户CLI/Web管理入口不受模型工具启停限制，不创建模型调用。启动Shell仍沿原opt-in/逐命令审批，不因增加后台能力而自动启用。

审批显示显式后台意图及允许自动转换的策略；审批绑定原args/工作区/环境。旧审批恢复与实际参数匹配规则保持，不能凭后台任务ID绕过命令审批。

## 6. 输出、额度与手动清理

- 固定目录`<workspace_root>/naiyue-agent-output/`；随机文件名沿现有32位hex-raw/text.log规则，任务记录保存引用。命令内部cd不改变归档根。
- 普通前台仍按现有单次16MiB等设置；不加工作区累计额度。达到单次归档额度继续排管道/执行，标记归档不完整。
- 显式后台从启动起激活归档，使用单任务64MiB；自动转换时激活归档并切换到后台单任务额度，已经保存的字节计入同一任务，不清零、不复制成第二个任务。
- 若转换前已经因前台额度丢弃过输出，保留“不完整/有缺口”的事实；可继续保存后续输出，但不能声称补回缺失部分。新增此分支的回归，不能无限缓存等待未来转换。
- 若用户将后台额度设得比前台额度更小，转换时已保存量达到或超过后台额度，则不再写并按output_limit停止；既有文件不自动裁剪，明确区分前台已产生量与转换后的写入约束。交接磁盘IO期间触限同样立即请求停止，交接前已观察到停止则保持前台结束，错误明确说明后台归档额度/写入原因，不误报用户停止。
- raw/text共同计入当前任务额度；后台实时查询使用有限内存尾部。必要文本视图沿当前规范化合同生成，磁盘quota不足不能称为完整UTF-8视图。
- 后台额度耗尽后记录output_limit、请求自身停止、继续排空直到runner确认必要收尾；只影响本任务，不停止其他任务，不将其记成用户主动停止。
- 进程已确认退出后生成文本视图触限，仅标记归档不完整，保留已知退出码；不能追溯改成命令执行超时/失败。
- 磁盘写入失败与进程退出是不同事实。后台无法继续保存时请求停止并保留有限尾部/诊断；必要收尾不确定才lost，已知退出码不被普通展示故障覆盖。
- 不新增TTL/自动删除；保留所有权/普通文件/链接检查、预览后精确集合确认。清理须与管理器共享短的准入保护：有starting/running/stopping或未收尾writer时，首期拒绝清理当前目录；不能仅检查根run busy。
- 手动清理已结束文件后，任务查询明确output_unavailable；任务状态和已知退出码保留，不冒充没有输出。

删除`max_workspace_archive_bytes`以及唯一承载它的ToolOutputSettings，不保留空配置对象/无用途参数传递。迁移Shell、aitest_kit、MCP、CLI/Web装配、脚本、导出和测试的真实消费者；保留它们自身单次结果/归档预算。配置读取处对已公开旧`[tool_output].max_workspace_archive_bytes`作明确已移除告警并忽略，其他未知字段仍拒绝；不修改真实用户配置，也不恢复旧执行限制。

## 7. 下一轮提示与界面

仅本会话新用户输入所触发的root运行读取待提示终态快照；不因任务完成新建run，不将输出正文提升到system角色，也不写伪造HumanMessage/AIMessage或旧call的第二个ToolMessage。

现有compose_model_prompt增加`background_tasks`段，只有应用生成的task_id/状态/退出码/原因及按需查询说明。每批条数和完整提示段UTF-8字节数分别由shell_tasks.max_notice_items/max_notice_bytes配置，默认20项/8KiB；剩余保留待提示。只放完整条目，若预算过小连一条都放不下，返回空快照、不确认通知并记录安全诊断，不截断ID/状态。固定本轮快照，任务在本轮中途结束留给下次用户运行，不并发修改LangGraph state。

接线：SessionExecution准备本轮资源时取得快照，AgentSession保存root专用段；SessionPort.resolve_prompt_parts显式带agent_path，child不获得该段。ToolSetMiddleware、手动压缩和context预览的实际消费者一并核对；手动压缩/审批恢复不消费通知。

有效模型响应通过现有接受校验后，确认本次携带快照的task_id/终态版本已提示；模型失败/被停止/请求未发出不确认。回执只更新通知集合，失败可诊断并在下轮重复提示，不能让成功模型调用因通知观察失败而失败。

上下文估算、自动压缩预算、SystemMessage parts描述、只读预览和ContextPanel标签同步，预览不能消费通知。状态段不包含命令stdout、完整命令文本或异常正文。

Web显示独立“后台任务”入口、应用级活动数/列表/输出详情/停止，不借用已结束run的SSE来投递新进度。首期用只读版本查询刷新：可见页面每3秒查有限元数据，面板打开才读选中任务的有界尾部；隐藏/卸载时停止轮询，失败诊断并提供重试。轮询常量放现有uiSettings，网络请求AbortController防串会话，不能触发模型。

交互CLI增加`:tasks`（应用总览）、`:task output ID`、`:task stop ID`，独立于当前Agent worker。状态变化在交互读取时显示固定提示，不增加单独终端服务或新顶层nyt子命令。

## 8. 应用接口、关闭与删除

AsyncApplication新增：`alist_background_tasks(session_id=None, limit=20, before=None)`、`aget_background_task_output(task_id, session_id=None)`、`astop_background_task(task_id, session_id=None)`。外部用户入口可做应用总览；工具端口强制传可信session_id。CLI async桥只提交到现有单事件循环。

05已在Web接入受现有authorize保护的GET `/api/background-tasks`、GET `/api/background-tasks/{task_id}/output`、POST `/api/background-tasks/{task_id}/stop`。HTTP校验格式，manager检查记录存在、工作区/会话归属及分页游标；输出通过受管记录读取，不接受任意日志路径。不存在/跨范围统一task_not_found（404），非法游标task_cursor_invalid（422），清理未确认task_cleanup_unconfirmed（409），意外操作失败background_tasks_unavailable（503）；日志缺失返回200和output_available=false/output_unavailable=true，保留状态及内存尾部。

| 边界 | 行为 |
| --- | --- |
| 审批拒绝/子Agent后台请求 | 零命令启动，沿原配对反馈 |
| 交接前根run停止或外部取消 | 当前调用负责请求停止并等必要收尾，不遗留无人持有worker |
| 后台交接后根run停止/失败/最终回复 | 后台继续；原run必要保存失败仍按原合同阻断，但任务查询/停止继续可用，不重放命令 |
| 切换会话/关浏览器/断SSE | 后端仍活着则继续，不因切会话停止 |
| 删除有活动/未收尾任务的会话 | 应用业务入口拒绝，提示先停止；须与后台准入原子仲裁 |
| 删除仅有终态任务的会话 | 可删除并释放其注册表元数据，不自动删除输出文件 |
| 正常退出nyt | 关闭新任务准入，同时停止根run和后台操作，等待spawn/进程/consumer/writer，再关闭应用资源 |
| 关闭无法确认必要收尾 | 报告具体清理失败，不声称全停；不得先关闭仍被writer使用的对象 |
| 崩溃或强杀后重开 | 不恢复内存注册表、不接管旧PID、不自动重跑，日志仍可手动查阅 |

会话删除准入在管理器与现有deletion_guard间使用明确顺序，等待进程/磁盘IO时不得持会话Lock。task_stop重复调用合并到同一停止操作，已结束任务返回当前终态；停止受理不等于停止完成。

## 9. 分片及每片退出条件

| 分片 | 交付 | 退出条件 |
| --- | --- | --- |
| 125-01 执行期限和单次归档 | 新上限解析接入Bash/PowerShell；删除工作区总额度及真实消费者/空对象；原前台行为保持 | 完整超时矩阵、严格校验、跨调用文件累加不再触发总限、MCP/aitest/前台归档回归；无后台能力完成声明 |
| 125-02 显式后台闭环 | 应用管理器、独立worker/控制、启动确认、64MiB日志、三个模型工具、关闭/删除/手动清理保护 | 起服务→工具先回ID→同会话下一run可用→查询/停止；拒绝零启动、同调用去重、跨会话拒绝、额度/启动/IO/关闭竞态；临时/持久App均接线 |
| 125-03 前台自动转换 | 120秒策略、同进程交接、独立总deadline、前后台输出连续性与缺口标记 | T<F/T=F/T>F/无期限、自然退出/停止竞争、spawn中取消、子Agent不转换；不再使用原run控制/进度 |
| 125-04 下一轮状态提示 | root通知快照/接受回执、SystemMessage及预算/预览、终态提示版本 | 完成时模型调用数不增加；下一用户run带通知；失败不丢、预览不消费、子/审批/压缩不消费、限额剩余不丢 |
| 125-05 CLI/Web管理 | 应用总览/有界输出/停止、鉴权路由、CLI命令、前端独立面板与完成提示 | idle/busy均可管理、跨会话总览、普通stop与task_stop区分、断连/轮询取消、浅深/窄屏/无数据空白、无模型请求 |
| 125-06 组合与交付验证 | 主服务＋后续请求、后台测试＋下一轮查询、性能脚本与平台矩阵 | 正式图/临时SQLite/Web/CLI闭环、所有新旧测试/构建、源码外候选安装、耗时/内存/收尾报告；原机证据分平台记 |

顺序依赖01→02→03→04→05→06。每片先TDD红灯，最小实现，回归后检查实际接线/迁移清理；不一次把所有片实现后才确认进程拥有权。具体文件与测试见[实施文件地图](125-background-shell-file-map.md)。

## 10. 验证与性能证据

正常场景：原Shell许可→管理器登记/启动→后台回执→原run保存并释放→下一run请求健康接口→任务输出/停止→runner确认收尾。失败场景：审批拒绝零启动；启动失败登记安全终态；后台日志触限只停止该任务；必要保存失败不否认已启动进程；收尾失败保留lost及可追查句柄。

确定性测试使用Event、可控clock和状态同步，不用50ms等极紧sleep窗口。真实本机进程用sys.executable启动本地临时服务/有界产出器，清理测试进程；模型使用固定假模型，不请求真实供应商或用户业务服务。

性能脚本拟为`scripts/benchmark_background_tasks.py`：短命令基线与有后台安静服务/持续输出任务下同会话及跨会话请求对比；提交/转换新增耗时、聊天P50/P95/最大值、事件循环延迟、内存峰值、实际日志字节、停止/关闭确认时间。至少3轮，输出JSON和环境/样本量，不把命令本身耗时称为管理器开销，不预先承诺零影响。

```bash
.venv/bin/python -m pytest -q tests/test_shell_task_settings.py tests/test_background_tasks.py tests/test_background_promotion.py tests/test_background_notices.py tests/test_background_task_lifecycle.py
.venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
git diff --check
```

上方后台任务测试除04的test_background_notices.py外已落地；01/02的实际验收见第12/13节，03见第14节。每片定向测试名按地图扩展，最终全量命令保持。沿现有标准wheel构建/安装验收入口，不改依赖/版本、不发布。无DB schema变更，回退不迁移用户库；已有日志继续可读，先停进程再换回代码，不能把代码回退当外部副作用回滚。

## 11. 参照和证据边界

- [Pi固定源码](https://github.com/earendil-works/pi/blob/a69bef789bc95abf0acee16f7b4660b70b650bb9/packages/coding-agent/src/core/tools/bash.ts)：schema timeout→execute→ops.exec→计时器/killProcessTree；本体不提供后台Bash。
- [pi-bg-bash固定源码](https://github.com/gvanderclay/pi-bg-bash/tree/349484c209f78fa3040deffe3842ef912a950d8b/src)：注册表/日志/转换可借鉴；自动唤醒、会话切换清理、POSIX wrapper不照搬。
- [AgentX固定DockerService](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f/AgentX/src/main/java/org/xhy/infrastructure/docker/DockerService.java)：execCreate→execStart→awaitCompletion辅助路径，未证明同等后台Shell合同，不因有容器就视为已有后台管理。
- [Claude Code工具合同](https://code.claude.com/docs/en/tools-reference#background-commands)：任务可跨最终回复、模型timeout与配置并存；本项目总deadline不重置，以用户确认规则为准。
- [OpenAI官方命令接口](https://learn.chatgpt.com/docs/app-server#command-execution)：进程身份与turn可分离，不推断所有客户端内部实现。
- [LangChain官方Shell middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in#shell-tool)及本机1.4.2源码before_agent/after_agent管理持久Shell资源；与现有逐命令审批/跨run后台拥有权不同，不替换当前工具。[Agent Server后台run](https://docs.langchain.com/langsmith/background-run)是图运行服务，未采用。

源代码/官方合同仅为设计依据，不是本轮产品或参考项目运行证明。首次调研报告中的128MiB总量、仅未传timeout才自动转换、自动清理等早期建议均由本Spec中的最新用户决定覆盖。


## 12. 125-01实际实现与验证（2026-10-21）

01收尾时的版本快照：当时02—06尚未实施，Shell仍等待前台执行完成，没有background参数/任务工具/120秒自动转换；当前02结果以第13节为准。

当前主链：load_settings → AppSettings.shell_tasks → CLI/Web create_builtin_tools → 双Shell工厂 → build_shell_tool.execute内require_tool_permit → 一处计算有效timeout → ShellAdapter.build_request → execute_process_request → HostProcessRunner.run/watch/settle → 原结果/错误回填。同步工具入口调用同一个async实现，不另算期限。

正常例：配置C=120，模型M=600，传给ProcessRequest的是120秒；配置未配/0且模型省略/null/0时为None。模型参数和应用上限的24组双Shell组合直接断言runner收到的请求。Find/Grep原期限仍沿原共享schema；AITest未接shell_tasks，保留原单次期限及归档容量。

失败例：负数/浮点/布尔/字符串/无穷应用上限在load_settings阶段报错，尚未构建模型或启动命令；不静默回退成无限。真实Bash以配置1秒运行printf EARLY; sleep 60，模型timeout省略、0和600均到期返回timed out并保留EARLY输出；既有真实图超时/停止/必要收尾测试继续通过。

归档主链：Shell/AITest的execute_process_request或MCP format_result → 同一个ShellOutputArchive → 本调用raw/text累计 → 单次max_archive_bytes（MCP为max_response_bytes）→ 返回完整性/路径。删除ToolOutputSettings/导出、output_settings参数传递、_workspace_limit与每次写入历史目录扫描；MCP初装配和运行中refresh都已迁移。保留目录所有权、普通文件/链接检查、安全独占创建、手动预览精确快照及确认；清理快照仍扫描目录，写入不扫描历史文件。

不同调用分别拥有额度，历史文件继续可读/可清理；前台容量触限仍继续执行并保留尾部。MCP历史结果的workspace_limit原因仍由presentation.ARCHIVE_REASONS消费，因此保留其历史投影，不再产生新的总量截断。旧公开[tool_output].max_workspace_archive_bytes只在配置输入边界告警忽略，其他未知键拒绝；不保留空配置类或虚设执行限制。实际用户设置、用户数据库及原日志均未修改，不自动删除文件。

按TDD先看到预期红灯：test_shell_task_settings与test_shell_output为40 failed/11 passed，缺少新配置、旧总额度截断第二次归档、写入扫描历史均被触发。迁移过程中修正测试调用删除旧参数后的残留括号；新搜索探针按现有Grep发现/扫描两次进程调用断言。未放宽业务断言、skip失败用例或修改conftest。

本机实际结果：

| 命令/检查 | 结果 |
| --- | --- |
| PATH补现有固定native资源后，pytest -q tests/test_shell_task_settings.py tests/test_shell_settings.py tests/test_shell_output.py tests/test_archive_directory_compatibility.py --tb=short | 74 passed in 4.84s |
| PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short | 4071 passed, 1 warning in 285.24s；含仓库既有research探针，Beta提示来自docs/research/mcp00/clients.py |
| npm --prefix frontend test -- --run | 49文件，395 passed |
| npm --prefix frontend run build | TypeScript/Vite通过；保留既有chunk大小提示，0构建错误 |
| compileall / pip check / AST与500行 / git diff --check | 通过；175产品模块＋39脚本，配置样例实际加载通过 |
| scripts.build_release --target macos-arm64及twine check | 标准Mac ARM64 wheel构建/包检查通过；175产品Python及全部前端资源与当前源码字节一致 |
| scripts.verify_wheel_install --wheel-dir候选目录 | 源码外干净安装通过，含真实CLI/Web、原生工具、审批/SQLite/备份恢复及MCP，0真实供应商请求 |

证据目录：build/spec125-01-h7w6v9zn，含backend.log、frontend.log、frontend-build.log、wheel-build.log、package-check.json及install.json/install.log。此次新增45项行为测试；实际配置上限的PowerShell为合成runner请求证据，真实Bash/安装为本机Mac证据，不声明Windows/Linux原机、真实模型或业务质量已验证。未新增依赖/DB schema，未改版本，未提交/push/发布。并行学习/长期记忆文档保留。

01收尾时安排的下一片为125-02，按第9节及文件地图实施显式后台：先验证应用拥有权和跨run查询/停止，再接日志上限、退出/删除/清理门禁与真实服务用例。当前仅说明准备做法，未自动开工。

## 13. 125-02实际实现与验收（2026-10-21）

用户已授权本片实现；03—06尚未实施。现已接入显式background=true、三个任务工具及应用管理接口。当前仍无120秒自动转换、下一轮自动状态段或独立后台任务面板；可在下一次用户触发的Agent运行中主动查询任务ID。

当前正常主链：

```text
CLI/Web配置 → 内建双Shell与task_list/task_output/task_stop
SessionService构造一个BackgroundProcessManager
  → 创建/恢复AgentSession时传窄任务能力
  → SessionExecution.arun/审批恢复的RunObservation.bind
  → 现有RunServices → make_invocation_context._process_tasks
  → Shell.execute：当前许可/root检查、执行上限解析、ProcessRequest
  → manager.submit：核对归属/准入/重复调用、复制请求和策略、登记并交接
  → 空Context中的独立worker → HostProcessRunner.run
  → ProcessRequest.on_started确认进程已创建 → running
  → ProcessCapture.record/finish_owned → 有界尾部＋单任务归档
原工具返回task_id/starting → 原run保存/释放；后台继续
下一run任务工具 → 当前许可/可信会话与工作区/root → 同一manager查询或停止
```

ProcessRequest.on_started只供manager确认真实spawn，不代表服务已就绪；03将另补与执行期限共用的计时原点。worker只持有冻结的argv/cwd/env、执行器、策略、独立控制及采集对象，不保存runtime/原RunServices/stream_writer。创建worker显式使用空contextvars.Context，普通根run结束、失败或停止不会停止已交接任务。task_id不是权限证明，模型查询/停止核对session/workspace；应用Python管理接口可做本实例总览。

Task工具输入采用严格schema，当前许可还核对tool_name和实际参数；list/output默认Allow，stop默认Ask。Shell审批目标显示[background]，一次性审批仍绑定原args/工作区/环境；本会话记住批准按background区分，旧前台命令批准不会自动授权后台。子Agent后台请求在工具实际入口和manager入口均拒绝。原Bash/PowerShell仍默认opt-in，新增三个控制工具进入既有活动工具集合。

正常实测：临时与持久应用均由真实Agent图调用Bash起本地HTTP服务，工具返回ID、模型最终回复后root busy释放；读取stdout中的动态端口并取得HTTP 200/healthy；第二轮task_list/task_output取得同一任务与输出，随后停止并确认资源收尾。Task完成后模型请求计数不增加，同一原工具调用只产生一份配对结果。真实Shell还核对应用1秒执行上限优先于模型600秒、每任务100字节配置真正触发output_limit、下一root运行停止不影响原后台，以及退出应用后PID不存在。

失败与必要收尾主链：

- 审批拒绝/子请求/交接前stop：零后台启动。manager重复(run_id,tool_call_id)返回既有操作，请求/策略身份不符拒绝；env/argv在登记后外部变化不影响已冻结操作。
- 单任务日志触限/磁盘保存失败：只请求自身独立控制，继续经runner确认进程/管道/consumer，再结束writer；终态分别是output_limit/archive_error。进程已知退出后生成text视图触限只标归档不完整，保留exited/exit_code。
- ProcessCleanupError有未结束consumer：Capture继续持有writer，consumer完成后独立收尾；记录lost、cleanup_confirmed=false。文件关闭报错也不得冒充清理成功，已知exit_code仍保留。
- 原run必要保存失败：原会话按原合同阻断，manager已接管的进程仍可通过应用接口查询/停止，不重放命令。
- 应用关闭：先关闭任务准入并请求根run/后台停止，等待spawn/runner/capture收尾，再关闭其他应用资源。无法确认的任务导致明确关闭失败，不声称已全部停止；不按旧PID推测接管。

维护准入：SessionPersistence.adelete在实际业务入口持有会话删除保留标志，有活动/未收尾任务则返回session_background_tasks_active；Web沿原鉴权及错误映射，现有删除对话框提示先停止。成功删除终态会话仅释放注册表记录，不删日志。CLI原:outputs clear迁到cli_async→应用preview/clear；SessionService在短锁中检查同工作区root并取得manager清理保留标志，释放锁后才做磁盘IO。提交后台任务/新root准入核对同一保留标志，清理失败也释放它。手动删除日志后task_output返回output_unavailable，保留内存尾部、状态和已知退出码。

新增manager、工具与生命周期测试；未修改conftest。先确认缺失manager的5项红灯，再确认“前台记住批准可授权后台”和“文件关闭失败仍报finished”的单项红灯并修源代码。工具集合/新增可选字段的旧精确断言同步扩展，原顺序/权限/输出断言保留。首轮全量4103通过、1项结构失败：SessionService模块禁止yield，新清理contextmanager违反此契约；改为直接返回已取得资源的ExitStack，并补异常释放与新run竞争测试，原结构测试保持。

最终实际结果：

| 命令/检查 | 结果 |
| --- | --- |
| pytest -q tests/test_native_session_lifecycle.py tests/test_background_tasks.py tests/test_background_tools.py tests/test_background_task_lifecycle.py tests/test_shell_lifecycle.py --tb=short | 42 passed in 3.59s |
| PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short | **4105 passed, 1 warning in 289.68s**；既有research代码的LangChain Beta提示 |
| npm --prefix frontend test -- --run | **49文件，396 passed** |
| npm --prefix frontend run build | TypeScript/Vite通过，0错误；保留既有chunk大小提示 |
| compileall / pip check / AST与500行 / git diff --check | 通过，177产品模块＋39脚本；两个Shell设置字段的样例实际加载通过 |
| scripts.build_release --target macos-arm64 / twine check / 字节核对 | 最终标准wheel通过，177产品Python及全部前端资源与当前源码一致 |
| scripts.verify_wheel_install --wheel-dir dist-final | 源码外干净安装通过，含CLI/Web、审批/SQLite/备份恢复及MCP，0真实供应商请求 |

本片新增34项后端行为测试和1项前端删除提示场景，旧精确集合/schema断言按新增工具/字段迁移；未放宽原安全/顺序/输出断言。证据目录build/spec125-02-40dez8gp，首轮backend.log/候选保留；最终使用backend-final.log、dist-final、package-check-final.json及install-final.json。真实进程/HTTP服务/安装证据为本机Mac Bash，Windows/PowerShell原机、跨重启进程恢复、真实模型质量及06组合性能不由此推定。前台自动转换尚未接入。未改真实用户配置/数据库/依赖/版本，未提交/push/发布；并行学习和长期记忆成果保留。

03具体准备顺序见[文件地图](125-background-shell-file-map.md#3-125-03前台自动转换)，本轮未实施。


## 14. 125-03实际实现与验收（2026-10-21）

正常链：root Shell许可通过 → execute_managed_foreground → manager.foreground/_register → 空Context的_drive → 唯一HostProcessRunner.run。runner在spawn前用同一个origin计算总deadline并通知候选；ProcessProtocol.process_exited直接标记已退出，避免清理较慢时误转后台。F到达后，候选先检查自然退出/停止/T，受保护地在线程中调整同一个capture的归档策略，再次检查后无await提交交接，返回一次task_id。原worker/进程/deadline/归档stem及累计字节不变。

等待期间的on_progress仅由foreground等待栈持有；worker不持有runtime、审批许可或publisher。交接之后直接返回，不flush旧进度。未转换命令继续使用原前台格式和shell_completed诊断；正常收尾的隐藏候选从注册表移除，避免普通短命令在应用内积累记录。已交接的后台记录继续保留本次应用生命周期。

失败链：取消或观察控制信号在交接前到达 → 独立操作收到stop → 等待已启动的promotion磁盘IO及worker收尾 → 保持原控制/异常身份。ProcessCleanupError不转成普通ToolMessage；收尾未确认的候选提升为可见lost记录，继续阻止删除/归档清理。启动OSError仍转换为原有安全错误提示。异常传出后移除私有异常对象，避免traceback间接持有原runtime；只保留安全类型与清理事实。失败后的副作用不自动重放。

归档将历史不完整原因与当前阻塞原因分开：前台已丢弃区间继续标command_limit，但后台较大配额允许在同一文件追加；已写量达到较小后台配额则只停止，不截短旧文件。子Agent/独立前台消费者/AITest保留execute_process_request；显式background沿02路径不使用F。前台审批target显示配置的自动转换规则，仍绑定原参数、工作区和执行环境。

配置示例增加foreground_wait_seconds=120；没有修改真实settings/.env、依赖、用户库或数据库schema。本片不含04通知/05面板/06组合性能。实际结果如下。


TDD先确认ProcessCapture.promote及manager.foreground缺失的红灯；新增事件同步用例复现“交接IO期间日志触限丢停止信号”“异常对象间接持有原run traceback”“短命令未带实际输出收尾进度”，再逐项修正源代码并保留断言。新增22项转换/生命周期及6项配置测试，共28项。background_app测试helper只增加wait_seconds以走真实AppSettings装配，未修改conftest。旧配置未知键测试迁移到真实typo，并新增正数/类型校验；审批target的Host/Web精确断言同步包含自动规则和完整原命令，不删除权限/工作区/实际结果检查。首个完成的全量为4132通过、1项旧Web目标断言失败；迁移后全量重新执行，结果如下。

| 命令/检查 | 最终实际结果 |
| --- | --- |
| 10个Shell/后台/进度测试文件的定向pytest | **156 passed in20.71s**，focused-complete.log |
| Web/Host审批及转换/生命周期四文件 | **58 passed in13.95s**，approval-promotion-final.log |
| PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short | **4133 passed, 1 warning in298.60s**；既有research探针LangChain Beta提示 |
| npm --prefix frontend test -- --run | **49文件，396 passed** |
| npm --prefix frontend run build | TypeScript/Vite通过，0错误，保留既有chunk大小提示 |
| compileall / pip check / AST与500行 / git diff --check | 通过，177产品＋39脚本；两份TOML样例实际load_settings通过 |
| scripts.build_release --target macos-arm64 / Twine / 包内容 | 标准wheel通过，177产品Python及前端字节一致 |
| scripts.verify_wheel_install --wheel-dir dist-accepted | 源码外干净安装通过：CLI/Web/native/审批/SQLite/备份恢复/MCP，0供应商请求 |

证据目录build/spec125-03-n628mz3f，最终使用backend-final-green.log、dist-accepted、package-check-accepted.json、install-accepted.json及verification.json。中间TDD与中断的回归/候选不作为最终验收，最终包/全量测试之后产品源码哈希再次核对一致。真实进程记录只启动一次、PID/累计归档不变，临时及持久会话第二轮可查；未交接短命令不积累隐藏操作，未确认收尾可见lost。真实证据为Mac Bash；PowerShell为共享请求/角色/策略合成证据，不代表Windows原机。未请求真实模型、未验证06组合负载性能/业务质量或用户掌握，未改真实配置/用户库/依赖/版本，未提交/push/发布。

唯一下一片125-04：按第7节及文件地图，接每会话终态快照/版本回执、下一新用户root运行的background_tasks系统段、预算/预览/ContextPanel标签；成功模型响应后确认，失败/取消保留，无自动唤醒，不把stdout提升到系统角色。04当前仅说明方案，未实施；CLI/Web独立管理入口留给05。


## 15. 125-04实际实现与验收（2026-10-21）

新增配置已获用户明确授权：shell_tasks.max_notice_items=20、max_notice_bytes=8192，严格正整数；实际应用设置经CLI/Web的临时/持久构建器传到SessionService持有的manager。只改样例与产品配置结构，不修改用户真实设置。

正常链：_drive确认终态/foreground暴露lost → manager登记会话待提示版本 → 新用户Human输入的SessionExecution.arun取得不可变BackgroundNoticeSnapshot → ActiveRun随本轮资源保存并冻结root提示段 → 现有RunServices/invocation_context只向匹配root运行传快照 → ToolSetMiddleware/compose_model_prompt组装background_tasks → prepare_request精确核对实际系统段及版本 → _accept在所有响应门禁后同步确认该快照。运行中的任务不反复提示；快照冻结后才结束的任务留待下轮。确认仅表示模型调用已成功接受输入，不承诺业务结果已分析或最终整轮已持久化。

失败链：请求未发出/响应拒绝/模型异常/停止或取消 → 不确认；下一新用户输入仍可获取同一待提示。回执普通故障记录safe error_type并保留待提示，不覆盖有效模型响应；控制异常维持原控制效果。回执只确认本快照task_id/version，之后新终态不丢失；重复确认幂等；会话删除同步移除其待提示，不动磁盘日志。对不可见的已完成前台操作不生成通知。

数据边界：快照只含可信session/workspace/run作用域、不可变(task_id,version)元组及应用生成text。正文只含task_id/status/exit_code/termination和固定查询说明，不含stdout、命令、env或原异常正文；不写聊天/checkpoint，不新增DB表。manager只持有ShellTaskSettings及待提示版本；ActiveRun/RunServices沿用现有瞬态对象，不新增ContextVar/通知服务/回调总线。

root/child共享项目资源时resolve_prompt_parts显式传agent_path并过滤background_tasks；审批恢复和手动压缩不获取快照。上下文预览只读manager同一快照算法，加入相同段/预算，若准备期间版本发生变化则降为unavailable；不会确认。context_usage的parts合法集合/估算/持久快照与前端ContextPanel中文标签同步，压缩已沿同一compose_model_prompt，不另建估算器。

实施文件：shell_task_settings、tools/background_tasks（快照与窄协议）、application/background_tasks、临时/持久构建器/session_service、session_state/agent_session/session_execution/run_events/session_context、agent_runtime的run_services/invocation_context/session_port/tool_execution/toolset/request_preparation/call_observation/context_usage，以及ContextPanel标签/测试、配置样例/当前文档。现有一参数测试lambda按内部新agent_path合同迁移，保留原断言；不改conftest。测试先红灯，再最小实现；05管理界面/06组合性能不在本片。


实现收尾：manager的_queue_notice只为显式/转换后的终态以及可见lost登记一次版本，快照按本会话/工作区过滤、完成顺序选取完整条目；不会读capture或归档。版本确认只删除本快照中仍匹配的待提示项，重复确认幂等，后续新终态和任务记录保留。新用户运行在项目资源IO前冻结快照，模型请求准备按parts字符边界核对实际系统段，所有响应门禁通过且未停止才确认。预览在最后一次checkpoint读取后核对版本，变化则unavailable。

TDD先观察4项缺失快照/配置默认值红灯（另10项非法未知键拒绝已通过），随后真实图提示缺失红灯；新增两个事件同步用例分别复现资源IO期间通知混入本轮和最后checkpoint读取期间预览版本失效，再修源代码。新增33后端/1前端：不可变/作用域/条数和字节额度/后续终态/重复及错误版本确认、无日志IO/删除释放、两种应用真实后台完成→下一用户轮、模型失败/响应拒绝/用户停止/回执故障、未实际带系统段不确认、无用户输入/预算过小、审批恢复/子Agent/手动与自动压缩、两种冻结时序和前端中文预算标签。复用background_app helper增加notice_items/notice_bytes来验证真实配置装配；两个旧测试lambda和一个现用probe迁移到agent_path参数，保留原断言，不改conftest。

| 命令/检查 | 最终实际结果 |
| --- | --- |
| 14个后台/上下文/作用域/生命周期文件定向pytest | **131 passed in15.08s** |
| PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short | **4166 passed, 1 warning in299.60s**；既有research代码的LangChain Beta提示 |
| npm --prefix frontend test -- --run | **49文件，397 passed** |
| npm --prefix frontend run build | TypeScript/Vite通过，0错误，既有chunk提示保持 |
| compileall / pip check / AST与500行 / git diff --check | 通过，177产品＋39脚本；两份样例实际load_settings通过 |
| scripts.build_release --target macos-arm64 / Twine / 字节核对 | 标准wheel通过，177产品Python及全部前端资源与当前源码一致 |
| scripts.verify_wheel_install --wheel-dir dist | 源码外干净安装通过，CLI/Web/native/审批/SQLite/备份恢复/MCP，0供应商请求 |

证据build/spec125-04-780n000n/{backend.log,focused.log,frontend.log,dist,install.json,verification.json}，初始红灯与两条时序复现日志一并保留。最终包/全量之后源码哈希核对一致。未新增DB schema/依赖，未改真实用户配置/用户库/版本，未提交/push/发布。保留并行学习和长期记忆成果，不推定真实模型质量/用户掌握/Windows原机或06组合性能。纯Python状态交付和本机Mac Bash真实进程已验证；现用真实模型probe仅迁移调用合同并编译，未向供应商运行。

04收尾时的下一片为125-05；现已按下节完成独立管理，06组合负载/性能验证尚未实施。

## 16. 125-05实际实现与验收（2026-10-21）

具体接口、当前正常/失败链与文件范围见[05实施合同](125-05-background-management.md)。CLI新增独立解析模块，经原AsyncCliApplication桥接三个应用方法，:tasks [before]/:task output ID/:task stop ID在根任务运行中仍可用；cli.py为498行，无第二个执行管理器。Web独立路由继承Bearer/Host/Origin鉴权，受信工作区下传应用/manager；未知及跨范围任务404，非法游标422，无绑定工作区不暴露其他工作区任务。清理未确认409，读/控制意外错误503并记录安全类型，日志缺失保留尾部/状态；输出按Web帧预算缩短HTTP尾部，不改变磁盘记录。

列表只读内存元数据，按整个筛选集合计算活动数、终态版本及元数据指纹；分页不改变活动数，也不消费04通知。前端App持有任务hook，切聊天保留面板；每3秒有限元数据查询，打开面板并选中才读有界尾部。隐藏/卸载取消读取，失败显示未知及重试；停止失败按task_id显示，旧选择响应不串任务。完成状态提示不创建run，不借旧SSE；沿原CSS Modules/浅深色tokens和440px侧栏，375px覆盖布局。

新增18项后端、9项前端（含App接线）先红后绿：Web缺路由8红、CLI缺入口8红、App缺按钮1红，后续帧预算/终端安全/归档状态/延迟停止归属补齐。前端局部client mock补入独立后台元数据响应，保留旧工作区标签的鉴权/调用次数/错误断言；虚拟计时在挂载前启用，未放宽断言、skip或修改公共测试基础设施。

实际验证：

- PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short：**4184 passed, 1原有LangChain Beta warning in303.50s**。
- npm --prefix frontend test -- --run：**50文件406 passed**；npm --prefix frontend run build：TS/Vite通过、328模块，保留分包体积提示，0错误。
- 179个Python产品文件、39个脚本的AST/compile，120份前端源文件及上述Python文件500行，pip check及git diff --check通过。
- 标准scripts.build_release macos-arm64、Twine、179份Python/4个前端产物逐字节核对、scripts.verify_wheel_install源外干净安装通过。最终wheel SHA256为29e3d170c4b2c5a98f01b39dafd94f55844ac3f6eb374e0aeb8150021718aaf4，未发布。
- 实际临时工作区/库及Python HTTP服务经浏览器查看/停止，活动数1→0、用户停止及完成提示可见；1280px/375px、浅/深色截图已保存，375px页面scrollWidth=375；页面警告/错误读取为空。临时页/服务已关闭，两个任务cleanup_confirmed=true，model_calls=0/provider_requests=0。Playwright扩展未连接，改用内置浏览器验收，未安装或修改浏览器扩展。

证据目录build/spec125-05-1jyfxqh7：backend.log、frontend.log、frontend-build.log、dist-final/、twine-final.log、install-final.json、verification.json、ui-result.json和ui-{desktop,mobile}-{light,dark}.png。dist/及install.json为停止错误归属修正前的中间产物，最终交付以带final的文件为准。

未改真实配置/用户库/依赖/版本、未提交/push/发布，保留01—04及并行学习/调研修改。测试与本机UI证据不代表真实供应商业务效果、Windows/Linux原机或性能保证；未记录用户掌握。

05收尾时下一片为125-06；当前已按下节完成组合/性能与统一侧栏，历史分片测试/截图保持对应时点。

## 17. 125-06实际验收与Spec125收尾（2026-10-21）

[06合同、真实链、性能与证据](125-06-background-acceptance.md#实际接线与验收2026-10-21)：正式图服务跨轮健康请求/停止、后台完成通知/正文、原日志保留及重开不接管已验；共享release_background_smoke已接源外安装worker，验证错误显式上抛。前端以一个图标打开ActivitySidebar，统一子智能体/后台任务/上下文页签，旧上下文图标仍直达；三个重复外框/关闭props及旧入口已迁移删除，子详情/上下文统计/后台hook的真实消费者保留。页签选择、停止、异步旧响应、键盘和焦点/375px浅深色以实际临时数据验收。

新增8后端/3前端；最终4192后端（310.25s、1原有Beta warning）、50文件409前端、TS/Vite、179产品Python+41脚本/122前端结构及四目标标准wheel/Twine/同源码字节通过。Mac ARM64源码外安装已实际跑新增后台流程；Linux/Windows本轮仅构建，原机和PowerShell实跑未验。证据build/spec125-06-ybcllk_s/{backend.log,frontend.log,frontend-build.log,verification.json,dist-final/,twine.log,install-final.json,ui-result.json,benchmark-isolated.json}。

单独复测macOS26.4 ARM64，每组3×40轮固定模型/真实持久化：无后台单轮P50/P95 43.35/90.22ms；静默任务42.78/89.18ms；持续输出43.46/91.61ms；持续输出且正常3秒读尾部43.05/92.67ms；50ms管理读取压测44.20/99.33ms。正常读尾部P95差2.44ms，不证明零开销/显著性或所有机器保证。1000条合成终态记录查询P95 2.73ms；64MiB真实洪峰触限收尾2.59s；活动进程关闭3次最大42.30ms。第一轮并行回归/构建负载的尾部波动保留于benchmark.json，实际参数/原始分布/范围见06说明。

临时浏览页/服务均已关闭，所有样例任务清理确认，UI固定模型3次/真实供应商0；不代表真实模型业务质量或用户掌握。真实配置/用户库/依赖/版本未改，保留并行学习和调研文件，未提交/push/发布。Spec125无下一本机实现片；下一候选按future-work为已结束子Agent实例续跑，先讨论，不自动实施。
