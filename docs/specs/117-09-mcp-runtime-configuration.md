# MCP-09：运行中新增、工具名单与服务启停

状态：2026-10-20，用户批准后已实现并完成本机确定性/安装/页面验证，插入06之后；07 TTL及08交付验收后续独立推进。
本片不提交/发布、不改真实用户配置/数据库、不升级依赖、不实现TTL或自动文件监听。

版本边界：本文主体保留09实施快照；后续07接入后正TTL不再拒绝，当前空闲行为以
[07 Spec](117-07-mcp-idle-reclamation.md)和使用说明为准；启用中改TTL仍沿09的连接配置变更边界。

## 当前链与目标

两个工厂启动时创建MCPSessionResources、临时发现、注册ToolSpec，再装配SessionService
和共享图。图本已支持aregister_tool、每次模型请求重建活动视图、旧批次冻结。当前MCP
配置/凭证值/目录只在启动读入，注册表只追加，资源配置身份混合连接字段与allowed_tools。
Web已有会话选择与只读目录刷新；CLI桥有方法但终端没有工具选择或配置刷新命令。

目标：零MCP启动后，用户编辑用户级settings.toml，明确刷新即可新增stdio/HTTP服务、
增减allowed_tools、以enabled停用/重启用；现有Web菜单/CLI可选用工具。新增注册不自动
扩大根会话活动集合或子角色名单。历史、工具选择和现有图/模型保持。

范围排除：任意Python模块热加载、内置工具替换、自动执行业务工具、服务安装、设置
编辑页面、Web任意文件路径读取、模型/权限/网络/子角色配置热更新。已有启用服务的
地址/命令/凭证/预算等连接配置变化本片明确报需重启，不静默忽略；停用后可在下次
启用时采用新配置。默认TTL仍0，正值拒绝。

## 结构与接口

本片涉及十余个已有生产文件及测试。新增mcp_client/refresh.py，作为现有manager的
配置装配辅助函数，不建立第二个管理器/注册平台；若CLI主文件接近500行，命令呈现
放application/cli_mcp.py。必要状态都由现有MCPSessionResources拥有。

配置读取：新增load_mcp_settings(path=None)，只校验mcp段，默认用户settings.toml；
文件不存在/解析/校验失败保留全部已生效状态并返回安全错误，不回退空配置。显式空
mcp.servers表示停用所有服务。运行中不重读模型、network、permissions或.env。

应用新增arefresh_mcp(settings_path=None)、get_mcp_status()；path仅程序调用/测试，
Web POST /api/mcp/refresh无路径入参，沿现有认证/Origin合同。CLI在当前chat进程提供
:mcp-refresh、:tools查看、:tools +name -name增减当前会话选择；不是独立nyt mcp
进程修改另一个应用。现有get_registered_tools保持返回ToolSpec。

刷新返回{servers:[{server_id,status:enabled|disabled|error,selected_tools:[原名],
draining?:bool,error?:本地枚举}]}。GET /api/session-controls补mcp_servers；MCP工具项
补available及unavailable_reason，不投影地址/命令/env/header/身份。Web在工具菜单加
MCP配置刷新按钮、刷新结果和服务状态；不可用项保留来源但禁用勾选。无数据留空。

## 身份、提交与连接

manager保留随机salt、启动网络配置、原输出预算、全部服务配置与已发布目录。
连接身份从连接相关配置和凭证引用值生成，不含enabled/allowed_tools；同一服务仅改
工具名单时复用原连接/进程。独立的授权配置身份在名单/目录/启停变化时更新；未变
服务的身份保持。MCPPermission身份仍只有原四个字段，schema/DB无需扩展。

manager串行处理刷新，按服务准备再发布：
1. 未变服务不建发现连接，不动身份/工具/owner。
2. 新增/启用/名单变化建立临时ConnectionOwner，发现并验证所有目录；finally退出。
3. 验证全部待发布ToolSpec及原权限规则；原注册名若属于内置/其他来源则拒绝。
4. 在同一应用loop无await的提交段发布该服务配置、目录、授权身份与ToolSpec。
   注册表先验证完整候选再替换该服务项，失败不留下部分工具；其他服务保持。
5. 失败只返回安全本地错误，保留该服务此前有效配置；其余有效变化独立处理。

ToolRegistry新增受MCP来源约束的批量发布方法，不开放普通工具同名替换。移除/停用
不删除历史ToolSpec，而是由其MCPPermission引用现有资源manager核对可用性；保留
实际已发现的定义供保存名/历史/旧批配对，不伪造未知工具定义。未来重新启用可发布
当前定义。普通register的唯一名合同不变。

比较：重建整个manager会改变无关身份并丢连接；只重读文件会产生注册表/资源脱节；
连接与允许名单共用身份会使每次加工具都重启进程。复用manager加两种现有职责所需
身份是本场景的最小路径。MCPPermission持有manager用于同步可用性核验，不持SDK
client；会话借用仍走SessionService，不建立另一套授权/上下文变量。

借用投影返回当前已发布目录/授权身份，实际client仍来自原ConnectionOwner。
每次tools/call前仍重新发现核对目录，且在最后一个异步预检后再次检查服务/名单/身份。
临时候选owner也纳入manager退出收束，刷新中应用关闭不能发布新配置或遗留进程。

## 关闭、旧批与失败链

服务关闭无需发现成功：先撤销可用性、使授权身份失效，再把旧owner标invalid；无
借用则关闭，有调用/连接等待者则由原borrow finally收束。已发送远端RPC正常收尾；
尚未提交者得到配对not_executed。重新启用时若旧owner仍收尾，新借用等旧任务关闭后
再创建；不得借旧失效owner或让旧清理移除新owner。关闭失败保留诊断和必要清理失败，
未确认关闭的同主体/连接不得新建替代；其他主体/服务不受影响。

ToolSet新请求过滤不可用MCP项，但不改保存的会话选择；重新启用同名后选择可恢复。
当前上下文预估和手动压缩也消费活动工具，统一经registry.select_available取可用定义；
旧批次仍用select保存名以保留配对，不能在恢复时悄悄过滤。预估异步等待后复核定义
引用，避免刷新穿插期间把旧定义的估算标为下一请求事实。
旧批次/旧待审在ToolSet派发处先比较保存MCP subject与当前定义：停用/名单移除/身份
变化返回安全配对未执行消息，不把旧许可套新定义，也不触发整个Agent失败。用户
拒绝仍走原HITL反馈；其他工具按原批次执行，不因一个MCP停用而丢配对。
直接executor许可名称/参数/subject篡改仍抛PermissionMechanismError；可用性变化
不是许可机制错误。旧session_allow因授权身份变化自动不匹配新调用，显式Deny保持。
服务开关是可用性撤销，与会话工具取消勾选（不撤销旧批）区别保留。

正常链：空启动 → 临时发现新server → 校验/发布 → Web/CLI刷新目录 → 用户选用 →
原schema/权限/审批/精确许可 → SessionService借用 → 单次RPC → 原消息/保存。
失败链：新server发现失败 → 临时owner关闭 → 返回失败状态且旧配置保持；或停用
发生于审批暂停/模型等待 → 旧调用派发返回not_executed → 模型继续 → 必要保存。

## 验证

先失败测试：零配置应用新增服务并调用；名单追加复用PID；服务停用不执行旧待审且
不使Agent失败；启用重新审批；失败保留旧服务；Web认证刷新/目录状态；前端刷新。
之后覆盖内存/SQLite、stdio/HTTP、根/子、同批配对、目录变化、无关身份保持、禁用
在途收尾/重启用竞争、配置/环境/权限失败、并发刷新、关闭/取消/候选清理、CLI命令。
用事件/可控服务同步，不用紧sleep；测试仅编辑临时配置文件，不改真实配置。

定向 → 全后端/前端/TS/Vite → compile/pip/AST/500行/diff → 原wheel构建/标准新venv
安装及必要运行中刷新探针。真实Web离线夹具检查新增/选用/关闭/状态与窄屏。SDK合成
服务/离线模型证据不代替真实业务、供应商或其他平台。完成更新handoff/使用说明/
当前总Spec和future-work，保留所有并行学习文件，说明07下一片方案。

## 实际实现与验收（2026-10-20）

本片已接正式链：`load_mcp_settings → AsyncApplication.arefresh_mcp → refresh_mcp`
借用原manager串行准备/关闭候选、按服务发布；`ToolRegistry.publish_mcp_tools`只允许
同MCP来源更新，普通register唯一名合同保持。两个工厂传入原输出预算。Web认证
POST与原工具菜单、CLI当前进程桥和`:mcp-refresh`/`:tools`均有实际消费者。
`MCPPermission.unavailable_reason → ToolSet`及executor最后预检阻止撤销后的提交；
`select_available`接新请求、上下文预估、手动压缩；旧批次仍保留完整select配对。
没有第二个注册层、旧刷新实现或新增数据库字段，不删除其他模块的真实入口。

先观察零配置新增缺arefresh_mcp、Web405、前端缺refreshMcp及CLI模块缺失红灯，再
实现。边界测试另复现排队借用在停用后新建进程/缺授权身份、停用工具仍计入上下文
预估；分别在异步恢复点核验连接配置、在当前工具消费者过滤。原失效连接用例曾因
把所有invalid都当退休等待而挂起，已中断该次运行并收窄为配置退休owner等待，原
失效调用即时失败合同保持；该中断不计通过。预估零工具组遵循既有“省略零组”合同，
另用普通工具保留计数断言。未放宽生产验证、skip或修改测试基础设施。

新增31项后端、5项前端。覆盖内存/SQLite授权更新、stdio与HTTP JSON/SSE、根与子
审批暂停、在途/排队/预检停用、重新启用、重复刷新、冲突整组拒绝、非法配置与
正TTL拒绝、候选取消/应用关闭/清理失败、终端真实桥、认证/Origin/未知路径字段。

实际验证命令与结果：

```text
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
3284 passed in 207.10s (0:03:27)

cd frontend && npm test -- --run
46 files / 363 passed（本片最后一次前端回归4.48s）

cd frontend && npm run build
TypeScript / Vite成功；保留既有大chunk提示

.venv/bin/python -m compileall -q naiyue_test_agent
.venv/bin/python -m pip check
No broken requirements found.

git diff --check
exit 0

.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp09-20261020/wheel-final
Successfully built naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl
287 files；157份生产Python与wheel逐字节一致，AST/500行通过

.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp09-20261020/wheel-final --output build/mcp09-20261020/install-final.json
exit 0；原生工具、Web/API、工具/审批恢复、SQLite重开和CLI均passed

.venv/bin/python build/mcp09-20261020/verify_install.py build/mcp09-20261020/wheel-final/*.whl tests/mcp_discovery_fixture.py build/mcp09-20261020/runtime-install.json
exit 0；源码外全新venv实际运行时刷新、三次调用1/2/1、重新审批、图不重建、进程全退出
```

安装探针首版误用了不存在的session_allow决策，正确被应用拒绝；修正探针为原有
approve＋remember_approved合同后重新从同wheel干净安装，通过全部断言，未改产品。
同wheel SHA256：`070e6a160828b25c0acfc1f60e6fcc19f576cd5dbcfd0ac737be6c5c33e4d591`。
证据：[后端日志](../../build/mcp09-20261020/pytest-final.log)、
[构建](../../build/mcp09-20261020/build-final.log)、
[标准安装](../../build/mcp09-20261020/install-final.json)、
[刷新安装探针](../../build/mcp09-20261020/runtime-install.json)、
[源码/页面审计](../../build/mcp09-20261020/verification-final.json)。

真实Uvicorn/Web/SQLite配离线模型及官方SDK合成stdio夹具：零配置启动，新增echo
后默认未勾选，选用/记住/调用；追加other后选用/审批/调用；停用后两项保留勾选但
禁用，启用后恢复且echo重新审批。tools/call恰好3次，计数1/2/1，前两次同PID，
第三次新PID。1440桌面与375窄屏完整入口可见、375无横向溢出、控制台warn/error空。
截图：[桌面](../../build/mcp09-20261020/desktop.png)、
[窄屏](../../build/mcp09-20261020/mobile.png)。临时页关闭、viewport复位，服务手动
SIGINT结束（130），审计全部夹具进程已退出；只保留忽略目录的验收文件。

边界：上述都是本机合成服务/离线模型，供应商请求0；不代表真实业务server、其他
平台、完整协议或用户掌握。相同配置刷新不重新发现远端独立变更的目录；需停用再
启用。运行中不更新子角色名单/模型/权限/网络，TTL仍0，正值拒绝。没有提交/push/
标签/发布或真实配置/用户数据库改写，其他MCP切片与并行课堂工作保留。

唯一下一片建议：回到07空闲回收，先讨论并确认实际改动范围，再实现。沿已选“新
调用先取得使用权”处理回收竞争，与本片停用/退休/重新启用协调；默认0保留连接，
仅显式正值启用、占用/在途/审批保护、可控时钟验收，之后08交付验收。

## 09交互收尾：统一刷新入口（用户批准，2026-10-20）

用户要求保留原“刷新工具与模式”按钮，移除独立“MCP配置刷新”按钮，恢复原入口布局。
工具菜单点击原按钮时顺序执行MCP配置刷新、已有reload读取有效工具/模式；权限模式
菜单仍只读目录。初始加载/打开菜单仍只GET，不自动应用配置。MCP失败仍触发reload，
保留安全失败提示和原服务；服务逐项结果仍可见，没有成功通知或自动勾选。请求中
禁用按钮/阻止重复提交，后续列表加载沿原loading/error/retry机制。CLI和两个后端
接口保持，不合并业务职责、不改变配置、权限、审批或资源合同。

修改现有ComposerControls及其现有McpRefresh测试，另在session-controls真实App测试
中验证POST前GET不开始、完成后新工具显示且不自动选用、POST失败仍GET、模式刷新
不执行MCP。先观察失败再实现，跑全前端/TS/Vite/diff；使用说明与交接同步。旧wheel/
页面截图仅为之前快照，本次前端收尾不代表重建或发布。

统一入口实际结果：先见6项预期红灯（原按钮未触发MCP、旧独立按钮仍在、失败未
重读目录），最小修改ComposerControls一份生产文件。MCP请求结束后无论成功失败
都走原reload，工具菜单原按钮绑定该流程，模式菜单保持GET；原loading/error重试
继续负责列表获取。没有新增后端接口、依赖或配置变更。

```text
npm --prefix frontend test -- --run src/components/McpRefresh.test.tsx src/session-controls.test.tsx
2 files / 17 passed（2.00s）
npm --prefix frontend test -- --run
46 files / 366 passed（4.33s）
npm --prefix frontend run build
TypeScript检查及Vite成功（147ms，保留既有大chunk提示）
git diff --check
exit 0
```

当前定向测试补3项，原5项MCP测试迁移原按钮并保留配对行为断言；覆盖逐服务失败、
请求失败仍刷新列表、并发重复保护、两接口顺序、无自动勾选/会话/模型调用，模式
刷新不应用MCP。真实临时Web零配置启动，写入私有夹具配置后只点击原按钮，出现
未勾选echo及local启用状态，无独立按钮或成功提示；SDK发现进程退出，业务调用0、
供应商0。1440桌面截图与375无横向溢出、控制台warn/error空，临时页关闭、viewport
复位。当前代码只改前端，未重跑后端，也未重建旧wheel或发布；之前后端/安装结果
保持其快照范围。日志：build/mcp09-20261020/unified-{red,target,tests,build}.log；
新截图：build/mcp09-20261020/unified-preview/{desktop,mobile}.png。
