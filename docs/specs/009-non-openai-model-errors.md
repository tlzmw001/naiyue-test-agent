# Spec 009：Anthropic 与 Ollama 基础模型错误分类

状态：2026-09-30；已按 TDD 实施并完成本地验证。只改模型接入层分类，沿用已接线的同步 Agent 失败事件。

## 当前数据流与证据

`create_agent` 的同步模型调用经 `agent_runtime` middleware 执行 `handler(request.override(model=model))`；异常进入 `model_access.errors.classify_model_failure`，运行层记录固定 `category/reason` 后原样抛出。当前只识别 OpenAI `AuthenticationError` 与 Python 内建 `TimeoutError`。`load_model_catalog` 已能构造 `ChatAnthropic` 与 `ChatOllama`，但没有这两家的真实服务调用证据。

本机 `anthropic` SDK 具备 401/403/404/429、连接/超时及服务端错误类；`ollama` SDK 将 HTTP 失败包装为含 `status_code` 的 `ResponseError`，无 HTTP 状态的流式失败可为 `-1`，连接失败可能是内建 `ConnectionError`，HTTPX 超时可直接传播。分类只依据异常类型或数值状态，不解析、记录原始错误正文。

## 契约

| 来源 | 稳定输入 | 项目分类 |
| --- | --- | --- |
| Anthropic | `AuthenticationError` / `PermissionDeniedError` | `access_denied/authentication_failed` / `access_denied/permission_denied` |
| Anthropic | `APITimeoutError` / `APIConnectionError` | `transient/timeout` / `transient/connection_error`；必须先判断超时子类 |
| Anthropic | `NotFoundError` | `request_rejected/not_found` |
| Anthropic | `RateLimitError` | `limit_exceeded/limit_unspecified`，不据此自动重试 |
| Anthropic | `APIStatusError` 且 `status_code >= 500` | `transient/server_error`；不据此自动重放 |
| Ollama | `ResponseError.status_code` 为 400、401、403、404、429、5xx | 分别为 `request_rejected/bad_request`、`access_denied/authentication_failed`、`access_denied/permission_denied`、`request_rejected/not_found`、`limit_exceeded/limit_unspecified`、`transient/server_error` |
| Ollama/HTTPX | 内建 `ConnectionError`、`httpx.TimeoutException` | `transient/connection_error`、`transient/timeout` |

Anthropic 400 可能含消费上限，不能一律判为请求格式错误；未在表中的状态与 Ollama `ResponseError(status_code=-1)` 保持 `unknown/unclassified`。所有分类都是诊断事实，是否重试需要后续单独判断响应阶段、副作用及预算。现有 OpenAI 认证失败和内建超时行为不得回归。

## 实现与验证

- `naiyue_test_agent/model_access/errors.py`：按更具体类型优先顺序加入上述判断，不改公开签名、事件结构或依赖。
- `tests/test_model_access.py`：先用已安装 SDK 原生异常与合成状态码写表驱动红灯，覆盖映射、未知/歧义边界、无正文依赖；再用本机 HTTP mock 验证两家 SDK 的代表性状态码到异常对象的转换。
- `tests/test_agent_runtime.py`：用 Anthropic 与 Ollama 各一个代表异常经 `build_runtime_agent` 验证失败事件、同一异常对象传播及脱敏，确保不是未接线的纯函数。
- `docs/handoff.md`、`docs/future-work.md`、`docs/project-plan.md`：记录红绿灯、代码入口与证据边界。

验收：先见分类断言的预期失败，再使定向与全量 pytest 通过，`compileall` 退出码 0、`pip check` 无损坏。没有 Anthropic 凭证和本地 Ollama 服务时，只能报告安装版本源码与确定性本地测试，不能声称供应商真实调用已验证。

## 实际结果

- 红灯：`.venv/bin/python -m pytest -q tests/test_model_access.py::test_non_openai_sdk_failures_have_safe_coarse_classification tests/test_agent_runtime.py::test_non_openai_failure_reaches_agent_event_without_leaking_body` 输出 `17 failed, 3 passed in 0.78s`；失败点是原实现返回 `unknown/unclassified`。
- 同一命令转绿：`20 passed in 0.53s`。新增 HTTP mock 测试分别使 Anthropic SDK 401 产生 `AuthenticationError`、Ollama SDK 404 产生 `ResponseError`，并经过分类函数；两项定向测试 `2 passed in 0.52s`。这不是实际供应商服务请求。
- 全量 `.venv/bin/python -m pytest -q` 输出 `65 passed in 0.72s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。
- Agent 接线测试经 `build_runtime_agent` 的同步调用边界观察 `model_call_failed`，核对固定分类、原异常对象传播、无成功用量事件及敏感正文不入日志。尚未验证真实 Anthropic/Ollama 服务、流式或异步路径。
