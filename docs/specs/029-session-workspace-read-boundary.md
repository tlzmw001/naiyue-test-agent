# Spec 029：会话可信工作区传递到 Read 执行边界

状态：2026-10-01，已按 TDD 实施；权限决策与审批仍未实现。

## 当前数据流与问题

CLI 解析 `--workspace` 后立即调用 `create_read_file_tool(root)`，工具闭包固定一个目录，再由 `build_local_application` 把该工具注册到共享 Agent。`SessionService` 保存会话的模型和 thread ID；`invoke_agent_run` 向 LangGraph 运行上下文传 `session_id/run_id`。若同一个 Agent 服务两个工作区会话，现有工具仍只能使用启动时的同一个根目录。

## 本切片契约

- 应用层在新建工作区会话时验证并保存规范化的可信根目录。保留现有 `create_session(model_id)` 签名及无工作区会话行为；增加独立的 `create_workspace_session(model_id, workspace_root)`。CLI 的显式 `--workspace` 改走该入口；没有该参数时仍不注册 Read 工具。
- `SessionService.run` 在互斥锁内取得该会话的工作区快照，按 run 传给运行层。保留现有 `invoke_agent_run(...)` 公开签名，新增工作区专用入口复用其运行事件与终局处理。运行上下文扩展为可选 `workspace_root`；会话/工作区选择均不接受模型生成的工具参数。
- 保留 `create_read_file_tool(workspace_root)` 的公开签名与现有直接调用行为；新增会话版 `create_session_read_file_tool()`，由 LangChain `ToolRuntime` 注入本次 run 的可信上下文。两种工具共用同一读取与路径校验实现；模型可见 schema 仍只有 `path/offset/limit`。会话版缺失或无效工作区时固定错误回填，绝不回退到进程 cwd 或另一会话的目录。
- 本片仍只读工作区内的普通 UTF-8 文件，保留隐藏路径、敏感后缀和分页规则；不实现 `allow/deny/ask`、审批、外部目录访问、写入、Bash 或跨重启恢复。路径解析与打开之间的竞态仍是已知限制。

## 影响文件与验证

- 生产代码：`application/__init__.py`、`application/cli.py`、`agent_runtime/__init__.py`、`agent_runtime/tools/read_file.py` 与工具导出。
- 测试：先为共享 Agent 的双会话独立读取、交叉绝对路径/符号链接拒绝、无工作区上下文拒绝、CLI 工作区接线写失败测试；再做最小实现。保留已有固定工作区工具测试。
- 文档：完成后同步 `docs/handoff.md`、`docs/future-work.md` 及本 Spec 的实际命令/结果。验证定向 pytest、全量 pytest、`compileall`、`pip check`、`git diff --check`；真实供应商读取与多用户身份授权不在本片证据内。

## 实施与证据

- 新测试先红：`.venv/bin/python -m pytest -q tests/test_application_workspace.py` 在收集阶段因会话版工具尚不存在退出码 2。最小接线后，定向测试暴露显式 `args_schema` 不含注入字段导致 `Invalid read_file arguments.`；为会话版增加含 `ToolRuntime` 的内部 schema，模型可见 schema 仍只有 `path/offset/limit`。指定 `ToolRuntime[dict, dict]` 后消除 Pydantic 对上下文的序列化警告。
- 假模型驱动一份 Agent、两个不同工作区会话：同名相对路径分别返回各自内容；另一个工作区的绝对路径和越界符号链接均回填固定错误；模型伪造 `runtime.context.workspace_root` 被框架注入覆盖，仍只读原会话工作区。无工作区会话调用该工具也回填固定错误。CLI 既有工作区读取和分页接线测试继续通过。
- 定向 `.venv/bin/python -m pytest -q tests/test_application_workspace.py tests/test_read_file_tool.py tests/test_application_cli.py`：`53 passed in 0.69s`。全量 `.venv/bin/python -m pytest -q`：`218 passed in 3.71s`。`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`；`git diff --check` 退出码 0（当前文件尚未纳入 Git 跟踪，此检查不覆盖未跟踪文件）。
- 这些是本机确定性测试；未对真实模型、并发目录替换、多用户身份或跨重启恢复做验证。`ToolRuntime.context` 的可信性来自应用入口传入，不能把模型不可见字段当成用户身份认证。
