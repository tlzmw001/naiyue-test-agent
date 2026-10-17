# Spec085-07：Auto权限审查（已实施）

接续085-06。2026-10-16按用户授权完成本片，本机验证见文末。只在已注册工具的有效调用未命中Deny/Allow/session_allow且默认Ask时提供模型审查；不重建Agent循环，不改官方批量暂停和一次性授权，不实施子Agent/UI/沙箱。

2026-10-16用户确认整片实施：自行为fast_model_id配置关闭/低推理，为detailed_model_id配置更高推理。程序使用两条目录配置，不临时改共享模型或强制推理值。固定System审查指令说明职责、实际影响/授权/项目约束、数据来源与allow|ask输出合同；相同JSON作为待审数据提供。快审追加“快速筛选、疑点Ask、简短输出”，详审追加“充分复核范围/授权/约束、不能确认仍Ask”；两者共享资料和公共规则，reason均有配置上限（permissions.auto.max_reason_chars，默认512）。不把短输出当成推理已关闭的证明。

## 改动与函数

- `tools/permission_rules.py`：PermissionSettings加入Auto和可选auto字段；两个ModelCatalog ID为fast_model_id/detailed_model_id。配置存在时都校验，未配置不得选择Auto。两条模型自身的输出/思考/请求预算继续来自models.json，不修改共享模型实例。
- `agent_runtime/tool_execution/permissions.py`：复用当前模块（现有约120行），新增AutoPermissionReviewer.review/areview和prepare_review_input；PermissionMiddleware把本地参数/路径/规则/default准备与剩余Ask模式处理分开，异步本地部分仍run_blocking，模型调用在hook线程之外await。仅Auto剩余Ask调用reviewer；保存实际决定后才到BatchApproval。
- `application/session_service.py.SessionService.__init__`从同一catalog.get取两个模型并构造reviewer，`application/__init__.py._assemble_application`及`agent_runtime/__init__.py`把它注入固定PermissionMiddleware。四App入口共用此链，async入口本身无需重复改造；SessionControlMixin/持久恢复沿同一配置校验模式可用性，不在运行中安装middleware或建立另一套ModelCatalog。
- `application/session_service.py`普通输入、`session_controls.py`steering/follow-up投递：在真实人类输入进入图时加可信来源标记；来源不依赖可选ControlObserver/TurnObserver成功。不信任任意HumanMessage类型或压缩摘要等于人类授权。工具结果/文件正文不为补证据而读取。
- `application/skills.py.prepare_run_resources`保留已加载的纯项目指令：当前函数把`load_workspace_instructions`结果和Skill索引拼成一个字符串；07改为返回分别可取的项目指令、主模型组合提示和资源根（简单命名字段，不另建资源管理器）。SessionService保留现有`run_project_instructions`供主模型，并保存纯项目指令的run快照；`resolve_permission_context`沿既有回调额外返回该快照，Permission交给`prepare_review_input`，不新增查询回调、不解析拼好的XML、不二次读盘。同步/异步run、resume、清理、restore初始化及原资源测试同步迁移。
- `agent_runtime/active_stop.py`：从现有StopBoundary提取finish_stopped_batch，保持未执行ToolMessage配对/事件/run_stopped/jump_to=end；Auto等待收到UserStop时调用。外部CancelledError仍清理后传播。沿RunControl.await_model管理审查等待。
- `tools/settings.py`现有loader直接消费扩展后的PermissionSettings，无需新增平行解析器；模式存储校验、样例和对应测试同步迁移。`stream_events.py`增加专用review标签，`async_message_observation.py`排除该标签，`frontend/src/types.ts`会话类型接受Auto，无新UI。无新DB列、审批历史、审查图或事件系统。

## 输入和结果合同

prepare_review_input从当前图messages投影：有可信来源的仍在上下文中的人类消息，以及按主Spec确认可提供的历史工具调用名称/参数；当前待审调用和准备事实单列。历史调用不代表人类授权。2026-10-16用户确认参考Claude，另外提供本run加载的AGENTS.md或CLAUDE.md，作为独立的`project_instructions`输入；替代原“排除AGENTS正文”的候选。继续排除工具结果、普通文件正文、助手解释/思考、Skill索引/正文和压缩摘要。write.content、Edit文本、命令脚本若是当前参数，则仍是待审数据，不能冒充可信指令。

项目指令沿现有`load_workspace_instructions`选择：工作区根AGENTS.md优先，不存在才查CLAUDE.md，不把两份拼接；空文件、加载错误、链接及文本大小检查均沿现有合同，不新增向上/子目录扫描。每个新run（包括审批恢复run）准备一次，同run不重读；快/详审复用同一快照。恢复旧批准批次仍不重跑审查，恢复run后续新批才使用新快照。没有文件则该字段为空，不编造内容。项目指令与真实人类消息保持独立来源，文件内声称“用户已同意”不能伪装成用户消息，也不能覆盖确定性Deny或固定审查边界；项目约束只参与Allow/Ask判断，不直接生成权限规则。指令必须纳入完整审查预算，不能静默截断可能位于末尾的限制；无法容纳则Ask。

参照证据：[Claude Code官方权限模式文档](https://code.claude.com/docs/en/permission-modes#how-auto-mode-evaluates-actions)明确客户端审查请求包含CLAUDE.md内容；这不证明其内部字段名与本项目相同，也不等于向审查器开放所有工具返回内容。

示意（非真实用户数据）：

```python
{
    'human_messages': [{'sequence': 0, 'id': 'human-1', 'content': '请运行当前项目的测试'}],
    'project_instructions': '<project_instructions source="AGENTS.md">只能运行本地测试，不要部署。</project_instructions>',
    'previous_calls': [],
    'current_call': {'id': 'call-1', 'name': 'bash', 'args': {'command': 'python -m pytest -q', 'timeout': 30}},
    'subject': {'operation': 'execute', 'workspace': '/project',
                'side_effects': 'may_mutate', 'execution_environment': {'kind': 'host', 'platform': 'windows'}},
    'reason': 'default'
}
```

固定审查指令、项目指令和待审数据分别标明来源组装。快/详审复用同一份有界输入；可裁减旧历史，当前调用和项目指令不静默截断。当前调用/上下文无法放入审查预算或来源不足时直接Ask。严格结果只有decision=allow|ask及有界reason；Deny/非法JSON/多余字段/异常/超时回人工Ask，并只记录安全原因和可得usage，不记录正文/脚本/项目指令。

## 正常链与失败链

1. 主模型提出Bash；现有prepare检查有效参数/目标，Deny→Allow→session_allow都未命中，default=Ask。
2. 本批mode=Auto，整理一次输入。fast.areview返回Allow则结束；只有有效Ask才发detailed.areview；普通失败直接人工Ask，不用第二次请求补救。
3. 最终Allow随PreparedToolCall保存；原Authorization直接发本次permit→Bash执行。最终Ask保存后原BatchApproval整批暂停；同批其他工具仍未开始。
4. 用户批准：原prepare_resume生成grant→Command恢复→Authorization消费grant→执行。准备和两个审查阶段均不重跑。
5. 用户停止审查等待：取消本run审查任务、用现有停止合同配对未执行项并结束，不伪装成“模型认为Ask”；外部取消保持原异常和清理。

审查不向主会话messages追加对话、不调用主模型切换、不发purpose=agent的主模型事件，也不把审查失败归为主模型回答失败。同步SDK沿同一审查算法的现有同步桥接方式调用，正式CLI/Web走原生await路径；实现时核对现有桥接后再定函数，禁止复制两套审查算法。

接线细化：复用项目已有“生成器步骤+sync/async驱动”的有限算法模式（context_summary_io），review/areview共驱同一_review_steps；只在两个驱动中的model.invoke/ainvoke不同，异步等待经RunControl.await_model，非原生async模型沿run_blocking排空。每次调用使用独立消息对象/局部变量，关闭原模型回调继承，增加naiyue_permission_review标签，必要时同步主流过滤，避免审查内容进入主模型delta。请求预算使用两条模型已有context_window_tokens-max_output_tokens的较小值，按两阶段完整消息计数；保留当前调用及整份项目指令，按时间顺序逐步去掉最旧历史项，不够或无真实用户文本则Ask且不请求模型。审查回复必须是无工具调用、正常结束的AIMessage及严格JSON（重复键、额外字段、非法decision、空/超长reason拒绝）；安全日志仅模型ID/阶段/决定/固定原因/可得usage，无正文。

## 验证

先写失败测试：快审Allow零详审；快审有效Ask才详审；两次Ask人工暂停；任何阶段普通失败直接Ask；非法Deny不采纳；Deny/显式Allow零审查；输入排除普通正文/伪人类/Skill/摘要；AGENTS优先/CLAUDE回退、纯指令不混Skill索引、无指令字段为空、同run快详审同快照/下一run刷新、恢复旧批零重审、新批使用恢复run快照、项目约束不覆盖Deny/伪造人类来源、完整指令超预算Ask；当前参数保留且超预算Ask；模式下一批生效；并发会话隔离；停止/取消/清理；诊断无原文。之后完整后端/前端/build回归。用假模型与临时配置，不改真实配置或发真实模型请求。

## 实施入口、迁移与实际验证（2026-10-16）

没有新增生产模块或依赖；在原权限模块内扩展reviewer。实际主链如下：

1. `SessionService._run_steps`调用`prepare_run_resources`，取得`prompt/project_instructions/read_resource_roots`三个命名字段；同次读取同时服务主提示和审查器。`_mark_human_messages`转换入口消息并复制附加来源标记，不修改调用方对象；steering/follow-up在可选观察返回或降级后也走此方法。摘要标记不会升级为人类来源。旧checkpoint中未标记的历史不补盖章。
2. `PermissionMiddleware.prepare_local_batch`取当前活动工具和本批模式，完成参数→目标→Deny/Allow/session_allow/default，返回`(batch, project_instructions)`，不把项目正文写入tool_execution。`_pending_asks`处理NoAsk/Bypass，Auto只取有效剩余Ask。
3. `prepare_review_input`产生示例JSON；`AutoPermissionReviewer.review/areview`驱动同一个`_review_steps`。`_fit_input`按两个模型窗口扣除输出预算后的较小值计数，最多裁掉旧人类/历史调用，保留最新人类、完整当前调用和项目指令。`_decision`验证正常终止、无工具调用、严格JSON和reason长度；支持供应商独立thinking/reasoning内容块，但不投递给主消息。
4. 如快审有效Ask、详审Allow，call更新为`decision='allow', source='auto_detailed'`，只保存决定和来源。原Authorization发permit再执行；如详审Ask或任一普通失败，保存Ask后原BatchApproval暂停整批，批准恢复从原HITL继续，不重跑准备或Auto。模型reason只参与结果合同校验，不进入主消息或诊断日志。
5. `RunControl.await_model`管理异步等待；UserStop由权限hook调用共享`finish_stopped_batch`配对、发原工具事件并结束。外部CancelledError保持传播，沿原应用合同记录failed/task_cancelled及manual_review_required，并清理busy/run快照，不冒充人类拒绝。

新增四个测试文件：`test_auto_permission_review.py`、`test_auto_permission_pipeline.py`、`test_auto_permission_controls.py`、`test_auto_permission_persistence.py`，合计56个用例。初始测试在reviewer缺失时报ImportError红灯；实现后逐步验证。`test_project_skills.py`迁移资源命名字段；两份上下文历史夹具补齐稳定ID/来源标记，完整对象相等、配对、错误身份和不提交摘要断言均保留；运行事件替身改读转换后的HumanMessage.content。未修改conftest、放宽断言或skip。

最终命令和实际结果：

- `.venv/bin/python -m pytest -q`：**1323 passed in40.44s**。
- `npm test -- --run`（frontend）：**9文件、27 passed**。
- `npm run build`（frontend）：TypeScript检查与Vite **186模块**构建成功。
- `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`：退出0。
- `.venv/bin/python -m pip check`：`No broken requirements found.`。
- 237个Python文件AST解析且均不超过500行；当前设置样例经真实loader及七工具规则校验，未来完整样例仅校验TOML语法。

来源/预算/JSON/同步异步阶段分支、safe usage日志及诊断故障、四入口启动前模型ID校验、SQLite模式恢复、停止/取消、并发会话隔离和批准旧批零重审均为确定性本机证据。无真实供应商调用或Windows验证；未改真实模型/环境配置，未提交或push。前端仅同步类型，模式切换UI仍后排。下一片先讨论文件工具能力的准确范围，再拆写085-08，不自动实施总Spec余项。
