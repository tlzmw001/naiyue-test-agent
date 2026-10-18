# Pi事件、会话保存、数量限制与用量统计核对

日期：2026-10-17。Pi本机源码版本a69bef789；当前项目源码亦已核对。本稿为来源限定的源码研究与用户确认的后续设计记录，不是Pi运行验收、账单证明或已实施声明。

## 1. 实时事件和会话文件是两种东西

Pi主Coding Agent链：Agent.processEvents更新状态并await listeners→AgentSession._handleAgentEvent先发扩展/界面通知，再在message_end时调用SessionManager.appendMessage。普通user/assistant/toolResult保存为message条目；custom_message、模型/思考切换、compaction、branch_summary等走专用append入口。message_update/token delta、工具进度等实时事件不因此全部永久保存。

SessionManager._appendEntry更新fileEntries/byId/leafId并调用_persist。持久模式首次出现assistant之前可暂存内存；首次flush写入全部条目，后续JSONL追加。--no-session/inMemory不落盘。通知发生早于该处理函数的持久化段，不能将界面看见事件直接称为文件已保存。

典型条目包含type、id、parentId、timestamp及message；assistant message带provider/model/usage/cost等。parentId组成会话树，不等于完整模型/工具执行span日志。

源码：

- [Agent事件处理](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/agent.ts:538)
- [AgentSession事件和保存](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/agent-session.ts:621)
- [SessionManager持久化](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:1015)

## 2. 数量和容量边界

本机所查SessionManager保存/加载/设置链未见按条数淘汰会话历史、仅保留最近N个run或定期自动清理旧会话的实现。它追加JSONL并保存全量fileEntries及byId索引；loadEntriesFromFile分块读取直到EOF，并把解析条目加入数组。统计/树读取仍消费全体条目，不能据此宣称大历史内存有固定上限。

SESSION_READ_BUFFER_SIZE=1MiB是读取块大小；MAX_SESSION_HEADER_SCAN_BYTES=1MiB限制头部发现扫描；MAX_CONCURRENT_SESSION_INFO_LOADS=10限制列表信息读取并发。三者都不是会话最多1MiB、最多10个会话或只保留固定数量事件。

AI的EventStream用queue/waiting交付瞬时事件，消费者迭代时shift移除；该类没有固定maxlen和持久重放合同，慢消费者可造成排队。它不能与本项目Web用于断线补读的256条环形队列直接等同。

主SessionManager压缩时追加compaction记录，buildContextEntries/buildSessionContext根据当前叶子、summary和firstKeptEntryId装配模型上下文，旧消息仍在文件；压缩不是删聊天档案或清理磁盘。

源码：

- [文件加载](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:514)
- [当前上下文装配](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:419)
- [追加压缩](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:1097)
- [全条目读取](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/session-manager.ts:1301)
- [瞬时事件队列](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/utils/event-stream.ts:4)

范围限制：这里核对主Coding Agent的AgentSession/SessionManager链及相关队列，不把一个文件的未发现推广成所有远程模式、扩展和存储后端永远没有清理策略。

## 3. token和费用统计

getSessionStats遍历sessionManager.getEntries，即全会话条目而非当前压缩后的上下文；累计assistant usage、带usage的toolResult、compaction和branch_summary。压缩前已发生的调用仍计入总数；当前上下文大小另由getContextUsage表达，不把累计消耗当作上下文窗口占用。

Pi Usage将input、output、cacheRead、cacheWrite分列；usage.cost由models.calculateCost按模型价格配置计算，支持价格档位和部分长TTL写入差异。getUsageCostBreakdown按模型归属与Tools/summaries分组。源码中的“billed”注释表达统计意图，不证明网关账单准确；缺失/默认零价格可能无法表达未知费用，本项目须保留未知状态。

LangChain标准化input_tokens通常已包含缓存读取/创建；当前Anthropic适配器也将三部分合成总输入。因此不能直接搬Pi的input+cacheRead+cacheWrite公式后再次加到LangChain input_tokens，避免重复计数。主、子、摘要、Auto及SDK失败/重试的账目范围需本项目单独定义，不能仅汇总最终聊天回答。

源码与现有测试入口（本轮未运行）：

- [全部会话统计](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/agent-session.ts:3247)
- [用量归组](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/usage-totals.ts:37)
- [价格计算](/Users/zmw/naiyue-personal-workspace/pi/packages/ai/src/models.ts:878)
- [统计行为测试](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/test/agent-session-stats.test.ts:100)
- [上下文装配测试](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/test/session-manager/build-context.test.ts:69)

## 4. tracing边界

本机Pi另有agent/harness的TelemetryContext选项与pi.ai.request等span schema，以及packages/telemetry的NOOP和InMemoryTelemetryContext。当前核对主AgentSession→Agent事件链未见这些span启动helper的实际调用或默认持久exporter；不能把接口/内存实现存在说成CLI默认已保存完整追踪。

coding-agent/core/telemetry.ts的PI_TELEMETRY是install telemetry开关，不是“开启全部模型/工具链路持久追踪”的证据。会话树与usage也不能复原每次SDK尝试的耗时、错误及完整父子span。

源码：[span schema](/Users/zmw/naiyue-personal-workspace/pi/packages/agent/src/harness/telemetry.ts:42)、[默认noop](/Users/zmw/naiyue-personal-workspace/pi/packages/telemetry/src/noop.ts:19)、[内存实现](/Users/zmw/naiyue-personal-workspace/pi/packages/telemetry/src/memory.ts:189)。

## 5. 当前项目的对应边界

- Web settings默认event_capacity=256、retained_runs=16。每run事件队列deque(maxlen=256)用于SSE临时补读，不是永久档案；_prune在新run注册时按已完成任务清理，活动任务另计，不能说整个系统绝对只存在16个run。
- checkpoint/会话元数据已经SQLite保存；原AIMessage usage及有限context_snapshot也可随当前checkpoint保存，但不是跨压缩、跨失败/摘要/审查的完整用量账目。
- 现有session_id/run_id/turn_id/model_call_id/tool_call_id和AgentEventSource已有逻辑关联，不是完全没有链路标识；尚无可查询的持久运行轨迹与完整尝试层追踪。
- 重启丢失的是Web交付缓存/回执，不等于所有聊天/会话状态丢失；跨进程请求幂等仍是独立能力，落盘事件不自动提供exactly-once。

## 6. 用户确认的保存与淘汰方向，尚未实施

用户2026-10-17确认记录以下方案。复用当前工作区SQLite；聊天、用量、诊断是不同数据职责，不据此新增独立服务。正式接口、表结构、必要保存合同及迁移测试须在实施Spec中展开，本记录不构成修改配置或实现授权。

1. Spec101独立聊天档案保存完整消息/工具结果与必要修订，数据库分页；压缩只改模型输入，不删原文。聊天原文和用量随会话保留，不按固定条数/天数自动淘汰，用户删除会话时清理其关联记录。
2. 按实际模型调用身份保存usage事实，覆盖主/子/摘要/Auto；聚合去重，压缩和诊断清理不扣除既有消耗。保存供应商返回口径和模型/价格版本，缺失usage或价格标未知，不补零；SDK内部不可见尝试不宣称已完整计费。
3. 持久轨迹只保存run/model/tool/approval/compaction/failure关键边界及关联ID、时间、状态和有限错误描述。正文/图片通过身份关联聊天档案，delta/高频进度主要实时交付。简单本机查询先服务故障定位；外部追踪平台及SDK逐attempt另按需求讨论。

初始可配置预算如下，是工程起点，未经过实际负载测量，也未写入当前配置：

| 数据 | 保留与淘汰规则 |
|---|---|
| 聊天原文、调用用量 | 随会话保留，删除会话时清理；不与普通缓存/轨迹一起淘汰 |
| 每run实时事件 | 最多256条，并加4 MiB编码载荷预算；任一超限即移除最早事件 |
| 已结束run的Web交付缓存 | 最近16个；活动run另计，清理旧缓存不改变执行/审批权威状态 |
| 每工作区持久诊断 | 最多30天，并设256 MiB有效轨迹载荷预算；先清过期，再按结束时间从旧到新整run清理 |
| 单run诊断 | 最多1 MiB，预留必要收尾空间；超限停止详细追加并显式标记不完整，保留终局摘要 |

缓存游标已过期时明确resync_required，重新读取当前状态/最近消息页，旧历史继续分页；不能把缓存缺口当消息删除。轨迹清理不拆散run，不淘汰正在执行的run；执行恢复、审批和必要保存继续由权威状态/checkpoint承担。启动、run收尾和诊断追加将超预算时小批次/短事务检查；清理失败须日志可见，可降级诊断故障不覆盖主执行结果。

字节预算只约束相应编码载荷，不代表Python进程RSS或整个SQLite/WAL/索引文件大小。SQLite删除后空闲页通常留待复用，文件不保证立即缩小，见[SQLite官方说明](https://www.sqlite.org/pragma.html#pragma_auto_vacuum)。聊天/用量长期保留仍有磁盘增长成本；空间查看、用户清理和必要保存失败合同须明确，不自动删除原文。

## 7. 当前checkpoint保存与清理边界

- 正式持久化工厂使用AsyncSqliteSaver.from_conn_string(storage.path)，与会话元数据共用同一个工作区SQLite文件；普通内存工厂不因此获得持久化。
- 当前官方SQLite saver以(thread_id,checkpoint_ns,checkpoint_id)为键保存checkpoint和pending writes。不同checkpoint_id会保留多版，不是只覆盖最后一份；一次run也可产生多份图执行checkpoint。
- 当前项目和所用SQLite saver未接入按条数、时间或容量的旧checkpoint自动修剪。压缩通过官方状态更新写入新的较小模型上下文，不清理旧checkpoint；关闭/重启也不触发历史淘汰。
- 用户删除会话时，SessionPersistence.adelete扫描历史中的子实例及当前内存实例，SessionStorage.delete在同一事务中删除主/关联子thread的writes、checkpoints和主会话元数据；保留其他会话。
- checkpoint不是诊断轨迹，不能套用第6节30天/256 MiB淘汰规则。后续修剪仍是独立待设计项，须保护最新执行状态、元数据确认锚点、审批/pending writes、恢复记录source_checkpoint_id和子实例归属/删除发现；不直接只保留最新一份或删除旧BLOB。Spec101完整聊天档案也须独立保存，不将旧checkpoint扫描当作稳定分页历史接口。

源码：[持久化接线](/Users/zmw/naiyue-test-agent/naiyue_test_agent/application/async_persistent.py:64)、[会话删除编排](/Users/zmw/naiyue-test-agent/naiyue_test_agent/application/session_persistence.py:35)、[事务删除](/Users/zmw/naiyue-test-agent/naiyue_test_agent/application/session_storage.py:209)、[手动压缩提交](/Users/zmw/naiyue-test-agent/naiyue_test_agent/agent_runtime/manual_compaction.py:68)。

## 8. 本轮证据

Pi部分只读核对本机源码与现有测试，版本a69bef789；Pi根目录未发现可直接执行的node_modules/.bin/vitest，不安装依赖、不宣称其测试通过。未执行真实模型请求，未修改Pi或产品代码/测试/配置、用户会话库。

本项目当前checkpoint边界另做临时SQLite探针：写3份不同checkpoint后重开仍保留3份，显式删除该临时thread后checkpoints=0、writes=0；无模型调用。已有确定性回归命令：

```sh
.venv/bin/python -m pytest -q tests/test_async_persistent.py::test_close_reopen_preserves_session_thread_and_history tests/test_persistent_sessions.py::test_compacted_summary_remains_actual_context_after_reopen tests/test_web_session_management.py::test_delete_includes_child_history_removed_from_latest_parent_checkpoint
```

实际结果：3 passed in 0.78s。仅验证已有持久化/压缩重开/关联子记录删除，不是第6节待实施方案的功能验收；文档另做引用/结构/空白检查。
