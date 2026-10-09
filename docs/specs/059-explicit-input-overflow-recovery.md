# Spec 059：明确输入溢出后的强制压缩与一次模型请求恢复

状态：2026-10-07，已按 TDD 接入并完成确定性验证。

## 当前链路与框架边界

本地应用的新 run 在 `before_agent` 按配置窗口做预防压缩；工具与运行中消息会在后续轮次增加输入。`select_model → 项目系统提示词装配 → observe_model_request → SDK/框架模型执行` 的失败目前直接抛出。需要处理供应商明确拒绝输入过大的情况，即使本地近似估算低于软阈值，也应尝试缩短当前实际请求。

Pi `a69bef789` 的 `isContextOverflow → AgentSession._checkCompaction → _runAutoCompaction` 识别失败、成功压缩后继续一次，恢复所需摘要失败则终止。它另外有部分 length 的恢复和静默溢出启发式，本片不复制。AgentX `3fcfa0e` 已核对摘要策略调用链，没有同形状的模型请求失败后恢复接口。

本机 LangChain 1.4.2 源码 `middleware/types.py` 的 `ExtendedModelResponse` 与 `factory._build_commands` 允许模型 wrapper 返回消息响应及额外 Command。框架先应用新模型响应，再应用 wrapper Command。故替换消息时 Command 必须包含 `RemoveMessage(REMOVE_ALL_MESSAGES) + 摘要 + 保留后缀 + 本次新响应`，否则清空消息会丢掉新 AI/结构化结果工具消息。内层 handler 返回 ModelResponse，外层选模仍校验本次新 AI；不直接写 request.state、checkpointer 或重跑 app.run。

## 本片契约

- 模型接入层识别可信 SDK 类型、HTTP 状态与白名单信号：OpenAI/兼容网关 SDK 的 HTTP 400 `code=context_length_exceeded`，或明确的 input exceeds context window 消息；Anthropic SDK HTTP 400 的 `prompt is too long` 消息。只在内存检查错误字段，不记录原错误正文。伪造同样文本的普通 Python RuntimeError、429、认证、未知 400、普通 length 不恢复。分类为 `request_rejected/context_window_exceeded`。
- 只在本地交互式 runtime 接线恢复 middleware，置于选模与项目指令装配之后、请求观测之前。使用失败 ModelRequest 的 model/system_message/messages/tools；失败请求没有提交 AI 响应，因此恢复只重发该模型节点，不重复执行之前的工具。下一次请求仍经过内层观测和既有 SDK 参数。
- 共享现有压缩策略为内部 `_compact_messages(..., force=False)`，原 `make_context_compaction_middleware` 公开签名与 before_agent 行为保持。第二个实际调用点用于恢复，抽取是为复用同一切点/序列化/预算/摘要算法，不能维护两套。强制模式跳过软阈值及“原请求仍能发送就跳过/失败后回退”的规则，但不绕过安全切点、配对、摘要及发送预算检查。摘要后的完整请求须比原请求近似估算更小，否则 `compaction_not_reducing`，不把原历史再发一遍。
- 强制模式在摘要前校验整份原历史；不完整工具对立即失败。本片沿用现有文本校验，包含非字符串内容块的多模态原历史暂不恢复，记录明确失败；不能静默丢弃图片等内容来缩小请求。
- 每次连续失败的模型调用最多做一次强制压缩和一次 handler 重发。第二次任何异常直接终止；压缩/摘要失败也终止，不使用 Spec 058 的回退。重试成功后未来独立的模型调用仍可处理新的溢出，当前不设整个 run 总恢复次数/耗时/费用预算；Pi 也会在成功助手消息后清除恢复标志。
- 只有 handler 重发返回成功，wrapper 才返回替换状态 Command。第二次失败或摘要失败，不提交新摘要；原 checkpoint 历史保留，包括已执行工具结果。外层模型响应门禁仍可能拒绝返回值，该情况也不提交 Command。结构化响应字段和新模型结果列表完整保留。
- 事件为 `context_overflow_detected → context_overflow_retry_started → context_overflow_retry_returned`；失败时 `context_overflow_recovery_failed(stage=compact/retry)`。returned 只表示重发 handler 返回，不等于 run_completed、门禁已通过或 checkpoint 已提交。关联 session/run/model 与固定规则码/安全异常类型，不记消息或原错误。原请求观测有 failed，重发观测有 returned/failed；外层 select_model 的终局事件针对最终结果，SDK 的内部请求重试仍是独立层。

## 影响文件与 TDD

- `model_access/errors.py`：新增识别函数，分类器复用同一判断，不新建错误类别。
- `agent_runtime/context_compaction.py`：复用提取策略、强制模式和严格缩小校验；既有助手函数导入路径不变，单文件低于 500 行。
- 新 `agent_runtime/context_recovery.py`：恢复 wrapper 和成功后状态 Command；与模型选择/运行控制职责分开。
- `agent_runtime/__init__.py`：本地交互式装配注册 wrapper，无公开签名变更。
- 新 `tests/test_context_overflow_recovery.py`：复用现有压缩假模型的摘要记录及配置 helper，以子类增加正常调用失败序列；不第三次复制同套配置，不修改 conftest。
- Spec、handoff、future-work、project-plan 更新结果；无 `.env`、依赖或《关键知识点》改动。

基线全量后先写红灯：低于本地阈值仍识别明确拒绝并压缩，原 SDK 异常允许名单/反例，已执行工具只一次，恢复响应含新工具调用仍执行一次，重发失败只一次/无状态替换，强制摘要失败不回退，无法切分与压缩不缩小失败，项目指令实际快照、checkpoint 继续对话和模型响应拒绝。定向及全量 pytest、compileall、pip check。先做确定性 SDK 样本与真实 create_agent 接线，不刻意向真实供应商发送超大输入，不宣称供应商真实窗口/故障恢复已验证。

## 实施与证据（2026-10-07）

- 新增 20 个测试，首轮 `.venv/bin/python -m pytest -q tests/test_context_overflow_recovery.py --tb=short` 为 `14 failed, 6 passed in 2.28s`。失败主要是明确 SDK 输入拒绝原样终止、恢复与强制模式未接线；普通 length 和非溢出错误原有行为通过。其中项目指令样本用错会话公开方法，改为现有 `create_workspace_session` 后，单例再次因缺少模型恢复链路失败；不把错误测试调用当作产品缺陷。
- 抽取 `_compact_messages` 是为 before_agent 与恢复 wrapper 两个实际调用点共享同一策略，保留原公开签名与普通压缩路径。强制模式不按本地软阈值跳过、不做原历史回退，要求完整文本/工具对安全且结果近似估算严格变小。新 wrapper 在项目提示词装配后使用实际失败请求，handler 仅重发一次，`ExtendedModelResponse` 的额外 Command 重建消息并包含新响应，以免清空 reducer 丢掉新 AI/结构化结果。
- 初次实现 20 个专项通过；补充结构化响应测试通过。复核新增不完整工具历史反例先红灯：原实现只校验被摘要前缀，仍尝试重发不完整后缀；加 force 全历史检查后转绿。专项最终 `.venv/bin/python -m pytest -q tests/test_context_overflow_recovery.py --tb=short` 为 `22 passed in 0.89s`。所有用例复用旧假模型及配置 helper，不新增 conftest 或全局测试基础设施。
- 最终 `.venv/bin/python -m pytest -q` 为 `515 passed in 15.35s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。这些是 SDK 类型样本与真实 create_agent/内存 checkpoint 的确定性证据，未制造真实供应商溢出或跨重启恢复。
- 已验证低于配置阈值仍强制压缩、系统/项目提示词冻结、原工具只执行一次、重试产生新工具调用仍路由一次、结构化对象及配套 ToolMessage 保留、重发失败与响应门禁拒绝不提交新摘要、后续同会话续聊使用压缩历史。`retry_returned` 是内层 handler 返回的观测，不等于外层门禁/状态提交/运行终局；压缩计算成功的事件也不能单独证明 checkpoint 替换已提交。
- 限制：仅识别白名单 SDK 拒绝，其他网关错误码、供应商、本地模型、413 请求字节超限、部分 length、静默截断和多模态强制恢复未覆盖。SDK 自身重试仍独立，整个 run 总恢复次数/耗时/费用预算未实现。用户确认方案并讨论机制，新增代码尚未逐段学习，无独立实现或面试追问证据。
