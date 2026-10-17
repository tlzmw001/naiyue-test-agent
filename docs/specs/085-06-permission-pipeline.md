# Spec085-06：确定性权限、批量审批与执行许可

日期：2026-10-16。状态：已按用户授权实施并完成本机回归，结果见第8节。接续085-05；Auto属于085-07，只展示设计，不提前实现。用户本轮确认：单文件Deny优先于同目录`/**` Allow。

## 1. 范围与现有主链

当前BatchApproval读取ApprovalPolicy.when；应用pending再次review路径；恢复由各工具grant_factory生成grant，工具再分类/消费。改为Permission节点准备并保存一次，局部官方HITL只读Ask，应用从保存对象准备恢复，Authorization消费通用grant并提供当前调用permit；工具显式核对真实目标后IO。继续create_agent/既有ToolBatch/ToolError/停止/事件/IO settle，不新增主循环、依赖、沙箱或全局会话对象。

实现Default/NoAsk/BypassApprovals、显式Deny/Allow、保守文件默认策略、精确命令规则、会话模式切换、仅内存session_allow及remember_approved后端能力。Auto在06不可选择，07才接审查器。无权限管理HTTP/CLI新命令、文件规则热重载、图片/模糊Edit/新搜索引擎/新进程实现；Bash旧运行及Unknown保护仍保持。

## 2. 数据与接口

- ToolSpec新增permissions（缺省ToolPermission(default='ask')）和validate_input，真实公开Pydantic检查；内置FilePermission(path_field,operation)与Bash通用Ask。作者通过prepare/default_decision扩展，无旧metadata兼容包装。
- PermissionSubject/PreparedToolCall/ExecutionPermit为JSON可保存事实；名称/原参数/有效参数、工作区、请求/真实目标、操作、side_effects和host/platform。工具对象不进state。准备参数错误仅存validation=invalid；不提前生成消息，后续交BaseTool原校验。
- ToolExecutionState沿已有tool_execution保存tool_names，Permission增加batch_id/mode/calls。batch_id取当前AI消息ID；准备结果节点先于interrupt完成，恢复不重跑。原始参数不被归一化或批准决定改写。
- 一次性grant由应用有效approve后生成，绑定session/agent_path/batch/call/name/原参数/subject；OneUseApprovalGrants.consume匹配后删除。Command只带官方decisions，grant放可信context。Authorization对Ask消费grant，对Allow直接发permit；permit按调用ID置于本次调用context副本，工具require_file_target/require_tool_permit消费或核对，避免并行共享“当前许可”。缺失state/绑定或grant属机制故障，上抛；目标变化属具体ToolException。

## 3. 规则与加载

扩展现有ToolSettings.permissions，保留tools字段和共同用户目录。文件缺失默认；读失败/整文件语法错/权限非法终止，工具字段非法仅工具额度警告后回退。四App入口可显式传settings，省略则默认加载；CLI/Web已加载的同一对象继续传入，不重复读磁盘。所有规则结合启动registry公开模型校验，动态登记在锁内校验候选全集再发布。

PermissionRule只有tool/path/args：tool精确名或*，args公开标量精确类型和值，path绝对或${workspace}，组件glob仅*、?、**；不支持[]/未知占位符/不完整**。Deny匹配请求或真实目标，Allow必须覆盖真实目标；Deny始终先于Allow，字段AND、规则OR。*+path只适用文件工具；*+args所有候选必须具备同类型字段。不存在工具/字段、非文件path规则在创建Agent前失败，不静默忽略。

## 4. 路径和原工具迁移

tools/paths抽出真实路径与父目录核对：普通文件/目录、工作区内外及隐藏/后缀只是准备事实；隐藏组件或.pem/.key默认Ask，不硬Deny。工作区选择只校验存在目录，移除旧隐藏根/凭证祖先硬拒；用户主动选定的根视为工作区，后代隐藏目标仍Ask。Deny仍优先于可信Skill资源Allow；外部Skill链接发现本身不授正文许可。

原同批Write→Edit→Read是实际消费者，故准备可确定的尚未存在目标，保存真实路径/最近现存父级锚点；是否存在及IO能力在执行时确认，断链/循环/不能形成目标则target_error。这样不为预测前序工具结果建设依赖图。执行重验真实路径/父级锚点，文件内容变化仍由原Write/Edit快照/提交冲突机制负责，不把前序合法写入误报为目标换链。写真实文件而保留文件/目录链接。

Read/Ls/Find/Grep/Write/Edit去逐工具权限分类和grant消费，IO helper接已授权目标。FilePermission声明保留工具原路径/无工作区提示；普通IO具体提示继续归工具。Grep/Find/Ls候选按同一规则检查：Deny过滤，未另获授权的隐藏/私密子范围跳过并报告；普通子目标继承已批目录范围，外部链接不能自动继承。不逐候选人工/Auto。跳过反馈按工具既有字节预算限制详情并标明报告省略，控制字符转义。保留当前rg/预算/匹配等能力，后片再换fd/分组搜索。

## 5. 正常与失败链

启动：load_tool_settings→prepare_tool_registry→validate_permission_rules→catalog→SessionService→固定图。

正常：ToolSet→model→Permission.prepare_batch（有效输入→subject→Deny→Allow→默认→模式）→保存→BatchApproval→官方interrupt→应用pending读取保存对象→approve→prepare_resume核对并造grant→Command恢复同一节点→ToolBatch→ToolError→Authorization→工具require_file_target→IO→同ID结果。r1 interrupted，r2恢复同session/thread/batch，不绑定旧run_id。

失败：Deny/NoAsk由Authorization给同ID error且标not_executed；人工reject沿官方；target_error在执行阶段ToolException→ToolError；参数错误沿原框架反馈；默认策略意外/非法返回退Ask并诊断，控制信号不转换；必要规则/授权机制失效上抛。批准后目标换链拒绝旧permit，无正文IO。无审批的Allow不要求人工grant。

## 6. 文件和方法

已有tools/permissions.py替换旧描述，保留OneUse机制并加入ToolPermission/FilePermission、require入口和candidate过滤。新增tools/paths.py；新增tools/permission_rules.py集中配置schema/validate_permission_rules/match_rules（启动、动态登记、批次及候选四个真实消费者；避免permissions.py超过500行）。新增runtime/tool_execution/permissions.py和authorization.py；改approval为保存决定映射，ToolSet state保留同源集合；feedback保护授权机制故障。新增application/session_approval.py移出pending/public/prepare_resume，session_service保持500行以下。

四App builder/CLI facade注入settings和resolve_permission_context；SessionControlMixin新增get/set_permission_mode，模式保存成功后再发布；resolve读取本批mode/session_allow快照。remember_approved只允许本次approve行，由保存对象生成同工具/操作/目标/环境的临时规则，不写配置/DB；仅目录起点不扩大/**。session_storage v2→v3事务只增permission_mode；restore校验，session_allow重启为空。describe/Web/前端SessionView及fixture同步permission_mode，无新路由。

普通自定义工具不要求工作区，因此无工作区会话的合法批准恢复也必须可走同一通用入口；resource_invocation允许workspace=None且资源tuple为空，不开放“无根却携带文件资源”组合。授权生成放在已接收恢复run的prepare阶段，失败仍产生原有run失败/cleanup并保留pending。remember行ID先做纯校验，非法提交不接收run。

旧ApprovalPolicy/Review/ContextualApprovalPolicy、attach_approval/approval_policies_for_tools、Read专用FileAccessRequest/Decision/回调metadata、ReadOnly/BashApprovalGrants及逐工具classify/when/review消费者全部迁移后删除。旧测试按新规则/default callback合同保留行为覆盖；硬隐私拒绝改为NoAsk或显式Deny测试，新默认Ask另测；不得为旧测试保留废弃路径。

## 7. 验证

先失败测试：精确Deny+目录Allow、**/组件边界、类型严格/未知规则、启动语法/IO失败；有效外部Allow零审批；Default一准备、全批暂停、恢复不重审、同名混合决定；NoAsk/Bypass不绕Deny；grant重复/参数/环境/目标变化；参数错误延迟/原提示；自定义默认策略异常Ask；递归Deny/隐藏跳过范围、父子链接、Write→Edit→Read；模式busy切换/保存失败、记忆仅当前会话、SQLite迁移/重启空记忆；sync/async/停止/事件和原完整回归。

最终pytest全量、前端test/build、compileall、pip check、diff、500行/旧消费者清理检查。仅临时配置/DB/文件和假模型，不改真实配置/凭证/会话库、不调用真实模型、不提交/push。Windows路径纯规则测试不冒充实际Windows端到端。

## 8. 实施入口与实际验证（2026-10-16）

当前可追踪主链：`application.build_local_application/async/persistent`先加载settings、建立registry并校验规则，再构造catalog和固定middleware。`ToolSpec.validate_input`过滤执行模型声明的注入字段（保留真实额外参数错误），`PermissionMiddleware.prepare_batch/_prepare`准备一次并写state；`BatchApprovalMiddleware._local`只把保存的Ask映射到局部官方HITL。`session_approval.pending_tool_approval/prepare_resume`从保存对象生成公共审批和通用grant；`ExecutionAuthorizationMiddleware._authorize`检查绑定、消费grant或构造拒绝，再把逐调用permit传入工具context副本。`require_file_target→verify_file_target`核对真实目标/父级锚点；Write/Edit仍调用原冲突/提交算法。图中新增的是前置Permission节点，Authorization仅包工具执行，不另加一轮模型或事件循环。

已删除旧ApprovalPolicy/Review/Contextual、attach/lookup/review_path、Read FileAccessRequest/Decision及metadata回调、ReadOnly/Bash专用grant、逐工具classify/when/review与无消费者旧反馈分支。工具体仍保留无工作区/IO错误防御、Write/Edit内容/提交冲突、Bash结果未知/运行机制保护；后者有真实进程消费者，等待双Shell片替换。模型配置/会话库的OS私有访问保护未删除，Windows整体兼容仍待WIN1。

测试迁移：纯合成工具显式声明Allow以保留原探针执行语义，不为兼容而默认放行新工具。`tool_feedback_helpers.invoke_tool_feedback`显式用NoAsk，旧隐私/外部负例保留不执行/不泄露断言；真实审批/恢复转由App与通用grant测试，不再用缺绑定的假批准。旧`.github/.gitignore`自动允许迁为显式规则或保守Ask/NoAsk；文件内软链接旧拒绝迁为按真实目标成功和保留链接断言。递归名称/正文负例保留并补跳过范围。未修改conftest、不放宽失败断言或skip；低层无观察图的步数预算从12到14，增加的Permission节点不增加主模型轮数。

初始两组权限测试在产品模块尚不存在时明确collection红灯；随后边界测试又复现无工作区自定义工具恢复被旧资源入口拒绝、工作区隐藏根残留硬拒，按新合同修正。最后一次实际命令与结果：

```text
.venv/bin/python -m pytest -q
1267 passed in 39.54s
npm --prefix frontend test -- --run
Test Files 9 passed (9); Tests 27 passed (27)
npm --prefix frontend run build
TypeScript检查通过；Vite 186 modules transformed，built in 93ms
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts
退出0
.venv/bin/python -m pip check
No broken requirements found.
git diff --check
退出0
```

233个Python文件AST解析通过，无文件超过500行；当前settings样例经产品loader和七工具规则校验通过，未来目标样例仅TOML语法校验。权限模块运行消费者旧名称检索为空。验证限本机macOS/临时真实文件和SQLite/假模型，不等于Windows、实网模型或生产业务效果。Auto、前端模式切换/不再询问入口、跨重启pending恢复、文件规则热更新、图片、双Shell和沙箱未在本片实现；下一步审阅[085-07](085-07-auto-permission-review.md)。
