# Spec143BO-S5：可配置到期清理与本机维护入口

日期：2026-11-17。状态：已实施并完成本机验证。依赖：[S1](143bo-01-storage-and-new-generation.md)、[S2](143bo-02-wiki-tools-and-scope.md)。本片为后续整体清理重构留下明确入口，首版不建设全局清理系统。

## 1. 产品合同

默认 TTL=30 天、expiry_grace_days=7，均来自配置。到期即从普通目录/read/search/导航排除；物理清理由机会触发，达到缓冲截止只表示可删除，不承诺第 37 天准点运行。

清理不调用模型。没有运行、库未打开或维护失败，都不使过期记录重新可用。首版不主动打开所有项目库、不为清理创建个人库、不增加常驻定时器。

典型例子：11 月 1 日有充分用户确认，默认 12 月 1 日同一时点过期；12 月 8 日同一时点开始具备物理删除资格。12 月 3 日读取页面不能续期；12 月 5 日新的充分确认若成功提交，GC 应看到更新后的有效期并保留。

## 2. 当前清理的实际边界

| 当前实现/入口 | 保护对象及触发 | 本片决定 |
| --- | --- | --- |
| SessionPersistence.afinish → checkpoint_retention.prune_after_finish | 必需归档/终局保存成功之后，原根运行仍拥有准入；保护当前 checkpoint、恢复和再生成引用 | 复用此收尾位置，算法与保护集合不改 |
| DiagnosticPersistence → DiagnosticRepo → diagnostic_storage.prune | 启动准备、诊断写入/终局时按年龄和字节容量清理；保护 open 组 | 保持原入口；写入时容量准入不能被改成可延迟的可选维护 |
| service/cloud/server._maintain_inputs → inputs.sweep_expired | Coordinator 的独立循环、过期上传与相关租约 | 不接入本机记忆；保留独立进程/所有者 |
| _maintain_resources → results.retention.sweep / lifecycle.sweep_idle | 云端任务 gate、维护/IO 租约、固定结果及闲置资源 | 不合并算法、期限和删除规则 |
| ApplicationLifetime / AsyncExitStack cleanup | 排空任务、关客户端/数据库等资源释放 | 不当成数据到期清理 |
| storage/maintenance.check_restore_marker | 检查未完成还原标记，拒绝不安全开库 | 不借此执行 GC |

本机当前不存在可直接注册所有领域清理的统一调度器。已有共同点是“必要保存成功后的可选维护位置”，因此只合并这一层。

## 3. 入口和所有权

新增 application/sessions/session_maintenance.py 的 after_finish 函数，调用方仍为 SessionPersistence.afinish 的必要保存成功分支、释放 _active 之前。直接传入已经存在的 agent/storage/session_id/run_id/snapshot/state/checkpoint_settings/memory_access；不使用全局服务定位器、回调注册表或额外线程池。

该函数顺序执行两个独立步骤：

1. state=ready 且原配置允许时，调用既有 prune_after_finish，保持原 snapshot/ownership/恢复保护。
2. memory_access 存在且维护到期时，在线程工作函数中调用 MemoryAccess.maintain_expired，再分别处理当前项目/已打开个人库。

两步分别记录异常，不让 checkpoint 裁剪失败阻止 memory 的独立检查，也不让 memory 清理失败改变已经成功的用户运行结果。必要保存失败时两步都不运行；取消仍通过 await_settled 等已开始 IO 完成后才释放运行/资源。

当前 afinish 会先通知 MemoryJobs.after_finish 再做 checkpoint 裁剪；本片保持这一相对顺序。后台 B 可能与 GC 并发，依 S1/S3 的版本、存在性和有效期最终校验解决，不持锁等待模型。

项目清理节流保存在该 MemoryAccess；个人库节流保存在全应用共享的 PersonalMemory，避免每个项目各清一次同一库。使用可注入的 monotonic 做间隔、wall clock 做到期判断；首次符合条件不节流。没有打开的个人库直接返回 not_open，不调用 repository() 导致新建。

未来统一调度只需在正确所有者内调用同一个 maintain_expired/prune_expired 方法，无需复制删除规则。未来是否扫描关闭项目、设置周期或外部维护命令，属于整体重构，当前明确不实施。

## 4. 仓储合同

MemoryRepo.prune_expired(now, grace_days, batch_size) 在所属库内执行有界事务，不接受任意范围/路径。返回：generation_id、deleted_memories、deleted_versions、deleted_sources、deleted_links、deleted_pages、skipped_changed、more_work、reason、duration_ms。

结果计数代表实际已提交删除；回滚不报告已删除。reason 来自程序枚举（disabled/not_open/not_due/no_candidates/pruned/conflict/failed），不记录正文、引文或凭证。

MemoryAccess.maintain_expired 协调 current workspace 与已经打开的 personal 两个独立结果；不做跨库事务。共享个人库失败不回滚项目已完成清理，也不能把两者合成“全部成功”。

## 5. 到期、版本和来源删除

资格采用 expires_at≤now−grace_days×86400。只处理新代 active/candidate/inactive 记录；forgotten 的最小墓碑/抑制保持，旧 legacy143bm 隔离数据不在本片删除范围。

历史版本符合 superseded_at≤截止点时可清理；整条记忆已具备删除资格时，其旧版本即使刚产生，也可随该过期条目分批清理。当前有效记录所需来源已由写入事务复制到当前版本，不能靠引用旧版本来维持范围或正文依据。

每次调用对旧版本、过期主记录、孤立关联/空页各最多处理 cleanup_batch_size 个工作单位，不循环至清空全库。每个版本最多 8 个来源，限制派生删除量；包含大量版本的记录先分页清旧版本，再在其余版本已清完时删除主记录和当前来源，禁止一次级联删除无限历史。

删除前在同一写事务重新检查 ID、版本、expires_at、当前状态及是否仍为当前版本。读取候选以后发生新确认则跳过，不通过重取新版本自动扩大本次删除集。

页的 active 可见性通过读取过滤即时改变；物理移除最后一个成员后，分批删除相关链接和空页。空页仍有未处理入站关联时保留其 metadata，先清关联再删页，不使用会产生无界级联的删除。链接读取本就过滤缺失/无可见内容目标，不向模型暴露半清理页。

上述关联删除只处理本库拥有的链接。个人页删除不为查找项目入站引用打开所有项目库；各项目读取时过滤失效个人目标，在该项目自己的有界维护中清除悬空链接。不能把跨库链接当作跨库事务或无限扫描的理由。

条目/页结构清理递增相关 page.version/catalog_revision，使已有游标和 B 的成员快照失效。仅删除不被前台投影消费的历史版本无需刷新目录版本，但 read(include_history) 的继续读取必须绑定相应版本投影。

改变 grace 配置影响下一次清理资格；改变 default_ttl_days 只影响新条目/新充分确认。显式 disable 不等于立刻物理删除，仍按条目期限/缓冲；显式 forget 沿独立抑制合同。

## 6. 失败、并发与关闭

正常：根必要保存成功 → 可选维护 → 取得现有 D1 → 单库短事务 → 重新核验 → 分批删 → 提交统计 → 释放锁 → 根运行释放。

失败：SQLite busy 或 IO 失败 → 本事务回滚 → 记录安全错误码和失败域 → 已完成的前台结果不改变 → 不在当前调用自旋重试。以后常规维护根据当时数据再检查，不是恢复失败的模型记忆任务。

复用 PersonalMemory.lock/MemoryAccess._lock 和各 Database.lock，顺序 S1（会话 history_lock）→D1→单个 DB；项目与个人事务分开，不持两个 DB 锁等待；不能 DB→D1。排空已开始维护后才关库，不能取消 await 就把线程仍在写的库关闭。

本片在实施时同步 docs/lock-hierarchy.md：标记会话收尾协调器、D1 的个人节流与 GC 消费者，以及 DB 的有界删除。checkpoint 裁剪仍遵守自己的 saver/DB 分离规则，不与 memory 事务交错持锁。

GC 后 SQLite 文件可能保留可复用空闲页；本片不在聊天收尾运行 VACUUM，不宣称删除已清除磁盘物理残留或已有备份。原始会话历史/概览、模型使用记录、诊断、云端资源和文件一律不由 memory GC 删除。

## 7. 配置和观测

采用总规范的 default_ttl_days=30、expiry_grace_days=7、cleanup_batch_size=100、cleanup_interval_seconds=3600。interval=0 表示每次合格收尾可尝试一个有界批次，并非常驻循环。

日志/报告区分：立即过期的可见性、符合删除资格数、实际删除、预算剩余工作、版本变化跳过、存储失败。不会为显示精确总数先扫描全部正文；unknown 时不填造假 0。

## 8. 文件和测试

修改 session_persistence.py 的原可选维护位置；新增 session_maintenance.py 因为这里有两个真实维护消费者，不建立泛化 CleanupManager。

扩展 MemoryAccess、PersonalMemory 和 MemoryRepo；新增 memory_sql/memory_retention.py 承担领域删除及索引查询。默认配置在 memory_settings.py，示例字段和用户文档在最终接线时同步；不修改真实配置。

新增 tests/test_memory_wiki_retention.py、test_session_maintenance.py；继续运行原 checkpoint retention 系列与个人库并发/关闭测试。

必测：TTL/缓冲前后精确边界、配置 0/非法/修改、局部 source-only 补证不续期、读不续期、未打开个人库、两个项目共享一次个人节流、无必要保存不清理、两种维护故障互不掩盖、GC 与新完整确认/拆页/B 提交并发、很多旧版本分批进展、空页/关联删除、原始历史/概览/旧代/抑制不变、关闭等待线程完成。

~~~sh
.venv/bin/python -m pytest tests/test_memory_wiki_retention.py tests/test_session_maintenance.py tests/test_checkpoint_retention.py tests/test_checkpoint_retention_boundaries.py tests/test_checkpoint_retention_planning.py tests/test_memory_personal.py -q
~~~

用固定时钟和事件屏障验证竞争；不使用 sleep 50ms 等时间窗口。S5 完成只能声明本机机会触发清理接线，不能声明全局清理优化已完成。

## 9. 实施接线（2026-11-18）

实际路径：SessionPersistence.afinish必要保存成功→目录标记/MemoryJobs.after_finish→session_maintenance.after_finish→原checkpoint prune→await_settled(run_blocking(MemoryAccess.maintain_expired))→D1→项目/已打开个人库各自的MemoryRepo.prune_expired事务。必要保存失败不进入该路径，维护失败只记安全诊断。默认旧代未切换时返回disabled，不为清理迁库。S6负责统一产品切换。

同库候选选择及ID/版本/状态/到期核验在同一写事务内，较候选在锁外采样更强：正常API确认不可能插入选择与删除之间；若GC先完成，后续写入仍按不存在/CAS失败处理，不新建替代。历史版本和当前快照共用一个版本删除预算，只有旧版本清完且本批有剩余版本预算时才删主记录；来源删除随各版本最多8条有界。仅历史清理不动目录，include_history继续读取用原有有界versions投影指纹绑定并返回前复核。

空页有入站关联时先分批清关联，后删页；页结构变化递增page.version/catalog_revision。项目外部个人链接检查持D1，按to_page_id保留可回绕seek位置，每次只核对一个有界批次，避免首批有效目标挡住后续悬空链接；个人库未打开/旧代无法判断时不宣称目标不存在。没有新增锁或全局注册器。

本片只实现已打开库的机会清理。并行143BP工具归档/输入缓存/用户手动清理不接本GC，也不改变其方案。验证证据保存在build/spec143bo-s5。

验证：最终针对性44项、扩展模块882项通过；全后端四组1674/1917/1766/1914，共7271通过，0失败/跳过。后补个人入口、域隔离及关闭用例由最终44项覆盖。标准Mac构建/Twine、570个打包Python匹配、compileall/pip check/diff通过。证据：[verification.json](../../build/spec143bo-s5/verification.json)。无真实用户库清理或迁移、真实配置修改、模型质量评测、提交发布。下一片S6。

### 审查修复

个人目标探测独立降级，失败保留未知外链、记录personal诊断，继续本库清理。页和链接使用主键seek分批检查；扫描状态由项目MemoryAccess/共享PersonalMemory持有，D1保护，仓储事务成功后才推进；直接仓储调用使用仓储实例状态。优先处理本批刚变空的相关页面，其余页面回绕扫描；未扫描完或仍有已知失效外链均返回more_work。没有为统计剩余空页全表扫描。新增3项回归包含SQLite VM指令数对比，100/1000有效页固定批量时开销保持有界。
