# Spec141F：空环境继续准备及启动诊断

## 1. 已确认范围

用户批准按上一轮核验继续，先完成控制端修正：取消后的空环境继续准备、首次start失败分类、
启动诊断合并为一次SSH、准备细分计时。沿用已验envd0.5.6/AITest0.6.0镜像；不改envd代码、
Git历史/对象校验/路径权限/传输格式，不升级Docker，不裁剪历史，不引入缓存或依赖。
日志formatter、envd搜索名额和结果增量另片；输入历史模式仍待决定。本片不提交/发布新版本或部署镜像。

## 2. 当前正常及失败链

SessionAPI.prepare_environment → prepare_sandbox_session建立服务single-flight任务 → activity.use →
_prepare_sandbox_session并行snapshot/create → install → check_sandbox → ready。
取消在排空已启动IO后只stop本次新建空环境。下一次create直接返回stopped原记录，随后被非running
检查转换为env_operation_unknown；既有测试只检查stopped，缺取消后继续这一段。上一轮探针已复现。

首次SSH创建：持久化creating身份 → attach事实 → 控制端ACK → docker start → exec envd attach。
start非零时当前set-e直接退出，空ready被判env_protocol_mismatch。handshake诊断至多分别调用
daemon/inspect/logs，新增多次认证；原异常必须保留，诊断不能扩大读权限或暴露原始错误正文。

## 3. 取消后的空环境继续

准备任务在进入activity.use之前读取原记录；无记录直接继续原首次并行流程，不增加远端调用。
仅stopped原记录进入activity.manage预留，再读并核对project/session/binding及已保存selection。
必须input_id/input_operation_id/input_snapshot均空、完整operation清单为空，才复用_perform_action(start)。
该动作保留项目可信/活动、容量、Docker身份及策略核验，不直接改lifecycle或复建容器。
unknown/destroyed/foreign/有安装或任务记录不自动启动；错误沿原合同或明确的身份错误传播。
legacy无selection仍绑定原记录，不重新解析当前默认模板。公开HTTP/工具接口不变。

正常例子：取消后原A stopped且无输入 → 同一准备任务管理预留 → 原ID start成功 →
释放管理预留 → activity.use → 复用本机ready输入/create原记录 → install → ready；不创建第二个容器。
失败例子：启动IO在途时再次取消 → run_blocking先排空 → 只在启动已确认成功时记录本次resumed_here →
释放管理预留后，收尾将该空环境stop；不销毁旧容器/双卷。start失败后的unknown不自动stop/重试。
resumed_here仅内存事实，start保存running之后在同一worker设置，避免协程取消丢失事实。
准备失败且尚未install时，新建fresh资源仍按141E销毁；本次恢复的旧空环境仅stop，不授予新建销毁资格。
progress.check在启动前及排空后检查；install_started后继续保留原unknown/恢复合同。

## 4. 首次start失败分类

控制端attach脚本在ACK后执行start，失败统一exit 3且不exec；stdout仍仅事实和帧，stderr不进入模型反馈。
SandboxChannel.open增加内部可选start_expected=False。只有明确获首次启动许可、已ACK后的空ready
且exit=3，才抛env_start_failed；普通连接exit3、缺ACK、坏帧继续原分类。退出2保持旧入口不兼容错误。
SandboxTransport只在消费本次首次启动许可时传True。新增错误由前端映射为“容器启动失败，核对原环境”。
独立verify_sandbox_startup直接Engine调用方同步传入start=settings.ssh is not None；它仍只拥有自己
刚创建并保存的容器，不能沿用141E以前create已start的假设。新增失败测试覆盖本机False/SSH True及异常清理。
handshake允许对该码做既有有界观察；可信且同次的启动事件仍可细分为原allowlist错误，并以from保留
启动失败cause；诊断失败或不可信时保留原异常对象，不以诊断故障替换，也不自动重试写操作。

## 5. 单次SSH启动诊断

docker_management新增仅由DockerEnvironment._startup_failure消费的固定只读脚本。
它使用现有command/json_script，无远端Python/jq或新网络服务。单次SSH内取得daemon/单容器事实，
在读取日志前核对保存daemon ID、linux、精确container ID/image/name/全部所属labels。
只对非运行且exited/非零退出，读取该次StartedAt之后的有界日志；created或running不读日志。
响应为单行JSON事实、实际用于logs的StartedAt行及原始日志；后一次查询若发生重启，其StartedAt
必须与前一事实中的时间一致，否则不采信诊断。控制端仍核对daemon/完整identity、状态及时间、长度上限，
严格解析行首NYT_ENVD_STARTUP及原allowlist。任何错误只记录secondary类型，保留握手主异常。
本机Docker保留已有直接调用，不为低成本本机探针建立额外协议。所有脚本参数按现有shlex引用。
单次RPC总时限和最多65536字节沿原诊断设置；不把日志正文/路径/凭证写入应用日志或工具结果。

## 6. 细分观测

独立profile脚本增加SnapshotBuilder._read_local/_verify_local两项detail计时，保留原一级阶段。
Recorder.begin记录该轮单调时钟基点；details之外输出有界timeline，仅group/白名单kind、相对start/end/
duration/failed，不保存参数、路径、环境或结果。只导出git与snapshot detail，不增加生产热路径日志。
已有2000事件上限继续限制timeline；相对时间允许独立判断四项inventory和最终核对的真实重叠。
stage区间并集和原主异常/观察无效合同保持。原报告字段兼容新增；不把有数据就当预算通过。

## 7. 影响及验证

生产：application/sessions/execution_binding.py、environments/preparation.py、execution/docker_management.py、
docker_environment.py、sandbox_channel.py、sandbox_transport.py，frontend/api.ts。
脚本：profile_sandbox_preparation.py、verify_sandbox_startup.py。新增/扩展对应行为测试，更新lock-hierarchy/handoff；均低于500行。
以上不在envd共享构建文件闭包中，release/envd.json无需修改，远端镜像保持。

先红：真实会话取消stop后直接继续；scope/selection/安装/操作/unknown拒绝，恢复启动在途取消排空；
真实sh脚本start非零与Channel分类、普通exit3不误判；单SSH诊断/身份错误不读日志、原异常保留；
timeline起止/重叠/字段隐私/事件上限与detail接线。禁止减弱旧身份/异常/取消断言。
再实现与定向回归；安全/架构复核；全量后端/前端、标准构建/Twine/源码外安装；
专用VM新控制端接既有镜像验取消恢复及准备/双沙箱，0供应商请求。公司Windows/两跳仍单独验收。

## 8. 本片验证结果（2026-10-30）

主要行为先红：取消后的三种继续结果3项失败，启动分类/批处理/时间线12项失败；实现后定向96项通过。
随后补管理入口cooperative取消、运行容量门槛、再次取消和真实诊断查询期间重启的回归。
重启期间事件错误采信的新增用例先红再绿；诊断成功分类保留cause、观察失败保留原异常对象。
独立startup直接Engine调用遗漏经失败测试复现，修正首次SSH许可后9项通过；分发startup按原候选方式
内联SECURITY_CODE，源码外实跑通过，无运行时scripts包依赖。本片未改conftest或共用测试helper，
原start退出码断言从17改为协议规定的3，仍断言未执行attach，没有放宽身份/状态/取消/异常断言。

- 两个完整后端CI分片3322+2819=6141通过；启动脚本新增用例在收集后补入，另跑该文件9项通过。
  当前6142项collect-only集合与上述JUnit成功节点去重集合逐项相等，零遗漏/跳过/失败；
  证明见`build/spec141f-20261030/backend-coverage.json`。重复METADATA负例仍有一个预期UserWarning。
- 前端69文件568项通过，TypeScript/Vite构建通过（保留原大chunk提示）；四目标标准wheel/Twine通过，
  应用hash一致为`7e9c6e078890571725322354acf3fe02972b4d4ce09680bf57daeb9eae37300f`。
  首轮误把frontend/dist传给已分层资源入口而失败；改回默认stage_frontend后四目标全部exit0，
  未修改或放宽构建检查。373份Python/4份Web与源码一致，全新虚拟环境安装门禁通过。
- 专用VM真实会话：A在容器已创建、尚未安装时取消后stopped；继续在同一容器和原卷ready，
  读取内容正确，B仍ready，2/2自身环境精确清理。记录resume=1.902s仅本机VM观测。
- 原有镜像刻意移除控制能力的自建无卷容器实际启动失败，旧镜像发出env_policy_mismatch；
  最终新控制端一次SSH取得严格匹配的诊断，异常cause保留，精确故障容器已删除。
- 最终安装包在源码目录外运行分发的独立startup/profile/acceptance：身份/权限/Bash/AITest通过，
  三轮首次准备1.750122/1.657276/1.702340s，20Git/671文件/4530744字节且清理3/3，
  每轮22项有效相对timeline，read_local与verify_local均实际接线。默认8MiB、双沙箱、20Skills、
  输出重叠/停止/重连及预算均通过，停止65ms，假模型暖运行38ms，0供应商请求。
- 安全与架构各自只读复核未发现剩余阻断项，均独立运行69项通过；文档分类口径已同步澄清。
  非本片已确认替代的内部路径不删除；本机诊断和普通/恢复连接均保留真实消费者，未引入并行旧实现。

源码/制品/故障/安装/集合覆盖完整索引为`build/spec141f-20261030/verification.json`。
专用VM最终容器和卷均0后停止，context仍default；原镜像ID仍
`sha256:20235fe60e36b675f96881b3e2f2003f685fb6ffd24766660372cca904f3e961`，未重建或部署。
envd清单/协议/共享源码保持141E候选，未改配置、版本、依赖、schema或用户数据，无commit/push/tag/PyPI。

按此前候选附件授权，只追加`naiyue-spec141f-windows-x64-recovery-candidate.zip`及SHA到既有v0.5.6，
原21附件及标签保持，不替换Linux镜像。本机数据不能证明公司Windows/两跳10秒目标；真实网页/业务/
大输出仍待原机。完整历史、目录权限优化、envd日志formatter/搜索槽位及结果增量未纳入本片。
