# MCP-04：根 Agent 的注册、授权、单次调用与结果

状态：2026-10-20，用户已批准并完成本机实现/验收；依赖01—03，不升级依赖、不发布。

## 当前链与本片范围

工厂准备内置ToolRegistry → 模型目录 → MCPSessionResources → SessionService →
create_agent。01已有JSON Schema校验/模型描述，03只有资源借用；模型尚不能调用MCP。
本片在两个工厂建图前发现所有启用server、只注册allowed_tools选项。发现失败则装配失败，
临时发现连接关闭，不占用任何用户会话。默认活动集合保持原内置工具；可经已有工具选择
入口选择MCP，也允许工厂显式initial_active_tools。空allowed_tools只发现、不注册。

资源层增加冻结配置的目录发现入口；返回配置副本、不透明identity、catalog，无真实凭证。
工具闭包只持有manager与冻结定义，不绑定SDK client。执行时直接复用manager.borrow，
从已获许可的ToolRuntime取得session_id/root和_run_control。现有SessionService.borrow_mcp
仍用于需检查活跃子实例的资源入口；本片工具执行显式拒绝非root，05再接子实例。
这样不增加为解决装配环而存在的可变路由器/回调或第二套权限/Agent运行时。

## 正常与失败链

正常：用户启用并选择server工具 → 启动发现冻结 → ToolSpec注册（JSON Schema、unknown、
sequential、默认ask）→ 现有活动工具集合 → 模型给出名称/参数 → 本地参数校验 →
Deny/Allow/会话记忆/默认权限与现有四模式 → 审批暂停 → 精确一次许可 → 执行端复核
名称/原名/配置/目录/参数 → manager按(session_id,root,identity)借用 → tools/list复核
全目录指纹 → SDK session.call_tool单次提交 → 文本/结构JSON转换 → ToolMessage回填
模型 → 最终回复。运行结束归还占用，连接继续由03会话资源拥有。

失败：非法参数、Deny、拒绝、NoAsk不发送tools/call；执行前目录不一致则拒绝旧工具。
RPC异常/超时/停止穿过borrow使连接失效，再转换安全工具反馈；不得自动重放已提交调用。
控制取消保持03语义，不能声称远端副作用被撤销。isError返回配对error ToolMessage，
不丢弃有界错误正文/归档；input_required、非text块、非complete结果明确不支持，不访问
资源URI、不自动进行sampling/elicitation/认证。SDK输出schema验证沿用其实际单次调用入口。

## 权限及结果合同

MCPPermission继承已有ToolPermission；subject新增mcp对象：server_id、remote_tool、
config_identity、catalog_fingerprint。target明确标识MCP来源、传输及参数，host仍表示
发起客户端的位置，不解释为远端工具工作目录。凭证/URL/进程参数不进入subject或日志。

AllowOnce仍绑定batch/call/session/agent/subject/精确args，只消费一次。用户选择
“本会话始终允许此工具”时，记住完整tool_name与上述mcp身份、会话/工作区/执行环境；
不记住args，其他合法参数也匹配。显式Deny始终优先，其他server/会话/配置/目录不匹配。
内置工具现有path/command/args规则保持。MCP含command字段也不按Shell命令记忆。
初始冻结目录改变后本次拒绝，需重启装配获取新定义；本片不做目录热更新。

模型可见正文限max_result_bytes UTF-8字节。artifact只保存有界投影、来源及截断/归档事实，
超长原始JSON使用已有ShellOutputArchive的安全目录/配额/清理合同，单次归档上限复用
server.max_response_bytes，共享tool_output.max_workspace_archive_bytes；不新增设置字段。
无工作区或归档失败明确标记，不能宣称完整。metadata/媒体不进入模型或历史，归档也不保存
未支持媒体payload。此为解析后的结果预算；stdio解析前硬内存上限仍属08边界。

审批公共行增加可选remember_scope='tool'，只从正式MCP subject投影；事件/历史/前端类型
同步。MCP按钮文案使用“本会话始终允许此工具”，CLI显示同一范围，内置按钮保持。
06的工具来源/富展示、05恢复/子实例、07 TTL、08跨平台与真实远程业务均不扩展到本片。

## 文件及验收

新增mcp_client/tools.py、results.py；扩展resources的冻结发现入口和两应用工厂装配。
扩展tools/permissions的MCP会话匹配分支；session_approval、session_events及前端审批
投影可选字段同步；不改DB表、公开调用签名、依赖版本或真实配置。

先观察失败测试：工具注册/根Agent审批→执行→模型闭环；工具级记忆跨参数且隔离身份；
invalid/deny/reject/NoAsk/Auto/Bypass；目录漂移零提交、响应丢失一次提交、超时/停止；
文本/JSON/isError/unsupported/截断与归档。真实SDK stdio及HTTP夹具与离线确定性模型，
不以此代表真实供应商或生产业务。03旧装配失败测试随“启用server启动发现”迁移断言为
发现进程已关闭、零业务调用，保留原异常及manager关闭断言。

定向测试后全量后端、前端测试/构建、compile/pip/diff及500行检查；按项目发行构建工具
生成本机候选wheel、源码字节核验与全新环境安装。只生成本地产物，不commit/push/发布。

## 实际实现与验收

`build_async_local_application/open_async_persistent_application`保存原内置initial集合，
创建manager后调用`register_mcp_tools`。`MCPSessionResources.discover`使用临时scope
借用03 owner，收集冻结server副本/不透明identity/catalog，finally释放整个临时scope。
所有启用服务均发现，选中项转ToolSpec；随后完整验证权限规则与活动选择，再建服务/图。
无MCP时保留原规则/选择的提前失败，未导入SDK且无外部IO。

`create_mcp_tool`闭包持manager/定义；`MCPPermission.prepare`生成来源与身份subject。
已有`prepare_permission_call → BatchApprovalMiddleware → prepare_resume/OneUseApprovalGrants
→ ExecutionAuthorizationMiddleware`保留精确调用合同。`remembered_rule/matches_session_allow`
仅为MCP增加工具级分支，session/config/catalog仍隔离；显式Deny在它前面。
执行端`require_tool_permit → manager.borrow → read_catalog → session.call_tool`，同一
次调用仅一次提交；异常穿过borrow使owner失效，再转安全反馈。没有SDK高层自动多轮。

`format_result`在已启动IO受控排空的run_blocking中投影JSON/ToolMessage，isError保留
error状态；媒体/input_required明确拒绝。超长支持数据的JSON保存到现有owned archive，
模型正文含路径/完整性事实；artifact只保存有界结构数据/来源/截断和归档事实。
未支持媒体与第三方meta不存入归档。无工作区、额度满、符号链接目录均明确降级。

审批公共行及事件的可选remember_scope同步至Web类型/按钮、CLI；未新增配置管理页。
03夹具增加明确tools模式及漂移标记用于结果边界；旧发现模式仍在tools/call时断言失败。
03装配失败断言迁移为“启动发现存在、业务调用为零、进程全部退出”，保留原异常身份
与manager关闭；停止检查同时核对发现/执行全部PID，未放宽断言。

先运行新测试观察2项预期红灯：旧记忆规则错误保留command、工厂不认识MCP活动名。
最小实现后第一批11项转绿；后补分支有部分首次即通过，不把它们宣称逐项TDD。
装配第一次因误用settings.tools.output失败，改为现有settings.tool_output后通过；
首次候选打包误将frontend/dist当作--frontend-assets的完整资源根，按现有默认构建入口
重跑成功，未修改构建器/放宽资源白名单。

| 命令/检查 | 实际结果 |
| --- | --- |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests/test_mcp_agent_tools.py tests/test_mcp_agent_boundaries.py tests/test_mcp_application_resources.py tests/test_json_schema_permissions.py tests/test_native_session_lifecycle.py -q` | 70 passed in24.16s；本片新增后端37项 |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q` | 最终3197 passed in164.13s；首轮3188亦通过，随后加入补充分支重新全量验收 |
| `npm --prefix frontend test -- --run` / `npm --prefix frontend run build` | 44文件348 passed in4.10s，TS/Vite成功；既有chunk>500kB提示保持 |
| compileall / pip check / AST及500行 / diff --check | 154份生产Python通过；No broken requirements found；exit0 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp04-20261020/wheel-final` | exit0，284文件，154份Python与当前源码逐字节一致 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp04-20261020/wheel-final --output build/mcp04-20261020/install-final.json` | exit0，全新环境原生工具/前端API/Find-Grep/审批恢复/SQLite重开/CLI全部passed |
| 安装包根Agent专项探针 | 另一全新venv、源码外运行：先审批零tools/call，A记忆后两次计数1/2，B重新审批计数1；全部进程退出，0次供应商 |

专项命令：`.venv/bin/python build/mcp04-20261020/verify_agent_install.py build/mcp04-20261020/wheel-final/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl tests/mcp_discovery_fixture.py build/mcp04-20261020/agent-install.json`，exit0。
三个报告archive-final/install-final/agent-install的wheel SHA一致：
`acbb4f11bc4791c4234032737eece1f87998da1c307f80ab9a6e2af1bd437835`。

专项覆盖显式Deny覆盖Allow/Bypass/会话记忆，Auto allow/双ask/无效结果转人工，
许可改参数/名称/identity/环境拒绝，根Agent stdio与HTTP JSON/SSE，目录首次连接/待审/
同连接二次调用漂移零新增提交，超时/停止/响应丢失一次提交，无回放；文本/JSON/
isError/媒体/input_required/截断/归档权限与降级。模型为本地确定性实现，真实SDK
夹具不代表任意远程业务、真实认证、模型效果、Windows/Linux原机或五平台交付。

当前只允许root；05需处理子角色显式名单、实例借用/审批/停止/最终释放，以及临时
SQLite重开后的旧待审、配置/目录不一致与必要保存失败。重启创建新资源identity，
本片不能承诺旧审批可直接执行或外部进程状态能恢复。先说明05方案待用户确认。
未修改真实配置/用户数据库、依赖版本或学习文档；没有commit/push/tag/PyPI发布。
