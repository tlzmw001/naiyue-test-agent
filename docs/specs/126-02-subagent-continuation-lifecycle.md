# Spec126-02：子实例的保存与生命周期边界

状态：2026-10-22 已完成本机验收。承接已完成的[126-01](126-01-subagent-continuation.md)，主合同见[Spec126](126-subagent-continuation.md)。只实施第二片，不新增列表工具、事件/UI 合同、配置、用户直聊或自动恢复执行。

## 当前主链与本片修改

正常：`subagent` → `subagent_records.admit` 重新验证当前角色/模型/权限与已确认 checkpoint → 新委派 → 子保存 pending_parent → `collect_chat_updates` 收集本 run 子 scope → `SessionStorage.finish_run` 同事务确认父状态、子归档与 ready。正常关闭后 `arestore_session` 仅装回父元数据，下一次显式调用才运行同 child thread。沿用 schema14，不增加第二份权威缓存。

失败：claim 后进程退出留下 running；子保存后父提交前退出留下 pending_parent；这两种重开仅允许原恢复流程配对父工具结果，不执行旧子工具，也不把实例推断为 ready。父事务提交后崩溃的 ready 可在显式新委派时复用。三个位置用真实子进程强制退出验证。

审批组合边界：子实例的 parent_reply_id 优先来自可信父图 `active_reply_id`，它跨根审批恢复保留，不能依赖新 ActiveRun 的未初始化 reply_id。内部 `SessionStorage.finish_run` 增加仅供活动父收尾传入的 `subagent_run_id` 关键字；事务重验它与存储 run_id 一致后才能确认子 ready。`SessionPersistence.afinish` 经过当前活动 run 校验后传入；普通恢复和恢复审批均不传，因此只保存恢复事实。公开应用/工具 API 不变。

父压缩后当前 prompt 可能不再含创建调用，当前 `aget_subagents` 因此遗漏实例。查询改为登记事实与旧调用推导合并；登记优先，历史未登记实例仍只读。`chat_queries.subagent_records` 保持指定 root thread 的分支引用查询，不能改成全会话列表。归档继续使用 01 的本 run 登记 scopes，未触碰实例不重扫。子压缩不换 thread，新委派重置本次模型计数，同次压缩不重置。

最后回复重新生成：`session_regeneration.regenerate` 准备新 root → `reply_storage.activate` 原事务中，以登记的 `last_parent_reply_id` 确认实际执行归属，将被丢弃 reply 触碰的实例置 retired、递增 version。更早前缀引用过的实例仍保留历史，但不可续跑；未触碰实例不退役。仅被丢弃部分引用的实例沿原 obsolete_children 删除档案，同时清空登记 checkpoint 指针，允许原修剪删除其 checkpoint。拒绝调用不改变实例最后真实委派归属。事务失败整组回滚。外部工具副作用不会回滚。

修剪以同一库存快照收集登记指针，短事务重查登记版本/指针，发生变化则保守跳过；原数量/字节预算不变。备份检查 schema14 的字段、登记归属和非空 checkpoint 引用，不反序列化载荷。通过实际备份→还原→重开→同 ID 新委派验证，而非仅检查表存在。

## 接口及文件

- 保持 `subagent`、应用查询、Web 路由与公开调用签名；复用现有登记读取，在原列表投影合并元数据。
- `subagent_history.py`：共享列表投影；`session_storage.py`、`async_application.py` 接线。原 thread 引用查询仍供重新生成使用。
- `reply_storage.py`：原 activate 事务补实例退役及完全孤立指针清除。
- `session_subagents.py`：跨审批保留逻辑回复归属；`session_persistence.py`：显式传入当前活动父 run 的确认权。
- `checkpoint_retention.py`：登记库存与事务复查；`storage_backup_schema.py`：登记归属验证。
- 确定性测试覆盖正常重开、角色/模型/当前许可重验、父/子压缩、前缀续跑后重新生成、未触碰实例、拒绝调用、事务回滚、修剪/删除/备份还原；单独子进程测试三个崩溃位置。

## 验收与限制

先观察本片新增行为测试的预期红灯，再作最小实现并跑关联回归、全后端、前端与标准构建。必要保存失败不能降级为成功，观察/修剪跳过不得改变成功结果；测试显式断言事件与模型/工具未重放。所有测试均使用临时库与本地确定性模型，不改真实配置、用户库、依赖、版本或并行文件，不提交或发布。

既有消费者迁移：真实崩溃测试的 finish_run 注入 lambda 接收新增内部关键字，以继续在原提交前退出点退出，不改变退出码/恢复断言；Web 删除用例保留父 prompt 已无引用、checkpoint/writes 全删及模型次数检查，将旧“子列表为空”改为登记仍返回同一个完整实例，并增加登记删除断言。未放宽失败边界或修改公共测试基础设施。

本片不承诺跨连接原子提交，不自动修复 running/pending_parent，不重放工具，不回退或 fork 子 checkpoint。列表可找回身份的底层查询完成后，模型可用的 `subagent_list` 与侧栏不可续跑原因在 126-03 接入。

## 当前可追踪的正常与失败例子

正常例子取自 `test_root_approval_resume_keeps_logical_reply_for_instance_retirement`：前一轮创建 worker，模型从工具结果获得完整 64 位 agent_id；下一轮主 Agent 的外部 read 进入审批，批准后继续该 worker。`session_subagents.prepare_subagent_instance` 从可信父图 active_reply_id 取得同一个逻辑回复身份，登记 last_parent_reply_id；新的父 run_id 与逻辑 reply_id 是两个不同字段。子完成后登记 pending_parent，活动父 `afinish` 提供 subagent_run_id，原 finish_run 事务同时提交子 head、ready 与父档案。用户随后重生成此逻辑回复，activate 用登记的真实最后委派归属将该 worker 退役；即使最早创建调用还在前缀中，也不能再次继续，子历史仍可读。

失败例子取自 `test_restore_approval_cannot_confirm_uncommitted_child`：子已完成并写入 pending_parent，父随后进入 read 审批，父必要保存前进程以 91 退出。重开读取官方 checkpoint，分类为 pending_approval；`arecover_session(..., action='restore_approval')` 只保存档案和恢复根审批，不传 subagent_run_id，因此登记整行、version 与 checkpoint 指针不变，模型/工具/事件均未重新执行。普通 continue 恢复同样不确认子；父事务已真实提交后退出的 ready 才允许下一次显式新委派。

正常例子中的必要操作是当前许可/角色/模型与 checkpoint 重验、子保存、父原事务确认；列表和历史查询只是只读观察。后台事件成功不证明必要保存成功。原工具副作用保留，故障探针用真正 write 和单独 executions 文件确认恢复前后恰好一次，没有通过模型报告推断文件执行次数。

## 实际验证与下一片

新增 22 项确定性测试。初始红灯确认压缩后列表丢失、退役/回滚缺失、备份归属缺口与修剪指针变化；崩溃测试确认普通恢复错误提升 pending_parent。独立架构复核再确认根审批恢复丢失逻辑回复归属、恢复审批错误提升 pending_parent，两项均先补双红灯回归再修正，并由安全/架构复核关闭。备份故障 fixture 使用已关闭、DELETE journal 的独立快照，遵循 check_snapshot 的读取前置条件；没有修改共享测试基础设施。

- `.venv/bin/python -m pytest -q tests --tb=short`：**4186 passed in 287.35s**。子进程 PATH 使用项目准备好的 build/release-native-0ff7df8/bin/fd。首轮 2 failed/4182 passed 的旧消费者迁移及新审批组合红灯日志保留，不计为最终成功结果。
- 续跑/生命周期/存储/崩溃/重生成专项 7 文件：**51 passed in 9.68s**；迁移后的真实崩溃/删除旧用例：3 passed in 3.20s。独立复核新审批组合：架构 2 passed in 1.75s、安全 2 passed in 1.78s。
- `npm --prefix frontend test -- --run`：50 文件、**412 passed**；`npm --prefix frontend run build`：TS/Vite 通过，仅原有 chunk 体积提示。本片无前端产品改动。
- `compileall`、181 份产品与 41 份脚本 AST/500 行检查、`pip check`、`git diff --check` 均通过；新增三个测试文件均小于 500 行。
- `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec126-02-m79lp9w3/dist-readme-final`：标准 wheel 构建/Twine 通过；181 份产品 Python、4 份前端资源和 README 元数据与当前源文件一致。SHA-256：c33e4a74c4f8d91a28f36705957f313192a731be75a6528e27055dc5f5f7a558。
- `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec126-02-m79lp9w3/dist-readme-final --output build/spec126-02-m79lp9w3/install-readme-final.json`：源外干净安装、原生工具、CLI/Web、SQLite/备份还原/现有 MCP 与后台任务门禁通过，MCP 合成业务调用 8 次、进程收尾通过，**0 次真实供应商请求**。它验证既有安装闭环；新的续跑专用安装 worker 留 126-04，不能据此提前宣布已完成。

证据目录：build/spec126-02-m79lp9w3；最终 backend-final.log、core-final.log、frontend-final.log、build-readme-final.log、wheel-readme-final.json、install-readme-final.json、verification.json。before/ 与 implementation.patch 区分本片和此前/并行脏工作。未改真实配置/用户库/依赖/版本，未提交、push 或发布。真实供应商、其他原机、专用续跑安装与浏览器多次委派验收另列；本轮没有用户掌握/独立调试证据。

唯一下一片 126-03：根专用只读 subagent_list(limit,before) 接本片可信登记与 legacy 投影，按完整条目/字节分页；max_list_items=20、max_list_bytes=8192、task_preview_chars=160 全部配置化；原 CLI/Web 按稳定 ID 聚合，多次新委派重置当前草稿/父绑定，审批恢复保留当前委派，旧事件/审批/停止不能命中新执行。复用现有侧栏，只展示可续跑候选/原因，不增加直聊或续跑按钮。本轮未实施 03。
