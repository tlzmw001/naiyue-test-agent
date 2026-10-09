# Spec 030：Read 权限判定与同进程审批闭环

状态：2026-10-01，已按本 Spec 实施并完成本地确定性验证；真实供应商与跨进程审批未验证。

## 当前链路与目标

应用会话已拥有规范化工作区，运行时注入 `workspace_root`，会话版 `read_file` 只读取该目录内普通 UTF-8 文本。工作区外路径目前一律报错。用户要求本片一次完成 `allow/deny/ask` 与真实审批；不能把 `ask` 返回工具错误冒充待审批。

本机 LangChain 1.4.2 `HumanInTheLoopMiddleware` 在 `after_model` 按 `when(ToolCallRequest)` 判断，命中后 `interrupt`，同步 `agent.invoke` 返回含 `__interrupt__` 和当前 `AIMessage` 的字典；同一 `thread_id` 用 `Command(resume={'decisions': [...]})` 恢复。探针确认 `ToolRuntime` 在恢复后的工具执行中可取得本次 `context` 与 `tool_call_id`。官方接口见 [Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)。

## 权限决策与边界

只覆盖会话版 `read_file`，不改变固定工作区工厂或模型可见的 `path/offset/limit` schema。规范化原始路径与符号链接真实目标后，按下表判定，`deny` 优先于 `ask`：

| 条件 | 决策 | 行为 |
| --- | --- | --- |
| 缺失可信工作区、无效/不存在目标、`..`、隐藏路径、`.pem/.key`、非普通文件 | `deny` | 直接由工具回填固定错误，不出现审批请求 |
| 目标在会话工作区内且通过现行规则 | `allow` | 原有 64 KiB/2000 行有界读取 |
| 目标在工作区外且未命中拒绝规则 | `ask` | 模型工具调用后、工具执行前由 HITL 中断；应用与 CLI 展示解析后的目标，待用户按每个调用批准或拒绝 |

后续 [Spec 031](031-read-text-hardening.md) 将工作区**内部**的普通 `.gitignore` 文件及 `.github` 目录组件从上述“隐藏路径拒绝”中有限放行；工作区外隐藏路径与敏感目标仍拒绝。本表是 Spec 030 实施时的原始边界。

Pi 的 `tool_call` 扩展示例能在执行前阻断；AIAutoTest 另装的 Pi 权限系统将工作区外设 `ask`、敏感路径设 `deny`，不属于 Pi 基础 Read 自带能力。AgentX 已核对的 RAG 工具在执行时重新验证知识库权限，但不是本地文件授权实现。本项目仍保留文件执行处的实际校验，不能只信前置 HITL 的判断。

## 审批契约

1. 一次 `run` 因外部普通文件中断时，运行事件为 `run_interrupted`，没有 `run_completed`，对应工具也没有 `tool_started`。应用从当前 AI 工具调用及 `__interrupt__` 中核对并保存每个待审的 `tool_call_id`、规范化目标与会话；任何普通新消息不得越过待审批状态。
2. CLI 展示编号及规范化目标。单个待审调用可用 `:approve` 或 `:deny`；同轮多个待审调用用 `:review approve reject ...` 按展示顺序一次提供全部决定。数量或取值不合法时不恢复、不执行任何文件读取。`quit` 退出进程会丢失内存中的待审批状态，应明确提示。
3. 应用 `resume_read_approval(session_id, decisions)` 只接受当前会话的一次待审批请求，并沿同一 thread/checkpoint 恢复；仅 `approve` 的调用获得一次性授权，`reject` 由 HITL 回填错误 `ToolMessage`。不支持 `edit`、`respond`、`allow_session` 或跨重启恢复。应用不能把模型自填的 `runtime`、工具参数或任意路径当批准证据。
4. 一次性授权与 `tool_call_id` 和规范化目标绑定。工具执行时重新解析目标；若目标变化、授权不匹配或已消费，拒绝读取。后续新模型回合的外部读取须重新审批。既有 `resolve` 到 `open` 的并发替换窗口尚未消除，不宣称 OS 级沙箱。
5. 同一会话保持 busy/待审批互斥；其他会话可运行。审批恢复中出现异常时记录 `run_failed`，保留安全失败展示与待审状态供诊断，不宣称自动恢复一致性。用户停止仅在活动运行的既有边界生效；等待人工审批时没有活动运行。

## 代码影响与 TDD 验收

- `agent_runtime/tools/read_file.py`：保留已有读取逻辑，增加可复用目标分类、一次性授权及执行时重验。路径、文件内容和审批对象不写入日志。
- `agent_runtime/__init__.py`：对会话版 Read 加条件 HITL；支持带可信一次性授权的恢复调用；把 `__interrupt__` 记为 `run_interrupted`。公开旧入口签名不变。
- `application/__init__.py`：维护每会话待审的调用 ID/目标，验证与恢复决定，阻止待审时普通 `run`；保留其他会话独立运行。`application/cli.py`：展示待审及接收审批/拒绝命令。
- 先写失败测试并确认红灯原因，再最小实现。至少覆盖工作区内放行、外部读取前确实暂停、批准后准确读取一次、拒绝零读取、敏感外部文件不请求审批、符号链接目标变化、伪造 `runtime`、同轮多工具混合决定、并发/错误及 CLI 命令。验证定向和全量 pytest、compileall、pip check，记录真实结果；不把假模型验证写成真实供应商、持久审批或多用户授权证据。

## 实施与验证记录

`application.SessionService` 保留会话内待审调用列表；`agent_runtime` 为会话版 `read_file` 挂载条件 HITL，并用原 `thread_id` 和 LangGraph `Command(resume=...)` 恢复。批准生成的授权仅在恢复调用的可信 context 内存在，工具执行时按调用 ID、规范化目标再核对并一次性消耗。CLI 用 `:approve`、`:deny`、`:review` 处理待审列表。结构化日志只记录审批事件、会话/run ID 和数量，不记录路径或文件内容。

先新增应用和 CLI 的失败测试：应用最初因缺少 `approval_requests`、越界仍拒绝而红；CLI 最初把审批命令识别为未知命令。按这些测试接线后，再为审批事件加失败测试并转绿。最终命令及实测结果以 [交接记录](../handoff.md)第 120 节为准。测试覆盖本地假模型、同进程内存 checkpoint、普通临时文件及 CLI 输入模拟；不构成真实供应商、可重启恢复、身份授权或无 TOCTOU 的证明。
