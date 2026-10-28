# Spec137：跨平台 Git 快照模式与链接边界

状态：2026-10-28 已实现，TDD 与 Git 主链定向回归通过。只修改共享快照算法及相关测试；不发布，不修改真实用户配置。执行端清单哈希、完整测试与镜像重建由整轮修复统一执行。

## 当前数据流与已复现问题

`SnapshotBuilder.prepare(LocalSource)` 读取真实 index 路径，再由 `read_input` 从安全句柄读取工作区字节和物理文件类型/执行位，构造应用对象库；`validate_tree` 复核内容、链接和 filter。envd 输入、结果捕获、本机 `ResultImporter.verify` 和 `GitDelivery._checkout_tree` 共用这些校验。

1. `check_links` 只按精确字符串追踪链接。树含 `a -> .`、`b -> A/../outside.txt` 时，校验认为 `A` 是普通目录；macOS 默认文件系统把 `A` 解析为 `a`，交付后的 `b` 越出项目。真实 verify/publish/merge 探针曾返回 completed 且工作区干净。
2. 当前快照丢弃 index mode，忽略 `core.filemode=false` 和 `core.symlinks=false`。Git 认为干净的工作区可能被快照改动：可执行脚本变为普通文件，或者以普通文件检出的链接变为文件。Windows 与不可靠权限文件系统会受到影响。

## 最小合同

- 链接校验为快照树的所有路径前缀建立 NFD + casefold 等价拼写集合。追踪任意链接时，遇到与树中名字不完全相同但等价的拼写，或同一等价名字存在多个拼写，明确以既有 `git_input_outside` 拒绝；普通精确的内部相对链接继续支持。此为保守的可移植性门禁，不声称完整仿真 APFS、NTFS 或所有 Unicode 文件系统规则。
- 不依赖当前运行平台决定链接能否交付；因此 Linux envd 生成的歧义链接也不能进入可交付结果。输入、结果验证和合并前的已有接线继续复用 `check_links`，不新增绕过验证的入口。
- 通过现有 `GitRepository.run(..., source_config=True)` 只读查询有效 `core.filemode`、`core.symlinks` 布尔值，遵循系统/全局/本地覆盖；保留现有禁 hooks、fsmonitor 和受限协议的命令前缀。未配置按 Git 默认 true；非法配置应明确失败。
- 保留 index mode：工作区是普通文件且 `core.symlinks=false`、index mode 为 `120000` 时，其字节仍表示链接目标，必须接受相同的链接边界校验。其余普通文件在 `core.filemode=false` 时保留 index 的 `100644/100755`；新普通文件按 Git 的 `100644` 处理。真实符号链接仍保持 `120000`；配置为 true 时按工作区的真实类型/执行位反映变更。
- 读取后仍复核源 HEAD/index/文件身份；同时复核所消费的两项 Git 配置，途中变化则拒绝接受快照。不修改用户 index、refs、配置或对象，不改变公开方法签名、依赖和 RPC 协议。

## 正常与失败主链

正常：LocalSource → 有效配置和 index mode → 安全句柄读取当前字节 → 按 Git 配置选择 mode → 原内容/链接检查 → fast-import → tree/commit/bundle。干净的 `core.filemode=false` 脚本和 `core.symlinks=false` 链接保持原 tree；真实内容改动仍进入快照。

失败：恶意结果提交含 `a -> .`、`b -> A/../outside.txt` → ResultImporter.verify → validate_tree → check_links → `git_input_outside`，不创建宿主结果 ref、不写工作区。历史缓存结果如果绕过首次验证，合并前的 `_checkout_tree` 仍会拒绝。以普通文件保存的链接如果改成 `../outside`，也必须作为链接拒绝，不能降格成普通文件绕过校验。

## 影响文件与验证

生产改动限于 `execution/git_snapshot.py`。测试扩展现有 Git 输入和交付边界文件，不修改测试基础设施。先运行新用例并确认预期失败，然后实现；覆盖大小写/Unicode/目录前缀歧义、精确内部链接、真实 Git 配置覆盖、mode/字节不丢失、配置变化与首次验证/合并拒绝。完成后运行 Git 输入、envd 输入、结果、交付相关回归。整轮完整测试和镜像重建由主任务统一执行。

Git 配置语义参考：[Git update-index 官方文档](https://git-scm.com/docs/git-update-index#_configuration)。本文的实际平台证据以本机回归为准，不把配置仿真称作 Windows 原机验收。

## 实施与验证证据

- 先红：新增输入/模式/配置变化回归为 `13 failed, 5 passed`；新结果验证/历史缓存合并门禁为 `2 failed`，失败均为预期的 mode 不一致或缺少 `git_input_outside` / `git_input_source_changed` 拒绝。
- 最小实现后，三个直接修改的测试文件为 `64 passed in 18.42s`。随后加强真实 global/local 相反值覆盖断言，未放宽原断言。
- 最终定向命令：`.venv/bin/python -m pytest -q tests/test_git_input_snapshot.py tests/test_git_input_boundaries.py tests/test_git_input_service.py tests/test_envd_git_inputs.py tests/test_git_results.py tests/test_git_result_boundaries.py tests/test_git_result_service.py tests/test_git_result_rpc.py tests/test_git_delivery.py tests/test_git_delivery_boundaries.py tests/test_git_delivery_filters.py tests/test_git_delivery_local_only.py`，结果 **128 passed in 74.20s**。
- `python -m compileall -q naiyue_test_agent/execution/git_snapshot.py` 与本片 `git diff --check` 均退出 0；无新增运行依赖/生产模块，无公开签名、RPC 或存储字段变化。共享算法由既有输入、结果和交付消费者接入，不保留并行旧实现。
- 当前证据为 macOS 本机真实 Git、可配置模式仿真和确定性回归。Windows 原机、完整后端回归、执行端重建及 Docker 实测由主发布前任务另外记录；本片结果不替代那些证据。
