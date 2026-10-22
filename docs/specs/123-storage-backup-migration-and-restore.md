# Spec123：会话库备份、迁移保护与显式还原

状态：2026-10-21，00—04的本机实现/组合验收与标准交付接线完成；备份、迁移前保护、显式还原、中断收尾及正式重开均有证据，04结果见第20节。四目标wheel构建通过，Mac ARM64源码外安装通过；Linux双架构/Windows原机与本轮远端CI仍待验，不声称四平台运行验收完成。

2026-10-22发行补充：备份校验的文件身份仍以设备/文件ID、长度及两次同源元数据检查为准。CPython 3.12 Windows的句柄fstat使用ChangeTime，而路径lstat兼容返回CreationTime，不把二者的ctime直接相等当作稳定文件的必要条件；句柄前后和路径前后分别核对ctime/mtime，跨来源仍核对设备/文件ID/长度。摘要、结构、归属和修改/替换拒绝合同保持。失败日志只记录固定阶段与差异字段名，不输出路径或备份内容。原失败CI39631304294及本轮红绿和原机结果由本轮发行交接引用。来源：[CPython句柄字段](https://github.com/python/cpython/blob/v3.12.10/Python/fileutils.c#L1036)、[路径兼容字段](https://github.com/python/cpython/blob/v3.12.10/Modules/posixmodule.c#L2015)。

## 1. 需求来源与首期承诺

2026-10-08最小持久会话之后已记录存储版本迁移、备份恢复、checkpoint增长与磁盘满等待办；后续曾把备份恢复排在checkpoint安全修剪之前。用户最新授权的Spec124已在另一会话完成小批checkpoint修剪，本Spec保留其结果；校验不得要求已允许修剪的历史父checkpoint或聊天归档来源仍存在。当前没有真实用户数据库损坏的复现证据，本项不是Windows10054或输出归档ValueError的修复。

用户确认的三个目标：

1. 对需要执行存储迁移的已有会话库，在迁移前创建并验证完整副本。
2. 迁移失败停止启动，保留迁移前副本与当前现场；失败步骤回滚，不声称已提交的前序步骤也被回滚。
3. 用户通过独立CLI选择备份，离线显式还原同一工作区会话库；还原中断后有确定的检查与收尾路径。

沿用SQLite、工作区身份、同库单应用持有、现有迁移和会话恢复合同。首期不增加云/定时备份、备份自动淘汰、跨工作区导入、单会话合并、自动坏库抢救、程序降级、checkpoint修剪或管理页面。

## 2. 当前代码事实与数据职责

本轮实际核对LangChain1.4.2、LangGraph1.2.12、langgraph-checkpoint-sqlite3.1.1；没有升级依赖。

当前入口：[工作区定位](../../naiyue_test_agent/workspaces.py)、[存储/迁移](../../naiyue_test_agent/application/session_storage.py)、[异步持久装配](../../naiyue_test_agent/application/async_persistent.py)、[会话恢复判断](../../naiyue_test_agent/application/session_recovery.py)、[顶层CLI](../../naiyue_test_agent/cli.py)。

| 入口/模块 | 当前职责 | 本片需要补足的边界 |
| --- | --- | --- |
| `workspaces.prepare_workspace` / `locate_workspace` | 普通启动读/创建标记、短持数据库实例锁检查维护标记后验证绑定；维护定位只读已有身份，不打开目标库 | 01/02已接；03替换文件复用同一实例锁，保持移动/副本规则 |
| `storage_files` | 普通单链接文件、重解析点/符号链接检查，非继承句柄，POSIX/Windows实例锁 | 复用这些保护，不另造锁文件或改变已有权限位政策 |
| `SessionStorage.__init__` | 长持实例锁，先检查维护标记再检查/创建主库；调用initialize_store备份并迁移已有旧库 | 02已接，SDK/Web/chat共用；03复用门禁 |
| `storage_migrations`及原迁移模块 | 4/5→6和chat→7由SessionStorage搬到固定步骤；原chat8、usage9、diagnostics10、reply11/12、duration13事务保留 | CURRENT_SCHEMA/源范围集中，备份成功后按原顺序执行；当前版本恢复整理保持，错误带版本/阶段/备份ID |
| `open_async_persistent_application` | 先打开SessionStorage，随后模型/MCP准备、官方AsyncSqliteSaver.setup、用量/诊断订阅和运行时装配 | 备份/迁移失败不能进入后续装配；框架持久格式变化需显式纳入迁移计划 |
| `SessionPersistence` / `session_recovery` | 按checkpoint、消息配对、审批和未完成工具状态决定能否使用会话 | 还原文件不调用这些执行操作，不将旧running或pending直接改成ready |
| `cli.main` / Web启动 / chat入口 | 默认Web，chat/mcp/storage延迟分发；持久启动共用SessionStorage，终端共享安全失败展示 | 01/02已接；03已注册restore，Web传输异常不打印原IO正文 |

权威数据以整个工作区SQLite快照为单位：会话/工作区绑定、根子checkpoint与writes、聊天档案、reply边界、用量及有限诊断一起保存。恢复时不拼接不同时间点的表。

模型/工具配置、凭证、工作区源码、库外图片及工具完整输出文件不在本快照内。DB中的文件引用可能恢复，文件本身不会因此恢复；还原不撤销文件修改或已发生的网络调用。

现有SDK/测试允许未绑定工作区的数据库。自动迁移备份允许其`workspace_id=null`；不为本功能强制补绑定。首期工作区还原命令拒绝未绑定备份，不凭当前目录替它补一个身份。

## 3. 接口、位置与所有权

### 3.1 维护CLI

| 命令 | 合同 |
| --- | --- |
| `nyt storage backups [--workspace PATH] [--session-db PATH]` | 01已实现，JSON列目标库备份，按created_at降序、backup_id打破平局；空结果为空数组；异常条目显示有限错误状态并记录安全诊断 |
| `nyt storage restore BACKUP_ID [--workspace PATH] [--session-db PATH]` | 03已接线；显式离线还原，展示目标、备份时间/版本/原因及还原会舍弃的时间范围，用户输入完整BACKUP_ID确认后执行 |

沿用`--workspace`默认cwd、既有`StorageSettings.data_dir`及`--session-db`路径语义，不新增真实设置。没有工作区标记时，不创建标记/新身份或空库；列表返回无可定位工作区，restore失败。已有标记且当前库丢失时允许查备份并显式还原。

storage命令只加载定位所需的现有应用设置，不读取models.json，不建ModelCatalog、Agent、MCP连接或Web服务。读取损坏目标库不是获得维护入口的前提。不注册可供模型调用的备份/还原工具或HTTP端点。

显式session-db足以定位时不因无关设置失败而阻断维护；默认定位只消费既有storage段，不验证网络/MCP/权限配置。若必须依赖storage段却无法可靠读取，不猜测默认data_dir去操作另一个库，应要求修正设置或显式session-db。复用现有配置文件位置，不写配置。

成功返回0；业务/IO/验证失败返回1；参数错误返回2；用户拒绝或无交互输入取消返回1且不修改目标；Ctrl-C返回130。首期无跳过确认的参数。列举与校验可在服务运行时进行，restore必须取得同一实例锁。

### 3.2 文件位置

所有位置从选定数据库路径派生，支持默认库及自定义文件名，不假设固定用户目录：

| 相对数据库所在目录的位置 | 所有者与用途 |
| --- | --- |
| `<数据库名>.lock` | 现有应用实例锁；保持文件和语义，不随还原移动/删除 |
| `<数据库名>.backups/<backup_id>/database.sqlite` | 已发布、关闭全部连接的独立SQLite快照 |
| `<数据库名>.backups/<backup_id>/manifest.json` | 备份事实；与数据库文件作为一个目录发布 |
| `<数据库名>.backups/.pending-<backup_id>/` | 未完成备份；永不当作可用备份，不自动删除未知现场 |
| `<数据库名>.maintenance/restore.json` | 当前未完成还原的有效阻断标记 |
| `<数据库名>.maintenance/<operation_id>/candidate.sqlite` | 已验证、准备安装的独立候选库 |
| `<数据库名>.maintenance/<operation_id>/original/` | 按原名称保存还原前主库、wal/shm/journal；说明见operation.json，不是已验证备份 |
| `<数据库名>.maintenance/<operation_id>/operation.json` | 完成后保留操作记录，供查证；不控制普通启动 |

backup_id/operation_id均为本地生成的32位小写十六进制UUID；命令只接受该格式，以目标库自己的备份目录解析，不把ID当路径。不得接受路径分隔符、`..`或外部目录。父目录、文件及临时产物均检查普通类型、链接和重解析点，沿用既有边界。

同盘本地备份用于升级/误操作保护，不承诺整盘故障后的异地恢复。只在实际迁移尝试前生成一份新副本；再次启动仍需迁移时可以生成另一份，不覆盖或暗中复用旧副本，不在普通无迁移启动中备份。

### 3.3 备份说明格式1

| 字段 | 生产者/类型/消费约定 |
| --- | --- |
| format_version | 备份生成器写整数1；未知格式拒绝使用 |
| backup_id | 生成器写32位hex；目录名和字段必须一致 |
| created_at | 发布前写UTC ISO8601时间；仅排序/展示，不证明完成或有效 |
| reason | 首期只接受`migration` |
| creator_version | 当前应用包版本；是生成副本的版本，不虚构升级前安装版本 |
| source_schema / target_schema | 备份前读取的应用库版本、当前代码声明的迁移目标；实际文件仍是source_schema |
| workspace_id | 原库绑定的UUID或null，不由当前目录推断 |
| database_name | 原库文件名，单一路径分量；本目录还原要求与目标库名一致 |
| checkpoint_versions | 创建时实际依赖的langgraph、langgraph-checkpoint、langgraph-checkpoint-sqlite版本；用于诊断，不据此自动证明兼容 |
| database_bytes / sha256 | 完成并关闭快照后，对database.sqlite取得的真实字节数和64位小写hex校验值 |

说明文件严格解析字段、类型和有限长度；限制读取64KiB，拒绝重复JSON键。数据库用流式计算hash，不整文件载入内存。校验值只发现内容变化，不提供来源认证；使用前仍须检查结构、路径、归属和支持范围。

## 4. 实例锁、定位与启动保护

01完成只定位入口，02完成普通启动的两次持锁门禁；03已接还原操作，复用这些门禁与同一实例锁。

维护与普通启动必须复用一个数据库实例锁，不存在单独的“维护锁”与应用锁互不相识的情况。首期不支持在线替换或同库多应用写入。

1. 拆出既有定位步骤，返回WorkspaceLocation：规范化root、读已有UUID、选择database_path；维护路径不查询坏库，不写入新UUID。
2. 普通prepare_workspace需要读取绑定时，按既有workspace锁→数据库实例锁顺序进行短验证；取得实例锁后先检查restore.json，再打开库。不得在持有实例锁时反向等待workspace锁。
3. 短验证释放后，SessionStorage重新取得并长期持有实例锁；再次检查restore.json，然后才允许检查不存在/创建空库/打开SQLite/执行迁移。关闭短验证与真正装配之间的竞态。
4. 直接使用SessionStorage的SDK入口同样必须检查标记。标记存在、格式非法或状态无法确认，都不得推断可以建新库。
5. restore取得实例锁后重新核对目标、工作区标记和备份归属；发布期间一直持锁。正常绑定/移动/副本规则继续执行，不能仅凭UUID相同跳过旧目录冲突检查。

仅约束遵循此协议的应用和维护命令，不声称阻止任意外部进程绕过锁直接改文件。公共函数签名保持，职责拆分通过内部函数完成。

## 5. 迁移前备份与验证

### 5.1 正常调用链

`Web/chat → prepare_workspace → SessionStorage持锁 → 识别已有源版本和迁移计划 → 创建并验证备份 → 原分步迁移 → 绑定工作区/后续装配`。

源版本/绑定由原库生产；副本和说明由备份模块生产；迁移入口消费“已验证备份”事实，不能从目录存在或manifest存在推断完成。

### 5.2 备份过程

1. 在源连接未开启业务写事务、未执行迁移时，用标准库Connection.backup写本次专属pending目录，分批复制，允许Ctrl-C终止。
2. 处理SQLite当前已提交状态，包括尚在WAL中的数据；不直接复制活动主库文件，也不手工删除源wal/journal。SQLite自身必要的日志恢复不等于业务迁移，不能承诺源物理字节完全未变。
3. 关闭目标连接，确保快照可作为无外部日志依赖的独立文件打开；若存在未收敛附属文件，本次产物不发布。
4. 对快照只读执行完整integrity_check、适用时foreign_key_check，并按source_schema检查必要表/列、单一版本行和已有工作区绑定。存在的checkpoint表/writes及元数据引用须符合该版本结构；不要以“无最新checkpoint”拒绝合法未运行/未完成会话。
5. 不反序列化不可信checkpoint BLOB，不运行模型、工具或恢复操作；备份有效只证明文件及存储结构可用，运行可恢复性由后续正式会话恢复判断。
6. 关闭检查连接、记录真实字节/hash并写manifest；flush/fsync必要文件，然后在同一文件系统发布整个备份目录，完成前不进入可用列表。
7. 发布/验证失败记录安全阶段诊断，停止迁移；不把pending目录算成功。已发布副本之后也要在restore使用前重验。

新建库不备份；已有当前版本库无迁移时不备份；过旧/未来/未知版本在写入前拒绝。当前支持源schema4—13，目标13；只对源<目标的已有库触发。将来框架持久格式发生变化，必须显式扩展迁移计划和兼容测试，不能靠包版本变化猜测触发。

### 5.3 失败与成本

备份时空间不足、权限、占用、取消、校验失败都不得进入迁移。空间预检只是提示，写入本身仍须处理失败；不为腾空间自动删备份、现场或用户数据。

完整快照与完整性/hash校验随库大小增长；首期在迁移前离线完成，明确阶段，不承诺大库瞬时启动。保留一份完整副本需要额外空间，无固定数据库容量上限或伪造进度。

## 6. 分步迁移与失败现场

复用现有迁移函数，单一目标版本/支持范围由存储入口声明；各步结构修改与该步版本更新处于同一事务。无需新增迁移记录表或业务schema字段，也不为备份功能单独提高应用库版本。

| 阶段 | 行为/异常/状态 |
| --- | --- |
| 源版本/结构不可识别 | 保留原库及附属文件，报unsupported_store_schema或有限结构错误；不尝试新建空库替代 |
| 备份失败 | 迁移调用次数为0；安全诊断和现有SessionPersistenceError边界可见 |
| 第N步失败 | 该步事务回滚，后续步不运行；前面已提交版本保留，原始备份保留 |
| 进程退出 | 下次从实际已提交版本重新检查；不根据文件mtime或一次开始日志推断升级完成 |
| 迁移成功 | 关闭迁移边界后继续原装配；失败的模型配置/MCP/后续setup不能伪称迁移失败或自动还原数据 |

迁移失败不自动覆盖原库、不执行降级DDL、不隐藏已提交前序步骤；终端显示失败阶段、可读到的当前/目标版本、已发布备份ID和维护指引。清理失败不得覆盖主异常，安全记录次要异常。

SQLite可读不等于任务可执行。running、failed、pending等状态不因备份验证或还原被修改；temporary权限记忆、旧MCP审批及未确认副作用沿用原恢复合同。

## 7. 显式还原与中断恢复

### 7.1 正常路径

`storage restore → 只定位已有工作区/目标 → 取得实例锁 → 验证备份/归属 → 展示与确认 → 重验 → 准备候选 → 持久阻断标记 → 保留当前文件组 → 安装候选 → 验证并完成 → 释放锁`。

确认前不写目标库、创建还原标记或移动原文件。目标库可不存在或损坏；有效工作区标记、同目标备份来源和明确用户选择仍是前提。没有有效备份时保留现场并失败，不自动执行SQLite .recover。

验证同时核对备份ID/目录、普通文件、大小/hash、source_schema/目标代码支持范围、必要结构、源绑定与当前工作区身份、数据库名及旧目录移动冲突。workspace_id=null的备份返回backup_unbound；未来格式、外工作区、篡改或不完整备份不得安装。

兼容判断针对快照的source_schema和实际存储结构；target_schema、creator_version及checkpoint_versions是诊断信息。不能仅因备份原本准备迁往更高target_schema就拒绝当前代码仍支持的源快照，也不能仅因版本字符串相同就跳过实际结构验证。

只允许已关闭且不依赖wal/journal的已验证快照作为候选来源；复制到目标目录同一文件系统的operation目录，流式复制后flush/fsync并再次检查大小/hash、完整性、版本和绑定。候选准备失败时当前库未被移动。

### 7.2 必要还原记录

restore.json是当前操作的启动阻断事实，由还原模块在移动任何原文件前写入并持久化。字段固定为：format_version=1、operation_id、backup_id、workspace_id、database_name、source_schema、candidate_bytes、candidate_sha256、phase、original_files。

phase只允许`prepared`或`installed`；完成记录写入operation.json，phase为`completed`后才解除restore.json。original_files枚举创建标记时存在的主库及`-wal`/`-shm`/`-journal`，每项记录固定文件名、字节数和sha256；只接受目标库名的这四种文件，不从JSON接受任意路径。

候选/现场路径由operation_id和固定布局推导，不信任JSON内的绝对路径。当前实例锁文件不在原文件组中。原始现场可以是坏库，必须标成现场，不能加入有效备份列表。

| 操作阶段 | 持久事实 | 再次执行同一restore的动作 |
| --- | --- | --- |
| 候选未完成 | 无restore.json，原库仍在原位 | 新操作重新验证/准备；不使用不完整候选 |
| prepared，未移动或只移动部分原文件 | 标记已落盘；候选及每个原文件预期hash已知 | 逐文件检查：只在活动位置则验证后移入original；只在original则验证为已移动；缺失/冲突/额外文件停止，不猜测或覆盖 |
| 全部原文件已保留，候选尚未安装 | prepared，original组可核对，目标位置无旧文件 | 验证候选，再用同文件系统单文件替换安装主库 |
| 主库已安装但phase更新前退出 | 全部原文件已保留，目标主库匹配candidate hash，旧附属文件不存在 | 识别为已安装，验证后继续收尾，不能把新主库当作待移动的原文件 |
| installed，尚未完成收尾 | 目标和original均可核对，标记仍阻断 | 不再次安装；复核后写completed记录并解除标记 |
| completed | operation.json已持久化；restore.json不存在 | 普通启动可进入原流程；重新运行restore会成为需重新确认的新操作 |

每次移动都使用新建专属original目录，不覆盖已有目标；所有打开的SQLite连接/文件句柄在替换前关闭，Windows文件占用失败保留标记和现场。原文件移动与日志阶段写入不是一个跨文件原子事务，恢复必须以文件存在性、已记录身份和hash核对，不能只相信phase字符串。

中断后普通Web、chat和SDK启动都只阻断并指引，不自动续做还原。用户再次执行相同backup_id并确认后，使用原operation_id收尾；不同backup_id、标记损坏或现场冲突拒绝。已准备的候选/已安装主库可按标记核对，不要求原备份文件在安装后仍存在才能结束收尾。

完成前不得删除original或备份。标记写入/解除和完成记录采用临时文件提交及必要同步；process-crash断点是首期可验证承诺，不把本机测试等同所有文件系统/硬件的断电保证。

### 7.3 完成含义

还原成功表示指定快照已安装并通过存储验证，不代表Agent已恢复、工具结果已核对或程序已降级。命令不打开SessionStorage自动迁移、不创建官方saver、不调用恢复图、不自动发起run。

随后由用户启动兼容版本/修复版，沿现有工作区绑定和会话准备逻辑处理旧状态；同一有缺陷版本再次启动仍可能重试迁移。说明这一点，不许把文件还原说成修复了迁移代码。

## 8. 异常、观察与安全边界

优先使用现有SessionPersistenceError及ValueError边界，增加有限错误码：backup_failed、backup_invalid、backup_unbound、migration_failed、restore_incomplete、restore_conflict、restore_failed；保留storage_in_use、unsupported_store_schema、workspace_database_mismatch等原码。原IO/SQLite异常作为cause，不把内容拼接进日志。

CLI直接输出用户选定目标和本地备份位置、阶段/有限原因、下一操作；日志仅阶段、版本、操作ID、错误类型，不输出消息正文、SQL参数、凭证、URL/token或checkpoint BLOB。列表损坏条目不能无声丢弃。

Web lifespan已保存ModelConfigError及SessionPersistenceError原异常供终端解释；存储异常向Uvicorn仅抛固定storage_startup_failed且抑制原cause显示，SDK和app.state保留原异常。Web终端同时处理SystemExit与未started的正常返回，Web/chat共享有限原因、阶段、可确认版本和备份ID。普通运行异常合同未重写。

没有真实配置写入、依赖升级或权限策略变化；备份和现场的文件保护复用当前规则，不恢复已被用户取消的私密模式位启动门槛。维护入口不加载外部插件或执行备份自带脚本/SQL。hash和UUID都不单独构成授权或可恢复性证明。

## 9. 文件边界与切片

本功能预计涉及超过8个文件，含生产/故障测试/发行文档；单文件500行限制保持，`application/cli.py`当前恰为500行，新增维护命令放顶层独立入口。

| 文件/目录 | 变更职责与实际消费者 |
| --- | --- |
| `storage_files.py` | 扩展必要文件/锁/同步操作，消费者是普通存储与维护；不修改既有API签名 |
| 新`storage_backup.py`、`storage_backup_schema.py` | 快照生成/格式1验证/列表与固定版本SQL结构校验；01由列表CLI/安装验收消费，02迁移门禁和03还原接入 |
| 新`storage_restore.py`、`storage_restore_record.py` | 执行器与严格记录/文件核对；候选、现场、标记、安装和重复命令收尾只由显式维护入口驱动，启动只读阻断事实 |
| 新`storage_cli.py`、既有`cli.py` | storage分发、定位、展示/确认/退出码；不进入应用装配 |
| `workspaces.py` | 复用定位/绑定验证，短验证与替换协调，保持移动/副本身份 |
| `settings.py` / `config_paths.py` | 复用原设置位置与storage校验供只定位入口使用；无关配置不能要求维护命令建立客户端 |
| `application/session_storage.py` | 持锁标记门禁、源/目标版本判断、备份后原迁移、SDK入口一致性 |
| 新`storage_maintenance.py`、`application/storage_migrations.py`、`application/startup_display.py` | 只读维护门禁、迁移执行与失败事实、终端展示分别负责；工作区/存储及Web/chat实际消费 |
| 现有迁移模块 | 只按实际需要同步目标版本来源/校验，保留原事务和行为，不借机统一全部存储职责 |
| `web/app.py`、`web/__main__.py`、`application/cli.py`及既有展示辅助 | 存储启动异常接线；500行入口仅调现有/新增有限展示函数 |
| 新专项测试与现有迁移/工作区/启动测试 | 故障、归属、WAL、跨进程锁、中断和原业务合同 |
| README、发行验证脚本/说明、future-work、handoff | 命令实现后更新使用文档；00不提前把未实现命令写成现有能力 |

新机制各有当前消费者；备份事实、还原状态与会话恢复状态分开。迁移门禁只在已知需要迁移时调用备份，不做无用途包装或重复解析。

| 切片 | 内容 | 验收/当前状态 |
| --- | --- | --- |
| 123-00 | 本Spec：范围、格式、失败/还原状态与切片合同 | 本轮完成设计文档核查，不代表产品实现 |
| 123-01 | 备份生成/验证/列表及storage backups，已有工作区只定位入口 | 已完成本机验收；73专项、3841全量、395前端及Mac ARM64源外安装，见第14节 |
| 123-02 | 普通启动迁移前备份、两次持锁门禁、迁移失败和终端信息 | 已完成本机验收；43专项、3884后端、395前端及安装后自动备份/迁移，见第16节 |
| 123-03 | 显式restore、确认、候选/现场/标记和重复命令收尾 | 已完成本机验收；75新增、3959后端/395前端、构建及源外真实还原；见第18节 |
| 123-04 | 旧库升级/还原重开、标准安装、四平台交付验证 | 本机组合/安装接线完成：新增13项、3972后端/395前端、四目标构建与Mac源外还原重开；三平台原机及本轮远端CI待验，见第20节 |

02先接完整启动阻断合同，03才发布会写restore.json的操作，避免先交付会生成标记但启动不认识标记的版本。每片TDD先确认预期红灯，再最小实现及回归；00是文档，不伪造红灯。

## 10. 验证合同

### 10.1 确定性专项

- 已提交但尚在WAL的数据进入快照；副本独立只读打开；source_schema与源业务行保持；备份函数从不调用SessionStorage触发迁移。
- pending目录、说明缺失、重复键、超长说明、未来格式、错误大小/hash、链接/重解析点、外部路径、跨库名和跨工作区均明确拒绝；列表保留错误事实。
- schema4—12升级至13；当前13和全新库不额外备份；未来/不支持版本不写；未绑定SDK库备份不伪造UUID。
- 注入备份/发布失败，迁移调用为0；注入中间迁移失败，仅该步回滚，前步版本及基线副本存在；重新打开只运行剩余步。
- 普通定位短验证与restore、SessionStorage装配间竞态；跨进程占用同一实例锁；标记存在/损坏且主库缺失时不建空库。
- restore拒绝/EOF/取消无目标改写；损坏当前库也能定位；候选失败原库不移动；包含wal/shm/journal的现场完整保留。
- 在标记落盘、每个原文件移动后、候选安装后但phase更新前、installed后、completed写入后但标记解除前真实结束子进程；重复命令核对并收尾，不覆盖证据。
- 错backup_id、冲突文件、篡改标记/候选停止；源备份在安装后缺失仍能按已记录候选hash完成收尾；文件组不混配。
- 还原后的正常/待审/中断/根子历史、用量和未知值可读；还原过程模型、MCP业务、工具执行、Auto审查和run启动次数均为0；不将未完成任务改ready。

使用事件/管道同步跨进程阶段，不依赖50ms等紧时间窗；观察断言在测试末显式检查，不能被生产异常隔离吞掉。失败测试不得靠放宽断言或skip转绿。

### 10.2 完整与安装验证

产品实现后运行现有后端全量、前端测试及TS/Vite构建、Python语法/500行、pip check、diff检查。常用命令沿用项目：

- `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short`（该本机既有原生资源存在时；其他环境使用其正式rg/fd）
- `npm --prefix frontend test -- --run`
- `npm --prefix frontend run build`
- `.venv/bin/python -m pip check`
- `git diff --check`

04扩展既有`scripts.verify_wheel_install`，不造平行安装框架：源码外独立环境/临时工作区，安装包命令可达；旧库→备份→迁移→模拟失败→显式还原→兼容版本重开，核对实际DB/会话/主子状态。模型配置不可用时storage仍可用；真实模型供应商请求为0。

Mac ARM64、Linux x86_64/ARM64和Windows x64分别执行门禁；本机测试不能代替其他原机。提交/push/发布、真实用户库还原须另有对应授权，本片设计和后续自动测试只用独立临时目录。

## 11. 参考与证据强度

- [Pi会话实现，a69bef7](https://github.com/earendil-works/pi/blob/a69bef789bc95abf0acee16f7b4660b70b650bb9/packages/coding-agent/src/core/session-manager.ts)：`_setSessionFile → migrateToCurrentVersion → _rewriteFile`，JSONL追加/读时迁移；只作版本与无效输入保留参照，不据此认定Pi有整库备份或安全跳过任意坏消息。
- [AgentX消息领域服务，3fcfa0e](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f/AgentX/src/main/java/org/xhy/domain/conversation/service/MessageDomainService.java)与[部署说明](https://github.com/lucky-aeon/AgentX/blob/3fcfa0e5c224fc64ca63aa60fd871e5d2f25159f/deploy/README.md)：消息/上下文进入PostgreSQL，文档提供pg_dump/psql备份恢复；不是本机验证了自动灾备或整体事务保证。
- [OpenCode迁移，4d59657](https://github.com/anomalyco/opencode/blob/4d5965762ec5e0d1a0b14630553fc62b51a11fba/packages/core/src/database/migration.ts)及[装配入口](https://github.com/anomalyco/opencode/blob/4d5965762ec5e0d1a0b14630553fc62b51a11fba/packages/core/src/database/database.ts)：Database layer持启动锁后apply，每步结构与完成记录同事务；借鉴步骤/失败合同，不照搬Effect/Drizzle或认为这是自动备份证据。
- [Python3.12 backup](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)、[SQLite backup](https://sqlite.org/backup.html)、[完整性检查](https://sqlite.org/pragma.html#pragma_integrity_check)、[文件与journal错配风险](https://sqlite.org/howtocorrupt.html)、[损坏抢救限制](https://sqlite.org/recovery.html)：底层方法与限制，不替代本项目接线/跨平台故障测试。

上述为固定源码/本机安装源码与官方资料核查。123-00未运行备份/迁移/还原探针，未修改真实配置或打开真实数据库；新机制的存在、真实实现与实际接线须由01—04分别提供证据。

## 12. 123-00收尾

本轮产物仅本Spec及future-work、handoff的当前状态同步。证据目录`build/spec123-00-nm7qmo21/`；验证命令：`.venv/bin/python build/spec123-00-nm7qmo21/verify_docs.py`。

实际结果：12节结构、5个本地文件链接、00—04共5行分片状态通过；生产源码/前端源码/测试/脚本661份文件的修改前后SHA-256映射完全一致，`git diff --check`通过。验证结果保存于`verification.json`，不把这些文档检查报告为备份/迁移/还原功能测试或应用构建通过。

未打开真实数据库，未运行新功能探针、产品测试或应用构建；没有真实配置/环境文件/依赖/版本修改，没有提交、push或发布。已有Spec121/122和并行学习改动保留；模型重试治理继续暂缓。

00完成时下一片为123-01；01/02合同与结果见下文。当前无新的存储开发切片；三平台原机与本轮远端CI待验，见第20节；01收尾时没有接自动迁移或还原。

## 13. 123-01实现合同

本节为01阶段合同，02接线后的当前入口以第15—16节为准。

- `storage_backup.create_backup(connection, database_path, *, target_schema)`：内部同步入口，调用方已持有数据库实例锁，连接指向所选主库且不在事务中。01不新增调用方锁协议；02将从SessionStorage持锁、迁移前位置消费。返回已发布manifest，不创建SessionStorage或执行迁移。
- `validate_backup(database_path, backup_id, *, workspace_id=None)`返回严格验证后的manifest；显式workspace_id时核对归属，未指定时允许未绑定SDK快照。`list_backups(database_path, *, workspace_id=None)`返回有限字典列表，status为valid/invalid/incomplete，错误码固定backup_invalid；单项损坏不丢弃，根目录不可安全读取则命令失败。
- `storage_backup_schema`集中声明源4—13版本的必要表/列、完整性和绑定校验，供生成和只读验证共同调用；SQL结构合同与文件发布/格式校验分开，不反序列化BLOB。检查当前session和chat_sync的checkpoint引用；不把已修剪的历史父节点/归档来源作为必须存在的执行锚点。框架表可都不存在，存在时必须成对且字段完整，pending writes和未完成状态保持原样。
- `workspaces.locate_workspace`只读已有标记并返回WorkspaceLocation，不打开目标库，不创建目录/锁/身份；`config_paths.load_storage_settings`只解析既有settings.toml的storage段。显式session-db跳过设置文件读取。
- 顶层storage只注册backups；输出JSON，包含选定目标与各条manifest/status/error。无工作区/无备份是成功空结果；未知条目显示有限、转义后的名字及invalid状态。合法条目按created_at降序再按ID排序，无可用时间留空。restore命令03再加入。
- 快照先收敛为DELETE日志模式并关闭，检查没有附属日志后才以`mode=ro&immutable=1`验证，避免列表创建shm。文件通过只读非继承句柄检查普通单链接及身份，目录逐层拒绝符号链接/重解析点；不新增私密权限位门槛。文件与manifest同步后同盘重命名发布，失败pending保留且不可使用；目录fsync只在支持该操作的平台执行，不声称跨平台断电保证。
- 本片测试共用专项fixture构造真实SQLite及4—13旧结构，仅服务专项测试，不修改conftest/全局helpers。此项是测试数据准备，不是产品迁移实现。
- 01在既有`scripts.release_smoke`追加已安装命令的列表验收：从真实持久化假模型场景生成备份后损坏临时源库，使用坏模型/设置文件和显式session-db列出同一副本，确认目标字节不变；04仍负责完整升级/失败/还原与四平台验证。

正常链：`nyt → storage_cli → storage段/已有标记定位 → list_backups → 格式/文件/SQLite只读校验 → JSON`。生成链：调用方持锁的源连接→pending独立快照→结构/哈希→manifest→发布。失败链：损坏/缺失当前库仍可定位，备份坏条目返回invalid并记录错误类型；生成IO/校验失败保留pending、抛backup_failed，不迁移源库。

## 14. 123-01收尾结果与当时入口

2026-10-21完成；证据目录`build/spec123-01-195vpiyx/`。01收尾时没有自动迁移调用点或restore命令，当时生成器由专项测试和安装验收调用；02接入后的当前主链见第16节。没有增加依赖或业务schema版本。

### 14.1 可检查的主链

- `cli.main(['storage','backups',...]) → storage_cli.main`：显式session-db直接跳过设置；否则`load_storage_settings → get_data_dir`只取原storage段。
- `locate_workspace`读取已有标记，返回`WorkspaceLocation(workspace_id, root, database_path)`，三项分别来自标记、已存在根目录和用户路径/原data_dir规则；不创建身份，不打开目标库。
- `list_backups → validate_backup → check_snapshot`只读备份自己的说明/快照。说明是固定字段dict，大小/hash对应快照字节，版本/UUID对应快照SQL事实；结果为`{backup_id,status,manifest}`或有限错误状态，再由CLI序列化JSON。缺失身份返回三个空字段/数组，不编造备份。
- 生成的确定性输入为临时`custom.sqlite`、schema4—13、session S/thread T、running/R、checkpoint C及pending writes；WAL中已提交的model_id修改必须进入副本。`create_backup`返回格式1dict，保留原source_schema、UUID/null和真实依赖版本；源业务行和版本不被迁移/改成ready，checkpoint及writes BLOB不反序列化。
- 故障注入覆盖manifest/发布/校验IO失败、取消、关闭连接再次失败；原异常或KeyboardInterrupt身份保留，pending不进入有效列表。坏JSON、hash/结构/归属、源锚点缺失、FK违规与危险链接明确拒绝；一个坏条目不遮蔽其他健康副本。

### 14.2 验证与证据限度

| 命令 | 本轮实际结果/证据 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_storage_backup.py tests/test_storage_backup_validation.py tests/test_storage_backup_files.py tests/test_storage_cli.py -q --tb=short` | **73 passed in1.34s**，`specialized-final.log`；初始56项因新入口不存在红灯，刷盘/清理2项及深层JSON另有红灯记录 |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3841 passed in249.89s**，`backend-final2.log`；此前3839为补齐最后2项前的结果 |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | **49文件395 passed**，TS/Vite构建退出0，既有大chunk提示保留，`frontend-final.log` / `frontend-build.log` |
| `python -m compileall -q naiyue_test_agent scripts`及AST/500行核查 | 169份产品Python、38份scripts；产品最大500、脚本最大350，`verification.json` |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec123-01-195vpiyx/wheel-final` | 标准wheel构建退出0；产品源码/前端包内字节逐项对应当前文件，`wheel-final.log` / `verification.json` |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec123-01-195vpiyx/wheel-final --output build/spec123-01-195vpiyx/install-final3.json` | 独立venv/源码外真实安装通过，含`storage_backups=passed`及既有Web/CLI/持久/审批/MCP验收，真实供应商请求0 |
| `.venv/bin/python -m pytest tests/test_release_artifacts.py tests/test_release_workflows.py tests/test_release_mcp_smoke.py -q --tb=short` | 安装fixture完善后**50 passed in8.41s**，`release-tests-final.log`；相邻文件/工作区/入口29项亦通过 |
| `python -m twine check build/spec123-01-195vpiyx/wheel-final/*.whl` / `python -m pip check` / `git diff --check` | PASSED / No broken requirements found / 退出0 |

安装验收最初直接走SDK，缺工作区标记，列表按合同为空；第二次试图为已有非空未绑定库补绑定，被既有workspace_database_mismatch保护拒绝。已用最小探针确认，最终改为创建第一个会话前`prepare_workspace`，并把身份传给WebSettings，保留原断言。另有一条新补测曾错误要求4KiB内标记产生RecursionError，本机实际是结构TypeError；经解析器探针核实后改为准确诊断断言，业务退出码/错误对象断言未放宽。失败日志保留，未将这些验收fixture问题作为产品缺陷修改保护。

架构/安全复核无剩余阻断；发现的深层JSON穿透已红绿验证。读取与文件替换遵循现有本地单应用边界，不声称抵御任意外部进程绕过协议改文件；hash/UUID不单独证明授权或运行可恢复性。Windows有句柄/锁分支注入测试，没有Windows/Linux原机实测；本机安装不能替代04四平台验收。

01未打开真实用户库、修改真实配置/环境/依赖/版本、提交/push或发布。对照01开始时667份已存在源码/前端/测试/脚本哈希，仅5份预期文件改变，其他662份保持；Spec121/122/124和并行课堂成果保留，不推定用户掌握。01收尾时下一片123-02，本轮已完成；当时下一片03还原，现已完成；04本机结果见第20节，跨平台原机待验。

## 15. 123-02实现合同

- 复用`storage_backup_schema`集中声明CURRENT_SCHEMA=13、SUPPORTED_SCHEMAS和`read_schema_version(connection)`，由生成器、快照校验和启动迁移共同消费。业务schema不升级，框架依赖不升级。
- 既有SessionStorage内的新库DDL、4/5→6及chat→7迁移搬入`application/storage_migrations.py`，与原chat_queries/usage/diagnostic/reply/duration迁移按原顺序调用。删除被替代的旧内部方法，不保留并行入口。新库与当前13不备份；当前版本的恢复整理仍执行，不把它误判成迁移。
- `initialize_store(connection, database_path, *, existing)`只由已持有实例锁的SessionStorage调用。已有源<13先调用01生成器，成功发布后再开始任何业务迁移；失败不调用迁移。每个实际版本步骤保留原事务，失败时必要rollback，记录可确认的实际版本，停止后续步骤；不回滚前序已提交步骤。新库初始化失败或无迁移的普通整理失败保持原storage_unavailable边界。
- 步骤准备时读取版本也是该步骤边界的一部分；备份成功后读版本失败仍携带源版本、目标、步骤和备份ID。失败后的诊断读取也失败时current_schema=null，不退回普通打开失败或用源版本代替当前值。
- 在既有SessionPersistenceError下新增结构化子类StorageStartupError，不改变现有构造签名。code仅unsupported_store_schema/backup_failed/migration_failed/restore_incomplete；随异常传递database_path、phase、source_schema/current_schema/target_schema、backup_id。未知字段为null，不猜测；记录原cause。中止与清理失败不得覆盖主异常，SessionStorage对所有退出路径关闭本次资源。
- 新`storage_maintenance.check_restore_marker(database_path)`只读存在性/路径安全；维护目录或restore.json不可信、标记存在均阻断，不解析或续做还原。root模块不导入application或模型配置。SessionStorage持实例锁后、检查/创建主库前调用；已有workspace的短绑定读取也先取得同一实例锁、检查标记再打开SQLite。短读取结束释放锁，长持有再次检查，覆盖中间竞态。
- 新库父目录不存在时，prepare_workspace不建数据目录/实例锁也不读库；真正SessionStorage创建父目录后再持锁检查。create=False维持既有无workspace.lock写入行为，读取已有库只取数据库锁；有workspace.lock时保持workspace→数据库顺序，数据库持有者不反向等待workspace锁。
- `application/startup_display`供Web/chat共享有限详细信息与既有配置/冲突提示；CLI500行入口只调用展示函数。Web lifespan保存SessionPersistenceError原异常，终端处理Uvicorn SystemExit和正常返回但未started两条失败路径。SDK仍收到SessionPersistenceError子类，不加载模型/MCP/saver后续装配；迁移成功后的模型配置失败仍是ModelConfigError，不自动还原。
- Web向Uvicorn传播固定的storage_startup_failed异常并抑制cause显示，防止框架自动traceback泄露原IO错误正文；原始存储异常仍保存在app.state.startup_error供终端安全展示。SDK原始cause和异常身份保持，不把Web传输异常当作根因；模型配置分支保持原合同。
- 既有迁移失败测试将通用storage_unavailable更新为已确认的新migration_failed，保留事务/版本/行数据断言并核对原快照。这是错误合同迁移，不放宽验证。专项fixture复用01的真实旧结构；只用临时目录，真实用户库/设置不变。
- 标准release_smoke把其真实持久化假模型场景库准备为schema12，调用安装后的SessionStorage启动自动备份并迁至13，核对唯一source12副本；再沿01损坏目标/坏配置的命令查询验收。此项扩展既有安装门禁，不另造安装框架，完整还原和四平台证明仍归04。

正常例：schema6→实例锁/无维护标记→验证并发布source6快照→原步骤7/8/9/10/11/12/13→原绑定及模型/MCP装配。失败例：8步骤失败时当前已提交7、源6备份仍可查，8本步回滚、9之后与模型加载次数0；再次启动从实际7生成新备份后继续。已有13的恢复整理失败不会显示migration_failed。维护标记与主库都缺失/损坏的组合以标记先阻断，不新建空库。

## 16. 123-02当前主链与实际结果

2026-10-21完成本机验收，证据`build/spec123-02-hiw53wuv/`。没有增加业务schema或依赖版本，也没有调用显式还原操作。

### 16.1 执行、失败与展示分工

1. `prepare_workspace → _read_binding`：已有父目录时短持`<数据库名>.lock`，`check_restore_marker`先于SQLite打开。绑定dict的UUID/root仍由目标库产生，原移动/副本判断消费；短读取释放全部句柄/锁。没有可用父目录时不写数据目录，保持原定位和随后SDK失败合同，不回退其他路径。
2. `SessionStorage.__init__`再次长持同一实例锁，在主库存在性检查/创建前检查维护标记。源连接由该实例持有，`initialize_store(connection,path,existing=...)`消费，不新建第二套锁协议。
3. `initialize_store → read_schema_version`得到实际源版本；已有4—12调用`create_backup`取得格式1manifest，只有成功返回后才开始原事务步骤。manifest的backup_id来自已发布目录，source_schema对应快照；新增库、已有13不生成副本。当前版本的usage恢复整理仍执行。
4. 固定步骤仍是6/7/8/9/10/11/12/13。源6、步骤8失败的测试里，源快照仍是6，目标库实际已7，步骤8的临时DDL回滚，后续步骤和模型加载均未执行。再次启动从实际7生成另一份备份后迁至13；不覆写source6副本。
5. `_failure`产生`StorageStartupError(SessionPersistenceError)`，数据形状为`code + database_path + phase + source_schema/current_schema/target_schema + backup_id`。code是有限原因，版本来自SQL读取，未知当前版本为None；异常保留原cause。回滚/连接关闭/诊断读取的次要失败只记录安全诊断，不覆盖主因。步骤前读版本失败同样保留已知事实。
6. SDK拿到原存储异常；`open_async_persistent_application`在取得存储失败时不进入模型、MCP资源或saver装配。迁移完成后的ModelConfigError保持原身份，库继续是13，原source12备份保留，不自动还原。
7. Web lifespan把原异常放入`app.state.startup_error`，向框架抛固定storage_startup_failed并抑制cause显示。终端在Uvicorn两种失败退出方式中取回原异常，Web/chat只输出有限原因、阶段、可确认版本、目标及备份ID；日志不输出底层错误正文。结构化启动事实不新增DB列或运行事件，普通会话运行错误边界保持原样。

移除：SessionStorage内部的旧4/5→6分支、旧`_migrate_chat_schema`方法和旧初始化序列，已搬到实际消费的storage_migrations，没有兼容转发。保留：原各存储模块初始化函数及事务，SessionStorage读写使用的chat_queries/usage/diagnostic/reply导入，当前13恢复整理，以及原async存储获取与清理合同。

### 16.2 验证命令与结果

| 命令 | 本轮实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_storage_migrations.py tests/test_storage_startup_guards.py tests/test_storage_startup_entries.py -q --tb=short` | **43 passed in2.09s**，specialized-final.log；初始31红/1原行为通过，入口3红/2通过，清理、维护访问及步骤前读版本分别有红灯记录 |
| 旧库/备份/工作区/CLI/Web/用量等17文件定向回归 | **205 passed in9.12s**，focused-final.log；最后读版本发现随后另补专项并纳入最终全量 |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3884 passed in257.24s**，backend-final2.log；上一轮3883在最后1项加入前，保留backend-final.log |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | **49文件395 passed**，TS/Vite构建退出0，既有chunk体积提示保留；frontend-final.log / frontend-build.log |
| `.venv/bin/python build/spec123-02-hiw53wuv/verify_structure.py`及`python -m compileall -q naiyue_test_agent scripts` | **172产品Python＋38脚本**AST/500行与编译通过；675份基线中仅12份预期源码/测试/脚本变化，其他663份SHA不变；verification.json |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec123-02-hiw53wuv/wheel` | 标准wheel构建退出0；包内源码/前端/README逐字节对应当前文件，wheel-build.log |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec123-02-hiw53wuv/wheel --output build/spec123-02-hiw53wuv/install.json` | 源码外独立venv真实安装通过；migration_backup/storage_backups均passed，schema12启动→唯一source12副本→目标13→坏目标/坏配置下仍可查询，provider_requests=0 |
| `.venv/bin/python -m pytest tests/test_release_artifacts.py tests/test_release_workflows.py tests/test_release_mcp_smoke.py -q --tb=short` | **50 passed in7.85s**，release-tests.log |
| `.venv/bin/python -m twine check build/spec123-02-hiw53wuv/wheel/*.whl` / `python -m pip check` / `git diff --check` | PASSED / No broken requirements found / 退出0 |

架构复核发现“步骤前读版本失败丢失备份/阶段”，已先红灯复现、修复并由复核者独立跑43项确认关闭；安全复核无剩余阻断。旧2项迁移测试仅迁移错误合同并增加源快照校验，原事务/版本/行数据断言保留；专项closed_store helper是复用真实旧结构的测试准备，不修改conftest或全局运行环境。

### 16.3 限制与下一片

Windows锁/句柄及标记分支有注入测试，跨进程实例锁在本机验证；没有Windows/Linux原机或断电验证。直接备份抛中断已测迁移不进入且锁释放；异步获取仍沿既有acquire_blocking/await_settled等待构造结束后清理的合同，不强行终止后台线程中的SQLite操作，不把此直接中断测试等同任意时刻立即取消迁移。

当前没有生成restore.json的产品操作；这里只读门禁，任何标记都阻断，不自动续做、不删除现场。没有打开真实用户数据库、修改真实配置/.env/依赖/版本或提交/push/发布；Spec121/122/124与课堂成果保留，不推定用户掌握。

02收尾时唯一下一片为123-03（显式还原与中断收尾）；本轮已按以下合同完成，结果见第18节。所有开发验证只用临时库，没有执行真实用户还原。

## 17. 123-03实现合同（实现前）

沿用第7节，不新增还原设置、HTTP或模型工具。`storage_cli`负责参数、有限JSON结果和交互；`storage_restore.restore_backup(location, backup_id, *, confirm)`负责持同一实例锁的完整操作。confirm的唯一生产调用方是CLI：收到已验证的预览事实后显示目标和备份信息并读取完整ID；返回字符串必须精确相等，不strip、不接受布尔确认。直接提前传确认字符串不能覆盖“展示—等待人类—执行”期间持续持锁的需要，因此使用此同步交互回调，不建设通用回调平台。拒绝/EOF为restore_cancelled，Ctrl-C透传至CLI返回130；确认前只允许按既有协议打开或创建同一实例锁文件，不替换/删除锁文件，也不创建maintenance目录。

`storage_restore_record`负责有限记录解析、固定路径派生、文件hash/身份及候选结构/归属检查；执行器不通过SessionStorage打开或迁移目标。original_files明确为数组，每项严格为`{filename, bytes, sha256}`，仅可出现目标主库及三种固定附属文件名，去重；bytes允许0，candidate_bytes必须为正，所有整数拒绝bool、上限2^63−1。restore.json仅prepared/installed，operation.json使用相同字段但phase=completed；JSON限制64KiB、拒绝重复键/额外字段/递归/非法ID或UUID。所有路径从已选目标及operation_id推导，不接受记录中的路径。

新操作先验证副本和工作区移动规则，再交互确认；确认后重新定位、重验副本，流式复制到独占创建的候选文件并fsync/关闭、重验。枚举并hash原文件组后，原子写入prepared标记并同步目录，之后才移动任何原文件。每次移动前检查全部位置/已存在的original清单；不覆盖同名文件。候选以同文件系统rename安装，前后所有文件和SQLite句柄关闭；installed标记、completed记录按序持久化，最后删除唯一活动标记并同步。保留原文件组、备份、操作目录及锁文件。

重复命令先严格检查活动记录并要求同backup_id；用记录中的候选或已安装主库验证归属/结构后再展示确认，不要求原备份仍存在。预览缺失的备份时间/原因不伪造。逐一核对原文件恰在活动/保留位置之一；唯一例外是全部original已保留、候选路径消失且主库匹配candidate事实，判为已安装。候选与已安装主库同时存在、缺文件、额外附属文件、hash/记录不符均restore_conflict。已写completed但未删活动标记时，核对记录一致性及目标/现场再解除。只有阶段串相同不能证明安全。

有限失败代码由CLI白名单输出，原始IO/SQL正文仅保留在异常cause，不进入日志/终端。日志只含阶段、有限ID及error_type；解锁/关闭的次要失败记录且不能覆盖主异常。未知IO/SQL归restore_failed，标记/现场不可信归restore_conflict；无标记不自动消费孤立候选或operation目录。测试使用临时库，覆盖不确认零替换、损坏/缺失目标、占用/归属/链接、原组逐字节保存、各状态真实进程退出与再次确认、记录/文件冲突和IO失败；安装验收新增真实nyt restore接线，不触发模型或自动迁移。完整升级后重开及四平台矩阵留04。

## 18. 123-03当前主链与本机验收

2026-10-21完成；证据目录`build/spec123-03-3gxfri73/`。新增两个产品模块，未新增依赖、业务表、schema或版本。新命令已从顶层CLI接线，启动只保留02的阻断职责，不自动执行还原。

| 调用阶段 | 真实函数与数据来源/消费 | 结果、失败及副作用 |
| --- | --- | --- |
| 显式入口/只定位 | cli.main延迟分发→storage_cli.main→locate_workspace；读取已有工作区标记及明确选择的DB路径，显式session-db跳过设置 | 无标记返回workspace_unavailable，不创建身份或空库；不读取目标内容或装配模型/MCP |
| 实例占有/预览 | restore_backup打开或创建原同名lock并取得非阻塞锁；check_location重读标记；新操作_selection消费validate_backup的manifest | 非本工作区/未绑定/旧目录副本冲突先拒绝；快照读取始终只读immutable，不反序列化BLOB |
| 人类确认 | _confirm展示目标、完整ID、schema、已有时间/原因/程序版本及舍弃范围；回调返回字符串给restore_backup精确比较 | 持锁等待；拒绝/EOF=1，Ctrl-C=130；无maintenance目录/目标改写。resume无时间数据就不显示时间 |
| 准备并阻断 | _prepare确认后再验→独占新operation目录→流式复制candidate→fsync/关闭→verify_snapshot→file_facts枚举当前主库/附属文件→write_record prepared | 候选事实来自备份bytes/hash/source_schema，原文件事实来自各自只读句柄；标记先于任何原文件移动；失败留下孤立候选但目标不动 |
| 保留与安装 | _finish→inspect_files按各文件恰在一处/hash核对→逐文件rename至original并同步→candidate rename为目标→重新验证 | 所有读/SQLite句柄均在rename前关闭；原锁不动；原文件组可以损坏，按字节保留而不伪称有效备份 |
| 收尾/重复命令 | installed标记→重验→completed operation.json→再核对active记录/目标/现场→unlink唯一active标记并同步 | 只有完整original已保留、candidate消失且目标匹配候选时判已安装；源备份丢失仍可收尾；冲突不覆盖/不猜测；无标记则是需重新确认的新操作 |
| 异常边界 | restore_backup有限ValueError白名单，其他IO/SQL/格式错误转restore_failed并保留cause；CLI只输出有限码 | 失败日志只有阶段/error_type，成功记录有限ID/schema；解锁/关闭次要IO异常安全记录，不覆盖主异常；不会启动run或自动迁移 |

正常例子：schema12备份、当前目标损坏且带任意wal/shm/journal→明确确认→四份原字节进入original→独立schema12候选安装→操作记录completed且active标记消失。原running/R、checkpoint opaque BLOB、pending write仍保持，未调用SessionStorage迁移至13。

失败例子：原主库已移入original后进程立即退出，prepared仍在→普通SDK启动restore_incomplete→再次相同ID确认→验证原主库已保留/其余附属文件仍在活动位置→只移动剩余文件后安装；不同ID、两处同名文件、缺失/改动原文件、额外附属文件、非法记录或候选被改均拒绝并保留现场。安装后、installed写入后及completed已写但active未删的进程退出分别覆盖；不靠phase串判定完成。

### 实际验证

| 命令/证据 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_storage_restore.py -q --tb=short`（实现前） | 最初17项因restore尚未注册红灯，red.log；另10个中断位置因执行器不存在红灯，processes-red.log；随后最小实现转绿 |
| `.venv/bin/python -m pytest tests/test_storage_restore.py tests/test_storage_restore_processes.py tests/test_storage_restore_failures.py tests/test_storage_cli.py tests/test_storage_startup_guards.py tests/test_storage_startup_entries.py tests/test_storage_migrations.py -q --tb=short` | **124 passed in5.27s**；75新增还原断言与49既有CLI/迁移/门禁专项，specialized.log |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3959 passed in268.82s**；backend.log |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | **49文件395 passed**；TS/Vite退出0，仅既有bundle大小提示；frontend.log / frontend-build.log |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec123-03-3gxfri73/wheel` | 标准构建退出0；新产品模块进入wheel，wheel-build.log |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec123-03-3gxfri73/wheel --output build/spec123-03-3gxfri73/install.json` | 独立venv/源码外真实nyt执行通过；schema12启动自动备份→13→破坏目标→坏设置/模型配置下列备份并完整ID还原→验证仍为12及坏原库字节保留；storage_restore/migration_backup/storage_backups均passed、provider_requests=0 |
| `.venv/bin/python build/spec123-03-3gxfri73/verify_structure.py` | **174产品Python＋38脚本**AST/500行/compile/pip/diff通过；681份既有源码/前端/测试/脚本中5份预期变化，其他676份SHA不变；新2产品/3测试文件；wheel源码/前端/README逐字节核对通过；verification.json |
| `.venv/bin/python -m twine check build/spec123-03-3gxfri73/wheel/*.whl` | PASSED；twine.log |

新测试覆盖源schema4—13、坏/缺目标、全四文件组、确认/EOF/Ctrl-C、锁贯穿交互、占用/无工作区/跨工作区/副本与移动、确认后再验、不加载模型设置、原库恰与候选相同、未来target_schema仅诊断、再次确认、源备份离线、严格JSON与各种记录/文件冲突、符号/硬链接、复制/标记/移动/安装/完成/解除标记IO故障及清理保主异常。真实子进程用精确文件提交点os._exit退出，不用紧sleep窗口。

check要求的架构/安全独立复核均无阻断；架构另验空original及仅WAL无主库，在prepared/安装未更新phase/installed/completed四点退出的8组合均通过。安全探针同样拒绝递归/重复键/带路径ID和filename/候选链接。唯一文字差异已修正：确认前沿既有协议可创建缺失的同名lock；不替换/删除锁。这些是本机源码/临时库证据，未自动升级成Windows/Linux原机或断电证明。

没有触碰真实用户库/环境配置、提交/push/标签或发布；保留Spec121/122/124及并行学习成果，不推定用户掌握。03收尾时不证明Agent业务可恢复，当时下一片123-04：旧库升级后还原重开及四平台交付矩阵；04本机结果见第20节，原机/远端执行证据仍分别记录。

## 19. 123-04验收合同（实现前）

本片只补组合验收和标准安装worker，不新增备份设置、业务schema或恢复策略。现有`ci.yml`四平台wheels均调用verify_wheel_install→release_smoke，沿用此入口，不增加发布/远端工作流。新`scripts/release_storage_smoke.py`由既有安装worker和确定性pytest共同消费，接受离线模型类型及CLI命令前缀；它只在调用者创建的临时目录中生成测试工作区，不面向真实库提供降级命令。脚本与运行模块分开，低层SessionStorage/restore仍不依赖Agent。

正常组合：真实create_agent与官方AsyncSqliteSaver生成正常两轮/根子Read/根待审Write三个会话，记录真实归档、checkpoint/writes、用量与诊断；关闭后仅在测试库构造前一版schema12→正式SessionStorage启动自动备份并迁至13→增加备份后的DB/库外变化→损坏目标→坏配置环境中通过实际CLI列备份/取消/完整ID还原→逐表核对schema12快照→正式应用重开，按原链再次迁至13。已知token值与未知计数分别保留，补回duration列时不伪造历史耗时；还原不撤销库外变化。

重开只读/恢复阶段模型、工具正文、Auto及run执行均为0；正常历史可续聊、根子历史可查询且旧委派不重放，待审会话不能普通restore直接变ready。显式restore_approval重新按现行策略构建审批，临时记住为空；人类随后approve/reject才进入原审批续跑，验证真实Write正文次数（批准1/拒绝0）、结果配对与收尾。故障组合覆盖旧库迁移后的再次还原重开和原4—12版本的元数据/BLOB保护；不把opaque测试BLOB称作Agent可恢复证据。

新增报告明确storage_roundtrip、history/usage/child/approval_reopen及恢复阶段0调用；安装离线模型需透传已有usage_metadata给实际运行链，仅修正验收模型投影，不改变生产模型处理。新增脚本必须进入源码外worker调用；相关pytest先证明缺少新验收入口/报告，再接线，已有恢复机制的组合检查可直接验证，不伪造产品缺陷红灯。

按同一当前源码/前端构建四目标wheel，检查平台标签、资源、源码指纹、依赖/格式和完整集合；仅macOS ARM64在本机真实安装运行。Linux x86_64/ARM64、Windows x64的原机安装/锁/停止/还原以及远端CI没有本轮运行证据时明确待验，不能以交叉构建代替。先完成可在本机执行的范围，结果同步handoff/future-work；不提交/push、打标签、触发发布或修改真实配置。

## 20. 123-04本机闭环与交付矩阵

2026-10-21完成本机范围，证据`build/spec123-04-ibr9fexh/`。没有修改生产运行时：只有既有release_smoke存储分支迁至新验收worker、离线模型透传usage_metadata，新增共享验收脚本及13项组合测试。不是新增数据库降级功能，也不是修复已复现的真实用户坏库。三平台原机/远端CI尚待验，本节不将它们标记完成。

### 当前验收主链

`verify_wheel_install → 源码外独立venv的release_smoke → release_storage_smoke.storage_scenario → 安装venv的真实nyt → 正式open_async_persistent_application`。pytest复用同一scenario，CLI前缀为源码`python -m naiyue_test_agent.cli`；安装worker传venv的nyt绝对路径，不从checkout导入产品。原CI四平台wheels及published-install均调用既有安装验证器，因此新验收自动接入其worker；没有修改或触发workflow。

| 阶段/数据生产者 | 实际输入、消费与验收 |
| --- | --- |
| 真实图生成数据 | create_agent/官方AsyncSqliteSaver、真实Read/Write工具；离线模型只替代供应商响应。生成正常两轮（101个已知输入token＋1次未知）、根委派→子Read→报告，以及根待审批外部Write三个会话；保存消息/归档、workspace绑定、用量、诊断、根子checkpoint/writes、审批interrupt |
| 旧版夹具→首次升级 | 关闭全部应用连接后，仅在临时库构造schema12（删除duration列并更新版本）；正式SessionStorage先自动生成schema12快照，再迁至13。_facts捕获13张表的真实行和BLOB，未反序列化快照；再写入备份后的标题与库外文件变化 |
| 实际离线CLI | 损坏目标；临时HOME/USERPROFILE中settings/models均无效，显式workspace/session-db仍可列备份。先取消且坏目标/maintenance不变，再完整ID确认还原；坏原库字节保留，active标记消失，13张表与schema12原快照完全相等，库外变化仍在 |
| 正式重开与查询 | open_async_persistent_application再次触发12→13及第二份独立备份；除版本及新增NULL duration外，其余行/BLOB逐项一致。正常历史/标题/置顶、根子归档、已知/未知用量和有限诊断可读；历史耗时保留未知。根子已完成任务不重放 |
| 待审恢复 | 普通arestore拒绝待审会话直接变ready；显式restore_approval保留官方原checkpoint内容，重新构建当前权限下的pending，session_allow为空。读取/恢复后断言模型0、Write正文0、Auto0且无run事件；随后人类approve/reject才调用原续跑入口，Write正文分别1/0次，ToolMessage配对且最终ready |
| 正常续聊 | 在上述0调用断言后，对原正常会话实际arun新用户消息；模型输入包含原两轮及新消息，只增加该轮1次模型调用，保存新回复/ready/checkpoint。history_continuation不是仅凭可读历史推断 |
| 真正中断任务 | 复用现有真实子进程夹具，在子待审批或Write已落地但结果未保存两处os._exit；夹具在生成会话前先通过正式API给空库绑定工作区，不绕过已有未绑定库的拒绝规则。之后自动备份/迁移→显式还原→正式重开，根仍需interrupted_execution恢复；restore_approval拒绝，continue只补outer错误ToolMessage，保留子历史，不重放旧Read/Write/子任务；已有执行计数和库外文件保持 |

测试划分：9项源schema4—12的SDK升级→还原→再次升级保留模型/状态和opaque BLOB（不声称opaque是可执行checkpoint）；2项真实图的人类批准/拒绝＋正常续聊；2项真实子进程中断后的还原恢复。所有数据都来自临时工作区，未打开真实用户库。

### 实际结果与证据

| 命令 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_storage_roundtrip.py -q --tb=short` | **13 passed in3.47s**；roundtrip-final.log。初始缺少新验收入口时2红/9已有组合通过；新增正常续聊报告先2红后实际实现，分别见red.log / continuation-red.log |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3972 passed in265.55s**；backend.log |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | **49文件395 passed**；TS/Vite退出0，仅原bundle大小提示；frontend.log / frontend-build.log |
| `python -m scripts.build_release --target TARGET --frontend-assets build/spec123-04-ibr9fexh/frontend-assets --output build/spec123-04-ibr9fexh/wheels/TARGET` | 四目标分别构建退出0；build-TARGET.log；共用同一次正式前端staging |
| `.venv/bin/python -m scripts.release_artifacts --complete-release build/spec123-04-ibr9fexh/wheels/*/*.whl` / `.venv/bin/python -m twine check build/spec123-04-ibr9fexh/wheels/*/*.whl` | 四份完整集合/平台二进制/白名单/源码指纹检查及Twine全部通过，均304文件、版本0.2.0、同一application_sha256；matrix.log / twine.log |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec123-04-ibr9fexh/wheels/macos-arm64 --output build/spec123-04-ibr9fexh/install.json` | 独立venv、源码外真实安装通过；storage_roundtrip/history_continuation/history_reopen/usage_reopen/child_reopen/approval_reopen均passed，恢复阶段model/write/auto均0，随后approved_write_calls=1，provider_requests=0；既有Web/native/MCP等门禁继续通过 |
| `.venv/bin/python build/spec123-04-ibr9fexh/verify_structure.py` | **174产品Python＋39脚本**AST/500行、compileall、pip check、diff及四wheel源码/前端/README逐字节核对通过；689份既有源码/前端/测试/脚本/工作流/发行配置仅release_smoke改变，其余688份SHA不变；新增1脚本/1测试，verification.json |

架构复核发现“历史可读不能证明正常续聊”的验收遗漏，已先补红灯再加真实arun与持久化断言，独立复核13 passed确认关闭；安全复核无阻断。首次验收脚本的duration断言按现有独立duration接口修正；崩溃夹具按既有绑定合同在空库时预先绑定，没有修改生产逻辑或放宽断言。

### 四平台证据边界

| 目标 | 当前wheel标签 | 本轮构建/归档/Twine | 本轮原机源码外安装/还原重开 |
| --- | --- | --- | --- |
| macOS ARM64 | macosx_11_0_arm64 | 通过 | **通过** |
| Linux x86_64 | manylinux_2_17_x86_64 | 通过 | 待验 |
| Linux ARM64 | manylinux_2_17_aarch64 | 通过 | 待验 |
| Windows x64 | win_amd64 | 通过 | 待验 |

以上Linux/Windows资源架构、哈希、许可证及包内容已交叉检查，不能替代原机锁/文件占用/停止/还原实测。原机矩阵已有CI接线，但当前改动尚未提交/push，本轮未运行远端CI或公开PyPI安装，不借用历史发布的绿灯。

Spec123当前没有待实现的本地功能切片；待验项为三平台原机/本轮远端CI证据；真实使用效果不在本轮，也不恢复助手对第1/2类的跟进。不能宣称任意损坏、磁盘灾难或所有断电可恢复。首期仍无自动淘汰/云备份、坏库抢救或自动还原；重开通过不等于所有历史任务均可恢复。未改真实库/配置/依赖/版本，未提交/push/标签/发布；Spec121/122/124及学习成果保持。下一开发候选沿future-work为后台任务生命周期，仍需先讨论范围，不自动实施。
