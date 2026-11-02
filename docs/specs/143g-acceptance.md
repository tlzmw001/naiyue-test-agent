# Spec143G：验收、性能与证据

2026-11-01用户确认的任务自动授权、外部网络开关与项目依赖合同见[143p](143p-s042a-task-capabilities.md)；该合同替代此前B逐工具审批计划，本机普通模式审批保留。

状态：2026-11-02。原S00本机已有证据，用户报告目标Linux和Windows三段通过；截图可见范围及新增门禁见[143h](143h-s00-backend-gate.md)。S01/S02/S03/S04/S05/S07.1本机范围以143j–143r为准，最新模型合同/证据见[143r](143r-s05-model-relay.md)；S06及其余切片仍待实施；本轮按143f细化为14个剩余单元、143t/u/v/w补齐接口/来源/回收合同。按143s手动管理任务，取消云端长期记忆；候选侧栏提前到S06.4。不能把确定性协议夹具记成真实供应商/业务验收。切片见[143f](143f-implementation-slices.md)。

## 1. 证据分级

| 证据 | 能证明 | 当前状态 |
| --- | --- | --- |
| 参考项目固定源码 | 特定版本的调用/字段/边界 | 已调研，见研究目录；不证明本机集成 |
| Mac格式54次合成往返/轻量tree探针 | 特定样本内容、进程数、原仓库不变 | 已有研究证据；不是Windows/B性能 |
| S04装配只读分析＋现有A基线 | 现有装配可分离位置、必要保存/观察降级/审批恢复与本机IO行为 | [143i](143i-runner-assembly-spike.md)已完成，80项通过；不是B装配或新增实机门禁通过 |
| 用户Windows中转HTTP/RPC | 用户已测直连服务及目标容器夹具链路 | 用户报告Linux/三段通过；截图核对Linux与外部断开，在线外部字段/恢复原始报告待归档 |
| B确定性单元/集成 | 本方案状态/身份/IO与故障合同 | S00探针及S01–S04/S07.1分片证据见143h–143q；Runner/控制/完整结果与候选浏览器已接，S07.2证据见143z；本机受控交付/真实供应商和公司最终拓扑待后续 |
| Windows＋Linux最终拓扑 | 已测端口/身份/挂载/生命周期，不外推全部宿主网络 | 原S00按143h记录；新增权限/网络/无TTL/模型出站门禁及真实业务待验 |
| 真实模型与测试业务 | 特定版本/场景能执行并产生结果 | 待单独记录，不由全绿测试代替 |

## 2. 必须覆盖的行为矩阵

| 编号 | 正常与失败断言 | 主切片 |
| --- | --- | --- |
| B01 | 两任务workspace/state隔离、非root/caps/no-new-privileges、拒绝宿主/管理入口/其他任务；实际负向访问 | S00/S02 |
| B02 | Windows中转HTTP＋真实proto RPC；容器内寻址、TLS/authority、断线、持续流/并发按所用业务验证 | S00/S11 |
| B03 | 单根run、根子归属、必要保存、停止、后台命令；普通任务工具无审批，显式deny无副作用，任务伪报不能生成所有者授权 | S01/S04 |
| B04 | command重复/摘要冲突/ACK丢失/重启对账；原回执查询先于新命令CAS，项目与回执同事务；未知外部副作用不重放 | S01/S04/S11 |
| B05 | 源变化/未跟踪proto/二进制/mode/link/碰撞/解压超限；固定父目录后替换竞态、FIFO/硬链接/挂载别名、批量Git来源和冻结排除策略、CRLF脚本预检 | S03/S07 |
| B06 | 同input上传0字节、共享只读输入/独立可写副本、分段断线/持久offset/中途崩溃/错误hash/磁盘满 | S03 |
| B07 | 原生各provider/API协议、流式取消/错误、SDK重试归属；key不进入任务；任意URL/跨代拒绝 | S05 |
| B08 | 停容器读Coordinator投影，不开任务DB/建Agent；S01台账/ACK，当前凭证补发旧来源outbox、epoch跨换代保持/seq摘要冲突/缺口、短事务分页/快照衔接；侧栏过程不进入主聊天/记忆，下载/草稿不触发记忆，用户发送并真实读取材料后按本机来源/遗忘规则处理 | S01/S04/S06/S09 |
| B09 | I→O文本/二进制/add/delete/mode正确；原HEAD/index/objects不变；容器销毁后R仍可用 | S07 |
| B10 | capture忙/不稳定拒绝、下载Range/ETag/hash；恶意patch/缺基线/错O拒绝；产物独立下载 | S07/S08 |
| B11 | 导出/分支/apply；变化路径闭合/缺I缓存退路；原HEAD→I未提交改动＋I→O、CRLF/LF仓库转换/不支持filter拒绝；本机冲突/锁/链接/partial；原index不变 | S08 |
| B12 | 加号菜单打开云端任务侧栏，顶部新增/选择已有、详情底部消息；打开/切换不创建容器，续聊复用原任务、草稿/旧回包不串；无模型提交工具/假run或tool_call；观察云端Agent/工具/错误/根子；工具错误与循环退出/unknown分开，停止/补充指令/核对恢复可用；主聊天可继续，桌面/窄屏/CLI、实例隔离与真实空态 | S06.4/S09 |
| B13 | v1/v2并存无A模式、任务固定版本、自动预备失败不切换、drain/schema/引用回收；独立Updater构建/回执登记，Coordinator无socket；重启补登记/旧任务不受影响 | S10 |
| B14 | 备份含state/WAL一致性和目录引用、离线新根恢复；B不读取/改写/回收A数据 | S01/S10 |
| B15 | 正式包/CLI/API无A主链；本机普通功能/本机历史保留；当前文档和锁表与代码一致 | S09/S11 |
| B16 | 任务SQLite换链接/FIFO、恶意schema不触发Coordinator打开；管理历史/其他任务仍可响应；任务数据摄取不越界 | S01/S06/S10 |
| B17 | 非root服务身份创建→任务写0600/0700→停/重建→备份/清理全链；不靠root chown/递归chmod；根凭证/管理FD不进任务 | S00/S02/S04 |
| B18 | 显式timeout=None、后端重启不恢复定时销毁；长任务/未收齐输出/必要保存/unknown/活动保存失败阻止空闲停止；静止后才停、未知占额、并发准入互斥 | S00/S02/S04 |
| B19 | Runner主动控制连接、旧连接/跨代拒绝、ACK丢失对账/反压、令牌续发；Runner kill令容器及后代退出，无回执保持unknown，无stdout约定行判定 | S04 |
| B20 | Coordinator所在网络逐个验证实际启用provider/API的DNS/TLS/认证/代理/SSE首块/空闲/取消；不通过Windows代跑掩盖出站位置变化 | S00/S05 |
| B21 | Runner解释器/venv/父目录不可写、同名模块不劫持；项目pip不改Runner；工具环境/FD无根凭证，同任务令牌仍可读；Kit版本/SHA、CLI、所选Python里的pytest和本地报告实际成功 | S00/S02/S04/S10 |
| B22 | 实际execd发布scope/无token可达性、任务→宿主其他已知服务/管理路由负向探针，先正向证明监听；白名单外挂载/特权请求拒绝，实例白名单不冒充task权限 | S00/S02 |
| B23 | 新建准备不调用模型，用户首条消息发送且输入就绪＋运行持久受理后关闭本机服务，云端继续Agent/必要保存/自主结果捕获；重开对账原任务，提交/结果ACK丢失不重复运行/回填；未完成上传不误报已接管 | S06.2/S07.2/S08/S11 |
| B24 | 侧栏工具失败但Agent运行、完成后补充指令、failed/blocked显式核对、Runner崩溃旧代终止后恢复分别验证；修复仍限原容器/deny/网络，不出现宿主Shell/临时扩权/未知副作用重放 | S04/S06/S09/S11 |
| B25 | 过程查看/状态对账/侧栏补充输入不进入主聊天或记忆；文件下载/未发送草稿不成为工具来源；用户确认发送后模型真实读取并保存才作为本机工具来源；晚到结果不插入运行中的模型输入或重复ToolMessage；云端无memory库/API/整理 | S06.2/S08.1/S09/S11 |
| B26 | Coordinator实际调度空闲释放；额度/unknown计数、结果待捕获与并发新run保护；显式删除逐阶段重启/墓碑/共享引用/路径边界；关联未删除任务阻止父会话删除，不能只禁用UI | S06.2/S06.3/S07.2/S11 |
| B27 | 代码/报告下载到本机结果目录；分析动作只准备material_refs草稿、不覆盖已有输入；用户发送后真实材料读取，无假run/tool_call或人类声明；离线材料可读，缓存缺失明确不可用，代码应用另确认 | S08.1/S08.2/S06.4/S09 |

143s新增B23的正向脱机样本使用不依赖Windows的任务；B02另验Windows中转断开的失败与恢复，不能将前者外推为预发布业务在Windows关机后仍可调用。侧栏/修复不改变B01/B17/B21/B22隔离与凭证验收；这些均为待实现断言，不是已通过证据。

S04.2b对B03/B04/B18/B19的专项入口见143q：根/子停止收束、后台跨轮与输出关闭、原控制ID重试、持久准入取消/ACK丢失、恢复各保存边界及提交后actor装配失败；未知Shell仅核对不重放。强停/换代准入有确定性后端替身测试，真实Linux容器后代退出/旧容器消失仍须最终拓扑证据，不能由这些测试替代。

S05对B07的本机协议与边界入口见143r：四类原生SDK经真实loopback HTTP、兼容接口的固定HTTPS mock上游、错误/重试归属、动态续期、真实WS/两轮工具/历史、原生流stop与上游关闭；Cookie不跨请求、绝对deadline含下游反压、同代profile冻结。上述证据不关闭B20公司出站及真实供应商门禁；不将loopback HTTP称作公网TLS/公司代理通过。

所有故障注入在末尾显式断言主异常、必要事件/保存和副作用；不能让观察隔离吞掉测试检查器的失败。真实外部写接口不用来做重试破坏性实验，优先只读/测试账户与可核对回执。

A权限回归迁移依据见143a第8节；源码中同UID旧提案不得恢复。B同容器Runner/state可被本任务命令影响，不能用上述测试声称具备A中受保护envd的全部防篡改能力。所有者命令权威、后端容器终止事实和任务执行观察分别断言，不把任务上报的退出码当独立审计。宿主不可信摄取测试需检查其他任务响应和实际无越界副作用，而非只检查错误字符串。

S09.1删除A的前置业务门禁是B02、B07/B20真实模型、B12 Web/CLI关键行为和一次结果取回/断线保存；S11再做完整矩阵/规模/组合。S08冻结消费者清单，S09两个提交各自全绿；不能以最终S11尚未执行为由先删除唯一已可用主链。

## 3. 性能场景与目标

下面是工程验收目标，不是已有实测或用户业务总耗时承诺。按最终Windows/OpenSSH/开发机/Docker拓扑，记录机器、版本、链路基线、样本大小、20轮及p50/p95/max；目标变更先写证据/修订Spec，不能通过改统计窗口隐藏失败。

| 场景 | 计时起止/条件 | 初始目标 |
| --- | --- | --- |
| 首次整机安装 | 下载→校验→镜像构建→模板可用 | 分阶段实测，无脱离包大小/带宽的固定秒数 |
| 首次输入 | Windows确认导入→input ready；含扫描/哈希/上传/校验 | 单列各阶段，区分文本项目/多小文件/200MiB二进制；不承诺≤10s |
| 已缓存新任务 | create接受→Runner ready，含容器创建、I解包/全部内容核对、运行时装配；镜像/I/项目所需依赖已预备，无队列等待 | 标准文本项目样本p95≤8s；样本用本仓库按143b规则捕获并冻结input_id/展开字节数/文件数，S03首测前登记。200MiB二进制、多小文件及GB输入另分档报告，不将8s外推全部规模；排队/首次依赖准备另报 |
| 同任务热态环境开销 | 请求准入→可进入模型/工具执行，排除真实模型和业务调用 | p95≤1s；20轮持续复用原容器/workspace，不重复导入/建图依赖安装 |
| UI控制通道 | 200MiB上传/下载期间，对照同链路无负载状态/停止请求；记录真实SSH TCP连接及ControlMaster使用 | p95额外延迟≤500ms；独立传输TCP连接或显式限速，不以多个逻辑channel冒充隔离 |
| 二进制吞吐 | 同链路、同大小、同机器磁盘条件，单次完整流与带断点流对照 | 完整流中位吞吐≥同链路raw SSH流基线的70%；不足就归因，不预判HTTP获胜 |
| 断线恢复 | 中断后恢复同transfer | 已确认段不重传，最多重传一个未确认段；续传结果hash完全相同 |
| 小差异结果 | 大目录中1KiB文本改动；8MiB随机文件4字节修改 | 记录capture/patch/download/verify，不把完整输出目录当默认下载；patch与结果一致 |
| 并发 | 默认2个任务同时运行，各执行文件与受控HTTP/RPC | 内容/事件/令牌不串；限额拒绝/排队可见；不声称单任务延迟不变 |
| 工具密集任务 | 确定性模型替身固定50次read/grep/edit/bash序列，A/B同输入、同调用及输出预算，均热态 | 记录端到端p50/p95及逐工具/调度耗时、内容一致；量化跨Windows往返减少的收益，不伪造必然倍率 |
| 模型代理增量 | 同一原生协议fixture和受控流节奏，Coordinator直连fixture与Runner经代理对照；真实provider另测 | 记录请求至首块/首token的额外p50/p95、块顺序/取消，协议块不冒称token；SDK重试次数相同，不靠缓存正文提高成绩 |
| 小差异本机交付 | 固定大I仅少量变化；分别测命中I缓存、仅归档缓存、无缓存，以及apply/branch/export | 报下载字节、解压读取/落盘字节、hash/Git调用和墙钟；apply避免全目录重复物化，完整export所需字节不隐去 |

网络基线使用无业务凭证的受控文件夹具，不输出私网服务地址。内存记录进程峰值及配置缓冲，证明大文件不会被完整读入JSON/内存；不要只看平均RSS推断没有瞬时峰值。

input_install单列extract/hash/commit，Runner ready与project_prepare完成分别上报；记录压缩/展开大小、文件数/分布、CPU/磁盘和模板digest。S00的约1–2秒创建/身份检查没有输入/Agent/模型，不计入上述新任务成绩。读取缓存命中不等于输入安装零成本，首版不以COW为前提。

不能把B热态目标与A新建冷准备直接比。公司A三轮9.953/16.875/11.297s只作为既有冷态观测；若做对照，应分别比较同会话热态、新任务缓存命中、首次输入和整机首次部署。

## 4. 标准自动化与构建命令

本节命令适用于实施后的代码，不表示本轮执行。使用项目既有依赖环境与Python3.12，不在验证中顺便升级依赖。native工具先按现有CI准备并将输出bin目录加入PATH，禁止修改测试来绕过真实工具缺失。

```sh
python -m scripts.native_assets --target linux-x86_64 --output build/test-assets
python -m scripts.ci_shards --index 0 --count 2 --output build/cloud-b/backend-0.xml
python -m scripts.ci_shards --index 1 --count 2 --output build/cloud-b/backend-1.xml
npm --prefix frontend test -- --run
npm --prefix frontend run build
python -m pip check
git diff --check
```

两个后端分片都必须完成并合并数量/失败信息；不能只报告分片0通过。具体项目输出目录需先创建，构建输出必须新建，不能把旧文件误算新产物。当前标准发行构建入口继续沿用：

```sh
python -c 'from pathlib import Path; from scripts.release_artifacts import stage_frontend; assets=Path("build/frontend-assets"); assets.mkdir(parents=True, exist_ok=False); stage_frontend(Path.cwd(), assets)'
python -m scripts.build_release --target linux-x86_64 --frontend-assets build/frontend-assets --output build/cloud-b/dist-linux
python -m twine check build/cloud-b/dist-linux/*
python -m scripts.verify_wheel_install --wheel-dir build/cloud-b/dist-linux --output build/cloud-b/install-smoke-linux.json
```

frontend-assets须来自当次标准前端资产打包步骤（上面的stage_frontend与现有CI相同），不能用空目录替代；若路径已存在应选择新的证据目录，不能将旧资产混入当次构建。Windows与现有支持平台同源构建/安装仍是正式发布门禁，不能只做Linux就宣称四平台通过。新增Runner镜像必须实际启动并核验source SHA/包版本、模板依赖、UID/能力和任务入口。

纯文档阶段只跑链接/结构/差异检查；不为没有产品修改运行全量或声称应用构建通过。

## 5. 实机探针与证据产物

拟新增真实探针分工：`verify_cloud_backend.py`（S00）、`verify_cloud_transfer.py`（输入/结果流）、`verify_cloud_lifecycle.py`（创建/恢复/故障）、`verify_cloud_workflow.py`（HTTP/RPC完整任务）、`profile_cloud_preparation.py`（分阶段性能）。先搜索当前探针可复用算法，禁止复制一套旧sandbox_probe后继续维护双份同职责代码。

每次证据目录包含：verification.json、源commit/dirty摘要、发行/镜像身份、平台与配置摘要（脱敏）、完整命令/exitcode/测试输出、阶段样本、失败分类。网页保留实际视图/响应和对应task/run/commandID，不用只有脚本成功替代Web完整链路。

真实业务最小闭环：导入含proto和一条测试的项目→Runner生成/修改测试→经中转执行HTTP与RPC→分析实际响应→固定结果→Windows审查/导出或应用→本地验证最终文件/产物；记录一次必要失败/重连，不只验证hello/pwd。测试质量与断言合理性另按固定样本评估，不因代码能跑就宣布自动化测试业务成熟。

## 6. 最终验收签出

逐项给出：已实现/已接线/验证命令/实际结果/限制/遗留/唯一下一步。B未通过的公司门禁不能被Mac探针或单元测试覆盖。测试/构建失败、A主链残留、关键消费者未迁移、未授权数据副作用均不能以“主体完成”关闭。

初稿时只有文档校验；S00证据在143h及build/spec143-s00-20261101/。S01独立控制底座的78项专项/8项存储架构、全量6274后端/568前端、构建与Mac源外安装证据在143j第13节及build/spec143-s01-20261101/verification.json。

S02实现及本机Linux ARM64实证见[143k第8节](143k-s02-lifecycle.md#8-实际接线与验证2026-11-01)与build/spec143-s02-20261101/verification.json：34项新增、cloud全量＋架构161、后端6308/前端568，三目标标准wheel与Mac源外安装；实际两容器/容量/0600和0700持久权限、真实POST ACK丢失后重开接管、无TTL与服务重启、网络正负探针及无socket，最终模板实际含Kit并生成pytest报告。宿主防火墙漏返回规则的失败及修正后原ID对账证据分别保留；公司目标机需使用包含ESTABLISHED/RELATED的受控白名单复验。

S03.1证据见[143l第8节](143l-s031-input-import.md#8-实际实现与验证2026-11-01)及build/spec143-s031-20261101/verification.json：47新增、cloud＋架构208、全量6355后端/568前端、四目标候选/Twine及Mac源外安装；真实最终源码镜像验证CLI捕获/上传/安全提交/正式任务创建、零字节复用、输入安装/pytest及修改后重建。输入索引/活跃任务经Coordinator进程重开保持；四项故障窗口回归修正，自动安全复核工具中止的限制单列。

S03.2及S04–S11完整来源/分段续传、Runner/模型/真实业务、Windows原机与性能统计尚未完成。S03.1的backend_ready/input_ready不表示已运行Agent，不能计入Runner ready目标，不预填用户掌握程度。
