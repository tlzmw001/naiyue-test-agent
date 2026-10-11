# Lesson 35：无效模型响应产生运行层异常

> 2026-10-10。复盘后按用户“继续”恢复一个具体分支：ModelResponseRejected('invalid_tool_calls')。其他保护错误、Graph控制分支、普通runtime/tool及App未报告回退仍待学习，不一口气读整个protected表达式。

## 数据流与对象

示意框架AIMessage.invalid_tool_calls=[{name:read,args:'{bad',id:call_1,error:'Invalid JSON'}]。这是框架已标记无效的调用，不是工具执行后抛出的错误；本课不展开供应商消息解析。_accept发现非空后raise ModelResponseRejected('invalid_tool_calls')，模型响应门禁先于工具执行。

原异常error是本项目运行层响应验收主动产生，不是SDK抛出：ModelResponseRejected对象reason=invalid_tool_calls。对应description是FailureDescription(source=runtime, category=model_response_rejected, reason=invalid_tool_calls, error_type=ModelResponseRejected)，两个对象并存。

## 本例核心行

- call_observation._accept第121–122行if message.invalid_tool_calls: raise ...；列表非空即拒绝，不发工具请求，不在本轮自动修复。
- contracts.ModelResponseRejected继承RuntimeError；__init__保存reason，再super().__init__设置固定前缀错误文字。本例reason是固定码。类是共同契约，拒绝判断由运行层拥有。
- 模型wrapper的try含handler与_accept，因此自己的验收异常也进入_failed→describe_failure_in_context；虽然该边界传source=model，不能将所有错误都认成SDK故障。
- RuntimeErrorPolicy第66行若isinstance(error,ModelResponseRejected)，protected=('model_response_rejected',error.reason)。条件表达式展开为if/赋元组，本课只展开这一个命中分支。
- 70–73行protected非None先执行；本例不是Write/BashOutcomeUnknown，所以source改runtime。resolve_failure输入classifiers=()不执行自定义分类器，builtin=lambda _error:protected固定返回本次(category,reason)，构造描述；_diagnostic仍保护诊断故障。
- 此return在普通source=model委托之前；本例不调用model_describer，也不调用self.classifiers。运行安全/控制语义优先，不能被扩展误归为普通模型错误。
- 描述报告给App，_failed记录模型调用失败，wrapper裸raise同一原异常。App复用描述补stage=execute，收尾后failed终局；本轮不执行无效工具，非ToolMessage修复路径，不是永久禁止下一次用户请求。

## 验证

真实AIMessage/Policy无网络探针验证本例error.reason、source重归runtime、category/type、运行/模型自定义入口均未调用；正常结果打印原异常与四字段描述，calls=[]。探针手动模拟_accept的一条判断，不宣称完整图验证。

完整fake模型响应门禁及保护规则回归：

```bash
.venv/bin/python -m pytest -q tests/test_agent_runtime_response_gate.py::test_rejected_model_message_fails_run_without_executing_any_tool tests/test_layered_failure_contract.py::test_runtime_protected_errors_cannot_be_overridden
```

实际`7 passed in 0.56s`，响应门禁参数场景无工具执行、记录原因/安全日志，以及已测保护错误绕过扩展。不是所有保护类型/供应商实网证明。本课只写学习文档、不改产品/测试/.env/依赖、不提交push、不作全量构建声明。

## 理解与下一步

- 已讲解本数据流，理解待反馈；独立实现/追问未评估。
- 检查：SDK正常返回AIMessage，但invalid_tool_calls非空，本例是谁产生原异常？模型SDK，还是运行层的响应验收代码？待答。
- 唯一下一步按同一数据流读工具结果不确定的保护分支，再覆盖停止/控制与普通runtime/tool分支；不恢复多层函数堆叠讲法。
