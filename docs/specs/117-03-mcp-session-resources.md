# MCP-03：会话拥有的异步连接资源

状态：2026-10-20，用户批准上一轮03方案后已实现并完成本机验收。依赖01/02，不升级依赖。

## 目标与当前路径

02的list在同一任务内创建、发现、退出客户端。当前应用由AsyncApplication拥有loop，
aclose创建独立关闭任务；SessionService管理AgentSession，持久化删除有恢复/运行保护，
AgentSessionSubagents.ainvoke_subagent在driver结束后释放实例。直接把02上下文留在run
里会丢失跨消息状态，交给应用关闭任务退出又会违反SDK/AnyIO同任务进入退出合同。

03只做资源归属与正式生命周期接线，不注册MCP ToolSpec、不给模型MCP工具、不新增
Web端点/CLI执行命令，不改数据库/真实配置/版本，不实现TTL或重试。

## 接口与所有权

- `MCPSessionResources(settings, network)`：工厂在正式loop创建，构造不导入SDK或IO。
  深拷贝并校验配置，捕获显式引用的环境变量（缺失在首次连接时报错），不用整个环境。
  配置/凭证在本应用资源实例内固定；改变配置或凭证需要重启，不实现热刷新。配置身份
  使用进程内随机salt的HMAC，包含配置、网络与实际引用值，只传递不透明身份，不记值。
- `borrow(session_id, agent_path, server_id, *, control=None)`为内部异步上下文：key为
  (session_id, agent_path, config_identity)，借出SDK client及冻结catalog；调用不能留存
  或自行关闭客户端。manager不授予工具执行权限，04仍必须先走参数/审批/一次许可。
  SessionService.borrow_mcp同步核对已加载会话/活跃子实例，直接返回资源层上下文；03的消费者为确定性
  资源探针，04才接工具执行。原SessionPort暂不新增未被框架消费的hook。
- 每key一个专门owner task，用空Context避免继承run/审批状态；owner独自enter/exit
  SDK上下文，握手+read_catalog受02连接预算约束，ready后解除启动deadline。借用者
  共享ready，不重复创建。跨会话/子实例不共享客户端。
- 每server应用内一个semaphore，消费max_concurrency；排队与创建也纳入借用任务跟踪。
  调用异常/在途取消保守标失效，阻止新的借用，等同主体其他在途借用退出后关闭。
  下一次显式借用可以新建；绝不在同一次借用里重放业务请求。最后一个创建等待者取消
  时关闭创建中的owner；其他等待者仍在时不取消他们共用的创建。
- 提供control时，以既有requested信号轮询取消本次借用；记录本机制发起的取消，仅在
  没有外部取消剩余时转换为ToolException停止反馈。外部取消保持CancelledError。
  正常释放不关闭客户端；协议/网络故障不承诺远端副作用停止或业务状态恢复。
- SDK库日志在owner和借用调用上下文中均脱敏，覆盖不同任务发起的RPC；02 list不变。
- `release_instance/release_session/aclose`拒绝该范围的新借用，取消并等待已接受的
  排队/创建/在途借用，最后由owner退出上下文；借用者不得关闭自己以避免自等待。
  全部关闭可重复并抵抗调用方取消；释放单个范围后不留永久墓碑，正式入口检查已删除
  会话/已结束子实例，避免删除失败时让会话永久失去MCP能力。
- 关闭错误按原异常身份去重：已上送的同一对象重复退出仅诊断；新清理失败可观测并
  在资源管理器关闭时失败，不能伪报成功，也不覆盖当前业务/取消原异常。
  SDK读取预算的致命失败可能先取消其内部task group，旧协议DELETE属于尽力操作，
  可能尚未发送；本机HTTP transport必须关闭，远程遗留协议会话由服务端回收策略负责。
  不自行复制SDK协议清理或声称远端副作用/会话已经停止。

## 正常和失败链及接线

正常：工厂创建资源manager → SessionService与AgentSession借用同一个manager →
正式应用loop上SessionService.borrow_mcp(A,root,server) → 应用server槽 → owner握手/
目录 → 借出 → 探针一次RPC → 归还；A下一轮取得同owner，B/子实例另建owner。
普通run结束、审批暂停、页面切换均不调用释放。

失败：连接/目录失败 → owner退出 → ready固定安全错误；在途RPC失败/取消 → 记下原
异常、标失效 → 当前所有借用退出 → owner关闭；关闭重复同一异常只诊断，新错误留
待manager关闭报告。下一次显式调用可重新建立连接，但不重新执行失败的原调用。

应用关闭先关闭MCP借用入口、停止/收束借用，再排空原operations/tasks并关闭模型；
MCP清理失败仍关闭模型并保留第一错误。两个应用工厂均注入资源，装配失败关闭资源；
未连接时无外部资源。持久化删除在已有删除保护内、删除存储前release_session；
子实例driver最终结束（含停止/失败）release_instance，暂停审批时保留。实际结束路径
和异常身份测试覆盖，不把释放放到仅有历史记录的实例枚举里。

## 文件与验证

新增mcp_client/owner.py与resources.py；02 connections.py增加显式环境快照参数。
应用async_application/async_persistent、SessionService、AgentSession、session_controls、
session_subagents、session_persistence同步接线；不新增串行化字段/存储表。

先红灯再实现：事件同步的假连接验证同任务拥有、首次并发唯一创建、最后等待者取消、
跨主体server槽、取消、失效不重放、关闭拒新/幂等/新清理故障。真实SDK stdio/HTTP
夹具验证状态连续性、独立连接、JSON/SSE读取预算、PID/HTTP关闭；正式应用两轮run、
持久删除、真实子driver结束、装配失败与应用退出验证接线。模型用确定性离线实现，
远程业务服务/生产质量/五平台独立。全后端、构建、pip/AST/500行与本机wheel安装按
既有流程运行；未实现04的权限/转换闭环不能称Agent已支持MCP工具。


## 当前真实调用与分支验收

`build_async_local_application/open_async_persistent_application`在准备模型后创建manager，
传给SessionService，再由其创建/恢复AgentSession时带入同一对象。共享的是管理器与
server额度，各(scope,config_identity)条目内的ConnectionOwner/SDK client独立。
SessionService.borrow_mcp同步验证已加载会话、子实例running/未settled及当前publisher
归属，再返回manager.borrow上下文；没有yield代理执行通道。manager排队、创建或
等ready，owner._own在空Context任务中进入open_connection/read_catalog，ready返回
`MCPLease(client,catalog,config_identity)`。借用者只能在上下文内使用client；归还时
释放本次额度，正常连接继续等待owner.stop。

`AsyncApplication._close_resources`停止已载会话，关闭manager入口并收束借用，再排空
原operations/tasks并关闭模型。`SessionPersistence._delete`在原删除保护范围内先
release_session，再删除存储和移除会话。`AgentSessionSubagents.ainvoke_subagent`
最终finally内release_instance，保留原异常和实例清理；审批暂停仍在driver内，不触发
最终释放。失败删除不会留下永久墓碑；清理错误按scope保存，其他主体释放不受污染，
应用最终关闭仍会报告所有未确认清理。

下述探针模型只在确定性测试中直接借用资源；MCP ToolSpec注册、模型选择MCP工具、
审批subject与结果转换仍属04/05，不把三轮probe当作Agent MCP执行闭环。

| 分支 | 证据与结果 |
| --- | --- |
| 同根跨run/其他会话 | 正式本地与持久应用都运行A两次、B一次，SDK计数1/2/1，A的PID相同、B不同；应用退出均消失 |
| 首次并发/等待者取消 | 事件门控制握手，两个等待者只创建一个owner；取消一个不影响另一个，最后一个取消则关闭；owner首tick前也可关闭 |
| server额度/在途失效 | 跨会话共享额度；排队取消不关闭旧连接；并发2时一个失败不关闭另一个正在使用的client，阻止新借用，最后一个归还再关闭 |
| 控制/调用已提交 | queued/connecting/inflight均响应既有RunControl；真实stdio wait取消退出，drop在已记录副作用后丢响应，只提交一次；下一次显式借用新进程从1开始 |
| 生命周期 | 持久化删除只关闭A；真实child driver完成后关闭child而保留root，结束实例再借用明确拒绝；两个工厂装配失败关闭manager且没有预连 |
| HTTP | JSON/SSE、现代/legacy真实SDK；A两次复用client、B独立，正常legacy两次DELETE；JSON/SSE读取预算拒绝后本地transport关闭 |
| HTTP业务状态 | fixture故意共享服务端计数，A/A/B得到1/2/3；客户端隔离不代表远端业务隔离 |
| 清理故障 | 新故障不覆盖原业务异常，但资源最终关闭失败；仍释放槽位和借用记录；其他会话释放成功；日志不含注入敏感正文 |
| 禁用 | 独立Python进程创建/关闭真实App，未导入mcp SDK、没有owner；构造不启动进程/网络 |

## 验证结果（2026-10-20）

新增31项测试；首批7个资源测试、4个应用测试先观察入口缺失红灯。关闭故障的槽位泄露、
owner首tick前取消、清理故障跨scope污染、结束子实例错误均各有复现后转绿。首次完整
回归3158 passed/1 failed：既有native_session_lifecycle禁止SessionService模块含yield，
修改生产方法为同步校验后直接返回资源上下文后转绿；未放宽旧测试。部分补充传输/
并发断言首次就通过，未把这些冒称逐项TDD。HTTP致命读失败的DELETE假设按SDK源码
和实际失败路径修正为本地transport必须关闭，正常legacy的DELETE断言保留。

| 命令/检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_native_session_lifecycle.py tests/test_mcp_application_resources.py tests/test_mcp_lifecycle.py tests/test_mcp_resource_failures.py tests/test_mcp_resource_transports.py tests/test_mcp_settings.py tests/test_mcp_discovery.py tests/test_mcp_connections.py tests/test_mcp_cli.py tests/test_mcp_transport_policy.py -q` | 101 passed in17.63s，含MCP96项＋既有架构5项 |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 最终3160 passed in143.66s |
| `cd frontend && npm test -- --run` / `npm run build` | 44文件347 passed（4.30s），TypeScript/Vite成功，保留既有chunk>500kB提示 |
| compileall / pip check / AST及500行 / diff --check | 152份生产Python通过，No broken requirements found，exit0 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp03-20261020/wheel-final` | exit0，282文件，152份Python逐字节一致；依赖与02完全相同 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp03-20261020/wheel-final --output build/mcp03-20261020/install-final.json` | exit0，源码外全新安装：原生工具/前端API/Find-Grep/审批恢复/SQLite重开/CLI均passed |
| `build/mcp03-20261020/verify_session_install.py`（独立安装探针） | 另一全新venv、源码外安装包的正式App三轮run计数1/2/1，2个进程退出，0次供应商 |

安装专项命令：`.venv/bin/python build/mcp03-20261020/verify_session_install.py build/mcp03-20261020/wheel-final/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl tests/mcp_discovery_fixture.py build/mcp03-20261020/session-install.json`，exit0。
另执行正式App在MCP wait期间aclose的事件同步探针：run_stopped=true、进程退出、
submitted_calls=1，0次供应商，报告build/mcp03-20261020/active-close.json。

最终wheel SHA256 `0ab2cac9f9dd2c316a4858bad26f885ed5be613e75d37190cb19b1a98c42d3eb`；
archive-final.json、install-final.json、session-install.json的SHA一致。原01/02及学习工作
保留；未修改真实配置/数据库/依赖版本，没有commit/push/tag/发布。五平台、真实认证
业务服务、生产质量与用户掌握均不从本机测试推定。唯一下一片04先说明根Agent工具
注册/授权/调用/结果闭环方案，等待用户确认。
