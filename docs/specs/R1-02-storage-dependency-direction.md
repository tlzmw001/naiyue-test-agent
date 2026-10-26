# R1-02：存储依赖方向与事务内职责

日期：2026-10-24。用户在第一片交付及第二片说明后授权“继续”。状态：已完成本机验收，未提交/合并/发布。基线是 `codex/project-execution-root` 的 d717d14，加已验收而未提交的 R1-01；本片保留第一片改动。

## 1. 合同与范围

按总体方案第 8.1 节：应用编排业务，仓储提供数据操作，Database 持有连接/锁/事务。storage 不直接或间接加载 application，application 不构造/执行 SQL。保留 schema 18、迁移步骤、错误 code/cause、公开 CLI/HTTP/工具参数及用户数据格式。不新增项目注册、多项目服务、Docker、依赖、用户配置或版本。

实施前 17 个 storage 文件有 43 条 application 导入。纯 SQL 模块和混合模块分别处理；仅改 import 名称无法解决记忆批次、写入和子实例投影的反向调用。

## 2. 逐模块迁移表

| 原位置（application 下） | 当前实施目标 | 职责 |
| --- | --- | --- |
| conversation/chat_queries, reply_storage, history_projection, history_queries | storage/conversation_sql/ 同名模块 | 档案/回复/可见文本/有界历史查询、原迁移 |
| subagents/subagent_storage | storage/subagent_sql.py | 子实例表、SQL/CAS |
| observability/model_usage_storage, diagnostic_storage | storage/observability_sql/ 同名模块 | 用量/诊断表、保存、查询 |
| memory/memory_schema, memory_lifecycle_schema, memory_overview_store, memory_queries, memory_personal_upgrade | storage/memory_sql/ 同名模块 | schema、FTS、概览、查询、个人旧格式备份 |
| memory/memory_lexical 的纯词法/摘要方法 | contracts/memory_lexical.py | 共享且不依赖连接的词法事实；应用 feature/select 保留原模块 |
| memory/memory_lexical.configure | storage/memory_sql/lexical.py | SQLite 函数注册；只调用共享纯函数 |
| errors 中 SessionPersistenceError/StorageStartupError | storage/errors.py | 唯一异常定义；application.errors 保留其余真实应用异常 |
| memory_queries 中 MemoryReadError/CONDITIONS | contracts/memory.py | 唯一记忆错误及条件字段合同 |
| sessions/session_recovery.recovery_record | storage/recovery_records.py | 持久恢复事实解析/归属检查；恢复决策和动作留应用层 |
| subagents/subagent_history 的 identity/target | contracts/subagent_identity.py | 同一算法供 SQL 查询与应用委派使用；列表合并留应用层 |
| agent_runtime/events 的两个 ModelInvocation payload | contracts/model_events.py | 原验证和唯一类型身份；events 保持实际生产者/消费者使用的导出 |

所有现有绝对/相对导入、生产与测试猴子补丁、脚本和当前文档同步迁移。已被替代的内部模块不留转发壳。新增 SQL 子目录按聚合组织，避免把几百行 SQL 合并为超 500 行文件；现有 Repo 和 ProjectStore 装配保持。

## 3. 混合职责与窄事务接口

### 3.1 记忆批次

应用 `build_batch(memory_repo, ...)` 管输入校验、token 预算、跳过原因、模型视图和输出；仓储 `batch_reader(sensitive_pattern)` 打开一个读取事务、注册原有敏感内容布尔 SQL 函数，提供 ArchiveReader 的 parent/binding/head/next_visible/visible 等有限方法。SECRET 使用已存在的不可变正则作为参数，沿用原 SQL 回调语义，不新增可发现/动态回调框架。

原算法的完整可见文本敏感检查仍在 SQLite 切片前执行。原始消息 JSON 和隐藏内容不进入模型批次。关闭连接/BEGIN/查询异常继续记录 memory_batch_read_failed，不能伪装空结果。原 memory.build_batch 仓储反向调用删除，作业和测试改为应用入口。

### 3.2 记忆修改

MemoryAccess._mutate → `target.edit_with_sources(archive)` → 在同一个作用域取得 MemoryWriter 与 ArchiveReader → 应用 memory_mutations 执行规则及 source.resolve → writer 保存 → fit_mutation → 退出作用域提交。

- 目标是项目库：一个写事务和同一连接，版本、来源、记忆/版本/来源/FTS/遗忘抑制一起提交或回滚。
- 目标是个人库：先项目来源读事务，再个人目标写事务；顺序保持，只有个人库发生写入，不承诺跨库原子事务。
- writer 只提供当前行/来源/抑制/匹配/保存/遗忘所需数据操作，不把 connection 或 SQL 返回应用层。
- disable/forget、TTL/候选/转正、新证据、用户来源、敏感检测和预算规则保留应用层；依赖的读取均在原事务范围内。
- `fit_mutation` 继续在提交前运行，任何错误都回滚；不把它降为普通投影故障。

### 3.3 查询投影

memory_results.source_reference 的 SQL 与持久来源核验下沉；输出裁剪/公开结果保留。仓储对子 Agent 提供同一个读事务内的档案记录与登记快照；调用方在应用层 merge_registered_subagents，不再由仓储调用应用合并函数。history 查询原有游标/文本可见性合同整体归查询实现。

## 4. 正常与失败主链

正常 run：SessionPersistence → RunUnitOfWork.finish_run → 单个 Database.transaction → 同层 reply/subagent/chat SQL → 更新会话 → 提交。表与事务没有重建，只调整依赖位置。

失败 run：最后会话 UPDATE 注入 SQLite 错误 → 聊天/回复/子实例和会话全回滚 → SessionPersistenceError 的 code/cause 保留，不能改成各 Repo 单独提交。

正常记忆：同事务读取当前版本和来源 → 原业务规则 → 保存全部投影 → 必要预算检查 → 提交。失败：来源过期/版本冲突/中途 SQL/必要预算错误 → 保留原异常、回滚所有目标投影，锁释放后可重试。

## 5. TDD、验证与清理

先新增失败测试：storage 绝对/相对/函数内反向导入；新 Python 子进程逐一导入存储模块时不加载 application；应用层没有 SQL 执行/构造。检查器本身用合成片段验证相对导入、动态导入和 _execute 非 SQL 的区别。

行为复用真实 SQLite 的 finish_run 回滚、子实例陈旧 CAS、个人来源过期/中途写失败、NUL 可见文本、备份恢复/旧库迁移；新增窄记忆事务内异常和异常身份验收。迁移测试 helper/故障注入位置只为保持同一行为验证，不能放宽断言。

最后：专项 → 全后端 → 全前端和 TS/Vite → 标准 Mac ARM64 wheel/Twine → 导入/旧消费者/AST/500 行及差异检查。使用既有 native-assets 的 rg/fd，无供应商/真实云资源依赖。记录运行结果后更新总体方案/handoff；下一步只说明 R1-03，获准前不实现。


## 6. 实际接线与旧路径清理

正常写入例子：MemoryAccess.query 校验当前会话/项目/请求 → _mutate 选择项目或个人 MemoryRepo → edit_with_sources 取得 MemoryWriter、ArchiveReader → memory_mutations.commit 读取当前版本和已保存来源、执行原业务规则 → writer.save 同事务更新记忆/版本/来源与 FTS 触发器 → memory_results.fit_mutation → 事务提交。

失败例子：writer.save 已插入但 fit_mutation 抛出 MemoryReadError → 同一个上下文退出时回滚全部目标表 → MemoryAccess 保留原异常 → 源和目标锁释放 → 同请求可重试且仍得到 version=1。新增测试同时覆盖 workspace/personal，末尾检查所有投影、事务状态和异常对象身份，避免检查器被隔离后产生假通过。

批次例子：memory_job_runner → 应用 build_batch(repo,...) → repo.batch_reader(SECRET) → ArchiveReader.parent/binding/history_version/next_visible/visible → 应用按预算装配 sources、next_position、complete、skipped、history_version → 读取事务释放。parent 返回 thread_id/conversation_revision/project_id；next_visible 仅返回白名单投影和敏感布尔值，模型输入不含原 JSON/隐藏字段。预算算法执行期间事务状态由新增测试观察。

保留的同库收尾：SessionPersistence → RunUnitOfWork.finish_run → write_chat_updates、reply_storage.finish_actions、subagent_sql.finish_parent → 会话 UPDATE → 一个事务提交。未改变 Database.transaction 或 schema 18；原 DDL 与版本步骤逐字面值核对保持。

删除的旧实现：12 个 application SQL 模块迁入领域 SQL 目录；MemoryRepo.build_batch / mutate_with_sources 的反向调用删除；应用 memory_results.source_reference 移至 storage/memory_sql/sources；session_recovery.recovery_record、subagent_history 的身份算法和 application.errors 的两个存储异常改为唯一低层定义。旧内部路径不保留转发壳。

确实保留的入口：应用 memory_batches.build_batch 管预算和模型装配；memory_mutations.commit 管转正/TTL/来源策略；subagent_history 保留档案列表投影；agent_runtime.events 继续提供真实事件消费者使用的两个共享 payload；恢复分类/动作仍在应用层。仓储 subagent_snapshot 返回同一读取事务的档案与登记快照，由两个实际应用调用方合并。

消费者迁移覆盖生产、CLI/Web 错误捕获、release/probe 脚本、导入结构检查及猴子补丁。两个批次故障注入从旧仓储方法移到 memory_job_runner.build_batch，发生时点和原业务断言保持；独立进程测试的内嵌脚本也迁到 storage.errors。没有改 conftest、跳过测试或放宽断言。

## 7. 验证命令与结果

命令在重构 checkout 执行，rg/fd 使用第一片按既有 native-assets 准备的 build/r1-01-test-assets/bin：

```sh
.venv/bin/python -m pytest tests/test_storage_dependency_direction.py -q --tb=short
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-02-final-dist
.venv/bin/python -m twine check build/r1-02-final-dist/*.whl
```

本轮已观察的证据：架构测试首次 **3 failed / 5 passed**，分别对应反向导入、间接加载应用和应用 SQL。迁移首轮专项 **135 passed / 1 failed**，失败是结构测试旧路径；更新当前入口后扩大专项 **256 passed**，最后清理专项 **81 passed**。首次全后端 **4860 passed / 1 failed**，唯一失败是独立进程脚本内旧异常导入；迁移后该进程/架构专项 **11 passed**。中途回归为清理迁移残留主动中断，保留日志，不计通过；最终全后端 **4861 passed in 339.93s**，新增 11 个架构/事务参数化场景；最终代码固定后重新完整运行。

前端 **50 文件 / 424 passed**、TS/Vite 和标准 Mac ARM64 wheel 构建已通过；前端保留既有大包提示。最终 wheel/Twine **PASSED**；包内 **255 份 Python 逐字节匹配**，12 个旧 SQL 模块均缺席；R1-01 的 15 份实现/测试文件字节保持。AST/500 行、tracked diff、导入和 schema 字面值检查通过。日志均在 build/r1-02/，没有模型供应商、Docker/SSH 或其他平台验收证据。


交付状态：第二片只改变内部职责与导入路径，真实 SQL、错误、必要事务和模型调用合同继续由行为测试保护。没有修改依赖/版本/真实环境配置；没有提交、推送、合并或发布。第三片的登记与迁移说明已写总体方案第 8.2 节，尚未实现。
