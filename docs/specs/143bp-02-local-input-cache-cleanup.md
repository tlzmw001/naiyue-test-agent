# Spec143BP-S2：本机云输入包缓存清理

2026-11-19 已接通本机云输入类别、持久依赖检查和整包退役。Windows 原生验收归 S4。实现按 [Spec143BQ 已接入的文件取回及云端回收](143bq-cloud-results-and-input-reclamation.md#9-实施状态)复核。前置：S1 与 [公共合同](143bp-00-contracts.md)。只处理本机项目 DB 旁的 `cloud-input-cache`，不访问 Coordinator，不改变捕获/打包/传输的内容与副本数量。

## 1. 当前数据与消费者

```text
LocalTaskDomain.capture
  → capture_cached（cache.lock，冻结/打包，命中检查，发布）
  → LocalTaskRepo.add_source（source_handle → captured）

LocalAttachments.launch / preparation.prepare（带 source_handle）
  → 根据 input_id 和操作 ID 获取云端 input use
  → CloudClient.upload(captured.directory) → 取得包内上传锁 → input_upload.upload
  → 云端命中或续传/提交 → 任务/附件关联 → 本机状态保存

LocalAttachments.action(cancel) / settle_retired（完整删除任务后）
  → 等待原附件 producer 排空 → _release_input → CloudClient.cancel_attachment_input
  → 释放云端 use → 从本机 journal 找原传输命令 → 核对并按需取消 transfer
  → 成功落库 cancelled / 清掉该条 pending_attachment_releases

nyt cloud input capture --cache / nyt cloud input upload
  → capture_cached / CloudClient.upload
nyt cloud task create
  → 读取请求 JSON，按 input_id 在云端获取 use 并关联任务
```

当前 `capture_cached` 持有根 `cache.lock`。上传另取包内锁，取消附件会读取同包 journal。这两个入口都需要与本机清理互斥。多个附件可共享同一输入包，须核对每个未完成操作。

BQ 已将 apply/branch/export 的准备、执行与重放入口退役。旧交付仍可查看写入范围，私有日志/备份位于项目 DB 旁的 `cloud-results/deliveries/<command_id>/`，文件保存入口也仍核对旧目的地冲突。历史 receive 中的 `source_handle/input_package` 和 `branch_available` 所用的来源回执路径字符串属记录元数据。当前文件取回从 receive/blob 保存产物，沿这条链没有本机输入包读取。

| 本机条目 | 当前职责 | 本片处理 |
| --- | --- | --- |
| `cache.json`, `cache.lock` | 根所有权、捕获互斥 | 永久保留，不参与年龄判断 |
| `inputs/<input_id>/archive` | 已冻结压缩包 | 与同包文件一起判断/退役 |
| `input.json`, `manifest.json`, `selection.json`, `receipt.json` | 包身份与捕获事实 | 与 archive 组成一包 |
| `upload-<instance_id>[-<upload_id>].json` | 上传命令 ID/续传 journal | 与所属包一起保护与回收；当前生产者独占创建、写入后 fsync |
| `upload.lock`、`upload-<attachment UUID>.lock` | 包内按上传身份互斥，正常结束后锁文件仍在 | 与包一起退役并删除，锁文件不计有效 mtime 或已删除的数据文件数、字节数 |
| `imports/<UUID>.json` | 独立本机来源回执 | 单独按 mtime 和引用判断；不读取其路径去删除项目文件 |
| `staging/capture-*` | 捕获临时树，正常由 TemporaryDirectory 收尾 | 只处理可完整识别、无活动捕获的遗留格式 |
| `readers.lock`（新增） | 跨进程缓存读取租约 | 保留，不参与年龄判断 |
| `staging/cleanup-<UUID>/`（新增） | 已退役待删包及最小清理记录 | 下次用户手动清理才续办，不自动重试 |

当前 marker 合同为 `{"format":"nyt-input-cache","schema":1}`。本片保留该合同，增加受管锁和暂存项；初始化半成品/未知 marker 不自动修复。正常捕获计额仍必须计入未实际删除的暂存字节。

## 2. 文件与接线

| 文件 | 工作 |
| --- | --- |
| `cloud_files/cache_cleanup.py` | 新增受管包/回执/暂存枚举、年龄判断、退役与清理；使用 S1 文件原语 |
| `cloud_files/cache_access.py` | 新增缓存所有权及共享读取租约入口；复用原 cache.lock 实现，不建全局锁注册服务 |
| `cloud_files/cache.py` | 原捕获锁/marker 接入共享所有权代码，识别新增受管文件；捕获顺序和命中时机保持 |
| `cloud_files/client.py`、`cloud_files/input_upload.py` | 受管包的 `CloudClient.upload` 和 `cancel_attachment_input` 在入口取得共享租约。覆盖原上传锁和完整上传/取消过程，沿用现有 journal 及云端 use 协议。独立捕获目录沿原路径 |
| `application/cloud_local/tasks.py` | LocalTaskDomain 持有短状态 `cache_users/cache_cleaning`，提供 `input_cache_use/cleanup_guard`；capture 覆盖发布到 add_source 间隙 |
| `application/cloud_local/tasks.py` 的 submit/retry、`preparation.py`、`attachments.py` | 创建和附件的准入、实际 producer 及取消/退役收尾参加同一 domain 保护，覆盖首次缓存接触到必要状态落库。forget 释放附件、原删除命令显式重试复用同一取消入口 |
| `storage/cache_cleanup.py` | 增加只读依赖汇总，沿实际表/JSON字段，不改业务记录 |
| `application/projects/cache_cleanup.py`、`application/projects/cache_cleanup_operation.py` | 接云输入处理器、生命周期、预算；policies 开启 cloud_inputs |

本服务活动预留保护捕获到落库及异步生产者交接。缓存根的共享读租约协调服务上传、取消恢复、独立 CLI 上传与清理。已有包内锁继续负责同一上传身份的互斥。

## 3. 持久依赖规则

从同项目 DB 的 `naiyue_cloud_operations`、`naiyue_cloud_bindings` 和 `naiyue_cloud_sources` 读取创建及附件依赖，包含已退役绑定；为核对旧未终结交付再读取 `naiyue_cloud_deliveries` 及其关联的 `naiyue_cloud_receives`。参数化 SQL，先校验 schema/身份。输出 `protected_input_ids`, `protected_import_paths` 及稳定原因；它们是本次快照，最终删除前在已取得占用保护后复核。字段无法解析或关联缺失时保留受影响缓存范围并报错，不能把坏数据当零引用。

| 生产记录/状态 | 判定 |
| --- | --- |
| create operation `phase` 不是 `prepared/cancelled`，且 request 有 source_handle | reserved/project/upload/task/environment/failed/unknown 均仍可继续，保护输入包和来源回执 |
| create 已 cancelled，原 producer 正在排空，或尚需核实 may_have_task 且没有已确认 task_id | 沿现有取消/恢复边界保守保留。已确认任务的历史 may_have_task 单值不构成输入依赖；仅按操作 ID 解除云端 use 不读取本机包 |
| `local_attachments` 为 uploading/failed 且已取得 input_id/source_handle | 可续传/重试，保护对应整包。尚未捕获时没有包 ID，由活动预留覆盖捕获、保存 source 及回写附件之间的间隙 |
| `local_attachments` 为 cancelling，或历史 cancelled、有 input_id 但缺少已解除凭据 | 按现有 `pending_input_releases` 语义，保护 input_id 对应整包直到原取消完成并持久化 `input_use_released=true`。优先使用已保存的 ID，不要求仍有 source_handle |
| `retained=false` 的 binding 带 `pending_attachment_releases={attachment_id: input_id}` | 即便 `local_attachments={}` 仍逐条保护对应整包，直到 `release_retired` 成功落库。多个 aid 共用一个 ID 时按剩余依赖判定 |
| attachment 为 ready/attached，或 cancelled 且已确认解除、producer 已排空 | 该附件无需续传或按 journal 结束未完成传输，单独不阻挡旧包清理。清除前仍复核同 ID 的所有其他消费者 |
| prepared 绑定的 input_id、历史 source/receive、只到预检或已终结的旧交付及基线 | 单独不阻挡清理。原记录、私有证据与用户保存的项目文件留给原功能管理 |
| 旧 apply/branch/export 仍为 executing/partial/unknown | 按 BQ-02 §4.2 核对人工恢复对本机包及来源回执的实际依赖，保护命中的单元；证据缺失时按下述规则保留 |

旧写入的恢复核对沿 [BQ-02 §4.2](143bq-02-result-ui-and-analysis-retirement.md#42-升级时正在写入的旧交付) 执行：

1. 从旧交付的项目、会话、command_id、receive_id 进入对应 receive。用 receive 的 binding_id、input_id、source_handle 与同项目会话的 binding、source 交叉确认，得出 `inputs/<id>` 和 source 中指向受管 `imports/<UUID>.json` 的回执。只对未终结且可能已经写入的旧动作走该链。
2. 只读核对既有恢复材料：旧 apply 的 `apply/operation.json` 与按序号保存的备份、export 的 `export/export.json`、branch 的 `branch.json`，以及旧流程可能单独下载到该 command 目录的 `archive`。路径从验证过的内部根和 command_id 构造，受单元扫描预算约束。判定例：旧 export 部分写入，且原包是唯一可证实的基线时保留 I；若私有 archive 经该 receive 的大小及 SHA-256 核对可独立提供同一基线，则这份原包可解除本项保护。旧 branch 若仍需原来源回执，单独保留对应 import。日志是否存在、done 列表、目的地守卫本身均不足以证明基线可释放。
3. 无法证明恢复材料独立完整时，对可定位的 I 或 import 保留并报告 `recovery_required`，给出原 command_id 和诊断位置。只能定位到某会话的候选时保护其全部可能关联单元，项目内无法界定时跳过该项目输入范围。不得从坏行推断零依赖。核对不创建材料目录、不会重放旧动作，也不从用户项目或云端取数。可证明与此恢复无关的单元仍按年龄处理。

其他 DB 依赖跳过记为 `input_in_use`，带上原因（例如附件仍待解除）。完整删除任务会在同一事务将待解除映射存入退役绑定，并清空原附件列表。不能用 retained 过滤、任务已删或某个附件已结束推断整个包可清。坏映射、跨范围身份或已知必需关联无法验证，保留可能受影响单元并报告；已按旧协议合法缺少的可选字段按对应旧记录语义处理。

新提交引用已过期且清掉的 source_handle 时，在域预留内先按已登记 source 的受管身份检查包，再登记或开始新云端工作，返回明确 `input_cache_expired`（重新选择来源）。已完成命令的重复提交沿原回执返回。该错误只用于经过所有权核验的本机缓存缺失；权限、损坏、未知路径仍各自报错。正常 source_handle 格式和外部请求 schema 不变。

不删除 sources/bindings/attachments/receives/deliveries 历史行，不修改用户附件名、任务状态或 source 路径；本片不做 DB 迁移。过期缓存与业务事实分离。

## 4. 占用保护与锁顺序

### 4.1 本服务的 domain 预留

- `input_cache_use` 在事件循环中先检查 cache_cleaning 再增加使用计数，finally 减少；可嵌套，覆盖全部 await/线程 IO 到必要 DB 结果保存。
- `cleanup_guard` 在没有使用者时原子设置 cache_cleaning；已有使用者使清理跳过整个项目输入类别，已有工作不被取消。
- 新建/重试须在建立持久依赖前取得预留，从提交协程交给后台工作时由计数或已提交的 DB 依赖连续保护，实际 producer 排空、必要保存完成后释放。附件捕获拿到 source 后至回写 input_id 的间隙同样受保护。清理已入场时返回 `input_cache_busy`。无输入的建任务、普通会话和读取已完成操作沿原有路径。
- `action(cancel)`、forget 复用的附件取消、完整删除成功后的 `settle_retired` 及其显式重试，均需覆盖 journal 读取与最终状态落库。清理占用或网络断开时保留可重试状态。单纯查询原删除回执只读。
- 预留只保护该项目缓存，不全机互斥其他项目。当前 UI 的运行后续、任务状态查询不被当成读缓存。
- 冷 host 由 G4 + DB OS 锁保证本服务不会同时创建 domain；跨进程读者仍靠下面的锁保护。

### 4.2 跨进程共享读锁

沿原 cache.lock 的非阻塞独占捕获锁不变；新增固定 `readers.lock`，只在根所有权已验证后创建。

| 操作 | 锁 |
| --- | --- |
| capture_cached | 原 cache.lock 独占，覆盖捕获/校验/发布 |
| CloudClient.upload（服务/CLI） | 验证缓存根后先取 readers.lock 共享，再校验包目录、打开包内 upload 锁并独占。提交/异常后先解锁并关闭上传句柄，最后释放共享租约 |
| CloudClient.cancel_attachment_input（当前附件或退役绑定） | 同根共享租约，覆盖解除 use、检查可选 journal 及结束原 transfer。包已清掉的 ready 附件也能取消：允许 journal 不存在，照常按 ID 解除云端 use。权限错误或 journal 损坏沿现有失败处理 |
| 清理 | cache.lock 独占 → readers.lock 独占；任一获取失败即释放已取锁并跳过 |

共享读锁用标准库：POSIX `flock(LOCK_SH|LOCK_NB)` / Windows `LockFileEx` 的共享非阻塞锁，覆盖固定字节；清理用相同文件/区间的独占锁，释放使用对应 API。文件打开/路径验证仍沿 storage/files，锁句柄不继承。不得把只提供独占语义的原 msvcrt 包装误用为共享锁。API 依据：[Python flock](https://docs.python.org/3.12/library/fcntl.html#fcntl.flock)、[Microsoft LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)；Windows 共享 flags=FAIL_IMMEDIATELY，独占额外加 EXCLUSIVE_LOCK，锁定区间统一为偏移 0、长度 1，不写入该锁定字节。

不在持 readers.lock 时调用 capture_cached；附件先捕获并释放原捕获锁，再由 upload 获取读锁。本服务的 domain 计数填补这两段之间的业务交接。读租约持有者不反取 cache.lock；清理所有 OS 取锁非阻塞，因此不等待持锁网络任务。

不同包或不同上传身份可以同时持共享租约；同包同上传身份沿用包内独占锁。上传与原有捕获可并存；清理只在两边都不忙时进入。CLI 若指向本机受管 inputs 目录必须同样获取读租约；无法识别所有权的缓存路径报错，不能用“非缓存”分支绕过。真正独立捕获目录不属于本机清理范围。

CLI 两次独立命令之间没有持有租约。已退出的独立 CLI 没有项目 DB 续传记录时，清理可按 3 天规则删除其无引用旧 journal；这会结束按该本机 journal 续传的能力，本机清理不取消远端 transfer。命中已有包不会刷新 mtime，用户在捕获结束、上传开始前另行清理，上传需要报告受管包已不存在并允许重新捕获。CLI `nyt cloud task create` 读取调用方的请求 JSON 并按 ID 建立云端 use，只有 `nyt cloud input upload` 会按包目录进入上传锁。

同版本服务与 CLI 的读者共享上述协议。旧 CLI 只取包内上传锁，跨版本并发的保证不能从新版双进程测试推出；升级说明须明确停止旧上传进程、用新版 CLI 访问同一受管缓存的兼容边界。Windows 原生共享/独占锁验收见 S4。

## 5. 包、回执与暂存的识别

- `input_id` 按现有 ContentId 合同，根与包目录全部不跟随链接。固定 metadata 校验包 ID、manifest/selection、receipt.directory 与目录身份；不为删除再全量解压/哈希 archive。
- archive 是普通单链接文件，不解压 tar。上传 journal 按 `upload-<instance UUID>[-<upload UUID>].json` 精确识别，验证 CreateTransfer 与本包的 ID、摘要、大小、codec。当前 journal 使用独占创建，`input_retired` 的指定恢复路径会移除旧 journal，云端直接命中也可没有 journal。包内形似 `upload-*.json.next` 的条目按未知项处理。
- 包内 `upload.lock`、`upload-<attachment UUID>.lock` 要核验为普通单链接受管锁文件并记入退役白名单。有效年龄与释放计数只取原有数据文件，锁文件的身份仍须复核。
- 任何未知文件、链接、外来目录或不可读取 metadata，跳过整个包并给原因；不盲目递归删除 inputs。
- 包时间取原有受管**数据文件**的最大 mtime，包括上传 journal，排除包内锁；读取不续期。
- imports 逐个校验 UUID 名称与 SourceReceipt；依赖通过数据库中 source_receipt 的受管路径匹配，不依赖 JSON 内 root 去找用户文件。独立回执按自己的 mtime，受保护则跳过；仍属于保留输入包的来源回执一并保留。
- 捕获残留只接受当前真实布局：`capture-*` 内的已发布 input、import.json、以及 `.nyt-capture-*` 下的 frozen 数字文件/result 固定包文件；通过根所有权、完整名字/类型树和无占用证明归属。未知或未能完整识别的半成品保留并报告 `unexpected_entry`，不把名字前缀单独当删除授权。
- 一棵捕获暂存树按原有数据文件最大 mtime 判断；没有可验证时间的数据空目录保留，直到有单独明确规则，不用目录 mtime 代替。

## 6. 整包退役与部分失败

不能直接从 `inputs/<id>` 逐文件删除：中途失败会留下正常缓存入口，下一次 capture_cached 命中它却发现缺文件。采用同文件系统 rename；不复制包，不改变捕获本身的副本层级。

### 6.1 退役记录

在所有保护、年龄和身份检查通过后，于 `staging/cleanup-<UUID>/` 写最小 `cleanup.json`：format/schema、请求 ID、类别、原相对位置、原包/目录身份、原数据文件和随包管理的锁文件白名单及 dev/ino/size/mtime、数据文件最大 mtime、phase。写入沿既有 write_json/fsync 协议，metadata 大小受 input_limits.max_metadata_bytes 约束，超限在任何移动前报错。

该记录只保存受管相对路径和身份，不含原项目路径、文件内容或云端凭证。根和记录先持久化；若空间/权限不足，不移动任何业务缓存。

### 6.2 执行顺序

1. 在 domain 清理预留、cache.lock 和 readers.lock 都持有时，复核 DB 依赖及目录/文件身份。
2. 准备 phase=planned 记录；关闭自身会阻止 Windows 移动包的子目录/文件句柄，保持父目录固定。
3. 将 `inputs/<id>` 原子 rename 为该清理目录下的 `payload`，验证移动后身份相符；不允许覆盖现有目的目录。失败则原包仍可用，报告 retire 步骤错误。
4. 必要目录同步、记录 phase=retired。此时包已退出可复用路径，后续同内容捕获按原逻辑重新生成；未实际删除的数据仍计入缓存容量。
5. 严格按记录的文件白名单重新核对并逐个删除，首个失败即停该包，记录残留位置和错误；其他独立包继续。删除全部数据并同步后，按底向上移除已空的受管目录，控制记录最后删除。
6. `files_deleted/bytes_deleted` 只统计本次确认删除的原数据文件；rename 不算释放，cleanup.json 不计入释放缓存量。

独立 imports 的单文件删除沿 S1 原语，无需为一个原子 unlink 新增日志。可识别多文件暂存树沿同一退役流程，避免失败后损坏正常捕获目录的识别。

### 6.3 中断后，仅下次用户主动清理处理

- planned 且 payload 不存在：核对后只收尾未执行的控制目录；原输入重新走普通扫描，不按旧计划直接删除。
- payload 已存在且身份匹配，即使 phase 仍 planned：按事实认定 rename 已发生，继续处理记录中的残留。
- 同 input_id 已重新捕获为新的 inputs 目录：只碰旧 payload，绝不顺着记录删除新包。
- 剩余文件的身份/mtime 发生未授权变化、出现未知文件或记录损坏：保留并报告；不依据旧清单盲删。
- 数据已删但目录/记录删除失败：显示 finalize/rmdir/sync 失败，下次主动清理收尾；不重复计算上次删除字节。
- cleanup 控制文件的新 mtime 不把旧数据变成“再保留 3 天”；对已退役单元延续已验证的原数据时间，同时复核残留未变化。不存在 startup/定时恢复删除。

## 7. 场景

正常：一个 5 天前的包只有 ready/attached 附件使用，云端早已提交，本机没有活动读取，同一 ID 也没有待重试创建、附件或待解除映射。用户选择云输入缓存 → domain/OS 预留 → 依赖复核 → rename 到 cleanup/payload → 删除固定文件 → 返回实际删除的数据文件字节。查询附件历史和已取回的项目文件仍可用。

活动：另一个终端正在用 CLI 上传同一受管缓存。读租约存在 → 清理无法取得独占 → 返回 input_cache_busy，上传继续；不能等待到用户上传结束后悄悄删除。

取消恢复：同一输入 I 的附件上传失败。完整删除任务已成功，但解除附件传输时断线，绑定现为 `retained=false`、`pending_attachment_releases={A:I}`。五天后点击清理，报告 I 仍待解除。原删除命令显式重试后按 A 的 journal 核对并取消传输，成功落库移除 A；下一次手动清理才允许退役 I。整个清理请求不发云端请求。

失败：包已退役，archive 删除成功，receipt.json 删除被拒绝 → 包失败且显示剩余路径；inputs/<id> 已无半包。随后用户重新捕获同内容仍按既有行为成功。下次手动清理只处理旧 payload，计数不重复。

## 8. TDD 与验收

计划测试：`tests/test_cache_cleanup_inputs.py`、`tests/test_cache_cleanup_input_dependencies.py`、`tests/test_cache_cleanup_input_leases.py`、`tests/test_cache_cleanup_input_recovery.py`，并扩展现有输入上传、附件取消和任务删除恢复测试。

- **识别与年龄**：3 天边界、未来时间、目录新但数据旧、journal 新导致整包保留；真实上传/命中产生的两类锁不造成未知项或续期；journal 可合法缺席，伪造 .next/非 UUID 锁名、未知暂存和损坏数据保留并报告；独立 imports 按各自 mtime。
- **业务依赖**：共用包的创建/附件仍可重试；producer 取消未排空；ready/attached 与已成功解除的 cancelled；旧 cancelled 缺解除凭据；retired binding 仅有待解除映射（含两个 aid 共用 I、断线重启及重复清理）；先清掉 ready 的旧包再取消仍能按 ID 解除 use。坏 DB 保守保留。只有历史 receive/旧绑定及已终结基线时旧包可清；对未终结旧写入分别验证唯一基线、已核验独立基线、仍需 branch 回执、证据或身份不明，断言保留范围及具体原因。历史记录、旧写入证据、目的地冲突保护和用户文件可继续使用。
- **并发**：用事件固定 capture → add_source → 附件回写的空隙、新操作准入、上传从打开包内锁到关闭、取消读取 journal 等时点。服务/CLI 与真正独立进程验证共享读取、同包同上传身份互斥、清理先入场拒绝新读、读者先入场清理立即跳过及锁获取失败回收。CLI capture 与 upload 两次独立命令之间清理后，后一次明确失败。原有捕获和上传可并行。
- **退役与恢复**：rename、同步、逐个 unlink/rmdir、记录落盘故障；重启不自动删、手动收尾、同 ID 新包不被误删；新提交已清的 source_handle 返回明确错误，已有终态命令可读；计额不提前扣减。

```bash
.venv/bin/python -m pytest tests/test_cache_cleanup_inputs.py tests/test_cache_cleanup_input_dependencies.py tests/test_cache_cleanup_input_leases.py tests/test_cache_cleanup_input_recovery.py tests/test_service_cache_cleanup.py tests/cloud -q
```

实施完成时用 rg 追踪 `capture_cached`、`CloudClient.upload`、`cancel_attachment_input`、`pending_input_releases`、`settle_retired`、source_receipt 及 cache 路径的真实调用方。静态交付 helper 和旧数据回归测试不作为生产读者。服务、CLI 与恢复入口全部接线并有并发验证后再开启类别。模拟 Windows 锁测试不是原生通过，原机证据归 S4。
