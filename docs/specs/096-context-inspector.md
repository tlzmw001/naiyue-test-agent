# Spec096：上下文构成查看

状态：2026-10-17用户已批准并接线，验收记录见文末。入口固定在发送键左侧；只读查看，估算与供应商报告分开。

## 目标与当前链路

当前 `prepare_run_resources → ToolSetMiddleware.model_inputs → ModelInputMiddleware → observe_model_request → 模型 → select_model._accept` 已能装配提示词、估算预算及读取usage；估算主要在日志，Web未投影。聊天展示保留的旧内容不能作为模型上下文。使用现有LangChain近似计数，不改压缩算法、权限、工具执行或模型调用次数。

新增链路：资源准备同时保留提示词来源 → 原工具装配生成相同文本及不含正文的长度描述 → 输入适配后生成构成快照 → 根/子各自发布统计事件 → 返回时补usage并将统计摘要放入AIMessage.response_metadata → 原checkpoint保存。Web根运行只接根统计；认证GET读取当前checkpoint并准备只读预估，运行中采用已发送的固定快照；前端发送区入口及与子智能体互斥的侧栏展示。

## 数据与接口

- `agent_runtime/context_usage.py`：唯一统计算法、固定来源类别、预算元数据、统计事件载荷；系统分项用累计前缀差分归属取整与封装开销，各消息按官方计数器分类，工具schema独立计数，分项相加等于总估算。图片标注框架粗估；CC图片载体归工具结果。来源只有有限类别和AGENTS/CLAUDE标签，不导出正文、路径、参数、像素、URL、凭证或完整工具schema。
- `application/skills.py`与`project_instructions.py`：已有读取返回提示词的同时保留来源，读取规则与完整文本不变。SessionService在run内冻结来源，收尾清除。ToolSetMiddleware使用同一个组装函数服务执行、预算和查询，系统字符串保持原样。
- `call_observation.py`：把现有model_call_id/model_id放入本次runtime context，供内层统计配对。`context_observation.py`发布`model_context_snapshot`的发送/返回或失败状态；成功响应统计附着在response_metadata，不变成模型文本。故障有日志、保留原异常与正常结果。没有供应商用量时为null，真实0保留。
- 成功响应的有限统计随官方checkpoint保存，无新表、schema迁移或额外update_state。历史无统计、失败请求未进入checkpoint、旧消息已被压缩移除时，不伪造历史构成。当前进程保留最近已观测请求，重启只恢复实际保存的记录。
- 新`application/session_context.py`及`AsyncApplication.aget_context_view(session_id)`：只读官方当前状态；空闲时用当前选中模型、活动工具、重新准备的项目/Skill资源计算；运行中不另读资源或估算未完成回复，使用最近已发请求快照。排队消息、草稿、未来工具结果不计入。恢复未完成/历史配置不可用时保留可读历史统计，当前预估显示不可用。
- 新`GET /api/sessions/{session_id}/context`：沿Bearer/Host/Origin及工作区归属，固定字段，应用查询失败日志+503，仅影响查看。返回session_id、current及basis（next_request/last_sent/unavailable）、latest_request。窗口、输出及压缩预留注明config/default/unknown；阈值用window-max(output,reserve)，不重复扣减。
- Web DeliveredRun保留最近根统计，SSE/快照重新同步一致。子事件不覆盖根统计。不按所有事件/字符增量重新读取文件或估算，不为查看发模型请求。
- 统计有独立交付降级：统计事件超出max_frame_bytes时，保留同一序号/调用ID并把snapshot_json投影为null，不制造序号缺口、不标主消息不完整。根run/session快照若仅因新增统计超限则省略context_snapshot，面板沿独立GET显示不可用；原主消息真的超限仍按原错误处理。测试按无统计的真实主视图计算预算，避免临时路径长度/操作系统影响固定预算断言；不能把可选统计变成审批前置条件。
- `ContextPanel.tsx`、`ContextPanel.module.css`、`useContext.ts`：沿现有浅色CSS Modules；当前预估、最近请求两个页签。发送键旁圆环+百分比，未知显示上下文图标与可访问名称，不能显示0%。侧栏与子智能体互斥，切会话关闭；移动端覆盖面板、关闭回入口。默认不展示原文。

## 改动文件

本片跨运行/应用/Web/前端，涉及超过8个文件；不是新增独立服务。

新增：本Spec、runtime/context_usage.py、application/session_context.py、frontend/src/contextTypes.ts、frontend/src/useContext.ts、frontend/src/components/ContextPanel.tsx及.module.css；对应Python/前端行为测试。

修改：runtime/context_observation.py、call_observation.py、events.py、tool_execution/toolset.py、runtime/__init__.py；application/skills.py、project_instructions.py、session_service.py、session_persistence.py、__init__.py、async_application.py；web/app.py、service.py、run_delivery.py；frontend/api.ts、types.ts、events.ts、App.tsx、Composer.tsx；handoff、future-work与必要当前文档。

不修改真实配置/用户库、依赖、审批、停止、压缩决策、旧学习记录或知识点文档。不实现手动压缩/Skills浏览/费用及全会话累计用量。

## 正常与失败例子

正常：GET空白会话 → 当前模型+工具+项目说明本地预估，latest_request=null；发消息 → select_model创建id → 最终输入统计事件带同id →供应商返回input=33280/output=1600/cache=10000 →同次快照与usage配对 →响应checkpoint保存；回复后的空闲预估加入新回复，可以与旧输入用量不同。缓存是输入子集；供应商没有按AGENTS等给精确分项。

失败：统计计数或资源读取失败 →日志仅安全错误类型 →面板不可用/重试，主运行不受可选观察影响。模型异常 →快照显示失败/用量未知，原异常继续上抛。切会话或切模型时旧GET不得覆盖新状态。服务重启GET只读已保存统计，不恢复工具、不发模型、不自动压缩。

## 验证

TDD先确认缺少接口/统计的红灯，再实现。覆盖提示词来源和文本不变、分项总和、图片与工具schema、压缩摘要、缺usage与真实0、模型切换/工具切换、根子隔离、统计故障原异常、只读无调用、鉴权/工作区归属、checkpoint重开；前端入口位置、两页签、重试/空白/失效请求、侧栏互斥与SSE重新同步。运行针对性及后端/前端全量、TypeScript/Vite、diff与500行检查；实际构建用离线模型/临时SQLite在1440和375px验证。所有统计示例必须来自测试或明确的设计示意，模型供应商和Windows原机证据另列。

## 实际接线与迁移收尾

- 执行：`SessionService._run_steps/_resume_steps → prepare_run_resources → resolve_prompt_parts → ToolSetMiddleware._inputs → compose_model_prompt → ModelInputMiddleware → observe_model_request → estimate_context/request_snapshot → 模型 → finish_snapshot → 原响应gate → 原checkpoint`。同一系统字符串、同一近似计数器，统计不添加模型文本。
- 查看：`WebApi.context → GET /sessions/{id}/context → WebService.context → AsyncApplication.aget_context_view → context_view`；已加载状态只读官方snapshot，空闲读取当前资源；运行中用DeliveredRun.context_view，暂停/异常未完成状态不伪装next_request。`ContextTrigger`只在Composer.actionGroup，`ContextPanel`和子智能体侧栏互斥。
- 迁移删除：`compose_tool_prompt`旧单独拼接、应用层`run_project_instructions`重复合并字符串/getter和无消费者的旧字符串loader；`prepare_run_resources`只返回来源parts、审查用项目说明、只读资源根。旧测试改验相同的最终提示词和parts收尾；无同步兼容执行链。
- 确实保留：独立框架`resolve_project_instructions`字符串回调仍由`probe_async_model.py`与`test_async_context_chain.py`使用，ToolSet在没有应用来源resolver时把它作为一个项目段，不解析字符串猜来源。
- 旧断言同步：新统计事件增加每次模型请求两个通知，保留全部原事件配对/终局/异常身份断言，仅同步确切序号、类型数及探针消费者；Auto资源读取计数改挂真实part读取入口，仍验证每run只读一次和快详审同一冻结内容。未改conftest或Vitest配置。
- 独立架构复核发现待审批被误标next_request，回归先红后绿；安全复核及交付降级增量复核无遗留确证问题。有限快照只允许固定来源标签与非负数字，不把历史metadata未知字段透出Web。

## 最终验收（2026-10-17）

- 后端：`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **2115 passed in83.63s**，日志`/tmp/naiyue-096-backend-complete.log`。本片11项新后端用例；最后关联模块51项亦通过。此前一轮TTY旧double遇线程退出时序失败，单项保留断言复跑及两轮全量通过，未修改该测试/CLI行为，不宣称排除此旧时序风险。
- 前端：frontend目录`npm test -- --run` → **15文件72 passed**，日志`/tmp/naiyue-096-frontend-complete.log`；`npm run build` → **TypeScript/Vite195模块成功**。新增4项覆盖位置/互斥、口径、0与缺失、旧GET、键盘及null统计连续序号。
- 结构/依赖：334份Python AST/≤500行、40份前端源≤500行、compileall、pip check、文档链接和git diff --check通过。无新增依赖/数据库表/真实配置修改，未提交/push，086/094/095既有工作树保留。
- 实际构建页：临时工作区、真实Read、临时SQLite、离线模型，验证1440×960与375×850，发送键旁入口、分项、报告用量、关闭回焦点、模型切换预算/历史分离及独立服务重启后只读查看。截图：`/tmp/naiyue-096-ui/desktop-current.png`、`desktop-request.png`、`desktop-model-switch.png`、`mobile-current.png`、`mobile-entry.png`。
- 当前页实际预估829 tokens，分项462+183+13+58+113=829；最近请求发送前估算799，与测试模型报告input1880/output78/cache200分开展示。报告字段来自明确的离线模型，不是实网供应商证据，不能拿这组差值评价真实计数准确性。测试没有自动发真实模型请求。
- 证据边界：本机macOS框架/SQLite/文件/HTTP/构建浏览器；无新真实供应商、Windows/Linux原机、业务效果或学习掌握声明。下一块先讨论手动压缩上下文，之后Skills查看。
