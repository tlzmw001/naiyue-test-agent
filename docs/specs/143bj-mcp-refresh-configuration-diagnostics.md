# Spec143BJ：本机 MCP 刷新身份误判与差异诊断

## 问题与边界

Windows 验收返回 `config_mcp / connection_configuration_changed`，但响应不列差异、日志只有 MCPResourceError，无法判断是本机配置实际变化还是指纹误判。当前本机刷新先比连接身份，再发现目录；失败保留旧定义。单纯远端 Schema 变化已有同会话刷新回归。

用户确认本机配置未改后，最小探针已复现根因：MCPServerSettings 的四个 float 超时字段使用 int 默认值；启动时 MCPSessionResources 对 model_dump 二次校验转换成 float，刷新时从省略默认字段的原文件直接构造，仍为 int。模型值相等，但 JSON 指纹中的15.0/15、0.0/0不同，导致误判。根因修复把四个默认值统一为声明的浮点类型，并保留差异诊断。

本片不放开连接热切换，不改变身份算法、重试、权限、连接收尾或远端调用。清理任务面板的已确认设计另行实现。Windows 这次现场是否正好命中该原因，仍以新包原机复验为准。

## 数据流与接口

正常：McpAPI.refresh → refresh_mcp → 身份一致 → 原目录发现及发布，不增加响应字段。

失败：本机候选身份与 old[3] 不同 → 比较旧/新服务已验证模型字段、捕获的引用环境、网络及拨号目标 → 原错误拒绝 → 日志记录安全错误码和字段名 → 原服务状态响应增加可选 `changed_fields: string[]`。旧定义和连接不变，不发送候选工具调用。前端类型接受该可选字段，沿原错误大类显示，详情可从 Network 响应和日志获取。

字段来源为 MCPServerSettings 的固定字段名，忽略原身份算法已排除的 enabled/allowed_tools；headers只比较是否变化，env只记录字段名，引用环境实际值只用 credential_environment 标签。网络和拨号目标分别标 network/dial_target；有身份差异却找不到具体字段时记录 connection_identity，明确不能解释，不假装找到原因。日志与响应均不含新旧值、token、URL、路径、环境变量名或配置指纹。

## 修改文件与验证

- mcp_client/refresh.py：在现有拒绝分支计算差异；沿现有日志和状态返回，无新辅助层。
- mcp_client/settings.py：四个默认超时字面量使用float，保证原文件省略默认值和二次解析相同；不升级依赖或改预算数值。
- tests/test_mcp_runtime_configuration_interactions.py：回归启动参数差异；补参数预算、明文headers、引用环境值变化、恢复原配置后差异消失与无凭证泄漏断言。
- frontend/src/types.ts：McpServerStatus 增加可选 changed_fields，既有响应兼容；不新增界面或自动操作。
- 本Spec和docs/handoff.md：状态、证据与待Windows回传。

先红后绿，stdio与HTTP均验证配置省略默认值、内容不变、仅服务端Schema变化时原会话刷新和新参数调用成功；继续检查真实配置变化被拒绝、差异日志、无凭证泄漏。运行后端、完整前端和TS/Vite。实现与本机确定性证据不替代Windows现场验证。

## 验证与交付状态（2026-11-16）

- 数值探针先复现旧对象与刷新对象相等但指纹不同，启动超时字段为float、刷新默认值为int。修复后相同探针指纹相同。
- 身份回归先红：stdio、streamable_http配置文件均未变化，仅更新服务端Schema，刷新返回error。修正4个默认字面量后通过，原会话以新参数调用成功，原配置文件字节保持不变。
- 差异诊断先红：启动参数、超时预算、headers、引用环境变化均无changed_fields；增加后4项通过，并断言旧定义保留、无候选tools/call、日志/响应没有token、地址或环境变量名。恢复原配置后诊断字段清除。
- MCP专项：`.venv/bin/python -m pytest tests/test_mcp_runtime_configuration_interactions.py tests/test_mcp_runtime_configuration.py tests/test_mcp_idle_application.py tests/test_mcp_discovery.py tests/test_mcp_settings.py tests/cloud/test_runner_mcp.py -q`，91 passed。
- 后端：按项目native_assets脚本准备macos-arm64测试工具后运行ci_shards两分片，3326+3338=6664 passed，零失败/错误/跳过。较早的诊断版全量因环境缺fd出现读取工具失败并在定位根因后中止，不能作为最终结果；最终两分片使用修复源码与明确PATH重新完成。
- 前端：78文件678 passed，TS/Vite生产构建成功，既有chunk大小提示保留。界面未新增交互，仅同步可选响应类型，无需新的截图行为验收。没有改锁、DB或配置文件。
- 证据：build/mcp-default-identity-red.log、mcp-default-identity-green.log、mcp-default-identity-backend-{0,1}.{log,xml}、mcp-config-diagnostic-{frontend,build}.log，以及build/mcp-default-identity-verification.json。
- 仅工作区实现，未提交/push/打包/部署。Windows及公司MCP服务现场待验证，不声称已确认原机唯一原因。

## 旧版临时恢复

对HEAD未修复设置类的隔离探针已验证：省略四个默认字段时与运行时指纹不同；显式写出相同数值后指纹一致。若原配置未设置这些字段，可在原MCP服务配置节显式补齐以下默认值后点击刷新，无需先重启。已有自定义值应保留，不得覆盖为默认值：

```toml
connect_timeout_seconds = 15.0
call_timeout_seconds = 0.0
read_timeout_seconds = 0.0
idle_timeout_seconds = 0.0
```

这只验证了原错误的临时绕过条件；后续远端连接、认证与Schema仍按现有合同校验，不能保证其他错误也随之解决。
