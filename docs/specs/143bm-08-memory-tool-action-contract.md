# Spec143BM-S8：前台动作契约与记忆模块统一收尾

日期：2026-11-17。状态：**S8-0/S8-1 已接线，验证记录见第 15、16 节；S8-2 已开始（第17、18节），S8-3/S8-4 未实施。用户已确认采用 `{request: {action, ...}}`，保留工具名 `memory`；接受旧格式中尚未执行的调用安全拒绝，并要求把之前分片的未完成事项合并推进。** 本稿现为 S0—S8 未完成事项的统一入口，第 14 节给出对账和关闭标准。本轮只修改离线/真实评测驱动及回归，未改变产品工具参数或真实配置，没有追加真实模型请求。

前置合同：[S5 空间与时间](143bm-05-space-time-memory.md)、[S6 程序装配确定数据](143bm-06-program-owned-memory-actions.md)、[S7 前台评测](143bm-07-foreground-evaluation.md)。本稿先修复 S7 的评测缺口，再改变前台工具参数；两部分分别验收，不能用参数改动掩盖评测问题。

## 1. 用一个例子说明本次要做什么

用户说：“以后在所有项目里，分析问题都先列事实，再列假设。”

现在的 `memory` 工具把“搜索时的 kind 筛选”和“写入内容的 proposal.kind”放在同一份大表单里。模型可能同时填两处。即使都填 `convention`，程序仍会因写入动作不允许顶层 kind 而拒绝，用户要求便没有保存。

本次让模型先选择动作，再填写这个动作的表单：

- 搜索表单有 query、kind 等筛选条件。
- 新建表单只有 scope、proposal、sources，类型只在 proposal.kind 出现一次。
- 更新表单再增加 memory_id、expected_version，仍需要完整 proposal。
- 停用和遗忘表单需要 ID、版本、来源和 reason，不要求模型生成新正文。
- 概览整理表单只登记任务，不伪装成直接写入完成。

模型负责判断“要做什么、内容和证据是什么”；程序负责选择对应表单、检查来源与权限、填入内部默认字段、提交和返回真实结果。此处减少无关字段，不把格式错误自动猜成另一种操作。

## 2. 当前证据与审查发现

### 2.1 已有真实校准能说明什么

原报告为 `build/spec143bm-s7-complete/foreground-calibration-01.json`：8 例、24 次逻辑模型调用、B=0，6 例工具参数拒绝；02 写入 active，07 新增 candidate 并保留旧项，全部后续注入为 0。退出 0 只代表当时驱动执行完成，未做人工语义验收。

本轮逐项读取真实参数：01 多了顶层 kind/reason；03/05/08 多了 kind；04 多了 kind/machine；06 多了 reason。kind/machine 与 proposal 中相应值一致，两个顶层 reason 与 proposal.reason 文字不同。不能把这六例都说成逐字复制失败，也不能仅移除字段就宣布六例业务成功；06 格式修好后仍应被直接改类门禁拒绝。

### 2.2 S7 有五项需先修复的问题

| 编号 | 触发与实际结果 | 影响及要求 |
| --- | --- | --- |
| R1 | 第一轮 list，第二轮 record 成功，第三次模型调用超预算。库内已有 1 条记录，feedback 只剩 list | 预算回调先抛错再采集 ToolMessage，最后一次已执行反馈丢失。先保存已发生的反馈，再拒绝下一次请求 |
| R2 | followup 的上下文准备抛 OSError。8 例实际各调用过一次主模型，报告却 cases=[]、logical_calls=0、structural_failures=0 | case 返回失败使已有证据与费用计数丢失。逐阶段保留 case，失败也参与计数 |
| R3 | catalog.aclose 抛 OSError | 异常越过批次 finally，报告不生成；应用退出异常也可能覆盖已构造的返回值。收尾失败单列，保留已有证据 |
| R4 | 前台模型请求 refresh_overview | auto_enabled=false 不禁显式整理。离线已观察后台任务失败，而 case 仍 completed。前台评测明确拒绝该动作，产品显式整理能力保留 |
| R5 | 06 前台已改成“禁止直接改类、旧项不变”，却保留后台回答要求“采用 ruff，并说明是正式约定” | 独立后续回答没有新声明，却被要求说新约定。前台事实指标和回答要求分别冻结，保留原后台标签作来源 |

R1—R4 的独立复现使用离线模型和临时库：`build/spec143bm-s7-review-architecture/probe.py`；本轮主审重跑结果在 `build/spec143bm-s7-review-architecture/run-77345cf2364749df832466edf35fa731/summary.json`。R5 通过当前 fixture 与 foreground_labels 输出核对。原始模型报告保持原样；本轮没有发现这五项已导致原 8 例发生隐蔽额外写入，不能把故障注入结果倒写成原批次事实。

## 3. 范围和不变量

本片包含：S7 失败证据修复、前台工具动作 schema、真实许可绑定接线、既有权限规则语义适配、模型可见详情入口及当前调用方迁移，以及 S0/S3/S4/S5/S6/S7 共用的评测工具、后台/前台真实校准、人工评分和独立验收收尾。已完成实现只做联合回归；当前检索升级继续延期。

以下行为保持当前合同：

- 检索算法、0.5 阈值、多查询、个人长期约定预算、触发轮数和字符数、工具结果裁剪。
- personal/workspace 范围和 convention/context 类型，90/30 天 active、30 天 candidate，原时钟及失效规则。
- 后台 A/B 动作、证据编号、只附来源、受控改类、不重试、不以 candidate 挽救失败任务。
- 前台 current_user / 保存来源身份、精确引文、真实 root 用户、版本与原文区间检查。
- 前台 update 仍为完整建议；直接换 kind 仍拒绝。本片不增加前台 append_sources 或后台式比较能力。
- MemoryAPI、MemoryAccess、存储结构、数据库版本及返回结果格式。
- 不新增依赖，不升级 SDK，不修改模型 profile、真实 `.env`、用户记忆库或其他环境配置。

## 4. 当前主链与新主链

### 4.1 当前真实调用链

`create_memory_tool()` 用 `MemoryInput` 同时产生 public/execution schema → `ToolRegistry` 校验注册 → 模型调用 → `prepare_permission_call()` 用 `ToolSpec.validate_input()` 校验及补默认 → 原始参数保存在 call.args，规范化参数保存在 subject.args → `ExecutionAuthorizationMiddleware` 核对原始调用并发放许可 → `memory.execute()` 再验证并精确比对许可 → `MemoryAPI.query()` 核验 RunScope、项目身份和目录 → 必要来源归档保存 → `MemoryAccess.query()` → 来源、CAS、生命周期门禁 → SQLite 事务 → 真实 ToolMessage。

失败时，schema 错误发生在业务写入前；坏源、旧版本、改类等错误来自后续业务门禁。二者必须分别观测，不能都归为“模型没理解记忆”。

### 4.2 新链路的数据形状

```text
模型生成的原始信封 {request: {action, ...该动作字段}}
  → MemoryToolInput 校验：顶层只有 request，内部恰好命中一个动作
  → public.model_dump()：规范化信封，进入 subject.args 和审批展示
  → 规则匹配专用视图：编译为当前 MemoryInput，沿用原 action/scope 规则
  → 执行许可：原始 call.args 与规范化 subject.args 都按原机制绑定
  → memory.execute：先验证完整信封及许可，再编译内部 MemoryInput
  → 现有 MemoryAPI → 来源保存 → MemoryAccess → 存储 → 原结果
```

四份数据不能混用：原始信封用于调用身份；规范化信封用于审批和执行一致性；内部 MemoryInput 用于业务操作；工具结果只描述实际结果。转换过程没有读库、推理、补证、选旧版本或自动续期。

## 5. 工具参数合同

### 5.1 顶层和动作选择

工具名仍为 `memory`，公开顶层为一个必填 `request` 对象，禁止额外字段。request 是八个严格对象的联合，每个对象有必填的单值 action。使用 Pydantic 普通联合导出嵌套 `anyOf`，不在根放联合，不使用依赖供应商支持的 if/then/else；每个分支 extra=forbid、strict=True。

模型仍会看到八个分支的说明，并非 UI 动态隐藏其他动作；区别是写入分支本身不再包含查询字段。不能声称 schema 已消除全部模型参数错误。

### 5.2 八个动作的完整字段清单

“读筛选”指 status、kind、machine、module、target、subject。读筛选对 read 也实际生效，不能在迁移中删除现有筛选保护。除表中字段外一律禁止，即使值为 null。

| action | 必填字段（除 action） | 可省略字段及默认值 |
| --- | --- | --- |
| list | 无 | scope=both；读筛选 status=active、其余 null；limit=null；cursor=null |
| search | query | scope=both；读筛选同上；expansions=[]；limit=null |
| read | scope、memory_id | 读筛选同上；version=null；offset=0；include_history=false |
| record | scope、proposal、sources | 无 |
| update | scope、memory_id、expected_version、proposal、sources | 无 |
| disable | scope、memory_id、expected_version、sources、reason | 无 |
| forget | scope、memory_id、expected_version、sources、reason | 无 |
| refresh_overview | 无 | target_session_id=null；start_sequence=null；end_sequence=null |

scope：list/search 可 both/workspace/personal；read 和四个写动作只能 workspace/personal 且显式填写；refresh_overview 不提供 scope。write sources 为 1—8 条；默认值仅来自代码，不由模型重抄全部内部字段。

所有旧边界保持：query 非空白且至多 2000 字符、expansions 至多 5 条且各非空白/不超限；ID 为 32 位小写十六进制；version 正整数至 SQLite 上界；expected_version 至上界减一；limit 1—50；cursor 至多 4096 字符；offset/sequence 非负；会话 ID 至多 128 字符；start_sequence 不大于 end_sequence。运行时 MemorySettings 更小的限额仍由业务层执行。

### 5.3 proposal 和 sources

直接复用当前 `MemoryProposal` / `MemorySource` 的语义、字段与验证，不创建第二套宽松版本。

- proposal 必填 kind、title、body、subject、basis、reason；intent 默认 candidate；machine/module/target/deadline 默认 null，aliases/tags/gaps 默认空列表。
- 字符、UTF-8 字节、标签数量、空白与有效 deadline 等检查沿用原模型；schema 和本地执行都保留。
- record/update 的 reason 只在 proposal.reason，不另提供 request.reason。disable/forget 的 reason 在 request.reason，因为这两个动作没有 proposal。
- current_user=true 仍由代码选择本轮真实用户消息；保存来源仍要求完整 session/thread/message/revision/conversation_revision 身份，quote 与 offset 沿用原检查。
- 仍不让模型提供状态、证据时间、expire_at、workspace_id、数据库路径或权限上下文。

### 5.4 可直接理解的调用示例

新建个人约定：

```json
{
  "request": {
    "action": "record",
    "scope": "personal",
    "proposal": {
      "kind": "convention",
      "title": "分析问题的顺序",
      "subject": "分析问题",
      "body": "在所有项目里分析问题时，先列事实，再列假设。",
      "basis": "user_declaration",
      "intent": "activate",
      "reason": "用户明确声明适用于所有项目。"
    },
    "sources": [{"current_user": true, "quote": "以后在所有项目里，分析问题都先列事实，再列假设。"}]
  }
}
```

这个例子只有一处 kind。程序验证真实引文和权限后按当前规则写入；例子不是实际用户库数据。

查询当前项目中的长期约定：

```json
{"request":{"action":"search","scope":"workspace","query":"本地回归","kind":"convention"}}
```

这里 kind 只表示筛选，不会改变任何记忆的类型或有效期。若把 query 或第二份 kind 放入 record 分支，会在调用执行前拒绝；不自动把它挪进 proposal。刷新概览为 `{"request":{"action":"refresh_overview"}}`，返回 accepted/task_id 后仍须等待后台真实结果。

## 6. 实现边界、复用与权限

### 6.1 两份合同分别服务谁

保留 `tools.memory.MemoryInput`：它仍有 MemoryAPI、后台提议编译、比较读取、自动召回和直接存储测试等真实消费者，不是应删除的废弃入口。

新增 `tools/memory_actions.py`：仅负责模型可见的八个动作模型、MemoryToolInput 和工具信封到 MemoryInput 的转换。复用现有字段约束、MemoryProposal/MemorySource；相同的三个读筛选可共用一个当前专用基类，不建立通用 schema 注册平台。避免循环导入：`memory.py` 在 create_memory_tool 内加载动作合同；动作模块需要内部 MemoryInput 时使用明确的依赖或函数内导入。单文件不超过 500 行。

拟定内部函数 `compile_memory_tool_request(envelope) -> dict`：严格验证新信封和选定分支，再以其字段构造 MemoryInput 并返回完整 model_dump(mode='json')。权限匹配与实际执行复用它；不得有各自不同的删字段规则。转换不接受旧顶层格式、不忽略未知字段、不将 sources 为空改成当前用户、不猜 scope/版本。

`create_memory_tool` 继续复用 `build_tool_schemas`、StructuredTool、ToolSpec、ToolRuntime 和现有执行调度。工具错误保持安全固定说明，明确 request.action 入口，不能把包含原文/参数的完整 ValidationError 返回或记录。

### 6.2 保留现有权限规则含义

当前 PermissionRule.args 只支持顶层标量，配置如 `tool="memory", args={action="forget"}`。仅把工具参数嵌套起来会造成启动校验失败或规则不命中，必须同步接线。

采用**仅针对内置 memory 的固定规则视图**，不新增任意嵌套路径语法，也不要求用户改真实配置：

1. `validate_permission_rules` 对新 memory 工具仍按 MemoryInput 的字段与类型验证规则；其他工具沿原 public schema。识别必须同时满足工具名和内置 MemoryToolInput 的受控 public 类型/继承关系；build_tool_schemas 会生成子类，不能使用 `spec.input_schema is MemoryToolInput`，也不能只凭同名工具认定合同。
2. `prepare_permission_call` 在公开参数验证通过后、匹配规则前，根据上述真实 ToolSpec 写入可信 `subject.argument_contract="memory_actions_v1"`。该字段由程序生成，不接受模型填写，并随既有 subject 进入许可绑定。其他工具和后台 flat 检查不加此标识。
3. `match_rules` 仅对带此标识的 memory 信封调用同一编译函数，使用内部 MemoryInput 做标量比较。原始 subject.args 保留完整信封，不在此覆盖或写回。标识与工具名/参数形状不一致时明确拒绝，不能退化成无匹配规则；没有该标识时保持原规则视图，以免误伤同名自定义工具。内置 memory 执行边界另强制核验该标识，旧许可缺失标识时在业务 IO 前拒绝。
4. 后台 `MemoryJobs.allowed()` 和普通聊天 `session_persistence.prepare_memory` 的内部 flat 请求仍按原样匹配。它们有只带 action 的调用，不能顺手补默认值改变旧规则命中条件。
5. 只识别完整的 `{request: ...}`，混合新旧格式、坏 request 或编译失败必须拒绝并可观测，绝不退回 `{}`/原始无匹配视图导致默认 allow。
6. 不把 proposal.kind、proposal.machine 等提升为旧顶层规则键；旧 kind/machine 规则是读筛选，record/update 的内部顶层值仍为 null。
7. wildcard `tool="*"`、deny 优先、allow、NoAsk/BypassApprovals 和 session_allow 使用现有语义。session_allow/一次性批准仍绑定完整规范化信封，不能改成只批准 action。

选择固定投影的理由：这是一次已确认的内置工具参数迁移，已有后台 flat 消费者和真实权限配置；只需保持它们的含义。新通用 resolver、所有工具的嵌套路径规则或权限配置迁移都超出本片。

### 6.3 执行许可不能因转换而变弱

prepare 阶段保存完整 raw call.args 与 normalized subject.args；执行阶段重新得到同一规范化信封，先执行当前 permission_binding、工具名、可信 argument_contract 和 subject.args 一致性检查，再编译内部请求。缺少或伪造合同标识不得执行。改变 action、scope、ID、版本、proposal 或 sources 的任一处都不得沿旧许可执行。

转换与业务门禁职责分开：编译成功不代表来源真实；MemoryAPI 继续检查 run/role/project/目录，先保存必要来源再写库。前台不能调用 commit_comparison 冒充后台 whole correction。

## 7. 同步迁移的消费者与兼容边界

| 消费者 | 本次处理 |
| --- | --- |
| 模型 tool_calls、根/子 Agent、审批恢复中的新调用 | 统一新信封，工具名和选择项仍为 memory |
| memory_context._memory_index 的 detail 提示 | 改为 `{tool:"memory", request:{action:"read",scope,memory_id,version}}`；检索选择和记忆内容不改 |
| app.memory.query / MemoryAPI / MemoryAccess | 保持原 flat request，非工具调用不用加一层 request |
| 后台提取/比较、MemoryJobs.allowed、自动 recall | 内部 flat 数据和行为保持；权限规则等价性回归 |
| 当前文档、合成前台工具测试/脚本 | 逐个迁移真实工具输入；不能改公共测试 helper 让所有旧格式静默被包装 |
| 已完成历史工具消息 | 按历史展示，不改写、不重放 |
| 升级前未执行的旧格式工具调用/旧审批 | 安全拒绝或终止该旧调用，零写库，不改写参数沿旧批准继续执行；用户下一轮使用当前工具合同 |
| 历史 Spec、已冻结真实评测报告 | 保留历史版本；加当前入口指引，禁止改原报告参数和标签来制造通过 |

同一片必须搜索所有 `call('memory',...)`、ToolCall JSON、当前文档示例及 detail 生成点。直接 MemoryInput/API/库测试不能机械迁移。旧测试应保留原业务、来源、顺序、版本和异常断言，只有模型工具输入形状迁移；专门保留旧格式拒绝测试。

## 8. 先修 S7：失败、资源和评分合同

### 8.1 预算与已发生事件

每例保留两次 foreground 模型请求加一次独立 followup、总计至多三次的上限。回调先采集本次输入中已发生的 ToolMessage，再判断是否允许启动下一次模型调用。预算拒绝事件单列，不计为已发起模型请求；已准入后供应商失败的请求必须计数。逻辑调用数与 HTTP attempts 分列，后者无观测就 unknown。

模型仅允许 foreground/followup 阶段；出现 extract/compare 等阶段应明确拒绝并记为评测协议失败，不能因 B=0 就当成没有后台活动。去重以真实 tool_call_id 为准，报告最终行和成功/失败反馈都保留，不伪造未到达的反馈。

### 8.2 每例结果不依赖正常返回

case 结果在资源准备前建立，并逐步更新 setup、模型请求/输出、feedback、写后快照、followup、调用账本、失败阶段。新增失败列表采用固定阶段/错误码/异常类型，禁止保存 provider 异常正文。

至少区分 setup、foreground_run、snapshot、followup_prepare、followup_model、followup_parse、cleanup。可继续的单例异常均产生一条 failed case，后续例子继续，禁止失败例补跑。没有取得的快照/身份使用 null 和明确 unavailable，不能以空 memories 冒充“未写入”。

run_batch 必须包含选中八例各一行；结构失败数包含上述失败。总调用数取逐次调用账本，不能因 case 没有正常返回而归零。模型输入、已写记录和已知用量即使后续准备/解析/收尾失败仍保留。

### 8.3 资源所有权和报告收尾

每例一个 catalog。批次在把 catalog 交给 case 前负责初始化失败的清理；进入 case 后由 case/应用资源栈承担收尾，批次不重复主动关闭同一份 catalog。应用关闭异常在退出资源作用域后合入该例结果，不覆盖此前结果。

清理故障记录为 cleanup 失败，保留原主异常；能继续隔离案例则继续，不能继续时剩余例标 not_run 及原因，整批失败但生成部分报告。不可把清理失败记成功，也不重新执行业务请求。报告独占创建，不覆盖旧文件；报告路径不可写时明确失败。进程被强杀或磁盘不可写不承诺一定产生报告，不为此新增持久队列。

### 8.4 前台评测隔离后台

在临时 AppSettings.permissions.deny 中加入显式 `memory + action=refresh_overview` 规则，通过真实权限链返回拒绝反馈；auto_enabled=false 继续保留。产品工具仍支持此动作，另有原后台和工作流测试验证。

评测检查根轮结束后不存在本例登记的后台任务，A/B 调用为 0。模型若请求 refresh，保留 denied 反馈并标记前台协议偏离，不能把拒绝后的最终说明当成记忆业务成功；不为该例再增加模型调用或临时开放后台。

### 8.5 06 的前台标签

为 foreground 独立冻结生命周期和回答要求，显式记录前台标签版本/哈希；保留 background_expected_facts 和 background_answer_requirements。不能覆盖源 fixture 或沿模型输出调整标签。

06 的正确行为：拒绝直接将旧 context 改为 convention，旧 ID、版本、正文、状态、证据时钟和期限完整保持；主回复如实说明未保存为长期约定，可以说明需要后台受控处理，但不在该前台评测中执行后台整理。

独立 followup 不包含新用户声明：若注入旧记录，只能表达旧试用事实并保留期限；若没有注入，说明未知。禁止声称已经确认为正式约定。06 新约定不设 must_activate/must_inject，也不强迫 must_extract 为真来惩罚正确的无写入拒绝；这个场景由显式生命周期及回答指标验收，防止“全部不做”冒充其他正例成功。

## 9. 分片实施与验收

| 子片 | 交付及先红后绿要求 | 完成条件 |
| --- | --- | --- |
| S8-0 | 先修 R1—R5；前台协议、失败结果和资源所有权测试 | 故障材料完整，调用计数可信，refresh 零任务，06 标签自洽；产品工具仍旧格式 |
| S8-1 | 八动作 schema、编译、权限固定投影、detail 提示与全部真实工具消费者一起迁移 | 所有新格式经真实 Agent/许可/API/存储贯通；旧格式不写库；内部 flat 消费者保持原行为 |
| S8-2 | 参数/权限矩阵与原 S0 的覆盖、评分、成本缺口一起补齐；支持两个入口的各 split；完整回归与构建 | 第 14 节 U01—U03 有可运行入口和独立断言；包内实现与源码一致；没有孤立 schema 或平行旧入口 |
| S8-3 | 冻结源码/工具 schema/协议/模型身份，先执行当前候选后台和前台各 8 例 calibration，并完成逐事实审阅与可比基线 | 每批至多 24 次，候选两批合计至多 48 次；S6 后台新效果和 S8 前台新效果分别报告；阶段内不补跑失败子集 |
| S8-4 | 沿原 S0/S5 顺序做 validation → 冻结 → 独立 holdout、共同指标/成本对照及最终证据对账 | 第 14 节 U04—U06 完成；明确区分工程交付、效果通过和未达标，不能把延期检索升级算进已完成 |

S8-0 可以先独立交付。S8-1 的 schema、权限和 detail 提示不得拆成可运行的半迁移状态，否则会出现“模型得到新表单、权限仍按旧字段”或“召回仍教模型调用旧格式”。上述五个单元属于同一收尾计划，保留实施顺序和阶段验收，不合成一个难以回归的大改动，也不再让旧分片各自保留重复的待办队列。

## 10. 测试矩阵

| ID | 场景 | 必须断言 |
| --- | --- | --- |
| E01 | list → 成功 record → 请求超预算 | 两次逻辑请求；成功 record 反馈及实际行都在；第三次未发出 |
| E02 | followup prepare/parse 抛错 | 保留前台请求/输出/写入，失败阶段正确，计数不归零，八例齐全 |
| E03 | catalog/app 清理失败；主请求同时失败 | 原主异常不被覆盖，cleanup 单列，报告存在且非零退出；无业务重跑 |
| E03a | catalog.get/settings/workspace/应用装配等接管前失败，以及正常应用退出 | 接管前失败仍关闭 catalog 一次并保留 setup 失败；正常路径批次不重复关闭；八例结果完整 |
| E04 | 请求 refresh_overview | 真实权限拒绝、任务登记数 0、A/B=0，无额外请求 |
| E05 | 06 原样保留/错误改类/无注入回答 | 旧行完整不变通过；任一改动失败；前台回答 rubric 不要求正式约定 |
| C01 | 八动作各一条有效最小输入 | 导出和本地 schema 一致，编译结果等于对应旧合法 MemoryInput |
| C02 | record 重复 kind/query/reason；list 带 proposal | 新分支明确拒绝，不进行字段搬移或猜测，无业务 IO |
| C03 | 空白、类型、长度、数值、未知 action、空来源和范围 both | 原门禁强度保持；缺省可选字段才由代码补齐 |
| C04 | provider 工具转换和 ToolRuntime | 根为 object，联合在 request，八个 action 互斥，runtime 不可见；转换无网络 |
| P01 | 同一旧 action/scope/kind 规则匹配新前台和原后台 | allow/deny 含义一致；proposal.kind 不影响旧 kind 规则 |
| P02 | wildcard、NoAsk、BypassApprovals、session_allow | deny 优先；session_allow 和一次性批准仍绑定完整信封 |
| P03 | 审批后篡改 action/scope/ID/版本/proposal/sources | 许可不匹配，零执行；根和子 Agent 都覆盖 |
| P04 | 规则视图转换失败、混合格式、合同标识缺失/错配、同名自定义工具 | 内置调用显式拒绝，不退回空视图或默认 allow；自定义工具不被当作内置合同 |
| P05 | 升级前旧格式待执行/待批准调用 | 不写库、不自动转换后复用旧批准，历史可读 |
| I01 | 正常 record / 同类 update / disable / forget | 原来源、事务、CAS、历史、遗忘抑制和时钟断言保持 |
| I02 | 坏引文、假身份、跨项目来源、旧版本、直接改类 | 与当前业务错误一致，不被参数转换绕过 |
| I03 | detail 提示被模型原样拿来调用 | 新 request 入口可执行；历史工具 detail 不受影响 |
| I04 | 项目会话及 personal-only 普通聊天的自动召回/权限、后台 A/B、显式 refresh 的产品回归 | 检索和触发不变；产品 refresh 仍 accepted 后正常释放；和评测禁用分开 |
| Q01 | calibration 全八例 | 参数拒绝数、业务拒绝原因、保存状态、召回、回答、成本分别记录 |

测试不得全靠 model_validate；至少 record、read、更新拒绝、根/子审批、旧调用拒绝通过真实 host.runs 路径。模型替身只用于机械合同，不当真实效果证据。

## 11. 预计修改文件与保留项

| 文件/范围 | 责任 |
| --- | --- |
| scripts/memory_foreground_evaluation.py | 阶段失败、反馈、计数、单例结果、资源收尾、前台评测拒绝 refresh |
| scripts/memory_space_time_probe.py | followup 准备/模型/解析边界；默认 session_id 和后台调用行为保持 |
| tests/data/memory_space_time/ 下新增 foreground 专用协议/标签 | 冻结入口差异，引用旧 fixture 哈希；不覆盖后台冻结资产 |
| scripts/evaluate_memory_optimization.py | manifest 补新动作模块、权限消费者、实际前台装配与协议文件 |
| scripts/memory_evaluation_runner.py、memory_worker_evaluation.py、memory_recall_evaluation.py、review_memory_evaluation.py | 按第 14 节补当前链覆盖、逐事实/B 关系和成本对账；扩展已有职责，不另建评测框架 |
| scripts/probe_memory_semantics.py、tests/data/memory_optimization/ 与 memory_space_time/ 新版本资产 | 当前后台真实批次、版本化矩阵、分组及共同评分身份；旧报告和旧冻结文件保留 |
| naiyue_test_agent/tools/memory_actions.py（新增） | 八动作与信封、编译；已有 JSON schema 注册/工具构造不重做 |
| naiyue_test_agent/tools/memory.py | 切换工具 public/execution schema，保持 MemoryInput，许可比对后编译 |
| naiyue_test_agent/tools/permission_rules.py | 固定 memory 规则投影与旧配置校验，不改变其他工具匹配算法 |
| naiyue_test_agent/agent_runtime/tool_execution/permissions.py | 验证真实内置 public schema 后写入可信 argument_contract，许可和规则匹配沿现有链路 |
| naiyue_test_agent/application/memory/memory_context.py | 仅模型可见 detail 的调用形状，不改召回算法/预算 |
| tests/test_memory_foreground_evaluation.py 与新动作/权限回归 | E/C/P/I 矩阵；超过 500 行按职责拆文件 |
| 现有 memory tool/workflows/API/approval/root-child 等测试及前台探针 | 只迁移实际工具调用，保留业务断言；API/库调用仍 flat；scripts/release_memory_smoke.py 的已安装工具 refresh 调用也须迁移并从 wheel 验证 |
| docs/memory-tool.md、当前总索引和 handoff | 当前用法和版本边界、结果、遗留项与唯一下一步 |

真实业务存储和过期/比较模块本片预期不改。没有新锁，锁层次预期不变；若实现确需改变锁作用域，先修订本稿及 lock-hierarchy，不顺便改并发设计。

## 12. 评测与结论口径

实施后先完成离线和工程检查，再沿既有只读模型配置分别执行当前候选后台、前台两个完整 8 例校准批次，每批最多 24 次逻辑调用、两批合计最多 48 次。不增加失败补跑或“格式修复重发”。SDK 已有重试策略原样记录，不能将逻辑请求数冒充 HTTP 次数。两入口各自的记录、指标和预算不能混成一个平均成功率。

工具参数错误、来源/CAS/改类拒绝、候选/激活、逐事实召回和最终回答分别列数。06 正确的业务拒绝不计成 schema 改善失败；07 是否选择了错误的新建、是否保留局部确认范围仍须独立分析，不由新 schema 自动解决。检索保持不变，后续未召回不能算工具 schema 回归，也不能因此从业务评分中删掉。

现有 S7 报告只能作为问题来源及历史参照。S8 修了标签和评测失败边界，不能直接拿旧报告与新报告算“同协议提升百分比”。正式比较继续使用 S5 已冻结的旧链与最终候选，评测协议修复后两版执行同一材料/共同评分；不能只比较新前台 schema 而宣称解释了整个记忆方案的收益。S8 实现前另保存当前源码供故障追溯，不自动再增加第三条真实采样基线。

完整验收仍按 S5 的两个入口×三个 split×两个版本×每批 24 上限计算，理论总上限 288 次逻辑调用，分阶段执行，不是一轮直接发出。候选 calibration 的最多 48 次包含在其中；原 S0 七类阶段的预算/重复设计不与现行两维 format=2、每批 repeat=1 协议叠加。新协议若需增加重复必须先明确新预算。执行前展示本阶段冻结清单、版本身份和最坏调用数；本次“合并待办”没有启动真实请求，也没有把先前单批 24 的执行范围静默扩大成一次 288 的运行。

calibration 若已暴露明确效果问题，先提交两入口的结果分析，按用户结论继续或调整；不为了完成清单直接消耗 validation/holdout。用户选择保留现方案时，可继续测量真实限制；不足仍计入质量结果，不修改检索、降低门槛或改标签来造通过。

人工评审需覆盖每条事实、来源、scope、kind、保存状态和后续回答；模型自述“已记住”不作为成功证据。无人工语义验收时明确 needs_human_review；全绿单测不等于真实模型效果通过。

实施验证命令以当时现有项目入口为准，至少包含：

```bash
.venv/bin/python -m pytest tests/test_memory*.py tests/test_model_memory_output.py tests/test_probe_memory_semantics.py tests/test_multi_project_memory.py -q
.venv/bin/python -m pytest tests/test_memory_foreground_evaluation.py tests/test_memory_evaluation_report_safety.py -q -W error
.venv/bin/python -m pytest tests -q
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec143bm-s8/dist
.venv/bin/python -m twine check build/spec143bm-s8/dist/*.whl
git diff --check
```

完整后端所需 fd/rg 使用项目既有测试资产按命令临时加入 PATH，不修改用户环境。构建后检查新模块真实打包且与源码一致。未通过完整回归不提交；本片没有隐含 commit/push/发布授权。

## 13. 设计依据与本轮验证的限度

- 本机固定依赖为 LangChain 1.4.2、langchain-core 1.6.6、Pydantic 2.13.5，已读其实际工具构造与项目注册/许可消费者。LangChain 官方支持 Pydantic/JSON Schema 输入，并隐藏注入的 ToolRuntime：[工具文档](https://docs.langchain.com/oss/python/langchain/tools)。
- OpenAI 的受约束 schema 要求根为 object，并支持合规的嵌套 anyOf；本稿据此选择 request 包裹，不启用或修改全局 strict 配置：[官方限制](https://developers.openai.com/api/docs/guides/structured-outputs)。这不证明当前兼容网关或其他供应商已通过真实请求。
- 本轮离线两分支原型已通过现有 build_tool_schemas、ToolRegistry 和 OpenAI/Anthropic 工具转换，runtime 不暴露；证据 `build/spec143bm-s8-spec/schema-probe.json`。没有实现八分支、没有真实供应商验证。
- Pi 本机 a69bef789：agent-loop.prepareToolCall → validateToolArguments → executePreparedToolCall，先规范化/验证参数再交给执行；AgentX 本机 3fcfa0e：ToolDefinition.toSpecification → AbstractBuiltInToolProvider.createTools/createToolExecutor → executeTools，定义模型可见参数与执行器分别接线。这里只借鉴“声明与实际消费一致”；不声称它们使用本稿的 memory 信封或本项目权限模型。两者为当前本机源码快照，不是上游最新版断言。
- 前次 S7 只读审查记录为记忆相关测试 626 passed / 22.17s，严格专项 31 passed / 1.78s；四类故障注入仍复现，说明现有测试未覆盖，不代表问题已修好。本次待办合并只做文档/现有证据对账，没有重跑这些测试或产品构建、没有真实模型采样。

## 14. S0—S8 未完成事项统一对账与关闭标准

本节响应用户“之前的分片还有没完成的吗，有的话合并到一起”。当前代码、分片最后实施记录和本地报告共同作为依据；历史正文中“下一步做 S2/S3/S4”等旧进度不重新变成待开发功能。

### 14.1 各分片现在还缺什么

| 原分片 | 已有交付与核对依据 | 真正剩余项 | 统一去向 |
| --- | --- | --- | --- |
| S0 | 96 条名义资产、离线入口、逐事实评分入口；独立 worker/召回校准各 8 条 | 场景覆盖与当前入口未闭合；旧项召回/B 关系等完整评分、成本、可比基线和留出验收未完成 | U01—U06 |
| S1 | build_batch/证据编号/binder 已接生产 runner，分片实施记录及 build/spec143bm-s1/ | 本轮未确认有独立产品实现尾项；长输入、配对、裁剪等纳入统一覆盖证据 | U01、U06，不重做 S1 |
| S2 | 多片段来源、核验属性、生命周期和迁移已接写入/读取；后续 S3/S5 已使用 | 本轮未确认有独立实现尾项；清理后来源、部分确认、TTL、迁移纳入联合验收 | U01、U06，不重做 S2 |
| S3 | A→检索→B→commit 主链与修复已实现，后续 S6 已替换抄写门禁 | B 真实关系判断、旧项召回和最终记忆质量尚无完整独立验收 | U02、U04、U05 |
| S4 | 有限拆查询/合并/预算/权限已实现；多事项校准及离线对照已有记录 | 完整分组的事实召回、误注入、成本/基线对照；不包括用户延期的检索升级 | U01、U03、U05 |
| S5 | 两类、期限、迁移、个人完整组已实现；后台旧/新首轮校准已有报告 | S5-3 效果未通过；完整逐事实人工评分、两个入口的 validation/holdout 未完成 | U02、U04—U06 |
| S6 | 程序装配动作及 07 离线重放已验证；build/spec143bm-s6-actions/ 明确无真实模型复跑 | 还没有用修复后的当前后台完成新的真实校准；旧输出重放不代表模型会生成正确新动作 | U04，必须包含后台批次 |
| S7 | 前台真实工具驱动和首轮 8 例报告已存在 | 五项审查问题、专用标签、可信报告，以及前台验证/留出入口 | S8-0、U02、U04、U05 |
| S8 | 本稿中用户已确认新信封方向 | 动作参数/权限/详情提示迁移及整个统一收尾尚未实施 | S8-1—S8-4 |

“未确认有独立实现尾项”来自本次接线和记录对账，不是重新完成整模块 bug 审查，也不保证没有未知缺陷。工程历史结果引用各阶段原报告，最终仍须在同一候选源码上回归。

### 14.2 合并后的六项共同交付

**U01：完整覆盖表和真实执行入口，归 S8-2。**

- 对原 input/comparison/recall 各 24 条及两维 semantic 24 条按行为列覆盖表，映射 `requirement_id → fixture case_id / test nodeid → 生产入口 → 必要断言 → 证据`。不能把“文件里有 96 个 case”当成 96 个行为都验证了。
- 现 `memory_evaluation_runner.execute_case` 的 comparison 仍是受控公开 record、B=N/A；保留它的历史基线责任。当前比较验收扩展 `memory_worker_evaluation`，真实走 MemoryJobs/A/B/提交，而非另造一条直接写库替代链。
- 现 worker 与多项召回矩阵各只有 8 个 calibration 场景。补各 split 的代表性覆盖：长/多消息、工具配对及裁剪、重复和多 span、旧源清理、七类比较关系、期限/更正、权限/隐私、失败不重试、预算/版本冲突、相关与干扰项；已有确定性测试能提供等价证据时直接映射，不机械复制测试。
- 修订资产另建版本及哈希，保留旧冻结数据/报告。两维 semantic 集接替旧七类语义验收，不能把旧类型标签直接改名后计成新通过；共同事实及边界覆盖仍须有映射。

**U02：评分闭环和两个入口的运行能力，归 S8-0/S8-2。**

- 复用 `review_memory_evaluation`，保留已有逐事实提取/有效/注入分母；补旧项召回、B 关系/目标、重复新增、事实级范围/时间及所有实际危险新增主张的审阅记录。未输出和输出错误分开，未尝试组/预算省略不从总体分母消失。
- 前台无 B，B 指标为不适用；后台 B 观察缺失是未完成，不能用不适用豁免。06 等禁止前台直接操作的专用标签以 S8-0 为准，不借用后台成功标签。
- 后台 CLI 已支持三个 split；前台当前硬编码 calibration。前台补显式 `--split calibration|validation|holdout` 和执行前计划校验，不提供绕过冻结顺序的真实 `all`。每批仍 8 例/最多 24 次。
- 当前 space-time 资产 24 例正式 sources 均为 human；不为了扩入口强行伪造混合角色。U01 的工具/助手输入边界使用真实角色和归档的确定性测试；若新增真实混合角色样本，须先补身份准入，不拼成用户消息。
- 人工材料绑定原报告哈希、case/fact/实际输出标识、reviewer 和依据。助手初审单列为草稿，不能替用户填“人工已通过”。

**U03：成本和证据身份闭环，归 S8-2。**

- 复用已有模型回调、usage/阶段事件和只读 DB 计数，分开 A、B、前台工具任务和 followup；报告逻辑调用、可观测 SDK attempts、输入/输出 token、查询/list/read 次数、预加载大小及放弃原因、等待/调用/提交/总耗时。
- 已有字段直接消费，缺口在评测边界做有界记录；不建设新遥测平台。缺失值明确 null/unknown，不补造 token、费用或耗时。未观测 HTTP attempts 不阻止其他已证实指标报告，但不能声称传输尝试数为 0。
- 同批 manifest 固定源码、实际工具 schema、资产/评分协议、模型有效参数和 settings；安全过滤已在前序修好，不重新实现。旧报告缺失的身份不回填成当前值。

**U04：当前后台＋前台的真实校准，归 S8-3。**

- 首先对最终候选执行两个独立完整批次，后台 8 例和前台 8 例分别审阅，不能用前台结果替代 S6 的后台新效果。重点检查 07 部分确认是否真正只附来源、02 工作区范围是否仍误判，以及来源/分类/状态/召回各阶段的遗漏。
- 按第 12 节保留 S5 旧链作为最终整体方案对照。旧报告若缺少新协议/参数身份不能直接用于非退化结论；在隔离快照中用相同评测侧修复取得新报告，不修改旧产品算法。旧版前台使用该快照真实导出的工具 schema；评测侧沿 S5 baseline 政策映射旧种子/标签，不能把新信封硬塞给旧工具、回移 S8 产品实现或靠丢弃字段造兼容。旧链无能力的指标注明政策差异或不适用，共同业务指标仍比较。
- 初轮候选至多 48 次；calibration 若完成两入口×两版本，阶段总上限 96 次。两者是包含关系，不额外相加。一个失败案例不单独补跑，修订后只执行新的完整冻结批次。

**U05：validation、冻结与独立 holdout，归 S8-4。**

- 校准分析后才进入 validation；确认版本/提示/参数后冻结，按原协议执行隔离旧版与候选 holdout。两个入口分别报告，单批 24、每个完整阶段至多 96，三阶段理论合计至多 288。
- 留出结果不用于本轮调参；若查看失败后修订算法，旧留出降为回归，另建独立问题家族和新协议，不能反复使用同一集合直到通过。
- 保留原零危险项、充分证据正例及同标签基线不退化门槛。当前检索限制可能造成未达标，必须保留结论；用户决定延期检索升级不等于授权将质量失败改成通过。

**U06：统一工程回归与关闭报告，归 S8-2/S8-4。**

- 最终候选执行相关/全后端、受影响前端、标准构建和安装 smoke；保留来源、状态、时序、CAS、审批和持久化断言，记录同一源码身份。各历史切片的全绿不能替代最终组合验证。
- 交付一个覆盖/待办台账和一份最终验收报告，逐项区分 `done / open / deferred / superseded`，done 必须指向证据；分列工程、模型语义安全、召回/回答收益和成本。旧文档只保留版本历史与此入口，不再各自排“下一步”。
- 若工程完成但质量门槛未达，明确“实现交付、质量未通过”；不能以提交文档或跑完命令关闭 S0/S5 的业务验收项。真实用户库采用、部署和发布不属于本计划。

### 14.3 已完成、被后续替换和明确延期的事项

已完成：S1/S2 主链接线、S3 两阶段替换、S4 多查询、S5 两类/迁移/个人组、S6 程序装配动作、报告 reasoning 过滤/有效模型身份、07 旧输出离线重放、普通召回离线对照。相关数据继续进入回归，不把它们重新列为产品待开发。

被后续合同替换：七类/固定最近三条/旧 TTL、要求 B 重抄不变正文、早期单阶段或无 B 的候选验收口径。原始资产和报告留存，当前需求以 S5/S6/S8 为准；旧直接写入实验不冒充当前 B 质量。

明确延期：降低检索阈值、主题提取/向量检索等检索升级。它们继续留作后续优化，本次只度量现有召回并如实报告。其他供应商/平台的额外实网验收、真实库迁移使用和发布也不因合并待办自动执行。

统一下一步为 S8-0，随后依次 S8-1 → S8-2 → S8-3 → S8-4；各阶段结果影响是否进入下一阶段，不再另开 S0/S5/S6/S7 的重复收尾支线。


## 15. S8-0 实施记录（2026-11-17）

- 五项新增回归先红：反馈丢失、followup 异常、setup 清理、标签和 refresh；原入口为 5 failed / 7 passed，见 build/spec143bm-s8-0/red.log。
- run_foreground_case 在 setup 前建立案例记录，回调持续维护调用/输入/输出/反馈；预算检查前采集已发生反馈。真实 record 后预算失败仍保存行及对应反馈，不增加业务调用。
- case 接管 catalog；应用资源栈和 case 收尾共用一次性关闭入口。关闭失败保留错误类型/阶段，日志不记录原异常正文；批次只清理交接前失败的 catalog。八例各有一行，失败计数不清零。
- followup 保留 prepare/model/parse 阶段、已生成请求/回答和写入差值；根轮快照先保存，后续失败不覆盖。未知快照为 null。
- 临时权限 deny refresh_overview，通过真实权限检查拒绝；根轮检查真实任务列表及意外 refresh 调用，协议偏离不进行 followup。仅允许 foreground/followup 模型阶段。
- 06 禁止新约定的强制提取/激活/注入，独立回答要求只描述旧试用事实或未知；原后台标签保留，前台标签版本/哈希单列。所有旧来源、CAS、改类及不重试规则保持。
- 新增故障覆盖还包括清理失败的八例报告和实际 followup 准备/解析失败。严格专项、记忆回归、全后端和标准构建结果以 build/spec143bm-s8-0/verification.json 为准；串行全后端中途主动停止，最终使用标准四分片结果，不计中止运行通过。

正常链：CLI → case 记录 → 应用/真实工具/许可/保存 → 快照及零后台检查 → followup → 退出清理 → 汇总报告。失败链：任一阶段异常 → 保留已有数据和失败阶段 → 一次性清理 → 同例 failed → 后续独立案例 → 部分报告/非零退出；无业务重跑。

下一步为 S8-1：按已确认合同同步迁移工具动作参数、权限和 detail 提示。S8 整体与真实语义质量仍未完成。


## 16. S8-1 动作合同实施（2026-11-17）

工具名 memory 保持，public/execution 统一 request 信封，八分支各有必填 action、严格 extra=forbid；从当前 MemoryInput 复制字段限额，必填字段移除 null 分支，写入来源 1—8 条。既有 MemoryProposal/MemorySource 和跨字段校验复用。没有另一套放宽的内部模型或旧格式自动包装。

正常链：ToolSpec.validate_input → prepare_permission_call 识别内置生成类型并写可信 argument_contract → match_rules 临时编译旧标量视图，保留 subject.args 完整信封 → 原审批/许可绑定 → execute 再验证信封、许可和合同标识 → compile_memory_tool_request → 原 flat MemoryAPI → 来源/CAS/生命周期/事务。MemoryJobs.allowed、个人自动召回仍使用原 flat 参数。

失败链：旧格式、混合字段或不相关字段在公开校验拒绝；合同错配或许可篡改在业务 IO 前拒绝；新信封通过后遇坏源/旧版本/直接改类，仍沿原业务错误返回，零重试。缺少合同标识的旧许可不能继续执行；同名自定义工具按其自身 schema/规则处理，不能仅因同名就套用新规则视图。

迁移：所有实际模型 memory 调用、根/子审批与工作流测试、源码外安装 refresh 烟测改为新信封；内部 MemoryInput、直接 API、后台与存储测试保持原调用。memory_context 的 detail 提示改为 tool + request；history detail 不变。当前工具文档与源码指纹清单同步更新，不改写冻结 fixture/历史报告或真实设置。既有字段构造/注册和权限主链继续使用，没有新增锁或依赖。

最初 15 项新测试均在旧实现失败；后续动作/许可/SDK转换与真实拒绝边界追加验证。严格专项78项、记忆662项、全后端四片7065项通过（0失败/错误/跳过）；标准Mac构建/Twine、源码外安装及5生产文件wheel一致通过。全量启动后追加的5项新测试在最终严格/记忆专项单独验证。证据位于 build/spec143bm-s8-1/verification.json。真实模型请求为 0，模型/网关的八分支实际效果留给 S8-3，不将离线 SDK 转换当成供应商验收。

下一步 S8-2：共用评测覆盖、评分/成本缺口与各 split 运行能力。S8 整体和业务质量尚未完成。


## 17. 审查修复及 S8-2 首单元（2026-11-17）

四项审查问题先写6项失败回归：setup重复行、三种非对象request、主异常后快照异常、旧链源码清单。修复后每case一行；refresh观察先判断request为对象；主运行失败在读取快照前记入record，后续异常追加；manifest按真实产品源码是否导入动作模块区分旧链缺失，仅该已知旧版能力允许缺失并列absent_legacy_sources，当前链及其他必需文件缺失继续失败。未复制新产品模块进冻结旧链。

S8-2 首单元支持前台显式 --split calibration|validation|holdout，默认calibration；加载模型前拒绝非法split、不完整8例和超过24调用计划，报告保存split及worst-case计划。4项分组回归先红后绿，无真实模型请求。选择验证/留出组须遵守第12节冻结顺序；本轮测试仅用离线替身，不声称留出已执行。

正常：严格加载冻结资产→选择完整split→预算计划→每例独立运行→汇总报告。失败：计划非法在模型目录加载前拒绝；单例setup失败保留一行并继续；主运行与快照同时失败保留两阶段及实际调用数。旧产品+当前评测脚本在临时镜像成功生成manifest，原快照未改动。

证据 build/spec143bm-s8-fixes/verification.json。S8-2 仍未整体完成：U01覆盖映射/各split代表性矩阵、U02完整B关系/目标/新增主张评分、U03分阶段成本及最终共同回归仍待做；本单元仅完成分组入口与四项修复。唯一下一步继续S8-2覆盖表及评分接线，不启动S8-3真实校准。


## 18. S8-2 覆盖对账起点（工程证据，不代替模型效果）

下表映射当前真实入口和既有确定性断言；本次完整回归结果关联第17节verification。它是U01的起点，尚未完成全部fixture/split逐项映射。

| 要求 | 生产入口 | 确定性证据（tests/ 下 nodeid） | 必要断言 / 状态 |
| --- | --- | --- | --- |
| 多span来源 | memory_batches → memory_sources | test_memory_evidence_storage.py::test_two_spans_are_both_resolved | 同消息两段均解析；工程覆盖 |
| 部分确认 | commit_comparison → memory_mutations | test_memory_program_owned_actions.py::test_source_only_real_commit_preserves_every_content_clock_and_cas | 正文/时钟/CAS不变；工程覆盖 |
| 旧源不可用 | memory_job_runner → commit_comparison | test_memory_program_owned_actions.py::test_worker_source_only_handles_missing_old_archive_without_retry | 合法新源补证且不重试；工程覆盖 |
| 关系与来源选择 | compile_comparison_decisions | test_memory_program_owned_actions.py::test_reuse_cannot_silently_expand_chosen_evidence_or_choose_one_proposal | 不扩证据/不任选P；工程覆盖；真实关系评分open |
| 预算跳过 | memory_job_runner | test_memory_s3_review_fixes.py::test_budget_skip_reports_partial_without_comparison_call | partial、B零调用；工程覆盖 |
| 版本冲突组 | comparison_candidates → runner | test_memory_s3_review_fixes.py::test_version_changed_proposal_invalidates_shared_group | 关联组不绕CAS；工程覆盖 |
| 有限完整查询 | memory_context query planner | test_memory_query_plans.py::test_single_oversized_sentence_is_omitted_not_cut | 不硬切超限句；工程覆盖 |
| 个人完整组 | prepare_memory_parts | test_memory_personal_conventions.py::test_all_four_complete_bodies_and_readonly | 完整加载且只读；工程覆盖 |
| 写入权限 | prepare_permission_call → memory.execute | test_memory_action_contract.py::test_existing_deny_rules_block_nested_writes_in_all_modes | deny优先且零写库；工程覆盖 |
| 失败案例完整性 | run_batch → run_foreground_case | test_memory_s8_review_fixes.py::test_setup_failure_has_one_row_per_case | 8行/8唯一ID；工程覆盖 |
| 三组运行计划 | run_batch → planned_calls | test_memory_s8_review_fixes.py::test_foreground_runs_selected_complete_split | split隔离、8例/24上限；离线工程覆盖 |

后续仍须映射长/多消息与工具裁剪、迁移/遗忘/隐私及各split的代表性材料，并实现B关系/旧项召回/重复新增/危险新增主张的完整评分与成本；不把本表列出的工程覆盖写成模型质量done。


## 19. S8-2 人工比较与输出评分单元（2026-11-17）

按 [评分细则](143bm-08b-review-metrics.md) 接入 memory-facts-v1 报告合同。历史无标识报告沿旧评分；新前/后台报告显式标识，源码manifest包含评分模块。后台每个事实均要求比较记录，未尝试仍保留总分母；旧项召回、完成率和已完成正确率分别统计；前台comparison_metrics=null。逐事实范围/时间和重复新增单列。所有保存的(scope,id,version)及实际followup回答必须有人工支持判断，漏评/引用越界拒绝，无依据输出阻断安全通过，未知快照明确unavailable。此模块只检查材料完整性及人工判断，不自行推断语义。

7项新回归先红，随后增加真实review接线/历史兼容测试，专项与整体验证见build/spec143bm-s8-metrics/verification.json；无真实模型调用或人工评分结论。正常链为两入口报告→绑定报告hash的人工材料→原case/fact校验→新比较/全部输出覆盖校验→独立指标与安全结果；缺材料抛错，主工程失败不被人工评分救回。

U02 本单元已接新比较/输出评分，代表性矩阵和U03成本仍未收尾；唯一下一步为成本观测与完整覆盖对账，不提前进入S8-3。不将本次新增字段称作S8整体完成。


## 20. S8-2 成本观测与覆盖资产对账（2026-11-17）

[成本合同](143bm-08c-cost-observation.md)已接后台ProbeCallback与前台Budget；准入后按run_id计模型调用，完成/失败回调采耗时与usage。未知usage使阶段总token为null，同时保留已知token和未知调用数；零调用阶段为0。临时MemoryAccess实例观察领域list/search/read调用和比较读取组/计划查询，原返回/异常保持，退出恢复，排除种子写入。后台案例抛错时失败项仍附已观测costs。HTTP、SQL、独立等待/提交和预加载token仍为null，不能据此声称完整费用/SQL性能已验收。

2项新增成本测试先红；固定时钟验证混合usage/失败，原异常和观察恢复；既有真实后台三次调用和前台2+1调用测试增加cost接线断言，相关62项通过。冻结源码后跑完整回归，证据build/spec143bm-s8-costs/verification.json，无真实模型请求。

coverage-inventory.json记录旧96条及两维24条资产的suite/split/来源角色、哈希和第18节11个真实test node。状态明确为inventory_complete_behavior_mapping_partial：这不是96场景行为均已覆盖的证明；原comparison受控公开写与当前真实B矩阵仍区分，当前worker/多事项召回各仅8条校准，验证/留出工程矩阵及每case等价映射仍open。

S8-2尚未整体完成。唯一下一步是U01逐case覆盖与worker/召回验证、留出工程矩阵收尾；U03缺失指标保持显式未知，不为填满数字扩建遥测平台。不进入S8-3真实校准。


## 21. S8-2 工程矩阵与覆盖收尾（2026-11-17）

按[矩阵合同](143bm-08d-engineering-matrices.md)新增worker/recall版本化工程资产各24条、三组各8条，旧校准资产保留；两个现有evaluate/CLI支持显式split，hash/ID/family/别名先校验，无网络模型。新worker覆盖全部七种关系、null复用、缺源/过期补证、推断候选、部分更正、无证据与坏P/O拒绝，核对真实写入/行数/版本/状态/时钟和两次逻辑调用。召回按真实注入集合、去重及零写库验证列项/条件/路径/否定/URL/围栏/截断和干扰。

memory_coverage_evaluation逐项执行旧input/comparison/recall共72例并标注入口，旧semantic24标为类型合同被替代，两维24标为等待真实采样/审阅；汇总新48条工程矩阵。旧受控写入12项预期不符原样保留，不冒充当前B质量或清零。对应关系分支映射跨工程组，仅作机械覆盖，不声称同语义家族等价。[工程覆盖表](143bm-08e-engineering-coverage.md)补31个现有test节点，节点存在及本轮完整回归分别核验。

7项分组测试先红后绿，随后补全量对账和hash/family防护；专项28通过。最终全后端、标准构建和覆盖报告见build/spec143bm-s8-matrices/verification.json及coverage-final.json。正常链：版本化资产校验→真实worker/召回→状态/注入断言→逐例对账；坏hash/组/别名在执行前拒绝。无新真实模型、配置/用户库操作、提交发布。

S8-2的本轮工程接线与覆盖对账收尾；不等同S0/S5业务验收通过。U03未知成本限制继续公开保留。下一步S8-3：冻结最终候选/旧基线及协议、展示阶段预算，分别运行后台与前台完整校准并分析，不直接消耗validation/holdout质量集。
