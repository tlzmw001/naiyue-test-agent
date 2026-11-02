# Spec143O：S03.2 来源策略、输入缓存与段续传

2026-11-01。用户“继续”授权，沿143b/143f和S07.1收尾方案实施；不进入S04.2/S05/S07.2，不提交、发布或公司部署，不修改真实配置。保留当前dirty工作，开工前源码字节副本与测试清单用于比对，禁止覆盖已有测试文件。

## 1. 当前链与借鉴边界

当前`CLI capture → Selection schema1 → capture/source_handle → tar+manifest → CloudClient.upload → CreateTransfer → 完整PUT → commit → InputPackage → task install`已接线。缓存只有远端同I命中，上传只有offset0整包重试。S07.1共享byte_stream只负责有界IO，输入语义继续由CloudInputs负责。

已追A的`SnapshotBuilder._inventory/_verify_local → GitRepository.run → HostProcessRunner`：批量index/路径查询及根/文件前后核对有价值；A额外fetch/临时commit/ref/bundle不进入B。B直接复用HostProcessRunner进程生命周期/预算/超时，不依赖S09将删除的GitRepository。OpenSandbox/DeepSeek在143b/143n中已核对的流接口不提供本产品持久offset/段确认语义，本片不将SDK stream当作续传。

## 2. 明确的来源与格式

现有manual schema1保持原含义和canonical字节；新增schema2的冻结来源策略（kind=git、固定安全排除、显式include/exclude相对路径、ignore规则及attributes规则原始内容），不可在schema1里改变解释。Git决定标准ignore语义，B不重写匹配器；显式exclude只表示路径/目录前缀，include为明确普通文件路径，安全/凭证排除不能覆盖。

`input select --source ROOT --output PLAN [--include PATH] [--exclude PATH]`用少量批量Git命令生成私有SourcePlan，包含规范Selection和仅本地SourceReceipt。只接受仓库顶层；读取cached/others/exclude-standard与stage元数据，未合并拒绝、工作树已删除条目记录为删除、submodule明确排除不递归。HEAD可为空；记录原HEAD、源根、Git转换配置、批量text/eol/filter/encoding属性，不执行filter/checkout/网络。受支持换行策略如实记录，工作区原始字节不转换；CRLF可执行shebang继续拒绝。

来源事实仅在本地私有plan/source.json，不进archive。选择策略的ignore/attributes内容以逻辑相对名称冻结，不泄露绝对来源/用户路径。文件、规则、index/HEAD等读取有界并记录身份/hash，所有config origin及include空/缺失目标都冻结；未跟踪执行位另记私有source_modes；capture前后核对同一事实，变化拒绝，不沿用过期执行位/ignore。发现目录有预算，跳过固定依赖/凭证目录，不跟目录链接；不对每个业务文件启动Git。

CLI capture显式二选一`--selection`（manual）或`--plan`（Git），输出二选一`--output`或`--cache`。普通目录继续用manual明确选择；不额外发明glob/ignore语言。

## 3. 本地缓存

`--cache`使用明确私有新根、格式marker和现有OS锁；私有根仅含保留初始文件时，可恢复空/Windows NUL/有效签名前缀锁，错误签名或其它文件仍拒绝；先调用同一个capture冻结/验证，再按input_id提交`inputs/I`。同I已有缓存时校验其archive/manifest/selection并返回原捕获事实；不因codec不同更改同I既有归档。source.json为本地来源/换行回执，不进输入身份；共享缓存将其独立保存为每次imports/<UUID>.json，由CapturedInput.source_receipt指向，不能复用另一个同内容来源的HEAD/路径。持续任务使用workspace，不重复捕获；重复导入不同本地状态仍是全量新归档，非块级增量同步。

缓存先作保守暂存预留；最终receipt/import文件全部落盘后按真实字节核对，之后只rename发布。额度统计真实已提交及失败暂存字节，不按状态免除；失败只清本次创建、身份可确认的固定文件，不递归清用户目录。没有自动删除已提交缓存/被引用I；额度不足明确拒绝，后续运维再做引用感知回收。本片TTL只针对未提交远端transfer。

## 4. 同路由段续传

扩展InputTransfer：服务端chunk_bytes、按顺序确认的segments(offset,size,sha256)、archive_identity、updated_at。CreateTransfer/命令身份不变；POST原回执不冒充实时状态，客户端随后GET同transfer。默认段8MiB、IO256KiB和段数/归档预算可配置。

PUT同一路由，Content-Length为本段、Upload-Offset为段起点、X-Content-SHA256为段hash。只接受当前offset追加；已确认相同offset/size/hash的重发核对实际请求体后返回当前持久offset；未知/跳跃/不同hash拒绝。offset=size才uploaded，commit仍独立核对整个archive/manifest/selection并发布ready。

顺序为固定文件FD写入→flush/fsync/close→同事务/CAS保存段账本+offset+文件身份。崩溃或DB失败留下未确认后缀时，只在固定私有文件上核对已确认前缀后截断后缀/fsync；短于已确认offset或已确认hash不符拒绝，不回退已ACK事实。身份未变的热路径不重复hash整个前缀；身份变化/重开对账才校验一次，避免每段累计全量扫描。

GET状态在空闲时执行同一恢复核对；正在上传/commit时只读已持久元数据，不争抢FD或返回未确认进度。PUT/commit/cancel共享原transfer预留；跨await不持线程锁/SQL事务，取消排空正在运行的真实IO后释放。byte_stream扩展固定offset写入及返回文件身份，artifact完整PUT继续沿同一函数，不复制传输循环。

CloudClient在原capture锁/instance日志下保留command_id；先查询I、POST原transfer、GET当前offset，然后只发送未确认段。段ACK丢失后下一次upload先GET对账，不能生成新ID或从0盲重传。上传前仍检查本地完整archive身份/hash，避免更换源后续接另一份字节；响应task/input/transfer/facts严格核对。网络失败明确交还调用方，由显式重试续接，不新增透明无限重试。

## 5. 暂存TTL与生命周期

未提交transfer按updated_at/可配TTL回收；当前上传/commit/cancel预留阻止回收。清理使用原cancel合同，已发布未登记窗口先对账为ready，不能变成未计数孤儿。保留命令回执，不删ready输入/任务引用/用户文件。普通失败记录有限错误码和类型，不能静默吞错。

服务现有双监听owner中接单个维护协程，按配置间隔调用输入sweep；启动/关闭同现有Server资源周期，封口并排空后再关库，不能留下无限等待的后台协程。维护错误可观察，下次重试；必要上传/提交失败仍返回调用方。CLI可查询/取消明确transfer，不增加无认证维护旁路。

## 6. 变更和验证

扩展contracts/cloud_inputs；新增来源DTO/选择器、local cache及client段上传模块；复用capture/archive/input_install并接CLI；扩展storage/cloud/inputs、input_upload/CloudInputs/byte_stream、input_routes/server。新SQL仅storage，锁表同步缓存OS锁、段预留和维护协程。每文件≤500，无依赖升级，不修改A锁定storage/files。旧完整上传循环被替代，旧mock消费者迁移到实时GET/offset合同并保留所有ACK/身份/失败断言。

TDD先红：Git ignore/未跟踪proto/执行位/删除/冲突/submodule/源策略变化及配置不执行；本地同I/不同codec复用、额度/中断；多段/错hash/跳跃/重复/ACK丢失/DB失败/重开截尾/损坏前缀；认证先于body；取消/关闭/TTL活动保护。复用真实SQLite/ASGI、现有安装器和实际Git。附200MiB流式探针记录RSS、确认段/断线恢复传输量及安装hash；本机Mac/Linux与Windows原机分别标明。

专项过后冻结源码，完整后端两分片与旧6408项集合比对、前端/构建、四目标标准wheel/Twine、Mac源外安装；最终wheel在专属Linux环境执行CLI来源→缓存→真实HTTP段上传/断线恢复→任务安装/测试/产物下载。用受信测试镜像注入确定性模型或正式waiting_model，不注入真实模型key。只清本片专属资源、恢复VM停止/默认context，记录证据和交接，再讲S04.2。

## 7. 本机实现与验收（2026-11-01）

S03.2本机实施完成，未提交/发布/公司部署。入口`input select → SourcePlan → input capture --plan/--cache → capture/source_handle → CloudClient/input_upload → 原transfer POST/实时GET/段PUT → CloudInputs.commit → task install`真实接线；manual selection继续使用同一capture，未保留第二条完整上传循环。Git元数据由现有HostProcessRunner执行6个批量命令，B不依赖A GitRepository/commit/bundle。

schema2的portable policy与私有SourceReceipt分离；所有Git config origin/include目标（含空/缺失）冻结，未跟踪执行位有独立事实校验；submodule后代不能绕过。共享本地I对象不覆盖来源回执，每次导入的source_receipt指向独立imports记录。缓存保守预留临时空间，最终必要元数据写完才核实真实额度，再rename发布；初始化marker或签名中断可在受限私有根重试，foreign根不自动认领。

段确认只有fsync/物理writer close后才能保存offset/账本/文件身份；热路径不重读完整已确认前缀，断线/DB失败核对并截尾，已确认缺失或hash变化拒绝。GET在原双键producer下对已发布未登记窗口补原commit，不能让新客户端被状态查询挡住。旧input client mock新增真实GET状态消费，保留原command/ACK/失败断言；Server重启夹具补真实维护接口并追加sweep次数断言，原端口重绑断言保留；无conftest/断言放宽/skip。

[verification.json](../../build/spec143-s032-20261101/verification.json)记录：新增27项、专项296、完整后端3323＋3112＝6435（旧6408全部保留，与collect清单完全一致）；前端69文件568和TypeScript/Vite通过。标准四目标wheel/Twine通过，同源450份生产Python及当前前端资源；Mac源外安装/native工具通过，实际Linux任务模板与Coordinator安装源码逐字节匹配最终wheel，Kit0.6.0。Windows/x64仅跨目标构建，Windows锁字节为模拟回归。

真实Linux ARM64/热镜像/loopback流中转中：200MiB随机源文件、209,725,440B tar归档、26段（8MiB预算）；服务端确认16MiB后故意丢段ACK，原transfer重试只发余下192,948,224B，已确认段重复0，总上传字节恰好等于归档大小。再次同I上传0字节；CLI子进程峰值RSS 137,134,080B（约130.8MiB，含依赖启动），中转IO块256KiB。6次Git选择约0.31s、capture约1.54s、恢复上传约1.01s、backend/input/Runner waiting_model约3.60s；均为单次本地样本，非p95/Windows/公司性能或A/B对等结论。

200MiB输入在实际任务内用Kit pytest逐块hash核对，1 passed；JUnit报告通过Runner artifact capture上传并取回，停止容器后报告字节/hash保持。小输入确定性write/Bash/reply/历史/产物/停后下载也通过；正式模板无S05模型如实waiting_model，0真实供应商/业务请求。Coordinator重启后大输入索引仍在、upload命中0字节。只清本片所属容器/网络/精确规则，临时DNS恢复、专用VM停止/default context不变，旧数据/镜像保留。

架构复核的published状态恢复、include证据、submodule后代，以及新边界复核的缓存真实额度/初始化/执行位问题均先红后绿并关闭；物理close后时间属性才稳定的回归另覆盖，不假称Windows原机已验证。Windows步骤见[原机复验](../cloud-input-s032-windows.md)。真实配置/依赖/旧A数据未改，无新增用户掌握证据。

下一片S04.2按143i现有装配线补根子运行、持久审批、停止/后台收束和显式恢复：复用SessionService/SessionPersistence、session_approval/session_recovery及OneUseApprovalGrants。审批决定从认证owner入口进入，Runner核对原subject/环境/checkpoint；不能靠模型或普通observer自批。未知副作用保持不重放，审批等待/必要保存/后台未静止继续阻止回收。先给正常/失败主链及详细Spec，再实现；S05真实模型、S07.2完整结果和Spec142不提前进入。
