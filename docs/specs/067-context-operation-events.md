# Spec 067：少量上下文操作事件与替换确认

状态：2026-10-08，产品接线/本地验证完成；前三次真实尝试失败后先停止，用户随后授权独立诊断。本轮一次诊断和一次原时限标准探针均通过真实压缩状态验收；此前间歇性摘要超时未复现，根因仍未知。新增四种事件，原阈值、切点、摘要、回退、最多一次溢出恢复和结果/异常保持。

## 当前链路与参照

before_agent 调 _compact_messages：使用当前模型预算估算，超软阈值才尝试切割/摘要；有效摘要产生 RemoveAll + summary + preserved 更新；失败按既有条件回退或抛异常。模型 wrapper 遇明确输入溢出，强制压缩并重试 handler 一次，返回 ExtendedModelResponse + Command；外层响应门禁仍可能拒绝，未必应用替换。此前 context_compaction_completed/overflow_retry_returned 是结果形成日志，不是状态替换证明。

Pi 本机 a69bef789：AgentSession _checkCompaction 使用 compaction_start/end，reason=manual/threshold/overflow，并在重建状态后返回 result；内部 cut/token 步骤不各增公开类型。AgentX 3fcfa0e 的 saveMessageAndUpdateContext 保存摘要并更新 activeMessages；说明结果形成、活跃历史和保存边界分开，不当作 Python checkpoint 契约。本项目仍用 create_agent/middleware/updates+values，确认位置复用 Spec 064 私有状态 adapter。

## 事件表与消费者

| 事件 | 产生时点 | 消费者与判断 |
| --- | --- | --- |
| compaction_started | 现有估算确定触发一次压缩尝试后、切割/摘要前 | CLI 显示压缩处理中；评测关联本次尝试/模型/trigger 和近似原输入。 |
| compaction_finished | 应用替换可读，或已知 skipped/fallback/failed，或退出仍 unconfirmed | CLI 结束提示并区别保留原历史/替换可读/失败；评测摘要用量、缩减及未提交反例。 |
| context_recovery_started | 现有 SDK 白名单识别明确输入拒绝后 | CLI 显示恢复；评测说明供应商拒绝与关联的逻辑模型请求。 |
| context_recovery_finished | 重试响应过外层门禁且替换/响应状态可读，或失败/未确认 | CLI/评测区别恢复成功、失败阶段与不确定；不等于整个 run 成功。 |

只新增四个 type，注册总数从 22 到 26。trigger=threshold/overflow，status 与 stage 放字段；预算、估算失败、切点遍历和摘要开始/返回继续是既有诊断日志，不新增公开事件或更改模型调用/turn 类型。

冻结有限载荷：operation_id、operation kind、model_id、可用 model_call_id、trigger、status/stage/reason、安全 error_type；已有近似输入/替换估算与消息计数、摘要实际用量/耗时可用时填，否则 None；可用 summary_message_id/visibility 与恢复相关 compaction ID、retry_attempts=0/1。没有正文、摘要、prompt/schema、SDK 原始异常、配置/凭证；reason 按固定白名单，默认日志白名单。

## 准确完成边界

compaction finished.status：applied / skipped / fallback / failed / unconfirmed。applied 只表示预期新 summary 和 preserved 前缀已在 graph_values/recovery_view 可读；不表示磁盘提交或业务成功。阈值以下不开始/结束；软阈值无切点但可发送是 skipped；符合旧资格的摘要失败保留原历史是 fallback；失败是旧逻辑终局；摘要生成但替换无法证明是 unconfirmed。

ContextOperationObserver 每 run 只持有限元数据和暂时的预期 summary/保留 ID；不用任意 summary 标志猜成功。拿到框架真实 summary.id，核对替换前缀/保留顺序及移除 ID，确认后丢掉消息对象引用。基线旧摘要、同 run 其他操作、新响应门禁拒绝或 Command 未应用均不误配。完整消息先发布，操作结束后发布，不从事件重放 reducer。

恢复 started 后关联强制压缩；retry handler 返回只是暂存，外层选模响应门禁通过才标记 accepted；替换和实际 retry AI ID 同时可读才 recovered。retry 返回但门禁拒绝为 failed(stage=response_validation)，原异常保持；重试/压缩本身失败为原 compact/retry stage；无状态观察为 unconfirmed。成功模型请求仍只一逻辑 model_call/turn，摘要调用不生成普通 turn。

## 接线与错误边界

新增每 run ContextOperationObserver 和作用域绑定，统一写显式 Runtime.context，核对同一发布器。应用 result/failed/final finish 关闭未确认操作，普通报告故障安全记录且不覆盖原主异常；框架/进程控制不伪造取消终局。

压缩 begin 在原 trigger 判断之后；摘要测量仅复用原计时/usage，结果准备只记录引用，不向模型消息注入新标记。skipped/fallback/failed 通知先登记，外层状态 adapter/模型开始/应用收尾锁外 flush，避免新的 observer 控制异常被旧算法异常包装；观测发布基础故障可禁用本 run 上下文通知，不改变已有 IO/状态结果。未使用正式发布器的旧入口仍只原日志。

```text
应用 run observer → 显式 Runtime.context
before_agent 压缩触发 → started → 现有切割/摘要 → 准备替换
模型开始/状态 adapter/应用收尾 → 发送已登记 skipped/fallback/failed
真实 values 或 recovery_view → 消息事件 → 匹配替换 → finished(applied)
SDK 明确拒绝 → recovery started → 强制压缩 → 现有一次 retry
外层模型响应门禁 + 状态可读 → recovery finished(recovered)
原异常/无状态确认 → failed/unconfirmed → 原运行终局
```

## 修改范围与测试

新 agent_runtime/context_events.py、context_operation_observation.py；events.py 注册/配对；context_compaction.py/context_recovery.py 只加记录点并复用已有计时；call_observation.py 对应逻辑模型关联/门禁；message_observation.py 状态确认；runtime/__init__.py、application/run_events.py 绑定与收尾/日志。context_observation.py 预算诊断不变。新单测/实际图/失败测试及有界探针、阶段文档同步。超过 8 文件，单文件 ≤500；不改公开签名、依赖、环境配置或存储/运行策略。

先红：成功压缩要等状态替换、阈值以下无事件、当前模型切换、软切点跳过、摘要失败合法回退/强制不可回退、摘要无效/预算失败、前次摘要不误配、明确溢出成功/重试失败/门禁拒绝不伪成功、结构化输出、无状态确认、报告故障与原异常、消费者故障、跨会话隔离、仅四 type 和日志无正文。部分故障后仅恢复视图可读时明确 visibility。

验证原日志和结果/异常、工具不重跑、调用计数不变；全量 pytest/compileall/pip check/行数与结构；有界真实模型压缩接线验证沿现有合成样本，不制造真实溢出或改模型配置。示例/假模型、真实请求与生产/业务效果分别报告。

真实验证扩展既有 scripts/probe_compaction_quality.py：从正式 app.subscribe_events 收集本片四 type，压缩组必须 started/finished(applied) 且 summary ID 对应实际状态；对照组无操作。结果增加 compaction_state_confirmed，固定四事实评分和超时保持；后述仅修正探针内存预算，不改模型配置文件或应用算法，不打印原文或新摘要。

真实探针前两次 compressed 失败，第二次安全诊断明确 compacted_request_over_budget。当前输出上限 16384 使原探针 window=17408、发送预算仅 1024，而固定样本保守替换估算 3031。补 16384/2048 合成回归先红，只修探针内存预算：window 下界改为 output_cap + summary_output_cap + 1024，保留估算样本下界、原 reserve/keep/评分；应用算法/配置文件不改。若第三次真实验证再失败，按调试协议停止该方向报告，不无限重试。

## 实际验证与失败记录（2026-10-08）

- 基线 `.venv/bin/python -m pytest -q` → `697 passed in 16.13s`。
- TDD 首批：实际图 5 个场景因没有公开上下文事件失败、阈值以下场景已通过（`5 failed, 1 passed in 0.71s`）；单测因模块不存在收集失败。最小接线后 8 例通过，后补反例不全部称先红。
- 两项身份确认先红修正：retry.id=None 被任意空 ID 消息误配；summary ID 相同但状态消息角色是 AI 被误配。增加有效 AI ID 与实际 HumanMessage 角色检查，同样的断言转绿。原算法/状态不修改。
- 四 type 以 start/end 配对和字段表达 applied/skipped/fallback/failed/unconfirmed、recovered 与 stage；注册总数 26。预算估算/切点/摘要返回等诊断日志保持，只有状态替换确认才报告 applied。摘要实际 usage 与原 monotonic 耗时复用，未新增普通模型/turn 计数。
- 最终全量 `.venv/bin/python -m pytest -q` → `723 passed in 16.48s`，新增 26 例（24 个上下文场景/单测与 2 个探针诊断/大输出预算回归）。覆盖模型切换、阈值/跳过/回退/失败、摘要预算、结构化重试、门禁拒绝、旧摘要/角色/ID/保留顺序、恢复视图、subject 不变、双会话、未知原文脱敏、无状态观察与报告/消费者故障。原结果/异常/调用次数/旧日志测试全部保留，不 skip、不改 conftest。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → 0；没有独立应用打包构建目标，此项为语法编译。`.venv/bin/python -m pip check` → `No broken requirements found.`。
- 本片19个文件的存在/代码行数/尾空白检查 → `touched_file_structure=PASS files=19 python_line_limits=PASS trailing_whitespace=none`。git diff --check退出0；全仓库无提交且文件未跟踪，该命令不能代替显式文件检查。
- 真实命令三次均为 `.venv/bin/python -m scripts.probe_compaction_quality --total-timeout-seconds 120`，每组最多 60 秒，合成固定历史、既有默认 heiyu-gpt-5.5。第一次 compacted 仅报告 ContextCompactionError，具体原因未捕获。第二次在新增安全 context_failure_reason 后明确 compacted_request_over_budget；本机保守预算复核为 window17408/send1024/projected3031，不满足发送上界。补失败测试，只改探针 window 下界，5 个探针测试通过。第三次 compacted 超过 60 秒、last_event=context_summary_model_started，父进程按既有逻辑结束 worker；根因未知，不推断为网关/模型/框架故障。
- 三次 baseline 都正常返回，四事实评分 4/4，compaction_state_confirmed=False；第三次 baseline 耗时 3483 ms、输入7721/输出41 token。这些只证明对照组；compressed 新事件和状态确认没有真实通过证据。不能把 723 例本地全绿或前期真实摘要证据替代本轮验收，不报告节省成本/生产质量；超时请求供应商侧实际 token/费用未知。

## 当前限制与下一步

原异常继续传播、已通过的回退/恢复策略不变；observer 只保留活跃操作的临时引用和有限元数据，结束即释放。自定义 invoke/无状态确认时 unconfirmed，不声称持久化。事件回调仍本地受信任同步，没有 token 流/异步、活动取消、持久交付、生产压力或多租户验证。

前三次真实失败已按规则停止，随后用户“好的继续”授权独立排查。2026-10-08 补充验证见下文，真实压缩状态接线通过；间歇性摘要长等待仍保留独立待办，不称为已修复。唯一下一步：设计 CLI 消费同一事件接口的最小切片，先明确消息显示去重与终局/审批/上下文提示，再实现；随后进入最小持久会话。新增代码待一起学习，不将助手实现或测试通过记为用户掌握。

## 后续独立诊断与真实验收补证（2026-10-08）

- 调用链与实际路由、一次性安全 HTTP 观测见[摘要请求耗时排查](../research/summary-request-latency-2026-10-08.md)。没有修改产品/测试源码、配置、协议或 SDK 默认值；独立诊断父时限 130 秒仅为观察请求尝试，不是产品修复。摘要和正常回答均走 Chat Completions，各一次 HTTP 尝试返回 200，没有重试。压缩组整段 39300 ms，摘要 18831 ms、输入7053/输出254 token，状态确认 applied/graph_values、四事实4/4；历史超时未复现，根因仍未知。
- 再运行原标准命令 `.venv/bin/python -m scripts.probe_compaction_quality --total-timeout-seconds 120` → exit=0，两组 returned。compacted：总42026 ms、摘要17964 ms、摘要输入7053/输出247 token、最终回答输入5385/输出66 token、compaction_state_confirmed=True、parse_ok=True、四事实4/4。baseline：3656 ms、输入7721/输出41 token、无摘要/无状态替换、四事实4/4。仍为每组最多60秒，原断言未放宽；其成功不证明 SDK 尝试明细或所有长期请求可靠性。
- 本轮重新运行 `.venv/bin/python -m pytest -q` → `723 passed in 16.52s`；compileall →0；pip check → `No broken requirements found.`。没有新功能/修复或新 TDD 红灯。压缩组含摘要的总用量12751 token，高于对照7762 token；这个短固定样本不支持成本优化或生产业务质量结论。
- 此补证只关闭“真实压缩状态接线未通过”的验收缺口；明确溢出恢复、其他供应商、持续摘要质量、间歇性超时与总运行预算仍未由此验证。用户没有新增独立实现或面试追问证据。
