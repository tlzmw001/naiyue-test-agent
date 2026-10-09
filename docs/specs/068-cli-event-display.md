# Spec 068：CLI 消费统一运行事件

状态：2026-10-08，用户批准展示契约后实施，确定性测试与有界真实 CLI 运行入口/展示器探针通过。沿用同步线程、原应用接口、工具权限与审批决定；不做 token/TUI/异步/存储改造。

## 当前链路与原因

CLI 主线程 input/控制命令，worker 调 app.run/resume，返回后用当前 Human ID 或 resume_start_index 提取 AI 文本，异常时只显示失败。因此前面已经完成的回答可能在后续失败时丢失；同时接事件和旧结果打印又会重复正文、失败或控制回执。

新链路：runtime/application → 统一事件 → CLI 展示器 → 终端；app 原返回/异常 → 同一展示器的补显/兜底。审批详情仍由结果 approval_requests 提供，回调不查询应用、不执行工具/审批。

参照已核对 Pi a69bef789 interactive-mode 的 message/工具组件与 agent_end 收尾，而非 print-mode 的最后回答输出；AgentX 3fcfa0e AbstractMessageHandler 的 partial/tool/end 回调。这里只迁移显示职责，当前无增量数据不伪造 token 流。沿用已验证 graph_values/recovery_view 区别及应用 finally 后的 run_finished。

## 具体接口和显示契约

- 新 application/cli_display.py，CliEventDisplay(session_id)，同步 __call__(RunEvent)，begin_call()、finish_result(result, input_message_id/resume_start_index)、终局是否已显示、control_receipt_count(kind)、write(text)。每次应用调用前清空本次显示账本；只保存消息 ID/hash、事件序号、有限计数，不复制历史正文。session/run 隔离，输出块由同一 RLock 串行；锁不包 app 操作、input、join。
- 普通 AI 的 message_completed 即显示完整文本，工具前后回答都显示；Human/System/摘要不打印成回答。同 ID/hash 不重复，正文相同不同 ID 两条都显示；message_revised 明确新版本。recovery_view 明确是失败后可读状态。文本块只提取已有文字内容，其他模态以类型提示，不伪造识别结果；终端控制字符可见转义，保留换行/Tab。
- 模型事件仅显示请求处理中；工具请求/dispatch/结果显示有限名称和状态，工具错误消息显示已有错误正文，成功大段结果不刷屏。不能把 dispatch 当执行器已进入；unknown 副作用仍用原人工核对提示和 Bash 部分输出。
- 控制 accepted 与 delivered/applied/discarded/unknown 分开；command 原回执仅在相应 accepted 未显示时补一次。停止 applied 不再独立打印终局，唯一 stopped 来自 run_finished。approval_requested 提示数量；返回后原 approval_requests 显示完整审批目标/命令和操作语法一次。resume 是新 run，新批次重新显示，不增权限。
- compaction/recovery 只显示开始和实际状态，不打印摘要正文。applied/recovered 不等于持久保存或整个运行成功；fallback/skipped 说明原历史继续，failed/unconfirmed 明确状态。上下文移除不删除终端既有记录。
- run_finished 四终局各一次；turn 结束不能显示 run 完成。failed 展示安全 error_type；manual_review_required 保持明确提示。异常 catch 保留原记录、类型及 Bash 部分输出，但已显示失败时不重复。准备拒绝仍走原 CLI 提示，无伪 run。
- 正常结果补显未出现的 AI：普通调用从 input_message_id 之后，恢复从 resume_start_index 开始；两者都不能确定时说明观察不完整，不盲目打印历史。缺 ID 降级仅在结果补显使用该位置的临时身份。缺消息事件仍补正文，缺终局事件显示“运行已返回，但未观察到终局事件”，不伪 completed。无回答且非审批保持原提示。
- CLI 注册订阅在首次运行之前，退出/EOF/异常时先等自己的 worker，finally unsubscribe。无 subscribe_events 的旧测试/诊断 app 和直接调用旧私有 helper 保持结果路径。普通同步输入也使用同一展示逻辑；不新增 JSON/管道模式，不保证编辑光标重绘。

## 影响文件与验证

新增 cli_display.py；修改 application/cli.py 的私有 helper 可选 display 与 main 接线；公开 application/runtime 签名不变。新增独立展示单测及真实 app/CLI 集成测试，已有 tests 不放宽断言、不改 conftest。更新阶段交接/计划/待办/README；关键知识点、配置、依赖不改。每个 Python 文件≤500行。

先红测试：结果返回前看见首条回答；随后失败仍保留、失败只一次；事件/结果去重与修订；相同正文不同ID；control accepted 不双回执；审批暂停/批准/拒绝/再次暂停；软错误继续和fatal unknown；摘要隐藏/恢复视图；事件不可用/无终局/无可靠边界；session隔离、退出退订和输出并发块完整。同步屏障/Event，不靠短sleep。

运行定向 pytest → 全量 pytest → compileall → pip check → 文件结构/显式差异检查。扩展既有 scripts/probe_message_events.py 的 --with-cli 开关，用合成随机纯工具、CLI 私有运行入口+display+app，验证两条助手文本与工具状态/终局；沿用2次逻辑请求/1次工具和95秒父时限，输出只有验收统计，不输出正文/凭证/原错误或改 .env。新增该开关离线接线测试。真实审批/异常/取消与生产效果不由单次探针证明。

## 实施证据与核心数据流

1. main 建会话后构造 display 并 subscribe；主线程和 worker 共用同一展示器。每次 _run_and_print/_resume_and_print 调用前 begin_call 清空本次 ID/hash/序号账本，有限回执计数跨调用保留。
2. app.run → 状态 observer → message_completed → display.__call__：确认 session/run/序号，正文即显示，摘要/Human/System 不作为回答。工具状态只描述 dispatch/处理结果，ToolMessage error 补安全原因，成功正文不刷屏。
3. 用户线程调用 steer/follow/stop 时，先读 accepted 计数，应用事件显示回执后原命令提示不重复；失败或无事件时仍有原命令兜底。展示锁不包应用调用、input 或 join。
4. run_finished 显示一次终局；应用返回后 finish_result 根据当前输入ID/恢复起点检查新 AI，哈希相同不重播，缺消息明确提示观察不完整并补显。缺终局不能推断 completed。approval_requests 仍由原结果显示完整列表一次。
5. 关键异常仍走原 catch 和日志；已有 failed 终局不再重复提示，Bash 部分输出/存档路径保留。main finally 等自己的 worker 后 unsubscribe，包括 EOF/input异常/启动输出异常。

首批实际旧 CLI 两例红灯：后续模型失败前 first reply 不可见、无 subscribe/unsubscribe；展示单测因模块不存在收集失败。最小接线后定向39例绿。机械改 print→emit 时误改两个 helper 调用名产生 NameError，由现有同步 CLI 回归复现后只修调用；没有放宽断言。

后续反例先红再修：正文存入去重账本→改文本哈希；非文本提示被结果重复→按消息ID去重；启动输出异常未退订→扩大现有finally覆盖；真实注册 control_outcome_unknown 未显示→接正式事件名，保留真实发布器回归；缺消息补显未说明观察不完整→补明确提示。新测试 helper 的参数名与 data.kind 碰撞只改 helper 参数名，不改业务断言。其他边界补测不全部算先红。

离线 probe 测试在当前进程执行只为子进程设计的 worker，root logger CRITICAL 导致后两项既有日志断言失败；测试自行恢复原日志等级和 tracing 环境后，probe+两项原断言同跑3例通过。没有修改既有测试/conftest或产品日志策略；这属于新增测试的状态隔离修正。

真实命令 `.venv/bin/python -m scripts.probe_message_events --with-cli --with-control` 退出0：

```text
real_message_events=PASS events=20 messages=5 model_calls=2 tool_calls=1 turns=2 controls=1 cli_display=verified replies=2 pairing=verified terminal=completed
```

使用已有默认 heiyu-gpt-5.5；CLI 私有运行入口+展示器通过真实 app，一次随机纯工具内注入Steering，工具执行前确已显示首条说明，最终文本/工具状态/唯一terminal与消息/turn/control实际ID配对。没有真实终端键盘/TUI或真实审批/停止故障证据，交互命令由已有Event同步测试验证；两次逻辑请求不代表SDK HTTP尝试统计。没有生产压力/业务质量/节省成本声明。

公开 application/runtime API、.env、依赖和关键知识点文档不改；新增实现不代表用户已独立掌握。真实消息观察已采用同步框架状态流，不是逐token或原生异步；标准输出阻塞可能延长同步回调，输出块串行不保证输入光标重绘。下一步讨论最小持久会话的保存/恢复契约，再写独立Spec；本片代码学习仍需一起完成。

最终验证：新展示/接线/探针26例通过；`.venv/bin/python -m pytest -q` → `749 passed in 16.52s`（较723新增26例）；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` →0；`.venv/bin/python -m pip check` → `No broken requirements found.`。项目无独立应用打包目标，此项仅语法编译。显式文件检查覆盖本片12个文件，Python均≤500行、无尾空白；全仓库未提交/未跟踪，git diff --check不能代替显式检查。无提交/发布/部署。
