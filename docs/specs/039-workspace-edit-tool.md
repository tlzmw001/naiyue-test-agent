# Spec 039：会话工作区内 `edit` 精确替换

状态：2026-10-02，已按 TDD 实现并通过本地确定性测试。用户要求在 `write` 后继续开发 `edit`；本片沿用 Spec 038 已确认的工作区写入边界。

## 现状与参考

当前 CLI 在指定 `--workspace` 时注册 `read/ls/find/grep/write`；应用每次运行将可信 `workspace_root` 放入 `agent.invoke(context=...)`，LangGraph 为工具注入 `ToolRuntime`。`write` 提供严格 schema、路径校验、大小上限、同目标进程内冲突、临时文件替换、提交结果未知时会话隔离；运行层通过工具 metadata 对含顺序工具的整批调用按模型原序执行。

本机 Pi 提交 `a69bef789` 的 `packages/coding-agent/src/core/tools/edit.ts` 接收 `path` 与 `edits[{oldText,newText}]`，先读原文件，再对原始内容找唯一且不重叠的匹配，最后写回；它还处理 BOM、换行、模糊匹配、diff 展示，并用同文件 mutation queue。我们的第一片采用同一基本输入和原文坐标语义，但**只做精确匹配**。Pi 的模糊匹配和预览 UI 暂不移植；本项目的工作区隔离、错误回填和结果未知语义沿 Spec 038。AgentX 已核对的内置工具注册机制提供职责参考，未找到等价本地文件 edit 契约。

## 用户行为与边界

- 模型调用 `edit(path: str, edits: list[{oldText: str, newText: str}])`。`edits` 非空；每个 `oldText` 非空、在**同一份原始正文**中恰好出现一次；多个命中区间不得重叠。`newText` 可为空。先验证所有编辑，再一次写回；一项无效则文件不变。完全相同的替换返回说明性错误，不伪报修改成功。
- 仅编辑已存在、工作区内的普通 UTF-8 文件；沿用 `write` 的隐藏/敏感/符号链接/遍历规则，不自动建目录，也不能新建文件。文件原文和替换结果各受当前 `NAIYUE_WRITE_MAX_BYTES` 上限约束；解码严格，不做损坏字符替换。保留原始 BOM、未被替换区域的换行字节和普通权限位；新文本按模型提供的字符原样写入，不自动转成文件原有换行风格。模型要改动某行必须提供与文件一致的精确文本。成功只返回修改块数与字节数，不回显正文或完整 diff。
- 执行前若文件内容在读取后变化，返回“文件已变化，请重新读取后再编辑”的错误工具消息，避免以旧快照覆盖新内容。`edit` 声明 `sequential`；与 `write` 共享现有同目标占用控制。跨进程变化在最终检查与替换之间仍有 TOCTOU 窗口，不声明强原子 compare-and-swap。
- schema、路径、匹配、重叠、大小、编码、可确认未提交的 IO 错误均为说明性 `ToolMessage(status='error', tool_call_id=原 ID)`，模型可重读重试。提交边界结果未知继续抛 `WriteOutcomeUnknown`，由现有应用会话隔离处理，不能自动重试；日志不记录文件正文、旧/新片段或原始异常路径。工作区外编辑审批、模糊匹配、diff UI、跨进程事务与崩溃后恢复另片设计。

## 接线与文件

- `agent_runtime/tools/edit.py`：严格输入、原文件读取、精确多处定位、不重叠验证、组装新正文、可信上下文与工具错误策略。复用 `write.py` 的私有路径和提交边界，避免两种写工具绕开不同安全规则。
- `agent_runtime/tools/write.py`：私有 `_write` 增加可选原始字节快照校验，在已存在的同目标锁内检查；原 `create_session_write_tool` 签名、模型 schema、结果不变。若快照不符，返回安全冲突错误；其他写路径保持不变。
- `agent_runtime/tools/__init__.py` 与 `application/cli.py`：导出并在有工作区时注册 `edit`。运行层/应用层已有 context、顺序 metadata 和未知提交异常处理，预计不改。
- `tests/test_edit_tool.py` 与 CLI 接线测试：先红灯，再实现。覆盖单/多处替换、相同原始坐标与重叠/重复拒绝、缺失/多处匹配、空替换、BOM/CRLF、非 UTF-8、越界/敏感/符号链接、读后变化、同批 read→edit→read、工具原 ID/status、未知提交会话隔离、CLI 真正注册。回归已有 write 测试。

## 验证

先运行新测试确认预期失败；实现后运行定向测试、全量 `.venv/bin/python -m pytest -q`、`compileall`、`pip check`，记录实际结果与限制。假模型及临时真实文件只证明本地接线和文件效果，不证明真实供应商会按 schema 正确调用或测试用例质量。

## 实施与验证记录

- 新测试先因 `create_session_edit_tool` 尚未导出而在收集阶段失败（`1 error`）；实现并接线后，`tests/test_edit_tool.py tests/test_application_edit_cli.py tests/test_write_tool.py` 为 `55 passed in 1.12s`。后补可信运行上下文伪造测试后，全量测试为 `372 passed in 7.29s`；随后又增加未知提交关联事件断言，最终全量复测为 `372 passed in 7.24s`；`compileall` 退出 0，`pip check` 为 `No broken requirements found.`。
- `edit.py` 已实现模型可见 `path/edits[]`、严格参数、可信工作区、原始文件唯一精确匹配及不重叠检查、BOM 与未触及的 CRLF 原样保留、字节预算和说明性错误。它复用 `write._write(expected_content=...)` 的现有临时文件提交与同目标占用锁；准备阶段和提交前都比较有界读取的原始字节，发现变化就回填错误。`edit` 声明 sequential，CLI 已注册；提交结果未知继续由原应用会话隔离逻辑处理。
- 证据为假模型、真实临时文件及 CLI 模拟输入；未调用真实供应商，也未测跨进程并发或断电恢复。Pi 的模糊匹配、diff 预览和同目标排队未实现；本项目选择唯一精确匹配、冲突拒绝。文件系统路径与内容校验之间仍有 TOCTOU 窗口，不能宣称沙箱或真正的跨进程 CAS。
