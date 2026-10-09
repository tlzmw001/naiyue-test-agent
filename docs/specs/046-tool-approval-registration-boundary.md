# Spec 046：工具审批注册与运行层边界

状态：2026-10-02，已实现并完成本地确定性回归与一次真实模型合成工具探针。用户已确认：应用入口选择并注册工具；通用 Agent runtime 被动接收工具及其审批描述，不按具体工具名增加分支。本片只整理现有 `read/ls/find/grep/bash` 审批链路，不增加正式工具、审批模式或权限范围。

## 当前数据流与问题

CLI 创建工具并传给 `build_local_application`。`agent_runtime._build_runtime_agent` 依据五个具体 `Session*Input` 类型拼 `HumanInTheLoopMiddleware.interrupt_on`；框架在执行工具前中断。`application._pending_tool_approval` 再按五个名字解析模型调用和审批目标；`SessionService.resume_tool_approval` 把批准目标分成只读路径和 Bash 命令；`agent_runtime.resume_agent_run_in_workspace` 构造两类一次性 grant 并放入 run context；各工具执行时重新检查目标并消费 grant。于是新审批工具需要同时修改通用 runtime 和应用的多处工具名分支。

Pi `agent-loop.ts` 的 `prepareToolCall` 只调用通用 `beforeToolCall`，具体拦截由调用方/扩展接入；AgentX 的会话处理器从 `BuiltInToolRegistry` 获取适用工具。这里借鉴职责边界，不照搬其不同框架的 API。LangChain 的 HITL 仍要求在创建 Agent 时提供按工具名索引的 `interrupt_on`，并依赖 checkpointer 暂停恢复。

## 本片契约

- 工具工厂在工具的 `metadata` 声明可选的审批描述：中断谓词、描述文字、从可信工作区和模型参数解析供人审查的目标、一次性 grant 的构造方式。沿用当前 `naiyue_execution_mode` 的元数据模式；普通无审批工具不需要描述。
- 应用入口传入工具列表后，runtime 通用地读取已声明描述，按实际工具名构造 `interrupt_on`；不 import `read/ls/find/grep/bash` 实现，也不按 `args_schema` 或名字识别工具。无规则时不创建 HITL；有规则但没有 checkpointer 时继续拒绝装配。重复工具名及无效描述在装配时拒绝。
- 应用层使用同一份描述核对中断请求与最近 `AIMessage.tool_calls` 的 ID、名字、原参数及顺序，展示规范化目标；不再自行维护五个工具名的审批分支。无效参数不进入审批，仍由工具校验回填模型错误。拒绝无副作用；批准只对该次工具调用、目标和可信工作区有效。
- 应用层根据已批准项按工具名生成 grant，传入通用恢复入口；runtime 只把可信 grant 映射放入 run context 并执行 LangGraph 恢复，不构造具体工具的 grant。工具按自己的名字取 grant，执行时重验并一次性消费。现有只读与 Bash 的公开审批行为不变。
- 保留现有公开调用签名；旧 `resume_agent_run_in_workspace` 作为兼容入口，其既有只读/Bash 参数仍可用。新增工具接线不需要修改 runtime 的通用装配与恢复路径。动态热插拔和权限模式不在本片。

## 影响文件与验证

新增通用审批描述模块；修改五个工具工厂的元数据、runtime 的通用 HITL 装配与 grant 传递、application 的待审批核对和恢复装配。新增确定性测试：合成审批工具只在工具工厂/应用注册即可中断、批准、执行；无规则工具不审批；重复/无效描述拒绝；现有五工具的批准、拒绝、无效参数、目标变化、跨会话和批次顺序回归。先写失败测试并记录红灯，再最小实现、定向及全量 pytest、compileall、pip check。真实模型质量和生产隔离不由本片证明。

## 实际实现与证据

- `agent_runtime/tool_approval.py` 提供不可变的审批描述、审查目标、一次性精确 grant 和注册校验。会话版 `read/ls/find/grep/bash` 工厂在元数据中声明；非会话版与 `write/edit` 不增加审批声明。应用层从传入的工具收集同一份描述，负责中断与模型调用对应关系、展示目标及生成 grant；runtime 通用地生成 HITL 映射、传递 `approval_grants` context。原公开 `resume_agent_run_in_workspace` 保留为兼容入口；应用使用新通用恢复入口，兼容入口仍为既有五工具构造映射，不作为新工具扩展点。
- TDD 红灯：新增 `tests/test_tool_approval_registration.py` 后，定向 pytest 在收集阶段因缺少 `agent_runtime.tool_approval` 退出 2。首次实现后定向审批与工具批次回归输出 `61 passed`；增加缺失 ID 与 `None` 目标的边界测试后，定向先出现 `1 failed, 3 passed`，定位到空 grant 字典的误匹配，修复为先检查调用 ID 是否存在。最终全量 `.venv/bin/python -m pytest -q` 输出 `418 passed in 14.67s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。所有受改动生产文件和新测试文件均小于 500 行。
- 已配置的真实模型合成探针 `.venv/bin/python -m scripts.probe_live_workspace_tools` 退出 0，最终输出 `status=PASS tools=ls,find,grep,read,write,edit,bash approval=paused_then_resumed final_file=verified`。这验证当前工具接线未被迁移破坏；合成 `approval_probe` 的动态注册、审批和授权由假模型测试证明，不声称真实供应商已调用该合成工具，也不证明生产身份/租户隔离。
