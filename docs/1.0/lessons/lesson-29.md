# Lesson 29：新版异常的两条路径与完整地图

> 2026-10-10。本课先建立普通模型超时的整体调用地图，展开内部描述报告与原异常传播的关系。分类器算法、快照闭包、ContextVar、控制异常和工具分支留后续逐段读；不记全部新模块已通读。

## 具体数据与范围

示例model_id=A、session_id=s001、run_id=r001，模型调用边界抛合成TimeoutError。假设普通执行超时，没有活动工具结果不确定、取消或框架审批中断；SDK可能先内部重试，本课从最终抛出的异常开始，不增加外部重试。

- 原异常error是同一个TimeoutError对象，沿调用栈传播。
- FailureDescription(source=model, category=transient, reason=timeout, error_type=TimeoutError)是内部受限失败事实，不是异常或AIMessage。
- RunFailureData(stage=execute, category=transient, reason=timeout, error_type=TimeoutError)是事件携带的数据。source表示错误域，stage表示应用运行阶段，不能互相代替；当前事件未新增source。
- RunFinishedData(status=failed, stop_mode=None, manual_review_required=False, failure=上述数据)是运行终局。模型调用失败事件和运行终局处于不同层级、不同时间。

## 实际链路地图

1. SessionService._run_steps先创建RunObservation；RuntimeErrorPolicy持有catalog.failure_describer()本次运行快照。observation.bind绑定策略/报告函数，make_invocation_context将其写入框架runtime.context。
2. 模型middleware的handler最终抛异常，wrap/awrap_model_call捕获；_failed调用describe_failure_in_context(error, context, source=model, model_id=A)。
3. RuntimeErrorPolicy.describe对普通模型异常调用模型目录快照describe(A,error)，进入describe_model_failure/resolve_failure/内置classify_model_failure。模型层不创建事件、不改变会话状态。
4. describe_failure_in_context将error和description交给注入的reporter；本例是RunObservation.record_runtime_failure，保存id(error)→(error,description)。报告只存事实，不发run_finished、不改failure。
5. _failed用同一描述记录日志及model_call_finished(failed)，返回后wrapper裸raise继续传播原异常。
6. 同步/异步IO驱动通过steps.throw(error)把执行异常交回_run_steps的yield处。SessionService catch保留primary_error；observation.failed用_describe_failure检查id及is，复用已报告描述，再补当前stage并写status/failure。描述通道与异常通道在这里汇合。
7. SessionService仍裸raise；finally先执行清理、持久化确认和运行占用释放，末尾observation.finish发run_finished。收尾不遮盖既有主异常；事件发布正常时外层继续收到同一个原异常。发布自身失效则不能保证消费者获得终局。

Runtime通过注入的回调报告事实，没有导入应用层具体RunObservation；模型层只输出共同contracts描述，没有调用Runtime。反向数据传递不等于反向模块依赖。

本课具体展开describe_failure_in_context的策略选择、描述生成、callback报告/诊断日志和返回；record_runtime_failure的身份键/值；_describe_failure的id及is匹配与复用。其后App/Runtime未匹配回退、自定义分类器与保护分支尚待逐行学习。

## 验证与证据边界

本地无网络边界探针实际调用ModelCatalog快照→RuntimeErrorPolicy→describe_failure_in_context→RunObservation；断言同一异常/同一描述、报告时failure=None且无终局、failed后写execute失败数据、finish后终局包含该失败。

首次探针遗漏observation.start()，publisher正确拒绝run_finished并抛Invalid run event lifecycle；补齐探针生命周期后通过。未修改产品代码，不将实验装配错误记为产品缺陷。该探针手动抛/捕获异常并手动finish，没有调用真实图或证明真实SessionService收尾。

完整装配的既有异步回归：

```bash
.venv/bin/python -m pytest -q tests/test_failure_extension_boundaries.py::test_async_custom_model_is_described_once_and_keeps_original
```

实际输出：`1 passed in 0.52s`。测试覆盖自定义模型失败仅分类一次、模型与运行事件使用一致失败字段、上层收到同一原异常；使用假模型，不是供应商实网证明。本课未跑全量构建、不改.env/依赖/产品代码。

## 理解与下一步

- 已讲解本链路与两条通道，用户理解待反馈；独立实现与面试追问未评估。
- 检查：运行层已经报告description，但原异常还没到SessionService的catch，此时observation.failure是否已有值、run_finished是否已发？用户答“没有”，正确判断两者此时均未产生；记录该时点理解证据，不升级为整条链路独立追踪或独立实现。
- 下一步从contracts.FailureDescription/resolve_failure细读分类器匹配、None与unknown、诊断失败回退，再沿本图学习模型目录快照和Runtime策略。不重新开发异常。
