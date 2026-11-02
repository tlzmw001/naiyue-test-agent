# Spec143N：S07.1 单产物与内部CLI闭环

2026-11-01。用户“继续吧”授权按143m第8节实施；不提交/发布/公司部署，不改真实.env/连接配置，不实现S07.2代码差分/Range或S05模型。当前剩11个独立验收切片（本片在内），分9轮；不把大切片按子项重复计数。S04.1已完成，143f旧顶部状态同步改正。

## 1. 当前链与参考

已读S03 `InputClient.upload → /transfers raw PUT → input_upload.receive → fsync/offset → CloudInputs.commit`；输入清单验证属于I，不强迫报告使用归档/manifest。A的`ResultService.capture → 固定operation/result → _download → verify → publish → ready`保留捕获一次、保存/发布区分和unknown保护；不引入其envd/bundle/base64消费者。

OpenSandbox1.2.0实际`FilesystemAdapter.read_bytes_stream`使用独立HTTP、aiter_bytes和finally关闭response，可借鉴有界流，不提供本项目任务结果持久合同；本片不让Coordinator通过SDK读取任务路径。DeepSeek Harness ce7073d的`file-upload/http-route → uploadStream → attachments.saveFileStream`采用原始字节通道、会话绑定暂存，不能把stream/取消当成续传或持久结果。源码在既有research克隆，定向核对实际方法，不重开整体技术选型。

Pi a69bef789 `createAgentSessionServices → createAgentSessionFromServices`的固定cwd服务边界与AgentX3fcfa0e `onCompleteResponse → saveMessageAndUpdateContext → sendEndMessage`的保存/展示分离继续沿用；不新建Agent循环。本片仍使用现有Python/create_agent，框架运行装配不变。

## 2. 最小外部合同

一次显式owner capture只冻结工作区内一份普通文件（例如reports/report.json）；允许0字节，拒绝链接/FIFO/设备/越界/凭证路径，读取前后核对身份/类型/字节数。相对路径沿用B便携路径规则。owner显式导出是所有者文件操作，不由模型决定授权；模型工具权限仍照常执行。首版限额走ArtifactLimits，不硬编码URL/端口/真实凭证。

`ArtifactRecord { artifact_id, command_id, task_id, project_id, input_id, generation, relative_name, size, sha256, captured_at }`为不可变事实；固定application/octet-stream，下载只作为附件，S07.1不提供HTML预览。未来ResultReceipt.artifacts[].file_id引用同一artifact_id/文件存储，不能另造第二套产物通道；完整patch/I→O和产物集合在S07.2。

| 入口 | 实际消费者与语义 |
| --- | --- |
| owner POST /api/cloud/v1/tasks/{task}/artifacts | command_id、expected_revision/generation、relative_name；单任务静止/无unknown/无环境操作/有效在线连接及artifact能力后持久接受，返回202原回执；重复ID先查原摘要 |
| owner GET /api/cloud/v1/tasks/{task}/artifacts/{artifact} | 只读已提交元数据；不启动容器、不读任务DB |
| owner GET .../{artifact}/bytes | 已提交原字节、Content-Length、固定hash ETag、nosniff/attachment；首片完整GET，Range后续 |
| task POST /internal/cloud/v1/tasks/{task}/artifacts/{artifact}/prepare | artifacts:write凭证；对照owner原命令、task/project/input/source generation/path/ID，配额原子预留；任务不能凭空创建owner操作 |
| task PUT .../{artifact}/bytes | 完整raw PUT，长度/hash/offset0/Content-Type检查；有界IO/fsync后uploaded，相同完整请求可重试；无分段续传声明 |
| task POST .../{artifact}/commit | 实际重算长度/hash，私有staging原子发布再登记available；崩溃窗口按原artifact元数据/bytes对账，不重新捕获 |

仅Coordinator任务API可接收任务凭证；owner路由鉴权/Origin/协议照旧。scope加入签发与续期，generation变化吊销旧凭证。所有新SQL在storage。Coordinator产物位于独立artifacts根，任务不挂载该目录或全局根。限额统计未收束/已提交对象，失败暂存不因改状态自动免除磁盘占用。

一个已鉴权的完整PUT拥有受长度/hash/超时和固定artifact约束的请求租约；在线token轮换不使其已接受字节失效。结束时重新核对generation/原capture占用/任务可用性，代际或归属变化拒绝uploaded；commit作为新请求必须用当前有效凭证。撤销旧token立即阻止新请求，但不回滚此前已接受的受限IO；这与已接受run不因连接断开重放的语义一致。不能因为每15秒轮换使大文件永远重传。

## 3. 必要保存、控制与重试

Task增加artifact_operation_id及artifact_capture/artifact_unknown回收阻碍；与root run、环境操作互斥。等待模型但没有运行的Runner可导出既有文件；无需伪造模型。HELLO显式声明artifacts:capture能力，缺能力不发送新帧。

控制新增artifact_capture、artifact_query、artifact_state、artifact_finished，沿现有connection/gen/boot/epoch fence及最终帧预算。大字节不走WS，接收/心跳与HTTP传输不同task/连接，不能被大文件串行堵住。owner断开不取消已接受producer；控制断开只标未知，重连查询原ID。

Runner在私有SQLite记录capture身份，再用固定根/不跟随父目录链接的source_handle读取并冻结到state私有spool；同ID相同摘要复用，不同摘要拒绝。只有完整spool+元数据fsync/发布后才标captured。已确认spool永不重扫为另一份文件；半捕获重开unknown、不自动重放，已发布spool但DB未确认可按固定元数据对账。根运行/后台/审批未静止拒绝捕获；捕获至必要上传ACK期间阻止新root执行。

完成顺序：Runner冻结 → task prepare/raw PUT/commit → Coordinator持久available → Runner持久ACK并释放本地capture占用 → artifact_finished → Coordinator核对自身available记录才成功owner回执、清artifact阻碍/登记静止。commit本身不提前开放新run，防止Runner仍忙时被下发新运行。捕获已知失败可保存稳定错误并在Runner静止证明后结束失败；网络/必要保存失败继续未知，不能用日志当完成证明。

下载持有已验证的服务侧文件句柄、按块读，响应finally排空正在读的worker再关闭；不让FileResponse重新打开未核对路径。服务关闭封口并排空操作/下载租约，不与SQLite事务或CT1锁跨网络。符号链接/丢文件/内容身份不符不返回可用数据。

## 4. 共享和清理

把input_upload已有完整raw PUT的纯字节接收（有界写/hash/flush/fsync/取消排空）提取为小模块，由输入和产物调用；两类DTO、提交验证与用途授权仍分别显式。不得把普通artifact伪装成input archive，也不复制另一份上传循环。

现有InputClient改名为同一CloudClient，迁移全部真实消费者/测试，不留无消费者别名。保留原upload实现，新增认证元数据/原字节下载；CLI与输入上传用同一连接/错误预算。所有新文件≤500行；不修改仍受A源码指纹保护的storage/files.py。

本片成为第三个“command_id/digest合并、HTTP只shield等待、真实producer完成才释放、关闭排空”的使用方（环境操作/根运行/产物捕获），提取最小CommandProducers并迁移三处实际消费者，不统一它们的准入/状态/业务规则；输入transfer的双键exclusive不是同一职责，不强行纳入。

## 5. 内部CLI

新增project create/list、task create/show/start/stop/refresh、run send、command show、history、artifact capture/show/download。变更命令用显式JSON request文件，内含command_id与需要的CAS值；客户端先严格DTO验证，不在重试时生成新ID或自动抓新revision替换请求。只读/下载用明确ID/输出路径。CLI只做HTTP客户端，不启动本地Agent/自动SSH/发现旧配置；正式人机交互在S09。

下载先取得已提交元数据，raw流式读取并核对长度/hash/ETag；同父目录私有暂存目录/普通文件，fsync后无覆盖发布，仅显式输出路径。POSIX固定源/目标目录FD并持源文件句柄至原子不覆盖link及inode核对；Windows保留排他删除句柄至发布核对后关闭。使用标准库原子不覆盖链接发布；目标已存在拒绝，不悄悄覆盖用户文件；不支持的文件系统明确报错，Windows原生行为须实机补证。中断不公布半文件，不能为了隐藏保存失败删掉可能已发布的用户目标。

## 6. 变更面和验证

contracts/cloud_artifacts与Runner帧/Task/Bootstrap；storage/cloud/artifacts和Runner私有capture日志；application/cloud产物服务/共享raw IO/只读下载租约；cloud_runner捕获和HTTP投递；service/cloud产物路由；CloudClient/CLI接线。锁表同步capture/下载/worker所有权。A/Spec142既有dirty工作、真实环境和数据保持。

先红灯覆盖：原字节/空文件/二进制、源变化/链接越界/FIFO、同ID/摘要冲突、prepare/PUT/commit/最终回执丢失、捕获/spool/发布/DB各中断点、token/跨任务/代际、配额、并发run/环境操作、HTTP取消worker排空、下载取消/目标竞争/错hash。用真实SQLite/ASGI/Runner内核和CLI，不新增产品fake模式。SDK/observer可能包装断言的地方在测试末显式核对。

实际Linux以最终标准wheel/镜像完成内部CLI导入→任务→确定性write/Bash→历史→capture/upload→认证下载，对比原始字节；再验正式无模型模板可以捕获既有文件、容器退出后仍能下载。模型/公司业务0请求，不替代S05/Windows原机门禁。标准全量后端两分片、前端/构建、四目标wheel/Twine/源外安装、源码无漂移；只清本片专属资源、恢复VM停止，收尾讲S03.2/S04.2，不自动跨片。

## 7. 本机实现与验证（2026-11-01）

单文件闭环已接到当前内部CLI及实际容器Runner，未切正式A入口/未提交/未发布或公司部署。正常链为`CLI CaptureRequest → CloudArtifacts.reserve/原回执 → control artifact_capture → RunnerArtifacts.accept/_freeze → task HTTP prepare/raw PUT/commit → ArtifactStore.publish/available → Runner本地ACK journal → artifact_finished → CloudArtifacts.finish`。下载为`CloudClient.download → owner ArtifactResponse/ArtifactDownload → 已固定服务FD → 私有本地暂存/校验 → 不覆盖发布`。Coordinator不读取任务SQLite或任务路径；产物独立保存，任务停止/删除运行容器后、Coordinator重开后仍可下载。

失败链保留原command/artifact身份：源变化不发布、完整spool已发布而journal失败时按原metadata/hash重开核对，published-before-index失败时原ID重试commit；网络ACK丢失不重扫源。Runner本地配额同时统计失败staging与available spool；复制中容量失败即使0字节staging也消费对象槽。没有创建spool的容量拒绝留下轻量持久failed回执，不触发断线/永久unknown；必要保存和身份不确定仍阻塞。下载固定私有源目录FD/目标目录FD及文件句柄，路径被替换不发布另一份字节；发布后不删除用户目标来伪造回滚。

真实CLI stop→start复验发现并修正旧断线归类：只有持久environment:stop_environment的进行中退出，或stopped+absent且无active run/artifact操作，才按预期停止处理；refresh不是stop，仍保留意外失联保护。已有unknown/必要保存阻碍不被清除。回归覆盖两种到达顺序及refresh分支。

验证证据在[verification.json](../../build/spec143-s071-20261101/verification.json)：新增32项，专项269、完整6408后端（与本轮collect清单一致、旧6376项全部保留）、568前端/69文件及构建通过。四目标标准wheel/Twine通过，均包含同源443份Python文件及当前前端；Mac源外安装/native工具通过，Linux ARM64真实任务/Coordinator安装源码与wheel逐字节树哈希一致。旧CLI两条测试已按本片开工前hash恢复，新增测试独立文件；未改conftest、未删旧断言。

Linux以最终wheel/模板运行完整CLI：同I第二次上传0字节、确定性write→Bash→reply与历史、10字节报告及65,792字节二进制均成功；重复原capture ID不重捕获，停止容器后仍下载，正常stop后能新代启动，Coordinator重启后两份下载hash不变。aitest_kit=0.6.0、非root、只挂task目录/只读bootstrap、无管理key/Docker socket。独立真实WS探针覆盖终局帧附近断线重连，字节只上传一次。产品无S05模型如实waiting_model；0真实供应商/业务请求。

本机热镜像、1324字节输入归档下两任务ready约3.28s/2.92s，捕获至查询到成功回执约1.66s/1.55s，包含多次独立CLI启动/轮询；不是p95、吞吐/大项目指标或A/B对等比较。Windows/x64仅跨目标构建，Windows原生下载共享删除/目录竞争和公司Runner链路仍待门禁；本片仅一个明确普通文件，不宣称完整结果patch/Range完成。

两轮本片专属服务/容器/网络与精确规则已清理，临时DNS恢复、专用VM停止/default context不变，旧VM/数据/镜像保留。无新增依赖或真实配置修改、无用户掌握证据。下一步先讲并收窄S03.2完整来源/冻结策略、双端缓存与段续传；随后S04.2根子/审批/恢复，不自动实现。
