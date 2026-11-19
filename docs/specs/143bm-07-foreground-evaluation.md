# Spec143BM-S7：前台独立评测驱动

2026-11-17 当前状态：五项评测问题已按 [S8-0 第15节](143bm-08-memory-tool-action-contract.md#15-s8-0-实施记录2026-11-17)接线修复，验证见 build/spec143bm-s8-0/verification.json；新真实模型批次尚未运行，质量仍待验收。以下审查描述保留为修复前证据。

2026-11-17 审查补充：当前实现存在超预算遗漏最后工具反馈、followup 准备异常丢失 case/调用计数、清理异常阻止报告生成、显式 refresh 混入后台及 06 回答标签沿用后台预期五项问题，尚未修复。详细复现与修复合同见 [S8 第 2、8 节](143bm-08-memory-tool-action-contract.md)。本片的五项修复、前台标签、validation/holdout 入口和后续效果验收统一归入 S8-0/S8-2—S8-4，不再独立排期。下文已有工程测试及首轮模型运行是历史证据，不代表这些边界已经通过。

2026-11-17，用户确认保留当前检索，继续下一切片。本片先完成前台驱动的工程接线；真实模型完整批次另行执行，不能把脚本替身当语义验收。

入口复用脚本组件装配 open_component_application（真实service资源与host assembler），仅注册memory工具，临时项目/个人库、auto_enabled=false。输入来自冻结space-time calibration正式sources，不把工具/助手材料改为用户证据。模型两次写入阶段调用，随后生产prepare_memory_parts/compose_model_prompt一次回答，总≤3；请求第三次写入模型前由回调预算拒绝，不重试。真实运行可复用既有catalog；单case独立进程/资源作用域中的loader patch仅用于显式catalog注入，不修改生产算法。

正常：可选旧种子经原生产来源准入→当前用户消息→host.runs.start→模型memory调用→真实权限/归档/MemoryAccess→结果回填→最终说明→读取真实库→独立后续回答。旧种子用同项目独立历史sid；followup需增加可选sid参数，默认probe保持旧驱动一致。
失败：来源、权限、kind变更拒绝均作为工具反馈保留；模型/预算异常仍保留实际存储与反馈，记录failed，不重新执行。种子不得进入本轮新确认。前台B调用数应0；直接kind变更不能要求成功。报告身份、安全过滤与协议复用既有实现。先覆盖正常写入、真实拒绝、预算失败和只读回答，再开放真实批次CLI；本片不直接复用后台must_activate标签评判前台改类成功。

修改文件：scripts/memory_foreground_evaluation.py、memory_space_time_probe.py、tests/test_memory_foreground_evaluation.py及交接。生产检索、工具签名、真实配置/用户库保持。先失败测试、最小实现、相关回归、标准构建；不新增前台产品功能。

预算回调故障修正：inline抛错使固定LangChain回调管理器产生未await警告；改用非inline回调调度后，正常/坏源/超预算3项在-W error严格检查中通过，超预算仍只执行两次。真实批次CLI、异常中间反馈完整留存、前台专用标签和改类测试仍为S7后续单元，不称S7整体完成。

## S7 完整驱动补充合同

运行失败时用工具回调保留已经完成的memory反馈，以tool_call_id去重；不能仅依赖正常run返回。旧种子在同项目独立历史会话准入，正文及真实scope/id/version经本次评测专用提示part作为不可信旧记录输入，避免模型在两轮预算内先list再update；它不构成新证据。每case独立catalog由应用关闭，批次不复用已关闭client。回调保留实际请求、可见输出及token，模型异常不保存provider异常正文。

独立CLI仅开放calibration（完整8例、每例≤3、总≤24），真实配置只读；protocol/rubric/source/model身份保存、独占报告、不中途补跑。前台06持续化改类的标签单列为“直接改类拒绝、旧项完整保持”；原后台标签放background_expected_facts，不拿前台禁止动作当必须成功。其余事实标签保留，语义状态待人工审阅。08混合助手材料不可提升成用户：正式声明仅human/user组成当前输入，其他角色通过归档原身份准入（不得自动把其内容放入用户提示）。本轮先不支持此类混合输入则整例明确失败并继续全批，不静默跳过。工程完成不等于模型质量验收。


## 实现与边界补充

失败反馈从模型下一次请求中的真实ToolMessage及工具结束回调采集，正常返回与中断结果按tool_call_id去重，避免失败run没有返回messages就丢掉已完成工具结果。原始异常正文不入报告。

独立历史会话避免将种子归档强塞当前图造成来源保存失败；评测专用旧索引仅在本case写入阶段追加到原prepare_memory_parts结果，正常检索仍执行，后续回答不追加该part。此为显式fixture前提，不能称产品自动找到旧项。没有改变产品检索或增加模型读取轮数。

直接改类场景以真实旧行与写入后行完全相等作工程检查；正文变化、版本变化或续期均不通过。模型可以选择延期给后台处理，是否准确解释仍须人工评分。前台标签保留background_expected_facts，单列禁止直接改类的生命周期合同，不覆盖冻结标签。

CLI命令：`.venv/bin/python -m scripts.memory_foreground_evaluation --config <existing-config> --output <new-report>`。仅calibration；当前不支持后台旧版本七类的前台重放，不宣称新旧前台非退化。退出0仅代表工程执行完整，语义保持needs_human_review。真实模型调用按完整8例执行，不补跑失败子集。

新增中断留存/标签/真实改类拒绝/完整8例CLI四项测试；包括此前三项共7项。日志屏蔽限定在独立CLI入口，run_batch不得改全局日志开关；回归覆盖后续日志消费者。

工程验证：最终记忆600项/20.73秒，严格34项/2.19秒通过，标准Mac构建/Twine、compileall/pip/diff通过。完整真实前台calibration8/8执行、24逻辑调用、B=0、结构失败0；6例工具参数拒绝（顶层kind/machine/reason等写入动作不接收字段），02保存active，07新增candidate且旧项保留。06真实模型在顶层reason先被schema拒绝；直接改类专门错误由确定性测试证明，不能把本次真实结果记为到达该门禁。全部后续注入0。原报告保留，未补跑；SDK attempts未知，语义未验收。证据build/spec143bm-s7-complete/{foreground-calibration-01.json,triage.json,verification.json}。下一步分析公开工具动作合同与冗余字段表达，再决定提示或schema优化。
