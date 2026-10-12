# Lesson 40：模型异常怎样变成安全描述

> 2026-10-11开始。当前只讲普通TimeoutError的内置识别与无自定义规则时的描述字段来源；框架错误、溢出识别、unknown诊断及校验仍待继续。

## 本次位置与具体数据

总图的生成D阶段，暂时没有自定义分类器：

```text
原异常E：TimeoutError('synthetic timeout A')
    ↓ classify_model_failure(E)
二元组：('transient', 'timeout')
    ↓ resolve_failure补source与安全类型名
描述D：FailureDescription('model', 'transient', 'timeout', 'TimeoutError')
```

## 本次核心代码

`model_access/errors.py`第85–90行：超时类型匹配后返回固定分类，连接异常返回另一个固定分类，其余走unknown/unclassified。先前溢出/框架/SDK状态规则对本例内置TimeoutError均不匹配，本次只定位，未逐行学完。

`isinstance(error, (APITimeoutError, anthropic.APITimeoutError, httpx.TimeoutException, TimeoutError))`判断对象是否为其中任意类型或其子类；类型集合不是四种异常同时存在。返回的是category/reason二元组，仍不是完整D。

无自定义规则时，`describe_model_failure`调用resolve并传入source='model'与内置分类函数；`contracts/failures.py`第117–121行接收二元组并构造D。这里只追正常分支，回调选择与诊断回退另读。

| D字段 | 本例的生产者 |
|---|---|
| source=model | describe_model_failure传给resolve的固定来源 |
| category=transient | 内置分类规则 |
| reason=timeout | 内置分类规则 |
| error_type=TimeoutError | safe_error_type从原异常类型名产生 |

## 本地验证

`.venv/bin/python -`合成两个正文不同的TimeoutError，调用当前classify_model_failure与describe_model_failure，各自输出：

```text
classification: ('transient', 'timeout')
description: {'source': 'model', 'category': 'transient', 'reason': 'timeout', 'error_type': 'TimeoutError'}
```

退出码0。仅验证当前类型规则与描述正常路径，不包含真实SDK请求、网络或App收尾。该超时分支不复制异常正文，分类不执行重试；其他识别规则可能读取SDK结构化码/受限正文，不能推广成全模块从不读正文。

## 待答与下一步

用户正确回答unknown/unclassified，说明ValueError不属于已识别超时类型，不因正文timed out命中；确认本分支类型识别理解，不泛化为整个模型分类模块从不读取SDK正文。

## 后续小段：unknown及安全诊断

仍使用模型边界抛出的ValueError例子。未知分类产生合法D：`FailureDescription('model', 'unknown', 'unclassified', 'ValueError')`，保留来源与安全类型；它表达当前规则无法进一步分类，不代表正常返回或允许继续运行。本例未恢复时，原异常仍上抛，App记录failed并收尾。

`unknown_failure_diagnostics`先生成只有error_type的字典，仅对列明的OpenAI/Anthropic APIStatusError与Ollama ResponseError读取status_code；只接收type严格int、100–599范围。ValueError不匹配这些SDK类型，返回`{'error_type': 'ValueError'}`。

`safe_error_type`只从type(error).__name__提取，要求ASCII字母/下划线开头、之后字母/数字/下划线，总长1–100；不符合返回固定Exception。它约束类型标签格式，不读取异常正文，不是任意文本脱敏器。

未知诊断是D之外的日志补充，不修改D；model middleware仅在category='unknown'时调用它，再把有限字段写进model_call_failed日志。原异常仍由裸raise传播，不能据此宣称调用者接收的是脱敏异常。

本机合成ValueError调用真实分类、描述与诊断函数，退出0：

```text
classification: ('unknown', 'unclassified')
description: {'source': 'model', 'category': 'unknown', 'reason': 'unclassified', 'error_type': 'ValueError'}
diagnostics: {'error_type': 'ValueError'}
raw message absent: True
```

只验证这三个局部产物未带该例正文，不是整个日志系统或真实SDK请求审计。未改产品代码。

用户正确回答不能当成功、unknown只是无法进一步分类，调用者仍收到原ValueError，D附在异常上；确认此失败传播理解，不记独立实现。

## 后续小段：描述格式与对应关系校验

两个检查位置：构造FailureDescription时dataclass自动调用__post_init__，检查字段格式；validate_failure_description接收原异常、描述与可选期望来源，检查描述能否用于当前异常，再检查格式。

__post_init__三组规则：source只能model/runtime/tool/application；category/reason必须字符串、小写字母开头、后续小写字母/数字/下划线、长度1–80；error_type必须字符串、ASCII字母/下划线开头、后续字母/数字/下划线、长度1–100。正则约束格式，不验证任意标签的业务语义或保证任意内容无敏感信息。

category/reason的any生成器等价于逐个检查两个值，只要一项不是字符串或fullmatch失败就抛ValueError；短路or保证非字符串不会继续送入正则。此处展开普通for写法解释，不修改源码。

validate先检查对象类型，再检查error_type等于safe_error_type(error)，提供source时检查描述来源一致。or/and短路：非描述对象不读取其字段，source=None不限制来源。失败抛诊断ValueError；成功重新调用__post_init__并返回同一描述，不生成另一个D。再检查格式是消费边界重新验证，不是正常构造第一次漏验，也不能称为完整业务语义校验。

具体例子：E=ValueError，D的error_type=TimeoutError。D可通过自身格式校验并构造成功，但不能通过validate的异常对应检查。校验失败如何安全回退、保留主异常在Lesson41讲解，不宣称校验本身吞掉自己的ValueError。

本机真实类/函数合成探针，退出0：

```text
matching description accepted: True
mismatched description constructed: True
mismatched description rejected: True
free text reason rejected: True
original exception unchanged: True
```

仅验证构造/对应校验局部边界，无网络、全链App或新全量测试。

用户正确回答不通过：error_type虽一致，期望model而描述tool，来源一致性检查拒绝。确认可选source约束理解，未记独立实现。

## 后续小段：框架异常与统一分类码

本机langchain_core/exceptions.py中ModelTimeoutError直接继承ModelError，与ModelAPIError是不同分支，并非Python TimeoutError的子类。检查本机源码及MRO后使用真实安装的ModelTimeoutError构造合成异常；不能据此宣称所有模型适配器都会把SDK超时转换成框架类型。

`classify_model_failure`先做输入溢出识别，随后第49–60行依次遍历8条框架类型规则。每行是(error_type, category, reason)三元组，循环将当前行解包为三个局部变量，isinstance匹配则立即return。本例前5条不匹配，第6条ModelTimeoutError命中，后续框架和SDK分支均不再执行。该循环只分类，不转换或替换原异常。

两个输入的真实局部探针输出（`.venv/bin/python -`，退出0）：

```text
TimeoutError classification: ('transient', 'timeout')
description: {'source': 'model', 'category': 'transient', 'reason': 'timeout', 'error_type': 'TimeoutError'}
ModelTimeoutError classification: ('transient', 'timeout')
description: {'source': 'model', 'category': 'transient', 'reason': 'timeout', 'error_type': 'ModelTimeoutError'}
```

统一category/reason便于消费者比较；保留error_type供安全诊断。原异常对象和类型仍保留。无网络或实际适配器转换验证。初次inspect.signature对框架异常类不可用，改用真实类源码/MRO和构造探针核对，未修改依赖。

用户正确回答不需要转换，直接识别ModelTimeoutError并返回transient/timeout，原对象/类型不变；确认分类与异常转换的区别。

## 后续小段：输入上下文溢出识别

读model_access/errors.py第23–48行的整个识别分支，按执行顺序：框架ContextOverflowError直接返回证据标签；其他必须OpenAI/Anthropic APIStatusError且HTTP400；OpenAI显式code=context_length_exceeded优先命中；其余读取message，非字符串或包含rate limit/too many requests则None；OpenAI匹配input exceeds the context window、Anthropic匹配prompt is too long，忽略大小写且有单词边界；最后None。

明确更正边界：前面的超时规则按类型匹配；溢出规则可以在指定SDK类型和状态门槛内读取结构化code及受限正文，输出固定码，不复制原文。显式code分支在正文排除之前，不能把rate-limit文字排除宣传为覆盖整个函数的优先规则。None表示该识别器未识别溢出，外层继续其他分类，不是无异常或成功。

classify_model_failure只判断证据标签是否非None，统一返回request_rejected/context_window_exceeded；helper的framework_context_overflow/context_length_exceeded等标签不是D.reason。识别/分类不进行压缩或重试，恢复算法留第7章。

本机真实框架类/SDK类构造的合成探针（httpx.Request/Response只作构造，未发请求），`.venv/bin/python -`退出0：

| 合成输入 | 溢出证据 | D分类/原因 |
|---|---|---|
| ContextOverflowError | framework_context_overflow | request_rejected/context_window_exceeded |
| OpenAI BadRequestError，400，显式code | context_length_exceeded | request_rejected/context_window_exceeded |
| OpenAI BadRequestError，400，普通无效字段 | None | unknown/unclassified |
| OpenAI BadRequestError，400，允许的精确短语 | input_exceeds_context_window | request_rejected/context_window_exceeded |
| 同类400，正文含too many requests与允许短语、无显式code | None | unknown/unclassified |

不是实网供应商行为或完整恢复证据。当前OpenAI普通400不会仅凭HTTP400统归bad_request，表中unknown是本机真实输出。

用户正确回答不识别，429在400状态门槛返回None，不再检查code。确认执行顺序理解。

本轮概念讲解结束，进入Lesson41；已确认类型分类、unknown与异常传播、来源校验、分类不转换原异常、输入溢出状态门槛。SDK认证/权限/状态与Ollama映射条目没有全部逐行讲解，本课不是model_access/errors.py整文件覆盖或独立实现完成声明。
