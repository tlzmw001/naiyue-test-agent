# Spec143BP-00：本机手动缓存清理公共合同

日期：2026-11-18。状态：详细实施设计，未实现。业务范围已经用户确认；本文件规定 S1—S4 共同遵守的接口、状态、时间和故障语义。总入口见 [143BP](143bp-local-settings-and-cache-cleanup.md)。云端修改不属于这些切片。

## 1. 范围与最小路径

只新增本机服务内的两类显式清理：`tool_outputs`（默认保留 7 天）和 `cloud_inputs`（默认保留 3 天）。全部九项拷贝优化暂缓。

用户打开齿轮菜单与类别窗口不会扫描或删除文件。窗口读取服务端策略；用户点击“清理所选”后，服务端固定时间、枚举本机范围、复核、删除并返回真实结果。首版不做单独预览接口、不保存候选删除计划、不增加逐文件确认。这样无需维护会过期的预览快照；文件身份仍在实际删除前复核。

```text
ProjectWorkspace（唯一窗口、请求状态）
  → WebApi（全局 service 路由）
  → service/cache_cleanup.py（鉴权、合同转换、查询）
  → CommandReceipts（已有请求去重、独立 producer、最终回执）
  → CacheCleanup（应用层范围、策略与结果编排）
  → HostManager 的逐 host 生命周期门闩
    ├─ 已打开：借用现有 DB、会话和缓存占用保护
    └─ 未打开：现有文件锁 + 只读 DB，不创建 Agent
  → 工具归档处理器 / 输入缓存处理器
  → 受管文件身份复核和文件系统操作
```

本功能不调用模型、不发云端 HTTP、不启动项目 Agent、不触发数据库升级或恢复，不改聊天、图片、checkpoint、记忆、日志、项目资产或云端结果。原 CLI 全量归档清理入口及其确认合同保持。

## 2. 配置与时间

新增 `CacheCleanupSettings`，由 `AppSettings.cache_cleanup` 持有；沿现有 TOML 加载链读取 `[cache_cleanup]`，不得只定义字段而遗漏 `load_settings` 顶层允许项和返回值。

| 字段 | 默认值 | 校验与用途 |
| --- | --- | --- |
| `tool_output_retention_days` | 7 | 严格正整数；工具归档保留天数 |
| `cloud_input_retention_days` | 3 | 严格正整数；本机输入缓存保留天数 |
| `max_scan_entries` | 100000 | 严格正整数；单次目录项、DB 行与受管文件检查的总预算 |
| `max_operation_seconds` | 60 | 有限正数；单次操作在安全单元边界检查的软时限 |

后两项是本次实施设计的资源保护默认值，不是性能测试结果。预算只限制一次用户操作，不产生后台续跑。配置无效时启动明确报错，不静默改成更短保留期限。首版窗口不提供期限编辑；前端展示 GET 返回的实际配置，不另写 7/3 常量。

- POST 准入时固定 `started_at_ns = time.time_ns()`；整个操作复用同一 `T`，各类别 `cutoff = T - days × 86400 × 10^9`。
- 比较 `st_mtime_ns < cutoff`，相等、未来时间均保留。一个删除单元以其中原有受管数据文件 mtime 最大值判断；目录、锁、所有权标记、新建清理控制文件不参与。
- 不用 ctime、创建时间、浏览器时间、自然日或本地日期字符串；不通过读取/复用触碰 mtime 续期。
- 文件系统时间精度以实际返回值为准。属性读取失败是错误，不能按零时间处理。
- 预算使用 monotonic 时钟；`max_operation_seconds` 不是强行取消 Windows 系统调用的承诺。已进入的安全单元先结算，未进入的停止。超预算报告 `scan_budget` / `time_budget`，不得称为扫描完成。
- `max_scan_entries` 在枚举过程中逐项计数；不先无界 `list()` 后检查，不递归扫描任意磁盘树。

## 3. 外部 HTTP 合同与消费者

唯一新增产品消费者是本机 Web 窗口。路径位于 `/api/v1/maintenance/cache-cleanup`，与 `scopes/{scope_id}` 无关。

### 3.1 GET `/api/v1/maintenance/cache-cleanup`

返回：

```json
{
  "scope": "registered_projects_and_persistent_chats",
  "categories": [
    {"id": "tool_outputs", "retention_days": 7, "available": true},
    {"id": "cloud_inputs", "retention_days": 3, "available": true}
  ]
}
```

以上数值仅为默认配置下的合同示例，产品从配置生成。S1 接线期间 `cloud_inputs.available=false`；S2 完成后启用。该接口不枚举文件，不读取云端连接密钥，也不要求当前项目或会话可用。

### 3.2 POST `/api/v1/maintenance/cache-cleanup`

请求体为严格 schema：`{request_id: UUID, categories: ["tool_outputs" | "cloud_inputs"]}`。类别非空、无重复；固定顺序 `tool_outputs`、`cloud_inputs`，前端规范化后发送。禁止客户端传根目录、项目列表、截止时间、文件清单、保留天数。未知字段拒绝。`X-Request-ID` 必须与 body 一致，`X-Service-Instance` 必填且由业务路由再校验当前实例。

沿已有 `IdempotentCommands` 执行：同实例同 ID/同请求只运行一次；同 ID 不同内容 409 `request_id_conflict`。同时只接受一个实际清理操作；另一个 ID 返回 409 `cache_cleanup_busy`，既有 ID 查询/重复读取原回执不受阻。不会为清理串行其他项目的聊天运行。

POST 等待最终结果；浏览器断线不取消独立 producer。已进入应用层的操作返回 HTTP 200 + `CleanupResult`，包括部分成功、扫描失败与内部异常；格式、鉴权、容量和准入拒绝按常规 4xx/503 返回，不伪造结果。应用层在已有生命周期登记操作，所有 IO 经 `run_blocking`。

### 3.3 GET `/api/v1/maintenance/cache-cleanup/{request_id}`

只查询原请求，不执行删除：

- 200 `{state: "running", request_id}`：原 producer 尚未完成。
- 200 `{state: "finished", request_id, http_status, result?, error?}`：从同一回执读取已完成结果或原准入错误。
- 404 `cache_cleanup_request_not_found`：本实例没有匹配此方法/路由的 ID；不是“确认没删”。
- 实例不匹配沿既有实例拒绝码；UI 显示原操作结果无法核实，不自动换 ID 重发。

复用 `CommandReceipts.entries` 中的任务与响应字节，不另建任务调度器、持久操作表或后台作业服务。为查询在现有回执旁保存受限的 `(method, path)` 目标信息，并提供 `lookup(request_id, target)`；不能拿其他命令 ID 读成清理结果。新信息在拒绝释放、`forget_scope` 和实例结束时与回执一同释放，遵守原 `max_receipts/receipt_cache_bytes/max_frame_bytes` 容量。

无需跨重启重放：回执随服务实例消失，重启后旧结果未知；再次清理由用户明确点击，以新的时间和身份复核执行。启动时不继续未完成删除。

### 3.4 鉴权与错误边界

三个入口均使用现有本机 token、Origin 与实例校验；写接口沿中间件鉴权后才登记 ID，路由显式调用本机管理入口校验，不只依赖全局依赖注入。GET 查询同样校验。此新路径的必填实例检查必须在回执查找/复用之前执行，不能只在第一次进入路由时校验而让重复请求绕过；沿 BrowserBoundary 的实例检查增加本路径的必填限制，其他路由合同不变。无鉴权、错误实例、不支持类别或非法请求不得产生目录枚举/删除。

## 4. 结果与统计

`CleanupResult` 字段：

| 字段 | 含义 |
| --- | --- |
| `request_id`, `scope` | 原请求与固定本机范围 |
| `started_at_ns`, `finished_at_ns` | JSON 十进制字符串，避免 JS 纳秒整数精度丢失 |
| `policies` | 本次各类别的天数与 `cutoff_ns` 字符串 |
| `status` | `completed` / `partial` / `failed` |
| `enumeration_complete` | 所选类别和范围是否全部遍历完 |
| `stop_reason` | `null` / `scan_budget` / `time_budget` / `result_budget` / `service_closing` / `root_unavailable` / `internal_error` |
| `totals`, `categories` | 总数和逐类别计数，不按项目无界堆积成功记录 |
| `details` | 跳过、失败、结果无法确认的可定位明细 |

计数字段：`scopes_visited`, `units_examined`, `units_deleted`, `files_deleted`, `bytes_deleted`, `units_retained`, `units_skipped`, `units_failed`。计数均非负，类别求和与总计一致；scope 计数是“范围×类别”的访问次数，不冒充独立项目总数；`bytes_deleted` 使用十进制字符串。一个包部分删除可以贡献 `files_deleted/bytes_deleted`，但不贡献 `units_deleted`。目录不计字节，新建控制文件不冒充用户释放的缓存量。

- `completed`：遍历结束，全部适用单元已处理，无跳过或错误。全部数据都未过期、目录从未产生缓存，也可以 completed + 零删除。
- `partial`：存在保护/变化跳过、单元失败或预算停止，但有范围完成检查；零删除也可以 partial。
- `failed`：全局枚举或请求级执行故障使处理不能继续。已经确认的删除数仍保留；不能把部分副作用归零。
- `units_retained` 用于未过期的正常保留；活动、未知格式、身份变化等用 skipped/failed 明细，不混成“没有缓存”。

每条 detail 包含 `category`, `scope_kind(project|chat|service)`, `project_id?`, `session_id?`, `unit_id?`, `path`, `step`, `outcome(skipped|failed|unconfirmed)`, `code`, `reason`, `hint`, `errno?`, `winerror?`。`path` 由已验证根目录构造，只用于认证后的界面定位；不能携带日志正文、文件内容、token 或连接配置。无安全路径时填写已经验证的最近父目录，禁止输出可疑外部目标。

固定步骤：`enumerate`, `read_metadata`, `check_dependencies`, `reserve`, `revalidate`, `retire`, `unlink`, `rmdir`, `sync`, `finalize`。错误先分类再生成中文说明；UI 不只显示一个总失败数。

| code | 展示/处理方向 |
| --- | --- |
| `scope_busy`, `input_cache_busy`, `recovery_required`, `legacy_delivery_dependency` | 暂时保留，说明依赖；用户完成/取消原工作后再清理 |
| `registration_changed`, `identity_changed`, `already_missing` | 本次跳过，重新检查；外部已删文件不计本次空间 |
| `permission_denied`, `sharing_violation`, `readonly_file` | 定位文件与错误码；提示检查权限/只读/占用，不猜具体进程 |
| `unexpected_entry`, `ownership_invalid`, `unsupported_schema` | 保留并暴露识别或格式问题，不能当成空缓存 |
| `io_error`, `metadata_unavailable`, `cleanup_internal_error` | 保留未处理部分并展示诊断 ID/类型，服务继续工作 |

响应预算取既有 `max_frame_bytes`，包含最终摘要及 GET 查询包裹结构。执行前为本单元最坏一条失败明细和最终摘要预留编码后的字节数；不足则停止后续单元、返回 `result_budget`。每单元遇第一个失败即停，其他问题待再次手动扫描；不通过截断已发生的错误掩盖失败。读取阶段也受同样的明细预算限制。前端可分组折叠已返回的明细，无需新增持久错误日志库。

## 5. 范围所有权与生命周期

- 当前服务的 `settings.storage.data_dir` 为唯一根；路径由 registry 及 chats DB 的身份导出，不接受浏览器路径。
- registry 仅取仍登记项目；`deleting` 等未完成状态显示跳过。包括未打开、原工作目录暂时离线的登记项目；清理管理目录不需要访问项目源码目录。
- 根 registry 已打开时借用其连接做短读；尚未打开但文件存在时，以原 registry OS 锁和只读连接建立清理读取上下文，校验 application_id、支持的 user_version 与字段，不调用会迁移/执行 reconcile 的惰性 property。此根文件锁先于所有项目锁取得并保留到操作结束；正常项目注册入口若在此期间竞争，沿已有非阻塞忙碌结果返回。根 registry 不存在时项目集合为空，仍可检查 chats，不创建 registry。不可验证的已有 registry 是全局枚举失败。
- 项目 DB/归档路径必须匹配本数据根的受管布局和登记身份；迁移中、外部遗留位置或不匹配时跳过，不能跟随任意数据库路径清理。
- 普通会话取 `chats/sessions.sqlite` 内仍存在的持久会话身份，只处理相应 `sessions/<session_id>/artifacts`；子 Agent 复用父归档，不重复计数。临时/RAM 会话和没有登记来源的孤立目录不扫描。
- 每个 host 单独取得既有 G4 生命周期门闩，复核登记 revision 与数据目录身份，再借用当前 host 或打开只读检查。禁止跨项目同时持有多个 G4。
- 冷路径复用 `locked_project_reader` 与 `validate_runtime_connection`，只读查询所需列，持有该 DB 的 OS 锁到该范围结束；不经 `ProjectStore/Database` 的正常初始化路径，后者会触发迁移。
- 热路径借用已有 DB，短事务读状态；不能再打开第二个需要相同 OS 独占锁的 ProjectStore。事务结束后再进行磁盘扫描/删除。
- 缺少 DB 但出现缓存、格式无法识别、恢复标记存在、状态不明：范围级失败/保护跳过；不按“没有引用”删除。DB 与缓存都不存在才是空范围。

## 6. 并发与关闭

全局 single-flight 是事件循环中的清理准入标记，无 await 修改，只阻止另一轮清理。G4 保护 host 开关/项目变化；活动保护由各类别现有 owner 加本片明确增加的预留承担，不拿全局互斥锁包住全部磁盘 IO。

必须在首次实施时同步 [lock-hierarchy.md](../lock-hierarchy.md)：记录清理准入标记、G4、A2/B1、DB、归档全局锁、输入缓存原独占锁和新增读租约锁的顺序、持有线程、跨 IO 范围。短 DB/RLock 不跨网络或 await。未注册消费者的预留不算实现。

| 路径 | 具体顺序与限制 |
| --- | --- |
| 范围枚举 | 清理事件循环标记 → 根 registry 读取上下文；已有连接短取 P1/registry DB，读完即释放短锁；冷 registry 的 OS 锁是整个操作的外层，先于 G4 和项目 OS 锁 |
| 热归档 | G4 → A2→B1，短锁登记后释放；保持 B1 清理预留，worker 逐组取归档全局锁；不从归档锁回调 A2/B1 |
| 冷归档 | G4 → 既有 DB OS 文件锁 → 只读查询；关闭查询事务后保留 OS 锁做文件 IO，不从 OS 锁反取 P1 |
| 输入缓存 | G4 → domain 清理预留（若热）→ cache.lock → readers.lock 独占；随后短 DB 依赖复核，事务结束后再删除；读者不反取 cache.lock |

执行前的登记再次核验通过已选定的根读取上下文读取身份/revision，热连接沿 RegisteredExecution 已有的 DB 读取方式，冷连接沿操作开始时已取得的根锁，不从项目锁反取根锁/P1，也不调用 reconcile。冷 SQLite 连接的进入、查询与退出必须在同一个受控 worker 中完成；不得把线程绑定连接交给随机线程池调用。跨 await 仅保留明确登记的 OS 锁/占用预留，查询事务均已关闭。

正常关闭先封清理准入，在安全单元边界停止、结算已完成操作，释放全部资源后退出已有生命周期登记。浏览器关闭窗口不发取消；服务退出不自动删除新的对象。异常、取消、部分删除都走 finally 释放，不捕获 BaseException 伪装成功；必要的线程收尾沿 `await_settled/run_blocking`。

## 7. 文件安全与 Windows 合同

- 沿 `storage/files.py` 的所有权、单链接、文件类型、路径检查，目录层使用已有 POSIX no-follow FD 和 Windows `directory_handle/signature` 方式固定父目录；不先 resolve 再把链接当普通目录。
- 枚举与每个删除单元执行前核对身份；POSIX 基于父 FD 定位名字，Windows 固定父目录句柄并重新核对路径。文件 dev/ino/size/mtime 与当前实现的 `changed_store_fields` 语义一致，不把 Windows 句柄/路径 ctime 差异当成替换。
- 支持 `windows_api_path` 的盘符与 UNC 长路径；关闭本次不再需要的目标文件句柄再删除，删除/移动子目录前释放阻止该操作的自身句柄，保留父目录保护。
- 不跟随 symlink/junction/reparse point，不递归删除未知条目，不自动改 ACL/只读属性、不关闭用户程序、不提权。
- 删除不套用输入发布 30 秒重试；共享冲突明确报告。用户处理后再次清理，重新检查状态。
- POSIX 删除与必要元数据发布使用既有目录同步；Windows 不对只读文件句柄 fsync，不强行使用 POSIX dir_fd。同步失败区分“名字已删除”与“持久化未确认”，不能把前者撤销成未发生，也不能宣布完整成功。
- 文件 unlink 成功且确认原名字已消失才计入本次逻辑删除字节；不宣称磁盘块已释放。失败或结果不确定不计成功；回执不会读取文件内容作诊断。

## 8. 当前证据与边界

本设计沿当前源码完成静态调用链检查，未执行删除或功能验收。标准库与框架选择沿项目现状，无新依赖、密钥或第三方账号；无发布/部署步骤。

- [Microsoft DeleteFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-deletefilew)：只读属性、共享句柄和长路径约束支撑错误分类；不据此承诺能够强制删除占用文件。
- [Python 3.12 os.stat_result](https://docs.python.org/3.12/library/os.html#os.stat_result)：mtime 与纳秒整数接口、文件系统精度；测试覆盖低精度和未来时间。
- [SQLite URI](https://www.sqlite.org/uri.html)：冷路径 `mode=ro` 沿现有实现；不对可能变化的在线库使用 `immutable=1`。
- 本机安装 `langchain==1.4.2`、`langchain-core==1.6.6`；[官方 Agent 文档](https://docs.langchain.com/oss/python/langchain/agents)说明模型/工具运行职责。本功能属于确定性的本机管理流程，不改 create_agent、middleware 或 checkpoint 合同。
- Pi 本地 `a69bef789`：`core/tools/bash.ts → OutputAccumulator.snapshot → fullOutputPath`，关闭临时输出流和保留供读取的路径是不同操作；这条链不证明其有本机 7 天清理。
- AgentX 本地 `3fcfa0e`：`ToolStateStateMachineAppService.processProcessor → AppPublishingProcessor.process → finally cleanupTemporaryFiles`，临时发布材料失败清理记录警告；这是任务临时文件，不等于本项目历史归档。参照对象生命周期划分，不搬它的定时容器清理或 Java API。

最容易失效的前提是“所有缓存消费者都参加占用保护”。S2 必须核对服务、CLI、上传恢复、旧交付全链路；未识别的缓存格式或依赖保守保留并说明。不能靠一张 `input_id` 引用计数表替代真实消费者核对。
