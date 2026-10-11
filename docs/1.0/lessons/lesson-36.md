# Lesson 36：工具结果不确定与会话核对门禁

> 2026-10-10。只追write提交边界的结果不确定路径；完整写工具/串并行/取消与普通runtime/tool分类仍留后续。Lesson35生产者检查用户未答，保留未确认。

## 具体数据流

临时文件准备好→os.replace提交目标→提交边界OSError→_replace_temp日志并raise WriteOutcomeUnknown(...) from error→工具观察层收到WriteOutcomeUnknown→Policy保护分类→描述报告/工具失败事件→原WriteOutcomeUnknown到App→本run failed、该会话manual_review_required=True→收尾释放busy但门禁保留。

本例存在底层OSError、业务异常WriteOutcomeUnknown、FailureDescription三个对象。_replace_temp局部error指OSError；后续tool/App捕获error指WriteOutcomeUnknown。底层通过__cause__保存，业务异常进入运行链路后保持同一对象。不能把之前保留原异常原则误读成所有层都禁止有意异常转换。

## 核心行

- write._replace_temp第123–129行：try os.replace，except OSError记录固定日志及类型，raise WriteOutcomeUnknown固定提示 from error。替换边界当前保守视作未知；不等价任意文件I/O故障均未知。准备阶段失败另有ToolException反馈。
- WriteOutcomeUnknown继承RuntimeError，非ToolException；工具第228–234行记录关联并裸raise，不能变成普通ToolMessage让模型自动重试。
- observe_tool_dispatch第318–324行捕获，outcome=outcome_unknown；describe_failure_in_context(error,source=tool)报告描述；tool_call_finished携带FailureData，随后裸raise同一WriteOutcomeUnknown。异步第351–357行同路径，取消另学。
- Policy保护项返回(tool_outcome_unknown,tool_outcome_unknown)，source=tool，自定义classifiers为空/模型入口不调用，构造FailureDescription(tool,tool_outcome_unknown,tool_outcome_unknown,WriteOutcomeUnknown)。这是已识别的结果不确定类别，非unknown/unclassified无法分类。
- App保留primary_error并failed；本例isinstance触发session.manual_review_required=True和pending_tool_approval=None，记录关联日志后raise/finally。核对门禁不是工具权限审批。
- 收尾busy=False不清除manual_review_required；failed终局携带核对快照True。该会话后续普通run入口被SessionManualReviewRequiredError拦截，其他会话不因此全部停用。未教学/新增核对解除接口。

## 故障注入与验证

无网络临时目录探针：人工包装os.replace，先真实替换目标OLD→NEW，再抛合成OSError；调用真实_replace_temp得到WriteOutcomeUnknown及OSError cause，目标仍NEW。真实Policy输出source=tool/category=reason=tool_outcome_unknown/type=WriteOutcomeUnknown，自定义分类器calls=[]；finally恢复进程内os.replace引用，临时目录自动清理，不碰用户文件。仅故障注入证明，不能宣称真实OS已复现替换后异常。

```bash
.venv/bin/python -m pytest -q tests/test_write_tool.py::test_uncertain_commit_stops_run_and_emits_correlated_event tests/test_application_write.py::test_unknown_write_outcome_blocks_only_affected_session
```

实际`2 passed in 0.66s`：未知提交终止运行/关联日志、核对门禁阻止重试且其他会话可运行。fake模型/临时文件，不是供应商网络或所有写故障证明；未跑全量构建。只学习文档，不改产品/测试/.env/依赖、不提交push。

## 理解与下一步

- 已讲解本例，理解待反馈；独立实现/追问未评估。
- 检查：未知写结果收尾后busy=False、manual_review_required=True，是否可立即在同会话重试写入？待答。
- 唯一下一步用停止/取消一个数据流补保护规则，再覆盖普通runtime/tool分类与App未报告回退；保持一次一个场景。
