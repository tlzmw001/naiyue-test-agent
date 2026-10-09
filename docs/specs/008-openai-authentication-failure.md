# Spec 008：OpenAI SDK 认证失败分类

状态：2026-09-30；已按 TDD 实施并验证。本切片只处理同步模型调用中的一种 SDK 错误。

## 当前数据流与问题

`create_agent` 的模型调用经 `agent_runtime.make_model_selection_middleware` 进入 `handler(request.override(model=model))`。handler 抛异常时，运行层调用 `model_access.errors.classify_model_failure`，记录脱敏 `model_call_failed` 事件，再把同一异常对象抛给调用方。目前分类函数只识别内建 `TimeoutError`；OpenAI SDK 的 HTTP 401 `AuthenticationError` 被归为 `unknown/unclassified`，无法准确提示这是访问凭证问题。

## 行为契约

1. 对已安装 OpenAI SDK 的 `AuthenticationError`，返回 `('access_denied', 'authentication_failed')`；保持内建超时和其他异常的现有映射。不要把所有 401/403、所有 `APIStatusError` 或异常文字都混为认证失败。
2. 不改变运行层事件结构、公开函数签名或异常传播：模型调用边界只记录固定 category/reason 和模型 ID，不输出异常正文、响应正文或凭证；`agent.invoke` 调用方收到原异常对象。无自动重试、回退模型或普通 `AIMessage`。
3. 先构造 SDK 原生异常做确定性集成测试，经项目 `build_runtime_agent` 实际接线验证分类与安全日志。再用与本地配置同一网关的合成无效凭证做一次受限请求，只打印异常类型、状态码和项目分类；不使用或修改真实凭证，不将网关响应正文写入日志。
4. 真实网关若返回的不是 OpenAI SDK `AuthenticationError`，如实记录，不为使探针成功而放宽分类规则；区分 SDK 类型测试与网关实测。

## 影响文件

- `naiyue_test_agent/model_access/errors.py`：增加单一 OpenAI SDK 类型判断。
- `tests/test_agent_runtime.py`：先写红灯集成测试，使用 SDK 原生 `AuthenticationError` 和含敏感标记的合成异常正文，验证分类、脱敏、同一对象传播及没有成功用量事件。
- `scripts/probe_openai_auth_failure.py`：从既有模型配置读取目标网关与模型名，生成一次性无效凭证并限制重试/超时；只输出安全诊断字段。
- `docs/handoff.md`、`docs/future-work.md`、`docs/project-plan.md`：记录结果和剩余分类范围。

## 验收

红灯失败点必须是原本的 `unknown/unclassified` 分类；定向及全量 pytest 通过，`compileall` 退出码 0、`pip check` 无问题。真实探针只证明此网关对无效凭证的当次响应及 SDK 映射，不证明所有 OpenAI-compatible 网关、流式后期错误或生产凭证轮换能力。

## 实际结果（2026-09-30）

- 红灯：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_openai_authentication_failure_is_classified_without_leaking_response` → `1 failed in 0.78s`；原事件为 `unknown/unclassified`，测试期望 `access_denied/authentication_failed`。原异常已透传，合成敏感正文未进入日志。
- 绿灯：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py` → `11 passed in 0.55s`；全量 `.venv/bin/python -m pytest -q` → `43 passed in 0.75s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。
- 当前网关探针：`.venv/bin/python -m scripts.probe_openai_auth_failure --model-id heiyu-gpt-5.5` → `status=PASS error_type=AuthenticationError http_status=401 category=access_denied reason=authentication_failed`。只发送随机无效凭证和合成短提示，禁用 SDK 重试；没有使用真实凭证发请求。探针没有经过 Agent middleware，事件接线由确定性集成测试证明。
