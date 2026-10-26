# R1-13D-04：沙箱额度、空闲停止与资源管理

2026-10-26用户“继续”授权本片。基线ce617d0，实际开发树project-execution-root，01—03未提交内容、真实配置/用户库/Docker资源及research保留。本片不提交/push；05真实双镜像验收与SSH14不实施。

## 1. 目标和当前链

目前EnvironmentService在项目注册库保存环境JSON，再创建容器/代码卷/状态卷；动作只按绑定锁串行，没有跨项目额度。EnvironmentAPI由应用根拥有，多个项目共享；ApplicationLifetime先停止运行、关闭SandboxOperationService与进程，再排空子宿主，但未停止容器。后台操作有持久journal及unknown/stale事实，不能只靠内存进程列表判断空闲。

本片新增真实消费者对应的机制：数据库短事务做跨项目预留；绑定活动门闩协调运行、RPC、输入准备与生命周期；owner loop定时检查空闲；根生命周期在运行与进程排空后停止容器。管理UI列出登记状态，使用原绑定启动、核对、停止和明确销毁。

参考边界：本机Pi agent-session.abort调用agent.abort后waitForIdle，强调先结束活动；它不自动证明Docker生命周期。AgentX ContainerCleanupService.suspendContainer先stop再更新状态、ContainerShutdownListener逐个停止运行容器；其删除异常后继续逻辑删除的做法不采用，本项目必须确认资源删除才释放额度。沿用本项目create_agent/现有关闭与journal，不引入运行时框架。

## 2. 配置和计数

```toml
[sandbox.lifecycle]
max_running = 2
max_retained = 10
idle_stop_seconds = 1800
sweep_seconds = 30
```

均为严格类型；running/retained为正数且running不超过retained，idle为0可关闭自动空闲停止，检查间隔正数有界。生命周期策略属于SandboxConfiguration，不进入SandboxSettings/会话快照/envd/模板指纹。固定文件刷新成功后用于后续预留与检查；不会修改既有镜像/网络/预算，也不因为降低额度杀掉已有任务或删除文件。

作用范围是当前服务/data_dir登记的全部项目。retained计入所有非destroyed记录；running保守计入除stopped/destroyed外的记录，包括creating/starting/unknown和未知状态。其他应用的Docker资源不接管。新建在数据库事务中检查两项并插入creating记录，事务结束才调用创建命令；start stopped时事务预留starting，只检查新增运行额度。已有登记的创建重试不再占一份额度；unknown在核对前不释放。已有保留量超过降低后的额度时允许使用/停止原环境，拒绝额外新建。

daemon/image inspect属于只读前置核对，额度拒绝不能创建容器或卷。停止确认后释放running；容器和两个卷都确认删除后才标destroyed、释放retained。失败保留unknown，严格核对daemon/ID/labels/镜像/卷归属，不调用全局清理。已停止资源仍保留文件，数量限额不构成磁盘字节硬配额。

## 3. 活动、空闲和竞争

EnvironmentActivity只用短内存锁保护每绑定使用计数和独占管理标记，不持锁等待I/O。正常run（包括压缩/审批恢复）、工具RPC/变更提交、输入准备持使用权；生命周期管理独占，遇到使用中明确env_busy。实际执行结束更新持久last_used_at；列表/状态/输出轮询不续期。创建写created_at/last_used_at，可空旧JSON不做猜测：旧记录无时间不自动回收，使用或启动后才记录时间。时间写入故障须记录并保守禁用该绑定自动回收，不覆盖原执行结果。

RunAPI在执行前取得使用权、准入再次核对绑定，排空后释放；因而检查和新run之间不会出现停掉已开始执行的窗口。后台命令即使root run结束也由journal保护；所有非终局、unknown/stale或输出未收齐的记录阻止自动停止。直接文件/进程RPC也经过同一绑定门闩。

自动检查只读取候选，真正停止前在独占门闩内重新检查时间、当前策略、journal和会话状态。持久running/interrupted/review_required（待审批/待恢复/待核对）阻止自动停止，不为检查加载模型、图或连接MCP。人工stop也拒绝未结算后台操作；明确确认destroy可清理未确认资源，但不能和本应用正在执行的run/RPC重叠。recover只核对原资源，不创建替代物；观察到外部启动导致超过额度时记录真实状态并限制以后新增，不隐藏事实。

周期任务由EnvironmentAPI的资源拥有者管理，刷新可唤醒读取新策略；关闭先封入口、停止检查并排空原操作/进程/运行，最后停止所有登记且未销毁的原容器，保留卷。一个容器失败不阻止尝试其他容器，错误按原主异常/二次清理规则传播。关闭客户端不等于关闭常驻服务。

## 4. 外部接口与页面

- `GET /api/v1/environments`：返回当前策略、登记计数和未销毁项（绑定/项目/会话ID、项目名、保存模板名、登记生命周期、时间、固定错误码、使用中标记）。不返回settings、Docker连接、镜像凭据/路径；列表不逐项探测Docker，明确标为登记状态。
- `POST /api/v1/environments/{binding_id}/{action}`：action限start/stop/recover/destroy；严格对象只允许confirm_destroy布尔字段，destroy须true。沿用全局认证/实例/Origin/X-Request-ID与幂等；动作只针对原登记绑定。已存在会话的原动作入口接同一服务逻辑。
- 新建额度不足仍按现有会话准备合同保留原session/input/request，不创建环境；状态在环境尚未登记时投影当前额度阻塞码，释放额度后核对原准备，不新建请求。
- 输入框上沿执行区域增加“管理沙箱”入口，打开跨项目列表；当前会话运行期间仍可管理其他空闲沙箱。展示运行/保留计数与上限、登记状态、空闲规则、启动/核对/停止及销毁确认。刷新仅重读列表，动作完成后更新；unknown显示待核对。销毁说明容器/代码卷/状态卷会删除、会话和已取回结果保留。列表分别返回busy活动使用权和unsettled未结算操作，后者允许明确确认销毁作清理。
- 无记录显示空白说明，不造示例；复用现有图标、浮层、主题/焦点/窄屏样式，不另做设置编辑器或实时Docker仪表盘。

## 5. 影响范围、测试和验收

配置/合同、environment_registry、EnvironmentService和新活动/管理协作者、DockerEnvironment生命周期、EnvironmentAPI/ApplicationLifetime/RunAPI、操作与输入RPC接线、service routes、前端api/types/管理组件及环境入口、文档/测试。SessionService已500行，不往其中增加职责。无新依赖或SQL表；JSON字段可空兼容，储存短事务不跨provider I/O。

先失败测试：配置和指纹隔离；跨项目/并发额度与预留；启动/unknown/降额/双卷删除确认；活动门闩与run准入竞态；时钟注入的空闲/后台/审批/只读不续期/旧记录保护；关闭排空顺序、二次失败、取消；认证/幂等/信息投影；前端空态、额度提示、原动作/销毁确认、焦点与错误不丢状态。

测试SandboxTransport当前只模拟create/validate，不模拟容器停止；按新生命周期合同扩展其状态/动作，保留原身份/进程/输出断言。重开会话测试先显式启动原停止容器再继续，不允许为旧断言保留退出时继续运行的旧语义。其他局部stub同步新增仓储/活动依赖，不使用生产getattr兼容测试，不删/skip/放宽断言。

最终专项、全量Python/前端、TS/Vite、标准wheel/Twine/源码外安装；隔离服务/受控Docker运输检查桌面/375px管理流程，不操作真实资源。真实Docker生命周期及两镜像组合统一留05。收尾写实际命令/结果与正常/失败主链，更新handoff唯一下一步。

## 6. 实现主链和验收结果

正常新建：SessionAPI.create_sandbox先保存含binding_id/input_id/selection的会话准备事实 → prepare_sandbox_session全程EnvironmentAPI.using → worker EnvironmentService.create在同一EnvironmentRegistry事务检查额度并reserve creating → DockerEnvironment创建/握手 → 必要保存running → 输入安装并确认。created_at/last_used_at为可空JSON事实，不加SQL列；环境ID和快照仍取已保存选择。额度满时无容器/卷创建，会话保留，environment_status显示当前额度阻塞；停止其他环境后对原sid继续准备，不创建替代请求。

正常运行：RunAPI._execute或regenerate在prepare_admission之后、confirm_admission之前取得execution_use → 原生命周期排空模型/工具/保存 → 更新时间并释放使用权。输入安装和结果捕获也覆盖整个异步操作。管理动作先取得互斥标记，worker按原ID与daemon验证身份，停止确认后记stopped；销毁检查容器、代码卷、状态卷归属并逐项确认缺失才记destroyed。关闭根应用先排空会话/操作/进程和子宿主，再stop所有未销毁记录；登记stopped也核对，覆盖外部手动重启。

失败链：creating/starting或生命周期失败保留unknown并占额度；管理遇活动使用权立即env_busy，不访问provider。sweep在管理前后检查unsettled，refresh整次只读使用权，本地竞争不改后台行成stale；采集器遇env_busy重试。后台首次终局且输出结算更新一次last_used_at，之后观察不续期。时间保存故障日志可见并保守禁用该绑定自动停止，不覆盖执行结果。单个容器关闭失败仍尝试其余容器并保留首异常。销毁不覆盖已知container_id，缺失回执不假设成功；HTTP缓存原请求结果，前端未知响应仅允许同ID核对。

先红后绿：配置/额度/空闲/关闭12红，HTTP缺接口1红，前端缺组件红；重生成/捕获使用权2红、空闲观察1红、后台终局时间1红、外部重启退出1红、容器ID不匹配1红后转绿。浏览器还发现按钮禁用后的焦点离开管理页，修正后实测焦点留在dialog；jsdom不能自动模拟浏览器禁用失焦，测试显式产生blur并核对，不能将这一项的首轮通过声称为红灯。

测试替身迁移只补真实合同：操作服务显式注入活动仓储；Docker替身增加停止/启动/销毁；重开显式启动原停止容器；输入安装的不可变字段仍逐字段相等，另断言可变使用时间不倒退。未修改conftest、不skip/放宽异常、权限和原绑定断言。原未结算controller-only行在对应测试断言后按原ID清理，因为没有远端进程。

实际命令与结果（工作树build目录保存日志）：

```text
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python -m pytest --collect-only -q tests
5489 tests / 528 files
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python build/r1-13d-04/run_shard.py 0
2745 passed in 346.66s
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python build/r1-13d-04/run_shard.py 1
2744 passed in 300.08s
cd frontend && npm test -- --run
64 files / 533 passed
cd frontend && npm run build
TypeScript + Vite passed, 0 errors; existing large chunk warning retained
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-13d-04/final-dist
.venv/bin/python -m twine check build/r1-13d-04/final-dist/*.whl
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/r1-13d-04/final-dist --output build/r1-13d-04/final-install.json
all exit 0; 345 Python / 4 Web / README bytes match current source
```

普通根目录pytest误收集research克隆及独立实验，收集缺少它们自己的依赖；按本项目CI入口限定tests后全量通过，不安装调研依赖或修改调研资料。归档/Spec等文件变化没有冒充功能测试。

IAB在真实认证临时服务、受控Docker运输及真实envd dispatcher中检查桌面1280×900、375px窄屏和明暗主题；完成停止→原绑定启动→单独确认销毁，额度与行状态同步。服务退出确认停止剩余原绑定（ui-shutdown-facts），0模型供应商请求。预览脚本首轮先撤销替身再关服务导致收尾只读Docker探测失败，修正ExitStack逆序后重做全流程、exit0。没有修改产品来迎合测试，不操作真实容器资源。最终[桌面](../../build/r1-13d-04/management-desktop.jpg)、[窄屏](../../build/r1-13d-04/management-mobile.jpg)、[销毁确认](../../build/r1-13d-04/destroy-confirm-mobile.jpg)、[深色](../../build/r1-13d-04/management-dark.jpg)，机器证据在build/r1-13d-04/verification.json；截图中的数据均为临时验收记录。定向架构/安全复核无剩余阻塞，不等于形式化无死锁或真实Docker证明。

已完成01—04，本片未提交/push。唯一下一步05：使用本机真实Docker验证两套兼容envd镜像、多会话原绑定隔离、热更新只影响新会话、额度与空闲、退出保留文件/重开、明确销毁双卷与故障恢复；只清理明确登记为验收所有的资源。SSH14仍待后续单独批准。
