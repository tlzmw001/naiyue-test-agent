# Spec 045：工作区内 Bash 完整输出与手动清理

状态：2026-10-02，用户确认后按 TDD 实施；本地验证通过，真实模型和跨进程行为未验证。

## 当前数据流与问题

审批后的 `bash` 启动宿主机子进程；两个读取线程分别消费 stdout/stderr，仅在内存保留各自末尾 4096 原始字节。正常或非零退出把尾部写入同调用 ID 的 `ToolMessage`；超时等未知副作用向应用层抛 `BashOutcomeUnknown`，CLI 展示有界诊断并阻断会话。此前输出无法回看全文。工作区内普通 UTF-8 文件可由现有 `read` 无审批分页读取，普通隐藏目录会被拒绝。

## 本片契约

1. 只有启用宿主机 Bash、已有会话工作区并经过逐命令审批的调用才可能创建输出产物。输出超出任一流的 4096 字节尾部预算时，在当前工作区下的非隐藏专用目录 `naiyue-agent-output/` 保存 stdout/stderr 的原始字节到不同文件。使用随机文件名，目录权限 0700、文件权限 0600；相对路径出现在正常和非零退出的模型工具结果中，由现有 `read` 处理回读。流之间不承诺全局交错顺序。未截断的调用不留下输出产物。
2. 专用目录只由本功能管理：首次创建时写入忽略目录内容的 `.gitignore` 和所有权标记；若已有同名目录却缺少或不匹配标记/忽略规则，不覆盖其中内容，不在该目录存储输出，向模型/用户明确说明归档不可用。工作区为非 Git 目录时仍可使用；不改工作区根 `.gitignore` 或现有配置。当前同一 OS 用户可用 Bash/文件工具接触这些文件；这不是跨用户隔离或不可篡改审计记录。
3. 单命令归档默认最多 16 MiB，当前进程内单工作区已保存输出默认最多 128 MiB；上限可由 `NAIYUE_BASH_OUTPUT_COMMAND_BYTES`、`NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES` 环境变量设置为正整数。达到上限、目录冲突、写入失败或输出管道读取不完整后继续排空可读的子进程输出，并保留有界尾部；结果明确标记 `archive_complete=false` 及原因，绝不把部分文件叫作完整输出。写入错误只记录错误类型，不把输出内容记入日志。正常及非零退出仍保留原有命令退出语义；超时/读管道故障继续人工核对，并让 CLI 展示已有产物路径与完整性标记。跨进程同时写同一工作区的总量竞争未在本片验证。
4. CLI 提供 `:outputs clear` 手动清理当前工作区的已归档输出；没有工作区或运行/审批恢复期间拒绝。命令先列出待删文件数和字节数，要求用户再输入 `:outputs clear confirm` 才执行。只认标记与固定文件名格式，只删除普通文件，不跟随符号链接，不递归删除陌生文件/目录；若目录异常则停止并说明。清理不影响用户其他文件，不自动设置 TTL 或后台定时清理。当前仅保证单进程 CLI 的活动运行检查，不宣称跨进程清理锁。
5. 保留现有 Bash 工具名、`command/timeout` schema、审批边界及 `read` 的 64 KiB/2000 行 UTF-8 分页契约。普通多行文本日志可直接用 `read` 回看；二进制、非 UTF-8 或单行超出 Read 页面预算仍不可通过现有 `read` 完整消费，后续独立切片处理。模型路径是便利引用，不是会话授权凭据；未来 SaaS 需重新设计租户归属与存储隔离。

## 影响文件与验证

- 新的 `agent_runtime/tools/bash_output.py`：专用目录、忽略/标记、容量、随机文件、归档状态与安全清理。与 `bash.py` 的进程控制职责分开，避免单文件超过 500 行。
- `agent_runtime/tools/bash.py`：读取线程接归档器；正常、非零和未知终局回填路径/完整性；schema 更新。
- `application/cli.py`：手动清理命令和超时人工诊断路径。
- `tests/test_host_bash.py` 及必要的新测试文件：先验证失败，再验证多行输出回读、stdout/stderr 分开、未截断不落盘、目录冲突、Git 忽略、容量中断不阻塞命令、超时保留、清理确认/活动拒绝/陌生文件不删除。
- `README.md`、`docs/future-work.md`、`docs/handoff.md`：同步实现状态、验证结果和限制；不修改《关键知识点》。

验证命令：`.venv/bin/python -m pytest -q tests/test_host_bash.py tests/test_bash_output.py`、`.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`。使用临时工作区和已知安全的本地命令，不调用真实模型、不改 `.env`。L1 检查模块/文档存在，L2 检查完整性与容量确由真实子进程产生，L3 检查模型结果路径可交给实际 `read`、CLI 命令按确认清理。

## 实际验证与限制

- 先加入长输出回读、容量、目录冲突、超时和清理测试；定向红灯为 `9 failed, 22 passed in 7.10s`，失败点是 Bash 结果没有路径/完整性信息及清理入口缺失。实现后再补单工作区跨命令上限、运行中拒绝清理、超时 CLI 路径、非零退出回读等样本。最终全量 `.venv/bin/python -m pytest -q` 为 `414 passed in 14.56s`；`compileall` 退出 0；`pip check` 输出 `No broken requirements found.`。
- L1/L2：新增 `bash_output.py` 是实际归档与清理实现；临时工作区内的真实本地命令产生独立 stdout/stderr 文件，验证 0700/0600 权限、Git 忽略、16 MiB/128 MiB 可配置上限的测试替代值、目录冲突和不完整归档的标志。读取线程继续排空超额输出，既有尾部反馈保留。
- L3：假模型批准 Bash 后从 `ToolMessage` 取得相对路径，实际 `read` 工具读取长文本；CLI 在超时后展示路径且会话仍阻断，`:outputs clear` 预览后确认才删除归档文件。测试仅覆盖同进程本地 CLI 和临时命令；未验证真实模型会自行使用路径、真实 pytest 大日志、长单行/二进制/非 UTF-8 回读、跨进程清理与容量竞争或 SaaS 用户隔离。
