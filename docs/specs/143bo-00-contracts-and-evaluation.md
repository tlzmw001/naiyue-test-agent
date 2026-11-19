# Spec143BO-S0：行为合同与评测基线

日期：2026-11-17；实施更新：2026-11-18。状态：S0 的样本与离线合同入口已实现，验证记录见第 8 节；真实 Wiki 驱动尚未接线。依赖：[总规范](143bo-memory-wiki.md)。本片不调用真实模型、不迁移用户数据，先固定之后不能随实现漂移的输入、预期与评分。

## 1. 小目标

同一句“以后先举例”在项目语境中应留在项目；“我平时学习技术习惯先举例”在无局部限定时可成为个人记忆。用这些具体例子定义产品，而不是先写代码再根据结果改答案。

## 2. 当前可复用入口

- tests/data/memory_optimization、tests/data/memory_space_time：原证据与场景出处。
- scripts/memory_worker_evaluation.py、memory_foreground_evaluation.py：后台/显式写入与后续回答的分离。
- scripts/memory_evaluation_costs.py、memory_review_metrics.py：真实 usage、失败和人工评分归属。
- tests/memory_comparison_helpers.py：E/O 绑定及旧 source-only 的确定性测试素材。

新建 tests/data/memory_wiki，是因为标签改为主题导航与长期价值，不能覆盖旧时空分类数据。新增 scripts/memory_wiki_evaluation.py 只承担新协议适配，成本记录和现有模型驱动优先复用；不复制整套供应商调用。

## 3. 固定数据合同

每例包含 case_id、split、来源原文及发生时间、当前项目、初始新代 Wiki、可提取事实、允许范围、目标大主题、需要读取的主题/历史、允许状态变更、必须保持的旧值、禁止结论。

模型输出与期望分离保存；人为写的预期不得混入模型可见的用户消息或来源。允许同义概括，不以与参考答案逐字一致评分；否定、例外、项目范围和执行授权必须保留。

至少 24 例：calibration 8、validation 8、holdout 8；固定语义家族分割，不将同一句的改写分到不同 split。业务协议另标生产代码摘要、fixture 摘要、驱动版本和已配置模型 ID；密钥/真实私人聊天不进入资产。

## 4. 必须覆盖的场景

| 编号 | 输入/触发 | 正确结果 |
| --- | --- | --- |
| W01 | 明确所有项目都先讲例子 | personal，记录用户范围依据 |
| W02 | 明确个人一般学习习惯 | personal；不强制出现“所有项目”字样 |
| W03 | “本项目以后先写 Spec” | workspace；不能以抽象性扩大范围 |
| W04 | 项目中仅说“以后先测试” | workspace，不缺失长期价值明确的本地约定 |
| W05 | 普通聊天中的项目限定材料 | 不伪造项目，不自动转个人 |
| W06 | AI/工具/用户引用第三方说法 | 不能独自作为个人范围依据 |
| W07 | 部分确认旧约定 | 程序保留旧正文/范围/时钟，只附新来源 |
| W08 | 较新完整确认或更正 | 正文不变用 confirm，不抄旧文；更正用 replace；同 ID/CAS，仅完整新证据续期 |
| W09 | 仅阶段进展、一次实验 | 留历史/概览，不提取长期记忆 |
| W10 | 同一规则出现在两个项目 | 不自动晋升 personal |
| W11 | 小主题由 30 条增至 31 条 | 合法同范围整合/拆页，或整组不提交 |
| W12 | 主题少于 30 条但正文超 2,000 token | 多页读完，内容不因读预算被丢弃 |
| W13 | 小主题目录超过单次预算 | 有真实 continuation，可遍历全部可访问页 |
| W14 | 同时存在 Wiki 导航和历史概览 | 各≤2,000，合计可超过旧 2,000 且≤4,000 |
| W15 | 已到期、尚在 7 天缓冲 | 普通目录/read/search 均不返回为可用记忆 |
| W16 | 新来源确认与 GC 并发 | 不误删已续期条目；事务失败不留下半页 |
| W17 | 范围内原聊天被清理 | 已有有效记忆可用；不伪造 available 或续期 |
| W18 | 页面关联指向其他项目 | 标题/简介/正文均不可泄漏 |
| W19 | A/B/读取/提交失败 | 无重试，无失败提议入库；概览独立成功可保留 |
| W20 | 数据升级前存在旧记忆 | 新工具、比较、导航均不可见；原历史/概览保留 |
| W21 | 引文存在但语义不支持跨项目 | 评分记范围扩大；不能把程序校验通过算语义通过 |
| W22 | 同事项个人默认与项目例外 | 当前项目采用例外，个人默认不被覆盖 |
| W23 | 显式直接写入与后台写入 | 使用同一权限/来源/范围/容量/时钟门禁 |
| W24 | 用户已忘记的来源重新出现 | 抑制保持，不能因新代切换重新导入 |

W01—W24 是覆盖要求；split 文件可组织独立素材，使每个 split 覆盖多种维度，不能把一整个关键能力只放 calibration。

## 5. 确定性与语义评分分开

确定性：token/byte 上限、分页完整性、scope 路由、旧代隔离、来源绑定、CAS、续期、≤30 条、失败次数、实际落库副作用、必要日志和权限拒绝。

语义：长期价值、范围含义、主题归属、条件保留、整合是否损失事实、页面/历史选择是否合理、最终回答是否正确使用记忆。模型 reason 不是自动通过依据；助手评分标记 reviewer=assistant，人工评分才是 human。

导航效果记录：是否发现正确大主题、选中正确页、是否读完整必要页、读取次数、传入 token、最终回答。不得把“存进数据库”或“工具成功”单独当成召回有效。

禁止只补跑失败子例后覆盖原批报告；每批固定集合完整执行、记录所有失败和实际调用。生产失败不重试不影响以后用户主动发起的独立完整评测。

## 6. 文件与接口产物

| 文件 | 内容 |
| --- | --- |
| tests/data/memory_wiki/protocol.json | 协议 wiki_v1、分割、标签、来源和计时口径 |
| tests/data/memory_wiki/fixtures.json | 24 个独立输入/预期与固定时钟 |
| scripts/memory_wiki_evaluation.py | 旧驱动能力适配、plan/offline/background/foreground 模式 |
| tests/test_memory_wiki_evaluation.py | split 泄漏、遗漏失败、源摘要和副作用检查 |
| build/spec143bo-s0/manifest.json | 本次基线的源码/资产摘要及只读验证结果，实施时生成 |

评测入口参数：--mode plan|offline|background|foreground、--split calibration|validation|holdout、--output-dir；真实模式使用现有模型配置入口，不新增供应商凭证字段。plan/offline 零网络；后台/前台分别记录独立后续回答。

S0 的具体接口边界：plan 校验资产后生成固定 8 例清单，offline 校验同一资产并可用 --report 检查既有报告的完整性与来源摘要。background/foreground 在 S6 接线前明确报 wiki_runtime_not_connected 且不写成功报告；不实现占位的真实调用。此时 model_id=null、model_requests=0、product_quality_passed=null，禁止把合同通过标为业务通过。

资产是独立合成场景，不是提前承诺 S1 的 SQL 插入格式；seed 到真实仓储的转换随 S1/S6 接线。input 仅含模型任务材料，expected/coverage 留在评分侧；scope/topic 的语义是否正确由复核负责，schema 只检查枚举/绑定合法。24 个独立 family 按固定清单分为 8/8/8，W 编号是覆盖维度，不是产品硬编码规则。

## 7. 验证与退出

先写可复现的失败测试：错范围标签、遗漏一个失败例、旧记忆混入种子或把 preview 当完整读取均使验收失败；再完成最小资产/驱动。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_evaluation.py -q
.venv/bin/python scripts/memory_wiki_evaluation.py --mode plan --split calibration --output-dir build/spec143bo-s0
~~~

S0 完成表示评测协议可执行且没有真实模型请求，不表示产品实现或业务效果通过。后续改变样本、阈值或评分必须生成新协议版本并解释差异，不能回写旧结果。

## 8. S0 实施与证据

2026-11-18 新增 fixtures.json/protocol.json、scripts/memory_wiki_evaluation.py 和 tests/test_memory_wiki_evaluation.py。复用既有 write_report 的排他创建，输出文件已存在时拒绝覆盖。来源摘要覆盖工作区实际生产/脚本/测试（含未提交文件），不只使用 HEAD；不读取用户模型配置或凭证。

先运行新测试，入口缺失导致 ModuleNotFoundError，确认红灯；最小实现后首批 14 项回归转绿；再增加可达材料/GC 时钟回归，确认长目录空成员问题后补齐数据，最终新增 15 项通过。专项连同既有评测闭环/成本共 24 项通过。plan/offline 产物是合同验证记录；报告检查保留 failed/skipped，assistant/human 归属分明，preview 不得标完整。具体最终验证命令、构建及全量结果集中保存在 build/spec143bo-s0/verification.json；未接真实模型，未操作用户库。

旧 Spec 的裸 pytest 会收集调研目录中的第三方用例；本次全量范围明确为 tests。标准构建脚本在仓库根以 PYTHONPATH=. 执行；不修改构建器或 pytest 基础设施。S1—S6 继续补领域断言、真实接线和模型评分，S0 的报告合同验证不替代这些验收。

最终结果：专项 24（含新增 15）通过；全量 tests 首轮 7,100 通过/33 失败，日志指向开发 PATH 缺 fd。使用本次已校验 wheel 的 fd/rg 临时加入测试 PATH 后，原 33 节点全部通过。首轮收集发生在最后一项新增边界回归之前，该项已在最终专项覆盖；不称一次全量全绿。Mac 标准构建/Twine、compileall/pip check 通过。最终资产/源码摘要在 final-assets/calibration/manifest.json，初稿 manifest 保留原摘要不覆盖；证据见 build/spec143bo-s0/verification.json。
