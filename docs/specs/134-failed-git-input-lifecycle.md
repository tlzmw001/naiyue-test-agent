# Spec134：失败的 Git 输入准备与回收

状态：2026-10-28 初版失败回收已实现；后续 Spec138 将回收扩展为按引用保护的定向 retire，取代本文仅 failed 可回收的合同。历史验证记录保留。本片不发布或修改真实配置。

## 当前主链与问题

新建沙箱会话的 request_id 固定派生 session_id、input_id 和 binding_id。`InputService.prepare` 先在项目注册库预留 `preparing` 记录，再调用 `SnapshotBuilder.prepare`；后者在应用数据目录创建 `<input_id>` 并写独立 Git 对象库。构建失败后记录转为 `failed`，同一 input_id 以后直接返回旧错误。失败若发生在建目录后，目录保留；直接重进 builder 又被不带 `exist_ok` 的 mkdir 拒绝。所有失败记录计入每项目 `max_snapshot_records`，当前删除失败会话也不回收输入记录。

## 目标合同

- 同一 request_id 仍重放原会话及其固定失败结果；不自动读取变化后的项目源码。显式重试使用新 request_id、新 input_id。
- 准备失败且 `snapshot is None` 时，仅清理该次应用拥有的半成品目录；不得碰来源仓库、ready 快照、其他 input_id 或 envd 卷。清理失败记录安全日志，原失败保持主异常。新的 input_id 可正常准备。
- 删除已失败的沙箱会话且确认无已绑定环境后，删除其 `failed` 输入记录，释放记录容量；该 input_id 的目录只在同样的归属和失败状态核验后清理。ready/preparing、正在执行或身份不匹配的记录不得删。删除会话的其他状态、历史数据和环境检查保持原合同。
- 由于会话库和项目注册库是两个数据库，删除后的输入记录回收是独立收尾：失败必须记录，不把已删除会话伪报为未删除。若回收失败，保留日志供人工定位；当前不自动扫描孤儿记录，也不扫描或删除来源项目内容。

## 正常与失败路径

正常：新请求 → 预留 → builder 生成快照 → 保存 ready → 创建并安装环境。失败：新请求 → 预留 → builder 报预算或 Git 错误 → 保存 failed → 清半成品目录 → 会话显示原错误；新请求可重试。若目录清理失败，保存的 failed 记录仍阻止同 ID 执行；日志标明清理失败。会话删除时先完成现有会话删除，再按项目和 input_id 核对 failed 记录并回收；失败只留下可诊断残余，不改变已完成的会话删除结果。

## 影响与验证

预计涉及 `application/environments/inputs.py`、`storage/git_inputs.py`、`application/sessions/session_persistence.py` 和相应测试。内部新增受限回收入口，不修改公开 API、schema、配置或依赖。先用失败测试覆盖建目录后失败、同 ID 不重试、新 ID 成功、删除失败会话释放容量、ready/其他项目不删除、清理错误可观察；再实现并运行相关及全量测试。文件删除只针对已验证的应用数据目录，需核对符号链接和并发锁。

## 当前验证

失败测试先复现半成品目录及失败记录残留；实现后覆盖准备失败目录清理、同 ID 幂等、新 ID 重试、失败会话删除后回收、ready 记录保护和符号链接拒绝。与快照/进程/镜像构建测试合并的定向回归为 127 passed；完整后端 5713 passed，见 `build/fix-052-aitest-053/backend-tests.log`。残留回收失败会记录错误类型；目前无后台孤儿扫描。
