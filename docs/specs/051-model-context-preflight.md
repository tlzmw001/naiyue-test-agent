# Spec 051：当前模型的调用前上下文预算

状态：2026-10-03，已按 TDD 实施；当前仅观测，不执行压缩。

## 当前数据流与问题

`SessionService.run → create_agent → select_model(request.override(model=...)) → observe_model_request → ChatModel → checkpoint`。模型条目已有 `context_window_tokens`（默认 128000）和 `max_output_tokens`（默认 16384）；`model_context_budget` 只在响应后记录供应商实际给出的输入用量。会话中途切模型时，预算必须在每次实际模型请求前按新模型重算。当前还没有自动摘要或压缩。

Pi 当前 `compaction.ts` 的触发条件是 `contextTokens > contextWindow - reserveTokens`，默认预留 16384；它参考最近有效 usage 与新增消息估计。我们借用“当前模型窗口减预留量”的决策，但本片使用 LangChain 对**本次最终请求**的 `count_tokens_approximately`，包含当前系统消息、历史、工具 schema；这样可避免切模型、系统提示词重载或工具集合变化时直接把旧模型 usage 当成当前请求的精确 token 数。该估计仍非供应商最终编码，尤其不能把跨供应商估算视为精确值。

## 本片契约

- 模型条目可选 `compaction_reserve_tokens`，正整数；未配置时默认 `min(16384, max(1, context_window_tokens // 4))`。常见 128K 模型与 Pi 默认同为 16384；较小窗口按窗口比例缩小默认值，避免预留量大于整个窗口。字段只影响本项目的预算观察，当前不触发真实压缩。显式值保留原值，不暗改配置。
- 本次调用所需预留取 `max(compaction_reserve_tokens, max_output_tokens)`：前者是压缩触发所留空间，后者是请求的生成上限；避免允许的输出空间被压缩阈值吞掉。若预留不小于窗口，标记预算不可满足并判定需要处理，但本片不阻断模型调用。输出上限仍由既有适配器传给供应商。
- 在当前项目选模和提示词装配之后、实际模型请求之前，记录独立 `model_context_preflight` 安全事件：模型 ID、session/run ID、窗口及来源、预留及来源、输出上限、有效预留、估计输入 token、阈值、`needs_compaction`、估算状态。超过阈值用 `>`，等于阈值不触发，与 Pi 判断一致。绝不记录消息正文、工具参数或密钥。
- 估计失败时记录安全错误类型和 `estimate_unavailable`，`needs_compaction=None`，保持原模型调用或原模型异常语义；不静默把估计失败当作预算充足。原 `model_request_context`、`model_context_budget` 事件形状不变。
- 不修改 checkpoint 消息，不调用摘要模型，不自动压缩、阻断、重试或改用户回答。该事件仅是候选触发判断，不证明模型真实窗口能力。

## 影响文件

- `model_access/__init__.py`：可选字段校验、默认值和独立非敏感元数据；不改 `.env` 私有文件或公开函数签名。
- `agent_runtime/context_observation.py`：在现有最终请求观测 middleware 内发新事件；复用当前框架请求，不新增 Agent 循环。
- `tests/test_model_limits.py`：先写失败测试，覆盖默认/覆盖/非法配置、阈值边界、切模型、系统消息及工具 schema、估计失败和脱敏；原有事件不变。
- 本 Spec、`docs/handoff.md`、`docs/future-work.md`、`docs/project-plan.md`：记录状态、命令、实际结果和下一步。

## 验证与后续

先看到目标测试因缺少调用前预算而失败，再实现并运行定向与全量测试、`compileall`、`pip check`。验收重点是同一运行中模型 A→工具→模型 B 两次 preflight 采用各自元数据，且不改变回答和历史。真实请求只用于比较供应商实际 `input_tokens` 与估算偏差，不能从一次短请求推断压缩效果。下一片再设计安全切点与摘要；如估算偏差明显，再调整估算方法或阈值，不把本片结果当精确安全门槛。

## 实施与证据

- 红灯：`.venv/bin/python -m pytest -q tests/test_model_limits.py` 得到 `3 failed, 4 passed`，分别因为新配置字段未被接受、两条调用前估算测试找不到实现入口。最小实现后，定向回归 `.venv/bin/python -m pytest -q tests/test_model_limits.py tests/test_model_request_context.py tests/test_model_access.py` 得到 `74 passed in 3.80s`。覆盖等于/超过阈值、输出上限高于预留量、切模后读取新模型限额、估算失败脱敏并保持模型调用。
- 全量 `.venv/bin/python -m pytest -q` 为 `437 passed in 14.94s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。三个修改的 Python 文件均少于 500 行。
- 当前私有默认网关上的一次单轮短请求实际返回：`status=returned`、`preflight_count=1`、`budget_count=1`、`estimate_status=available`、`estimated_input_tokens=15`、`actual_input_tokens=13`、`needs_compaction=False`。探针只输出这些安全数字，不输出提示词、回答、模型配置或凭证。这说明一次真实请求已接线，不能推出长历史、多模态、其他供应商或准确阈值的效果。
- 未修改 `.env`、依赖或《关键知识点》。下一片是安全切点、旧历史摘要、前次摘要续写和 checkpoint 证据；在该片前仍没有自动压缩能力。
