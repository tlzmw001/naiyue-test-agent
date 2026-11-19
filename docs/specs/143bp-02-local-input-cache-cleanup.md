# Spec143BP-S2：本机云输入包缓存清理

日期：2026-11-18。状态：待实现。前置：S1 与 [公共合同](143bp-00-contracts.md)。只处理本机项目 DB 旁的 `cloud-input-cache`，不访问 Coordinator，不改变捕获/打包/传输的内容与副本数量。

## 1. 当前数据与消费者

```text
LocalTaskDomain.capture
  → capture_cached（cache.lock，冻结/打包，命中检查，发布）
  → LocalTaskRepo.add_source（source_handle → captured）

LocalAttachments.launch / preparation.prepare
  → source_handle → captured.directory
  → client.upload → input_upload.upload（续传 journal，提交）
  → 云端任务/附件关联 → 本机状态保存

旧 LocalDeliveries.launch
  → source_for → original_archive / 来源回执
  → 差异/基线/应用与恢复

CLI cloud input capture/upload
  → capture_cached / client.upload
```

当前 `capture_cached` 只有捕获期间的缓存 OS 独占锁；上传和旧交付不会持有这把锁。因此“清理拿 cache.lock”本身不足以保护全部读取。输入包按内容身份复用，某个附件结束不能代表其他消费者结束。

| 本机条目 | 当前职责 | 本片处理 |
| --- | --- | --- |
| `cache.json`, `cache.lock` | 根所有权、捕获互斥 | 永久保留，不参与年龄判断 |
| `inputs/<input_id>/archive` | 已冻结压缩包 | 与同包文件一起判断/退役 |
| `input.json`, `manifest.json`, `selection.json`, `receipt.json` | 包身份与捕获事实 | 与 archive 组成一包 |
| `upload-<instance_id>[-<upload_id>].json` | 上传命令 ID/续传 journal | 与所属包一起保护与回收；`.next` 按写入协议识别 |
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
| `cloud_files/input_upload.py` | 对本机受管缓存取得跨进程读租约，覆盖从首次 metadata 读取到最终提交；独立非缓存捕获目录保持原路径 |
| `application/cloud_local/tasks.py` | LocalTaskDomain 持有短状态 `cache_users/cache_cleaning`，提供 `input_cache_use/cleanup_guard`；capture 覆盖发布到 add_source 间隙 |
| `application/cloud_local/attachments.py`、`preparation.py` | 生产者从首次读取缓存到本机结果落库参加同一 domain 使用保护 |
| `application/cloud_local/deliveries.py`、`delivery_inputs.py` | 旧非 files 交付从读取输入/回执到结束与必要恢复参加保护和读租约；不能只保护 original_archive 那次短打开 |
| `storage/cache_cleanup.py` | 增加只读依赖汇总，沿实际表/JSON字段，不改业务记录 |
| `application/projects/cache_cleanup.py`、`application/cache_cleanup.py` | 接云输入处理器、生命周期、预算；policies 开启 cloud_inputs |

这里新增租约有实际四类消费者：上传、旧交付、捕获到落库的交接、清理。单纯直接传一个 busy 布尔值无法覆盖 CLI 进程；单纯拿现有 cache.lock 包住全部上传会改变并发捕获/上传行为，因此采用第 4 节的两类锁。

## 3. 持久依赖规则

按同项目数据库读取完整相关记录，参数化 SQL，先校验 schema/身份。输出 `protected_input_ids`, `protected_import_paths` 及稳定原因；它们是本次快照，最终删除前在已取得占用保护后复核。字段无法解析或关联缺失时保留受影响缓存范围并报错，不能把坏数据当零引用。

| 生产记录/状态 | 判定 |
| --- | --- |
| create operation `phase` 不是 `prepared/cancelled`，且 request 有 source_handle | reserved/project/upload/task/environment/failed/unknown 均仍可继续，保护输入包和来源回执 |
| create 已 cancelled，但内存 producer 未排空或 may_have_task 尚未核实 | 活动/恢复保护，不能只看 cancelled |
| `local_attachments` 为 uploading/failed | 可续传/重试，保护 source_handle 对应包；input_id 尚未产生时靠 domain 活动保护 |
| `local_attachments` 为 ready/attached | 云端提交和关联已经成功，不仅因为附件仍存在就永久保护本机副本 |
| attachment cancelled 且 producer 已排空 | 解除该消费者保护；仍检查共享该包的其他记录 |
| retained 旧绑定 `input_id != None` | 在旧代码交付尚未退出期间保护基线输入及来源回执 |
| 旧 receive 存在 `input_id/source_handle/input_package`，仍可用于交付或恢复 | 即使当前没有正在执行的 delivery，也保护；不能只查 preparing/executing |
| 旧非 files delivery 的准备、执行、失败、partial、unknown 或仍使用的已完成基线 | 顺着 receive/source 查实际依赖，保护；与旧路径退出合同一起释放 |
| 仅历史 source 行存在、没有上述消费者 | 不永久保护可重建缓存；超过 3 天可清，保留历史 source 元数据 |

旧结果在本机可继续发起代码交付，因此这里有意保守：旧路径未退出时，一些超过 3 天的包仍会显示 `legacy_delivery_dependency`。不为提高删除率提前改变旧任务/交付行为。

被手动清除后的过期、无活动依赖 source_handle 若被再次直接提交，返回明确 `input_cache_expired`（重新选择来源），不偷偷重捕获可能已经变化的项目文件、不复用旧上传 command_id 发不同内容。该错误只用于经过所有权核验的本机缓存缺失；权限、损坏、未知路径仍各自报错。正常 source_handle 格式和外部请求 schema 不变。

不删除 sources/bindings/attachments/receives/deliveries 历史行，不修改用户附件名、任务状态或 source 路径；本片不做 DB 迁移。过期缓存与业务事实分离。

## 4. 占用保护与锁顺序

### 4.1 本服务的 domain 预留

- `input_cache_use` 在事件循环中先检查 cache_cleaning 再增加使用计数，finally 减少；可嵌套，覆盖全部 await/线程 IO 到必要 DB 结果保存。
- `cleanup_guard` 在没有使用者时原子设置 cache_cleaning；已有使用者使清理跳过整个项目输入类别，已有工作不被取消。
- 清理期间新缓存操作返回 `input_cache_busy`，不先登记一个会被误报成功的上传；已有 producer 在第一次缓存 IO 前再次检查。主会话普通聊天和工具运行不因为此标记停止。
- 预留只保护该项目缓存，不全机互斥其他项目。当前 UI 的运行后续、任务状态查询不被当成读缓存。
- 冷 host 由 G4 + DB OS 锁保证本服务不会同时创建 domain；跨进程读者仍靠下面的锁保护。

### 4.2 跨进程共享读锁

沿原 cache.lock 的非阻塞独占捕获锁不变；新增固定 `readers.lock`，只在根所有权已验证后创建。

| 操作 | 锁 |
| --- | --- |
| capture_cached | 原 cache.lock 独占，覆盖捕获/校验/发布 |
| input_upload、旧交付读取 | readers.lock 共享，覆盖整个缓存消费过程 |
| 清理 | cache.lock 独占 → readers.lock 独占；任一获取失败即释放已取锁并跳过 |

共享读锁用标准库：POSIX `flock(LOCK_SH|LOCK_NB)` / Windows `LockFileEx` 的共享非阻塞锁，覆盖固定字节；清理用相同文件/区间的独占锁，释放使用对应 API。文件打开/路径验证仍沿 storage/files，锁句柄不继承。不得把只提供独占语义的原 msvcrt 包装误用为共享锁。API 依据：[Python flock](https://docs.python.org/3.12/library/fcntl.html#fcntl.flock)、[Microsoft LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)；Windows 共享 flags=FAIL_IMMEDIATELY，独占额外加 EXCLUSIVE_LOCK，锁定区间统一为偏移 0、长度 1，不写入该锁定字节。

不在持 readers.lock 时调用 capture_cached；附件先捕获并释放原捕获锁，再由 upload 获取读锁。本服务的 domain 计数填补这两段之间的业务交接。读租约持有者不反取 cache.lock；清理所有 OS 取锁非阻塞，因此不等待持锁网络任务。

两次普通上传可同时持共享租约；上传与原有捕获可并存；清理只在两边都不忙时进入。CLI 若指向本机受管 inputs 目录必须同样获取读租约；无法识别所有权的缓存路径报错，不能用“非缓存”分支绕过。真正独立捕获目录不属于本机清理范围。

在共享锁及其 Windows 原生测试就绪之前，不启用 cloud_inputs 类别；不能只完成服务内计数就宣布覆盖跨进程。

## 5. 包、回执与暂存的识别

- `input_id` 按现有 ContentId 合同，根与包目录全部不跟随链接。固定 metadata 校验包 ID、manifest/selection、receipt.directory 与目录身份；不为删除再全量解压/哈希 archive。
- archive 是一个普通单链接文件；不进入它的 tar 成员。只处理固定管理文件与符合现有上传 journal 格式的名字，校验 JSON schema/内容关联；合法 `.next` 也计入文件 mtime。
- 任何未知文件、链接、外来目录或不可读取 metadata，跳过整个包并给原因；不盲目递归删除 inputs。
- 包时间为待一起回收的所有原有受管文件 mtime 最大值，包括上传 journal；读取不续期。
- imports 逐个校验 UUID 名称与 SourceReceipt；依赖通过数据库中 source_receipt 的受管路径匹配，不依赖 JSON 内 root 去找用户文件。独立回执按自己的 mtime，受保护则跳过；仍属于保留输入包的来源回执一并保留。
- 捕获残留只接受当前真实布局：`capture-*` 内的已发布 input、import.json、以及 `.nyt-capture-*` 下的 frozen 数字文件/result 固定包文件；通过根所有权、完整名字/类型树和无占用证明归属。未知或未能完整识别的半成品保留并报告 `unexpected_entry`，不把名字前缀单独当删除授权。
- 一棵捕获暂存树按原有数据文件最大 mtime 判断；没有可验证时间的数据空目录保留，直到有单独明确规则，不用目录 mtime 代替。

## 6. 整包退役与部分失败

不能直接从 `inputs/<id>` 逐文件删除：中途失败会留下正常缓存入口，下一次 capture_cached 命中它却发现缺文件。采用同文件系统 rename；不复制包，不改变捕获本身的副本层级。

### 6.1 退役记录

在所有保护、年龄和身份检查通过后，于 `staging/cleanup-<UUID>/` 写最小 `cleanup.json`：format/schema、请求 ID、类别、原相对位置、原包/目录身份、原数据文件白名单及 dev/ino/size/mtime、源文件最大 mtime、phase。写入沿既有 write_json/fsync 协议，metadata 大小受 input_limits.max_metadata_bytes 约束，超限在任何移动前报错。

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

正常：一个 5 天前的包只有 ready/attached 附件使用，云端早已提交，本机无活动读取或旧交付依赖。用户选择云输入缓存 → domain/OS 预留 → 依赖复核 → rename 到 cleanup/payload → 删除固定文件 → 返回实际字节。原项目文件、附件历史、云端输入包均保持。

活动：另一个终端正在用 CLI 上传同一受管缓存。读租约存在 → 清理无法取得独占 → 返回 input_cache_busy，上传继续；不能等待到用户上传结束后悄悄删除。

失败：包已退役，archive 删除成功，receipt.json 删除被拒绝 → 包失败且显示剩余路径；inputs/<id> 已无半包。随后用户重新捕获同内容仍按既有行为成功。下次手动清理只处理旧 payload，计数不重复。

## 8. TDD 与验收

计划测试：`tests/test_cache_cleanup_inputs.py`、`tests/test_cache_cleanup_input_dependencies.py`、`tests/test_cache_cleanup_input_leases.py`、`tests/test_cache_cleanup_input_recovery.py`，并扩展已有输入缓存/上传/旧交付测试。

必测：3 天边界与 futuremtime；目录新但数据旧；journal新导致整包保留；共享内容一消费者活跃；failed上传仍可重试；cancelled但未排空；ready/attached解除本机依赖；旧 retained task/receive/完整基线保护；坏DB保守保留；独立imports；未知暂存；capture→add_source交接；服务和 CLI 两并发读成功；清理不等待读者；捕获/上传原并发不被改成串行；两个 OS 锁失败回收；rename/同步/每次 unlink/rmdir/记录落盘故障；重启后无自动删除；手动残留续办；同 ID 新包不被旧清理记录删除；缺缓存 source_handle 明确错误；容量不提前扣减。

```bash
.venv/bin/python -m pytest tests/test_cache_cleanup_inputs.py tests/test_cache_cleanup_input_dependencies.py tests/test_cache_cleanup_input_leases.py tests/test_cache_cleanup_input_recovery.py tests/test_service_cache_cleanup.py tests/cloud -q
```

实施完成时再次用 rg 核对所有 `capture_cached`、`client.upload`、`original_archive`、source_receipt 与 cache 路径消费者；生产、CLI、恢复和测试均接入后才允许 policies 开启类别。模拟 Windows 锁测试不是原生通过，原机证据归 S4。
