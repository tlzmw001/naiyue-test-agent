# Spec078：Web 工具显示与审批闭环

状态：2026-10-09，用户要求继续上一片明确的下一步；已实现并验证，结果见交接第225节及 Web 工具说明。本片复用已有工具和审批应用接口，不修改原工具权限/算法或 .env/依赖。复杂功能先记录此 Spec，再 TDD。

## 现有链路与参照

当前 Web lifespan 以 tools=[] 打开 AsyncPersistentApplication；RunDelivery 的独立 arun 生产任务与领域订阅分别拥有生命周期，SSE 断开不取消任务。message DTO/前端仅保留正文，审批状态阻断输入但没有审批入口。

本机源码核对：Pi a69bef789 interactive-mode.ts 的 tool_execution_start 创建按 toolCallId 索引的 ToolExecutionComponent，end 用真实 result/isError 更新；这证明工具显示与循环分开，不证明它有我们相同的审批协议。AgentX 3fcfa0e BuiltInToolRegistry.createToolsForAgent 根据 provider.supports 创建 specification/executor，适用工具由上层装配提供。我们复用 CLI 已有注册方式、应用 pending_tool_approvals/aresume_tool_approval 和 LangChain HITL，不复制 Java 接口或重写循环。

## 范围与职责

1. 启动设置有 workspace 时注册 read/ls/find/grep/write/edit；无 workspace 仍为聊天。增加显式 --host-bash（默认关闭），只有有 workspace 才允许，与原 CLI 相同；这不是沙箱保证。配置由参数/设置对象提供，不编辑真实配置文件。
2. 保留模型工具调用及 ToolMessage 的公开字段，正文走现有文本转换；临时运行 view 增加 tool_states，消费已有 tool_call_started/finished，含边界/outcome/固定 reason。参数来自完整 AI snapshot，工具结果来自对应 ToolMessage；不显示伪造结果，缺失结束证据标为未确认。
3. 会话 view 增加 approval_run_id 和 approval_requests（已有公共 tool_name/tool_call_id/规范目标/可选工作区），不暴露 grant 对象。后端 GET 复用已有会话接口，不恢复重启后 interrupted/failed 记录。
4. POST /api/sessions/{id}/approvals：严格 {request_id, origin_run_id, decisions:[{tool_call_id, decision:'approve'|'reject'}]}；所有调用必须有且只有一个决定、同原顺序。后端在串行准入锁内再读取当前待审信息，核对来源运行、ID及顺序；幂等回执优先于过期检查，重复同请求返回原 run_id，冲突/过期409。恢复调用 aresume_tool_approval，产生新 run_id，沿原 checkpoint/授权和保存链路执行。浏览器不能修改 args/目标/授予持久权限。
5. 展示与交互：工具详情折叠默认闭合、参数和正文使用转义 pre；成功/错误结果/未执行/副作用未知分别标记。审批区显示真实工具/目标，每项批准或拒绝，默认无选择；全选完后显式提交整批。恢复后接新运行 SSE，控制/停止同上一片。拒绝回流 ToolMessage，模型可以继续；HTTP失败保留选择、不自动重发。

页面继续白色主区/浅灰工具容器/黑色主动作/CSS Modules，不重排聊天布局或增加外观框架。Pi 的可折叠工具组件、现有 CLI 的逐项决定整批恢复、当前 Web 的独立控制请求分别作为显示/授权/传输参照。

## 修改面与接线

- application/session_service.describe_session：增加派生 approval_run_id（无新 DB schema）；公开 pending_tool_approvals 继续沿用原签名。
- web settings/entry/app：启动工具和 opt-in Bash；schemas/service/run_delivery：审批准入/去重/异步恢复；serialization/delivery：工具字段及状态投影。
- frontend types/events/api/useConversation：工具字段、事件状态、审批提交与新 run 订阅；Conversation + 新 ToolCall/ApprovalPanel 组件及 CSS。
- 新后端/前端测试；离线 socket 探针扩展工具/审批、浏览器以临时 workspace/库验证 UI。README/交接/待办同步；不改关键知识点。

依赖、Agent 循环、工具执行、ToolMessage 错误回填、工具串并行、HITL 授权复核与 checkpoint 表结构保持。新字段同步生产、序列化、前端、测试；view 仍受1MiB预算，工具状态为内存交付投影，不作为持久授权。

## 验证

TDD：先失败测试缺工具接线、DTO缺字段/事件状态、审批路由/严格请求/关联缺失；再最小实现。覆盖工作区工具成功/错误回填；workspace 外 read 中断且批准前无读取；混合批准/拒绝、ID/来源/顺序/重复/多余决定拒绝；同请求去重；恢复新run、停止、保存失败仍阻断；服务重启受限状态不自动审批。

前端验证参数/结果按ID关联、无重复普通助手工具正文、错误/未确认标记；每项无默认决定、提交完整批次、失败保留选择、恢复事件刷新；转义脚本/长内容/375px布局。

命令：全量 pytest；前端 Vitest、tsc+Vite；compileall、pip check、diff check；真实 TCP 离线探针。实际浏览器用合成临时文件/假模型验证审批操作与布局（不是收费模型或业务效果证据）。若真实模型验证则合成材料、独立库、最多两逻辑调用/120秒截止；本片优先确定性闭环，不因此增加收费重跑。

## 已知边界与下一步

审批只支持同进程当前批次，重启 interrupted/failed 不恢复；Bash 是宿主机命令且每次审批，不是沙箱。无图片/附件、持久草稿、权限模式/SaaS、多用户/租户。工具结果是授权用户可观察的内容，前端转义 HTML，日志不记录正文/参数/key；输出读取沿已有权限规则，不承诺自动识别输出中任意秘密。

完成后先让用户实际试用 Web 工具/审批，再确定是否冻结最小Web里程碑并系统学习代码；不自动扩展长期会话/权限工作。

验收补充：不同轮次重复使用调用 ID 时，结果按所在 AI 消息和调用 ID 配对，不能用全会话最后一个同 ID 结果覆盖旧工具卡；状态也核对其已知来源运行，来源缺失时显示已有结果而不借用其他运行的失败状态。审批选择只检查对象自身已选字段，原型属性不构成用户决定。
