# Spec 010：模型请求超时与 SDK 重试边界

状态：2026-09-30；已按 TDD 实施，本地验证通过。

## 当前数据流与问题

`load_model_catalog` 从本地 `models` 条目取得供应商与模型名，`_build_model` 构造 LangChain `BaseChatModel`；运行层选模 middleware 调用 `handler(request.override(model=model))`，只观察终局失败。当前构造没有显式请求超时或 SDK 重试设置。已安装的 OpenAI/Anthropic SDK 默认各重试 2 次、HTTP 超时默认约 10 分钟；Ollama 客户端默认 timeout 为 `None`。因此现有模型调用次数及等待行为由不同 SDK 的默认值隐式决定。

## 本切片契约

- 每个 `models.<id>` 可选 `request` 对象，允许 `timeout_seconds` 与 `sdk_max_retries`。缺少 `request` 或缺少其中某个字段时，分别采用项目默认 `60` 秒与在线 SDK 重试 `2` 次。重试次数指初次尝试之后的额外尝试；可重试故障最多尝试 3 次，认证等不可重试故障不会强行尝试 3 次。
- `timeout_seconds` 必须是有限的正数，不能是布尔值；`sdk_max_retries` 必须是非负整数，不能是布尔值。未知字段或不合法值在模型构造及网络请求前以 `ModelConfigError` 拒绝，不输出密钥。旧配置没有 `request` 字段时保持可读，`schema_version` 仍为 1；本次不修改真实 `.env/models.json`。
- `openai`、`openai_compatible`、`anthropic` 均把有效值传给适配器的 `timeout` 与 `max_retries`。`ollama` 仅通过 `client_kwargs.timeout` 设置 60 秒或显式覆盖值；当前没有受支持的 SDK 重试参数，若其 `request` 显式给 `sdk_max_retries`，必须拒绝而非静默忽略。Ollama 重试设计待有真实本地模型时另开切片。
- 只补 OpenAI SDK `APITimeoutError → transient/timeout`、`APIConnectionError → transient/connection_error`；先判断超时子类。现有 middleware 仅记录最终 `model_call_failed` 并原样抛出，不再另加 `ModelRetryMiddleware`，不把终局异常转成 `AIMessage`。
- HTTP 客户端的 `timeout` 不等同整个 Agent turn 的严格墙钟截止；多个网络阶段、重试和多轮模型调用会增加总耗时。任务级预算、取消、流式部分输出后的重试留到运行层切片。

## 影响文件与测试

- `naiyue_test_agent/model_access/__init__.py`：校验可选请求配置，按供应商传适配器参数；不改公开 `load_model_catalog` 与 `ModelCatalog.get` 签名。
- `naiyue_test_agent/model_access/errors.py`：添加上述两类 OpenAI SDK 异常的保守分类。
- `tests/test_model_access.py`：先写失败测试覆盖旧配置默认值、显式覆盖/部分覆盖、各适配器接线、非法值与 Ollama 不支持重试；用本地 HTTP mock 核验 OpenAI SDK 对可重试状态默认最多 3 次、配置 0 时只有 1 次。
- `tests/test_agent_runtime.py`：经现有 Agent 装配测试 SDK 超时/连接异常最终事件各一条、原异常传播、日志不泄漏正文。
- `docs/specs/004-local-model-config-format.md`、`docs/handoff.md`、`docs/future-work.md`：同步配置契约、验证结果与遗留边界。

验收顺序：先运行新测试确认预期红灯，再做最小实现；定向与全量 pytest、`compileall`、`pip check` 通过。真实网关不故意触发超时或 5xx；本地 HTTP mock 可证明 SDK 请求次数，真实供应商的长尾响应和计费行为不在本切片证明范围。

## 实际验证

- 修改前基线 `.venv/bin/python -m pytest -q`：`65 passed in 1.15s`。新增测试的定向红灯：`13 failed, 2 passed in 1.98s`，分别定位缺少项目默认传参、`request` 字段被拒绝及 OpenAI SDK 错误落入 `unknown`。
- 最小实现后同组定向测试：`15 passed in 1.93s`。随后用本机 HTTP mock 分别验证 OpenAI 与 Anthropic 适配器配置实际进入 SDK：503 默认 `sdk_max_retries=2` 时各请求 3 次，显式 `0` 时各请求 1 次。Anthropic 测试最初把 mock 客户端注入为不受该适配器支持的模型参数，测试装配错误；改在 LangChain 创建底层 HTTP 客户端的边界注入后，定向测试 `2 passed in 1.94s`。
- 最后补充配置驱动 Agent 装配的默认传参断言后，全量 `.venv/bin/python -m pytest -q`：`82 passed in 3.42s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。
- 确定性证据覆盖配置默认/覆盖/拒绝、四类适配器参数接线、两家在线 SDK 可重试失败的实际尝试次数，以及 OpenAI SDK 超时/连接失败经 Agent middleware 的终局脱敏事件。未发送真实网关故障请求，未验证服务器是否已经处理一次超时请求，也未实现任务级截止或 Ollama 重试。
