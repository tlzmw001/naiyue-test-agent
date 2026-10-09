# Spec 035：将文本读取工具统一命名为 `read`

状态：2026-10-02，已实施并完成本地确定性验证。用户明确要求把 `read_file` 及相关函数、类型一起改名，不保留两套容易歧义的名称。本片只改名称，不改变文件读取、工作区权限、审批、分页或模型调用行为；`write/edit` 和工具批次调度另写 Spec。

## 当前数据流与原因

CLI 创建 `create_session_read_file_tool()`，运行层通过 `SessionReadFileInput` 识别并配置 `HumanInTheLoopMiddleware(interrupt_on={'read_file': ...})`。模型返回 `tool_calls[*].name='read_file'`；应用按此名称识别外部文件审批，恢复后工具从可信 `ToolRuntime` 取工作区和一次性授权。`grep` 的长行提示也指向 `read_file`。当前只有进程内 checkpoint，尚无跨重启旧工具名迁移要求。

Pi 的对应文本/图片读取工具名为 `read`。本项目先采用一致的通用工具名，但当前能力仍限受控 UTF-8 文本，工具描述必须明确这一点。

## 修改范围与接口

- `agent_runtime/tools/read_file.py` 移为 `read.py`；`ReadFileInput`、`SessionReadFileInput`、`create_read_file_tool`、`create_session_read_file_tool`、`_read_file` 以及工具内部函数变量改为相应的 `ReadInput`、`SessionReadInput`、`create_read_tool`、`create_session_read_tool`、`_read`、`read`。不保留旧工厂别名。
- 模型可见工具名改为 `read`；分页提示、错误提示、日志事件和 `grep` 的长行提示统一使用新名称。可信根、审批 grant、路径/文本策略和输出预算不变。
- 同步 `agent_runtime` 的 HITL 名称、`application` 的审批分支和 CLI 注册；现有 `ls/find/grep` 的导入路径改为 `.read`，行为不变。把历史遗留的 `pending_read_approvals/resume_read_approval` 别名移除，只保留已有的 `pending_tool_approvals/resume_tool_approval`；泛指四种只读工具的一次性授权改称 `ReadOnlyApprovalGrants` 与 `read_only_approval_grants`，避免误以为只服务 `read` 或将来可授权写入。公开审批方法签名的命名变更由本次用户明确要求。
- 更新当前测试中的工具名、工厂导入、模块 monkeypatch 路径和精确提示断言；历史 Spec/交接段落记录的是当时名称，不追改历史。追加交接说明新名称及迁移边界。

## TDD 与验证

先增加新名称的失败测试，确认因缺少 `create_read_tool` 而红；再实施完整改名并更新旧测试中故意依赖旧接口的断言。定向验证工具直接调用、工作区/审批、CLI、`grep` 长行提示与混合工具调用；全量 pytest、compileall、pip check 均通过才称完成。检查当前生产代码和测试不再引用 `read_file` 旧名称；文档历史引用允许保留，须有新交接指出已过时。

本片不证明真实供应商在新工具名下主动选择 `read`，也不证明跨重启旧会话迁移。真实模型探针可另做，不能用改名后的单元测试代替。

## 已执行验证

- 红灯：`tests/test_read_tool_name.py` 在旧导出下因缺少 `create_read_tool` 产生预期收集错误；改名后直接检查固定工作区与会话工厂都向模型暴露 `read`。
- 定向 `.venv/bin/python -m pytest -q tests/test_read_tool_name.py tests/test_read_tool.py tests/test_application_workspace.py tests/test_application_read_approval.py tests/test_application_cli.py tests/test_application_ls.py tests/test_application_search.py tests/test_grep_tool.py` → `106 passed in 1.93s`。
- 全量 `.venv/bin/python -m pytest -q` → `297 passed in 4.42s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` → 退出 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。
- `rg` 检查 `naiyue_test_agent/ tests/ scripts/` 不再含旧 `read_file` / 工厂 / schema 名称。旧历史文档保留当时名称；尚无真实模型调用新名称的探针。
- 命名收尾发现旧 `pending_read_approvals/resume_read_approval` 实际是四种只读工具的别名，用户要求消除歧义；现已仅保留通用 `pending_tool_approvals/resume_tool_approval`，并将只读授权对象/上下文字段显式命名为 read-only。别名删除测试先红 `1 failed, 1 passed`，改名后定向 `43 passed in 1.14s`；重新执行全量 `.venv/bin/python -m pytest -q` 为 `298 passed in 4.61s`，compileall 退出 0，pip check 输出 `No broken requirements found.`。生产代码和当前文档无旧别名，测试中仅保留四条 `hasattr` 否定断言。
