# Spec124：任务收尾的有界 checkpoint 修剪

用户2026-10-21批准实现“任务收尾时按阈值小批修剪”，并实测耗时。本片与并行Spec123分开，测试只使用临时库；不改真实配置/用户库，不提交或发布。

## 1. 目标与现有主链

`SessionExecution._afinish_run → 等待子任务settle → SessionPersistence.afinish → collect_chat_updates → SessionStorage.finish_run提交归档/确认状态 → release_run → run_finished`。

在必要保存提交成功后、原run释放前增加一次可选的小批修剪。复用现有同会话串行边界，不增加后台任务、维护busy或抢占调度。普通内存应用不接入。新输入仍遵循现有run准入；修剪增加的收尾耗时须测量，不承诺零开销。

聊天原文/工具结果、累计用量、诊断、会话元数据和幂等操作记录保持原合同。可回收checkpoint与整个历史库容量分开；活跃/异常/未知依赖可阻止修剪，保护集合和新增会话本身仍可能增长。

## 2. 配置与接口

新增可选`[checkpoint_retention]`，由AppSettings加载，不修改任何真实settings.toml。当前工程值为`enabled=true`、`trigger_checkpoints=128`、`max_delete_checkpoints=128`、`max_delete_bytes=33554432`，全部可配置；非法配置安全禁用本项并告警，不能静默用较激进默认删除。最初8MiB在120轮、每侧4KiB的合成累积上下文中生成量超过回收量，最后20轮144→207持续增长；真实行为红灯后调为32MiB，再对照积压与耗时，结果见第8节。

threshold针对单thread/namespace的checkpoint数；查询至阈值即可，不做整库COUNT。删除数量和编码载荷为单次根run收尾的总预算，不代表SQL耗时、RSS、WAL或整个文件大小。旧积压在后续安全收尾继续处理，测试检查常规生成速度下积压不持续增长；极大单任务或持续异常没有硬容量保证。

新`application/checkpoint_retention.py`仅服务持久化收尾：按现有元数据取得拥有者/归档边界，必要时读取官方状态，计算删除的完整三元键，交存储短事务重验/执行。仍使用当前AsyncSqliteSaver，保留官方序列化，不实现替代saver。

## 3. 可清理状态与保留集合

- 必要保存成功且根会话ready、最新确认checkpoint一致、无待审批/图任务/未确认保存时才进入；失败/中断/恢复中根会话不修剪。
- 当前根、归档中已登记子thread及重新生成记录拥有的旧thread由持久化身份发现，不按字符串前缀猜归属；发现其他会话也声明同一thread则跳过。未识别归属保留。
- 当前根/仍归档的子thread必须已追平最新checkpoint，且官方状态无next/tasks；失败子thread独立跳过。退休的重生成旧thread只在操作终态和引用可确认时处理，不改变操作记录。
- 保留当前最新状态、元数据确认状态、所有归档同步锚点、当前最后任务重生成source（尊重source_thread_id），以及这些状态中的恢复source。来源字段仅作历史溯源而无执行消费者时不自动永久保留。
- 保留checkpoint的所有writes；删除只针对确定候选三元键的checkpoint及相同三元键writes。未知/孤立writes不凭缺失checkpoint批量删除。
- 当前仅处理已核验的SQLite/v4完整状态。DeltaChannel、旧格式、无法读取/超出检查预算、缺失执行锚点、归属或状态不一致均跳过并诊断；不强制截断祖先链。
- 增量归档只在最新到已保存边界之间行走，保留该边界使下次增量仍有完整父链。旧parent指针的历史连续性不再作为任意回放承诺；当前正式消费者逐项验证。

## 4. 正常与失败链

正常例：任务B完成产生C1…C128；finish_run先提交完整聊天和最新C128；最后任务重生成需要C120，二者及其他实需锚点进入保留集合；删除预算内其余旧状态及writes，保持C128续聊和C120重生成。下一任务的新checkpoint不在本次候选中。

失败例：归档提交失败直接沿原session_save_unconfirmed阻断，不调用修剪。归档成功但DELETE失败，只回滚维护事务、记录安全错误类型，原成功结果及确认状态保持；磁盘/连接故障不能伪报删除成功。模块不抛正常维护错误覆盖主运行结果，外部取消/框架控制不吞掉。

删除事务在原SessionStorage锁下使用BEGIN IMMEDIATE；重验session/run/thread/checkpoint/归档版本与拥有者，按精确参数键删除并提交。与其他会话的写入仍会短暂串行；不在锁内遍历大消息、调用模型/工具、备份或VACUUM。

删除会话必须同时消费现有chat_sync及重新生成记录的持久归属，避免旧checkpoint修剪后漏掉已压缩出根上下文的子thread。不创建第二套归属注册表。

复核收敛：thread→set(session_id)归属由sessions/chat_sync/所有状态的regeneration元数据一次建立，提交时在锁和事务内重建一次；历史reply来源不充当归属注册表。当前last-reply source须通过本会话唯一归属证明。没有新增表、索引或schema迁移，不与并行Spec123目标13冲突。

超预算单条记录先在SQL中按编码载荷资格过滤再LIMIT，避免大的旧前缀永久挡住后面的小候选。length(BLOB)取长度，不把旧正文反序列化进Python；但超长前缀仍可能增加扫描行数和writes长度查询。预算只界定删除数量/载荷，不承诺扫描量或耗时恒定；用128/1280大前缀计时验证，只有证据显示受阻时再考虑扫描游标。

旧schema6的纯checkpoint→归档回填合同保留；其测试数据生成显式关闭新修剪，原分页和完整内容断言不变。已经修剪的现代会话若又丢失独立档案，不假装可从不完整checkpoint重建全部原文，必须报chat_history_source_missing并从有效备份恢复。新专项对此有失败断言，不放宽缺父链校验。

## 5. 文件与实施顺序

预计超过8个文件：新配置模型、新修剪模块；settings聚合/加载、async_persistent装配、session_persistence收尾/删除发现；新增专项测试、性能脚本、本Spec及README/handoff/future-work。若存储执行可留在模块内则不增加无用途转发。单文件不超过500行，不升级依赖或改公开应用API。

先写行为失败测试并确认失败原因，再实现最小路径，随后补边界。已有脏文件只做本片必要局部编辑，保存基线便于区分并行修改。标准构建通过后检查新模块进入wheel。

## 6. 验证与性能合同

确定性测试覆盖配置默认/关闭/非法安全禁用、低于阈值不删、全局数量/载荷预算、续聊/重开、最后任务连续重生成、压缩后聊天完整、根子归属/删除、pending writes/待审批/失败/恢复源、事务重验/回滚、观察故障不覆盖主结果与跨会话隔离。异步顺序用事件同步，不用紧sleep。

性能脚本只构造可复现临时数据库和假模型：对照关闭/开启修剪的必要保存/整体收尾耗时，记录中位数/p95/max、实际删除数/载荷与最终积压；分别观察阈值未命中、普通回收、已有积压、多会话写入。结果为本机合成场景，不代表真实供应商/其他机器。性能检查不写脆弱毫秒测试断言。

最终运行全后端、前端测试/TS/Vite、Python语法/500行、pip check、diff检查、标准本机wheel及源外安装；不以测试通过替代真实性能数据。结果和已知限制回填本Spec，handoff只更新本片短摘要，保留并行Spec123的进度。

## 7. 参考边界

当前安装langgraph1.2.12/checkpoint4.2.0/sqlite3.1.1；aprune仅继承基类并抛NotImplementedError，已在内存库实测。官方[可选扩展与Delta限制](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Agent Server TTL](https://docs.langchain.com/langsmith/configure-ttl)及[SQLite写事务](https://sqlite.org/lang_transaction.html)供合同参照，不当作本项目已有修剪实现。

Pi a69bef789的compact→appendCompaction→buildSessionContext保留原记录并重建上下文；AgentX 3fcfa0e5的activeMessages→listByIds→buildHistoryMessage区分持久历史和模型输入。这些源码事实支持职责分开，不提供可直接移植的LangGraph清理算法。

## 8. 实际结果

已接入`open_async_persistent_application → SessionPersistence.afinish → prune_after_finish → _commit`。必要保存成功后执行可选维护，再释放原run；没有新增后台任务、数据库表/索引或依赖。`adelete`补用已有chat_sync发现已压缩出根上下文的子thread。当前配置只在应用启动时加载。

性能命令（macOS 26.4 ARM64、Python 3.12.14，临时SQLite＋合成模型，3轮开/关交替，共1,776次测量任务、0真实供应商请求）：

```bash
.venv/bin/python -m scripts.benchmark_checkpoint_retention \
  --output build/spec124-qkf_gtwv/performance-32m.json --turns 120 --repeats 3
```

表中耗时只计修剪函数，包含检查/等待数据库的时间；触发删除的行单独统计真正删除的调用。不能把整体任务开关差值直接解释为修剪成本。

| 场景 | 中位数 / P95 / 最大值（ms） | checkpoint关闭→开启，三轮终值一致 |
| --- | --- | --- |
| 未达阈值，36次检查 | 0.133 / 0.152 / 0.166 | 不删除；此场景阈值显式设为100000 |
| 单会话120轮、每侧4 KiB；66次触发删除 | 7.771 / 8.566 / 12.183 | 1440→121；收尾最大127，末20轮在97—126间波动 |
| 已有384份积压；12次触发删除 | 1.468 / 2.104 / 2.104 | 540→28 |
| 两会话并发、各60轮；30次触发删除 | 8.501 / 34.634 / 65.548 | 合计1440→164，每会话82 |

普通120轮场景整体任务中位数关闭129.441ms/开启128.674ms，P95关闭226.850ms/开启232.899ms；开启并没有证明整体更快，差值含调度/SQLite噪声。该样本checkpoint＋metadata有效载荷约819.62MB→131.35MB，不含writes、索引、WAL或文件空闲页；没有执行VACUUM。后两者以及归档原文的长期增长不由本项预算限制。

两个架构复核问题先写红灯再修复：大记录前缀挡住小候选；历史reply来源反查导致规划扫描/错误永久归属。修复后补量化探针：128/1280个超预算前缀时，实际删除调用P95为1.421/2.444ms，小候选均有进展，但大记录故意保留；这两例显式使用16000字节预算，最后一次检查超预算跳过。其他会话1000/10000条历史boundary时，实际删除调用P95为1.273/1.333ms，未出现按历史记录逐条反查的增长。canonical会话/归属记录、SQL扫描量和锁等待仍非硬耗时上限。

最初8MiB默认在相同120轮上下文中出现积压：关闭1440份，开启207份且末20轮144→207持续增加；`backlog-red.log`为实际回归红灯，调为32MiB后`backlog-green.log`为1 passed in15.74s。`performance-final.json`是修复算法但仍为8MiB的历史样本；当前最终依据为`performance-32m.json`，不混用。

新增25项确定性测试已随最终全后端通过，覆盖配置/预算、重开续聊/用量/连续重生成、真实手动压缩、审批/pending writes、恢复源、子档案/会话删除归属、事务重验/回滚、必要保存失败不维护、归属冲突及Delta安全跳过。架构复核两项已回归关闭，安全复核没有剩余高置信发现。

```bash
PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
# 3768 passed in 249.33s (0:04:09)
npm --prefix frontend test -- --run
# 49 files / 395 passed
npm --prefix frontend run build
# tsc --noEmit成功；Vite 325 modules成功，保留既有chunk体积提示
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec124-qkf_gtwv/wheel
# exit 0；本地候选wheel，不发布
.venv/bin/python -m twine check build/spec124-qkf_gtwv/wheel/*.whl
# PASSED
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec124-qkf_gtwv/wheel --output build/spec124-qkf_gtwv/install.json
# exit 0；CLI/Web/原生工具/审批/SQLite重开/MCP passed，0供应商请求
```

另有170份Python文件AST/500行、compileall、pip check及diff检查通过；wheel中的166份生产Python源码及已构建前端逐字节一致。证据目录`build/spec124-qkf_gtwv/`，最终后端日志`backend-final.log`、安装报告`install.json`。包版本仍为0.2.0本地候选，SHA-256为`e201f4930a63e0b8fba415f20792a6952a9d88725c9e5fedd7e03a73b165d298`；不等同公开PyPI的0.2.0。

首次安装包构建误把frontend/dist传给接受已分装web/licenses资源的`--frontend-assets`而被标准校验拒绝；改回默认标准构建入口成功，未改构建脚本或放宽资源检查。首次全后端3758 passed/1 failed，旧schema6回填夹具在默认自动修剪下丢失模拟旧版的完整源，已按第4节显式关闭该夹具的修剪；原完整性断言保留，并新增现代档案丢失的失败合同测试。上述失败日志均保留。

本机耗时不代表Windows、用户磁盘或真实模型/工具负载；没有对真实用户库运行修剪。跳过/失败会保留旧状态并诊断；持续跳过、单条超预算、更多会话与必须保留的状态仍可能增长，本片不提供整个历史库硬容量保证。Spec123备份/迁移/还原仍是独立并行工作，不宣称其功能已经可用。
