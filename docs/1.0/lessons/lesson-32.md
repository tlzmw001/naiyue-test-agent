# Lesson 32：分类器单参/双参调用适配

> 2026-10-10。读contracts/failures.py第49–73行ErrorClassifier和normalize_classifiers。统一的是函数调用形式，不修改分类内容、不提前运行分类器。

## 具体输入输出

示例原函数one(error)、two(error,source)、optional(error,reason='timeout')。输入列表[one,two,optional]，输出元组[单参包装函数,two原函数,单参包装函数]（这里方括号只示意顺序，实际返回tuple）。调用方统一classify(error,source)，包装函数忽略source并调用捕获的原函数(error)。两必需参数函数原样保留；单参能调用的优先，保护可选参数原默认值。

## 核心行与语法

- 49–50 Callable[[参数类型列表],返回类型]是可调用对象类型提示，|表示两种签名或两种返回值；没有创建新异常/基类，也不会运行函数。
- 53 Iterable表示可遍历输入，返回tuple；54 result=[]累积适配函数，55每次取一个函数或可调用对象。
- 56–58 callable检查可调用性、iscoroutinefunction检查async def；对象__call__也检查，getattr缺失时None。拒绝非函数数字/异步分类器，分类是短小同步判断，异步Agent不意味着分类必须异步。普通同步函数误返回协程仍靠resolve_failure返回验证兜底。
- 59–62 inspect.signature拿参数说明，失败TypeError/ValueError转换为固定ValueError；from None抑制展示原上下文，不表示吞错成功。
- 63–64 signature.bind(None)只检查一个位置参数能否满足签名，不调用函数、不检查异常对象类型。None是占位值，不能读成实际分类输入。
- 65–70单参绑定失败后尝试bind(None,'model')检查两位置参数是否可调用。'model'也是占位值，不固定实际source。不支持则ValueError；支持则原函数item直接append。
- 71–72 try的else在单参绑定成功时执行，不是if的else；lambda用默认参数classify=item在创建包装时捕获当前函数，避免循环后都引用最后item。等价为def adapter(error,source,classify=item): return classify(error)。包装收到两个参数，只将error传给原函数，保留原函数默认值。
- 73 tuple(result)固定函数序列，返回的不是分类结果；后续resolve_failure循环才执行函数。

签名是否能用一个/两个位置参数调用才是标准，不按参数名字判断。required keyword-only source不支持该位置调用接口；三个必需参数不支持。可变参数等只要能按单参调用会按单参路径处理，不宣称只允许精确一/二个形参。

## 为什么单参优先

optional(error,reason='timeout')既能单参调用也能双参调用；直接塞source会把reason覆盖成model。单参优先包装后实际optional(error)，reason仍timeout。需要获得动态source的分类器使用def classify(error,source)，第二参数必需，不设默认。

## 验证

无网络探针使用真实normalize_classifiers/inspect.signature：适配后calls仍空；执行统一两参数调用得到one/TimeoutError、two/TimeoutError/model、optional/timeout；one变包装且two保持原对象；两个单参包装各调用正确原函数；非可调用、三必需参数、async def均拒绝。bind占位输入不运行函数。

```bash
.venv/bin/python -m pytest -q tests/test_failure_extension_boundaries.py::test_legacy_classifier_optional_parameter_keeps_default tests/test_failure_extension_boundaries.py::test_configuration_is_atomic_and_rejects_async_classifiers
```

实际`2 passed in 0.81s`：可选默认参数保留、异步分类器及非法配置原子拒绝。fake模型无网络，只改学习文档，不改产品/测试/配置/依赖，不作全量构建验证。

## 理解与下一步

- 已讲解，用户理解待反馈；独立实现与追问未评估。
- 检查：def classify(error,reason='timeout')经适配后统一调用(error,'model')，原函数reason得到哪个值？用户答“'timeout'”，正确识别单参优先包装保留原函数默认值；记录该分支理解，不升级为签名适配独立实现或闭包机制全部掌握。
- 唯一下一步模型目录configure_error_classifiers/failure_describer的配置与每次运行快照，随后RuntimeErrorPolicy沿Lesson29完整图继续。
