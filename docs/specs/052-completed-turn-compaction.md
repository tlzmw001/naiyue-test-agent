# Spec 052：完整旧轮次的上下文压缩

状态：2026-10-03，已按 TDD 实施本地同步路径；真实摘要质量和真实长会话接线尚未验证。

## 当前链路与目标

本地应用 `SessionService.run → create_agent` 使用内存 checkpoint；配置模型在最终 `ModelRequest` 前已有 `model_context_preflight` 近似预算事件，但 `needs_compaction=True` 仍会原样发送全部消息。Pi 当前实现按模型窗口与预留量判断、保留最近约 20000 tokens、摘要旧历史并记录压缩边界。LangChain 的 `before_agent` 可在新用户输入进入本次 state 后、第一次模型调用前返回 `RemoveMessage(REMOVE_ALL_MESSAGES)` 加新消息；本机一次假模型探针确认旧 checkpoint 仍在 `InMemorySaver` 历史中。本片借用 Pi 的行为，不直接使用内置 `SummarizationMiddleware`，也不实现 Pi 的会话树。

## 本片契约

- 仅在本地应用的**新 run 开始**检查完整历史；审批 `Command(resume=...)`、工具后的模型回合和同一 run 的 steering/follow-up 不在本片自动压缩。模型在检查时从可信 `session_id` 解析；若该模型没有本项目窗口/预留元数据，保持现状。
- 用当前模型窗口、压缩预留量、输出上限和 `count_tokens_approximately` 估计本次系统提示词（含本 run 冻结的项目指令）、工具 schema 与 state 消息。只有估计值严格超过 `window - max(reserve, output_limit)` 才压缩；不超阈值时不额外调用摘要模型。
- 最近保留目标默认 `min(20000, max(1, window // 4))` tokens；每模型可选正整数 `compaction_keep_recent_tokens` 覆盖。仅以 `HumanMessage` 作为新保留段的起点，整段保留该用户轮次及其后的消息；不拆 `AIMessage.tool_calls` 与 `ToolMessage`。若找不到非零安全切点，报固定 `no_safe_cut`，不删除状态、不假装压缩成功；轮次中途切分留后续片。
- 旧前缀须是可完整序列化的文本消息，工具调用 ID 与结果成对。明确序列化完整旧前缀为转义的 XML，包含工具调用和工具结果双方的完整 ID；不能直接用框架 `get_buffer_string(format='xml')`，因为本机源码显示它会截断长调用 ID，且 ToolMessage 的 XML 不带调用 ID。先检查内容块类型，不静默省略多模态内容。若前缀以本项目已有摘要消息起始，取出旧摘要并作为独立的 `<previous-summary>` 传给摘要模型；本次被移除的其他旧消息完整传入。摘要提示保留目标、约束、已完成/进行中工作、关键决策、下一步及精确文件/错误引用。
- 摘要模型使用**本次选中模型**，不绑定工具；每模型可选正整数 `compaction_summary_output_tokens`，默认 `min(2048, max_output_tokens, max(1, reserve_tokens * 4 // 5))`，显式值不得超过输出上限或压缩预留量。模型接入层为 OpenAI/Anthropic 绑定 `max_tokens`、Ollama 绑定 `num_predict`；发送前估算摘要请求输入加该摘要输出上限是否装入当前窗口。装不下报 `summary_input_exceeds_budget`，不采用“只取末尾 4000 tokens”的静默裁剪。模型失败、返回空文本、工具调用、明确错误或截断均报安全固定原因；只记录错误类型，不记录内容。成功才返回 `RemoveMessage(REMOVE_ALL_MESSAGES) + 摘要 HumanMessage + 最近原消息` 给 LangGraph reducer，后续正式模型调用只看到新状态。
- 压缩事件关联 session/run/model ID、旧/新消息数、估算值和固定结果码，不记原文。摘要请求单独于正常 Agent 模型循环；本片不做自动重试，沿用模型对象现有 SDK 请求策略。失败不构造成模型回答，运行沿现有失败路径结束。

## 影响文件与边界

- `agent_runtime/context_compaction.py`：最小压缩策略、切点、摘要边界与安全事件。独立于应用会话实体，不创建新的 Agent 循环。
- `agent_runtime/__init__.py`：只在本地交互式装配的 `before_agent` 注册；放在 run 控制重置之后、模型前消息注入之前。不修改公开函数签名。
- `model_access/__init__.py`：每模型最近保留目标和摘要输出上限的可选配置、非敏感元数据及供应商参数绑定；不改真实 `.env`。
- `tests/test_context_compaction.py`：先失败后实现；测试完整旧轮次切点、工具对、切模、旧摘要续写、checkpoint 旧版本可读、无安全切点、摘要输入过大、摘要失败/截断、消息/凭证脱敏与运行失败清理。已有应用/预算/工具/审批测试全量回归。
- 本 Spec、交接、后续与总体计划：记录实际证据和剩余任务；《关键知识点》未获追加授权，不修改。

## 本片不覆盖

不做轮次中途切分、完成一次 run 后立即压缩、供应商溢出后的压缩重试、持久化跨重启恢复、摘要质量保证或多模态摘要。单次真实短请求不可能证明压缩有效；用固定长历史任务和真实模型对照评测在后续独立阶段实施。本片以确定性假模型验证真实 `create_agent` 状态接线，保留一条真实模型长上下文验证为待办。

## 实施过程中的边界修正

- 最初“可摘要”测试把合成模型窗口设得小于完整摘要请求，正确触发 `summary_input_exceeds_budget`；已调整测试样本的窗口/预留量，使其分别覆盖正常摘要与故意超限，不放宽预期结果。
- 初用 LangChain `get_buffer_string(format='xml')`，针对工具结果的新增失败断言表明其 XML 不包含 ToolMessage 的调用 ID；核对本机源码还发现长工具调用 ID 会缩略显示。现改为明确转义的文本 XML，保留工具调用及结果双方完整 ID，对不支持的多模态/函数调用结构报错，不静默舍弃。
- 一次真实网关合成三轮探针在第三轮网络等待超过两分钟，人工以 SIGINT 停止，退出码 130；没有摘要完成证据，也不能确认慢在供应商、摘要输出预算还是其他请求阶段。该现象促使本片加入较小的摘要输出上限及供应商参数绑定，之后没有再次宣称真实长会话已通过。探针不打印回答、配置或凭证；原私有配置未修改。

## 验证结果与剩余问题

- 红灯先后出现：新测试模块不存在导致收集失败；最近保留配置和摘要输出配置被拒绝；绑定输出上限的断言缺失；ToolMessage XML 未带调用 ID 的断言失败。实现分别收敛这些点。合成“可摘要”用例最初的窗口不足以容纳摘要请求，保护按预期先报 `summary_input_exceeds_budget`；调整样本的窗口/预留量后，正常和故意超限两条测试各自成立，不以放宽断言掩盖失败。
- 定向 `.venv/bin/python -m pytest -q tests/test_context_compaction.py tests/test_model_limits.py` 为 `20 passed in 0.97s`；最终全量 `.venv/bin/python -m pytest -q` 为 `448 passed in 15.72s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。Python 生产与测试文件均少于 500 行。
- 假模型经真实 `create_agent` 和本地应用会话接线：A 完成旧轮次，切 B 后由 B 摘要并回复；第二次压缩把上次摘要作为独立字段续写；工具调用及结果同在旧前缀，模型摘要输入保留双方 ID；摘要失败或输入超限时正常回答模型不调用，原消息和新用户输入仍在最新内存 checkpoint，旧 checkpoint 历史可读取，应用 busy 标志复位。另验证无安全切点与旧多模态消息明确失败、摘要模型绑定本次输出上限。
- 仍未验证真实摘要模型完成、摘要事实保留率、真实 token 误差、跨供应商长会话、重启持久历史、并发切模竞态及完成 run 后的即时压缩。摘要请求使用模型对象自身现有 SDK 超时/重试；当前未设置独立的压缩总耗时预算。真实探针等待过长的具体原因尚未定位，不据此宣称供应商故障或输出上限是根因。
