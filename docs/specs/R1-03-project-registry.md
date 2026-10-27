# R1-03 项目注册、旧库导入与默认新建位置

版本边界：本片记录的是当时的注册合同。R1-07D 已按用户确认替换“重新定位后旧会话固定原路径”的行为：注册项目会话/子实例引用 project_local，显式重新定位后跟随当前位置；历史路径仅审计。新增注册 revision 与完整迁移、准入规则见 [R1-07D](R1-07D-admission-and-project-local.md)。

2026-10-24，用户已批准实施。基线 d717d14＋当前未提交 R1-01/02；只在
codex/project-execution-root 实现，不提交、合并、发布，不改真实用户配置或库。

## 1. 当前主链与问题

CLI/Web → prepare_workspace → 用户目录 marker → 项目库绑定 → build_application
→ 一个 ProjectStore、图/saver、SessionService。AppSettings 没有项目默认位置。
SessionRepo.bind_workspace 的旧重定位会改写会话和子实例目录，不能复用为新重定位。
Database 已拥有连接、排他文件锁和事务；R1-02 禁止 storage→application 或应用 SQL。

本片新增 app.projects 领域 API，不切换旧 CLI/Web 创建、执行、恢复入口；资源装配
迁移在 R1-04，CLI 在 06，项目/会话侧栏、文件夹选择器在 07，沙箱在后续片。
新登记项目不等于已能从现有单项目 runs API 运行。旧 marker 验证入口保留其真实消费者。

## 2. 数据和职责

- 可选 projects.default_parent_dir 随现有 AppSettings/load_settings 读取，默认 None。
  必须为非空绝对路径，可展开 ~；不要求启动时存在、不创建目录。它只是默认位置，
  不是允许目录范围；可在其他可访问目录添加或新建。与 storage.data_dir 分开。
- 独立 data_dir/projects.sqlite：自己的 application_id/user_version，复用 Database
  的锁/事务/关闭，在独立初始化函数建注册表与操作表，不改 schema 18 项目库。
- RegisteredProject：UUID、名称、LocalSource(path) 或 GitSource(url,ref)、项目库位置、
  登记生命周期 pending/active/migrating/deleting、位置是否获信任。位置可用性读时检查，
  不把 missing/not_directory/unreadable 当成删除。嵌套目录可登记并返回提示。
- Local 规范路径唯一（符号链接归一；Windows normcase），项目 UUID/库路径唯一。
  Git 本片仅登记来源和预留库路径，不 fetch、不伪造本机根或创建可运行的 Git 项目库；
  实际准备仍在 R1-12。URL 不接受内嵌密码或 HTTP 凭据。
- 操作：ID、动作、规范参数/指纹、项目 UUID、进度、结果与错误码；ID 重用但参数不同
  拒绝。成功重试返回同一个结果，不重复建目录/建库。跨库没有原子事务承诺。
- SQL/DDL/行映射在 storage；规范化、信任、重定位和流程在 application/projects；
  共享冻结数据与异常在 contracts/projects。不新增通用 resolver、回调平台或框架。

## 3. API 与实际接线

app.projects 提供异步 list/get/add_local/create_local/add_git/import_legacy/rename/
relocate/remove/operations/reconcile；有副作用请求显式提供 operation_id。
Local 添加/创建/重定位显式 trusted=True，不由 marker 自动授予信任。
create_local(parent=None, folder_name=...) 复用默认位置，未配置则要求显式父目录；
只接受不超过 255 字符的一个合法目录名，拒绝绝对名、分隔符、. 和 ..。
操作系统可有更严格的字节/字符限制；长度先验证，防止原生结构长度截断。可选 git_init=True。

ProjectAPI 使用同一 ApplicationLifetime 的 operation/run_blocking；ProjectService
持有注册表仓储、配置与当前拥有的项目库。注册表第一次调用时打开并对账；关闭由
现有资源栈在在途操作结束后完成。未使用 API 的旧启动路径不创建额外文件。
临时/RAM 应用保留同名 API，但调用报 project_registry_unavailable，不开注册表、
不登记、不写 marker。应用只暴露一个入口，不给 CLI/Web 加平铺转发。

## 4. 正常路径与失败路径

### 4.1 添加已有 Local

用户确认目录 → 规范化/信任/冲突校验 → 注册表同事务写 pending 项目与操作 →
核验目录身份 → 在应用默认 UUID 目录以独占新建方式打开 ProjectStore →
保存新库文件身份与空库摘要凭据 → 新空库写绑定 UUID →
关闭项目库 → 操作和项目一起标 active。用户代码目录没有写入。
若该路径已登记且 active，返回已有项目，不新建 UUID；不同操作请求也不复制登记。
显式添加已有目录若发现旧 marker，只提示走旧库导入，不能为旧身份另造项目。

注册表落 pending 后中断：重开按同 ID 核对默认库是否存在/UUID 是否匹配；新空库可完成
初始绑定，但必须有本操作已保存且仍匹配的文件身份/空库摘要；缺凭据报
project_database_unconfirmed。有其他身份或不明数据则拒绝，已有库先只读核验，
不能在核验前迁移。失败保留操作进度和错误。
目录在核验间被替换时拒绝，不仅检查同一路径字符串。

### 4.2 新建文件夹

确认父目录/名称/信任 → 记录 mkdir_planned → 以已核验的父目录句柄相对创建 → 记录目录
设备/文件标识 → 可选 git init → 项目库/登记完成。已存在同名目录不视为本次创建。
若崩溃恰在 mkdir 与保存文件标识之间，存在目录的所有权无法证明，报告
project_creation_unconfirmed，不自动收养/删除目录。已记录标识的步骤可安全对账。
git init 使用 argv、cwd=该目录、无 shell；失败保存明确错误，重试前核对目录标识。
POSIX 使用 dir_fd；Windows 使用 NtCreateFile 的 RootDirectory 句柄，不退回按字符串
路径创建。微软参考：https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile 。
在项目库操作后、提交登记前复查目录身份和已有 marker 冲突，不让替换目录继承信任。

### 4.3 旧库导入

只扫描 data_dir/workspaces/*/sessions.sqlite，加用户显式传入的自定义库。
以排他库锁＋只读 SQLite 读取原绑定 UUID、规范原位置和版本，不迁移旧表/反序列化
checkpoint、不建立任何旧目录 marker。当前 Application 拥有的库通过现有仓储读取，
不再争自己的文件锁。不同库相同 UUID、同路径不同 UUID、目录 UUID 与绑定冲突均拒绝。
一个旧库一个确定操作 ID；结果逐项包含错误，重复导入保持同一 UUID/历史和库路径。
无绑定不猜项目。位置缺失仍可导入并从原库读取历史；导入不等于信任该位置。

### 4.4 改名、重新定位和删除登记

改名只改注册表；重新定位必须 expected_root 与当前一致、显式确认新位置。
记录 migrating → 核验新位置/冲突 → 更新注册表。项目库 UUID 与旧会话/子实例的
执行目录均保持，不能调用旧 bind_workspace 做迁移；旧会话继续按原目录准入。

删除登记先进入 deleting 并检查项目库的资源所有权和活动/未确认状态。当前 Application
仍拥有的项目拒绝删除，第四片才支持独立关闭 ProjectHost；其他库必须能取得排他锁，
运行/审查未确认状态则拒绝。然后删除登记并保存完成操作。此片保留项目库和归档，
回执 data_retained=True，绝不删用户目录/个人库；自动导入跳过已删除登记的库，显式
重新导入可恢复。没有沙箱时不搭空的 Docker 清理器。

失败只报安全错误码并记录错误类型，不记凭据或原始异常内容。未知结果不能伪报成功。

## 5. 影响文件与验证

- contracts/projects.py，application/projects/{api,service,operations,locations}.py；
  若职责/500 行限制需要拆开，按登记、目录创建、导入等实际职责拆分。
- storage/{registry_database,project_registry,project_databases}.py；Database 增加
  独立 registry 打开 profile，不复制其事务/锁机制。
- application/core.py/assembly.py 接线；settings.py/config_paths.py 或独立 settings
  子模块加入已授权的配置模型；只更新 docs/examples，不改用户配置。
- 新测试分注册/恢复、旧库导入、应用生命周期、配置；不改 conftest。

TDD：先测添加不写 marker、默认外创建、UUID/路径冲突、中断/未知窗口、旧目录失效、
原库保留、旧会话不重绑、删除资源保护、临时不登记和 API 真接线，再实现。
故障注入在真实 SQLite 事务/文件动作边界，关闭后重开对账，不靠纯 mock 宣称持久恢复。
配置测试覆盖读取与非法值；结构测试保持 storage 单独可导入和应用无 SQL。
最终专项＋全后端、前端测试/TS-Vite、标准 Mac ARM64 wheel/Twine，并核对包成员。
测试只用临时目录/本机 git、模型替身；不创建用户项目、不请求供应商或云资源。

## 6. 当前接线与可追踪主链

本片代码已接线。assembly 在持久 profile 创建惰性 ProjectService，登记清理到既有
AsyncExitStack；core 总是构造 ProjectAPI，RAM profile 的 service=None。
API → owner.operation → run_blocking → service 的串行锁 → ProjectRegistry；
关闭沿已有 owner 等待在途线程（包括被取消的调用）结束，再释放注册表和项目存储。
新增取消测试用 threading.Event 同步，不依赖紧 sleep 时间窗。

正常示例见 test_add_existing_directory_without_marker_and_reopen：选择目录 →
reserve(pending) → initialize_reserved_store → ProjectStore.open_reserved →
Database 持有实例锁与真实连接 → 新库独占创建/已有库 query_only 身份检查 →
保存 database_created 文件身份与摘要 → bind_reserved_identity → database_ready →
复核目录与 marker → finish 同一注册表事务更新 active 和操作 done。

失败示例见 test_identity_is_checked_on_actual_owned_connection_before_migration：
在真正打开 Database 的边界换成他项目 schema17 库 → query_only 检查抛
project_identity_conflict → 初始化/迁移没有执行 → 关闭锁/连接 → 原操作保存错误，
项目保持 pending；他项目库内容与版本不变。没有先迁移再核验的旧入口。

目录竞态测试在 mkdir 前替换父路径为指向未选目录的链接，POSIX dir_fd 仍引用原
已选目录对象，未选目录没有新增文件夹；路径改变使登记失败。目录/新库凭据未落盘的
未知窗口分别保留 project_creation_unconfirmed / project_database_unconfirmed，
不自动收养既有内容。这里只交付查询/对账 API，人工解决未知状态的前端在 07 讨论。

旧库事实只读验证，pending import 恢复仍核对 UUID、marker、实际来源；已有项目重新
导入时使用注册表已确认的新位置，不把历史绑定的原目录重新升级为当前权威位置。
默认 scan 跳过已删除登记的库，显式导入可恢复原 UUID，目录/历史/个人库保持。
删除当前 Application 拥有的项目明确 project_in_use；本片不声称可独立关闭 ProjectHost。

本片 12 个新产品文件＋5 个既有接线/存储文件，新增配置示例，无依赖/版本/schema18
变化，无真实配置/用户库操作。没有把 Git 来源伪装成本机工作目录，没有提前实现浏览器
目录选择器、CLI/Web 多项目或云端执行。旧 CLI/Web/workspaces 函数保留真实消费者，
后续迁移在 04/06/08 清理；本片没有新增空兼容层。

## 7. 验证记录

最初登记/配置测试为 16 failed / 5 passed；接入恢复/API 测试时 7 failed / 5 passed。
独立复核用确定性测试复现 marker 冲突、pending 假成功、未知库收养、目录替换、父目录
竞争和校验前迁移等缺口，均已修正；Git 已记录初始化的各阶段均不重复执行。
最终专项 **198 passed in 4.96s**；新增 **59 项**行为/配置测试，无 conftest 或全局
测试 helper 改动。首轮全后端 **4919 passed in 335.81s** 后，又为原生目录名结构的
长度边界增加红绿验证并冻结最终源码；最终 **4920 passed in 336.00s**。
前端 **50 文件 / 424 passed**，TypeScript/Vite 构建成功（仅既有大包警告）。
标准 Mac ARM64 wheel 构建及 Twine 通过，**267 个 Python 包成员逐字节一致**，
当前 README 已进入包。源码目录外安装同一 wheel，验证默认配置、默认外新建、不写
marker、注册表关闭重开及原操作 ID，全部通过；不以此声明 Windows 或真实模型验收。

命令均在重构 checkout 执行；重建需换新的输出目录，避免覆盖已验证产物：

```sh
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-03-dist
.venv/bin/python -m twine check build/r1-03-dist/*.whl
.venv/bin/python build/r1-03/installed_smoke.py
```

证据 `build/r1-03/verification.json`、`backend-final.log`、`focused.log`、`frontend.log`、
`frontend-build.log`、`wheel-build.log`、`wheel-content.log`、`twine.log`、`installed-smoke.log`。
最初安装探针把 macOS /var 与 /private/var 的别名作字符串比较，已规范两侧路径并用
真实 TOML 换行修正验证脚本，仍强制包从安装目标加载；原失败保留在
`installed-smoke-first.log`，未为通过探针修改产品或放宽行为断言。
曾误用系统 Python3.9 做 AST 检查，改用项目 .venv Python3.12 后，267 份源码全部
通过解析/500 行限制；最终 885 个冻结输入无漂移。独立架构/安全只读复核发现的全部
问题均已按真实测试关闭。未提交/推送/合并/发布，未改依赖/版本/真实配置/用户库。

## 8. 唯一下一步

讲解并确认 R1-04，多项目资源、普通对话、归属路由、应用共享个人库/模型和统一
运行额度。第三片 API 不代表现有 CLI/Web 已能选择或运行新项目；第四片也不提前
实现第七片的左侧栏/目录对话框。参见总体方案第 8.3 节，批准前不实施。
