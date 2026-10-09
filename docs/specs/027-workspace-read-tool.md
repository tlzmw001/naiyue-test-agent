# Spec 027：显式工作区内的只读文件工具

状态：2026-10-01；已按 TDD 实施。本切片只实现 `read_file`，不接写入、审批、命令执行、MCP 或异步工具。

## 当前数据流与问题

CLI 调用 `build_local_application(config_path, tools=[], ...)`，运行层把列表交给 LangChain `create_agent`。框架能公开工具 schema、校验调用参数、执行工具并把 `ToolMessage` 回填，但当前 CLI 没有文件工具。运行层已有同步工具事件及截断/调用 ID 守卫，不应另写 Agent loop。框架默认参数错误可能包含模型提交的原始参数，不能不经控制直接返回。

## 本切片契约

- 增加 `create_read_file_tool(workspace_root)`，返回 LangChain 工具 `read_file`。模型可见参数只有非空字符串 `path`；Pydantic 严格拒绝多余字段、非字符串及空值。参数错误回填固定安全 `ToolMessage(status='error')`，不执行文件读取或泄露参数。
- 工作区根目录由启动者显式提供，须为现有、非隐藏且不位于 `.env`、`.ssh`、`.git` 内的目录；构造时解析为绝对真实路径。工具仅接受根目录内的普通 UTF-8 文本文件。拒绝 `..` 路径段、指向工作区外的符号链接、目录、隐藏路径段及 `.pem`/`.key` 文件。对尚未存在、不可读、非 UTF-8 或超出读取上限的文件，回填固定错误。根目录内的普通文件可用相对路径，也可用根目录内绝对路径指定。
- 一次读取上限默认 64 KiB；读取最多上限加一字节，超过上限不把部分内容回填模型。文件错误和拒绝行为按类别记录日志，不记录路径、参数、内容或底层异常正文。此为本地工作区边界，不宣称对并发路径替换具有操作系统沙箱保证。
- CLI 增加可选 `--workspace PATH`。未指定时仍传 `tools=[]`，保持原有聊天入口；指定时创建并注册唯一 `read_file` 工具。无效工作区在开始对话前安全失败。CLI 不打印工具原文或底层异常正文，只保留已有模型回答展示。`--config` 等既有调用保持兼容。

## 模块与接口

- `naiyue_test_agent/agent_runtime/tools/read_file.py`：工具工厂、严格参数 schema、路径与文件读取边界。通用文件工具放在运行层，应用入口负责提供可信工作区，不从运行层反向导入应用。
- `naiyue_test_agent/application/cli.py`：可选工作区参数与注册接线；不改 `build_local_application` 公共签名。
- `tests/test_read_file_tool.py`：先以独立工具调用和假模型图验证成功、参数错误、路径拒绝、大小、编码及错误回填。
- `tests/test_application_cli.py`：假模型通过 CLI 实际调用 `read_file`，验证未指定工作区不暴露工具、指定时可读、无效工作区拒绝。
- README、交接和 future-work 同步使用方法、实际验证及遗留项。

## 验收与证据边界

先运行新测试确认预期红灯，再做最小实现，运行定向和全量 pytest、`compileall`、`pip check`。L1 检查工具与 CLI 参数存在；L2 检查边界和安全错误的真实实现；L3 用假模型验证 CLI → 应用组合 → `create_agent` → 工具 → `ToolMessage` → 模型回复。无真实网关调用或业务用例质量评测，不声明跨进程隔离、写权限或沙箱。后续工具切片再设计 `ls/find/grep`、写入审批、同轮并发和长文件分页。

## 实施与实际结果

- 红灯：新增工具测试时，`.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py -k 'read_file or workspace'` 在收集阶段因 `agent_runtime.tools` 尚不存在而退出码 2。单独执行 CLI 新测试为 `2 failed, 14 deselected`，原因是旧 CLI 不识别 `--workspace`。
- 最小实现：`read_file` 工厂绑定现有工作区；Pydantic 拒绝错误参数；工具执行时解析真实路径并限定在根目录内，读取上限加一字节判定大小；LangChain 工具处理选项把参数/文件失败转成固定错误 `ToolMessage`。CLI 只有收到 `--workspace` 才把此工具交给现有应用组合入口。没有新增 Agent loop 或修改已有公开函数签名。
- 定向：首轮 `.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py` → `31 passed in 0.63s`。复核发现隐藏目录作为工作区根、隐藏符号链接别名两个绕过；各自的回归断言先得到 `1 failed`，随后修复。最终验证结果见下方。
- 最终定向：`.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py` → `34 passed in 0.62s`；全量 `.venv/bin/python -m pytest -q` → `199 passed in 4.12s`。
- 构建/依赖检查：`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` → 退出码 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。项目无独立构建目标。
- L1/L2/L3：新工具模块存在且真实读取临时 UTF-8 文件；直接工具调用验证错误状态与固定内容；假模型经 CLI 发出 `read_file` 调用后，第二次模型输入确有文件 `ToolMessage`，最终回答由模型生成。没有真实模型网络调用、业务质量评测或并发路径替换验证。
