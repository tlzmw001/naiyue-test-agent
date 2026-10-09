# Pi Bash 结果链与本项目差距（2026-10-02）

本记录只读核对本机 Pi 提交 `a69bef789` 与当前 `naiyue-test-agent` 代码。源入口：Pi `packages/coding-agent/src/core/tools/bash.ts`、`output-accumulator.ts`、`truncate.ts`，`packages/agent/src/agent-loop.ts`；本项目 `agent_runtime/tools/bash.py`、`application/cli.py` 和 `agent_runtime/__init__.py`。Pi 的**模型调用 Bash 工具**与用户直接在终端输入 Bash 的路径不同，以下分开说明。未运行 Pi 完整应用或真实模型；本项目只做假模型和无害临时目录探针。

## 一条模型工具调用的数据流

模型发 `bash({command, timeout?})`。Pi loop 先找工具、准备并校验参数，再经过 `beforeToolCall`；校验/拦截失败变成带调用 ID 的错误工具结果。工具的 `execute` 把命令交给 `BashOperations.exec`，默认 `createLocalBashOperations` 用 `spawn` 在 cwd 运行 shell，stdout/stderr 到达时交给 `OutputAccumulator`，可经 `onUpdate` 发部分结果。完成后工具返回 `content` 和截断/完整输出文件 `details`；非零退出、超时、中止会抛带有已收集输出和原因的错误。Pi loop 把抛出的工具错误变成 `isError=true` 的 `toolResult`，以原 tool call ID 写入上下文，下一次模型调用可看到。Pi 对明确的模型回答错误/中止另走终局，不能和单个工具失败混为一谈。

本项目已接 `HumanInTheLoopMiddleware → ToolNode → bash.py`。批准后，正常退出返回字符串，非零退出抛 `ToolException`，LangChain 生成 `ToolMessage(status='error')`；同一 tool call ID 的结果进入 checkpoint，框架再调模型。CLI 当前只打印 `AIMessage` 的文字回答和审批请求，不直接展示正在运行的 stdout/stderr 或完整的 `ToolMessage`。超时抛 `BashOutcomeUnknown`，整次 run 失败并把会话设为待人工核对；这是本项目对可能已经发生副作用的保守取舍，**并非与 Pi 相同的结果回填语义**。

## 需要保留、补齐和暂缓的机制

| 机制 | Pi 本机源码行为 | 本项目当前行为 | 建议 |
| --- | --- | --- | --- |
| 参数错误 | loop 在 `beforeToolCall`/执行前校验，错误成为带 ID 的 `toolResult`，模型可重试 | Bash 缺少 `command` 时，应用组装审批请求抛 `RuntimeError`，整次 run 失败 | **下一小片先修**：不启动进程，给模型可行动的错误 `ToolMessage`；同时验证审批请求不会误接受无效参数 |
| 普通退出/非零退出 | 输出及状态进入工具结果；非零标记 `isError`，loop 继续，模型可读并决定下一步 | 正常和非零均回填 `ToolMessage`，测试覆盖原 ID 和非零状态 | 当前机制基本具备；增加真实 pytest 失败样本验证模型拿到错误摘要 |
| 长输出 | 流式 UTF-8 解码，按 2000 行/50 KiB 保留**尾部**；截断时保存完整输出到临时文件，并在文本中给出路径 | stdout/stderr 各留**开头** 4096 字节，后续直接丢弃；按原始字节截断，可能切断 UTF-8 字符 | **下一小片优先补**：先保证尾部错误摘要与 UTF-8 边界正确，再决定完整输出产物的大小上限、存放位置、清理和回读契约；不能无限写磁盘照搬 Pi |
| 运行中展示 | 工具可 `onUpdate` 发送部分输出，Pi 交互 UI 渲染 | 同步 CLI 在工具运行时无输出；用户只看到最终 AI 回复 | 等流式/异步入口切片再接事件与增量展示；先让最终结果可见且模型可用 |
| 超时/中止 | `AbortSignal` 与超时杀进程树；工具错误中包含已收集输出，loop 生成错误 `toolResult` | 默认 60 秒、最多 300 秒；超时杀进程组，但丢失已收集输出、整次 run 失败；`:stop` 只在当前 turn 后生效 | 保留“副作用未知不自动重试”的终局；另设计安全保存部分诊断。活动取消与子进程树验证放到运行控制切片，不把 turn 后停止称为杀进程 |
| 执行后端/环境 | `BashOperations`、`spawnHook`、命令前缀、shell 配置可替换；默认继承进程环境并可暴露 PI 会话变量 | 固定本机 `Popen`，shell 路径可由环境配置；仅传最小环境 | 未来隔离/远程后端接入时再抽象；环境继承不要照搬，以免带入模型密钥。pytest 所需变量应按具体场景配置/审批 |
| 用户直接运行 Bash | `AgentSession.executeBash` 产生 `bashExecution` 会话消息；可选择不送入模型上下文；运行时排队以维护消息顺序 | CLI 没有独立的用户直接 Bash 命令，只有模型工具调用 | 属于新入口能力，不是当前模型工具结果链缺陷；需要时单独设计 |

## 本机可复现探针

- `.venv/bin/python -m pytest -q tests/test_host_bash.py`：`8 passed in 2.01s`，证明当前批准/拒绝、工作区 cwd、工作区外访问、非零、环境清理、超时阻断和 CLI 接线的确定性样本；不证明 Pi 等价或真实模型使用质量。
- 在临时目录用 `_run_host_command` 输出 5000 个 `A` 后再输出 `FAILED 1 test`：退出 0、`truncated=True`，返回正文长 4096 字符且**不含**失败摘要。另输出 4095 个 ASCII 字节后接 `中`：截断结果出现替换字符 `�`，原字符消失。这两项是当前输出策略的真实缺陷，不只是设计推测。
- 假模型发 `bash` 调用且 `args={'timeout': 1}`：当前 `app.run` 抛 `RuntimeError('Bash approval has invalid command')`，没有错误 `ToolMessage`。这说明参数校验与审批组装次序尚未收敛；不能用“框架会回填工具错误”概括这条路径。

结论：下一片优先解决无效参数回填、保留尾部/UTF-8 完整性的有界输出，并补真实 pytest 成功/失败产物的端到端验证。完整输出文件、活动取消与增量 UI 需要明确存储、隐私和生命周期，单独设计。权限模式和 OS 沙箱沿原阶段计划推进；它们不是上述结果链问题的替代方案。

后续状态（2026-10-02）：以上为修复前的源码与探针快照；所列两项优先缺口现已按 [Spec 043](../specs/043-bash-argument-and-output-feedback.md)修复并完成本机回归。Pi 对照表的“本项目”列保留历史状态，不能用于描述修复后的当前行为。
