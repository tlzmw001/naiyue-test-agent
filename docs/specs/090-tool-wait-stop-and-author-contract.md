# Spec090：工具排队、协作停止与作者约定

日期：2026-10-16。状态：**090A/B/C均已实施并验证；091A/B已完成，下一片审阅092A只读历史。**

本轮设计按顺序拆为八片：090A排队 → 090B文件停止与作者约定 → [091A进度后端、091B进度界面](091-tool-progress.md) → 090C可选执行期限 → [092A历史读取、092B中断后续聊、092C重启待审批](092-session-restart-recovery.md)。每片先讲当前/目标调用链、写失败测试、实施、验证，再介绍下一片；不一次改完。

## 1. 已确认的目标和边界

- 取消正常串行排队的固定60秒期限；前序工具未完成不等于调度失败。
- 仍按批次调度：本批含sequential工具则整批按调用顺序执行，否则受本批max_concurrency限制。
- 整批前置审批期间工具零执行；不增加全树总并发、文件等待队列或后台工具。
- Write/Edit同真实目标冲突继续返回busy。目标占用到已开始IO和清理实际settle后释放。
- 文件工具在可协作的位置响应当前RunControl；正在执行的系统调用、Pillow和任意Python线程不能承诺即时终止。
- 自定义工具沿用ToolSpec和ToolRuntime，不增加必须实现的timeout/cancel/cleanup三回调，也不把“注册成功”解释为行为正确。
- 090A只取消排队期限，暂不修改Shell/搜索执行期限。用户随后确认090C：Bash/PowerShell/Grep/Find默认不限时，支持timeout参数，省略或0不限时，正数设置本次执行期限。模型请求、解释器探测和必要清理期限与执行期限分别处理。

参考及事实边界见[能力盘点](../research/tool-system-capability-audit-2026-10-16.md)。Pi a69bef789的Agent循环按工具执行完成推进，文件修改队列等待前序完成；它也依赖执行器配合signal。AgentX 3fcfa0e中所核对的工具循环没有证明任意工具可硬终止。本项目选择busy而非Pi文件队列，是本次已确认的范围选择。

## 2. 当前调用链与缺口

```text
权限准备 → 官方整批HITL（可能暂停）
 → ToolBatchMiddleware.after_model/aafter_model建立本run计划
 → wrap_tool_call/awrap_tool_call发布dispatch开始
 → _SequentialPlan/AsyncBatchPlan等待本call的位置
 → 执行许可 → 工具 → ToolError → 配对ToolMessage
 → 后续模型或停止边界 → checkpoint/会话元数据确认
```

090实施前：同步Condition和异步Condition等待均用60秒截止。A还在正常运行，B也会超时并把计划标失败。异步取得位置后检查_run_control；同步分支缺少对应检查。Read/Write/Edit及图片内部没有完整的中途检查；Ls已有协作停止，搜索与Shell已有进程停止协议。以上缺口现由090A/B补齐，实施记录见第6、7节；不把旧缺口当作当前状态。

### 090A：修改类、方法和文件

| 现有文件 | 修改及实际消费者 |
|---|---|
| `agent_runtime/tool_execution/batch.py` | 删除_WAIT_ENV、wait_seconds读取/校验/字段及仅为期限存在的import；修改_SequentialPlan.__init__/execute和ToolBatchMiddleware建计划调用。_dispatch_tool_call与_adispatch_tool_call均在真正取得位置后检查当前RunControl。 |
| `agent_runtime/tool_execution/async_batch.py` | AsyncBatchPlan删除wait_seconds参数/字段和包围Condition的asyncio.timeout；保留Condition、Semaphore、failed/position、异常唤醒及取消传播。 |
| `tests/test_tool_batch_scheduling.py`、`tests/test_async_tool_batch.py` | 将旧期限测试迁成“前序仍活跃时后序持续等待，收到完成/失败/停止后才推进”；保留计划缺失、重复ID、异常身份和顺序断言。 |
| `tests/test_active_stop.py`及相关审批/失败测试 | 补sync/async取得位置后停止的同IDnot_executed、零工具动作和无后续模型断言。 |
| 当前说明/脚本中的旧环境变量消费者 | 先rg核验后删除已失效配置说明/探针参数，不保留兼容读取。 |

不新增调度器、每文件锁队列或统一工具执行deadline。计划失败/未登记/重复执行仍是ToolBatchSchedulingError；普通ToolException转换为error结果后，原批次允许继续的规则保持。

正常例子：A为运行测试的sequential工具，B为Read。A超过原60秒仍有真实活动，B持续排队；A返回并settle后B取得位置，检查停止，再执行。实际测试用事件控制完成，不等待真实60秒。

停止例子：用户在A期间停止。A按自身合同收尾；B取得位置时发现control.requested，生成原call_id的stop_tool_message，工具体零调用。不能为了立即显示B取消而让它越过A执行或提前释放资源。A若不配合、仍未settle，B仍等待；091展示这一事实。

### 090B：文件中途停止与作者约定

新增一个小模块`tools/control.py`，供Read/图片/Write/Edit至少四个实际消费者使用：

```python
def check_tool_stop(control, feedback: str) -> None:
    # control为现有runtime.context['_run_control']，无新ContextVar。
    # requested时抛带已准备具体提示的ToolException，否则返回None。
```

它不决定权限、不清理别人持有的资源、不调用Task.cancel。单个工具仍决定检查位置和准确提示。该共同操作实际重复于多个长循环，才抽出此函数；不再增加控制对象或新异常类。

Write当前父目录阶段有except ToolException再包装为WRITE_PARENT_CHANGED；090B必须收窄该捕获范围到真实目录IO，把协作停止检查放在对应捕获外，避免用户停止被误写成“父目录改变”。其余工具也沿实际except边界核对，不能仅插入check后就认为具体提示保留。该点用专门回归检查停止提示、父目录已创建事实和原文件字节。

| 文件/方法 | 新检查位置与数据传递 |
|---|---|
| `tools/read.py`：read → _read | 从ToolRuntime取control，内部直接传参；开始、分页跳过/长行的每个既有chunk前后、构造结果前检查。停止时本次读取不给半段正文，ToolException说明读取未完成。 |
| `tools/images.py`：read_image/prepare_image_blocks | 同一control直接传递；读取、识别/解码、resize、编码的阶段之间检查。不给半个图片内容块；当前阶段中的Pillow调用必须等返回。 |
| `tools/write.py`：write → _write | 检查于目标占用前、父目录逐层创建之间、临时文件写入/flush/fsync前后、最后权限/目标复核后且_replace_temp调用前。原文件提交前停止；可能已创建父目录，反馈不得说毫无副作用。 |
| `tools/edit.py`：_edit/_scan_target/_copy_to_temp | 扫描和复制既有64KiB循环、计划准备前后、diff完成后、flush/fsync前后及_replace_temp前检查；control显式传入内部函数。 |
| `tools/edit_matching.py`：_source_lines/scan_normalized_matches/prepare_edit_plan | 精确/归一化各遍扫描均在chunk边界检查；保留现有预算和超预算只限制模糊匹配的规则。 |
| `tools/ls.py` | 保留已有“部分结果+停止/不完整信息”合同，仅核对开始、枚举及结束检查；不为了形式一致改成丢弃已有结果。 |
| `tools/registry.py` | 登记门槛保持；补准确docstring/文档引用，不新增生命周期证明字段或工具级强杀能力。 |
| `docs/tool-authoring.md`（新增） | 真实注册示例、许可/停止/进度/IO超时/资源所有权及确定性测试要求；091补进度示例。 |

内部参数增加不改变模型可见schema及现有公开工厂参数。控制字段仍是框架注入，禁止暴露到自定义工具公开输入。修改前搜索内部测试/脚本实际调用，全部迁移，不保留只供旧测试使用的包装。

### Write/Edit的提交分界

```text
_reserve_target取得真实目标busy占用
 → scan/prepare/temp写入：检查停止，可放弃
 → 校验目标/权限/冲突 → 最后一次停止检查
 → _replace_temp开始：等待提交实际返回，不再改写为“没有修改”
 → 已确认成功返回成功结果；真实未知仍按现有具体ToolException说明核查
 → finally清理临时文件/句柄 → 确认settle → 释放busy
```

失败例子：Edit复制到临时文件一半时用户停止。抛具体ToolException，finally删除临时文件，原文件字节不变，busy释放后后续操作可进入。此error结果由现有ToolError配对；当前RunControl仍requested，停止边界防止再发模型请求。

竞争例子：ReplaceFileW/os.replace已经进入后收到停止。实际替换成功就保留成功ToolMessage，run随后停止；替换失败/未知保留原错误合同。不把必要fsync/清理取消成“成功停止”，不自动重试提交。

## 3. 自定义工具的三层责任

| 层 | 实际保证 | 不保证的内容 |
|---|---|---|
| ToolRegistry结构校验 | BaseTool/名称/schema/注入隔离、execution_mode/side_effects、权限适配及结果通道有效 | 无法通过函数签名证明它会响应停止或释放网络连接 |
| 当前运行机制 | 批次排序/并发、执行许可、ToolError、RunControl、开始/结束事件、已启动IO settle | 不强杀任意Python线程，不自动替作者清理所有第三方SDK资源 |
| 作者代码、说明与测试 | 每项外部IO设置有限超时；循环检查现有control；自己创建的资源在finally/async with收尾；停止/失败后副作用说明准确 | 不能只写注释就声称已经正确实现，需相应行为测试 |

原生async工具应使用async SDK；同步SDK优先用其IO timeout。用wait_for/to_thread超时不能证明后台线程停止。asyncio.CancelledError等控制异常必须清理后原样传播，不能转换为普通ToolException；用户协作停止在明确的安全点反馈，沿当前run停止合同处理。

作者只关闭自己拥有的资源；借用App共享client不能每次调用都关闭。外部命令优先复用HostProcessRunner及其进程/管道/consumer收尾；不另外spawn不可追踪后台任务。普通失败抛准备好的ToolException，意外异常走统一反馈；机制故障、GraphBubbleUp与必要清理失败保持原上抛。

## 4. 验证及与其他模块的关系

090A先补失败测试，分别证明旧排队期限/同步放行后缺停止检查，再改代码。测试用Event、Condition状态、可控时钟/调度替身推进，不写sleep(60)；测试总超时只防测试挂死，不成为生产排队期限。

090B覆盖Read远offset/超长行、图片阶段、Edit精确/模糊多遍、提交前停止、提交后停止、父目录已创建、临时清理失败和同目标busy持有时间。检查真实文件字节、同ID结果、无多余模型调用、必要保存和原异常身份。不能仅mock check_tool_stop证明接线。

建议定向入口：

```bash
.venv/bin/python -m pytest -q tests/test_tool_batch_scheduling.py tests/test_async_tool_batch.py tests/test_active_stop.py tests/test_file_tool_stop.py
```

`tests/test_file_tool_stop.py`及`tests/test_file_tool_stop_integration.py`现已接入，090B结果见第7节。每片完成后执行当片回归、compileall、diff检查；提交前全量pytest及适用前端构建必须通过。

权限准备、同进程审批恢复不重复Auto、Windows真实目标比较和提交API、输出预算及模型接口均保持。086以后直接复用无排队期限和停止检查，不重复实现；兄弟Agent普通失败仍不广播根停止。Windows系统调用仍须原机验收，协作停止测试不提供沙箱保证。

## 5. 090C：用户已确认的独立执行期限切片

2026-10-16最新决定覆盖前轮“搜索暂保留有限默认”的候选：Bash/PowerShell/Grep/Find均默认不限时，模型公开输入支持可选timeout；省略或0不创建执行deadline，正数表示本次调用的秒数。不能省略参数后又偷偷继承旧60/20/15秒默认值。

该片涉及tools/schemas/settings及四个工具的参数、描述和工厂接线，以及search_protocol的check_search/stream_records、ProcessRequest和HostProcessRunner启动/监视逻辑；沿原停止、结果预算、归档和必要收尾，不添加后台任务。没有消费者的旧默认执行期限配置及样例同步清理，真实用户配置不擅改。timeout的单位/正数校验/配置上限在090C实施讲解时逐项展示，当前不通过扩090A提前实施。

090C安排在091B之后，确保默认不限时时有真实进度和可用停止。必须先红灯覆盖省略/0/正数、Grep/Find新参数及校验/审批/执行一致性、内部无遗留deadline、无输出/启动中停止、部分结果及清理故障；这一片不能以090A的排队测试代替验收。

### 5.1 091B完成后的具体实施方案（用户已确认并按此实施）

在已有schemas.py声明三类工具schema共用的ExecutionTimeout字段类型（非新类），由ShellInput/_GrepInput/_FindInput实际使用，描述/严格非负校验同源；四工具统一`timeout: int | None = None`，单位秒，严格整数；省略/null/0归一为内部None，负数、bool、小数和字符串走现有参数错误反馈。正数只给本次执行建立deadline；不把审批/串行排队计入本次执行期限。Grep从候选发现到各搜索分组共用一个deadline，不能每个rg子进程重新获得完整时长。

用户已确认同时去掉Shell的`max_timeout_seconds=300`限制及配置，因为0已允许无限执行，该字段不构成执行上限；避免用户明确传600秒反而报参数错误。删除Shell `default_timeout_seconds`、Grep/Find `timeout_seconds`等已无消费者的旧执行默认字段与两份样例说明，不保留兼容读取；真实用户配置不擅改。此处删除300秒限制已获本轮明确授权。输出/条目/归档预算不受影响，解释器probe、模型网络和必要cleanup仍保留各自已有期限。

| 现有文件 | 拟改动及当前消费者 |
|---|---|
| `tools/shell.py` | build_shell_tool的公开/执行schema及execute/execute_sync都接受0；ShellAdapter.build_request直接传归一化值，不再用`timeout or settings.default_timeout_seconds`。描述与参数错误提示同步。Bash/PowerShell继续复用该共享实现，解释器适配器无需另写计时逻辑。 |
| `tools/grep.py`、`find.py` | _GrepInput/_FindInput和两工厂公开schema增加timeout；sync/async函数逐级传到_GrepSearch.__init__/_find_files，在实际搜索开始只创建一次可选deadline。搜索错误/部分结果、权限复核、进度及停止保持现有分支。 |
| `tools/search_protocol.py` | check_search接受deadline=None并始终检查停止；stream_records在无限时把None传给ProcessRequest，有限时传当前剩余秒数。不能用无穷浮点数或过期默认值伪装无限。 |
| `tools/processes.py` | ProcessRequest.timeout_seconds允许None；HostProcessRunner.run/_watch在无deadline时只等待真实退出/停止/必要IO错误。新增类内启动等待方法，仍由run拥有原spawn task，监测启动中的停止与可选deadline，不能直接无限await spawn后忽略stop；_settle仍负责已获取/迟到句柄及有限清理，不创建后台脱管任务。 |
| `tools/settings.py`、`docs/examples/settings.example.toml`与`tool-runtime.example.toml` | 删除无消费者的默认执行期限及已确认删除的300秒字段/对应校验，保留probe_timeout_seconds/cleanup_timeout_seconds等。loader沿原普通预算提示合同，不新增实时配置读取。 |
| 测试与当前使用/作者说明 | 新执行期限用例覆盖四工具省略/0/正数、严格校验、分组共用剩余量；用可控时钟/Event推进超时，验证静默进程/启动中停止、真实进程收尾和部分结果。原有限默认/负数等测试按明确的新合同迁移，其余权限/配对/清理断言保留。 |

正常例：`bash(command="python -m pytest", timeout=0)`经过参数校验/权限/整批审批/排队后，build_request得到timeout_seconds=None，run不创建执行deadline；工具持续报真实进度，进程退出后按原归档/清理/ToolMessage/保存结束。失败例：`grep(pattern="TODO", timeout=30)`在整个搜索超过30秒后停止当前自有进程、确认收尾，保留原已授权部分结果及incomplete/timeout提示；不广播根stop，不抹除其他工具的真实成功。用户停止则始终沿原RunControl，清理不能被“无限执行”选项关闭。

090C不新增运行层/应用层对象、事件类型、审批协议或前端控件；091A/B的进度和停止界面直接复用。本轮仅实施090C，不提前实现092恢复。

## 6. 090A实施与验证结果

2026-10-16仅修改两个生产文件：batch.py删除等待配置/计时器及同步guard缺口，async_batch.py删除等待期限；无新生产模块、依赖或真实配置变化。内部计划构造参数同步迁移，生产/脚本无旧环境读取、wait_seconds或tool_batch_wait_failed分支。旧env只在回归测试和带版本边界的历史文档出现。

新增tests/test_tool_batch_wait_and_stop.py共8例；原test_tool_batch_scheduling迁移内部构造和旧env拒绝测试，后者改为4组旧值不影响装配。旧行为明确废除，未放宽其余参数/身份/顺序/审批断言。新例先**7 failed/1 passed**（精确复现sync/async期限和同步停止缺口），最小修改后四文件**39 passed in3.21s**。

全量命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1718 passed in51.86s**，完整输出/tmp/naiyue-090a-pytest.log。前端`npm --prefix frontend test -- --run` → **9文件27 passed**；`npm --prefix frontend run build` → **TypeScript/Vite186模块成功**。compileall/pip check通过；284个Python AST、105个生产文件≤500行通过；diff检查通过。

真实create_agent同步应用分别在max_concurrency=1/2验证：第一项开始后请求停止，busy保持到第一项settle，成功结果保留；第二项零执行、同ID user_stop错误/not_executed事件，单次模型请求、无遗留图任务、严格事件配对且无观察降级。异步已有相同停止用例及新增取消等待者唤醒回归通过。超时推进用可控时钟/计时器及Event，不等真实60秒；5秒只作为测试自身防挂保护，不进入生产。

090A收尾时未实施090B/090C/091/092/086、未发真实模型请求、未做Windows原机验收，未提交/push。090B现状见第7节。此为090A收尾时点，090C当前结果见第8节。

## 7. 090B实施与验证结果

新增tools/control.py的check_tool_stop，复用ToolException和现有RunControl；直接由read.py、images.py、write.py、edit.py、edit_matching.py调用，无新ContextVar/异常类/注册字段/依赖。工具公开schema和工厂参数未改。ToolRegistry仅补结构校验不证明生命周期行为的说明，作者约定见[tool-authoring](../tool-authoring.md)。Ls原实现未修改，继续保留授权部分结果和不完整信息。

实际正常链路：批次guard → 执行许可 → 工具从runtime.context取control → 内部函数直接传入 → IO/分块安全点检查 → 原成功结果/内容块/artifact → checkpoint/会话保存。旧测试三个_scan_target替身同步透传control，原冲突、顺序、审批、内容和状态断言全部保留，没有添加旧签名转发入口。

实际停止链路：Edit扫描/归一化/复制或Read读取阶段收到request → 下一安全点抛具体ToolException → with关闭句柄、finally清临时文件 → 目标占用释放 → ToolError产生同ID error → 当前RunControl防止再发模型 → stopped checkpoint与ready元数据。Write父目录捕获已收窄，停止不再改写为WRITE_PARENT_CHANGED，可能已创建目录的事实保留。最后目标复核后仍检查停止；_replace_temp进入后不再检查并改写结果，等待真实提交及收尾。

tests/test_file_tool_stop.py首批23例先**21 failed/2 passed in11.36s**；失败明确显示原实现继续IO/提交、未清临时准备；2个通过例为提交完成后保留成功。tests/test_file_tool_stop_integration.py首批3例先**3 failed in1.09s**，原实现返回success；最小实现后两文件26 passed in1.04s。后补真实IO错误+停止、清理失败、最后复核以及已进入提交等待/busy等边界，不将这些追加例冒称逐项TDD。

临时文件删除失败沿既有合同记录安全错误类型，保留主ToolException及原文件，可能遗留临时文件；测试显式确认该事实。真实fsync失败与停止并发保留IO反馈和原OSError cause。忙占用保持到句柄/删除尝试实际settle，已开始replace期间停止仍保留真实成功。文档首个Python示例已实际注册并经过正式图读取合成文件，返回line_count=3且runtime不在公开schema。

最终两份新测试共**35例**；初次定向文件/图片/编辑/Ls/主动停止/Windows提交回归256 passed in3.41s，追加边界也由最终全量覆盖。全量命令`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1753 passed in51.50s**，完整输出/tmp/naiyue-090b-pytest.log。前端`npm --prefix frontend test -- --run` → **9文件27 passed**；`npm --prefix frontend run build` → **TypeScript/Vite186模块成功**。compileall/pip check通过，287个Python AST、106个生产文件≤500行、新测试≤500行、文档链接及diff检查通过。原有脏工作保留，未暂存/提交/push、未改真实配置/依赖/工作区数据库。

此为090B收尾时点；091A/B已完成，090C结果见第8节，092/086仍未实施。无真实模型请求或Windows原机证据。


## 8. 090C实施与验收记录

仅修改七个现有生产模块：schemas.py/settings.py/shell.py/grep.py/find.py/search_protocol.py/processes.py；没有新增生产模块、线程、ContextVar、审批节点或前端控件。ExecutionTimeout为已有schemas模块内的共享字段类型，ShellInput/_GrepInput/_FindInput实际继承同一严格非负整数约束和说明；公开schema与执行schema继续由原build_tool_schemas产生，runtime不向模型公开。四工具省略/null/0归一为内部None，正整数指定本次秒数，无300秒上限。没有无穷浮点数、巨大默认期限或兼容读取。

ShellAdapter.build_request不再用timeout回退60秒；Grep/Find在实际搜索开始创建可选deadline，Grep发现及所有分组共用它。check_search无deadline时仍检查控制信号；有deadline时返回同一次检查得到的剩余秒数，stream_records直接传给ProcessRequest，避免再次读时钟产生负数。旧默认执行字段及Shell上限校验从settings移除，两份样例同步注释；probe/cleanup/网络请求配置、输出/归档/记录预算保留。旧配置key按原extra=forbid及工具预算警告回退合同处理，不兼容恢复这些字段，权限设置保留。

HostProcessRunner.run创建可选deadline；类内_await_spawn等待原spawn task完成或当前停止/超时，asyncio.wait不取消正在获取的句柄，取得后进入原_watch。超时/停止即交给原_settle，持有迟到句柄、进程组、管道及consumer直到收尾或必要清理失败。原cleanup_timeout_seconds仍生效，不能被无限执行关闭。真实spawn/consumer的TimeoutError及外部CancelledError保留身份，不冒充本次执行期限；停止后迟到的真实获取失败同样保留原异常。

正常调用示例：模型bash(timeout=0) → 原参数/权限/整批审批/排队 → ShellAdapter.build_request(timeout_seconds=None) → runner._await_spawn → _watch（无执行deadline，仍响应停止） → _settle → 原输出归档/ToolError或成功ToolMessage/保存。有限搜索：grep(timeout=30) → _GrepSearch.deadline固定一次 → stream_records向发现/分组传剩余秒数 → 超时收尾 → 原授权部分结果及incomplete/timeout；不设置根RunControl，不取消兄弟工具或修改已确认成功。

三份新测试覆盖模型公开schema、省略/null/0/600完整参数链、严格非法参数的工具错误且runtime不泄漏、旧配置key诊断、同进程审批前零执行及600恢复、四工具×0/600原生异步图、整次Grep分组期限、部分结果/真实子进程超时、静默停止、启动阶段停止/期限/迟到失败、真实异常身份及必要cleanup预算。PowerShell解释器由跨平台Python子进程夹具替代，进程runner/流/清理真实；不是Windows原机或真实PowerShell验收。

工具首批测试工厂遗漏create_builtin_tools所需settings，修正测试构造后22 failed；搜索首批6 failed；进程首批8 failed/1 passed，明确复现原有限默认、schema拒绝、None不支持和启动停止缺口。初版共享类型把description放在nullable内部导致四个schema形状断言失败，调整为整个可空字段注解后相关110 passed in5.84s。追加记录检查到建请求之间跨deadline的用例先1失败，改用同次检查剩余值后转绿；其余追加边界不冒称逐项TDD。

原五份测试按获批合同迁移：Find/Grep精确公开字段集合增加timeout；Shell配置用例移除旧默认字段、验证600通过与负数拒绝；原默认大小关系/搜索有限浮点配置用例改为现行额度非法输入，六个旧key的extra/fallback由新测试逐项保留；HostBash描述的60/300断言改为默认不限时/0/无最大值约束，输出额度/工作区断言保留。首次全量1847 passed/1 failed暴露最后一个旧描述断言，迁移时漏掉同例300断言使定向再失败一次，已完整迁移该合同；没有放宽权限、异常身份、进程回收、事件或内容断言。


最终三份新测试共**60例**，`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q tests/test_tool_execution_timeouts.py tests/test_process_execution_timeouts.py tests/test_search_execution_timeouts.py` → **60 passed in4.09s**（/tmp/naiyue-090c-new-final.log）；含旧Shell/搜索/停止/进程回归的中间定向156 passed in14.85s，随后新增八组原生异步图也由最终全量覆盖。最终全量`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1863 passed in54.95s**（/tmp/naiyue-090c-all-final.log）。前端`npm --prefix frontend test -- --run` → **9文件33 passed**（/tmp/naiyue-090c-frontend.log）；`npm --prefix frontend run build` → **TypeScript/Vite186模块成功**。compileall/pip check、295 Python AST/107生产文件≤500行/新测试≤500行、样例/相对链接及diff检查通过。

当前settings.example.toml经load_tool_settings实际装配，工具字段无fallback；完整tool-runtime.example.toml已按原说明包含未实施的tools.subagent，不能整份套用，此次只校验其中八工具现行段落并保留未来草案，不把整份草案被loader回退当作接线成功。两份样例只更新执行期限相关说明，真实用户配置/依赖/数据库未改。生产中不再读取default_timeout_seconds/max_timeout_seconds或搜索settings.timeout_seconds；保留模型网络、解释器探测、必要清理及测试探针自身防挂预算，各有当前消费者。

本机证据为macOS文件/进程IO、原生同步/异步图、真实rg/fd与Python协议子进程夹具；没有真实模型新请求、Windows/Linux原机或原生PowerShell验收。无暂存/提交/push，原有脏工作保留。八片已完成090A/B/C及091A/B，余下092A→092B→092C；086子Agent仍独立未实施。唯一下一步讲解并审阅092A，不自动实施。
