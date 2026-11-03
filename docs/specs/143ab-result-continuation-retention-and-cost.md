# Spec143AB：后台续聊、结果保留与自主捕获成本修正

状态：2026-11-02，按用户三项建议核实后实施；不进入S09实机验收，不提交/发布/部署。保留此前S08脏工作、四份Spec142及独立交接段落。

## 1. 已核实的根因与选择

修订前，RunnerRuns.reserve把pending_result_id与任何非background blocker都作为拒绝条件，而CloudResults.begin又要求没有background_commands，因此持久mock服务使上一轮pending结果和下一轮相互等待。结果额度按task.result_ids历史累计，Runner plans/done spool也不回收。freeze_result每次解包I、复制I、将整个O写入/fsync，并对全部I/O重新写Git对象；不可变I的成本可以复用，O仍须按内容核对。

正常链：根本轮必要历史已确认、Runner ready，只有尚未开始捕获的pending → 用户新消息准入事务中把旧结果标记superseded并记录superseded_by新run_id、移出保留集合 → 新run和新ResultPlan同事务登记。新run准入失败时旧结果不变。捕获已经开始或verifying/unknown/failed时不自动替代；显式重试/放弃仍保留。失败链：begin与新run竞争同一task gate/CAS，只有一方成功，不扫描混合时点O；后台服务仍阻止空闲释放。

## 2. 保留、引用和清理

max_results约束保留结果，不约束历史总轮数；superseded/abandoned与已清理墓碑不占保留名额。默认保留最新available，其余已固定结果无活动引用即回收；最新结果与唯一在途结果允许一份轮换重叠。Runner准入/pending同样允许max_results+1的单份交接余量，用于Coordinator已受理、旧plan尚未GET核对的短暂窗口；不提前标done或删除未知计划，额外计划仍有上限。全部旧结果被引用时可拒绝新增，UI应说明等待接收/下载释放，不再建议丢弃当前workspace重建任务。全局命令/历史额度仍独立存在，不承诺无限历史。

Coordinator的结果引用使用独立meta租约（owner-only result lease/release，receive_id作为lease_id，服务端配置TTL/数量预算），不能把可续租数据混入不可变ResultBundle。下载的实际FD生命周期持有TaskIO预留；清理在同一task gate核对无租约/在途IO后先持久标记deleting，再清理固定自有artifact/staging目录，最后核销索引/额度并保留deleted墓碑。失败保留deleting供维护重试，不能提前释放磁盘预算；新读取拒绝deleting/deleted。保留字段与新状态同步DTO/序列化/UI/测试。

本机接收在取远端元数据前申请租约，每个分段前续租，成功/确定失败退出时释放；意外进程退出由TTL收敛。已完整校验并保存的本机材料不依赖远端R，后续分析/代码交付不永久钉住远端副本。若完整字节已落本机但最后发布失败，显式retry只重新校验本机固定字节并完成发布，不再要求连接远端或续租；部分接收仍按原lease/Range流程继续。租约到期后原结果可能清理，未收齐材料显式不可用，不回源替换为另一轮。实际清理只作用于测试临时根及产品管理目录，不在本轮操作真实用户结果。

Runner确认Coordinator available/abandoned/superseded/deleted后，先清除该结果spool，再清除对应plan；步骤幂等、目录身份/不跟随链接、清理失败可观测并保留可重试计划。丢commit ACK先查询同result_id，不重扫workspace。保留必要命令/历史台账，不能删除未知执行状态以腾额度。

## 3. 捕获优化与成本边界

每个任务在workspace之外保留仅属于原input_id的私有baseline Git对象库/清单。首次验证I归档并构建I树，后续复用；不读取当前workspace的git配置/ignore/filter，不把mtime/size缓存当O内容权威。缓存身份/缺失/损坏明确核对，输出仍属于任务上报，本机应用继续独立重算I→O闭合。

O库存扫描每轮读全量受管内容，同一读取计算SHA256和Git blob ID；沿原源文件/目录身份前后复核。已知I内容可复用对象，无需写出冻结副本。只对变化内容再次读取并核对后保存，必要时用临时对象库以I库作只读alternate；本轮变化对象/临时stage结束后回收，不能让常驻ODB随轮数积累。报告仍按本轮目录真实捕获。只对最终发布的manifest/patch/report及发布目录fsync，临时可重建数据不逐文件fsync。保留原freeze_result调用合同及幂等固定spool规则。

性能最坏仍为O(受管总字节)读取＋O(变化字节)冻结/对象写入，不承诺GB项目1秒。输入默认4096文件/128MiB、输出4096文件/256MiB是可配置准入上限，不通过暗增默认掩盖成本。新增“上一轮终局→可接受下一消息”与“终局→available结果”指标，分别报告缓存冷/热、600/4000小文件及二进制规模、文件/字节/变更数量。用户给出的0.9/5.1秒只作线索；Mac复测与2CPU目标机结果分开，目标机留S09。

## 4. 实现与验证边界

主线负责Coordinator准入/保留/租约/本机接收/前端状态、共享DTO、存储和最终接线；Runner spool/plan清理与快照性能模块互不重叠，按项目rndflow构建/验证规则分派。先用现有真实Coordinator/Runner/SQLite/HTTP夹具写失败测试，覆盖后台服务续聊、begin竞争、失败回执不替代、超过64轮仍可用、租约/FD保护、GC中断/磁盘失败、spool重复清理/ACK丢失、基线只建一次、完整内容核对/变化、原二进制/CRLF/链接/执行位/防变更行为。新增接口不接受任意路径/URL，不升级依赖，必要锁顺序同步lock-hierarchy。

实现后对143a/143b/143g/143v/143z同步当前合同，更新消费者快照并运行完整回归/构建/包检查。实际结果追加在本节之后，未测前不称完成。

## 5. 接口与本机证据

新增owner-only `POST /api/cloud/v1/tasks/{task_id}/results/{result_id}/lease`、`/release`，请求固定`{lease_id: UUID}`，只能作用于已登记的task/result。lease返回固定lease_id/result_id/expires_at；release幂等返回released。续租不改变ResultBundle及ResultRecord.revision；默认ResultLimits.result_lease_seconds=300、max_result_leases=128，可配置。服务端只给available租约，deleting/deleted拒绝新读取，完整task删除只清自身lease前缀。ResultRecord增加superseded_by、retired_phase与superseded/deleting/deleted状态，列表/前端同步。

本机性能探针：`.venv/bin/python -m scripts.profile_result_capture --label candidate --output build/result-profile.json`。2026-11-02 macOS26.4/arm64/APFS/Python3.12.14，15逻辑CPU可见、未限2CPU；每个文件8KiB，600/4000文件，1个文件变化，两次input_id相同。独立冷首轮后20个新result_id复用同I，nearest-rank p95。数据见`build/spec143ab-result-capture/{before,after,comparison}.json`。

| 样本 | 冷首轮 原→新 | 热态p50 原→新 | 热态p95 原→新 |
| --- | --- | --- | --- |
| 600文件/4.69MiB | 0.813→0.568s | 0.720→0.142s | 0.775→0.147s |
| 4000文件/31.25MiB | 4.234→3.167s | 4.216→0.676s | 4.402→0.692s |

该表只度量freeze_result，包含扫描、基线准备/复用、patch/report与spool发布，不含模型、必要终局投递、网络上传/Coordinator提交。143g新增的终局→可接收下一消息、终局→available为S09目标机完整链测量项，本轮未执行、不冒称已达标。GB规模须先显式调整输入/输出预算，再单独报告，不能把此表外推。

安全/接线复核补齐：任务删除租约同事务清理；下载关闭失败且FD未关闭时保留可重试登记；最新结果按task.result_ids持久准入顺序而非墙上时钟选择；本机完整字节发布重试不依赖远端存活。新增回归先确认失败再修复。只修改共享测试夹具以模拟新lease API/必要维护组件，不改conftest、不减断言或skip；旧“pending必然阻止新run”断言按新合同迁移到fresh supersede与capturing阻止两组回归，显式abandon仍独立验证。

最终完整测试、构建、源字节一致与保留文件校验汇总至`build/spec143-result-review-20261102/verification.json`。S09 Windows/Linux/公司网络门禁仍未进入；未提交、推送、发布或部署。


最终验证（2026-11-02）：冻结最终源码后两份完整backend shard为3323＋3605＝6928 passed，0 failed/0 skipped；原6883节点经生成UUID参数归一化与明确pending测试迁移后全部保留，新增45。前端76文件/625 passed，默认与cloud-candidate构建退出0。四目标主应用wheel/Twine、Mac源外安装退出0，537 Python源逐包字节一致；A机器消费者906条无漂移，compileall/git diff --check通过。未执行真实公司/2CPU/GB门禁，0真实供应商请求；代码未提交。
