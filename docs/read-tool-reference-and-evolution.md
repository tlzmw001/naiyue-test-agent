# Read 工具对照与演进建议

状态：2026-10-01，源码调研记录；下一切片已按 [Spec 028](specs/028-read-file-pagination.md)实现，本文的“建议”段保留当时的设计输入，不再代表待确认接口。初版与分页结果分别见 [Spec 027](specs/027-workspace-read-tool.md) 和 Spec 028。

## 已核对的参考实现

- Pi 本地源码 `a69bef789`：`packages/coding-agent/src/core/tools/read.ts` 的 schema 为 `path`、可选 `offset`、可选 `limit`；`createReadToolDefinition → resolveReadPathAsync → ops.access/readFile → truncateHead → content/details`。相对路径以 cwd 解析，绝对路径原样解析；这个基础工具没有工作区包含关系检查。`truncate.ts` 默认把**工具输出**限制在 2000 行或 50 KiB，并返回下一次读取的 `offset` 提示；实现先把整文件读入 Buffer，再对文本截断，故输出限额不等于进程读取限额。图片走独立处理分支。
- AIAutoTest 的 Pi 集成是额外的权限层，并非 Pi 基础 `read` 自带的文件限制：`agent_runtime/pi_worker/src/permissions.ts` 在 approval 模式设 `read: allow`、`external_directory: ask`、部分 `.env` 和私钥路径 `deny`，full_trust 模式允许所有路径。`agent_runtime/pi_worker/test/search-permission.test.ts` 验证 `.env` 的 read 被权限 gate 阻断。此层授权不是操作系统沙箱。
- AgentX 本地源码 `3fcfa0e`：`BuiltInToolRegistry.createToolsForAgent → AbstractMessageHandler.buildStreamingAgent` 装配内置工具；目前检查到的内置实现是 `rag_search`，其执行中按用户验证知识库。没有在这条源码链中找到可直接类比 Pi 的任意本地文件 `read`，因此不能用 AgentX 推断文件读取边界或分页细节。

## 当前实现中的必要条件和可简化处

| 检查 | 判断 |
| --- | --- |
| `ReadFileInput` 严格 schema、固定错误回填 | 保留。避免模型把未知参数误当成功输入，并避免原始参数/异常正文进入工具结果。 |
| 可信工作区 `root` 与解析后 `target.relative_to(root)` | 当前单工作区权限边界的核心；不能只靠提示词、原始字符串前缀或后缀黑名单。 |
| 原始路径段与解析后路径段各做一次隐藏检查 | 在当前“所有隐藏路径禁止”的政策下不重复：分别阻断隐藏别名指向公开文件、公开别名指向隐藏文件。相关回归测试均已存在。 |
| 单独拒绝 `..` | 与最后的真实目标归属检查在防越界上重叠；还会拒绝最终仍在工作区内的 `a/../b`。是保守语法政策，不是实现包含关系所必需。后续可按明确路径契约简化。 |
| `resolve(strict=True)` 与 `is_file` | `resolve` 对符号链接真实目标和包含关系必要，`strict=True` 的存在性检查与最终 `open` 部分重叠；`is_file` 明确拒绝目录及特殊文件，不只是为了捕获 open 错误。 |
| 隐藏路径与 `.pem/.key` 拒绝 | 当前是粗粒度政策，不是完整的秘密识别：会阻止有用的 `.github`/`.gitignore`，也不能识别所有非隐藏凭证文件。 |
| 初版 64 KiB 上限 | Spec 027 是**整个文件**超过上限就失败；Pi 的 50 KiB/2000 行是**单次输出**截断并允许续读。现行 Spec 028 已改为每页文件正文最多 64 KiB/2000 行、提示另计；读取不整文件载入内存，但深页需重复扫描。 |

当前 `resolve` 到 `open` 之间仍有路径被并发替换的窗口。单次大小限制也不是整个 run 的累计读取或模型上下文预算。以上均不能宣称为沙箱或完整权限系统。

## 当时建议的最小下一切片（已按 Spec 028 实施）

先保持显式工作区及现有读权限范围，给 `read_file` 增加可选 `offset`、`limit`（以行计）和受限的续读结果。模型不传新参数时保留现有小文件行为；大文件返回完整行组成的一页、已显示行号及下一次 `offset`，不因文件总大小超过 64 KiB 而整次失败。单次返回仍须受字节预算约束，读取过程也须限制内存，避免照搬 Pi 的整文件读入。输入须拒绝零、负数、非整数及过大的 `limit`；覆盖 UTF-8 多字节、空文件、长单行、末页和文件在分页间变化的情况。先写接口/边界 Spec 和失败测试，再最小实现与 CLI 假模型续读验证。

后续独立切片再决定：是否以可信配置声明额外只读根目录或审批外部读取；如何替换“一律拒绝隐藏文件”的粗策略；`ls/find/grep` 与大文件检索如何配合；图片、PDF、网页进入哪个专用模块；累计输出/成本预算、取消、审计及进程级隔离如何验收。RAG 文档解析不应直接塞进原始文件 `read_file`。
