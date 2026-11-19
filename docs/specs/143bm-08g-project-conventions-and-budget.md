# S8-3 校准后修订草案：项目长期约定与前台预算

状态：用户已明确不采用项目长期约定完整预加载，原因是注入内容过多；第2、3节及对应生产改动仅保留为未采纳提案，不得据此实施。继续仅个人长期约定尝试完整预加载，项目长期约定走普通检索。用户现已确认第4节三次前台调用＋一次后续回答的预算调整，本轮仅实施该部分。依据为08f和build/spec143bm-s8-baseline/analysis.md；不覆盖旧协议及原始报告。

## 1. 问题与范围

02证明workspace/convention保存后因普通检索未命中而未注入，旧workspace/user却由偏好分支注入。06前台搜索及更新成功，第三轮最终说明被两轮交互预算阻止。本切片仅补项目长期约定完整预加载、修改评测调用预算和对应测试/文档；检索算法、后台比较候选发现、写入/来源/TTL/不重试门禁不调整。06后台找不到旧项仍为已知限制，不声称本切片解决。

## 2. 当前路径和拟议路径

现状：prepare_memory_parts→_personal_conventions→MemoryAccess.query(list/read)→个人完整组；_query_plan→search→_merge_related→剩余预算注入；最终_render用于真实请求与预览。普通聊天由MemoryAccess.personal_only限制存储访问。

拟议：仍由prepare_memory_parts装配，个人组和项目组分别完整读取。项目组仅在实际项目会话且非personal_only时执行scope=workspace、kind=convention、status=active查询，项目身份继续由MemoryAccess._binding验证，不能拿目录名替代project_id。每组最多1次list和max_limit次read，不新增分页扫描或重试。按(scope,memory_id)去重、检查list/read版本一致、完整正文及敏感项准入，继续走allowed，不直接读取DB绕过权限。

正常例：项目A有“解释设计先举例”，用户问“依赖注入是什么”；不依赖搜索命中即可把完整项目约定注入。切换项目B不读取A。普通聊天只有个人组。

失败例：项目组有两条，第二条读取失败/分页不完整/正文不完整，则项目组整体不预加载，个人组可独立成功；项目组仍可由当前普通查询召回，失败不重试。版本冲突的具体ID须从预加载及普通相关组统一排除，其他项目组失败不连带排除正常个人组。敏感项继续省略并诊断，不因本次扩范围放宽来源和敏感检查。

## 3. 预算与输出合同（拟议）

个人personal_convention_tokens默认800保持；新增workspace_convention_tokens默认800，取值0–8000，0关闭项目组。总injection_tokens默认2000保持。读取完整组后，分别计算实际渲染开销：个人优先，再项目；每组须同时满足各自上限和加入后的总上限，超限整组不加入，不选择最近N条或截断正文。剩余预算仍由既有session_directory/related顺序消费，不承诺至少保留400 token（真实包装开销和实际配置会影响余量）。某组未占用的额度可用于另一组/related，但不突破另一组自身上限。

_render新增workspace_conventions键，personal_conventions及related保留；各实际消费者同步，包括followup注入ID统计、报告过滤/成本和测试。组间不相互覆盖，prompt明确用户当前指令及项目规则优先。诊断分别记录两组的listed/read/injected/tokens/reason，不输出正文或凭证。预览与正式请求共用同一装配入口，所有读操作零写库。

## 4. 前台评测（拟议）

每例最多3轮foreground模型调用＋1轮独立followup，完整8例每批上限32。正常search→update→final占3轮，followup占1；第四轮交互在模型请求前拒绝。仅foreground预算调整，后台8例仍24。提示同步允许必要的搜索/读取后单次变更，工具失败如实报告不重试；不承诺3轮覆盖任意复杂工具流程。

预算数由同一评测常量/计划生成逻辑消费，回调准入、preflight、报告max_logical_calls、汇总assert、CLI和测试同步。新前台协议版本及哈希单独冻结，旧24次报告保留。若以后完整新旧两入口均跑，单split上限2×(24+32)=112，三split理论336；这只是新计划算术，不构成本轮112/336调用授权。实施后先离线验证，再展示新完整批次预算，不补跑旧06子集。

## 5. 文件与验证

生产影响：memory_context.py（复用现有组读取逻辑，按scope参数化，不建新缓存/注册层）、memory_settings.py、settings加载消费者及两个示例文档。新增可选配置字段不改真实配置文件；不写.env。消费者：memory_space_time_probe.py的注入ID提取；memory_foreground_evaluation.py的预算及提示；相关settings/context/foreground测试和当前记忆文档。跨500行时按既有职责拆分，先搜后写，不保留被替换的内部个人专用并行实现。

TDD验收：项目4条完整加载/无固定3条；跨项目隔离/普通聊天零workspace读取；个人与项目同时加载/预算各自超限/总上限/禁用；列表分页/读失败/版本变化/权限拒绝/敏感项/零写库；正式与预览一致；related去重与冲突排除。前台离线search→update→final→followup恰4调用成功，额外foreground调用拒绝且保留已写库状态/反馈，无后台B调用或重试。更新现有预算测试为新协议并保留拒绝与副作用断言，不放宽安全断言。

完成后跑专项、完整后端、标准Mac构建/Twine和产物接线核对。再由用户确认新真实采样预算；当前草案阶段不执行额外模型调用。

## 本轮实施：仅前台预算

用户确认三次交互＋一次独立后续回答。评测脚本统一常量驱动callback、计划及批次上限；foreground_protocol.version=2并保存3/1/32与实际driver_sha256，原协议哈希及旧报告仍保留。提示允许先查旧项再一次必要变更，错误不重试。新增真实工具离线search→record→final→followup回归先红后绿；原超预算测试迁移到第四次交互阻止，保留零写库/已写库/反馈/调用数断言。项目预加载提案不实施。工程证据见build/spec143bm-s8-budget/verification.json。

下一真实单元建议只运行前台calibration旧/新各完整8例，同新预算各最多32，总最多64；后台产品和驱动没有本轮变更，已有后台报告留用并标注运行时点。不补旧06单例，不进入validation/holdout。新旧前台应使用相同新驱动、保持旧产品冻结，执行前另冻结资产/身份；当前单元真实请求0。

前台v2真实新旧完整校准已执行，各8例各31调用，总62/64，无补跑。候选7结构完成/06失败；旧8结构完成但8次写入参数失败。06候选search→read→update已写context v2，第四次说明被预算拒绝；lifecycle标签要求旧项完全不变，但模型实际未请求改类，属于评分预期与同类更新政策待澄清，不能认定绕过改类门禁。仅候选01后续注入。证据build/spec143bm-s8-foreground-v2/{freeze.json,verification.json,comparison.json,analysis.md}。旧生产/候选源码哈希复核一致；不因06继续加预算，不进入验证/留出，人工评分未完成。


## 最新修订：移除前台评测调用次数门槛

用户确认前台次数预算无必要，移除每例/每批的人工调用次数上限。后台限制保持。回调只观察前台调用和一次独立followup，仍拒绝非foreground/followup阶段以隔离后台任务；生产运行时已有超时、取消与框架终止行为继续生效，不改配置。前台协议升v3，报告foreground_limit/batch_limit/max_logical_calls/worst_case_logical_calls为null，实际调用与usage照常记录。完整8例和split预检保持，旧报告不重写。不添加失败重试。离线回归验证超过原三次交互也能最终回复并执行followup、已写库和反馈保留；旧次数阻断断言迁移为正常结束，非预期阶段/刷新禁用保护保持。此修订不处理06时间类别评分政策，不将解除次数限制自动视为质量通过。

实施验证：前台协议v3已接线，5条迁移测试先红后绿；专项25、全后端7105通过，标准Mac构建/Twine通过。证据build/spec143bm-s8-no-call-cap/verification.json。未启动真实采样；旧v1/v2报告保留，次数超限失败不回写为成功。后续以后台主流程结果审阅为优先。
