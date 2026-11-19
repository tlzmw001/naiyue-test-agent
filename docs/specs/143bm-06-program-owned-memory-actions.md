# Spec143BM-S6：模型选择动作，程序装配确定数据

日期：2026-11-17。用户已确认实现。范围限记忆模块，不调整召回算法、类型、TTL、触发阈值或重试策略。

当前状态：程序装配动作已实现，工程回归及 07 旧输出离线重放已有证据；离线重放不能证明当前模型会生成正确的新动作。修复后的新后台真实校准及共同效果验收统一转入 [S8 第 14 节 U04—U06](143bm-08-memory-tool-action-contract.md#14-s0s8-未完成事项统一对账与关闭标准)，不再单独排期。

## 1. 问题与当前主链

`MemoryJobs → memory_job_runner._compare → compile_comparison_decisions → MemoryAccess.commit_comparison → memory_mutations.commit`。
A 根据 E 编号提取内容、范围和时间类别；程序生成 P/O 引用，B 判断关系、证据支持和确认范围；编译器装配公共写入请求，提交层再次核验来源、权限、CAS 和生命周期。

07 中 B 已声明 supplement/partial，却追加了正文说明。旧编译合同要求完整 claim，再逐字比较旧正文，导致合法的“只追加来源”意图不能完成。这个重复抄写任务应交给程序。

## 2. 本次合同

| B 决定 | 模型负责 | 程序负责 |
| --- | --- | --- |
| duplicate / unsupported | 选择关系、P/O、解释 | 零写入、不续期；可解析但多余的 claim 忽略并诊断，不从中救回内容 |
| supplement + partial/unknown | 选择 O、正式新 E，直接支持关系和确认范围；claim=null | 从 O 构造完整旧内容，仅附来源与说明；正文、范围、类型、条件、状态、证据/确认时点、期限保持 |
| 其他写入关系，接受单个 P 的内容 | claim=null，选择全部该 P 的正式证据 | 使用已核验 A 内容，不要求 B 重抄；不自动添加 B 未选择的证据 |
| 其他写入关系，需要合并/改写 | 生成 claim 的正文、事项、依据等语义内容 | kind 始终继承消费 P 的一致类别；scope、ID、版本、来源身份和时间仍由程序装配 |

- 单个 P 的复用允许 new/different_conditions/unresolved_conflict/whole supplement/correction；所有原关系门禁仍执行。多 P 且 claim=null 拒绝，不能任选一条。
- 部分/未知确认的 source-only 仅用于明确的 supplement 且 support=direct；unsupported/inference、correction/partial 不能退化成来源追加。unknown 表示整体确认范围不足，不表示新证据没有支持关系。
- source-only 忽略可解析 claim 的全部字段和 decision.gaps 对内容的改动，包括 kind/title/conditions；写入值全部取 O。不同于更正，输入 A 的类型不改变 O 的类型。
- claim 中的 kind 变为可省略的冗余字段；如存在，不作为写入依据并诊断。A Extraction 的 kind 仍必填，两类校验不变。
- source-only 和零写入动作不消费 A 的 kind，因此合并多 P 时也不要求它们 kind 相等；生成新内容的多 P 合并仍要求一致 kind，不能猜类别。所有动作的范围与对象引用校验仍保留。
- 日志只记固定动作/被忽略字段名称，不记录正文、引用、模型原始结果；safe_log 故障不改变结果。
- 不修复非法 JSON、未知字段、无效引用或 schema 错误；不增加模型调用、失败重试或候选救回。

## 3. 正常与失败数据流

正常：旧 O1 为“本地和 CI 使用隔离环境”，新 E1 只确认本地。B 返回 supplement/partial/direct、O1、E1、claim=null。编译器读取 O1 内容，生成带当前 expected_version 的 update；实际 commit 重验 E1、CAS 和抑制规则，保留原正文/期限，追加来源、新版本和说明。原始聊天缺失不由保存的旧引用伪造恢复。

多余内容：上述决定携带新正文时，程序仍只执行 source-only，记录忽略事件。忽略正文不构成语义支持证明；B 若把否定消息误标为支持仍属模型风险，需要效果评测。

失败：E99 不存在、来源跨项目/被抑制、O1 版本已变化、来源容量超过 8、correction 仅 partial、改类缺新真实用户 whole 证据，均拒绝且不重试。一个组编译失败不产生该组写请求；提交仍沿既有逐请求事务合同。

## 4. 同类门禁排查清单

| 位置 | 处理与原因 |
| --- | --- |
| B partial/unknown 的正文/条件恒等 | 编译阶段改为程序继承；commit 恒等检查保留为内部请求完整性防线，不再要求模型复制 |
| B kind 必须与 A 相同 | 改为程序继承 A；source-only 继承 O；真实旧 kind 更正门禁保留 |
| B 接受 A 却需完整 claim | 增加显式 null 复用单 P，完整证据集合校验；多 P 合并仍需模型表达 |
| duplicate/unsupported 的额外 claim | 零副作用动作忽略可解析冗余内容并诊断 |
| 更新可选 title/reason/aliases/gaps/conditions | 已有省略继承，不新增抄写要求；source-only 全部从旧项保留 |
| A 引用 | 已经只选 E 编号，quote/offset/session/revision 来自程序，不改 |
| A identity_terms | 可省略的技术身份选择；必须真实存在，不做模糊身份替换 |
| P/E/O/group ID、覆盖/重复消费 | 保留：对象选择和防止重复执行，不是正文重抄 |
| 前台 memory record/update | 完整内容表示真正的新建/替换，未提供 partial 动作；保持公开签名。当前输入身份可用 current_user，由程序补齐 |
| 前台引用 quote/offset、CAS、工具权限 | 保留：引用选择/来源核验/授权与并发边界；不做近似引用匹配或自动改写审批参数 |
| 精确 duplicate/known_conflict、FTS digest、迁移映射 | 保留：程序数据比较，不由模型重新生成既有值 |
| 概览/批次边界/预加载版本与完整性 | 保留：输入输出容量和快照一致性，不是复制任务 |

## 5. 文件和验证

生产修改：memory_proposals.py（B schema、动作编译、固定诊断）、memory_comparison.py（正向任务说明）。公共 MemoryInput、存储 schema、权限入口不变；既有 commit 的防御检查保留。

测试：新增动作合同与真实库测试；现有 S3 固定矩阵的历史 partial_rewrite_rejected 按批准的新行为迁移预期，并保留原冻结资产及不改正文/不续期断言，明确它不再是当前合同的失败案例。其他历史失败不能转成功来掩盖回归。当前测试和 scripts/memory_worker_evaluation.py 统一消费新增 comparison_s6.json / comparison_s6_protocol.json，旧 S3 资产不变。无需修改共享 conftest/helper。

先红后绿覆盖：null source-only、冗余完整 claim 被忽略、A kind 继承、单 P 复用、完整证据/多 P 限制、不写库动作冗余内容、实际库来源追加/期限/状态、引用和 CAS/权限失败无重试、日志失败隔离、text/json_schema/function_calling 的同源 schema。

最后运行记忆专项、完整后端分片、标准 Mac 构建、Twine 和 wheel 源码一致检查。旧真实报告保持不变；07 使用保存的 A/B 输出离线重放，不能称新模型语义验收。真实模型复测在新完整批次进行，不补跑失败子集。本次不触碰真实配置/用户库、不提交发布。


## 6. 实际实现与验证（2026-11-17）

生产仅改 memory_proposals.py 和 memory_comparison.py；未新增通用处理器/公开动作/数据库字段，commit 恒等检查作为程序编译请求的防御检查保留。Schema、文本提示和原结构化适配器消费同一个 ComparisonResult；A kind 仍必填。

新增39测试：核心17项先红后绿，后续同类多P kind问题3项先红后绿；其余为安全边界、接线和协议回归。未修改共享测试基础设施。S3冻结资产不覆盖；S6独立机械矩阵更新批准后的 partial 预期，当前worker/test统一消费，并强化正文/证据时点/最后确认时间/期限保持断言。

最终源码：记忆588项/22.22秒，完整后端四片1850+1772+1820+1580=7022项（0失败/错误/跳过），标准Mac构建、Twine、两生产模块wheel逐字节一致、compileall/pip check/diff check通过。真实用户库/配置、Git提交与发布均未操作。

旧真实A/B输出离线重放8例completed，provider_calls=0。07的原始partial扩写输出被编译为只附来源：原正文不变、版本2、原证据时点/最后确认时间/90天期限不变，来源同时保留old-u1和u1；原聊天未恢复。新提示效果未经真实模型重新采样，不称语义通过。普通召回仍不足，先前评测报告过滤与身份记录问题不在本片门禁修改范围。

证据：build/spec143bm-s6-actions/verification.json、source-manifest-final.json、offline-replay-final.json、case07-replay-state.json。最终四片、构建与源码核对使用同一候选；较早中间验证保留但不作为最终证据。
