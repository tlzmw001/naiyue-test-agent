# Spec 040：有界内存的大文件 `edit`

状态：2026-10-02，已按本 Spec 实现并完成本地确定性验证；真实供应商调用和跨进程一致性尚未验证。用户指出 Spec 039 的 `edit` 与整文件 `write` 共用默认 1 MiB 预算，导致小范围修改较大文件也被拒绝。本 Spec 只处理这个问题，不改变跨会话同目标冲突策略。

## 当前链路与目标

当前 `edit._edit` 先读 `NAIYUE_WRITE_MAX_BYTES + 1` 字节并把整个原文解码成 `str`，算出新正文后调用 `write._write(..., expected_content=original)`；后者再次把新正文整体编码，并在进程内同目标占用锁下比较原始字节、建立同目录临时文件和 `os.replace`。因此原文件和结果都受整文件写入上限约束，内存至少随文件大小增长。模型的 `path/edits[{oldText,newText}]`、工具名、成功与错误 `ToolMessage`、工作区权限和未知提交后的会话隔离均已接线，本片保持这些公开契约。

验收例子：工作区内有 16 MiB 的有效 UTF-8 文件，目标片段只有几十字节。模型提交一次精确 `edit` 后，仅目标片段变化；`read` 仍按原分页/搜索能力定位上下文，模型无需把 16 MiB 内容放进上下文。本片不设置 `edit` 的固定原文件或结果文件大小上限，也不新增大小配置项；`NAIYUE_WRITE_MAX_BYTES` 继续限制模型整文件 `write` 的内容，同时作为 `edit` 单次提交替换文本的预算（默认 1 MiB），但不拿这个预算限制原文件或生成结果。不修改 `.env` 文件，也不新增依赖。文件仍受可用时间和磁盘空间等实际资源约束，不能承诺任意规模都能完成。

## 推荐处理方式

1. 以已经验证的工作区路径规则打开现有普通文件，在整个“扫描 → 构造 → 提交”期间持有同一进程内目标占用权；其他会话同目标仍收到原有 `busy` 工具错误，不排队。不同文件仍可并行。保持普通权限位、BOM 与未被替换的字节原样。
2. 用固定大小分块读取，增量验证整份文件是合法 UTF-8，并计算原文 SHA-256。把每个 `oldText` 编码成 UTF-8 字节，在块与块之间保留最长模式的末尾重叠区，保证跨块命中不会漏掉或重复计数。每个旧片段必须在原文中恰好命中一次；多个字节区间不得重叠。输入替换片段的累计 UTF-8 字节数仍不超过整文件 `write` 的 1 MiB 模型输入预算。编码、缺失、重复或重叠失败时均不创建正式替换结果。
3. 匹配全部通过后，在目标同目录建私有临时文件。第二遍从原文件分块复制未修改区间，跳过旧片段、写入新片段；同时对第二遍读过的**全部原文字节（包括被替换区间）**重算 SHA-256，并检查原文件身份/大小。若两遍原文不同，清理临时文件，回填“文件已变化，请重新读取”的错误 `ToolMessage`。完整写入后保留普通权限位、flush/fsync，重新验证目标路径，再用 `os.replace` 提交；提交边界无法确认时沿用 `WriteOutcomeUnknown`、关联事件和应用会话待核对状态。
4. 扫描和复制只保留固定块、最长匹配片段、少量匹配偏移与模型提供的替换文本，内存随模型输入和编辑项数量增长，不随原文件线性增长；临时磁盘空间随输出文件大小增长，磁盘不足是可确认未提交错误。跨进程写入在最后一次校验与替换之间仍存在 TOCTOU，不能称跨进程事务或沙箱。

不采用“只去掉现有上限，保留整文件读写”：当前 `read`、解码、`updated` 和重新编码会同时保留多份全文，文件越大，内存风险越高。Pi `a69bef789` 的 `edit` 是完整 `readFile`/全文字符串/`writeFile`，可作为功能行为参照，但不是本项目有界内存方案。

## 文件、接口与实施顺序

- `naiyue_test_agent/agent_runtime/tools/edit.py`：保持公开 `create_session_edit_tool()`、工具名和模型 schema 不变，改为分块扫描与分块构造；不新增 edit 文件大小环境变量。原有小文件也走同一算法，避免两个路径的匹配语义漂移。
- `naiyue_test_agent/agent_runtime/tools/write.py`：只整理 `edit/write` 共用的私有目标占用与临时提交步骤，让 Edit 能在扫描前取得占用权并复用原提交/结果未知契约；不改变现有模型 `write` 的参数、1 MiB 默认上限或跨会话 busy 行为。若现有代码能小范围复用，避免新模块；确需拆文件时先同步本 Spec。
- `tests/test_edit_tool.py` 增加先红灯的真实临时文件用例；`tests/test_write_tool.py` 与必要的应用测试保护原有写入、冲突和未知结果路径。`README.md`、`docs/future-work.md`、`docs/handoff.md` 同步已验证能力与限制。本片预计涉及 7–8 个文件（含文档），不升级依赖、不改环境/凭证文件。
- TDD 顺序：先让 1 MiB 以上文件的小范围编辑用例正确失败；再使单文件扫描/复制转绿；随后补共享占用与提交边界，最后全量回归。不把现有测试改成跳过或放宽来迁就实现。

## 验收与回退

- 正常：16 MiB UTF-8 文件中的短片段成功编辑，原文其它字节、BOM、CRLF、普通权限位保持；多个不重叠替换、模型同批 `read→edit→read`、不同文件并行保持。
- 拒绝：跨 64 KiB 分块的唯一匹配必须命中；缺失/重复/重叠、跨块无效 UTF-8、模型替换文本超预算、工作区外/符号链接、文件被另一会话占用均回填原调用 ID 的说明性错误，原文件不变。
- 失败：扫描后外部修改、复制中修改、临时文件写入失败均不替换正式文件；`os.replace` 结果未知触发 `run_failed` 与会话待核对，禁止自动重试。用事件同步测试竞争，不以短 `sleep` 判定时序。
- 验证命令：`.venv/bin/python -m pytest -q tests/test_edit_tool.py tests/test_write_tool.py tests/test_application_edit_cli.py`、`.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`。记录实际输出；另以受控 16 MiB 样本记录运行时间和进程峰值内存，不能仅凭算法声称性能已验证。
- 代码回退只涉及本地模块与文档；开发验证仅操作临时工作区文件。对真实工作区一旦 `os.replace` 成功，不承诺自动回滚，结果未知时沿用现有人工核对流程。

同目标队列、Pi 模糊匹配、diff 预览和工作区外编辑审批不在本片。队列若进入下一片，须先决定跨会话整文件 `write→write` 是保持冲突拒绝还是允许后写覆盖，并定义等待上限与停止语义；目前无证据要求改动已确认的冲突安全边界。

## 本地实施证据

- 红灯：先增加 16 MiB 文件跨块短替换测试；旧代码返回 `SizeLimit`，该用例 `1 failed`。随后实现分块扫描、UTF-8 增量校验、原文哈希、同目标占用、分块临时写与提交前复核；`write` 仍保留整文件预算和原有对外接口。
- 新增及调整的测试覆盖跨块唯一命中/重复、跨块无效 UTF-8、扫描后及复制中外部修改、临时文件 I/O 失败、扫描期间同目标 `write` busy、原字节/BOM/CRLF/权限位、原调用 ID 回填和未知提交终局。原有“扫描后修改”测试更换注入点以适配新链路，断言没有放宽。
- 定向 `.venv/bin/python -m pytest -q tests/test_edit_tool.py tests/test_write_tool.py tests/test_application_edit_cli.py`：`61 passed`（复制中修改测试增加前）；该新增测试单独 `1 passed`。最终 `.venv/bin/python -m pytest -q`：`378 passed in 7.49s`；`compileall -q naiyue_test_agent tests scripts` 退出 0；`pip check` 输出 `No broken requirements found.`。
- 独立 16 MiB 临时文件探针：结果 `edit replaced 1 block(s); 16777236 bytes`，前缀实际变为 `prefix VALUE suffix`，耗时 `0.021` 秒，`tracemalloc` 记录本次 Python 分配峰值 `0.19 MiB`。这只是本机单次样本与 Python 跟踪内存，不包含解释器基线、内核页缓存或高并发负载，不能推断所有文件规模的性能。
