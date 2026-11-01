# Spec141C：公司 Docker 启动兼容与可操作的失败状态

状态：2026-10-30 本机实施与常规验收完成；amd64 模拟环境200MiB压力未通过，原机待验收。本轮不发布、不部署、不升级公司 Docker。

## 1. 范围与现有调用链

正常：网页提交 → `createConversationSubmit` → 服务端待发送回执 → 准备作业 →
`EnvironmentService.create` → `DockerEnvironment.create/handshake` → 常驻 attach →
`envd.serve` → `initialize_identity` → 资源准备 → 原会话自动发送。

失败：公司容器在 `initialize_identity` 的能力检查退出（exit 1，非 OOM）；
用户确认 CapInh=0xeb，而当前必须为零。控制端握手只得到 env_stopped。
另有探针确认：消息受理后等待列表查询仍持有发送锁；状态查询失败只更新 notice，保留旧准备阶段。

本次只修上述权限兼容、失败诊断、交互和已确认的私有输入只读回收，补齐同生产启动门禁。
不引入新传输依赖、HTTP 服务、密码认证、手动回收页面或自动重放写请求。
无公开 API 签名变更；配置、镜像清单修改须以具体候选另行核对授权。

## 2. 权限启动合同

- 控制进程必须 Linux/root；Effective/Permitted/Bounding 精确等于既有六项能力（0xeb），
  Ambient=0、NoNewPrivs=1。继承能力仅允许预期集合的子集。
- 在创建任何工作线程之前，通过标准库 ctypes 调用 libc capget/capset，仅清空 Inheritable，
  保留 Effective/Permitted；原本为零则不调用 capset。不增加 SETPCAP 等权限。
- 重读 `/proc/self/status`，按最终严格状态（Inheritable=Ambient=0）复验。
  调用失败、异常能力或复验不符均拒绝启动，记录有限的能力值/错误码，不记录环境或任意异常正文。
- UID/GID1000 工作负载、补充组、有效/许可/继承/ambient 零能力及私有状态隔离保持，
  多 Docker 版本按实际行为兼容，不按版本字符串放宽。

## 3. 启动诊断

envd 在正式 serve 失败时输出一条固定前缀的结构化启动事件，包含已验证的错误码；
控制端仅在新建/启动握手失败后，核对容器归属并读取有界日志，按 State.StartedAt 限定本次启动，识别最新固定事件。
任意日志文本不进入用户错误或服务日志。诊断失败只记录 secondary 故障，保留原异常。
不对健康 RPC 增加 Docker 请求或 SSH 建连。容器为运行中时不把普通断线错误改写成启动错误。

## 4. 交互合同

- waiting_environment 回执已受理后释放本次提交锁；列表刷新是观察，不阻塞导航或改变受理结果。
  刷新失败有诊断，不能转入“发送失败”路径再次提交。
- 状态查询失败保留最后已知事实，独立记录当前会话的同步失败提示；显示“状态更新失败”和最后已知阶段。
  下一次成功查询（即使事实未变）清除此提示。切换会话不得串提示。
- 保留 A 切 B 后仍在 A 自动发送、原请求去重、草稿与图片保留；管理入口持续可用。

## 5. 私有输入回收

只在应用私有 git-inputs 目录内，对删除失败的普通、单链接、非 reparse 只读文件进行一次受控重试。
检查父目录链与文件身份；不改项目源文件、不遍历符号链接、不放宽 ACL/锁定类失败。
删除成功才释放数据库记录/额度；失败保持原异常及现有诊断。Windows 原生用例需单独运行。

## 6. 验证与影响范围

先红后绿：`test_envd_identity` 覆盖现代零继承、旧非零、capset 失败/复验失败/异常权限拒绝；
启动诊断测试覆盖安全码、无效/过大日志、归属错误与 secondary 失败；前端覆盖列表挂起、查询失败/恢复与会话隔离；
回收覆盖只读成功、ACL失败、不安全链接、失败不释放记录。

生产文件预计：envd/identity.py、envd/__main__.py、execution/docker_environment.py 及必要诊断辅助、
application/environments/inputs.py、frontend 的提交/同步/执行状态展示；相应测试、验收脚本与文档同步。
使用现有能力与入口；必要辅助仅在职责或单文件500行约束要求时拆分。
无锁关系改变；若实现新增嵌套或锁，同步更新 lock-hierarchy。

同生产门禁必须启动正式 root+六能力 envd，再 hello、执行/停止UID1000任务，并核对私有状态隔离；
镜像基础检查只证明依赖/架构，不再替代正式启动。
入口为 `python -m scripts.verify_sandbox_startup --context <context> --image <候选tag> --output <新报告路径>`，
SSH模式另传 `--context default --ssh-host <既有别名>`；仅创建/销毁自身随机命名的测试资源。
Windows原生进程/双通道、明确发送后的取消与迟到回复、只读回收接入既有 release_smoke → verify_wheel_install，
不以四平台安装成功代替真实SSH验收。迟到回复需远端 held=1、取消后 retired=1、释放后 remaining=0 的配对证据。
复用现有 persistent 验收的671文件、20 Skills、双沙箱、Read、停止与输出，补 Bash/AITest 和分段耗时。
该脚本默认8MiB quick，`--stress`启用200MiB；采样期间输出增量另报，stress须观测到真实重叠。
Read/Skills/无供应商整轮有硬预算；任意失败保留结构化报告，清理全部自建对象且不覆盖原异常。
沙箱性能夹具关闭无关记忆功能；生产记忆配置保持不变。所有入口拒绝Python -O绕过验收断言。
Spec141目标继续作为报告门槛；少量冷创建仅报告样本，不假称稳定p95。
完整后端/前端及标准构建通过后独立复核；Windows原生和公司Docker19两跳链路未运行则明确标为待验收。
共享公司资源和现有k8s容器禁止改动；压力和故障实验只在获准一次性资源执行。

## 7. 本机结果与保留限制（2026-10-30）

- 后端使用既有 CI 两分片：2864 + 3172 = 6036 全部通过，零失败/错误/跳过；前端567项与TS/Vite构建通过。
- 标准控制端 macOS/Windows wheel、执行端 Linux ARM64/amd64 wheel 与镜像构建、Twine、源码逐文件对应核对通过。
  macOS源外全新虚拟环境安装通过，原生双通道/明确发送后的迟到回复/子树清理/私有只读回收均接线通过；不外推Windows原生结果。
- Linux真实capset机制：初始Inheritable=0/0xeb均归零，任务UID/GID1000、补充组空、E/P/I/A零能力；控制E/P/B仍0xeb、NNP=1。
  正式权限启动门禁在ARM64和amd64模拟VM通过，Bash/AITest/私有控制状态隔离/停止/自身清理通过。
- 671文件/20 Skills/双沙箱常规门禁通过。ARM64完整Read p95=3.280ms、Skills p95=8.010ms、停止55ms；
  amd64 QEMU对应22.377ms/106.691ms/84ms。两次冷准备ARM64为2.314/2.137s、amd64 QEMU为16.227/15.038s，
  仅少量本机样本，不作为公司首建p95证明。
- ARM64完整200MiB采集压力通过，读取批次观测输出由65536增至983040字节，小读p95=30.593ms。
  **amd64 QEMU压力保留failed**：60s命令超时、returncode=-9，观测176226304字节未达到200MiB；
  活跃输出期间小读p95=266.663ms。没有放宽断言、增加时限或将失败改写成通过。
  另一个不含控制端归档的通道/envd探针验证了持续输出期间的停止46ms、进程已回收、通道继续可用，不能替代完整采集压力。
- 离线legacy归档的manifest/config/11个gzip层逐一核对；现代daemon通过API1.40重新导入并再次正式启动通过。
  Docker的index/image ID与config ID可不同，核对其引用链，未将差异当成内容不一致；这不是旧daemon实测。
- 证据与未通过项完整保存在 `build/spec141c-20261030/verification.json`。候选Windows ZIP/配套Linux离线ZIP在同目录，
  尚未提交、打标签或上传Release；Agent候选仍沿用0.5.6包内版本，以源码校验区分，envd构建清单按用户同意更新到0.5.6。
- 唯一下一步：公司使用新候选先跑startup.py/default acceptance.py，再做网页与真实任务；原机大输出吞吐单独测量，
  不把QEMU限制直接归因为公司问题，也不以本机单项通过宣称原机全部可用。

## 8. 公司原机首层证据（2026-10-30，用户截图）

用户提供startup.py输出和exit code截图：status=passed、exit=0、transport=ssh、phase=completed，
创建与握手9.5s，任务UID/GID1000、组空、NNP=1、CapEff/Prm/Inh/Amb均0；控制状态/Socket/环境访问均false。
Bash/AITest、停止及自建资源清理通过，停止0.094s、供应商请求0。原始JSON文件及源码安装校验未提供，
因此按“截图观察”记录，不扩大为完整Windows/公司性能验收。顶层env_stopped日志可由通道主动关闭产生，
结合本次退出码和完整成功结果判定为成功收尾；不泛化为所有env_stopped均无害。
完整输入快照、20Skills/Read、双沙箱、网页和真实模型尚待后续门禁。唯一下一步为默认acceptance.py。

### Windows 验收夹具的换行合同

公司默认acceptance.py在warm_reads阶段的AGENTS.md逐字节断言失败，截图没有实际响应字节。
本机注入Windows默认文本换行后复现：fixture生成CRLF，SnapshotBuilder按原字节保存CRLF，
与脚本写死的LF期望冲突。修正仅针对验收生成的AGENTS.md和20份SKILL.md，显式使用UTF-8/LF；
不转换用户项目文件、不修改资源读取/快照语义、不放宽逐字节断言，也不改变Agent或执行镜像。
回归在测试内部模拟Windows默认文本写入，检查生成文件和真实快照对象的确切字节。
验收失败时报告只增加固定夹具的字节数、SHA256和CRLF计数，不打印项目内容。
公司须使用修正脚本重新验收，原26.172/24.625秒仅是已观测的完整准备耗时，不是热读取性能证据。

本机先确认CRLF参数化回归红灯，再固定生成字节后相关75项通过；另注入CRLF响应，确认仍拒绝
不符字节、记录无内容诊断并清理两沙箱。标准Mac ARM64构建及Twine通过，application SHA保持
`d19e6ce6c1baeda013db04ee6bcf2b50ae3d7699b1a6f105b597bde2ef2f879c`，原候选373份Python源码一致。
公司独立脚本 `spec141c-acceptance-lf-20261030.py` 的SHA256为
`5845691b6eeecf3754458d137a0c11104adeccdcedb0c6cd407ef5108855dac9`，与校验附件追加v0.5.6候选区。
证据 `build/spec141c-20261030/acceptance-lf/`；公司重跑结果待提供，未扩大为原生Windows全部通过。

## 9. 公司原机快速主链通过（2026-10-30，用户截图）

独立LF修正脚本退出0、status=passed、phase=completed，经SSH完成671文件/20Skills/两沙箱及8MiB门禁。

| 路径 | 观测结果 |
| --- | --- |
| 两次完整首次准备 | 24.328 / 28.312秒；两个样本，不是p95，仍超过条件15秒目标 |
| 资源直接读取40次 | p50=63ms、p95=141ms、max=188ms |
| 完整Read40次 | p50=110ms、p95=187ms、max=266ms |
| 两沙箱并发资源读取80次 | p95=406ms |
| 20Skills完整资源准备20次 | p95=157ms |
| 输出期间并发资源读取40次 | p95=391ms |
| 输出与读取实际重叠 | true；读取批次前1572864字节、后8388608字节，最终8MiB完整归档 |
| 停止 | 0.125秒；A停启重连时B仍可读 |
| 假模型暖运行 | 0.375秒；0供应商请求，不代表真实模型HTTP或回答时延 |

三个热路径预算标记均true。测试使用假模型，缺失预算/调用身份/结束原因的诊断不可外推为真实模型故障；
Git的换行转换提示没有阻止本次严格字节检查通过。测试主动停止与清理会触发当前通道关闭日志env_stopped。
证据 `build/spec141c-20261030/company-quick-acceptance-observed.json` 只摘录截图，未获得原始JSON；
公司200MiB压力、网页交互和真实业务尚未通过。下一步验网页新会话，冷准备的具体阶段另需计时证据。
