# Spec143BM-01：按对话关系整理输入并绑定原文证据

日期：2026-11-16。状态：**S1 已接入工作区，本地验收记录见第14节**。

本片对应 S1，属于 [Spec143BM 总索引](143bm-memory-optimization.md)。业务目标与已确认的失败跳过边界见 [总体方案](../长期记忆优化总体方案-2026-11-16.md)。源码核对基线为 `60108431ceeeaf100df008da4765b879505fc1d7` 加当前工作区；实现时必须保留其他会话修改。

本片使模型读到有关系的对话，而不是任意字符片段，并让模型选择程序生成的原文编号。它不负责判断新旧记忆应否合并；两阶段执行、事项检索和最终决定属于 [S3](143bm-03-proposal-comparison.md)，证据落库和同消息多片段去重属于 [S2](143bm-02-evidence-lifecycle.md)。

## 1. 完成后的行为与范围

- 用户、助手、工具消息保留消息边界和真实调用关系；短交互能完整装入时不拆开。
- 4096 字符仅作为读取分页，不再自动成为模型输入的语义切点。
- 超长交互按完整消息、段落、句子、代码/日志行逐级装批；最后的硬切明确不完整。
- 模型以 `sources=[{"evidence_id":"E1"}]` 选择原文；程序还原真实版本、位置和引文。
- 重复前文只提供理解依据，不被当作新增处理范围或新的确认。
- 所有提议与证据映射仅存在于本轮内存；失败、超限和版本冲突跳过，不重试或恢复。

本片不改公开 `memory` 工具参数，不增加模型调用，不创建后台恢复表、提议包、继续入口或提交回执。不改变既有 active/candidate/TTL 语义，不引入模型分段器、向量服务、通用条件系统或新依赖。

## 2. 当前主链与问题位置

正常生产链如下，名称均对应当前文件，不把测试辅助函数当生产入口：

```text
MemoryJobs.after_finish / request
  -> MemoryJobs._release：必要归档已保存，固定 thread/revision/upper，accept 推进接纳位置
  -> memory_job_runner.drive -> _batch -> _prepare
  -> build_batch(storage.memory, ...) -> MemoryRepo.batch_reader(SECRET)
  -> ArchiveReader.next_visible / visible -> history_projection.VISIBLE_TEXT
  -> model_batch -> _messages -> prepare_memory_output -> bound.ainvoke/invoke
  -> parse_model_result -> proposal_request
  -> MemoryAccess.query -> memory_mutations.commit -> memory_sources.resolve
```

`collect_chat_updates()` 已生成 `reply_id`、工具结果的 `parent_message_id`，`reply_storage` 保存真实回复边界；无需另建对话存储。`history_projection` 已提供可见原文与 Python 字符位置，排除 system、压缩摘要、图片 carrier、隐藏思考和 artifact。

当前 `memory_batches._batch()` 每次读取 `source_chars`，再二分截取能放下的任意字符前缀。内部虽有 `reply_id/tool_call_id`，`model_batch()` 未把完整关系交给模型。当前 `_Reference` 要求 `source_key+quote`，`proposal_request()` 在片段中查找唯一精确引文，重复句和格式差异均可能被拒绝。

失败链：归档分支不匹配或 SQLite 读取失败 -> `MemoryReadError` -> runner 记录安全原因并结束本轮；模型输出/来源绑定失败不重新调用模型。已接纳位置不回退。错误不能转换成空批次并声称成功。

## 3. 数据归属与消费接口

### 3.1 原文单元 EvidenceUnit

在现有 `contracts/memory.py` 增加内部 TypedDict 合同；不新增对象注册层。实例由 `memory_batches` 创建，runner/S3 消费；存储层只返回受限行数据，不调用应用层分段逻辑。

```python
EvidenceUnit = {
    "unit_id": str, "evidence_id": str | None,
    "turn_key": str, "message_key": str, "sequence": int,
    "session_id": str, "thread_id": str, "message_id": str,
    "revision": int, "conversation_revision": int,
    "role": Literal["human", "ai", "tool"],
    "reply_id": str | None, "parent_message_id": str | None,
    "tool_call_id": str | None, "name": str | None, "tool_status": str | None,
    "occurred_at": float, "user_origin": bool,
    "text": str, "start_offset": int, "end_offset": int, "text_chars": int,
    "origin": Literal["new", "overlap"], "context_only": bool,
    "partial_before": bool, "partial_after": bool,
    "tool_pair_status": Literal["not_tool", "paired", "missing", "invalid"],
}
```

位置采用当前 `visible()` 的 Python Unicode 字符下标，左闭右开；`text == 原可见正文[start_offset:end_offset]`，不先 strip、改换行或规范化再保存位置。`message_key` 只在本批定位消息，`turn_key` 只表示已核对的交互关系，都不是可写的记忆 ID。

`unit_id` 为本批有序 `U1...`；可引用单元额外取得 `E1...`。编号只在当前批次有效，不要求跨任务稳定。`evidence_id is None` 表示只供理解，模型不得引用。没有持久化编号或从编号重新查库的公开接口。

完整自然片段最多 300 字符，与 `MemorySource.quote` 上限一致。字符数不等于 token 数，完整消息仍可由相邻多个单元共同表达。超长片段不能为了获得编号而剪掉否定或条件；无合适自然边界的硬切片段只有 `unit_id`，不能支持写入。

`partial_before/after` 表示本批没有展示该消息相应方向的全部原文。完整句可以位于部分展示的消息中，因此“有编号”只证明该 span 完整，不证明其限定充分；缺少必要前文的提议必须由后续比较拒绝。

### 3.2 MemoryBatch 与模型投影

```python
MemoryBatch = {
    "units": list[EvidenceUnit], "evidence_map": dict[str, EvidenceUnit],
    "next_position": list[int], "complete": bool, "history_version": int,
    "skipped": list[str], "context_char_limit": int,
    "coverage": {
        "read_ranges": list[tuple[int, int, int]],
        "omitted_ranges": list[tuple[int, int, int, str]],
    },
}
```

`read_ranges` 记录本批送入模型的新增 `(sequence,start,end)`，排除 overlap；`omitted_ranges` 记录明确跳过的正文区间及固定原因。两者均受本批扫描预算约束；覆盖记录不能包含原文或可变模型错误文本。没有被读取的整条消息不能因只展示了 metadata 就计为全文已读。

`model_batch(batch)` 返回 `units/complete/skipped/coverage`，每个模型单元只投影 `unit_id/evidence_id/turn_key/message_key/role/text/origin/context_only/partial_before/partial_after/occurred_at/user_origin/tool_pair_status` 及必要的 `reply_id/parent_message_id/tool_call_id/name/tool_status`。内部完整来源身份、完整 archive 行和 `evidence_map` 不重复发送。

工具调用头使用 `role=ai/text=""/context_only=true` 的元数据单元，只含真实 parent、call ID、名称和配对状态，不包含参数、reasoning 或 artifact。它不拥有证据编号，不独立推进正文覆盖。模型可看到“调用关系未知”，程序不能虚构配对。

### 3.3 BoundProposal 交给 S3 的合同

```python
bind_proposal_evidence(proposal: Mapping, batch: MemoryBatch) -> BoundProposal
BoundProposal = {
    "proposal": dict, "evidence_ids": list[str],
    "sources": list[dict], "evidence": list[EvidenceUnit],
    "context_units": list[EvidenceUnit], "context_complete": bool,
}
```

此函数只处理引用合同：`proposal` 必须为对象，`sources` 为 1–8 个仅含 `evidence_id` 的对象，ID 唯一且全部在本批映射。业务字段由本阶段对应 schema 校验，返回的 `proposal` 移除 `sources`；不会在此选择旧记忆、决定 action 或执行写入。

`sources` 从映射构造并逐个通过现有 `MemorySource.model_validate()`：`current_user=false`、完整 session/thread/message/revision/conversation_revision、精确 `quote=unit.text` 与 `offset=unit.start_offset`。`evidence` 保留对应可信角色、时间、调用关系供 B 读取，不允许模型覆盖。

允许引用已展示的完整 overlap 片段作辅助依据，但至少一个引用必须来自 `origin=new`；纯 overlap 提议拒绝。同一旧消息新增一个引用位置也不产生新的确认时间。跨消息的事实需要多条引用；超过 8 个支持片段则跳过该提议，不擅自删证据凑数。

binder 唯一负责生成 `context_units`：从本批已展示单元中取每个引用在同消息内紧邻的前后自然单元及真实工具调用头，按原序去重；已作为 evidence 的片段不再重复。只纳入完整单元，不补读、不截半句，原文总字符不超过 `batch.context_char_limit`，该值来自 `batch_overlap_chars`。上下文不会自动进入 `sources`。

若引用已位于消息真实起止边界，该方向不需要邻接单元；否则缺失邻接材料、预算省略或配对信息不完整时 `context_complete=false`。该标记必须送 B；若结论依赖缺失限定则跳过，不能把“编号有效”当作支持充分。S3 若放不下必要 evidence/context 整组跳过，不另行摘要这些原文。

若 B 发现结论依赖 context 中 A 未正式引用的必要限定，本轮跳过相关提议；不自动把 context 升级为 sources，也不重新调用 A 补引用。只有最终正式 sources 完整支撑的结论才能保存；因此 A 应同时引用主张和必要限定，而不是仅挑一个容易匹配的短句。

## 4. 归档读取与交互边界

扩展现有 `ArchiveReader`，仍在 `MemoryRepo.batch_reader()` 的同一只读事务中取得一个批次。所有 SQL 值使用绑定参数，字段投影用固定 allowlist；不返回 `body_json`。

拟新增内部方法如下，返回普通 dict/list，不新建通用分页框架：

```python
metadata_page(session_id, thread_id, *, start, upper, limit) -> {"rows": list, "more": bool}
turn_bounds(session_id, thread_id, *, sequence, upper) -> dict
tool_link(session_id, thread_id, *, parent_message_id, tool_call_id, before_sequence) -> dict | None
```

`metadata_page` 复用 `VISIBLE_WHERE/FIELDS/VISIBLE_TEXT`，返回现有长度、整消息敏感标记和时间，增加程序认定的 `user_origin`、真实输入/steering 标记。`tool_link` 在 SQL 内仅核对指定 AI 消息的调用 ID/名称，结果必须位于同 session/thread、工具结果之前；不能按最近一个助手消息猜关系。

精确返回约定：`metadata_page.rows` 包含当前 `next_visible` 的字段，加 `user_origin:bool/input_kind:str|None`；`more` 只说明冻结范围还有行，不把未返回行算已读。`turn_bounds` 返回 `turn_key/start_sequence/end_sequence/start_known/end_known`，未知边界以当前消息为范围、known=false；end 始终不超过 upper。`tool_link` 缺失返回 None，存在时返回 `status/parent_message_id/parent_sequence/reply_id/tool_call_id/name/occurred_at`，status 只为 paired/invalid；重复 call ID、错误名称或跨回复配对返回 invalid，不选择其中一条猜测。

`turn_bounds` 优先使用已有 `naiyue_reply_boundaries` 的真实用户输入位置与 `reply_id`。steering 仍属于正在进行的交互，不开启伪造的新根轮次。旧数据缺边界时，可用可核验的 root `naiyue_human_input` 作为开始；无法确认时使用单消息 `unknown:<sequence>` 单元，不推断跨消息归属。

冻结范围截在轮中时 `end_known=false`，完整轮次装批只能对已知边界作承诺；输出继续/缺口标记，不把范围末尾冒充真实交互结束。回复边界是元数据定位，历史内容仍必须通过原 visible allowlist；上下文摘要不能因有 reply_id 而重新成为普通消息。

调用 ID、工具名称、parent 关系必须一致；缺失标 `missing`，冲突或重用造成歧义标 `invalid`。这类工具正文可作为明确不完整的上下文，但不分配可写证据编号。并发工具结果可任意返回顺序，始终按真实 call ID 绑定，失败/未知状态不变成成功。

空正文的 AI 工具调用也保留受限调用头，否则工具结果会无端失去来源。读取预算内找不到调用头时，不无限向前扫描。名称/ID 不是执行权限；超长或敏感关系字段不截成另一个身份，标关系不可用。

冻结 `upper` 限定新增正文范围；不会把任务开始后新到的消息并入当前批次。显式历史起点通过 `context_floor` 限制可用前文，前文不能跨项目、线程、分支或用户指定的下界。

## 5. 装批与超长分段算法

1. 校验 source scope、thread、conversation revision、start/upper；取得本事务 `history_version`。
2. 有界读取元数据并识别交互范围。先装一个完整交互；还能容纳时追加后续完整交互。
3. 如果当前批次已有完整交互，下一个完整交互放不下，直接结束当前批次；不要截下一条消息填空。
4. 单个交互超过 token/扫描预算，才按完整消息装入。工具调用头与结果关系保留，正文可分段。
5. 单消息仍超限，依次选择段落空行、完整句末、代码/日志换行边界；分隔符保留在原文 span 内。
6. 不按普通逗号或固定 4096 字符切句；括号/引号和 fenced code 内不按普通句号拆句。可用标准库逐字符状态和固定分隔符处理，不引入语义分词服务。
7. 生成最多 300 字符的可引用自然单元；更长且没有安全自然边界时，硬切仅作 `context_only`，带 `hard_cut` 原因。后半段若起于同一被硬切句内，直到下一自然边界也保持不可引用。
8. 每次尝试都按最终 `model_batch` JSON 计入包络预算。至少一个新单元与关系元数据都无法容纳时，跳过该消息未读尾部并记范围；禁止停在同一位置无限循环。
9. 返回严格前进的 `next_position`；只有新增已展示或明确跳过的部分推进它。后续批次来自同一任务剩余预算，不是失败重试。

完整片段能装下时不得先作任意字符二分截断。二分只可用于最终无自然边界的硬切上下文，或确定预算上界后回退到已知自然边界。

不会复制一个巨大消息到内存再尝试切分：正文按 `min(read_chunk_chars,剩余读取预算)` 读取，可跨 I/O 页合并未完成句；读页边界本身不生成证据编号。整消息敏感检查仍在 SQLite 内先于任何正文片段输出。

## 6. 前文重叠与进度

当前批次从长消息/长交互中间开始时，最多带 `batch_overlap_chars` 的同交互前文，优先保留真实用户限定与紧邻前文。只用原文；不用前一轮概览替代证据。查不到或放不下时省略并标 `context_omitted`，不猜内容。

overlap 来自同一可读分支与 `context_floor` 以上的原文，仍经过敏感、时间与可见性门禁。其完整片段可以辅助引用，不允许只有 overlap 的批次发起模型调用。批次编号重新分配，但真实来源 identity/span 不变。

`next_position=(sequence,offset)` 沿用现有含义：下一处尚未被本任务本轮处理的新增正文。overlap 不参与推进；范围中的敏感/空消息、未知时间及不可装入尾部通过明确 skip 推进。超出 `max_batches` 或总期限的尾部仍按原合同跳过。

`complete=true` 仅表示本批已走到冻结 upper 后方，不表示模型提取正确，也不表示每个消息全文都已提供。`read_ranges/omitted_ranges` 向 runner 的 coverage 和有界 skipped 原因转换；概览下界/上界只用新增范围，不能被 overlap 拉大。

现有持久 `accept()->next_sequence=upper+1` 不改。本片不增加成功游标、跨重启批次状态或待办。失败后的安全日志/现有概览覆盖信息用于诊断，不构成可恢复承诺。

## 7. 预算与配置

新增项放进现有 `MemorySettings`，同步配置载入、完整示例和参数说明；不修改用户真实 `.env`、模型文件或运行配置。

| 字段 | 默认 / 合法范围 | 用途与约束 |
|---|---|---|
| `batch_max_messages` | 128 / 1–1024 | 本批读取元数据条数及关系查找数共享上限；含 overlap，不做无界扫描 |
| `batch_read_chars` | 65536 / 1024–262144 | 应用层累计取出的可见正文字符上限，含 overlap；不是 SQLite 整消息敏感检查上限 |
| `batch_overlap_chars` | 600 / 0–8192 | 同交互前文上限，须 `<= batch_read_chars // 4`；0 表示不附带前文 |

上述值是有界首版规格初值，不是实测最优。`read_chunk_chars` 保留原用途之外的公开读取行为，在本片仅作为 I/O 页上限，不要求它小于 `batch_read_chars`，实际读取取两者剩余额度最小值。

实际发送仍受 `_limits()` 计算的模型输入上限、输出预留、结构化 schema 开销、system、前概览和关系元数据共同约束。overlap 首先让出可选空间，不能挤掉唯一完整新证据。运行时正值/范围校验失败不自动扩大预算。

继续使用当前 `count_tokens_approximately` 与 `prepare_memory_output` 的既有计数/协议适配。另会话 Spec143BL 的未实施校准算法不成为本片依赖；不在这里增设第二套 token 校准平台。估算不保证供应商绝不超限，实际调用失败按原合同结束。

## 8. 具体接口迁移与接线

| 当前入口 | 本片拟改合同 | 实际消费者 |
|---|---|---|
| `build_batch(..., source_chars, model_view=False)` | 增加必填 `max_messages/max_read_chars/overlap_chars/context_floor`；删除内部双投影预算开关，统一按 `model_batch` 计数 | runner `_prepare`、正式批次测试及语义探针 |
| `_batch(archive, ...)` | 仍负责事务内读取和装批，按第 4–6 节返回 MemoryBatch | `build_batch` |
| `model_batch(batch)` | 只输出第 3.2 节投影，不输出 evidence_map 或完整可信来源身份 | runner `_messages`、预算测试 |
| `_Reference` | 从 `source_key+quote` 改为严格单字段 `evidence_id` | `_Proposal/response_schema` 与 binder |
| `bind_proposal_evidence(proposal,batch)` | 第 3.3 节内存绑定合同 | 现有单阶段请求构造；S3 的 A→B 接口 |
| `proposal_request(proposal,sources,related)` | S1 阶段改成 `proposal_request(proposal,batch,related)`，先用 binder，再复用既有写请求门禁 | S1 尚未切入 S3 时的 runner |

S1 可按已有单阶段合同先验收输入收益，不提前加入 B。此时 runner 的 related 预算可继续重算，但每次重建都遵守本片边界。S3 接入后移除 A 直接选择旧 ID/构造写请求的旧路径；最终统一由 S3 生成决定并提交，不长期保留两条并行处理链。

runner 从冻结 settings 传三个新增限额；`context_floor=max(1, job.requested_start or 1)`。`source_chars=read_chunk_chars` 保持 I/O 语义。`build_batch` 对所有数值作严格整数/上下界校验，context_floor 不能高于当前 start 所指范围；非法输入沿 `memory_batch_position_invalid` 拒绝，不靠 bool 冒充整数。

runner 原 `batch['sources']` 的空批判断改成“是否存在 `origin=new` 且非空正文单元”；只有调用头/overlap 时推进已有 skip 范围而不调用模型。S1 阶段 `_related` 如仍存在，只从新增正文单元构造原查询；S3 替换它时删除这个临时消费者。概览 coverage 与 proposal binder 分别消费 `coverage` 和 `evidence_map`，不从投影文本反推来源。

`parse_model_result` 的拒绝重复 JSON key、非有限数、截断、refusal、无效工具调用与严格字段合同保持。`text/json_schema/function_calling` 三协议均使用新引用 schema；S1 暂沿 `MemoryResult`，S3 接入时统一切成 `MemoryExtractionResult/MemoryComparisonResult`，同步模型适配测试并删除旧单阶段 schema，不能偷偷回退到普通文本协议。

`ArchiveReader.next_visible` 仅有当前批次实现消费者；替换其职责后删除旧内部方法和引用。`ArchiveReader.visible/saved/history_version` 仍被最终来源重验消费，必须保留。公开 `MemoryInput/MemorySource` 字段和工具说明中的精确引文用法保持。

后台旧 `_QUOTE_FORMAT` 及 `quote_not_found/quote_ambiguous` 手抄引文诊断随旧协议退出；迁移成 `evidence_id_missing/evidence_not_citable/overlap_only/evidence_limit` 等固定内部原因。不能为了保留旧诊断测试再接受第二套 `source_key+quote` 后台载荷；最终来源的精确原文核验仍然保留。

## 9. 正常与失败例子

正常例：第 10 条真实用户说“以后本地回归用 `.venv`。CI 保持原样。”，第 11 条 AI 发出 call-A，第 12 条工具成功，第 13 条助手解释。四条属于同一真实回复；预算足够时同批提供，call-A 的调用头和结果配对。

程序把两个完整句分别绑定 E1/E2，工具完整结果绑定 E3。A 提议本地环境引用 E1；若提议还概括实际验证，应另外引用 E3，并在比较时看到 CI 限定。binder 交付原文与真实偏移，不使用模型复写的 quote。最终是否激活仍由 S3/S2 的门禁决定。

失败例：第 12 条工具正文超长，在两个 I/O 页之间包含否定句。读取分页先合并未完句，不把前半句单独作为成功证据；模型预算不足则选择前一自然边界。仍无法完整提供时标缺口，依赖这一结果的提议跳过，其他完整用户声明可继续独立处理。

A 若引用 E999、伪造 metadata、只引用重叠前文或硬切片段，抛 `memory_proposal_invalid` 并给固定内部 diagnostic；不生成 candidate 作为失败替代、不调用修复模型。完整 JSON 无效时沿当前整次输出拒绝逻辑，不能从半个响应拼出权威概览。

## 10. 安全与失败分类

- 只有已保存、当前可见的 root 消息进入自动整理；不扩成全项目/子 Agent 扫描。
- 整消息敏感扫描先于任何分页输出，NUL/Unicode 行为复用现有 SQLite 可见投影；秘密在消息尾部也不能先泄露其前段。
- 系统提示、隐藏思考、图片数据、完整工具参数、artifact、压缩摘要均不作为新证据。
- 角色、用户来源、时间和成功状态仅由归档事实产生；模型看到这些字段也不能自行写回。
- 返回前来源结构版本变化、读取异常或非法位置抛安全错误；最终提交仍重新检查来源，不把只读时的一致性当作永久有效。
- skip 原因用固定码，如 `sensitive/time_unavailable/empty/envelope_too_large/hard_cut/context_omitted/tool_pair_unavailable`；日志只记 ID、范围、数量和错误码，不记完整原文/模型正文。
- 一条坏提议不影响其他独立提议；同一写入组的证据不完整时整组跳过。停止/超时不回滚已经确认成功的独立保存。

## 11. 影响文件与职责

| 文件/范围 | 必要变化 |
|---|---|
| `application/memory/memory_batches.py` | 装批、投影、编号与绑定；保留严格响应解析责任 |
| `application/memory/memory_job_runner.py` | 使用新批次 shape、提示引用合同、预算与 coverage；本片不加第二调用 |
| `storage/memory_sql/archive.py` | 有界元数据、回复边界与调用关系读取；不返回原始 JSON |
| `storage/conversation_sql/history_projection.py` | 复用其 SQL 表达式和 Unicode 行为；如需帮助函数须保持现有公开投影输出不变 |
| `contracts/memory.py`、`memory_settings.py` | 内部形状及三项真正被消费的限额 |
| `docs/examples/settings.example.toml`、`docs/examples/full/settings.full.toml`、`docs/memory-tool.md` | 同步三项配置与 I/O 页/语义边界说明，真实配置不动 |
| `tests/test_memory_batches.py`、runner/flat_contract/task_diagnostics/overview_progress/structured_output 对应测试 | 迁移编号协议和覆盖断言，新增本片矩阵 |
| `tests/test_model_memory_output.py`、`test_database_uow.py`、`test_probe_memory_semantics.py` | 验证真实模型适配、只读事务及探针消费者，保留原安全边界 |

`memory_batches.py` 当前 295 行、runner 当前 417 行。若实现会超过 500 行，将自然边界与单元装配放进 `application/memory/memory_input_units.py`，仅供 `memory_batches` 直接调用；理由是同一明确职责与行数限制，不建立通用 parser/resolver。新增前再次搜索已有等价分段器，不能复用会改变精确原文的检索分词器。

本片不改数据库 schema：所需关系与可见原文均已归档。新证据属性落库、来源片段身份及去重由 S2 统一迁移；S1 不另建来源表，也不承诺在 S2 前解决同消息多引用被存储去重的问题。

## 12. TDD 与验收矩阵

先用真实临时 SQLite 归档和受控模型建立失败测试，记录失败原因，再实现。现有 `save()` helper 固定同一 reply 且没有真实边界，必要时只在对应测试文件补真实边界构造；说明原因，不全局放宽 fixture 或断言。

| ID | 场景 | 必须断言 |
|---|---|---|
| S1-T01 | 多个短完整交互 | 完整交互同批；下一交互放不下时不切半条凑满 |
| S1-T02 | 4096 页边界穿过短句 | I/O 跨页后句子完整、原始 offset 精确 |
| S1-T03 | 超长单轮、多消息、长代码 | 先完整消息再自然边界；代码内句号不误切；缺口显式 |
| S1-T04 | 超长无标点、Emoji、NUL | 严格推进、重建新增字符无重叠或漏计；硬切不可引用 |
| S1-T05 | 并发工具、空 AI 调用头 | call ID 正确配对，结果顺序变化不串线，参数不输出 |
| S1-T06 | 孤立/歧义工具、错误状态 | 不伪造成功，不为不可配对正文生成可写证据 |
| S1-T07 | 相同句两次、空格/反引号 | 不同 E 定位不同真实 span；模型无需手抄，不使用模糊匹配 |
| S1-T08 | overlap 与新范围 | 不推进两次；至少一个 new 引用；新工具结果可辅助引用旧限定 |
| S1-T09 | 伪造/跨批 ID、重复 ID、9 引用 | 硬拒绝且无模型重试/无写入；其他独立有效提议不受影响 |
| S1-T10 | 300/301 字符自然单元 | 不突破公开 quote 上限；缺限定不因截短获得可写编号 |
| S1-T11 | 敏感尾部、隐藏块、跨 scope/thread | 全消息先检，隐私字节不进入批次/日志，旧来源安全断言保持 |
| S1-T12 | 三个新限额及极小输入预算 | 合法边界/非法组合；扫描、正文、实际投影分别有界且不死循环 |
| S1-T13 | source revision/history 变化 | 不提交旧引用；不重跑 A；已有独立保存不回滚 |
| S1-T14 | 三种模型输出协议 | 真正 schema 与解析同步，拒绝截断/refusal/重复 key/非有限数 |
| S1-T15 | 3 批/超时/重启 | 接纳基线保持，尾部跳过，重启不恢复任务、不补跑 |
| S1-T16 | 正常与失败接线 | 正式 runner 看到新投影；模型调用、覆盖、失败计数均显式断言 |

批次 token 断言改为真实发送的 `model_batch` 包络，不把内部 `evidence_map` 的重复索引算供应商输入；仍需独立检查内部读量有界，不能通过删预算断言让旧测试过关。旧引用入口测试迁移到 ID 合同，公开工具精确引文测试保持。

实施时专项命令：

```bash
.venv/bin/python -m pytest tests/test_memory_batches.py tests/test_memory_job_runner.py tests/test_memory_flat_contract.py tests/test_memory_task_diagnostics.py tests/test_memory_overview_progress.py tests/test_memory_structured_output.py tests/test_model_memory_output.py tests/test_database_uow.py -q
.venv/bin/python -m pytest tests/test_memory_lifecycle.py tests/test_memory_semantic_inputs.py tests/test_memory_scheduling.py tests/test_memory_workflows.py tests/test_probe_memory_semantics.py -q
git diff --check
```

整片合入前按总索引执行全后端、前端及项目标准构建；本片不自行发真实模型请求，业务效果使用 S0 的固定协议另行授权验证。源码修改后还须扫描 `source_key/quote` 的后台模型协议消费者和 `model_view` 旧调用，迁移脚本/测试后删除废弃内部分支。

## 13. 交付界限与待实现记录

L1：合同、实现及测试文件存在；L2：分段/绑定不是占位逻辑；L3：正式 runner 的实际模型输入和提交来源都经过新合同。仅纯函数测试通过不能宣称已接线，S1 通过也不能宣称 S3 的语义比较有效。

实现后记录验证命令、实际结果、结构变更与旧入口删除清单，更新总索引及对应handoff。初稿仅为文档设计；当前实施证据与限制见第14节，不能把文档设计或工程测试当成真实模型质量结论。

## 14. S1 本地实施记录（2026-11-16）

用户明确选择直接实施S1，S0不在本轮交付范围，临时评测草稿没有作为冻结基线。S2–S4尚未实施。

- L1：新增memory_input_units.py和test_memory_input_units.py；TypedDict合同在contracts/memory.py，三项配置进入MemorySettings并由runner消费。没有数据库迁移。
- L2：真实归档元数据、回复边界及工具配对；完整交互→完整消息→自然片段；跨页读取、300/301字符界限、重复句位置、硬切不可引用、有限前文与缺配对均有回归。
- L3：drive→_prepare→build_batch→ArchiveReader→assemble/model_batch→模型→parse_model_result→proposal_request→bind_proposal_evidence→MemoryAccess.query→原来源门禁。仍是单阶段，B复核留给S3；辅助context不自动写入sources。
- 工具继续使用已归档可见正文和有界元数据，不展开参数/隐藏内容/artifact。默认8轮或20000用户/助手字符的触发保持；来源重叠不算新确认，失败无新增重试。
- batch_max_messages共享消息与关系解析配额，读取先预留关系预算，并不承诺容纳128条正文；累计正文含重叠受batch_read_chars限制，前文总量受batch_overlap_chars限制，最终服从模型输入包络。

先观察到旧入口不接受max_messages、缺binder的4项红灯。补充边界分别复现同消息前文缺失、多行引号被切、工具头未进context、超长交互提前切下一消息，再修改实现转绿。新增14项用例；300/301和context不自动升证据为补充回归，不宣称每项均单独观察过红灯。

helper迁移原因：旧source列表/手抄quote协议退出，合同测试改用显式EvidenceUnit/batch，事务检查迁到真实memory_input_units.tokens。多批概览改用读取预算制造批次，为700条遗漏用例显式提供消息配额；保留范围、遗漏、版本和结果断言。

删除项：后台source_key+quote、_QUOTE_FORMAT及格式诊断、model_view双计数、ArchiveReader.next_visible。保留公开工具精确quote、visible/saved/history_version及来源重验。独立安装发现release_memory_smoke模拟输出仍用旧协议，已迁为E1，保留原任务成功/来源/持久化断言。

证据目录为build/spec143bm-s1/：verification.json汇总实际结果，source-verification.json核对6份产品源码与最终wheel逐字节一致，implementation-record.json记录红灯/范围/限制。最终全量采用现有ci_shards的8分片：accepted-backend-0.xml至accepted-backend-7.xml，共6716项，失败/错误/跳过均0；独立安装为accepted-install.json，memory.status=passed且provider_requests=0。

专项252项、新增输入边界14项、发行相关63项通过；前端78文件681项及TS/Vite生产构建通过；标准macOS ARM64 wheel、Twine、源外临时环境安装、compileall、pip check及diff检查通过。Vite仍提示既有chunk-size warning。SQL文本守卫曾把提示词select…from识别为SQL，改为同义choose后原守卫及相关67项通过，未放宽检查；调用头时间曾误取结果时间，红灯后改用归档调用时间，相关60项通过。最终8片及构建使用这些修正后的源码。

早期全量主动中止、SQL文本守卫失败、烟测旧协议失败均不计最终通过；以accepted-*报告为准。复验入口：

```bash
.venv/bin/python -m pytest tests/test_memory_input_units.py -q
PATH="$PWD/build/mcp-default-identity-assets/bin:$PATH" .venv/bin/python -m scripts.ci_shards --index 0 --count 8 --output build/spec143bm-s1/recheck-backend-0.xml
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec143bm-s1/recheck-dist
.venv/bin/python -m twine check build/spec143bm-s1/recheck-dist/*.whl
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec143bm-s1/recheck-dist --output build/spec143bm-s1/recheck-install.json
```

全量须将index依次取0至7，各运行一次；原生工具来自既有native_assets脚本产物。更换平台时使用该平台的正式target和新输出目录，不覆盖已有证据。

确定性测试与模拟模型证明工程合同；未请求真实供应商，未做S0留出评测。S2前同消息多span存储去重限制仍存在，S3前不能宣称语义复核和部分确认续期防护生效。未改真实配置/依赖/用户库，未提交发布；wheel包含并行工作区修改，只是本机验证产物。


### 14.1 S1 审查修复（2026-11-16）

只读审查复现后修复四处边界：重叠工具正文保留 missing/invalid 门禁，binder 再检查；调用头与工具正文按真实结果 sequence 同组装批，空头不推进正文游标；旧记忆检索回退只选择新增非空正文，没有正文则返回空候选；续读解析若有限前文覆盖消息起点，则合并解析后保留真实新增偏移，否则无法确认引号/括号/fence状态，续读正文保守仅作上下文。后者可能少提取长消息中的完整句，遵循宁缺勿错，不增加扫描预算或持久解析状态。

新增6项回归先得到6失败、原14项通过；最小修复后输入/装批56项通过。记忆相关回归404项通过；标准macOS ARM64 wheel构建、Twine、源外安装烟测通过，三份修复源码与wheel逐字节一致，真实供应商请求0。全后端分片1在并行token校准test_model_limits的模块属性校验处失败（1980通过、1失败），该文件新进程单独12项通过；分片0为3370项通过，关键记忆链路复验81项通过。本轮不将全量声明为通过，也未修改该并行模块或测试。仍按原触发阈值及失败不重试合同，不推进S2。
