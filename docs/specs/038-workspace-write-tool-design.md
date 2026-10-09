# Spec 038：首个真实 `write` 工具

状态：2026-10-02。用户确认首片仅允许会话工作区内写入；本片已按 TDD 实现并通过本地确定性测试。这里的目标是当前单进程、单用户 CLI 的可验证切片，不声称文件系统沙箱或跨重启恰好一次执行。

## 为什么现在做、现有链路是什么

用户让模型能把用例 Markdown 或 pytest 文件写到指定工作区；`write` 应是通用文件操作，而不是“设计用例”或“生成 pytest”的固定业务工具。当前 `read/ls/find/grep` 已从应用会话取得可信 `workspace_root`，运行层将它放在 `ToolRuntime.context`；`read` 在执行处重验路径并把可恢复错误变成同调用 ID 的 `ToolMessage`。`application/cli.py` 尚未注册 `write`。已有 `ToolBatchMiddleware` 在模型一次请求多个工具且含 `sequential` 标记时按原调用顺序放行完整工具链，但目前仅用合成 `write_probe` 验证；它既不写文件，也不协调不同会话写同一路径。

预期数据流：`AIMessage.tool_calls=[read#1, write#2, read#3]` → HITL（本片若不涉及外部写则无写审批）→ 批次计划检测 `write` 的 sequential 元数据 → 三个 `ToolCallRequest` 按 #1/#2/#3 进入具体工具 → `write` 从可信 context 取根目录并校验/写入 → 带原 ID 的成功或错误 `ToolMessage` 回到下一次模型调用。工具层不 import 应用层；模型接入层不感知文件写入。

## 参考项目核对与本项目取舍

- Pi 本机提交 `a69bef789`：`packages/coding-agent/src/core/tools/write.ts` 的模型参数是 `path/content`，语义是创建或整文件覆盖，并自动创建父目录；其具体实现调用 `withFileMutationQueue`。`file-mutation-queue.ts` 按规范化目标给同一文件的修改排队。Pi 的这个内置工具本身没有声明 `executionMode: sequential`；通用 loop 的整批调度机制与具体文件写实现分离。我们借鉴“通用 write + 同目标冲突控制”，不把 Pi 的无条件覆盖或进程内队列当成本项目的权限保证。
- AgentX 本机提交 `3fcfa0e`：`BuiltInToolRegistry.createToolsForAgent → AbstractBuiltInToolProvider.createTools/executeTools → doExecute` 把工具规范/执行器注册到 `AbstractMessageHandler.buildStreamingAgent`；已核对的内置 RAG 工具在执行时重验知识库权限。未找到同等的本地文件 `write` 工具，因此不能声称它给出了文件覆盖、原子写或工作区外审批的现成答案。Java/LangChain4j 的错误字符串也不是本项目的 `ToolMessage.status` 契约。
- 本机 LangChain 1.4.2 的 `HumanInTheLoopMiddleware` 已用于只读工具的工作区外审批。官方 [HITL 文档](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)确认 `when` 可逐调用决定是否中断、审批按请求顺序恢复；它不会替我们核验文件内容和实际落盘目标。因此本工具执行处仍须做安全判断。

## 建议的第一片用户行为

模型调用 `write(path: str, content: str)`。`path` 可为相对路径或位于本会话工作区内的绝对路径；`content` 是 UTF-8 文本，允许空字符串以创建空文件。成功时创建新文件或**整文件替换**已有普通文件，返回简短成功结果（创建/替换、UTF-8 字节数），不把内容回显给模型；局部修改留给后续 `edit`。工具描述明确“完整重写”，避免模型误以为追加。模型参数采用严格 schema、拒绝额外字段；内容上限建议默认 1 MiB，可由单独环境变量调整，非法配置在装配时拒绝。上限按 UTF-8 编码后的字节数计算，不按字符数。

建议第一片**只允许工作区内写入**，工作区外与通过符号链接通向外部的目标直接返回安全拒绝。原因是当前 CLI 审批界面只展示目标路径，尚不能向人展示待写内容或 diff；把 Read 的“批准路径”直接复用于写入，会让人批准一段未看过的内容。工作区外可审批写入作为紧随其后的权限切片：审批界面先展示准确目标、创建/覆盖、内容预览或 diff 与摘要，并把一次性授权绑定 `tool_call_id + 规范目标 + 内容摘要`；恢复时及实际执行前重验。此处预留的是行为契约，不提前增加一个无人消费的回调或伪审批接口。完全信任/Auto 模式另片设计。

为避免当前安全边界被写工具绕开，第一片沿用 `read` 的路径原则：拒绝 `..`、隐藏/凭证路径（工作区内 `.gitignore` 和 `.github/` 的已有例外可沿用）、`.pem/.key`、目录或特殊文件；任何现有符号链接路径组件先拒绝。父目录必须已存在且在工作区内，第一片不自动 `mkdir`。这是比 Pi 小的可用切片，理由是自动建多级目录会扩大路径重验和失败清理范围；是否补建目录在真实使用反馈后单独验证。非工作区会话缺少可信根目录时拒绝执行，不从模型参数中推导根目录。

## 文件提交、冲突和失败语义

对通过校验的同一规范目标，在**当前进程**采用目标级互斥；不同目标不相互阻塞。建议锁忙时给后到的写调用返回“目标正被另一写入占用，请重新读取并确认后重试”的冲突 `ToolMessage`，避免排队后静默后写覆盖先写。实测本机临时文件系统不区分文件名大小写，但 `Path.resolve()` 仍保留输入大小写；锁键须把 Unicode 规范形式和大小写变体归为同一目标，以防 `Case.md`/`case.md` 绕过冲突。区分大小写的文件系统上这会保守地拒绝这类同时写入。相同模型批次中两个 `write` 已由运行层顺序放行，第二个是明确的后续完整重写；跨会话同时命中同一目标才需冲突测试。锁住后重新检查目标、大小和策略；在同目录建立私有临时文件，写入完整 UTF-8 内容并关闭，最后用 `os.replace` 原子替换。替换前失败要清理临时文件且原文件保持原样；不得先截断正式目标。成功仅说明当前进程观察到完整替换，不承诺断电持久性。

`os.replace` 已执行但结果回填、进程、checkpoint 随后失败时，磁盘可能已改变而模型未收到成功结果。此时不能自动重试；记录与 `run_id/tool_call_id` 关联的“副作用结果未知”事件，由应用报告需人工核对。跨进程竞争、恶意进程在路径检查与替换之间更改目录（TOCTOU）、崩溃后去重均不在本片保证范围；这些是今后做持久会话/多用户服务前的门槛，不能用进程内锁或原子替换宣称沙箱。

实施时发现当前 `SessionService` 在运行异常后会释放 `busy`，且审批恢复异常会保留原待审项；若提交边界结果未知，下一条消息或再次 `:approve` 可能让同一线程重放未确认的工具调用。因此本片须在应用会话中设置“写入结果待人工核对”标志：`run` 和 `resume_tool_approval` 都在进入 Agent 前拒绝；CLI 明确提示核对工作区文件并新建会话。该标志只对明确的 `WriteOutcomeUnknown` 设置，不把普通可恢复工具错误、模型失败或用户停止误标为写入未知；当前单进程会话无解除/恢复入口。以后持久恢复须用持久化操作记录另设计，不能只靠此内存标志。

| 情况 | 对模型/运行的约定 |
| --- | --- |
| schema 错、内容超限、路径被策略拒绝、父目录缺失、目标类型不对、同目标锁冲突 | 未发生正式文件替换；固定、可行动的错误 `ToolMessage(status='error', tool_call_id=原 ID)`，不回显内容或原始异常 |
| 临时文件创建/写入失败且可确认尚未提交 | 清理临时文件；安全错误 `ToolMessage`，提示检查工作区空间/权限或缩小内容；日志仅记录类型/阶段/关联 ID |
| 提交边界或提交后无法确认结果 | 不告诉模型“未写入，请重试”；运行失败或明确的“结果未知，先核对文件”终局，禁止本工具自动重试 |
| 写入成功 | 同 ID 成功 `ToolMessage`；`run_completed` 仍只说明 Agent 调用结束，不代表生成内容质量合格 |

`GraphBubbleUp`/审批中断与用户停止是控制信号，不转成普通工具错误。任何日志不得含文件正文、原始异常中的敏感路径或凭证；用户可见拒绝信息不透露受保护路径是否存在。现有 `:stop` 是 turn 边界停止，不保证回滚已经提交的写入。

## 拟影响文件与 TDD 验收

- 新增 `naiyue_test_agent/agent_runtime/tools/write.py`：严格参数 schema、执行时工作区校验、目标级冲突与提交、固定安全错误、`naiyue_execution_mode='sequential'`。是否把已有 Read 的路径分类抽成公共代码，按实际第三次重复和模块边界决定；不预先重构只读工具。
- `naiyue_test_agent/application/cli.py`：在选择工作区时注册 `write`。非工作区会话不注册；不更改现有 CLI 审批命令。
- `agent_runtime/__init__.py` 预计无需增加写审批规则；现有 `ToolBatchMiddleware` 应自动消费工具元数据。若验证发现现有接线不足，先更新 Spec 再改运行层。应用会话已有 `workspace_root` 传递路径，不应重复造会话状态。
- 新增定向测试：先红灯观察工具缺失，再实现。覆盖新建、覆盖、空内容、非 UTF-8 可编码问题、内容字节预算、越界/隐藏/敏感/符号链接/目录/缺失父目录、原文件在临时写失败后不变、同目标双会话冲突、不同目标独立、同轮 `read → write → read` 的次序与新内容可见、错误 `ToolMessage` 的原 ID/status 与下一模型输入、日志脱敏及 CLI 真正注册。额外覆盖提交结果未知后同会话的普通运行及审批恢复均被阻断、其他会话不受影响。用事件同步竞争测试，不用短 `sleep`。不能把假模型测试称为真实模型质量验证。
- 完成时运行定向和全量 `.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`；记录真实输出。再用受控临时工作区执行一次 CLI 或应用假模型链路，检查 L1 文件存在、L2 真正写入、L3 CLI/运行层接线；真实供应商调用、内容质量和持久恢复各自单列证据。

## 范围定案与实施证据

用户确认：**第一片仅在会话工作区内写入**；工作区外等审批界面能展示并绑定待写内容后再接入。实际实现采用整文件覆盖、父目录已存在、默认 1 MiB UTF-8 字节上限（环境变量 `NAIYUE_WRITE_MAX_BYTES` 可改，非法值在工具构造时失败）和进程内同目标并发冲突。已有文件替换时保留普通权限位，新文件由私有临时文件替换而来，当前权限为 `0600`；不复制特殊权限位。

- `agent_runtime/tools/write.py` 已提供模型可见的 `path/content` schema、运行时可信工作区校验、敏感/隐藏/符号链接路径拒绝、进程内目标占用检测、同目录临时文件与 `os.replace`，并声明 `metadata['naiyue_execution_mode']='sequential'`。目标占用键对 Unicode 规范形式和大小写做保守归一，避免本机不区分大小写文件系统上的别名绕过。`tools/__init__.py` 与 CLI 已注册；模型无权在参数中指定 `runtime/workspace_root`。`agent_runtime` 装配代码不需要新增写工具特例，证明了运行层实际消费元数据。
- 临时写入前失败回填固定、安全、可行动的错误工具消息；提交边界失败抛 `WriteOutcomeUnknown`，写工具与应用记录可关联事件，应用将该会话标为待人工核对并拒绝其后 `run`/审批恢复，CLI 告知检查工作区文件后重启会话。其他会话可继续。普通工具错误不触发会话隔离。
- TDD 首次红灯为 `ImportError: cannot import name 'create_session_write_tool'`（测试收集 1 error）；补实现后曾有 12 条失败，原因是新测试错误地把 checkpoint 历史中的旧 ToolMessage 当成本次结果、并要求模型原始工具参数从状态消失。测试改为按原调用 ID 检查本次结果，只验证工具反馈/日志不泄漏正文。随后权限位与关联事件测试先红 `2 failed`，未知结果阻止同会话重放测试先红 `2 failed`，CLI 提示测试先红 `1 failed`，超长内容编码前拒绝测试先红 `1 failed`；均按契约修复，不跳过失败用例。
- 定向 `.venv/bin/python -m pytest -q tests/test_write_tool.py tests/test_application_write.py tests/test_application_write_cli.py tests/test_application_cli.py tests/test_application_cli_read_tools.py` 在最后几项补充测试加入前实测 `59 passed in 1.20s`。最终 `.venv/bin/python -m pytest -q` 为 `352 passed in 6.65s`；`compileall` 退出 0；`pip check` 输出 `No broken requirements found.`。测试用假模型、临时真实文件和 CLI 输入模拟，覆盖同轮 `read → write → read` 的文件变化与原 ID 回填、双会话同目标冲突、不同目标并行、大小写别名保守冲突、审批恢复的未知结果阻断。一次新测试错误假设模型传入的 `runtime` 应触发 schema 错，实测框架用可信 `ToolRuntime` 覆盖该字段；改用两个真实工作区断言伪造值不能改写目标，失败转绿。别名冲突测试先红 `1 failed`，归一锁键后转绿。为遵守单文件 500 行上限，CLI 测试按原有工具行为拆为两个文件，断言语义未放宽。
- 已知边界：没有真实供应商用 `write` 的请求与质量评测；没有工作区外写审批、自动建父目录、跨进程锁、崩溃后去重、持久会话隔离或可宣称的沙箱。路径校验到替换之间仍有 TOCTOU 窗口；`os.replace` 的本地测试不证明断电持久性。进程在替换成功后、结果/checkpoint 确认前崩溃时，内存待核对标志也来不及保存，恢复前仍须另设计持久操作记录。
