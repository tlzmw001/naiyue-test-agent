# Spec 072：Read 权限策略扩展样板

状态：2026-10-08，按用户批准的“边界小片→异步→Web”实施。本片只接会话Read策略接口，不实现完整权限产品或改造全部工具。

## 当前链路和本片目标

read工厂注册ApprovalPolicy，HITL when按工作区/资源根判断ask；应用review只有root/args；工具执行重新分类并消费一次性grant。默认逻辑已经共享分类器，但没有可信应用注入的策略，review缺session/resources上下文，不能正确承接按会话判断的策略。

本片：普通文件规范化和旧安全校验→不可变FileAccessRequest→可选策略allow/ask/deny→现有HITL→带可信上下文的review→执行前同一接口复核→ToolMessage。借鉴Pi hook/DSH pre-execute与guard分工，使用现有框架，不引入插件内核。

## 接口与范围

- 新agent_runtime/file_permissions.py：FileAccessRequest含tool_name/operation/workspace_root/target/session_id；FileAccessDecision含allow/ask/deny和有限安全原因；attach_file_permission_policy(tool, callback)仅接受明确声明此能力的工具及同步本地回调，不能由模型参数注入。read首先声明，其他工具未接入时拒绝配置，不伪称生效。
- 规范化目标仍由旧Read分类器和资源根验证产生，目标无效/敏感/非法路径时不调用策略。没有自定义策略沿用原decision；策略只能决定这个已验证的普通文件是否允许/需审批/拒绝，不能改target。错误/不合法返回失败关闭并回填安全具体原因，GraphBubbleUp/进程控制异常不吞。
- 现有公开工厂/工具参数/ApprovalPolicy签名保持。新增ContextualApprovalPolicy子类，review_with_context供工具实现；应用私有_pending_tool_approval增加可选context，旧描述仍调用原review。run/resume传session_id/workspace_root/read_resource_roots的可信当前上下文；不把它们做模型字段，不持久化callable。
- 会话Read工厂when、contextual review、执行复核调用同一resolve接口；资源Root与sessionID一致。恢复后策略变deny时不读取文件，ask保持原精确一次grant，拒绝/错误回流允许模型继续。默认行为/敏感限制/分页/编码/大小/TOCTOU现状保持。
- 本片回调为同步、快速、无网络的确定性决策；后续原生异步可复用值对象。无CLI模式/配置修改、允许会话记忆/Auto/全信任、外部write/edit或Bash沙箱；这些之后逐工具接入。

## 影响文件与验证

新增file_permissions.py；tool_approval.py增加兼容子类/注册校验；tools/read.py增加样板接线与安全错误；application/session_service.py传当前上下文；新增确定性真实文件/实际图测试与注册边界测试。更新Spec/交接/计划/待办，不改关键知识点/环境配置/依赖/公开签名。Python≤500。

先红：可信注入deny阻止读取并回流原因、allow普通外部文件免审批、ask工作区内需精确批准、按session隔离、注册Skill资源审批与复核一致、策略异常/非法返回失败关闭、敏感目标allow不绕过、审批后撤销deny、目标重验与一次grant；默认旧测试全部通过。非法/未支持工具注册拒绝，所有新组件真实接线。

验证定向和全量pytest、compileall、pip check与显式结构检查。用假模型和tmp文件证明框架、HITL及IO边界，不新增付费真实模型请求来验证纯确定性策略；不称为完整权限系统/异步/Web。

## 实施结果（2026-10-08）

本片已接线：可信调用方通过工具metadata注入同步策略，模型参数仍只有path/offset/limit；when、带上下文review、实际读取均调用同一resolver。应用普通run与审批resume传入session/workspace/Skill资源根；旧ApprovalPolicy仍按原review调用。审批后复核为deny时不读正文，策略异常只记录异常类型并回填带下一步指引的安全ToolMessage，GraphBubbleUp继续传播。

TDD证据：最初新模块缺失收集失败；声明契约但尚未接线时4失败/2通过，暴露deny未拦截、allow仍审批、ask未暂停和异常未拒绝。接线后首批10例通过。随后审批描述与误返回协程的回归先出现2失败/10通过，逐项修源后转绿；其余新增边界通过，不把全部17例声称为先红。

- `.venv/bin/python -m pytest -q tests/test_read_permission_extension.py tests/test_tool_approval_registration.py tests/test_application_read_approval.py tests/test_application_skills.py` → `44 passed in 1.05s`。
- `.venv/bin/python -m pytest -q` → `798 passed in 19.15s`（此前781例，新增17例）。
- `.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → exit 0；无独立应用打包目标。
- `.venv/bin/python -m pip check` → `No broken requirements found.`；`git diff --check` → exit 0。因项目文件尚未跟踪，另显式检查本片文件存在、Python行数与文档结构。

测试覆盖临时真实文件与实际create_agent/HITL路径：allow/ask/deny、批准/拒绝、会话隔离、Skill资源上下文、批准后撤销、异常/非法返回、协程误返回关闭、注册拒绝、敏感目标与中断传播。没有真实模型请求、业务质量或生产规模证据。未修改.env/真实配置/依赖/关键知识点，未提交或部署。

限制：仅会话Read支持此接口，其他工具配置时明确拒绝；不是插件沙箱。敏感/无效路径仍由旧分类器拒绝，TOCTOU现状保持；没有CLI权限模式、会话级授权或完整权限服务。下一步按Spec071拆分原生异步实施Spec，先核对模型/middleware、工具调度、上下文和持久化的异步调用链，不直接铺全部Web。
