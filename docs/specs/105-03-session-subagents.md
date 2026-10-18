# Spec105-03：本会话子实例、审批与停止归属

日期：2026-10-18。状态：用户已确认并实施，验收通过，实际结果见第8节。依赖[105-02](105-02-session-execution.md)，本片保持一层、前台、批量审批及独立子路径，不增加并发额度或后台能力。

版本指引：本文保留本片实施时点；当前对象/入口及组合收尾见[105-05](105-05-session-lifecycle-acceptance.md)，State瞬时字段和独立观察/子实例Map已由ActiveRun替代，不按历史字段恢复旧实现。

## 1. 当前与目标

改前根执行已归AgentSession，但Service._subagent_instances[(sid,path)]拥有所有子实例；根执行的临时subagents参数调用服务的创建/停止/settle等方法。目标现已落地：A._subagent_instances[path]唯一拥有A的子实例、driver、control、live审批和future；服务保留共享角色定义/图、注册和SessionPort路由。

本片预计7个生产文件、相应十余个测试与1个已有供应商探针消费者，超过8文件。先保存本片基线，保留此前脏树；不提交push，不改真实配置/数据库/依赖，不发供应商请求。

## 2. 角色协作：复用已有两个行为

AgentSession在现有构造接线中借用invoke_subagent=Service.ainvoke_subagent和registered_subagents=Service.get_registered_subagents，并接已有progress/result设置。前者是当前RunServices实际使用的回调：服务只在登记锁内解析目标会话和共享role={spec,agent}，锁外await A.ainvoke_subagent(...,role=role)。A创建、执行与收尾自己的实例。后者仅用于现有compact的角色描述读取。

这两个是已有实际调用方，不新增resolver注册表、回调容器或完整Service引用。比较直接传参：把role提前传入根运行会丢失本轮之后动态注册的角色，因此仍在真正调用时解析；把整服务放入对象会扩大私有穿透，故复用这两个窄行为。角色与图不复制到会话Map；借用函数不改变共享资源关闭归属。

## 3. 方法及文件

| 文件 | 变更 |
|---|---|
| agent_session.py | 混入AgentSessionSubagents/AgentSessionApproval，创建本会话实例Map，借用上述两个既有行为及设置；根查询扩展为按path取冻结模型/活动工具/权限事实；提供child_thread_ids |
| session_subagents.py | Service部分保留注册、角色列表及ainvoke路由；新内部AgentSessionSubagents接prepare_subagent_instance/ainvoke_subagent/_subagent_steps/settle_subagent_drivers/assert_subagents_settled。key=(sid,path)改path，保留instance内原身份 |
| session_approval.py | AgentSessionApproval接pending批次、live审批注册/校验/提交/释放；Service部分保留按sid路由。纯解析/通知/grants算法不改 |
| session_controls.py | create/restore注入借用函数及设置；模型/工具/权限/子线程路由归A；根/子scope停止移至A，在同一状态锁内校验并选目标，锁外request/flush/唤醒；服务删除prepare_stop协调及全局实例访问 |
| session_service.py | 删除全局实例Map及subagent_instance旧可变读取；resolve_model路由A；根执行/compact/恢复不再传临时subagents参数 |
| session_execution.py | 使用已有借用invoke回调/角色列表；失败和finish直接请求本对象scope停止、settle/assert；删除临时协作参数 |
| session_state.py | 仅补准确生命周期/持久化边界说明，字段布局保持；ActiveRun见第7节 |

AgentSession.prepare_subagent_instance(agent,task,*,role,parent_context,parent_state,parent_tool_call_id)保留原返回(graph,instance)，不重新解析角色。Service.ainvoke_subagent公开调用签名保持，A.ainvoke_subagent多接内部已解析role。实例可变数据只由所属对象及其内部driver使用；外部探针改用有限的子状态快照，不新增通用get_session/get_state。

## 4. 锁与身份

固定服务登记锁→会话短状态锁，角色解析后退出登记锁再调用对象。A内子Map、父run、停止与审批使用同一短锁，不在持锁时反调服务。prepare先校验parent_context的sid等于self.session_id，再验证父工具permit；锁内检查父run活动/stop、角色、重复path并安装实例。父工具调用和root_run/path/批次绑定不变。

控制prepare_stop与原服务的目标选择合成同一对象request_scope_stop，防止嵌套获取非重入锁。审批validate_approval_submission为本对象锁内辅助，不再反调获取同一锁的snapshot/remember方法；已校验的rules在当前锁内去重并deepcopy写session_allow。future.set_result/cancel与控制request、外部flush、IO、await driver均在状态锁外。

## 5. 正常与失败链

```text
根A._arun → RunServices的既有invoke回调
  → Service只解析A和共享角色 → A.ainvoke_subagent(role)
  → A.prepare_subagent_instance → A的Map[path]
  → A._subagent_steps → 共享角色图(child_thread)
  → 待批时A.register_live_approval保存该实例future → 主会话通知
用户提交 → Service按sid路由A → A同锁校验父run/本实例/批次/决定
  → 锁外唤醒future → 原子批次恢复 → 摘要/原artifact返回根图
根finish → A.settle_subagent_drivers → A.assert_subagents_settled → 原必要保存
```

子审批不新建根run，不重跑Auto，父busy保持；兄弟独立审批/完成保持。子任务普通失败沿工具错误路径返回根模型，不因单子失败停止无关兄弟。根主异常/应用关闭时A.request_scope_stop只选本会话当前root_run；等待其driver、清理审批再必要保存，原异常和次异常优先级保持。未知角色不创建实例；伪sid、旧run/批次、重复启动和重复决定拒绝；模型/工具副作用不重放。

## 6. 测试与迁移

先红灯：根运行期间实例只在A且key为path、两会话同角色隔离；父sid伪造拒绝；子审批直接属于A且提交不改变B；scope停止/settle和旧run保护；共享角色可在会话创建后注册，模型/图仍共享。保留已有根/子事件、scope、停止、故障、SQLite重开、硬退出与Web/CLI审批断言。

旧测试record_instances/approval_notifications中的故障注入改到AgentSession对应类方法（方法代码仍只一份），此为拥有者迁移必需，不放宽断言。旧Map读取改本对象；清理空表断言保持所有已装载会话都为空。provider_acceptance_agents只迁有限状态快照读取，不运行真实请求。删除已无生产调用的Service子实例/driver/approval内部方法，不保留测试兼容。

验证：定向tests/test_agent_session_subagents.py及test_subagent_*.py、test_agent_session_execution.py、test_session_state.py；完整PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q；frontend内npm test -- --run及npm run build；compileall/pip check/500行/文档链接/diff，架构和安全复核。公开API/SessionPort/HTTP/SSE/SQLite schema8与数据格式不变，无数据迁移。

## 7. ActiveRun建议评估（本片只记录）

建议按运行寿命收拢busy/run_id、冻结资源、窗口/停止、Steer/Follow-up队列和当前观察是有价值的，可减少瞬时状态的重复复位，并让busy从对象存在派生。建议放在105-04开始前细化合同，再单独确认实施，不混进本次子实例迁移；104仍通过is_busy读准入事实，不依赖内部存放位置。

当前“第一、二组都要直接持久化”并不成立：session_allow、pending_turn没有直接写入会话元数据；待审批从checkpoint重建，manual_review映射存储state。现行显式映射已避免整包写入瞬时字段，不能将本次建议描述为修复已存在的误持久化。是否持久化本会话Allow不随此次拆分改变。

ActiveRun若实施，必须保留关闭控制窗口与保存后释放两个时点：前者要快照pending_turn、关闭观察、丢弃队列并记录事件，后者才可_run=None。仅用一句赋None不能替代这些行为；旧run身份检查、失败/取消保存保护也须保留。后续需明确子实例是否随run拥有，以及必要保存如何取得已冻结的收尾事实；本片不创建ActiveRun、不迁移字段或更改持久化语义。

参考源码核对：本地Pi agent.ts的runWithLifecycle创建activeRun，finishRun清空它，但isStreaming/streamingMessage/pendingToolCalls仍位于_state，Steer/Follow-up队列仍独立；它没有将上述全部字段打包成同一个ActiveRun。AgentX的ConversationAppService.chat创建ChatContext再调用handler，ChatSessionManager保存带emitter/interrupted的SessionInfo，它关联交付，不等于本项目保存后释放的根运行。因此本建议以本项目生命周期/维护收益判断，不以“参考项目都如此”为依据。

## 8. 实际实现与验收（2026-10-18）

已修改7个生产文件、16个测试文件（含新建test_agent_session_subagents.py），及1个既有供应商验收探针消费者。AgentSession混入同实例的AgentSessionSubagents、AgentSessionApproval，唯一实例表以path为key。Service只保留共享角色注册/解析及按sid路由；原Service.prepare_subagent_instance、_subagent_steps、register_live_approval、release_live_approval、settle/assert及全局实例表删除，根执行临时subagents参数删除，无旧实现重导出。

新建和restore都注入已有invoke_subagent/registered_subagents两个行为及原progress/result设置；根RunServices继续只接原invoke回调，未注入会话/服务对象。会话创建后再登记角色的行为已用真实装配/假模型链验证，同角色图及checkpointer仍共享。现有provider_acceptance_agents改读subagent_status的有限复制快照并在轮询时重新取值；未运行真实探针。

审批和停止在本会话锁内配对父run、path、pending与控制，锁外发送/唤醒。新增self.session_id检查及_require_subagent确保将B的实例交给A释放时拒绝，不取消外部future。跨会话批次提交、旧run停止均拒绝且保留正确会话活动状态。业务子失败/父主异常、取消与必要保存的优先级未改变。

实际正常链：A根图 → 原invoke回调 → Service取A和共享角色 → A.ainvoke_subagent/prepare → A的path实例 → A._subagent_steps/角色图 → A.register_live_approval → 主界面原通知。提交经Service路由A，在A锁内校验后锁外set_result，子恢复同批并回填父工具结果；根finish直接A.settle/assert → 原必要保存。

实际失败/停止链：伪造parent sid或permit在创建前拒绝；停止A只标记/唤醒A当前run的实例，B仍等待自己的审批，批准B后只产生B的工具效果。根异常先请求本会话停止并等待driver，再清审批/实例并必要保存；现有硬退出/SQLite重开/控制异常测试保持。有限状态/子thread查询不暴露future或可变实例。

- TDD红灯：.venv/bin/python -m pytest -q tests/test_agent_session_subagents.py --tb=short，4 failed in1.07s，均缺少计划迁移的对象方法/实例表，见/tmp/naiyue-10503-red.log；实施后4 passed in0.99s，增强foreign实例/旧run检查后4 passed in0.73s。
- 定向组合：.venv/bin/python -m pytest -q tests/test_agent_session_subagents.py tests/test_subagent_*.py tests/test_agent_session_execution.py tests/test_session_state.py tests/test_native_session_lifecycle.py tests/test_session_port.py --tb=short，170 passed in5.55s，见/tmp/naiyue-10503-focused.log。
- 完整后端：PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q，2439 passed in106.21s（0:01:46），见/tmp/naiyue-10503-backend.log。
- 前端：frontend内npm test -- --run，26 files/186 passed；npm run build，TypeScript成功，Vite210模块成功，见/tmp/naiyue-10503-frontend.log、/tmp/naiyue-10503-build.log。
- compileall/pip check成功；394份生产/脚本/测试Python AST均通过且不超500行，Runtime反向application导入为0。独立架构/安全复核没有确证的本片引入问题；主审补核新旧身份、父停止/兄弟待批、保存期busy、动态角色及回调锁方向。

两个既有测试观察helper（record_instances、approval_notifications）改为在当前AgentSession类方法上观察，按所属app过滤；这是拥有者迁移所必需，未改conftest/公共基础设施，未删行为断言、放宽或skip。旧105-02临时协作者测试改在对象实际停止/settle入口注入故障，保留原顺序及异常身份断言。

12份变更文档及README合计680个本地链接目标、88个标题锚点检查通过，git diff --check通过。上述为本机确定性/假模型/临时SQLite和真实退出进程证据，不是供应商业务或Windows原机验收。未改用户配置、用户数据库、依赖或前端行为，未提交push。

## 9. 下一步：105-04方案与ActiveRun细化

105-04原目标仍为持久化/新建装载/恢复/移除与关闭接线，保留独立存储IO/事务、显式快照和原092/101恢复链，不引入新存储方案。先展示当前实际私有接线及待改消费者，再细化第7节建议：若采用ActiveRun，应将瞬时字段和观察收拢、busy派生于占用存在，关闭控制窗口时保留运行占用直至必要保存完成；永久事实/待批重建来源保持原语义。该新增状态布局需用户确认，尚未创建ActiveRun或改变105-04生产代码。最后105-05组合验收，再104并发；默认10的既定方向不变。
