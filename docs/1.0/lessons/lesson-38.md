# Lesson 38：失败描述随原异常回到应用

> 2026-10-11，Spec081后的衔接课堂。用户表示此前链路复杂、整体未理解；恢复学习，一次只追普通模型TimeoutError。用户答“没有”，正确识别标注时应用尚未完成失败收尾；其余理解检查待回答，未评估独立实现或面试追问。

## 本课范围

先区分两个实际对象：原TimeoutError和FailureDescription。描述字段示例为source=model、category=transient、reason=timeout、error_type=TimeoutError。固定属性naiyue_failure的值是(run_id, description)，没有创建新异常包装。

主链：模型/handler抛原异常→模型middleware捕获→Runtime委托模型规则产生描述→在原异常上标注当前run与描述→middleware裸raise→SessionService捕获→RunObservation读取本run描述并生成带stage的失败数据→必要清理/保存→终局通知与原异常传播。

本课只说明描述回传；下行RunServices、分类器选择算法、各控制/工具/恢复分支以及清理持久化细节留对应后续主题。旧Lesson29的reporter/id表属于历史实现，不再作为当前回传机制。

## 代码位置

- agent_runtime/call_observation.py：wrap_model_call/awrap_model_call捕获、_failed描述、裸raise。
- agent_runtime/errors.py：describe_failure_in_context委托策略并标注。
- contracts/failures.py：FailureDescription、attach_failure_description/read_failure_description。
- application/session_service.py：捕获原异常后调用observation.failed，finally收尾。
- application/run_events.py：_describe_failure优先读取标注；failed生成RunFailureData。

## 本地验证

不读取模型配置、不发送请求。使用实际RuntimeErrorPolicy、describe_failure_in_context、RunObservation和标注函数，人工产生TimeoutError并通过嵌套try/except裸raise。此探针不是完整Agent/SDK执行。

实际输出：

```text
description: {'source': 'model', 'category': 'transient', 'reason': 'timeout', 'error_type': 'TimeoutError'}
annotation run: lesson-run
before application: completed None
same exception: True
same description: True
after application: failed {'stage': 'execute', 'category': 'transient', 'reason': 'timeout', 'error_type': 'TimeoutError'}
```

before application中的completed是RunObservation的初始值，未发布完成终局，不代表运行已经完成。探针没有调用finish或真实持久保存。

## 理解检查与下一步

检查：下层完成error.naiyue_failure标注、但应用尚未捕获时，是否已代表整个run失败并完成收尾？用户答“没有”，正确，仅记录该时点理解。

## describe_failure_in_context六行

当前源码agent_runtime/errors.py的describe_failure_in_context（Spec082清理后从第84行起，旧97–103为课堂开始时快照）。示意context含run_id和已装配的_failure_policy；source=model，model_id为当前模型标识。函数主链未改变，分项绑定/legacy failure_data入口已删除。

Spec083更新后按函数名定位（当前从第90行起）；分类器统一(error,source)，保护分类不再走通用resolve。此前Lesson32讲过的单参包装是历史实现；本课return描述/raise异常的主线保持，不把这次实现当用户理解证明。

1. context.get('_failure_policy')取当前运行的策略对象。
2. isinstance检查是否为项目策略；独立调用或缺失时构造默认策略。
3. policy.describe接收原异常和来源/模型标识，返回描述。分类内部算法留下一段。
4. attach_failure_description把当前run与描述放到原异常上；普通标注失败会记录固定诊断，不替换原异常，该helper仍返回本次描述。
5. return description返回给直接调用者_ModelSelectionMiddleware._failed；后者用描述记录本次调用的失败。该return没有退出app.run，也没有把描述当运行结果返回给用户。
6. wrap_model_call/awrap_model_call随后裸raise传播原异常；App通过原异常属性读取描述。

检查：return description直接返回给谁？它是否会成为app.run的正常返回值？待答。

下一步根据反馈确认本函数的局部return与外层raise，再逐段解释策略的普通模型委托或标注/读取；随后补普通观察隔离并回第二章stop_reason.py。局部正确答案不能替代当前整体理解。

## 后续安排更新

用户随后明确希望重新建立体系。旧段落保留历史学习位置，当前改按learning-roadmap的6课计划从Lesson39重建普通失败主线；尚未开始新课，不把本课部分讲解或一次正确回答升级为整链掌握。课堂使用Spec083的固定分类器协议，完成本范围后再回stop_reason。
