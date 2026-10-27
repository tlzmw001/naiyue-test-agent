# R1-12B：新会话执行位置、持久绑定与沙箱运行

状态：2026-10-25，用户在12A交付和12B方案说明后授权继续；本片已完成本机验收；未提交/push/发布，13/14未实施。

## 1. 本片目标与当前链

12A 已有 prepare_input/query/install_input、Docker文件/进程/任务能力及一次安装；这些后端尚未由产品会话选用。当前 SessionService 准入只解析 Local ExecutionRoot，RunLifecycle 在短锁里构造 HostFileSystem/HostProcesses；上下文预览/恢复审批重新构造 Host，子实例只保存本机根；Web/CLI 的创建请求只有 model_id。

本片把新会话的 Host/Docker 选择接到上述真实消费者。项目身份仍管理历史/记忆，执行位置由会话绑定确定；普通对话/临时会话保留原行为。既有会话不通过项目菜单换绑；沙箱失效不回退、不自动重建。13交付/14SSH、用户级Skills复制、凭据历史净化不在本片。

## 2. 数据合同与迁移

项目库19→20：增加独立项目身份表，记录 project_id/source_kind，旧Local绑定迁入；GitSource 身份没有伪造的本机路径。原 naiyue_workspace 表继续承载旧Local位置，既有本机导入/重定位合同不改变。Git登记保持不创建库；首次打开Git历史宿主时通过已有注册操作日志初始化真实项目库，不fetch/执行项目内容，打开历史不要求信任；新执行始终核验信任和生命周期。

会话新增 execution_setup_json，保存不可变创建请求（model_id/include_untracked）、input_id和binding_id。execution_binding 增加 sandbox，此时执行根/工作区保存字段为空，绝不把 /workspace 变成本机 Path。binding_id 指向注册表的既有环境记录；input_id 指向12A输入记录。子实例新增 environment_binding_id，复用原execution_binding标记；备份结构/读写/迁移/恢复同步校验。旧本机/普通对话行保持语义。

创建请求ID由调用方提前确定；项目+请求ID派生固定session/input/binding ID。会话预留先保存sandbox类型和请求，后续失败不会留下可在Host执行的会话。相同ID相同请求查原记录，不重复建会话/容器；参数不同冲突。创建中/失败事实可查看，允许显式核对/继续原准备，unknown不凭重试重建容器或更换输入。永久输入失败需新建会话，不静默换一份输入。

GitSource 的信任是显式独立管理动作，带当前项目revision及自己的HTTP请求ID；不会因fetch/打开历史/继续准备自动授予。只有已确认信任且active才创建沙箱/开始run；无本机来源的项目拒绝Host。LocalSource原有信任逻辑继续有效。创建请求不混入可变的trusted字段。

## 3. 准入与能力合同

准备阶段在事件循环外读取登记/环境并验证，构造一次本次执行能力（environment/files/processes/task_queries）；回到循环复核会话快照、项目revision/目标仍可用及宿主未关闭，再提交已有全局准入。短运行锁只安装已准备能力，无Docker/文件/SQL IO。环境start/stop/destroy/recover在相同准入锁与会话表短锁下预留管理状态，原子拒绝已有run；Docker动作不持这些锁，结束才释放预留。新run/压缩/审批续跑在准备前及正式准入时检查预留，避免ready检查后的竞争。

Host 使用原 ExecutionRoot 校验；sandbox 的 ExecutionRoot 为空，EnvironmentRef 是冻结的执行事实，工具只读能力对象，不读来源路径。文件/进程目标由Docker记录验证，既有每工具校验保持。run资源发现、预览、压缩、审批恢复、根/子Agent、任务工具和Web/CLI任务列表均用同一绑定；不为沙箱丢掉读取Skill的资源许可。

根/子审批准备及恢复比较原environment主体和当前绑定；停止审批只配对结果，不要求环境在线。子实例首次记录binding_id，续跑比较原绑定并验证；根项目重定位不改变沙箱位置。进程输出/stop/list沿原操作可用，不借用Host后台任务表。

history/memory归属继续只按项目/会话/库核验。沙箱使用环境能力验证执行范围，不对远端根做本机stat。记忆整理仍在控制端处理已保存聊天，不给记忆后台任务文件能力；沙箱会话的项目/个人记忆不因本机路径为空而消失。原本机通知和记忆任务与沙箱进程任务按真实拥有者路由。

## 4. API、界面与失败

保持原 model_id 创建请求有效，默认Host；增量增加execution=host/sandbox、include_untracked和请求ID，Git信任独立命令。使用原认证、实例握手、HTTP幂等与错误映射，不新增连接协议。领域SessionAPI增加明确的sandbox创建/准备入口；Web/CLI共用，不让客户端任意指定容器ID或借用另一个会话的binding。

状态返回execution描述、准备状态/错误和可执行性。准备失败保留会话和原请求，页面显示明确原因并提供核对/继续原准备；创建期间禁发送，unknown不当成功。历史可打开，停止/销毁等生命周期由应用核验原binding后执行，不自动清空代码卷。

继续仅限input已ready后的原容器/安装核对，input停在preparing/failed只能显示事实并提示新会话；不扩大12A输入恢复合同。准备与删除复用会话history_lock，准备中拒绝删除；已有未销毁沙箱也拒绝删除会话，必须显式处理环境/产物，不能自动销毁或遗留孤立进程/卷。项目删除同样核对Git项目库和存量环境。

Web沿用用户给定桌面端参考：项目选择旁独立显示“此计算机 / Docker沙箱”；新会话可选，已有会话显示实际绑定。提供输入清单说明与未跟踪文件明确选择；忽略项不可选，错误只显示受检相对路径，不显示敏感正文。普通对话不出现可执行沙箱选项。

视觉方向：沿当前应用的中性表面、轻边框、紧凑圆角与现有字体；用CSS Modules，不新增UI库/字体/图标依赖。定位→状态→动作，选择器键盘可达、焦点可恢复，桌面和375px检查。参考既有ChatGPT桌面截图的项目/位置分离；当前项目目录对话框的显式信任和操作核对；已有会话菜单的只读状态呈现。交互沿现有hover/focus，不添加装饰动画。

CLI新增明确环境参数和未跟踪输入选择，--no-session不允许sandbox；恢复会话使用原绑定，不能用参数换绑。GitSource来源与信任如实显示，无本机路径时不构造Path(None)。

## 5. 正常与失败链

正常：Web新会话选择Docker→服务创建请求预留sandbox会话→12A准备输入→既有环境create→install原input→确认→会话prepare/状态→run准入准备能力→模型/工具在/workspace执行→必要保存聊天/子实例/历史/记忆→重启解析同binding。

失败：容器不存在→状态标env_expired且历史可读→新run准入拒绝，文件/进程无Host回退。安装回执丢失→查询固定operation_id，completed才确认，unknown保留原状态。审批记录来自另一binding或容器替换→过期，必须重新审查；停止审批仍可收尾。

## 6. 影响文件与验证

storage：execution_storage/migrations/backup_schema/project_databases/sessions/subagent_sql，相关升级/备份/还原探针；contracts：执行能力和数据合同；application：host装配、sessions准入/执行/恢复/预览、subagents记录、history/memory/task路由、environment生命周期；service/web：请求/状态/错误与信任；CLI入口；前端api/types/useConversation与位置选择组件。新增协作者只用于上述真实替换点，文件超过500行时按现有职责拆出，不保留并行Host构造链。

先写并运行失败测试，再实现：

1. schema20迁移，Local/chats语义、GitSource无伪路径身份、备份还原/重开，错项目/损坏setup拒绝。
2. 正式SessionAPI创建sandbox、同请求幂等/冲突、准备失败和unknown核对、必要保存失败不可执行。
3. 正式run/资源/预览/审批/子实例续跑/任务查询使用原绑定，过期/替换拒绝且不回退；停止与历史仍可用。
4. 项目/个人memory/history在sandbox可用，控制端记忆任务不访问远端路径；原Host不回归。
5. Web认证/幂等/错误/输入/信任，CLI新建/恢复，UI新会话选择及准备失败/已有绑定显示、窄屏/键盘。
6. 现有本机Docker两个真实会话正式run写同名文件隔离，重启/审批/子实例及失效拒绝；不请求真实供应商。

独立架构/安全复核、对抗检查后跑完整后端/前端、标准构建/包内容核对/源外安装。新锁/顺序同片更新lock-hierarchy；不动线程池/全局归档锁优化，不改真实配置/用户库/依赖，不提交发布。


## 7. 当前接线与可检查主链

正常例：项目P中新建沙箱会话S，请求选择模型M和`notes.txt`。HTTP `/scopes/P/sessions` → `SessionAPI.create_sandbox` → `execution_binding.create_sandbox_session`：P与请求UUID派生S/I/B；先由`SessionService._create_session`必要保存`sandbox`标记和以下setup，再开始Git/Docker IO。失败请求的S仍可列出，不存在回到Host的默认分支。

```json
{"model_id":"M","include_untracked":["notes.txt"],"input_id":"32hex-I","binding_id":"32hex-B"}
```

`prepare_sandbox_session` → `EnvironmentAPI.prepare_input`（12A输入仓储I）→ `EnvironmentService.create(binding_id=B)` → `install_input(B,I)`；已登记的creating/unknown环境不重建，安装仍只核对原操作ID。Web的`prepare`/`status`返回execution状态，失败返回受检相对路径。创建请求ID、初始模型和文件清单由页面冻结；信任使用另一个请求ID与expected_revision，不混入可变的创建参数。

`RunAPI._execute` → `SessionService.prepare_admission`在事件循环外解析B，`resolve_capabilities`产生冻结的`PreparedExecution(environment,files,processes,task_queries,project_revision)` → `confirm_admission`复核原绑定与revision → `_admit`复核宿主/会话快照/环境管理预留 → `RunLifecycle.admit_run`安装能力。远端身份是`RunIdentity(project_id=P,execution_root=None)`及`RunScope.environment=B`；`/workspace`只由sandbox环境按POSIX解释，不参与本机Path/stat/cwd。工具资源、预览和审批恢复都从原文件能力重新发现资源。

子实例`SubagentRegistry`沿父run的文件/进程能力调用，`subagent_records.admit`比较P/S/B并保存environment_binding_id；重开续跑仍用原实例与checkpoint。根审批恢复用当前B重新准备原subject，原execution_environment不匹配则过期；离线仅恢复待审事实供取消，不还原执行许可。

`MemoryAPI`仍验证项目库UUID及根/子调用身份；sandbox进一步核对同一个files能力和environment，不做Host目录校验。项目库的`workspace_binding`对Git提供UUID和None路径，`ArchiveReader`及概览仓储共用它；`MemoryJobs._execution_path`返回None，后台整理只读控制端已保存聊天。个人库仍由Application共享。`TaskAPI._queries`按每条已登记沙箱绑定加入真实任务拥有者，再与原Host/记忆任务合并；输入尚未建环境时仅缺少该进程拥有者，不掩盖其他会话任务。

失败例：删除/失效B → `environment_status`保留历史并报告不可用 → 新run及文件/进程校验拒绝，子实例记录保留。`environment_action`在S1内取得准入管理预留，整个Docker动作结束后才释放；先开始run时busy拒绝管理，先管理时run/压缩/审批续跑拒绝准入。正式Event回归及真实销毁后run拒绝已覆盖。

迁移收尾：旧`_aprepare_saved_configuration`纯配置异步壳已删除，由`session_recovery.prepare_saved_configuration`直接供三处调用；同步纯函数不做IO。旧历史库fixture和release_storage_smoke同步移除schema20字段后再构造旧版本，原升级/备份/还原的内容比较保留；结构断言同步增加setup/环境字段及管理contextmanager，没有减少权限、配对、事务回滚和异常断言。新增执行状态带来的小frame回归已由剩余字节预算收缩消息预览修复，保留2048字节上限及分页身份。


## 8. 本机验收（2026-10-25）

- 后端：全量两组`2654 passed in 279.65s`、`2673 passed in 270.44s`，补充GitSource控制端记忆整理`1 passed in 0.95s`，合计当前collect的5328项；两个组均exit0，补充测试已加入可复跑的完整文件清单。命令：`.venv/bin/python build/r1-12b/run_full.py`及`.venv/bin/python -m pytest -q tests/test_sandbox_memory_jobs.py`。旧备份/还原/源码外release_storage_smoke保留内容及异常检查。没有真实模型质量证据。
- 前端：`npm --prefix frontend test -- --run`，57文件/463项通过；`npm --prefix frontend run build`，TS/Vite exit0。创建模型及文件清单在未知回应后保持原请求，凭据路径受检展示，永久输入失败提示新建会话。IAB真实浏览器走通创建/打开原沙箱会话，1365及375px检查，Escape回到选择器；截图仅临时合成数据，见build下`ui-*-shipped.jpg`。
- 标准构建：`scripts.build_release`分别构建Mac/Linux ARM64候选；`twine check`两包PASSED；330 Python/4 Web资源与当前源码和dist逐字节一致，MIME解码后的README逐字节一致；1043冻结输入无漂移。最后五处变化仅为已重验的前端细节，无Python变化。
- 源外安装：`scripts.verify_wheel_install --wheel-dir build/r1-12b/wheels-macos-shipped --output build/r1-12b/install-macos-shipped.json` exit0；pip check、native工具、CLI/Web、配置、history、备份/升级/还原/重开、子实例和记忆探针通过，0供应商请求。
- 最终镜像：由上述Linux标准包及固定Python基镜像构建`naiyue-envd:r1-12b-reviewed`；`scripts.verify_sandbox_sessions`与`verify_sandbox_inputs`均通过。正式SessionAPI/RunAPI两个沙箱并行写同名文件隔离，同创建请求不重复，实际Skills/上下文/任务、子实例、重开审批及子实例续跑、销毁后新run拒绝通过；原输入bundle/隐藏Skill/忽略项/凭据/重复安装/重开也通过。所有探针0供应商请求，测试容器/卷清空，专用VM/镜像保留，默认context仍default。公司SSH与外部网络策略不由本机结果代替。
- 架构/安全独立只读复核的问题已关闭；新增Event、事务/身份/HTTP/未知结果回归，锁表已同步。关键IO没有静默成功或Host回退。

详细命令、镜像ID、包哈希和证据索引：工作树`build/r1-12b/verification.json`；本次没有修改真实settings/.env/用户库、依赖或主目录并行工作，没有提交发布。

下一步仅讲解R1-13：取回output_commit/bundle、受控导入/增量diff审查、只建引用的分支与严格干净线性合入、Git/SQLite操作对账；13实施需下一轮明确授权。14 SSH及R2仍在后续。
