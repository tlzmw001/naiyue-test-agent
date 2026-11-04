# Spec143AH：S10.2 自动预备、静止备份恢复与引用回收

2026-11-03 用户授权连续完成143剩余代码与本机验证，再由用户执行最终Windows验收。本片承接143AG唯一Updater链；不发布、远程部署、修改真实配置、删除用户资源或兼容旧A。原455项dirty与四份Spec142摘要保存在build/spec143-s102-s11-20261103。

## 1. 当前链与改动边界

`cloud release update`→UpdateStore原ID/目标锁→可信manifest SHA→prepare_template→Owner register/activate；旧任务固定snapshot。`upgrade`只有本任务raw备份，无完整实例备份/恢复目录或引用回收。registry/history由管理域拥有，任务workspace/state不可由管理进程解析SQLite；输入archive_identity及artifact blob_identity绑定物理文件，恢复后须核对内容再重绑。管理根另有inputs/artifacts、history、transfers及tasks/<id>/environment.json。

本片增加三个用户入口，复用固定SSH分发。自动更新在线使用Owner API；完整备份/恢复/回收采用离线运维入口：必须先维护排空、停止任务环境与Coordinator，取得原CloudOwner和Updater全局build锁，再核对后端无本实例容器。离线链不构造Agent、不调用模型、不自动停止业务。

## 2. 自动发现

ReleaseIndex增加`channels: {channel: release_id}`，渠道必须引用索引中的既有发行；显式发行入口保持原合同。发布端生成索引时明确更新所选渠道。UpdateSettings增加渠道、是否自动激活、更新缓存/磁盘预算；不创建或修改实际配置。

`nyt cloud release discover --settings FILE`：读取配置渠道→固定manifest→现有兼容预检；当前active相同返回up_to_date；不兼容返回requires_maintenance及具体issues。由instance/template/manifest SHA/当前active revision/库存代次/激活策略派生稳定UUID，存原manifest SHA再调用同一detached worker。重复触发共用job锁/构建锁；prepare-only仅登记ready。Linux既有调度器调用此单次入口，不在Coordinator里新建特权调度服务。轮询间隔由部署调度配置，失败保留原active。Windows通过`--remote`同样可以触发。

## 3. 完整静止备份与恢复

UpdateSettings增加可选`cloud_settings_file`（当前CloudSettings私有路径）与`recovery_dir`（独立新根父目录）。操作前核对task_data_dir、配置根、连接instance及backend/UID，不从任务消息取得路径。work_dir/recovery_dir与源根及受保护根不得相互包含；恢复目标为recovery_dir/<restore-command-id>/attempt-<随机ID>（最多三次保留尝试），不接受任意覆盖路径。

命令：`backup --command-id UUID`、`backups`、`restore BACKUP_UUID --command-id UUID --confirm BACKUP_UUID`、`restore-activate RESTORE_UUID --confirm RESTORE_UUID`，均支持现有`--settings/--remote`。

备份持有CloudOwner（失败cloud_in_use不推进）→严格只读注册数据/静止检查/物理无容器→管理库通过SQLite backup生成一致快照，管理文件复制只含固定自有目录→每个实际任务目录用143AG隔离raw备份容器→所有字节/目录类型/预算/SHA复核→manifest与完成标记持久发布。保留WAL语义；管理历史/共享投影只由管理域打开，任务SQLite始终仅raw复制。无task目录的未启动任务保留元数据；有目录必须完整验证environment标记。未登记/链接/特殊节点拒绝，不静默跳过。预算涵盖整份备份；失败留incomplete、不能列为可恢复。

备份manifest固定instance、源根身份、版本/协议/schema、tasks/templates/input/artifact引用、文件SHA/大小/mode及完整内容。只记录身份与必要元数据，不含部署配置、根密钥或命令环境；任务内容仍按私有备份保护。备份重试同UUID不换源，已完成备份重读完整核验。

恢复先核对manifest及所有内容→新目录中还原管理快照/自有字节、环境标记及workspace/state→核对input/archive、artifact bytes的hash，重绑新的inode身份；不改变逻辑input/result ID。撤销旧task凭证、清除旧控制连接与纯IO lease，任务保持stopped/absent，generation沿原start换代，历史epoch/内容与固定版本保留。必须核对该备份的schema及当前程序支持。恢复目标含pending guard，普通服务拒绝启动。

显式restore-activate再次取得源/目标Owner和build锁，核对源无容器、原备份/恢复身份及目标核验摘要，先给旧根写superseded fence，再开放新根；中断后按同ID续接，不能同时开放两根。不会修改现有CloudSettings或启动服务；用户将部署data_dir明确改到新根后启动。旧根数据保留，旧根启动明确拒绝。恢复前原根已有不一致恢复/GC操作则拒绝，不猜测回滚。

## 4. 引用回收

`gc --command-id UUID`生成固定候选/保护原因/大小/摘要；`gc --command-id UUID --confirm DIGEST`在同一offline Owner+build锁中重新核对快照，变化即拒绝。所有删除只在测试临时根验证，真实资源留用户按此确认入口操作。

保护：每逻辑模板active、非deleted任务snapshot（含stopped）、未完成/failed/unknown更新job、升级预留及备份、保留实例备份/未完成恢复。备份按配置保留数/显式引用保留；候选备份在本轮仍保护其版本，避免同次连锁清理。输入/结果用原回收链，当前GC不删除其管理根。bootstrap/无法证明归属/不完整未知备份保守保留并给原因。

仅回收可证明由本work_dir job生成的未引用ready镜像、SHA缓存和完整且不受保护的旧备份。镜像须核对完整ID、构建label、本配置tag命名空间，任何外部tag或任何容器引用均保护；无全局prune。登记ready元数据与物理删除使用持久deleting journal：先阻止未来引用，再按原ID完成物理删除，失败不恢复ready或释放保护；重试可核对已不存在。离线排他防止新任务/激活并发。禁止链接/硬链接/特殊文件及目录替换，删除固定受管目录，未知文件不扩展范围。

## 5. 文件、锁与验证

扩展contracts/cloud_releases、cloud_install/settings/source/store/worker/remote、cli/cloud_release及发布清单脚本；新增cloud_install/discovery、offline、backup、restore、reclaim及必要的固定raw树工具，storage/cloud/operations负责全部SQL/自有SQLite快照与恢复重绑。service启动读取恢复guard；不新增依赖或第二套构建器。各模块≤500行；共享树算法扩展143AG raw复制/校验，按第三次相同职责提取，不复制三套安全遍历。

锁：原UP1 job→UP2 build→CloudOwner（单根，恢复切换按规范路径排序非阻塞取得两根）→短DB锁；无网络/Docker跨DB锁。离线Owner排除运行Coordinator，但不替代后端无旧writer证据。新增顺序同步lock-hierarchy。

TDD：渠道选择/引用错误、重复发现/失败不激活/prepare-only/预算；在线Owner拒绝离线操作、活动或未知writer拒绝、task SQLite恶意schema不被宿主打开、WAL/二进制/目录mode一致、links/FIFO/复制竞态拒绝、坏SHA/中断不发布；完整backup→restore新根→input/artifact/history可用→切换旧根fence；GC保护active/stopped/job/backup/容器/其他tag，引用变化/删除中断恢复及路径竞态。专项后完整tests分片、前端/构建/四包/Twine/源外安装、同源字节与保护范围、架构/安全/对抗复核。

真实Docker/Windows/公司网络证据与受控进程明确分开；用户最终Windows验收包含Linux侧命令。S11补组合探针/性能采样/验收包，未完成原机项不伪称通过。

## 6. 实施中完整调用链复核补充

最终复核补充：管理备份payload及prepared新根的describe_tree清单必须同时核对字节/路径/权限；prepared之后chmod文件或目录，启用前拒绝且旧源仍可用。共享verify_tree使用显式verify_modes开关；原始任务备份payload有意收紧为0600/0700，不能将manifest中的原业务权限误作归档实际权限，恢复后才还原原权限。整机备份外层清单登记的是实际归档权限，启用权限核对。

registry.meta沿既有键合同读取：history:<task-id>是原始initializing/ready字符串，其他运维meta为JSON；新快照不得假设全部为JSON或改历史存储格式。真实history归档→完整备份→新根恢复→history可读为组合回归。仅未完成恢复操作保护源备份；completed恢复根已独立复制，旧操作不再读取备份，允许按有效保留数进入后续GC候选，避免备份永久积累。

恢复prepared binding/fence固定准备时原根dev/inode，activate持有原根Owner后先核对，不允许rename原根并复制等内容到原路径来绕过唯一启用。备份清单另记录当次源根FS身份供查证，它与持久instance_id、路径字符串是不同事实，不承诺跨盘/跨主机迁移。任务删除会将原升级回执redact；raw升级备份仍存在但依赖proof不再可得时，GC显式列unresolved_reference并保守保护版本/缓存，不KeyError或默删旧镜像。

## 7. 本机交付与验证

2026-11-03最终冻结源码：6283后端测试全部通过，0失败/错误/跳过，四个既有CI文件分片不重叠且总数与正式tests收集一致；72文件594前端及标准构建通过。513生产Python/4前端文件与四目标标准wheel逐字节一致，四wheel/Twine、Mac源码目录外全新venv和依赖安装smoke、真实四wheel＋Kit/lock离线manifest往返、旧S10.1实际wheel拒绝root2/缓存root1＋DB2探针均通过。源码freeze、原455项dirty快照、四份Spec142保护和具体命令/中间红灯保存在build/spec143-s102-s11-20261103；当前[验证报告](../../build/spec143-s102-s11-20261103/report.md)区分证据等级。

正常链可从CLI固定SSH参数→UpdateStore原ID→独立worker→operations分派→UP2/Owner/物理writer检查→backup/restore/reclaim→storage短事务和隔离维护字节链追踪。失败链包括在线Owner/残留helper/目录别名/源根替换/权限变更/内容损坏/预算/旧格式/确认摘要变化，均在副作用前拒绝；删除不明持久guard，原ID续接，finalizing不再删除。原始任务SQLite/WAL只在隔离维护域当字节复制，host仅打开自有registry/history。

架构/安全只读复核提出的问题均已补失败回归并收口；真实history＋artifact完整备份恢复、再次freeze、多根启用拒绝、已安装标准wheel脱离launcher离线备份均有实际回归。Docker可执行返回受控，本机无可用daemon；不声称真实Docker隔离、Windows SSH/Linux操作、公司业务/provider或目标性能通过。当前未提交/push/发布/部署，真实配置/用户数据/Docker资源及无关Spec142保持。唯一下一步为用户按最终手册原机验收。

- 所有任务与维护writer查询同时覆盖nyt-instance及org.naiyue.upgrade-instance；原helper异常退出先按已保存完整身份核对/停止，unknown不越过。root普通marker与guard在取得Owner锁后重新读；恢复fence绑定唯一restore UUID、目标根inode/路径及准备摘要，两根按路径排序非阻塞获取。旧control目录/明文task token不进备份或恢复。
- 原state/results/.baseline-cache含旧inode，隔离恢复清除该可再生缓存；result-plans/spools仅在Owner已确认终态且plan摘要一致时退休，无法证明则恢复失败，不能在宿主解析task SQLite或任意删任务内容。
- task业务pin与维护工具镜像分离：显式maintenance_receipt或当前active/可信bootstrap模板选择运维执行器，先无挂载验证maintenance_probe真实导入raw backup/restore、协议/UID/backend，再挂本任务。完整备份记录运维模板并纳入required依赖；旧S10.1镜像缺restore模块不能作执行器。
- 备份required_templates由active、非deleted任务snapshot、bootstrap及维护模板重算，绑定逻辑模板/release/fingerprint/image ID。只保这些执行依赖，恢复时先裁剪未使用ready并写退休标记，保留规范化发行身份及历史终态回执；避免每份新备份永久引用所有闲置ready的闭环。任何必要动态ready缺失均失败。
- GC撤销ready时写每逻辑模板退休标记并提升库存代次；恢复也提升代次。discover新UUID绑定该代次，旧completed/register UUID仍只返回历史结果，不能复活已删ready。当前配置渠道、未完成job、raw升级备份、保留实例备份及已准备恢复都保护所需资源。
- 新job创建与GC共用UP2；GC在work_dir和管理根持久双guard。预览/确认间引用或物理身份变化拒绝；guard发布但删除前失败可解除本操作guard；deleting不允许普通启动或新job，finalizing只开放guard不再删除，防重试误删新镜像。目录回收用有界FD/stat清单，不为GC通读大文件；外部tag、多tag、容器引用或无本目录构建回执的镜像保留。
- 防旧管理程序忽略新guard：新完整备份管理快照/恢复目标使用root.schema=2、registry user_version=2；Runner state_schema仍1。源根只在显式restore激活/GC确认后、写guard或撤销ready前，将registry提升2并原子提升root marker；当前程序读1/2，旧严格v1程序拒绝2。先升DB也覆盖旧进程锁前缓存root1的竞态。无表DDL或任务状态迁移，不允许旧Coordinator读已guard化根；失败现场由当前程序原ID续接。实际验证仅临时根，真实Linux升级与格式变化列最终验收。
