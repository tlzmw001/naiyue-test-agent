# 更新、备份恢复与 Windows 远程触发（S10.1/S10.2）

当前工作区能力，尚未发布或部署。源码/包身份以本轮验证记录为准，不能仅凭0.5.6版本号判断包含本功能。Windows/Linux原机及真实Docker验收统一后置；本机受控HTTP/独立进程结果另列。

## 1. 执行位置与首次准备

日常可以在Windows调用命令，经已配置OpenSSH alias触发Linux；Linux独立worker下载、校验、构建和预检，通过所有者API登记/激活。Windows关闭或SSH断开不取消已受理worker，之后用原command-id查询。Coordinator不持有Docker socket，Updater不直接写cloud.sqlite。

首次部署需要：

1. 在Linux专用运维venv安装包含10.1的已核验wheel，Coordinator也须具备releases/maintenance接口。旧服务没有这些接口时，先按原部署方式停服、安装和启动；本指南不自动执行部署。
2. 保留已核验Docker backend、模板UID/GID及构建权限。Updater只使用管理员配置的Docker可执行入口/私有wrapper；不从任务消息取得Dockerfile、下载URL或shell。
3. Linux配置私有UpdateSettings及CloudConnection文件，非任务可写；Windows配置私有RemoteUpdateSettings，SSH alias和host key沿既有批准配置。目录/文件权限沿0700/0600；路径必须绝对，禁止链接替换。
4. 正常安装使用标准wheel。Updater用`python -I`隔离运行，不从项目工作区/PYTHONPATH寻找自身代码。

## 2. 配置归属

`--settings`是Linux更新配置，不是Coordinator的CloudSettings，也不是原`release prepare`的构建输入JSON。`--remote`指Windows本机的远程更新连接配置，两者互斥。

UpdateSettings的必要字段：

| 字段 | 内容 |
| --- | --- |
| work_dir | Linux私有更新目录；jobs、校验缓存、备份都在这里 |
| index | 固定可信HTTPS发行索引，或绝对路径的私有离线index.json |
| trusted_prefixes | 索引/清单/文件/每次重定向允许的HTTPS来源及路径前缀；GitHub发行下载需要明确允许最终资产域，不能任意跟随重定向 |
| platform | linux-arm64或linux-x86_64，与实际Docker架构一致 |
| connection_file | 现有CloudConnection私有文件，绑定Coordinator实例与所有者身份 |
| build | 已核验的运维构建参数：template_id/backend_id/image_tag/docker_host/docker_executable/package_index/uid/gid，及构建超时、显式build_proxy_env |
| task_data_dir | 升级已有任务时需要的实际cloud根，必须与连接实例标记一致；普通新版本准备不需要读取任务数据 |

wheel、Kit、锁文件、base image和每次输出路径由已选择发行与操作ID生成，日常配置不要求填写旧产物路径。原`release prepare`仍接收完整TemplateBuildSettings，底层构建实现共用。

可选`download_proxy`只用于发行HTTP下载，保存在私有配置中；不隐式继承环境代理，不写journal/argv/镜像。构建代理继续使用现有显式build_proxy_env或已核验私有wrapper，两者不替任务运行网络配置。

下载大小/分块/超时及备份文件数/总字节/CPU/内存/PID预算均可配置。备份容器只挂本次task的workspace/state只读及专用备份目录可写，非root、无网络、无cap、no-new-privileges。

RemoteUpdateSettings字段：

- `ssh_host`：既有OpenSSH alias，不能是任意SSH参数或shell。
- `remote_python`：Linux运维venv的Python绝对路径。
- `remote_settings`：上述Linux更新配置绝对路径。
- 可选`ssh_executable`、`timeout_seconds`。命令固定BatchMode/StrictHostKeyChecking/无TTY/禁共享ControlMaster，参数逐项校验并POSIX quoting。

## 3. 日常操作

以下身份/路径均需替换为实际值。`COMMAND_UUID`由调用方生成并保存，断线重试始终使用同一个ID；不能用新ID猜测原操作是否成功。

```bash
nyt cloud release check RELEASE_ID --remote REMOTE_JSON
nyt cloud release update RELEASE_ID --command-id COMMAND_UUID --remote REMOTE_JSON
nyt cloud release status COMMAND_UUID --remote REMOTE_JSON
nyt cloud release resume COMMAND_UUID --remote REMOTE_JSON
```

Linux本地执行时把`--remote REMOTE_JSON`替换为`--settings UPDATE_JSON`。Windows只负责固定SSH调用，不构建Linux镜像。

check输出目标清单SHA、当前active修订号、版本/协议/能力检查及维护要求。清单可声明最低Coordinator版本；版本号不同不直接等于协议不兼容。若要求维护升级，先按第5节处理，再用原ID继续。check会读取/缓存可信清单，不构建或激活。

update受理后立即返回原command-id；journal会记录verifying/building/registering/activating及失败阶段。兼容且完整预检成功后才登记ready并CAS激活给新任务。实际预检同时核对Runner包版本、协议、state schema、Kit/pytest/报告/原生工具和非root边界。

坏hash、缺产物、版本不匹配、失败/中断不会被当作成功。已完成校验文件复用；缓存命中仍核验SHA。选定原清单SHA可在catalog发布下一版后从缓存继续，不换同ID的新内容。已构建镜像通过构建输入label/image ID复核后重做预检，不只凭tag接受；登记/激活丢ACK重试同派生UUID，不重复推进active。

同部署更新目录按job锁→全局build锁串行执行；忙碌任务返回blocked/busy，显式resume即可。worker已退出但journal仍running时，status显示interrupted。unknown表示必要物理事实未确认，不等于失败副作用不存在。

## 4. 新旧任务与显式换代

R2激活后，新的同logical template任务固定R2；原任务仍保存R1的template snapshot/image ID，不热换。已登记旧版本可用显式activate重新选择给新任务：

```bash
nyt cloud release activate RELEASE_ID --template-id TEMPLATE_ID --expected-revision ACTIVE_REVISION --command-id COMMAND_UUID --remote REMOTE_JSON
```

已有任务先通过现有控制入口完成必要保存并停止环境，后台/保存/未知操作未收束时会拒绝升级。随后执行：

```bash
nyt cloud release upgrade TASK_ID RELEASE_ID --command-id COMMAND_UUID --remote REMOTE_JSON
nyt cloud release status COMMAND_UUID --remote REMOTE_JSON
nyt cloud release resume COMMAND_UUID --remote REMOTE_JSON
```

仅同state schema支持此流程。Owner先预留任务并确认旧generation无物理容器；Updater在隔离容器内复制全部普通workspace/state文件（含WAL，不解析SQLite），拒绝链接/硬链接/特殊文件及复制期间改动。manifest/完成标记持久确认后，Owner核对原task/generation/源模板/目标发行/备份身份，CAS切换固定模板。原state_epoch与历史保持，后续沿既有start生成新generation，撤销旧代凭证并核对Runner身份。

原start若丢ACK留下unknown/running，resume查询并reconcile原启动UUID，不新建启动操作。Runner waiting_model如实记录；requires_recovery要求沿现有恢复入口处理，不自动重放不确定业务。

失败且尚未提交新模板时，可以显式取消预留：

```bash
nyt cloud release cancel COMMAND_UUID --remote REMOTE_JSON
```

取消要求该job当前未被worker持锁，先核对本操作备份容器的完整ID/name/image及instance/task/command/reservation标签；停止并确认退出后才解除预留。不同身份容器不触碰，无法确认则保留unknown。备份失败/超时、worker被杀后的resume也先收敛原容器，不盲目启动第二个。已提交新模板后不能用cancel把旧程序指回已写入状态。

失败备份与最多三个有界复制尝试保留，10.1不清理备份/旧镜像。备份的恢复管理与引用回收留给10.2。

## 5. Coordinator与Windows程序

需要替换Coordinator程序时，先进入维护：

```bash
nyt cloud release maintenance enter --command-id MAINTENANCE_UUID --remote REMOTE_JSON
nyt cloud release maintenance status --remote REMOTE_JSON
```

维护draining会列出原运行/必要保存/未决命令/传输与清理租约。原输入可完成或取消，已登记结果保存可排空；新任务/run/环境启动/独立捕获/删除/新下载受准入限制。已经deleting的结果清理允许完成并保持可见租约。ready不是“无连接”，空闲Runner/观察连接无需人为全部断开。

ready后按原部署方式在专用venv安装check所列SHA对应的wheel并重启管理服务，不在运行中替换进程。check给出需用的coordinator_wheel元数据，下载及安装仍须按选定SHA核对。维护标记跨重启保留，确认版本/协议/实例及旧任务后再leave：

```bash
nyt cloud release maintenance leave --command-id LEAVE_UUID --remote REMOTE_JSON
```

10.1不安装系统服务、不静默杀业务或修改防火墙。Windows本机包沿原安装方式单独升级，云端握手核对协议/能力；本机服务协议2与云端owner协议1是两条不同合同。已登记发行也会在激活和Coordinator启动时重新检查兼容性；管理程序降级后若不满足当前active的最低版本，启动明确拒绝，须恢复支持该发行的管理程序，不能以旧程序可import作为回退证明。非active的已登记旧版本也会在任务升级、环境启动/核对及发送新消息时检查当前支持；停止、取消预留与必要保存仍可收束。

## 6. 发布端生成更新输入

发布者先完成标准同源四平台wheel检查，再准备明确Kit产物、锁定工具链和digest基础镜像，生成发行清单/index；不是只向PyPI上传一个新版本号。下列工具只生成本地产物，不上传或部署：

```bash
python -m scripts.cloud_release_manifest --wheel-dir WHEEL_DIR --kit-wheel KIT_WHEEL --kit-profile release/cloud-test-toolchain.json --test-lock release/cloud-test-requirements.txt --release-id RELEASE_ID --source-commit SOURCE_COMMIT --base-image BASE_IMAGE_DIGEST --public-base-url PUBLIC_RELEASE_ASSET_BASE --output NEW_OUTPUT_DIR
```

可选`--previous-index`保留既有发行条目；同ID不同SHA拒绝。默认最低Coordinator版本取目标包版本；只有确认兼容时，发布者才显式设置`--min-coordinator-version`。清单中的required_capabilities指Owner实际实现能力，不能以清单自述替代Runner预检。

目录中的wheel/Kit/锁文件/发行JSON/index需一并按既有可信渠道发布，固定索引渠道应保留仍需取用的发行。清单SHA用于与可信索引比对，不宣称可独立抵御发布账户失陷。真实发布/远程部署仍是单独授权操作。

## 7. 渠道发现与自动预备（S10.2）

发行生成器的`--channel`默认stable，索引保留已有channel并显式推进所选channel。运维UpdateSettings的`channel`选择该渠道；执行下列命令一次即发现一次，可由现有调度器按管理员选择的周期调用固定命令。本项目不自行安装cron或系统服务。

```bash
nyt cloud release discover --remote REMOTE_JSON
```

只有可信索引指向的固定manifest参与更新。`auto_activate=true`（默认）通过完整准备、预检和登记后CAS激活；false停在ready，随后使用第4节显式激活。已激活同SHA返回up_to_date，不兼容返回requires_maintenance，旧active保持。发现操作ID绑定实例、manifest SHA、active revision、版本库存revision及激活策略，相同状态重复调用续接原操作；GC/恢复后库存变化可产生新预备操作。调度失败/断线不跳过原ID核对。

## 8. 运维目录与完整备份

新增私有配置字段由管理员在原配置上明确添加：`cloud_settings_file`指实际Coordinator JSON，`task_data_dir`与其中data_dir完全一致，`recovery_dir`是独立恢复根。work_dir、当前cloud根、recovery_dir及protected_roots不能相互嵌套或借目录别名重叠。备份位置固定为`work_dir/instance-backups/<backup-id>`，不接受任务提供路径。

`maintenance_receipt`可指向已通过预检、包含本轮维护工具的TemplateReceipt。默认选择当前active或bootstrap模板，并先在无挂载容器中检查维护协议、原始备份/恢复模块和UID/GID；旧业务任务pin不会被更换。旧镜像没有维护工具时需先准备本轮维护镜像，再明确选择它。维护镜像与实际backend、构建UID/GID、宿主运行身份必须一致。

操作顺序：Coordinator在线时enter维护→排空已有操作→逐任务正常释放环境并核实stopped/absent→按原部署方式正常停止Coordinator→远程触发离线备份。Windows仍能经SSH触发，操作不依赖Owner HTTP在线。只停止进程而遗留容器（包括已退出容器或上次维护helper）会拒绝，不能靠数据库lifecycle冒充物理停止。

```bash
nyt cloud release maintenance enter --command-id MAINTENANCE_UUID --remote REMOTE_JSON
# 排空、释放环境、正常停Coordinator后：
nyt cloud release backup --command-id BACKUP_UUID --remote REMOTE_JSON
nyt cloud release status BACKUP_UUID --remote REMOTE_JSON
nyt cloud release resume BACKUP_UUID --remote REMOTE_JSON
nyt cloud release backups --remote REMOTE_JSON
```

备份包括一致的管理registry/history、输入/结果/传输字节和各任务workspace/state；管理程序不打开任务SQLite，确认无writer后由隔离维护容器复制原始数据库/WAL/普通文件并校验。token/control目录不打包，恢复后重新签发运行身份。目录内未知节点、链接、特殊文件、变动或预算超限显式失败。complete只在全部内容清单完成后发布；列表中的complete/integrity=not_checked不是全量校验通过，restore/GC会重新验证字节。失败保留原ID/尝试目录，不自动删用户内容。

容量参数：`max_cache_bytes`默认2GiB、`min_free_bytes`默认256MiB；`backup_max_bytes`默认4GiB、`backup_max_files`默认100000，另有维护容器memory/cpus/pids。它们是拒绝操作的预算，不是超过后自动清理。需要扩大时由管理员根据原机磁盘和样本修改私有配置，不能把默认128MiB输入额度当200MiB性能样本通过。

## 9. 新根恢复及唯一启用

首版是同实例/同Docker backend及UID/GID、兼容当前管理/Runner格式的恢复，不是跨主机迁移或任意降级工具。受保留备份引用的镜像必须仍在；更换机器、跨state schema不自动猜迁移。

```bash
# Coordinator和全部writer仍须停止，恢复不会覆盖当前根。
nyt cloud release restore BACKUP_UUID --command-id RESTORE_UUID --confirm BACKUP_UUID --remote REMOTE_JSON
nyt cloud release status RESTORE_UUID --remote REMOTE_JSON
nyt cloud release resume RESTORE_UUID --remote REMOTE_JSON
# 等phase=prepared，检查返回restore_root和备份身份后显式启用：
nyt cloud release restore-activate RESTORE_UUID --confirm RESTORE_UUID --remote REMOTE_JSON
nyt cloud release status RESTORE_UUID --remote REMOTE_JSON
```

prepare将完整内容复制到recovery_dir下的独立新根，重绑输入/结果的物理身份，清除可再生结果缓存和旧运行凭证；历史、task/input/result身份及任务固定版本保留，任务停在stopped。prepared根禁止Coordinator启动。activate重新核对全部准备内容、权限和旧源状态，先给旧根写superseded fence，再启用新根；重复或丢ACK沿原RESTORE_UUID续接，不能从同一旧源启用第二份恢复根。

启用新根前还须检查OpenSandbox后端的`allowed_host_paths`：新`restore_root`必须落在允许挂载的宿主路径范围内。若原白名单只允许旧根，由管理员按最小范围显式允许新根，并按后端部署要求使配置生效；不要自动放宽为整块磁盘。Coordinator的`data_dir`变化不会自动修改后端白名单。核对不通过时保留prepared现场，不启动业务任务。

completed后管理员另建一份CloudSettings，将data_dir明确设为返回的restore_root（其他可信配置照原机核对），启动该新根Coordinator，核验原instance_id/历史/结果和任务启动，然后leave维护。更新日常运维配置中的cloud_settings_file/task_data_dir为这份新根配置；原恢复操作日志仍用原设置查询，不能中途重定向它。原根保留查证且拒绝启动，不自动删除或改回fence。

带恢复/GC保护的管理根使用root schema2及registry user_version2；当前程序可读，旧S10.1程序明确拒绝，防止旧程序忽略fence。它不是Runner state schema升级（仍为1）。发生故障应使用支持该格式的本轮候选及原ID恢复，不能移除guard或改小schema来强开。

## 10. 引用回收：先预览，再确认同一清单

GC也要求维护、任务释放、Coordinator停服和物理writer不存在。保留数`keep_backups`默认3；`gc_backup_verify_bytes`默认8GiB是每次全量备份核验预算；坏备份和未核验备份不会抵扣有效保留数，也不会被当成可删除对象。`gc_max_resources`默认256限制一次盘点规模，超限显式拒绝。

```bash
nyt cloud release gc --command-id GC_UUID --remote REMOTE_JSON
nyt cloud release status GC_UUID --remote REMOTE_JSON
# 审阅phase=prepared中的candidates/protected，原样使用plan_sha256：
nyt cloud release gc --command-id GC_UUID --confirm PLAN_SHA256 --remote REMOTE_JSON
nyt cloud release status GC_UUID --remote REMOTE_JSON
nyt cloud release resume GC_UUID --remote REMOTE_JSON
```

活动/停止任务pin、active/bootstrap/维护模板、待完成更新/恢复/升级、当前渠道manifest和保留备份的必要版本均受保护；坏/不明引用保守保留。只处理可证明归属本work_dir、原构建label和唯一tag的无引用镜像，以及无引用SHA缓存和超保留的已验证整机备份。不扫描旧A卷/目录，不执行全局prune或force。

确认后重新盘点；引用或文件身份变化则拒绝并要求新预览。真正删除前先撤销ready/标记retired并提升库存revision，再逐路径比对原清单删除。失败保留guard，禁止Coordinator和新更新操作绕过；沿原GC_UUID续接，完成后才开放。外部tag/容器引用变化仍由Docker无force删除保护；不把一次预览当永久授权。
