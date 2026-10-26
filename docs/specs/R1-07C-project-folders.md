# R1-07C：本机文件夹选择、创建与信任

状态：2026-10-24 用户批准后已完成本机验收；基于 07B 当前工作树，未提交、push、合并或发布。

## 1. 范围与当前主链

当前 ProjectWorkspace/ProjectNavigation 已能选择已登记项目，但没有文件夹入口。ProjectAPI.add_local/create_local 已实现规范路径、单个名称、安全 mkdir、登记、操作 ID 和失败对账；HTTP 项目写命令使用共享服务回执。浏览目录不应经过 ScopeServices.get 或创建项目库。

本片实现侧栏添加项目、页头项目/此计算机入口、已有项目/普通对话选择、已有文件夹选择、新文件夹创建、显式信任，以及失败后的结果核对。不做远端/Docker、项目移除/重定位 UI，不给已有会话改绑。旧版未登记 marker 沿用既有 import_required 提示及正式导入入口，不覆盖或重写旧身份。

## 2. 目录读取合同

新增认证 GET /api/v1/directories，参数 path（可选绝对路径，可展开 ~）、show_hidden、offset、limit（默认 100，上限 200）。调用 ProjectAPI.directories → run_blocking → application/projects/directories；不访问项目注册表/模型/图或项目指令，不写文件。

返回 path（规范路径）、parent、separator、identity、items[{name,path,symlink}]、next_offset、positions[{name,path}]、default_parent_dir、default_error。只枚举目录；隐藏项默认关闭，支持显式查看；排序按名称。常用位置只列真实存在的位置，Windows 包含可枚举盘符。分页为当前目录读取，不承诺内容快照，客户端按路径去重；目录本身的身份在读取前后核对。

未给 path 时，默认位置可用则使用它，否则使用主文件夹，并明确返回默认位置失效原因。显式 path 失败绝不改读其他目录。无权限、消失、非目录、非法路径返回现有 project_directory_* / project_invalid_path；记录错误类型，不记录目录内容或原始敏感异常。

identity 是规范路径与既有 directory_facts 的 SHA256 摘要，不是权限凭证。通过字符串避免 Windows 64 位 inode 在 JavaScript 中丢失精度。选择当前目录时捕获该摘要；相对路径不绑定服务 cwd。

## 3. 确认与写命令

LocalInput/CreateInput 和对应 ProjectAPI/ProjectService 增加可选 expected_directory（64 位十六进制摘要）：已有目录核对目录本身，创建核对父目录。旧调用方省略时保持现有合同；本片浏览器始终传递。校验所得 directory_facts 直接进入原操作记录，后续沿既有 require_same_directory/mkdir_under 校验，不能再次捕获替换后的事实作为已确认目录。

请求记录仅在提供时加入 expected_directory，保留旧请求重放。先查匹配的已完成操作，再按原信任/目录规则执行；不能让重试重复创建。任何预览、路径输入或取消都不会登记项目；确认页展示规范路径（新建时为明确父目录＋单个名称）与权限说明，用户明确勾选信任后才发写请求。

前端为确认对象保存稳定 X-Request-ID；失败后的同一请求重试沿用它，不自动重发写命令。结果不明确时用既有 GET /projects/operations 核对该 ID：已完成则读取项目并进入；未找到/尚未完成明确显示。对有记录的未完成登记，可显式继续完成：现有 reconcile 增加可选 operation_id，仅恢复选中的记录；不意外恢复其他项目。继续完成使用新的 HTTP 回执 ID、原登记操作 ID，复用原操作阶段与目录身份，不能重复 mkdir。取消/修改目录后重新确认产生新 ID。

## 4. 界面与状态

沿用 07B 中性色、字体/圆角和 CSS Modules。页头项目入口打开原生 dialog 的项目选择视图，提供已有项目、普通对话、选择文件夹与新建文件夹；侧栏“添加项目”直接打开目录视图。目录对话框使用路径栏/上级按钮、常用位置和目录列表，支持键盘进入、隐藏项与加载更多，明确加载、空、错误及重试。窄屏压缩常用位置，保留完整操作和可滚动内容。

使用原生 dialog 管理打开/关闭、Escape 与模态背景，并显式保证 Tab 边界循环；第三份相同职责与 SessionSearch/SessionMenu 共用 dialogFocus，保留原键盘断言。写命令进行中禁止关闭，返回结果后保留确认/错误。测试环境 jsdom 缺少 showModal/close，只在对应测试文件补这两项 DOM 状态模拟；真实焦点/模态行为由实际浏览器验证，不修改共享测试基础设施。

路径请求用 AbortController 与请求身份隔离，旧响应不覆盖新目录。输入路径尚未读取、读取中或失败时不能确认旧目录。确认路径中的不可见控制和方向字符显示为转义文本，提交仍保留真实路径。确认对象冻结路径/摘要/名称，返回目录视图后清除确认。添加成功刷新项目树并进入对应项目，旧项目的草稿和会话状态仍由 07B 保留；已有会话不改绑。

## 5. 正常与失败链路

正常：添加项目→GET directories→浏览/输入路径→捕获规范目录及摘要→确认信任→POST projects/local（或 create）→原 ProjectService/操作记录/安全 mkdir/项目库→成功项目→刷新侧栏→进入该项目→用户新建会话。

失败：目录选择后被替换→expected_directory 比较失败→拒绝登记/创建，不把新目录当成已确认目标。写请求断线→服务持有原回执→页面保留确认和请求 ID→用户核对指定操作→已完成则进入；未完成则显式恢复该操作，仍核对原目录。默认目录失效→明确提示并可浏览其他位置；取消或任意失败均不切到别处执行。

## 6. 改动及验证

影响：application/projects 的 directories、locations、service、api；service 目录路由/项目输入/接线；frontend API/类型、ProjectWorkspace/App/ProjectNavigation 入口、项目/目录/信任视图和样式；对应测试和交接。

测试先红后绿：无注册/库/模型副作用；鉴权/实例/来源；任意其他目录、默认位置失效、隐藏项/排序/分页/父目录、非法/无权限/消失；确认后目录或父目录替换；重复添加/同 ID 重试/定向对账；取消零写入、先勾选才提交、Win 路径显示、目录晚响应、创建冲突与结果未知、7B 草稿/身份保持。聚焦回归稳定后冻结输入，完整后端/前端、构建、标准 wheel/Twine/源码外安装、桌面和 375px 实际页面验证。


## 7. 本机验收与接线（2026-10-24）

本片 11 项后端新回归、14 项前端新回归先看到预期失败再实现。初期测试装配修正了错误的 settings 导入和缺少 Origin 的写请求，保留业务断言；仅当前测试文件模拟 jsdom 缺少的 dialog 方法，没有修改共享测试基础设施。真实浏览器发现原生 dialog 首项反向 Tab 可离开模态框，补显式边界循环；第三份相同职责与 SessionSearch/SessionMenu 共用 helper。jsdom 的组合选择器返回顺序经最小探针确认不同于 DOM 顺序，helper 改为按 DOM 顺序筛选控件，保留旧键盘测试。此项也在实际浏览器重验。

| 验证 | 命令（工作树根目录） | 实际结果 |
|---|---|---|
| 聚焦后端 | `.venv/bin/python -m pytest tests/test_project_directories.py tests/test_project_navigation.py tests/test_project_api.py tests/test_project_registry.py tests/test_project_registry_boundaries.py tests/test_project_registration_review_cases.py tests/test_multi_project_runtime.py tests/test_service_protocol.py tests/test_storage_startup_entries.py tests/test_service_lazy_application.py -q --tb=short -x` | 76 passed / 3.80s |
| 完整后端 | `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short -x` | 5063 passed / 407.87s |
| 完整前端 | `npm --prefix frontend test -- --run` | 56 files / 458 passed |
| TS/Vite | `npm --prefix frontend run build` | exit 0；保留原有大于 500kB 的 chunk 提示 |
| Python 编译 | `.venv/bin/python -m compileall -q naiyue_test_agent scripts` | exit 0 |
| 标准候选 | `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-07c/wheel` | exit 0；427 包成员 |
| 包元数据 | `.venv/bin/python -m twine check build/r1-07c/wheel/*.whl` | PASSED |
| 源码外新环境安装 | `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/r1-07c/wheel --output build/r1-07c/install-smoke.json` | exit 0；安装清单各能力通过，0 供应商请求 |

运行/测试/构建输入冻结后无漂移：964 个文件；297 份 Python 包内源码与当前源码一致，4 份 Web 资源与 dist 一致，README 元数据一致；测试和临时可视化服务未进入 wheel。候选 SHA256：`991a173059dfc0d546d65139f871cafc6a692fc8dde007c34443667b740b5d68`。日志、逐片差异、冻结清单和结果位于 `build/r1-07c/`，摘要 `verification.json`。

真实 TCP 服务＋一次性目录在本机浏览器完成：默认位置以外选择已有目录→信任→登记；默认位置下新建目录→信任→登记→新会话→固定模型回答；切回原项目保留草稿。桌面、375px 窄屏、长名称、隐藏项、Escape 后焦点返回、反向 Tab 循环均实看；截图见该目录。服务已正常关闭，登记仅在一次性数据目录，未触碰真实配置或用户库。浏览器证据来自 macOS，Windows 路径与身份精度只有确定性测试；固定模型与安装测试不证明真实供应商或业务效果。

L1/L2/L3 对账：目录领域查询经 ProjectAPI 接认证 HTTP 路由；前端 API 经共享服务认证/实例核对；侧栏与页头都调用同一 picker；确认使用原登记/创建服务，失败只核对/恢复明确的原操作。新字段贯通输入模型、API、服务、持久请求指纹和测试，未改 schema。旧焦点边界逻辑已被 helper 替换，当前三个调用方均保留测试；没有增加另一套注册/建库入口。默认位置只作起点，任意路径和已确认目录摘要仍经服务校验。

边界：旧版未登记身份继续由正式导入入口处理；项目移除/重定位 UI、Git 初始化、Docker/远端、Shell/进程后端迁移不属于本片。未改真实环境配置、依赖或版本。下一步讲 R1-08 环境绑定与本机文件操作，获批准后另写实施 Spec；没有提前实现。
