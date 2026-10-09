# Spec 049：模型窗口与单次输出上限

状态：2026-10-03，已按 TDD 实施并完成本机确定性验证与当前网关的一次短请求。

## 当前数据流与问题

`load_model_catalog → ModelCatalog.get → select_model → observe_model_request → ChatModel`。当前模型配置仅含供应商、模型、思考和请求超时/重试；逐次模型请求能记录框架输入摘要与供应商返回的 `input_tokens`，但无法计算相对于模型窗口的预算。模型对象的单次生成上限也未由项目显式配置。

参考 Pi 当前源码：自定义模型 `contextWindow`/`maxTokens` 缺省分别为 128000/16384；前者参与压缩阈值及输出空间估算，后者作为默认单次输出上限。Pi 不通过请求自动改写窗口元数据。可识别的上下文溢出走压缩并最多重试一次；错误的输出上限不能靠压缩修复。当前项目没有压缩，因此本片只配置与观测，溢出恢复在后续压缩切片实现。

## 本片契约

- 模型条目可选 `context_window_tokens` 与 `max_output_tokens`，均为正整数；不填分别采用 128000/16384。字段位于每个模型条目，不在供应商级共享。配置文件版本仍为 1；不迁移或修改现有私有文件。
- `context_window_tokens` 是本项目采用的预算假设，不宣称供应商实际能力；显式值/默认值的来源在安全观测中可区分。`max_output_tokens` 是每次模型请求的生成上限，按适配器参数映射：OpenAI/兼容网关、Anthropic 用 `max_tokens`，Ollama 用 `num_predict`。构造期不联网。
- 对已配置模型，每次有可信 `usage_metadata.input_tokens` 的成功响应，另记录 `model_context_budget`：模型 ID、窗口、单次最大输出、按满额输出计算的可用输入预算 `max(0, window - output)`、输入 token 数、是否超过这项预算及字段来源；无用量或失败则记录 `input_tokens=None` 和 `threshold_exceeded=None`。这是模型响应后的观测，不是发送前的阻断，也不是未来压缩的触发阈值。压缩预留量以后单独设计。观测不读取正文，也不改变响应/错误。
- 不自动把上限裁剪到当前消息长度，不自动压缩或重试，不把模型拒绝改造成普通回答。若模型实际窗口较小，错误沿现有模型失败路径传播；若实际输出上限较小，用户需修改模型配置。后续压缩切片再参考 Pi 做可识别上下文溢出的有界恢复，不能将任何 HTTP 400 当作溢出。

## 影响文件与接口

- `model_access/__init__.py`：校验默认值并构造模型；将非敏感配置预算和来源放入模型标准 `metadata`，供内层观测读取，不改变公开函数签名。
- `agent_runtime/context_observation.py`：保留原有 `model_request_context` 事件形状；仅当选中模型含本项目预算元数据时另发 `model_context_budget`。
- `tests/test_model_limits.py`：先写失败测试，覆盖四类适配器、默认/覆盖、非法值、逐次预算/缺失用量/异常及无敏感内容；原有 `tests/test_model_access.py` 与 `tests/test_model_request_context.py` 回归。
- `docs/handoff.md`、`docs/future-work.md`、`docs/project-plan.md`：记录实测结果、下一阶段溢出恢复及输出上限差异边界。不修改 `.env`、依赖、公开 API 或 `docs/关键知识点.md`。

## 验证标准

1. 定向测试先因缺少配置/预算行为失败，最小实现后通过；已有测试保持通过。
2. 假模型经配置运行装配 → 选模 → 两次模型调用的真实 `create_agent` 路径，预算事件每次一条，按响应实际 `input_tokens` 算可用输入预算；切模后采用新模型值。
3. `.venv/bin/python -m pytest -q`、`compileall -q naiyue_test_agent tests scripts`、`pip check` 通过。真实供应商限制、自动压缩与溢出恢复不计入本片验证。

## 实施与证据

- 定向红灯 `.venv/bin/python -m pytest -q tests/test_model_limits.py`：`3 failed`，分别因默认输出上限未设置、覆盖字段被拒绝、预算事件不可达。最小实现后相关定向回归 `.venv/bin/python -m pytest -q tests/test_model_limits.py tests/test_model_request_context.py tests/test_model_access.py` 为 `69 passed in 3.68s`。
- 全量 `.venv/bin/python -m pytest -q` 为 `432 passed in 14.60s`；`compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。上述均为本机结果，不证明不同供应商接受默认上限。
- 当前私有配置默认模型的一次真实短请求返回 `outcome=returned model_id=heiyu-gpt-5.5 input_tokens=10 output_tokens=17`，证明当前网关接受该请求的默认单次输出上限；不证明其真实最大上下文或最大输出能力。未打印回答正文、配置或凭证，未修改 `.env`。
