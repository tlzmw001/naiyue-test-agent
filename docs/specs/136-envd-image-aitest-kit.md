# Spec 136：执行端镜像内置 AITest Kit 0.6.0

## 当前链路与问题

`build_envd` 从 `release/envd.json` 构建 Linux 执行端 wheel；`build_sandbox_image` 检查 wheel 和 Docker 架构，把执行端的固定依赖写入 `runtime.txt`，`sandbox/Dockerfile` 安装依赖与 wheel。容器以非 root 身份运行 envd。控制端的 `aitest_kit` 工具最终在所选环境中寻找 `aitest` 可执行文件；当前基础镜像未安装它，沙箱内调用会在启动前报“aitest executable is unavailable”。

## 本次合同

- 本次新制作的 Linux x86_64 执行镜像预装 PyPI `aitest-kit` **0.6.0 基础 wheel** 与其必需依赖，提供 `aitest` CLI。0.6.0 wheel 的 SHA-256 固定在 `release/sandbox-image.json`，构建时先下载并校验，再传入 Docker；校验失败时不修改镜像。仅安装基础包，不启用可选 `server` extra，不启动 Console。
- 执行端 wheel 仍只包含原 58 个执行源；AITest Kit 是**镜像内的独立分发包**，不混入 `naiyue-test-agent-envd` wheel 或增加其依赖清单。控制端、协议、工具调用签名和权限不变；容器内安装的包与项目工作区数据分开，镜像不包含用例、环境变量、凭据或用户配置。
- 镜像仍使用现有 Python 基础镜像、Dockerfile 和构建入口。一次 pip 解析同时处理固定的执行端依赖与 Kit wheel 依赖，构建内运行 `pip check`，避免后装 Kit 改写执行端依赖而未发现。构建报告记录 Kit 版本与 wheel 哈希。
- `sandbox/check_runtime.py` 保留执行端源、版本、协议、原生工具、控制端模块和模型 SDK 缺失检查；增加 Kit 版本、CLI、依赖可用性检查，并按两个分发包的合法依赖闭包检查额外安装包。检查不运行真实测试或访问网络。

## 正常与失败链

- 正常：固定清单 → 下载 0.6.0 wheel → 校验 SHA → Docker 构建安装 → `pip check` → 容器启动 envd → Agent `aitest_kit` 工具在沙箱查找 `aitest` → 执行如 `--help`/`doctor`，输出通过现有进程协议返回 Agent。
- 失败：版本或 wheel 哈希不符时在 Docker 构建前退出；依赖冲突时 pip 构建失败；容器内缺 CLI/版本错误时镜像检查失败。实际测试工作区无配置时 `doctor` 可报告业务前置条件，不把它当镜像缺包。

## 影响与验证

`release/sandbox-image.json`、`scripts/build_sandbox_image.py`、`sandbox/Dockerfile`、`sandbox/check_runtime.py`，相关构建测试、镜像使用/发行文档和交接记录。先加失败测试覆盖缺 Kit、坏哈希及真实镜像 CLI，再做最小实现；随后运行定向与完整测试、构建，并在独立 x86_64 Docker context 中实际构建/检查镜像。公司 Windows/Linux 原机仍需单独验收；本次不推送、部署或替换现有镜像。

## 本机验证（2026-10-28）

- PyPI 0.6.0 基础 wheel 为 `aitest_kit-0.6.0-py3-none-any.whl`，SHA-256 `419559056a1d16b695d9b1e91a7f0d15ddc389996a369207bd3dcf8afada2331`，与清单一致；本地 AITest 仓库当前分支的 `pyproject.toml` 仍是 0.5.0，因此没有拿工作目录冒充 0.6.0。
- 新镜像测试先因缺 `load_image_profile` 红灯，接线后 `tests/test_envd_artifacts.py` 39 passed。合并三个修复的定向测试 127 passed。`build_envd --target linux-x86_64` 输出并核对 envd 0.5.3 wheel；`build_sandbox_image` 在 `colima-nyt-r1-14-amd64` 构建 `naiyue-envd:0.5.3-aitest-0.6.0-amd64`，pip 同次解析安装、`pip check` 通过，构建报告见 `build/fix-052-aitest-053/image.json`。
- 完整后端 `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q tests` 为 5713 passed（1 条故意重复 ZIP 成员的预期 warning）；前端 `npm --prefix frontend test -- --run` 为 554 passed，`npm --prefix frontend run build` 退出 0；Windows 控制端候选 wheel 按现有 `build_release` 流程成功构建，尚未作为新版本发布。
- 非 root、只读根、禁网、临时 `/tmp` 的容器内 `check_runtime.py --aitest-version 0.6.0` 七组全通过，实际找到 `/usr/local/bin/aitest`，核对 58 个 envd 源文件、版本、协议、原生工具和全部声明依赖；未加载控制端和模型 SDK。第一次检查忘记给只读容器可写 `/tmp`，文件与 Unix socket 两组失败；修正**检查命令**后 7/7 通过，产品代码未据此放宽。
- 同镜像的临时禁网容器执行 `aitest init --target /tmp/kit-workspace` 后 `aitest doctor --workspace /tmp/kit-workspace` 均退出 0：模板创建 42 项、doctor 为 `ok=2, warn=2, fail=0, info=2`；两个 warn 是空工作区尚无 suite 和 generated pytest。这证明 CLI 及内置模板在镜像内可用，不代表真实接口测试通过。
- 新镜像的真实 Docker 输入 8 项、结果 8 项、进程/恢复 10 项通过；三份报告 `provider_requests=0`。这是本机 Linux x86_64 验收，Windows 闪窗和公司 Docker 19.03/SSH 原机仍未验证。
- `docker image save --platform linux/amd64` 导出 tar，137,631,232 字节，SHA-256 `277e323ac61924012a91877954d0da72fd316c6dfc309e3808f468b1aedf69ef`。归档包含单一 `linux/amd64` manifest；`docker image load` 后再次运行七组检查通过。tar 和 `.sha256` 在 `build/fix-052-aitest-053/`；没有上传、推送或替换旧镜像。测试容器/卷清空，x86_64 Colima 已停止。
