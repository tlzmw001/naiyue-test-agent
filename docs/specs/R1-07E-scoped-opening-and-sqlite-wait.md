# R1-07E：冷打开按范围协调与 SQLite 写锁等待

2026-10-25，用户批准仅实施锁评审第 2、3 项。第 1 项线程池、第 4 项归档/记忆锁、R1-08 和真实配置均不改。

## 1. 当前链路与问题

HTTP/CLI → ScopeServices.get → HostManager.open → assemble_host → 开库/迁移、共享模型准备、MCP discovery、根/子图装配。前两层均持全局 asyncio 锁跨越整个装配，A 的慢 MCP 会阻挡 B、chats、临时宿主及侧栏列表。热服务快速路径不受影响。

ProjectStore → Database 的 sqlite3 连接与 open_storage → AsyncSqliteSaver 的 aiosqlite 连接共享库文件，两者目前隐式使用 5000ms 等待。运行收尾修剪的写事务可与其他会话的 checkpoint 写竞争；启动迁移不是已开放宿主的常态并发路径。

## 2. 冷打开合同与实现

在已有 application/lifetime.py 中引入内部 ScopeTasks，实际消费者为 HostManager 的项目/chats/临时宿主及 ScopeServices 的交付装配。复用既有生命周期模块，保持应用根包的模块白名单，不放宽架构测试。它仅负责按键串行生命周期操作、共用打开任务和取消收尾，不缓存业务结果。每个键的短寿命锁含使用者计数，最后一个持有者/等待者离开才移除；所有字典操作均在拥有者事件循环内，无跨线程管理锁。

- 同一范围的并发打开共用一个 Task；至少还有一个等待者时，单个等待者取消不取消共用装配。最后一名等待者取消则取消并收尾，保留旧的部分获取释放合同。成功对象仍由原有 hosts/services 字典持有，失败任务移除后可重试。
- 旧装配已取消但尚在清理时，新请求先等清理结束，再重新装配，不继承旧请求的 CancelledError。
- 不同范围打开、列会话和关闭互不等待。列会话不加载模型/MCP，但与同范围打开/关闭串行，防止冷库读取与持久宿主争夺文件所有权。
- open/close/relocate/remove 在同范围串行，不削弱 project_in_use、身份/信任、历史只读及重新定位合同。
- CLI/协议选择在项目 ID 未知的准备阶段，先按规范 root、再按推导或显式指定的旧库路径协调；旧 marker 查询复用 locate_workspace，只作协调键，不授予项目身份。得到项目 ID 后再进入宿主门禁，保留同目录准备/绑定/导入的互斥，并允许其他目录选择。
- 明确项目 ID 或注册表已命中目录时，不为协调而额外解析旧 marker；沿用注册项目不依赖旧 marker 的选择合同。准备阶段提前取得的注册列表随本次调用传递，不重复查询同一事实。
- 共享模型仍由 DeferredModels.get 单次准备；宿主装配参数使用本次局部副本，不并发改共享 options。
- 打开 Task 自己登记 owner.operation；根 lifetime 显式持有正在打开的宿主任务集合。服务退出先封口/取消交付装配，根退出取消并等待宿主装配，再关闭已发布宿主、模型与存储。关闭中完成的装配必须清理，不得发布。
- 临时会话断线显式取消该范围尚未完成的服务/宿主装配，收尾后再销毁；不等待其他项目。
- 异常保留原身份；无等待者的任务异常仍消费并记录安全的 error_type，取消属于生命周期操作。

正常例：A discovery 等 Event，B 项目/chats/临时宿主可装配并列历史；A 两个调用者拿同一实例。失败例：首个等待者取消，其他等待者继续；服务关闭则主动取消装配，资源释放，无延迟发布。

## 3. SQLite 配置与接线

StorageSettings 新增 busy_timeout_ms：严格整数、默认 5000、允许 0..30000。沿用原默认；0 表示锁冲突立即失败。AppSettings/load_settings 与维护读取统一解析，不改个人记忆的独立 busy_timeout_ms。

宿主 settings.storage → open_storage → ProjectStore/Database 的 sqlite3.connect(timeout=毫秒/1000)，以及 aiosqlite.connect 相同 timeout。持久项目、chats 和 RAM 宿主均接线；侧栏冷库读取和登记建库也使用当前存储设置。低层独立存储入口默认继续 5000，不升级依赖、不改变 schema、不重试或重放模型/工具。

正常例：A 修剪写事务被 Event 暂停，B checkpoint 已进入 SQLite 写等待，事件循环仍响应；释放 A 后 B 保存成功。失败例：超出配置等待时间，saver 抛原 sqlite 异常，现有运行失败/保存合同保留；释放锁后连接可继续使用。超时只控制竞争等待，不保证 SQL 的总执行时间。

## 4. 范围与验证

影响：应用范围协调/lifetime、服务 scope/临时关闭接线；StorageSettings、Database/ProjectStore/open_storage、宿主/注册建库/侧栏配置传递；对应测试、设置示例、handoff。本片不改公开 HTTP/CLI 协议，不引入依赖。

先运行失败测试确认旧代码阻挡跨范围打开/列表、取消首等待者导致重复装配，以及 SQLite 配置缺失。再最小实现、聚焦回归；事件测试覆盖同范围唯一装配、失败重试、取消隔离、关闭/重定位顺序、临时断线和根关闭无残留。SQLite 测试覆盖配置默认/非法值、两连接 PRAGMA 值、真实修剪写事务与另一会话保存、超时后可继续写入。最后完整后端、标准构建及 diff/行数检查；真实模型、远程 MCP 和其他系统不作为本机测试证据。

## 5. 当前可追踪主链

正常打开：ProjectAPI.open → HostManager.open → ScopeTasks.open(('host', project_id))。_opening 的值为 Task；_waiters 记录实际等待者数量，serial 的按键锁只覆盖本范围生命周期。Task 自己进入 owner.operation → _open_host → assemble_host；装配成功后才写 _hosts、登记 children/resources，并把同一 ProjectHost 返回给等待者。chats 的 host key 使用 None，临时宿主使用 ('temporary', identity)，不与项目键相撞。

HTTP：ScopeServices.get 的热服务仍直接返回；冷服务通过同一个 ScopeTasks 算法按 scope_id 装配。服务关闭先封口并取消服务装配，根 lifetime 取消并等待 _opening_tasks，再收尾已发布宿主及模型/存储。个人等待者取消仍有其他等待者时不取消 Task；最后一人取消则等待资源收尾。清理中的新请求等旧 Task 结束后重新打开。临时断线只取消本 scope 的装配，再停止/清空其宿主与交付。

目录选择：规范 root 门禁 → 仅在未登记 legacy 分支推导旧库门禁 → 等待后重新核对登记 → 项目 ID 门禁 → 信任/位置核对与会话列表。已登记项目不因新增门禁依赖旧 marker；别的目录不等待这条链。冷侧栏读取与同一范围的开关库协调，不建立模型或 MCP。

SQLite：AppSettings.storage.busy_timeout_ms → 宿主 open_storage → ProjectStore/Database.connect 与 aiosqlite.connect，毫秒统一转成秒。注册建库、冷侧栏和 CLI 绑定接同一设置；组件测试装配也显式传递 settings.storage，不制造产品与 fixture 的配置分叉。测试通过 SQLite 删除触发器在真实修剪事务内暂停 A，追踪 B 的 pending writes/checkpoint SQL 已进入写等待，释放 A 后确认 B 有流式 message_delta、completed 终局、ready 行和可读取历史。0ms 冲突保留 SQLITE_BUSY，释放后同一 saver 继续写入成功。

## 6. 本机验收记录

- 新增 24 项；核心失败测试与审查补充回归的红灯保存在 build/r1-07e/。架构审查发现的取消收尾、新旧目录选择以及已登记 marker 边界均已修正并复核；安全审查无新增发现。包根白名单保持，未放宽测试或保留废弃 scope_tasks 模块。
- 最终专项：`python -m pytest tests/test_domain_packages.py tests/test_scoped_host_opening.py tests/test_sqlite_busy_timeout.py tests/test_history_recovery_boundaries.py tests/test_terminal_projects.py tests/test_service_temporary.py tests/test_multi_project_lifetime.py -q`，**64 passed / 7.77s**。
- 完整后端按 collect-only 清单划分为互不重叠的两组，233＋233＝466 文件，2561＋2560＝**5121 项**；分别 **250.33s / 178.53s**，均退出 0。清单并集等于完整 collection，没有删用例或跳过失败；不是单进程全量通过声明。
- 标准 `scripts.build_release --target macos-arm64 --output build/r1-07e/wheel-verified`（含 TS/Vite）、Twine、源码目录外新 venv 安装 smoke 全部通过。295 Python/4 Web 资源与候选一致，测试/脚本未打包；CLI/HTTP/SSE、临时会话、SQLite 重开、升级/备份/还原等 smoke 保留，restore 阶段 model/write/auto 均为 0，供应商请求 0。
- 969 个冻结输入无漂移，改动 Python 文件均不超过 500 行；compileall、pip check、git diff --check 通过。前端 141 个源码文件相对 07D 未变化，本片未重跑前端单元测试；标准构建已运行。真实模型/远端 MCP 性能和其他系统尚未验证。

完整命令、退出码、分组清单及限制见 `build/r1-07e/verification.json`。早期失败和主动中断日志只作过程证据，不计为通过。只修改本片 23 个文件，保留此前 dirty 工作；未提交、推送、合并或发布。第一项线程池与第四项归档/记忆锁仍待后续证据驱动处理。
