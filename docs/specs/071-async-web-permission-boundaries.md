# Spec 071：异步/Web提前与权限扩展边界讨论

状态：2026-10-08，用户认可边界→异步→最小Web→整体学习→反馈强化的顺序。本稿记录已确认方向和职责设计；首个Read边界样板已按Spec072实施，其余具体实施Spec/API与前端栈仍需逐片确定。

## 当前事实

最新里程碑：[Spec075](075-integrated-async-application.md)按用户要求把工具/应用/持久化/正式流式消费合并完成，882例与真实两进程工具/控制/保存/恢复通过；以下Spec072/073状态是历史时点，Spec074也已完成上下文异步。当前应用有正式异步API和文本delta，CLI仍同步；活动取消和Web仍未实施，成熟权限/高级恢复后置。

最新进度：[Spec072](072-read-permission-extension-seam.md)已完成会话Read的可信策略接口与带上下文审批review，默认行为保持；17例新增边界验证和全量798例通过。仅Read支持策略注入，其余工具迁移与完整权限产品后续逐片推进；原生异步、流式和Web尚未实施。下文探针与“本轮只读”记录对应此前设计轮次，不代表Spec072无产品改动。

后续[Spec073](073-async-model-call-middleware.md)已完成选模/响应门禁与请求观测的双路径：无工具底层编译图ainvoke、内存checkpoint和有界真实模型通过，全量812例。此前“原生异步未实施”是Spec072时点；现在只有模型边界部分接入，统一应用、摘要/恢复、工具调度/审批、持久化、流式/Web和活动用户取消尚未接通。

目标是逐片扩展并减少跨层返工。当前read/ls/find及部分grep外部目标已有一次审批；write/edit硬限制工作区；host Bash获批后按宿主用户权限访问，工作区不是沙箱。仍缺会话级授权、可配置范围、外部写审批/审查、Auto/完全信任模式及撤销/审计。

Spec046已有ApprovalPolicy元数据和when/review/grant_factory通用接线，新工具无需每层增加名字分支。但read分类、write._target_in_workspace及edit共享helper仍含固定策略，外层allow不能覆盖执行器硬拒绝。后续须迁移有限适配点，不能承诺任何功能零改动。

## 参考源码

- 用户明确指深度求索DeepSeek Harness，非LangChain Deep Agents。DH核对master提交5badb15009ae1756c3afe0ae0cef1faafc290ccc：Cordis管理插件服务/注册/生命周期；core tools prepareScheduledTool运行tools/pre-execute，ask交独立approval.request；allow后仍检查guardReason，deny形成错误结果，才dispatch。user-approval记录asked/decided；缺服务时ask降deny。sandbox-policy另解析模式/root，由后端执行约束。插件不是跳过所有底层约束的万能开关。
- Pi本机a69bef789：AgentSession安装beforeToolCall→extensionRunner.emitToolCall；agent-loop验证参数后await hook，block转错误工具结果，afterToolCall另处理结果。普通观察订阅者与授权关卡不同。
- AIAutoTest加载@gotgenes/pi-permission-system，加载失败拒绝启动；permissions.ts配置规则、桥接allow_once/allow_session/deny。这是外接扩展，不宣称Pi core内置完整权限产品。
- AgentX本机3fcfa0e：partial/工具/end经MessageHandler→transport送客户端，保存和trace另分职责；SSE不证明我们的Python路径原生异步。

源码：
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/tools/src/index.ts#L1495
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/interaction/user-approval/src/index.ts#L215
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/sandbox/sandbox-policy/src/index.ts
- 本机Pi agent-session.ts:485/agent-loop.ts:619；AIAutoTest pi_worker/src/session.ts/permissions.ts；AgentX AbstractMessageHandler.java:342。

DH仍为developer preview；借鉴接口和职责，不引入Cordis/插件市场/热卸载、不替换已确认Python/create_agent。

## 建议契约

应用组合策略/会话授权/审批通道；runtime消费注入依赖、运行标准循环；工具提供实际操作/规范化资源并在执行前复核，保持类型、文件一致性、IO及错误回填；UI显示后端允许动作、提交决定，不自行扩大授权或直接执行工具。

流程：规范化请求→策略allow/ask/deny→必要审批→执行前复核→工具主体→ToolMessage。when/review/实际执行须消费同一规范化请求。策略、审批交互、工具机制分别可以扩展；现有默认约束保持。先定边界/迁移最小必要接线，不构建完整规则数据库或Auto评分器。

## 本轮异步探针与建议顺序

假模型临时探针：内存app._agent.ainvoke抛NotImplementedError，缺awrap_model_call；持久app先抛NotImplementedError，SqliteSaver不支持async。无真实模型请求，不算新功能/失败修复。

当前还包含同步摘要invoke、tool_batch Condition.wait、同步工具/grep进程/Bash、SQLite元数据IO、同步订阅回调。异步是一次必要底层扩展，不能只改await。小文件IO可以有界线程卸载，须明确取消/副作用边界。

推荐顺序：
1. 边界小片：定稿策略/审批/工具/Web契约与默认回归，只处理实际必要的接线点。
2. 原生异步与流式观测：补模型/工具middleware、摘要/恢复、调度/控制、async saver/元数据IO；共用策略算法，保留完整消息/终局语义，增量暂定内容与完成/持久确认分开。
3. 最小本地Web：同一application提供消息、工具卡、会话恢复、已配置模型选择、Steering/Follow-up、已有审批和after_turn停止。loopback单用户，无账户登录但须有本地访问/来源校验和副作用API授权。断开HTTP流不等于取消，不放宽工具权限或偷偷恢复受限会话。
4. 冻结版本系统学习：沿消息/模型/工具/权限/控制/上下文/checkpoint/元数据/UI追任务，复写、排错、增工具/middleware/评测分别验证。AI实现不等于掌握。
5. 按反馈强化：允许范围/会话授权/Auto与信任模式、工具/上下文、高级会话恢复、测试业务闭环各片实施。

活动取消在异步片明确范围，验证模型/摘要/SDK重试/工具子进程和副作用未知；未验证前保持after_turn，不因async/stream假称取消已支持。事件域接口与框架/HTTP协议分开；前端栈/传输细节在对应Spec确定。

本轮只做源码/官方材料与现有路径探针；未安装运行参考项目、改代码、跑全量回归或宣称生产能力。推进顺序现已认可，具体实施契约逐片确定；完整代码学习延至Web里程碑，各片仍保留简短机制和验收。

## 异步原因与Web展示职责澄清

用户认可顺序，追问除逐字符展示之外的异步理由及独立界面模块。同步/异步是等待与调度方式，流式/非流式是结果交付方式；invoke/ainvoke可等完整结果，stream/astream按选择的模式交付进度或token。首期单活动run仍有模型等待、控制/API处理、事件发送等多项并行等待，不需先支持多用户才有价值。

异步适合Web任务管理：等待模型/工具时继续处理Steering/Follow-up/停止/审批/API请求；独立调度事件消费与执行；按约束并发等待工具；为多会话/多Agent及超时/取消传播准备明确任务生命周期。当前CLI已有Thread保持输入可用，线程也能流式输出，异步不是唯一方案；CPU工作不会由async自动提速，同步IO需明确卸载，取消需验证SDK/工具是否实际停止。审批interrupt本身不要求持续挂起一个协程。

Web展示独立于模型/runtime。建议分网络事件接收与控制提交、展示状态、消息/工具/审批/状态组件三类职责；Web入口只调用application，runtime不导入前端组件或HTML。现有CliEventDisplay继续服务终端，复用领域事件语义而不把print或日志变成Web数据协议。

Pi a69bef789 interactive-mode消费message_update、tool事件，调用AssistantMessageComponent/ToolExecutionComponent更新并requestRender；AgentX3fcfa0e前端service、chat state/hook、组件分层，其AbortController断连接与后端interruptSession请求是两步，不推断取消模型调用已完成。本项目不重写pi-tui，后续采用Web组件框架，技术栈对应片再定。

显示规则：临时文本delta追加到同一消息草稿，完整消息按实际ID校准/替换草稿，不把最终文本再追加一遍；修订更新对应显示对象；tool_call_id关联工具卡和审批结果；run_finished驱动四种终局，临时文本不等于已保存。模型发送通常是token/text chunk，不保证逐字符，浏览器可批量刷新。

恢复显示与当前模型上下文分别设计：压缩移除模型消息不应让当前页面既有聊天记录消失；首期存储只验收活跃上下文延续，尚无完整历史浏览。Web须明确初始化/重连的数据来源与缺失提示，后续完整历史投影属于读取/展示能力，不混进模型循环。慢浏览器/断流不能无界堆积或自动等同取消；事件传输适配器须在运行前接好订阅，不能以当前每run订阅快照保证任意热接入。

本轮只读当前CLI/事件/Pi/AgentX和Python3.12/LangChain官方材料，未新增功能实现/测试，核心讲解尚无用户复述或独立实现证据；未修改关键知识点。
