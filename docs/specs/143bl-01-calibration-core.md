# Spec143BL-01：单系数计量、模型身份与样本

日期：2026-11-16。状态：**已按用户确认改为单一动态系数与单一校准估值，S1已实现并通过本地验证**。总合同见 [Spec143BL](143bl-context-token-calibration.md)，下一片为 [143BL-02](143bl-02-runtime-compaction.md)。本片不改变模型调用次数、压缩触发行为或消息提交路径。

## 1. 本片目标与现状

建立请求计量、样本校验与模型身份，使 S2 能把一次实际 input usage 正确配给该次请求，并让后续消费者使用同一个校准估值 C。窗口、输出额度和压缩阈值继续沿用现有合同。

核对基线为 `60108431ceeeaf100df008da4765b879505fc1d7` 加当前工作区；既有 143BK 手动压缩修复已在该提交中，其他并行改动保留。

当前源码事实：

- `model_access/configuration.py:98` 的 parse_model_configuration 校验模型配置并生成 definition/public_configuration；本片不新增用户配置字段。
- `model_access/__init__.py:176` 的 catalog_from_configuration 构建 SDK 模型，with_thinking 在第116行按请求复制模型。元数据已用于上下文窗口/输出/压缩参数，本片沿同一模型装配接入身份。
- `cloud_runner/models.py:11` 的 RunnerModels 用 bootstrap 凭证与任务代理路由构建 SDK；renew 仅轮换凭证。`application/cloud/models.py:54` 已生成含真实上游策略的稳定 profile_id。
- `agent_runtime/context_usage.py:89` 和 context_compaction 的各预算点仍直接字符估算；`model_access/usage.py:22` 已归一化 input，不能再次相加缓存。
- metadata 可随消息保存；本片不新增数据库表、顶层 checkpoint 字段、全局缓存、锁或 ContextVar。

本稿替换先前未实施的双系数设计。此前 r=A/E 时，`ceil(A+r×(E_new−E))` 与 `ceil(r×E_new)` 完全等价，前缀基准没有为该预估带来额外数值优势；近期最大比例则只保证更保守，尚无本项目效果证据。故取消第二系数、P/Q双估值、N样本窗口、前缀基准及其内容指纹；不留下另一条隐含决策公式。

## 2. 单一数值合同

E 为未经校准的整请求估算，A 为同次完整有效响应的 normalized input_tokens。对当前 identity、purpose、media，使用该槽最新有效样本的 `r=A/E`；校准值 `C=ceil(r×E_new)`。显示、压缩触发、摘要预算和发送检查使用同一个 C。

| 条件 | C | method |
|---|---|---|
| 当前用途/媒体有有效样本 | ceil(r×E_new) | ratio_estimate |
| text摘要没有自身样本，但同身份有agent.text样本 | ceil(r_agent_text×E_new) | inherited_agent_ratio |
| 没有兼容样本、身份不可用或请求形态不支持 | E_new | uncalibrated |
| 原始估算不可用 | null | unavailable |

每个用途/媒体只保存最新一份有效样本，不取历史最大值、不做累计平均或 EMA。r 可以小于1；没有人为设为至少1的下限，也不使用1.25等固定上限。不增加固定5%或其他误差余量。r>0 由严格的 E>0、A>0保证；大但合法的比例不能悄悄裁小。

只保存整数 E/A；使用标准库 Fraction 或整数算式 `(A×E_new+E−1)//E` 得到 C，避免浮点误差改变阈值等号边界。新完整有效响应立即替换对应槽，缺失 usage 不替换为0，不改变已有样本。

C 是经验预测，不是实际测量或数学上界。内容分布、system/tools和图片变化可能使历史比例不再代表新输入；本片保留原有错误恢复，并由 S4 收集误差和 overflow 证据，不声称零溢出。

## 3. 模型身份与配置边界

本片不新增 `context_estimation`、`calibration_sample_limit` 或 `safety_margin_ratio` 配置；这些属于已撤回且未发布的设计，不需提供兼容解析。现有配置解析、默认值、public_configuration 和配置示例保持原语义。真实 models.json、.env、部署配置不在编辑范围内。

在现有模型 metadata 中新增内部键 `naiyue_context_estimation`。其常量可沿已有键的组织方式声明在 configuration.py，但该键不是用户可配置对象。模型装配从已解析的实际模型、能力和请求设置构造以下字段，不新增独立配置服务：

| 字段 | 类型与来源 |
|---|---|
| version | 整数1；计量合同版本 |
| route_fingerprint | 64位小写SHA256十六进制；本机有效上游或云端稳定profile |
| model_id / request_model | 配置逻辑ID / 发送前已经解析的模型名称 |
| kind / api | 现有provider kind / 现有api，非OpenAI api为null |
| thinking_fingerprint | 有效native thinking设置的规范JSON指纹 |
| input_profile_fingerprint | image_inputs、reasoning_output等影响输入表示的已解析能力指纹 |
| adapter_fingerprint | 原始计量/本项目输入投影版本＋适配器与langchain-core版本的指纹 |

identity 由上述事实的规范JSON求SHA256，包含计量版本；键排序、UTF-8、紧凑分隔符、拒绝NaN。这里只哈希配置与适配事实，不哈希请求正文、历史前缀、system文本或工具schema，不记录credential、header、原始endpoint。不将窗口、reserve、输出cap、超时、SDK重试、温度纳入身份；摘要绑定输出cap不改变身份，有效thinking预算改变则改变身份。

本机 route_fingerprint 在catalog构建模型后取**有效endpoint**，包含SDK环境变量解析结果；不能只哈希可能为空的配置base_url。沿现有catalog_lifecycle的同种SDK对象读取base_url：OpenAI root_client、Anthropic原生client、Ollama模型/原生client的已解析地址，只读构造信息、不发请求。规范化只去末尾`/`，不合并不同path；无法取得时identity=None，标identity_unavailable，不用随机值伪装跨重启稳定身份。

RunnerModels构建catalog后、暴露模型前，用`bootstrap.models.profile_id`的指纹替换route_fingerprint。临时任务代理URL、task_id、短期bootstrap token不参与；既有profile和renew协议不变。真实上游策略改变使profile变化；key轮换、凭证续期不误失效。

with_thinking为复制后的模型创建新的metadata字典并更新有效thinking_fingerprint，不修改catalog中原模型。MODEL_DEFAULT继续返回默认模型。此处只增加内部装配，不改变公开Python API签名。

## 4. 请求表示与计量边界

计量对象是ModelInput投影后的框架请求，仍早于SDK完整线级payload。调用者先完成必要输入投影；纯计量模块不读取文件、不联网、不执行工具、不重复准备模型。

原始E继续由现有count_tokens_approximately取得，包含system、projected_messages、tools；禁止启用其内置use_usage_metadata_scaling，避免分母已被校准。工具schema沿现有公开convert_to_openai_tool支持的表示计数，不调用SDK私有payload构造器，也不新造provider serializer。

对整个新请求计算E，再乘一次r。上一回复只按其实际回传内容进入E，不另加output_tokens；工具图片carrier按既有投影计入，不再计一次源图片。消息id、本地metadata或前缀是否相同不决定另一条公式；本片没有内容指纹和旧A增量复用。

历史编辑、system/tools变化和压缩后都重新估算完整输入。只要当前可信消息链仍提供兼容样本，就继续按比例预测；样本随被删分支一并消失时冷启动。比例复用是启发，不意味着新schema开销已经实测。

当前生产主链response_format=None。非空response_format、供应商内置server tools、多次采样/隐藏上下文请求或无法确认表示的内容，标unsupported_request_shape，使用原始E、不应用旧比例、不产生新样本。原始计数失败则unavailable；必要输入转换/配置错误仍由原调用链传播，不能当作计量降级吞掉。

形态检查覆盖system与projected_messages的同一整份输入；AI消息中的普通tool_use/function_call内容块属于支持的客户端工具历史，server_tool_use等服务端或未知块仍禁用采样。

media只有text/image：实际投影后含图片block为image，图片已替换为文本则为text。未知PDF、音频或opaque server context不作为已支持形态。图片内容不会被复制、哈希或写进校准记录/日志。

## 5. 消息内的持久化schema

独立键为`message.response_metadata['naiyue_token_calibration']`。记录为纯JSON，最多16 KiB；字段集合精确，拒绝未知字段/version、非有限数值及不一致关系。旧消息无字段是正常冷启动，不回填、不迁移旧库。此前双系数schema从未实施，本片直接使用下面的version=1形状，不读取其废弃草稿字段。

### 5.1 CalibrationEnvelope

| 字段 | 类型 / 约束 |
|---|---|
| version | 整数1 |
| identity | 64位小写hex |
| revision | 正整数；当前identity消息链内的派生记录版本 |
| source | response / summary |
| agent | PurposeState，普通请求的两媒体槽 |
| summary | PurposeState，摘要请求的两媒体槽 |

PurposeState精确字段：`reported_model_hash`（同用途最后一次明确报告的model名称的64位小写hash，或null）、`text`（Sample或null）、`image`（Sample或null）。固定两个用途、每用途两个媒体槽；每槽只保留最新一份，总共最多4份，没有可配置保留数量。

Sample精确字段：`sample_id`（32位小写uuid hex）、`raw_input_tokens`（本次E>0整数）、`actual_input_tokens`（本次A>0整数）。用途和媒体由所属槽表示，不重复存；一个envelope内所有非空样本ID必须唯一。所有整数拒绝bool，E/A始终是原始估算与对应实际量，不保存C作分母。

reported_model_hash属于用途级的“最后已知报告”，不是声称每个Sample都收到该名称。新响应没有报告model时保留最后已知hash；不把缺失名称补进本次样本事实。

记录不参与provider正文/参数构造；S2/S4通过真实SDK请求fixture验证该键未发送。序列化大小保护在读取前后都执行。

### 5.2 选择、替换与坏记录恢复

只读取调用者给出的当前可信消息链，不查询累计用量表，不跨会话/分支搜索。先读取有界头部的identity和正整数revision，把当前identity最高可识别revision记为revision_high_water；只完整选择该最高版本。相同revision、内容完全相同的副本去重；不同内容冲突使当前校准失效，不任意挑选，不回退低版本。

最高记录校验失败时标invalid_record、C=E，保留high-water。下一次有效响应以high-water+1建立新记录，能够覆盖坏记录。较低版本损坏只诊断，不影响更新的有效记录。无法识别identity/revision的坏头部忽略并诊断，不推断其顺序，也不影响其他模型；无可识别头部时high-water=0。

每次实际请求开始由S2生成独立sample_id；retry、摘要各自独立，不共用逻辑call_id。同一冻结measurement与同一响应重复调用complete_sample必须生成相同候选；若输入记录已有相同ID、相同槽和E/A，则返回原记录作为幂等结果；同ID不同内容/槽是冲突，诊断并返回None，不覆盖。

新有效样本只替换对应purpose/media槽，继承其他仍兼容的槽，生成source=response、revision=high-water+1的新记录。每个槽旧比例在本槽新有效响应后立即退出，不等待其他请求累积；新媒体响应不覆盖另一媒体槽。

carry_compaction_state先校验非空summary_envelope的identity必须与agent_measurement.identity一致；不匹配则诊断并返回None，不混合两种身份的状态，也不阻断原压缩。校验通过时以非空summary_envelope为唯一基底，否则继承agent_measurement.source_envelope；不再合回更旧的记录。返回source=summary、revision高于两个输入high-water/记录版本的副本，因此位于消息头的新summary仍胜过后缀旧AI。它不新增agent样本、不重拟合比例、不建立旧输入基准。

当前identity有效但无样本时，carry可生成空槽记录，防止保留后缀复活已经失效的旧记录。identity=None时返回None，S2继续原压缩与必要提交，不能因缺少派生metadata阻断。没有有效summary usage仍可carry旧记录；摘要失败、候选未提交时旧消息不变。

### 5.3 响应model的用途内隔离

从框架归一化的response_metadata.model_name/model字符串取得hash，不保存原始响应体。请求alias与响应snapshot名称不同是正常情况，不直接比较二者。

新有效响应的已知model hash与当前同identity、同purpose的reported_model_hash明确不同时，清空该purpose的text/image槽，再写本次样本并更新hash；另一purpose保持不变。缺失/null报告不清空槽或最后已知hash。实际请求model、路由、API、thinking或适配身份改变仍按identity隔离，两用途旧记录都不兼容。

这只能在响应后发现显式变化，不能提前识别同alias背后的隐藏切换，也不替代模型选择/授权检查。

## 6. 内部接口与所有权

新增`agent_runtime/context_calibration.py`，集中公式、schema、记录选择与样本生成，不新增计量服务。文件≤500行；若展开后超过500行，固定将Envelope/PurposeState/Sample、read_calibration、头部high-water与记录选择迁到`context_calibration_records.py`，主模块单向调用。消费者仍只使用本节入口，不新建注册层或平行算法。

| 接口 | 输入与结果 | 实际消费者 |
|---|---|---|
| measure_request(model, system_message, projected_messages, tools, *, purpose='agent', response_format=None, calibration_history=None) | 返回不可变RequestMeasurement；显式history只作样本来源，缺省从projected_messages读取 | S2普通请求/压缩候选/摘要；S3预览 |
| complete_sample(measurement, response, *, sample_id) | 唯一完整AIMessage与该次冻结measurement；返回新envelope或None，不修改输入 | S2普通响应、校验成功的摘要响应 |
| carry_compaction_state(agent_measurement, summary_envelope) | 第二项可空；返回source=summary的候选envelope，身份不可用时None | S2构造候选Human summary |
| read_calibration(value) | 严格读取单份JSON，返回不可变/复制后的记录或None | measure_request与持久化测试 |

S2接线时将BudgetEstimate/RequestMeasurement的定义原样迁至contracts/context_calibration.py，以供运行状态与计量核心共享且满足静态分层；算法和schema不变。

RequestMeasurement精确保存purpose、media、identity（可空）、source_envelope（可空）、revision_high_water、budget、sampling_eligible、calibration_ratio。budget available时calibration_ratio为正的精确Fraction；无可用样本时为1，只服务C=E和规划，不代表存在一个实测样本。budget unavailable时calibration_ratio=None，消费者不得自行补默认值伪装可用预算。请求对象不保存正文副本、内容hash、前缀数量或样本数量配置。

BudgetEstimate字段固定：status（available/unavailable）、raw_input_tokens（E）、calibrated_input_tokens（C）、method、invalidation_reason。available时E/C为非负整数；unavailable时E/C均null、method=unavailable。S3沿现有ContextEstimate暴露E/status，只将method、C、reason放入calibration投影。

method固定为：ratio_estimate、inherited_agent_ratio、uncalibrated、unavailable。invalidation_reason为null或：no_samples、identity_changed、identity_unavailable、media_changed、unsupported_request_shape、invalid_record、raw_estimate_unavailable。

同次多个原因成立时按“raw不可用→不支持形态→身份不可用→坏记录→身份改变→媒体无样本→无样本”报告首因。存在兼容样本、包括summary继承agent.text时reason=null；未采用本槽样本的具体来源已由method表示。不报告system_changed/tools_changed/history_changed等已取消的前缀失效原因，不把异常正文作为reason。

### 6.1 measure_request的固定顺序

1. 调用者已执行必要输入转换/配置校验，本函数不捕获这些前置错误。
2. 计算完整请求E；仅在本估算边界将原计数器失败转为unavailable，记录安全error_type。
3. 确定shape、identity和media；identity有效时读取唯一最新记录及high-water。未知身份/不支持shape仍保留E、C=E，sampling_eligible=false。
4. 有效记录中选择当前purpose/media槽，取r=A/E；summary.text槽空时仅可继承同identity的agent.text，不能继承image或跨用途的其他组合。
5. 有样本则C=ceil(r×E_new)，无样本则C=E。若同identity当前purpose只有另一媒体槽，报告media_changed；只有其他identity记录时报告identity_changed；其余无样本报告no_samples。
6. 返回本次不可变结果。坏记录或无样本不阻止后续建立新样本：shape/identity/E合法时sampling_eligible仍为true。不改持久状态，不因观察事件成败改变结果。

system_message为单个SystemMessage或None；现有system_messages=[]/[prompt]分别映射为None/prompt，不丢弃或拼接额外系统消息。摘要拆出唯一system，余下消息作为projected_messages、tools=[]、purpose=summary。当前未知的额外系统形态须核对消费者，不能静默只取第一条。

### 6.2 complete_sample的边界

调用者仅在完整响应返回后调用，负责从ModelResponse.result提取唯一AIMessage；零条/多条不猜测对应关系。函数验证sampling_eligible、E>0、normalize_model_usage(response.usage_metadata).input_tokens>0及支持的单次输入语义。通过后更新对应槽；失败返回None，不表示清空历史。

原始Anthropic缓存已由适配器归一化，不能再次加缓存；output、reasoning和total不进入A。取消/部分流/错误响应不采样；外层门禁拒绝的响应不会进入checkpoint，其临时记录自然不持久化。

complete_sample不决定业务成功/失败，不发布生命周期事件，不负责必要存储。S2将记录附到响应并由原checkpoint提交；S3的统计快照属于独立观察数据，不是校准状态来源。

## 7. 一个连续例子与失败边界

第一轮发送前E=1000，没有样本，C=1000；完整返回A=4000后，agent.text槽存E/A=1000/4000，下一测量r=4。

第二轮完整输入E=1500，C=1500×4=6000；返回A=5400，立即用1500/5400替换该槽，r=3.6。第三轮E=2000，统一C=7200；显示、压缩与发送检查均使用7200，不再产生7400的另一个决策值。未回传的output统计不额外加进输入。

高估也可以纠正：最新E=2000/A=1000，r=0.5；新E=3000时C=1500。合法大比例例如E=1000/A=8000，则新E=2000时C=16000，不截为1.25或5。

无usage响应使complete_sample=None，下一请求仍读取原槽。更换模型身份后若可信历史没有该身份样本，C=E冷启动。压缩成功携带原agent样本，按压缩后完整E乘原比例；新主响应再替换本槽，不声称压缩后已实际测量。

原E不可用时返回E/C=null；若S2此前已经确定原请求超预算，之后候选计量不可用必须阻断，本纯模块不自行决定放行。最高revision损坏时本次冷启动，后续有效响应以更高revision恢复；候选失败/取消/必要保存失败不能提前改变旧消息记录。

## 8. 实施顺序与修改范围

1. 先写纯计量与身份行为的失败测试，固定本稿单值公式和无新增用户配置合同。
2. 沿现有catalog/model metadata接入有效身份，验证本机、thinking复制和Runner profile。
3. 实现严格schema、最高revision选择、纯measure/complete/carry，逐项转绿。
4. 清查新增字段的真实消费者和序列化，记录S1能力边界；S2再替换实际预算消费者。

| 文件 | 本片修改 |
|---|---|
| model_access/calibration_identity.py（新） | 身份构造与thinking更新，catalog/复制/Runner三处复用；不改用户schema |
| model_access/__init__.py | catalog构造后metadata接线、有效route读取、with_thinking复制更新 |
| cloud_runner/models.py | catalog构建后注入稳定profile身份，renew行为不变 |
| agent_runtime/context_calibration.py（新） | 单值计量、严格记录与纯函数 |
| tests/test_context_calibration.py（新） | 单系数、样本替换、严格记录及异常回归 |
| tests/test_context_calibration_identity.py（新） | 本机/云端身份与thinking复制 |
| tests/test_model_limits.py、tests/cloud/test_model_configuration.py | 原配置/public配置/profile不变及无敏感值断言 |

不修改docs/examples/models.example.json，不新增用户开关。S1不替换现有压缩/预览消费者，也不删除独立原始计数用途；这些由S2/S3迁移。本片完成只能称“单系数核心与模型身份已接线”，不能称“压缩已经使用校准”。

## 9. TDD与验收

先确认行为失败，再写最小实现；缺模块/import错误不能替代公式或接线行为红灯。不修改conftest、不放宽现有断言、不用模型自述代替检查。

| 用例 | 必须断言 |
|---|---|
| 连续1000/4000、1500/5400 | 第三轮E=2000时C=7200，旧4倍立即退出本槽，无第二估值 |
| 小于1、极高倍率、整数边界 | C可低于E；无固定裁剪；大整数/向上取整不受float误差影响 |
| 分母与响应配对 | 样本E是发送前原始估算，不能用C或其他请求E替代 |
| 缺失/零/负数/bool/部分usage | 无新样本、旧槽不清空；缓存/输出不重复计入 |
| 四槽隔离 | 同用途/媒体只保留最新样本；agent.image不污染agent.text或summary.text |
| schema与16KiB | 未知字段/version、非法hash/id/revision/重复样本ID拒绝；空槽合法 |
| 最高revision与恢复 | summary在消息头仍胜过后缀旧AI；副本去重；冲突冷启动；新有效响应覆盖坏高版本 |
| 采样幂等 | 同ID同内容不重复推进或改变记录；冲突ID不覆盖 |
| 内容与system/tools变化 | 重估完整E、仍可按兼容比例预测；没有前缀匹配分支 |
| provider/模型/API/thinking/版本 | 身份变化隔离，模型复制不改变原metadata |
| 本机effective endpoint | SDK环境解析后的路由变化可见；URL/key不进入记录或日志 |
| cloud profile与renew | token/task route续期不改变身份，稳定profile变化会改变 |
| 响应model变化 | 同purpose报告hash变化清该用途两槽；null保留已知hash，不混写样本事实 |
| summary携带 | 继承agent.text只作为先验；无usage仍可carry；无identity或两输入identity不同则返回None且不阻断原压缩 |
| unsupported与坏记录 | 不支持shape不采样；坏记录但身份/shape/E合法时下一响应可重建 |
| 配置兼容 | 既有配置/public配置不变，没有需要用户填写的新字段 |

实施后执行：

```bash
.venv/bin/python -m pytest -q tests/test_context_calibration.py tests/test_context_calibration_identity.py
.venv/bin/python -m pytest -q tests/test_model_limits.py tests/test_model_access.py tests/cloud/test_model_configuration.py tests/test_model_usage.py
.venv/bin/python -m compileall -q naiyue_test_agent
.venv/bin/python -m pip check
git diff --check
```

全量/构建门禁见S4，每次实际提交仍满足项目要求。记录有效红灯、实际命令和最终结果；纯函数、MockTransport和结构检查不等于真实模型效果。

本片已实现核心模块与模型身份，验证记录见下节；实际请求采样和压缩预算尚未迁移。S2直接消费第6节接口，S3投影同一C，S4在同批有效请求上比较原始E与C的误差；不得重新引入双系数或未验证的保守上界。

## 10. S1本地实施记录

实际实现为 context_calibration.py（232行）及 calibration_identity.py（49行）。身份模块由catalog构造、with_thinking和Runner三处复用，不需要修改configuration.py或用户配置。核心返回冻结的数据对象，历史记录读取/生成均复制，避免就地修改传入消息；它不执行模型、工具或必要存储。

身份接线测试先取得有效行为红灯：构造模型缺少校准metadata；接线后转绿。首次夹具曾误将base_url放进原生OpenAI配置，修正为兼容provider后才得到上述红灯，夹具错误不计复现。核心测试在实现前写入，但未单独运行到算法行为红灯，不把整片所有算法声称为完整TDD证据。

正常链：catalog→attach_identity→模型metadata；measure_request冻结E/C与来源→complete_sample配本次A→调用者把候选记录附到消息→下一测量使用新比例。失败链：坏最高revision→本次冷启动→有效响应以更高revision恢复；无身份不采样/不伪造carry；估算异常返回null并记录安全error_type。ModelInput、压缩和checkpoint实际采样接线归S2。

本片21项新增测试通过；合并模型配置/限额/用量回归共152项通过。前端78文件681项及TS/Vite构建通过，后者只有既有chunk体积警告；compileall、pip check与diff检查通过。最终后端全量两分片3342＋3353＝6695项全部通过，0失败/错误/跳过；报告build/spec143bl-s1-backend-{0,1}.xml，汇总build/spec143bl-s1-verification.json。早版全量因缩小身份中的输入能力字段而中止，864项通过不计全量。

标准macos-arm64 wheel构建成功，build/spec143bl-s1-final-wheels；日志build/spec143bl-s1-build.log。四份生产变更与wheel对应源码逐字节一致。wheel包含当前工作区已有改动，不能当隔离S1发布产物。未调用真实模型，未验证Windows/Linux原机或生产效果，未提交/发布。

2026-11-16审查修复：普通Anthropic tool_use和OpenAI Responses function_call历史不再错误禁用校准；系统消息参与形态与媒体分类。新增适配器输出到输入投影的两条回归、两条系统内容边界回归及三条未知/服务端块拒绝回归，先运行得到4 failed/3 passed，再修复生产代码后定向159 passed。本轮后端全量3365＋3351＝6716项通过，0失败/错误/跳过（包含当前并行工作区变更）；标准macos-arm64 wheel构建成功，包内校准模块与源码逐字节一致。证据build/spec143bl-s1-fix-backend-{0,1}.xml及build/spec143bl-s1-shape-fix-build.log；compileall与diff检查通过。未真实调用供应商或提交发布。
