# Spec 070：默认持久会话与跨进程恢复

状态：2026-10-08，按用户已批准Spec069/SQLite/default CLI范围实施完成；本地确定性/两个独立进程和有界真实模型探针通过。首片恢复安全结束的会话，受限状态重启只识别/阻断；同进程审批/错误/控制行为保持，不宣称完整崩溃恢复或生产级会话成熟度。

## 当前链路与修改

CLI → build_local_application → SessionService内存元数据＋InMemorySaver → app.run → graph状态 → 应用清理/终局。新持久路径由CLI资源生命周期组合SessionStorage＋官方SqliteSaver＋同一SessionService/Agent；关键保存直接接应用运行边界，不通过可隔离observer。

模块：application/session_storage.py负责私有SQLite文件/实例锁/元数据SQL；application/session_persistence.py负责running/确认checkpoint/受限状态和恢复校验；application/persistent.py组合catalog/session/official saver。原__init__只提取被内存/持久两入口共用的装配块，原公开签名不变。SessionService新增可选内部_persistence，create/select/run/resume在现有生命周期接关键保存。超过500行前拆内部逻辑，不另写Agent运行时。

## 依赖与资源

- 固定langgraph-checkpoint-sqlite3.1.1，与现有langgraph1.2.12/checkpoint4.2.0兼容（pip dry-run已确认不升级已有依赖）。固定新增传递aiosqlite0.22.1/sqlite-vec0.1.9；这些由官方包声明需要，本片不增加异步/向量业务。安装及正式连接/序列化兼容探针独立验证，不把依赖声明当运行证明。
- DB默认Path.home()/'.naiyue-test-agent'/'sessions.sqlite'，--session-db仅覆盖位置；默认目录新建0700，数据库/锁文件0600，拒绝既有文件symlink/不安全权限，附属文件检查；不修改.env。资源close等worker完成后执行。当前macOS/Linux用标准库fcntl实例非阻塞独占锁；第二进程在模型调用前被拒绝，不承诺Windows/多进程写入吞吐。
- metadata与saver独立连接/事务，同一文件不假装全事务。repository有线程锁，saver用其内部锁/check_same_thread=False。显式SQLite访问失败日志只含阶段与错误类型，不含原SQL/凭证/消息。
- graph.with_config设置本机CONFIG_KEY_DURABILITY='sync'，仍为原生Pregel副本，状态观察器继续接线；核对/测试该已固定版本的内部key。不是把整个invoke搬到async，不承诺工具exactly-once/任意掉电无损。

## 元数据与协议

应用表naiyue_sessions：session_id PK、thread_id UNIQUE、model_id、workspace_root nullable、state(ready/running/interrupted/failed/review_required)、run_id nullable、checkpoint_id nullable、checkpoint_ns默认''、created_at/updated_at、last_run_status nullable；schema标识1。日期真实UTC；只存ID/必要元数据，不存API key/模型对象/消息全文。框架表只由官方saver管理，不用SQL猜checkpoint内容。

1. create在内存安装记录前提交元数据；失败不产生可用会话。select_model先提交选择再改内存，不丢中途切模型的最终选择。
2. 进入run的观察开始后、模型/工具之前，提交running+run_id；失败本次run failed且零模型/工具。持久入口同进程也只允许一个活动run；原内存应用多会话能力不改。
3. finally在busy仍开启时处理持久结束，再释放现有控制/忙碌状态；持久结束异常也必须执行原清理。正常completed/stopped核验get_state的确认checkpoint/无next/无interrupt，保存ready/对应ID；失败/待批/unknown保存受限状态。关键保存失败先failed，不能先发completed；原主异常优先于报告/持久结束次级异常。

实现中清理故障反例表明ready不能在关键清理之前提交：先完成pending_turn快照、队列/控制等原清理，持久路径保持busy直到确认提交；清理已失败则先确定failed再保存受限状态。两个运行入口共用私有_finish_run收尾，存储失败仍强制释放进程内忙碌/队列资源，最后报告终局，不覆盖原主异常。
4. running残留/最终保存未确认时阻断本进程再次执行及重启恢复；普通已记录failed/待批同进程原重试/审批保持，但新进程不自动续跑。不能把安全标记默认为false。
5. restore仅ready，验证model仍在catalog、workspace重新校验，checkpoint/thread/namespace最新ID与记录一致、无待执行节点/interrupt；空新会话允许无checkpoint，存在确认ID则不得缺失。不调用模型、不重放工具；恢复session/thread保持，新run换ID。缺/坏元数据或未支持schema明示失败，不新建空历史冒充恢复。

## CLI契约

默认持久新会话、--no-session内存模式、--resume可选ID、--session-db覆盖路径。临时与resume/显式DB互斥；临时不触碰会话DB/锁但工具产物照常。只改CLI默认，现有build_local_application保持内存API。

无IDresume先读取元数据索引（所有工作区）按updated_at DESC、ID ASC，编号/完整ID/时间/模型/工作区/状态；TTY输入编号/ID、空行/EOF退出，无效选择重试；非TTY只列出退出。没有DB/会话只提示暂无、不创建新session或发模型请求。指定不存在ID报错；选择后用原workspace构造工具，显式--workspace不一致拒绝；host-bash仍需用户本次显式启用及有效workspace。标题/搜索/TUI/全文预览/continue/分支不做。正常创建/恢复显示session ID和模式。

存储失败不静默回退内存。CLI退出finally unsubscribe→关闭storage，worker先join。恢复校验失败不修改已有会话。正在使用的DB同库第二入口失败，不绕过实例锁。

## 测试和范围

先红：跨应用/两个独立进程消息/工具/摘要延续、ID/选模/workspace隔离；默认CLI/临时零落库/无参选择/空库/非TTY/参数冲突；running/failed/interrupted/manual阻断；开始/最终写入故障零调用或无completed且仍清理；模型切换写入故障不改内存；checkpoint缺失/ID错/next残留；锁第二进程/权限/关闭后再开。

旧CLI内存控制/工具回归明确加--no-session以保持验收范围，原断言不放宽；新增测试独立覆盖持久默认和相同核心行为。此修改原因是用户明确更改默认，不改conftest。故障与并发用Event/状态同步，不用极短sleep。

影响生产__init__/session_service/cli/cli_display与三个新持久模块，requirements新增固定官方包及其传递项；新持久/CLI/跨进程测试与旧CLI入口模式迁移、可重复离线worker脚本、阶段文档。Python每文件≤500。不碰AIAutoTest/模型配置/关键知识点；无提交发布。新资源只在持久入口创建，测试DB全部tmp路径。

验证：定向/全量pytest、compileall、pip check、固定版本官方saver兼容探针、显式行数/尾空白/接线检查；真实模型独立有界两进程探针（一个保存、一个恢复，总95秒/最多两逻辑调用），合成随机事实且stdout仅统计；不输出正文、凭证。外部模型超时不算持久化缺陷，跨进程离线证据单独记录。代码完成不代表用户掌握，保留核心链路学习待办。

## 实际实现与验证

- 官方依赖dry-run与安装只新增3个固定包，无已有包升级；requirements全部固定版本与实际环境一致。官方saver小探针通过原生Pregel with_config副本、sync保存和get_state读到确认ID；持久装配继续共用旧middleware/工具/控制/消息观察。
- 初始红灯：持久测试因模块不存在收集失败；旧CLI默认无DB、无法识别临时/resume参数（3 failed/3 passed，旧parser拒绝未知参数导致冲突测试已通过，不能算新实现证据）。最小实现后15例通过。旧30个CLI用例显式临时入口，原断言不变；未改conftest。
- 清理失败反例先红：图结束后先存ready，随后turn快照清理失败，会留下不应恢复的状态。改为共用_finish_run先关键清理、再最终确认、最后释放持久busy并报告；旧内存/审批/控制/异常测试通过。主异常优先于次级存储错误，不覆盖失败类型。
- 其他先红修正：失败run擦除最近确认ID→保留旧确认指针但状态仍受限；CLI选模写入错误未安全处理→保留原选择并提示；公开WAL未在连接前检查→验证既有附属文件；断链DB被当空库→先交存储安全检查。跨进程探针离线模型的*args遮蔽外层Namespace导致AttributeError，最小诊断确认receiver_type=tuple后改局部参数名，未改存储策略或放宽测试。
- 新32例（17会话/存储、12CLI、3进程/预算）覆盖工具历史/ID/摘要/选模保存、ready/running/failed/interrupted/manual受限状态、开始/最终写入与清理失败、原异常/次级错误、SQL只读、schema/链接/权限/实例锁、默认/临时/空列表/无参选择/非TTY/取消/直接resume/参数冲突。后补场景不全部算先红。
- 最终 `.venv/bin/python -m pytest -q` → `781 passed in 19.13s`；compileall →0；pip check → `No broken requirements found.`；全部requirements版本核对PASS；DB/lock/WAL/SHM私有权限探针PASS。Python触及文件≤500行，显式结构检查；项目没有独立应用打包构建目标。
- `.venv/bin/python -m scripts.probe_persistent_session`（由进程测试调用，纯假模型）和`.venv/bin/python -m scripts.probe_persistent_session --real`（本轮一次默认heiyu-gpt-5.5）均返回：

```text
persistent_session_probe=PASS mode=real two_processes=True session_id=stable thread_id=stable run_id=new checkpoint=advanced history_used=True
```

离线mode字段为offline。真实父进程95秒、各worker一逻辑请求，两进程独立加载模型/DB，随机合成事实只出现在首进程消息，恢复进程问题不含答案；检查精确回答、ID稳定和checkpoint推进，stdout仅统计。本轮真实没有工具副作用/审批/压缩/故障恢复证明，这些本地场景与生产/业务效果分别报告；SDK HTTP重试尝试未单独统计。测试/探针DB全部临时目录，未创建用户默认库或修改.env。

## 当前边界与下一步

当前仅macOS/Linux fcntl单实例锁、本地轻量单活动run，非SaaS/多进程写入平台；同进程审批恢复通过，重启时仍只允许ready。running/failed/interrupted/review_required留记录阻断，不做自动修复、工具重放或回滚。busy/队列/锁不持久，用户排队消息跨崩溃交付未实现。

数据库schema与框架表版本迁移/备份清理、重启审批/异常恢复、全历史展示/分支、多用户隔离、长会话存储增长与生产压力、原生异步和活动取消另片。持久状态确认与事件可靠送达分开，发布故障不构成数据库回滚；外部工具副作用仍不能与checkpoint作一个事务。保持stdio观察不代表持久事件。

用户仅参与需求/选型/CLI行为确认，尚无本片独立实现/面试追问证据。下一步按核心数据流一起读创建→run收尾→restore，随后按既定顺序复盘工具/上下文生产能力；会话高级恢复另行设计，不自动扩大本片。

最后预算反例先红：探针总时限已过时仍因max(1, remaining)启动worker；改为先检查截止时间，剩余≤0不再发请求，正数才传timeout。故障测试使用局部时钟替身不污染全局time模块。最终781例在该修正后通过；先前真实正常路径证据保持，不追加重复真实请求。
