# Spec127-11：记忆后台状态与诊断收敛

2026-11-04，用户批准“详细错误落日志，前端只解释基本状态”。不改变记忆保存、来源/权限/CAS校验、受理进度、批次额度和重试语义；不改公开接口、数据库、真实配置或模型调用次数。

## 当前链路与修改

MemoryJobs → memory_job_runner.drive/_batch → 概览保存及逐条 proposal_request/MemoryAccess.query → 独立保存计数 → result_status/termination → BackgroundTasks。

正常例子：模型无新建议、概览保存成功 → completed → “本次记忆整理已完成”，不能暗示必定新增记忆。失败例子：概览成功、建议引文不匹配 → 原 memory_proposal_invalid 拒写、失败计数增加 → partial → “已保存部分整理结果，其余内容未完成”；日志包含任务ID、批次、建议序号、校验细分原因，成功结果继续保留。

## 后端

- memory_batches.proposal_request 保持公开错误字符串与类型，内部异常附带仅来自代码常量的诊断原因：字段、更新目标/范围、来源键/重复、引文不匹配/不唯一、写入请求构造。worker 为实际消费者；不添加新的异常层或解析框架。
- memory_job_runner 在现有失败日志中增加批次、阶段、建议序号及内部细分原因；批次整体失败记录发生阶段，原异常继续传播到既有终局处理。一次任务结束写一条 INFO 汇总：task_id、result_status、termination、batches、overview_saved、memory_saved、failed、skipped_count。
- 不记录模型返回、来源文本、完整异常字符串/Pydantic输入、凭证或原始供应商响应。原计数/事件/异常类型与API输出保留。
- 接线复核发现共享服务默认WARNING会过滤INFO汇总；仅在service.__main__为memory_job_runner启用INFO，其他日志级别不变。日志投递复用diagnostics.safe_log，日志设施异常不改变已保存结果或掩盖主异常；独立进程启动接线和故障注入先红后绿验证。CLI/Web均委托该共享服务，无需修改它们的启动器。

### 引文格式诊断（2026-11-06）

仅在逐字查找失败时，对指定的当前批次来源片段和引文移除空白与反引号，再统计连续子串命中。原失败日志保留 `detail=quote_not_found`，追加 `quote_format_match=unique|ambiguous|none`。规范化后引文为空时归为 `none`。唯一命中仅是排查信号，写入仍要求逐字且唯一匹配。日志只记录分类，不记录引文、来源文本或偏移。

涉及 `memory_batches.py`、`memory_job_runner.py` 和诊断测试。验证同一来源唯一命中、多处命中、无命中、空引文以及不会跨来源搜索，并检查 worker 的原拒写、部分完成状态和日志脱敏。

## 前端

仅记忆任务使用紧凑状态与一句说明；Shell输出及归档保持。任务ID保留用于日志关联；原始JSON摘要与原始termination不再显示。

状态：等待中、整理中、停止中、已完成、部分完成、失败、已停止、结果未确认。优先使用活动/未知生命周期；终局使用result_status，无明确结果时保守显示未确认。

终局说明：完成“本次记忆整理已完成”；部分完成“已保存部分整理结果，其余内容未完成”；失败“本次整理未完成”；停止“本次整理已停止，停止前已保存的结果保留”；未确认“暂时无法确认本次整理结果”。原因按上限（batch_limit/timeout）、来源变化（source_changed）、停止（user_stop/application_close/root_not_completed）、部分内容未保存（partial+completed）、其他异常归并；成功不额外显示原因，未知值不原样输出也不猜故障类型。未知生命周期不能被旧completed结果覆盖。

## 范围与验证

生产文件：memory_batches.py、memory_job_runner.py、BackgroundTasks.tsx、service/__main__.py。扩展记忆合同与worker测试、后台面板测试；日志专项独立文件以遵守500行上限，不改fixture/conftest。更新handoff。先记录红灯，再实现，再专项/完整后端与前端测试、标准构建和diff检查。

覆盖：无新建议正常完成、概览成功/建议全失败、格式和来源各分支、安全日志、权限拒绝、并发保护原回归、批次上限、停止/未知、终局汇总计数、Shell输出保留。测试证明状态/诊断，不证明真实提取质量改善；无真实模型请求。

## 本机结果

2026-11-04：初始后端12项红灯、前端12项红灯（含2项旧展示合同迁移）确认后实现；正式入口日志过滤与日志设施异常另2项先红后绿。新增后端17项、前端10项；保留Shell文本转义/停止及记忆保存/事件/CAS原断言，没有改测试基础设施。

最终完整后端四分片1583+1538+1662+1611=6394通过，零失败/错误/跳过；前端72文件609项通过；标准TS/Vite构建通过，仅原有chunk大小警告；compileall、500行检查、diff检查通过。诊断专项17项通过，包含独立进程正式入口INFO接线与日志故障注入。最终源码/测试哈希一致，证据见[verification.json](../../build/memory-status-20261104/verification.json)。无真实供应商调用、用户数据/配置变更、提交或发布；本片不声称真实记忆提取质量改善。
