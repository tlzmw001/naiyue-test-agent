# Spec 021：两种模型 unknown 的最小观测

状态：2026-10-01，已按 TDD 实施并完成本地确定性验证。用户授权按既定建议做纯观测切片；未改变模型结束原因归一、工具路由、错误抛出、重试或对外入口签名。

## 当前数据流与缺口

`SessionService.run → invoke_agent_run → create_agent → select_model → handler(request.override(model=model))`。`handler` 抛异常时，`classify_model_failure` 给出 `category/reason`，模型 middleware 记录 `model_call_failed` 并抛原异常，运行层记录 `run_failed`。当分类为 `unknown/unclassified`，模型事件没有异常类名或可安全提取的 SDK HTTP 状态，评测只能计数，难以归因。

`handler` 返回 `AIMessage` 时，`normalize_stop_reason` 从已适配的 `response_metadata` 得到项目结束原因并写回。若为 `unknown`，消息目前仍按既有规则进入框架路由；该原因没有独立日志事件，`model_call_usage` 只记用量。只看失败 run 会漏掉正常完成或执行工具的响应型 unknown。

## 本切片契约

1. `model_call_failed` 在 `category='unknown'` 时额外记录 `error_type=type(error).__name__`；仅对已识别的 OpenAI/Anthropic SDK HTTP 状态异常和 Ollama `ResponseError`，记录范围为 100–599 的整数 `status_code`。不读取/记录异常 `str(error)`、body、URL、header、请求、响应或堆栈。其他既有分类的事件形状保持不变；原异常传播与终局不变。
2. 在有 `AIMessage` 且统一原因为 `unknown` 时，模型 middleware 在交还框架前记录一次结构化 `model_stop_reason_unknown`，关联当前 `run_id/model_id`，字段为 `normalized_reason='unknown'`、`reason_source`（`status/finish_reason/stop_reason/done_reason/missing`）、安全原生原因码或 `None`、是否脱敏、有效/无效工具调用数量。字段来源按当前归一化的优先顺序选择；不存整个 `response_metadata`。
3. 原生值只有是短小的 ASCII 小写下划线枚举码，且不含明显凭证语义时才可作为 `native_reason_code` 记录。其他值记 `None` 与 `native_reason_redacted=True`；字段缺失时记 `reason_source='missing'`、`native_reason_code=None`、`native_reason_redacted=False`。规则宁可漏掉不安全的原生值，不把任意网关文本放入日志。该过滤不是对任意输入的绝对脱敏保证；真实供应商字段与访问权限仍需后续验证。
4. `unknown` 响应目前允许的工具执行、checkpoint 与 `run_completed` 行为不变。事件只记录**逻辑模型调用**，不声称有 SDK 每次重试的独立轨迹或持久化事件库。`run_started` 的 `run_id/session_id` 可与模型事件的 `run_id` 关联。

## 影响文件与测试

- `model_access/errors.py`：仅在模型接入层提取安全异常诊断字段，不更改 `classify_model_failure` 的返回签名。
- `model_access/stop_reason.py`：提取 unknown 元数据字段来源和受限枚举码，不更改归一化签名。
- `agent_runtime/__init__.py`：在现有模型 middleware 的成功/失败分支输出上述结构化日志；不增加事件总线。
- `tests/test_agent_runtime_unknown_observation.py`：新增聚焦的应用接线测试，覆盖未知异常类型、OpenAI SDK 未分类状态、未知结束值并有有效工具、缺失元数据、恶意/超长原生值脱敏；验证原异常身份、事件关联、工具执行次数、run 终局及无正文/凭证泄露。
- `tests/test_agent_runtime.py`：仅同步更新两条对 `unknown` 失败事件形状的精确断言；不放宽原有身份、终局和脱敏断言。
- README、后续清单和交接仅记录真实完成情况，不把日志当作持久评测仓库。

先运行全量基线；写失败测试，确认缺少观测字段导致红灯；最小实现后运行定向与全量 pytest、`compileall`、`pip check`。L1 验证代码和测试存在；L2 验证只抽取受限字段；L3 验证通过 `build_local_application → app.run → create_agent` 的实际 middleware 路径产生日志，且当前 unknown 工具策略未改变。

## 实际验证与边界

- 基线 `.venv/bin/python -m pytest -q`：`140 passed in 3.76s`。新增 9 个参数化场景后，定向 `tests/test_agent_runtime_unknown_observation.py` 为预期 `9 failed in 0.90s`：未知异常缺 `error_type`，未知响应没有独立日志事件。最小实现后同文件 `9 passed in 0.83s`。后补 Anthropic/Ollama 异常、典型密钥前缀和混合有效/无效工具调用的观测验证，未放宽原有断言。
- 新字段使 `tests/test_agent_runtime.py` 的两处旧精确事件形状断言失败；只同步更新这两处对 `unknown` 新增 `error_type` 的预期，保留原异常身份、run 终局和脱敏断言。定向 `tests/test_agent_runtime_unknown_observation.py tests/test_agent_runtime.py`：`33 passed in 0.90s`（追加最后一个混合调用验证之前）。最终全量 `.venv/bin/python -m pytest -q`：`153 passed in 3.84s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。
- L1/L2/L3：异常诊断在 `model_access/errors.py` 只从异常类与限定 SDK 类型的 100–599 数字状态取值；结束原因诊断在 `stop_reason.py` 只取已知字段名及短 ASCII 小写下划线枚举码，排除常见敏感词/凭证前缀，其余标记脱敏。运行层通过真实 `create_agent` + 假模型在模型调用边界记录。测试证明未知结束原因仍可放行有效工具并正常完成；若同条消息还有无效调用，仍按 Spec 020 整条拒绝，两个工具调用数量照常入观测事件。
- 当前是进程日志上的结构化字段，未实现持久化或查询/评测聚合。受限枚举码过滤是保守启发式，不能证明任意网关值绝无敏感信息；如需采集不可确认安全的值，须另定隔离与审查方案。未调用真实未知供应商响应，也没有 SDK 内部每次重试的独立事件。
