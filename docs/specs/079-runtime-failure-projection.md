# Spec079：分层错误契约与自定义异常接入

状态：2026-10-10，用户“做吧”批准复审方向；修订后已实施并通过本机完整验证。此前三个文件搬移草案停止执行，取舍历史保存在异常架构复审文档。未提交/push，未修改.env/依赖或请求真实模型。

## 1. 验收目标

模型接入、运行、应用层可独立使用，无反向导入；每类错误有明确产生/转换/处理/展示边界；新增模型、工具、应用自定义异常不需要修改不相关层。保留收到的原异常对象、控制信号、事件字段和现有公开签名。不把框架SDK原始对象序列化给模型或Web。

## 2. 依赖和数据流

contracts仅依赖标准库，包含安全失败描述/分类函数协议/受限错误名字形。模型接入、运行与应用各使用自己的错误策略；下层不导入上层的具体错误。Runtime响应拒绝的协议类型放基础contracts以保留model_access旧导入别名，判定与分类归runtime；不由模型层依赖runtime来兼容。

模型异常：模型归一化入口（框架ModelError族优先，SDK补充）→runtime描述与事件→注入的本run内部报告端口传递描述及原异常引用→App匹配原异常身份使用同一描述→清理/保存/终局→原异常传播。

工具异常：可恢复ToolException及框架handle_tool_error继续返回ToolMessage；致命异常由runtime工具观察描述/报告并传播，副作用未知保持人工核对。没有把所有异常转工具结果。

应用异常：应用自己的映射处理准备/会话/保存问题，附实际stage，不借模型分类器识别应用IO。没有来自runtime的报告时执行阶段由runtime错误策略兜底，其他阶段由应用策略处理。

内部报告端口不是可丢弃的UI订阅者；仅存在于运行上下文、记录安全描述和原异常身份，不写checkpoint或公共事件。报告普通故障需固定日志、保留原异常，App使用安全兜底。来源source用于内部职责及映射匹配，不添加旧model_failure日志字段；既有RunFailureData四字段/RunFinishedData及DB结构保持。

## 3. 契约与扩展

FailureDescription为冻结数据：source（model/runtime/tool/application）、category、reason、error_type。category/reason是受限固定代码格式，类型名受限；没有正文/traceback/请求对象/恢复动作。ErrorClassifier返回FailureDescription或None；None表示未匹配，不是成功。支持原单参数error形式，也支持(error,source)获取实际边界，避免通用Runtime/Tool分类器凭异常类型猜来源。注册时先检查能否单参数绑定（包括依赖默认参数的旧回调），能则按原单参方式调用；只有必须接收两个参数时才传source，推荐source为必填位置参数。签名无效拒绝配置；描述来源须符合当前边界，错误类型须保留输入异常的安全类型名。

所有映射是显式实例配置的不可变tuple，不使用全局注册表。配置拒绝非callable及异步分类函数；运行分类是同步、快速的纯转换，不发网络。扩展先于普通内置映射，取消/Graph控制与工具副作用未知/响应拒绝等运行保护语义先处理，不能被普通扩展改成成功。扩展或内置诊断抛普通Exception/返回非法数据→固定安全日志并unknown回退，不执行重试。

匹配与兜底必须显式区分：仅None表示未匹配；合法unknown描述仍是已匹配，保留其来源和具体reason，不能用category字符串推断。App策略提供try_describe用于该区别；默认describe继续返回完整兜底。旧failure_data(error)没有model_id参数，必须使用确定的默认映射，不读取绑定的目录策略；扩展仅通过明确带来源/模型ID的新边界接线。

ModelCatalog新增configure_error_classifiers(model_id,classifiers)、describe_failure(model_id,error)及failure_describer()快照方法；默认构造/加载签名不变。按model_id隔离扩展，运行开始复制映射快照，换模型仍选本模型描述器。现有classify_model_failure二元组入口和ModelResponseRejected导入保留兼容，主调用转向模型纯describe入口/运行响应判定。

LocalApplication/AsyncApplication新增configure_error_classifiers，关键字model_id/model/runtime/application；配置修改由SessionService锁保护，任何会话busy时拒绝，避免中途改变契约。全部校验通过后才提交，部分无效不修改其他映射。新方法不改变现有构造/运行/恢复签名。扩展是可信Python装配能力，没有新增HTTP传任意函数的接口。

RunObservation持有应用/运行策略和本run报告表，原异常身份匹配安全描述；不同run/App互不污染。bind和make_invocation_context传递运行策略/报告回调，模型与同步/异步工具实际接线。默认裸runtime仍可独立使用，不要求Application存在。

## 4. 层归属

模型：ModelConfigError、SDK/框架模型错误归一化、输入溢出识别；复用已安装ModelError/ContextOverflowError并保留原SDK补充识别，不依赖会话/事件。

Runtime：响应门禁、ContextCompactionError、ToolBatchSchedulingError、工具结果未知/取消/框架执行错误；新runtime.errors为该层目录和映射。现有context_compaction/tool_batch里的异常名重导出同一类对象，保留旧导入可用。

App：SessionBusy/PendingApproval/ManualReview/Persistence等归application.errors，旧session_service/session_storage及application公开导入保留同一对象。application.failure_policy处理域错误；技能发现软错误仍依具体发现契约，不被全部改成run失败。Web public_error继续在边界处理公开码。

仅改变描述语义：框架模型权限/404/429等映射不再漏为unknown；上下文/调度/持久化有明确层分类；通用未知仍unknown。上下文压缩时用于回退的模型分类调用保持在模型层，不混入App策略。重试次数、超时、恢复上限/切割算法、审批和清理顺序不改。

同形故障扫描发现摘要失败处理仍直接调用不受保护的二元组分类器；已复现其诊断ValueError变成compaction_internal原因链而遮盖原TimeoutError。此路径使用上下文中的本run RuntimeErrorPolicy及真实model_id调用模型描述器，无应用上下文时用模型层默认describe；保留原摘要异常作为ContextCompactionError的cause，不更改压缩/回退预算算法。已知控制/门禁/压缩/调度错误描述归runtime，工具副作用未知归tool，不因处于模型包装内就交供应商分类器。

## 5. 影响与兼容

涉及三个目录与contracts基础包、装配上下文和对应测试，超过8个文件；不能按旧三文件清单实现。每个产品文件≤500行，超过时只按职责拆出模块。没有新依赖/升级/环境配置/事件字段/DB迁移/前端变动，不提交push。

旧异常导入同一类对象、构造签名/类型名保持；SDK适配器可能已包装原SDK错误并保留cause，“原异常身份”指本边界收到的对象，不承诺与SDK内部第一对象相同。旧failure_data(error)保留签名/位置并委托Runtime转换。

现有故障注入测试若分类责任迁移，改打真实新映射入口而保留原断言；不放宽/skip。新增独立依赖/描述/扩展/接线/事件与失败身份测试。学习文档以真实参与记录，不把助手实现标为用户掌握。

## 6. TDD与验证

先写当前同步/异步模型分类故障及unknown诊断故障回归，观察抛出的诊断异常替换原异常的红灯。再写共享契约/模型/Runtime/App扩展缺失的失败测试，逐层实现到绿。

验收：三个层独立导入、无反向依赖；原响应拒绝/控制类型兼容；框架与原SDK错误分类；两模型/两App不同映射隔离；自定义模型错误不改App；自定义工具错误返回ToolMessage且run继续；App自定义准备/结果错误不改模型层；扩展故障/非法来源/非法字段安全回退且原异常保留；模型失败转换只做一次且日志/事件/终局一致；配置busy拒绝及快照；持久化失败/取消/审批/主要次要错误保持。

定向pytest后全量`.venv/bin/python -m pytest -q`，`.venv/bin/python -m compileall -q naiyue_test_agent`、`.venv/bin/python -m pip check`、`git diff --check`、产品行数检查。无真实模型故障/付费请求需求，不以单测通过宣称全部供应商/生产负载验证。

## 7. 完成与后续

L1模块存在，L2真实归一化/扩展/回退，L3模型/工具/App正常与异常链实际调用均须验证。保存三条真实代码时序、异常目录与命令结果后完成本片。外部重试/attempt观测、未知样本评测库及更多供应商实网故障仍独立待办。

## 8. 本轮实施与验证结果

L1/L2：contracts安全描述与签名适配、model描述/目录快照、runtime错误目录/保护策略/内部报告端口、App错误目录/应用映射及配置API均为真实代码。L3：普通/摘要模型、同步/异步致命工具、App终局复用描述、可恢复工具回填均经确定性接线测试；实例映射隔离/配置busy拒绝与原子性/导入独立均有对应断言。三条时序及目录已同步exception-architecture.md。

新增三个测试文件34例：failure_boundary_regression、failure_extension_boundaries、layered_failure_contract。先有模型分类/unknown诊断四例4 failed in0.99s；共享模块缺失collection红灯；审查发现合法unknown覆盖/旧桥接上下文退化两例2 failed in0.62s；摘要诊断cause污染1 failed in0.84s；摘要扩展未接1 failed；控制进扩展2 failed；contextual接口1 failed；旧可选默认参数兼容1 failed。均修源代码转绿。没有将这些预期红灯算作已验证功能，缺模块红灯也不替代原行为回归红灯。

旧应用诊断测试只迁移到真实RunObservation._describe_failure故障注入点，非法码/原异常/阶段/脱敏断言保留。新回归加called断言，确保故障注入真的发生。最终回归中发现误加旧日志error_source字段导致8失败，移除源码额外字段保留原断言，source仅内部使用。

最终命令与实际结果：

- `.venv/bin/python -m pytest -q --tb=short`：962 passed in34.15s。
- `npm test -- --run`（frontend）：9文件/27测试全部通过。
- `npm run build`（frontend）：tsc --noEmit与Vite成功，0错误。
- `.venv/bin/python -m compileall -q naiyue_test_agent`：退出0。
- `.venv/bin/python -m pip check`：No broken requirements found。
- `git diff --check`：退出0。
- 产品78个Python文件无超过500行；AST比较355个仍在原位置的既有公开定义参数签名相同，搬移异常旧导入另有身份兼容检查。

check skill独立架构/安全只读审查完成。架构三个具体发现（unknown匹配、旧桥接无model_id、旧可选默认参数）均补回归并修复，最终无剩余高置信问题；安全审查无高置信具体利用路径问题。回调为可信Python装配，合法字形不证明内容永远无敏感语义；不声称恶意插件隔离、多租户或任意诊断/日志设施失败全部保证。

本片达到已确认的本机切片验收，不称完整生产级。用户理解/独立实现/面试追问仍需后续课堂证明。下一步从FailureDescription与resolve_failure沿三条时序学习新版代码，然后回第2章stop_reason。
