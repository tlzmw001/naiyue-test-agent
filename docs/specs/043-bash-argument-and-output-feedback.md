# Spec 043：Bash 参数错误回填与有界尾部输出

状态：2026-10-02，已按 TDD 实施并通过本机验证。用户确认先补 [Pi Bash 结果链审计](../research/pi-bash-result-chain-2026-10-02.md)里已复现的两个非权限缺口；本片保持 Spec 042 的显式宿主机开关、逐命令审批、工作区 cwd、默认超时、未知副作用终局和 CLI 交互，不引入沙箱、完整输出文件或流式 UI。

## 当前链路与问题

模型的 `AIMessage.tool_calls` 到运行层 `HumanInTheLoopMiddleware`。现有 Bash 审批规则对任何 `bash` 调用都中断；应用 `_pending_tool_approval` 再取 `args['command']`。若缺少 `command`，应用在工具 schema 校验前抛 `RuntimeError`，模型没有收到带原调用 ID 的 `ToolMessage`。正常执行时 `bash._capture` 只保留 stdout/stderr 各自最初 4096 字节，后续输出被丢弃，且可在 UTF-8 多字节字符中间截断。测试失败摘要常在末尾，模型可能只见到前面的进度文本。

## 本片契约

1. 模型可见 Bash 参数用同一份严格 Pydantic schema 校验：`command` 非空字符串、最多 8192 字符，`timeout` 可选且为 1–300 秒整数，不允许未知字段。HITL `when` 对无效参数返回“无需审批”，让调用进入 ToolNode 的参数校验并形成原 ID、`status='error'`、可行动说明的 `ToolMessage`；应用组装审批请求时用同一校验只纳入有效 Bash 调用。有效调用保持原有完整命令审批和一次性 grant。无效调用不得启动 shell，不能造成 `run_failed`。
2. stdout/stderr 仍分别限 4096 **原始字节**，但改为持续保留各自的最后 4096 字节，超过预算标记 `truncated=true`。返回 UTF-8 文本时不因人为截断产生替换字符：丢弃尾部窗口开头不完整字符的续字节，再解码；命令原本输出的非法 UTF-8 仍可由 `errors='replace'` 表示，不冒称原文有效。现有 `exit_code`、`truncated`、stdout/stderr 标签和非零 ToolMessage 错误语义保持。
3. 不保存完整输出文件；如果以后需要回读，另定位置、大小、权限、清理和授权。超时、子进程未退出或输出读取错误仍沿用 `BashOutcomeUnknown` 的保守终局，不在本片改成普通模型错误结果。

## 影响文件与 TDD 验收

- `agent_runtime/tools/bash.py`：把模型参数 schema 与运行时注入 schema 分层；提供一个供 HITL 和应用共同调用的参数有效性判断；把 `_capture` 改为有界尾部环形窗口，并在解码边界处理 UTF-8。
- `agent_runtime/__init__.py`：Bash HITL 规则接入条件；既有工具装配和 Read/ls/find/grep 规则不变。
- `application/__init__.py`：待审批列表跳过无效 Bash 参数，使行动请求与待审条目保持一致；有效命令仍绑定原调用 ID、全文和工作区。
- `tests/test_host_bash.py`：先添加缺失/空命令、无效 timeout 的假模型回填用例，确认红灯为当前 `RuntimeError` 或错误审批；再加有效+无效混合同轮、长输出末尾 `FAILED 1 test`、跨 UTF-8 截断边界，以及临时 pytest 成功/失败真实命令结果。测试只对临时文件运行已知安全的命令，不调用外部服务或真实模型。
- 更新 README、future-work、handoff 与本 Spec 的真实验证结果。生产代码仍低于 500 行，不修改 `.env`、依赖、公开函数签名或既有宿主机权限取舍。

验证命令：`.venv/bin/python -m pytest -q tests/test_host_bash.py`、`.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`。记录红灯与绿灯的实际输出；CLI/模型可见内容以假模型 `ToolMessage` 接线和临时 pytest 命令证明，不把它写成真实供应商行为。

## 本机验证结果

- 红灯：先加入 9 个行为样本，定向运行得到 `7 failed, 10 passed in 2.77s`。失败分别落在无效参数先进入审批组装、长输出丢失末尾摘要和 UTF-8 截断；临时 pytest 成功/失败样本在旧逻辑下也能通过。
- 绿灯：`.venv/bin/python -m pytest -q tests/test_host_bash.py` 得到 `17 passed in 2.75s`；全量 `.venv/bin/python -m pytest -q` 得到 `395 passed in 8.86s`。`compileall` 退出 0；`pip check` 输出 `No broken requirements found.`。
- 无效参数经 HITL 条件放行到 ToolNode 校验，原调用 ID 的错误 `ToolMessage` 已由假模型运行验证；有效与无效同轮时只审批有效命令。真实临时 pytest 命令的通过/失败摘要分别回填，长输出末尾摘要及跨 UTF-8 边界样本通过。没有真实供应商调用、完整输出回读、SaaS 授权或 OS 沙箱证据。
