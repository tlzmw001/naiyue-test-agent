# Spec 057：发送预算确有需要时按消息边界切分轮次

状态：2026-10-07，已按 TDD 接入并完成确定性回归。

## 当前数据流与触发范围

[Spec 056](056-no-safe-cut-soft-threshold.md)已使“超过软触发阈值、没有完整轮次切点、但原请求仍在估算发送上界内”继续运行。真正需要再缩短请求的两种情况是：没有完整轮次切点且原请求 `> 窗口 − 最大输出`；或完整轮次切点虽存在，但保留后缀加摘要上限的估算仍无法装入发送上界。当前分别报 `no_safe_cut`，或先付摘要调用成本再可能报 `compacted_request_over_budget`。

Pi `a69bef789` 的 `findCutPoint` 可选用户或助手消息为保留起点，但从不选工具结果；若落在轮次中间，`prepareCompaction` 将早期历史与当前轮次前缀分开，`compact` 为轮次前缀单独生成摘要，再把摘要和保留后缀关联。本片借鉴切点与配对规则，继续使用现有 LangChain `before_agent` 与一次摘要调用；在摘要输入里显式标明 `<turn-prefix>`，不声称实现 Pi 的两次独立摘要或会话树。

## 本片契约

- 保留当前完整轮次切点作为首选。只有它缺失且原请求超过估算发送上界，或它的保留后缀加完整摘要输出上限可能超过发送上界时，才在后续消息边界找切点。按消息顺序选**最早可行**的边界，以尽可能保留原文；候选只能是 `HumanMessage` 或 `AIMessage`，不能是 `ToolMessage`，不能越过本次最新用户输入。
- 候选的移除前缀须通过已有工具调用/结果配对、文本与无效调用校验；保留后缀也须没有孤立工具结果或未完成调用。若候选从 `AIMessage` 开始，须找到它所属轮次的真实 `HumanMessage`，并在摘要输入中分隔早期历史与该轮次前缀，明确要求保留原请求、前缀中的决定/工具结果，以及理解保留后缀所需事实。若从 `HumanMessage` 开始，沿用普通完整轮次摘要。
- 任何摘要模型请求都先检查 `摘要输入估算 + 摘要输出上限 <= 配置窗口`；候选保留后缀连同系统指令、工具 schema、摘要前缀与摘要输出上限须估算 `<= 窗口 − 最大输出`。摘要返回后仍执行 [Spec 054](054-post-compaction-budget-guard.md) 的实值复核。原始 ID、消息内容和工具结果不截断；一次完整消息不可拆开。
- 无候选时保持安全失败；原始输入在发送上界内的切点 0 情况仍按 Spec 056 跳过压缩。已存在的完整轮次方案如仅因摘要输出上限保守估算而无后续候选，仍可按原流程尝试一次摘要，并以真实返回长度复核，避免不必要的失败。摘要失败不会提交替换状态，不自动重试。
- 成功事件增加 `split_turn` 布尔值与切点索引（不含原文）；模型新响应拒绝、工具执行/审批、配置、公开 API 均不改。

## 影响文件与 TDD 验收

- `agent_runtime/context_compaction.py`：在现有 `before_agent` 内选择并校验备用消息边界，必要时生成标明轮次前缀的摘要输入；复用现有模型调用、日志和 LangGraph reducer。文件低于 500 行。
- `tests/test_context_compaction.py`：先写完整应用链路失败样本，含一轮中 `Human → AI(tool_calls) → ToolMessage → AI → 新 Human`，证明不能从工具结果起保留、前缀工具对同去、保留后缀和摘要进入下一次模型输入；另覆盖切在 `AI(tool_calls)` 前时工具结果随它保留、单条超大用户消息仍失败、完整轮次正常路径不变。若接近 500 行上限，新用例放独立聚焦测试文件。
- 本 Spec、交接、待办与总体决策记录实现和证据。无 `.env`、依赖或 `docs/关键知识点.md` 修改。
- 定向和全量 pytest、`compileall`、`pip check`。确定性假模型可证明结构/接线，不证明真实供应商 token 精度、真实摘要质量或持久会话恢复。

## 实施与证据（2026-10-07）

先写两个完整应用链路失败样本，红灯为 `2 failed, 14 deselected`：完整轮次后缀超发送预算、无完整轮次切点。实现后按顺序扫描候选消息边界，分别检查移除前缀、保留后缀的工具配对，摘要请求预算和预计发送预算；在 `AIMessage` 边界切分时，单次摘要请求以 `<conversation>` 和 `<turn-prefix>` 区分历史与当前轮次前缀。首次测试样本中更早的 AI 边界本来已可行，故调整合成窗口和长度以真实复现“只能在工具结果之后切分”，保留原断言。第二个样本确认切在工具调用前时调用与结果仍一起保留。

合并 Spec 056 后定向 `.venv/bin/python -m pytest -q tests/test_context_compaction.py` 为 `16 passed in 0.62s`；全量 `.venv/bin/python -m pytest -q` 为 `459 passed in 14.81s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。这些验证包含真实 `build_local_application → app.run → create_agent` 接线但使用确定性假模型。尚未执行真实供应商对轮次中途摘要质量、token 精度、成本或长会话的验证。
