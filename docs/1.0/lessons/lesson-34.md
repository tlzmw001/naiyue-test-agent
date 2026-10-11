# Lesson 34：RuntimeErrorPolicy委托普通模型异常

> 2026-10-10。本课读agent_runtime/errors.py第46–54行策略构造、第74–77行模型委托、第81–83行诊断函数。第57–73行控制/保护分支只定位：本例普通TimeoutError不命中，具体条件与实现下一课逐行读。第78–79行运行错误分支未读。

## 所处链路与数据

SessionService开始run时RuntimeErrorPolicy(runtime规则, catalog.failure_describer())；本对象持有classifiers运行层规则及model_describer模型快照入口，二者不是合并分类列表。实际模型失败入口调用policy.describe(error,source='model',model_id='A')，普通超时交给模型快照，模型层分类器只调用一次。

## 核心行

- dataclass(frozen=True)构造两个字段，classifiers默认空tuple、model_describer默认None；__post_init__不执行分类。
- object.__setattr__(self,'classifiers',normalize_classifiers(...))在冻结对象构造后处理里保存规范化元组；普通self.classifiers=...会被frozen拒绝，此处显式初始化转换，不是运行途中自动解冻。
- model_describer非None则normalize_classifiers((self.model_describer,))检查同步可调用/通用支持签名；丢弃返回值，保留原函数与其(model_id,error)接口，不把它替换成(error,source)分类器。尾逗号构造单元素tuple。
- describe参数source默认runtime、model_id默认None；调用模型描述时边界显式提供source=model及实际ID。普通TimeoutError不命中前置控制/保护分支，进入74行。
- 75条件表达式：有注入入口则生成单参lambda(error)→self.model_describer(model_id,error)；无入口则直接用describe_model_failure(error)。展开def adapter(error): return self.model_describer(model_id,error)。lambda创建时不分类，等resolve_failure调用才执行，model_id来自本次describe参数。
- 76–77运行层resolve_failure将这个适配入口作为唯一分类器classifiers=(describe,)，内置lambda _error:None不重复SDK分类，report_diagnostic=self._diagnostic处理委托入口自身故障或非法返回。
- 模型内层resolve_failure负责模型规则/内置分类；运行层外层负责委托边界验证/诊断回退。两次调用resolve_failure不等于自定义模型函数调用两次，不触发模型请求/重试。
- staticmethod _diagnostic不接self，固定日志及safe_error_type记录描述过程故障，不采用正文；它不是运行终局发布入口。
- 普通模型路径不执行self.classifiers；这些是运行层扩展规则。模型自定义规则存于目录快照。后续保护错误可优先覆盖source，尚未逐行讲解，不能把所有source=model均简单概括为模型分支。

## 验证与边界

无网络ModelCatalog/FakeModel/RuntimeErrorPolicy探针：构造后calls=[]；普通超时source=model仅调用model_rule，未调用runtime_rule；没注入入口用内置timeout；注入broken(model_id,error)抛ValueError时日志runtime_failure_diagnostics_unavailable/error_type=ValueError，返回unknown描述error_type仍TimeoutError。没有实际模型请求。

```bash
.venv/bin/python -m pytest -q tests/test_layered_failure_contract.py::test_custom_model_error_reaches_model_and_run_events_without_core_edits tests/test_failure_extension_boundaries.py::test_async_custom_model_is_described_once_and_keeps_original
```

实际`2 passed in 0.52s`：同步/异步装配下自定义模型分类到事件/原异常，异步断言只调用一次。仅改学习文档，无产品/测试/.env/依赖修改、不提交push、不作全量构建/实网声明。

## 理解与下一步

- 已讲解模型委托与构造，理解待反馈；控制/保护/普通运行分支未逐行读，独立实现/追问未评估。
- 检查：普通TimeoutError、source=model，同时配置模型与运行层分类器，会执行self.classifiers里的运行层分类器吗？待答。
- 唯一下一步同一describe方法的控制/保护错误优先级，再补普通runtime/tool分类分支；沿Lesson29图继续。

## 暂停新内容：分类器与描述的数据流复盘

用户表示调用链长、再次迷糊，尚未搞清分类器/描述关系。暂停保护分支推进，前述局部分支正确回答不能证明完整链已理解。教学问题：此前按函数逐层展开，包装/选择入口与真正判断异常的规则混在一起，需先分清数据生产者与产物。

核心三项：error是原异常对象；classifier是接收异常并给分类结果的可调用函数；description是FailureDescription数据对象。自定义分类器直接返回完整描述或None，内置classify_model_failure返回(category,reason)元组，由resolve_failure补source/error_type并构造描述。描述不是新的原异常，不改变error的对象身份。

普通模型超时的数据流：TimeoutError→运行层选择model入口/A→A自定义规则返回None→内置规则返回(transient,timeout)→模型层resolve_failure构造FailureDescription(model,transient,timeout,TimeoutError)→运行层返回同一描述→后续报告/事件投影。选规则、适配函数、保护调用的函数不等于新增异常判断规则；外层resolve也将模型入口当可调用分类入口使用，模型业务分类仍只有一处执行。

区分准备与执行：normalize_classifiers输入函数序列/输出适配函数序列；failure_describer读取配置/返回按model_id选择规则的快照函数；实际异常出现后才执行具体规则。FailureDescription是最后的数据格式；resolve_failure负责执行候选、选择结果/构造与故障回退。

无网络实际探针用ModelCatalog+RuntimeErrorPolicy，给A配置返回None的示意规则，临时包装内置分类函数记录返回值并finally还原进程内引用；真实顺序：model selector→A / custom classifier→None / builtin classifier→('transient','timeout') / model description→四字段 / runtime returns same description object。断言准备阶段无规则执行、各规则收到同一原异常、Runtime返回模型层同一描述。没有调用模型/应用终局，不当作完整SessionService验证。

理解检查改为：分类器返回FailureDescription后，原TimeoutError对象会变成这个描述对象吗？用户答“两个对象分别存在”，正确区分原异常与描述产物；记录此概念理解，不升级为完整调用链独立追踪。下一步仍先确认数据流，用户准备好后再恢复保护分支；不自动堆新代码。
