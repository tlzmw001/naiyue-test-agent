# Spec 054：压缩结果的发送前预算复核

状态：2026-10-03，已按 TDD 实施；真实供应商 token 误差与溢出恢复未验证。

## 当前链路与问题

本地应用 `SessionService.run → create_agent → before_agent` 在新 run 开始时，按当前模型的窗口与预留量估算消息；超过 `窗口 − max(预留量, 最大输出)` 时，选完整旧轮次生成摘要，保留最近原消息，并交给 LangGraph 的消息 reducer。当前代码只检查**摘要请求**能否装入窗口，未对 `摘要 + 保留消息 + 系统提示词 + 工具描述` 再做预算复核，便可能继续调用正常模型。

不落盘的完整应用链路假模型复现：模型配置窗口 500、最大输出 20、预留 50，压缩触发阈值 450；摘要成功后，正常模型收到的输入近似估算为 874，超过窗口扣除最大输出的 480。这是确定性本地反例，不是供应商真实 token 计数。Pi 当前 `shouldCompact` 使用 `contextWindow − reserveTokens` 触发压缩；本片的发送前估算阻断是本项目对已发现缺口的局部防护，不声称 Pi 有同一实现。

## 本片契约

- 摘要返回且通过现有有效性检查后，构造拟提交的新历史：`summary` 加完整保留消息。用现有 `count_tokens_approximately`，连同本 run 冻结的系统提示词和工具 schema 估算下一次正常模型请求。
- 两种阈值分开：`窗口 − max(预留量, 最大输出)` 继续作为**软触发阈值**；`窗口 − 最大输出` 作为按配置估算的**发送上界**。若压缩后估算仍严格大于发送上界，报固定原因 `compacted_request_over_budget`，记录既有安全失败事件，既不返回替换状态，也不调用正常模型。等于上界时允许继续。压缩后高于软阈值但未越过发送上界时不失败；可能在下个 run 再次触发压缩。
- 保留现有摘要失败语义：异常终止本次 run，原消息和本次用户消息留在内存 checkpoint；不把摘要伪装成正常回复，不自动丢弃近期完整轮次，也不改 SDK 重试次数。
- 成功事件增加压缩后估算值，便于区分“压缩实际缩小了请求”和“仍接近窗口”；只记数值，不记消息内容。估算是近似值，且窗口来自配置，不构成真实供应商接受请求的保证。

## 影响文件与验证

- `agent_runtime/context_compaction.py`：在已有摘要校验后、`context_compaction_completed` 与 reducer 更新前复核，复用已有异常记录分支。
- `tests/test_context_compaction.py`：先增加完整应用链路回归测试，证明红灯时摘要成功而正常模型仍被调用；实现后验证正常模型不调用、失败原因、原 checkpoint 未替换、应用 busy 复位。已有成功压缩测试继续证明正常路径不受影响。
- 本 Spec、`docs/handoff.md`、`docs/future-work.md` 和总体决策：记录实测结果与剩余边界。不修改配置、公开 API、依赖或 `docs/关键知识点.md`。
- 运行定向和全量 pytest、`compileall`、`pip check`。本片用确定性假模型验证接线；不增加真实网关请求，后续需按供应商真实用量核对近似误差。

## 独立后续切片

`invalid_tool_calls` 在历史中如何安全且完整地摘要、保留轮次大于窗口时是否做轮次中途切分、摘要模型失败后何时允许原历史继续、正常模型返回可识别上下文溢出后的最多一次压缩重试，均不由本片改变。普通失败 `ToolMessage(status='error')` 已是可摘要的配对历史；不能与 `invalid_tool_calls` 混为一谈。

## 实际验证

- 基线 `.venv/bin/python -m pytest -q tests/test_context_compaction.py` 为 `8 passed in 0.61s`。新增完整应用链路测试先失败：摘要返回后 `app.run` 未抛 `compacted_request_over_budget`，事件却记录 `context_compaction_completed`、正常模型收到输入且 run 完成；这与不落盘探针的 874/480 反例一致。
- 在摘要有效性校验后增加复核与固定原因，成功事件增加压缩后估算值。定向测试 `.venv/bin/python -m pytest -q tests/test_context_compaction.py` 为 `9 passed in 0.62s`。回归测试验证摘要调用一次、正常模型调用零次、失败事件、原消息与本次输入仍在 checkpoint、应用 busy 复位；已有成功测试验证压缩后估算可高于软触发阈值而不超过发送上界。
- 全量 `.venv/bin/python -m pytest -q` 为 `452 passed in 14.71s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。这些是确定性本地验证，没有真实供应商溢出请求或账单证据。阈值以配置与近似计数为依据，不能保证供应商必然接受。
