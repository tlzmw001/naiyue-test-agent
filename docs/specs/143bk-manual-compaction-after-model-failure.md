# Spec143BK：模型失败后手动压缩与准备提示收尾

## 当前事实与目标

用户报告本机glm 53flash估算远低于实际导致上下文超限，手动压缩又显示运行失败/正在准备压缩。实际用量已有记录但不反馈校准。校准需要明确模型/请求范围和摘要输入估算的一致性，本片不增加未经确认的校准机制。

现场日志尚未提供，以下为代码与确定性测试确认的问题，不声称就是现场唯一原因。当前langchain-core 1.6.6的count_tokens_approximately默认chars_per_token=4.0、use_usage_metadata_scaling=False；即使启用内置缩放，源码上限为1.25，还依赖provider一致的usage元数据，不能据此承诺修复严重低估。context_usage.finish_snapshot保存同次估算和实际usage，model_usage_persistence保存统计，但都没有回写后续预算。

定向参照：本地Pi a69bef789的estimateContextTokens取最近有效assistant usage与后续增量，错误/中止/零usage不采信；agent-session阈值压缩另排除压缩前旧usage。AgentX 3fcfa0e的AbstractMessageHandler.setMessageTokenCount把input/output计入消息，ConversationAppService转TokenMessage后由SummarizeTokenOverflowStrategy消费；这是不同运行时，不作为本项目直接实现。LangChain官方参考：https://reference.langchain.com/python/langchain-core/messages/utils/count_tokens_approximately 。下一片候选需让展示、准入、压缩检查共用同一计数口径，并处理模型、工具/系统提示变更和压缩后的旧样本失效；普通请求与独立摘要请求不可直接套用同一个usage基准。

两处确定性复现：
1. useConversation.compact设置准备提示；display只处理completed/stopped，failed/unconfirmed/running不会清掉准备提示。
2. 普通请求超限后恢复重试也失败，快照保留next=('model',)、任务model.error；actor已空闲。compact_checkpoint开头拒绝所有未完成快照，因此RuntimeError，连摘要请求都没执行。

## 最小修改与安全边界

- display遇到compact运行视图时仅清除原准备提示；failed/unconfirmed显示对应结果，保留草稿，不假称检查点提交失败时原状态已确认保存。
- compact_checkpoint允许空闲会话仅剩失败model节点：next严格为('model',)，tasks非空，全部name=model且error存在、无interrupts。其他pending模型、工具节点、混合任务、审批仍拒绝。
- 沿原force摘要校验完整工具配对，调用原摘要模型与两步提交；不重跑图，不自动继续用户任务，不执行或重放工具。摘要失败保留原状态，提交失败继续原恢复保护。失败model头在摘要失败、无可压缩内容或停止且未提交时，继续按失败收尾，不能向持久层宣告ready；否则原未完成头会造成session_save_unconfirmed。失败后仍可再次手动压缩。
- 不修改用量字段、自动压缩阈值、模型错误识别、配置/依赖/数据库/锁。实际失败是否命中该路径仍需用户日志确认。

## 正常与失败链

正常：主会话压缩入口→原compact run准入→读取失败model快照→原_acompact_messages摘要→原状态替换并确认→applied，前端清掉准备态。普通模型请求次数不增加。

失败：仍有工具节点/审批/非失败model→原拒绝；摘要请求超限/失败→原压缩失败结果保留状态；提交不确认→原ManualCompactionCommitError和恢复保护。前端结果视图不再显示准备中。

## 文件与验证

frontend/src/useConversation.ts、frontend/src/composer-commands.test.tsx；agent_runtime/manual_compaction.py、tests/test_manual_compaction.py；本Spec与handoff。

先红后绿验证三类前端状态和失败model后手动压缩；拒绝其他未完成节点，不重放工具、不调用普通模型、原检查点不变及提交确认保护。完整前端/构建、上下文及用量专项后运行必要后端回归。浏览器验证生产页面失败和运行中提示。用量校准方案另行讨论；原机日志尚待补充。

## 同模式核查与验证边界

- 初始未完成头拒绝只存在于compact_checkpoint；session_persistence的ready完整性检查必须保留，因此新增路径仅在摘要确实提交后返回applied，未提交按失败收尾。没有放宽普通运行、工具审批或持久化完整性检查。
- 前端准备文案的设置与收尾均在useConversation，未修改普通消息、停止或自动恢复订阅；completed/stopped既有结果保持。
- 回归包含：超限→自动恢复重试仍失败→idle失败model头→手动摘要成功；摘要失败/停止/无更新→原头和历史不变→再次手动成功；六类其他未完成快照继续拒绝，不写状态。新增10例。
- 真实glm 53flash、Windows现场、服务端拒绝码与摘要输入是否也超限尚未得到日志/真实请求验证。修复未涉及分块摘要或实际用量校准；用户明确要求校准稍后讨论。

## 本轮结果

- 红灯：新增前端3例失败（build/compact-notice-red.log）；失败model头的应用级回归失败于completed checkpoint门禁（build/manual-failed-model-red.log）。补充摘要失败用例复现持久层checkpoint_not_safely_finished，因此未提交的新分支继续失败收尾。
- 专项命令：`.venv/bin/python -m pytest tests/test_manual_compaction.py tests/test_context_overflow_recovery.py tests/test_context_compaction.py tests/test_model_usage_paths.py tests/test_context_budget_degradation.py -q`，88 passed，见build/context-recovery-green.log。
- `npm --prefix frontend run test -- --run`：78文件681项通过；`npm --prefix frontend run build`：tsc/Vite通过，仅既有chunk体积提示。日志build/compact-notice-frontend.log、build/compact-notice-build.log。
- 生产preview Chromium：1440/375px各检查failed/unconfirmed/running，六项通过、无页面异常，准备提示消失、草稿保留且未提交普通run。`node build/compact-notice-browser/probe.mjs`；证据及截图build/compact-notice-browser/verification.json，已目视检查宽屏运行中及窄屏失败截图。mock SSE响应会结束，运行中截图出现既有恢复连接提示属于fixture边界，不作为真实长连接证明。
- 最终全量命令：`PATH="$PWD/build/mcp-default-identity-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index N --count 2 --output build/context-recovery-final-N.xml`（N分别0、1），3326+3348=6674通过、0失败/错误/跳过，见对应log/XML。此前在补停止/无更新边界时主动中止的全量不计通过。
- 汇总build/context-recovery-verification.json；git diff --check通过。本片单独提交，未修改真实配置、发布或部署；其他并行改动保留。

## 提交前独立快照

仅将本片6个文件暂存，handoff仅包含本段变更；git checkout-index导出当前暂存快照，确认Python包来自该快照，使用原虚拟环境与按原脚本准备的fd/rg资产。排除未提交MCP及其他文档/探针；后端两分片3326+3343=6669全部通过、0失败/错误/跳过，前端681项和生产构建通过。与整合工作区少5例为排除MCP新增用例，非skip或删测试。提交前只更新上述文档结果，四个代码/测试文件与测试快照逐字相同。安全专项只读审查未发现阻断项，核查会话准入、工具不重放、原头保留、同thread写入与写入确认保护。日志build/compact-commit-backend-{0,1}.log/XML、build/compact-commit-frontend.log、build/compact-commit-build.log，汇总build/compact-commit-verification.json。
