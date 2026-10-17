# Spec085-12：共享进程启动、采集与收尾（已实施）

版本边界：本片保存原实施时点；[085-13](085-13-search-tools.md)已接Grep/Find流式rg/fd并移除旧搜索入口，Bash同步薄桥迁移为run_async_sync，正常consumer等待与取消协调以13为准。当前下一步是[085-14双Shell](085-14-dual-shell.md)审阅。

接续085-11。用户已授权本片实现，验证后讲085-13。本文只细化总Spec第13节的进程生命周期。先接现有Bash作为真实消费者，13搜索、14双Shell继续复用，不创建只有测试使用的进程服务。

## 当前链路和本片目标

当前`create_session_bash_tool→require_tool_permit→_run_host_command`中同时拥有解释器/环境准备、Popen、两条daemon读取线程、wait/轮询停止、归档和结果分类。`_kill_group`依赖POSIX killpg；父退出与输出EOF混在join超时判断，普通超时/用户停止也抛BashOutcomeUnknown，经运行层特判终止。后续rg/fd/PowerShell不能各复制一遍。

本片提取一个宿主进程运行器：输入已经准备好的argv/cwd/env和本次执行许可的环境标识，输出退出/超时/停止/完整性事实。它不匹配权限、不解析Shell、不解释rg/fd行协议，不自行调用模型或重新执行命令。Bash的原输入command/timeout及审批保持；环境继承、解释器发现、合并输出/2000行64KiB和PowerShell放14完成。

## 类、方法和文件

| 文件 | 类/方法及消费者 |
|---|---|
| 新`tools/processes.py` | `ProcessRequest`保存argv/cwd/env/timeout_seconds/merge_output/eof_policy/execution_environment；`ProcessOutcome`保存returncode、termination（exited/timeout/user_stop）、output_complete及收尾事实；`ProcessCleanupError`只表示必要回收未确认，保留主异常/cause和安全事实。`HostProcessRunner.run(request, consume, control)`是异步唯一执行算法；同步薄桥`run_process_sync`调用同一算法。 |
| 新`tools/process_protocol.py` | `ProcessProtocol`用公开SubprocessProtocol接connection_made、pipe_data_received、process_exited、pipe_connection_lost、connection_lost。回调只记录事实/把字节送入有界队列，不写盘；高水位暂停公开pipe transport，消费后恢复。stdout/stderr协议按request选择合并或分流。 |
| `tools/settings.py`及两份配置样例 | 接`ProcessSettings`和`ToolSettings.process`：cleanup_timeout_seconds=5、shell_exit_quiet_milliseconds=100、max_pending_output_bytes=1MiB。沿启动快照/额度非法诊断回退，不读取项目.env。 |
| `tools/bash.py` | 删除`_run_host_command/_capture/_kill_group/_unknown_with_output`及BashOutcomeUnknown。`_prepare`验证permit，准备原argv/cwd/env、尾部和现有BashOutputArchive消费者；`bash/abash`分别走薄桥/直接await；`_feedback`格式化已确认结果。同步桥不在已有eventloop中嵌套asyncio.run，必要时用本次工作线程拥有自己的loop。 |
| `tools/builtin.py`及必要工厂调用方 | 传同一验证后的process settings。runner可由工厂闭包持有，运行状态全部是run内局部变量；无当前需求不新增RunServices字段、ContextVar或App控制对象。 |
| `agent_runtime/tool_execution/feedback.py`、`agent_runtime/errors.py`及实际类型消费者 | 清理失败显式上抛，不交普通ToolError；普通启动/非零/已确认收尾的超时转具体工具反馈。替换BashOutcomeUnknown的真实故障消费者和旧测试，保留异常身份、控制、必要保存/阻断断言；删除无消费者旧类型/特判，不留双执行算法。 |

consume是当前Bash已有尾部/归档的实际消费者：async消费者处理字节，需要磁盘IO时在受管工作任务中执行并由runner确认settle，不能把错误留给后台日志。13会以同一位置接rg/fd行解析；本片不实现未来解析器或新归档格式。每个生产文件≤500行。

队列预算按尚未消费完成的字节计算，触及高水位暂停两流，降到半水位恢复。公开transport的一次读取已经交付的整块字节仍需保留，因此允许一个读取回调块的有限超量；不声称能在回调收字节前精确截断内存。消费者未确认结束时不并发finish归档；清理故障只带安全收尾事实，不携带输出或环境。

当前BashOutcomeUnknown生产消费者已核对为tools/bash.py、agent_runtime/errors.py、tool_execution/feedback.py、tool_execution/observation.py、application/cli.py、application/session_service.py；这些会随新故障边界同步迁移。对应现有9份测试为test_active_stop_bash、test_application_run_events、test_classifier_contract、test_cli_event_display、test_cli_event_integration、test_host_bash、test_layered_failure_contract、test_tool_subscription_boundaries、test_turn_failure_boundaries；保留所代表的控制/故障合同，不通过删测试保留旧Unknown混合语义。

实施核对还发现两处没有旧类型名字的真实消费者：test_tool_package_boundaries对旧私有runner打补丁、test_tool_error_feedback按bash名字保护所有RuntimeError。前者改测共享runner收到同一control和原参数；后者保留ProcessCleanupError身份上抛，另测普通Bash意外错误同样转换。旧混合语义类型/私有runner和Bash名字特判在生产、测试、scripts中均无残余；历史Spec记录保留版本边界。

## 具体数据和正常调用

以下是拟定数据示意，不是真实执行输出：

```python
ProcessRequest(
    argv=['/resolved/bash', '-c', 'python -m pytest -q'],
    cwd=workspace_root,
    env=prepared_environment,  # 工具代码准备，不由模型任意传入
    timeout_seconds=60,
    merge_output=False,       # 本片保留现有两流反馈；14再改Shell合并
    eof_policy='shell',       # 后续搜索用complete
    execution_environment={'kind': 'host', 'platform': 'macos'},
)
```

1. 既有权限准备/整批审批完成后，Bash入口验证当前permit；人工等待不占执行timeout。
2. Bash准备request和输出消费者，runner建立本次执行deadline，检查停止，再调用公开`loop.subprocess_exec`。
3. 创建期间即使收到取消，也接管最终创建出的进程；不能留下“spawn await已取消、子进程无人收尾”的窗口。
4. 协议持续交付字节，消费者更新原尾部和归档。进程退出与管道EOF分别记录，不先wait完成才读输出，也不用communicate收无限内容。
5. 确认父退出、输出完整性及本端资源关闭，结束归档，返回ProcessOutcome；Bash把结果格式化给ToolMessage，原模型继续。

例如退出码0、两流自然EOF，Outcome为`termination='exited', returncode=0, output_complete=True`。非零也先确认收尾，工具给具体错误和保留输出；runner不把stderr含Error当成失败，也不解析命令语义。

## 超时、停止和管道边界

- 超时或用户停止且父仍运行：POSIX独立session/进程组终止并回收直接子；Windows调用taskkill /T /F并确认其结果/父回收/本端关闭。不是沙箱，不承诺主动脱组等任意后代都结束。
- cleanup共用一份5s截止时间，各步骤不能重新获5s。完成必要收尾后，普通超时给具体ToolException，提示已有副作用须核对，不自动重试；用户停止让原run_control安全边界停止后续模型/排队工具。真正CancelledError在清理后保持原信号。
- 父正常退出但后代仍持输出管道：Shell在最后输出后等短静默，可主动关闭并标不完整，持续输出仍受剩余执行deadline约束。搜索的complete策略必须有完整结构化EOF或明确失败，不能套Shell静默就报搜索完成。
- 消费/采集出错仍进入统一收尾；必要资源未回收抛ProcessCleanupError，由运行层保留真实故障效果。保留主异常和清理事实，不用普通错误消息遮住进程仍未确认结束的问题。

## 分片边界与验收

本片即让Bash使用新runner并迁移其旧Unknown分类，以满足存在/实质/接线三级验证；14负责Shell产品层配置、环境继承、发现和双Shell输出。旧归档格式/clear/read仍有消费者，继续保留。本片不引入沙箱Operation、JobObject、后台任务平台、持久Shell或进程自动重试。

TDD先覆盖真实sys.executable子进程的正常/非零、大量双流、分片/乱码字节、父退出但后代持管道、超时、启动中取消、活动停止、消费者故障、回压和统一收尾截止；以事件/显式握手同步，不靠50ms竞争窗口。再接Bash同步/异步正式图，检查同ID、整批审批前零启动、停止不进入下一次模型、错误回填及ProcessCleanupError不被转换，原输出归档/配额回读和必要保存保护不回归。Windows分支的本机单元/假transport证据与Windows实际进程证据分开；全项目Windows可用性仍由15/WIN1验收。

## 实际主链与错误边界

正常：`create_builtin_tools(settings.process)→create_session_bash_tool`创建同一个StructuredTool，带sync func和native coroutine。既有权限/整批HITL/ExecutionAuthorization之后，`abash→_prepare→require_tool_permit→ProcessRequest→HostProcessRunner.run→loop.subprocess_exec→ProcessProtocol.deliver→consume→to_thread(record)`；消费者处理的仍是原4096B双尾部和原归档。exit、EOF、队列消费和connection_lost分别确认，`_feedback→ToolNode`生成原ID/name结果，模型继续。同步入口只经`run_process_sync`调用同一算法，没有第二套Popen/读取线程。

超时例：`printf EARLY; sleep 60`且timeout=1，经原审批后启动。`_watch`返回timeout，`_settle`在一份cleanup截止内终止组、确认父回收、本端管道/consumer/transport关闭；`_feedback`生成带EARLY、exit_code、output_complete和原归档路径的具体ToolException，ToolError回填同ID error，下一次模型照常处理。工具不自动重试，提示核对已发生副作用；这不再自动阻断会话。

用户停止：同样先收尾并生成该调用的停止错误；排队工具由原batch guard生成not_executed/user_stop，原RunControl/安全边界使run_stopped，不再调用下一次模型。取消信号在确认收尾后保留原CancelledError和参数。必要收尾故障则ProcessCleanupError放行：RuntimeErrorPolicy沿既有tool_outcome_unknown事件分类，工具观察outcome_unknown，SessionService标manual_review_required，CLI只展示原安全提示；不把异常正文/环境/输出放入诊断。

失败时ProcessCleanupError.facts只保存布尔收尾事实；__cause__保留原主异常。若consumer未settle，内部consumer_task仅用于最终归档关闭，不进消息/事件/持久数据。归档不得在写入尚未结束时并发finish。5秒是协调器的总等待预算，不能强制终止阻塞中的Python磁盘写线程；同步桥/事件循环关闭仍可能等待已开始的磁盘IO，不能宣称任意宿主IO都有5秒硬停止保证。

## 验证结果（2026-10-16）

- 首次真实红灯：新进程模块不存在；Bash接线前分别缺少HostProcessRunner/原生异步入口、实际超时仍抛旧Unknown。新增三份测试共26例，另加两例sync/async普通Bash意外错误转换；没有修改conftest、skip或弱化控制/权限断言。
- 生命周期真实子进程：退出0/7、双流大量字节/回压、分片及非法UTF-8、合流、超时、用户停止、外部取消、创建中取消、消费者故障、父已退出但后代持管道的shell/complete差别。未settle消费者在同一收尾期限内报CleanupError并保留取消cause，释放握手后可继续确认消费者结束。
- 额外红灯/修复：取消窗口重复终止同一组、关闭管道故障被第二次关闭错误覆盖、回压回调故障停在事件循环而未进入runner，均先复现再修；不通过放宽异常/资源断言处理。
- Bash真实sync/async Agent图：原审批前零启动、两条串行批准后停止第一条且第二条不启动、原ID消息配对、无下一次模型；原生runner在图eventloop线程直接执行。普通超时模型继续、不标manual review；CleanupError保留原异常身份、故障事件/保存阻断/CLI安全提示。既有归档、额度、回读/清理、非零和环境合同回归保留。
- 定向8份测试：**100 passed in11.03s**（`/tmp/naiyue-08512-focused-final.log`）；最终`.venv/bin/python -m pytest -q`：**1514 passed in41.77s**（`/tmp/naiyue-08512-full.log`）。
- 前端`npm test -- --run`：**9文件27 passed**；`npm run build`：TypeScript及Vite **186模块**成功。compileall/pip check通过，当前配置经实际loader+内置注册校验，未来样例TOML解析通过。

证据是macOS真实本地进程/临时文件、假模型/正式图与现有保存故障注入；Windows taskkill参数、返回码和helper关闭仅为假transport分支测试，没有Windows原机或Linux实机、远端模型、完整沙箱证据。真实环境/模型设置及依赖未改；未提交/push。下一步[085-13搜索审阅稿](085-13-search-tools.md)，不自动实施。
