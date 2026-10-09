# Spec 050：上下文压缩接线可行性探针

状态：2026-10-03，框架探针已运行；本片未接入正式运行层的压缩。

## 当前数据流与问题

`SessionService.run → create_agent → 运行层模型选择 → 模型请求观测 → ChatModel → checkpoint`。Spec 049 已让每个模型有窗口假设和单次输出上限，并在成功响应后记录 `input_tokens`。当前没有压缩预留量、发送前的触发决策、消息摘要或上下文溢出恢复。`model_context_budget.threshold_exceeded` 只表示响应后观测的满额输出预算，不是压缩条件。

Pi 当前源码 `a69bef789` 的自动阈值以 `contextWindow - reserveTokens` 为界，默认 reserveTokens=16384，压缩后目标保留最近约 20000 tokens；已完成的模型响应超限时压缩但不重试，可识别溢出/可恢复截断时压缩并最多重试一次。AgentX 当前源码在装配会话历史时调用独立 token 策略；其摘要策略按消息数阈值判断。这些是参考项目的实际路径，不直接决定本项目实现。

当前安装 LangChain 1.4.2 的 `SummarizationMiddleware` 在 `before_model` 计数、选切点、生成摘要，并用 `RemoveMessage(REMOVE_ALL_MESSAGES)` 加摘要和保留消息更新状态；其 `model`、profile/`fraction` 阈值在中间件实例构造时绑定。本项目每次模型调用可选不同模型，不能在未经探针验证时把内置的固定模型阈值当作会自动跟随选模。

## 本片范围与问题

新增一个不接入产品入口的确定性探针，用当前 `create_agent`、本项目现有选模 middleware、假模型、工具和内存 checkpointer 回答：

1. 同一 thread 先用模型 A 完成工具调用，再切到模型 B。固定 `SummarizationMiddleware` 的 `fraction` 触发条件实际依据哪个模型的窗口？摘要生成由哪个模型负责？
2. 切点落在 `AIMessage.tool_calls` 和对应 `ToolMessage` 之间时，压缩后的模型输入与 checkpoint 是否保留成对消息？之前的 checkpoint 是否仍可追溯？
3. 摘要模型失败时是否把失败传播为异常，checkpoint 留下什么状态？此问题由当前安装源码和探针分别核对；checkpoint 尚未测试恢复入口，不能直接称为可恢复。

探针仅输出消息类型、调用 ID、所选模型、摘要次数和 checkpoint 结构；不打印真实请求、提示词正文或凭证。不联网、不改 `.env`、依赖、现有 API 或生产 middleware。探索性实验无 TDD 红灯声明。

## 形成后续实施 Spec 的判据

- 明确压缩预留量与 `max_output_tokens` 的不同职责；以 `context_window_tokens=128000`、暂定 `reserve_tokens=16384` 举例，阈值是 111616。实际默认值及配置入口由正式实施 Spec 决定，不由探针暗中定稿。
- 明确是否复用内置中间件，或以薄的自定义适配实现动态模型窗口与 Pi 式响应后用量/溢出路径；必须避免先摘要后得不到安全切点、重试无限循环、工具调用结果脱配和静默丢失历史。
- 正式实现须先写确定性失败测试，再接线；至少覆盖阈值边界、切模、工具对、摘要失败、checkpoint 和最多一次溢出重试。真实模型用量、摘要质量与费用另作集成/评测，不由本探针证明。

## 探针结果

命令：`.venv/bin/python -m scripts.probe_context_compaction`，退出 0。假模型及本机 LangChain 1.4.2 + LangGraph 1.2.12；无网络、无真实供应商调用。可复现脚本见 `scripts/probe_context_compaction.py`。

项目回归：`.venv/bin/python -m pytest -q` 输出 `432 passed in 15.04s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。探针是探索性实验，并非新功能测试红灯/转绿或生产压缩能力。

```text
success selected=B summary_calls=1 bound_profile=100 active_profile=200
after_compaction=summary,ai_call:probe-1,tool:probe-1,ai,human,ai
previous_checkpoint=human,ai_call:probe-1,tool:probe-1,ai
failure summary_attempts=3 active_model_calls=0 checkpoint=human,ai_call:probe-1,tool:probe-1,ai,human
```

- 固定摘要模型 profile 的 `max_input_tokens=100`，触发值为其 50%=50；假 token 计数为每条消息 15。首 run 的第一次模型前 1 条、工具后模型前 3 条，均不触发；切到 B（其 profile 为 200）后，下一 run 模型前共 5 条、计数 75，触发摘要一次。若使用 B 的窗口，50% 应为 100，此处不应触发。源码 `SummarizationMiddleware.__init__` 固定 `self.model`，`_get_profile_limits` 读该模型；项目 `make_model_selection_middleware` 只在 `wrap_model_call` 替换实际回答模型。结论：内置 `fraction` 不能未经适配就代表本项目的动态当前模型窗口。摘要请求由构造时的摘要模型发出。
- `keep=('messages', 3)` 的初始切点正落在 ToolMessage；框架 `_find_safe_cutoff_point` 回退到对应 AI 工具调用之前，B 模型实际收到 `summary → AI tool_call → ToolMessage → AI → Human`。返回后当前 checkpoint 是这条压缩状态加 B 的 AI；用压缩前保存的 checkpoint ID 仍能读到原始四条消息。这只证明进程内 `InMemorySaver` 的本例，不证明重启持久化、所有多工具结构或历史分支界面。
- 摘要模型每次抛合成 `RuntimeError` 时，内置重试共尝试 3 次，最终原异常向上传播，B 回答模型 0 次调用。失败后的最新 checkpoint 包含完整原始工具对和新加入的 Human，没有摘要。当前未验证再次调用/恢复的去重语义，也未验证真实提供商的失败分类或实际成本。

下一实施切片先决定动态阈值契约：至少基于当前所选模型的 `context_window_tokens`，同时区分输出上限与压缩预留量。内置 middleware 的安全切点和状态更新已有本机可复用证据，但不能原样采用其固定 `fraction` 触发方式。正式实施前还须明确压缩是在响应后、下一次模型前，还是两处各有职责；摘要结果如何关联原文和 checkpoint；识别溢出的条件及最多一次重试的保护。以上尚未实现。

## 后续设计方向（2026-10-03，尚未实施）

用户确认压缩策略尽可能参照 Pi，而不把 LangChain 内置 `SummarizationMiddleware` 直接接入产品。借鉴的是当前模型窗口减预留量的阈值、最近内容保留与安全切点、旧内容及前次摘要的续写、压缩记录的可追溯性，以及会话结束/新请求前和可识别溢出时的处理时点。继续使用 `create_agent` 的模型—工具循环、消息状态更新和 checkpoint；不重写运行时，也不逐字移植 Pi 的 TypeScript 会话树。

正式实施先另写 Spec 并分片：先做当前选中模型的发送前预算/触发判定，再做完整旧轮次的摘要与保留，最后根据测试和评测证据决定轮次中途切分、溢出恢复及持久化分支如何接入。摘要不能静默裁掉待移除历史；无安全切点、摘要输入超限或摘要失败时不得伪造成功。各片明确原始历史可追溯的实际存储边界，不能用本探针的内存 checkpoint 证据声称跨重启恢复。
