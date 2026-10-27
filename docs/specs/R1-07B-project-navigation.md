# R1-07B：项目侧栏与会话切换

状态：用户于 2026-10-24 授权实现；已完成本机验收。基于当前 R1-07A 工作树；不包含 07C 目录选择、创建或信任操作。

## 1. 范围与当前主链

当前浏览器启动后以固定 WebApi(scope_id) 渲染 App。Sidebar 只有该 scope 的会话；useConversation 保存该 scope 的草稿、选择和 SSE 观察。GET /scopes/{id}/sessions 会经过 ScopeServices.get，冷项目会打开运行宿主、加载模型，因此不能用它批量填充项目树。

本片新增普通对话和项目目录；项目可展开、选择，并打开其会话。已有项目 CRUD、信任和执行目录校验继续由后端负责。本片不改变会话归属、不移动历史，不增加项目管理按钮。

## 2. 元数据接口

- 复用 GET /api/v1/projects：items 为 RegisteredProject，含 project_id/name/source/lifecycle/trusted/availability；不创建宿主。
- 新增 GET /api/v1/navigation/{scope_id}/sessions，返回 SessionRow 数组（仅标题、置顶、模型、身份、时间和状态字段，不返回 thread_id 或工具配置 JSON）。scope_id 仅接受 chats 或项目 UUID；经过现有 Bearer、Host、Origin 和服务实例校验。不会经过 ScopeServices.get。
- 应用层 ProjectAPI.list_sessions(id)、ChatAPI.list_sessions() 调用 HostManager 的目录读取。热宿主复用其已持有的仓储；冷项目用 require_existing + runtime_profile 校验打开已登记库；未创建的 chats 库返回空列表，不创建库。错误保留明确失败，不转换成空列表或读取其他库。
- 将 ProjectSelection 的同类读取迁至 HostManager 共享的锁内方法；选择入口已持锁，不重复取得锁。目录读取不加载目录内 AGENTS/skills、不加载模型、不编译图。

## 3. 前端状态与显示

ProjectWorkspace 以服务连接为根，持有项目目录、当前 scope 和访问过的 scope。每个访问过的 scope 固定 WebApi 和 App；切换不改变已有 App 的 scope，也不重新绑定会话。未访问项目只读元数据，不挂载 App。

每个 App 继续持有自己的 useConversation（草稿按 scope 内 session_id 保存）。非当前 App 隐藏并暂停 SSE、会话/后台任务轮询和辅助观察；服务端执行不停止。返回时只读同步状态与历史，再恢复 live run 的观察；不会因为返回而自动发起恢复执行。已有显式点击会话的恢复流程保持。

主题和发送后的滚动偏好由外层页面持有并传给各 scope，不随项目恢复旧值。展开状态同样属于整棵导航树，切换 scope 后保留。

跨项目点击携带 {scope_id, session_id, request revision}。迟到响应只能更新原 scope 的私有状态；已隐藏时不得重建观察。选择请求等待该 scope 的本地提交结束再处理。初始链接只决定首次 scope；会话打开后的 project_id、实际执行目录不能由切换按钮改写。

侧栏依次显示普通对话、项目标题与状态、展开的会话。项目目录和已展开的非当前会话目录沿用现有只读轮询节奏；页面隐藏时跳过查询，另提供手动刷新，不要求重新加载页面丢失草稿。当前 scope 继续提供现有搜索、置顶、改名、删除和组内排序；其他 scope 的会话可直接点开，点开后使用同一管理能力。搜索明确说明当前范围。每组分别展示加载、空列表和可重试错误；目录失败不显示为空项目。

沿用现有中性色、字体、圆角与 CSS Modules：项目标题行分开“展开”和“进入”操作，缩进表达从属，当前 scope/会话高亮；不增加装饰背景。窄屏沿用抽屉，切换后关闭。页头显示项目名称（普通对话单列）和“此计算机”，路径使用会话真实 workspace_root，并保留不可用说明。

## 4. 正常与失败链路

正常：展开 B → navigation/B/sessions → ProjectAPI → HostManager 锁内仓储读取 → B 标题列表；模型加载计数仍为零。点击 B/s → 创建固定 B WebApi 的 App → prepare(s) → 历史与执行状态。A 的草稿留在 A；A 的 SSE 断开，但其 run 继续。返回 A → session(s) 只读刷新 → live SSE 续订。

失败：项目目录丢失/不可信 → 项目树标注状态；仍可读其独立存储的目录和已保存历史，开始 run 继续由现有后端拒绝。库丢失或身份不匹配 → 对应目录显示失败与重试，不读取 chats 或其他项目。服务实例变化/认证失败 → 显示现有明确错误，不换服务、不自动重放写命令。

接线核对发现旧 history_only 仅允许“不可信且目录不可用”的组合。本片将其调整为不可信项目也可打开历史宿主（包括目录仍存在时）；历史宿主的建立不授予执行权限，RegisteredExecution 在每次执行前核对实时登记和信任，不能因宿主已缓存而跳过。回归测试要求历史准备不读取项目指令、不调用模型，启动 run 明确拒绝。

## 5. 改动与验收

影响：application/projects 的 API、HostManager、selection；service 导航路由和 app 接线；frontend WebApi、ProjectWorkspace/ProjectNavigation、App/Sidebar、conversation 同步及辅助观察；对应测试、当前交接文档。

验收包括：冷目录零模型/零宿主；空 chats 不建库；热库复用；错误库/跨 scope/认证拒绝；A/B 同名会话隔离；草稿返回保留；切换中迟到响应隔离；SSE 离开中止/返回续订且不发停止或恢复命令；后台观察暂停；原管理能力回归；目录加载/空/错误/不可用状态；桌面与窄屏视觉检查。

开发按失败测试→最小实现→聚焦回归→独立架构与安全复核→冻结输入→全量后端/前端、构建、标准 wheel 安装验证推进。不提交、不发布。

## 6. 本片限制

草稿和已访问 scope 的状态保留在当前页面内存，切换项目可恢复；刷新或关闭页面不提供草稿持久化。仅展开项目读取元数据，进入项目/会话后才装配其宿主。会话管理和搜索作用于当前 scope，不跨项目移动或排序。已访问 scope 保留至页面关闭，暂不增加自动回收。

## 7. 下一片 07C 准备内容（未实施）

1. 侧栏增加“添加项目”，输入区项目入口提供已有项目、普通对话、选择已有文件夹、创建新文件夹。切换只决定新会话归属，已存在会话保持原身份。
2. 认证目录查询返回真实路径、父目录、常用位置和子目录元数据，界面支持路径输入、返回上级、隐藏项开关、键盘选择以及加载/空/不可读状态。默认新建位置只作起点，不限制用户浏览其他有权限的位置；浏览不写项目登记或身份。
3. 用户选定目录后展示规范路径和权限说明，再明确确认信任；通过现有 ProjectAPI.add_local/create_local 登记，不新建另一套项目身份或直接提交任意 trusted 标志。取消不登记。
4. 新建文件夹明确父目录和单个合法名称，复用既有安全 mkdir、操作 ID、失败对账及冲突规则。添加成功刷新项目树并进入该项目，不修改原会话。
5. 校验跨平台路径、目录消失/替换、无权限、同目录重复添加、新建重名、默认位置无效、取消及双页面操作；用真实 HTTP 和桌面/窄屏交互证明路径与信任边界。

正常链：浏览→选择→显示规范目录→用户确认信任→既有 ProjectAPI→侧栏刷新→项目下新会话。失败链：选定后目录被删除/替换→后端拒绝→保留选择与错误，不切到其他目录。07C 不实现 Docker、远端环境或对现有会话的执行身份改绑。

## 8. 回归发现与修正

首轮完整后端在 4037 项通过后发现 1 项失败并按 -x 停止：test_storage_startup_entries.py::test_chat_reports_structured_migration_failure_without_private_body。目录读取曾把 SessionPersistenceError（包含其子类 StorageStartupError）统一改写为 ProjectError，导致 CLI 缺失迁移阶段、版本和备份恢复提示。已用该既有用例独立复现；修复只移除对存储领域异常的通用包装，保留原异常及结构化失败事实。OSError 的目录检查失败仍记录并映射。相关 24 项聚焦回归通过，未修改或放宽既有测试。首轮日志/候选保留为 build/r1-07b/*-initial，修正后重新冻结并完整回归、重建候选和重新安装验证。

## 9. 最终验收与交付边界

最终完整后端 **5052 passed in 410.04s**；前端 **55 文件 / 444 passed**；TypeScript/Vite、compileall、差异格式检查、变更生产文件 500 行上限通过。标准 Mac ARM64 wheel、Twine 与源码外全新环境安装通过；包内 295 份 Python、4 份前端资源及 README 与当前输入一致，955 冻结输入无漂移。最终 wheel SHA256：`7145271a50976e35e52e9b80c5a47c1d5f426613c567df2b2dfb74186ea956a0`。

实际应用内浏览器在真实 TCP 测试服务检查 1280×720 桌面和 375×812 抽屉；项目展开、当前范围、真实路径 tooltip、切换保留草稿、按项目发消息和显示固定回答均可见。测试数据均可销毁，0 次供应商请求，不代表真实模型效果或用户掌握。截图及完整命令/结果位于 `build/r1-07b/`，汇总为 `verification.json`。Vite 的 JS 包大于 500 kB 为现有构建提示，构建零错误。

未改真实配置、用户库、依赖或版本；主目录并行 Spec129/XML 未纳入候选。旧 ProjectSelection._sessions 已迁至唯一 HostManager 目录读取实现；当前消费者是 ProjectSelection、ProjectAPI 和 ChatAPI，未保留平行旧实现。新增接口已在共享服务/main.tsx 接线；legacy 只读页面保持。实现位于独立重构工作树，尚未提交、推送、合并或发布。唯一下一步为讲解并批准 07C；目录浏览、文件夹创建/选择与信任 UI 未实施。
