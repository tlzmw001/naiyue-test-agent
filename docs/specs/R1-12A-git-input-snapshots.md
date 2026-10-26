# R1-12A：Git 输入快照与 bundle

状态：2026-10-25 用户确认调研后的默认规则并授权实施；本片已完成本机验收，未提交/push/发布。12B 尚未实施。

## 1. 范围与当前链

R1-10/11 已有 `app.environments`、独立代码/状态卷、有限 envd RPC、持久 operation_id 和关闭接受门。目前代码卷为空，没有 Git 输入准备；GitSource 只登记 URL/ref，不下载。12B 再接新会话的环境选择、会话绑定和 UI，本片不改这些入口；结果取回/合入在13，SSH在14。

本片提供可调用的领域 API：准备/查询本机输入、把输入送入已有沙箱并检出固定任务分支、核对原安装结果。沿用现有工具、审批、异常与身份合同；不开放模型工具调用这些管理 RPC。

唯一来源是本次登记项目。LocalSource 必须可信、可用且项目目录自身就是标准 Git checkout 根；无 Git、无 HEAD、非标准布局明确拒绝，Host 仍可用。GitSource 允许对已登记 active 来源做本机 fetch 和静态检查，不执行项目代码、不自动改变项目 trust；沙箱 create/run 的可信项目门槛保持，GitSource 会话信任交互留12B。

## 2. 用户确认的输入规则

| 类型 | 行为 |
|---|---|
| tracked 当前内容、模式与删除 | 纳入，不改真实 index、工作区、HEAD 或用户分支 |
| untracked、nonignored | 默认不传，调用方可明确给出相对文件路径；不要求 git add |
| ignored | 首版不传；显式 include 也不能绕过 |
| 隐藏项目 Skills | 遵守同一分类，不整体排除点目录 |
| 项目外个人 Skills/凭据/配置 | 不复制，不隐含挂载 home |
| 当前候选输入中的已知凭据 | 明确拒绝，只有 code/相对路径，不含内容，不偷偷退回历史版本 |

首版确定性凭据规则：真实 `.env` / `.env.*`（明确的 example/sample/template 后缀除外）、常见 SSH 私钥名称、`.netrc/_netrc`、`.aws/credentials`、PKCS12 容器；文件内容命中标准 PEM/OpenSSH 私钥头同样拒绝。公钥、普通证书 `.pem`、`.env.example` 不按后缀一刀切拒绝。不宣称识别所有凭据或扫描/清理历史。

相对路径须规范 POSIX 形式，无绝对路径、`..`、控制字符或 Git 元数据分量。选入路径必须是文件/受支持的内部相对符号链接；拒绝越界/绝对链接、特殊文件、目录及来源身份变化。GitSource 无工作目录，不接受 include_untracked。

初版拒绝 submodule/gitlink、LFS 指针和需要自定义 checkout filter 的当前输入；不启动 filter、hooks、fsmonitor、子模块或自动下载 LFS。限制是当前输入特性，不把历史当作已经净化。

## 3. 本机快照与必要存储

`original_head` 是准备时的固定源提交；`input_snapshot` 是实际输入的提交。无树变化时可复用原提交，有变化时创建 app-owned 快照提交，父提交是 original_head。提交作者明确属于应用，不伪装用户提交。

应用私有目录：`data_dir/workspaces/<project_id>/git-inputs/<input_id>/`，包含独立 bare 对象库、临时 index 与 bundle。只在应用目录写对象/ref；不在用户 `.git` 写临时对象/引用。不使用 stash/reset 或用户 hooks；`hash-object --no-filters` 按实际字节生成 blob，临时 index 生成 tree。枚举与读取受配置的数量/单文件/总字节预算约束，源 HEAD 与已观察文件身份在完成前复核；检测到变化明确失败，不承诺对外部编辑器提供跨文件原子冻结。

Git 进程复用 HostProcessRunner 的超时、背压与回收；对象/checkout 安全命令禁用系统/全局配置、hooks、fsmonitor、pager、自定义 attributes 与非必要协议。来源的只读 ignored/untracked 分类须读取真实系统/全局/本地 ignore 配置，包括 core.excludesFile；该分支仍强制关闭 hooks/fsmonitor/attributes 等执行面，不能因安全命令禁全局配置而绕过用户忽略规则。GitSource fetch 仅从新 app-owned bare 库运行，单独使用本机用户认证/SSH配置及 HTTPS/SSH/file 合法来源，受控 GIT_ALLOW_PROTOCOL 禁止 URL 重写进入 ext/helper 协议；剥离环境 Git 配置/对象/索引/目录注入。用户自己的 credential helper、sshCommand/ProxyCommand 可以执行本机认证进程，不等于执行项目代码。凭据不写输入、bundle 元数据、日志和沙箱；当前 URL 校验继续生效。

磁盘边界：有界 bundle、当前树的真实 blob 大小/总量、Git命令超时与导入后缓存字节阈值。fetch 固定保留 pack；导入后阈值不是执行过程的硬磁盘配额，Docker CPU/memory/pids 也不是卷配额，本片不声称跨平台限制磁盘峰值。SHA-256对象格式、linked worktree/separate-git-dir等初版未支持布局明确拒绝。

注册表升级4→5，新增 `git_inputs` 仓储：input_id、project_id、source revision、请求清单、准备状态、固定提交、清单/排除原因、文件预算、摘要与错误码。SQL 参数化，不从 storage 反导 application。

input_id 使用32位随机十六进制调用方请求键。同键同请求返回已完成不可变快照，不重新取当前工作区；不同请求冲突。preparing/failed 的中断事实保留，不能用重试悄悄换一份输入；查询明确返回未确认/失败，重新准备需新 ID。磁盘成功而注册表提交失败仍不报告 ready。

配置复用 `AppSettings.sandbox`，新增嵌套 `git`：本机/容器 Git executable、命令超时、文件数/单文件/总内容/bundle/接收缓存总字节和快照记录数。严格校验并在输入/环境创建时冻结；旧记录缺此节按默认补齐，不修改真实用户设置文件或升级 Python 依赖。

## 4. bundle 与容器接收

完整 bundle 只导出 `refs/naiyue/input/<input_id>` 所需的可达对象/历史，绝不导出全部用户 refs/config。源库/快照树不等于已无敏感历史；该限制在返回值/文档明确。

增量只有接收端通过有限 `input_probe` 确认持有 original_head 后才生成，排除该基准的可达对象。接收端在 verify 时缺基准则明确 `git_input_base_missing`，控制端改传完整 bundle；不能凭远端URL、旧本机缓存或一次探测永久假定基准存在。

bundle 经版本化有限 RPC 分块进入 `/state/git-inputs`，不经 Shell，不挂载宿主目录。传输 ID 从 input_id 与 bundle 摘要确定；begin 冻结长度/摘要/ref/提交，chunk 用精确偏移和原字节核对支持安全续传，大小/总缓存有上限。文件名只由受检 ID 生成，普通可见文件不能提供接收路径。

接收端核对长度/SHA256、bundle refs 唯一性与目标、基准、对象完整性和当前输入树，导入独立对象缓存。缓存导入是无用户代码副作用的幂等阶段；不等于输入已经安装。clone/fetch/checkout 禁用 hooks/filters/子模块，并使用本机已固定提交，不接触源凭据。

最后以现有 journal 的 `git_input` 操作安装一次：只允许空代码卷、没有其他活动变更/进程或读取；检出 `naiyue-task/<input_id>` 并核对 HEAD。新输入安装时封住该环境的其他执行提交；既有活动项导致拒绝，不偷偷停止。返回固定 input_id/original_head/input_snapshot，控制端核对后保存 binding 的输入事实，任务分支由 input_id 确定。成功后可正常执行工具；失败/unknown 保留不可执行状态或拒绝覆盖，必须显式检查/销毁，不对半份目录重跑 checkout。即使容器 journal 已 completed，本机 binding.input_snapshot 的必要保存未完成时，普通文件/进程准入仍拒绝；输入管理、原 operation_status 核对继续可用。

丢失安装回执沿原 operation_id 查询，已完成则确认，unknown 则明确未确认；禁止重新 checkout 覆盖 Agent 后续修改。正常关闭继续使用11的接受门与原操作收尾，不能把新增输入任务漏出生命周期。

协议2增补有限 input_* 操作及 git_input kind，旧 envd 不支持时明确失败，不能绕过成普通 Shell。标准 sandbox Dockerfile 安装 Git；不自动安装宿主软件。

## 5. API 与职责

- `app.environments.prepare_input(project_id, *, input_id, include_untracked=())`：worker 准备本机不可变输入，无模型/宿主建图。
- `app.environments.input(project_id, input_id)`：读取既有状态，不重新准备。
- `app.environments.install_input(binding_id, input_id)`：核对项目/冻结 revision、接收能力和绑定；传输/导入对象并登记一次 git_input 安装，返回确认后的输入事实。
- 原 `operation_status(binding_id, operation_id)` 继续核对安装结果，不创建新操作。

`contracts/git_inputs` 只放数据与纯校验；`execution/git_*` 放受限 Git 进程、文件快照与 bundle；`storage/git_inputs` 放仓储；`application/environments/inputs` 编排来源/记录/绑定；`envd/git_inputs` 接收与安装。沿用 EnvironmentAPI，不增加顶层门面或新的全局框架。

同 input_id 的准备串行；同 binding 的安装复用已有生命周期锁。DB 短事务不跨 Git/Docker IO；事件循环只调用 run_blocking。容器接收锁只属于单个 envd，不跨项目；与 EOP 活动预留的嵌套/释放顺序同步写 docs/lock-hierarchy.md。无新增线程池或全局项目锁。

## 6. 正常与失败链

正常：API → registry 读来源/revision → 预留 input_id → 有界枚举与凭据/特性检查 → app bare fetch/临时 index/commit → full bundle/SHA → registry ready → 已有绑定接收确认基准 → full/delta 分块与校验 → journal 预留 git_input → 空目录检出任务分支 → 回执核对 → binding 保存输入事实。所有必要保存失败都不能报告成功。

失败：显式选择被忽略文件 → 快照前报 git_input_ignored（附相对路径），不写用户 index/HEAD、不安装环境。安装提交后失去回执 → 原ID查询；完成才确认，未知保留原ID且不重放。恶意 bundle/ref/路径 → 导入/checkout 前明确拒绝；中途进程或保存故障 → unknown，不恢复 Host 跑。

## 7. TDD 与验收

先新增失败测试，再实现以下正常/反例：

1. 部分暂存、未暂存修改、删除/二进制/可执行模式；真实 index/HEAD/工作区字节保持，快照树等于明确输入。
2. 未跟踪默认排除/显式选择、ignored 拒绝、隐藏 Skills、凭据拒绝及示例/公钥证书允许；无正文泄漏。
3. 无Git/无HEAD、路径越界/链接/特殊文件、LFS/submodule/filter、预算及来源 revision 竞争拒绝。
4. GitSource 本机 fetch 后固定提交、来源前进不改变既有输入；bundle 中无用户 config、凭据环境或无关 refs。
5. 完整 bundle、已确认基准的增量、探测后基准消失补全；篡改长度/摘要/ref/目标提交拒绝。
6. 仓储重开/4→5迁移、同ID重试/冲突、中断状态、必要保存失败。
7. 正式 EnvironmentAPI 与 envd 安装、重复请求不覆盖后续修改、丢回执只查原ID、两个绑定并行无全局阻塞、关闭封口。
8. 标准 Linux wheel 构建镜像，在现有本机 Colima 实际传输、工具读取/执行和恢复核对；不挂宿主目录，不请求真实供应商。

最后完整后端回归、标准构建/包内容核对；本片不改前端源码，按项目标准构建验证。架构/安全独立只读复核后做对抗检查。全部命令、真实结果与限制在实施完成后追加；不以历史11的通过替代本片验证。

不实现：12B会话/UI/信任交互、13结果导入/合并、SSH、个人记忆传输、忽略文件例外、敏感历史净化、任意插件安装。

## 8. 当前实现主链与复核

正常实例：项目内 app.txt 在 index 中是 staged，工作区是 working input。`EnvironmentAPI.prepare_input` 通过 run_blocking 进入 `InputService.prepare`，GitInputs.reserve 保存 preparing；SnapshotBuilder 读取真实分类及当前文件字节，独立对象库/临时 index 生成 input_snapshot，full bundle 摘要与不可变清单保存为 ready。真实 index/HEAD/对象库不写入；结束时复核 HEAD、index 的 stage 清单、源根与已观察文件身份（包括已删文件重新出现）。

`install_input` 在原接受门和 binding 锁内读输入、探测基准、通过 input_begin/chunk/cache 送入 `/state/git-inputs`。InputReceiver 核对摘要、唯一 ref、固定提交及父关系、对象和真实当前树，返回三个固定字段。SandboxOperationService 先保存稳定操作 ID，envd 在 EOP1 下核对空目录/活动/读取并持久预留，再 worker 检出任务分支。原结果指纹和值一致后，confirm 必须提交 binding.input_snapshot；工具端随后沿 EnvironmentService 的普通准入进入正式文件/进程能力。

失败实例：安装已经完成而 binding 确认写入失败，install_input 报 git_input_io_failed，binding 保留原 operation_id，files/processes 拒绝 git_input_install_unconfirmed；operation_status 查原 ID 后可重做必要保存，绝不重做 checkout。半份检出/unknown 在 envd 重启后仍封住新执行；两个不同 binding 不互相阻塞；正常关闭涵盖传输全过程并拒绝关闭后才到达的正式安装提交。

独立复核三项已补先红后绿回归：来源全局/本地 excludesFile 不可绕过；Git 非 UTF-8 路径统一为 git_input_invalid_path；确认必要保存失败时不能执行。纯 payload/路径校验在 contracts，application 不导入 envd；接收端和控制端共享合同。新模块均有实际 API/RPC/仓储调用方，无空占位；已有11进程及清理异常分支保持。

旧入口清理：本片原来没有 Git 输入实现，不保留第二套 clone/Shell 安装路径；三个字节路径解码点归一到 git_path，应用编排不反导入接收实现。原 EnvironmentService/DockerFileSystem/操作日志用于现有文件与进程工具，是真实消费者，继续保留。注册表 v1/v3 迁移夹具移除之后版本才有的 git_inputs 表，保留原有数据/指纹断言，当前升级终点同步为5。

## 9. 实际验收（2026-10-25）

| 项目 | 真实结果与记录 |
|---|---|
| 新增行为 | 49 项；有 red 记录后实现，fixture 构造错误与真实产品红灯分别记录 |
| 输入/原沙箱专项 | 148 passed in 29.27s；`build/r1-12a/regression-final.txt` |
| 完整产品后端 | 两个不重叠文件组，2641 passed in 269.63s + 2656 passed in 251.81s = 5297，0失败/错误/skip；groups-final.json、full-results.json、两份 XML/日志 |
| 前端/构建 | 459 passed；TS/Vite 0错误，保留既有 chunk-size 提示；本片不改前端源码 |
| 发布构建检查 | 标准 Mac/Linux ARM64 wheel、Twine 均通过；329 Python、4 Web资源及README与源码逐字节一致，研究clone/测试/环境文件未入包 |
| 源外安装 | Mac新venv、源码目录外正式CLI/HTTP/SSE、文件、history/memory、存储升级/备份/还原smoke通过；install-verified.json |
| 真实Docker | 最终Linux wheel→naiyue-envd:r1-12a-final 镜像，公开API准备/安装、正式Read/Shell、部分暂存保持、隐藏Skill发现、ignored排除/凭据拒绝、重复不覆盖、重开原ID确认通过；docker-inputs-verified.json |
| 原沙箱合同 | 文件/图片/权限过滤、none/external冻结策略；进程/字面argv/非零/超时/后台/丢回执/输出/正常关闭/控制端崩溃/envd重启；ProcessCleanupError固定类别及机制异常传播真实探针通过；docker-files/processes/cleanup.json |
| 复核/资源 | 架构及安全独立只读复核无遗留阻断；1031份生产/测试/脚本/前端冻结输入哈希一致，pip check通过；自建容器和两类卷已清理，默认Docker context仍default |

主要复现命令（从该工作树根目录运行）：

```sh
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_git_input_snapshot.py tests/test_git_input_service.py tests/test_git_input_install.py tests/test_git_input_boundaries.py tests/test_envd_git_inputs.py tests/test_sandbox*.py tests/test_envd*.py tests/test_docker*.py tests/test_registry_execution_revision.py tests/test_domain_packages.py --tb=short
.venv/bin/python -m scripts.verify_sandbox_inputs --context colima-nyt-r1-10 --image naiyue-envd:r1-12a-final --output build/r1-12a/docker-inputs-verified.json
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/r1-12a/wheel-verified --output build/r1-12a/install-verified.json
```

完整回归的实际参数数组保存在 full-results.json；groups-final.json 与 `pytest tests --collect-only` 的5297项核对无遗漏/重复。`pytest` 未指定 tests 的首轮发现会误收 docs/research 和第三方研究clone，不属于产品测试；没有修改测试配置或skip，改为明确产品 tests 目录后执行上述全量。

最终镜像 ID `sha256:3e06ded824f043de5d0439ce83743f57135b8068b108fa68ac290be711d81e55`，使用专用 colima-nyt-r1-10；原工具探针所用前一镜像与最终镜像的329份Python字节完全相同，最终重建只同步README元数据，再重跑输入主链。真实请求到模型供应商为0，GitSource本机file fetch已测，未用真实公司私有仓库/凭据或远端SSH验收。缓存限制是导入后阈值，非硬磁盘配额；不承诺历史凭据净化，也不宣称业务测试效果已经达标。

真实配置、用户库、Python依赖、版本和既有dirty保持；只更新实现及相关文档。证据总表 `build/r1-12a/verification.json`；下一片12B须先讨论会话绑定/信任与UI，再实施。
