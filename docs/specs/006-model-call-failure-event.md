# Spec 006：模型调用失败事件与终局异常传播

状态：2026-09-30；已按本切片契约实现并通过确定性验证。继续复用 LangChain `create_agent` 的循环，不改变会话选模时点。

## 当前数据流与问题

`build_runtime_agent` 装配 `make_model_selection_middleware`。每次模型调用从可信运行 context 取 `session_id`，解析 `(model_id, model)`，记录选模尝试，再执行 `handler(request.override(model=model))`。如果 handler 抛异常，当前 middleware 不记录本次模型调用失败；异常虽向外传播，但缺少脱敏的失败轨迹。配置加载失败发生在此链路之前，不纳入本切片事件。

Pi 在模型 API 边界产生错误事件，上层循环收到错误后结束本轮；AgentX 在模型流错误回调记录失败。我们沿用 Python LangChain 的模型调用边界，不复制 Pi 的事件流，也不把异常转成普通 `AIMessage`。

## 本切片行为契约

1. 在选模成功并记录选模尝试后，`handler(request.override(model=model))` 成功则照旧返回；失败时生成一条 `model_call_failed` 日志事件，然后使用裸 `raise` 将同一个异常对象传播给 `agent.invoke` 调用方。不得用失败文字构造 `AIMessage`，不得自动重试或切换模型。
2. 事件仅含 `event="model_call_failed"`、可信解析出的 `model_id`、`category`、`reason`。日志正文只含固定事件名与分类，不写异常文本、原始提示词、凭证或响应内容；原始异常仅沿 Python 异常通道传播，由未来应用入口负责安全展示。没有现成 trace ID，本切片不虚构会话追踪能力。
3. 首批分类只覆盖可确定的 `TimeoutError → (transient, timeout)`；其他 `Exception → (unknown, unclassified)`。事件表示模型调用边界失败，不声称未知异常一定由供应商造成。更细的 OpenAI/Anthropic/Ollama SDK 映射、取消、流式部分输出、超时预算及重试另起切片验证。
4. 缺少 `session_id`、无法解析模型 ID、`record_selection` 自身失败都发生在模型 handler 前，继续原样抛出，不伪造 `model_call_failed`。只有 handler 抛出的异常才记此事件。
5. 不修改现有公开函数签名、配置格式、`.env`、依赖或真实模型诊断脚本。本切片只支持现有同步 `invoke` 路径；异步/流式路径尚未验证。

## 影响文件

- `naiyue_test_agent/model_access/errors.py`：仅存纯异常分类函数，保持“模型层分类、运行层观测”的单向依赖；不在这里写日志或调用 Agent。
- `naiyue_test_agent/agent_runtime.py`：现有选模 middleware 在 handler 边界记脱敏事件并重新抛出。
- `tests/test_agent_runtime.py`：先以失败假模型写红灯用例，验证事件字段、日志不泄漏、异常对象身份、无正常回答；确认正常调用没有失败事件、handler 前错误没有失败事件。
- `docs/project-plan.md`、`docs/handoff.md`：同步阶段范围、运行结果与学习证据。

## 验收与证据边界

1. 先运行定向测试观察预期红灯，失败点必须是缺少日志事件或分类实现，不允许通过放宽断言变绿。
2. 定向及全量 pytest 全绿，`compileall` 退出码 0，`pip check` 无损坏依赖。检查事件在当前 `make_model_selection_middleware` 实际接线处产生，而非仅测试孤立的分类函数。
3. 此测试只证明本地同步假模型的模型调用边界。不能证明真实供应商错误归类、异步/流式路径、持久轨迹、应用安全展示、取消或自动恢复已完成。

实际结果：新增两种失败假模型用例先输出 `2 failed in 0.60s`，失败点均为期望的日志事件不存在；实现后 `.venv/bin/python -m pytest -q tests/test_agent_runtime.py` 输出 `6 passed in 0.60s`，全量 `.venv/bin/python -m pytest -q` 输出 `38 passed in 0.77s`。`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。失败测试经项目 `build_runtime_agent` 装配验证接线，且断言同一异常对象上抛与异常正文不进入日志。
