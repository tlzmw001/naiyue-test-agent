# S03.2 Windows 原机复验

本片的Mac测试、Linux ARM64真实HTTP/容器及Windows锁字节模拟不等于Windows原机通过。原S00中转结论不变；S03.2须单独补证。先由获准操作者部署独立B验收实例和本片最终模板，不使用A数据根、不把S00探针控制容器当成B产品；公司部署和真实配置修改另行确认。

## 1. 候选与连接

使用独立Python3.12环境安装`build/spec143-s032-20261101/dist-windows-x86_64/`中的候选wheel，复制后核对本片verification.json里的SHA-256；这是本地候选，不是新PyPI版本。保留A当前环境。Git需可从PATH调用，记录Python/Git/Windows版本。

连接使用[S03.1第2节](cloud-input-s031-windows.md#2-专用传输连接)的独立SSH文件通道与私有连接JSON，Host/Origin仍是远端owner_authority，endpoint才是Windows本地转发端口。只关闭本次SSH窗口，不终止其它SSH进程。连接文件和SourcePlan所在目录ACL应只允许当前账户访问，不打印/提交token或内网地址。

下面变量均填实际绝对路径：`$CloudNyt`为新环境Scripts\nyt.exe，`$CloudSource`为新测试Git仓库，`$CloudPlan`、`$CloudCache`位于source之外，`$CloudLimits`为私有限额JSON，`$CloudConnection`为私有连接JSON。

## 2. 来源和缓存

先用无凭证测试仓库：包含tracked文件、未跟踪proto、普通CRLF文本、二进制和被ignore的文件。`.gitignore/.gitattributes`用UTF-8写入；PowerShell5默认UTF-16文件不能当UTF-8策略使用。Git不需要先产生提交；记录的HEAD可为空。

```powershell
& $CloudNyt cloud input select --source $CloudSource --output $CloudPlan --limits $CloudLimits
$First = & $CloudNyt cloud input capture --source $CloudSource --plan $CloudPlan --cache $CloudCache --limits $CloudLimits --codec tar | ConvertFrom-Json
$Second = & $CloudNyt cloud input capture --source $CloudSource --plan $CloudPlan --cache $CloudCache --limits $CloudLimits --codec gzip | ConvertFrom-Json
```

检查private plan里的selection.paths/executables/policy：ignore项和固定凭证/依赖排除不进入输入，普通proto纳入；不打印完整来源配置。两次input_id/directory/archive_sha256一致，第二次复用既有codec；source_receipt是两份独立本地回执。保留cache，upload使用返回的directory。

用新plan文件分别验证：修改ignore、Git index执行位或新增路径后，旧plan capture报source_plan_changed；重新select后才能捕获。tracked执行位由Git stage元数据决定；Windows普通未跟踪文件通常没有POSIX执行位。CRLF shebang且被指定为执行文件时应明确拒绝；普通CRLF文本字节不转换。子模块及其后代不能通过父Git plan include，应作为另一个明确manual来源处理。reparse/链接用原机补证，不把模拟当实际行为。

## 3. 200MiB与续传

默认128MiB展开预算不够本探针。验收实例与本地限额应显式配置：单文件256MiB、展开260MiB、归档272MiB、缓存1GiB，默认段8MiB、IO256KiB；只在独立验收配置使用。测试文件用分块写入方式生成200MiB随机字节，不要一次在PowerShell内存中构造整份数组。重新select/capture并记录文件SHA和归档大小。

```powershell
& $CloudNyt cloud input upload $First.directory --settings $CloudConnection
```

上传中用另一连接查询原command回执的resource_id，再查询transfer。原command_id位于capture目录的`upload-<instance_id>.json`，不是每次重试新建；多实例时必须选当前instance的日志。

```powershell
$Receipt = & $CloudNyt cloud command show $UploadCommandId --settings $CloudConnection | ConvertFrom-Json
& $CloudNyt cloud input transfer show $Receipt.resource_id --settings $CloudConnection
```

确认offset已大于0后关闭本次文件SSH通道，再以相同端口重连，用同一capture目录重新upload。GET先核对已确认前缀、截断未确认后缀；重试从已确认offset继续，已确认段不重发。若传输在人为断线前已完成，只能算缓存命中，不能算续传门禁；应改用可控响应丢失/限速探针，不依赖紧sleep窗口。

记录第一次中断前已确认offset、重试发送字节和最终归档SHA；完整archive commit/校验后才能GET input和创建task。再次upload必须cache_hit=true、uploaded_bytes=0。对准备放弃的明确未提交transfer可用`input transfer cancel <id>`；ready输入不能被cancel或TTL删除。

## 4. 安装与证据

通过内部CLI创建项目/task/start，检查input_ready、实际项目文件SHA/模式并在容器运行Kit pytest；报告通过artifact capture/download取回。停止容器、Coordinator重开后应能查询I、再次零上传和下载已提交产物。不要给Coordinator挂Docker socket。

报告仅含版本/候选SHA、OS/架构、Git进程次数、输入/归档字节、确认offset/重试字节、RSS测量范围、安装文件hash、pytest摘要和清理结果。需要实际Windows验证的重点是Git stdin/进程、文件身份/执行位、cache OS锁和reparse，不包含模型供应商或公司业务RPC验收。保留本地回执/日志供核对，不发送凭证或源内容。
