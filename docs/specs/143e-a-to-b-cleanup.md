# Spec143E：A→B 迁移范围与清理清单

2026-11-01用户确认的任务自动授权、外部网络开关与项目依赖合同见[143p](143p-s042a-task-capabilities.md)；该合同替代此前B逐工具审批计划，本机普通模式审批保留。

状态：拟实施清单，随 [Spec143](143-cloud-task-b.md) 审阅。本轮只写文档，不执行下表代码/数据删除。

## 1. 替换合同

B是打断旧云端合同的迁移：不支持A的Docker模板配置、execution_binding、envd协议、Git输入/结果bundle、旧云端客户端与旧云端数据库。**不做运行时A/B选择、不保留假设外部调用者的内部兼容包装。** 公开云端协议升级时明确握手拒绝旧客户端。

这不代表删除普通本机功能、修改既有本机会话历史或执行旧数据清理。B使用新cloud数据根；旧A目录/卷/镜像不被B扫描、迁移、回收。用户以后要删除它们，另给目标、范围和验证。保留历史Spec/课堂记录用于查证，顶部标明被替代边界，当前入口指向143。

每片先找实际消费者，再以“新链接线＋测试迁移＋旧链删除”为同一完成单元。对于静态引用不明显的框架hook、动态装配、脚本与打包清单，沿真实生产链确认；不能仅因没有普通函数调用或名字含legacy/sync就删。

下表切片号指替代职责所属片；有A正式消费者时不提前删断当前构建。S08冻结机器清单，S09.1先通过候选真实业务门禁再正式切换并删除A运行消费者/必需打包项，S09.2清理余留工具/文档，见143f。两个提交都须独立全绿，不存在长期兼容期或用户A/B开关。

## 2. 当前消费者与迁移目标

| 当前路径 | 当前实际职责/消费者 | 迁移处理与切片 |
| --- | --- | --- |
| `service/app.py`、`service/scopes.py` | 本机服务、各项目Host与Web装配 | S02/S09分出Coordinator装配，云端不装Agent；本机装配继续有消费者 |
| `application/sessions/execution_binding.py` | 本机/远端能力选择、创建/准备沙箱、续聊准入 | S04/S09删除A沙箱分支与包装；本机位置/权限准入按调用保留，不再接Docker执行绑定 |
| `application/environments/` | A环境/准备/输入/结果/交付/额度与清理 | S04–S09按职责移到cloud或client，不保留空facade；仅实际通用算法可迁移 |
| `execution/docker_cli.py`、`docker_environment.py`、`docker_management.py` | 本机经SSH执行Docker生命周期 | S02/S04被OpenSandbox适配器替代后删除，不继续支持旧Docker context配置 |
| `execution/docker_files.py`、`docker_processes.py` | Agent工具的远端envd代理 | S04/S09改为Runner内HostFiles/HostProcesses后删除 |
| `execution/sandbox_channel.py`、`sandbox_transport.py`、`sandbox_connections.py` | 常驻SSH工具请求/响应/连接复用 | S04/S09删除；新控制面不用它转发逐工具调用 |
| `execution/sandbox_runtime.py`、`sandbox_child.py`、`sandbox_output.py` | A执行上下文/子进程/输出适配 | S04确认当前通用进程消费者后迁移必要算法，删除A专用包装 |
| `envd/` | A容器侧协议、文件/进程/Git/操作日志与权限机制 | 整包退出正式构建，不作为B守护进程；先按143a第8节逐项迁移身份/路径竞态/FD/环境/停止/未知回执断言。仅HostFiles/HostProcesses可用不代表已替代其安全语义 |
| `execution/git_snapshot.py` | 原项目私有Git快照/历史对象/bundle输入 | S03以archive/manifest替代；保留必要防变更/路径语义的测试，删除快照提交主链 |
| `execution/git_results.py` | bundle验证、对象导入/发布ref | S07以固定结果验证替代；删除bundle读取和publish结果ref路径 |
| `execution/git_delivery.py` | branch/merge/discard交付 | S08保留并迁移安全branch算法，merge改为内容预检apply；旧历史回放/包装删除 |
| `execution/git_repository.py` | 多个Git调用/环境/路径安全能力 | 不按文件名整删；只保留新比较/分支交付及本机仍用的函数，删除A独占消费者与包装 |
| `contracts/sandbox.py`、`git_inputs.py`、`git_results.py`、`git_delivery.py` | A字段/线协议/回执 | S01/S03/S07/S08迁移到cloud内容/任务合同；无bundle/output_commit/binding字段兼容解析 |
| `storage/environment_registry.py`、`sandbox_operations.py`、`sandbox_journal.py`、`git_inputs.py`、`git_deliveries.py` | A环境、操作与交付记录 | 新cloud.sqlite独立建表；S09删A仓储装配/活跃迁移入口，不对旧库执行DROP |
| `storage/registry_database.py` | 当前本机项目注册与包含A的schema迁移 | 保留本机项目注册消费者；A表/仓储不再被新B引用。已有本机DB的迁移链与全新schema分开核验，不能粗暴删历史版本后破坏本机库 |
| `storage/resources.py`、`project_store.py`、conversation/reply/usage仓储 | 现有checkpoint/历史/必要保存 | S04保留Runner单任务装配，S06复用查询算法到Coordinator独占投影；禁止Coordinator打开任务库/调用saver.setup，不共享项目活动库 |
| `application/memory/memory_access.py`及同包作业/来源模块 | 本机个人/项目记忆、来源与后台整理 | 按143s保留本机消费者，不建云端memory客户端/API/作业；任务过程不输入整理，正式取回材料按现有来源/质量/遗忘规则处理 |
| `application/sessions/task_queries.py`、后台任务工具与侧栏 | 当前本机任务查询/控制与进程生命周期 | S06.2a/b接手动管理/关联与已有查询，S06.3接资源删除，S06.4接加号/列表/详情底部输入候选，S09.1正式切换；不注册本机模型创建任务工具。本机关闭只关闭观察，不借BackgroundProcessManager的关闭流程取消云端任务 |
| `model_access/`、`agent_runtime/`及middleware/tool_execution | 当前模型循环/原生SDK/权限/资源准备 | S04/S05复用；增加内部模型连接覆盖，不能整套重写或顺便升级依赖 |
| `execution/host_files.py`、`host_processes.py`、`host_shell.py` | 普通本机IO与进程；Runner内也直接需要 | 保留，绑定Runner工作根/权限；移除的只是A远端代理 |
| `service/legacy_history.py`及BrowserConnection.legacyDatabase | 仍有普通本机只读旧库消费者 | 不是A专用，无证据不删；云端不注册此路由/不挂这些数据 |

若某条路径在实施前发生变动，以实际 owning source与消费者复核更新本表；清单不是授权按通配符删目录。

## 3. API、前端、脚本与发行链

| 范围 | 同片需要清理的消费者 |
| --- | --- |
| A环境API | `application/environments/api.py`、相关web环境/结果路由与DTO；新任务命令接线后删除旧路径，不做旧binding转task的隐式映射 |
| 前端 | `api.ts`的executionOptions/refreshEnvironments/sandboxManagement/manageSandbox/createSelected/prepareEnvironment及A结果方法；关联types、SandboxManager与测试同步迁移 |
| 交付UI | 删除 bundle_size/output_commit/original_head作为远端结果必需字段；本机原HEAD只留连接器分支交付回执 |
| 配置 | 删除新版本A Docker context/envd/旧sandbox profile解析分支；提供明确不支持A配置的错误，不自动转写真实settings |
| 构建 | `scripts/build_envd.py`、`build_sandbox_image.py`、`envd_artifacts.py`及envd发行清单消费者退出；标准wheel/release构建接入真实Coordinator/Runner/模板产物 |
| 探针 | `verify_sandbox_*`、`release_sandbox_smoke.py`、`sandbox_crash_probe.py`、sandbox_probe及A性能脚本按消费者迁移/归档；不以旧脚本仍import来保留旧runtime |
| 测试 | A协议/容器替身测试迁至实际B入口，保留身份/顺序/权限/取消/未知操作/终局保存/清理失败的行为断言；只有明确取消的A线格式断言可以随格式删 |
| 当前文档 | README、handoff、project-plan、CLI帮助、安装/SSH/模板/结果说明与锁层级同步；历史Spec141/142标版本边界，不把拟实现当已接入 |

构建中不能残留“文件存在但不装配”的A/B模块。打包清单验证应证明envd不再进入B产物、Runner实际被启动、frontend实际消费新DTO；不能只以源码rg无引用作为接线验收。

## 4. 删除/保留判定模板

每片在验证记录写四项：旧入口与真实消费者；替代后的入口/数据；迁移的行为/异常断言；删除项与保留消费者。清理是替代逻辑的附带收尾，不是一片无行为证据的“大扫除”。

示例：S03替换SnapshotBuilder → 输入捕获接口返回input_id/manifest → 保留文件变化拒绝/路径越界/未跟踪文件纳入断言 → 删除临时commit/bundle逻辑；S08仍用到Git受控环境/安全ref更新，所以这些函数保留。

禁用或不可达不算删除；新主链下继续维护旧wire DTO/同步包装/旧工厂也不算收束。反过来，保留普通本机Host/历史接口不算A兼容，因为它们仍有独立实际消费者。

## 5. 数据与代码的不同迁移

不兼容A意味着B无需读取旧A数据，不意味着允许销毁它们。新云端根目录与任务ID重新建立；Windows输入可重新冻结，用户真正需要的源代码/产物继续作为普通文件导入，不通过A数据迁移器恢复。

普通本机会话库/个人记忆不能被B初始化或schema清理误伤。若当前共享registry里同时含本机项目与A表，先保持本机可打开的历史schema链，再剥离当前消费者；不能把“无需A兼容”扩大为“无需任何本机历史兼容”。本地历史保留不要求B远端导入这些历史。

Spec142仍为未实施方案。B下它的“当前工作区复制、独立后续写入、稳定聊天边界”继续有效；其中复用A bundle/capture/publish的具体接线由143的新输入/结果/任务合同取代。先完成B闭环，再单独按142审阅/实施，不顺带加分叉功能。

## 6. 收束检查

S08.2已建立[机器消费者清单](../../release/a-consumers-s08.json)与`python -m scripts.a_consumer_inventory check`，当前冻结900条，包含源码revision、实际字节hash、静态/相对/字面量动态导入、lazy exports、传递装配、CLI/API、前端、脚本/打包与行为测试。check拒绝漂移，不自动批准；经审阅后才能显式freeze。保留`GitRepository`和native `GitInputError/git_path/object_id`真实B消费者，不据此保留A bundle协议。当前正式A仍使用，删除必须在S09.1实机门禁之后按清单核销，不在本片伪称零A。

最终从Windows Web/CLI分别跟踪：导入→任务→Runner→模型/工具→历史→结果→本机交付；失败跟踪断线后command_id对账、容器丢失、必要保存失败、patch冲突。所有已声明组件满足L1文件存在、L2实质实现、L3正式入口接线。

除人工搜引用外，加入架构检查禁止B主链导入envd/sandbox_channel/DockerFiles/DockerProcesses与bundle合同；检查正式包内容和CLI帮助没有旧A入口。历史文档/研究资料保留引用可以列白名单，不能把生产残留放白名单掩盖双栈。

检查从S01保护新B依赖；S08冻结A退出清单，记录源码SHA、真实消费者、替代入口、行为测试、包成员/构建/脚本及保留原因，新增A引用必须更新清单。动态装配/hook和CLI/API需沿启动链核对，不能只依赖import计数。S09.1删除门禁还包括真实模型、Windows业务HTTP/proto RPC、Web/CLI同任务、结果取回和断线保存；失败不进入删除提交，S11不再承担首次业务可用性验证。

权限清理签出额外列两栏：迁移的保护与明确不提供的保护。前者至少包含目录句柄/链接与特殊文件、受管进程回收/输出收齐、权限subject复核/根子归属（本机审批仍有消费者；B不新增远端审批）、活动保存失败/unknown占额；后者包含B同UID任务内Runner/state防篡改。不得把删除六项envd控制cap视为这些行为自动不再需要，也不得未经单独方案复活root控制/旧wire。普通本机仍用的算法和测试继续保留。
