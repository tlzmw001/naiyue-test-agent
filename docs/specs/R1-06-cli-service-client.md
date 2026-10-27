# R1-06 CLI 服务客户端

状态：2026-10-24 主链及第 11 节三项异常尾项均已完成本机验收。未提交/发布。

## 1. 目标与现有数据流

实施前 nyt 默认 Web，nyt chat 为终端，命令语义保持。原 chat_startup 直接读取/绑定
ProjectStore，cli_async 的 AsyncCliApplication 在线程事件循环拥有 Application，
chat/审批/观测/任务通过 app.call(lambda api: ...) 调域接口。终端显示消费 RuntimeEvent。
R1-05 的共享服务已有认证握手、范围 HTTP/SSE、幂等回执与正常关闭流程。

本片让终端只拥有输入、显示和网络连接，Agent/数据库/模型由共享服务拥有；
不实现 Web 项目 UI、目录浏览、Docker 或远端执行，不修改真实配置/依赖/版本。

正常：两个 nyt chat → ensure_service → 同一实例 → 各自明确项目和 session →
POST run → 独立 SSE → 同一服务执行/保存；关闭一个持久终端不停止其 run 或另一终端。
失败：断线/回放过期 → 用原实例和游标重读/取快照，不重发有副作用命令；
服务实例改变或请求结果未知 → 明确提示，不能自动发起新 run。

## 2. 启动、项目与外部兼容

- 新增 chat --project UUID、项目列举入口；保持 --config/--workspace/--resume/
  --session-db/--new-workspace/--no-session 的明确意图与冲突检查。
- 正常项目由 --project 或规范 cwd/--workspace 匹配注册表；未登记需显示真实目录
  并取得显式信任确认，非交互调用需要显式信任选项，不能把模型文字当授权。
- 旧项目通过原 UUID/显式库导入，不按 cwd 重绑已保存会话；执行目录失效仍可看
  历史，新 run 拒绝。客户端不直接开库，选会话/恢复/必要保存都走服务。
- 旧 --session-db/--new-workspace 兼容准备在服务端复用已有 workspace 安全逻辑及
  显式导入；其写身份行为仅限这些明确的旧模式，新正常项目继续不写 marker。
  数据库锁/UUID/复制冲突检查保留，不能先开另一份同库应用。
  显式旧库因目录身份冲突不能导入时，仅 --resume 可使用独立只读历史入口：
  重新核验文件锁/库 UUID/会话与 checkpoint 归属，不登记项目、不创建运行宿主、
  不信任或重绑目录；模型配置缺失也能看历史。目录重定位不修改旧会话执行目录。
- 服务配置路径、数据位置和 host/port 沿既有 ServiceOptions；添加 --settings 以
  明确非默认配置来源，不写用户配置。缺少服务则受控拉起，有冲突则提示原因。
- 当前 nyt 默认 Web 启动器仍在 R1-07 接共享服务，不能宣称本片已支持其多窗口 UI。

## 3. 无目录服务与临时会话

原 --no-session 不创建身份/聊天库或开放 memory/history。R1-05 启动先开 chats
会破坏此边界，因此服务 Application 采用无默认宿主 profile：模型/PersonalMemory
对象和懒项目服务归根拥有，project/chats 仅在请求时打开。模型也按需加载；
空历史、列举会话、选择取消先走服务的元数据查询，不依赖模型配置，不新建聊天库。
选中可执行会话后才装配运行宿主。旧磁盘/RAM 工厂不变。

- 临时宿主调用唯一 assemble_host(database_path=None)，根/子 saver 和档案均 RAM；
  共享根模型和全局额度，memory/jobs 不注入，project_id=None。不登记项目或写 marker。
- 有目录临时会话使用经验证的本机执行目录，仍受 R1-01 准入；不回退到 cwd/主目录。
- POST /api/v1/temporary 创建有界票据（尚不建 RAM 宿主），返回 temp-UUID 范围。
  GET /api/v1/temporary/{id}/lease 建立独占 SSE 租约，在生成器保护内创建宿主；
  重复租约拒绝，断线后不可恢复该临时范围，必须明确新建。
- 租约与运行事件订阅分开：普通事件流重连不销毁会话；租约连接断开立即封临时
  准入，停止 run/子任务/后台工作、结算必要 RAM 保存、排空交付，销毁宿主和回放。
- 清理由请求作用域显式 finally 保证，不依赖 SSE 生成器析构。
- 清除该临时范围的命令回执正文和响应任务引用，防止已销毁聊天仍能从回执取回；
  关闭范围不能通过旧请求 ID 重新执行。票据/关闭事实仅保留必要元数据，数量有界。
- 服务关闭先通知全部租约结束，复用 R1-05 的先停止后等连接顺序，不能让空闲租约
  阻塞 Uvicorn。初始化中租约关闭会取消正在等待的宿主装配。显式终端退出等待临时关闭确认；异常断线由 SSE finally 完成同一清理。
- 服务锁/发现文件及工具外置归档仍可出现；临时聊天不写项目/chats/个人数据库。

## 4. 终端协议适配与显示

- TerminalClient 只拥有 ServiceClient 的网络事件循环；不持 Application、graph、
  工具定义、连接或数据库。使用明确 request/stream/close，而不是动态 getattr 代理。
- 删除旧 app.call 和拥有 Agent 的 cli_async。离线 storage 备份/还原和只读 MCP discovery 仍保留其已有适配器。
  终端输入/网络读取确需线程时保留，
  关闭网络线程不能等待持久 run 完成，也不能向服务发送全局 shutdown。
- JSON 事件解码为已有只读事实供 CliEventDisplay 使用，不重建任何运行能力。
  校验版本/会话/run/序号；格式或显示失败记录诊断并由快照补显，不能改变运行结果。
  GET/SSE 可有限次重连，但必须重新核验发现记录和握手仍是原服务实例；写请求不自动重发。
  普通 EOF 从已收到序号续读，只有显式回放过期才跳到快照序号；快照未终局继续等待。
  空闲观察也发现首个已快速完成的 run，断开/未知控制结果不转成新 run。
- 复用消息/工具/子 Agent/审批/进度/MCP 展示；完整消息/分页/错误不得因重写减少。
  历史读取、回放过期和大消息以已有有界查询补足，不伪造终局事件。
- 每次新消息/审批/控制生成一次 UUID，重试复用 ID 和原参数；服务端仍核验审批
  batch、origin_run、每项决定与 remember，不在客户端把许可当成已批准。

## 5. 命令和退出

- 保留 :model、:tools、:mcp-refresh、:usage、:diagnostics、:tasks/:task、:outputs、
  :approvals/:approve/:deny/:review、:stop（根/子）、Steering/Follow-up、:pending/:send。
- 审批准备与提交统一交给已有服务审批入口；CLI 只选择批次和逐项输入。
- :outputs 的预览/ClearPlan 留在服务端，返回确认 ID/数量/字节；客户端只提交确认
  ID，不反序列化可伪造的文件计划，保留宿主 guard/变化核验及忙碌拒绝。
- 控制以服务端 active_run_id 为准，不能用本地 worker 判定其他终端的运行；
  过期 run_id 拒绝。工具列表返回可选状态与不可用原因 DTO。
- 同会话忙碌拒绝新 run，保留待发送输入；停止后必须显式 :send，不能自动重发。
- 交互 :quit/Ctrl-C 关闭持久客户端，已接受运行留在服务；临时客户端关闭自身租约。
  管道输入沿现有顺序等待输出/审批结算，EOF 未决子审批仍明确处理，不能挂住。
- 终端显示验收以客户端实际写出的终局提示为完成屏障；服务端 `run_finished` 只证明
  运行已结束，不能证明网络事件已渲染。断言退出前已显示回答的交互测试等待
  `CliEventDisplay.write` 完成；多轮场景等待对应次数，原输出、历史和资源断言保留。
- 另一个客户端同会话发消息时，空闲终端通过只读状态观察发现新 run，再订阅其
  SSE；不重跑请求，不把别会话消息投到当前终端。

## 6. 服务补充入口

- CLI 启动/旧库导入/会话选择是已认证管理操作，项目/会话归属在服务端核验。
- 输出归档预览/确认只保存服务端计划，数量有界，使用一次并保留幂等回执。
- 添加 history 只读恢复视图所需的 checkpoint/recovery DTO，不能把框架对象传给终端。
- 既有版本 1 路由保持，新增入口同样使用认证、请求上限、幂等、错误映射和关闭态。

## 7. 影响文件与清理

application assembly/core/HostManager 扩展无默认宿主和临时宿主；资源拥有顺序不变。
service 增加临时租约、CLI 启动管理、输出确认及只读恢复 DTO，扩展 scopes/receipts/client。
CLI chat/startup/approval/background/observation/mcp/display 改协议；新增少量职责明确的
网络/事件适配模块，各文件<=500行。删除 cli_async，旧测试/探针迁移实际入口，
不是把生产旧桥复制进兼容层。Web 旧入口仍有消费者，留到 07。

测试模型与工具注入迁到服务装配 seam；若调整 helper/conftest，是为测试真实 HTTP
服务并保持环境隔离，不提供假成功、不放宽原业务/异常/权限/顺序断言。

## 8. 验收

先红后绿，分层验证：

1. 无默认宿主服务仅临时请求时无 registry/chats/个人库或 marker，RAM 根/子可执行。
2. 真 TCP 租约断开停止 run/后台工作、关闭 RAM/事件/回执，另一持久会话仍可用；
   断开前无宿主的票据不泄露资源，空闲租约不阻塞服务退出。
3. 两个真实终端进程连接同实例，独立会话并行、同会话观察和忙碌；退出一个另一可用。
4. 批次审批/remember/子停止/Steering/Follow-up/停止后显式重发/历史恢复/任务输出
   与清理/模型工具设置/用量诊断完整保持；新请求 ID、重复/未知结果与实例变化明确。
5. 无效配置、缺失执行目录、复制/旧库冲突、启动/取消/断线/输出失败的主异常与保存保持。
6. 已废弃桥无生产/脚本/测试消费者；应用不反向依赖 CLI/Web，storage 不导入 application。
7. 全后端、前端、TS/Vite、标准 wheel/Twine、源码外安装和新命令验证；不调用真实模型。

完成后只解释 R1-07；不提交、推送、发布或修改主目录并行 Spec129/XML 代码。

## 9. 当前接线与迁移收尾

正常项目聊天：chat.main → prepare_terminal → TerminalClient.request(terminal/prepare)
→ ProjectSelection.prepare → 返回 scope_id/root/sessions → 客户端 bind → 带明确范围创建
或选择会话 → ScopeServices.get → ProjectAPI/ChatAPI 打开对应宿主 → WebService 与
RunDelivery 接收 run。终端消费 SSE，经 event_fact 校验后交 CliEventDisplay；终局后
通过 terminal history/message 的 checkpoint 与 SHA-256 分块读取完整消息，补显最终结果。
运行请求只发一次；网络读取重连核对原实例，服务端仍拥有实际执行与必要保存。

失败例：控制已经被服务接收但响应连接丢失 → ServiceError → CLI 保留文字、提示未确认，
不把它发成新 run。目录消失 → 执行准入拒绝，旧会话只读历史继续可用。
后台任务查询/停止连接失败有日志并显示“未确认”，仅 task_cursor_invalid 显示分页错误。

临时链：POST temporary → pending 票据 → 独占 lease → active → RAM 宿主 → 会话/run。
关闭链由请求 finally 或显式 DELETE 驱动：封准入/通知结束 → 取消未完成的宿主装配 →
seal → 停根/子/后台进程及必要结算 → drain → 清除该范围回执正文/输出确认计划 →
移除宿主、子生命周期引用。失败继续尝试其余清理，保留主异常并记录次异常。
不持全局宿主锁等待慢清理，不定时扫描文件系统，不因临时断线停止其他持久会话。

删除项：cli/cli_async.py 与 tests/cli_portal_helpers.py；生产/脚本/测试不再消费
AsyncCliApplication 或 app.call。旧 CLI 测试改用真 TCP 服务、显式协议格式化 stub 或
测试侧宿主观察；保留业务结果、异常身份、顺序与权限断言。write 故障注入只针对用户
目标文件，避免把新增服务发现文件的原子写入当成被测 Write 操作。

保留项：Application 的原磁盘/RAM 工厂仍有 SDK、运行层和测试消费者；CliEventDisplay
原生 RuntimeEvent/历史方法仍由原生运行观察测试使用；离线 storage/mcp 命令仍有实际
消费者；Web 旧启动器和页面暂留到 07。新增 service_application 是无默认宿主的服务
装配入口，不是平铺领域模块，架构测试继续覆盖 projects 等领域不导入 CLI/Web。

完成的终端订阅仅保留最近一个完整结果及仍活动 Task/游标，已持 Task 的并发 wait
继续共享；多轮不会无限驻留每轮完整历史。诊断/消息显示失败只记观察故障，不改变
服务端的运行、权限或必要保存结果。

## 10. 最终本机验收（2026-10-24）

- 新增 30 项；完整后端 `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` → **5014 passed in 391.32s**。
- 新合同与故障专项 40 项；旧命令/最后安装检查专项分别见 build/r1-06 的日志。测试包括两个独立终端进程同实例、持久断开继续、临时根/后台清理、初始化取消、失败清理主异常、未知控制不重发、实例变化、回放过期/EOF、首个快速完成 run、8 轮有界结果缓存、输出确认归属/幂等/复用拒绝、信任取消和无模型元数据查询。
- `npm --prefix frontend test -- --run` → **424 passed**；`npm --prefix frontend run build` → **tsc/Vite 成功**。
- 标准 `scripts.build_release --target macos-arm64`、Twine、源码外独立虚拟环境安装通过；安装的 CLI 临时聊天走真实 HTTP/SSE，项目查询和新命令帮助也通过，原生工具/Web/SQLite/MCP/后台/子 Agent/记忆冒烟保留。测试使用固定模型，不是供应商效果验证。
- 最终 wheel SHA-256：`546b837ec891ab5a28468980071366bb063cabe042534814c629ba0e634a3ea2`；294 份包内 Python 与 4 份前端资源逐字节一致，937 冻结输入无漂移。
- compileall、pip check、diff --check 通过；生产/测试/前端源均不超过 500 行。合同与安全专项复核无剩余高置信问题。
- 汇总：[verification.json](../../build/r1-06/verification.json)。未改依赖、版本、真实配置或用户库；未提交、推送、合并、发布，不包含主目录并行 Spec129/XML 改动，无新增用户掌握证据。

唯一下一步：解释并批准 R1-07 的共享 Web 启动、项目/会话侧栏、输入区项目入口和目录对话框。R1-07 尚未实施。

## 11. 三项异常合同收尾（2026-10-24，用户已批准）

范围仅限本节，不进入 R1-07；第 10 节为收尾前验收时点。

1. 审批/根停止/子停止：本地参数错误仍提示逐项输入；服务已知的前置条件拒绝
   （例如 stale_approval/stale_run）明确提示未接受；连接/响应失败或无法确定结果的
   错误提示“结果未确认”，记录安全 code，不自动提交新请求。EOF 结算遇到停止
   提交错误退出到现有错误处理，不在循环里生成新 ID 重试。
2. SSE JSON/事件字段无效：拒收该事件并显式关闭坏流，记录观察故障，切换为同一
   已认证实例的 GET 状态轮询；只有 done 才读完整历史并补显。原会话/run/游标仍
   明确，不重发运行/控制，不伪造终局事件。状态/历史查询自身失败仍明确失败。
3. 关闭：临时 DELETE 失败仍取消/等待本地观察、关闭 HTTP、清除本地引用和网络
   线程；后续清理失败记日志且不覆盖最先异常。chat.main 的 finally 始终 join
   已有 worker；无原异常时关闭失败返回非零并提示清理未确认，有原异常则保留它。

正常链：现有审批/控制成功响应继续原流程；有效事件继续实时显示；正常关闭返回原
退出码。失败链：已提交后响应丢失 → 仅提示核对；坏事件 → 原实例 GET → 终局历史；
输入/运行异常＋关闭失败 → 记录关闭失败 → join → 原异常继续传播。

修改文件：cli_approval.py、chat.py、terminal_client.py；新增本节回归测试，不改协议、
数据库、依赖或真实配置。三处以上审批/停止消费者复用一个局部错误呈现函数，避免
同一未知结果合同在各命令中再分叉；不增加通用异常框架。Spec/交接同步最终证据。

验证：先观察失败测试，再最小修复；专项与最终复核稳定后运行一次完整后端回归及
标准候选构建/安装，冻结输入核对包内容。前端源码不变，不把旧结果当新增验证。

### 11.1 收尾结果与验收

三项均已接入当前消费者。参数错误保留原提示；服务拒绝与结果未知在审批/根/子停止
分别呈现，EOF 停止错误不循环重发。坏观察关闭 SSE 后切 GET 状态轮询，认证/实例
及状态查询错误继续明确失败；终局后经原完整历史入口交 finish_view 补显。关闭依次
尝试临时 DELETE、读流清理与 HTTP 关闭，清除本地引用、停止网络循环；首错保持，
CLI 在 finally 等待 worker，原异常保持或返回非零，并给出清理未确认提示。

- 初始 16 项：15 failed / 1 passed，确认缺口且保留原参数校验；最终新增 19 项。
- CLI 相关专项 63 passed / 16.05s；边界与真实 TCP 专项 31 passed / 5.39s，两组有交集。
- 完整后端一次：**5033 passed in 391.41s**。本片相对 R1-05 共新增 49 项，其中本次 19 项。
- 标准 Mac ARM64 wheel 构建一次、Twine、源码外安装一次通过；安装后的临时 CLI
  HTTP/SSE、项目查询以及既有原生工具/Web/SQLite/MCP/后台/子 Agent/记忆冒烟保留。
- 最终 wheel SHA-256：`b0cc77a4fd6864e19933d96511f401569798481667b2e119f00f90bd47444b66`。
  294 份 Python / 4 份前端资源逐字节一致，938 冻结输入无漂移。
- 仅 3 个生产文件＋1 个新增测试文件；compileall、pip check、diff --check、500 行限制通过。
  定向安全复核通过。前端源码未改，未重跑前端测试；第 10 节的 424 项为先前证据。
- [本次汇总](../../build/r1-06-closeout/verification.json)。固定模型/故障注入，不是供应商
  效果验证；未改依赖、版本、真实配置或用户库，未提交/推送/合并/发布，无新增掌握证据。

当前第六片按已确认范围收尾；下一步仍是讨论并批准第七片的具体实施小片，不提前实现。
