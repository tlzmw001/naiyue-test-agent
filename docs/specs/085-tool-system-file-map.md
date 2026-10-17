# Spec085 附录：文件、函数与迁移消费者

日期：2026-10-15，2026-10-16分片更新。属于[总 Spec085](085-tool-system-rebuild.md)，本表是最终目标清单。085-01/02/03A/03B/04/05/06/07/08及[085-09 Write/Ls](085-09-write-parents-and-ls-feedback.md)、[085-10 Edit](085-10-edit-matching-and-feedback.md)及[085-11图片](085-11-read-images.md)及[085-12共享进程](085-12-process-lifecycle.md)及[085-13搜索](085-13-search-tools.md)、[085-14双Shell](085-14-dual-shell.md)已实施。主文讲合同和调用过程，本附录供逐项勾验，不是另一套方案。

实施从[085-01统一错误](085-01-unified-tool-errors.md)开始，先改行为、不搬目录；再做第1片内部的目录/依赖、注册/设置小步，之后按总Spec第2至7片推进。下表是最终目标清单，不等于085-01一次修改全部文件；当前准确14个生产文件及测试范围见该小Spec。

已完成02目录/依赖、03设置、04注册和05动态集合；06接ToolPermission/FilePermission、保存决定/授权、模式与schema v3，旧审批metadata/when/review/grant_factory已删除。06实际拆出`tools/permission_rules.py`供启动/动态注册/批次/候选四个消费者共用，`tools/paths.py`负责真实目标，`application/session_approval.py`负责保存对象到审批/grant的投影；新增原因及完整文件清单见06。07已接Auto，详见小Spec；下表仍含文件高级能力和进程等最终目标，不能当作全部实施授权。

下列路径相对仓库根目录。新函数名是拟定接口，旧函数名来自当前源码；实现发现更合适的已有入口应先同步本表。所有生产文件仍须≤500行，不能只靠下列文件“存在”就验收，还须有实质实现和真实调用。

08实际只改tools/read.py与paths.py，新增两份测试，无新生产模块/类；巨行/UTF-8前缀/续读反馈、权限前有限文件名变体与执行禁用重选已接。后续小片建议09 Write/Ls、10 Edit、11图片、12共用进程、13搜索、14双Shell、15终检/WIN1；[09](085-09-write-parents-and-ls-feedback.md)已实施，仅改write/ls并复用原subject/控制/跳过报告；[10 Edit](085-10-edit-matching-and-feedback.md)已实施，新增edit_matching/edit_diff两模块，精确扫描/提交继续在edit.py，复制时生成有界反馈，五额度和原生artifact已接。[11图片](085-11-read-images.md)已实施，新增images/messages两个模块、ReadImageSettings、显式api/profile、标准图片历史/请求载体、摘要及Web文字投影，模型样例与现有合法测试已迁移。[12共享进程](085-12-process-lifecycle.md)已实施，新增processes/process_protocol、ProcessSettings与Bash实际接线，删除旧Popen/读线程/Unknown和名字保护分支。[13搜索](085-13-search-tools.md)已实施，新增search_protocol、Grep/Find设置及实际rg/fd/严格schema接线；删除旧发现/预读/扫描阈值，复核kind并处理嵌套取消，旧同步薄转发删除。[14双Shell](085-14-dual-shell.md)已接公共ShellAdapter/环境、PowerShell、OutputAccumulator与共享归档预算；默认六文件活动集合，新增shell/powershell/output_text三个生产模块。[15本机终检/Windows复盘](085-15-final-audit-windows.md)已完成，报告列实际保留/删除项及平台阻点；全量1597/前端27及构建通过。当前Windows安全文件/锁、覆盖/Edit与命名空间授权仍待修，WIN1原机验收未完成，下一步先讨论安全文件/锁修复。

## A. 独立工具包：从运行层迁出并按职责扩展

| 目标文件 | 来源/动作 | 主要类、函数与真实调用方 |
|---|---|---|
| `naiyue_test_agent/tools/__init__.py` | 迁移旧tools公开集合并收口新接入合同 | 导出ToolSpec/ToolRegistry、内置集合与会话工具工厂；旧runtime路径不留转发包 |
| `tools/registry.py` | 04已新增ToolSpec/Registry；其余字段随消费者接入 | ToolSpec、ToolRegistry.register/get/select、validate_input；校验side_effects三值及unknown缺省、原生content/content_and_artifact格式；App登记、ToolSet、权限/批次读取；无execute分发器 |
| `tools/settings.py` | 八工具、图片、Process及共享归档额度已接 | ToolSettings/load_tool_settings；启动与登记校验，模型schema/描述/执行复用同值 |
| `tools/permissions.py` | 替代runtime/file_permissions与旧ApprovalPolicy描述 | ToolPermission、FilePermission、PermissionSubject、PermissionRule、ExecutionPermit、OneUseApprovalGrants；审查对象/许可含execution_environment；prepare/default_decision、match_rules、require_file_target；工具与runtime共同消费，不能反向导入runtime |
| `tools/paths.py` | 从read/write抽出已有重复路径职责并扩展 | resolve_workspace_root、prepare_file_subject、verify_file_target；真实路径/父级/链接、资源范围；Read/Write/Edit及搜索共同消费 |
| `tools/schemas.py` | 03B已新增并接线 | 四个真实工具工厂复用公开/执行Pydantic派生和runtime注入；工具保留具体字段定义，Read原私有构造迁入，不生成BaseTool副本 |
| `tools/builtin.py` | 04已新增；05取消专用开关，14已登记双Shell | 当前create_builtin_tools(settings)登记八项，活动集合统一选择Bash/PowerShell；不是插件扫描器 |
| `tools/read.py` | 移动read.py并改造 | create_session_read_tool、_read、_decode_read_text；分页/长行/图像入口，具体原因仍在工具；去safe白名单与重复权限解析 |
| `tools/images.py` | 新增 | read_image/prepare_image_blocks；Pillow识别、方向/尺寸/格式、预算，只有Read调用；不决定模型接口 |
| `tools/ls.py` | 移动ls.py | create_session_ls_tool、_list_directory；同一层目录与名称权限，不复用正文扫描链 |
| `tools/find.py` | 13已迁移fd | create_session_find_tool/_find_files；native glob/smart case、文件目录、NUL流、过滤后计数和有限子集排序；旧rg/Python glob删除 |
| `tools/grep.py` | 13已迁移分组rg | create_session_grep_tool/_grep_files/_GrepSearch；候选先授权、原生glob/JSONL、受管分组/取消、输出复核；删除旧预读/逐文件进程和扫描阈值 |
| `tools/write.py` | 移动write.py并扩展真实目标 | create_session_write_tool、_write、_reserve_target、_replace_temp；保留busy/提交/清理，父目录获准后创建 |
| `tools/edit.py` | 移动edit.py并扩展 | create_session_edit_tool、_edit、_scan_target、_copy_to_temp；协调原快照/精确匹配、匹配计划、原子提交与反馈 |
| `tools/edit_matching.py` | 新增 | prepare_edit_plan、scan_normalized_matches；有限Pi归一化/源坐标、唯一/重叠、有界扫描；Edit执行/获准预览复用计划 |
| `tools/edit_diff.py` | 新增 | build_edit_feedback；firstChangedLine、局部diff、预算内完整patch、预览；不另读未授权源文件 |
| `tools/bash.py` | 移动并替换Popen实现 | create_session_bash_tool；Bash语法与提示、构造请求/反馈；不保留专用审批grant/两采集线程 |
| `tools/powershell.py` | 新增 | create_session_powershell_tool；5.1/7发现、启动/编码、退出码原语义；与Bash保持两个名字 |
| `tools/shell.py` | 新增，共用双Shell实际流程 | ShellAdapter.resolve_executable/probe_version/build_request/format_outcome、build_environment/build_shell_tool；精确命令、继承环境、超时/停止反馈；不翻译脚本、不持久cwd |
| `tools/processes.py` | 新增，共用四类当前进程消费者 | ProcessRequest/ProcessOutcome/ProcessCleanupError、HostProcessRunner.run、平台终止/回收；异步主实现和同步调用桥接 |
| `tools/process_protocol.py` | 新增，与生命周期算法分工 | 基于公开SubprocessProtocol的数据/exit/pipe事件和有界队列；进程服务唯一直接消费者，避免processes.py超过500行 |
| `tools/search_protocol.py` | 13已新增并接线 | NUL/JSONL RecordBuffer、DiagnosticTail、SkippedTargets、stream_records，三个实际流消费者；不做权限决策或复制进程收尾 |
| `tools/output_text.py` | 14新增，当前双Shell消费者 | OutputAccumulator；增量解码、ANSI/控制/换行规范化、有界行字节尾部与实际范围 |
| `tools/output.py` | 迁移bash_output.py并扩展 | ShellOutputArchive、原始归档/条件文本视图、共享额度；保留preview_shell_outputs/clear_shell_outputs及旧受管文件格式识别的真实消费者，不自动删旧文件 |

表内 `tools/...` 均指 `naiyue_test_agent/tools/...`。文件IO本身不套新Operations接口。同步工具调用共享异步进程算法的薄入口，已有真实同步SDK路径仍可用；已有事件循环中不阻塞嵌套asyncio.run，异步正式路径直接await。原生文件IO仍可用run_blocking/await_settled确认已开始任务结束，不把所有同步函数判为冗余。

八个会话工具工厂统一改为接受对应设置并返回ToolSpec；BaseTool是其tool字段，不保留返回旧裸工具的平行工厂。CLI/Web/脚本/测试同步迁移到该明确合同。

副作用注解已确认：read/ls/find/grep的ToolSpec填read_only，write/edit/bash/powershell填may_mutate；自定义未填缺省unknown。权限prepare_local_batch从可信定义附加到PermissionSubject/PreparedToolCall并保存，Auto使用同一快照；不从模型args接受该字段，不影响已有串并行或直接放行。

执行环境已确认：应用通过现有ToolRuntime.context提供真实执行事实，首版execution_environment为kind=host及platform=windows/macos/linux。prepare_local_batch附加并保存，Auto/审批说明读取，authorization及一次性许可绑定/核对；缺失/改变不沿用旧授权，不静默降级宿主。不新增环境注册模块，未来隔离实例与限制随实际沙箱后端设计。

artifact已确认：Edit/自定义工具需要时使用原生content_and_artifact，JSON详情随ToolMessage/checkpoint保留；模型必需信息仍在content，事件/Web当前不展示artifact，前端详情后排。Edit反馈生产者生成有界diff/patch/首个修改行，不新增通用结果类或永久产物平台，不改变ToolError失败链。

## B. 运行层：只负责图、许可消费、调度和观察

| 文件 | 动作与函数变化 | 调用方/迁移约束 |
|---|---|---|
| `agent_runtime/tool_execution/__init__.py` | 新增职责包入口 | 只导出本组真实装配项，不再把工具模块挂回运行层 |
| `tool_execution/toolset.py` | 新ToolSetMiddleware，prepare_tool_view/compose_tool_prompt，before_agent/before_model与wrap_model/tool | 在第一次摘要前准备首个集合，后续模型请求准备新集合；模型/预算/权限/审批恢复共用，不做工具副本 |
| `tool_execution/permissions.py` | 新PermissionMiddleware、prepare_local_batch；AutoPermissionReviewer.review/areview | 固定after_model节点；完整规则/模型审查一次，写ToolExecutionState；RunControl处理审查取消 |
| `tool_execution/approval.py` | 05已接BatchApprovalMiddleware，后片再换保存的权限结果 | 当前按保存名称创建局部官方HITL，保留原审批观察；不改共享interrupt_on；when/review重复判断尚未消除 |
| `tool_execution/feedback.py` | 新tool_error_feedback | 直接传官方ToolErrorMiddleware，具体ToolException/普通Exception/机制放行；一份同步回调供两路径 |
| `tool_execution/authorization.py` | 新ExecutionAuthorizationMiddleware | 许可与当前调用绑定检查、Deny/NoAsk消息、通用人工grant消费；文件工具另显式verify真实目标 |
| `tool_execution/batch.py` | 已移动，04模式已改读Spec；动态批次后续 | ToolBatchMiddleware/_SequentialPlan/_registered_pending；读取本批ToolSpec；修拒绝唯一serial后同步planNone误判 |
| `tool_execution/async_batch.py` | 移动async_tool_batch.py | AsyncBatchPlan原协调算法复用；并发上限/停止/失败配对不减断言 |
| `tool_execution/observation.py` | 从call_observation.py移入工具相关部分 | _prepare_tool_call、ToolCallObservation、tool_guard_reason、observe/aobserve_tool_dispatch；事件/日志复用现有publisher |
| `agent_runtime/__init__.py` | 改build_runtime_agent/_build_runtime_agent/_assemble_interactive_runtime及build_configured_runtime_agent | tools改tool_registry及两项显式会话读取；固定适配器、create_agent(tools=[])；删静态approval_rules与tools捕获 |
| `agent_runtime/call_observation.py` | 保留选模/模型观察，迁出上述工具/HITL部分 | 现有公共安全小函数按实际消费者保持或同步改导入；不建第二套事件投递 |
| `agent_runtime/run_services.py` | 扩展已有RunServices | 传工具服务/注册表读取与许可所需端口；保留真实ContextVar作用域，不新增全局可变会话状态 |
| `agent_runtime/invocation_context.py` | 改make_invocation_context | 将本run工具/停止/进程依赖放入ToolRuntime.context；保留显式跨线程上下文 |
| `agent_runtime/resource_invocation.py` | 接同一上下文，保留资源准备/settle | Skill/AGENTS与文件资源读取规则同主许可语义对齐，尤其真实路径/外部链接 |
| `agent_runtime/async_invocation.py` | 对齐运行上下文/工具state | 不新增主循环，不改values/updates/必要清理协议 |
| `agent_runtime/active_stop.py` | 工具观察导入；提取finish_stopped_batch供StopBoundary与权限hook共用 | 保留StopModel/StopBoundary、未执行消息配对；Auto子调用停止不能绕过run终局 |
| `agent_runtime/run_control.py` | 复用await_model与requested；按实际需要扩展命名中立等待入口 | 工具获得停止检查/信号，不反向import运行层；UserStop身份不变 |
| `agent_runtime/context_compaction.py` | 改_arguments/_validate_removed_prefix/_serialize_text_history | 读同一当前工具集合/prompt，支持标准内容块；摘要算法/切点/回退复用 |
| `agent_runtime/context_recovery.py` | 改_begin_recovery/_retry_request的输入来源 | 保留canonical，provider图片副本不作为持久替换输入；本轮工具集合不变 |
| `agent_runtime/context_observation.py` | 请求预算与图片成本 | 最终生效schema/文本/图片可观察，复用现有安全字段，不记录图片base64 |
| `agent_runtime/errors.py` | 删Write/BashUnknown类型依赖，保护ProcessCleanupError | 失败分类用于诊断，不能让观察/分类器决定重试或授权 |

表中 `tool_execution/...` 指 `naiyue_test_agent/agent_runtime/tool_execution/...`，其他 `agent_runtime/...` 也在同一项目包下。ToolExecutionState并入现有互动state扩展；简单JSON数据定义放permissions.py或toolset.py对应拥有处，不为每个字段再建一个types.py。

085-07项目指令补充（2026-10-16用户确认并实施）：`application/skills.py.prepare_run_resources`保留纯AGENTS/CLAUDE指令与组合主提示的独立命名字段；`session_service.py`正常/恢复run保存两种现有用途快照，`session_controls.py.resolve_permission_context`额外返回纯项目指令，`tool_execution/permissions.py.prepare_review_input`读取并单独提供。会话创建、restore和sync/async清理同步新增字段；资源入口测试迁移保留原行为，新审查测试覆盖优先级/同run冻结/下一run刷新/不混Skill/预算/旧批不重审。不新增指令loader、查询回调或管理器，现有`load_workspace_instructions`仍是唯一文件选择与校验入口。

## C. 模型、应用、入口与存储

03A已新增根层`config_paths.py::get_agent_dir/get_models_path`，给模型加载、工具配置、CLI/Web和脚本统一默认路径；无LangChain或上层依赖，不自动迁移用户文件。CLI/Web模型路径已可选覆盖；下一片不再新增路径参数。

| 文件 | 拟修改/新增内容 |
|---|---|
| `model_access/__init__.py` | api/profile.image_inputs解析/模型构造/元数据；保留模型目录凭据文件检查及SDK生命周期 |
| `model_access/messages.py`（新） | prepare_model_messages与薄ModelInputMiddleware；标准图块/Chat Completions载体/未知能力提示，只改请求副本 |
| `application/__init__.py`（已有装配） | 04已实现prepare_tool_registry，多种装配复用；设置加载与builtin已有真实入口，后片权限校验在实际消费者接入，不另建tool_setup转发模块 |
| `application/session_controls.py`（已有） | 05已扩展SessionControlMixin承载活动工具；模式及会话Allow后片；resolve_active_tool_names/resolve_permission_context供固定适配器读取，不新增单独控制对象或无消费者的session_tools.py |
| `application/session_approval.py`（新） | 从session_service移出pending解析/公开审批/prepare_resume；保存事实与官方action核对，取消重新classify |
| `application/session_service.py` | __init__/_create_session/describe_session/_run_steps/_resume_steps；会话字段、服务传递、旧Unknown特判删掉；保留busy/部分工具保护 |
| `application/__init__.py` | build_local_application/_assemble_application，注册表与新会话控制委托 |
| `application/async_application.py` | build_async_local_application及异步控制委托，沿同一SessionService合同 |
| `application/persistent.py`、`async_persistent.py` | 同一工具装配/恢复校验，资源拥有与关闭原合同保留 |
| `application/cli_async.py` | 可信登记/会话控制薄委托，不删仍服务CLI的线程/Runner facade |
| `application/session_storage.py` | 05已接schema v2活动JSON列及v1事务迁移，模式列后片；参数化保存/读取；Windows锁/ACL替换由WIN1独立验收 |
| `application/session_persistence.py` | 保存/恢复模式/活动集合，旧记录缺省规则、未知工具报错；不自动恢复pending审批 |
| `application/async_session.py` | 与generator的字段/错误/清理接线一致；必要保存失败仍影响终局 |
| `application/failure_policy.py` | 旧工具Unknown特判退出，真实进程收尾/部分状态仍受保护 |
| `application/cli.py` | 复用builtin/settings、双Shell统一活动集合选择、现有输出清理入口迁移；删Write/BashUnknown专用catch/partial输出，因为普通输出进入ToolMessage |
| `application/skills.py` | 修工具import及额度来源，保持发现边界；外部Skill链接不自动读取/授权正文，普通Read/Edit可经Ask使用；不扩大为项目插件代码执行 |
| `web/app.py` | lifespan使用同一工具集合/设置，注册共享图；现有授权和应用拥有关系保留 |
| `web/settings.py`、`web/__main__.py` | 05删除host_bash字段/参数与专用校验，不再新增host-powershell开关；统一工具选择启用。运行设置由装配默认读取，不加tool-settings路径或自动工作区配置 |
| `web/service.py`、`web/run_delivery.py` | 当前App接口/import与必要会话字段投影；不删除manual_review安全状态，不加Python上传执行 |
| `web/serialization.py` | 新project_event_for_web：消息快照先投影文字/标明范围、重算副本hash/bytes，再进DeliveredRun帧检查/缓冲；原事件不变 |
| `frontend/src/types.ts` | SessionView增加permission_mode/active_tools，四处现有会话fixture同步；不顺便实现新交互 |
| `requirements.txt` | 新增固定Pillow依赖，保留既有版本；实际安装前按Spec批准范围执行 |

`application/project_instructions.py`、现有资源/事件/清理模块默认保留；因调用点/字段必要变化才改，不为统一风格重写。`web/schemas.py`保持现有请求合同，`web/serialization.py::text_content`已支持内容块文字，复用它补事件投影，避免base64在忽略前先超帧。首版会话活动/模式控制提供应用SDK，不新增浏览器Python登记或管理HTTP接口；Shift+Tab、工具启停面板及其HTTP控制适配、图片/diff专用展示分别列后续。

## D. 明确删除项与保留项

| 删除目标（迁移消费者后） | 替代/边界 |
|---|---|
| 整个旧 `agent_runtime/tools/` 包（9个文件） | 独立tools包；不留旧import转发壳 |
| `agent_runtime/tool_batch.py`、`async_tool_batch.py` | 移至tool_execution，算法不因此重写 |
| `agent_runtime/file_permissions.py` | 新通用ToolSpec权限声明/规则，旧attach_file_permission_policy同步消费者迁移 |
| `agent_runtime/tool_approval.py` | 新准备对象/局部HITL；OneUseApprovalGrants移入tools.permissions继续使用，不删一次性授权语义 |
| `agent_runtime/project_prompt.py` | ToolSet.compose_tool_prompt替代唯一项目prompt附加入口，迁其runtime调用和测试，不两次附加 |
| SAFE_*集合、_safe_*_error、_set_safe_errors、handle_tool_error设置 | ToolException准备具体公开提示+官方ToolError；handle_validation_error还在使用，不误删 |
| ApprovalPolicy/ContextualApprovalPolicy/attach_approval/review_path/静态approval_policies_for_tools | ToolSpec.permissions+保存的PreparedToolCall；删metadata双轨和重复review/grant_factory |
| ReadOnlyApprovalGrants别名、BashApprovalGrants专用形状 | 通用调用许可/OneUseApprovalGrants；不能删人工批准本身 |
| WriteOutcomeUnknown/BashOutcomeUnknown及应用/CLI/观察/错误策略类型分支 | 普通ToolException/真实ProcessCleanupError按总Spec分开，不保留旧混合语义 |
| Bash旧固定env、同步Popen与采集线程/旧独立双流尾部 | 共享进程服务+合并输出+配置环境；已保存旧日志读取/清理继续支持 |
| 旧NAIYUE_WRITE_MAX_BYTES/NAIYUE_BASH_SHELL/NAIYUE_BASH_OUTPUT_*与批次WAIT环境配置读取 | 迁到TOML逐键设置，移除_positive_limit/_write_limit旧解析；不改父进程业务环境继承，不保留双重优先级 |
| create_read_tool/create_ls_tool/create_find_tool/create_grep_tool固定workspace工厂 | 当前生产与scripts无调用，只有旧测试/导出；测试迁会话ToolSpec入口或实际IO helper后删除，不当作长期对外合同 |

保留：真实同步App API、sync文件IO与必要settle、RunServices桥接、写busy/快照/原子提交、会话库锁/普通文件与链接检查、模型配置内容校验/错误脱敏、Web token/loopback约束、未配对工具/保存未确认人工核对、真实旧日志。2026-10-16用户取消模型配置的私密权限门槛，已删除该模式位检查，不另建配置ACL校验；同日继续取消会话库/锁/附属文件的模式位门槛，不新增会话文件ACL检查。区分废弃代码兼容与已有用户数据可读；不以清理为由删数据或跳过其余安全检查。

## E. 测试、脚本与文档消费者

新增行为测试建议文件：`tests/test_tool_registry.py`、`test_tool_settings.py`、`test_dynamic_tools.py`、`test_tool_error_feedback.py`、`test_permission_pipeline.py`、`test_auto_permission_review.py`、`test_batch_approval_adapter.py`、`test_tool_symlinks.py`、`test_read_images.py`、`test_model_image_messages.py`、`test_edit_matching.py`、`test_process_lifecycle.py`、`test_host_powershell.py`、`test_web_images.py`。若现有文件已能清楚承载行为，优先扩展，不为了凑清单另建。

副作用验收加入registry/pipeline/Auto测试：未知值拒绝、未填unknown、八工具映射、保存/恢复同值、快审/详审输入一致、模型args不能覆盖，以及read_only不会直接免审或绕过Deny；注册、权限和Auto已按04/06/07验证；PowerShell映射随双Shell片验证。

执行环境验收加入pipeline/Auto/approval测试：宿主三平台事实来源、模型参数不能伪报沙箱、保存/恢复和许可环境绑定、同环境恢复不重审、环境缺失/改变时工具零执行；模型输入/审批说明不含完整env或凭证。不以这些宿主测试声称沙箱能力。

artifact验收加入registry/动态工具/Edit/保存测试：文本与原生二元组两种格式、同步/异步同ID配对、JSON详情checkpoint往返、模型请求及摘要/预算不混入artifact；现有事件/Web文本显示不变，失败仍按ToolException/ToolError处理。当前离线机制探针不替代后续产品接线测试。

| 现有测试文件（均在tests/） | 必须保留/更新的验证 |
|---|---|
| test_read_tool.py、test_read_tool_name.py、test_ls_tool.py、test_find_tool.py、test_grep_tool.py | 新路径/schema/工厂入口、原具体提示、参数错误、预算/不完整、名称与权限区分；不只改import |
| test_write_tool.py、test_edit_tool.py、test_bash_output.py、test_host_bash.py、test_async_builtin_io.py | 大文件/提交/清理、原始日志识别、退出码、异步实际执行；旧Unknown断言迁新明确合同 |
| test_read_permission_extension.py、test_tool_approval_registration.py、test_application_read_approval.py、test_application_ls.py、test_application_search.py、test_application_write.py | 迁新注册/规则/保存对象；批准、拒绝、变目标、不同会话、无副作用均保留 |
| test_tool_batch_scheduling.py、test_async_tool_batch.py、test_active_stop_approval.py、test_active_stop_bash.py、test_application_stop.py、test_active_stop.py | 顺序/并发、拒绝唯一serial、整批零提前执行、停止/取消配对及异常身份 |
| test_application_composition.py、test_async_application.py、test_persistent_sessions.py、test_application_session.py、test_application_workspace.py | 共享App/会话隔离、字段/旧schema迁移/必要保存，未知工具恢复失败 |
| test_application_cli.py、test_application_cli_read_tools.py、test_application_write_cli.py、test_application_edit_cli.py、test_web_tools.py | 新集合/设置入口、具体工具错误显示、现有交互回归；不得依靠旧CLI异常补输出 |
| test_session_system_prompt.py、test_model_access.py、test_model_limits.py、test_model_request_context.py、test_context_compaction.py、test_context_compaction_fallback.py | 动态工具prompt/schema/预算、图片api/profile/摘要/溢出副本；本轮绑定不变 |
| test_project_skills.py、test_application_skills.py、test_agent_middleware_order.py | 实际Skill链接及资源刷新、真实middleware先后，不用静态注释代替轨迹 |
| test_observation_calls.py、test_model_tool_subscriptions.py、test_agent_runtime_tool_events.py、test_tool_subscription_boundaries.py、test_application_run_events.py、test_application_message_events.py | error_result/tool_failed/run终局区分、消息ID/顺序/事件不重复、观察故障不吞主错误 |
| test_application_run_event_failures.py、test_control_races_and_failures.py、test_application_control_events.py、test_application_turn_events.py、test_classifier_contract.py、test_layered_failure_contract.py、test_turn_failure_boundaries.py、test_cli_event_integration.py | 真实机制/取消身份、控制与turn、必要保存和未确认保护；迁废弃类型但不减少行为断言 |

脚本：`scripts/probe_live_workspace_tools.py`、`probe_skill_loading.py`、`probe_message_state_boundaries.py`改新import/ToolSpec装配并保留原行为证据；搜到其他真实消费者同片迁移。真实模型脚本执行须另确认样本/模型/请求预算，迁代码不等于本轮实网验证。

前端只同步`frontend/src/types.ts`及`App.test.tsx`、`approval-flow.test.tsx`、`conversation-boundaries.test.tsx`的会话fixture；若其他fixture也声明SessionView，实施用tsc枚举并补齐，不增加模式UI或工具页面。

文档：README、工具配置样例、handoff、future-work、project-plan及当前运行/调试入口同步；旧Spec/lesson保留历史并注明被替代入口，不批量改写课堂“当时实现”。不自动修改用户的关键知识点或学习掌握证据。

## F. 子Agent独立设计入口

已确认的一层/前台/同进程、多实例内部审批及停止合同，准确新增/修改文件、调用端口和六个实施切片统一见[Spec086主文](086-subagent-foreground.md)及[文件地图](086-subagent-file-map.md)。本附录不并列维护第二份子Agent文件清单；基础085仍先完成注册/统一错误/动态集合/保存权限。

086明确覆盖的阶段差异：Web审批行改用approval_id，多来源事件增加source；子实例独立状态/当前批审批通道、父工具持续等待，取消旧候选父重入账/空中断协调，正常串行排队不受60秒故障阈值；复合subagent仅明确ToolException转换，未知子图机制异常上抛。此处上方“Web请求不变/算法不因此重写”只描述085基础片，不适用于完整086接线。配置样例已移除旧batch_wait_seconds项并补subagent输出预算，产品实施片同步迁移读取，不保留无消费者配置。

实施终检：rg全项目旧import/工厂/metadata/异常名→逐个说明剩余消费者；生产不得残留无用兼容分支，历史文件可留有版本说明。不得删除旧测试/放宽断言/skip；迁移前后行为差异只允许总Spec明确列出的变化。Windows专用锁/ACL最终采用何种实现由WIN1复盘决定，当前只列静态阻点，不凭模块命名宣布跨平台完成。
