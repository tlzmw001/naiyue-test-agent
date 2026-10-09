# Spec 028：Read 工具按行续读与权限实施顺序

状态：2026-10-01；分页切片已按 TDD 实施。工作区策略与审批为后续独立切片，不将本 Spec 当成已实现的权限系统。

## 当前链路与问题

CLI 的 `--workspace` 在启动时调用 `create_read_file_tool(root)`，并将工具交给 `build_local_application → create_agent`。模型只能提交 `path`，工具在绑定的真实工作区内解析路径，读取最多 64 KiB；文件一旦更大，整次调用失败。当前 CLI 只创建一个会话；以后共享 Agent 的多会话不能靠一个固定 root 隔离工作区。

## 本切片：只改 Read 的读取能力

- `read_file` 增加可选、严格整数的 `offset`（从 1 开始的行号）和 `limit`（最多返回的完整行数，1–2000）。保留 `path` 及现有工具工厂签名，模型不传新参数且文件不超过 64 KiB、2000 行时，结果仍为原文；先触及任一预算即分页。
- 超过任一默认预算或显式给出分页参数时，返回完整行和机器可读的续读提示：`[read_file: lines X-Y; next_offset=Z]`，末页为 `next_offset=none`。空页也明确报告无内容及 `next_offset=none`。**文件正文**每页最多 64 KiB；续读提示及用于分隔的换行不计入此正文预算，因此整个工具消息可略大于 64 KiB。实现读取时不把整个文件载入内存。无完整行能放进一页时返回现有固定工具错误，不返回不完整 UTF-8 或伪造续读位置。
- 保留当前路径、隐藏文件、敏感后缀、工作区和固定错误边界。文件在两次读取间变化时，每次读取实时文件；行号不是快照游标，可能重复或跳过内容，这一点在工具提示和文档中说明。此切片不新增审批、额外根目录、图片或文件快照。
- 测试先红后绿：小文件兼容、大文件多页、`offset/limit` 严格校验、UTF-8/CRLF、空文件/末页、长单行、分页间文件变化与路径拒绝；定向及全量 pytest、compileall、pip check。现有 CLI 假模型接线保持，新增一次模型提交分页参数并收到续读提示的验证。

## 权限与工作区的后续顺序（已确定方向，接口另写 Spec）

1. **紧接分页后的工作区上下文切片，且在接入任何写入/命令工具之前**：应用层维护会话→可信工作区；调用运行层时按 run 传递，不让模型的工具参数选择工作区。先保持“只读当前工作区”，用同一个 Agent、两个不同会话验证互不越权，替换启动时固定 root 的共享风险。
2. **随后做工具权限决策切片，仍在写入/命令工具之前**：应用层提供模式与规则，运行层在工具边界消费。规则至少区分 `allow/deny/ask`，默认工作区内普通读取允许、敏感路径拒绝、工作区外读取需审批；`deny` 优先于 `ask`。决策和实际执行均验证规范化目标；审批只绑定具体会话、调用与目标，不能扩大成永久全局授权。此步先用假审批与假模型验证拒绝和隔离，不把未接线回调算作权限。
3. **审批生命周期切片**：使用当前 LangChain/LangGraph 可验证的 interrupt/checkpoint/resume 机制，应用层展示请求并接收批准或拒绝，运行层在执行前暂停且恢复后重验；记录脱敏的请求与决定。当前 InMemorySaver 仅可做同进程原型；若要求重启后待审批恢复，须先落实持久 checkpointer。之后才开放外部只读路径的可审批执行，并验证拒绝/批准/重启/并发。审批不同于操作系统沙箱。
4. **引入写入/命令工具时**：在已有权限边界上按工具风险增加规则。未来可提供 `approval`、受限 `auto`、`full_trust` 等模式，但每种模式的动作清单、越界行为、审计和不可绕过的执行检查须单独验收；`full_trust` 仍不等于系统隔离。

策略/审批可由应用注入运行层，不允许运行层静态导入应用。单纯可选回调无法表达持久暂停和恢复；不在本分页切片添加不执行的回调占位。具体接口在工作区与审批各自的 Spec 中由真实测试固定，避免一次性预设计。

## 实施与验证

- 红灯命令：`.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py -k 'pages_large_text or rejects_invalid_pagination or pages_utf8 or empty_and_overlong or page_budget_and_file_change or model_can_request_a_read_file_page'` → `5 failed, 9 passed`；失败来自旧 schema 拒绝分页参数和旧读取器拒绝大文件。
- 初次实施中，`read_file` 保留根目录/path 检查；无分页参数且文件不超过 64 KiB、2000 行时直接返回原文。其他情况逐行读取，最多 2000 行；当时单页正文预留 512 字节用于续读标记，并对整条消息再次检查 64 KiB。跳过前面的行时也分块消费，不整文件加载。单行超过单页可用预算时返回固定工具错误。下述修订取代预留及整条消息检查两点。
- 2026-10-01 修订：用户确认 64 KiB 只计算文件正文，`next_offset` 提示继续返回。取消 512 字节预留和整条消息 64 KiB 的二次拒绝；正文最多 64 KiB、最多 2000 行，工具消息的提示另计。新增边界测试须证明恰好 64 KiB 的无换行单行可以完整返回，提示不会导致整次失败；超出正文上限的单行仍返回固定错误。
- 原有“超 64 KiB 整文件拒绝”测试已被新契约取代，更新为继续验证缺失与二进制文件拒绝；新增测试验证大文件多页和 CLI 假模型接线，并未放宽原有路径拒绝断言。
- 定向 `.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py` → `48 passed in 0.68s`。全量 `.venv/bin/python -m pytest -q` → `213 passed in 3.97s`。`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。
- 证据限于临时文件、假模型 CLI 与本机测试；没有真实供应商文件调用、并发文件替换、外部目录审批或业务质量验证。跨页时文件变化会改变行号含义，单条超过预算的长行尚不可续读。

## 长单行边界的后续确认

用户确认沿用 Pi 的完整行分页取舍：最多 2000 行或本项目的 64 KiB 单次返回预算；一整行超出预算时，当前 Read 返回固定工具错误，不做行内字节/字符游标。Pi `a69bef789` 在此情形也不通过 `read` 返回半行，而是提示使用其独立的 Bash 工具取字节片段。本项目目前没有经过权限与执行约束验收的 Bash 工具，因此不向模型提供不可用的 Bash 命令建议；待受控 Bash 工具接入后，再单独验证明确、可执行且不泄露敏感路径的提示。长行当前不可由 Read 自身续读，是已接受限制，不把行内游标列为当前计划。

确认规则时发现初次实现对低于 64 KiB 的 2001 行短文件提前返回原文，绕过 2000 行预算。新增回归测试先红 `1 failed`，随后只修正早返回条件：同时满足字节和行预算才直接返回。定向 `.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py` → `49 passed in 0.64s`；全量 `.venv/bin/python -m pytest -q` → `214 passed in 3.90s`；`compileall` 退出码 0，`pip check` 为 `No broken requirements found.`。

64 KiB 限制现指**每页从文件取得的正文文本**，续读提示和分隔换行另计；它也不是本次调用的累计磁盘读取限额。未传分页参数时，代码先做有界探测，必要时重置文件位置按行读取；指定 `offset` 续读时不会重复探测，但会从文件开头扫描到目标行。与 Pi 当前先将整文件读入 Buffer 的做法相比，本项目峰值内容内存不随文件大小线性增长，但不能由此推断所有文件上的总 I/O 或耗时更低。

## 正文预算修订与性能核对

- 用户确认：64 KiB、2000 行只计文件正文，`next_offset` 仍在工具结果末尾返回。边界测试先红 `1 failed`：旧代码把恰好 64 KiB 的一行判为过长；去除 512 字节提示预留与整条消息的 64 KiB 二次检查后，该行完整返回，提示另计。超出 64 KiB 的单行仍失败，其他路径与权限规则未改。
- 定向 `.venv/bin/python -m pytest -q tests/test_read_file_tool.py tests/test_application_cli.py` → `50 passed in 0.95s`；全量 `.venv/bin/python -m pytest -q` → `215 passed in 3.97s`；`compileall` 退出码 0；`pip check` 为 `No broken requirements found.`。假模型 CLI 接线测试保留。
- 一次临时合成对照：12,000,000 字节、120,000 行、每行 100 字节，取 500 行；预热后各重复 5 次，本项目有界读取在 `offset=1` 的耗时中位数约 0.4 ms、`offset=60001` 约 6.2 ms；整文件读取并切行的简化对照分别约 7.2 ms、7.1 ms。单次 `tracemalloc` 观测峰值约 0.2 MiB 与 28.5 MiB，但追踪显著影响耗时，因此不把两组计时直接比较。该对照不是 Pi 原程序基准，也不代表真实仓库、冷缓存或多页会话；只支持“早页节省内存/读取量、深页重复扫描”的成本分析。实际瓶颈出现后再用代表性文件与负载决定是否优化。
