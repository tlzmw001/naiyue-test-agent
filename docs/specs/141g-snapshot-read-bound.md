# Spec141G：按实际文件大小读取本机快照

## 范围与证据

用户于2026-11-01批准只修小文件读取请求长度。Windows原机671文件实验中，
16MiB上限的read_local中位数3.173331s；临时16KiB上限为0.654683s，内容校验与清理通过。
已展示buffered_file_read从2.526727s降至0.033～0.035s；这不是正式修复或完整准备10秒证明。
上传分块、Git调用、目录检查、权限、缓存及并行调度不在本片范围。

## 正常与失败合同

现有InputService.prepare → SnapshotBuilder.prepare → _read_local → read_input；
该函数也用于ResultExporter捕获工作区。保留接口和两类消费者。
安全打开后以fstat得到opened；大小超过原配置limit仍报git_input_budget。
仅把read(limit+1)改为read(opened.st_size+1)，正常内容必须恰好等于opened.st_size。
读取后复用一次fstat：读取结果或实际文件超过原limit报git_input_budget；
长度不一致或句柄身份变化报git_input_source_changed，不返回截断/多余内容。
其余路径、链接、打开前身份、内容、总预算和最终来源核对保持。

正常例：12KB文件在16MiB配置下请求12001字节并完整返回12000字节。
失败例：打开后文件增长或缩短，在长度/后置fstat检查中拒绝；越过limit仍拒绝为budget。
无新锁、公开字段、依赖或配置项；仅既有worker执行读取，无主循环新增IO。

## 改动与验证

生产只改execution/git_snapshot.py的read_input；独立测试覆盖请求长度、空文件、
边界/超限、读取期间增长/缩短、元数据不变的短读/多余字节，以及读取后身份变化。
测试仅在本文件包裹fdopen注入确定性事件，不修改conftest或共享测试辅助设施。
先记录失败测试，再最小修复、相关回归、完整后端/前端、标准构建和源码外安装验证。
按此前用户授权同步release/envd.json构建哈希，保留envd0.5.6候选版本、依赖及文件清单；
不重建/替换远端镜像，不升级Docker，不提交或发布新正式版本。
Windows完整准备节约约2～3秒仍是实验推算，候选安装后需独立原机复验。

## 当前结果

本机修复与验证完成（2026-11-01）。Windows候选的读取及完整准备测量随后完成，
三轮均ready且清理成功，但稳定低于10秒的目标未达，详见下方原机结果。

- 红灯：旧实现11项新测试中9失败/2通过，记录read-bound-red.log；修复后新增13项通过，
  含随后补充的读取后越限增长及句柄身份变化。初批相关回归80项通过。
- 最终后端6155项全部通过，CI分片3208/2947；当前collect节点与两个成功JUnit集合逐项一致，
  无缺漏/失败/跳过，见backend-coverage.json。首轮两分片缺fd的失败日志保留；
  按项目CI准备native_assets并只为测试进程补PATH后完整重跑，未改测试断言。
- 前端69文件568项及TS/Vite构建通过，保留既有大chunk提示；未修改前端源码。
- 四平台标准build_release、Twine通过，应用源码哈希均为
  5de1f7204a8b4ac9900cc25b2624acb6c2fc5aed5bd54c8165ee5612df399004；
  标准verify_wheel_install的全新依赖安装/产品门禁通过。
- 源码外安装核对377文件；默认16MiB策略下三轮671文件读取/结束核对与内容校验、清理通过，
  实际安装源码哈希吻合。运行平台为Mac，不冒充Windows性能证据。
- Standard独立安全复核无阻断，独立运行13项通过；源码仅read_input变化。
  与141F的377个应用输入比较仅git_snapshot.py不同，未引入新API、同步包装或并行旧分支。
- envd清单仅execution_sha256更新为
  e2cabbf2091d3825ee6445161966c5c2a218dae55ff5fd4662c05c8d4b59d46d，
  独立0.5.6版本/依赖/文件清单保持；标准执行wheel构建/Twine通过，无镜像构建或部署。

精确命令、候选及交付状态以build/spec141g-20261101/verification.json为索引。
没有新增用户独立实现/掌握证据，没有真实模型请求、Git提交/push或新正式版本。

## 公司Windows候选原机结果（2026-11-01，用户截图）

候选安装核对377文件。默认策略下读取三轮0.760761/0.736371/0.796684s，
中位数0.760761s；相对旧受控样本中位数3.173331s减少2.412570s。
读取探针measured且cleanup_confirmed=true。一次完整准备使用字面占位符SSH_ALIAS导致
name_resolution_failed，不作为性能样本；改回实际SSH别名后的有效结果如下。

| 轮次 | ready | snapshot | preflight | container_create | handshake | input_install |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 9.953s | 6.969s | 1.656s | 2.000s | 3.171s | 2.938s |
| 2 | 16.875s | 12.844s | 2.250s | 2.359s | 3.485s | 3.985s |
| 3 | 11.297s | 8.250s | 2.062s | 2.031s | 3.266s | 3.010s |

报告measured/completed、count=3、median=11.297s、all_within_10_seconds=false；
cleanup attempted=confirmed=3、errors=[]。这是原机测量完成，不能称稳定10秒验收通过。
上轮141F完整准备中位数17.343s，两批差6.046s（34.86%）；机器/链路等条件并非严格配对，
不得把整体差值全部归因于本片。后续截图补齐第2轮read_local=1.891s、verify_local=2.188s；
20次Git累计19.266s，含并行，不能等同串行墙钟或可节约时间。用户随后决定接受当前首次耗时，
不再推进Git/上传性能优化。
截图摘录与计算见build/spec141g-20261101/company-candidate-observed.json。

## 用户确认的阶段收束（2026-11-01）

用户接受当前每个新沙箱的首次准备时间，停止在本阶段，不继续Git命令合并、并发度实验、
上传调优或容器池。不把未达到稳定10秒改写为通过，也不再将其作为近期阻塞门槛。
已核对生产链：同会话后续run直接resolve_capabilities/check_sandbox，transport.validate复用
SandboxConnections.get的健康通道；不会调用新快照/创建/上传。即使显式再次prepare，同ID已ready
输入、既有容器及已安装标记也有复用返回。每轮仍有有界资源读取、状态检查和实际模型/工具工作。
新会话需新准备；停止后的原环境需启动，断线或服务重启需核对/重连，不能保证零耗时。
idle_stop_seconds代码默认1800秒，实际依运行配置，本轮不读写或修改用户配置。
后续仅按正常使用出现的热态重复准备/功能异常重新评估，不自动开启新的性能实现。
本轮为代码核对与文档决定同步，未改产品或构建输入，未重跑功能测试/构建或发布。

## 网页对话验证：模板镜像差异（2026-11-01，待配置纠正后验证）

17:15截图中主故障为sandbox_creation_failed，phase=handshake、code=env_protocol_mismatch；
随后的preparation/web_run/service错误为该次未就绪的后续表现。env_stopped也可来自失败收尾，
不能独立据此判为用户主动停止。MemoryReadError在记忆入口捕获后返回空结果，单独分类。
用户确认本次是新建会话首次发送，网页所选模板仍使用早期镜像，而独立验收显式使用
naiyue-envd:spec141c-legacy-amd64。脚本--image只作用于独立临时AppSettings，不回写真实配置。
因此先纠正已确认的入口配置差异；具体旧镜像内部是启动失败还是attach/hello不符尚无现场日志，
不能把env_protocol_mismatch等同于已证实数字协议版本不同。
正常修正路径：仅编辑实际生效模板原image行→新会话执行位置刷新环境配置→重新选择该模板→
创建全新会话发送；旧失败会话冻结原selection，不随模板刷新换镜像，不自动迁移或销毁旧环境。
无需重打/导入镜像，不修改其他SSH/模型设置。配置修改由用户执行，本轮未操作真实配置或Docker。
本机确定性诊断确认空stdout+退出255、非JSON banner也会包装为同一错误码；
启动/诊断相关既有测试22项通过。仅记录诊断，无产品修改，不重开性能优化。
证据build/spec141g-20261101/web-profile-mismatch-diagnostic.json；新会话修正后的结果待用户回传。

## 网页Shell不可用：实际工具配置含Windows路径（2026-11-01，待用户验证）

用户报告Bash/PowerShell均出现沙箱Shell不可用，并确认实际tools.bash.executable填写
本机D盘bash.exe。生产链DockerProcesses.shell将settings原样序列化，envd.execute_process
在Linux内通过ShellAdapter.resolve_executable/shutil.which解析显式路径；不存在则立即报
Shell interpreter is unavailable in the sandbox，尚未启动用户命令。
本机在实际envd入口以受控which复现相同异常，默认None对照解析/bin/bash；
相关shell_adapters/docker_process_port测试16通过。显式路径无静默fallback是原合同，
不改为自动忽略用户指定解释器；空字符串也不等同默认None，会被设置验证拒绝。
独立startup脚本shell payload.settings={}，只验证镜像默认Bash；网页真实工具配置此前未覆盖，
所以独立验收通过不证明实际自定义executable在Linux可用。PowerShell能力需单独判断，
本次不为恢复Linux任务安装PowerShell或制作镜像。
恢复建议：用于沙箱的实际配置注释tools.bash.executable，启用默认发现；
需要保留D盘本机Bash时保留原配置，另存沙箱配置取消该固定路径，用--settings显式启动。
工具设置随服务装配，sandbox配置刷新仅更新sandbox部分，故改工具设置需正常停服重启；
不能声称两份配置可在同一运行服务中同时提供不同工具设置。真实配置由用户修改，本轮未操作。
后续用Bash执行pwd/id -u确认；配置修正后的真实结果尚未回传。证据
build/spec141g-20261101/shell-executable-diagnostic.json。性能优化仍按用户决定停止。
