# Spec143 最终 Windows＋Linux 验收手册

本手册针对S09正式B、S10.1/S10.2及S11最终候选。候选尚未提交/发布/部署；版本号仍为0.5.6，必须同时核对candidate_id、候选ZIP SHA及wheel SHA。旧R1/R2附件、已安装0.5.6和本轮候选不能只按版本号判断同源。代码/本机夹具完成与下列原机验收通过分别记录。

## 1. 准备与机器分工

Windows运行本机Agent/Web、文件捕获/取回、OpenSSH和验收工具；Linux运行既有OpenSandbox、Coordinator、Runner及独立Updater。保留已经通过的S00主机/Docker/网络证据，只重测受本轮影响项；换了机器、backend、基础镜像或网络策略时再按[S00](cloud-s00-local-and-windows.md)检查对应边界。第一次完整B部署按[原机准备手册第2–5节](cloud-s091-windows-and-linux.md)，本页优先于其中旧阶段包和结果交付文字。

准备四个独立目录：解压候选只读材料、Windows本轮venv、私有配置、测试项目及报告保存目录。私有配置/SSH key不放入任务输入或候选目录。不要清理旧用户库、A会话/目录/镜像或全局Docker资源。旧sandbox会话不支持迁移或继续执行；新建B任务验证，普通本机会话历史保留。

手册中的ROOT/PY/REMOTE_JSON等均由操作者填写原机真实值，不照抄示意字符串；命令非零立即保留原操作ID和错误码，解决后从同一步继续。

## 2. 验证候选并更新 Windows

先在Windows核对ZIP外侧.sha256，再解压到新目录。PowerShell中填实际路径：

```powershell
$Kit = (Resolve-Path '本轮候选解压目录').Path
$Root = (Resolve-Path 'Windows本轮验收根').Path
$OldNyt = '原安装的nyt.exe绝对路径'
$Settings = '本轮原有settings.toml绝对路径'
$Models = '本轮原有models配置绝对路径'
# 停止本轮对应旧共享服务；不能用新CLI强行复用旧服务。
& $OldNyt service stop --settings $Settings --config $Models
if ($LASTEXITCODE -ne 0) { throw '先核对原服务是否正常停止' }
Get-Content (Join-Path $Kit 'SHA256SUMS') | ForEach-Object {
  $Parts = $_ -split '  ', 2
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Kit $Parts[1])).Hash.ToLower() -ne $Parts[0]) {
    throw "SHA mismatch: $($Parts[1])"
  }
}
# 首次候选用新venv；已有本轮独立venv可保留原依赖，明确换wheel。
py -3.12 -m venv (Join-Path $Root 'venv-final')
$Py = Join-Path $Root 'venv-final\Scripts\python.exe'
$Nyt = Join-Path $Root 'venv-final\Scripts\nyt.exe'
$Wheel = Get-ChildItem "$Kit\wheels\*win_amd64.whl" | Select-Object -ExpandProperty FullName
& $Py -m pip install $Wheel
if ($LASTEXITCODE -ne 0) { throw '候选安装失败' }
& $Py -m pip check
if ($LASTEXITCODE -ne 0) { throw '候选依赖检查失败' }
Set-Location $Kit
& $Py -m scripts.verify_cloud_candidate --root $Kit --installed
if ($LASTEXITCODE -ne 0) { throw '候选或安装源码不一致' }
```

需要已批准的公司镜像源/离线依赖时沿用既有安装方式，不通用升级全部依赖。本包包含应用/Kit和锁文件，不是所有第三方依赖的离线wheelhouse。校验输出必须为passed且installed_files非零。保留候选SHA和校验JSON；用原settings/models/workspace参数启动新Nyt，打开这次真实私有入口并强刷。原机命令中的端口、连接信息和密钥沿既有私有配置。

## 3. Linux 管理程序和执行模板

Coordinator与Updater都须安装本轮Linux x86_64 wheel；不能只升级Windows。先enter维护、正常排空/释放验收任务并按原部署方式停Coordinator，再在独立运维venv安装同源Linux wheel、pip check、候选已安装源码检查。重启原Coordinator后核对原instance_id/历史/已发布结果；这里是用户在原机明确执行的升级步骤，本机开发并未替用户部署。

执行模板通过[更新手册](cloud-updates.md)的prepare或可信manifest更新路径完整构建/预检。第一次准备需原机实际base image digest、Docker backend ID、UID/GID、网络/镜像源和已批准wrapper；不能把本机受控Docker测试当真实预检。确认输出模板image ID、runner_release_id、wheel/Kit/锁SHA以及真实解释器、非root、Kit/pytest和工具预检；任一不符停在这里。

本轮包含标准四平台wheel，方便发布端生成同源manifest；日常Windows/Linux验收只安装各自平台。生成器及配置归属见cloud-updates第2/6节，`source_commit`填写实际基础HEAD，并保留candidate_id/实际源码SHA说明未提交身份。`public_base_url`必须是管理员批准的真实可访问发行位置；本地离线index用Updater的离线模式，不要用示例URL假装已发布。

如要验证旧新版本：保留已经验证的旧模板和停止任务；以本轮模板作为新版本，准备/激活后新任务固定新模板，旧任务仍固定旧模板。两个release标签指向同一wheel不能算两版本证据。需要显式旧任务换代时按第8节做，不热替换运行中的进程。

## 4. 照片中10项问题逐项复验

使用两个独立任务，分别发送内容明显不同的消息、生成不同报告；任务1至少三轮，任务2至少两轮。再建任务3，按1→2→3→1的顺序切换；全程记录task_id/run_id与时间。每项填写通过/失败/未测，不能仅截最终静态画面。

| 照片项 | 实际操作 | 预期 |
| --- | --- | --- |
| 1 任务只有ID | 新建两个任务，观察侧栏和时间 | 显示可读标题/真实时间，能分清先后；短ID仅辅助 |
| 2 模型加载占位 | 首次打开模型列表、等加载、选模型，再开关列表 | 加载态不作为可选模型残留；无数据为空或明确错误 |
| 3 发送后内容卡输入框 | 发送正常消息及被拒绝消息各一次 | 即时显示受理/发送反馈；拒绝有可操作错误，原内容不无提示消失；无重复执行 |
| 4 终局工具卡裸文本 | 包含read/write/bash的任务，终局后切换再返回、刷新 | 工具调用/结果配对为卡片，终局前后与历史展示一致 |
| 5 结果互见/命名 | 在三任务之间反复切换中央结果窗口 | 每个任务只见自己的结果；运行序号/时间/摘要可辨；来源可核对 |
| 6 底部挤压正文 | 长历史，打开过程/主任务/结果入口，1440宽和375宽各看一次 | 历史滚动区有足够空间，辅助内容在弹窗/折叠入口内，操作区紧凑 |
| 7 revision409误文案 | 后台仍更新时操作或并发刷新造成revision过期 | 提示状态变化/刷新后重试；不误报“任务仍运行”等执行拒绝，刷新可继续 |
| 8 停止隐式杀后台 | 无活跃run但保留测试mock后台服务时查看停止入口 | 文案/确认明确含后台任务；实际停止对象与提示一致，日志可核对 |
| 9 删除409静默失败 | 两窗口取得旧任务状态，另一处改变revision后确认删除 | 显示拒绝/刷新提示，不把失败当已删除；刷新后显式再删 |
| 10 Win11终端弹窗 | 原Win11＋Windows Terminal设置，正常停启本机共享服务两次 | 无额外python控制台弹出，后台日志可查；退出launcher不杀已受理云任务 |

照片5/10需要在原出现问题的Windows环境复验；本机API隔离/launcher参数检查不能替代这两项。删除仅针对本轮明确标记的测试任务；先截图/记录其ID，保留其他任务。

## 5. 报告、代码和主 Agent 分析

按[结果交付合同](cloud-result-delivery.md)在同一固定结果完成以下操作，每项保留result_id和交付操作ID：

1. 点击“结果与产物”应在屏幕中央弹出，内部滚动，关闭/Escape返回原任务。报告列表只包含本轮专用产物目录文件；源码、manifest/patch不混成勾选报告。没有浏览器单文件下载入口。
2. 勾选两个报告，浏览选择已有目录，预检后点保存；新建文件夹再保存一次。普通“已核对保存位置和文件”勾选已删除，保存按钮保留，两项覆盖确认之间不留大空白。
3. 同名同内容可复用；同名不同内容明确列出冲突，重新确认后覆盖。预检后主动修改目标文件，再点保存必须拒绝；重新预检/查询回执后覆盖勾选清空，不能沿旧许可写新内容。关窗/重开继续查询原操作，失败显示真实partial/unknown。
4. Git项目：未预检不能应用；预检通过应用本次新增/修改/删除路径，其他文件保持。符合Git条件时可创建新分支，核对分支和文件；有本地改动导致分支条件不满足时明确拒绝。无Git目录只有应用入口。
5. 做本地内容冲突，预检必须显示冲突；确认“强制覆盖”后仅处理本次变化路径，不清空其他文件。路径、摘要、权限等失败不可被强制绕过。留一份自己的测试副本，供比对覆盖前内容。
6. 连续两轮云端改同一文件：第一轮完整应用后，第二轮预检沿更新后的本机基线继续，不误用最初输入；随后手动改该文件，下一轮必须识别本地冲突。新分支/报告保存/分析不应推进当前目录的应用基线。发生partial/unknown先按原ID核对，不换ID重做。
7. 点“交给主Agent分析”：来源主输入框得到草稿与固定材料引用，已有草稿保留，不自动发送。用户明确发送后模型才运行，真实调用cloud_result_read读取固定清单/patch/报告；不是仅把“已分析”文本塞入会话。材料接收后暂断云连接，已接收材料仍可读；未接收材料明确不可用。

## 6. 断线、任务隔离和公司业务

分别做两种样本：不依赖Windows中转的文件任务，在Windows观察端关闭后继续执行并自主产出结果；依赖Windows中转的公司HTTP/proto RPC任务，在中转断开时明确失败/不可达。前者成功不能证明后者关机仍能调公司服务。重新连接/重开本机服务后核对原run/command/result/transfer，不额外发送来“恢复”。

至少一个真实HTTP接口和一个proto RPC测试，记录测试环境/接口类别、AITest实际命令、结果报告及业务断言；地址、认证、请求响应敏感内容保留本地不回传。使用真实已配置供应商验证模型目录、非流/流、工具调用/取消/错误与各原生协议路径。协议夹具通过不等于真实供应商通过。

双任务并发时核验内容/事件/结果/凭证不串；按既有原机手册第7节核验容器挂载、UID/GID、caps、NoNewPrivs、资源限制和管理面拒绝。任务中不得有宿主Docker socket或真实供应商key。先证明目标服务存在，再做拒绝访问检查，connection refused不能作鉴权通过。

上传/下载中断后沿原transfer继续，SHA一致、已确认段不重传，最多重传一个不确定段；结果下载/保存未知ACK沿原操作查询。停止、强停、后台服务/子任务恢复和删除仅用本轮可丢弃测试资源，未知工具副作用先核对，不自动重放。

## 7. 可执行双任务20轮采样

工具会创建专用项目和两个任务并发送40次真实模型请求，可能产生供应商费用；只有操作者显式运行并给出`--confirm-runs 40`才开始。先做rounds=1、task_count=1的烟测，通过后使用一个新的输出目录做正式20轮；不将烟测和正式样本混合。

Windows在私有目录创建两个JSON（UTF-8、保护NTFS权限），均放在source外。selection只用schema1显式文件列表，路径按实际测试项目填写；不要选密钥、.env、私有配置或.git：

```json
{"schema":1,"paths":["实际选定的公开测试文件相对路径"]}
```

workflow.json结构如下，所有绝对路径和模型/模板ID填原机实际值，message可按测试项目补充，但保留`{task_marker}`：

```json
{
  "connection_file":"Windows私有CloudConnection绝对路径",
  "source":"Windows公开测试项目绝对路径",
  "selection_file":"私有selection.json绝对路径",
  "template_id":"实际已预检模板ID",
  "model_id":"实际云端模型ID",
  "message":"请调用task_output_directory，在返回的本轮产物目录中写acceptance.txt，内容必须包含此完整标记：{task_marker}。然后结束。",
  "rounds":20,
  "task_count":2,
  "timeout_seconds":900.0,
  "poll_seconds":0.5
}
```

```powershell
Set-Location $Kit
& $Py -m scripts.verify_cloud_workflow --settings '私有workflow.json绝对路径' --output '新的验收输出目录' --confirm-runs 40
# 连接断开或进程退出后，只用同配置、同输出目录及原命令恢复：
& $Py -m scripts.verify_cloud_workflow --settings '私有workflow.json绝对路径' --output '原验收输出目录' --confirm-runs 40 --resume
```

工具固定首次instance_id；同路径连接文件换实例会拒绝。每次POST前持久保存原UUID/请求，恢复先查原回执；unknown/failed/CAS冲突直接失败，不换ID重试工具。修复连接后可继续原ID；真实unknown需人工核对，不把重复执行冒充恢复。输出目录加锁，禁止与source重叠；脚本不清理已创建任务，验完后按确切ID自行释放。

`report.json`记录包版本、平台、instance/input/project/task/run/command/result/release/generation、第二次上传0字节、原容器复用、跨任务结果404、逐轮字节和阶段耗时及p50/p95/max。恢复中断轮标resumed，不进入连续墙钟统计；样本不足20需另开完整样本补证。完整stdout/report不含token/endpoint/消息，私有journal和downloads可能含请求或源码，不能整目录外传。

脚本的run_terminal含真实模型和业务耗时；ACK不等于环境已就绪。它**没有测量**“请求准入→可进入模型/工具”的纯热环境开销≤1s，也不测raw SSH吞吐或UI负载增量。ready_seconds是同机观测start→ready（不含先前create），不能直接冒充143G要求create接受→ready≤8s。各项真实门禁按下一节单独取样。

## 8. 更新、备份恢复和回收验收

按[更新手册第7–10节](cloud-updates.md)使用Windows `--remote`触发Linux，保留原UUID。先在本轮专用验收根做完整演练，不能对未盘点的真实业务根做恢复/GC。

| 操作 | 核对结果与故障样本 |
| --- | --- |
| discover/update | 同一渠道重复发现续接同ID；auto_activate=false只ready；完整预检失败/坏SHA/缺盘时旧active保持；SSH断开后status/resume原ID |
| active/旧任务 | 新任务固定新模板，已有任务保留旧pin；显式upgrade先停止写入并备份，启动新generation；不兼容拒绝且stop/recover/必要保存可继续 |
| 整机backup | enter维护→排空→全部任务环境释放→Coordinator正常停服；有遗留task/helper容器必须拒绝；成功有complete及SHA，opaque state/WAL不被管理程序打开 |
| restore | 以备份UUID确认，准备独立新根；prepared不可启动；内容/权限变化启用拒绝；再按RESTORE_UUID确认启用，新根历史/输入/结果可读且任务可再次运行，旧根明确拒绝 |
| GC | 同样离线前提，先预览candidates/protected，再确认同一plan_sha256；active/停止任务pin/待完成操作/保留备份所需版本受保护；外来tag/容器不删；无force/prune |
| 运维失败恢复 | 受控中断worker后沿原ID继续；删除不明时guard阻止重开或新更新，完成后开放；不得删guard/改schema绕过，旧程序须拒绝新格式管理根 |

创建维护、恢复、GC时确认的是具体UUID或清单摘要；这与已删除的“普通报告保存额外确认勾选”是不同动作。这里是操作者执行会改变本轮测试资源的运维步骤，不是自动清理命令。

## 9. 性能与最终判定

按[143G完整矩阵](specs/143g-acceptance.md)登记原机CPU/内存/磁盘、Windows/Linux/Terminal/OpenSSH/Docker/SDK/镜像版本、SSH链路、队列和样本展开字节/文件数/变化量。正式每场景20轮，保存原始值并按nearest-rank报p50/p95/max，不仅报平均。

必须分别记录：首次安装分阶段、首次输入扫描/哈希/上传/校验、缓存新任务create接受→Runner ready（标准文本p95≤8s）、同任务纯热环境开销（p95≤1s）、上轮必要终局→下一轮可准入、终局→结果available、200MiB传输期间状态/停止请求额外延迟（p95≤500ms）、同链路raw SSH基线与完整/续传吞吐（中位≥基线70%）、二进制小差异、双任务并发、50工具序列、模型代理首块增量和小差异本机交付。默认输入128MiB/4096文件不足200MiB样本，须先在本轮测试配置明确提高额度并记录，不能悄悄截小样本。

冻结组件可在实际目标机独立跑：

```powershell
& $Py -m scripts.profile_result_capture --rounds 20 --label '原机实际CPU内存和平台' --output '新的capture-profile.json'
```

这是600/4000文件冷/暖冻结组件采样，不能代替整链路、2CPU/2GB或公司网络结果。固定50工具和供应商对照复用当前协议fixture/正式工具轨迹与143G方法；未取得独立样本的项填未测，不用模型自述补数字。

最后回传一份脱敏验收记录：candidate_id/ZIP SHA、两端安装检查、模板receipt的非敏感身份、照片10项表、Git/非Git连续取回结果、真实HTTP/proto RPC/供应商结论、断线原ID状态、运维演练、原始性能样本和未通过项。每项含机器、操作、预期、实际、错误码及必要ID；可附report.json和局部截图。不要回传私有连接配置、journal、SSH key、token、供应商原文、用户源码或整份HAR。出现失败保留现场和原ID，不删库/换任务掩盖问题。

## 10. R3审阅修复补验（2026-11-03）

使用同一R3候选更新本轮独立Windows环境与Linux测试服务，保留原S11/R1/R2现场；不要将新内部准入拒绝协议与旧Coordinator/Runner混用。仍按前文核对candidate_id、ZIP SHA与两端安装身份。R3包含R1落盘与R2兼容修复，原生捕获、中文路径/输出、CRLF和文件锁测试继续保留。

- 结果窗口点击“清理未引用缓存”，记录文件数/字节数；有保留材料时清理后仍可分析、保存和交付。在专用验收会话接收材料、删除该测试会话后，从另一会话触发清理，确认孤儿可回收；不要删除真实业务会话制造样本。正在接收/交付时应拒绝清理或禁用入口，完成后可重试。
- 在专用Linux测试项目产生非便携输出路径，应用、创建分支和底层导出预检均应报告具体路径且禁止强制执行。可用普通`report:notes`文件名；无需在Windows先创建非法名称。
- 准入竞争若出现，业务拒绝须显示结构化失败，控制连接保持，不能凭拒绝生成unknown或要求显式恢复；忙碌结束后可再次发起。人工不能稳定触发时标未测，确定性协议/持久化/重连回归已覆盖，不通过改真实状态库制造故障。
- 历史任务较多时记录准入耗时；本片只去除run/artifact预留中的全表任务反序列化，环境启动仍执行SQL计数扫描，没有承诺恒定时延。维护校验异常隔离与结果详情删除竞态已有确定性回归，原机不需手工损坏数据库复现。

## 11. R4原机反馈补修复验（2026-11-04）

本地实现与代码审查不覆盖原机性能测量。保留R3原始报告，修复候选另记candidate_id和ZIP SHA；两端同候选，旧结果可供对照。

1. 专用任务启动一个跨轮后台服务，结束本轮后点击“停止后台任务”：应先出现居中确认，取消时服务继续存活，确认后才停止。任务状态在确认期间变化，应取消并重新核对。当前界面明确说明全部后台命令/服务影响，不提供虚构的进程清单。
2. 环境额度用尽时创建并遇到失败，释放专用环境后启动原任务：云端恢复ready时本机状态应收敛，不持续显示“准备失败”；原失败操作记录仍保留。
3. 无标题任务首次发送有效消息后，列表和详情显示折叠空白的前80字符摘要。后续消息不改已有标题；没有消息的任务仍用ID和真实创建时间，不补造历史标题。
4. 在专用测试任务中创建多文件目录再删除，记录真实耗时。删除独立使用resource_limits.cleanup_timeout_seconds（默认30秒），不再受2秒扫描预算限制；超限保留deleting与原操作，可继续核对。不要靠删库或换command ID恢复。
5. 发送窗口断线后恢复，核对原command ID：已有本机受理回执可以显示，原消息不会自动换ID重复发送，随后编辑的新草稿必须保留；查到受理后旧“核对并重试原请求”提示应消失。观察快照暂忙会在租约期限内重试，其他错误仍显示。

此前未实测的性能指标继续记未测：现有ready_seconds从create返回后计时，不是完整create接受→ready；共享profile仍复用SSH隧道，未具备独立传输TCP/限速保证。不能将本轮代码review改记为8s/1s/500ms/70%等原机门禁通过。模型流式、续传字节核对和二进制交付正确性有本机测试；原机延迟/吞吐与50工具对照仍需独立样本。
