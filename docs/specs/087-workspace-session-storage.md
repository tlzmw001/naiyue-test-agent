# Spec 087：工作区标识、会话数据根与跨平台实例锁

状态：2026-10-16，已按用户批准实现并接入CLI/Web；本文替换之前路径SHA草稿。实现前基线临时补上本机现有fd路径后1610通过；不安装依赖、不修改真实配置或环境文件。

## 已确认范围

- CLI/Web显式--workspace优先，否则使用启动cwd；固定真实根，不调用os.chdir。每目录多个会话/同进程子Agent共享库，不同目录独立服务可并行；同库仍一个应用拥有。单服务工作区切换与同项目多独立服务后排。
- 首次启动在真实工作区创建.naiyue/workspace.json，格式version=1及UUID workspace_id；初始化用本地workspace.lock协调和临时文件原子发布。无效/不可写标识报错，不覆盖。--no-session不创建标识/数据库。
- 全局settings.toml新增[storage] data_dir（可选绝对Path，支持~）；未配置为用户目录/.naiyue-test-agent/data。真实配置不改。目录不可用明确失败，不回退C盘。CLI/Web加载一次同一启动快照。
- 数据路径=data_dir/workspaces/<UUID>/sessions.sqlite；--session-db显式覆盖单文件位置。数据库旁锁与所有SQLite附属文件跟随该位置。UUID独立于cwd/端口/项目位置。
- 关闭服务后，同机改名、同盘/跨盘移动只要携带标识即可重启找回。运行中搬目录、跨电脑搬历史不保证。复制保留重复ID时拒绝自动接管；显式--new-workspace才给副本新ID并使用新库，不复制原历史。该参数与--session-db、--resume、--no-session互斥；只允许检测到副本时重置，避免误重置原工作区。
- 旧全局库/旧会话不迁移、不兼容、不回退。删除旧schema1/2迁移和NULL工具列表默认补写；新schema4含工作区绑定表，旧schema3同样明确拒绝。获批实现时离线删除固定旧默认库及.lock/-wal/-shm/-journal；不启动自删、不扫描其他自定义库。

## 真实主链与数据合同

入口解析根及设置 → prepare_workspace(root, data_dir, database_path, new_workspace, create) → WorkspaceLocation(workspace_id, root, database_path) → SessionStorage(path) → bind_workspace(workspace_id, root_path) → 官方saver/SessionService/Agent。

prepare_workspace只做标识、路径和只读冲突探测；真正重绑必须在SessionStorage独占锁取得后再次判断。create=False用于空resume，不新建目录或标识。显式自定义库必须与标识一致，不能混用另一个工作区的历史。

数据库schema4保留naiyue_sessions原字段和官方checkpoint格式，新增单行naiyue_workspace(workspace_id,root_path)。bind_workspace首次登记；已登记ID必须一致。根变化时：旧位置不存在或已由另一ID占用才允许更新；旧位置仍持有同ID且为不同真实目录则报workspace_id_conflict；权限/损坏导致无法确认不当作不存在。一个SQLite事务更新绑定和所有会话workspace_root，状态/checkpoint/session/thread保持，失败不进入模型。历史正文不做路径替换，恢复时临时授权为空并重新准备AGENTS/Skill；running/interrupted/failed仍按原恢复合同。

CLI预检先bind，再将已确定的绑定连同storage.path交给原异步桥；关闭预检锁后，正式异步构造重新取锁并bind/验证，不假装无缝交接。程序库显式SessionStorage(path)及无工作区会话仍有真实消费者，可以不绑定工作区；不是旧格式兼容。WebSettings增加可选workspace_id及不序列化的启动设置快照；原显式数据库调用签名仍可用。Web只列当前工作区会话，API正文不新增字段。

## 平台文件/锁

新增根级storage_files.py服务项目标识和SessionStorage两处消费者，避免root工作区模块反向import application形成循环。POSIX按需fcntl+O_NOFOLLOW；Windows按需ctypes/CreateFileW(OPEN_REPARSE_POINT)、句柄校验及msvcrt.open_osfhandle，固定offset0的LK_NBLCK一字节锁，LK_UNLCK释放。只明确竞争归BlockingIOError→storage_in_use，其他IO保留失败。普通文件、非reparse/链接、单硬链接检查保留；不检查私密位或改ACL。描述符不继承给Shell。

ExitStack顺序确保连接先关闭再解锁/关闭fd；构造失败、取消也收尾，释放异常有日志，原主异常优先。.lock存在不代表被占用，不能删除锁来解锁。项目标识并发初始化使用同一平台锁，完成后释放；应用运行锁按UUID数据库保持到关闭。SQLite按名字打开，不宣称目录树恶意替换的沙箱保证。

## 正常/失败例子

A首次生成W1，库D/data/workspaces/W1/sessions.sqlite登记A；B独立生成W2并可同时运行。A关闭后搬到C，C仍W1；取得W1库锁后将绑定及会话根A→C，原ID/历史继续。复制A→B时A仍持W1，B默认报冲突；--new-workspace仅更新B标识为W2，新库/新历史，A不变。第二服务争用同W1库时在catalog/model/tool之前拒绝；关闭后可重开，异常退出后running残留不自动恢复。

## 文件和接线

涉及13个生产文件，新增2个；范围超过8文件但不新增服务/依赖，按切片实施：

| 文件 | 职责 |
|---|---|
| config_paths.py | StorageSettings、默认数据根；配置与数据位置独立 |
| 新storage_files.py | 平台普通文件/锁/原子写基础 |
| 新workspaces.py | 标识/UUID/只读绑定判断/副本初始化、WorkspaceLocation |
| tools/settings.py | 在已有全局配置入口校验storage，失败不回退 |
| application/session_storage.py | schema4/绑定事务，平台锁接线、明确竞争码 |
| application/session_persistence.py | 删旧NULL工具补写，保存/恢复合同保持 |
| application/async_persistent.py | 取锁后绑定再加载catalog，新增可选绑定参数 |
| application/cli_async.py | 传递预检绑定到正式异步入口 |
| application/cli.py | cwd/标识/数据根/副本参数/恢复和占用反馈 |
| web/__main__.py | 同CLI定位和参数、启动快照 |
| web/settings.py | 可选ID/启动快照，原必填字段保持 |
| web/app.py | 快照与绑定交给正式异步装配 |
| web/service.py | 同工作区会话列表和校验 |

测试新增工作区身份/移动/复制/根配置/平台IO与故障/真实并发进程验收；迁移旧schema升级测试为拒绝；保留当前格式恢复/原ID/权限/事件断言。现有CLI/Web入口测试需要隔离cwd，避免新默认标识写到真实项目；允许在conftest对这些模块做临时cwd及数据根隔离，不变更产品逻辑/不放宽断言。

同步README、docs/web-chat.md、配置样例、handoff/project-plan/future-work/Spec070版本边界。不改真实.env/配置/其他项目，不提交或push。

## 验证与阶段

1. 先失败测试，再实现平台文件和配置/身份；路径存在/中文/链接/首建并发/原子发布失败/损坏、重复ID、不可写数据根、配置冻结。
2. 绑定事务与真实fake模型会话：关闭改名/跨路径移动，历史/ID/模式/工具保持；受限状态不放行；副本默认拒绝/显式新ID、原库不变。故障注入验证回滚和零模型调用。
3. CLI/Web接线、默认cwd/显式根/数据根/临时零标识/空恢复/工作区列表；两个真实进程同时持不同项目库、同库拒绝、关闭/异常终止释放、子进程不继承。
4. 定向和全量pytest、compileall、pip check、前端测试/build、AST/500行、diff检查。实际Win/Linux环境缺失时明确实机待验，mock不冒充原机。其他WIN1的归档/fchmod/Edit/Deny命名空间不合入。
5. 实施后核查固定旧全局文件的普通类型及锁占用，离线清理存在目标，记录结果；不迁移/备份用户已放弃的数据。

官方参考：[Python3.12 msvcrt](https://docs.python.org/3.12/library/msvcrt.html)、[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)；Pi源码默认cwd和运行时切换提供职责参照，不声称同一持久格式或自动移动实现。


## 实施与验收记录

- 新增workspaces.py、storage_files.py，合计13个生产文件接线；未新增依赖、操作系统抽象平台或独立服务。配置/身份定位→预检存储绑定→异步正式装配再次持锁/绑定→catalog/saver→原会话run链不变。
- CLI真实fake模型验收：cwd A首聊→关闭→移动B→从其他终端目录显式选择B并resume，session/thread保持、checkpoint推进、人类/助手两轮消息全保留，元数据根同步改为B。绑定不重写历史正文；运行/审批异常状态不自动转ready。
- Windows分支用句柄与CRT mock检查reparse标志、非继承、句柄转换失败关闭、固定字节范围和真实IO错误不冒充锁竞争。不是Windows原机证明。POSIX双进程实际验证不同项目并持、同库竞争、正常/异常退出释放、初始化并发同ID；副本/损坏/原子发布/事务故障和设置路径均有本机回归。
- 删除schema1/2升级和NULL工具集合补写；旧格式1/2/3拒绝且文件内容不变。保留实际脚本/测试消费者需要的显式SessionStorage及未绑定程序化存储，无旧CLI全局入口或假设兼容层。
- 初始10例先失败后转绿，后续补真实进程、入口、Windows句柄及收尾故障。最新完整命令与结果见handoff；Python/前端均本机验证，没有真实模型业务请求或Windows/Linux实机结论。离线旧默认库清理结果同列handoff，不提供启动自删。

- 最终全量：`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1634 passed in50.64s**，日志`/tmp/naiyue-spec087-acceptance-full.log`。前端9文件27测试及TypeScript/Vite186模块构建通过；compileall/pip check、279文件AST/105生产文件行数、两份TOML语法/当前样例schema、diff检查通过。
- 旧默认库离线清理实际结果：持有原`.lock`后删除19243008字节的`sessions.sqlite`及其`.lock`，其余三个附属文件不存在。未碰自定义数据库或真实配置，未为本项目创建真实工作区标识；未提交/push。
