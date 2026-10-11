# Lesson 27：异常转换为统一失败数据

> 2026-10-10。承接第2章模型错误，按用户确认的顺序先补跨层失败链；本课读events.py24–45与call_observation.py263–272，应用failed/收尾下一段。

## 数据流与入口

TimeoutError对象→failure_data→classify_model_failure返回transient/timeout→safe_error_type返回TimeoutError→RunFailureData('execute',...)校验→调用方放入结束事件，同时保留并传播原异常。数据对象不继承Exception，不是raise的新对象。

示例字段：stage='execute'、category='transient'、reason='timeout'、error_type='TimeoutError'。没有正文、原异常引用或status_code；session/run ID属于外层事件关联。stage表示记录失败的阶段，不定位底层最初出错函数。

## events.py24–45逐行

- safe_error_type取type(error).__name__，re.fullmatch要求英文大小写字母或下划线起首，后续0–99个字母/数字/下划线；不满足返回固定Exception。与unknown_failure_diagnostics直接取类名不同；这是字形限制，不声称合法字形就无任何敏感语义。
- @dataclass(frozen=True)由标准库生成字段初始化等方法，常规字段重新赋值抛FrozenInstanceError；本类字段都是字符串。不是抽象基类，也不直接靠类型注解校验。
- 四字段声明输入契约。Literal stage是静态提示，真实运行校验在__post_init__；dataclass生成__init__赋字段后调用这个方法。仍不用手写__init__，来源与Pydantic不同。
- 37–38限定prepare/execute/finalize/cleanup，不合法抛ValueError。
- 39–42依次检查category、reason；any中生成器展开成for循环：非str或fullmatch失败任一成立→拒绝。or短路避免对非字符串做regex。格式小写字母起首、后续0–79小写字母/数字/下划线，整体1–80字符；只限制格式，不校验属于某个完整语义枚举，形式合法的新字符串也能过。
- 43–45error_type需str且匹配对应1–100类型名字形，否则ValueError。不记录str(error)/traceback原文。

## failure_data263–272逐行

- try包含分类、取类型、构造并校验失败数据；不是执行一次模型请求。
- 265–268嵌套条件表达式等价：若CancelledError→cancelled/task_cancelled；否则若WriteOutcomeUnknown或BashOutcomeUnknown→tool_outcome_unknown/tool_outcome_unknown；否则调用模型分类器。按顺序只选一个，不尝试执行异常对象。UserStop不在此函数专属分支，观察层另行处理。
- 269固定stage execute，与应用按prepare/finalize/cleanup捕获阶段不同；safe_error_type(error)取原异常的受限类名，返回RunFailureData，原异常未被替换。
- 270捕获的是转换/诊断过程中普通Exception，名为diagnostic_error，与输入error区分。
- 271日志固定事件call_failure_diagnostics_unavailable，只写diagnostic_error受限类名。
- 272回退execute/unknown/unclassified，error_type仍从原error取，不错误地换成诊断故障类型。本函数没有重新抛原error，传播在调用方；回退也不意味着执行成功。普通诊断失败的保护不宣称日志设施等所有后续失败绝不可能再抛。

## 验证

本地合成RuntimeError/TimeoutError/CancelledError/WriteOutcomeUnknown结果符合上述分支；常规修改frozen字段抛FrozenInstanceError；mock分类器抛ValueError或返回带空格的错误码，均记录ValueError诊断并返回原RuntimeError类型的unknown数据；非法动态类型名变Exception。无网络/真实.env/源代码或测试修改。

```bash
.venv/bin/python -m pytest -q tests/test_run_events.py::test_failure_payload_cannot_hide_mutable_or_raw_error_content tests/test_agent_runtime_tool_events.py::test_tool_exception_records_failure_and_propagates_original_error
```

实际5 passed in 0.51s：四字段反例及工具异常事件/原异常传播接线。局部确定性验证，不是全量构建/真实模型或生产测试。

## 掌握与下一步

- 已讲解；用户理解待反馈，独立实现/追问未评估。
- 用户答“原异常”，正确识别回退数据保留原异常类型；未补记独立实现或其他问题通过。模型调用成功问题未答，不强制。
- 下一步application/run_events.py的failed与session_service捕获/finally收尾，读完汇总异常职责并确定整理Spec，随后回模型stop_reason模块。
