# R1-14A-01：执行端专用安装包与镜像

2026-10-27，用户确认「只放有用的」并批准继续。替换此前把完整 Agent wheel 和全部运行依赖放入沙箱镜像的构建方式；保留旧127MiB验收包作为对照，不部署公司机器、不提交/push/发布。当前工作树为 `codex/ssh-remote-docker`，基于 `v0.5.0`。

## 0.5.1 发行范围（2026-10-27，用户已授权）

用户随后明确批准：本次全部代码/测试/文档交付main，打标签发布0.5.1，离线包作为GitHub Release附件。该授权替换上文“未提交/push/发布”的当前操作边界，既有0.5.0开发快照和验证数据保留其历史时点。

已将28个获批文件整合到主目录main，保留同期service stop/logs、项目收起和图片菜单提交；README三方合并，handoff保留两边记录。原开发工作树和1352项本地研究资料保留，不纳入提交。唯一Python版本事实改为0.5.1，根依赖锁定/DDL/锁/真实应用或SSH配置不变，前端私有构建项目版本不作为Python版本源。

发行顺序：整合完整后端/前端与构建 → 独立架构/安全复核 → 同版本控制端/执行端wheel和真实x64镜像 → 导出单平台归档/重导入/check.sh/ZIP SHA → 仓库保存小型归档清单与下载说明 → allowlist提交/push main → 同提交main CI全部通过 → v0.5.1标签触发既有PyPI流程 → 四平台公开安装/文件SHA核对 → GitHub v0.5.1 Release上传离线ZIP及校验文件并下载复核。正式附件名 `naiyue-envd-0.5.1-linux-amd64-offline.zip`，镜像tag `naiyue-envd:0.5.1-amd64`，清单 `release/offline/0.5.1-linux-amd64.json`。不把旧0.5.0镜像换名充当0.5.1；大ZIP保持在build目录并作为附件，不进入Git历史。

公司原机和普通密码14B仍未完成；公开发行不改变该验证边界。main CI/PyPI/附件任何阶段失败都保留实际状态，不移动已发布标签、覆盖PyPI同名文件或关闭安全规则。最终结果以[0.5.1说明](../releases/0.5.1.md)、归档清单与流水线记录为准。

## 目标与当前链路

当前 `scripts.build_sandbox_image → inspect_wheel → 完整应用 METADATA 的全部 Requires-Dist → sandbox/Dockerfile → pip install application.whl → python -I -m naiyue_test_agent.envd serve`。执行端只使用文件/进程/操作日志/Git输入和结果能力；Agent循环、模型供应商、Web/CLI、项目/聊天/记忆管理、MCP管理不在执行端运行。

已用本机导入探针确认：加载 envd.operations 不加载 application/agent_runtime/model_access、OpenAI/Anthropic/Ollama SDK；但工具算法文件仍引用 ToolRuntime/ToolException/工具注册类型，需保留现有 LangChain/LangGraph 依赖。tools/__init__.py 的立即聚合导入会拉入无关工具；使用标准Python模块延迟导出保留现有公开符号，避免为改变导入路径迁移大量调用方。两端复用同一份算法和源文件，不复制算法或按函数裁剪第三方包。

正常新链路：发布源码及明确执行端清单 → 现有setuptools/native-assets流程构建独立envd wheel → 执行端专用检查器 → 仅执行端固定依赖 → 现有Dockerfile/容器启动/RPC → 原Windows控制端。文件/命令/许可/错误/恢复协议及数据库保持；同版本envd hello仍返回原package_version/protocol。

## 构建合同

1. 新发行产物名 `naiyue-test-agent-envd`，只用于Linux amd64/arm64沙箱；同一仓库、同一版本、同一 `naiyue_test_agent` 命名空间和envd入口。完整控制端包仍为 `naiyue-test-agent`，其pyproject、入口、前端及原检查器不放宽。两个包分别安装在控制端/容器的独立环境，不支持在同一site-packages混装。
2. `release/envd.json` 为人工可读的固定源文件和依赖名称清单。初始58个Python源文件，仅envd、执行所需公共合同/文件/进程/Git/日志/工具模块及父包初始化；明确排除application、agent_runtime、model_access、web、cli、mcp_client、记忆/历史/子Agent业务模块及前端资源。共享settings/合同文件包含同文件内的额外声明可以保留，不重构预算或公共类型。
3. 初始45个固定依赖名称：Pillow、pydantic、langchain/langchain-core、jsonschema及其现有依赖闭包。版本只从根pyproject现有精确锁定读取，不重复维护版本，不升级或新增第三方依赖。干净安装后核对实际安装集合/版本，无OpenAI/Anthropic/Ollama及其模型适配、FastAPI/Uvicorn/MCP、sqlite-vec等控制端依赖。Python标准库SQLite仍保留。
4. 构建仍使用项目既有setuptools `setup.py`、原生工具下载/哈希/许可流程；只选择清单文件，不先安装完整Agent再删除。新 `scripts.build_envd --target linux-x86_64|linux-arm64 --output DIR`；输出目录拒绝覆盖同名产物。
5. `_assets/envd.json` 记录schema_version=1、包版本、协议版本、target、各Python源文件SHA256、依赖精确值及聚合execution_sha256；`native.json`和原生许可继续沿现有格式。共享源文件与当前仓库逐字节一致。无Web/配置/凭证/测试/控制端代码或nyt入口。
6. `scripts.envd_artifacts` 独立执行端stage/inspect：固定允许路径、无重复/路径穿越/链接、精确metadata/tag/依赖、清单/源哈希/原生二进制与许可。完整应用仍走原inspect_wheel；执行端不借放宽主应用检查器通过。
7. `scripts.build_sandbox_image` 保留命令参数，但内部只接收envd wheel。完整Agent wheel必须在任何Docker修改前拒绝；迁移原6项架构场景到新产物，保留两个x86别名、两个ARM别名、跨架构拒绝的断言。实际消费者/文档同步新入口，不保留整包镜像兼容分支。

## tools包导出

保留现有 __all__、符号路径、对象身份和工具工厂行为。标准 `__getattr__` 仅在访问原有导出时import相应模块并缓存该对象；直接导入settings/read等执行模块不立即加载builtin/subagent/web工具。不存在的新符号仍为AttributeError。Python import锁负责模块初始化，不新增应用锁/线程池/通用插件注册层；默认控制端使用所有工具时仍按需加载原实现。

## 验证与交付

- TDD：先证明导入执行模块会连带加载无关工具；再改延迟导出。验证原符号对象、star-import、未知名、控制端实际工厂消费者。
- TDD：专用stage/inspect、58源允许清单、45依赖精确锁定、Linux标签、native/许可和任意额外控制端/前端/路径/链接/坏哈希/缺依赖/未固定依赖拒绝。主包原拒绝与前端门禁保持。
- 干净安装：全新容器环境只安装执行端wheel及其声明依赖，源码目录外启动envd/共享操作；通过 `pip check`，核对模块与安装分布均不存在控制端组件，完整Agent保持自己的构建/安装验证。沿已有干净Python基础镜像验证，不额外为Linux wheel在Mac绕过平台安装；不能使用开发虚拟环境已装的SDK掩盖缺依赖。
- `sandbox/check_runtime.py` 成为交付程序的版本化来源：保留线程、异步线程、子进程/异步子进程、文件/SQLite/tar、Unix socket七项分组中的执行语义；runtime组改为验证envd发行元数据、58源哈希、依赖、native及控制端缺失，不要求个人记忆sqlite-vec或完整Agent/前端哈希。原主应用验证不删弱。
- 用新镜像重跑真实Docker文件/会话/进程/输入/结果/交付探针所需场景，模型保持离线替身；原有审批、停止/恢复、不重放、错误分类、路径边界、隔离和本地交付语义不减。默认与Docker19.03官方seccomp验证；单平台Docker legacy归档、重导入、最终交付脚本验证与SHA/ZIP检查。
- 新Windows完整包与执行端共享源、版本和协议核对；若tools包源变更则重新构建控制端包，不能沿用旧源码哈希。
- 交付新ZIP和镜像tag，旧包保留。新镜像大小/节省量仅由实测报告。已授权的本机amd64 VM可启动验证，结束停止并核对容器/卷0；不改真实应用/SSH/Docker设置，不访问或部署公司主机。公司kernel5.4/Docker19.03仍须原机验收。

失败：缺必要模块/依赖、未知路径或哈希不符明确失败，不回落完整包、不联网补装SDK、不关闭安全策略。新构建失败保留日志和原产物，不改用户配置；已存在的数据/会话没有迁移。连续三次相同修复失败停止并报告。

## 影响文件与范围

本片超过8个文件：tools/__init__.py；release/envd.json；scripts/envd_artifacts.py、build_envd.py、build_sandbox_image.py；sandbox/Dockerfile、check_runtime.py；tools导出/执行端打包/既有release架构测试；Spec、镜像构建/SSH使用文档和handoff。必要时将检查器按职责分文件以满足500行上限。无新服务、运行配置字段、DDL、权限开关或模型/工具API签名变化。不在本片拆分全部工具框架或重新设计运行时。

## 状态

2026-10-27已完成本机实施与交付检查，未提交/push/发布或部署公司机器。源码与依赖允许清单、独立wheel构建/检查器、原镜像入口消费者及延迟工具导出均已接线。旧整包镜像构建分支已替换；其6项平台场景迁入执行端产物测试，保留实际断言和Docker调用顺序，没有删除原主包门禁或改conftest。

正常实链：`build_envd → stage_envd/prepare_native → setuptools wheel → inspect_envd_wheel → build_sandbox_image → Dockerfile → python -I -m naiyue_test_agent.envd serve`；控制端既有DockerEnvironment通过envd RPC调用原文件/进程/Git实现。失败例：整包wheel、篡改并重算清单的源码、伪造metadata/filename/manifest版本均在Docker调用前明确ValueError。版本伪造是独立只读复核发现的问题，先见DID NOT RAISE红灯，再补当前源码版本核对并通过全部相关回归。

实际验证（本机PATH按既有native-assets流程包含fd/rg，输出目录 `build/r1-14a-envd/`）：

```bash
.venv/bin/python -m scripts.ci_shards --index 0 --count 2 --output build/r1-14a-envd/backend-0.xml
# 3077 passed / 335.96s
.venv/bin/python -m scripts.ci_shards --index 1 --count 2 --output build/r1-14a-envd/backend-1.xml
# 2607 passed / 273.51s
.venv/bin/python -m pytest -q tests/test_envd_artifacts.py tests/test_tools_exports.py tests/test_release_artifacts.py
# 92 passed / 1.56s，1条来自故意重复ZIP成员夹具的预期warning
```

完整两片5684项后补源码/版本伪造2项，92项全量相关回归覆盖最终检查器；JUnit与最终collect-only逐ID并集5686项，无遗漏/重复跨片/失败/error/skip，见test-coverage.json。导出/构建输入/源码自证/版本自证均保存红灯日志，不把后补测试称作先行TDD。单文件最大267行，无新应用锁、API签名、配置、DDL或根依赖变化。

Linux x64/ARM64 envd wheel、完整Windows/Mac应用wheel均按标准流程构建并通过Twine及各自检查器；Mac源码外全新安装通过。58个共享源与两份完整应用wheel逐字节相同，执行源哈希 `1f76d77867a69f14444e88bdbf56a4f5e4123a0c49b149887e8ce8e7e2405991`，完整应用哈希 `6f7bd3f2714896a0134bdbd4fd8e9f65335c3dce31a52bbe4be2cc9ef3387f6b`；两者聚合范围不同，不能要求聚合值相等。原主包仍含354源/72依赖/4Web资源，执行端为58/45/0。

真实x64 Docker六脚本environment/sessions/processes/inputs/results/delivery分别10/8/10/8/8/6项，共50项通过，包含控制端异常退出、子Agent/审批恢复、未知操作不重放、Git输入/捕获和三种本地交付。模型为离线替身，0供应商请求。默认与Moby19.03官方seccomp各7项通过，干净容器pip check退出0，无缺依赖；非root只读容器不能写pip缓存的提示保留。旧整包镜像被新执行端检查程序明确拒绝，旧产物未删除。两位只读复核收尾无剩余阻塞。

新镜像 `naiyue-envd:0.5.0-r1-14a-runtime-amd64` 已导出、核对旧Docker manifest.json/单平台/gzip层、重导入，并执行交付同一check.sh全部7项通过。六文件ZIP为 `build/r1-14a-envd/naiyue-r1-14a-runtime-company-linux-amd64.zip`，114,655,922字节（109.34MiB），SHA-256 `0144538f3c10a594e5528321f914e5bbe0d2024bb0c0391c04ce77165adf0541`，比保留的旧ZIP减少13.94%；ZIP CRC、每文件SHA及版本化探针一致性通过。完整报告verification.json、bundle.json、archive-validation.json。

收尾前容器/卷0，两个Colima VM均Stopped；原ARM配置、默认context及真实SSH/Docker配置核对保持。收尾汇总最初把pip缓存warning合并输出当作整段精确成功文本，断言失败；实际pip命令退出0，保留日志后按最终结果行明确核对，不改变产品或测试断言。公司TencentOS/kernel5.4/Docker19、Windows原机和ARM64执行镜像未由本轮运行证明。本轮没有前端改动或新增掌握证据。

唯一下一步：用户按[SSH离线检查说明](../remote-docker-ssh.md#32-051-release-离线镜像附件)传输新ZIP，在公司原机核对并导入该镜像、运行7项check.sh；通过后再装同源Windows完整应用快照做真实SSH任务验收。14B普通密码仍独立。
