# Spec091：工具运行进度与有界输出

日期：2026-10-16。状态：**091A/091B均已实施并验证；090C结果见Spec090，当前下一片审阅092A。** 接续[090](090-tool-wait-stop-and-author-contract.md)，按091A后端、091B Web/CLI实施。

## 1. 要解决的问题

目前有tool_call_started/finished，但started表示进入dispatch观察边界，可能还在串行排队；工具运行中不向现有Web/CLI传递真实进度。用户无法分辨排队、扫描文件、进程持续输出及正在停止收尾。

目标是显示真实阶段、已有计数、最后活动时间和Shell有界尾部，使用户有依据发起现有停止请求。进度不延长/缩短执行期限，不证明工具已停止、不替代最终ToolMessage、checkpoint或run终局。

参考事实：Pi ToolDefinition的onUpdate可传中间结果。当前LangGraph ToolRuntime.stream_writer是**可调用对象**，异步工具发送custom事件，外层astream显式订阅custom。本机已验证工具体未结束时外层收到custom。091A回归又复现LangGraph1.2.12同步custom订阅在max_concurrency=1时等待者占满同一executor，工具零进入并挂住（原090A停止回归＋有界faulthandler探针）；不能通过调高并发或增加线程池绕过。因此同步适配在已有dispatch入口用request.override/runtime的dataclasses.replace注入本run接收函数到官方stream_writer字段，同步stream保持updates/values；异步仍原custom通道。两种入口的工具API、载荷校验和Publisher相同，无新回调注册表、ContextVar或并发语义变化。

## 2. 调用链与具体数据

```text
模型提出工具调用 → 前置权限/整批审批
 → dispatch开始（原事件）
 → 批次报告queued；取得位置后进入授权/准备阶段
 → 工具require_tool_permit → ToolProgress(runtime, settings)
 → 真正IO/循环改变进度 → runtime.stream_writer(payload)
 → async_message_observation接收custom；同步由dispatch注入的writer交给同一观察器
 → 验证kind/版本/类型/预算和当前调用身份
 → 原RunEventPublisher.publish('tool_progress', ToolProgressData)
 → Web RunDelivery快照/SSE、CLI订阅者
 → 原tool_call_finished与ToolMessage/必要保存分别收尾
```

工具负责编制可观察事实；session_id/run_id及事件sequence/timestamp由当前可信Publisher添加。模型不能用工具参数选择另一run。不给工具新增持久记录、网络队列或第二个ContextVar。

示意工具发送的custom，不是模型消息：

```json
{
  "kind": "naiyue.tool_progress", "version": 1,
  "batch_id": "assistant-12", "tool_call_id": "call-test", "tool_name": "bash",
  "activity": "running", "phase": "process_output",
  "completed": 18432, "total": null, "unit": "bytes",
  "text": "8 passed, 2 remaining\n"
}
```

completed为当前阶段累计已处理量，total未知时为null，不推算百分比；phase改变可从零计数。text是**最新完整尾部快照**，客户端替换、不追加。只能传字符串/受限数字，不允许任意对象、bytes、图片或artifact；自定义工具不能把异常repr/凭证当进度。

activity取queued/preparing/running/stopping/settling；phase最长64字符，unit只支持bytes/lines/entries/matches或null；completed/total为非负整数或null，已知total时completed不得大于total。batch_id取当前tool_execution.batch_id，即所属AI消息ID，防止同run后续批次复用call_id时串状态。queued来自调度层，running和具体阶段来自实际工具/IO；调用尚未进入工具体时显示preparing，不宣称命令已启动。终局仍由原finished决定，不新增另一套成功/失败协议。

## 3. 091A新增和修改的类/方法

| 文件 | 具体改动和消费者 |
|---|---|
| `tools/progress.py`（新增） | ToolProgress(runtime, settings).report(...)；绑定当前runtime.tool_call_id，单调时钟节流、UTF-8有界尾部、数值校验、阶段变化/最后实际活动刷新，调用stream_writer。由八内置工具及作者示例使用；普通观察失败记录安全类型、每调用有界诊断，不能改变工具结果。 |
| `tools/settings.py` | 新ToolProgressSettings：enabled=true、min_interval_milliseconds=200、max_text_bytes=8192。ToolSettings.progress装配，loader识别[progress]；数值非法按已有普通预算规则日志+默认，权限解析仍启动失败。数值为本Spec工程建议。 |
| `tools/builtin.py`和八工具工厂 | 显式传同一启动settings.progress；不修改模型schema，不给每工具重复增加三个相同配置key。独立调用工厂仍有默认设置。 |
| `agent_runtime/stream_events.py`、`events.py` | 新ToolProgressData（上述字段、无kind/version重复）及tool_progress载荷注册；沿原EventData/序列化，默认日志不记录text。 |
| `application/run_events.py`、`agent_runtime/run_services.py`、`invocation_context.py` | RunObservation创建一个ToolProgressObserver；作为已有RunServices的tool_progress可选字段传递，按原publisher/session/run身份检查投影为_tool_progress_observer。随run释放，无新ContextVar。 |
| `application/session_service.py`及四个应用装配入口 | 可选progress_settings参数透传启动settings.progress，到普通/审批恢复run的RunObservation；保留原调用默认，不新增运行中配置读取。 |
| `agent_runtime/tool_execution/batch.py` | 在dispatch内的等待/放行位置发对应进度；不以tool_call_started等同真正运行。不改变原started/finished配对及boundary。 |
| `agent_runtime/tool_execution/observation.py` | 在已有模块内新增ToolProgressObserver.begin_dispatch(request)、report_dispatch(...)、accept_custom(payload)、finish(batch_id,call_id)，不另建运行层tool_progress.py。dispatch开始登记(batch_id,call_id,name)，发布finished之前finish关闭该键；sync/async、正常与异常终局共用每run对象及校验实现，RLock保护身份/终结标记。GraphBubbleUp仍原样传播。 |
| `agent_runtime/message_observation.py`、`async_message_observation.py` | 同步stream仍updates/values，避免已复现的单线程executor饥饿；异步增加custom，传给context中已创建的observer。保留原messages/updates/values/interrupt处理及主异常优先。异步只接收当前根命名空间，086以后由各子自己的观察器投影来源。 |
| `tools/read.py`、`images.py` | 读取字节/行、图片处理阶段；不向进度事件发送文件正文或base64。 |
| `tools/write.py`、`edit.py`、`edit_matching.py` | 准备/扫描/匹配/临时写入/提交/清理阶段及已处理字节；不发送待写正文、旧文件正文、diff内容。 |
| `tools/ls.py`、`find.py`、`grep.py` | 枚举条目、已匹配/已处理数量和阶段；被权限跳过的文件不泄露正文；不能把发现候选数叫做已完整扫描文件数。 |
| `tools/shell.py` | 共享Bash/PowerShell实现，在consume完成已有OutputAccumulator/归档record后报告实际观察字节及当前规范化tail。不在阻塞线程里另开事件循环，不改HostProcessRunner的背压/清理所有权。 |
| `docs/tool-authoring.md`、`examples/tool-runtime.example.toml` | 带注释[progress]及自定义工具示例，写明SDK/工具须主动报告实际活动。只改样例，不改真实用户配置。 |

观察器只保存当前批的调用身份、终结标记和接收校验必需的有限状态，不保存展示用输出尾部/时间视图；下一批替换该索引，旧batch_id的迟到数据直接丢弃，不累计整轮进度日志。Shell尾部由已有OutputAccumulator产生，Web/CLI的最新展示快照由091B消费者持有。登记dispatch及终结边界并绑定当前AI批次，不能仅凭payload里一个call_id接受任意数据。对已终结call、错误名称/版本、过大/非法载荷丢弃并有界记录安全诊断。工具作者是可信代码，载荷校验不被宣传为隔离恶意Python代码的安全边界。

分层约定：tools/progress只依赖框架ToolRuntime和工具包设置，以普通custom载荷报告实际IO事实，不导入本项目agent_runtime/application、Publisher或事件数据类；运行层负责调度/流接收/事件归一化，不做文件IO、页面刷新或会话持久化。应用RunObservation创建每run观察器并经现有RunServices传入，属于装配职责，不把进度解析搬进应用层。模型接入层无本片变化。预算由应用启动设置装配到报告器及观察器，不在每次report时读取用户配置。

RunEventPublisher当前handlers是创建时快照，没有动态subscribe；本方案不新增该能力。直接沿已有RunObservation→RunServices→显式Runtime.context传递observer，比新增运行中订阅/包装Publisher更少一步。tools侧只调用框架stream_writer，不导入runtime；同步dispatch注入writer及异步流接收均交给同一observer，无需工具作者选择运输路径。observe_tool_progress共用于dispatch/调度/异步接收，隔离普通观察错误，但GraphBubbleUp/BaseException原样传播，不能直接套用会捕获GraphBubbleUp的普通Exception包装。

后端观察失败按已有observe合同隔离普通Exception；GraphBubbleUp/BaseException/停止信号不得吞。Publisher发送失败不能反向使文件提交重试或改变已确认成功。

## 4. 091B Web/CLI与数据消费

| 文件 | 修改 |
|---|---|
| `web/run_delivery.py` | DeliveredRun.observe合并tool_progress到已有tool_states；保存最新phase/activity/计数/text/last_activity_at，已终局记录不逆转。run结束时仍无终局的工具标unconfirmed。 |
| `web/app.py`、`application/cli.py` | 把实际启动加载的同一progress设置传给交付/终端消费者，避免独立默认值覆盖用户预算；不新增配置读取。 |
| `web/serialization.py` | dataclass投影同步验证新字段；先预算裁剪再做现有整帧检查，禁止附带任意custom对象。 |
| `application/cli_display.py` | 消费新事件，按tool_call_id更新阶段/计数及有界输出；非TTY按已有间隔降频，不每次输出整份历史。停止请求接受后显示停止中，收到真实终局才显示结束。 |
| `frontend/src/types.ts`、`events.ts`、`components/Conversation.tsx` | 扩展ToolState和事件分支，按run_id/sequence及batch_id防旧消息覆盖，phase只能绑定所属AI行；text替换；queued/准备与真实运行分开。 |
| `frontend/src/components/ToolCall.tsx`及现有样式 | 工具折叠行显示阶段、进入dispatch后的历时、距最后活动时长；展开显示真实计数和Shell尾部，保留参数/最终结果；复用现有停止按钮。 |
| `frontend/src/useConversation.ts` | 原停止请求接受状态驱动stopping，保留旧run结果不串入新run的规则。 |
| `frontend/src/App.tsx` | 把当前run身份与已接受停止状态传给原Conversation/ToolCall，不新增页面状态管理层。 |

091B接线细节：tool_states仍按call_id保留最新一份，增加batch_id（所属AI消息ID）、started_at/last_activity_at/finished_at及最新进度。started/finished事件没有batch_id，消费者从已有消息投影倒查最近包含该调用的AI行；第一条可信进度可补齐尚未观察到的批次身份。后续批次复用call_id时替换最新快照，旧AI行不再挂上新批进度，原最终消息仍展示历史结果。不新增按批次无限累计的展示仓库。非本run、旧序号、错批次和终局后进度不更新快照。Web快照增加stop_requested，重连也能显示停止中；仅接受停止不修改工具终局。CLI沿已有串行writer输出阶段/计数/最新尾部，以已有progress间隔合并同阶段更新，finished前补显最后快照，不新增刷新线程。

时间显示由浏览器/CLI当前时钟和事件时间派生，不给服务器增加伪心跳。长时间无新活动显示“距最后活动…”，不自动断言卡死；没有进度报告的自定义工具仍显示dispatch历时；界面使用“已历时”，避免把排队时间误称为纯执行时间。浏览器本地每秒刷新时间，终局后停止；CLI按实际事件输出当时的阶段/计数/历时，不新增刷新线程。节流不阻塞停止和finished；最后未发送的真实进度可在工具返回前flush，但终局后迟到custom必须被过滤。

原Web ring buffer继续有界，重连发现序号缺口走现有完整run快照恢复；快照包含每call最新进度，避免只能看到最近几条增量。配置的max_text_bytes上限不承诺整个历史可放进max_frame_bytes，最终仍受现有帧合同约束。进度不进模型上下文、会话checkpoint或Shell完整归档的重复副本。

## 5. 正常和失败示例

父批包含sequential的Bash A和Read B：审批时两者零执行；批准后A运行、B显示排队。A的真实stdout更新尾部和观察字节，30秒无输出仅显示最后活动距今30秒。用户停止后A显示停止/收尾；runner确认进程与消费者结束后返回部分输出的error结果，B收到未执行结果，最后保存确认才显示run停止。

Edit提交期间停止：显示提交/收尾；实际提交成功保留success。进度发布/Web听众抛异常不能把success变成error，也不能提前释放目标busy。若必要清理失败，仍走原ProcessCleanupError/必要保存失败合同，进度没有豁免权。

## 6. 验证与实施门槛

091A先补`tests/test_tool_progress.py`、`tests/test_tool_progress_integration.py`、`tests/test_tool_progress_builtins.py`：工具完成前真实create_agent流收到进度；sync/async、批次queued/running、节流/Unicode尾部、身份/非法载荷/迟到事件、观察故障、取消清理、进度不进ToolMessage/checkpoint。八内置各验证至少一个真实接线点，不能只测reporter。

091B补`tests/test_web_tool_progress.py`及现有CLI事件测试，前端`tool-events.test.ts`、`ToolCall.test.tsx`覆盖快照重连、旧run、终局不倒退、停止中/未确认及HTML文本转义。用事件同步让工具等待，断言界面在结束前收到数据；计时用可控时钟。

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q tests/test_tool_progress.py tests/test_tool_progress_integration.py tests/test_tool_progress_builtins.py
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q tests/test_web_tool_progress.py tests/test_cli_event_display.py tests/test_cli_event_integration.py tests/test_observation_isolation.py
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

三份091A测试已接入；Grep/Find依赖PATH中的rg/fd，上例显式加入本机已安装位置，不修改应用发现规则或下载工具。091B Web专项现已接入，结果见第8节。全量回归、compileall、依赖/≤500行/diff检查按现有要求执行；没有真实Windows环境不将PowerShell模拟进度当原机验收。

## 7. 091A实施与验收记录

本片仅新增一个生产文件tools/progress.py（ToolProgress），运行观察器放进已有tool_execution/observation.py；只记录本批身份及终结状态，无展示尾部/时间快照。ToolProgressData沿原事件类型登记、Publisher及序列化，RunObservation→RunServices→invocation_context按run身份传递。SessionService新增可选progress_settings，由四个应用构建入口传入启动预算，普通/审批恢复run共用同一设置，不新增配置读取/ContextVar/生命周期回调注册表。

工具工厂新增可选progress_settings，模型schema未加字段；create_builtin_tools显式传同一settings.progress。八内置在实际IO/阶段接线，内部函数直接传同一报告器；文件/图片/Edit不发正文或base64，Shell在已有Accumulator/归档record之后发送观察字节及规范化尾部。控制异常后停用writer并原样上抛，防止finally清理阶段再次调用失败writer。原文件/进程清理所有权及真实结果合同不变。

首批报告器/设置/作用域/未结束接收测试先19 failed in11.51s，最小实现后19 passed in0.95s；内置首批9失败，其中6例确实缺报告，Ls/Find/Grep三例还暴露新测试手工permit遗漏tool_name等字段，已按实际许可形状补齐，不修改权限实现或放宽断言。接线后首轮相关67 passed in1.44s。后补同步流、批次排队、接收器故障、四种应用启动设置、控制传播与清理不重试等边界；两类控制writer重试均先复现，再修对应断开逻辑，不将其余追加覆盖冒称逐项TDD。

首次全量发现同步custom/max_concurrency=1阻塞，借原090A停止测试及15秒有界faulthandler诊断复现，并从当前LangGraph源码确认共享executor饥饿。该次全量已按唯一日志句柄确认后终止，不将它计为通过。同步改由既有dispatch覆盖官方runtime.stream_writer，异步仍custom，同一ToolProgressObserver接收；原并发1/2停止回归及新“未结束即收到进度”通过，工具内config仍等于请求的max_concurrency。未增加线程池、后台任务或私有框架补丁，未升级依赖。同步message_observation.py最终无改动。

4份原测试共5例同步迁移获批的新事件合同：载荷类型总数27→28且显式检查ToolProgressData；只订阅run/call的用例仍检查原事件顺序/配对/内容/状态，精确序号计入新增queued/preparing及Read更新；消息探针精确事件总数18/22，主模型仍2次。没有删除/skip用例、放宽比较或保留旧事件平行入口。

最终新增三份进度测试共**41例**，含原文件停止/作用域/分层等定向命令**102 passed in3.07s**。全量命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1794 passed in51.78s**，完整输出/tmp/naiyue-091a-pytest.log。前端`npm --prefix frontend test -- --run` → **9文件27 passed**；`npm --prefix frontend run build` → **TypeScript/Vite186模块成功**。compileall/pip check、291个Python AST/107个生产文件≤500行/新测试≤500行、样例TOML/作者代码实际图调用/文档链接/diff检查通过。

作者示例实际图返回line_count=3并收到count_lines进度bytes=8，runtime不在公开schema。四种内存/持久化×sync/async应用均验证关闭进度设置实际生效且文件结果不变。原有脏工作保留，无暂存/提交/push、真实配置/数据库/依赖改动。此为091A收尾时点；091B当前结果见第8节，090C/092/086仍未实施。无真实模型请求或Windows原机证据。


## 8. 091B实施与验收记录

本片没有新增生产模块。Web在原DeliveredRun内合并最新进度，按当前run/call/batch/名称及终结状态校验；started/finished从已有AI消息投影定位批次，progress补全尚未观察到的批次身份。只留每call_id最新一份，复用ID时替换，Conversation按AI行ID绑定，不给历史行挂上新批次进度。finished合并保留最新阶段/计数/尾部；run终局及生产任务异常退出均把缺少工具终局的记录标unconfirmed。普通观察失败不改变工具结果，停止接受只设置stop_requested，不伪造finished。

web/app.py把实际启动progress预算传给RunDelivery→DeliveredRun，serialization先生成类型受限、UTF-8有界的副本，再沿原整帧限制/有界SSE交付。独立消费者默认沿原工具预算。没有新路由、数据库字段、进度checkpoint或轮询接口。CLI同样由原入口显式传预算，按已有间隔合并同阶段更新，结束前补显最新一份，转义终端控制字符；序号单调去重，无新线程/刷新任务。

前端原types/events增加平铺字段及tool_progress/control_accepted分支，ToolCall沿原CSS Modules显示阶段、真实计数、最新尾部及本地时间，unknown total不生成百分比。App→Conversation只传当前已接受停止的run身份，useConversation用原回执/快照恢复停止中，过期watch回调和旧run回执不能覆盖当前状态。终局清理计时器、最终ToolMessage保持原结果。没有新展示仓库或通用处理层。

实际链路：工具stream_writer → 同一运行观察器/Publisher → DeliveredRun.observe/_observe_tool → view及原SSE → applyEvent → Conversation按batch_id匹配AI行 → ToolCall。正常结果走原finished/ToolMessage；执行异常或缺少终局走原故障/未确认展示，不由进度决定结果。整个历时从dispatch开始，可能包含排队，文案明确为“已历时”。

首轮后端/CLI新增行为为6 failed/15 passed，前端4 failed/4 passed，随后接线转绿。HTTP接线追加测试先发现测试工具报告器与9字节观察预算不一致（按真实配置补齐测试报告器），再确认Web交付未使用已加载预算（8192而非9），最小装配修改后转绿。工具停止测试的RunFinishedData初版遗漏stop_mode，已按当前真实合同修正测试构造；未修改原终局合同。追加“旧序号”和“生产任务缺少run_finished”先2失败，再修对应消费边界。首次全量1801通过/2失败，暴露新消费者不必要依赖完整WebSettings，改为实际装配入口直接传progress预算；原两个观察隔离用例及断言保持，未用兼容分支或修改旧测试掩盖。

最终新增Python9例（新增test_web_tool_progress.py六例、原CLI测试新增三例），前端新增六例。HTTP用真实持久化应用/图和确定性等待验证工具未完成即能查询进度、缓冲溢出后快照恢复、最终结果/模型上下文不混入进度。相关观察隔离/Web/CLI定向45 passed in1.29s；最终全量`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1803 passed in52.89s**（/tmp/naiyue-091b-pytest.log）。前端`npm --prefix frontend test -- --run` → **9文件33 passed**（/tmp/naiyue-091b-frontend.log），`npm --prefix frontend run build` → **TypeScript/Vite186模块成功**，CSS10.95kB/JS367.35kB。compileall、pip check、292 Python AST、107生产文件≤500行及diff检查通过。

离线原生浏览器验收使用临时SQLite/正式HTTP/SSE/构建后的页面和实际运行的测试工具，无真实模型请求：1280×900与375×812均已查看，375px文档宽度等于视口，长中英文文本无横向溢出，script文字保持转义。第一项运行/第二项排队、重新同步保留进度、接受停止后仍等待测试工具4秒必要收尾、实际错误结果/未执行/已停止全链路可见。截图/tmp/naiyue-091b-stopping.jpg、/tmp/naiyue-091b-mobile.jpg、/tmp/naiyue-091b-terminal.jpg。重连时观察到原responseBytes的stream_reader_detach_failed取消诊断，不是零日志；未出现正文脚本执行。Playwright外部扩展未连接，改用应用内浏览器完成，未安装扩展。临时服务/标签已关闭，视口已恢复。没有真实Windows/Linux原机或真实模型新证据，不以此宣称平台验收完成。

当前主链没有新增运行机制，091A的运行层/工具报告器未改；旧tool_states按start/finish整条覆盖的分支已被合并逻辑替代，CLI旧集合去重改成单调序号，不保留平行入口。真实配置、依赖、用户数据库未改，原有脏工作保留，未暂存/提交/push。此为091B收尾时点；090C当前结果见Spec090第8节，最新下一步以handoff为准。
