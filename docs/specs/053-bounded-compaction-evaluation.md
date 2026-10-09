# Spec 053：有界真实摘要探针与固定事实评测

状态：2026-10-03，已按 TDD 实施并完成两次当前私有默认网关的有界固定样本对照；不改变压缩策略或模型配置默认值。

## 当前链路与问题

本地应用在新 run 的 `before_agent` 估算窗口并调用同一模型生成摘要；摘要成功才替换消息，然后正常 `create_agent` 模型调用。现有 `context_compaction_started/completed/failed` 能关联 run，却不能区分摘要请求实际开始、返回或停在哪一阶段，也没有摘要耗时和供应商用量。一次真实网关长会话探针等待超过两分钟后人工中止；假模型测试不能回答默认 2048 输出上限是否足够保留事实。

## 本片范围和接口

1. 在现有 `agent_runtime/context_compaction.py` 的摘要模型调用边界记录安全结构化事件：`context_summary_model_started` 和 `context_summary_model_finished`。两者关联 session/run/model；开始事件包含摘要输入估算和输出上限；结束事件包含 `returned/failed`、单调时钟耗时、能取得时的供应商输入/输出 token。失败只记录异常类型，不记录正文、提示词、凭证或错误字符串。正常回答已有 `model_context_preflight` 和 `model_request_context` 事件，不另改模型调用链。
2. 增加独立的 `scripts/probe_compaction_quality.py`。父进程按总截止时间启动受控子进程，先跑压缩、再跑不压缩的对照；超时终止自身创建的子进程，并输出最后可观察阶段。子进程关闭 hosted tracing，只输出脱敏 JSON 行。命令接受已有私有配置路径、可选 model ID、摘要输出上限和总时限；不写或复制 `.env`，不修改配置对象的磁盘文件。评测专用窗口与最近保留目标只在该子进程的模型对象元数据中覆盖，明确不是供应商真实能力。
3. 两组运行使用同一段固定的合成消息：早期用户消息写入四个精确事实，后续消息加入无关长文本，最后询问四个事实。两组只改变是否触发本项目压缩；各自建立独立的本地应用和会话。使用项目真实 `build_local_application → app.run → create_agent` 接线。检查摘要是否发生、回答是否为可解析 JSON、每项事实是否精确匹配；只输出逐项布尔分数、消息/用量/耗时元数据，不打印模型回答或合成消息原文。该固定样本是机制与事实保留探针，不能代表业务质量总体。
4. 输出上限 2048 为默认实验参数；可用 CLI 指定 4096/8192 重跑，不预先改变产品默认。若模型实际用量缺失，结果显示 `null`，不能推算实际费用。总超时包含两个子进程；超时、供应商错误、摘要失败均显示失败阶段，退出非零，不伪造对照结果。

## 验证与限制

- 先写失败测试：固定事实评分的正确/错误 JSON、脱敏事件与用量、脚本父进程在模拟子进程超时后的终止/阶段报告，以及假模型经真实应用路径证明压缩组与对照组的消息形状和四事实评分。测试不依赖窄 `sleep` 时间窗。
- 实现后运行定向与全量 pytest、`compileall`、`pip check`。再以当前私有配置运行一次有界真实模型探针；记录命令、实际退出码、耗时、摘要/回答阶段、token 可用性和固定事实分数。若总时限到达，报告受阻，不把测试全绿当作真实摘要完成。
- 不引入依赖、不改变公开 API、`.env`、配置默认值、正常 Agent 循环或压缩失败策略。探针用合成历史一次性进入 run，因此真实请求验证的是压缩 middleware 与模型接线，不验证跨 run checkpoint 恢复。真实货币成本、不同任务分布的摘要质量和长会话稳定性仍待后续评测。

## 实际验证

- 红灯：新探针模块不存在时，定向 pytest 在收集阶段报 `ImportError`；摘要开始事件断言报缺失。补入最小实现后，定向 `.venv/bin/python -m pytest -q tests/test_compaction_quality_probe.py tests/test_context_compaction.py` 为 `11 passed in 0.63s`。在补充摘要耗时输出字段前，假模型接线测试因缺失 `summary_duration_ms` 失败；补上后转绿。
- 首轮全量 `.venv/bin/python -m pytest -q` 为 `451 passed in 15.05s`。补充摘要耗时输出字段后的最终全量为 `451 passed in 15.58s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。三个改动的 Python 文件分别为 254、246、118 行，均低于 500 行。
- 命令：`LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m scripts.probe_compaction_quality --config .env/models.json --total-timeout-seconds 120`。两次执行均退出 0，压缩组和未压缩组均返回可解析 JSON，四个固定事实均精确匹配。第一次压缩组摘要实际输入/输出 `2658/263` token、最终回答输入/输出 `1011/73`；总用时约 24.4 秒。对照组最终回答 `3345/152` token、约 10.2 秒。第二次压缩组摘要 `2658/312` token，摘要请求耗时约 19.3 秒，最终回答 `1036/103`，整组约 29.7 秒；对照组 `3345/96`，约 6.9 秒。两次摘要输出均未碰到 2048 上限。
- 这只说明 2048 上限在此固定短样本未形成瓶颈。压缩组单次总 token 消耗反而高于对照组；不能声称省钱或广泛事实保留。脚本只报告 token 用量，不计算货币费用。原长会话探针等待过长的根因仍未知，此固定样本没有复现；跨多轮收益、长工具输出、较复杂事实和并发仍需后续样本。探针没有改私有配置，不输出回答正文或密钥。
