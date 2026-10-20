# PyPI 发行指南

发行项目：`naiyue-test-agent`；安装命令：`nyt`；首版：`0.1.0`；许可证：MIT。

## 一次性配置 PyPI

1. 登录现有 [PyPI 账号](https://pypi.org/account/login/)。可复用 aitest-kit 的维护账号。
2. 在 Account settings 确认邮箱已验证、双因素认证可用，妥善保存恢复方式。
3. 打开 [Publishing](https://pypi.org/manage/account/publishing/)。在 **Add a new pending publisher** 中选择 GitHub，填写：

|字段|值|
|---|---|
|PyPI Project Name|`naiyue-test-agent`|
|Owner|`tlzmw001`|
|Repository name|`naiyue-test-agent`|
|Workflow name|`publish.yml`|
|Environment name|`pypi`|

Workflow name 填文件名，不是界面显示名 Publish，也不填 `.github/workflows/` 前缀。发布器记录的工作流和 environment 必须与仓库一致。

4. 点击 Add，确认记录存在。Pending Publisher 不预占名称；首次上传成功后创建项目并转为正式发布器。

后续由 GitHub OIDC 发布，不需要在 GitHub Secrets 填 PyPI 密码或长期 Token。每次发版不需要重新登录。若项目已存在且归当前账号管理，应从项目 Manage → Publishing 添加普通 Trusted Publisher。

官方说明：[首次创建项目](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[使用发布器](https://docs.pypi.org/trusted-publishers/using-a-publisher/)。

## 一次性核对 GitHub

- 仓库：`tlzmw001/naiyue-test-agent`，Actions 已启用。
- Environment 名称为 `pypi`。可从 Settings → Environments 预先创建；工作流首次引用不存在的环境时 GitHub 也会创建。
- 若设置 deployment branches/tags，允许对应的 `v*` 标签。自动发布不要求配置每次人工批准；是否增加环境审批由维护者决定。
- 只有 publish job 有 `id-token: write`。构建和测试任务仅需读仓库，PR 不触发上传。
- OIDC 上传 job 直接定义在 `publish.yml`；被它调用的 CI 工作流不作为 PyPI 信任身份。

## 本地构建候选 wheel

需要 Python、Node.js/npm 以及构建工具；这些是维护端需求。安装用户无需 Node/npm/Rust。

```bash
python -m pip install build==1.3.0 twine==6.2.0
npm --prefix frontend ci
npm --prefix frontend test -- --run
npm --prefix frontend run build
python -m scripts.build_release --target macos-arm64 --output build/candidate
python -m scripts.verify_wheel_install --wheel-dir build/candidate --output build/install-smoke.json
```

目标名：`macos-arm64`、`linux-x86_64`、`linux-arm64`、`windows-x86_64`。同平台安装测试必须在对应 OS/架构运行；交叉构建与归档检查不能代替运行验证。已存在的同名产物不会被覆盖，需要选择新的候选目录。

构建器只从 `release/native-tools.json` 指定的上游资产下载，校验固定 SHA-256；在全新暂存树复制 Python、前端和许可证；成品检查按白名单拒绝额外文件。Linux 原生资源检查静态 ELF，macOS 从 Mach-O 最低系统版本生成标签；平台 wheel 不绑定 CPython ABI。

源码开发仍可以使用原 requirements 清单和 `python -m` 入口。直接在未准备资源的源码目录运行通用 wheel 构建会拒绝缺失资源；发行构建统一使用上述脚本。首版仅上传已验证的平台 wheel，源码在 GitHub，暂不上传 sdist。

## 每次发版

1. 核对工作区、变更范围和目标提交。其他会话仍在改代码时，候选包仅作探针；等改动稳定后统一验证。四份 wheel 的 Python/前端指纹必须一致。
2. 修改唯一 Python 版本事实 `naiyue_test_agent.__version__`；pyproject 从该值读取版本。前端 package.json 是独立构建项目的版本，不作为 Python 发布版本来源。
3. 跑全部测试、前端构建与安装 smoke，提交并确认主分支 CI 成功。
4. 在确认的提交上创建并推送对应标签，例如 `v0.1.0`。此步骤会正式上传 PyPI，须核对授权、提交和产物后执行。
5. Publish 自动调用 CI：校验标签、全部后端测试、前端测试/构建、四平台安装 smoke、集中白名单/版本/源码指纹检查全部通过，才上传四份 wheel。
6. 发布后的四平台 job 查询 PyPI 元数据、核对发行文件 SHA-256、下载正式文件，并通过普通 PyPI 安装再次验证产品链路。

CI 安装 smoke 使用全新 venv、临时 home/workspace/SQLite、源码外 cwd 和移除系统工具的 PATH。验证命令、包内 rg/fd、生产 Find/Grep、前端/API、审批恢复、SQLite 重开与 CLI；使用确定性模型，模型网络请求为零。报告作为 Actions artifact 保存，不进入产品包。安装检查现在要求本包及全部依赖均以wheel安装
（`--only-binary=:all:`），不允许CI预装编译器掩盖用户机器缺少二进制依赖的问题。

MCP专项通过原安装worker的必经路径运行，复用离线模型与checkout中的
`tests/mcp_discovery_fixture.py`，不引入pytest到安装环境。检查禁用路径零MCP IO、
只读list零工具调用、运行中新增/选用/审批、根与子实例复用、普通失败与结果未知、
停用旧待审、TTL重建及冷历史零重放，报告写入`install.mcp`。任一断言失败均阻断
安装检查，原生工具/Web/CLI/内置工具检查保持。

2026-10-20用户确认取消Intel macOS，当前发行矩阵仅保留上述四目标；
`release/native-tools.json`同时决定构建目标、CI安装和发布后安装范围。完整发行
检查要求恰好包含这四个不同目标，拒绝额外旧Intel包、缺包及重复包，并继续检查
版本与应用源码指纹一致。既有依赖版本保持；历史已发布的Intel文件不受影响。
四目标二进制标签解析、本机macARM安装与MCP主链已验证，Linux双架构及Windows
原机结果待本批提交的CI。详见[08交付记录](specs/117-08-mcp-delivery-verification.md)。

## 失败处理

- 测试/构建/安装失败：先修复并重新验证，不跳过门禁。
- PyPI `invalid-publisher`：核对 owner、repository、workflow 文件名和 environment。
- 标签和包版本不一致：修正发版目标，不让工作流猜版本或修改源码。
- 上传后安装验证失败：保留已上传事实及日志，先定位，不自动重打标签或重传不同文件。
- PyPI 不允许覆盖已用文件名；修复通常使用新版本。索引传播延迟可以等待后重新执行安装验证，不以重新发布处理。

本地检查与远端 CI、上传成功与公开安装成功分别是独立证据。尚未执行相应阶段时，不宣称已发布或已验证全部系统。
