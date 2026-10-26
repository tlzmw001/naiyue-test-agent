# R1-04 多项目运行与普通对话

状态：2026-10-24 用户批准实施；已完成本机实现与验收，未提交或发布。

版本边界：本片的默认宿主、旧工厂与 Application 平铺领域别名是当时的过渡路径，已由 [R1-07D](R1-07D-admission-and-project-local.md) 删除。当前仅 open_service_application 创建根应用，再显式 projects.open/chats.open/temporary.open 选宿主。旧会话冻结原路径的合同同步改为 project_local 跟随用户显式重新定位；原验证记录仍保留为历史时点。

## 1. 目标和现状

R1-03 已有注册表和项目数据定位，但 assembly 为一个库创建一个完整应用，
SessionService 的额度及 MemoryAccess 的个人库也各自拥有。新登记目录没有
marker，不能直接接旧的 marker 检查。保持每项目一张根图及同库子图，不引入
跨库 checkpointer，不创建 worktree，不实现 Docker 或前端。

正常例子：一个应用打开 A、B；A/B 各建会话并交替或并行执行，普通对话另开库；
关闭并重启后，各宿主从自己的库恢复，模型只加载一次。
失败例子：B 的数据库已占用，B 打开失败并释放中途取得的资源，A 继续运行；
A 原目录消失，历史仍可打开，新 run、子实例续跑及新进程/文件操作明确失败。

## 2. 对外入口和过渡

- 新增 `await app.projects.open(project_id) -> ProjectHost`，同 ID 重复打开复用。
- `await app.projects.close(project_id)` 显式关闭；关闭后旧宿主引用不可再用。
- 新增 `await app.chats.open() -> ProjectHost`，其 project_id 为 None，独立持久库。
  已有持久工厂显式以 `data_dir/chats/sessions.sqlite` 启动时采用 chats profile，
  app.chats.open 复用默认宿主；拒绝给此保留路径传项目绑定。
- ProjectHost 暴露 sessions/runs/history/tasks/memory/observability/tools/subagents/mcp。
  这些是已绑定所有者的领域对象，不经当前界面选择或 cwd 动态分发。
- Application 仍只有一份 models、projects、chats 及共享资源。
- 现有 app.sessions 等保留为工厂参数明确选定的默认宿主领域对象，不增加转发层；
  CLI 拥有者循环、Web 幂等/错误映射及既有 HTTP/SSE 签名保留，分别在 05/06/07 接新入口。
- RAM/--no-session 不创建 registry、身份文件、个人库或聊天库；原有临时语义不变。
- Git-only 项目尚无执行环境，open 明确失败；不隐式 clone 或猜本机路径。

## 3. 资源拥有关系

| 资源 | 所有者和关闭责任 |
| --- | --- |
| ModelCatalog/模型客户端 | Application，一次加载，全部宿主关闭后才关闭 |
| 个人记忆 | Application 的 PersonalMemory，延迟打开，同锁/同 Database；项目关闭不关它 |
| 全局 root run 额度 | Application 的 RunAdmission，根运行从准入直到必要保存/清理结束占用 |
| 项目注册表 | Application，沿用 R1-03 |
| ProjectStore/saver/根图/子图 | 各 ProjectHost，不能跨项目复用 |
| 会话/任务/项目记忆/写入器 | 各 ProjectHost，域对象从该宿主的仓储查归属 |
| MCP 资源与捕获借用者的工具 | 各 ProjectHost，新建注册表并注册该宿主借用方法 |
| 可共享无状态工具定义 | 同一组输入 ToolSpec，可注册入不同宿主工具表 |

ProjectHost 是资源和领域 API 的组合，不递归 build_application，不含另一个应用
项目注册表或模型目录。默认和附加宿主都独立跟踪操作/流任务，均无模型关闭权。
应用有独立生命周期，关闭先封住所有宿主入口、停止运行、
等待所有宿主必要 IO，由应用关闭模型后排空写入器并关闭各宿主库、共享个人库。
显式关闭单个宿主不会关闭模型。保留原异常优先，清理失败有日志。

显式 close 不等同应用退出：操作正在进行、根运行/审批未结束、后台进程/记忆任务
仍占用或必要保存不确定时拒绝，保留宿主；不自动停止这些任务或自动空闲回收。
删除已打开项目在写入 deleting 操作前拒绝，和 open/close 共用管理锁；重定位需
先 close 再修改位置再 open。同位置的显式信任确认可以继续，旧会话仍不改位置。

## 4. 归属和执行位置

- open 用登记 UUID 取得数据库，持锁后在迁移前检查实际连接里的 UUID 与会话身份；
  不采用先检查再解锁重开的方式。历史数据库 root_path 可因显式重定位而不同。
- chats 同样持锁核验已有库：必须无项目绑定、会话和子记录都无项目/执行目录，
  拒绝误放的项目库；不会先迁移再核对。默认旧入口继续先开库核验，再加载模型。
- 注册项目新建会话自动绑定登记目录，不能传另一个目录；普通对话只允许无目录。
- SessionRepo 的已绑定宿主接收当前登记目录作为创建边界，仅影响新增记录；
  不改数据库旧 root_path，不重写旧 session/子实例的路径。
- SessionService 构造执行根时附带受信的注册位置校验对象。它不是序列化字段，
  从仓储恢复后由所属宿主重建，模型不能提供。工具仍只使用本次 run 的执行根。
- validate_execution_root 保留 stat/类型检查；对注册宿主由该对象重新查询当前登记
  的 UUID、active、trusted、LocalSource、规范路径，并拒绝与保存路径不一致。
  无注册校验对象的既有入口仍按 marker 校验，不把未知身份当已登记成功。
- 旧路径不再登记到此项目时，报 execution_root_unavailable/not_registered 或 wrong_project；
  缺失/not_directory/unreadable 仍如 R1-01。任何失败都不改执行目录。
- 保存的子根先从数据库读取，再绑定该宿主校验对象；比较其路径/类型/项目与父
  实例快照一致，续跑仍用子记录原目录。
- 会话、历史、观测和任务入口以所属宿主的 metadata/已加载 actor 查 ID；
  错宿主的 ID 不能查到别库或被当空结果成功。任务 ID 还受该宿主任务表限制。
- 单个库的 UUID 验证和查询范围保留；不扫描所有项目猜 session_id 的归属。

## 5. 普通对话与记忆

普通对话路径 `data_dir/chats/sessions.sqlite`，有独立 saver/根子图；project_id、
execution_root 都是 None。文件/Shell 仍没有目录权限，项目 history 能力不注入。
memory 工具只允许 personal，both 读请求缩为 personal，workspace/refresh_overview 拒绝。
普通对话仍可查看自己的聊天历史（产品历史），不能使用跨项目 history 工具。

MemoryAccess 借用应用 PersonalMemory；原独立构造者默认拥有自己的 PersonalMemory，
保持现有低层测试和脚本消费者。个人库不按项目分割。来源仍在当前宿主事务内核验，
不能跨库伪造来源。普通对话源使用保留档案命名空间 `chats`（不是 project_id）；
现有 source_workspace_id TEXT 可存此值，不需要 schema 变化。读取其他项目保存的
个人记忆时正文可见，来源不属于当前库则明确不可用，不暗中打开其他项目。
普通对话只注入个人偏好和相关记忆，不产生项目概览/批次后台任务。

## 6. 数据流和受影响文件

打开：ProjectAPI.open → HostManager.open → ProjectService.get → open_storage(registered)
→ ProjectStore/Database 持锁验证 UUID → 共享 catalog/personal/admission → assemble_host
→ 宿主 MCP/观测/MemoryAccess/SessionPersistence → assemble_runtime(saver) → ProjectHost。

执行：host.sessions.create → 固定项目/位置写入 SessionRepo → host.runs.start →
SessionService._admit → 注册位置检查 + 共享 RunAdmission → 原 RunLifecycle → 必要
保存/清理 → 释放全局额度。额度数的是实际 busy actor，不另设易泄露的计数器；
取消/保存失败在清理结束后释放，仍拥有未清理子工作时继续占用。

恢复：host.sessions.restore → 本库 metadata/checkpoint → install_restored_session 重建
校验对象 → 历史可用；新 run 才执行位置准入。状态显示也使用同一检查。

预计修改 assembly/core/lifetime；新增 host assembly/manager/API 资源对象；扩展
SessionService、SessionRepo、storage opening profile、execution_roots、子实例恢复；
调整 MemoryAccess/API/来源/普通对话注入；任务及观测加显式归属检查。已有消费者
继续调用原领域对象，未使用的旧装配分支删除，不保留平行装配实现。

## 7. 验收

先写失败测试并确认预期红灯，再实现：

1. 两个真实 SQLite 项目 + 普通对话，复用打开、根/子 saver 分离、单次模型加载。
2. 交替和并行执行、重启恢复各自原文，错宿主 session/run/history/tasks/usage 拒绝。
3. 新项目不写 marker 也能执行；移动后旧会话不重绑定，目录缺失新执行失败。
4. 全局 max_active_runs 在项目/普通对话间共同生效，失败/取消/保存后释放。
5. 个人记忆唯一实例、跨项目可见，普通对话 personal 可用、项目能力拒绝。
6. 打开/编图/存储锁失败、取消取得资源、显式关闭忙碌、应用关闭及重开释放锁。
7. --no-session 没有新增持久副作用；既有 HTTP/CLI 和 storage 导入方向测试通过。

最终运行全部后端、前端测试、TS/Vite、标准 wheel/Twine 和源码外安装检查。
不提交/推送/发布，不调用真实模型，不把本机测试当生产证明。完成后讲解 R1-05。


## 8. 实际接线与迁移收尾

- application/assembly.py 先验证默认库再加载一次 catalog，构造应用生命周期、PersonalMemory、RunAdmission；
  projects/host_assembly.py 是唯一宿主装配，默认与附加宿主共用，不递归创建 Application。
- ProjectAPI.open → HostManager.open → 注册信息与 opening profile → ProjectHost；
  ProjectHost 持有已经绑定本库/本 SessionService 的领域 API。models/projects/chats 由应用拥有。
- SessionService._admit 在应用共享准入锁内快照所有宿主 busy actor，再调用原 admit_run；
  仍拥有未清理子工作的 run 不会提前释放额度。保留普通短锁，不为统计引入重入锁。
- ExecutionRoot.authority 仅在内存由宿主重建；RegisteredExecution 查询当前登记，
  状态与实际工具/续跑都用原目录检查。恢复审批额外显式传绑定根，仍重做原 subject/策略比较。
- chats opening profile 在实际连接迁移前拒绝项目库；无目录聊天只接 personal memory。
  personal 来源事务用 chats 命名空间；注入同时核对活动工具和固定 Deny，临时会话无个人能力。
- 显式关闭/删除/重定位由 HostManager 同锁协调；已打开项目删除不先写 deleting，
  位置变更要求先 close。旧会话/子记录路径不改，列举/排序及新建顺序按 UUID 覆盖新旧记录。
- 应用关闭先排空所有宿主运行、审批、后台工作及必要 IO，再关模型、写入器与存储；
  显式项目关闭只关闭自身资源。原先已验证的异常优先/清理断言保留。
- 库别名由 owns_database 在三处拥有关系比较中统一规范化；仍保留开库文件安全检查。
  同步模型 SDK worker 的 stop/steer 等控制仍可调用，关闭态检查不会强加事件循环要求。
- 已删除原 assembly 中重复的完整应用装配；没有动态字符串代理或旧门面转发。
  现有 CLI/Web 的真实默认域对象与适配职责保留；公开工厂/HTTP/CLI 签名未改。
- 测试内部拥有者引用改指宿主，新增断言宿主父对象/应用级模型的关系；
  MemoryAccess 的装配替身移到实际生产模块并接受借用个人库关键字，无 conftest 修改、放宽或 skip。

## 9. 实际验收（2026-10-24）

```text
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
4950 passed in 355.11s
```

新增 30 项确定性测试，六个 test_multi_project_* 文件覆盖实际 A/B/chats 图、SQLite、
模型共享/并发、重开、个人记忆写入/注入、作用域拒绝、目录消失与进程保留、审批冷恢复、
取消半开资源、忙碌关闭、删除/重定位及跨宿主归档清理。最初 5 项红灯见 initial-red.log；
独立复核 4 项红灯见 review-red.log，后续来源/别名/活动工具失败用例日志同目录保留。
最后相关专项 117/5.28s；前端 50 文件 424 项、TS/Vite、标准 Mac ARM64 wheel/Twine、
源码目录外安装通过。272 包内 Python 文件与源码一致，898 冻结输入无漂移。
证据：build/r1-04/verification.json 与各日志。没有真实供应商请求；未新增依赖、迁移或配置，
未触及真实用户库；Windows/Linux 原机、服务协议、UI、Docker 不在本次证明内。

唯一下一步是说明并批准 R1-05 单一服务与会话协议；当前尚未实施第五片。
