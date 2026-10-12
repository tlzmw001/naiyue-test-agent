# Lesson 42：本次运行使用哪份异常规则

> 2026-10-11开始。4个场景均已有正确复述，当前讲解收束并进入Lesson43；下文按课堂推进顺序保留当时状态。独立debug/实现未验收。

## 模块功能与具体规则形状

Lesson41解释给定规则怎样选择D；本主题解释规则由谁配置、存在哪里、本run取得哪份、middleware怎样收到。当前3类归属：模型规则按model_id存ModelCatalog._error_classifiers；普通Runtime规则存SessionService._runtime_classifiers；App领域规则存SessionService._application_classifiers。保存的是原同步可调用对象的tuple，不是失败描述或旧签名适配包装。

示意配置（与当前数据结构一致，非真实运行输出）：

```text
model rules: {'A': (quota,)}
读取未配置的B：get('B', ()) → ()
runtime rules: ()
application rules: ()
```

本例quota来自已有测试，只识别合成VendorQuotaError，产生model/limit_exceeded/vendor_quota/VendorQuotaError的D；不是SDK真实异常或业务配额证据。

本例两个model_id复用同一个假模型对象；验证的是按ID的规则映射隔离，不是SDK实例或凭证隔离。

## 完整准备与执行交互

```text
app.configure_error_classifiers
→ SessionService先校验规则/准入，再保存到对应层
→ 一次run开始：SessionService._run_steps
→ catalog.failure_describer()取得按model_id的规则映射快照和可调用描述端口
→ RuntimeErrorPolicy(runtime规则, model_describer=该端口)
→ RunObservation.bind把本run策略放入RunServices.errors
→ make_invocation_context把同一策略写入context['_failure_policy']
→ middleware捕获模型异常，describe_failure_in_context读取策略
→ RuntimeErrorPolicy.describe(source='model',model_id=本次模型)
→ 快照描述端口(model_id,error)选该模型规则
→ describe_model_failure→Lesson41的resolve选择图→D
```

App领域策略另挂observation.application_errors，用于App自己的描述选择。Runtime规则不是所有模型失败先过一遍的全局规则；普通model边界直接委托模型端口，保护类型另有固定分支。Publisher/run归属检查与ContextVar内部实现本次只定位，不冒充已逐行学完。

两种端口：分类器(error,source)返回D或None；本run模型描述端口(model_id,error)选规则后返回D。这是不同职责，不是兼容两种分类器签名。

## 当前先跑的分支：按模型选规则与保留旧快照

复用tests/test_layered_failure_contract.py中的test_model_classifiers_are_model_scoped_and_snapshotted，入口87行。它直接用假模型构造目录，配置与描述不调用模型/网络。

| 时间/分支 | 输入与配置 | 预期描述原因 |
|---|---|---|
| 配置后，当前目录A | A有quota，error=VendorQuotaError | vendor_quota |
| 配置后，当前目录B | B未配置规则，同一error | unclassified |
| 取得快照后清空当前A，调用旧before | 旧快照仍有A→quota | vendor_quota |
| 清空后，直接读当前目录A | 当前A规则为空 | unclassified |

before=catalog.failure_describer()返回描述函数，不是D；before('A',error)执行选择才产生D。快照冻结的是模型ID到规则tuple的映射，不深拷贝函数及其捕获对象。

该测试直接更新ModelCatalog以隔离快照机制；公开App.configure入口拒绝活动run期间改规则，不能据测试推断运行中可用App热改。本次还会单独用实际接线测试定位run如何取得旧规则，不把目录单元当整条middleware运行证据。

## VS Code入口与观察

87行入口断点，准备假模型F10，configure/failure_describer调用按需F11；看目录catalog._error_classifiers。取得before后看它是可调用对象；进入返回的describe时，局部selected保存旧映射，model_id为当前A或B。清空A后对照当前catalog映射和before内部selected；每次正常D用reason/error_type观察即可。

当前代码定位：model_access/__init__.py的ModelCatalog configure/failure_describer；application/session_service.py的run策略构造；application/run_events.py的bind；agent_runtime/invocation_context.py的显式context；agent_runtime/errors.py的policy委托。只展开职责/分支，闭包/上下文内部机制等用户点名。

下一步根据目录/快照测试观察确认模型规则隔离与旧映射，再追本run的RuntimePolicy/context接线。本人理解、debug、独立实现未评估。

助手当前运行该单例：`.venv/bin/python -m pytest -q tests/test_layered_failure_contract.py::test_model_classifiers_are_model_scoped_and_snapshotted`，1 passed in0.77s。仅目录/描述快照单元证据，不是完整App/网络或本人debug验收。

## 本人复述与固定场景范围

用户正确说明规则按model_id区分、A有quota/B无规则，before持有当时映射，清空A后旧快照仍用旧规则、当前目录用新配置。记录模型规则/映射快照机制复述，不自动记独立debug/实现。

本主题按4个场景单元固定范围，不按所有源码if计数，也不涵盖外围恢复/停止/应用收尾：

| 场景 | 问题与测试入口 | 当前状态 |
|---|---|---|
| ① 按模型规则与快照 | test_layered_failure_contract.py::test_model_classifiers_are_model_scoped_and_snapshotted | 正确复述 |
| ② 本run策略注入context | test_run_services.py::test_single_binding_builds_explicit_context_and_restores_parent | 当前讲解 |
| ③ 归属检查与退出还原 | test_run_services.py::test_context_projection_rejects_mismatched_run；复用②嵌套/异常退出还原 | 当前讲；新增独立session/run不匹配单元验证policy/control/publisher都未注入，旧部分复述不升级本新机制理解 |
| ④ Runtime模型委托与默认行为 | test_failure_annotations.py::test_application_reads_annotation_and_classifies_model_once（跨层）；test_failure_extension_boundaries.py::test_explicit_default_failure_conversion_is_independent_of_bound_model_catalog（局部） | 待讲；标注/收尾只定位，深入留Lesson43–44 |

当前已复述1个，余3个；②③同一短测试覆盖，学习连续走完整测试，不要求在中途停下或补多轮基础题。当前count是教学分组，不是声称所有配置/隔离分支均完备。

## 当前场景②：把同一策略对象交给调用上下文

test_run_services.py入口15行创建publisher('s','r')、control、policy=RuntimeErrorPolicy()，再services=RunServices(errors=policy,...)。bind_run_services在with范围提供当前services，make_invocation_context('s','r',None,None,())读取它；publisher匹配后，把services.errors放进context['_failure_policy']。这是同一对象的传递，不是复制/新建Policy，不在此重新分类。

该单元构造的是默认Policy，没有配置模型描述端口；只验证策略对象的接线身份。真实run在SessionService._run_steps先用runtime规则和catalog.failure_describer()构造Policy，再由RunObservation.bind执行同样的services/context交付。不能把默认Policy单元当完整模型快照/middleware接线证明。

本例生成的context实际字段形状为session_id='s'、run_id='r'、_run_control=control、_failure_policy=policy、_run_event_publisher=publisher。测试在context构造之后断言context['_failure_policy'] is policy，证明传递身份。

VS Code15行入口断点，准备对象F10；进入bind与make_invocation_context按需F11。在factory看services.errors与context字段，在测试帧看services.errors is policy、context['_failure_policy'] is policy。不展开整个control/publisher树。后半测试另有异session拒绝、内层空services覆盖/异常还原与最终None，可完整单步，作为场景③后续说明。

当前下一步根据这个测试的实际观察确认“同一Policy从services进入context”，再整体讲其归属/还原。闭包/ContextVar内部语法仍按需展开。

助手实际运行当前单例：`.venv/bin/python -m pytest -q tests/test_run_services.py::test_single_binding_builds_explicit_context_and_restores_parent`，1 passed in0.80s。另用本地合成绑定投影打印session_id=s、run_id=r、same_policy=True，退出0；仅验证当前对象/上下文边界，未执行middleware或模型请求。

## 本人复述②正确，点名深入Token与reset

用户正确说明RunServices携带本run policy，make_invocation_context将同一对象放进_failure_policy，不重新分类/复制。记录场景②复述理解，当前①②已复述，③④余2个；随后用户点名ContextVar.set返回Token及reset为何None，停止其他方向，只解释恢复机制。

Token是set产生的恢复凭据，公开token.var指向所属ContextVar、token.old_value是set前绑定值；没有旧绑定时是Token.MISSING。reset(token)恢复那次set之前的绑定状态，不是固定赋None；旧值为对象时恢复同一引用，不复制整个RunServices。未绑定状态get返回声明的default，本项目default=None，所以最外层撤销后看到None。依据[Python3.12官方文档](https://docs.python.org/3.12/library/contextvars.html#contextvars.Token)。

当前测试的两个with形成：初始未绑定/getNone→外层services（外token旧值MISSING）→内层空RunServices（内token旧值为外层services）→内层异常finally reset恢复外层→最外层finally reset恢复未绑定/getNone。作用域还原不回滚对象内部状态，finally确保正常或异常离开都尝试恢复。

助手独立Python进程使用项目实际_RUN_SERVICES/两个不同RunServices验证：初始None=True、外token.var.name=naiyue_run_services、外旧绑定MISSING=True、外set后isOuter=True、内旧值isOuter=True、内set后isInner=True、内reset后isOuter=True、外reset后None=True，退出0。只是上下文绑定边界探针，未操作用户IDE或发模型请求。

VS Code保持原15行测试入口，进入run_services.py第32行，执行set后看token.var.name/token.old_value；到36行reset前后看_RUN_SERVICES.get()。内层旧值是外层services，内层退出后测试30行is services成立；最外层退出后31行None成立。单步里的同名token属于不同bind函数帧，每次set有各自token；观察时保持当前帧。

Token恢复已讲，用户对此机制尚未新复述；场景③的归属检查也仍待整体说明，不能提前标场景③完成。唯一下一步停在当前嵌套恢复观察，根据疑问解释，再讲所属session/run检查。

## 场景③继续：归属匹配才投影到context

用户说继续，本次只补make_invocation_context的归属检查。services.publisher若存在，matches(session_id,run_id)要求两个标识同时相等；不匹配跳过这份services的policy/control/publisher/观察器投影。返回的是调用者自己的context，不是None或异常；仍在with范围的当前services没有被清空，最后退出再由token还原。此结论限context投影，不声称所有内部读取路径/租户授权已隔离。

源码完整条件是publisher is None or matches(...)：缺publisher时允许单独投影存在的策略/控制服务；正式run与本次单元均带publisher，不能误讲所有无publisher情况都会拒绝。services不存在则不注入；各workspace/grants/read资源参数仍按调用者输入处理，本例None/空所以只剩标识。

旧测试只直接验foreign control缺失，为教学分支补独立test_context_projection_rejects_mismatched_run（59行入口，different-session/different-run两参数案例），不改生产或helpers。服务属于s/r，请求other/r或s/other，最终context只含请求标识，且当前services仍是原对象；这样直接验证policy/control/publisher均未误注入，不依赖守卫抛错被隔离。

VS Code选择参数案例Debug Test，59行入口：构造服务F10，make调用F11，在factory看session_id/run_id、publisher.matches(...)为False，context不含_failure_policy等；回测试context精简字典与current_run_services() is services成立。无需改变环境配置或发模型请求。

验证`.venv/bin/python -m pytest -q tests/test_run_services.py`，5 passed in0.77s。新增是既有行为的边界测试，非新功能/修复，不虚构TDD红灯；无本轮全量/前端/实网证据。当前③归属与还原均已讲，用户复述仍待；之后仅剩④Runtime委托/默认场景讲解。

## 本人复述③正确

用户正确说明session/run匹配才注入、不匹配只跳过且当前绑定不清空，退出with用token恢复。记录带publisher的正式路径归属与作用域恢复概念复述；无publisher允许单独服务的源码条件仍保留边界，不把复述升级独立debug/实现。①②③已复述，当前最后④。

## 最后场景④：普通模型异常的委托与缺省

限定普通模型异常，不覆盖Runtime更早的进程/Graph信号和固定保护分支。middleware调用describe_failure_in_context(error,context,source='model',model_id)，取得context策略，若不是RuntimeErrorPolicy才构造默认策略；policy.describe普通model分支有可用model_describer则调用(model_id,error)，否则直接describe_model_failure(error)无自定义模型配置。随后验证D的model来源/原类型；端口或返回校验普通故障在本分支诊断回退，正常路径本次重点。

有端口主线：App在run准备取得catalog.failure_describer快照→RuntimePolicy→services/context→middleware→Runtime.describe→快照describe(model_id,error)→按ID取规则→describe_model_failure→resolve→D→Runtime校验后返回。普通model分支不会先用Runtime自己的classifiers再跑模型规则，也不进行第二遍model resolve。

两个端口不同职责：model_describer(model_id,error)决定用哪组模型规则并产D；ErrorClassifier(error,source)在该组里认领或None。不是兼容两种分类器签名。

缺省主线：直接RuntimeErrorPolicy()没有model_describer→describe(error,source='model')→默认模型内置分类→D。该显式Policy不从当前services偷偷补一个别的model_describer；是否用某份服务策略由context接线/传入对象决定。测试分别有本地假模型App跨层链和局部缺省链，不混作真实模型请求证据。

| 正常分支 | 独立入口与输入 | 观察 |
|---|---|---|
| 本run有模型端口 | test_failure_annotations.py::test_application_reads_annotation_and_classifies_model_once，51行，A配置classify识别合成VendorQuotaError | Runtime self.model_describer存在，model_id=A；classify一次，D.reason=vendor_quota；标注/App读取只定位，内部留Lesson43 |
| 显式Policy无模型端口 | test_failure_extension_boundaries.py::test_explicit_default_failure_conversion_is_independent_of_bound_model_catalog，37行，TimeoutError | self.model_describer=None，默认transient/timeout；绑定另一份带catalog端口的services后，仍调用原policy，before/during相同 |

VS Code各测试入口断点，准备F10、describe调用F11；Runtime model分支看model_id、self.model_describer is None、source，端口回D后看description.reason/error_type。正常有端口的Fake App测试要经过框架调度，可保持justMyCode默认主要看项目函数；标注/清理跳过实现细节保持当前目的。缺省测试不发请求，catalog只用于给另一份环境services装端口。

助手当前两单例共同运行：2 passed in0.77s。无本轮代码/测试变更、网络或新全量证据。本主题4场景讲解已覆盖，①②③有正确复述，④待反馈；独立规则编写仍待，不标全部目标完成。下一步确认最后委托/缺省关系，收束规则传递地图后进入Lesson43的描述随原异常回传。

## 四场景复述收束

用户正确说明有model_describer按model_id委托快照、没有端口用默认内置规则，限定普通模型异常。4场景均有正确复述，记录规则来源/接线概念理解；独立编写、调试及完整系统掌握未独立验收。本课分支讲解收束，进入[Lesson43](lesson-43.md)的描述标注与原异常回传。
