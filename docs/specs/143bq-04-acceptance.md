# Spec143BQ：实施与验收矩阵

状态：验收合同。第 6 节记录设计时的基线，实施和复审证据见 [主 Spec](143bq-cloud-results-and-input-reclamation.md#9-实施状态)。

## 1. S1 工具与保存

| ID | 场景 | 必须检查的结果 |
|---|---|---|
| R01 | 源码包、报告、二进制产物 | 同一工具/同一普通文件链下载，SHA/字节/绝对路径正确；结果不含文件 bytes |
| R02 | query 分页、无结果、多结果 | 空数组真实；分页有界；固定 result 后不跟随 latest |
| R03 | 同名不同内容 | force=false 为 conflict，目标零写；结果给出路径/原因/force_allowed |
| R04 | 首次明确强制覆盖 | 首次 force=true 可覆盖普通文件，不强制先冲突或生成确认票据 |
| R05 | 冲突后改名 | 新路径写入成功；原文件逐字节不变；源 artifact 名称/ID/SHA 不变 |
| R06 | 相同内容、重复 tool_call/operation | 已存在内容核验；重复同 ID 不启动第二份 IO；异参拒绝 |
| R07 | 路径与权限 | 目录外审批/deny、符号链接/重解析点、目录替换、敏感目录、父目录不存在、Windows 路径均按合同拒绝 |
| R08 | permit 绑定 | 换会话/项目/环境/revision/batch/call/force 均不能复用；记住普通写不扩大为强制覆盖 |
| R09 | scope | 子 Agent、Runner、普通/临时/错 Host 会话无能力；伪造 ID 即使直调应用端口也拒绝 |
| R10 | 多主会话、相同远端 task、退役绑定 | 只见自身关联；操作回执不串会话；只有完整本机缓存可按原身份继续保存 |
| R11 | 容量/网络/SHA/lease 失败 | 未验证文件不进用户目录；无假完成；当前结果失效不切换其他结果 |
| R12 | status 只读、服务重启 | GET/status 不启动 IO；等待接收纳入原 preparing 阶段活动/父删除保护；显式恢复核对原日志 |
| R13 | 并发 writer 与目标预检后变化 | 保留原 writer 互斥/观察复核；force 不绕过身份变化 |
| R14 | 下载受理后任务删除/结果轮换 | lease/排空合同生效；未完全接收则明确不可用；本机完整文件可继续校验保存 |
| R15 | 必要回执失败与观察失败 | 写入/日志收尾失败如实 partial/unknown；普通 UI 观察失败不撤销真实写入，不覆盖主异常 |
| R16 | 端到端主 Agent | 假模型按 query→download→status→Read 运行；最终读取真实落盘文件；未触发第二个 cloud 模型工具或自动唤醒 |

测试优先扩展 `tests/cloud/test_files_delivery.py`、`test_result_receive.py`、`test_receive_refresh.py`、`test_result_download_proxy.py`；新增 `test_result_tool.py`、`test_result_tool_permissions.py`、`test_result_tool_recovery.py` 覆盖新行为。新断言先看到正确红灯后实现，不能仅对函数名/字段存在做镜像测试。

## 2. S2 退出与兼容

| ID | 场景 | 必须检查的结果 |
|---|---|---|
| U01 | 新/旧任务普通产物手动多选保存 | 预览、目录选择、预检、覆盖、进度及已保存路径保持；模型调用为零 |
| U02 | 只含旧 manifest/patch | 不提供 apply/branch/export 或伪源码下载；明确没有普通产物 |
| U03 | 当前结果窗口 | 无分析/浏览器下载/旧代码按钮；保留保存到本地和预览；无自动发消息 |
| U04 | 用户草稿与旧材料草稿 | 文字不丢、不自动发送；旧材料失效提示可见，不静默丢弃 |
| U05 | 历史消息/已完成回执 | material_refs/旧结果仍可显示，正文不重写；旧工具调用不被执行 |
| U06 | 老前端/直接旧 API | 明确能力退役，非成功空对象；当前字节预览与 files API 保持认证及消费者 |
| U07 | 升级时旧 prepared/executing/partial/unknown | 新准入停止；真实 IO 排空；恢复证据保留，不自动续写或回滚 |
| U08 | 静态迁移闭环 | 无生产代码指向删除的回调/工具/执行分支；共享材料/写入函数有明确保留消费者 |
| U09 | 桌面/窄屏/键盘 | 保存、冲突、失败原因均可访问，真实数据空态不填示例 |

迁移当前 `CloudResultReceive.test.tsx`、`CloudCodeDelivery.test.tsx`、`CloudResults.test.tsx`、`CloudFilesDelivery.test.tsx`、`cloud-material-submit.test.tsx`，以及 material/service_registration/child/history 等后端用例；保留对应权限、顺序、异常及历史兼容断言。退役动作的旧成功测试改为退役合同，实际文件保存行为迁入新入口。

## 3. S3 回收与竞争

| ID | 场景 | 必须检查的结果 |
|---|---|---|
| C00 | 删除预览与旧确认 | 新策略进入 preview_digest 与确认说明；旧摘要不能授权扩大后的范围 |
| C01 | 最后一个任务完整删除 | 删除前固定候选；task succeeded 后才物理删除输入；容量在磁盘确认后减少 |
| C02 | 两个任务/多个附件共享 | 删除一个保留；最后一个删除可回收；相同 ID 去重不重复记账 |
| C03 | 上传未提交、提交后未关联 | 实际 transfer 与 associate use 都保护；关联成功同事务替换保护 |
| C04 | GET cache hit → 尚未关联 | 客户端先取得 use；交叉删除不会穿过窗口；旧快路径无旁路 |
| C05 | 读取与分段恢复 | 真实 FD、最后一个 reader、两段之间、客户端重启、ACK 前均保护；关闭后才释放 |
| C06 | 取消与未知结果 | 明确取消先排空/核对再 release；断网不解除保护；无 TTL 偷删 |
| C07 | acquire 与 retiring 双序 | acquire 先提交则 retained；retiring 先提交则新关联/读取拒绝；确定性事件同步 |
| C08 | 删除预留后有附件请求 | 原 task deleting 事务封关联；候选不会遗漏已成功关联项 |
| C09 | 每个崩溃窗口 | 候选前/墓碑后/retiring 后/删一半/全删未记账/记账后，均可查询和原命令幂等继续 |
| C10 | 同内容重新上传、旧 committed 重放 | 新 incarnation 不被旧回收触碰；旧 transfer 回执不能复活不存在的包 |
| C11 | 两个任务同时删共享包 | 原引用仍在时保留；一个命令拥有 retiring；另一命令不重复 unlink/核销 |
| C12 | 包/目录被替换、链接、未知文件/挂载 | 拒绝危险删除，原因可见，仍保守计费；不逃逸 inputs 受管范围 |
| C13 | unlink/rmdir/fsync/DB 权限或 IO 失败 | 主任务保持 succeeded；清理 partial/failed/unknown；不退出服务；失败原因含步骤/系统码 |
| C14 | task 已移出列表 | 删除记录可打开；GET 只读，retry 只作用原候选；profile/instance/task/cmd/tombstone 校验 |
| C15 | 释放环境/本机移除关联 | 没有输入回收、没有新候选；原功能行为保持 |
| C16 | 从未关联的 committed 包 | 即使无 use、很旧、另一任务删除，也不被本轮回收 |
| C17 | 未完成旧迁移、旧成功删除无候选 | 正确保护/明确无法追溯；不扫描孤儿包补候选或虚报释放 |
| C18 | owner v1/v2、Runner v1 | 旧 owner 写前被拒并提示升级；v2 正常；Runner 任务读取原合同保持 |
| C19 | 根/registry 升级、旧程序/备份 | 新格式旧程序拒绝；升级中断封口；旧必要恢复未核对/登记时禁止开启回收；还原校验一致；不能只改版本字段降级 |
| C20 | 最新结果轮换与 outbox | 原结果保留/lease 用例保持；outbox 随完整删除清理；不引入独立裁剪 |

新增 `test_task_input_reclamation.py`、`test_input_use_races.py`、`test_input_reclamation_recovery.py`、`test_input_reclamation_upgrade.py` 和删除记录前端用例。扩展现有 task_delete/recovery/io/integration、input_client/resume/download、task_attachments、manual_task_delete、input_expiry 等测试，不用 mock 掉核心 DB 事务和真正的文件删除阶段。

竞争/故障测试必须在最终断言核对业务状态、必要事件、计费与实际文件，不把检查器异常吞进业务 catch 后算通过。Windows 专项覆盖打开句柄阻止删除、WinError 5/32、重解析点、盘符/路径身份；不能用 macOS 测试宣称 Windows 实机通过。

## 4. 实施顺序与检查命令

1. S1：新增行为红灯 → 最小接线/文件名映射/持久阶段 → 工具闭环及原手动保存回归。
2. S2：建立退役、历史和残留恢复合同测试 → 删除真实旧消费者 → 当前文档/窗口同步。
3. S3：输入占用与协议/格式门禁 → 候选/回收/记账 → 原回执恢复与本机 UI；协议不能只改字段而缺任一写入入口。
4. 每片通过后执行变更适配的构建/测试；整体验收运行全部已有后端与前端测试，不为本片升级依赖、修改真实配置或用户数据。

现有代码基线命令（已执行）：

```bash
.venv/bin/python -m pytest tests/cloud/test_files_delivery.py tests/cloud/test_material_tool.py tests/cloud/test_task_delete.py tests/cloud/test_task_delete_recovery.py tests/cloud/test_task_delete_io.py tests/cloud/test_task_delete_integration.py tests/cloud/test_input_download.py tests/cloud/test_task_attachments.py -q
```

实施后的针对性命令（新测试尚未创建，当前不执行）：

```bash
.venv/bin/python -m pytest tests/cloud/test_result_tool.py tests/cloud/test_result_tool_permissions.py tests/cloud/test_result_tool_recovery.py tests/cloud/test_task_input_reclamation.py tests/cloud/test_input_use_races.py tests/cloud/test_input_reclamation_recovery.py tests/cloud/test_input_reclamation_upgrade.py -q
.venv/bin/python -m pytest tests/cloud -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m pytest tests -q
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec143bq/release
git diff --check
```

按[标准发布构建](../releasing.md)使用现有资源准备、分片与校验方式；Mac 构建通过不替代 Windows、Coordinator/Linux 的相关原生检查。真实云端 smoke 需独立测试实例和已确认数据范围，部署/删除授权不由 Spec 自动产生。

## 5. L1/L2/L3 与迁移收尾

- L1：新增工具/端口/占用/回收合同和测试存在，所有新文件不超过 500 行。
- L2：没有占位路径；实际 receive、权限、文件写入、依赖事务、unlink 和计费均被行为断言覆盖。
- L3：工具从主会话可调用；子作用域不可获得；手动保存共用底层；task delete 后调用回收；retired binding 后能核对；新协议覆盖真实 CLI/附件/缓存命中路径。
- 列出旧分析/代码交付已删除的函数、注册、调用方和保留的历史/文件消费者；未使用包装不能为旧测试保留。
- 新字段逐项核对模型、序列化、数据库、恢复/备份、前后端类型、CLI 与错误映射；同步 lock-hierarchy 的实际新调用顺序。
- 提供一个正常保存和一个冲突恢复、一个成功回收和一个删除成功/回收失败的真实调用链证据；测试通过不等于用户已掌握或线上业务通过。

## 6. 本轮设计验证记录

2026-11-18 工作区已有大量并行记忆和上下文改动，本轮只新增 143BQ Spec、更新有关文档入口及保存设计验证日志，不改这些并行产品代码。

已核对现有源码、既有合同、Pi/AgentX 对应工具调用链和本机 LangChain 版本。旧行为基线命令实际结果：`41 passed in 2.61s`，见[日志](../../build/spec143bq-design/baseline-tests.log)。这不是新功能测试。

本轮 Spec 使用结构检查验证：文件行数、相对链接、JSON 示例与现有 ID validator、约定字段/动作一致性及 diff 空白错误；检查结果保存在 `build/spec143bq-design/`。不运行应用构建来冒充尚未实现功能的验收，不宣称远端或 Windows 实机通过。
