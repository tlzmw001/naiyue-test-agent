# Lesson 31：resolve_failure选择描述与诊断失败回退

> 2026-10-10。逐行读contracts/failures.py第76–101行；safe_error_type第26–28行作为输入辅助一并说明。normalize_classifiers内部签名适配算法留下一课，不把职责定位记作该函数全部已读。

## 输入与输出

承接整体图：error进入分类过程，resolve_failure返回FailureDescription或None，调用它的middleware仍负责报告/裸raise。示例error是TimeoutError、source=model；classifiers为可信同步自定义函数序列；builtin为所属层内置分类函数，返回(category,reason)或None；report_diagnostic仅报告分类/描述过程的故障；fallback默认True。星号后的配置必须关键字传递。

自定义分类器可收error或(error,source)，返回完整描述或None。normalize_classifiers把调用接口统一成(error,source)，本课只讲输入输出，不展开适配实现。

## 逐行与分支

- 79 safe_error_type保存原异常类型名，检查标识符格式/长度，不合格用Exception；不采用str(error)正文。
- 80 try覆盖自定义函数适配、执行、返回验证、内置执行及描述构造；普通Exception转入诊断故障分支，控制类BaseException不统一吞掉。
- 81按配置顺序遍历标准化分类器；82传同一error及当前source。
- 83–84只有None表示未匹配，继续下一函数。
- 85–86若返回协程对象，先close，避免未await对象残留；不会执行异步分类，随后类型校验仍失败。
- 87–89要求FailureDescription实例，source与本次分类错误域一致，error_type与原异常受限类型名一致。类自身已校验字段格式，这里校验描述与输入的对应关系。错误不重新归属其他层。
- 90第一个合法描述直接return，后续自定义和内置均不执行；合法unknown/unclassified也是命中，不等于None。
- 91自定义均None或为空时才builtin(error)；内置接口与自定义不同，返回两元素元组或None。
- 92–93仅当builtin返回None且fallback=False时返回None，允许App.try_describe交其他域尝试；不是失败终局/重试开关。
- 94条件表达式用内置元组，否则默认unknown/unclassified；解包赋category、reason。
- 95构造FailureDescription，source及error_type取本次输入。
- 96捕获普通诊断Exception，如分类器bug、非法描述、内置分类/描述构造故障；与参数error两个不同对象，原error不被覆盖。
- 97–98调用report_diagnostic(diagnostic_error)，实际层入口提供记录固定码日志的回调。
- 99–100报告自身普通Exception则记录failure_diagnostic_report_failed及受限类型；无日志正文。不是保证任何进程控制异常或日志系统故障都被吞掉。
- 101返回source+unknown/unclassified+原error_type，不继续尝试其他分类器或builtin；不在该函数raise原异常。原异常由外层模型middleware捕获处裸raise继续传播。

## 三个核心例子

1. 自定义返回None→内置识别TimeoutError→transient/timeout。
2. 自定义返回合法unknown描述→直接采用→不调用内置，即使内置能识别超时。顺序是一种显式覆盖约定。
3. 自定义构造reason='request timed out'→post_init抛ValueError→report_diagnostic收到ValueError→回退描述error_type仍TimeoutError。分类器出bug不代表原业务异常变成ValueError。

正常没匹配且fallback=True得到unknown；fallback=False得到None。诊断故障即使fallback=False仍返回unknown描述，不伪装成普通未匹配。

## 验证

无网络探针实际调用resolve_failure验证以上三分支及fallback True/False，共五条路径。跟踪列表实际分别为[custom_none,builtin]、[custom_unknown]、[custom_invalid]，诊断记录第三项ValueError；描述类型名TimeoutError，无修改原error引用。

```bash
.venv/bin/python -m pytest -q tests/test_failure_boundary_regression.py::test_model_diagnostic_failure_preserves_original
```

实际`4 passed in 0.80s`：同步/异步模型分类故障及unknown诊断故障保留原异常。使用合成模型，无真实供应商请求。只改课堂/地图/交接，不改产品、测试、依赖或.env；未跑全量构建。

## 理解与下一步

- 本函数已讲解，理解待反馈；独立实现/追问未评估。
- 检查：自定义分类器返回合法unknown/unclassified描述，内置本可识别为timeout；会继续调用内置吗？用户答“不会”，正确识别第一个合法描述即返回、unknown并非未匹配。记录该分支理解，不升级为整个函数独立实现/追问能力。
- 唯一下一步读ErrorClassifier与normalize_classifiers的单参/双参签名适配，再回模型目录的配置快照与RuntimeErrorPolicy，继续沿Lesson29地图。
