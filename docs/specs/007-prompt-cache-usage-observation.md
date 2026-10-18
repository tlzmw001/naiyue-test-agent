# Spec 007：提示词缓存用量观测

当前补充（2026-10-18）：[M6两片](107-model-request-prompt-cache.md#9-m6-02当前实现与下一块准备)已接模型profile能力及构造默认、总输入/输出/缓存读写口径、异步主子/摘要/Auto观察及快照/UI；有界探针已迁异步，未执行实网。下文为本Spec成稿时的历史边界，同步运行入口及局部解析已由当前主链替代。

状态：2026-09-30；已按 TDD 实施并验证本切片。缓存控制参数留给后续切片。

## 当前数据流与问题

`build_runtime_agent` 将选模 middleware 装入 `create_agent`。一次同步模型调用依次经过可信 `session_id`、`resolve_model`、`record_selection`、`handler(request.override(model=model))`，然后返回 LangChain `ModelResponse`；失败路径已有脱敏事件。成功路径当前直接返回，没有记录供应商报告的缓存用量。`langchain-openai` 会把 OpenAI 响应中的 `cached_tokens` 放到 `AIMessage.usage_metadata.input_token_details.cache_read`，但兼容网关是否返回该值尚未验证。

供应商的提示词前缀缓存不同于 LangChain 整条回答缓存。对支持自动缓存的 OpenAI 模型，未传控制参数仍可能命中；传参数本身也不证明命中。Pi 的供应商适配器既装配缓存参数也读取用量，本切片只实现后者。

## 本切片契约

1. 仅在同步 `handler` 正常返回后记录一条 `model_call_usage` 标准库日志事件，不修改 `ModelResponse` 或 Agent 最终消息。字段为 `event`、`model_id`、`input_tokens`、`cache_read_tokens`。后两项缺失时为 `None`，与供应商明确报告的 `0` 区分。
2. 从本次 `ModelResponse.result` 的 `AIMessage.usage_metadata` 读取数据；不从提示词长度、请求参数或延迟推算命中。日志不包含消息正文、原始响应、密钥、会话 ID 或异常正文。
3. handler 前失败或 handler 抛异常不记录成功用量事件；现有失败事件与异常传播保持不变。
4. 不新增配置字段、缓存开关、SDK 参数、依赖或公开 API 参数；不修改 `.env`。异步/流式独立调用、重试聚合与多供应商实际用量字段留待后续验证。

## 影响文件与验证

- `naiyue_test_agent/agent_runtime.py`：复用现有模型调用边界，在成功路径提取、记录用量。
- `tests/test_agent_runtime.py`：先写失败测试，使用假 `AIMessage` 验证正数、零、缺失及原回答不变；检查失败路径没有成功事件。
- `scripts/probe_prompt_cache.py`：用现有本地模型配置和合成的相同长前缀做两次有界真实请求，只打印状态与 token 统计，不打印提示词或回答；它验证当前网关是否报告用量，不作为 OpenAI 官方端点行为证明。
- `docs/handoff.md`、`docs/future-work.md`：记录红绿灯、真实网关结果和未完成控制能力。

验收：先看到断言缺少成功事件的预期红灯；定向与全量 pytest 通过，`compileall` 退出码 0，`pip check` 通过。真实网关实验需区分 `cache_read_tokens > 0`、`= 0` 和 `None`；重复前缀不保证命中，第三方网关即便报告数值也只作为它自身的报告。失败或未返回字段不得宣称命中。

## 实际验证（2026-09-30）

- 红灯：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py::test_successful_model_call_records_reported_cache_usage` → `4 failed in 0.90s`，四种情况均因 `model_usage` 事件缺失而失败。
- 绿灯：`.venv/bin/python -m pytest -q tests/test_agent_runtime.py` → `10 passed in 0.83s`；`.venv/bin/python -m pytest -q` → `42 passed in 0.90s`；`compileall` 退出码 0；`pip check` → `No broken requirements found.`。
- 网关实验：`.venv/bin/python -m scripts.probe_prompt_cache --model-id heiyu-gpt-5.5` → 首次 `input_tokens=7399, cache_read_tokens=None`，第二次 `input_tokens=7399, cache_read_tokens=6912`，`status=PASS requests=2`。两次请求使用同一合成前缀，没有传缓存策略参数。第二次的正值是 HeiyuCode 经 LangChain 报告的缓存读取证据；首次缺失不是零，也不能从这两次请求证明 OpenAI 官方端点的计费、后端身份或长期命中率。
