# 异常架构复审：分层独立、调用脉络与自定义扩展

日期：2026-10-10。此文保留设计复审时的事实和取舍。用户随后“做吧”批准方向，修订Spec079已实现并通过本机验证；当前入口和使用方式以[异常职责地图](exception-architecture.md)及[Spec079](../specs/079-runtime-failure-projection.md)为准。下面“当前”描述的是复审前状态，不继续按旧三文件范围实施。

## 1. 结论与纠正

当前有可工作的失败链、RunFailureData与局部保护，但尚不能称三项要求全部满足。仅提取一个failure_data函数和修复诊断遮盖不能代替整体契约审视。最小切片用于推进实现，不能把设计视野限制为某三个文件。

已知事实：三目录59个Python文件AST导入扫描未发现model_access→runtime/application或runtime→application反向导入；这是静态检查，不覆盖动态加载/运行时语义。没有反向导入也不证明职责清晰。

语义问题：ModelResponseRejected位于model_access但由运行层响应门禁产生；应用failed将非模型异常交给模型分类器；模型分类器同时识别SDK失败和项目响应拒绝；分类/描述/日志/控制分散，缺少稳定的来源和扩展契约。先前Spec079原样集中这些判断，会保留这些混杂。

## 2. 框架能力应先复用

当前本机langchain1.4.2/core1.6.6/openai适配器已有ModelError基类及认证/权限/无效请求/不存在/限流/API/连接/超时/ContextOverflowError统一类型。OpenAI/Anthropic适配器的对应异常多重继承SDK类与框架类，保留SDK捕获兼容与cause。不能仅按之前手写SDK分支推断框架没有公共异常抽象。

合成OpenAI429经过已安装适配器_handle_openai_api_error：实际OpenAIRateLimitError，是ModelError、ModelRateLimitError和SDK RateLimitError；cause是原SDK对象，但本项目分类仍unknown/unclassified。这是适配器辅助函数探针，没有发送真实请求或完整模型invoke；具体供应商/路径仍需按实际接线覆盖，不声称所有第三方集成都已归一。

工具有BaseTool.handle_tool_error与ToolErrorMiddleware。前者处理ToolException（含子类）；后者提供同步/异步回调，显式返回内容才转错误ToolMessage，返回None则传播；框架Graph控制信号保留。参数绑定/验证发生在更前面的ToolNode，不会全部经过此回调。官方说明见[ToolErrorMiddleware](https://reference.langchain.com/python/langchain/agents/middleware/tool_error/ToolErrorMiddleware)；本机源码也已核对。已有工具配置不能不加区分地再套一层通用吞错。

## 3. 参考项目的实际自定义路径

Pi a69bef789：extensions注册工具→wrapRegisteredTool→wrapToolDefinition执行自定义definition.execute→agent-loop捕获任意工具执行错误，生成isError工具结果；模型Provider.streamSimple必须遵守AssistantMessageEventStream终局契约，error/aborted包含错误助手消息。扩展runner还有ExtensionError及onError监听。此证据说明扩展依赖既有协议/边界，不证明有一个统一异常注册器或要求所有模块错误继承同一根类。

AgentX3fcfa0e：InsufficientBalanceException继承BusinessException；业务服务抛该类；GlobalExceptionHandler的BusinessException处理器统一HTTP结果。另外模型TokenStream.onError是独立的流失败入口。共同基类用于具体业务边界，不等于所有SDK/框架/工具故障同一根类；其直接错误文本展示不照搬。

## 4. 当前自定义能力分级

| 能力 | 当前状态 | 证据/限制 |
|---|---|---|
| 定义Python Exception子类并抛出 | 有 | 未匹配模型分类会unknown，原异常传播；不是完整分类扩展 |
| 自定义可恢复工具异常反馈模型 | 有框架基础且项目工具已用 | 本轮ToolException子类+handle_tool_error实际返回ToolMessage(error)，call_id保留，合成内部正文未反馈 |
| 自定义模型异常类别，不改通用分类器 | 尚无明确项目入口 | 当前固定isinstance分支，无项目classifier注入/映射协议 |
| 自定义运行/应用领域失败描述 | 尚无系统契约 | 增加类型通常要改多个分类/展示点，来源不够清晰 |
| 自定义异常恢复/公开展示 | 局部 | tool handler及Web public_error能改，但不是独立、成套且已验证扩展接口 |

本轮探针仅框架工具调用，非整Agent任务端到端验证。类型提示/合法字形不保证字符串本身不含秘密。

## 5. 推荐方向（尚待确认，非实施Spec）

各层拥有自己的异常语义与适配/分类入口，共享一份不依赖SDK/框架/会话/UI的安全失败描述契约；来源由实际捕获边界/上下文提供，不能仅凭TimeoutError等类型猜。运行层把描述叠加执行阶段转换为既有RunFailureData，应用只消费运行失败和识别应用自身领域错误，展示层做公开映射。

拟议共享契约只描述category/reason/error_type等安全事实，执行阶段、run/session关联和恢复动作属于使用它的层。不在模型层引入RunFailureData、publisher或会话对象。若需要新增来源字段先确认内外schema，不能悄悄改变事件/DB。共享契约是基础依赖，不是新的业务运行层。

模型入口优先识别已安装框架ModelError族，补未归一SDK/本地/自定义供应商；框架is_retryable只作为信息，不能自动替代预算/阶段/副作用策略。Runtime只处理循环、门禁、工具/控制自身语义；App管理会话/准备/保存/收尾与公共显示。分类函数不负责实际重试、压缩或执行工具。

自定义扩展首选显式注入到相应模型/运行/应用实例的分类函数或有序映射，匹配则给安全描述，不匹配交默认映射，再无匹配保留unknown。注入顺序、优先级、同步/异步、故障回退需要正式契约；不采用全局可变注册表。普通扩展不能吞掉Graph控制/取消，不能把副作用未知降为安全成功。工具错误反馈优先用现有框架hook，分类扩展与是否回流模型的处理策略分开。

共同Exception父类仅在明确需要统一捕获的某层错误族中考虑；不要求SDK/框架/所有插件都继承项目根类。ModelResponseRejected归属需重新设计，但不能让model_access重导出runtime类来假装兼容；现有导入兼容/签名限制在修订Spec单列，必要变更必须先获得用户明确授权。

## 6. 三条必须可追踪的示例

| 示例 | 预期责任链与扩展要求 |
|---|---|
| 模型超时/新供应商配额异常 | 模型边界归一或本模型实例扩展→运行层失败观察→应用失败收尾→终局展示；新供应商不改App |
| 自定义工具的数据缺失 | 工具明确可恢复handler→错误ToolMessage→模型决定下一步；新工具不改runtime异常if列表 |
| 应用报告或会话保存失败 | 应用自己的错误映射→finalize/cleanup失败→公开映射；不能由模型分类器假装它是供应商失败 |

每条图都要标出产生者、捕获者、描述转换、恢复决定、日志/事件、原异常传播与终局时点；文档中的类型目录每项绑定真实代码/测试。读懂局部行和独立追踪链路分别验收，不仅增加函数/基类数量。

## 7. 修订后的验收与推进

1. 先确认上述分层与共享契约方向，再重写Spec079；旧三生产文件清单不继续使用。
2. 明确每层归属、框架复用、现有导入/API兼容和分类码，画三条示例，定义注入与安全失败描述校验。
3. 验收包含：下层独立导入/运行、无反向依赖；分别新增模型/工具/App自定义错误无需修改不相关层；两个应用的映射互不污染；未知/诊断故障保留原异常；控制/工具副作用/恢复语义不回归。
4. 正式实现按依赖自底向上分片，但沿完整设计逐步接线；TDD先红灯再实现，不用只移动文件冒充完成。

本轮仅设计复审/源码与合成探针，未修改产品/测试/依赖/环境。当前59文件导入检查、框架异常辅助转换、工具错误反馈是不同证据，不代表全量功能/生产验证。用户表示局部代码能看懂但整体脉络仍不清楚，不能据此记独立追踪或面试掌握。

## 8. 当前实现的复杂度复审（2026-10-11，讨论，未批准改代码）

用户在总图后仍反馈复杂，询问设计是否常见、Pi/AgentX细节是否如此。暂停课程，评价Spec079实现的可读性，不把“生产级目标”当复杂度自动合理化。此前章节是历史设计，此节针对已实现代码。

### 当前判断

保留分层和失败事实契约，但普通失败的实现路径有收敛空间。框架/参考项目能证实异常归一、边界捕获与错误反馈/事件的先例，不能证明本项目整套嵌套resolver、身份报告表与多种回调签名是普遍行业标准。没有总体统计，不声称大多数项目采用/拒绝本方案。

### 当前源码证据

- 本机Pi=a69bef789，AgentX=3fcfa0e，LangChain1.4.2/core1.6.6/LangGraph1.2.12。
- Pi的api/openai-responses.ts181–191：provider捕获故障，AssistantMessage.stopReason=error/aborted、errorMessage，推error事件；agent-loop.ts346–358将最终消息返回，196–200据stopReason结束turn/agent。agent.ts504–526另兜意外运行异常为失败消息/终局；工具executePreparedToolCall681–708捕获工具错误生成isError结果。它有内部归一/格式化/控制细节，不能称没有错误适配，普通失败公开协议更集中。
- AgentX的PortalAgentController35–37→AgentAppService.createAgent84–86抛InsufficientBalanceException→BusinessException→GlobalExceptionHandler32–36统一业务HTTP结果。模型流走AbstractMessageHandler342–355 TokenStream.onError：发送结束错误、报可用性、调用阶段/完成钩子。TracingMessageHandler305–317另包装上下文。因此不能将AgentX整个产品误称只有单一catch或断言都比本项目简单。
- LangChain官方[Model exceptions](https://docs.langchain.com/oss/python/langchain/models#model-exceptions)提供ModelError族；[自定义middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom)支持边界catch/raise；[Tool error](https://docs.langchain.com/oss/python/langchain/middleware/built-in#tool-error)可显式反馈或传播。框架支持分层处理，但不要求本项目的报告表/双resolver组合。
- 本项目RuntimeErrorPolicy73–77以外层resolve调用模型描述入口；模型describe_model_failure95–104再次resolve，默认模型路径存在两个resolve边界。规则注册/策略构造/执行都能再次normalize；内部报告还需要ContextVar/context回调、id(error)表及App身份复用。这些是实际自定义适配成本，而非LangChain规定流程。

### 应保留与待审视

应保留：模型/运行/应用错误归属；可恢复工具反馈与致命/副作用未知区别；停止/取消/审批控制；主要异常不被诊断/收尾故障掩盖；安全事件与保存确认。参考Pi的捕获所有工具错误策略不能直接替换本项目副作用未知门禁。

待审视候选（非实施计划）：普通模型委托是否可避免重复进入通用resolve；签名适配是否仅在注册时执行、内部固定协议；描述是否有更显式的跨层传递入口。业务处理分支数量与普通路径绕行是两种问题，应分别评价。

内部报告表当前承担真实契约：下层按实际模型ID分类，同时上层仍收到同一原异常，事件/公开签名保持。取消此机制不能无代价改为运行失败包装或统一结果；会改变现有异常对象/捕获契约，需要用户另行确认。保留精确原异常的前提下，只能先收敛重复委托/适配，不能承诺直接照搬Pi协议就兼容。

设计验收需增加：从入口能直接追出普通路径，业务判断与兼容/观察辅助分开，扩展一个错误不要求理解全部框架适配。已有测试通过只证明已测功能，不证明可读性最优；用户仍不清楚不是靠再加更大的总图解决。

本轮仅本地源码/官方文档对照，未跑新全量测试/实网，不改产品/测试/.env/依赖，不批准重构或变更异常契约。

## 9. 未知写结果的必要性与收敛方向讨论（2026-10-11，未实施）

本节包装载体为上一轮候选；用户随后提出原异常属性方案，当前推荐转为第10节与Spec080，不实施RuntimeFailure包装。

用户要求先解释核对门禁为什么存在，并在实施前聊清完整方案。当前只讨论，不将下面候选写成已批准Spec。

### 核对的目的与当前过度保守之处

目标是避免将“没有确认结果”误当“确定未发生”，进而自动重放有副作用操作。普通覆盖写入同一内容本身通常有幂等特点，不能用重复发券/支付风险直接替代文件写风险；需根据操作阶段、原因、目标是否被其他操作改变判断。

当前_replace_temp对os.replace的所有OSError均转WriteOutcomeUnknown，App在之后普通run准入时阻止该会话。准备阶段失败已另走ToolException。这个提交边界判定是统一保守策略，没有精细区分；不能称Python/框架要求所有替换失败都结果未知。Python官方只承诺成功替换是原子操作：[os.replace](https://docs.python.org/3.12/library/os.html#os.replace)。此前“真实替换后人为抛错”探针是故障注入，不证明本地正常文件系统的普通报错都会在替换后发生。

核对源码未找到对受限会话完成只读核对并明确解除门禁的公开闭环；新建/恢复ready会话的False初始化不是该闭环。当前只能称停止/门禁基础，不能称成熟恢复功能。Pi a69bef789 write.ts202–229在mutation queue内等待mkdir/writeFile结束，并检查abort；agent-loop工具catch统一错误结果，核对的普通写失败链没有本项目这套永久会话核对门禁。

本次建议分开决策：异常通路结构收敛先保持工具行为；普通已确认失败与真正未知、允许只读核对与解除条件作为独立工具策略切片，不能悄悄删除现有门禁或一起混改。

### 候选结构：单一内部运行失败载体

建议让Runtime的未处理执行失败携带原异常E和统一描述D，通过一个运行失败载体传播；App直接取得E、D，补stage、收尾、发终局，不再先走reporter回调、再通过id(error)表回查。普通模型分类只由模型层执行一次，Runtime不再把整个模型入口当作另一个普通分类器嵌套解析。

载体暂名RuntimeFailure，归Runtime，Model Access不依赖它；它不是AIMessage、不是正常回答，也不是新Agent loop。可恢复工具仍回ToolMessage；UserStop/外部CancelledError/Graph控制保留专门路径；保护分类与副作用核对不得被一般扩展覆盖。

候选公共应用合同：app.run/arun仍抛同一个原异常（推荐），内部用载体；还原需要退出载体捕获上下文并完成收尾，验证类型/对象身份、原cause与traceback，不能只验证is。也可选择App外部统一RuntimeFailure，但属异常捕获行为变更。已通过异步问题询问用户偏好，尚未收到答案，不将预选项当同意。运行层可独立调用入口及middleware的异常行为兼容范围也需在正式Spec中列明；不能仅因App签名没变就宣称全API兼容。

结构变更预计超过8个文件：Runtime错误协议/模型与工具观察/同步异步调用/上下文接线，App观察/会话与恢复，以及对应测试/文档。模型配置、事件/DB字段、前端、工具业务策略拟保持；分类器旧单参/双参支持本轮不删除，不为微小签名检查成本引入另一套注册平台。

最脆弱前提是载体不会抢在现有恢复/框架控制之前包装原异常。正式Spec需逐捕获边界核对middleware顺序与已执行工具，并用同步/异步、独立Runtime调用、直接middleware、审批/停止/溢出、保存次错测试证明。若需改变已公开Runtime异常合同，单独请用户明确接受，不能伪装纯内部重构。

### 实施前验收条件

先定三层输入/输出与外部异常合同，列明保留/替换/删除的组件及调用方，写阶段Spec后批准。TDD验证模型分类一次、E/D直接传递、原异常/原因链保留（若选此合同）、可恢复工具继续、未知副作用门禁、停止/取消/审批控制、App领域/保存错误、诊断故障回退、跨模型run快照/实例隔离和日志脱敏。

发布前沿既有构建/全量后端与前端检查，不修改.env/依赖，不顺带改变重试、压缩算法、权限、工具核对策略或DB。当前没有实施授权，不创建RuntimeFailure源码或修改旧回归来预先接受新行为。

## 10. 评估用户的原异常属性方案（2026-10-11）

用户确认应用入口同一原异常合同，建议描述直接附在原异常上。推荐采用，当前范围与验收见[Spec080草稿](../specs/080-exception-failure-annotations.md)，未实施。

避免包装的动态类型变化、多入口还原与异常context变化。具体ContextCompactionError253/349是否受包装影响取决于包装位置，不当已发生bug；本项目从未实施包装。真实ModelRetryMiddleware显式retry_on=ModelTimeoutError识别已标注原异常（2尝试），不识别不继承该类的合成包装（1尝试）。

9类本机异常标注通过，含OpenAI/Anthropic APITimeoutError、框架ModelTimeoutError、项目与控制异常。原args/cause/context/suppress保持，拒绝setter时记录诊断/保留原E，辅助函数不进入E traceback；非法标注/类型不符/getter故障回退探针通过。无实网。

必要修正：读写失败固定日志而非空pass；App验证描述及原异常类型，不盲信getattr；属性携run_id防foreign scope串用。单值属性无法保证共享异常并发精确分类，拒绝foreign scope后可能退化，不隐藏为完全等价旧表。

ContextVar删除范围修正：它还在传本run策略，属性只替代上行报告。删除reporter/id表，保留仅策略绑定，摘要和模型快照仍接线；内部未发布绑定迁移单列，既有公开入口保持。普通model同时去外层通用resolve，原控制/原因链/事件/工具策略保持。

预计5生产文件及测试/文档，无需包装失败类/各出口解包。本轮只做Spec和评估记录，未改产品；用户真实方案参与与代码示例不升级为独立实施/面试能力。

## 11. 自定义异常与架构决策的顺序（2026-10-11）

用户询问属性方案是否没有自定义异常，以及应先讲需求还是讨论常见架构。明确已有领域异常保留，仅不新增通用RuntimeFailure；属性解决描述传递，不替代业务类型。自己的业务语义确需表达时定义领域异常，已有SDK/框架类型够用时保留；停止/取消/Graph信号单独处理，不要求所有类继承一个项目根类。

决策先后：具体失败场景/期望行为→接口与诊断合同→分层职责→框架/参考模式比较→最少机制→TDD与调用链可读性验收。用户确认业务边界/风险和体验；助手负责穷举候选常见路径、核对框架和源码并给取舍，不要求用户先设计异常类树或列完供应商异常。

类型表达“发生什么”，处理策略回答“继续/恢复/停止/暂停/失败”，描述负责安全诊断，事件表达观察与终局；不能从一种描述category自动推出控制动作。保留原异常指边界收到的实例，不禁止有明确业务语义的既有转换，如摘要故障转ContextCompactionError并保留cause。

共同异常基类只有某个错误族需要一致捕获/字段时才引入；AgentX BusinessException面向业务HTTP，LangChain ModelError面向模型集成，作用不同。Pi的统一消息/事件协议也不是本项目必须把SDK异常包装成一个根类的依据。

当前先确认行为与接口，Spec080仍草稿，核对门禁政策单独待讨论；用户此问不是批准实施。没有产品修改或新运行证据。

## 12. 用户完整健壮性需求的架构方向（2026-10-11）

已读用户粘贴原则及本轮工具重试/反馈/熔断/备用、模型重试/切换/质量需求，形成[Agent健壮性设计](../agent-resilience-design.md)。来源与处理方式是两个坐标；类别可跨重试耗尽→反馈，不能把来源或单个TypeError直接映射动作。

核对在线SDK已有指数退避/抖动，缺项目attempt治理；工具没有通用重试。重放安全、SUT结果与工具故障区别、熔断Closed/Open/HalfOpen及健康作用域、资源隔离与权限区别、历史不冒充新证据、流式部分输出、候选能力/实际模型轨迹、质量与传输健康分开，均纳入目标。

LangChain四种现成middleware优先评估本项目接线；Pi provider-retry生产调用关闭SDK重试并取消等待，AgentX应用调用HA gateway取实际实例/降级候选，未将其内部机制未查部分算已确认。五层/大一统重构不是当前结论，按独立能力切片推进。

无效模型调用回流与当前fatal门禁冲突，用户已通过选择回复确认可配对反馈/协议破坏停止，需独立响应归一与有界修复Spec。Spec080仍是基础草稿，后续能力没有实现/验收声明。本轮未改产品。

## 13. Spec081后的可读性与观察诊断复审（2026-10-11，仅评估）

用户指出observe用字符串方法名、普通Exception隔离可能掩盖编程错误；分类适配/重复规则/兼容入口仍复杂。当前暂停课堂推进，先验证建议，没有将讨论当成新的重构实施授权。

无网络探针实际复现：observe(obj,'begni',...)返回None、记录AttributeError并标记_observation_failed，随后正确begin调用次数仍为0。这是潜在拼写错误的失效机制，未发现或声称实际生产调用已经拼错。模拟callable捕获边界确认lambda内属性拼错仍被Exception捕获；直接传obj.begin时，方法查找在进入helper前发生，缺失会直接抛出，且更利于IDE导航。可调用改造需保留None/已降级观察器和日志/返回fallback语义。

不建议生产逻辑按测试模式对AttributeError/TypeError改变传播：类型本身不能区分调用拼错、参数错和观察内部的普通故障；生产隔离与测试严格验收可以同时成立。建议正常路径测试显式检查无非预期观察诊断/降级及必要事件，故障注入测试明确断言预期降级；可调用方法绑定用于更早暴露方法名错误。具体验收合同和迁移范围需补入执行Spec后再实现。

RuntimeErrorPolicy保护分支和_runtime_classification确实重复compaction/tool_batch，通过公开describe时两类先命中保护，因此后者两条不可达。收敛时须保留保护分类优先于自定义规则的合同。normalize_classifiers在配置和resolve_failure执行时重复适配，有准备工作集中化空间；两种签名兼容是已确认合同，不能直接删inspect或限制新签名。

模型层保留一次resolve_failure负责自定义规则顺序/None与合法unknown/内置分类/校验/诊断回退，属于三层共用算法；Runtime普通model的外层resolve套层已移除，不能把当前描述为仍有旧双resolve。failure_data和6个bind入口仍存在，但产品主链不再调用它们（session_service有一个旧bind导入）；已有兼容测试/外部导入合同须明确后再删，它们不再各持ContextVar。

emit_event工厂在publisher存在时同步立即求值，不缓存/排队工厂；补明此合同有价值，未来延迟求值须重新审查循环变量捕获。events/turn_observation局部导入置于标准库前的风格差异成立。

定向现有测试37 passed in0.94s；探针退出0。仅复现和评估，不宣称已修复、完整全量或真实服务验证。优先建议可调用观察入口及严格观察验收、去重两条保护规则；共享resolve和公开兼容边界保持，后续是否集中适配另定范围。

实施更新：用户随后明确“做吧”，按Spec081第7节实施绑定方法调用、测试健康断言和两条重复规则去重。新增17个场景，先7红灯后定向51通过；全量1011/前端27及构建通过。普通观察内部AttributeError/TypeError仍隔离，缺失方法查找和非法字符串操作在helper前/边界前暴露。签名适配准备工作与旧入口进一步收敛未包含在本轮；本节上文是实施前评估证据。

### 兼容入口的具体对象与保留依据

用户追问“兼容的什么”，核对HEAD与当前调用方：兼容的是前版本项目Python模块导入名、调用参数/默认语义与嵌套绑定还原，当前没有外部消费方/发布版本需求证据。failure_data(error)与5个bind入口在已提交HEAD已有；bind_failure_policy不在HEAD，是新异常切片内部策略注入入口，当前只有1个测试使用，不宜泛称已发布旧API。

当前bind_run_event_publisher仅2个测试使用；其余4个早期bind入口无实际调用；failure_data仅兼容测试使用。测试依赖不证明产品必须保留。前轮保留主要沿Spec079/081明确兼容范围，不能据可import就推定正式对外支持。

须区分配套current_*读取函数：current_run_control仍被Bash消费，用于停止/活动进程控制；current_run_event_publisher仍被AsyncApplication.aclose消费，用于阻止活动run内部关闭应用。getter不全部是闲置兼容代码，若统一到current_run_services应同步迁移这些生产调用及回归，不能只删除。旧bind桥目前写单一RunServices ContextVar，并未保留6套独立通道。

可讨论的清理方向：只有旧测试或无调用的内部bind/failure_data入口迁移至当前合同；真实App/Runtime运行接口与有生产调用的读取职责明确保留或迁移。用户本轮是询问理由，没有作为删除/破坏性接口变更授权。
