# Spec143Q：S04.2b 根子、停止、后台边界与显式恢复

状态：2026-11-01用户“继续”授权实施，2026-11-02本机收尾；沿143p的NoAsk固定任务能力，不恢复远端逐工具审批，不跨S05。最终验证范围与实机限制见第7节。

## 1. 当前链与修改原因

本片开始前：owner runs → RunnerRuns持久回执/CAS → WS command → RunnerJournal → SessionService/create_agent → RunUnitOfWork同事务历史 → history ACK → finished；只装配单根工具。完成后存在后台工作时quiet=false，而Coordinator要求quiet=true才结束run，缺少独立静止更新。RunnerJournal把正常stopped错误归为interrupted；失败后无显式恢复路由，生命周期禁止带unknown启动，尚不能形成恢复闭环。

本片复用现有SubAgentSpec/子图、ControlQueue.stop、BackgroundProcessManager、SessionPersistence三阶段恢复及CommandProducers，不创建第二runtime、RPC框架或自动重试业务命令。沿A的正常停止/子驱动收束/输出关闭/必要保存与恢复测试逐项保留语义。143i的装配线、143a的同UID任务信任限制继续有效。

## 2. 接口与身份

- cloud settings与私有RunnerBootstrap增加subagents；运行配置按UTF-8 JSON限制48KiB并核对完整bootstrap不超过既有64KiB读取上限，避免预留任务后才发现无法读取；沿现有角色校验（第三处使用时提取公共解析），只允许现已注册的任务工具；子Agent不提供嵌套委派/后台命令。模型绑定使用同一catalog，模型网络/凭证仍待S05。
- owner `POST /api/cloud/v1/tasks/{id}/control`：command_id、expected_revision、expected_generation、kind=stop|recover、run_id（可空：仅无前台轮次时停止后台）、checkpoint_id（与Runner描述一致；从未进入图时为null）。回执复用GET commands；所有者鉴权显式可见。
- Coordinator→Runner新增control_command/control_query；Runner→Coordinator新增control_state/control_finished。命令冻结target root/run/checkpoint；每帧沿原generation/boot/epoch/connection fence，凭证过期拒绝新动作。重复同ID/摘要查询原结果，不作用于下一轮；不同摘要冲突。断线只query，不重放原run；显式重发stop/recover可继续相同ID的安全控制动作。
- CloudTask增加control_operation_id、last_run_id及runner_recovery只读描述；同事务预留控制操作，阻止新run/捕获/普通环境管理。保留独立命令回执与原根run状态；不以WS ACK当完成。
- owner原环境commands增加acknowledge_unknown=false。仅显式true允许保留workspace/state的强制停止，以及后端已确认旧容器不存在后的新代启动；不会隐式清unknown/原run。自动回收不传此标志。后端无法确认停止仍unknown、占额度，禁止新写入者。

## 3. 正常、停止与后台链

正常根子：settings→bootstrap→runner_tools注册subagent/list与task_list/output/stop→现有assemble_session_runtime(subagents)→根调用子图→子结果/独立thread归档→根必要保存；同任务共享工作区/依赖/NoAsk，跨任务不可使用。子失败/停止仍沿现有收束后保存路径。

普通完成与环境静止分开。根completed/stopped且必要历史确认后结束原run；quiet=false保留background_commands阻止回收，但允许下一轮查询/停止后台。Runner heartbeat的活动报告绑定last_run_id，不让旧quiet清除新轮次阻塞；根执行/控制/捕获/必要历史/unknown期间不允许活动报告解除保护。恢复描述只传kind/reason/checkpoint/原命令与run/未知调用ID，不上传工作区路径或完整checkpoint。

停止：owner持久预留→Runner持久控制回执→请求现有根/子stop并等待真实驱动收束→BackgroundProcessManager按session停止并收齐输出，不永久关闭管理器→原根必要保存→控制终局事件落盘→历史ACK→Coordinator完成stop回执。stopped是正常可续聊终局，不当作未确认执行。任务/HTTP/SSH断开不取消真实producer；停止失败/保存未确认不冒报成功。

## 4. 显式恢复与失败链

Runner在新boot先将running记unknown，不自动执行旧模型/工具。持久活动标记用于识别上一Runner未确认的后台工作；同代重启不能声称这些进程已消失，需物理换代。清理失败、应存在但缺失/损坏checkpoint、源身份/检查点不符或必要保存失败均保持blocked/unknown。已登记run但尚未进入图、实际分类为ready/never_run时，允许显式核对null检查点，仅结束旧命令、不创建伪检查点或执行图。

用户查到恢复描述后，以原run和checkpoint发起recover。Runner确认无在途根/子/后台写入者，封口旧SessionService并收束；用相同storage/checkpointer重新装配现有SessionService。未完成图只调用SessionPersistence.arecover(action=continue)的END→配对缺失结果→END三阶段，不调用模型/工具；源checkpoint保持不变。恢复提交把会话状态、必要历史和控制终局写同一事务；旧未知run标为已显式核对但不改称业务成功。此后仅新输入产生新run。

恢复保存中断保持原ID/原源checkpoint，允许按已持久恢复phase继续；不能以换command_id绕过尚未核对的控制操作。控制终局已落盘但ACK丢失，重连补发历史/原结果，不重复修复。仅后台未知且checkpoint已ready时，物理换代后可记录“旧后台/输出未确认”的显式核对，不伪造完整输出。

显式重复POST可投递同一个未终结stop/recover，沿原command_id/digest/原回执/原预留，不产生第二控制操作；断线自动流程仍只query。恢复必要事务已提交而内存actor安装失败时，任务保持blocked、不给Coordinator完成帧；原ID重试仅补齐arestore/actor装配，复用已提交终局，不重复历史/恢复事务，也不执行图。后续新run已开始后，旧控制请求只返回其原结果，不能改变新轮次。

WS在持久准入过程中取消时，须等本地准入与真实producer登记完成再传播取消；不能留下有running回执但无人执行的窗口。模型失败后次要activity/journal保存失败须记录且保留原模型异常；正常运行的必要保存失败则保持blocked。

## 5. 影响与验证

实际影响contracts/cloud_runner/cloud_tasks/cloud_lifecycle；cloud settings/bootstrap/control/runs/lifecycle与必要产物准入；Runner kernel/transport、新增actions及Coordinator runner_actions（与根运行、产物职责分开）；RunnerJournal；现有background manager的session收束；SubAgentSpec公共解析与AppSettings消费者；路由/CLI薄入口、锁表及当前文档。每文件≤500行；不修改真实配置/用户数据，不增依赖/升级，不提交部署。

先红后绿覆盖：配置/角色bootstrap往返与非法角色；根子真实工具/归档/后续复用；根/子模型阻塞及进程停止，后台跨轮、输出完成及静止报告，旧活动报告不能解除新run；stop重复/冲突/并发/跨代/ACK丢失、父保存失败；失败图显式恢复不重放未知Shell、源检查点不变、恢复各持久点中断/原ID重试；强停/后端停止不确认/换代准入。Coordinator不打开Runner库，控制终局先校验已确认历史；失联不清unknown。

沿标准native-assets环境运行专项与完整两分片、前端/构建、四目标wheel/同源检查/安装。最终Linux容器的进程后代收束/物理代际、Windows和真实模型/业务另验；本机确定性证据不替代这些门禁。S04.2b收束后唯一下一片S05。

## 6. 内部CLI与核对顺序

以下为本片实际入口；TASK_ID、COMMAND_ID、CONNECTION_JSON和REQUEST_JSON均替换为当前实例真实值。连接配置仍使用S07.1的私有CloudConnection文件，不新建通用SSH/代理协议，不改真实配置文件。

```sh
nyt cloud task show TASK_ID --settings CONNECTION_JSON
nyt cloud control stop TASK_ID --settings CONNECTION_JSON --request REQUEST_JSON
nyt cloud control recover TASK_ID --settings CONNECTION_JSON --request REQUEST_JSON
nyt cloud command show COMMAND_ID --settings CONNECTION_JSON
```

stop/recover请求字段：新的command_id（UUID）、当前expected_revision/expected_generation、kind与子命令一致。停止在途根时必须填当前active_run_id；无前台轮次时停止受管后台可以run_id=null。恢复必须填runner_recovery.run_id及其checkpoint_id，不自行猜测checkpoint；仅实际never_run描述允许null。重复同一动作使用同一个请求文件和ID，不能为绕过unknown生成新ID。

“停止本轮”是control stop，收束当前根子及该任务受管后台，保留环境和文件；“停止环境”仍为cloud task stop，请求kind=stop_environment。普通环境停止要求已经静止；若Runner崩溃、旧活动/控制unknown，所有者可在确认风险后显式acknowledge_unknown=true强停。查原命令直到后端确认succeeded且task的environment_phase=absent/lifecycle=stopped；unknown时只能对账原操作，不能启动第二写入者。随后以新操作ID、最新CAS执行task start并带acknowledge_unknown=true，等待新代Runner上报恢复描述，再control recover；强停/启动本身不会把原run改成业务成功。

这条逃生路径不允许覆盖artifact_operation_id；未确认捕获需沿原产物命令/提交链核对，不用恢复聊天解除结果保存保护。B无逐工具审批面板，本片只交付内部CLI/API；正式Web入口仍在S09。模型catalog未配置时仍waiting_model，不通过临时根key代跑。

## 7. 本机实现与验收（2026-11-02）

实现已接实际owner路由/内部CLI→RunnerActionCommands→带fence的WS→RunnerActions→现有SessionService/ControlQueue/SessionPersistence→RunnerJournal同事务历史→ACK→owner回执。配置角色、根子工具与归档、后台跨轮/停止输出收齐、正常stopped续聊及显式恢复均由确定性模型/真实本机Shell覆盖；不是只存在未调用的模块。正常和失败链按第3/4节，关键数据生产者与消费者见第2节。

新增35项，专项命令及实际结果：

```sh
PATH="$PWD/build/spec143-s042b-20261101/test-assets/bin:$PATH" \
  .venv/bin/python -m pytest tests/cloud tests/test_storage_dependency_direction.py \
  tests/test_subagent_execution.py tests/test_subagent_stop.py tests/test_subagent_continuation.py \
  tests/test_session_recovery.py tests/test_background_tasks.py -q
# 407 passed in 16.11s

PATH="$PWD/build/spec143-s042b-20261101/test-assets/bin:$PATH" \
  .venv/bin/python -m scripts.ci_shards --index 0 --count 2 \
  --output build/spec143-s042b-20261101/backend-verified-0.xml
# 3094 passed, 1 warning in 318.16s

PATH="$PWD/build/spec143-s042b-20261101/test-assets/bin:$PATH" \
  .venv/bin/python -m scripts.ci_shards --index 1 --count 2 \
  --output build/spec143-s042b-20261101/backend-verified-1.xml
# 3395 passed in 370.97s
```

合计6489后端，JUnit按classname/name逐项比较保留S04.2a的6454项、无移除/失败/skip；一个warning来自既有envd恶意重复METADATA夹具。新能力先红后绿，恢复检查点/必要事务失败、提交后actor装配失败、持久准入取消和显式原ID重试均有单独复现日志；既有能力由原回归继续覆盖，不把初始已通过的边界测试称作新红灯。为观测同ID显式重投递，新测试显式消费第二份相同WS命令，保留原回执/CAS/副作用断言；无conftest或旧断言放宽。

`npm --prefix frontend test -- --run`实际568 passed/69 files；`npm --prefix frontend run build`通过，保留既有chunk-size warning。标准stage_frontend/build_release构建macos-arm64、linux-x86_64、linux-arm64、windows-x86_64四份候选，Twine四份通过，452个Python文件逐字节同源且匹配当前源码，Mac源码外全新环境安装/正式smoke通过；`pip check`无冲突、compileall与git diff --check通过。最终1117份源码/测试hash无漂移；[verification.json](../../build/spec143-s042b-20261101/verification.json)关联XML、日志、安装与构建hash。初轮完整测试在最终修复后主动中止，保留backend-final-*诊断，不用于最终通过声明；backend-verified-*为最终源码重新运行结果。

清理/保留：角色解析在第三个真实消费者出现时提取一次，AppSettings/CloudSettings/RunnerBootstrap统一使用，删除AppSettings重复实现；删除CLI重复kind校验，保留网络前真实校验。新增两控制模块分别承担Coordinator所有者回执与Runner停止/恢复执行，不创建第二循环/通用代理。A正式运行/权限/环境仍有当前消费者，按S09切换时删除，不提前破坏本机/A入口；无新增兼容包装、依赖升级、真实配置/用户数据/Spec142变更。锁/预留/排空顺序已同步锁表。

限制：架构与安全独立复核任务均因额度不足未完成，不能记为通过；已做本轮源代码自查并为发现问题补先红后绿。强停/物理换代使用确定性后端替身，旧后台用持久dirty故障夹具；本轮未运行最终Linux容器/后代进程和Windows公司拓扑，没有远程部署、提交、发布或真实供应商/业务请求。S00既有用户报告不自动覆盖这些新增门禁；同UID任务信任限制不变。没有新增用户独立掌握证据。

唯一下一片S05：先核对当前模型装配/SDK和固定参考源码，详细Spec冻结原生协议、受限任务凭证/续期与固定上游；再以真实HTTP协议fixture测Chat/Responses、Anthropic、Ollama、流式停止/错误/重试归属，接Runner正式catalog。真实供应商/公司出站单独记录，不扩大到共享记忆/UI/自动更新；剩8独立单元。
