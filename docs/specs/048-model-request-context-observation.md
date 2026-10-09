# Spec 048：逐次模型请求的上下文输入观测

状态：2026-10-03，已按 TDD 实施并完成本机确定性验证与一次真实模型探针。

## 当前数据流与问题

`SessionService.run → agent.invoke → select_model → project_prompt → 模型`。应用在每个 run 冻结项目指令；运行层已记录模型选择、成功响应的 `input_tokens` 和缓存读取量，但没有记录每次模型调用实际收到的框架侧消息、系统提示词和工具 schema 的安全摘要。checkpoint 的历史长度不能代表当前请求，项目文件的 64 KiB 读取上限也不是 token 预算。

参考 Pi `a69bef789`：其上下文用量以模型返回的有效 usage 为基础，并对之后新增的消息作估算；模型窗口缺失时不报告百分比。当前本项目模型目录尚无可信上下文窗口字段，因此本片不设压缩阈值、预留输出 token 或自动截断，不把字符数称为 token。

## 本片契约

- 在所有已装配的同步 `create_agent` 路径中，模型选择和项目指令装配之后、调用适配器之前，按每次模型调用记录一条 `model_request_context` 结构化日志。它包含 run/session 关联、消息总数及角色分布、系统/普通文本字符数、AI 消息中有效工具调用数量、提供给模型的工具数量，以及工具 schema 的 UTF-8 JSON 字节数和 SHA-256 指纹。只记录计数与摘要，不记录提示词、消息正文、工具参数、工具描述、工作区路径或内部 thread ID。
- 此处的 `ModelRequest` 是 LangChain 框架交给模型适配器的输入，不等于供应商最终 HTTP 请求；工具 schema 指纹来自框架工具定义，不宣称等于供应商编码后的 schema。非文本内容不估算 token。
- 如果本次模型调用成功，事件同时记录 `AIMessage.usage_metadata.input_tokens`；缺失时为 `None`，不推断为零。若模型/适配器抛异常，也记录请求摘要及 `input_tokens=None`，再原样抛出；不能把失败改成模型回答。现有 `model_call_usage`、失败分类及终局事件语义保持不变。
- 工具 schema 序列化不可用时记录一个不含原始错误文本的安全警告和 `schema_status=unavailable`，仍继续模型调用；不因观测代码影响业务结果。响应中无 AI 消息仍由原有模型响应门处理。
- 本片只建立逐次输入与返回用量的对照基础，不新增模型窗口配置、预算百分比、压缩或用户界面。

## 文件、接口与验证

- 新增 `agent_runtime/context_observation.py`：只处理 `ModelRequest` 摘要和日志；不改公开 API。
- 在 `agent_runtime/__init__.py` 将该 middleware 放在项目指令 middleware 之后，使日志看到已装配的系统消息，并让现有选模 middleware 保持外层处理。
- 新增确定性测试：假模型经真实 `create_agent` 触发两次调用（含工具结果），检查两条日志的消息分布、系统提示词变化、工具 schema 稳定指纹和用量缺失/存在；模型抛异常时仍有安全请求日志且异常原样传播；合成敏感内容不进入日志。先看到预期红灯，再最小实现转绿。运行全量 pytest、compileall 与 pip check。
- 后续以配置的可信模型窗口和输出预留量计算预算，以固定真实模型样本核对供应商 usage；自动压缩另片设计。此片的确定性测试不等于真实模型 token 计量或生产日志交付验证。

## 实施与证据

- 基线全量测试为 `425 passed in 14.80s`。先加入两条端到端测试，执行结果为 `2 failed`，失败原因均是缺少 `model_request_context` 事件；最小实现后定向两条转绿。另补一条 schema 序列化不可用时仍继续调用模型的测试，最终定向为 `3 passed in 0.54s`。
- 新增 `context_observation.py`，在 `agent_runtime/__init__.py` 的 middleware 列表末尾接入；普通/工具后模型请求、失败调用与不可序列化工具 schema 的记录均有测试。只输出结构化计数、长度和指纹；不输出正文、参数、路径或 thread ID。
- 使用现有私有模型配置作一次仅含合成文本、无工具和工作区的真实模型探针；命令关闭托管 tracing，只打印安全字段。实际输出：`status=PASS`、`events=1`、`message_count=1`、`system_text_chars=40`、`tool_count=0`、`input_tokens=23`、`outcome=returned`。这证明现有网关链路返回一次可用的 `input_tokens`，不证明模型窗口大小、适配器最终 HTTP body、任何压缩策略或跨供应商行为。
- 最终 `.venv/bin/python -m pytest -q` 为 `428 passed in 15.34s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。变更的产品 Python 文件分别为 454 行、82 行，低于 500 行。
