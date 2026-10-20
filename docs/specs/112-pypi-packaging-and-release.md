# Spec112：PyPI 本地 Web、nyt 与五平台发行

版本边界（2026-10-20）：本文保留原五平台发行的设计和历史验收。用户已确认后续取消Intel macOS，当前四平台范围及门禁见[Spec117-08](117-08-mcp-delivery-verification.md#用户确认四平台发行范围2026-10-20)和[发行指南](../releasing.md)，以release/native-tools.json为当前目标事实；此决定不改历史发布文件，也不复用本文历史发版授权发布当前工作树。

## 1. 状态与目标

2026-10-18 用户授权：先写 Spec，再实现打包、nyt、macOS/Linux/Windows 五平台 CI/CD；项目自身沿用 MIT，重写安装用户 README，发布包仅包含必要运行内容。

本轮实施发布准备，不创建正式标签、不上传 PyPI、不修改真实模型配置或会话库。外部账号邮箱/2FA 及 PyPI Pending Publisher 由用户配置；正式发布前单独核对提交与产物。

2026-10-18 后续授权：用户已提交并推送业务修改、配置 PyPI；重新全量验证和五平台安装验证，通过后创建 `v0.1.0` 并正式发布。上述“仅发布准备”是初始阶段边界；当前按该后续授权执行，实际发布结果以文末验收和 Actions/PyPI 记录为准。

目标体验：`pip install naiyue-test-agent` 安装后，`nyt` 启动本地 Web，`nyt chat` 进入已有终端交互，`nyt --help/--version` 无需模型配置即可运行。现有 `python -m` 入口及业务合同保持。

## 2. 当前链路与缺口

- Web：`web.__main__.main` → settings/workspace → create_web_app → 持久应用 → Uvicorn；前端默认指向源码树 frontend/dist。
- CLI：`application.cli.main` → 同一异步应用的终端桥；未声明安装后的命令。
- Find/Grep：工具 → shutil.which(fd/fdfind/rg) → HostProcessRunner；依赖机器 PATH。
- 只有 requirements 环境快照和前端 lockfile，没有 Python 打包元数据、许可证或 Actions。
- 配置/工作区/SQLite 独立于安装目录；打包不迁移用户数据。

## 3. 交付合同

|目标|原生资源|wheel 平台|
|---|---|---|
|macos-arm64|Apple ARM64 rg/fd|按实际 Mach-O 最低版本确定 macosx 标签|
|macos-x86_64|Apple Intel rg/fd|按实际 Mach-O 最低版本确定 macosx 标签|
|linux-x86_64|上游静态 musl rg/fd|manylinux_2_17_x86_64|
|linux-arm64|上游静态 musl rg/fd|manylinux_2_17_aarch64|
|windows-x86_64|MSVC x64 rg.exe/fd.exe|win_amd64|

Python 以现有 3.12 为验证基线；Python 支持声明以安装/测试结果为准，不根据 LangChain 下限推断项目支持范围。首版发行 wheel，不上传尚不能独立重建前端/原生资源的 sdist。Linux 静态 ELF 和 macOS 最低部署版本在构建时检查；平台运行验证由对应 runner 完成。

### 3.1 内容白名单

wheel 只允许：

1. `naiyue_test_agent/**/*.py`，不包含 pyc、缓存及开发源码外资源。
2. 包内 `_assets/web/index.html` 和其构建 assets JS/CSS。
3. `_assets/bin/rg[.exe]`、`fd[.exe]`，仅当前目标平台。
4. `_assets/native.json` 的工具版本、目标及文件哈希。
5. 第三方许可证/版权声明，以及标准 dist-info（MIT、METADATA/README、入口、RECORD 等）。

tests、scripts、docs、AGENTS、原始 frontend、node_modules、.env、数据库、日志、报告、venv、Git/编辑器文件均不进入 wheel。README 作为标准包描述写入 METADATA；不打包项目开发文档。构建采用全新临时暂存树，包内容验证按白名单拒绝意外文件，避免旧 build/lib 泄漏。

运行依赖由 pyproject 的明确依赖声明交给 pip 安装；不复制虚拟环境。保持已有运行依赖版本，不顺便升级。开发验证工具放到 dev extra/构建依赖，不成为运行依赖。

### 3.2 原生资源与前端

配置文件记录 rg/fd 固定上游版本、每个平台资产 URL、归档 SHA-256、工具成员名和许可证成员。下载仅发生于构建，哈希不一致/缺文件/不支持平台直接失败；只读取预期普通文件，不解压任意路径，不自动重试或从 PATH 替换发行资产。

前端复用 npm ci/build，不新增 UI 功能。只复制构建入口及所需 JS/CSS，收集 production 前端包许可证。运行时不需要 Node/npm/Rust，资源使用安装包内路径。

运行时发行版优先且固定使用自身工具；manifest 存在但文件缺失或不可执行应给出明确安装错误，不能悄悄调用机器上的另一个版本。源码开发未带资源时保留当前 PATH 工具与 frontend/dist 开发路径，此分支有真实源码消费者。

### 3.3 nyt 与初次配置

统一入口只负责路由/帮助/版本，延迟导入 Web 或 chat 入口，保留现有参数；无参数默认 Web，首参数 chat 进入终端。非法子命令由 argparse 明确拒绝。缺模型配置时提供默认配置位置及配置指引，避免只显示异常类型；不写示例凭证到用户目录。

正常：console script → 路由 → 原入口 → 包内静态资源/工具 → 现有应用；配置与数据继续归用户目录/工作区。

失败：构建缺资源在上传前阻断；安装资源不全明确失败；缺模型配置明确说明，其他原错误仍沿原安全日志和返回码传播，不吞错或泄露配置内容。

## 4. 构建与验证脚本

- pyproject.toml、小型 setuptools wheel 定制：声明版本/依赖/nyt；由暂存 manifest 生成 py3-none-平台 wheel，不产出 any wheel。
- release/native-tools.json：五目标固定来源与 SHA-256；必要许可证文件。
- scripts/build_release.py 及必要同职责辅助模块：下载校验 → 独立白名单暂存 → 构建平台 wheel → 成品检查。复用 frontend 已构建目录；不修改源码资源目录。
- scripts/verify_wheel_install.py：检查归档后创建独立环境，在源码外安装并清理 PYTHONPATH/工具 PATH；验证 help/version、原生工具和生产 Find/Grep、前端/API、临时持久应用的运行/审批/重开，输出有限结果，不使用真实供应商。
- scripts/release_artifacts.py --tag 及构建器参数：标签去掉 v 后必须与 Python 单一版本事实一致。

构建暂存树和 wheel 检查逻辑由确定性测试覆盖：额外文件拒绝、缺资源、错误校验和、归档非普通文件、平台/版本错误。安装探针代码保留仓库中，通过绝对脚本路径在临时 cwd 执行，不打入运行 wheel。

## 5. CI/CD

### 日常 CI

push/PR 执行后端、前端测试与构建；五目标 runner 构建 wheel 并做真实本平台安装 smoke。使用现有测试行为，平台发现需修复的问题先定位再最小修复；不 skip 或放宽已有断言。

### 标签发布

publish.yml 监听 v* 标签，校验版本；复用验证工作流/任务，等待同一提交的全部测试和五份安装验证通过，再集中收集五份 wheel。单独 Ubuntu 发布 job 只下载验证产物并使用 PyPA Action 上传，最小权限 contents:read、id-token:write，environment=pypi。

集中验收还比较五份 wheel 的 Python/前端内容指纹，拒绝相同版本号下混入不同源码快照。Python 文件仅归一化跨平台换行后计算指纹，不改变发行文件；源码提交和产物哈希分别保留证据。当前并行修改期间的本地 wheel 仅作构建探针，最终以冻结提交的 CI 产物为准。

前端测试/构建/许可证整理在一个共享 job 执行一次，产物供五个平台构建读取。浏览器资源与目标 OS 无关，这样五份 wheel 使用同一份 UI，也避免各 OS 的构建路径、换行或 CSS 哈希差异。构建器增加 --frontend-assets 接收该受检产物；本地未提供该参数时沿用源码前端构建目录。

Pending Publisher：项目 naiyue-test-agent、owner tlzmw001、repo naiyue-test-agent、workflow publish.yml、environment pypi。OIDC 发布步骤直接位于该工作流，不把被信任身份误配成可复用 workflow。

发布后查询 PyPI 版本/文件列表和 SHA-256，下载实际发行文件核验，再运行五平台公开安装 smoke。首次版本/文件不可覆盖；失败不得自动改标签或重传不同内容。

## 6. 影响文件与边界

新增打包元数据、MIT、release 配置、构建/检查脚本、测试与 .github 工作流；修改包 __init__/nyt 路由、前端资源默认值、Find/Grep 的 binary 定位和对应测试；重写 README，增加发布操作文档。

不实施 Spec111、不修改 Agent 调度/权限/审批/存储合同、不修改用户 .env 或数据库。既有 docs/future-work、handoff、Spec103/111 脏修改由其他会话持有；只在收尾读取最新 handoff 后补本片短摘要，保留其余内容。

## 7. 验收清单

- [x] 先运行新增测试，确认预期失败，再实现并转绿。
- [x] 全部既有后端/前端测试和构建通过；新文件不超过500行。
- [x] 本机候选 wheel 构建、白名单、真实二进制、源码外干净安装、Web/CLI 与临时运行/审批/重开验证通过（非冻结提交）。
- [x] 五目标候选归档可构建与检查，Python/前端指纹一致。
- [x] 对应 OS 真正运行测试由 CI 证据确认，不能用本机交叉打包替代。
- [x] README、MIT、第三方声明齐全，候选包按白名单检查无私有配置/开发材料。
- [x] 标签版本门禁、同提交全部通过门禁、OIDC 最小权限和公开安装后验已接线。
- [x] 正式上传 PyPI 并通过五平台公开安装后验；0.1.0五份文件摘要与已验证产物一致，见第10节。

## 8. 本轮实现与证据（2026-10-18）

已接入 pyproject/MIT、延迟导入的 nyt 路由、包内资源与源码开发分支、固定 rg15.2.0/fd10.5.0 的十份上游归档摘要、独立暂存及白名单、共享前端产物、五目标矩阵、同提交门禁和 OIDC/公开安装后验。运行依赖固定现有 runtime 快照，pytest/构建工具放 dev extra；未升级当前环境。README 重写为安装/配置/使用说明，维护操作集中在 [发行指南](../releasing.md)。

删除工具层两处旧 which 定位并迁移原测试替身，保留真实源码开发的 PATH/frontend/dist 消费；不增加运行时下载、安装器平台或历史业务兼容层。正常与失败主链见第3节和实际 scripts/cli/resources 入口。

- 打包三文件新增56项参数用例均有预期红灯记录；最终与 Find/Grep/停止/Web启动合跑 **95 passed in3.53s**。无 conftest 修改，原断言保持。检查标签、五目标完整性、代码指纹、文件白名单、哈希、归档类型、链接及 -O/PIP 配置边界。
- 五份候选 wheel 均构建成功并通过 twine/白名单/标签与指纹检查（`/tmp/naiyue-112-archives.log`）；约3.6—4.3MB/份，未包含依赖环境。其共同 application_sha256 为 fc1caa9f805257b304cae9e088880ec0e6205cd777203629df6da3c85076bcd7，仅标识当时快照。
- 共享前端分支另建 macOS ARM64 候选267文件、3,559,736字节；源码外 fresh venv、清空工具PATH、公开依赖解析、pip实际安装报告hash核对、Web静态资源/API、生产Find/Grep、审批恢复、SQLite重开和CLI全部通过，模型请求0次（`/tmp/naiyue-112-shared-install.log`；报告在忽略的build目录）。这些候选不是正式发行物。
- 当前前端快照 **39文件288 passed**，TS/Vite构建0错误，保留既有chunk体积提示（`/tmp/naiyue-112-frontend-current*.log`）。actionlint1.7.12、打包Python AST/500行、pip check、diff检查通过。
- 并行改动期间尝试全量后端：**35 failed、1494 passed，218.07s后由本任务中断自己的等待进程**（`/tmp/naiyue-112-backend-current.log`）。失败涉及消息/上下文/文件工具等正在修改的业务合同；后续同名单例已变化，不能将该快照当最终结果，也未改另一会话业务实现或放宽测试。等代码稳定再做完整验收。
- 架构/安全只读复核已完成。发现并用红灯关闭：复制文件/祖先链接、优化模式消除摘要assert、pip来源和实际wheel不一致，以及共享产物根路径resolve抹掉链接证据；最新入口单例通过。不以合成探针替代Windows/Linux运行或公开PyPI安装。

当前状态：发布相关实现已落地，最终整体验收待业务改动稳定；没有提交、push、tag或PyPI上传，没有修改真实配置/会话库。下一步是冻结同一提交后重跑全量并重建五份包，再由五平台CI确认可发行，PyPI账号配置可并行完成。


## 9. 提交前集成复核补充

真实Uvicorn启动验证发现：lifespan装配的ModelConfigError被Server.run转为SystemExit(3)，入口原except不可达，首次缺配置没有指引。修复保持实际装配顺序：lifespan通过AsyncExitStack进入原application_factory，只有进入失败的ModelConfigError记在当前app.state.startup_error，仍向Uvicorn原样抛出；Web入口只将对应启动SystemExit转换回同一异常，交现有安全诊断与配置指引。存储先失败时仍显示存储错误，不提前读取模型配置、不新增装配或模型调用。新增真实子进程回归，禁止仅mock Server.run证明接线。


提交前联合验证：后端2948 passed in123.74s、前端39文件289通过及TS/Vite成功；缺配置真实启动与Web生命周期24项通过，架构独立复跑确认修复。最终macOS ARM64候选267文件、3,561,109字节，application_sha256=817011664dff46ea7a73de4333c3d21a8c86536d8f98d00f1f3bf2e042eb7eb7；twine/白名单与源码外全新venv安装通过，工具/Web/API/审批/SQLite/CLI链正常且0模型网络请求。报告`build/ship-review-20261018/install-smoke.json`、日志`/tmp/naiyue-ship-wheel-{final,check,install}.log`。本轮仅提交push主分支，不推v标签或上传PyPI；五平台实跑与正式发行仍沿远端CI与单独发版授权。

## 10. 正式发行验收（2026-10-18）

用户业务提交 `dd1d358` 本地与远端一致且初始工作区干净。重新执行后端全量 **2948 passed in126.30s**（`/tmp/nyt-release-dd1d358-backend.log`）；前端 **39文件289 passed**、TS/Vite生产构建成功（同前缀 `frontend.log` / `build.log`），保留既有chunk体积提示；465份Python AST/500行、compileall、pip check和diff检查通过。

首轮远端CI的targets和frontend整理步骤真实失败：`shell: python`将脚本保存在checkout外，导入仓库`scripts`包时抛出ModuleNotFoundError。新增两项回归从checkout外的runner脚本执行完整步骤并清除PYTHON环境，两例正确红灯；两处步骤在本地导入前显式加入checkout路径，三份打包测试 **59 passed in2.02s**、actionlint通过，未变更产品代码和发布权限。架构/安全独立复核无新增发行阻断。

当前等待修复提交的完整五平台CI；标签和PyPI发行需在该提交的验证成功后执行，不能将首次跳过的五平台任务视为通过。

首轮Linux后端另有两项现有断言失败（2946通过）：rg的JSON匹配行可能含NUL后的字节；Linux文件名精确匹配不能依赖macOS的Unicode等价。发行修复限定为：Grep在已有匹配行解码前截取首个NUL之前的预览，保留原binary/end和未完整搜索提示；Read在原路径缺失后的有限候选中补充NFC名字逐个可分解字符的NFD形式，再与既有引号规则组合，不遍历目录、不重选已审批路径。生产影响仅`tools/grep.py`、`tools/paths.py`，对应原测试保留，新增确定性原生JSON记录及精确名称探针；正常工具消息与权限/重验链保持，失败仍走既有工具错误。修复后重新全量验证并等待同提交CI。

两项平台回归在本机先复现预期红灯，再转绿；相关122项通过。使用发行固定rg/fd运行完整后端 **2952 passed in124.64s**（`/tmp/nyt-release-platform-full.log`），前端仍为本轮已验证的相同源码/产物。修复后macOS ARM64候选267文件、3,561,323字节，application_sha256=`686099809e2efd56569d2296ac68c37d4e58cd2654191fcb36723c822c0d7e8e`；twine/白名单/源码外全新安装通过，工具/Web/API/审批恢复/SQLite重开/CLI全部正常，0供应商请求（`build/release-platformfix-install.json`）。安全独立64项回归通过，原授权和审批重验链未改；465份Python AST/500行、compileall/pip/diff、actionlint通过。远端`0ff7df8`五平台构建/安装已全部成功，但其Linux全量尚缺这两处修复，正式门禁必须使用接下来的最终提交。

### 冻结提交与标签实证

最终产品提交为 `bf66c64083e4d825c2309a2aba48a8ebcc93f8c1`，三项修复分别提交为`0ff7df8`、`8d5df9b`、`bf66c64`并推送。其[主分支CI](https://github.com/tlzmw001/naiyue-test-agent/actions/runs/39150378504)全部成功，Linux后端 **2952 passed in423.30s**，五平台实际构建/白名单/twine/全新安装及集中指纹检查通过。

复核远端提交、包版本、无冲突标签和PyPI尚无0.1.0后，在该提交创建并推送`v0.1.0`。[标签发布工作流](https://github.com/tlzmw001/naiyue-test-agent/actions/runs/39151037235)的全部发行前门禁再次成功：Linux后端 **2952 passed in422.69s**，前端39文件289项及生产构建、五平台安装和集中检查均通过。取回该run的五份wheel，逐份比较138个生产Python文件与冻结源码一致，版本0.1.0、application_sha256均为上述`68609980…`；报告与归档位于忽略目录`build/release-v010-published/`。

### 首次PyPI上传阻塞（历史尝试）

该run首次publish job在上传首个文件时收到HTTP400：`Non-user identities cannot create new projects`。日志提示Pending Publisher的项目名与上传元数据不一致；五份wheel的METADATA已逐份核对为`Name: naiyue-test-agent`、`Version: 0.1.0`，GitHub环境为`pypi`且无额外审批。当时公开PyPI版本JSON为404，后置五平台公开安装被跳过；该次尝试未发布成功。

按[PyPI官方上传排错说明](https://docs.pypi.org/trusted-publishers/troubleshooting/#upload-errors)，需要用户核对账号Publishing及Your projects中实际绑定的项目名；该失败可能已将Pending Publisher转换成另一个名称的项目。账号配置修正前不改包名、不移动标签、不盲目重复上传。用户核对完成后，重跑原run的失败任务，继续使用同一批已验证wheel，再核对PyPI文件/摘要和五平台公开安装。真实配置/会话库均未修改，真实供应商请求0次。

### 重试发布与五平台公开安装通过

用户回复“再试一次”后，确认工作区干净、标签仍指向`bf66c64`、PyPI尚无0.1.0，执行`gh run rerun 39151037235 --failed`。原run第2次尝试的publish成功，[PyPI 0.1.0](https://pypi.org/project/naiyue-test-agent/0.1.0/)正式包含五份wheel；沿用原标签和已验证产物，没有重新构建或改动产品代码。

本机重新运行五包归档检查，并查询正式PyPI JSON、下载全部五个发行文件：文件集合、大小、SHA-256和归档报告逐项与原验证产物一致，结果`build/release-v010-published/pypi-verification.json`。首轮公开安装Linux两架构/macOS Intel通过，Windows x64/macOS ARM64在安装前元数据查询阶段持续404并耗尽12次等待；正式元数据与文件已能获取后，仅重跑这两个失败校验，未重复上传。

原run第3次尝试整体**success**，五平台`python -m scripts.verify_wheel_install --wheel-dir dist --from-pypi --output build/pypi-install.json`全部通过。取回五份报告并再次核对版本0.1.0、实际文件SHA、包内原生工具、前端/API、Find/Grep、审批恢复、SQLite重开和CLI六项结果均passed，provider_requests=0。报告与最终workflow身份位于`build/release-v010-final-reports/`；[成功的发布工作流](https://github.com/tlzmw001/naiyue-test-agent/actions/runs/39151037235)关联源提交仍为`bf66c64083e4d825c2309a2aba48a8ebcc93f8c1`。

当前状态：Spec112首版发行合同全部验收，正式包名`naiyue-test-agent`、版本0.1.0、命令`nyt`；可从公开PyPI安装。用户模型配置与工作区数据保持原位置，本轮未发真实模型请求。后续用户使用按README配置模型并启动Web或chat；新的开发事项另行讨论。
