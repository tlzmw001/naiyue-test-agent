# Spec143AG：S10.1 显式更新、SSH触发与版本预检

2026-11-03用户批准实现10.1，并要求完成后讲解10.2方案。本片承接143C/143F及已确认讨论：Windows可经已有SSH别名调用Linux固定更新入口，Linux执行并保存可查询状态，断线不拥有更新进程。无真实配置修改、用户资源清理、远程部署、发布或提交；初始dirty与Spec142保护见build/spec143-s101-20261103。

## 1. 现状与职责

- CLI cloud.release.prepare → TemplateBuildSettings → prepare_template：只读取本地产物、构建和完整预检，私有独占输出TemplateReceipt；尚无下载、发行登记/active或独立更新进程。
- CloudInputs.create_task从CloudSettings.templates取回执并把template_snapshot/digest/runner_release_id固定入任务；生命周期重启沿固定快照，不随配置变化。
- CloudRuntime拥有registry及CT1任务锁；数据库schema严格核对。新发行仓储复用registry.meta和commands短事务，不另建数据库schema或绕过仓储SQL边界。
- Coordinator无Docker socket。已鉴权owner API接收构建回执；task API不能登记发行。Updater拥有部署授权的构建权限，只执行固定构建/备份程序，不接收任务命令。

## 2. 本片接口与数据

新增严格CloudRelease：release_id、agent_version、source_commit、coordinator_protocol/runner_protocol/state_schema/min_connector_protocol、必需能力、平台wheel清单（文件名/URL/字节/SHA）、Kit产物、测试依赖清单、模板基础镜像与指纹。发行ID限安全标识；来源仅固定信任的HTTPS origin/路径，禁止凭证、片段、任意重定向或任务提供URL。清单SHA由运维配置固定的发行索引提供；同ID不同摘要拒绝。支持显式本地私有发行目录以供离线验收，不降低文件身份检查。

UpdaterSettings独立私有JSON：工作目录、可信发行索引/本地目录、构建运维参数（复用TemplateOperatorSettings；具体产物输入由发行生成TemplateBuildSettings），CloudConnection配置路径、维护备份的任务数据根、下载/构建预算；凭证不写journal或SSH argv。部署配置不是聊天授权。目标发行输入决定wheel/Kit/lock/base输入，不并行维护另一构建链。

CLI：
- `nyt cloud release check RELEASE --settings FILE`：读取可信清单及当前实例，输出协议/状态/能力检查、当前active、需维护项，无构建/激活。
- `... update RELEASE --command-id UUID --settings FILE`：持久化请求并启动脱离终端的固定worker；返回同ID状态；重试不创建第二个producer。
- `... status UUID --settings FILE`：读取原命令；`... resume UUID`显式重启未持锁的中断worker，重用已校验文件/镜像/回执。
- `... activate RELEASE --command-id UUID --expected-revision N --settings FILE`：显式选择ready版本给新任务，CAS防止过期覆盖。
- `... maintenance {enter,leave,status} --command-id UUID --settings FILE`：持久维护准入，活动运行/必要保存/传输/未知操作有可见阻碍；程序安装/重启沿原部署方式，Updater状态独立于Coordinator。
- `... upgrade TASK RELEASE --command-id UUID --settings FILE`：仅同state_schema、物理环境已停止且无未决操作时预留；独立受限容器备份workspace/state后固定新模板，再通过已有start恢复新generation。运行中任务不静默停止；旧状态写入后不自动回退。
- 同一CLI支持`--remote PROFILE`：新增独立RemoteUpdateSettings保存已有SSH alias、固定远端Python绝对路径和UpdaterSettings绝对路径。只拼固定子命令及经过schema校验的标识/数字，POSIX shell quoting；BatchMode/StrictHostKeyChecking/无TTY/无共享控制连接。无通用远程shell API；本机不传服务器凭证。远端update worker start_new_session，SSH断开可继续查status。

`required_capabilities`明确指Coordinator owner握手能力；Runner固定工具链与版本由真实完整预检验证，不把清单自述当作实现。全局发行/维护回执resource_id用instance_id，升级用task_id，保持既有UUID合同。

Owner API `/api/cloud/v1/releases`（list/get/register/activate）、`/maintenance`、`/tasks/{task}/upgrade`预留/提交；调用方先owner_authorize，再严格DTO。命令ID全局去重/请求摘要校验。只向owner握手增加releases/maintenance能力；Updater按需检查握手能力，不给无消费者的CloudProfile增加字段。

发行记录和active指针在Coordinator registry.meta；commands保存回执。初始配置模板为可信部署的bootstrap选择：没有动态active时继续使用原配置；动态active原子选择后新任务固定新回执，旧任务不变。registry启动能读持久发行，不依赖重写settings。

## 3. 正常/失败主链

Windows CLI → SSH固定命令 → Linux接收UUID及发行选择 → 私有journal/worker锁 → 可信清单/流式限额下载 → prepare_template（完整输入指纹；恢复仅复用身份匹配的既有镜像/回执） → 核对Runner实际版本与协议/schema → owner register → owner activate（原active revision） → 新任务从active固定模板。Updater不直接写活动cloud.sqlite。

ACK丢失：同command-id先读journal/owner receipt，登记/激活不重复。已构建但未预检：核对image ID及构建标签/输入指纹后执行完整预检，不能仅凭tag接受。失败状态保留原active与有限脱敏错误；worker被杀标记interrupted可显式resume。工作目录排他锁跨进程；原子JSON/fsync防半写，持有锁才推进阶段，原请求不可变。

维护准入：事务内设置maintenance标记；同一registry事务的任务创建/run/环境start/输入与结果新工作检查标记，现有history/控制必要保存/状态读取允许；维护ready必须没有活跃生产者/传输/未决动作。无I/O跨DB锁。任意既有工作导致draining，重试查询；leave与重启后标记保持明确。

升级：owner在CT1+短DB事务验证stopped/absent/无sandbox/运行/后台阻碍，设置environment_operation_id占用既有互斥；该命令operation为release:upgrade，不假装EnvironmentOperation，不被普通reconcile消费。Updater用新预检镜像运行固定backup模块，挂载原workspace/state只读、专用备份目录RW，非root/无网/无cap/no-new-privileges；不把整个cloud根挂入。备份不在宿主/Coordinator解析SQLite：物理停止后复制全部普通文件（含WAL），有界/FD身份/拒绝链接与特殊类型，manifest+持久完成回执。owner核对备份身份及已ready目标后CAS改固定模板，保持state_epoch与历史，清除预留；旧generation凭证在start换代撤销。重试不再重写固定身份，不清理备份。完成后复用既有environment start/Runner恢复；失败保留明确阶段与备份，不报升级运行已恢复。

## 4. 实现与验证

模块分别为contracts/cloud_releases、cloud_install的发行输入/下载/工作日志/worker/remote/升级备份、storage/cloud/releases、application/cloud/releases与maintenance、service/cloud/release_routes、CLI release分发及当前任务准入。复用现有配置读取、文件锁/FD私有存储和TemplateReceipt；不新建服务/依赖。超过8文件已明确，单文件≤500行。

锁表同步：Updater跨进程私有工作锁不嵌入CT/DB；release激活与维护只短DB事务；升级CT1→DB，网络/Docker/备份不持锁；maintenance与生产准入在同DB事务校验，禁止检查后竞态窗口。

TDD先红：清单/可信来源/坏SHA/协议/Runner实际版本；ready/active CAS、原ID重试与新旧任务固定；下载/构建/登记ACK丢失/worker中断续接/并发进程；维护与准入竞态；旧写入者未停拒绝、同schema升级备份与换代/失败；SSH参数注入与断线独立。随后专项→完整后端CI四分片/前端全量/构建→四目标wheel/Twine/源字节对包/Mac源外安装。真实两个不同构建输入和受控HTTP/进程验收与公司/供应商/Docker原机证据分开，未完成者不伪称通过。架构/安全只读专项复核及对抗检查。

S10.2仅在最终讲方案：自动发现/预备共用本片执行器、备份管理/恢复与引用回收；本片不加定时器、不删旧产物、不跨schema迁移、不发布或远程升级真实服务。

实施补充：失败升级增加显式cancel（仅未提交新版本时释放原预留，不删除备份）；升级占用阻止确认未知删除，通用环境reconcile明确拒绝release:upgrade。纯IO租约在独占CloudOwner启动、对外接受工作前回收，未决业务命令保留。已停止备份还会在全部复制结束时复核先前文件/目录身份。独立进程测试以标准wheel安装到临时venv（复用第三方依赖，受控前端夹具不声明UI验收），保持`-I`隔离；正式包安装另做全新依赖环境验证。

当前实现补充：minimum Coordinator版本与包补丁号相互独立；清单可要求维护升级，发布工具默认最低版本为目标包版本。规范化全局manifest身份与原始下载SHA各自固定；同image的新预检观察不改模板身份，保留原ready回执。原命令固定目标上下文/实例/清单SHA，缓存可脱离后续catalog继续。备份容器加command/task/instance/reservation/image标签并在超时/恢复/取消前kill/rm后复核，unknown不解除预留；取消DTO强绑定原预留。原启动unknown显式GET+reconcile同UUID。维护涵盖pending输入、新capture/delete与下载lease，已有必要保存和deleting GC可排空。所有SQL已留存储层，应用架构守卫保持。登记、激活与独占启动均重新校验当前协议/schema/能力及最低Coordinator版本，防止管理程序降级后继续接受不支持的active。未激活ready目标的升级预留/未完成提交、已固定旧版本的environment start/refresh及原IDreconcile、新run:start同样核对当前支持；拒绝发生在新副作用前，terminal回执/stop/cancel/必要保存继续放行。无动态发行记录的bootstrap任务保持原冻结snapshot，不因默认配置变化重绑。

运维用法与首次安装边界见[cloud-updates](../cloud-updates.md)。本机Docker socket不存在，真实daemon两版本构建/备份/换代与Windows SSH原机保留后置；本轮真实wheel/HTTP/隔离安装/detached进程与受控Docker响应分级记录，不冒充公司通过。

## 5. 本机实施收尾

最终6220后端（0失败/错误/跳过）、72文件594前端及标准构建、四目标wheel/Twine、500生产Python及前端逐字节对包、Mac源外全新依赖安装、真实6产物发行索引往返通过；架构/安全复核收口。118项执行边界专项含标准wheel隔离子进程/真实本机HTTP与受控Docker可执行响应。最终命令/日志/失败修复边界见[report](../../build/spec143-s101-20261103/report.md)与[verification](../../build/spec143-s101-20261103/verification.json)。旧用户资源/配置及四份Spec142未改，无提交/push/发布/部署。真实daemon与Windows/Linux原机仍待统一验收，S10.2仅讲方案，未实施。
