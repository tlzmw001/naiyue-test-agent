# Spec080：原异常标注失败描述，收敛内部回传

状态：原独立草稿已并入获批的[Spec081](081-event-exception-convergence.md)第二片，工程已于2026-10-11随bcbe6be本地提交，未推送；下文保留当时的独立实施草案及评估证据，不再作为待执行范围。用户确认应用入口继续抛同一个原异常，并建议用其属性传递描述；实现未创建RuntimeFailure包装类。

用户随后提出完整Agent健壮性需求，见[健壮性设计](../agent-resilience-design.md)。本Spec只对应行为/诊断基础，不覆盖工具重试、熔断隔离、备用或质量验证；先确认处理合同，尤其invalid_tool_calls反馈与现有整轮拒绝的变化，再锁定本片。当前不直接实施。

## 1. 目标与合同

后续[事件与异常收敛讨论](../event-exception-convergence-design.md)拟集中一次run的依赖。本草稿“其他ContextVar不变”是原独立范围；共同实施前需重写执行Spec，不能按原五文件边界直接改事件系统。

当前Runtime先回调报告D，App再按id(E)查描述，普通模型委托还嵌套通用resolve。改为D随E原样上行，保持异常具体类型、对象身份、原原因链、控制传播及公共调用签名/事件/DB字段。

run/resume/arun/aresume/stream/Web不新增异常还原步骤；独立Runtime和直接middleware调用仍抛原类型。工具反馈、重试、溢出/压缩、停止/取消/审批、未知副作用门禁及保存顺序不改。

本方案仍保留已有自定义业务异常：ModelResponseRejected、ContextCompactionError、ToolBatchSchedulingError、Write/BashOutcomeUnknown及App会话/保存异常。撤回的仅是为统一回传新增RuntimeFailure包装，不把项目自己的语义错误改成全部SDK异常。自定义异常类型与自定义分类器扩展仍是两个独立能力。

## 2. 数据与调用链

标注键固定为`naiyue_failure`，内部值是`(run_id, FailureDescription)`二元组。无App上下文可用None；App读取必须匹配自己的publisher.run_id。D仍为原四字段，不新增run_id。只读当前异常，不沿cause搜索，防止外层ContextCompactionError被当成内层模型TimeoutError。

```text
下行配置：ModelCatalog快照 + RuntimeErrorPolicy → 当前调用context
上行失败：原异常E → 所属层分类D → 标注E → 裸raise同一E
应用处理：读本轮有效D → 加stage → 原有失败状态/收尾/终局
标注不可用：App领域规则 → 既有执行阶段回退 → unknown
```

属性只用于描述事实，不决定权限、核对/停止动作；这些仍由异常类型与既有控制流程决定。不直接序列化E、其__dict__或标注；事件仍显式投影RunFailureData。

## 3. 共享标注函数

扩展现有contracts/failures.py，不增加新业务层/注册系统：

- `validate_failure_description(error, description, *, source=None)`：要求FailureDescription，验证原四字段格式及error_type对应safe_error_type(error)，指定source时须一致；返回原D，普通非法数据抛固定ValueError。resolve、模型描述委托、标注读取复用它。
- `attach_failure_description(error, description, *, run_id=None)`：只尝试设置项目标注；普通验证/赋值故障记录固定诊断和受限类型后False，成功True。不空pass，不修改args/cause/context/suppress/traceback，不覆盖原异常。
- `read_failure_description(error, *, run_id=None)`：缺失返回None；普通读取故障、非法形状/描述、错误类型不符或foreign run记录固定诊断后None；合法unknown返回D。使用显式None判断，不用类别/真值代表未匹配。

单值属性可能被共享异常对象的其他捕获覆盖。run_id防止串用，不能保证同一实例跨并发逻辑失败的精确归类；拒绝foreign记录后允许退化。多次复用且无法更新标注也不保证描述新鲜。正常每次独立异常路径保留一致性；不对复用/不可写情况宣称调用事件与终局分类必然完全一致。若必须保证该边界，本草稿需要另定方案。

## 4. 接线变更

1. RuntimeErrorPolicy普通model分支直接调用model_describer/default模型入口并验证D，不再将模型入口当普通分类器交外层resolve；普通入口故障仍日志/unknown回退，BaseException控制保留。
2. describe_failure_in_context保留签名：通过context策略生成D，使用context.run_id标注E，返回D；删除reporter调用。既有模型/同步工具/异步工具捕获点仍记录本次D并裸raise。
3. 故障ContextVar缩为只传RuntimeErrorPolicy，内部绑定改为bind_failure_policy/current_failure_policy。替换Spec079新增且未发布的双端口内部函数；内部API迁移明确包含在批准范围，不保留无用途reporter参数。已发布应用/Runtime入口签名保持。
4. make_invocation_context继续注入_failure_policy，移除_failure_reporter；RunContext移除相应字段。属性解决描述上行，不解决模型/摘要策略下行；其他事件/控制ContextVar不变。
5. RunObservation删除报告表/报告锁/record_runtime_failure；bind仅绑定策略；_describe_failure先读本run标注，再保留App try_describe/原stage回退。failed/cancelled控制与阶段判断、finish及SessionService清理不变。
6. 摘要/恢复仍取_failure_policy，原ContextCompactionError原因链不改，不从cause偷取model描述。

## 5. 文件范围与非目标

生产改动限定5文件：contracts/failures.py；agent_runtime/errors.py、invocation_context.py、__init__.py；application/run_events.py。相应测试/文档使总文件超过8个，执行前核对diff。

模型配置、工具业务实现、SessionService/AsyncSession、前端、Web公开映射、DB、依赖/.env不改；call_observation现有捕获入口通过helper接线，不新增解包。若发现额外必需改动，先更新Spec。旧单参/双参分类器兼容及model_id快照不删，不为签名检查成本另造缓存平台。

## 6. TDD与验收

先看到正确红灯再实现。旧内部绑定测试只迁移真实接口位置，保留原身份/字段/安全断言：

- 标注：内置/框架/已安装SDK/项目异常，拒绝setter、故障getter、非法值/字段/type、foreign scope、合法unknown、无正文泄漏。
- 传播：同一E、原具体类型/cause/context/suppress、原traceback不包含标注辅助函数；ModelRetry及ContextCompactionError捕获不受影响，无包装/还原。
- 接线：同步/异步模型分类一次、调用及终局数据一致；run/approval resume、stream/Web复用边界；独立Runtime和SDK接口保持。
- 回退：标注失败不替换E，诊断可观测；不查错run模型规则，无法保留专用分类时按原阶段退化；独立异常的两App与模型切换快照隔离。
- 行为：ToolMessage继续、fatal工具、未知副作用门禁、UserStop/外部CancelledError/Graph控制、停止/审批/溢出/摘要回退不变。
- 收尾：prepare/execute/finalize/cleanup、首/次错误、发布故障、busy释放及持久化blocked不变。
- 兼容：公开签名、旧异常导入身份、legacy failure_data、默认参数、两参source保持；无反向依赖/单文件超过500行。

最后执行后端全量pytest、compileall、pip check、frontend npm test/build和git diff --check，记录实际结果。不把原异常写入事件/存储；不宣称所有SDK或共享异常复用均支持。回滚只涉及源码，无DB迁移。

## 7. 本轮评估证据

独立进程探针非产品实现：9种内置/框架/SDK/项目/控制异常支持属性，本机args/cause/context/suppress保持；拒绝setter仍抛同一E并保留cause，辅助标注不进E traceback；同实例两run覆盖已复现。

真实ModelRetryMiddleware(retry_on=ModelTimeoutError,max_retries=1)对已标注原异常执行2次并恢复，对不继承该类型的合成包装执行1次后传播。包装catch中重raise原异常挂包装context已复现。读取形状/type/归属不符及故障getter回退探针通过。

没有真实模型请求、产品/测试源码修改、全量构建或提交。用户提供方案与分析是实际设计参与，不记已独立实施/全部面试掌握。草稿待确认后再实现。
