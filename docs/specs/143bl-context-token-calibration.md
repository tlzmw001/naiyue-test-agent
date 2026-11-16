# Spec143BL：用实际模型用量校准上下文预算

日期：2026-11-16。状态：**S1核心与身份、S2运行与压缩接线已实现并通过本地验证；S3/S4尚未实施**。

采用“同次请求实际 input / 原始估算持续更新一个比例，整条压缩链与界面共用一个校准估值”的方案。保留 Python / LangChain create_agent、现有安全切点、摘要提交与单次 overflow 恢复。第一版不增加网络计数请求、不依赖某家服务端压缩、不新增共享校准数据库。

用户本轮确认简化为单一动态系数 r、单一校准估值 C。取消此前的近期最大倍率、双估值、可配置样本窗口和前缀锚点；固定 5% 余量仍不采用。保留原压缩阈值、输出额度与发送预算。用户已授权实施并完成S1/S2；后续界面和真实效果验证按S3/S4推进。

## 实施入口与规范归属

按 S1 → S2 → S3 → S4 顺序实施。每片注明生产接线进度；S1 的纯算法通过不能宣称压缩已采用校准，S2 的运行闭环也不能冒称界面和恢复验收完成。切片是审阅和验证单元，不要求分别发布。

| 分片 | 文档 | 核心产物 |
|---|---|---|
| S1 | [计量、身份与样本合同](143bl-01-calibration-core.md) | 模型身份、E/A 配对、单一 C 算法、最新样本读写与纯函数接口 |
| S2 | [运行与压缩闭环](143bl-02-runtime-compaction.md) | 正常请求采样、摘要独立计量、所有预算消费者、单次恢复与必要提交 |
| S3 | [投影、恢复与界面](143bl-03-projection-recovery.md) | v1/v2 读取、预览一致性、消息恢复、现有日志/事件和界面口径 |
| S4 | [验证与交付](143bl-04-verification.md) | TDD 与回归矩阵、标准构建/安装、有界真实效果验证及交接 |

总稿定义范围与全局合同；S1 是内部类型、样本 schema 和算法的唯一详细定义，S2 定义执行时点，S3 定义对外快照/展示，S4 定义验证门禁。实现中的局部文件拆分可以遵守 500 行规则，但不可改变上述合同；发现合同矛盾先同步 Spec，不由实现者自行择一。

目标是纠正已经观察到的系统性低估，让本地原始估算得到实际用量反馈。调用后 usage 不能预知首次请求、突然增加的大段内容或网关隐藏变换，因此本方案提供待实测验证的校准预测和明确失败边界，不承诺所有供应商零溢出。

## 1. 当前事实与证据

本轮分片核验基线：main，HEAD `60108431ceeeaf100df008da4765b879505fc1d7` 加已有工作区改动，[143BK 手动压缩修复](143bk-manual-compaction-after-model-failure.md)已在该提交中。初次调研基线是 `3f364393f76e4a89744ed065baf6d1134bd78ba3` 加当时工作区；本轮核对相关主链未因提交改变。实施时保留并行 MCP、学习与记忆方案改动。当前请求准备/恢复主链以 [Spec118](118-request-preparation-and-context-budget.md) 的最终实现为准。

正常链：`request_preparation.prepare_request` 固定模型、system、tools → `context_compaction._compaction_steps` 投影消息并检查预算 → 图提交必要更新 → `call_observation.awrap_model_call` 固定发送配置 → `context_recovery` → `ModelInputMiddleware` → `observe_model_request` → ChatModel。

失败链：明确输入 overflow → `context_recovery` 用原请求配置强制摘要 → 验证候选 → 只重试该模型请求一次 → 成功响应与压缩消息一并提交。第二次失败继续抛出，不重放已执行工具。

当前缺口：

- `agent_runtime/context_usage.py:89` 只用 `count_tokens_approximately` 估算；`finish_snapshot:133` 保存 usage，没有反馈预算。
- `context_compaction.py:49/213/271/300/303/321/370` 分别粗估保留后缀、触发、候选、摘要输入和最终输入。仅修改界面或触发位置不能解决全链路问题。
- `context_summary_io.arun_summary_steps` 直接调用摘要模型，绕过普通模型 middleware。
- `model_access.usage.normalize_model_usage` 接收框架已经归一化的输入用量；实际安装的 `langchain_anthropic` 已将原生 input、cache read、cache creation 求和。这里再次加缓存会重复计算。
- `ModelUsagePersistence` 是异步、可降级观察消费者，不可成为预算状态的必要来源。

本机依赖：LangChain 1.4.2、langchain-core 1.6.6、langchain-openai 1.6.6、langchain-anthropic 1.7.5、LangGraph 1.2.12、tiktoken 0.14.0。框架估算默认字符数 / 4、每消息额外 3、每图 85。已提供 `use_usage_metadata_scaling`，但本机实现将倍率限制在 1～1.25，使用 total_tokens 且只检查 provider 一致，不能覆盖本需求。

无网络探针结果：

| 构造材料 | 当前框架估算 | o200k_base 正文计数 | 正文计数 / 框架估算 |
|---|---:|---:|---:|
| 英文句子重复 300 次 | 4204 | 3001 | 0.714 |
| 中文句子重复 300 次 | 2254 | 6000 | 2.662 |
| 中英混合 JSON 重复 300 次 | 4129 | 6000 | 1.453 |

这些数值比较字符估算与一个确定 tokenizer 的正文计数，不包含完整供应商协议开销，不是 GLM 实测。复验命令：

```bash
.venv/bin/python -c 'import tiktoken; from langchain_core.messages import HumanMessage; from langchain_core.messages.utils import count_tokens_approximately; s="请检查接口测试失败的原因，保留请求编号、错误信息和用户约束。"*300; print(count_tokens_approximately([HumanMessage(content=s)]), len(tiktoken.get_encoding("o200k_base").encode(s)))'
```

另一个控制探针使用 window=2000、output=100、reserve=300，历史为 4000 个中文字、AI("ok") 和新 human("next")。有无上一响应 input=1800/output=100，当前估算均为 1021，压缩均返回 None；打开框架 scaling 也仅为 1277。它证明当前决策忽略历史 usage，不证明现场网关的具体误差。

初次调研时默认模型配置路径不存在，未取得用户现场 GLM 的真实限额。源码默认 window=128000、output=16384；reserve=min(16384, window//4)，keep=min(20000, window//4)，summary_output=min(2048, output, reserve*4//5)。输入校准不能自动纠正配置错误的窗口大小。

## 2. 官方和参考项目给出的依据

### 2.1 OpenAI 与 ChatGPT 的证据边界

没有找到足以证明 ChatGPT 网页内部估算或校准算法的公开资料。下列是 OpenAI API 的公开能力，不能替代 ChatGPT 产品内部实现的证据。

- Responses 提供 `/responses/input_tokens`，按请求的模型及输入结构计数，覆盖工具、图片、文件及格式开销；本地 tokenizer 不能覆盖全部这些内容。[官方计数文档](https://developers.openai.com/api/docs/guides/token-counting)
- Responses 支持按 rendered token count 触发服务端 compaction，也有独立 compact 端点；这是特定 API 的能力，不代表兼容网关都支持。[官方压缩文档](https://developers.openai.com/api/docs/guides/compaction)
- input_tokens 已包含缓存部分；缓存读取/写入是其中的分项。缓存改善计算成本，不会让对应上下文消失。[官方缓存文档](https://developers.openai.com/api/docs/guides/prompt-caching)

### 2.2 Anthropic 与 Claude Code

- Claude Code 的公开 statusline 用最近 API 响应的三项输入量计算占比；首次请求前、压缩后尚无新响应时 current_usage 为空。这证明其显示口径，不等于公开了全部压缩触发算法。[状态栏文档](https://code.claude.com/docs/en/statusline#context-window-fields)
- 原生 Anthropic 总输入需将 input_tokens、cache_creation_input_tokens、cache_read_input_tokens 相加；框架已归一化后不能再加一次。[缓存用量](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#tracking-cache-performance)
- `messages.count_tokens` 可用于请求前计数，但受请求类型和服务能力限制；流式 usage 具有累计字段，不能逐 delta 相加。[Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)、[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming#event-types)
- thinking 的回传规则与模型/模式相关；完整输出计费用量不等于下一轮回传文本的体积。[上下文窗口](https://platform.claude.com/docs/en/build-with-claude/context-windows)
- 启用服务端工具/阈值压缩后，一次 API 可能含多次采样，顶层 usage 可以是多次输入的和。只有明确归一化到本次对应输入后才能参与校准。[多次采样用量](https://platform.claude.com/docs/en/build-with-claude/compaction-threshold#understanding-usage)

### 2.3 Pi、AgentX 与 LangChain

Pi 本机 SHA `a69bef789bc95abf0acee16f7b4660b70b650bb9`：`packages/coding-agent/src/core/compaction/compaction.ts:202` 使用最近有效 usage 加后续消息估值；`agent-session.ts:3299` 在压缩后无新 usage 时将当前测量显示为空。它输出一个 context token 数，已核验主链没有“最新比例＋近期最大比例”的双系数。但其 input+output/total 不能直接当作下一次输入真值，其字符估算、图片估值与新用户输入检查时点也不能原样照搬。

AgentX 本机 SHA `3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f`：`AbstractMessageHandler.java:450` 将返回 input 减去历史 bodyTokenCount 后归给当前用户消息，滑窗再累计这些字段；摘要策略实际按消息条数触发。该差值混有 system/tools/协议开销，本项目不采用“把整请求差值伪装成某一条消息的真实 token”。

LangChain 的可选 usage scaling 使用最近有效 usage 推导一个比例；本机的 total_tokens 口径及 1～1.25 限制不能直接照搬。现成 SummarizationMiddleware 支持自定义 token_counter，默认仍是字符估算；本项目已有安全切点、必要提交、手动压缩和 overflow 恢复合同。保留这些实现，只统一其计量函数。[官方 middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/built-in)、[估算函数文档](https://reference.langchain.com/python/langchain-core/messages/utils/count_tokens_approximately)

### 2.4 单系数参考与证据边界

LibreChat-AI/agents 核验 SHA `f685c3dc588672c3a4bd614ce007d244d3452192`：响应 usage 进入 Graph；pruner 以累计 provider 消息输入量 / 累计 raw 发送量更新一个 calibrationRatio；主要预算、摘要触发和只读 contextUsage 复用该比例。[样本更新源码](https://github.com/LibreChat-AI/agents/blob/f685c3dc588672c3a4bd614ce007d244d3452192/src/messages/prune.ts#L2884-L2928)、[预算与摘要调用链](https://github.com/LibreChat-AI/agents/blob/f685c3dc588672c3a4bd614ce007d244d3452192/src/graphs/Graph.ts#L3467-L3570)、[只读预览](https://github.com/LibreChat-AI/agents/blob/f685c3dc588672c3a4bd614ce007d244d3452192/src/agents/AgentContext.ts#L2141-L2188)。

它另有 instruction overhead、固定预留、0.5～5 倍率限制和特殊重计数保护，不能概括为整个系统只有一次乘法；其累计比例也不同于本片的最新有效比例。本片只借鉴“一个动态比例被各预算消费者复用”，不照搬累计账本、倍率限制或其他机制。

这些源码证明单系数路径实际存在，不证明它在本项目优于双系数。此前的近期最大倍率是自行提出、未经同批数据验证的保守策略；已核验的 Pi、AgentX、LangChain 及官方说明没有为该策略提供效果证据。第一版不把它设为必选合同，真实误差、overflow 和摘要成本按 S4 单独验证。

## 3. 计量合同

只区分三个数，所有预算消费者使用同一个 C：

| 数值 | 含义 | 使用者 |
|---|---|---|
| E | 本次固定请求未经校准的本地估算 | 原始分组、生成样本、误差分析 |
| A | 该请求完整返回的 normalized input_tokens | 已测输入事实、更新比例 |
| C | 用最近兼容样本比例计算的当前校准估值 | 界面、主动压缩、摘要、发送与恢复检查 |

只用 input_tokens 校准输入。output_tokens、total_tokens、累计会话用量不进入 A；缓存不减去，也不重复加；reasoning 不重复加入输入。上一回复只有实际被当前请求回传的内容才进入当前 E，不再另加上一轮 output 用量。

有效样本要求同一次固定请求、完整有效响应、A/E 均为正整数、用量语义是一次输入、适配身份明确。缺失、负数、bool、错误类型、零占位、取消、部分流、语义未知的聚合用量均不更新样本，不把旧比例清零。外层响应门禁拒绝的响应不额外强行持久化。

继续以 normalize_model_usage 为输入归一化入口。兼容网关如果错误上报 usage，校准也会受影响；真实验收必须确认该假设，不能以本地算法测试代替。

## 4. 单系数算法与本轮修正

### 4.1 唯一计算规则

在同模型/路由/适配身份、同用途 purpose、同媒体类别下，取最新一份有效样本：

```text
r = A_sample / E_sample
C = ceil(r × E_current)
```

没有兼容样本时 C=E，method=uncalibrated；原始估算失败时 E/C=null，method=unavailable。每次完整有效响应立即替换对应类型的旧样本，升高和降低采用相同规则。r 可以低于1，不做近期最大值、平滑、额外倍率或人为上下限；使用精确分数与整数向上取整，避免浮点误差改变阈值等号。

界面和预算都消费同一 C，不再保留 predicted/decision 两套数值或隐藏的额外放大。原始 E 与返回后的 A 保留各自含义。

### 4.2 删除旧锚点公式的原因

已撤回的方案使用最新样本作为 anchor，且 r=A_anchor/E_anchor，因此：

```text
A_anchor + r × (E_current − E_anchor) = r × E_current
```

两式在原条件下完全等价；此前“锚点＋增量让这一估值更准确”的理由不成立。现合同删除仅服务该分支的 anchor、消息数量/前缀指纹、system/tools 内容指纹和增量匹配，不保留等价算法的两条运行分支。这里的删除不影响模型路由/适配身份指纹，也不删除现有产品的工具配对、输入投影或权限校验。

旧的第二系数只是让数值更保守，尚无数据证明减少 overflow 的收益足以抵消过早摘要/拒绝的成本。本轮单系数并非已经实证更准，而是先完成可验证的最小反馈闭环。

### 4.3 请求形态与适用边界

请求先经过当前 ModelInput 投影，E 包含 system、完整投影消息和 tools。测量仍早于 SDK 最终序列化，不调用 SDK 私有 payload 构造器，不启用框架内置 usage scaling 再乘一次。

模型身份包括配置 model_id、已解析请求 model、有效路由、API、thinking、输入适配和计量版本。本机取 SDK 构造后的有效 endpoint，包含环境解析结果；云端用稳定 bootstrap.models.profile_id 替代临时任务代理路由。只保存指纹，密钥轮换和 bootstrap token 续期不改变身份，不记录 URL/凭证原文。

普通请求和摘要分 purpose；text/image 分媒体。每一类只保存最近一份有效样本，最多四份，当前请求只使用其中一份。它们用于隔离不同输入分布，不是同一请求并行使用多个系数。summary 没有自身 text 样本时，可沿用同身份 agent/text 的比例作低可信先验，method=inherited_agent_ratio；含图样本不借给纯文本摘要。

system、tools 或历史改变时重新计算整请求 E；兼容比例仍只是一种启发，不承诺新内容分布与旧样本一致。历史编辑/重生成只从当前可信消息链读取记录；如果可用记录已被移除就冷启动。压缩后也直接计算新请求 E，不保留旧输入量作加法基准。

当前 Web、Runner、子 Agent 主链为 response_format=None。非空结构化输出、供应商内置 server tools、多次采样/隐藏上下文或无法确认表示的内容，标 unsupported_request_shape，使用原始 E，禁用旧比例和新采样。必要输入转换/配置错误仍按原合同传播，不能吞成估算失败。

返回的明确 model 名称只作为该 purpose 的最后已知证据。同 purpose 后续报告变化时清该用途的 text/image 样本，再存本次新样本；缺失名称不伪造变化。不会跨 agent/summary 推断整个后端变化；真实请求身份改变仍冷启动。不可见的 alias 后端切换无法提前识别。

### 4.4 连续三轮例子

以下为讲解数据，不是真实模型效果：

| 请求 | 当前 E | 发送前使用的 r | 发送前 C | 返回 A | 响应后的新 r |
|---|---:|---:|---:|---:|---:|
| 第1轮 | 1000 | 无样本，按未校准处理 | 1000 | 4000 | 4 |
| 第2轮 | 1500 | 4 | 6000 | 5400 | 3.6 |
| 第3轮 | 2000 | 3.6 | 7200 | 尚未返回 | 尚不更新 |

第2轮的分母必须是1500，不能使用已校准的6000。第3轮显示和预算判断都使用7200，没有7400的第二决策值。若之后压缩后的 E=800，且最新兼容样本已携带到摘要中，则 C=ceil(3.6×800)=2880。

另一个阈值例子：W=128000、O=R=16384，则 T=B=111616。已有 r=4 时，E=25000 得 C=100000；E=29000 得 C=116000，触发压缩。压缩后 E=10000 得 C=40000，通过发送预算后才提交。

## 5. 状态归属与接线

正常处理顺序：

1. 固定模型/system/tools并投影消息，调用 measure_request，冻结本次 E/C 及采样前态。
2. 原压缩/发送入口使用 C；实际调用返回后，把该请求 E 与其 normalized A 交 complete_sample。
3. 校准记录附到本次 AI 的独立 metadata，随原 checkpoint 必要提交保存。
4. 下一请求从当前消息链读取最新有效状态；摘要候选通过时携带最新样本状态。

新增 `agent_runtime/context_calibration.py` 提供纯函数，复用 ModelInputMiddleware。测量归一次 wrapper 的局部变量，历史样本归消息；不新增共享 middleware 可变状态、事件订阅、ContextVar、数据库表、锁或服务。

已有 ModelCallState 只传递不可变 measurement 给观察层。观察进入时冻结 E/C，返回后只补 A；不得事后用 A 重算已发送请求的 C。overflow 每次尝试拥有自己的 sample_id 与局部 measurement，不能把首次 E 配给 retry A。

当前正式图在 async-only 的模型准备节点已不支持同步 invoke，且未发现独立同步 ModelInput 消费者；S2 删除该不可达同步 override，回归实际 async 主链，不建立同步校准算法。

持久键仍为 `response_metadata['naiyue_token_calibration']`，内部 version=1，最多16KiB。Envelope含identity、revision、source及agent/summary两类状态；每类仅保存text/image最新样本及最后已知报告模型hash。完整字段/首因以S1为唯一来源。不存请求正文副本；metadata不得进入供应商输入。

revision 只解决最新派生状态选择，与多样本窗口无关。读取当前 identity 中最高可识别 revision 的完整记录；相同副本去重，不合并历史记录。最高版本损坏时本次冷启动，不回退旧比例；下个有效响应以 high-water+1 重建，避免被旧坏记录永久卡住。

压缩 carry 优先以有效 summary_envelope 为唯一基底，否则复制原 agent measurement 的状态，再产生更高 revision。摘要无 usage 时仍可携带已有样本；无法建立身份时返回 None，不伪造记录、不改变原压缩成功语义。候选失败/取消不修改旧消息；成功后按原必要提交生效。

根/子 Agent、不同会话只读自己的消息；重生成只读源 checkpoint 保留记录。旧数据无校准键就冷启动，不从累计账单或旧观察 snapshot 猜造样本。采样/普通观察失败应诊断并降级；必要输入、配置及提交错误继续传播。

## 6. 所有预算检查共用 C

保持现有 W（窗口）、O（输出上限）、R（压缩预留）：

- 软触发 T=W−max(O,R)，C>T 时考虑主动压缩；C=T不触发。
- 发送预算 B=W−O；压缩后 C≤B即可，不要求再次降至T以下。
- R/O不相加；输出额度只预留一次，thinking如已计入输出cap不重复扣除。
- R>O时阈值与发送预算之间已有R−O空间；R≤O时二者相等。阈值与比例都不是误差保证。

统一迁移：首次/工具后/补充消息的输入检查；保留后缀与安全切点；完整候选；实际摘要输入；实际摘要后的候选；摘要失败时原输入fallback；overflow强制减少及单次retry；预览和诊断。

后缀只用于规划：从本次冻结的 agent 样本状态选择与已投影后缀媒体兼容的比例，对后缀原始估算计量；缺少该媒体样本时按未校准处理。不给单条消息分摊实际A；工具配对和完整轮次规则不变，最终必须复核完整候选。

候选规划为 `C_placeholder + S`，S是已有summary_output；S不再乘校准倍率。实际摘要请求单独计量，`C_summary+S≤W`才发送。摘要到手后重新估完整主请求，`C_post≤B`才提交；force还要求`C_post<C_original`。

原输入和post使用同一份冻结agent样本状态；摘要自身的新usage只更新summary用途，不通过改变agent比例制造“减少”。保留后缀导致媒体类别改变时，仍从冻结状态选择与候选媒体兼容的单一比例，不能把image比例强加给text。数值减少是策略估值变化，不是实测压缩量。

## 7. 失败与降级

| 情况 | 规定行为 |
|---|---|
| 本次usage无效/缺失但有兼容样本 | 不更新、不清零；下一请求继续用已有比例 |
| 无兼容样本 | C=E，标未校准；不填0 |
| 模型/API/路由/适配身份变化 | 旧样本不参与；单纯凭证续期不变身份 |
| system/tools/历史变化 | 重算完整E；当前链仍有兼容样本时只沿用其比例 |
| 观察投递/用量落盘失败 | 直接采样继续；普通观察不改变主结果 |
| 校准记录损坏 | 固定reason、当前冷启动；后续有效响应可恢复 |
| 原始总量估算失败 | 沿Spec118可观察降级；E/C为空，不声称已验证预算 |
| 已知超预算后候选无法计量 | 阻断，不借估算失败放行原超预算输入 |
| 摘要输入C+输出额度>W | 发送前明确summary_input_exceeds_budget，原上下文不动 |
| 无安全切点/候选仍超预算/强制压缩无进展 | 按原合同失败，不循环摘要、不截断用户输入或工具结果 |
| 摘要临时失败 | 仅当原C≤B且满足既有fallback条件才发原输入；force不可回退 |
| provider明确overflow | 沿既有一次强制恢复，不原样重发，不从错误猜新比例 |
| retry再失败/取消/必要保存失败 | 原异常与提交合同保持，不重放工具 |
| 结构化输出/聚合或隐藏上下文语义不受支持 | 保留原执行行为，禁用比例和采样，不伪造准确性 |

**本片仍不解决任意超大历史如何摘要。** 校准可能发现原请求与摘要都被预算策略判超限，此时失败并保留原上下文。单系数仍可能高估或低估；分块摘要、超大单条处理是独立切片，不可写成此次已交付能力。

## 8. 配置、接口与展示

不新增用户配置项。此前草案的 context_estimation、calibration_sample_limit、safety_margin_ratio、margin_tokens 均不属于实施合同；它们尚未发布，无兼容/迁移或保留空对象的需要。模型身份沿现有配置解析与模型metadata装配，public_configuration和云端profile输入不因派生元数据改变。真实models.json、.env和部署配置不修改。

四个内部入口继续为 measure_request、complete_sample、carry_compaction_state、read_calibration；类型以S1为准。measurement只保留单一calibration_ratio，预算只有raw_input_tokens(E)、calibrated_input_tokens(C)及来源/状态；不保留双值别名。

ContextSnapshot新写version=2，保留version=1读取。ContextEstimate原estimated_input_tokens和groups仍表示E；estimate.calibration精确增加method、calibrated_input_tokens、invalidation_reason三个字段。不存在恒为0/1但没有展示需求的sample_count。详细schema见S3。

当前占比、压缩提示和剩余预算统一用C，剩余为max(0,W−O−C)；原始分类仍以E为分母，注明本地估算。最近请求的实际A与发送前C分别展示，不把事后真值回填成事前预测。旧v1按旧原始口径展示，不伪造校准。

复用现有观察事件。安全日志只包含逻辑模型身份、调用关联、E/C/A、来源、固定reason和预算结果；不记录正文、tool args、图片、真实URL、凭证或完整provider错误体。摘要started/finished复用同次sample_id配对，不增加观察hook。

## 9. 实施范围与分片

| 切片 | 主要落点 | 完成标准 |
|---|---|---|
| S1核心 | context_calibration.py；model_access内部metadata；cloud_runner/models.py；核心/身份测试 | 最新样本r与C、严格记录、模型身份及凭证续期先红后绿；无新用户配置 |
| S2运行 | model_input、run_context、request_preparation、context_compaction、context_recovery与原装配 | 全部预算统一C，摘要独立采样，response_format贯通，必要提交不变 |
| S3投影 | context_usage/observation、session_context/agent_session、前端types/events/ContextPanel | v1/v2、预览、重开/重生成/Runner和统一展示 |
| S4验收 | 原测试扩展、新有界probe、标准构建/临时安装、相关指引与handoff | 实现证据与真实误差证据分离，报告实际结果与限制 |

摘要仍用context_summary_io的原generator `(bound_model,messages)` IO协议；计量/采样留在原generator。手动压缩保持两步提交，overflow候选只在retry成功且门禁通过后提交。

收尾搜索生产、脚本、测试和当前文档的旧预算调用，普通Agent/compaction不保留并行算法。原始组成统计、明确标为原始估算的探针可保留；长期记忆和自动权限审查等独立用途不扩入本片。不为未发布草案建立兼容层。

实现文件≤500行；条件拆分按S1/S2固定边界执行。不新增共享缓存或锁；若出现真实新需求，先更新Spec与锁表，不能当临时捷径。

## 10. 验证与验收

先用失败回归证明现有usage不影响下一次预算，再按分片实现。必测：

1. 4倍误差可纠正；最新有效比例上调/下调立即生效，低于1也适用；分母永远是原E。
2. 缺失/零/负数/bool/部分usage不更新；input/cache/output/reasoning归一化不重复计数。
3. 当前完整请求含system/tools及实际回传内容，采样不会把retry A配给首次E。
4. 模型/路由/API/thinking/适配身份隔离；凭证续期不误失效；purpose/media最新槽互不污染。
5. system/tools/历史改变仍重算E；编辑/重生成只使用当前可信消息链，压缩carry不重复或丢失有效样本。
6. 摘要超预算、真实摘要后候选超预算、无安全切点、无进展、fallback/force边界。
7. 原输入、摘要、retry各自冻结E/C/A，失败/取消/必要保存错误不提前修改状态、不重放工具。
8. 最高revision选择、合法副本、冲突/坏记录与后续恢复；summary在头部仍胜过后缀旧AI。
9. 根/子隔离、SQLite重开、Runner必要历史、旧v1与新v2、空值和只读预览。
10. 界面和所有预算消费者使用同一个C，groups仍为E，最近A单列；没有旧双值、N配置或锚点分支残留。

各片给定向命令，S4给完整后端/前端、原生工具资产、浏览器、标准构建和临时wheel安装命令。本次只做文档与公式校验，不把这些未来命令写成已经通过。

真实probe按S4六个串行场景，通常7次、最多12次物理模型HTTP；不改真实预算。记录同请求E/C/A，在相同有效校准集合比较误差及C低估率，并记录overflow、压缩触发、拒绝和耗时。未发送输入无provider真值，不计算伪精确“无必要压缩率”。

模型效果门禁、分母和证据不足退出码以S4为准。单系数只是本轮最小方案，不能预先声称优于双系数或消除overflow；固定构造测试全绿也不证明现场GLM用量语义或摘要保真。

## 11. 后续边界与回退

只有实际数据证明最新比例波动造成持续问题时，才评估单系数平滑、其他估计方式或额外保护；不预建第二系数与配置平台。对照须使用相同请求与实际A，分别报告误差/低估、真实overflow和摘要成本，不能仅凭数值变大就声称改善。

官方请求前计数可作为冷启动、多模态、突增输入或临界预算的后续补充。第一版不接入，因为目标兼容网关支持、payload一致性、延迟与请求限制未确认。新增时仍需保留已知超预算不得因计数失败放行的合同。

不迁移供应商服务端compaction，不引入分块摘要，不从累计账单推断窗口，不训练跨会话比例。派生校准metadata可被旧代码忽略；旧快照v1仍可读，旧代码可能不显示新快照，但不得破坏消息历史。已提交摘要不因代码回退自动还原，不能将回退描述为无损恢复。

当前已按用户授权实施S1核心与模型身份，真实配置/依赖/数据库不变，未调用真实模型或提交发布。此前离线探针是问题证据，S1测试也不代表真实模型效果。S1本地实现记录见其第10节；S2已接实际请求采样、压缩全部预算和preflight，验证见S2第12节；下一实施片为S3。
