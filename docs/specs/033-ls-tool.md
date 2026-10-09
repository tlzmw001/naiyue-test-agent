# Spec 033：工作区目录 `ls` 工具

状态：2026-10-02，已按用户选择实施 Pi 风格的全部名称可见性，并通过本地确定性验证。

## 当前链路与参考

当前 CLI 选择 `--workspace` 时只注册 `read_file`；应用服务把可信根目录放入 run context。Read 在工具内重验目标，工作区外普通文件由 `HumanInTheLoopMiddleware` 中断，应用保存待审批调用，同一 checkpoint 恢复时按调用 ID 与规范化目标一次性授权。有效工具调用的参数和执行错误作为安全 `ToolMessage(status='error')` 回给模型。

Pi `a69bef789` 的 `packages/coding-agent/src/core/tools/ls.ts`：`path` 可选，默认当前目录；`limit` 可选，默认 500；按名称不区分大小写排序，目录加 `/`；默认包含 dotfiles；按条目数和 50 KiB 截断。它的通用本地工具没有本项目的会话工作区与审批策略。本项目沿用目录呈现、dotfile 名称可见性与有界输出，但叠加工作区和审批边界。`ls` 只能看到名称，Read 的内容读取权限保持原样。

## 输入、输出与边界

- `ls(path?: str='.', limit?: int=500)`；严格 schema，拒绝多余字段、非正整数和非字符串路径，显式 `limit` 上限 2000。相对路径从会话可信工作区解析，绝对路径可用；目标必须是目录。`..` 片段拒绝。
- 工作区内目录直接列；工作区外目录先审批，批准只绑定该工具调用 ID 和当时的规范化目录，并在真正列目录前重验。拒绝后工具不执行。无工作区的会话版工具返回安全错误。
- 像 Pi 一样显示目录中的所有名称，包括 `.env`、`.git`、`.ssh`、`.pem`/`.key` 和符号链接名称；目录项无法 stat 时跳过，不读取文件正文，不递归。工作区内隐藏目录也可列其名称；工作区外目录仍须审批。名称可见不代表 `read_file` 可以读取内容。列名中的换行、回车和控制字符转义，保证一行一个条目。
- 目录项按 `casefold()` 再按原名排序，目录加 `/`，空目录返回 `(empty directory)`。最多返回指定条目数及 50 KiB 正文，触顶后附短截断提示；提示不包含路径。这里的 `limit` 限制模型输出而非目录扫描成本，大目录排序仍需枚举名字，性能优化留待实测。
- schema、路径策略、审批未生效、目录 I/O 和未知只读执行异常均返回固定、可行动、脱敏的错误 `ToolMessage`，保留原调用 ID；控制中断不吞掉。缺失与受限目标共用一条提示，避免泄露敏感路径是否存在。正常工具事件和下一模型 turn 可观察。

## 实施范围与接线

- 新增 `agent_runtime/tools/ls.py`，复用 Read 的可信根目录验证和一次性审批 grant；导出 `create_ls_tool`（绑定单工作区）及 `create_session_ls_tool`（会话注入）。不新增依赖，不改 Read 的公开签名。
- 运行层把会话 `ls` 接入同一个 HITL 中断配置；现有恢复入口的 `approved_reads` 参数保持兼容，其内部一次性 grant 供两种只读工具使用。
- 应用层识别 Read 与 ls 的待审批调用、校验中断动作的名称/参数与模型调用一致、按原顺序恢复；保留已有 `pending_read_approvals`/`resume_read_approval` 兼容入口，新增中性命名供 CLI 使用。待审批展示包含工具名。
- CLI 选择工作区时同时注册 Read 与 ls，审批展示真实工具名；不改无工作区会话。
- 更新交接与后续工作，仅记录本地确定性测试和真实验证边界；不把它称为生产沙箱。

## 验证

先写失败测试：默认/指定路径、排序与目录标记、空目录、条目/字节截断、参数错误、所有隐藏项/敏感后缀/符号链接名称可见、越界和不存在、工作区外审批批准/拒绝/目标变化、Read 与 ls 同批审批、跨会话工作区、工具错误同 ID 回填及下一模型 turn 可见、CLI 接线。随后最小实现、定向 pytest、全量 pytest、`python -m compileall`、`pip check`。真实供应商工具选择、目录变动竞争窗口及极大目录性能不由假模型测试证明。

## 实施记录

- 红灯：新增测试在收集阶段因 `ls` 模块和导出不存在而出现 2 个导入错误，这是新工具尚未存在导致的预期失败。
- 代码入口：`agent_runtime/tools/ls.py` 实现严格 schema、目录分类、有界列名和固定安全错误；`agent_runtime/__init__.py` 将 `ls` 加入条件 HITL；`application/__init__.py` 在同一待审批批次识别 `read_file` 与 `ls`，保留旧公开入口并增加中性命名；CLI 在显式工作区时注册两个工具。
- 本地定向验证：`.venv/bin/python -m pytest -q tests/test_ls_tool.py tests/test_application_ls.py tests/test_application_read_approval.py tests/test_application_cli.py` → `46 passed in 0.79s`。全量 `.venv/bin/python -m pytest -q` → `256 passed in 3.95s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests` 退出 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。
- 范围：假模型、临时目录、进程内 checkpoint 和 CLI 模拟验证；没有真实模型主动选用 `ls` 的请求、跨重启审批或大目录负载数据。目录规范化到扫描之间仍有并发替换窗口；`limit` 限制模型输出，但当前仍要枚举和排序整个目录。下一工具建议 `find`，用于跨目录按名称定位文件，再做内容搜索 `grep`；Bash/Edit 按用户要求靠后。
