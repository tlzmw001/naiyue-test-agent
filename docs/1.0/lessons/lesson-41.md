# Lesson 41：自定义分类规则怎样选择

> 2026-10-11开始。先讲固定(error,source)输入、None与合法unknown结果的区别；注册校验、规则故障安全回退与本人编写练习尚待继续。

## 具体主线

仍用模型边界的TimeoutError，source='model'。无自定义规则时内置分类为transient/timeout；有规则时resolve先依次调用自定义规则，未认领才走内置。

```text
E + source → 自定义规则1 → 返回None → 自定义规则2 → 返回None → 内置分类 → D
                          返回合法D → 立即结束选择，采用该D
```

自定义分类器统一用两个位置参数调用`classify(error, source)`，返回FailureDescription或None，同步执行。ErrorClassifier的Callable声明描述此接口，真正运行时检查另在normalize/validate，本段未展开注册机制。

## 真实代码与区别

contracts/failures.py第110–117行：normalize给出已校验规则，依次调用；None则continue；非None若是误返回的协程先close避免未await告警，随后validate拒绝无效结果；合法D由return立即结束resolve。协程close不是await执行规则。不合法结果或规则抛错走外层诊断回退，不能误讲为所有非None都成功或规则失败继续内置；该故障分支后续再读。

| 规则结果 | 含义 | 后续选择 |
|---|---|---|
| None | 本条规则不认领 | 下一条规则；都未认领才调用内置 |
| 合法unknown/unclassified描述 | 本条规则已经给出分类结论 | 采用D，不继续规则或内置 |
| 非法结果/抛错 | 分类机制故障 | 诊断/安全回退，后续小段讲 |

## 本地边界验证

两个只用于教学的规则：skip(error,source)返回None；claim_unknown(error,source)返回与E/来源一致的unknown描述。调用真实describe_model_failure，`.venv/bin/python -`退出0：

```text
None path: transient timeout called: ['skip']
valid unknown path: unknown unclassified called: ['claim_unknown']
original type unchanged: TimeoutError
```

第二组配置为[claim_unknown,skip]，skip未执行；当前resolve在合法D处return，内置也不执行。只验证描述选择，不负责异常传播；原异常上抛由middleware完成。无网络、新生产分类器或App运行。

## 当前检查与下一步

上一轮None/unknown检查未作答。用户重定向教学方法后不要求补答；保留未评估状态，不记理解。

## 教学方法调整：先看模块与分支（2026-10-11）

用户反馈逐行短段虽然看到代码，但速度慢、沉浸细节且记不住脉络。确认默认顺序：模块功能/边界→完整情况与分支→各层交互及函数→少数关键函数；其他函数点名后深入。停止每几行检查题，不把未逐行覆盖算必补课时。既有正确回答仍保留，本课未完成/未记独立实现。

### 当前模块的功能与完整选择分支

普通异常分类选择以原异常E、来源source和规则为输入，选择合法D；自身不执行重试、不改变E、不决定run结果。

| 阶段/分支 | 负责函数 | 结果 |
|---|---|---|
| 配置无效：非同步可调用、不可检查签名或不能按两个位置参数调用 | normalize_classifiers | 配置入口调用时拒绝注册；若执行时再次校验出错，走resolve诊断回退 |
| 自定义规则返回合法D（含unknown） | resolve_failure→validate_failure_description | 采用第一个合法D，停止选择 |
| 自定义规则返回None | resolve_failure | 继续下一条，全部未认领才调用内置 |
| 内置识别成功 | model/runtime/application各自builtin | 固定分类/原因组装D |
| 内置未识别 | resolve_failure | 常规fallback=True生成unknown；App try_describe的fallback=False且builtin=None返回None，允许上层继续选择 |
| 执行规则/内置/结果校验抛普通异常或非法返回 | resolve_failure | 记录诊断，返回当前来源的unknown D；不是继续下一规则或内置 |
| 上述诊断报告再次失败 | resolve_failure | 记录固定安全日志，仍返回unknown D；不替换原执行异常 |

### 普通模型失败的跨层交互

```text
模型middleware捕获E
→ describe_failure_in_context
→ RuntimeErrorPolicy.describe：普通source=model委托本run模型描述端口
→ ModelCatalog.failure_describer返回的describe：取本run按model_id冻结的规则
→ describe_model_failure
→ resolve_failure：选择自定义/内置/兜底，必要时调用classify_model_failure
← 返回D到Runtime
→ attach_failure_description：D附在E上
→ middleware裸raise E
→ App的RunObservation._describe_failure读取本run标注
→ RunObservation.failed记录结果与失败数据，必要收尾后尝试终局
```

模型内置规则和各模型自定义规则归模型接入；普通runtime/tool规则归Runtime；应用领域规则归ApplicationErrorPolicy。共享resolve只共享选择机制。Runtime已识别的保护类型直接生成固定描述，绕过这些自定义选择；图限定普通模型失败，不覆盖所有控制/保护分支。

### 主动展开与点名深入

当前关键函数为resolve_failure（选择/故障分支）、RuntimeErrorPolicy.describe（由谁分类）、RunObservation._describe_failure（复用描述与回退），先说明职责和分支；signature.bind、闭包、getter/setter等细节仅在用户点名或必要时深入。后续按完整分支继续，不再沿文件逐行推进或补答上一题。

## 分支单元测试与本人debug安排

用户确认每个分支配独立测试，结合讲解与本人debug。当前9个选择/注册分支的测试名、单独命令、生产函数断点与变量见[分支调试指南](../exception-branch-debug-guide.md)。复用注册拒绝测试，在原tests/test_classifier_contract.py补8个测试，不修改生产实现、conftest或IDE配置；不把现有行为的补充测试称作TDD红灯实现。

第一条：test_none_classifier_continues_to_builtin，E=TimeoutError、source=model，自定义规则返回None，继续内置得到transient/timeout。对照第二条：test_valid_unknown_stops_later_rules_and_builtin，合法unknown已认领，不进入内置。先完整运行两条分支，再自行选择故障分支深入。

实际验证（助手执行，不是用户掌握证据）：

```text
相关5个测试文件：62 passed in 1.52s
全量后端：1058 passed in 34.60s
pytest --trace单例：1 passed in 4.56s
断点111：error类型TimeoutError、source=model、调用后result=None
断点121：('transient', 'timeout', 'TimeoutError')
compileall与git diff --check：退出0
```

未发模型请求，未验证真实供应商/生产效果；前端未改，本轮未重跑前端测试/构建。当前唯一下一步由用户运行第一条分支并观察调用栈/变量；未补记本人debug、独立实现或本课完成。

用户后续明确首选VS Code，并反馈对象展开冗余。指南补VS Code解释器/Debug Test入口、111行分类调用与测试132行最终D两个断点、对应帧的精简WATCH/字典投影。核对项目已有pytest配置与官方文档，未修改IDE配置或操作其UI；用户实际调试待开始，后续以其停下的位置继续。

用户再明确要从测试入口单步走完整链、不在两个中途位置断点；当前默认改入口121行一次暂停，F10/F11/Shift+F11配函数输入/返回/副作用地图。指南补debugpy showReturnValue配置文本及当前帧locals监视表达式，定点/Pdb转备选。本机扩展源码确认(return)呈现与配置支持，未运行用户IDE或代改配置；不作本人debug验收。

## 本人复述：None→内置分支（2026-10-11）

用户自述这条单元测试已理解，并复述TimeoutError→模型描述入口→选择函数→传入分类器返回None→内置分类→分类结果组装对象并返回。主链复述正确；函数名口述Reserve对应resolve_failure，补充多规则时None先继续下一条，全部未认领才用内置。构造的是D，不包装/替换原异常E。记录本分支自述理解及复述证据，不推断已完成VS Code单步、独立编写或整章掌握。

## 下一分支：合法自定义D认领并终止选择

同样的TimeoutError、source=model，测试test_valid_unknown_stops_later_rules_and_builtin（当前入口134行）。第一条match返回预先构造的合法unknown/unclassified/TimeoutError描述；resolve校验后立即返回同一D，不执行第二规则或内置。该函数对比上一条None→内置，避免更换原异常增加负担。

输入/输出主线：test构造E/expected→describe_model_failure(E,[match,forbidden])→resolve检查规则并执行match→validate(E,D,source=model)→返回同一D到测试→assert result is expected。match把一次调用写进calls，是独立可观察副作用。

forbidden是测试守卫，同时作为第二规则和替代的内置函数；不该执行。其普通AssertionError若被错误调用可能被诊断回退捕获，因此result is expected身份断言有意义：安全回退生成的同字段新D也不应蒙混通过。monkeypatch仅在该测试内替换，结束后恢复。下一步按入口断点单步验证认领分支，不重复None检查题；本人观察待反馈。

## 本人复述：合法D认领顺序正确

用户自述此分支理解，正确说明两个分类器、第一条给unknown结果后立即return、第二条不执行。用户称结果为“包装之后的异常”，本次明确纠正：返回的是FailureDescription，不是包装异常，原E=TimeoutError，D与E是不同对象。记录选择顺序复述证据，对象术语纠正后尚无新复述，不记整个模块或独立实现掌握。

## 下一分支：分类器自己抛错

测试test_classifier_failure_returns_unknown_without_continuing，当前入口160行；仍用E=TimeoutError，规则broken执行时抛另一个ValueError（诊断故障）。resolve捕获的是规则故障，记录model_failure_diagnostics_unavailable，停止当前选择并生成D=FailureDescription('model','unknown','unclassified','TimeoutError')；第二规则/内置都不执行。None是规则正常不认领，抛错是分类机制故障，两者走不同分支。

E（原模型异常TimeoutError）、fault（规则故障ValueError）、D（对E的回退描述）分开；D.error_type仍来自E，日志诊断类型来自fault。此单元只验证描述选择，不向外抛E；真实middleware的原异常传播由既有边界测试保护。

debug入口：160行开始，准备阶段F10，描述调用F11，broken中输入current/source，执行raise后进入resolve的except，观察diagnostic_error类型ValueError、原error类型TimeoutError、最终result四字段、calls单条和forbidden_calls空。不追加逐行问答。

为直接验证“后续未被调用”，补强本测试与非法描述测试的守卫调用记录/空列表断言，避免守卫AssertionError被诊断回退捕获后仅凭相同unknown字段漏判。未放宽/删除已有断言，未改生产或测试基础设施。`.venv/bin/python -m pytest -q tests/test_classifier_contract.py`结果20 passed in0.81s，diff检查通过；无本轮全量/前端/真实模型证据，未提交。下一步用户单步观察故障回退分支。

## 本人复述：分类器故障与诊断报告

用户自述本分支理解，复述分类器抛错→选择函数except→匿名报告函数提取安全类型并打日志→返回unknown描述对象。主流程正确，描述对象称谓已采用；口述expect/save分别校正为except/safe_error_type。具体调用顺序是report_diagnostic(diagnostic_error)→传入的lambda→safe_error_type(fault)→logger.error；resolve进入try前另已提取原error的error_type用于D。

日志诊断类型ValueError，D.error_type仍TimeoutError；两个safe_error_type调用读取不同对象，不能把原异常类型当分类器故障类型。不推断已经独立VS Code调试、编写或完成整课。

## 下一分支：正常返回，但描述不符合当前边界

test_invalid_description_returns_unknown_without_continuing入口182行。原E仍TimeoutError、期望source=model；wrong_source正常返回结构合法的D_wrong（source=tool），随后validate_failure_description因来源不一致抛ValueError；resolve接住这一校验故障，记录诊断并返回D_fallback（model/unknown/unclassified/TimeoutError），不执行后续或内置。

与上一条对比：异常从分类器函数体产生，或从返回值校验产生；都会进入分类机制故障回退。D_wrong可构造不代表能在当前model边界使用。单步先看wrong_source返回，再F11进入validate比较description.source与source，随后except与回测试后的新D；forbidden_calls应为空。只定位关键交互，不重新逐行讲全部格式检查。

## 本人复述：返回值校验失败

用户正确说明分类器正常返回D，但source=tool与期望model不符，validate抛错→except记录日志→返回新的unknown D，不继续后续分类器。记录该分支复述理解，未自动记本人已单步运行或独立编写。

## 下一分支：诊断报告再次失败

测试test_diagnostic_report_failure_still_returns_unknown，入口202行，直接调用resolve_failure以注入broken_report。原E=TimeoutError；broken_classifier抛fault=ValueError，resolve外层except调用broken_report(fault)；报告器把fault写入reports后抛RuntimeError，resolve内层except记录固定failure_diagnostic_report_failed与安全RuntimeError类型，随后仍返回针对原E的model/unknown/unclassified/TimeoutError描述。

三个异常对象分开：原模型E、分类故障fault、报告故障report_error；它们不互相替换。输出D仍基于E。reports==[fault]表示报告器确实接到分类故障；builtin_calls==[]表示没有转去内置。此测试是人为报告器故障注入，未证明真实日志IO故障；固定logger.error正常可用是本次路径前提，不声称无限层故障均可吞掉。

按入口单步：broken_classifier输入E/source→抛ValueError；broken_report输入fault→抛RuntimeError；resolve内层except观察report_error/diagnostic_error/error三个类型；回测试看D/reports/builtin_calls。关键代码只读resolve第122–127行完整诊断回退结构，不添加逐行检查题。下一步根据用户复述/观察确认本分支，然后模块级收束普通选择/诊断，再看注册与未认领特殊路径。

## 本人复述与日志缺口发现

用户正确复述分类ValueError→报告RuntimeError→内层except日志→返回描述原TimeoutError的unknown D，并指出当前日志只留报告故障，丢失初始分类故障的日志信息。按用户假设先运行该测试并开启ERROR实时日志，确实只有`failure_diagnostic_report_failed error_type=RuntimeError`，1 passed in0.52s。reports=[fault]只是测试内存，不是持久日志；若报告器先写完再抛错，可能另有初始日志，不能推广所有报告故障都必然丢日志。

当前保底日志可观测性不足，最小候选为在现有报告故障类型旁增加初始diagnostic_error安全类型，不加原文；已记future-work待完善。本轮按用户继续学习指令不改产品或把缺口算已修复；复述与设计问题识别分别记录，不推断独立编写/实网证据。

## 下一分支：内置分类正常执行但无法识别

test_builtin_unknown_keeps_safe_type_without_raw_text入口152行，E=ValueError，无自定义规则。describe_model_failure→resolve跳过空规则集→classify_model_failure正常返回unknown/unclassified→构造model/unknown/unclassified/ValueError的D。整个选择不进入诊断except，无分类机制故障；未知只是现有内置规则未识别。该概念Lesson40已讲，本次用独立单元复访，不重讲所有类型规则。

`.venv/bin/python -m pytest -q --log-cli-level=ERROR tests/test_classifier_contract.py::test_builtin_unknown_keeps_safe_type_without_raw_text`结果1 passed in0.51s，本例无诊断故障日志。完整middleware仍会记录原模型调用失败，这条局部单元没有运行middleware；不能泛称unknown模型失败不记录日志。

VS Code入口152行开始，调用describe时F11，在builtin返回后看classified=('unknown','unclassified')，返回测试后看vars(result)及error.args保持原文但D不带原文。下一步本人观察后将正常unknown与诊断回退unknown放回模块分支图。

## 本人复述：内置未识别正常返回

用户正确说明空自定义→内置不识别ValueError→正常返回unknown/unclassified→构造D、不进except。记录正常未知分类与分类机制故障的区分；未推断独立debug/实现。

## 当前9条独立测试的进度与下一条

已讲且有正确复述的6条：None→内置、合法unknown D→结束、内置未识别、规则抛错、非法描述、报告器再次故障。剩3条独立测试入口：None→下一条自定义命中、注册阶段单参数规则拒绝、App fallback=False未认领返回None。计数限定当前分类选择指南，不是整个异常系统完成度；None→下一条属于已有路径组合复访，可快速对照。

当前下一条test_none_classifier_continues_to_next_rule入口103行：同一TimeoutError，规则[skip,match]。skip记录调用后None，resolve的continue进入第二条match；match返回预建合法D=FailureDescription('model','transient','custom_timeout','TimeoutError')，validate通过并return同一D，内置不执行。custom_timeout只是测试区分自定义/内置结果的固定标签。

关键观察：resolve循环的classify.__name__依次skip/match；result先None后D；calls记录两次且顺序skip→match；回测试result is expected。对比第一条只有skip时才进入内置。入口单步即可，不重讲已理解的校验/返回细节。当前为讲解与预期，用户对此组合分支尚未新复述；后续收束剩下注册和应用特殊未认领，再进入本run规则接线。

## 本人复述：None继续下一规则

用户正确说明第一None→第二合法D→直接返回、不走内置，确认此组合分支。当前9条入口有7条正确复述，剩配置拒绝与App特殊未认领；不升级独立debug/实现。

## 下一分支：配置阶段拒绝单参数分类器

test_single_argument_classifier_is_rejected_without_invoking入口19行，直接normalize_classifiers([legacy])模拟配置入口检查。legacy只有一个参数error，与统一按两个位置参数(error,source)调用的协议不符；它只是拒绝用例，生产不保留单参适配。

模块功能为准备规则列表：检查同步可调用、可检查签名、能按两个位置参数调用，合法时返回原可调用对象构成的tuple，不包装/执行规则。实际App configure与ModelCatalog configure均在写入配置前调用该检查；复杂原子性/本run快照留后续主题。

本例输入[legacy]→inspect.signature得到(error)→signature.bind(None,'model')只验证两个占位实参能否匹配签名，不执行legacy→内部TypeError→normalize转换为固定ValueError('Failure classifiers must accept error and source')→pytest.raises捕获预期配置错误→calls仍为空。参数注解不在此校验范围，能接收两个位置参数不等于函数内部行为或返回值已验证。

单步入口19行，pytest.raises准备F10，normalize调用F11；在normalize看item.__name__、str(signature)，bind执行失败后看TypeError转ValueError；回测试calls=[]。本测试没有进入resolve，所以没有构造unknown D。normalize运行时也被resolve调用，若那里报错则由resolve的except回退，区分调用方/阶段，不把函数自身称为永远只注册使用。

下一步确认配置拒绝，再看最后App fallback=False未认领返回None；本课本人编写练习仍未开展，全部函数未逐行通读不是当前课堂完成条件。

## 本人复述：注册接口检查

用户正确说明注册先检查两参数调用协议，signature.bind只验签名不执行，单参拒绝后的ValueError由测试捕获，calls空。记录此机制复述；当前9条已有8条正确复述，最后App未认领分支待反馈，独立规则编写/调试仍未评估。

## 最后分支：应用本层未认领，返回None

test_application_no_match_returns_none入口229行，E=ValueError、ApplicationErrorPolicy默认空自定义。policy.try_describe→resolve(source=application,builtin=_application_classification,fallback=False)。应用内置只识别会话忙/审批/人工核对/持久化/项目指令等领域异常，普通ValueError正常返回None；resolve在classified is None and not fallback条件成立时直接None，不构造D，不进诊断except。

fallback控制正常未认领时是否补unknown：builtin=None且True→unknown D；builtin=None且False→None；builtin返回合法unknown元组或自定义合法unknown D仍认领；机制故障即使False仍诊断回退unknown。不能泛称False禁止产生unknown。

单元验证止于try_describe返回None与E.args未变。真实消费者RunObservation._describe_failure先读取本run标注；无标注时尝试应用描述，应用返回None才继续：execute阶段使用RuntimeErrorPolicy，非execute阶段构造应用unknown描述。None不表示原异常消失/执行成功，而是允许消费者继续选择。ApplicationErrorPolicy.describe这个另一入口会在try_describe返回None时自行补应用unknown，区别由调用方目的决定。

VS Code229行入口单步，try_describe中观察source=application、classifiers=()、fallback=False；进入_app分类函数正常None返回，在resolve看classified=None/fallback=False→returnNone；回测试result is None，原error.args保留。当前只讲最后分支，未把9条全记掌握；待本人复述后整体收束，再安排小规则练习及本run接线主题。

## 九条分支讲解收束与完整图

用户正确复述App未识别、fallback=False返回None让上层继续、不代表成功，并要求合成完整图后继续下一部分。当前9条入口均有正确复述，记录分支概念理解；独立编写/调试及整链追踪未独立验收，本课规则练习仍待安排，不把讲解收束等同全部学习目标完成。

完整配置/选择/诊断图见[调试指南](../exception-branch-debug-guide.md)“九类分支的完整调用图”，①–⑨对应各独立测试；图同步运行时重复规则校验、builtin=None的正常未认领、诊断故障无条件unknown，避免阶段与合同混淆。原异常传播/保护/应用收尾在外围，不画成所有异常都必经resolve。诊断二次失败日志缺口未修复。

下一主题开始[Lesson42](lesson-42.md)：本次运行使用哪份规则，按配置保存→run快照→上下文接线→按当前模型选规则的模块图推进；先用已有目录快照测试观察模型隔离与旧快照，不展开闭包/ContextVar全部内部实现。
