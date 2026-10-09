# Spec 058：摘要失败时按原请求预算有条件回退

状态：2026-10-07，已按 TDD 接入并完成确定性回归。

## 当前链路与问题

`LocalApplication.run → agent.invoke → before_agent.compact_before_new_run → 摘要模型 → 状态替换 → 正常模型循环`。目前摘要模型异常、空输出或截断均报 `ContextCompactionError`，即使原请求只跨过软触发阈值、仍处于估算发送上界内，也会结束 run。现有完整轮次/消息边界切点和摘要后的预算复核已接线，本片仅补失败后的行为，不变更切点算法或摘要质量策略。

用户同意上一轮有条件回退建议。Pi `a69bef789` 的发送前 `_checkCompaction → _runAutoCompaction` 失败时记录失败并返回 false，发送前调用可随后继续新提示词；Pi 的 overflow 路径则先识别正常模型请求溢出，再压缩并最多重试一次，摘要失败会记录 `willRetry=false`。它不是“摘要失败且原历史超预算”的进一步恢复。本片借鉴失败可观测和保留历史，不声称沿用 Pi 的完整恢复机制。AgentX `3fcfa0e` 所核对的 `ConversationAppService → TokenDomainService → SummarizeTokenOverflowStrategy → ChatModel.chat` 未提供同形状的失败回退结果。

## 本片契约

- 当原请求近似估算 `E <= B = 窗口 − 正常最大输出`，且原历史通过现有文本/工具配对安全检查时，以下摘要失败可返回 None，使 LangGraph 保留原历史继续正常模型循环：现有失败分类识别为 `transient` 的超时、连接失败、服务端错误；摘要返回合法 AI 文本结构但正文为空或结束原因为 length。等于发送上界允许回退。复用原输入，不生成替代摘要、不修改消息内容/ID、不重跑整个 Agent。
- 补现有模型失败分类的 OpenAI `APIStatusError` 5xx → `transient/server_error`，以支持在线供应商的已确认场景。认证、权限、配置、取消、未分类异常和普通 400/429 不因本片自动回退；摘要响应带有效/无效工具调用、错误结束原因或非文本结构也不回退，即使同时为空或 length。结构错误优先于空文本/截断。
- 原请求超过 B 时，任何摘要失败都继续以原固定异常终止。无安全切点、摘要请求超预算、内部实现异常、压缩后仍超预算不属于可回退原因。若摘要本身可恢复但原历史检查不通过，记录拒绝回退原因并保持原失败；不能因一次摘要异常掩盖工具对不完整。
- 先保留 `context_compaction_failed`，可继续时新增脱敏 `context_compaction_fallback`：关联 session/run/model，固定回退原因、原失败原因、E 和 B。不记录异常正文或消息。不得发 `context_compaction_completed`。后续正常模型成功则沿既有 run_completed；正常模型失败仍 run_failed，不伪造回答。
- 本片不新增摘要重试。摘要调用仍经过既有 SDK 重试；SDK 耗尽后的错误才进入本片判断。正常模型调用是使用原历史执行任务，不是再次调用摘要。供应商上下文溢出恢复、强制压缩与 Agent 级最多一次重试保持后续独立切片。

## 影响文件与验证

- `agent_runtime/context_compaction.py`：在现有失败边界保存可恢复原因，按原预算及历史安全性记录回退或继续抛出；不改变公开 API。
- `model_access/errors.py`：仅补 OpenAI SDK 5xx 已知分类，与已有 Anthropic/Ollama 服务端错误语义一致。
- 新建 `tests/test_context_compaction_fallback.py`：既有压缩测试已有 463 行，为遵守单文件上限拆出聚焦测试；使用独立的局部假模型记录摘要及正常请求，第二处少量 helper 重复暂不抽象、不改 conftest。
- 旧 `tests/test_context_compaction.py` 的空摘要/截断失败用例若与新批准行为冲突，只调整合成输入预算让它们确实处于“不能回退”的区间，继续保留原异常、历史与 busy 断言；新测试单独验证可回退区间，不能放宽断言掩盖实现缺陷。
- Spec、handoff、future-work、project-plan 更新实际结果；不改 `.env`、依赖或《关键知识点》。

先运行基线，新增完整应用链路测试确认红灯后实施。断言摘要仅一次、正常模型原历史逐条相同、工具对保留、checkpoint 与 busy 清理、事件顺序/安全字段、发送边界及禁止回退原因；正常模型再次失败仍有正确终局。定向与全量 pytest、compileall、pip check。确定性测试不证明真实供应商精确 token 边界或故障时可用性；本片无需制造真实供应商故障。

## 实施与证据（2026-10-07）

- 基线全量 `459 passed in 14.85s`。新增聚焦测试后，`.venv/bin/python -m pytest -q tests/test_context_compaction_fallback.py` 红灯为 `17 failed, 17 passed in 1.44s`：可恢复失败与发送上界相等时仍抛异常、缺少拒绝回退事件、OpenAI SDK 5xx 分类未生效。禁止回退及超过上界的断言原本已通过。
- 最小实现用本次调用的局部 `fallback_reason` 标明可恢复摘要异常或空/截断文本；普通结构错误与 error/tool_use 优先拒绝。外层先记录原压缩失败，再校验 `E <= B` 和完整原历史；通过才返回 None，LangGraph 保留原消息进入既有模型循环。原历史不完整时另记录 `context_compaction_fallback_rejected` 并继续抛原固定异常。复用分类器，仅新增 OpenAI 5xx 与已有服务端错误的相同映射。
- 新专项测试 `34 passed in 0.64s`。两项旧“空/截断摘要必失败”样本原请求估算 90、发送上界 290，属于批准后的回退区间；将其合成最大输出改为 220，使发送上界 80，明确断言原请求大于上界，原异常、checkpoint 与 busy 断言保留。未放宽断言、skip 或修改测试基础设施。
- 定向 `.venv/bin/python -m pytest -q tests/test_context_compaction_fallback.py tests/test_context_compaction.py tests/test_model_access.py tests/test_agent_runtime.py --tb=short` 为 `133 passed in 3.88s`；全量 `.venv/bin/python -m pytest -q` 为 `493 passed in 15.10s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 为 `No broken requirements found.`。修改的 Python 文件均低于 500 行。
- 未制造真实供应商故障，也未实现供应商溢出识别/强制压缩/最多一次恢复。SDK 重试次数和正常调用策略不变；不同请求仍各有 SDK 策略，当前没有独立的整个 run/压缩总耗时或总费用上限。用户确认范围并提出溢出机制问题，新增代码由助手实现，尚无独立编写或面试追问证据。
