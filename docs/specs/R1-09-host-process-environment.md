# R1-09：冻结环境与本机进程后端

2026-10-25 用户批准：按已讨论方案实现 09，完成后讲解 10。基线为 R1-08 加锁层级文档的当前 dirty 工作树（build/r1-09/baseline.json）；保留此前修改，不提交、push 或发布。不改真实配置/依赖，不实现 Docker/envd、远端重连、线程池及归档/记忆锁优化。

## 1. 当前链路与修改原因

Shell 工具工厂创建 HostProcessRunner、查本机解释器并探测版本；执行时读取 execution_path 构造本机 ProcessRequest，再直跑或交给 BackgroundProcessManager。AITest 工具同样在本机构造环境、which 和字面 argv。只有 08 的文件能力经过已核验的环境装配；这两条进程入口尚不能安全接入其他环境。

正常：runs.start → 准入核验并冻结 ExecutionRoot → 工具权限/审批 → Shell 本机构造请求 → 前台捕获或后台管理器 → HostProcessRunner。失败：仅带 Path 的上下文仍可能运行本机命令；工具创建时探测宿主会将宿主状态误当成其他环境状态。

## 2. 合同与最小设计

- contracts/process.py 定义有类型 ProcessPort：environment、owner_session_id、validate、shell 和 argv。shell 接收解释器名称与命令，argv 接收受信配置和字面参数；请求不允许模型指定提供方、容器、任意 runner 或 cwd。沿用现有 settings/control/progress 与输出格式，不新建通用调度平台。
- RunCapabilities 增加 processes，RunScope.environment 与 files.environment、processes.environment 必须一致。HostProcesses 从本次已核验的 HostFileSystem 构造，绑定相同 ExecutionRoot/项目/拥有会话；构造不做 IO。普通无目录对话没有进程能力，有目录临时会话有明确 Host 能力，不增加项目身份/history/memory。
- 新执行与权限准备都核对能力、会话、环境描述、项目、目录/revision 和单次许可；缺失/未知/不一致拒绝，不能临时补 Host。完整环境描述参与审批，主/子 run 与审批恢复均装配/继承原绑定。
- Host 后端负责解释器发现/版本、本机环境变量构造、AITest 可执行文件查找、cwd、ProcessRequest 和 runner。解释器准备用事件循环拥有的 task 在同一 run/设置内共用；阻塞发现/探测在线程完成，不新增线程锁，不缓存单次请求、模型调用结果或工具许可。
- 版本探测也显式使用本次冻结执行根；不读取服务的当前目录。独立进程回归覆盖服务启动目录已删除、其他有效项目仍能首次执行 Shell 的场景。
- Shell/AITest 工具保留公开工厂参数和模型 schema、字面 argv、超时/停止/错误/输出语义，只调用注入端口。移除工具创建时宿主专属 available/version 事实；不可用在已选择环境执行准备时报错，工具仍可登记。旧 metadata 只有内部测试读取，无产品 API 消费者。
- 原 ShellAdapter/build_environment/BashAdapter/PowerShellAdapter 下沉 execution/host_shell.py，迁移实际调用方后删除旧位置定义，不留转发兼容层。HostProcessRunner 与 ProcessRequest 继续供 Host 后端和 fd/rg 等确定的本机内部消费者使用；其 Path 与 host 默认事实不是执行环境选择器。

## 3. 执行、后台任务与归档

HostProcesses.shell → 准备解释器 → 构造冻结环境描述的 Host 请求 → 根调用复用 BackgroundProcessManager 的 foreground/submit（有 manager 时）；子调用保持前台且拒绝显式后台；没有 manager 的低层正式能力测试沿用直接前台捕获。HostProcesses.argv → 在目标 Host 环境查找配置的 executable、保留 prefix_args/args 字面值 → 现有前台捕获。沿用 process_execution 的捕获/进度/归档/异常算法，不复制；该模块仍有 Host 后端与后台管理器消费者。

后台 _Operation 保存 EnvironmentRef；注册要求显式传入且与 ExecutionRoot/项目/请求完全一致，去重也比较环境。核验在登记和创建 worker/capture 前完成；线程执行目录检查，不在服务事件循环中等文件/数据库 IO。后台管理器继续拥有进程与清理，不保存本次 runtime/publisher/许可；只记录必要冻结事实。

目录失效：新前台/后台启动拒绝；已有任务不自动终止，list/output/stop/aclose 不做新执行健康检查，不重新选择环境。归档仍由本机应用写入执行目录之外的 data_dir；真实归档错误继续原停止规则，前后台转换、原执行截止时间、进程组停止与收尾确认都保留。

## 4. 接线、清理与锁

RunLifecycle.admit_run 创建 files/processes → ActiveRun → RunObservation.bind；根的普通/审批/压缩绑定一致。子实例继承父能力但保留原保存绑定核对与各自 agent_path 许可。validate_saved_approval 冷恢复按核验目录重建两种能力。RuntimeContext helper 显式装配两能力，缺能力反例绕过 helper，不自动补许可。

影响 contracts/process、run_context、execution/host_processes、host_shell、Shell/Bash/PowerShell/AITest、权限、后台管理器/端口、run 装配/恢复/子实例、测试/脚本与当前文档。字段同步检查序列化与 DB：端口只存内存，后台任务仍 RAM 托管，不改项目 schema；HTTP task 字段保持。

锁不新增；解释器准备 task 的字典仅所属事件循环访问。BackgroundProcessManager 的登记预留短锁内只做内存操作；目录核验先在线程完成再回到循环复核关闭/删除/清理状态。按 docs/lock-hierarchy.md 同步 B1 与新增能力 task 的生命周期，原捕获/归档嵌套保持，不改其他锁。

## 5. TDD 与验收

先红：只带路径无进程能力不可执行 Shell/AITest；跨会话/绑定/文件能力不一致拒绝；替身非 Host 端口能接到请求且工具工厂不启动本机探测；未知环境缺端口不回退。再绿：真实 Host Shell cwd/字面 argv/环境变量/超时、主/子及恢复同一绑定，后台记录环境与去重冲突、目录消失后拒绝新执行但已有 list/output/stop 可用、并发解释器准备共用且循环响应。

迁移现有直接工具/后台 fixture 与 adapter 故障注入位置，保留顺序、权限、异常身份、停止、前后台转换与输出断言。专跑 import 方向、包结构与锁约定；完整后端、显式 TS/Vite、标准 wheel/Twine/源码外安装，核对源码和资源、冻结输入无漂移。真实模型/真实 Docker、远端和其他 OS 原机不属于本片证据。

## 6. 已接入的当前主链与可检查例子

正常项目 Shell：RunAPI.start → SessionService 准入核验/冻结 ExecutionRoot → RunLifecycle.admit_run 创建 HostFileSystem 和 HostProcesses(files) → ActiveRun.processes → RunObservation.bind → RunScope.capabilities.processes。RunScope.environment、files.environment、processes.environment 是同一份不可变描述，含 binding_id/kind/project_id/root/platform/project_revision。端口和准备任务不进入数据库；会话持久化仍引用原 project_local/local 绑定。

模型提出 `bash(command="pwd")` 后：prepare_permission_call 在线程中调用 ShellPermission.prepare → require_process_system 核对当前会话、环境与文件能力 → HostProcesses.validate 核对目标健康 → 原审批机制生成单次许可。执行时先 require_tool_permit，再 require_process_system → HostProcesses.shell。Host 后端在线程中准备解释器/版本、构造含完整环境描述的 ProcessRequest（argv/cwd/env/timeout），进程在当前事件循环使用 HostProcessRunner 的原生 async IO。权限选择、进程执行、捕获归档及观察投递仍各归原有职责。

实际生产根会话有 process_tasks：同一请求进入 execute_managed_foreground → BackgroundProcessManager.foreground；显式 background 则 submit。两者先在线程调用 _validate_binding，检查执行根、项目、请求环境和外部归档根，然后回到事件循环 _register。关闭/删除/清理与调用去重在短锁内再次核对，保存冻结 _Operation.environment、深复制请求及设置，交接给空 Context 的 worker。前台转后台仍沿用原截止时间；状态、输出、停止查询不重新做目录健康检查。低层工具测试与子 Agent 无后台管理能力时走原 execute_process_request；显式子后台请求仍拒绝。

AITest：同一能力/许可核对 → HostProcesses.argv 在线程按目标环境和受信设置定位 executable → `[executable, *prefix_args, *args]` → 原前台执行。模型参数保持字面 argv，不拼成 Shell；配置的环境变量、输出边界和超时原语义有现有测试保护。

子实例：prepare_subagent_instance 先验证原保存绑定，再继承本次父 run 的同一 processes；agent_path/单次审批保持各自身份。恢复：validate_saved_approval 依据已核验原根重建 files/processes，比较整个审批 subject。tests/test_host_process_environment.py 的真实注册项目测试走父→子 `pwd`→关宿主→重开恢复→根 `pwd`，检查相同环境/拥有会话及新 run 的新端口。

失败例一：只有 ExecutionRoot 的直接上下文不等于执行能力，Shell 在准备权限时返回不可用，不执行命令；跨会话、绑定、文件环境不一致则拒绝。替身 sandbox ProcessPort 可接到 Shell/AITest，工具创建和转发阶段不能探测 Host 解释器。两种路径分别由 test_host_process_environment 覆盖。

失败例二：后台绑定或请求描述缺失/改变时不登记、不建 worker；目录失效拒绝新执行，但已运行的任务可以继续查询输出和停止。线程核验期间关闭管理器，返回后 _register 明确拒绝，不能生成 capture 或 worker。test_background_process_binding 用事件同步核对，原后台测试继续保护去重、跨会话访问与取消/收尾。

复核修正：解释器发现改为 run 时才执行后，服务启动目录可能已消失。先用独立子进程真实删除 cwd 观察 FileNotFoundError，再将 probe_version 改为必须显式接收冻结 root；直接调用测试同时检查 probe 的 cwd。POSIX 回归保留真实删除，Windows 用确定性 Path.cwd 故障注入，均要求有效项目的真实 Shell 成功，不跳过断言。

旧链清理：tools.shell 不再创建 runner/查找解释器/探版本；Bash/PowerShell 旧 adapter 和 build_environment 定义已搬至 execution.host_shell，所有实际测试消费者迁移，无旧位置转发。tools.aitest_kit 不再执行本机定位/构造。保留 tools.processes 的真实 Host runner/request、工具工厂必需的同步入口、process_execution 捕获算法、fd/rg 的确定 Host 消费者及 benchmark 的原生 runner；后台 benchmark 已显式携带冻结环境。内部 shell available/version metadata 删除，仅其内部测试迁移；公开工具工厂、模型 schema 与 HTTP 任务字段保持。

测试 helper 变更原因：原 typed_context/typed_scope 是低层图的正式能力装配入口，现为显式 ExecutionRoot 同时创建 Host files/processes；显式空 RunCapabilities 不会被自动补齐，缺能力反例直接构造 RuntimeContext。原工厂 runner 配置断言改在后端真正创建时检查，数量/配置断言保持；懒版本探测的故障注入与用户命令故障分开，未放宽异常身份或清理/停止断言。

## 7. 本机验收记录（2026-10-25）

- 新增 14 项：正式能力缺失/错绑定、替身非 Host 分发与禁止工具创建时 Host IO、并发准备及事件循环响应、真实项目父/子/恢复链、服务 cwd 消失、后台冻结环境/失效目录查询、关闭竞争及不一致请求拒绝。先观察缺能力的原执行和 cwd 探测红灯，再做对应实现；早期迁移失败日志保留，不计作通过。
- 最终核心命令：`PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_host_process_environment.py tests/test_background_process_binding.py tests/test_bash_process_runner.py tests/test_shell_adapters.py`，实际 `27 passed in 4.15s`（build/r1-09/final-core.txt）。原场景仍由完整回归覆盖；架构/安全专项复核完成，cwd 发现已关闭，Windows 测试没有跳过该行为。
- 最终完整后端：472 文件，5165 项。实际对 groups-final.json 中互不相交的两组调用 `pytest.main(['-q', '--tb=short', *files])`，分别 **2583 passed / 259.65s** 与 **2582 passed / 153.05s**，均退出 0；断言并集等于 collected-tests-final.txt 的完整 tests 清单。可直接复跑 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests`。首轮完整回归的旧 runner 工厂测试位置已迁移并完整重跑；误从根目录收集 docs/research 的独立实验时缺外部探针依赖，按项目正式 tests 目录重新收集，无删除或跳过用例。
- `npm run build`（frontend）显式执行 TS/Vite，退出 0，既有大 chunk 警告保留。`python -m scripts.build_release --target macos-arm64 --output build/r1-09/wheel-verified`、`python -m twine check build/r1-09/wheel-verified/*.whl`、`python -m scripts.verify_wheel_install --wheel-dir build/r1-09/wheel-verified --output build/r1-09/install.json` 均退出 0。标准构建脚本复用 dist，已单独编译前端。
- 产物中 301 Python 源与当前工作树一致，4 Web 资源与刚构建 dist 一致；989 冻结实现/测试/构建输入无漂移。源外新 venv 验证真实搜索、Read→Edit→Read、审批/重开、CLI/服务、后台命令、子 Agent、记忆以及存储升级/备份/还原；0 供应商请求。compileall、pip check、diff check 通过，17 个本片生产 Python 文件均未超过 500 行。具体报告 build/r1-09/verification.json、artifact-check.json、install.json。
- 未改前端源码，不重复前端单元测试；未使用真实模型/真实 Docker 或 Windows/Linux 原机，替身端口验证不能当作沙箱隔离证明。原 dirty 和主目录并行工作保留；无真实配置/用户库/依赖修改，未提交、push、合并或发布，无新增用户掌握证据。唯一下一步：解释并确认 R1-10 的 Docker/envd 文件通道、生命周期和 external/none；11 接进程恢复，12 接 Git 输入与会话环境选择。
