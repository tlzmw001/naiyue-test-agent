# Spec 133：Agent 与 envd 独立版本及旧镜像复用

## 当前链路与问题

- 控制端 `EnvironmentService._create` 先检查配置镜像的平台和固定镜像 ID，再创建容器并调用 `DockerEnvironment.handshake`。
- `handshake` 发出协议 2 的 `hello`。envd 返回协议号、自身包版本、环境和会话身份；控制端目前要求 envd 包版本与 Agent `__version__` 完全相等。因此 Agent 单独升级，即使执行协议未变，也会以 `env_protocol_mismatch` 拒绝旧镜像。
- Windows 的 `git_input_source_changed` 误报发生在控制端 `EnvironmentInputs.prepare → SnapshotBuilder.prepare → read_input`，早于向 envd 传送输入。envd 的同一模块也用于 Linux 容器内的结果快照，但本次 Windows `ctime` 语义差异不要求改变已发布 Linux 执行端。
- `scripts.build_envd → stage_envd → inspect_envd_wheel` 从 Agent `__version__` 生成并验证 envd 发行版本；envd wheel 又包含同一个根包 `__init__.py`。仅移除握手比较仍不能让两个包独立发布。

## 本次目标与边界

Agent 0.5.2 能复用已发布的 `naiyue-envd:0.5.1-amd64`。包版本保留在 `hello` 中供诊断，不再作为与 Agent 相等的运行门禁。协议号、环境、会话身份和响应形状继续严格核对；不改变 RPC、存储、权限、操作或已有镜像。协议 2 的现有操作合同须保持兼容；将来新增必需能力时，先定义能力协商或提升协议版本，不把任意未来 Agent 版本宣称为无条件兼容。

构建版本改从执行端发行清单 `release/envd.json` 读取。该清单在当前源码中设 envd 版本 0.5.2，因为本次共用的 `git_snapshot.py` 源码已经变化；这只表示**若从当前源码构建新 envd 包**的版本，不要求用户更换已发布的 0.5.1 镜像，也不在 Agent 0.5.2 Release 发布新镜像。清单固定执行源码与锁定依赖的哈希；源码或依赖改变而未同步提升执行端版本及哈希时，构建明确拒绝。执行 wheel 内的根包版本由 envd 版本生成，避免把 Agent 版本写进执行端。构建检查仍核对发行元数据、源码哈希、协议和依赖。

后续 Agent 仅有控制端源码变化时，`release/envd.json` 版本保持不变；执行端源码或锁定依赖变化时提升其构建版本，是否实际发布新镜像另由运行需求决定。既有镜像可长期使用，但当 Agent 需要旧镜像没有的必需能力时，应先做能力协商或升级协议及镜像，不承诺无条件永久兼容。

## 正常与失败链

- 正常：Agent 0.5.2 配置既有 0.5.1 镜像 → Docker 检查 Linux 平台并冻结镜像 ID → 容器启动 → `hello` 返回协议 2、envd 包版本 0.5.1、原环境和会话 → 控制端接受 → 在 Windows 生成输入快照并向容器传送，执行工具、取回结果。
- 失败：`hello` 的协议、环境、会话或响应字段错误 → `env_protocol_mismatch`，创建记录保留失败状态；不降级到本机执行，也不把包版本不同本身归类为失败。

## 影响文件与验证

- `naiyue_test_agent/execution/docker_environment.py`：仅调整 `hello` 结果验证；不修改 `request` 的请求/响应身份门禁或公开 API。
- `release/envd.json`、`scripts/envd_artifacts.py`、`scripts/build_envd.py`：新增 envd 独立版本源；执行包的根 `__init__.py` 使用该版本，其他现有源/锁定依赖仍来自当前仓库。旧 0.5.1 产物不改写。
- `tests/`：先增加旧版 envd 返回 0.5.1 时的失败测试，以及控制端版本变更不影响 envd 构建版本的测试，再做最小实现；保留错误协议/身份、篡改元数据/源码拒绝测试。用真实 Docker 的 0.5.1 镜像运行当前 `scripts.verify_sandbox_inputs`，覆盖创建、输入安装、工具和结果路径。
- `README.md`、`docs/releases/0.5.2.md`、`docs/remote-docker-ssh.md`、`docs/handoff.md`：0.5.2 安装说明指向 0.5.1 镜像和旧附件，明确协议兼容边界与公司原机未验收。
- 提交前运行定向与完整后端测试、前端测试和构建、控制端 wheel 构建；main CI 与标签发布的 PyPI 四平台公开安装是发行门禁。0.5.2 Release 不列新镜像附件。

## 本机验收（2026-10-27）

- 红灯：旧版握手拒绝 0.5.1 envd 返回值；旧打包流程无独立 envd `version` 字段。新增测试分别得到预期失败后，实现转绿。后来增加执行包根模块不能静默丢新逻辑的保护，未把这项补充称为先行红灯。
- `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests/test_envd_artifacts.py tests/test_sandbox_review_cases.py tests/test_git_input_snapshot.py tests/test_git_input_boundaries.py`：92 passed。完整后端同 PATH 下 `-m pytest -q tests`：5702 passed、1 个构造重复 ZIP 项的预期测试警告，646.79 秒。
- 前端 `npm --prefix frontend test -- --run`：68 文件、554 项通过；`npm --prefix frontend run build`、Agent Mac ARM64 wheel、独立 envd Linux x64 wheel、Twine、compileall、pip check、diff check 均退出 0。独立构建检查了当前 profile 的执行源码和依赖哈希。
- 真实 x86_64 Docker 使用**已发布且未改写**的 `naiyue-envd:0.5.1-amd64`：`scripts.verify_sandbox_inputs` 的输入/工具/重开路径和 `scripts.verify_sandbox_results` 的结果捕获/审查/重开路径全部通过，两个报告均为 `provider_requests=0`。测试容器和卷清理后 VM 停止。报告见 `build/release-0.5.2/old-envd-inputs.json` 和 `old-envd-results.json`。
- 本机验证不能代表公司 Windows 的路径/句柄时间戳语义、公司 Linux Docker 19.03/内核及两跳 SSH 原机环境；原机验收保留。main CI、PyPI 和正式 Release 须在发布后单独核对。
