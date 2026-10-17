# Spec097：输入框指令、手动压缩与模型/推理强度

用户2026-10-17授权实施。沿用096页面、异步App及官方checkpoint，不新增依赖、工具或执行循环。

版本边界：以下“加号只保留压缩”与“Skills后排”为097完成时快照；后续[Spec098](098-skills-picker-and-explicit-command.md)已增加加号技能项及`/skills`，其余已存在按钮仍不重复。

后续[Spec099](099-session-sidebar-management.md)将模型按钮改为桌面/窄屏均显示模型名与推理强度，删除指针图标和展开箭头；SQLite升级schema6并迁移现行4/5。下文窄屏图标及schema5为097历史实现边界。

## 1. 范围与参考

模式左侧增加加号。用户同轮补充：加号不重复其他按钮已有功能，因此菜单首版只保留“压缩上下文 /compact”；上下文、模型/推理、工具、权限及子智能体沿各自独立入口。已接入的文字快捷指令`/compact`、`/context`、`/model`、`/tools`、`/permissions`、`/agents`保留，但不在加号内重复。首版指令无参数，未知指令或多余参数提示且保留草稿。普通消息、Steering/Follow-up走原入口。Skills查看仍独立后排。

模型入口移至上下文和发送键之间，删除顶部重复选择器；窄屏用图标，菜单展示完整名称。模式不再显示圆圈。向上弹出，Escape关闭并归还焦点，切换会话关闭。

Pi当前`AgentSession.setThinkingLevel`从可用档位选择并记录会话变更；`compact`调用独立摘要后替换上下文。Pi手动压缩先abort；本项目为保留当前审批/停止合同，空闲才接收，不自动停止运行。AgentX当前Java源码未发现对应compact/thinking入口，不虚构等价支持。当前LangChain模型是Pydantic对象，可以浅复制适配器设置并共用已有SDK资源；不修改共享模型原对象，不重新建客户端。

## 2. 数据与接口

- `GET /api/models`保留现有字段，新增`thinking_options`：按model_id提供`default_level`（解析后的配置默认值）和`supported_levels`。未配置thinking时只有“配置默认”。不根据模型名字猜能力，不泄露提供商配置。
- Session元数据新增`thinking_level`，默认`model_default`。`PATCH /sessions/{id}/thinking`严格接收`{model_id, thinking_level}`；同时核对当前model_id防止过期菜单误改。只接受支持档位或model_default。选择模型原接口不变，原子重置thinking_level。根作用域使用覆盖值，子角色及审批模型仍用自己的配置默认。
- SQLite schema4→5仅新增上述非空默认列；现有4是当前消费者，保存当前会话，不恢复已删除的1/2/3兼容。新增库直接5。读取、恢复、历史及Web类型同步。真实用户库本次不打开；迁移在临时SQLite测试。
- `POST /sessions/{id}/compact`接收独立`request_id`，返回原run receipt；复用RunDelivery的锁、幂等、SSE、断线不取消及停止。请求指纹加入操作区分，压缩/普通消息不可复用同一request_id。运行mode扩展`compact`，context trigger扩展`manual`。

## 3. 正常调用链

`Composer`加号选择“压缩上下文”或输入`/compact` → 同一`useConversation.compact` → WebApi.compact → 认证后的WebService.compact，确认工作区/恢复及can_run → RunDelivery以compact模式启动原交付任务 → AsyncApplication.acompact_context登记task → SessionService._run_steps(compact=True)。

SessionService复用busy锁、RunObservation、资源准备、持久化start/finish及清理；compact期间steering关闭，Follow-up亦不接收。应用session_compaction组装当前根模型、prompt_parts、活动工具，传给Runtime的手动压缩入口。Runtime只读已完成checkpoint，验证无next/interrupt/error及完整工具配对；调用原_compaction_steps(force=True, trigger=manual)。保留最近完整用户turn，历史不足返回skipped，不发摘要。摘要只有通过格式/预算/缩减校验才产生更新。

通过后两次官方更新：`aupdate_state(snapshot.config, update, as_node='model')`使用既有消息writer写入完整替换，再`aupdate_state(written, None, as_node=END)`结束任务。当前框架只有values=None时支持END，初版把values与END合并的测试实际失败；INPUT只更新输入channel，不能直接替换messages，亦已排除。临时内存图探针实际验证model writer→END得到summary/保留消息且next/tasks为空，不执行model函数、不调用模型。没有ainvoke，没有新工具/普通AI响应。读回checkpoint核对summary/保留ID及无next，沿原MessageStateObserver投递移除/完成事件，ContextOperationObserver确认applied；应用必要保存完成后释放busy并发run_finished。空历史在检查停止前用END建立空checkpoint，避免开始即停止变成保存未确认；不调用模型。最新上下文GET仍独立只读，压缩完成后刷新。菜单触发保留已有草稿，文字指令仅在收到启动receipt后清除且不得覆盖后来编辑。

模型选择：菜单选择B→原aselect_model→同一选择锁保存`model_id=B,thinking_level=model_default`→UI确认。选high→带B提交新PATCH→校验B支持high→先写DB再改会话值。下次SessionService.resolve_model同时取模型ID/档位，以Catalog浅副本设置供应商参数；旧请求持有旧对象，不受后续选择改变。切回配置默认直接返回原模型。Anthropic覆盖清除相互冲突的thinking/reasoning_effort字段，Ollama使用reasoning，OpenAI使用reasoning_effort。

## 4. 失败与停止

- 忙碌、审批/恢复或人工核对状态：拒绝compact，保留草稿和旧审批，不排队、不执行任何模型/工具。
- 摘要失败或无安全切点：原上下文不写入，compaction_finished标failed或skipped。手动操作返回显式compaction_status，run正常收尾表示请求已处理，UI必须显示压缩未成功，不能报已压缩；下一轮可继续使用原上下文。
- 替换/读回/必要元数据保存失败：异常上抛，不能按普通摘要失败降级，不宣称applied或ready。持久化阻断沿原合同。
- 用户停止摘要：RunControl沿原异步/线程settle边界，结果run_stopped，不写尚未提交的摘要。提交阶段按必要IO收尾，不以取消伪报回滚。
- 选择失败：保留已确认值，重读当前配置；会话/模型切换期间旧响应不得覆盖新选择。未知level及过期model_id返回固定错误，无提供商错误正文。

## 5. 文件与职责

改动超过8个文件，分三组顺序实施并验证：

1. model_access/__init__.py及thinking.py：保存支持档位，返回公开元信息，构造隔离覆盖；SessionService/SessionControlMixin及AsyncApplication添加会话设置；session_storage/persistence/recovery同步保存恢复。模型选择函数可移至已有controls mixin以维持500行，不新增平行入口。
2. Runtime context_compaction.py共享trigger/手动切点，新增manual_compaction.py负责官方状态替换与核对；应用新增session_compaction.py负责组装本次输入，SessionService原生命周期增加compact分支；contracts/session_events及context_events扩展有限枚举。Web schemas/app/service/run_delivery接入口、操作幂等及交付。
3. 前端types/api/useConversation保存选择并启动compact；ComposerControls受控菜单并增加加号；Composer统一文本指令和菜单分发；新增ModelMenu组件/CSS，与原context入口保持顺序；App移除顶部选择器。新增有限commands定义复用文字/菜单项，不建设插件命令平台。

README/web-tools/handoff/future-work同步当前入口；不修改真实配置、依赖、关键知识点或课堂掌握记录。

## 6. 验证

先失败测试再实现：供应商映射及共享对象不变/非法档位/模型切换重置/临时SQLite迁移与重开；手动压缩只摘要一次、保留最后turn及ID、无普通AI/工具执行、摘要失败保留原文、太小跳过、忙/审批拒绝、停止不写及保存失败不报成功；Web认证/工作区、幂等操作隔离；前端位置/菜单/文字指令、草稿、档位能力、失败/过期响应、键盘关闭与模型切换。

全量Python pytest、前端vitest、TypeScript/Vite build、diff及≤500行检查。用临时SQLite/确定性模型实际构建页面查看桌面与375px、真实HTTP压缩与模型/档位选择；无真实供应商请求，不把离线验证称为供应商能力验证。

## 7. 实施记录与边界

- 主链已接上述类/函数，新增Runtime/manual_compaction.py、application/session_compaction.py及前端ModelMenu/commands；没有新工具、新依赖或平行执行器。删除顶部模型select及无消费者CSS。会话字段、SQLite、恢复、认证路由、交付与前端类型一起更新。
- 模型覆盖通过四种实际适配器对象的离线测试，Anthropic懒SDK属性在浅复制前实化以共用客户端，清除互斥thinking/effort。声明支持档位来自启动配置；不代表实网供应商能力已验收。
- 压缩例子：旧消息`H1,A1,H2,A2`→模型输入`Summary,H2,A2`，只有摘要调用；当前页面仍沿原投影保留可读旧对话。关闭服务后的恢复沿既有checkpoint，只保证保存后的上下文，不新增独立完整轨迹库。既有摘要仍限受支持的文本内容，多模态摘要未在本片扩展。
- 架构复核发现manual继承split-turn回退，与保留末轮矛盾；新增小窗口用例先红，现手动只选Human边界、完整末轮放不下则在调用摘要前失败，自动threshold/overflow保持。另用deferred GET先红复现失败读回被旧刷新覆盖，确认读回也递增版本后转绿，模型与档位配对不被覆盖。
- 停止摘要不写新状态，新的空会话开始即停止也能保存ready；对首次写入和第二次END分别注入失败，均不报applied，设置人工核对并禁止继续。待审批拒绝compact且原批次与消息不变。
- 当前浏览器验收只使用临时工作区/SQLite、真实Read和确定性模型，截图位于`/tmp/naiyue-097-ui/`：desktop-commands-final.png、desktop-model-menu-final.png、desktop-after-compact.png、mobile-commands-final.png、mobile-model-menu-final.png。加号最终只有compact，窄屏模型菜单完整在屏内；实际切强度high、切模型重置默认、输入/compact后估算下降均已观察。真实模型、用户配置/数据库、Windows/Linux原机未参与。

## 8. 最终验证结果

2026-10-17当前会话实际执行：

- `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q`：**2128 passed in85.55s**，记录`/tmp/naiyue-097-backend-final-complete.log`。
- frontend目录`npm test -- --run`：**16文件79 passed**，记录`/tmp/naiyue-097-frontend-complete.log`。
- frontend目录`npm run build`：TypeScript零错误，Vite **198模块**，记录`/tmp/naiyue-097-build-complete.log`。
- `compileall`、`pip check`、`git diff --check`通过；338 Python AST、44前端源文件≤500行检查通过。
- 架构复核两项发现已用对应失败测试闭合，安全复核没有未解决发现。空会话开始即停止的新增回归也先红后绿；13项新增后端及6项新增前端验证包含于最终全量。不修改全局测试基础设施；旧顶部模型入口的UI断言迁至新按钮，认证响应断言新增公开thinking_options，原保护断言保留。
- 临时离线服务关闭后以同一SQLite重开，无新增模型调用即可读取压缩摘要、最近完整轮和模型B/配置默认。实时阅读历史与模型上下文的原差异保持，不宣称新建完整轨迹存储或供应商计费验证。未提交/push。
