# Spec138：沙箱输入定向回收与创建所有权

状态：2026-10-28 已实现，定向回归通过；完整后端与镜像交付由本轮主任务统一验证。本片不发布、不修改真实配置。

## 当前链路与问题

`create_sandbox_session` 保存会话 setup，`InputService.prepare` 在项目注册库预留 preparing 后生成输入，随后创建环境并安装。输入容量按项目全部记录计数；当前删除会话仅回收 failed。因而正常 ready 输入在环境销毁、会话删除后仍永久占额；reserve 后进程中断的 preparing 也无法释放。原 ID 不重跑仍然是正确合同，不能通过重复读取已变化源码恢复。

低层 `EnvironmentAPI.create` 仅在创建前查询会话存在，随后异步创建期间未占有会话 history_lock。会话删除可穿过这个间隙，留下没有 owner 的环境。此入口当前仅有验收脚本和测试消费者；Web 正式入口已经使用 history_lock。

## 本片合同

- 新增内部恢复领域入口 `EnvironmentAPI.retire_input(project_id, input_id) -> bool`，会话删除收尾使用同一入口。只针对调用者明确给出的 ID，不自动扫描旧孤儿，不新增 UI/CLI，不改变容量定义。
- 回收前确认：目标项目/输入身份有效；没有任何会话 setup 引用该输入；没有未销毁环境引用；没有关联 git_result 操作或交付记录（包括 failed/unknown/已完成及 discarded 的历史审查）；没有活动 prepare 或输入安装尚未登记引用。保留已销毁环境及普通操作历史，但它们本身不继续占有无结果的输入。
- `preparing` 不因状态名直接删除。取得同 input 锁后才核对全部引用；活动 prepare 必须先结算。注册库已有 OS 排他锁保证同数据目录不会有第二个应用 owner。正常重开后不再有旧 prepare owner，只有无引用 preparing 才能回收。
- ready/preparing/failed 均遵循相同引用门。存在引用返回 false；身份/IO/存储验证失败按原异常拒绝且可观测。安全目录验证与删除沿用现有应用目录保护，不触碰来源仓库、envd 卷、结果缓存或其他输入。先删除输入目录，再删除记录；中途失败保留记录供原 ID 显式再次回收。
- 历史已删除会话留下的孤儿由维护调用者显式调用同一 `retire_input`。公开 prepare_input 的独立调用可能尚未安装，因此不得自动假定无会话引用就是废弃输入。
- 会话删除已完成后回收失败仍记录安全日志，不伪报会话未删除。被结果/交付引用的输入继续计入原有保留额度；本片不引入结果清理或无限历史。
- 低层 create 在实际持有所属宿主 operation 与会话 history_lock 后复核会话存在，并保持至创建结算。删除也检查低层环境的 session_id 关联；创建已完成且环境未销毁时拒绝删除 owner。先删除获胜时，create 必须在第二次检查失败，不创建环境。

## 正常与失败路径

正常：ready 会话 → 销毁其环境 → 删除会话 → 定向 retire → 引用检查为空 → 删除输入目录和记录 → 新 ID 获得原额度。失败：builder 中断 → 重开显示原 preparing/unconfirmed → 删除会话 → 同样的无活动/无引用检查 → 回收；不重跑旧快照。若旧结果仍引用输入，删除会话成功但 retire 返回 false，原结果 diff/交付保持可用。

并发：prepare 持输入锁；显式 retire 等待其完成后核对引用。会话 setup 的首次保存也取得输入锁，避免新 owner 与回收交错。安装在同 binding 锁内短取输入锁，将 input_id 必要保存到环境记录后才释放；后续传输不持输入锁，retire 此时能看到环境引用并拒绝。不同 ID 不互相阻塞。

## 锁与影响

沿用 S1(history)、GI2(input)、DKR2(binding)、项目宿主 gate 与数据库短事务。会话创建为 S1 → GI2 → 会话注册短锁/DB；删除完成后直接借已加载宿主的 operation，为 S1 → GI2 → DB，不取得 host gate，不引入 S1 → G4 的反向依赖；公开 host close 仍拒绝存在活动 operation 的宿主。只有冷项目显式回收才在 host gate 内借项目库，为 host gate → GI2。低层 create 先取得并释放 host gate，再取得 S1，禁止 host gate → S1。安装为 DKR2 → GI2 → DB，释放 GI2 后才进入 SOP2；不新增 GI2 → DKR2/SOP2。目录删除与 Git/Docker IO 均不持数据库事务。

预计修改 inputs、EnvironmentAPI、git_inputs 仓储、SessionRepo 引用查询、execution_binding、session_persistence 及锁表。使用现有 schema/状态/公开签名；移除已被完整回收入口替代的 discard_failed/delete_failed 内部入口，并迁移其测试。

## 验证

先红后绿覆盖正常 ready 销毁删除释放容量、preparing 中断重开删除、显式旧孤儿回收、会话/活动环境/结果/交付引用保护、不同项目及符号链接保护、目录失败可重试、事件同步 prepare/install 与回收互斥、低层 create/delete 两个获胜顺序。再运行现有输入、结果、交付、会话与生命周期回归；本片不以替身测试代替真实 Docker/公司系统验收。

实际记录：新增最小行为测试先得到 **5 failed**（ready/preparing 删除未释放、缺少显式 API、低层 create 未保护 owner）；最小实现与原输入/API 回归为 **14 passed**。后续补充引用、事件同步、冷项目、异常保护后，以下组合 **68 passed in 55.95s**；compileall 与限定范围 `git diff --check` 退出 0。

```sh
.venv/bin/python -m pytest -q tests/test_sandbox_input_retirement.py tests/test_sandbox_create_ownership.py tests/test_git_input_service.py tests/test_sandbox_sessions.py tests/test_sandbox_api.py tests/test_git_result_service.py tests/test_git_delivery_service.py tests/test_sandbox_lifecycle_races.py tests/test_sandbox_lifecycle_application.py tests/test_sandbox_operation_recovery.py tests/test_sandbox_session_http.py
```

测试构造纠正：首次新增冷项目用例给了不存在的 project，实际正确报 project_not_found；改为两个真实登记项目验证交叉引用保护。单独 receipt 保护夹具补齐保留的 destroyed 环境，避免夹具自身造成 shutdown 找不到环境。关闭竞态探针证伪了“公开 close 会进入排空”的假设：实际先以 project_in_use 拒绝活动删除；回归验证该真实合同及删除完成后成功关闭，没有放宽生产检查。最终全部断言保留引用保护、错误、身份与无新增副作用证据。

组件接线：领域 retire_input 既是删除收尾入口也是历史孤儿的按 ID 恢复入口；原 discard_failed/delete_failed 已移除。正常低层创建与删除共用现有 history_lock，原验收脚本签名不变。结果/交付引用保留、destroyed 环境新捕获在操作 reserve 前被拒绝；已完成结果在会话删除后仍可 diff 并建立本机分支，测试覆盖此完整链。
