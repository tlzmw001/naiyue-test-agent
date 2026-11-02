# Spec143L：S03.1 最小安全导入

日期2026-11-01，用户“继续吧”授权按S02收尾方案实施；不自动进入S04或S03.2，不提交/发布/公司部署。S02生产主链见143k，输入总合同见143b。

## 1. 当前事实与范围

S02已启动真实后端，但任务/inputs仍由受控夹具登记，镜像以sleep维持环境，没有项目安装器。A的git_snapshot.read_input使用POSIX逐级父FD、open/fstat/前后身份核对，Windows识别reparse；它把每个文件完整读内存并进入Git快照，不适合新的大文件流传输。B继承这些保护，写有界流捕获，不引用A bundle/commit/envd。标准库tarfile/gzip负责编码，不使用extractall。

本片：显式文件选择→canonical manifest/selection→tar.gz或tar→一次完整二进制PUT→验证并提交I→正式任务创建→容器内安装及回执。完整Git来源/ignore/eol转换回执、多段续传、自动暂存回收及规模优化在S03.2；结果在S07。首版保留源字节，不进行换行转换。显式源来源记录为manual，不能冒充已读取Git转换策略；后续分支写回前必须补完整来源合同。

捕获使用显式selection文件：schema=1、paths（相对文件路径列表）、executables（所选普通文件子集），规范排序，拒绝默认排除的.git/.venv/node_modules/缓存/已知凭证文件。本片不自动读取Git配置/filters/ignore或隐含纳入整个目录。选择范围改变即policy_sha改变；selection归档后由结果捕获继续消费。相对安全符号链接可保留，Windows不支持的reparse类型明确拒绝。空目录不交付。

manual schema1的paths限定首次输入，不是结果文件白名单：S07结果范围是任务project工作区，仍应用本schema固定安全/凭证/依赖缓存排除，必须纳入Agent新建的普通文件。输入归档的entries=paths核对不能复制成O只允许原文件的约束。S03.2再冻结完整Git/ignore/来源策略；不得在同一schema下静默改变旧policy解释，规则变化产生新的策略身份。当前接口不支持把任务工作区以外目录加入结果。

## 2. 格式与准入

input_id=SHA256(canonical manifest)，manifest固定schema/selection.policy_sha256/排序entries；entry有path/type(file|symlink)/size/sha256/mode，link另有target。UTF-8 JSON键排序、无空白、拒绝重复键/未知字段/非有限值。paths使用NFC规范、拒绝绝对/点段/反斜杠/控制字符/Windows保留名、ADS、尾点空格及大小写/Unicode碰撞；还拒绝文件与父路径冲突。链接目标按整个清单解析，拒绝越界/循环/歧义，不经链接创建其他文件。

归档固定前两个成员manifest.json、selection.json，其余为data/<path>，按清单排序；普通文件/相对symlink，无目录/硬链接/设备/FIFO/sparse；安全PAX路径元数据需有界且限制字段。gzip level可配置默认3。传输大小/hash与内容身份分开；服务端实际计算两者。tar解析头/展开字节/数量/单文件/元数据/尾部均有预算；不以tarfile默认行为代替校验。

默认单文件64MiB、输入展开128MiB、归档160MiB、文件数4096、manifest/selection各1MiB、IO256KiB、pending数8、已提交归档加预留归档512MiB、单次上传超时120秒；均由显式配置默认值提供。metadata另受大小/条数预算，不将归档预算冒称物理磁盘硬配额。本片PUT的预算为整个归档上限，且只支持offset=0的一次完整PUT；不宣称断点续传。S03.2在相同路由/record上增加段落，不硬编码8MiB。

## 3. 生产数据流与失败

正常：`cloud input capture → 安全流读取/私有staging → manifest/归档/本地回执`；`cloud input upload → owner鉴权/instance → GET input命中则零上传 → POST transfers持久回执 → PUT bytes → POST commit → GET input`；`POST tasks → 同事务核对项目/ready输入/容量并写task+receipt → S02 start → SDK preflight → install_input → backend_ready`。

捕获固定清单/源根身份，逐文件在读取前对照清单身份，读取计数/hash和句柄前后核对，结束再核对源路径和目录成员；源变化或不可读明确失败，不产生可用捕获目录。普通源硬链接复制字节，不生成归档硬链接。Windows使用实际文件handle/reparse检查，不声称本机Mac单测已证明Windows原机。

失败上传：持久offset仍0；取消/短流/hash错误不标uploaded，下一次offset0完整重试从头覆写自身暂存。uploaded后的同内容重复PUT实际核对请求体再返回原offset，不能只信请求头。写入fsync后才持久offset=size。commit持有本transfer操作预留，流验证归档后只提交archive/manifest/selection到inputs/<I>，不在Coordinator解包项目文件/执行代码。先目录原子提交，再写ready记录；若目录已提交而DB未落盘，下次commit按固定archive/hash清单对账，不盲覆盖。注册记录存在但文件丢失/被替换拒绝，不能只凭记录启动任务。

已ready同I可以复用原归档，即使本次压缩字节不同；传输记录明确cache_hit，不把新archive_sha冒充原归档事实。受限metadata/文件身份仍校验；同ID不同摘要拒绝、原回执先于容量。取消只删除未提交的自身staging，路径由ID派生；已提交输入不可取消。无后台自动删除或旧数据清理。

## 4. 存储、并发和身份

复用registry meta，键input:<hash>/transfer:<uuid>，值严格DTO JSON；与现有history状态同属受控索引，按ID查询，本片无需新表或A迁移。所有SQL归storage/cloud/inputs.py。transfer注册与command receipt同事务；ready登记与transfer状态同事务；正式task登记与receipt同事务并检查ready input/保留额度。S01/S02注册表格式仍1；不增加兼容分支。

传输bytes/commit/cancel用同一owner事件循环的每transfer操作预留（不是跨await线程锁），不同transfer互不阻塞；commit/cancel由独立producer持有transfer/input预留，HTTP取消立即返回但不撤销已接受操作；raw流取消则排空实际本地IO再释放。每次本地IO在线程内经runtime.operation，普通元数据保留S01既有取消语义；关闭先排空inputs生产者，再排空lifecycle，各自失败不跳过其他资源。任务创建不依赖backend运行，但必须选已配置固定template，启动继续由S02命令处理。

客户端在私有capture目录持OS锁，按服务instance保存原transfer command_id，发POST前fsync；create/upload/commit ACK丢失后重用同ID。GET input命中返回零上传；ready包登记包含archive文件身份，热准入核对身份/大小和小metadata文件，不重复hash完整归档；commit/安装在同一次流中核对归档SHA与逐文件SHA。等长替换/文件丢失拒绝。目录发布后DB失败，原commit可核对已发布包补登记；显式cancel先恢复这类发布窗口再拒绝取消，不生成未计数孤儿输入。

inputs/staging/捕获缓存只在显式新根、私有服务账号下读写；目录和文件不跟链接，失败记录有限码/类型，日志不含内容/凭证/内网地址。Coordinator不打开任务SQLite、不挂Docker socket。captured source只读，不修改.env或Git。新增settings限额不修改真实配置文件。

## 5. 安装与版本边界

安装程序属于cloud_runner，但本片不启动Agent循环：SDK用固定解释器/固定参数调用安装模块，参数只含受控input_id。挂载仅本任务workspace/state及该I的RO目录。安装器再次验证manifest/selection/归档，写独立workspace内staging，先普通文件后安全链接，完成全部hash/模式核对后提交project目录并写安装回执（实际执行根为/workspace/project）。必要状态在/state保存，S04沿用同一模块/回执。

初始workspace必须空；中断后保留可核对阶段，只处理自己标记的staging，不清空未知/任务已有目录。已有安装标记且身份匹配时重建不重装、不重哈希修改后的业务代码；标记/项目丢失或身份不符失败。发布目录→回执之间崩溃，以持久安装阶段及已安装清单核对恢复，不能把用户后续改动覆盖为I。

镜像更新：包含本模块的当前wheel按S02受信入口重建新模板；旧模板缺模块不能标安装成功，不自动向旧任务热注入代码。S02环境阶段新增input_ready事实/必要安装错误，不称Agent ready。旧S02测试夹具迁移成真实已提交输入，并给FakeBackend补消费同一安装函数的受控测试实现；保留原权限/异常/配对断言，不保留产品输入校验旁路。

## 6. 接口和文件

owner新增POST/GET transfers、PUT transfers/{id}/bytes（Content-Length、Upload-Offset、X-Content-SHA256）、POST commit（{}）、DELETE transfer、GET inputs/{id}、POST tasks；task app不暴露。JSON元数据受原_body大小/深度/鉴权保护；raw请求在消费body前验证身份、ID、重复关键header、长度/类型/预算。

新增contracts/cloud_inputs.py；输入stream/归档/捕获模块按职责分文件（每文件≤500）；storage/cloud/inputs.py；application/cloud/inputs.py、input_upload.py；service/cloud/input_routes.py；cloud_runner/input_install.py；cli/cloud_inputs.py；连接器capture/upload模块。扩展runtime/settings/commands/生命周期/SDK/DTO/CLI，锁表同步。优先标准库及已锁定httpx，无新依赖。现有A主链仍有消费者，S09再退；本片不留临时协议/包装或绕过正式commit的产品入口。

实现细化：Windows源硬链接读取在cloud_files.paths中使用只读reparse检查句柄；不修改A已锁定envd使用的storage.files，也不升级envd版本。service/cloud/http.py只放有界JSON读取，三个IO使用方共用application/cloud/work.py，不保留旧复制worker包装。S02测试helper改成已验证/提交输入并用同一安装函数，公开接口未增加夹具旁路；S01“尚无tasks入口”的断言迁移为严格准入，并保留tasks:run未暴露及原身份/回执断言。

## 7. 验收

先失败测试覆盖：canonical身份/路径与链接、源变化/硬链接/reparse、CRLF executable、原字节/二进制/模式、恶意tar/PAX/重复/膨胀/尾部、上传鉴权先于body、短流/超长/hash/取消/原ID/额度/commit崩溃对账、同I零上传与独立工作区、task不能引用未ready输入、安装中断/停止重建不覆写。真实SQLite/ASGI/线程事件同步，不只测工具函数。

专用本机Linux后端用最终源码镜像完成实际owner API导入→建任务→安装→修改→停重建保留，Kit在实际project中跑pytest；独立观察挂载/身份。复用S02专用VM和唯一资源标记，恢复原停止状态，不操作公司机器。Windows最小目录复验另给步骤，不把Mac模拟认作原机通过。安全/架构复核后跑后端两分片、前端/构建、标准wheel/Twine/源外安装，更新handoff并讲S04.1。

## 8. 实际实现与验证（2026-11-01）

本片完成，证据`build/spec143-s031-20261101/verification.json`，Windows原机步骤见[最小复验](../cloud-input-s031-windows.md)。入口为`nyt cloud input capture/upload`；正常链为`cli.cloud_inputs → cloud_files.capture/InputClient → owner input_routes → CloudInputs/InputRepository → create_task → S02 lifecycle_actions.start → OpenSandboxBackend.install → cloud_runner.input_install`。没有公开夹具/输入校验旁路、第二套上传协议或A bundle依赖。

输入校验与逐文件hash在受限流内进行，Coordinator只保存归档与小metadata，真实解包在容器内。任务新增input_ready，backend_ready仍不代表Agent运行；实际项目根为/workspace/project。新版本镜像包含同一个安装器，停重建沿用其安装回执，不覆盖用户修改。

| 验证 | 实际结果 |
| --- | --- |
| 新增行为 | 47项；`tests/cloud`全量＋存储架构208 passed/5.25s，包含原S00/S01/S02断言，无skip |
| 产品后端 | 最终两分片3278＋3077＝6355 passed，380.29s/322.25s；唯一warning为既有恶意重复METADATA夹具 |
| 前端/构建 | 69文件568 passed；TypeScript/Vite通过，仅既有bundle大小提示，无前端源码改动 |
| 发行候选 | 标准四目标wheel/Twine通过，417个生产Python逐文件匹配当前源码且四包同源；Mac源码外干净安装通过，最终Linux ARM64 wheel在真实镜像运行；Linux x64/Windows只交叉构建 |
| 实际导入 | 安装后的CLI捕获3文件、约256KiB展开/3264B gzip，经真实Uvicorn owner API上传/commit/POST tasks；同I再次导入上传0字节 |
| 实际安装/执行 | 两任务input_ready及backend_ready，选定测试解释器在真实project执行pytest 1 passed/JUnit；一侧改proto后stop/start保留，另一侧原内容不变 |
| 独立观察/重开 | 最终image/UID1000/cap/NNP/2CPU/2GiB/PID256/三个精确挂载/输入RO/execd loopback和Coordinator无socket通过；实际Coordinator重启后input索引、缓存零上传及活跃任务保留 |
| 小样本计时 | capture 0.103s、upload＋commit 0.265s；两次backend＋input ready 2.680s/2.459s，不是Runner ready、p95或公司链路性能，不能外推大项目 |

正常及故障的持久事实均可检查：错误hash/短流不推进offset；创建/上传/提交ACK丢失复用客户端日志中的原ID；发布后DB失败由原transfer恢复；prepared安装在rename前/后中断均核对后继续；连续取消不会提前释放producer预留，显式cancel不能把已发布未登记I变成孤儿。架构复核4项已先红复现、修正并二次关闭。新增已知凭证/私钥标记、LFS跨小块、非法Windows字符、源变化、Unicode/PAX/尾部及等长归档替换回归；自动安全复核子任务被工具标为可能网络安全风险而中止，未作为完整自动复核通过，已返回具体问题由本地回归和手工检查处理。

首次全量因修改A锁定storage.files触发envd源码指纹门禁；本片已将该文件逐字节恢复到S02基线，Windows源读取保存在B捕获模块，不推进envd版本。原失败日志保留，最终完整两分片重跑全绿。其余既有源仅改11个（9生产/2测试），新增14生产/5测试源，最大196行；两个测试变更分别迁移S02真实输入夹具和S01已实现任务创建入口，未删减原权限/取消/回执断言。不改真实.env/用户配置、依赖或既有A数据，不保留产品旁路。

本次测试容器/网络/三条专用bridge规则/运行期DNS文件已收束，镜像/证据/私有夹具保留，专用VM恢复停止、默认Docker context不变。无提交/push/发布/公司部署或真实模型/业务请求。首版单次PUT支持完整重试，未实现分段续传/完整Git来源/自动清理；Windows实际句柄/reparse/路径需原机验证。没有新增用户掌握证据。

唯一下一步：讲解并确认S04.1最小Runner/必要历史投递/控制。先沿143i装配拆分线复用现有循环和Host工具，消除项目级全局库/旧A绑定依赖，使用本片输入安装及固定测试环境；不复制Agent运行时、不把测试替身作为产品模型或注入真实key跳过S05。本轮不自动跨片。
