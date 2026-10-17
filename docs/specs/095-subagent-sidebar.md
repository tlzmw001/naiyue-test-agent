# Spec095：子智能体侧栏

状态：已实现，本机确定性验证通过。承接 Spec086、094；用户 2026-10-17 批准本片实现。

## 目标与界面合同

- 主会话顶部增加“子智能体”入口，打开名称列表；同名的不同委派按 agent_path 独立显示，以短标识和任务区分。
- 点击列表进入详情。详情顶部不显示角色/实例名字，只保留返回列表、状态和任务说明；消息以主会话已有分割线、Markdown、工具卡片显示，不增加过程/结果标签页。
- 审批全部保留主会话现有 ApprovalPanel。子侧栏只显示等待状态及“到主会话处理”入口；审批不自动展开侧栏。窄屏返回审批时关闭覆盖面板。
- 原单子智能体停止沿用当前 scope stop API；只有匹配当前运行的活跃实例可停止。没有子输入框、重新运行/续聊、后台能力。
- 读取失败明确显示错误与重试；无记录显示空状态，不填演示数据。

## 改前调用链

根模型调用 subagent → SessionSubagentControls.prepare_subagent_instance 计算会话+父消息批次+工具调用 ID 的摘要 → 独立 child_thread_id → 子图事件附 source 经同一发布器 → DeliveredRun.observe / applyAgentEvent。

目前 AgentView 只有状态和最近批次的工具摘要，AI 正文未投影；ToolCall 内嵌工具摘要，没有独立详情。子图完整消息已经保存在官方 saver 中，父 ToolMessage 只接报告和 artifact。根结束后活动实例被释放。

## 改后模块及方法

1. `application/subagent_history.py`：提取正在使用的确定性身份函数 `subagent_identity`，增加 `subagent_records`，仅按真实父 AI 调用及其配对结果建立可读索引。模型传来的 thread_id/agent_path 不可作为读取凭证。
2. `application/session_subagents.py`：实例创建复用同一身份函数，摘要输入/路径/线程格式不变。
3. `application/async_application.py`：`aget_subagents(session_id)` 从当前根 checkpoint（未加载会话经既有只读 history）列出委派；`aget_subagent_history(session_id, agent_id)` 先证明属于该会话，再经官方异步 graph state 读取派生 child_thread_id，返回复制的消息及保存状态。不 invoke / update_state / 恢复子图。
4. `web/service.py`, `web/app.py`：增加认证 GET `/api/sessions/{session_id}/subagents` 与 `.../subagents/{agent_id}`，复用 session 工作区归属、统一 frame 大小和公共错误边界。详情输出复用 message_view；活跃同会话实例以原 delivery 的最新状态/消息覆盖保存快照。不会接受任意 saver thread 名称。
5. `web/agent_projection.py`, `web/run_delivery.py`, `frontend/src/agentEvents.ts`, `types.ts`：子投影保存正文增量、完整/修订消息、工具结果和进度；不混入根消息、不清空前批结果，按 batch/run 关联同名 call ID。根快照仅保留身份、状态、父批次及最近工具批摘要，不附完整子消息；`agent_view` 单独复制选中实例消息及进度，并去掉可从消息读取的重复 result 正文。子详情超限仅影响详情读取，不能阻断根审批。未确认草稿明确标识。只展示公开文本，不读取推理块/模型配置。
6. `frontend/src/useSubagents.ts`, `components/SubagentSidebar.tsx`, `SubagentSidebar.module.css`：拥有列表/详情选择、异步请求与过期响应保护、错误/重试、消息查看。详情直接渲染现有 Conversation，无独立消息格式。切换会话清理选择，异步旧请求不可覆盖新选择。
7. `App.tsx`, `Conversation.tsx`, `ToolCall.tsx`：入口和紧凑委派链接接侧栏；保留同一审批区域及草稿/模型/模式/停止流程。移除主 ToolCall 中重复展开子工具的旧展示，由侧栏接管。
8. `api.ts` 增加两个只读方法与错误文案。当前文档和 handoff 同步新入口。

## 数据与完整场景

列表项示意结构：`{agent_id, agent_path, agent, task, tool_call_id, status}`。该结构由应用根据真实根消息构建；任务不是新增的人类授权。消息详情为 `{...record, messages, has_compaction, available}`，Web 会附当前 live 的 tool_states/source（若有）。UI 名称只用于列表，详情不渲染名称。

索引和子事件投影同时保存 `parent_batch_id`；同一根 run 内复用 outer call ID 时，入口和任务必须绑定原父 AI 批次。历史错误反馈可能来自正常停止，读取失败项的子 checkpoint 中 run_stopped 以区分已停止/失败。没有 checkpoint_id 时 available=false，不能把仅含 thread_id 的空 StateSnapshot 当作已保存记录。

根摘要的 tool_states 不携 args/result/text 正文，仅保留状态/计数/时间；独立详情保留完整消息及实际进度，React 按 batch_id 合并详情和摘要，摘要不能覆盖掉详情已有的输出尾部。原 Web 子工具结果测试已迁移到独立详情查询，保留相同正文/参数/状态断言，不保留主工具内嵌的平行展示链。

为支持生成中刷新/重连，根摘要携带当前 provisional 草稿及 delta_index（不携已完成的子消息）；所有子草稿共享 max_frame_bytes 的四分之一预览预算。超出预算只传 id/index 和 content_unavailable 标识。客户端照索引继续消费增量，不能把正常缺前文判成全根序列中断；详情过滤缺前文的尾段，明确提示长消息在完成后同步，独立 GET 仍可读取可交付的完整记录。完整 message_completed 会替换草稿并解除标识。真实事件序号缺口仍按既有重同步处理。根摘要另带 snapshot_sequence，选中详情在新快照或子状态变化后重读，避免精简快照覆盖已完成历史；不会随每个文本增量重复GET。

正常：根消息 batch1 的 outer 调用 tester → 既有摘要 H → source root/tester/H 的 seed、AI 正文、read/grep 及最终回答逐条进入 agents[path].messages → 用户打开列表选择 H → 只读 GET 校验 session/workspace/H 并获取 checkpoint 消息 → Conversation 配对工具结果并显示进度。SSE 持续更新所选实例；最终根消息仍只收到子报告。切换别的实例不影响运行和审批。

失败：用户打开 H 后切到另一会话/实例，旧 GET 返回时 generation 检查丢弃；读取未知 H 返回 404、不尝试任意 checkpoint。saver IO 失败记录安全类型并返回明确错误，侧栏可重试，运行和主会话草稿保持。未开始/未保存的委派允许空消息，不能伪造任务结果。

## 保留的边界

- 列表覆盖当前根 checkpoint 可关联的委派，加上当前 HTTP job 的实例；没有新增跨压缩的永久委派目录。和当前主会话历史一样，压缩掉的更早消息不由本片重建。
- 服务重启后读取子 checkpoint 是只读查看，不能据历史待执行位置恢复子工具；未知终局显示状态未确认，不能当作仍等待当前审批。
- 已保存消息保留，瞬时进度/耗时不另外落库；没有保存的数据不伪造。
- Web 原有大小上限继续适用；选中子详情超限返回显式 view_too_large，不静默删消息，不把整份子历史装入根快照。
- 不更改模型上下文、审批/权限/停止语义、依赖、配置和 DB schema。

## 验证

先红灯：子正文增量与多批消息保留；只读详情跨会话/未知 ID/认证/IO 故障；刷新服务后读取真实 SQLite 子历史；列表→详情无名称、复用工具卡片、审批只在 main；切会话/快速切实例旧响应被丢弃。

回归：后端 pytest 全量、前端 Vitest 全量、TypeScript/Vite 构建、compileall/pip check/500 行/差异检查；本地浏览器桌面与 375px 实际组件验收。使用确定性模型和临时数据库，不请求真实供应商，不改 .env，不提交/push。


## 实施验收（2026-10-17）

- 后端 `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`：**2104 passed in82.31s**，记录 `/tmp/naiyue-095-backend-final.log`。
- 前端 `npm --prefix frontend test -- --run`：**14 files / 68 passed**；`npm --prefix frontend run build`：**TypeScript/Vite 192 modules，0 errors**。
- 新增应用/Web历史、摘要、侧栏及接线测试，新增行为与定位的状态缺陷均先有对应红灯。复用测试 helper，不修改共享测试基础设施；原 Web 子正文/参数检查迁到独立详情接口，保留完整状态与配对断言。
- 专项覆盖：9与18次64KB读的独立详情及主审批、同批18份大参数/结果的摘要隔离、当前草稿重连及缺前文回退、compact snapshot刷新详情/保留尾部、相同outer ID跨批关联、切实例/会话丢弃旧GET、已停止历史重开、无checkpoint不伪造available、鉴权/未知ID/安全IO错误。
- 原生本地HTTP、构建资产、临时SQLite和确定性模型的浏览器验收：桌面1440×900与375×812消息流/工具展开、审批只在main、等待不自动开栏、窄屏回审批、一次读取批准、单独停止兄弟继续、根完成、关闭重启后读取原子记录。375px `scrollWidth=clientWidth=375`。截图 `/tmp/naiyue-subagent-sidebar-design/095-actual-progress.png`、`095-actual-mobile.png`、`095-actual-restarted.png`。浏览器证据来自临时验收服务，不代表真实供应商或零故障保证。
- 架构/安全两轮只读复核的批次、停止状态、大小隔离和增量基线问题已用测试闭合；最终无确证阻塞项。
- 没有真实供应商请求、Windows/Linux原机或业务质量证据；未改真实模型配置、用户库/工作区，未提交或push，086/094既有工作树保留。

唯一下一步：讨论只读“上下文构成”面板的数据口径和入口，再确定实施Spec；之后安排手动压缩和Skills查看。Steering/Follow-up与工具/模式管理已有入口，不重复实现。
