# Spec143BL-04：上下文校准的验证与交付

日期：2026-11-16。状态：**S4已实现；2026-11-16继续验收已修复存储分层和Responses历史压缩问题，修复后六个现场场景通过，工程终局见10.1**。

前置：[总方案](143bl-context-token-calibration.md)、[S1](143bl-01-calibration-core.md)、[S2](143bl-02-runtime-compaction.md)、[S3](143bl-03-projection-recovery.md)。本片不新增产品能力，完成确定性回归、标准产物检查和有界真实计量验证，并给出证据边界。

已确认使用单一动态系数：每个 purpose/media 只保留最近有效样本，r=A/E，C=ceil(r×E_new)；没有兼容样本时 C=E。显示和预算共用 C，自动触发 C>T，发送检查 C≤B。没有历史最大值、平滑或固定余量；窗口、输出额度、压缩预留和保留策略继续来自实际配置。

## 1. 三类结果分别验收

| 结果 | 必须有的证据 | 不能替代什么 |
|---|---|---|
| 机制正确 | 确定性请求/响应 fixture、状态与异常断言、完整测试终局 | 不能证明某网关真实 usage 正确 |
| 供应商接线和误差改善 | 同一请求 E/C/A 配对、实际模型/API、完整返回用量、物理请求账本 | 不能证明所有模型、语言、图片或窗口边界 |
| 摘要质量 | 固定事实样本的摘要保留和后续答案检查 | token 变小或请求成功不等于事实保真 |

本轮文档只规定后续执行，不把命令列表、已有历史通过结果或启动中的进程写成新实现已通过。

## 2. 分片实施顺序与红灯

| 切片 | 首批失败测试的行为 | 结束时必须已接入 |
|---|---|---|
| S1 | 4 倍输入偏差可建样本并影响下一测量；新有效比例升降立即生效；错误身份不可复用 | 纯函数、严格 metadata、现有模型身份接线、本机和 Runner 稳定身份 |
| S2 | 当前代码忽略 usage 导致本应压缩却不触发；摘要与 post guard 同样按旧 E 放行 | 正常/手动/overflow、采样、候选、摘要、fallback 全链统一 C |
| S3 | v2 当前被拒绝；当前 UI 使用 E；新校准记录的重开与重生成边界尚无覆盖 | v1/v2 读写、预览、SSE、UI 和必要持久路径 |
| S4 | 探针把不完整/错误配对数据算成功、越过物理请求上限、输出敏感字段 | 有界测量探针与实际报告；标准构建/安装证据 |

每片先确认红灯失败原因是目标行为，而不是导入错误、缺依赖或夹具自身错误；再做最小实现。测试基础设施如确需调整，必须说明现有 helper 无法表达什么边界，不能放宽断言或吞掉检查异常。

任何源码修改后，先完成本片定向与相关回归，再进入下一片。只有新修改或新的未解决问题才重复已经完成的测试。构建失败先修构建。连续三次同方向验证失败按 AGENTS 停止，报告尝试和当前假设。

## 3. 确定性矩阵

### 3.1 计量与身份

- 正常 1 倍、4 倍、低于 1、非法零/负数/bool/字符串、异常大合法倍率；分母始终是原始 E。
- 统一公式 C=ceil(rE_new)；冷启动 C=E。无任何隐含 1.05、clamp 到 1 的下限、历史最大值或样本窗口配置。
- r 随相同用途/媒体的完整有效响应立即更新；比例 4→3.6 后下一请求直接使用 3.6，比例随后升到 5 也立即生效。旧样本不以近期最大值继续参与计算；summary 和保留 AI 的记录副本不重复采样。
- 同一身份与兼容请求形态下，编辑内容、system/tools 变化由新的整请求 E 反映，使用最新兼容比例作预测；不需要内容前缀指纹或旧实际输入加减。thinking、模型/API/路由等身份变化按 S1 失效，不能混用其他模型样本。
- agent/summary 与 text/image 各保留最新样本槽，总数最多 4；无有效 usage 不覆盖。最高 revision 损坏时明确冷启动，新有效响应以更高 revision 恢复；不能合并历史副本复活旧样本。
- 同模型 alias 的响应 model 变化处理、非空 response_format 禁用校准、未知块、不支持的 aggregate usage 不作为新样本。
- 云端稳定 profile 跟随真实策略，不跟随任务代理 URL、bootstrap token 续期变化；不含凭证/真实路由原文。

### 3.2 运行与持久化

- 首次模型、工具后、选入补充消息后的每次请求都检查同一 C。
- 缺失本次 usage 时仍能使用兼容历史；部分流/取消不新增样本；输出和 reasoning 不错误累加到输入。
- Chat Completions、Responses、Anthropic、Ollama 的真实 SDK 构造加 MockTransport/受控 server fixture，验证归一化字段与流合并；不用网络实测制造故障。
- 摘要输入超限、规划候选可放但真实摘要后超限、无安全切点、工具配对不完整、摘要异常/取消、必要保存失败。
- 主动压缩暂时失败仅在原 C≤B 时可按现有条件回退；已知超限和强制恢复不可降级放行。
- 原请求 overflow→summary→retry：每次 E/A 配对正确，原配置固定，retry 只一次，工具副作用次数不增加。
- 压缩保留旧 AI usage、删光旧 AI、手动两步提交、主异常身份、异常观察器与必要保存故障分别覆盖。
- SQLite 关闭重开、regenerate 的真实源 checkpoint、根/子独立消息、Runner 重开与必要 history message 分片。

### 3.3 可见数据

- 当前预估与预算均使用 C，最近 A 和原始 E/groups 保持各自语义；不存在把 A 回填后重算历史 C 的“自我验证”。
- v1 只显示旧原始估算；v2 严格校验；unknown/null 不变成零。
- C=T 不触发；C=T+1 触发；post C=B 可发送，C=B+1 拒绝。R≤O 与 R>O 两种配置都验证。
- 原始 groups 相加仍为 E；C 不分摊成供应商分类用量。r<1 时合法的 C<E 仍用于显示和预算，不偷偷恢复成原始 E。
- busy/审批/过时 preview 保持原语义；preview 选择有效 thinking，切换后重新验证。
- 统计投递超限省略时保留事件序号与主体响应；校准状态不依赖该观察事件。

## 4. 测试命令与资产

以下新增文件名是本组 Spec 的明确实施目标，执行时应已经由相应切片创建：

- S1：tests/test_context_calibration.py、tests/test_context_calibration_identity.py。
- S2：tests/test_context_calibration_runtime.py、tests/test_context_calibration_compaction.py。
- S3：tests/test_context_calibration_projection.py、tests/test_context_calibration_recovery.py。
- S4：tests/test_context_calibration_probe.py。

各片按自己的定向命令执行。整体重点回归：

```bash
.venv/bin/python -m pytest -q tests/test_context_calibration.py tests/test_context_calibration_identity.py tests/test_context_calibration_runtime.py tests/test_context_calibration_compaction.py tests/test_context_calibration_projection.py tests/test_context_calibration_recovery.py tests/test_context_calibration_probe.py tests/test_context_compaction.py tests/test_context_budget_degradation.py tests/test_context_overflow_recovery.py tests/test_manual_compaction.py tests/test_context_inspector.py tests/test_async_context_chain.py tests/test_context_operation_boundaries.py tests/test_last_regeneration.py tests/cloud/test_model_configuration.py tests/cloud/test_runner_kernel.py tests/cloud/test_runner_history_archive.py
```

全量测试前按现有 `scripts.native_assets` 准备 rg/fd。下列命令用于本机 macOS arm64；其他机器采用 release/native-tools.json 中的实际 target，不能拿 Mac 结果冒充 Windows/Linux 原机结果。首次使用一个不存在的输出目录；已存在则选新编号，不删除/覆盖旧资产。

```bash
.venv/bin/python -m scripts.native_assets --target macos-arm64 --output build/spec143bl/test-assets
PATH="$PWD/build/spec143bl/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short --junitxml=build/spec143bl/backend.xml
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent scripts
.venv/bin/python -m pip check
git diff --check
```

并行后端可采用已有 `scripts.ci_shards` 的两个完整分片，但必须收齐两份终局且覆盖同一源码；不能把一个分片成功说成全量成功。依赖缺失先按当前项目安装规则解决，不改依赖版本、不 skip 失败项。

## 5. 浏览器与标准产物

在现有 frontend/e2e 夹具上新增 `context-calibration.spec.ts`，复用独立服务与 API fixture，提供 v1、已校准 C≠E、未校准、不可用、压缩后无新主响应五类输入；已校准类分别覆盖 C>E 和 C<E。不是伪造生产数据，页面截图应标为测试夹具验收。

```bash
READING_PROD=1 npm --prefix frontend run test:browser -- context-calibration.spec.ts
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec143bl/wheels
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec143bl/wheels --output build/spec143bl/install-macos-arm64.json
```

浏览器检查使用现有 desktop 1440×900、mobile 375×812 项目，人工检查两种截图的数字、标签、分类、阈值提示及无横向溢出；记录 console/pageerror。端口和服务启动沿现有 Playwright 配置，不修改环境配置去迁就测试。

`build_release` 使用现有 allowlist staging、标准前端构建及 native assets；不使用替代打包方式。`verify_wheel_install` 会在临时 venv、临时 home/workspace/SQLite、源码外 cwd 运行受控模型 smoke，验证 wheel hash、包内工具和实际安装位置，不替换用户环境；安装依赖可能联网，但不会调用真实模型。无需 `--from-pypi`，本片没有发布。

补充产物检查：构建包中确实包含新计量模块，冻结源文件与打包源码一致；源码路径计量回归不能代替安装态接线证据。如果现有 release smoke 不经过两轮有效 usage，扩展既有 `scripts/release_smoke.py` 调用的最小 fixture，让安装态至少验证一次“首轮保存样本→第二轮使用样本”，不引入 pytest 到安装环境，也不复制一套产品实现。

## 6. 新增窄范围真实计量探针

### 6.1 为什么不直接执行旧探针

- `scripts/probe_context_compaction.py` 是未接产品的旧 LangChain SummarizationMiddleware 探索，不能作为当前产品校准验收。
- `probe_compaction_quality.py` 主要比较摘要质量，并在内存改写预算；不能据此证明用户实际配置下的阈值行为。
- `run_provider_acceptance.py` 现有 CLI 只允许 Responses，默认计划最多 60 个请求，包含无关工具/权限/子 Agent 场景；本片不扩大执行它。
- `provider_acceptance_cases.CaseEnvironment.open_app` 会改变输出额度，不直接复用该装配方法。

因此新增 `scripts/probe_context_calibration.py`，只做本片计量验证。复用 `provider_acceptance_budget.RequestBudget/guard_catalog` 的物理请求限额和现有 `component_fixtures.open_component_application` 装配；创建自己最小的临时应用入口，不另写 Agent 循环。

### 6.2 CLI 与执行边界

该 CLI 是实施后新增，不是当前已存在命令：

| 参数 | 合同 |
|---|---|
| --config | 必填，既有模型配置路径，只读，不复制含凭证配置 |
| --model-id | 必填，必须在配置中存在；不自动切换其他模型 |
| --report | 必填，新文件独占创建；已有报告拒绝覆盖 |
| --live | 显式允许真实请求；缺少时只输出零请求预检并退出 |
| --request-limit | 默认 12，整数 1～12；主请求/摘要/原恢复链每次 HTTP 发送均计入 |
| --total-timeout-seconds | 默认 600，正有限数；整个单模型计划的总时限 |

脚本只创建临时新 workspace、会话和 SQLite，所有工具只允许读取该 workspace 中脚本生成的固定材料。只启用 read 工具；没有 shell/write、外部文件或现有会话操作。

保留目标模型的 window/output/reserve/keep/summary_output、thinking、API 和模型名称，不能为了让测试触发压缩而改预算。只在探针拥有的 SDK 客户端禁用内部重试，记录 sdk_retries=0；正常产品的 overflow 一次恢复仍可执行，所有实际发送计数共用一个上限。

当前 guard_catalog 的适配器类型包含 ChatOpenAI/ChatAnthropic/ChatOllama，绑定 owned SDK HTTP client；S4 必须通过 CC、Responses、Anthropic、Ollama 四种 MockTransport/受控本地协议 fixture 验证上限确实在发送前生效，不能仅凭 isinstance 判断声称全部覆盖。无法确认拥有客户端或发送路径的适配器，预检 blocked，零真实请求。

没有 --live、配置不存在、模型不存在、无法解析实际限额、unsupported request shape 或 guard 不适配时，退出码 2，报告 blocked 与固定 reason。不得回退到默认模型或扩大请求预算。

示例执行方式，两个变量由操作者指定既有路径和模型 ID，不需要把凭证发送给助手：

```bash
.venv/bin/python -m scripts.probe_context_calibration --config "$NYT_CALIBRATION_CONFIG" --model-id "$NYT_CALIBRATION_MODEL" --report build/spec143bl/calibration-preflight.json
.venv/bin/python -m scripts.probe_context_calibration --config "$NYT_CALIBRATION_CONFIG" --model-id "$NYT_CALIBRATION_MODEL" --report build/spec143bl/calibration-live.json --live --request-limit 12 --total-timeout-seconds 600
```

本次写 Spec 不执行 live；后续实现后的真实验收，应在目标模型与此有界范围明确后执行。

### 6.3 固定串行场景

每个场景只执行一次，不为改善指标重复生成。纯文本使用固定合成文本和 JSON，无真实项目数据。材料内容及生成次数写进探针源码，报告只保存 fixture 名称/版本、字符数与 SHA256。

| 顺序 | 操作与固定输入 | 通常模型请求数 | 验证重点 |
|---|---|---:|---|
| C1 冷启动 | 300 次重复“请检查接口测试失败的原因，保留请求编号、错误信息和用户约束。”；要求只回 ACK | 1 | 首次 C=E；首次 A 建立样本 |
| C2 续聊 | i=0…149，逐行 `{"case_id":i,"status":"待核验 pending","expected_http":200}`；要求只回 ACK | 1 | 整请求 E 包含实际回传内容，使用首轮比例；返回后更新该槽最新样本 |
| C3 工具变化/结果 | 通过 `sessions.select_tools` 从空集启用 read；要求一次读取 calibration.txt，内容为 i=0…199 的 `record=i; result=待检查 pending; owner=fixture`，随后只回 ACK | 2 | tools/system 与工具结果变化进入新 E，预算仍用同一 C；最多一次指定 read |
| C4 手动压缩 | 调用现有 `app.host.runs.compact` | 1 | 原预算不变；检查摘要自身预算、applied/skipped/failed 终局；summary 样本与 agent 分开，提交时携带最新记录 |
| C5 压缩后主请求 | 要求只回 ACK，不再读文件 | 1 | 压缩后新 E 使用保留的 agent 比例；主响应实际 A 更新该槽，summary 比例不污染它 |
| C6 关闭重开续聊 | 关闭临时应用，重开同一临时数据库、restore 同会话，再要求 ACK | 1 | metadata 恢复，各用途/媒体最新样本保留，无额外回放旧请求 |

通常 7 次模型请求；允许原产品流程产生额外主动摘要/一次 overflow 恢复，但总物理 HTTP 发送不得超过 12。探针自己不新增重试、模型切换或自动修复。

如果原配置导致 C4 无法安全压缩或摘要自身超预算，按产品实际结果记录 compaction_not_covered/failed 并停止后续依赖场景，保留先前计量；不能修改 keep/window 或伪造 applied。窗口过小导致 C1 被拒绝，同样记录，不临时缩小材料重试。图片、服务端多次采样、未知 usage、网络中断等异常边界只在确定性测试覆盖，不用现场请求试探。

### 6.4 停止条件与报告

任何 provider/network 失败、请求上限、总时限、工具偏离/重复执行、输入或状态保存失败立即停止当前计划，不循环。usage 缺失不制造失败请求重试：记录未能校准的原因，可按剩余计划收集，但不能判定测量效果通过。

报告采用固定安全字段：spec/fixture 版本、代码 SHA、模型逻辑 ID、API、配置限额数值及来源、开始/结束时间、每个场景状态、每次实际调用的本地关联 ID/purpose/E/C/A、是否有样本/所用方法、物理 HTTP 次数、耗时、固定 reason。不保存正文/回答、tool args、图片、配置副本、URL、headers 或原始 provider error。

调用结束记录是唯一追加一次的测量来源。agent 读取 S3 同次请求冻结的 snapshot estimate 和最终 usage；summary 复用现有 context_summary_model_started/finished 日志，按 S2 在调用开始生成的同一 sample_id 配对 started 的 E/C 与 finished 的 A。探针只接收数值白名单，不将整条日志原样写报告。不新增产品观察 hook 或另一套计量。

不能把重试的 A 配给首次 C，不从累计用量差值猜输入，也不在返回后重算 summary 的 C；没有完整配对就记 incomplete。不同 purpose 分别统计；只有 agent 完整有效返回且 A>0 的行进入误差指标，summary 行仅用于摘要计量证据。

退出码：0=所有机制场景完成且计量指标可计算；1=执行失败/机制检查失败；2=预检阻断；3=流程有未覆盖场景或 usage 不足。即使退出 0，模型效果仍由下述指标和局限单独判定，不能将 0 等同生产效果通过。

## 7. 真实效果指标与分母

同批有效 agent 行为集合 V；其中 method=ratio_estimate 的行构成已校准集合 V_cal，冷启动行不混入“校准改善”均值。C 在本稿始终表示单次校准估值，不用它命名样本集合。缺 usage、A=0、partial、失败、summary、重发重复记录逐项报告数量，不静默排除。

每条有效行计算：原始绝对相对误差 |E−A|/A；校准误差 |C−A|/A；原始低估指示 E<A 与校准低估指示 C<A。

| 指标 | 分母与报告方式 |
|---|---|
| 用量可用率 | 有完整有效 A 的 agent 返回数 / 全部完整 agent 返回数 |
| 校准覆盖率 | V_cal 的条数 / V 的条数 |
| 原始与校准误差 | 在相同 V_cal 上报告 mean、median、max，并列逐请求值 |
| 原始与校准低估率 | V_cal 中 E<A、C<A 的行数分别除以 V_cal 条数 |
| 明确 overflow | 实际发送中明确被归类为输入超限的次数 / 物理模型 HTTP 次数；列出恢复结局 |
| 阈值行为 | 每次 C、T、B 与 trigger/skip/fallback/post-check，禁止从成功请求倒推出未触发压缩一定正确 |
| 耗时 | 本地计量耗时与模型等待分别报告；不能拿总延迟差声称计量开销 |

本轮样本上，只有 V_cal 至少 3 条、同批校准 median 误差低于原始 median、校准 C 的低估率不高于原始 E 的低估率，才可写“这组样本的校准改善”。否则报告不改善或证据不足，不能用调整材料后挑选通过结果。改成单系数不放宽这项既定效果门禁；不能以估值下降、压缩次数减少或实现更简单代替准确率证据。逐一解释剩余 overflow，必要时回到设计；不承诺零 overflow，也不把本探针写成单系数优于其他策略的对照实验。

不报告“无必要压缩率”这样的伪精确指标：未发送候选没有对应 provider 真值，不能仅用本地估算标定它本来无需压缩。可报告 C 与实际预算阈值的距离、压缩触发次数和拒绝原因，供人工检查；不得事后构造另一个估值宣称已经验证双系数收益。

摘要质量只检查预先固定的合成事实是否保留，另记 quality_checks；不因 ACK 或 token 下降就判断摘要语义通过。当前 7～12 请求仅是有界接线和误差样本，不能代表所有模型/语言/图片/长上下文分布。

## 8. 探针自身的 TDD

新增 `tests/test_context_calibration_probe.py`，不调用真实模型，验证：

1. 缺 --live 的所有路径零网络；无效目标不回退，配置文件内容/mtime 不变。
2. 指定模型、原预算和 thinking 不变；只有探针拥有的 SDK 禁止重试。
3. HTTP 边界最多 12 次；第 13 次在发送前拒绝，包含自动摘要和 recovery，重开共用原账本。
4. C1～C6 串行、只有受限 read、应用关闭/异常都会清理拥有的客户端，原异常不被清理覆盖。
5. 方法、purpose 和 E/C/A 正确配对；重复 finish 不重复计数；missing/partial/summary 分母正确。
6. 报告独占创建、非法输出不覆盖；每个终局 flush，失败也保留安全数字；敏感哨兵字符串不出现。
7. 模拟时限、provider失败、C4跳过/失败、无usage、预算耗尽，都得到指定退出码和后续 not_run。
8. 四类适配器 guard fixture；未覆盖的 SDK 发送形态预检阻断。

报告写入失败是本探针必要结果保存失败，非“观察失败”，必须停止并明确退出，不继续消耗模型请求。公共产品响应语义不因这个离线/独立探针改变。

## 9. 收尾清单与授权边界

- 搜索普通 Agent/compaction/preview 链残留的旧预算调用，列出移除和保留项；长期记忆/Auto 审查不迁入本片。
- 本地代码、前端类型、序列化及 Runner profile 一并验证；模型身份沿现有配置接线，不新增校准窗口配置或对应示例。旧模型配置继续可用，不改用户真实配置、依赖版本或现有数据库。
- 新文件均小于 500 行；纯文档检查、有效红灯、确定性绿灯、构建与真实请求分别记录。
- 每份证据绑定同一实现 SHA 或明确的工作区源码哈希；测试中途再改源码则重跑受影响项，最终全量与产物重新绑定。
- 只写明实际运行过的命令、退出码、通过数量、报告位置；失败或未覆盖保留，不以旧记录替代。
- 总方案/S1～S4 标记实际完成状态，更新 handoff 顶部短摘要；不更新课堂掌握记录或《关键知识点》。

本片不含 commit/push/tag/PyPI/Release/部署或安装替换用户环境。代码回退只忽略派生校准 metadata，不自动还原已经提交的摘要；不得把回退写成无损恢复历史。用户随后明确要求实现时可连续按 S1→S4 执行；尚未获得目标模型真实调用范围时先完成所有确定性工作，不伪造现场验收。

## 10. 本地实施与现场证据（2026-11-16）

新增 scripts/probe_context_calibration.py 与 context_calibration_report.py，复用临时组件应用、RequestBudget/guard_catalog、正式会话/工具/压缩/重开和现有快照/日志；不建立另一套 Agent 循环。探针独占创建报告、冻结发送前 E/C、完整返回后配 A、摘要按 sample_id 配对，取消/异常/必要报告保存失败终止后续。新增 guard 四协议真 SDK 受控 HTTP 断言、探针编排/数值与失败测试。摘要 started 日志补 method/invalidation_reason 两个白名单字段；探针在自己生命周期内计时既有 measure_request 调用，不另算预算。release_smoke 增加安装态两轮有效 usage、快照比对与 SQLite 重开断言。

测试红灯分别记录在 build/s4-probe-red.log（6项行为失败；此前缺模块的收集失败不算有效红灯）、s4-probe-pair-red.log、s4-summary-log-red.log；release smoke 红灯为旧实现没有有效样本。最后探针/四协议/摘要定向30项通过。补上C5/C6丢样本假成功回归（build/s4-lost-state-red.log），逐条核对上一完整有效E/A生成的C；受控六场景fixture同步使用真实公式，原fixture仅固定给400不能表达该合同。四协议 guard 本身已有实现，此处新增覆盖不冒称新机制 TDD。

真实配置只读选择 .env/models.json 的默认 heiyu-gpt-5.5（Responses）。现场执行：

```bash
.venv/bin/python -m scripts.probe_context_calibration --config .env/models.json --model-id heiyu-gpt-5.5 --report build/spec143bl-s4-live.json --live --request-limit 12 --total-timeout-seconds 600
```

实际退出3；4次物理请求，C1/C2/C3完成，C4手动压缩返回failed，C5/C6未运行。没有调整预算/材料重试。报告保留首次现场源码hash；后续补摘要method、operation报告和本地计时的收尾版本未重复现场请求，不将首次报告冒称最终源码全流程验证。该次未收集到压缩固定reason，不能仅凭保留额度推断根因；最终探针已收集现有 compaction_finished/recovery_finished 的固定reason和终局。

| 请求 | E | C | A |
|---|---:|---:|---:|
| C1 |2288|2288|10416|
| C2 |4675|21283|13881|
| C3 工具前 |4965|14743|14132|
| C3 工具后 |7325|20850|16984|

相同3条已校准行：原始/校准误差中位数64.87%/22.76%，均值62.69%/26.80%，低估率100%/0%；完整返回usage可用率100%。满足“这组样本改善”，不代表模型全分布或摘要质量通过。没有现场摘要调用，因此摘要保真、压缩后续聊和现场重开均缺证据；确定性恢复测试不能替代这一缺口。

浏览器新增 context-calibration.spec.ts 六类夹具×两视口；截图为合成夹具，不是现场模型效果。标准Mac构建、临时源码外安装和全量测试终局见交接的本轮收尾条目；不包含提交、push或发布。

最终工程验收：相关回归293项通过（build/spec143bl-s4-regression-final.log）；前端78文件688项、harness类型检查、生产构建、最终产物两视口12项浏览器均通过；标准macos-arm64 wheel与源码外临时安装通过（spec143bl-s4-final-install.json含context_calibration=passed），关键计量模块与wheel源码逐字节一致。pip check、compileall、git diff --check通过。

后端全量未通过：分片0为3259 passed；分片1为2902 passed/1 failed后提前停止。失败为 tests/test_storage_dependency_direction.py::test_application_does_not_construct_database_sql，单独复跑仍失败，定位未跟踪的并行记忆文件 application/memory/memory_comparison_reads.py:21 在应用层构造SQL；未修改此并行工作。两份终局为build/spec143bl-s4-verified-backend-{0,1}.xml，不能将此前主动中断的探索全量当作通过结果。完整证据索引build/spec143bl-s4-verification.json，包含S4文件哈希和运行前后整体源码哈希（其他并行改动可能变化）。

当前结论：S4实现与有界现场验证已交付，但整体工程门禁因并行存储分层失败未通过；现场C4/C5/C6仍缺覆盖。下一步先协调修复并行存储分层失败并复验全量，再单独定位现场压缩失败，不调整材料挑选更好指标。未提交/push/发布。

### 10.1 继续验收：Responses 历史内容修复范围

现场诊断返回 unsupported_summary_content；安装版本 SDK 将客户端 function_call 同时保留在 AI.content 与 AI.tool_calls，reasoning 也保留在 content。原严格通用文本校验拒绝这些已知块，摘要前即失败。新增仅供压缩历史使用的 context_summary_content.py（原压缩文件483行，不能继续内嵌扩张）：AI reasoning/thinking/redacted_thinking 不作为可见事实进入摘要；Responses function_call 只有 id/name/JSON arguments 与已解析 tool_calls 一致时才省略重复内容，由原工具序列化输出一次。未知块、矛盾调用、服务端工具与非AI内容继续拒绝；原消息不修改，工具配对/必要提交/预算策略保持。正常链是正式probe→manual compact→校验与序列化→既有summary IO；失败链在矛盾内容校验处抛unsupported_summary_content，旧checkpoint不写。先以SDK同形块复现，随后做确定性与有界现场复验，不以换材料提高指标。


继续验收先单独复现存储分层失败。应用侧 suppression 查询改走 MemoryRepo.any_suppressed，内部复用 MemoryWriter.suppressed，所有来源保持同一只读事务；新增遗忘来源仍拒绝的行为回归。未修改配置、权限或记忆语义。

现场诊断使用同一配置/固定输入，最多8次，实际4次，报告build/spec143bl-s4-diagnostic-live.json：C4固定reason=unsupported_summary_content。新增 tests/test_context_calibration_probe_runtime.py 将已安装SDK同形的reasoning/function_call块送入正式应用链，修复前同样失败；修复后六场景通过。tests/test_context_summary_content.py 保证匹配的调用序列化一次、原消息不变，矛盾参数/未知块/非AI推理块仍拒绝；缺失/非法arguments单独复现后统一为受控失败。

修复后独立完整现场复验：

```bash
.venv/bin/python -m scripts.probe_context_calibration --config .env/models.json --model-id heiyu-gpt-5.5 --report build/spec143bl-s4-fixed-live.json --live --request-limit 12 --total-timeout-seconds 600
```

退出0，7次物理HTTP，C1～C6全部完成，C4 applied（事前估值16998→提交后6838），固定事实保留。C5 E/C/A=2962/6868/7740，C6=2981/7790/7760，证明agent比例压缩carry与SQLite重开后可用；summary E/C/A=4815/11165/13992，method=inherited_agent_ratio，未污染agent比例。5条已校准主请求原始/校准误差中位数61.73%/11.27%、均值62.28%/18.41%、低估率100%/20%；用量可用率100%。本组样本改善，仍存在低估，不能推广为任意模型或零overflow保证。三次现场尝试分别4、4、7次，累计15次；每轮有独立上限与报告，旧失败报告保留，未更换材料和原预算。

相关680项回归通过（build/spec143bl-s4-fixes-focused.log），包含存储分层、记忆、摘要内容、校准/压缩/恢复和安装smoke。最终完整工程结果与源码/产物一致性记录在build/spec143bl-s4-fixes-verification.json。


最终安装门禁额外发现并行记忆双阶段迁移的旧夹具：release_memory_smoke仍给单阶段响应、缺matter，源测试可复现。已同步提取/比较两次固定响应和精确2次调用/62输入tokens/2对生命周期断言，保留保存/历史/归属/异常检查。test_model_memory_output中已删除response_schema入口同步迁到ExtractionResult.model_json_schema，协议、严格输出、输出cap与不修改原模型的断言不变；两组46项通过。更广相关759项通过。旧安装失败报告和已加载旧测试字节码的分片失败保留；最终结果仅取修正后终局，不恢复废弃接口、不删/skip或放宽测试。


最终终局：后端四片1589+1777+1714+1761=6841通过，0失败/错误/跳过；index2取closed-backend-2-recheck.xml，其余取closed-backend-{0,1,3}.xml。并行记忆新增比较矩阵/候选读取在全量期间继续改动，额外41项回归通过，不与全量重复相加；其文件hash变化在verification中标明，安装产物按构建快照验收。前端688、浏览器12、标准macos-arm64构建、closed-install源码外安装（校准与两阶段记忆全部passed）、compileall/pip check/diff检查通过。最终证据build/spec143bl-s4-fixes-verification.json，现场六场景报告fixed-live.json。此前失败/中断报告均保留不计通过；未提交/push/发布。该组固定样本及Mac安装通过，不代表其他模型、任意摘要语义或Windows/Linux原机验收。

### 10.2 只读审查后的探针修复（2026-11-16）

成功门禁要求六场景完成、各场景必要计量齐全（C3至少两个主调用、C4摘要），全部记录必须完整返回且E/C/A有效，pending和已结束的incomplete均不得通过。按发生顺序分别维护agent/summary最近有效样本，首次summary允许继承agent，后续使用summary自己的比例；错误比例退出1，缺证据/usage退出3。

探针专用ProbeBudget在每次HTTP发送前检查Measurements.summary_failed。摘要finished即使未配对，只要带error_type也设置终止状态；原产品主动摘要fallback不能继续发送下一主请求。守卫抛既有AcceptanceBudgetStop，探针退出1；C4执行failed退出1；固定reason为无安全切点、摘要预算不足等未覆盖情况以及skipped保持退出3。正常产品压缩降级逻辑不变。

测试夹具补充正常摘要started/finished，缺usage夹具保持无可用摘要usage；新增六个红灯用例及正式应用摘要ReadTimeout→fallback发送前阻断回归。验证结果见handoff顶部本轮摘要。没有追加真实模型请求；之前现场报告属于修复前探针，不冒称本次失败边界的现场证据。

### 10.3 最终review四项修复（2026-11-16）

- 成功门禁保留每次失败尝试。只有同run_id/model_call_id的一次failed→一次returned，且context_recovery_finished确认recovered/retry_attempts=1，才允许首次overflow失败退出误差集合后通过。未恢复、跨run错配、多次失败、无retry不能豁免；失败计量与恢复记录仍入报告。正常返回缺usage仍退出3。
- 所有场景监听既有tool_call_started/finished；只允许C3的一次read，其他场景调用、未知/未启用工具、非成功终局设置tool_deviation。ProbeBudget在下一次发送前拒绝，计划退出1；参数及实际目标仍由ReadOnce和C3最终配对断言核验。不更改产品权限行为。
- v2 usage必须包含四字段；v1缺cache_write_tokens继续兼容。原始记录不修改。
- 摘要输入支持Anthropic客户端tool_use：id/name/input必须与规范tool_calls一致，input为对象；验证后由原规范调用序列化一次。未知/服务端块、矛盾及不完整工具配对仍拒绝；原消息不变。正式应用回归同时覆盖Responses和安装SDK实际_format_output生成的Anthropic消息，再走manual compact→续聊→SQLite重开。

本轮新增测试先确认5个行为红灯（overflow误判、两种工具偏离、v2缺字段、Anthropic历史拒绝），随后最小修复并补未恢复/跨run/重复失败等反向断言。验证与提交结果见handoff顶部。未追加真实模型请求。

最终提交集合独立导出验证：后端四片1801+1711+1750+1550=6812通过，0失败/错误/跳过；定向284、前端688、两视口浏览器12通过；标准Mac wheel、源码外临时安装、compileall/pip check/diff通过，10个改动生产模块包内逐字节一致。全量发现两条旧测试需同步版本/字段合同：test_session_state纳入thinking_level断言；test_model_usage_paths明确v2缺字段拒绝、v1兼容，原非法数字和不修改原记录检查保留。修正后对应完整分片重跑，旧失败保留不计通过。证据build/spec143bl-final-commit-verification.json；本次提交隔离未暂存记忆/配置改动，无真实模型请求。
