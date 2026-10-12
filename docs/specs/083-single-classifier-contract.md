# Spec083：统一分类器调用合同与直接保护分类

状态：2026-10-11用户批准后已实现并通过本机验收，架构复盘已写入exception-design-review第14节。代码范围仅分类器合同与保护分支；用户随后授权提交并推送，最新收尾与验证见[交接第323节](../handoff.md#323-当前代码提交与推送收尾2026-10-11)。复盘区分源码/历史记录/推断。

## 1. 当前路径和目标

修改前ErrorClassifier允许(error)或(error,source)，normalize先单参bind，必要时包装lambda；配置/策略/resolve可能重复经过这段适配。保护分支已经生成固定分类元组，却再借空classifiers和builtin lambda走resolve。

改为所有自定义分类器都按(error,source)调用，normalize只校验同步callable及可接收两个位置参数并返回原函数tuple，删除单参适配/包装。额外默认参数用于捕获值可以保留，但调用者始终传两个位置参数；不根据参数名猜回调语义。配置阶段继续拒绝不能接受这两个参数的签名/协程回调，校验不执行回调。

普通自定义分类继续共享resolve：顺序、None继续、合法unknown命中、来源/类型/格式验证、诊断回退保持。模型描述端口(model_id,error)是另一个二参数职责，现有验证与调用方式保持。

保护分支先按异常类型确定来源与分类码，再直接构造FailureDescription。普通构造失败仍记录安全诊断并unknown回退；诊断报告再失败也不能替换原异常，记录受限类型后回退。不可被自定义规则覆盖，Graph/进程信号、原异常/原因链、用户停止/未知副作用的执行动作不由描述决定。

## 2. 文件与迁移

生产仅contracts/failures.py（ErrorClassifier/normalize）与agent_runtime/errors.py（保护分支）。测试test_layered_failure_contract、test_failure_extension_boundaries、test_failure_annotations、test_failure_boundary_regression统一单参数分类器为两参数；不改SDK二元组builtin、ToolException handler或独立故障注入函数的签名。旧默认参数兼容测试改为显式source后捕获默认值，新增单参拒绝/原子配置测试，保留其余行为断言。

新增test_classifier_contract覆盖新签名、函数身份/不执行、来源、无效参数/配置原子性；保护分支不调用resolve、扩展绕过、非法reason与诊断故障不替换原异常/不泄漏正文。文档同步Spec079历史边界、异常导航、future-work/plan/handoff；新增架构复盘记录或扩展既有review，完成实现后再分析旧方案。

## 3. 验证和范围

先新增红灯，再最小实现与回调迁移，定向pytest；最终全量pytest、compileall/pip check、frontend测试/构建、diff check。组件有真实实现和调用；两生产文件≤500，注册API外形、事件/DB/模型配置不改，仅分类回调内部协议按用户授权统一。

不新增依赖/环境配置，不自动提交push。共享resolver的其他重复准备工作不并入本片；已有Web失败投影待办独立。复盘依据已提交HEAD/cceb5e0、Spec079/080/081/082、AGENTS历史与设计记录，不把猜测当作当时逐字提示词或行业统计。学习仍在Lesson38，本片不新增掌握证据。

## 4. 实施和结果

新增12例：单参拒绝/配置原子性与7类保护绕开resolver先得到`9 failed, 3 passed`；原函数身份及非法保护原因/诊断二次失败回退为当前基线。四个既有测试文件迁移分类器及两处嵌套quota调用；显式source后的默认捕获语义仍保留，原行为断言不放宽。

定向`54 passed in 1.45s`；最终`.venv/bin/python -m pytest -q --tb=short`为`1050 passed in 36.00s`。compileall/pip check/diff check通过；frontend9文件27通过及tsc/Vite构建成功。独立只读架构复核54例通过、无阻塞发现。AST确认normalize无lambda包装、生产≤500行。

两生产文件净行数持平（contracts减少6行、Runtime增加6行）：增加的是保护构造失败及诊断再次失败的显式回退，收益是单一回调合同与少一次通用解析，而非牺牲原异常合同换行数。证据为本机假模型/故障注入；无真实模型请求、环境/依赖/DB修改或生产保证。
