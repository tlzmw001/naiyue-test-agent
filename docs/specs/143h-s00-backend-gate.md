# Spec143H：S00 本机原型与 Windows 复验

日期：2026-11-01。本稿记录S00时点：先在本机跑通，再由Windows对新开发机复验，本机无法连接该开发机；原实验不包含S01管理服务或A迁移。后续用户已批准S01，其实现/验收单独见[143j第13节](143j-s01-control-foundation.md#13-实际接线验证与限制2026-11-01)。

当前补充：用户报告目标Linux及Windows在线/断开/恢复三段通过；已核对所发截图，范围见第7节。第1–6节保留原探针与本机执行时点，第8节是审阅后新增门禁，不能把旧报告追认为覆盖这些新要求，也不能把S01离线通过当作新增实机通过。

## 1. 实验目标与环境

本机使用独立Colima Linux ARM64测试机 `nyt-spec143-s00`，不修改现有Docker默认context、不启动旧测试机、不操作原A容器/卷/镜像。OpenSandbox server/SDK候选固定公开PyPI 1.2.0，下载核对PyPI SHA；实际execd、基础/任务镜像与依赖另记录digest/版本。研究源码a6c0cfc不自动当作发行包源码身份。

创建专属bridge网络、可信控制面容器、非root任务模板及测试目录。控制面需要Docker socket管理容器；任务容器不得挂该socket。控制面与任务的权限要求不同，不能用控制面的root/host网络掩盖任务放宽。

正式探针拟入口为 `python -m scripts.verify_cloud_backend`，实现置于 `scripts/cloud_probe/`；仅测试工具，不接入产品启动/正式wheel。已有A探针通过envd/SSH工作，不能复用其执行链来证明B；可复用证据格式及安全断言。探针应从SDK实际创建/查询/续租/暂停恢复/终止任务，以Docker只读inspect核对实际生效策略。

## 2. 正常与失败链

正常：读取显式参数/私有环境凭证 → SDK创建两个带本次run标识的任务 → inspect非root/网络/caps/挂载 → 任务内Python身份和文件读写 → 独立workspace/state → 续租/暂停恢复 → 销毁后原目录重建 → 明确清理所属容器 → 保存报告。

失败：任一步创建或调用失败，保留阶段/异常类型/已知资源ID；仅清理已确认属于本次run的容器，清理错误单独记录并保留主异常。创建ACK未知时不遍历删除所有容器，报告需按run标识对账。禁止报告文件已存在时先创建资源再失败。

配置中的地址、端口、镜像、路径和超时均通过参数/文件提供。真实模型/预发布凭证不进入本机原型；网络测试使用专属HTTP和真实gRPC夹具。探针报告只保存状态、哈希、版本与阶段，不保存API key、完整环境、请求/响应业务正文。

## 3. 安全与状态检查

| 门禁 | 实际检查 |
| --- | --- |
| 身份 | 任务进程uid/gid非root且与模板一致，NoNewPrivs=1，Eff/Prm/Inh/Amb/Bnd能力为0 |
| 容器策略 | 非privileged、无cap_add、cap_drop含ALL、无host PID/IPC、seccomp未关闭、资源限额生效 |
| 网络 | 任务在显式命名bridge，网络ICC=false；从A向B监听端口的连接必须失败，B本机正向连接先成功 |
| 文件 | 只挂本任务workspace/state及选定只读输入；不能读写其他任务目录、宿主秘密夹具、Docker socket |
| 凭证 | 控制面key不放任务env/argv/mount；任务内只观察是否出现秘密标记，不回传环境原值 |
| 生命周期 | SDK查询/续租的到期时间有实际变化，pause/resume与连接重建有效，kill确认删除后可从原持久目录重建 |
| 清理 | 验证所属资源身份再删除；清理失败不可把整个报告标通过；测试数据保留在专属证据目录 |

若非root模板启动、drop ALL或命名bridge不受候选产物支持，先记录真实错误和单一假设，按项目协议验证，不放宽策略制造成功。连续三次失败停止该问题并报告证据；替换后端/改变隔离合同须先修订Spec讨论。

## 4. 本机网络夹具与公司证据

本机夹具应覆盖HTTP及真实proto定义的gRPC，经过“任务bridge入口→受控转发→仅loopback服务”的中转，验证正常调用、断开与恢复。夹具是Windows反向SSH已落到Linux后的等效网络段；**不能证明Windows睡眠、实际SSH/防火墙、真实预发布RPC或公司允许中转**。

本机通过后准备可复制的探针/配置模板/版本与hash清单/Windows步骤。目标Linux通过Windows SSH执行同一后端检查；Windows保留反向隧道，最终从目标任务容器访问用户实际HTTP/RPC。公司部分尚未测试时，报告独立标pending，不关闭整个S00。

## 5. 验证与交付

持久探针组件采用失败测试→实现→回归；临时后端探索单独记为实验，不称作TDD。测试覆盖策略拒绝、资源归属、主异常与清理失败、报告无秘密，以及实际正常/故障路径。专项命令拟为 `.venv/bin/python -m pytest tests/cloud/test_backend_probe.py -q`。

真实运行使用隔离工具依赖环境或专属控制面镜像，不将OpenSandbox/测试夹具依赖加入当前产品pyproject或升级其锁定依赖。报告存入 `build/spec143-s00-20261101/`，记录平台、实际包/镜像身份、步骤、退出码和限制；本机实验结束停止本次测试机，保留证据与必要复验产物，不删除原数据。

本机S00原型、Windows复验包、公司S00验收分别报告。不得用本机ARM64模拟成功宣称公司AMD64/Windows实际通过，不提交/发布/远程部署。

## 6. 本机实际结果（2026-11-01）

本机完整后端与中转门禁已执行，报告在 `build/spec143-s00-20261101/`，不是B产品完成声明。实际版本：SDK/server1.2.0、grpcio1.85.1、protobuf7.36.2，Docker29.5.2/kernel6.8.0-117-generic/ARM64。wheel公开来源SHA与五个镜像身份分别记录在packages/sources.json及images.json；execd1.1.0的多架构index为`sha256:6cf7dba2f21f0b536e100563d841ac58a9f31c2b0a081b7ac76796a24d6f47e2`，复验配置固定此digest。

- `backend-gate-3.json`：非root/五组cap为0、只读输入、独立文件、peer网络/管理端口拒绝、HTTP＋proto-gRPC unary/bidi的成功/断开/同端点恢复、续租、pause/resume、kill后原目录重建均通过；剩余任务容器空、清理错误空。
- `backend-ssh-online.json`、`backend-ssh-offline-2.json`、`backend-ssh-reconnected.json`：使用真实Mac OpenSSH反向转发到专用Linux，再从任务容器调用Mac loopback夹具；三协议结果true→false→true，endpoint摘要一致。不是Windows/真实预发布证据。
- 初次execd命令实验拒绝SDK argv字段；验证command文本接口成功，固定参数均shlex转义。未更改上游、升级execd或放宽隔离。
- 首次SSH断开实验报告失败，因Colima配置ControlPersist保持转发；只取消本次两个转发后offline正确。复验命令显式关闭多路复用；保留失败报告`backend-ssh-offline.json`，不把前台退出当实际断线。
- 41个新探针测试和原探针专项合计83项通过；核心策略、资源归属/异常、准备文件、字节转发/重绑入口分别记录过预期红灯后转绿。后端临时探索单独作为实验，不冒称全部探针路径由TDD证明。
- 两次工具镜像构建和非root模板构建退出0；复验工具镜像按已观察依赖约束重建。项目/控制面pip check与新脚本compileall通过。未改产品源码/依赖，未运行产品全量或前端构建，不作发布准备完成声明。

具体运行命令与Windows步骤见[复验手册](../cloud-s00-local-and-windows.md)。本机容器创建与身份检查约1.17–1.39秒，已缓存模板，无Agent、项目输入或真实模型；不是143g新任务SLO验收。公司S00仍pending，唯一下一步为Windows连接新开发机复验，不自动开始S01。

## 7. 目标机用户报告与截图核对（2026-11-01）

用户报告Linux门禁与Windows三段均通过。截图显示Ubuntu22.04.5/x86_64/kernel5.15.0-170、Docker29.9.2、SDK/server1.2.0、grpcio1.85.1、protobuf7.36.2；可见任务镜像标识一致。未取得原始JSON或开发机AI的代理修正diff，不能证明代码/配置与原复验包完全相同。

| 报告 | 截图可确认 | 仍需归档的部分 |
| --- | --- | --- |
| backend-local.json | completed/local_backend_passed；三个创建/身份样本2.393/1.742/1.719秒，UID/GID1000、NoNewPrivs=1、五组cap=0、2GiB/256PID；文件/peer与指定管理端口/模拟HTTP与gRPC三段通过，续租/pause-resume/重建为true，remaining/cleanup为空 | 原始报告/镜像和包hash、实际配置摘要及源码差异 |
| windows-online.json | 顶部completed/local_backend_passed及可见身份/版本通过 | 底部external_relay与endpoint摘要未入图 |
| windows-offline.json | expected=offline，外部HTTP/unary/bidi全false，remaining/cleanup为空；这是预期通过 | 原始报告及与另外两段的同端点摘要核对 |
| windows-reconnected.json | 用户报告恢复通过 | 本组未展示截图/原始JSON，不能声称已由本机AI核对三段一致 |

`company_acceptance=pending`是现有脚本初始化后不自动更新的字段；`simulated_relay.scope`中的pending是模拟子测试静态说明，真实SSH结论另看external_relay。两者不等于运行失败。`provider_requests=0`意味着本探针未测真实模型；夹具不能替代真实预发布proto/TLS/并发或Windows休眠验收。归档现有四份报告即可补齐证据，不无理由重跑同一门禁。

原实验收尾按所属资源可逆停用：确认无本次探针运行后正常停专用控制容器、关闭专属Windows隧道/夹具，保留镜像/证据/专用空网至核对完成；不操作既有容器或撤销共享代理。新增验证重新启用本次专用资源前核对目标/范围，不重启全机Docker。

## 8. S00补充门禁与下一步（未执行）

先归档原报告、镜像/包版本、脱敏代理修正和资源清单，再按下表差集验证。实际地址/端口/账号沿目标机配置，不把公司凭证带回仓库。此表是执行范围，不是远程部署/防火墙修改授权。

| 顺序 | 最小操作/正向与负向验证 | 通过证据与失败处理 |
| --- | --- | --- |
| 1 | 只读核对服务UID/GID、相关数值账号及父目录权限；用专用非root账号在S00根创建任务目录，任务写0600文件/0700子目录，停止/重建/隔离备份与清理仅本次数据 | 记录每阶段执行身份/所有者/模式/结果；不以root chown或chmod全树制造通过。正式账号映射见143c第3节 |
| 2 | 检查实际发布端口绑定；从Windows非SSH入口和另一任务测试未授权execd/管理端口不可达，宿主受控正向请求证明端点存在；检查任务入口无owner路由与宿主账号可信范围 | 记录scope/可达布尔值，不输出认证头。loopback不提供本机账号鉴权；无token访问若超出受信范围，先隔离入口/修订并验证，不能默认为有鉴权 |
| 3 | 对已知宿主其他监听服务做正向存在＋任务负向连接，验证实际宿主地址/转发路径；用管理key请求白名单外挂载、策略外特权行为应拒绝且无容器副作用 | 不扫描无关内网、不更改全局Docker规则。若需防火墙/监听配置修改，先给目标配置和影响范围取得对应部署授权 |
| 4 | 在Coordinator最终等价网络/代理环境逐个调用实际启用的provider/API；验证DNS/TLS/认证、最小原生请求、流首块/间隔/取消，真实key仅从机器现有安全配置读取 | 按provider/API记录状态/请求ID/超时阶段与是否依赖Windows，无key/正文/URL。只测TCP或Windows直连不通过B20；不能连则明确外部前提受阻 |
| 5 | 同固定SDK/server显式timeout=None创建专属任务，查expires_at为空；只重启本次专用OpenSandbox控制服务后重查无到期调度，任务/目录保持 | 保留创建请求类型/脱敏响应/重启前后容器事实；不改后端默认TTL、不重启Docker。原续租结果仍作为能力记录，不必再重复测 |
| 6 | 镜像中放任务不可写的独立Runner依赖路径，任务写同名模块/操作项目venv不改变启动导入；用受控入口程序替身验证退出/kill后容器及后台写入者收束 | 替身只证明后端entrypoint关系；实际Runner仍须S04复验。无根环境/控制FD，项目依赖可写与Runner路径不可写分别测试；不放宽cap/seccomp |

S04装配只读spike可先于上述新增实机项执行，现已完成，见[143i](143i-runner-assembly-spike.md)：当前装配/执行/保存真实链、Runner/Coordinator/本机依赖表与80项既有回归通过。只读分析不需要连接公司机器，没有新建工厂或实现Runner；后续产品代码从S01按已确认计划开始。不能再把“原三段通过”“新增项未验”“装配分析完成”合成同一条未完成状态。

切片前提按实际消费者落实：S01的临时根/真实SQLite/ASGI合同不使用Docker或模型，可按[143j](143j-s01-control-foundation.md)先实施；第1/2/3/5项在S02接真实生命周期前通过，第6项在S04实际Runner前通过，第4项在S05真实模型接入前通过。共享的权限/网络失败须先修订部署边界，不以离线S01通过替代实机门禁，也不要求重跑原Windows三段。

临时SQLite路径探针已在Mac SQLite3.51.0复现mode=ro跟随相对链接和FIFO打开超过1.5秒（busy_timeout=0.1不约束open）；只使用合成库并回收子进程。脚本及结果在`build/spec143-review-20261101/sqlite-path-probe.py/json`，不是B漏洞或公司实机证明。Coordinator不读任务DB的新设计以B16验收，不靠调SQLite超时修补这条路径。

当前复验ZIP保持原固定产物，不因Spec改动重打或覆盖；包内旧文档描述原探针范围。开发机AI接续时以本节/143a权限表为新增任务单，保留原包SHA与源码差异，不把更新文档的源码hash变化误算作已部署候选通过。
