# 143AK 审阅问题修复

用户已确认按 P2 → P4 → P1 → P3 推进，随后修复交付非便携路径及结果详情删除竞态。本片不提交、发布、部署或改真实配置，不处理未证实的 renew 竞态及其他记录项。

## P2 准入拒绝闭环

当前 dispatch → Runner accept 的业务拒绝穿透 receiver；普通 failed 状态帧不结算 Coordinator。control 已有必要历史终局，不能照抄其短分支而缺少结算。

最小路径：复用 Runner journal 的 meta 表保存独立准入拒绝（原 command_id、完整 command 摘要、操作和安全原因）；不写 run_started/run_finished，不改变已运行命令、恢复目标或实际活动状态。仅明确未执行的 runner_busy/model_unavailable/unsupported_thinking_level/result_capacity/artifact_busy 可转拒绝；身份、认证、必要 IO 失败继续原异常合同。原 ID 重试和查询返回同一拒绝，不能重跑。

增加内部 fenced admission_rejected 帧，携带上述固定身份与当前 phase/quiescent。Coordinator 核对任务、原操作、完整命令摘要和当前预留后，在同一 registry 事务失败结算命令、释放本次 active_run/artifact 字段；run 关联未执行结果标记 abandoned、退出 task result_ids。不伪造执行历史，不清除其他操作的 blocker；实际 blocked 保持真实阶段及已有保护，不因未执行的拒绝新增 recovery_required。断线后由原 ID query 重发拒绝完成结算。本机/owner 公开 API 签名保持。

影响：contracts/cloud_runner.py、storage/runner_journal.py、cloud_runner/transport.py/results.py、application/cloud/runner_control.py/results.py 及回归测试。不新增锁，沿 CT1→registry DB；Runner 拒绝保存走既有 store transaction。失败保存不得发送成功拒绝回执。

## P4 维护隔离

两个 server maintenance loop 在普通 Exception 边界记录安全类型并按原间隔重试，CancelledError 等 BaseException 继续传播。不宣称修复损坏行；保持 API 在线。测试意外校验失败后再次运行、取消传播。

## P1 准入计数

仅 environment:start 计算 retained/active SQL 聚合，留在原写事务内。run/artifact/control 等 reserve 不读取全表 tasks。墓碑不删除。SQL 聚合仍为扫描，首版只承诺取消无关热路径扫描及 Python 全表反序列化；不改 schema 或增加计数双写。

## P3 本机缓存恢复

沿143w补用户显式回收无引用缓存入口：核对全部保留接收记录，保留其 blob 与可续传 staging；活动接收、交付与材料读取须受保护。仅删除自有缓存中的无引用 blob 和无引用 staging/quarantine，拒绝链接及不合法缓存结构；不清空仍被引用材料，不改用户保存目录、主聊天或云端任务。返回实际释放字节/文件数，容量提示引向明确清理动作。实现前核对实际调用与锁范围并补充本节。

## 交付小修复

非便携 O 路径三动作在预检阶段统一拒绝，报告具体路径；合法 Git 对象不等同于本机可交付。结果详情与删除核销竞态返回明确 task unavailable，避免 source_handle KeyError。

## 验证

逐项先运行失败回归，再做实现与相关测试；P2 覆盖拒绝持久化、重启/重连查询、非业务错误传播、旧 ID 摘要冲突、结果同事务结算、连接保留。P3 覆盖共享引用、活动保护、失败续传、坏链接、清理后可接收及认证路由。最后全量后端、前端测试/构建、标准 wheel 构建，Windows原生证据仍由用户原机验收。

P3接线补充：`LocalReceives.cleanup`在manager admission内拒绝同项目接收/交付flight，借ReceiveFiles已有publication短锁登记独占IO标记，不持mutex跨IO/await。材料读取及新增接收/交付准入用users预留阻止并发清理。worker读取所有receive/material引用快照，以delivery_paths固定父句柄删除非引用普通单链接blob及无续传用途的staging/quarantine，按既有条目预算单次有界；保留deliveries目录内的交付日志/备份。本机认证POST `/cloud-receive-cache/cleanup`带project/session身份，结果窗口按钮显式触发，返回实际文件数/字节数。仍被引用的材料占额不在本片强制删除范围。

P2补充：reserve记录原last_run与pending_result身份，拒绝同事务恢复原待捕获结果。Runner仅在替代run已写入命令journal后回收superseded计划，避免先清计划后拒绝导致无法恢复。准入拒绝本身不新增恢复blocker，临时busy结束后的真实ready心跳可恢复正常准入。

## 本机完成证据（2026-11-03）

六项均已接入。新增27项后端回归与1项前端回归；最终冻结源码全量后端6363通过（零失败/错误/跳过），前端72文件595项通过，TS/Vite构建成功。四目标标准wheel构建及Twine通过，每包514个Python源文件逐字节匹配；Mac源外新venv标准安装烟测通过，没有供应商请求。UI实拍使用真实组件与明确标记的模拟API；实际清理由临时目录后端测试验证。详见`build/review-reliability-20261103/verification.json`与报告。

已有delivery_capacity两处简化domain夹具补入真实ReceiveFiles以覆盖新增活动保护；原容量断言保留，未修改conftest或放宽断言。P2临时busy回归补入后重新执行上述全量，最终证据对应同一源码冻结。Windows/NTFS、公司Linux网络及真实业务仍待用户原机验收；不提交、上传、发布或部署。
