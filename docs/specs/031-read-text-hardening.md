# Spec 031：Read 文本识别、错误回填与普通点文件

状态：2026-10-02，已按本 Spec 实施并通过本地确定性测试。用户确认先强化文本 Read；图片与其他编码仍是未来必做的独立能力。

## 当前数据流与问题

模型传 `path/offset/limit` → LangChain schema 校验 → 应用可信工作区和可选审批 → `_read_file` 分页读取 → `bytes.decode('utf-8')` → `ToolMessage`。目前非法 UTF-8、长单行、缺失文件和权限拒绝最终都显示 `File is unavailable for reading.`；`handle_tool_error` 的固定字符串覆盖了 `_read_file` 的异常内容。含 NUL 等可解码的非文本字节会原样返回。所有隐藏路径拒绝，连正常的 `.gitignore`、`.github/workflows/...` 也不可读；未来 `ls` 的可见性必须与 Read 一致。

## 本片契约

1. 保留严格 UTF-8，不用替换字符悄悄改写源码。只检查实际返回的正文页；前面跳过的页不保证整文件编码有效。
2. 对返回正文中的 C0 控制字符（除 tab、LF、CR）及 DEL 拒绝为 `File contains unsupported non-text bytes.`；包括 NUL，不以扩展名猜格式。合法换行、CRLF 和 tab 不变。
3. `UnicodeDecodeError` 回填 `File is not valid UTF-8 text.`；单行超过正文预算回填 `A line exceeds the read_file page limit.`。缺失、访问失败、路径/审批拒绝、其他未知错误仍回填 `File is unavailable for reading.`。参数 schema 错误仍为 `Invalid read_file arguments.`。工具处理器只转发这几种白名单消息，绝不反射任意异常正文或路径；日志继续只记安全异常类型，不记录路径和内容。
4. 工作区**内部**允许任意层级的普通 `.gitignore` 文件，以及作为目录组件的 `.github`；它们仍需是普通文件，且不在 `.env*`、`.ssh`、`.git` 等敏感路径下，`.pem/.key` 后缀仍拒绝。其他隐藏组件继续拒绝。工作区外任何隐藏组件继续拒绝，即使是 `.github` 或 `.gitignore`；工作区外非隐藏普通文件继续 `ask`。通过符号链接指向敏感/隐藏实际目标仍拒绝。`..` 继续拒绝，不扩大路径表达能力。
5. 固定工作区工厂与会话版工具共用读取与分类逻辑；`path/offset/limit` 公开 schema、审批命令和运行时 ID 契约不变。

## 文件与验证

- `agent_runtime/tools/read_file.py`：最小改动路径分类、正文解码检查与安全工具错误回填。
- `tests/test_read_file_tool.py`、必要的会话审批测试：先加失败用例，确认原因；再实现。既有测试中关于 `b'\xff'` 和长单行的固定错误断言按本 Spec 的**显式契约变更**更新，不能放宽状态或内容断言。
- `docs/read-tool-maturity-review.md`、`docs/future-work.md`、`docs/handoff.md`、必要时 README：标记已实现、剩余边界和实测结果。
- 定向与全量 pytest、`compileall`、`pip check`。测试覆盖非法 UTF-8、NUL/控制字节、正常 tab/CRLF、普通点文件、敏感点路径、外部隐藏目标、符号链接与任意异常正文不泄漏；验证会话审批路径没有回归。探针和假模型测试不代表真实供应商验证。

## 明确后续能力

- **图片 Read**：后续基于明确的多模态模型配置，检测受支持图片格式、设输入大小/尺寸预算、必要时转换或缩放，以模型可消费的图片内容返回，并验证无视觉能力模型的表现；沿用会话权限、审批、日志脱敏及取消/失败边界。不能把图片识别等同于 RAG 文档入库。
- **其他文本编码 Read**：用真实样本确定支持的编码（如 GBK/UTF-16）、编码指定或受控检测策略；保留原始字节和转码证据，禁止无提示替换坏字节；验证分页在多字节边界上的正确性及错误回填。这是通用 Agent 的能力，不因未来 RAG 模块而删除。
- 深页索引、跨页快照、上下文 token 预算、活动取消、OS 级路径绑定仍是其他切片，不包含在本次修改。

## 实施结果

共享 `_read_file` 在快速读取和分页正文两处统一严格解码与控制字节检查；安全工具错误处理器仅转发本 Spec 的白名单错误文字。工作区内仅对 `.gitignore` 文件和 `.github` 目录组件放行，实际目标仍重新分类；工作区外隐藏路径维持拒绝。固定工作区工具、会话版工具及 CLI 装配接口未改变。

先新增失败测试，定向实测 `3 failed`，原因分别是原通用错误掩盖编码原因、NUL 被当文本返回、普通点文件被拒绝；补长单行和会话版 `.github` 测试也先得到 `2 failed`。实现后定向 `43 passed`，仅有两条旧测试对非法编码与长单行的**原有通用错误文字**断言仍红；按本 Spec 明确的新输出契约改为逐类精确断言后，全量 `.venv/bin/python -m pytest -q` 实测 `232 passed in 3.90s`。`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`。没有真实供应商调用、图片输入或其他编码样本的运行证据。
