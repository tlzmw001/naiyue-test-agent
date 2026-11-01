# Spec141D：现有候选包的首次准备分段实验

本片先采集优化依据，不实施容器池，不改变生产准备顺序、权限、恢复或传输合同。
用户已确认继续减少重复操作、并行准备及按证据减少重复输入，目标是在原机判断完整准备10秒预算。
本片交付独立脚本，直接使用当前Windows候选安装及envd镜像，无须重装或重建镜像。

## 1. 当前主链与测量边界

正常：LF修正验收脚本的fixture生成671文件 → 临时ServiceApplication注册独立项目 →
sessions.create_sandbox → InputService.prepare → DockerCLI.preflight → DockerEnvironment.create →
handshake → InputService.install（probe、begin/chunk/cache、git_input提交及确认）→ ready。
create_sandbox及status合计与此前24～28秒口径一致；fixture生成、应用启动、验证读取和清理单独放在计时之外。
每轮使用新会话/新容器，只保留一个当轮沙箱；操作系统及镜像缓存不清空，不将其称为磁盘冷缓存测试。

失败：保留原主异常；终止继续采样，清理临时项目注册的精确资源；清理失败单独记录并使测量失败。
取消时先停止并排空本临时项目仍在进行的准备任务，再枚举资源，防止预检尚未登记环境时漏清理；
收尾由await_settled托管。中止报告status=cancelled、退出130；失败轮只有attempt_seconds，ready_seconds及预算为null。
记录失败后的二次诊断使用既有safe_log，不以日志故障覆盖业务原异常。
不扫描/清理其他Docker容器，不使用用户真实data_dir、模型凭证或项目源文件。

## 2. 观测方法

独立进程内临时包装已安装方法，finally恢复，不修改site-packages：

- 一级阶段：InputService.prepare、DockerCLI.preflight、DockerEnvironment.create、handshake、InputService.install。
- 嵌套明细：InputService._transfer、SandboxTransport._open、GitRepository.run、DockerCLI._run、SandboxTransport._request。
- Git只保存命令类别/次数/耗时，不保存参数、输出、路径；Docker只保存固定命令类别，不保存SSH别名/argv/脚本。
- RPC只保存操作枚举/次数/耗时及input_chunk编码字符数，不保存正文、身份、指令、环境、凭证或返回内容。
- 安装提交至确认的耗时包含远端运行、排队、轮询及往返，不谎称可直接测出远端checkout单独CPU时间。
- 一级阶段用于解释总耗时；Git/Docker/RPC/transfer/connection是嵌套明细，不能再累加到总时间。

记录器仅保护短内存操作，不跨IO，不使用生产锁。每轮结束后汇总次数、总耗时和最大值；
少量轮次只报告中位数/范围，不报告有代表性的p95。观测器故障保留业务原结果/异常，标记测量无效。

## 3. CLI与产物

scripts/profile_sandbox_preparation.py可作为单文件分发；--fixture-script指向已通过的LF验收脚本，
复用其fixture，不复制第三套生成逻辑。保留原CLI的context/image/ssh-host/ssh-executable输入。
--runs默认3，范围1～5；--output必须不存在，拒绝-O。三轮仅表示重复新会话测量。
报告status=measured表示测量及验证完成，不代表10秒预算通过；每轮单列within_10_seconds。
失败时输出已有阶段及error_type，不输出异常正文；原始报告不含机器/用户路径及SSH目标。
记录控制端版本、被观测源码哈希、fixture脚本哈希，区分同版本不同候选。

只执行现有prepare、hello和一条固定AGENTS.md读取；provider_requests=0，无真实模型调用。
验证读取严格检查LF内容，避免空环境/错误快照产生成功计时。输入bundle尺寸通过已保存输入记录获取。
候选附件单独追加当前Release，不替换已有ZIP、wheel、镜像或标签；匿名下载回读核对哈希。

## 4. 测试与验收

先补失败测试：同步/异步计时、恢复被包装方法、错误与诊断不泄露参数/正文、主异常保留、
一级与嵌套计时分离、有界事件、观测器故障使测量无效、报告失败仍保存及拒绝覆盖既有报告。
应用替身测试验证三轮独立新建、每轮清理、ready和固定内容检查、失败后停止后续轮次、清理错误保留主异常。
不修改conftest或全局测试基础设施；夹具只在本测试中注入。
再用私有本机Docker上下文跑一次完整实际流程，核对阶段/调用计数、bundle、读取及自身资源清理。
本机计时不替代Windows；最终由用户运行同一脚本并反馈JSON，再选首个优化点。

范围文件：本Spec、独立脚本、对应测试、lock-hierarchy及handoff；生产代码和配置保持现状。

## 5. 本机交付验证（2026-10-30）

首批8项按模块缺失红灯后实现；复核发现取消早于资源登记、失败轮误作快速就绪、二次日志覆盖主异常，
均增加失败回归后修正。最终相关93项通过（16.29s），标准Mac ARM64构建退出0、Twine PASSED，
应用源码hash仍为d19e6ce6c1baeda013db04ee6bcf2b50ae3d7699b1a6f105b597bde2ef2f879c。
独立安全/架构复核已关闭发现项；重复取消的事件探针确认排空自己的资源且不停止外部准备任务。

本机源码方式最初直接执行.py时因未安装项目包而在setup报ModuleNotFoundError，没有创建Docker资源；
随后模块方式完成一轮。正式验证另建venv安装标准候选wheel，从源码外目录直接执行单文件，
三轮完整ready为2.567575/2.031356/1.884348秒，每轮671文件/4530744字节bundle、29次Git、严格内容验证，
三个自建资源均确认销毁，前后Docker容器/卷0，默认context保持default，专用VM恢复Stopped。
依赖来自已有本机依赖目录，项目代码确认从新venv安装的wheel加载，不靠仓库源路径接线。
这些是本机ARM64 Docker结果，不代表原生Windows或公司SSH已达10秒。

独立分发脚本spec141d-preparation-profile-20261030.py的SHA256：
bb2fe0795b34574e3ebb78aed52027546d8bb66e4c4a725d9ab52a07226d6d61。
验证与安装版三轮原报告位于build/spec141c-20261030/；脚本和SHA追加既有v0.5.6候选附件，
不替换既有15个附件、Agent/镜像或标签。下一步只等待公司同脚本分段数据，按证据选择首个优化点。

## 6. Windows原机分段证据（2026-10-30，用户截图）

退出0，三轮清理attempted=confirmed=3/errors=[]；bundle均4530744字节。

| 轮次 | 完整准备 | snapshot | preflight | container_create | handshake | input_install |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 24.891s | 14.063s | 1.625s | 3.922s | 2.266s | 2.922s |
| 2 | 24.516s | 14.125s | 1.578s | 3.797s | 2.156s | 2.765s |
| 3 | 24.062s | 13.516s | 1.797s | 3.640s | 2.157s | 2.906s |

均值24.490s，本机快照13.901s（约56.8%）；远端预检/创建/握手合计7.646s，输入安装2.864s。
保持各段耗时不变且没有并行争用的理想反事实：只并行快照与远端准备，三轮约17.078/16.985/16.468s。
这不是优化后实测；也表明仅空容器池不减少本机快照成本，不能据此达到完整10秒。
后续截图显示首轮29次Git合计11.094s，占14.063s快照约78.9%；其余快照工作约2.969s。
rev-parse七次2.423s、config四次1.344s；除fetch/bundle的27次调用平均344.963ms。
这些是调用端到端时间，不能全部归因为Git启动或杀毒软件；本轮不改产品代码。
截图摘录和派生计算：build/spec141c-20261030/company-preparation-profile-observed.json。

## 7. Windows本机Git固定成本对照（追加实验）

目的：区分原生Git进程、HostProcessRunner、每次run_async_sync创建/关闭asyncio循环的成本。
源码确认Git路径使用complete EOF策略，没有正常完成后固定300ms等待；同步快照每次Git调用都新建asyncio循环。
这仅构成可验证假设，不预设Windows循环初始化就是根因。

独立scripts/profile_git_process_cost.py只运行固定git --version，在临时空目录执行；不创建Git仓库、
不访问用户项目、不读写运行配置、不启动Docker、不连接SSH、不调用模型。使用已安装候选包及标准库。
通过一次真实GitRepository.run捕获内存中的ProcessRequest，原生subprocess和Host运行器严格复用
该argv、过滤环境、cwd、时限和进程创建标志；报告不保存这些参数或命令输出，只保存版本/源码指纹和数值。

六组依次为：空循环每次创建、原生相同Git请求、Host每次创建循环、Host复用循环、
完整GitRepository当前入口、完整GitRepository临时复用循环。后者仅在实验进程patch该模块的
run_async_sync，finally恢复；不修改已安装文件或全局asyncio策略。
所有Git输出必须与setup的完整原始字节相等，Host结果还须确认子进程/管道/消费者完整收尾。
默认每组5次，--runs范围1～10；--timeout-seconds默认10且有界；记录每次/min/median/max及含循环
创建/关闭的整组耗时，少量样本不声称p95。setup单列，不计入各组。不将measured当成性能通过。

--output使用新文件并拒绝覆盖，拒绝-O；异常报告只记类型、失败阶段和已有采样，中止返回130。
TDD覆盖同请求/完整收尾/输出断言、循环复用与恢复、原异常、报告隐私与不覆盖、取消。
先验证脚本，再由原生Windows实测决定生产优化；Mac结果只证明实验接线。
本追加范围为本Spec、新脚本及测试、handoff；无新锁，无需修改锁层级或发行清单。

追加实验的18项测试先红后绿，连同原分段实验/进程生命周期共46项通过（2.12s）；
标准Mac ARM64构建退出0、Twine PASSED，application hash仍为d19e6ce6c1baeda013db04ee6bcf2b50ae3d7699b1a6f105b597bde2ef2f879c。
从源码外目录直接使用已安装候选wheel运行单文件，六组各5次均严格验证，status=measured、0模型请求；
Mac完整Git当前/共享组中位数10.524/9.777ms，只证明实验接线，不说明Windows根因。
安全和架构复核无阻断；失败中断时共享组的partial group_seconds可能不含随后关闭耗时，
此时整体status已标失败，不能拿失败组做完整成本对照。证据build/spec141c-20261030/git-process-cost-delivery/。
独立脚本SHA256为81871b8ff047fde90ce0bcabfe980f249e6d6b2495947acaf73ff4add96e9cde；
只追加脚本/SHA至既有Release，不替换原有17附件或标签。下一步由Windows运行六组对照。

## 8. Windows固定成本实验结论（2026-10-30，用户截图）

退出0、status=measured，每组5次；原始JSON未提供，以下为可见表格摘录。

| 组 | median_ms | max_ms |
| --- | --- | --- |
| empty_loop_per_call | 0.0 | 0.0 |
| native_same_git_request | 312.0 | 313.0 |
| host_runner_per_call | 312.0 | 313.0 |
| host_runner_shared_loop | 328.0 | 375.0 |
| git_repository_per_call | 328.0 | 344.0 |
| git_repository_shared_loop | 344.0 | 485.0 |

结论：排除“反复创建事件循环是目前主要瓶颈”的假设。原生相同Git请求已耗时312ms，
共享循环未带来改善；0ms是当前采样/时钟分辨率下的显示值，不表示事件循环绝无成本。
不能据此进一步区分Git启动、系统加载、安全软件的各自贡献，也不把5个样本当成p95证据。

下一步优化以降低Git进程次数和缩短关键依赖链为主，并与远端创建并行；保留前后来源变化、
文件身份、内容/链接/过滤器及对象验证。已在本机当前仓库用真实GitRepository只读对照确认：
初始rev-parse的目录/格式/HEAD三次查询可合为一次且输出逐字节相等；尚未实现生产迁移，
无HEAD/不支持格式/非法布局等分支仍需对应回归。相同快照内已确认的不可变tree OID可直接传递，
不能删除必要的源仓库末次核对。配置合并、其余Git重排仍需逐条验证语义，不承诺固定减少数。

按312ms估算每减少10次调用约节省3.12s，属于线性估算，不是优化实测。
远端准备7.646s加安装2.864s仍约10.510s，即使快照无成本且完全重叠，也不能仅靠本机优化达到10秒。
当前远端create_remote持久化/核验容器后仍另起SSH执行container start；下一片须评估把此动作并入
后续常驻attach建立阶段，减少一次认证，同时保留先保存身份、权限核验及启动状态不明的恢复合同。
首次准备目标预算可设本地/远端各6s、安装3s、其他0.5s，并行后约9.5s；这只是设计预算，
需要公司同671文件/4.53MB夹具完整复验，不能把阶段预算或预热命中当成首次达标。
本轮只记录实验、只读核验和优化依据，不改产品、配置、包、镜像或Release。
