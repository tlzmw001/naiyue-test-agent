# R1-11：沙箱进程与操作恢复

2026-10-25 用户批准按10收尾时讲解的六部分实施11，完成后只讲12。基线为 build/r1-11/baseline.json；此前 dirty/主目录并行工作保留，不提交/push/发布，不修改真实应用配置/用户库/依赖。真实 Docker 只使用10准备的专用 Colima 和本片自建资源。

## 1. 范围与当前链

10已有 app.environments、DockerFileSystem、有限JSONL exec→Unix socket envd及文件算法；文件RPC不能跨断线核对写入，envd无进程持久记录。09的 ProcessPort 已让工具与 Host 执行分离，但 Host 后台管理器的内存登记/本机路径不能被冒充为远端恢复。

11补 DockerProcesses 和环境作用域任务查询、两端操作记录、有界输出续读及正常关闭；正式 Shell/AITest/Write/Edit/task 工具通过原 RunScope/许可接能力。无 HTTP/CLI/UI 沙箱选择，不改已有会话本机绑定；Git输入和新会话选择仍在12，交付13，SSH14。

正常例：Shell工具已获许可→DockerProcesses核对完整环境和会话→稳定operation_id与指纹在本机登记→envd先持久预留→只启动一次原生进程→保存状态/有界带偏移输出→本机续读并归档→原工具反馈。丢回执：本机保留unknown及原ID→只查询原ID→返回原操作状态/结果；不另起ID重放，也不在未确认远端未收件时重发submit。

## 2. 数据、协议和恢复

- operation_id为32位hex，正式工具从binding/session/run/agent_path/tool_call_id生成稳定ID。文件subject额外携带这一调用事实，不共享可变context；进程相同。显式内部操作API也要求调用方提供ID。
- 指纹包含操作种类和规范化参数；文件排除每次准备会变化的stat/anchor快照，但包含已审查的requested/resolved/workspace/工具参数和环境。相同ID不同指纹拒绝；预留、运行、已完成/已知失败、未知均不再次执行。
- 本机注册表3→4增加 sandbox_operations（绑定/会话/项目、ID、指纹、元数据JSON），项目库schema19不动。只存必要投影/归档进度，日志不输出命令、文件内容或provider body。关闭/重开使用同一个仓储，不跨库事务。
- envd在独立命名状态卷 `/state` 保存SQLite journal和有界输出，代码卷仍 `/workspace`。增加状态卷名称的确定派生、labels和实际挂载核对；恢复/销毁同步核对两个卷。此为持久布局变更，协议升2；旧容器不静默改挂载或重建，版本/策略不符明确拒绝。
- envd预留事务提交后才派发副作用，事务/数据库锁不跨进程等待；完成后保存有限结果再回执。文件操作也走这一层，坏结果/超预算记录为unknown。保存和启动/替换之间的崩溃窗口仍可能unknown，不宣称跨崩溃恰好一次。
- envd新实例遇到上次starting/running记录标unknown，保留ID/已落盘输出；不凭PID杀进程、不根据文件摘要伪造成功。容器PID1退出后重启不代表旧命令完成，旧操作不可再启动。正常活跃envd仍拥有控制对象并可确认stop。
- 使用有限命令 operation_submit/status/output/stop/background；task_list直接读本机登记投影，不另建远端列表入口。原始未登记的write/edit入口不再供客户端执行。只读文件RPC保持；请求身份、版本、大小与完整环境校验保持。

## 3. 进程、输出和工具语义

- envd使用现有 HostProcessRunner 在容器命名空间执行，重用启动/超时/进程组停止/输出背压和收尾算法。内部原生请求只代表容器本地，不能交给控制端Host runner执行。Shell解释器和AITest executable在容器发现，配置的环境值不继承控制端凭据。
- root可显式后台，前台超既有 foreground_wait_seconds转后台，执行期限不重置；子实例只前台。AITest literal argv不经过Shell，依赖缺失明确失败，不在宿主代跑或自动安装包。
- 尚未交接后台的调用取消时，发送原ID的stop并等待确认；交接后不随原run结束停止。文件worker同样持操作级停止信号，保留原commit前检查，提交后只能核对结果，不能声称撤销写入。
- 输出记录带绝对字节偏移，取回上限受协议预算控制，重连按原offset续读。envd按每操作和全环境额度删除最早输出段，响应携带最早可用offset、总观察字节及gap；不伪报完整输出。记录满额拒绝新副作用，不静默删除去重墓碑。
- 进程终态不等于输出已取完；本机必须拉到该终态最终observed_bytes，或明确记录缺段，才结束归档。不能因最后一次输出响应先于进程终态而丢尾部。
- 本机归档位于 data_dir/workspaces/<project>/artifacts 下，使用原有归档目录和命名格式。落盘先于进度提交；恢复时按已提交位置覆盖尚未提交尾部，避免重复。验证目录/文件类型及已记录文件身份，不接收远端提供的本机路径。
- 沙箱进程总是准备本机 raw 归档供恢复，格式化反馈复用原输出算法；查询提示使用 task_output，不引导沙箱 Read 读取控制端路径。输出帧严格核对字段、偏移、base64和长度；不合法帧标协议失败，不将其误报成本机磁盘失败。断线退出的采集任务允许在重新查询活动任务时恢复。
- task_list显示最后确认状态及unknown；task_output即使断线仍返回本机已知输出/归档并标stale。task_stop只有原envd确认原操作终止才报停止；不可达或重启后的unknown报stop未确认。列表/输出不要求容器在线。
- 本机真实归档IO失败/后台归档额度耗尽，按原合同尝试停止；停止无法确认保留unknown。前台归档额度满仍允许执行，但反馈明确截断。原规范化输出算法复用，有缺段/只保存尾部时显式标明。

## 4. 生命周期、配置与锁

- SandboxSettings新增冻结配置：每操作输出暂存8MiB、环境总暂存64MiB、最多1024条操作记录、同时执行16个操作、查询轮询100ms。均严格校验，可配置；完成记录不自动按时间删除，随显式销毁环境清除，满额拒绝新操作。正常关闭沿用已有 stop_timeout_seconds 和有限RPC期限，未确认停止报错且保留记录。
- 应用正常关闭先封新环境操作，结算已接纳调用，停止已登记活动远端进程并确认，再关注册表；不自动停止/删除容器。意外服务退出不发停止，远端已接管进程可继续，本机Agent不会安排下一步。
- ApplicationLifetime具体持有环境 API 的收尾入口，先请求已装配 host 停止 run，再关闭环境进程，随后等待已接纳应用操作和关闭仓储；不新增通用 shutdown hook 注册框架。提交在接受门内计数，关闭等提交结算后才逐个停止，防止迟到提交逃过关闭扫描。
- 每binding生命周期锁原样；操作journal使用独立短锁保护预留/状态/输出事务，不持锁跨原生进程等待或Docker IO。同ID并发提交必须单飞。控制端按操作ID串行归档同步，不跨ID嵌套；事件循环不等线程锁。按实际调用更新 lock-hierarchy。
- 状态卷位于工具文件根之外，避免项目文件操作覆盖；同一容器内已获准任意Shell并非envd记录的独立安全边界，不以此声称防同UID恶意进程篡改。隔离承诺仍是容器/宿主边界。

## 5. 影响文件与接线

contracts/sandbox（协议/ID/指纹）、sandbox_settings、registry_database/独立operation仓储；envd/journal、operations、processes及dispatch/server；execution/docker_environment/docker_files/docker_processes；application/environments的操作协调/任务查询/关闭；tools/write/edit注入调用ID、background_tasks允许有环境无本机Path的查询；ApplicationLifetime现有资源关闭顺序接环境收尾。标准镜像创建状态卷，不添加依赖；旧文件协议测试/策略fixtures迁移且保留权限、错误身份和顺序断言。

## 6. 验收

先红：同ID并发/重复执行一次、不同参数冲突、lostACK后只查原ID、崩溃窗口unknown不重放、文件已替换回执丢失；输出offset去重/跨段/额度/gap、重开归档不重复、不可达仍读本机已知输出；root/子限制、超时/停止未确认/真实归档失败；注册表升级保留项目与环境。

真实Docker：正式工具Shell/AITest literal执行、前台/后台/停止/超时；断开控制端后原命令继续、重新打开后按ID取回；模拟丢启动/写回执并核对副作用只一次；容器/应用重开、输出溢出、原环境消失无Host回退；关闭确认停止但保留容器。只管理本片带归属标记的自建容器/两个卷，模型固定离线响应、0真实供应商请求。

最终架构/安全复核、完整后端/前端与TS/Vite、标准Mac/Linux wheel/Twine/源外安装、真实最终镜像探针、冻结输入/包内容核对、compileall/pip/diff检查。交付正常/失败真实链及后续12计划，不提前实现12。

## 7. 当前主链和迁移核对

- 进程正常链：正式 Bash/AITest 工具完成许可 → RunScope.capabilities.processes 的 DockerProcesses.shell/argv → `_execute` 生成稳定 ID → `SandboxOperationService.submit` 先在 SandboxOperations 登记 → DockerEnvironment.request → envd EnvironmentOperations.submit → SandboxJournal.reserve 提交后启动 worker → execute_process 重用 HostProcessRunner → journal 状态/输出 → refresh 同步到控制端 SandboxOutput → 原格式化反馈。这里的原生 runner 在容器内运行，不是控制端 Host 回退。
- 文件正常链：正式 Write/Edit 将工具调用 ID 注入 subject 副本 → DockerFileSystem → EnvironmentService.request → file_call/submit → envd 登记后交给原 FileDispatcher/EnvdFiles 安全 IO → 持久保存二元反馈。WriteOutcomeUnknown 专门保留替换边界的不确定性，兼容原 ToolException 错误处理。
- 回执丢失链：原 ID 本机留存 unknown → refresh 的 operation_status 核对相同 ID/指纹 → 已知结果返回，活动进程继续取输出，无法确认保留 unknown；没有新 ID、重发 submit、重新写文件或启动第二份命令。
- 输出恢复链：remote observed_bytes 和绝对 offset → 严格帧检查（已确认总量不可回退）→ 归档身份检查 → raw 文件写入/fsync → 本机进度提交。准确目录为 `data_dir/workspaces/<project_id>/artifacts/naiyue-agent-output/<operation_id>-raw.log`。控制端重开从持久 offset 续取；远端裁剪返回 gap，终态仍须取到最终总量。
- 取消/关闭链：提交前已停止则拒绝；提交期间取消先等已接纳提交结算，再停止原 ID；只有已返回后台回执才算交接。正常 ApplicationLifetime.aclose 先停 run 控制并封环境新提交，等待接受门计数为零后扫描原操作并确认 stop；envd 不可达明确保留停止未确认。进程和容器的生命周期分开。
- 查询链：task_list 只读本机元数据；task_output 可从断线前已归档内容返回 stale，并在恢复连接后重启采集；task_stop 先核对绑定/会话及本地操作类型，拒绝文件操作 ID，再发远端停止。输出查询不接受远端给出的本机路径，也不要求本机执行目录存在。

旧测试按新合同迁移：协议常量、第二状态卷与冻结配置、注册表 v4 升级夹具同步；test_file_port_dispatch 的直接沙箱调用补 run ID（正式运行已有该身份），保留六工具派发/校验断言并新增 subject 复制注入断言。不是放宽断言或为缺身份生产请求提供默认 ID。旧未登记 write/edit 的超预算测试已迁入真实操作层，保留“文件确实写入但回执未知”断言；没有保留并行的原始写入口。

独立复核补出的取消窗口、文件 ID 被 task_stop 停止、归档目录身份错误、错误的本机 Read 提示、坏帧和输出总量回退均有对应回归。规格变化只涉及本片：未加入沙箱会话选择/新会话持久环境绑定/Git 输入/结果取回/SSH；已有 Host 会话、线程池、全局归档锁与个人记忆锁的约定保持。


## 8. 最终验收与限制（2026-10-25）

- 新增36项，正式tests共485文件/5240项。`groups-final.json` 按文件互斥分两组，`pytest -q --tb=short *files` 实际 **2620 passed in235.63s** 与 **2620 passed in196.86s**。首轮发现旧provider测试缺run ID，已按第7节迁移，最终源码完整重跑；未放宽/skip断言。
- 核心命令 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_sandbox* tests/test_envd* tests/test_docker* tests/test_file_port_dispatch.py tests/test_registry_execution_revision.py tests/test_process_transport_input.py tests/test_tool_package_boundaries.py tests/test_layered_failure_contract.py tests/test_domain_packages.py` 实际 **118 passed in7.68s**，日志core-verified.txt。
- `npm --prefix frontend test -- --run` →56文件/459 passed；`npm --prefix frontend run build` →TypeScript/Vite退出0。本片未改前端源码。
- 标准 `python -m scripts.build_release --target macos-arm64 --output build/r1-11/wheel-final` 与linux-arm64/linux-wheel-final成功；Twine两个包均PASSED。`python -m scripts.verify_wheel_install --wheel-dir build/r1-11/wheel-final --output build/r1-11/install-final.json` 源外干净安装通过，保留存储升级/备份/还原、history/memory、审批/子实例/CLI/MCP等验收，0供应商请求。
- 两包323 Python源/4 Web资源与当前源码/dist一致，1026冻结输入无漂移；AST/500行、compileall、pip check、git diff --check通过，见artifact-check.json/frozen-final.json。依赖和标准构建定义未改。
- 最终镜像由该Linux wheel及10已核验的Python base RepoDigest构建，镜像ID `sha256:167f8b1170a641bb438569ceb18639e15475e6f11ea5b0f39d074f057c6a5cde`，上下文colima-nyt-r1-10。`python -m scripts.verify_sandbox_environment --context colima-nyt-r1-10 --image naiyue-envd:r1-11 --output build/r1-11/files-final.json` 和对应 `scripts.verify_sandbox_processes --output build/r1-11/process-final.json` 均退出0。结果含正式工具、丢回执副作用一次、26万字节输出超过64KiB暂存后的gap/END尾部、应用正常关闭确认停止且容器保留、真实os._exit后原操作继续与输出恢复、envd重启unknown及无Host回退。
- AITest通道通过配置Python可执行程序检验literal argv，未运行实际aitest-kit业务用例；另在最终镜像直接验证默认AITest依赖缺失时启动前明确拒绝，见missing-dependency.txt。没有自动安装该业务依赖。Mac ARM64控制端/本机Linux ARM64容器为真实环境；Windows原机、远端SSH、公司防火墙和真实供应商/业务效果不在本轮证据内。
- 架构/安全只读复核发现均关闭；最终窄复核分别2项（输出总量回退）和19项通过。自建容器与代码/状态卷清理完毕，default context仍default，VM与镜像保留。仅有既有Vite大chunk及Docker必填BASE_IMAGE lint提醒，无构建错误。
- 正常关闭按每个原操作的冻结RPC/stop期限逐项确认，没有新增全应用总硬截止时间；多个不可达操作可能累积等待，最终保留停止未确认并报错。完成墓碑没有自动TTL；默认1024条满额须显式处理环境，不静默删除去重记录。同UID任意Shell不是journal隔离边界。

汇总：`build/r1-11/verification.json`。未提交/push/发布，真实配置/用户库/依赖/此前dirty及主目录并行工作保持；没有新增用户独立掌握证据。唯一下一步是讲解并确认R1-12的Git输入快照/bundle/新会话执行选择；12未实现，结果交付13、远端SSH14。


## 9. 异常合同补充复核（已修复）

2026-10-25 用户确认后续12可先用现有本机Docker验收，并要求核对沙箱工具异常是否遵守原合同。此次只核对、故障注入和记录，未修改生产代码，未开始12。

已复现的缺口：EnvironmentOperations._execute捕获ProcessCleanupError时将其保存为通用unknown/env_operation_unknown；DockerProcesses._execute随后抛ToolException，公共ToolErrorMiddleware生成error ToolMessage并继续模型。旧且当前仍有效的tool_error_feedback明确对ProcessCleanupError返回None，要求运行层处理，不能当作普通工具失败。此差异是跨RPC错误类别丢失，不是统一反馈中间件本身的变化。

最小确定性探针复用正式Bash工具/运行图、真实EnvironmentOperations与SandboxOperationService/注册表，传输用内存Engine直接派发，execute_process只注入ProcessCleanupError（未启动真实进程），实际结果：injected_exception=ProcessCleanupError、remote_state=unknown、remote_error_code=env_operation_unknown、tool_message_status=error、model_continued=true，provider_requests=0。证据 `build/r1-11/error-contract-probe.json`。

既有命令 `.venv/bin/python -m pytest -q tests/test_tool_error_feedback.py tests/test_layered_failure_contract.py tests/test_docker_process_port.py tests/test_envd_operations.py` → **46 passed in4.42s**，说明已有分层断言和本机保护保持，但没有覆盖这一跨RPC类别恢复。原5240全绿不代表该缺口不存在。

建议修复范围：远端用有限、明确的错误类别保留进程回收未确认等现有机制故障；本机恢复对应受保护异常，让原运行层处理；普通工具失败继续给可配对错误反馈，普通断线/回执未知保持原ID查询合同。不得反序列化任意Python异常，不把全部unknown都升级为机制失败。先补能经正式图复现的回归再实施，属于11收尾，独立于12的Git输入与会话接线。


### 9.1 用户批准的最小修复合同（2026-10-25）

用户“好的继续”授权先修此项，12暂不混入。本机Docker验收沿用专用Colima；保留既有dirty及主目录，不提交/发布，不改真实设置/依赖/库。

只增加一个有限结果错误码 `env_process_cleanup_unconfirmed`，定义在contracts/sandbox。envd遇到现有ProcessCleanupError时仍持久保存unknown，但错误类别用该固定码，消息只用固定文本；不发送任意异常类、原始正文、facts对象或consumer_task。已有unknown记录不根据类名字符串反推异常。

控制端操作协调器增加纯结果检查：只有shell/argv、unknown、ok=false且错误码精确匹配才恢复本机ProcessCleanupError，提示携带原operation_id。value在通用unknown处理前检查；DockerProcesses在submit返回后、每次refresh后检查，保证前台/自动后台前和显式后台尚未交接时都不会把机制故障转成普通反馈。已有后台任务的列表/输出仍允许展示unknown，不重新执行或采用远端PID。取消/停止的原异常保留规则不变。普通unknown、普通tool_error及坏响应继续原合同。

正常链：已确认收尾的非零退出/超时→原工具error反馈→模型可继续；失败链：envd原生runner抛ProcessCleanupError→持久unknown+固定码→控制端恢复受保护异常→原ToolErrorMiddleware返回None→运行层按tool_outcome_unknown处理，不再调用下一轮模型；必要stop未确认不能覆盖此主异常。

影响：contracts/sandbox、envd/operations、application/environments/operations、execution/docker_processes；新增局部确定性测试，不改conftest或通用helper。测试经过正式Bash/AITest工具/运行图和真实journal/本机仓储，传输只用内存派发；用Event控制“submit即返回故障”和“首次running后轮询出现故障”。验证ID/原故障/事件配对及分类、模型一次、原ID仅一次提交、无私密正文；反例验证普通unknown不会被升级。真实Docker在最终镜像内做受控故障注入并经过exec/socket/持久记录/正式图，不修改常驻镜像源码或增加生产测试开关。完整后端、构建、最终镜像和现有恢复probe保持。

未增加锁/存储schema/依赖/工具公开参数/通用错误框架。新错误码兼容当前协议2的result载荷；旧的通用unknown不获得追溯分类。针对固定码的解释只在执行层，不在低层contracts反向导入Python运行异常。


9.1补充：独立复核确认同一类别还会在自动后台交接响应被忽略、以及stop过程中才抛出故障时丢失。自动交接须按原_merge验证并保存返回行、检查固定类别后才能handed；stop未确认后查询同一个ID的operation_status，核对并保存结果，只有精确固定类别恢复机制异常，其余仍保留停止未确认。查询不启动/重放操作；二次查询失败不得覆盖原stop错误。正常取消/已有主机制异常仍由原except收尾保留。新增相应Event/真实协议状态驱动反例，不依赖紧sleep。


### 9.2 收尾实现与最终验收

当前链：envd原生runner抛ProcessCleanupError → unknown行内固定env_process_cleanup_unconfirmed及固定公开消息 → _merge核对原ID/指纹/种类 → raise_process_failure只按shell/argv+unknown+ok=false+精确code恢复本机ProcessCleanupError → 原ToolErrorMiddleware保留异常 → tool_call_finished为outcome_unknown/tool_outcome_unknown，模型不进入下一轮。submit/refresh/结果格式化均检查；自动后台先合并返回行再检查、通过后才handed。stop未确认只查原ID状态，核对保存后可恢复同类别，不重发submit或用远端PID。普通unknown和原取消规则保持。

新增8项回归：最初6项预期红灯，复核追加promotion/stop两项也先红再修；最终专项命令 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_sandbox* tests/test_envd* tests/test_docker* tests/test_file_port_dispatch.py tests/test_registry_execution_revision.py tests/test_process_transport_input.py tests/test_tool_error_feedback.py tests/test_tool_package_boundaries.py tests/test_layered_failure_contract.py tests/test_domain_packages.py` → **146 passed in10.59s**。正式tests共486文件/5248项，groups-final.json按文件互斥分两组，各2624项，实际 **231.19s/225.43s** 全过。前端56文件459项及TS/Vite重跑通过，无前端源码变化。

标准Mac/Linux ARM64 wheel、Twine、Mac源外干净安装/原产品smoke通过；323 Python和4 Web资源逐字节一致，1028冻结输入无漂移，AST/500行/compileall/pip check/diff检查通过。未升级依赖/更改schema、实际配置、用户库、工具公开参数或原锁顺序。

真实本机Docker使用标准最终Linux wheel构建的新镜像 `naiyue-envd:r1-11-cleanup`，ID `sha256:ee23911c0ed6fed0ac8e637c817a05a3ab76bfcdced0efc210cadc42562458fd`。命令：

```sh
.venv/bin/python -m scripts.verify_sandbox_cleanup_failure --context colima-nyt-r1-10 --image naiyue-envd:r1-11-cleanup --output build/r1-11-cleanup/cleanup-docker.json
.venv/bin/python -m scripts.verify_sandbox_processes --context colima-nyt-r1-10 --image naiyue-envd:r1-11-cleanup --output build/r1-11-cleanup/process-docker.json
```

均退出0。新探针foreground/background_before_handoff/literal_argv/persisted_fixed_error_code/no_private_exception_body均passed；通过自建容器内独立测试解释器的/tmp socket和journal注入故障，实际走Docker exec/JSONL/socket/持久记录与正式模型工具图，没有生产测试开关或源码/原入口改写。此为受控故障注入，不声称真实OS偶发回收故障已自然复现。原真实进程/文件丢回执、超时、输出溢出/尾部、停止、正常关闭、envd重启和控制端os._exit恢复均passed，0供应商请求。测试容器/代码卷/状态卷已清理，default context仍default；专用VM/镜像保留。

架构/安全最终窄复核分别24/35项通过，追加分支无遗留阻断。旧envd的通用unknown记录不会根据类名字符串获得追溯分类；使用新语义须本机与envd均运行本次修复代码。实际AITest业务、远端SSH和其他原机平台仍保持第8节证据边界。汇总 `build/r1-11-cleanup/verification.json`，原第8节日志保留为追加修复前时点。未提交/push/发布；下一步回到12A，不混入本片实现。

## 10. 提交前输出清理保护补充

2026-10-25 提交审阅复现：根 run 已结束但沙箱后台命令仍在运行，TaskAPI 的项目归档清理只检查本机任务，可能移除沙箱 raw.log 并触发下一次采集的必要归档失败。正常链修正为 TaskAPI worker → SessionService 本机忙碌/预留 → SandboxOperationService 按项目清理预留 → 查询活动/unknown/stale/未采集完操作 → 原预览或清理。失败链在预览或清理前抛 ValueError，保留归档与操作事实；清理预留期间 submit/refresh 明确 env_output_busy，不提交新进程、不追加归档。条件锁仅短内存操作，DB 与文件 IO 在锁外；退出释放所有预留。不同项目不共用清理标记，不改变正常归档错误停止进程的合同。回归入口 tests/test_sandbox_output_cleanup.py，覆盖活动/未知/输出未收齐、清理期间提交互斥与释放。
