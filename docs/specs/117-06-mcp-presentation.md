# MCP-06：CLI / Web 来源、审批与结果展示

状态：2026-10-20，用户授权实施，已完成本机实现/验收，依赖00—05。只推进本片，不提交或发布。

## 当前链与范围

注册后的 MCPPermission 已有 server_id / remote_tool / transport；Web 工具选择只输出
name/description。审批 public_approvals / ApprovalItemData / CLI / Web 只说明记住工具，
尚未输出可用的结构化来源。format_result 的 artifact 保存来源、截断和归档事实，
message_view / message_observation 都未投影；普通 ToolException 经共享错误层后丢失
“未提交”与“提交后未知”的区别。聊天保存完整 ToolMessage，但大消息 SQL 预览不读 artifact。

本片复用原页面样式和执行/审批链，增加可检查的安全展示事实。无服务管理页、安装器、
文件下载接口、DB迁移、连接回收、重试或新依赖。不编辑真实配置、环境和用户数据库。

## 数据与接线

新增 mcp_client/presentation.py，只承担 MCP 的可信来源与有界展示投影。
工具目录从真实 MCPPermission 投影 mcp_source={server_id,remote_tool,transport}，
同时展示明确配置该工具的 subagent_roles；主工具选择不改变角色工具名单。
审批从已准备 subject.mcp 取两个标识，不暴露 config_identity / catalog_fingerprint。
同步公共审批、审批事件、CLI 与 Web 类型。说明本会话允许对完整 MCP 工具有效，参数
可变，根/子共享该会话规则，定义/配置改变失效；审批仍绑定当前调用。

成功 / isError 结果保持现有 artifact.source 形状，增加 type=mcp_result、version=1、
outcome 与 transport。已知预检/网络失败先穿过原 borrow 退出以失效连接，再返回带相同
call_id 的 error ToolMessage；内容和日志仍是本地安全文字。outcome 为 success、
tool_error、not_executed、outcome_unknown、unsupported_result。提交后超时/断连只能
表示结果未知，不断言远端未执行。不可展示的响应表示已收到但不支持，不伪装为未执行。
权限机制错误、停止与取消保持既有传播。共享授权产生的拒绝/参数错误经最外层 ToolSet
按当前已选 MCPPermission 补来源及 not_executed；不新增授权决策，不根据名称前缀推断。
HITL 用户拒绝直接在 after_model 生成 ToolMessage，不经过 ToolSet；在该既有生成点
从匹配 id/name 的 prepared subject 补相同事实。tool_call_finished 保留既有执行反馈
分类，MCP 更细结果来自最终消息；UI 在结果到达后优先使用结果事实。

同一 mcp_result 投影服务 Web message_view、实时 message snapshot、CLI；只保留
版本、来源、结果枚举、截断/字节数、归档完成/原因及受限相对路径。不重复输出
structured_content，不输出地址、命令、env、headers、凭证、内部身份。旧04/05原始
artifact 可以读取其明确来源和 is_error 事实；无法识别的旧错误保持普通工具结果。
归档路径只接受既有 naiyue-agent-output/<32hex>-raw.log / -text.log，不作为 URL。

聊天仍保存完整原消息。chat_queries 从超长消息 JSON 中提取去除 structured_content
的有界 artifact，再交同一白名单投影，确保历史分页与实时一致；不增加存储字段或
第二份持久事实。根与子复用既有消息/组件链。结果区呈现来源、状态、正文、截断与
实际归档路径；未截断不显示“归档失败”，无事实时留空。未知结果明确提示核对副作用。

## 正常与失败例子

正常：注册 MCPPermission → session-controls 来源/角色 → 用户选择主工具 → 既有
许可 → MCP call → format_result(success/isError, bounded artifact) → 共享观察/保存
→ snapshot / history 同投影 → CLI / ToolResult。长结果沿既有 ShellOutputArchive
保存，UI 只显示实际完成状态与相对路径，无新增读取接口。

失败：审批拒绝/无效参数 → 原授权层配对错误 → ToolSet 补 not_executed → 原保存；
已提交请求断连 → 原 borrow 清理 → 本地安全 error ToolMessage(outcome_unknown)
→ 模型继续与原必要保存 → 终端/页面显示未知且未重试；原始异常文本不进入展示。
停止/取消与必要保存失败仍按原合同处理，不以展示为由吞掉控制/必要操作失败。

## 影响与验收

生产：mcp_client tools/results/presentation；ToolSet；message_observation；
tool_execution/approval（HITL拒绝消息的实际生成点）；
application session_approval/cli_approval/cli_display/chat_queries；审批事件合同；
web service/serialization/chat_history；前端 types、ComposerControls、ApprovalPanel、
ToolResult、ToolCall（根/子共用）。仅这些当前消费者需要新字段，不改模型 schema。

先观察失败：真实 SDK 图成功/服务错误/未执行/提交后未知的来源和状态；投影白名单与
路径拒绝；工具目录/审批来源；实时/SQL大消息/重开一致；前端结果、审批与选择器。
再最小实现并跑定向回归，全后端、前端测试、TS/Vite、compile/pip/500行/diff，沿用
项目 wheel 构建及全新 venv 安装验证。模型和服务均为合成夹具；不冒充真实业务验证。
完成后记录实际结果，更新 handoff 使用说明，并说明下一片待用户确认。

内部接口迁移：适配器已知普通失败由直接 coroutine 的 ToolException 改为配对 error
ToolMessage，正式图仍是同一错误反馈行为；inactive_child 旧单测同步检查 status、
call_id/name、not_executed 和原零新连接/零调用断言。其余许可篡改/环境异常断言不改，
不为旧内部测试保留无消费者的并行异常路径。框架共有错误层仍处理其余普通异常。

## 实际结果

新增22项后端、9项前端。首轮真实图先观察缺少 mcp_result 的预期红灯；前端9项均
先观察缺少来源/状态/归档红灯。补充边界先复现畸形 outcome/archive_reason 导致投影
TypeError，以及 CLI 同正文修订状态漏显；改为有界枚举比较并纳入元数据指纹后转绿。
其他补充分支首次即通过，不声称所有用例逐项先红。没有修改测试基础设施。

HITL拒绝的独立消息生产点已补来源，原许可/控制/异常类型合同保持。旧内部 coroutine
单测按上节迁移，不删除测试/放宽断言。JSON定义、内置工具、子实例、保存/清理、停止
与冷恢复均随全量回归；没有额外重试或把工具错误改成整个 Agent 失败。

| 验证命令/检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_mcp_presentation.py tests/test_mcp_web_presentation.py -q --tb=short` | 22 passed in8.77s |
| `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | 3253 passed in200.14s |
| `npm --prefix frontend test -- --run` | 45文件358 passed in4.00s |
| `npm --prefix frontend run build` | TypeScript/Vite成功，保留既有chunk>500kB提示 |
| `.venv/bin/python -m compileall -q naiyue_test_agent` / `.venv/bin/python -m pip check` | exit0；No broken requirements found |
| AST / 500行 / `git diff --check` | 155份生产Python通过；diff exit0 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/mcp06-20261020/wheel-final` | exit0；285文件，155份生产Python与源码字节一致 |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/mcp06-20261020/wheel-final --output build/mcp06-20261020/install-final.json` | exit0；源码外全新venv标准安装、原生工具、前端API、Find/Grep、审批恢复、SQLite重开、CLI全passed；供应商0 |

报告与日志位于build/mcp06-20261020：presentation-tests.log、pytest-final.log、
build-final.log、archive-final.json、install-final.json和install-final.log。
wheel SHA256：7de5cf2daa242b71b0ee405bf69c06b175406ac900f2b6249a06adb36311d811。
本片未单独把MCP图探针运行于安装包；MCP协议/展示集成来自源码测试和以下浏览器
实际链，安装包则有全源码字节一致与标准安装证据，二者不混称。

浏览器：Playwright扩展未连接后改用IAB，使用正式Web应用＋离线模型＋真实SDK stdio
合成服务，不改真实配置/用户库。实际点选工具查看local/echo/stdio与tester角色，
审批后执行15059字节长结果、完整归档，刷新重开历史仍显示相同来源/状态；另验证
isError与断连未知反馈，模型都继续回答，业务tools/call总计3、未重放。1440×1000
桌面与375×900窄屏检查，375屏document.scrollWidth=375，长路径换行；前端warn/error
日志为空。截图desktop-result.png、mobile-result.png、mobile-approval.png、
mobile-unknown.png；临时视口恢复，测试标签与本地夹具结束后关闭。

无依赖升级、DB迁移、配置改写、新文件服务、重复授权解析或废弃并行入口。原00—05
及用户并行学习记录保留。这里不证明真实模型/业务服务、其他操作系统、全部协议
或用户已掌握。06基础闭环已齐，建议唯一下一片08基础版交付验收；07显式TTL可在
基础版后独立实施，默认0且正值仍拒绝。下一片先说明范围等待用户确认，不自动发布。
