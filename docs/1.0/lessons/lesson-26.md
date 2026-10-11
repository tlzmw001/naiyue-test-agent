# Lesson 26：模型失败的异常对象与分类结果

> 第2章，2026-10-10。errors.py全部核心已讲：响应异常、输入溢出识别、分类和unknown诊断；用户独立实现/追问未评估。课堂开始曾误称ModelFailure，核对立即纠正：当前没有该类，分类结果是(category, reason)二元组。

## 一条具体链路

示意模型消息invalid_tool_calls非空→call_observation._accept抛ModelResponseRejected('invalid_tool_calls')→wrap_model_call捕获→_failed调用classify_model_failure→得到('model_response_rejected','invalid_tool_calls')并记录→裸raise继续抛原异常。不是模型答案，不执行该响应的工具；工具自身可恢复错误回ToolMessage是另一条链。

## 核心代码与数据形状

- RejectionReason=Literal[...]是四种字符串的类型声明，非自动运行时校验。当前构造器没有membership检查，不把类型提示误说成Pydantic验证。
- ModelResponseRejected继承RuntimeError，代表响应经过项目检查被拒绝；并非仅HTTP失败，也不表示模型供应商直接返回这个Python异常。
- __init__(self,reason:RejectionReason)接收调用方给的原因；self.reason=reason保存在实例，供分类/上层读取。
- super().__init__(f'Model response rejected: {reason}')调用父类初始化异常说明。合成例子reason='invalid_tool_calls'，str(error)='Model response rejected: invalid_tool_calls'、args=('Model response rejected: invalid_tool_calls',)。不加入供应商原始正文。
- classify_model_failure(error:Exception)->tuple[str,str]接异常对象，返回两个字符串；error仍是异常对象，分类结果不是替代异常。
- 首分支isinstance(error,ModelResponseRejected)接受该类及子类，return 'model_response_rejected',error.reason返回二元组。category粗分组，reason具体原因；调用方category,reason=...解包使用。

四种原因的实际生产位置call_observation.py113–121：missing_ai_message表示没有AIMessage；stop_reason_error表示项目归一化终止原因为error；invalid_tool_calls表示框架解析出的无效工具调用非空；missing_tool_calls表示归一化为tool_use但有效tool_calls为空。判断按上述顺序，第一处raise结束后续判断，混合错误不会收集成列表。stop reason归一化细节后续专门读。

## 验证证据

纯Python合成异常探针验证reason/str/args/isinstance及分类二元组，退出0，无模型请求。

```bash
.venv/bin/python -m pytest -q tests/test_agent_runtime_response_gate.py::test_rejected_model_message_fails_run_without_executing_any_tool tests/test_agent_runtime_response_gate.py::test_model_hook_rejects_response_without_ai_message
```

实际8 passed in 0.60s：六类响应场景及两类缺少AI消息；断言拒绝原因、无工具执行、run_started→run_failed、关联ID及日志不含模拟私密正文。确定性假模型证据，不是供应商实网/全量构建/生产验证。本轮只改学习文档。

## SDK异常例子：鉴权与超时

用户正确回答“不会触发重试”，记录对分类与重试职责的局部理解；未答category/reason，不补记已掌握。

- 本机OpenAI._make_status_error源码把401映射成AuthenticationError；项目不在本函数收HTTP响应，而是接SDK已经创建/抛出的异常对象。
- 50–51：isinstance(error,(AuthenticationError,anthropic.AuthenticationError))匹配OpenAI或Anthropic对应类及子类；两个同名类是不同Python类。元组是备选类型集合，非异常实例列表。匹配后返回('access_denied','authentication_failed')，不会在此判断具体key是否失效或网络重试。
- 74–76：超时类型四项分别是OpenAI、Anthropic、httpx.TimeoutException以及内置TimeoutError，任一匹配返回('transient','timeout')。ReadTimeout是HTTPX超时子类，能命中基类判断。transient仅是分类标签，不承诺重试成功或自动触发重试。
- 77–78：SDK连接异常及内置ConnectionError返回('transient','connection_error')。本机两个SDK的APITimeoutError都是APIConnectionError子类，因此先匹配超时再连接保留具体原因；反过来会误归入粗连接分支。
- 示意链：正常已配置SDK重试路径先尝试/可能重试，最终抛异常→框架传到观察中间件→分类→日志/事件→原异常继续传播。不是按transient标签驱动SDK重试；当前不展开SDK重试条件全部规则。输入溢出早于这些分支，下一段专门读，不凭跳读标全函数完成。

本轮纯本地六异常探针：两SDK鉴权结果access_denied/authentication_failed，两SDK超时、httpx.ReadTimeout与内置TimeoutError结果transient/timeout；两SDK超时继承连接异常均True。构造合成请求/响应不发送网络，无真实.env。

```bash
.venv/bin/python -m pytest -q tests/test_model_access.py::test_non_openai_sdk_failures_have_safe_coarse_classification tests/test_model_access.py::test_anthropic_sdk_401_is_classified_through_mock_transport
```

实际19 passed in 0.55s：18分类参数化用例及Anthropic SDK模拟401传输；测试范围含其他分类，不代表其代码全部已讲。无真实供应商请求/全量构建声明。

## 掌握与下一步

- 异常类/分类入口及鉴权、超时、连接分支已讲解；用户正确回答分类不触发重试、连接判断提前会将超时归连接类。独立实现/面试追问未评估。
- 用户进一步归纳SDK内部重试对项目不可见，补充两个边界：max_retries是上限，不是所有错误必须用满；不可重试的错误可首请求就抛。重试后成功同样会返回正常结果，目前项目事件不记录每个SDK请求尝试。
- 下一步unknown安全诊断82–89；响应异常/分类及溢出识别已讲，独立实现/追问未评估。

## 补充：SDK重试对项目的可见性

当前观察层_prepare发一次model_call_started，然后调用handler，SDK请求/退避/重试在handler内部，返回后_accept或_failed发model_call_finished。当前单次包装调用的开始/结束事件代表一次逻辑模型调用，不是每次HTTP尝试。中途失败后恢复成功、初次直接成功在此事件粒度上均完成；不能从配置值推出实际重试次数，最终异常也不证明用满上限。

本机OpenAI SyncAPIClient.request源码读取max_retries，for range(max_retries+1)，满足剩余次数/重试规则等条件才continue，否则抛或返回。_should_retry默认不重试401，且有服务端头部等条件；不概括为所有失败都先重试两次。可通过SDK/传输层接入取得更细观测，当前项目未接入，不新增功能。

本轮现有OpenAI/Anthropic SDK模拟503重试边界测试各默认2次重试/显式0两例：4 passed in 3.31s。仅验证模拟503的配置接线与请求次数，非真实服务或全量构建证据。

## 52–73：其他SDK类别与Ollama状态码

用户要求后续考虑外部重试，已扩展future-work现有M3，记录方案评估/次数预算/SDK叠加/安全attempt观测及验收，不在课堂实现或修改重试策略。

- 52–53：Anthropic.PermissionDeniedError→access_denied/permission_denied，权限不足不等于鉴权失败；不凭异常推断具体业务授权配置。
- 54–55：Anthropic.NotFoundError→request_rejected/not_found；可能涉及模型或目标资源不存在，不把404直接推断成唯一根因。
- 56–57：Anthropic.RateLimitError→limit_exceeded/limit_unspecified；类型只证明匹配该类别，不细分速率/并发/额度等原因。
- 58–59：先匹配OpenAI或Anthropic APIStatusError，再访问status_code并比较>=500，返回transient/server_error。and短路避免对其他无status_code的异常读取该属性；isinstance也接受具体状态异常子类。
- 60–61：ResponseError来自ollama，匹配后取整数status；不是OpenAI/Anthropic错误基类。Ollama各状态常用一个ResponseError表达，项目再按码区分。
- 62–73：400→request_rejected/bad_request；401→access_denied/authentication_failed；403→access_denied/permission_denied；404→request_rejected/not_found；429→limit_exceeded/limit_unspecified；>=500→transient/server_error。命中即return，不必写elif；不存在已知映射时退出该嵌套块继续下面判断，并最终79返回unknown/unclassified。
- 当前匹配并不对称：OpenAI普通403/404/429未写对应分支，不能把Anthropic专属分支讲成全部供应商已支持；本机OpenAI RateLimitError(429)确实落unknown/unclassified。记录当前限制，不顺手修复。unknown不表示忽略错误，仍分类记录/原异常传播；安全诊断下一段读。

本轮合成异常探针验证三Anthropic专属分类、两SDK503、OpenAI429当前unknown、Ollama七状态含418unknown；不发送网络。现有非OpenAI18分类例实际18 passed in 0.60s。仅局部确定性验证，无产品代码修改、实网/全量构建声明。

检查：Ollama.ResponseError状态418没有匹配分支，会被吞掉还是继续返回什么分类？待答。

## 25–42与48–49：输入上下文溢出识别

本轮用户继续，未答上一检查题，不补记已掌握。

示意OpenAI BadRequestError：status_code=400、code='context_length_exceeded'、message为合成文本。识别函数返回安全固定枚举，再分类为request_rejected/context_window_exceeded。None只表示未识别该原因，不是成功或没有错误。

- 25声明Exception→str|None；26说明读原始错误判断但不把正文当返回值。识别函数不发网络、不压缩、不重试。
- 27–28只接受OpenAI/Anthropic APIStatusError及子类，不对任意RuntimeError正文做关键词判断，Ollama不在当前识别范围。
- 29–30必须status_code=400；其他状态即使有同样code/文字也不识别成溢出。这是本项目当前识别契约，不宣称所有供应商只会400。
- 31–32OpenAI类型+code准确匹配，立即返回context_length_exceeded。SDK从body解析code；本轮合成body构造证据验证字段，非供应商实网证据。
- 33读取error.message；34–35先确认是str，或含rate limit/too many requests则None。or短路；re.search匹配返回Match对象/否则None；re.I忽略大小写，正则中的|表示两短语任选。仅约束后续文本识别，不覆盖已经return的结构化code分支。
- 36–39OpenAI文本准确短语input exceeds the context window，返回input_exceeds_context_window；40–41Anthropic文本prompt is too long，返回prompt_too_long。r字符串保留反斜杠，\b为单词边界，不需整句相等，允许前后说明，未加入泛化too long匹配。
- 42未命中返回None。48–49分类只判断识别结果is not None，三个细原因统一为request_rejected/context_window_exceeded；恢复中间件保留细原因用于恢复事件。
- 实际context_recovery.py84/106调用同函数，未识别则原异常继续抛，识别后尝试强制压缩并重发一次；运行层主动恢复，不是识别函数触发SDK重试。完整压缩/状态提交第7章读。输出finish_reason=length是正常模型响应字段，与当前请求输入被HTTP拒绝不同。

八个本地合成案例验证三种命中、普通400、非SDK异常、429、混合限流文本拒绝，以及结构化code先于文本防误判的真实优先级。无网络/真实.env。

```bash
.venv/bin/python -m pytest -q tests/test_context_overflow_recovery.py::test_explicit_overflow_forces_compaction_below_local_threshold tests/test_context_overflow_recovery.py::test_other_errors_are_not_retried tests/test_context_overflow_recovery.py::test_normal_length_response_does_not_trigger_overflow_recovery
```

实际9 passed in 0.62s：三种明确输入溢出触发恢复，五个非溢出错误不恢复，正常length响应不恢复；假模型接线证据，不是全部恢复故障/实网/全量构建验证。仅学习文档变化。

本轮检查：OpenAI400、code缺失、message='Unsupported max_output_tokens value'时识别返回什么，会触发强制压缩吗？待答。

## 问答：这些是不是全部异常

用户问“这些就是目前全部异常吗”。明确三个范围：classify_model_failure当前识别的模型失败分类，不是所有SDK可能异常的穷举，也不是全项目异常清单。函数有model_response_rejected、request_rejected、access_denied、limit_exceeded、transient、unknown六类；取消在观察层单独分类，不由该函数承担。

未匹配异常若从模型包装try内的handler/_accept抛出，普通Exception可进入_failed并分类unknown后原异常继续传播；不能把兜底分类说成所有错误已细分、可恢复或覆盖所有位置。_prepare实际在try外，不经过同一捕获段；异步明确捕获CancelledError/UserStop，Exception本身不包所有BaseException。

当前项目还定义配置、压缩、会话持久化、会话忙碌、审批、工具副作用未知等异常，后续按模块阅读；本轮只核对类型/调用边界，不改变异常处理、不跑网络或宣称新全量测试。用户理解待反馈；下一步仍unknown安全诊断82–89。

## 问答：与Pi异常设计是否类似

本轮重新核对本地Pi提交a69bef789，非实网/运行探针。总体目标相近：明确区分模型失败/取消/工具错误，生成可消费生命周期事件；传递协议不同，不能说实现一致。

- Pi packages/ai/src/types.ts331–333明确StreamFunction契约：调用后请求/模型/运行失败编码到返回流，error/aborted终局AssistantMessage包含errorMessage。
- api/openai-responses.ts165–192和api/anthropic-messages.ts770–793捕获异常、设置stopReason、errorMessage，push error事件并end；可保留已收到的部分内容。不是普通成功回答，也不能说Pi从不使用throw。
- agent-loop.ts347–360接error事件、取最终消息并存context/发message_end；193–199检查error/aborted，发turn_end/agent_end后return，不执行该消息工具。agent.ts486–535还有外围catch，将其他executor异常转错误消息和终局事件，再finally清理；活动忙碌检查在try前，不能扩成全Pi任何异常都不会上抛。
- agent.ts554–558的message_end保存state消息；coding-agent/agent-session.ts650–669在message_end保存assistant消息；ai/api/transform-messages.ts195过滤error/aborted助手消息，不因为历史保存就当后续正常LLM答案。
- 本项目call_observation.wrap/awrap在模型边界捕获→classify→失败日志/事件→裸raise；SessionService捕获后失败记录/清理，继续抛原异常。失败事件承担展示/状态观察，异常承担程序控制，不人为构造一个正常AIMessage替代SDK失败。具体checkpoint由框架/项目提交时点决定，不按Pi错误消息协议等同。
- Pi请求适配器设置SDK maxRetries=0并用retryProviderRequest接管请求重试；本项目当前保留SDK重试，外部重试是future-work M3评估项。Pi coding-agent还存在更上层自动重试，不能把所有重试都混称同一层或直接照搬预算。
- Pi工具执行catch转isError工具结果；本项目可恢复工具问题返回错误ToolMessage，未知副作用/调度/存储等致命问题仍可终止。不是任何工具相关异常都保证继续。

本轮仅源码核对/笔记，无产品或测试变化，不称行为全等/新生产验收；用户理解待反馈。下一步保持unknown_failure_diagnostics82–89。

## 问答：整体抽象与异常地图

用户关注架构清晰，感觉没有整体抽象类和汇总。本轮应用naiyue-thinking按当前代码核对，不实施新异常基类/注册器或重构。

当前共同契约已存在：events.py30的冻结dataclass RunFailureData(stage,category,reason,error_type)，被ModelCallFinishedData/ToolCallFinishedData/RunFinishedData等引用；web.serialization用asdict序列化事件，run_delivery取终局failure。它是失败表达的数据抽象，不是Exception基类，不会替代raise。

职责地图：各模块/SDK产生异常→所属边界捕获，模型用classify_model_failure→观察层生成RunFailureData/事件并原异常传播→应用执行运行收尾/终局与特殊处理→Web/CLI消费事件/安全结果。可恢复工具错误走错误ToolMessage，恢复策略单独判断，不由category自动执行。

当前不足如实记录：异常类在所属模块，缺少一份面向学习的全局职责地图；failure_data在call_observation包含跨域特殊分支，application/run_events也有分类组合与回退；应用部分非模型异常复用模型分类器后落unknown，尚非全项目完备错误目录。共同schema存在不代表语义已经完整统一。

建议保留原SDK异常与模块异常、围绕现有RunFailureData整理清晰汇总，先完成学习再评估集中失败映射的小切片；没有共同捕获/恢复规则前不为形式增加ProjectError或ABC。该建议尚非重构实施授权。

参照证据：此前本轮Pi a69bef789流错误助手消息/终局协议已追链；本轮AgentX3fcfa0e的TaskExecutionHandler.java110–120通过AiServices装配服务，AbstractMessageHandler.java337–354消费TokenStream.onError，发送终止消息/失败上报/错误完成hook。仅说明共同边界可以依靠协议与消费入口，不推断全项目异常都继承某一根类，不迁移其直接错误正文展示策略。

本轮源码核对与文档检查，没有功能实现/测试或线上成熟度声明。用户理解待反馈，下一步仍unknown诊断，再进入其他模块时复访本地图。

## 已确认的整理顺序

用户问先学完还是现在改。建议先完成当前错误小模块的unknown诊断，再针对RunFailureData、call_observation.failure_data和应用failed消费点走通一条失败链，随后单独讨论小范围整理；无需等待整个项目学习结束。

先以实际链路确定异常归属/转换/处理/展示及具体重复或职责问题，形成范围与Spec后再实施。不是看到相似代码就默认提取，不提前增加统一异常父类、外部重试或改变终局策略；原异常传播/工具反馈/取消/阶段信息/诊断失败不遮盖主异常作为行为约束。用户随后明确“没问题，就这么做”，确认学习→汇总→确定最小整理的顺序；不把顺序确认扩成尚未设计的重构范围授权。

## 82–89：未知异常诊断

本段已讲解，用户理解待反馈。

示例普通RuntimeError→{'error_type':'RuntimeError'}；未被分类的OpenAI BadRequestError(400)→{'error_type':'BadRequestError','status_code':400}。category/reason仍unknown/unclassified，诊断字段只补信息，不改变终局、重试策略或原异常。

- 82参数是Exception，返回dict[str,str|int]，即字符串键、字符串或整数值；不是返回异常或分类二元组。
- 83目的为限制诊断内容；84 type(error).__name__取异常类名，保存在初始字典。不读取str(error)、message、body、URL、header、工具参数。普通内置/SDK类名例子已验证；当前函数未调用safe_error_type做类名字形校验，不能把此处说成任意动态类名也经过字段过滤。
- 85只从OpenAI/Anthropic状态异常及Ollama ResponseError取status_code；不是看到任意对象有同名属性就记录。
- 86从对应异常取字段；87严格type(status_code)is int并要求100≤值≤599，拒绝字符串、无效默认值或超范围；and短路，100<=x<=599是Python链式比较。
- 88在原details字典新增键，89返回该字典；没有状态码时键缺失，不填None或0，也不改变error对象。
- 消费点call_observation.py145只对category=='unknown'调用，否则details={}；extra.model_failure中**details展开成顶层日志字段，再合并run关联。诊断函数本身不记录日志。当前status_code附在这份日志诊断，不是RunFailureData现有字段，下一课区分。
- 现有捕获后仍raise，因此不把未知异常当成功或静默忽略；当前未知stop_reason是另一路响应观测，与异常unknown不同，后续stop_reason.py读。

五个本地合成探针验证普通/SDK400/默认-1/普通对象同名状态属性/超范围600，仅取预期字段，无网络。现有未知异常应用接线用例：

```bash
.venv/bin/python -m pytest -q tests/test_agent_runtime_unknown_observation.py::test_unknown_exception_has_safe_diagnostic_and_still_fails_run
```

实际4 passed in 0.57s，断言unknown分类、状态可选、原异常同一对象继续抛、run_failed、run关联及日志不含合成私密正文/提示/端点。不是实网/完整生产日志链或全量构建保证；只改学习文档。

当前下一步：RunFailureData及failure_data逐段读，再应用failed/收尾，随后汇总异常职责并确定整理切片。当前顺序先把这条跨层失败链闭合，之后回第2章stop_reason.py；不提前标用户掌握。
