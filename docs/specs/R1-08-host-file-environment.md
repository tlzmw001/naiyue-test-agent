# R1-08：已核验环境与本机文件后端

2026-10-25 用户批准实施第八片。基于已验收的 07E，保留已有 dirty 工作，不提交、推送或发布。本片不迁 Shell/AITest/后台进程、不创建 Docker/envd、不改线程池或归档/记忆锁。

## 1. 实施前链路与缺口

SessionService.prepare/confirm_admission 已在线程核对 project_local 的注册事实和目录，RunLifecycle.admit_run 冻结 ExecutionRoot。RunObservation.bind 将 RunIdentity/能力/观察器装配成只读 RunScope。文件权限准备和六个工具分别读 execution_path，再直接调用本机 Path/安全读写/搜索算法。AGENTS/Skills、冷上下文预览和 Skills API 也直接按本机 Path 读取。MCP 可用性只检查服务启停/定义，没有按会话环境检查 stdio 的入口。

正常例：选项目 P → 解析注册位置 X → run 冻结 X → prepare_file_subject/审批 → Read/Write 本机 IO。失败例：只有 Path 或复制另一个会话的许可时，还缺少独立的环境能力核对；后续远端接入若继续使用同一入口，可能错误选择本机 IO。

## 2. 数据与所有权合同

- 复用 schema19 的 project_local/local、project_id、会话/子实例及审计路径，不另建一套 Host 绑定表，不重复迁移 07D。它们是当前 Host 绑定的持久事实；后续实际容器记录属于 Docker 分片。
- EnvironmentRef 是不可变、可序列化描述：binding_id、kind(host/sandbox)、project_id、root 字符串、platform、project_revision。不含凭据、连接、Path、锁或客户端；根目录按环境平台作纯路径解析，必须绝对且不能含 `..`。Host 的 project_local ID 由项目 UUID 确定；旧 local ID 由已保存规范路径的摘要确定，路径/修订仍独立参与审批核对。描述不授予权限。
- HostFileSystem 是应用装配的实际能力，保存原 ExecutionRoot、项目和拥有会话，提供 EnvironmentRef；不是从请求 env_id/Path 临时选择的后端。主 run 与子实例均从已核对的原绑定装配；每次工具入口仍核对绑定、所属会话和目录。
- RunScope 增加 environment，RunCapabilities 增加有类型的 files。模型调用状态和单次许可仍保持既有生命周期，不放入共享能力。无目录普通对话 environment/files 均为空。有目录临时会话可有 Host 文件能力，不增加身份文件或项目 history/memory。
- 元数据预览/Skills 发现可以由应用为已确认的目录构造仅用于资源发现的 Host 能力；没有所属会话的能力不能用于模型文件工具。生产 runtime 不因缺 files 而默认创建 Host。低层测试 fixture 显式装配实际能力，缺能力反例直接构造正式 RuntimeContext，不能被 helper 补成成功。

## 3. 文件接口与实现

contracts 定义 FileSystemPort：prepare_target/verify_target、read/write/edit/list、异步 find/grep，以及资源的 info/resolve/list/read 原语。输入为已验证参数/审批 subject、现有设置、停止和进度；不接受提供方/容器 ID，不传本机句柄。路径事实是环境内字符串，权限规则做纯路径匹配；Host 具体实现内部才使用 Path、stat 和本机 rg/fd。

六个文件工具保留原 schema、ToolSpec、截断/进度/异常与反馈，只消费注入的 files。HostFileSystem 复用现有 _read/_write/_edit/_list_directory/_find_files/_grep_files 和资源发现算法，保留已有 Windows 语义、链接校验、内容/元数据摘要、临时文件、fsync/原子替换和变更反馈。Host 内部核心算法仍有实际调用方，不复制或删除。Write/Edit 的 _reserve_target 仍按真实绝对路径共享，不能按 session_id 分开。

AGENTS/CLAUDE/Skills 的选择/解析/提示词保持应用职责，现有有界算法改为通过 files 的资源原语访问文件；Host 后端不反向导入应用层。应用仅以 PurePath 做路径组合和策略判断，不在控制层 resolve/stat 文件。正常 run、审批恢复准备、冷热 context 预览和 Skills API 都接这个入口。元数据读取不能从历史审计路径回退；注册位置仍走原 authority 解析。

## 4. 权限、恢复和 MCP

- FilePermission 通过 files 获取实际路径事实；执行前 require_file_target 同时检查 files/environment/会话/原目录和原许可。缺失或不匹配直接失败，无本机或主目录回退。固定 Deny、显式 Allow、外部路径审批、候选过滤和 Skill 豁免保持原合同。
- subject 记录已核验的完整环境描述。审批执行、记住规则、恢复核对都检查它，旧许可不能借到另一个绑定。旧批次没有新环境描述时按原恢复核对路径明确失效，可取消，不重放工具。
- MCPServerSettings 增加 allow_sandbox（默认 false）。stdio MCP 在 sandbox 环境默认不出现在工具视图，实际执行前再次拒绝；显式开放才允许。本片只接合同和确定性反例，不创建 sandbox 后端。Host/普通对话当前行为、HTTP MCP、应用 web 请求保持既有合同。

## 5. 影响面与验证

生产：环境描述/文件接口、Host 实现、RunScope/能力、根/子运行装配、资源/上下文/Skills 入口、文件权限和六工具、审批恢复、MCP 设置/视图/执行。旧内部 workspace_root 仅在本片实际消费者处按职责处理，外部 HTTP/CLI/存储字段保留，不全局替换。

先红后绿验证：只有路径无能力拒绝、能力来自别的会话/项目/环境拒绝、六工具经替身文件能力而不访问本机、真实 Host Read→Write/Edit→Read、链接/互斥/停止/反馈；失效目录不回退；旧会话恢复和子实例延续原绑定；AGENTS/Skills/预览经后端；stdio 的可见性与执行双检查、HTTP 和 Host 兼容。复用既有测试保护 IO 算法；测试 helper 只做正式装配，不改断言/放宽权限。

结束前核对生产/测试/脚本/序列化及所有旧路径的实际消费者，专门运行包结构与导入方向测试，之后冻结输入、完整后端、标准构建/Twine/源码外安装及本片架构/安全复核。前端源码若未变只跑标准 TS/Vite 构建；真实模型/远端/其他 OS 另外标注。


## 6. 已接入的当前主链

正常项目 run：RunAPI.start → SessionService.prepare_admission/confirm_admission（线程检查登记、目录和 revision）→ RunLifecycle.admit_run 从核验后的 ExecutionRoot 创建 HostFileSystem → ActiveRun.files → RunObservation.bind 将同一能力与其 EnvironmentRef 放入只读 RunScope → FilePermission.prepare 获取目标事实 → prepare_permission_call 在 subject 记录完整环境/项目 revision → 独立工具 context 注入许可 → require_file_target 核对会话、能力、环境和 subject → HostFileSystem 调用原安全 IO 算法。实际 IO 不由模型或传入 env_id 选择。

子 Agent：SubagentRegistry.prepare_subagent_instance 保留原 project_local/local 续跑核对，将当前父 run 的 files 放入本次 instance → _subagent_steps 绑定子 RunIdentity/观察器与同一文件能力。拥有会话不变，单次许可的 agent_path 仍分别核对；live files 不进入持久记录。已有子实例在新 run 中仍先检查其保存绑定。

恢复和预览：validate_saved_approval 为当前原绑定重新装配 HostFileSystem，再准备并比较整个 subject；未知执行类型转为 approval_restore_unavailable 并记录日志。context_view 的热路径从会话绑定解析、冷路径从库中绑定解析，不从历史展示路径构造能力。SessionAPI.skills 在注册宿主下核对请求目录与权威当前位置，元数据能力没有 owner_session_id，不能进入文件工具。

资源：RunLifecycle._prepare_resources → prepare_run_resources(files) → 项目指令选择和 Skill 元数据解析 → FileSystemPort.resource_info/resolve_resource/list_resources/read_resource。应用只做纯路径组合与范围策略；Host 内部执行本机 lstat/resolve/有界读取。`.agents` 或 `.claude` 是普通文件时，子路径的 NotADirectoryError 按原行为视为缺少 Skill 来源，不能因此阻断项目 run；执行目录本身失效仍由独立健康检查拒绝。

失败例：删除已准入目录后，六个文件工具在真正访问之前都抛 execution_root_unavailable，许可不能令其访问其他目录。复制其他会话能力或改变环境描述会先被身份核对拒绝。只携带 Path 的直接图调用没有文件能力，不执行 Read；低层调度测试须显式 bind 正式 RunScope。有目录临时会话有文件能力，无目录临时会话没有；两者都不因此获得项目 history/memory 或创建项目身份文件。

MCP：ToolSetMiddleware.prepare_tool_view → registry.select_available(context) 使用 MCPPermission.unavailable_reason(context) 过滤；awrap_tool_call 和 MCP execute 的连接借用前/提交前再次检查。保存批次或直接调用工具都不能绕过 sandbox 的 stdio 默认拒绝。allow_sandbox 只开放本机 stdio 的调用资格，不把该服务搬入沙箱。

低层测试装配已同步：runtime_context_helpers 为有显式 ExecutionRoot 的 fixture 注入实际 Host 能力，无会话 ID 时与既有 feedback harness 使用同一测试拥有者；显式传空 RunCapabilities 不会被补能力。缺能力反例直接构造正式 RuntimeContext。旧工具 schema、许可断言和安全 IO 断言保持；Find/Grep 的进程设置测试在后端实际创建 runner 时核对同一配置，原五个 runner 的检查保留。


## 7. 本机验收记录（2026-10-25）

- 先观察缺文件能力/跨会话能力/资源后端/MCP gate/无效描述和普通文件 Skill 来源的红灯，再实现；日志在 build/r1-08/，早期失败不计作通过。
- 相比 07E 新增 30 项，最终专项 158 passed；完整后端 470 文件、5151 项，按互不相交的文件分组为 2576 passed / 257.55s 与 2575 passed / 169.14s，两个进程退出码均 0。完整清单是 collected.txt、groups.json、files-0.txt、files-1.txt；完整日志 full-final-0.txt / full-final-1.txt。
- 专项命令：`PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_environment_contract.py tests/test_host_file_environment.py tests/test_file_port_dispatch.py tests/test_mcp_environment_boundary.py tests/test_execution_root_files.py tests/test_project_skills.py tests/test_application_skills.py tests/test_web_skills.py tests/test_subagent_execution.py tests/test_temporary_subagent_scope.py tests/test_restart_approval.py tests/test_project_local_binding.py tests/test_context_inspector.py tests/test_storage_dependency_direction.py tests/test_tool_package_boundaries.py tests/test_domain_packages.py`。结果 `158 passed in 12.83s`。
- 完整后端可直接复跑 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests`；本次实际用 pytest.main 对 groups.json 中两组分别传入 `-q --tb=short`，断言文件并集等于收集清单、组间不重复。
- `npm run build`（frontend 目录）显式运行 TS/Vite，退出 0；现有大 chunk 警告仍在。标准 `python -m scripts.build_release --target macos-arm64 --output build/r1-08/wheel-verified`、Twine 和 `python -m scripts.verify_wheel_install --wheel-dir build/r1-08/wheel-verified --output build/r1-08/install.json` 均退出 0。wheel 构建脚本本身复用 dist，不自动编译前端；已单独构建并核对四个 Web 资源一致。
- wheel 中 298 个 Python 源与本次工作树一致；976 个冻结实现/测试/构建输入无漂移。源码外新 venv 走真实搜索、Read→Edit→Read、审批及重开、CLI/服务协议、子实例、记忆、升级/备份/还原；0 供应商请求。详见 build/r1-08/verification.json 与 install.json。
- 架构与安全专项复核均完成；普通文件 Skill 来源回归已修正并复核。compileall、pip check、diff check 通过。未改前端源码，不重复前端单元测试；没有真实模型、真实远端沙箱或 Windows/Linux 原机验证。
- 保留此前 dirty 工作；本片未改真实配置/用户数据库/依赖版本，未提交、push、合并或发布。Shell/AITest/后台进程能力仍在 09，Docker/envd 在 10；线程池、归档/记忆锁仍暂缓。
