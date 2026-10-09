# Lesson 17：会话工作区如何到达 Read 工具

状态：2026-10-01，代码已讲解；用户正确复述工作区来源及框架注入时点，记“引导后理解”。独立实现及面试追问待验证。

## 一条具体数据流

示意工作区 `/work/A` 与 `/work/B` 各有一个 `contract.md`，内容分别为 `A` 和 `B`。当前假模型测试用临时目录和 `FIRST_PRIVATE` / `SECOND_PRIVATE` 验证同一 Agent 的两个会话，见 `tests/test_application_workspace.py`。

1. CLI 有 `--workspace /work/A` 时创建会话版 Read 工具，并调用 `app.create_workspace_session(model_id, Path('/work/A'))`。工具注册时并未绑定 `/work/A`；这个目录属于应用会话。
2. `SessionService.create_workspace_session` 先用 `resolve_workspace_root` 验证并规范化目录，再存入该会话记录的 `workspace_root`。另一个会话可存 `/work/B`。
3. `SessionService.run` 在锁内读取该会话记录的 `thread_id/workspace_root` 并生成新的 `run_id`。工作区不为空时调用 `invoke_agent_run_in_workspace`，最终以 `agent.invoke(..., context={'session_id': ..., 'run_id': ..., 'workspace_root': root}, config={'configurable': {'thread_id': ...}})` 启动本次图运行。`context` 是本次运行注入的数据；`thread_id` 用于 checkpoint 历史，两者职责不同。
4. 模型只看到 `read_file(path, offset?, limit?)`。当模型发出 `{'path': 'contract.md'}`，LangGraph `ToolNode` 在真正调用工具前构造 `ToolRuntime`，从模型参数中去掉伪造的注入字段，再注入本次可信的 `runtime`。项目工具从 `runtime.context['workspace_root']` 取到 A 的根目录，调用共同的 `_read_file(root, path, offset, limit)`，解析为 `/work/A/contract.md`。B 的同名路径则解析到 `/work/B/contract.md`。
5. A 传入 B 的绝对路径或指向 B 的符号链接时，`target.relative_to(root)` 不能证明目标属于 A，工具返回固定错误；缺少工作区上下文也固定错误。这里验证的是应用传入的会话根目录与文件目标之间的关系，不是多用户身份认证，也不是操作系统沙箱。

## 源码入口

- `application/cli.py`：注册会话版工具，创建工作区会话。
- `application/__init__.py`：`create_workspace_session → _create_session → run`。
- `agent_runtime/__init__.py`：`RunContext` 与 `invoke_agent_run_in_workspace → _invoke_agent_run → agent.invoke`。
- `agent_runtime/tools/read_file.py`：`SessionReadFileInput`、`create_session_read_file_tool`、`_read_file`。
- 本机 LangGraph `ToolNode._inject_tool_args`：先剥离模型传入的注入字段，再填入构造出的 `ToolRuntime`；本机 `BaseTool.tool_call_schema` 把注入字段从模型可见 schema 排除。

## 理解检查

若 A、B 会话共享一个 Agent，且模型在 A 的 run 中提交 `read_file(path='contract.md')`，指出 `workspace_root` 最初由谁写入、`runtime.context` 由谁注入，以及最终文件路径为什么不会落到 B。用户回答：应用创建会话时传入工作区；框架在调用工具时注入运行信息，工具再从中取得工作区。回答正确，补充了每次 run 会先从会话取目录并写入 `agent.invoke(context=...)` 的中间一步。记录为“引导后理解”；尚无独立改写或面试追问证据。
