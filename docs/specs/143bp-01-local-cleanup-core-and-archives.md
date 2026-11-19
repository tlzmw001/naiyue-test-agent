# Spec143BP-S1：本机清理基础与工具输出归档

2026-11-19 已接通认证 HTTP、归档处理和同实例回执查询。Windows 原生验收归 S4。前置为[公共合同](143bp-00-contracts.md)。本片交付后可通过认证 HTTP 完成工具归档清理及查询。前端入口归 S3，云输入类别在 S2 接入。

## 1. 当前调用链与问题

现有 `service/terminal_sessions.py: preview/clear → TaskAPI.preview_outputs/clear_outputs → SessionService.output_guard → BackgroundProcessManager.output_guard → tools/output.py` 是当前 scope 下的全量清理。`ClearPlan` 保存文件名字、大小、mtime、inode，执行要求整个目录快照一致，没有日期筛选，也没有逐文件失败结果。

工具归档由 `ShellOutputArchive` 写入 `<archive_root>/naiyue-agent-output`，所有权文件为 `.naiyue-bash-output-v1`，另有固定 `.gitignore`。受管数据文件名为 32 位小写十六进制调用 ID 加 `-raw.log/-text.log/-stdout.log/-stderr.log`。项目内多会话共享根，普通会话各有根，子 Agent 继承父根。

新入口必须复用这些所有权规则和运行/后台任务保护；不能把旧 clear 接上按钮后宣称支持 7 天。

## 2. 文件与职责

以下路径是计划新增/修改，未声明已存在新实现。

| 文件 | 动作与职责 |
| --- | --- |
| `naiyue_test_agent/cache_cleanup_settings.py` | 新增四个配置字段及严格校验 |
| `naiyue_test_agent/settings.py` | 接入 AppSettings、TOML 白名单与加载 |
| `naiyue_test_agent/contracts/cache_cleanup.py` | 新增类别、请求、策略、结果和明细 schema |
| `naiyue_test_agent/application/projects/cache_cleanup_operation.py` | 应用层 `CacheCleanup`；准入、时间、范围顺序、预算及汇总 |
| `naiyue_test_agent/application/projects/cache_cleanup.py` | host 冷/热路径适配；不堆入已存在的 HostManager 主文件 |
| `naiyue_test_agent/application/projects/host_manager.py` | 增加内部 `cache_cleanup_scope` 入口，沿 G4 接适配器 |
| `naiyue_test_agent/application/projects/service.py`、`storage/project_registry.py` | 有界登记快照入口，不调用会访问源码目录的 `_view/availability` |
| `naiyue_test_agent/storage/cache_cleanup.py` | 只读、参数化读取会话状态和 S2 依赖事实；同一纯查询函数供冷热连接使用 |
| `naiyue_test_agent/tools/output_cleanup.py` | 新增按调用分组、时间筛选、身份复核及单组清理；不改旧 ClearPlan 形状 |
| `naiyue_test_agent/tools/output.py` | 复用既有所有权/命名和全局锁；必要时提供窄内部入口，旧公开函数行为保持 |
| `naiyue_test_agent/storage/cache_cleanup_files.py` | 归档、输入包、暂存共同需要的受管文件身份与删除原语；不建通用插件框架 |
| `naiyue_test_agent/application/sessions/session_service.py`、`session_persistence.py`（同目录） | 内部归档保护支持已验证、未加载的持久会话 ID，核对 S2 的活动/恢复事实并复用 A2→B1 的同根准入 |
| `naiyue_test_agent/application/service_application.py`、`application/core.py` | 构造并持有唯一 CacheCleanup，复用 root owner/settings/hosts |
| `naiyue_test_agent/service/cache_cleanup.py`、`service/app.py` | 新管理路由、显式授权和注入；不依赖当前 scope |
| `naiyue_test_agent/service/browser.py` | 新维护路径必填实例 header，在回执复用前拒绝缺失/错误实例；其他路由不变 |
| `naiyue_test_agent/service/receipts.py` | 原回执新增目标元数据和只读查询，producer/容量语义不变 |
| `docs/lock-hierarchy.md` | 同步实际新增预留与取锁顺序 |
| 配置示例与测试 | 见第 7 节；只在实施时按范围修改模板，不改真实 settings/.env |

本片超过 8 个文件，但不新增进程、网络服务或依赖。拆分是因为 HTTP、应用协调、受管文件规则和存储读取职责不同；每个新增模块须有上述真实消费者，不建立策略注册层。

## 3. 应用层接口

`CacheCleanup.policies()` 返回配置与已接入类别；`await CacheCleanup.execute(request_id, categories, *, result_limit_bytes)` 返回公共 `CleanupResult`。服务端 result_limit 来自原回执配置，不接受客户端覆盖。

`ProjectService.cache_cleanup_scopes(limit)` 提供公共合同所述根读取上下文，返回项目 ID、revision、lifecycle、database_path/source_kind；直接 SQL 限制 `limit+1`，检测超限，不先全量加载再截断，不调用 `_view`、源码目录 availability 或注册 reconcile。根 registry 冷读由 `storage/cache_cleanup.py` 复用原文件锁及只读打开模式，校验 `registry_database.APPLICATION_ID`、当前兼容的带 revision 格式（user_version 2—6）及必要字段；旧格式/未知格式明确要求先沿正常项目管理入口升级，不能借清理升级。其 OS 锁外置，SQLite 连接只在创建它的 worker 中使用和关闭；必要的下一次短读可在同一根锁下建立独立只读连接，不跨线程传递连接。普通会话和输入依赖查询同样有界；依赖集合未读完整时不得删除相应范围。

`HostManager.cache_cleanup_scope(scope_ref, category)` 是内部异步上下文：持 G4，核对登记；已打开 host 借用生命周期与数据库，未打开 host 使用一个受控 worker，在线程中完整持有只读连接和 OS 锁，执行数据收集与该范围文件 IO，再关闭。线程中的 RLock/SQLite 连接不可从另一个线程退出或使用。

`storage/cache_cleanup.py` 提供 `read_cleanup_scope(connection, expected_profile)`：校验 runtime 身份、读取持久会话 ID/状态。无迁移、无写事务、无会话反序列化、无模型加载。S2 在该模块增加输入依赖查询，不另开全库并行连接。复用 `locked_project_reader` 时覆盖其 SQLite URI 的中文、空格、百分号、问号及 Windows 盘符/UNC 路径：URI 必须正确转义，UNC 使用 SQLite 可接受的本机文件路径表达，不把 `Path.as_uri()` 的主机 authority 无条件传给不支持它的 SQLite 构建；必要适配写在现有 `storage/project_databases.py` 的读取入口并补其原调用回归，不另建无锁冷读路径。

项目库兼容性沿 `storage/backup_schema.py` 的受支持版本集合与本次所需列分别检查。flat 项目库可为 25，已迁移 Wiki 的项目库为 26；两类都应可读。根 registry 的 `PRAGMA user_version` 单独按 2—6 校验。正常打开项目的 `assemble_host → storage.memory.prepare_wiki` 可能备份并将 25 升至 26。冷清理直接读现有库；不调用上述准备入口，不读取 Wiki 正文，不创建备份。现有库无法证明项目/会话身份或缺少查询必需字段时，按公共合同报告原因并保留文件。

`output_cleanup` 的输入为服务端已验证的归档根、截止时间和受管上下文，输出为单元事实/结果；不接收任意 UI 文件清单。扫描与执行都调用同一命名/所有权规则。

## 4. 枚举与占用

1. 固定类别和 T，登记唯一清理操作；先枚举 registry 的仍登记项目身份，再普通会话。项目按 ID，普通会话按 session_id 顺序，保证结果可重复；受预算限制时如实报告未完成。
2. 每个项目只访问 `<data_dir>/workspaces/<id>/artifacts` 与它的受管 DB；不检查源码目录是否在线作为删除前提。项目已删除登记/状态改变/revision 改变则跳过。
3. 普通会话来自 chats DB；无 DB 无归档为零，缺库却有残留目录不猜来源。不给旧会话创建 actor；已删除会话的孤立目录不扩展扫描。
4. 热 host 同时核对与该归档根相关的 `SessionPersistence._active/_recovering/_blocked` 和 actor 的 busy/approval/manual-review/子实例状态。持久层短锁 S2 可以进入 A2，A2 分别进入 S3 读 actor、进入 B1 的 `output_guard` 登记占用。新建同根恢复或删除预留也须在 S2 下与此登记互斥，再释放短锁做 IO。未加载的普通会话先由 DB 验证 ID，按 `session_archive_root` 导出归档根，沿同一保护路径处理。
5. 冷 host 在 G4 + DB OS 锁下，若持久会话为 `running/interrupted/review_required` 或存在必要恢复状态，整个共享归档根跳过。热 host 同样检查持久恢复状态，不能只看已加载 actor。
6. 保护期间后台任务注册/新运行继续沿原 B1 清理标记拒绝该根的准入；已经运行的任务使清理跳过。其他项目运行和消息发送不受全局清理标记限制。
7. 当前后台输出查询读取内存快照并通过 archive.available 检查文件；保持其已有锁顺序，不在 `_GLOBAL_LOCK` 内回调持 archive 私有锁的方法。正常 Agent 工具读取受同根活动运行保护。

收尾例子：一次对话已经通过 `storage.runs.finish_run` 将 DB 状态写为 `ready`，同一个 run 仍可能停在 `SessionPersistence.afinish → session_maintenance.after_finish`。后者处理 checkpoint 保留和记忆过期，返回后才释放持久层与 actor 的运行占用。此时用户点击清理，返回该根 `scope_busy`；独立项目仍可继续。手动清理不调用这些会话维护钩子。

G4 防止本服务打开/关闭/移除/重定位该 host；登记提交前复核是第二道检查，不靠扫描时快照删除。跨进程遵守已有 DB 文件锁，无法取得视为忙碌。不要为清理强制关闭外部进程。

## 5. 工具归档算法

### 5.1 受管单元

- 校验归档目录、父目录身份、固定 marker 内容和 `.gitignore`；没有目录则零结果，不创建它。
- 数据文件必须符合现有 `_NAME`，是普通单链接文件且非重解析点。未知文件、子目录、symlink、marker 损坏：该归档根不删除，返回具体路径和原因。
- 按 32 位调用 ID 分组。一个调用的 raw/text/stdout/stderr 任一较新则整组保留。旧格式允许只有 stdout 或 stderr，不能假定一定有 raw。
- 截止时间严格按公共合同；未过期计 retained，不生成成千上万条逐文件保留详情。
- 枚举全根的所有权/名字一致性后才进入该根删除；达到扫描预算而未验证完，整根不开始删除。

### 5.2 删除

1. 每组执行前重新检查根、marker、组成员、dev/ino/size/mtime；新增成员或身份变化则跳过，不删其余成员。
2. 同步 `_GLOBAL_LOCK` 只覆盖必要的单组复核/删除，不包住全机遍历；保持 writer 与旧 CLI clear 的互斥关系。
3. 在公共文件原语中固定父目录，关闭校验文件句柄，逐个删除本组文件。任何文件首次失败，停止该组，记录原因；其他独立组继续。
4. 每次实际删除后增加确认的文件/字节数。FileNotFound 表示已被别人移除，记 skipped，不增加计数。
5. 删除后不删除归档根、marker、`.gitignore`；后续工具输出仍沿原逻辑写入。
6. 部分删除不承诺回滚。剩余文件仍符合按调用 ID 分组的格式，下次手动清理可以继续；聊天正文和保留下来的短工具结果不重写。

这里不新建归档副本、回收站或压缩包，不改写输出生产逻辑。恢复需要的活动归档事先保护，而不是删除后尝试重建。

## 6. 正常与失败调用链

正常：窗口发送 tool_outputs → 鉴权/原回执 producer → CacheCleanup 固定 T → P1/P2/chats 枚举 → P2 冷路径只读确认无恢复 → `output_cleanup` 找到一个 9 天前的 raw/text 组 → 复核并删除两文件 → 汇总返回 → 同 ID GET 得到相同结果。断言没有构造模型、没有打开新的 ProjectHost、未写历史 DB。

失败：P1 正在执行命令 → output_guard 拒绝 → 记录 `scope_busy` → P2 两文件中第一项已删、第二项返回 WinError 32 → 该组 failed、如实保留第一项计数 → P3 继续 → HTTP 200/status=partial。前端展示 P1 原因和 P2 文件/错误码；会话仍运行，清理 finally 释放 G4/B1/句柄/全局准入。

请求级异常：registry 读失败或根身份丢失 → 不把范围当空 → 停止相应层，构造 failed/partial 结果；已发生副作用保留。错误日志只记录稳定身份与安全类型。

## 7. TDD 与验收

先新增失败测试并确认失败原因，再最小实现。计划测试文件：

- `tests/test_cache_cleanup_settings.py`：默认值、覆盖、非法类型、TOML 接线。
- `tests/test_cache_cleanup_outputs.py`：期限、组、识别、部分失败和计数。
- `tests/test_cache_cleanup_scopes.py`：冷热 host、全本机范围、身份/移除竞争、恢复保护；用事件暂停 `after_finish`，断言 DB 为 ready 时仍跳过本根，完成后再次手动执行才可清理。另用冷 flat 25/Wiki 26 及未知版本验证兼容性，断言无迁移、无新备份；同根恢复与清理两种准入顺序都验证。
- `tests/test_service_cache_cleanup.py`：鉴权、实例、去重、查询、请求失联、容量、异常。
- `tests/test_service_receipts.py`：原命令行为及新增目标信息释放回归。

必测：7 天严格边界；同组一新一旧；future/低精度mtime；多个会话共享项目归档只删一次；未打开项目；未加载普通会话；已移除登记；未产生缓存；缺库/坏库；窗口打开无 IO；正在写/后台输出未关闭；扫描后身份改变；未知文件；只读/共享冲突；部分删除后继续其他项目；重复请求与错 ID；关闭和取消释放；结果预算先停后续操作；旧 CLI 全量清理无语义变化。

命令（实施时执行，本设计阶段未运行）：

```bash
.venv/bin/python -m pytest tests/test_cache_cleanup_settings.py tests/test_cache_cleanup_outputs.py tests/test_cache_cleanup_scopes.py tests/test_service_cache_cleanup.py tests/test_service_receipts.py tests/test_shell_output.py tests/test_bash_output.py tests/test_execution_root_archives.py -q
```

在现行 `docs/examples/settings.example.toml` 和 `docs/examples/full/settings.full.toml` 增补 `[cache_cleanup]`。两份模板都沿正常 `load_settings` 加载，验证清理配置和既有 Wiki 配置可同时生效。收尾检查新增文件 L1 存在、L2 真实实现、L3 GET/POST/GET 查询实际接线，并将锁顺序记录到当前锁表。完整回归和原生 Windows 验收见 S4。
