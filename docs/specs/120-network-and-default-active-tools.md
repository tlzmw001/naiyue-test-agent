# Spec120：代理认证、直连名单与默认活动工具

日期：2026-10-20。基线：当前工作树。状态：**120-01/02 已按用户授权实现并完成本机离线、构建与干净安装验收。** Windows 原机及真实用户网络未验证；未改写真实配置，未提交或发布。

本 Spec 合并两项配置需求：在 `settings.toml` 中分别填写代理地址、用户名、密码及不走代理的地址；配置 CLI/Web 新会话默认启用的工具。下列新增字段现已接入当前源码，实际实现与证据见第11节；本轮未执行 PyPI 发布。

初始记录仅授权写 Spec；随后用户明确“直接做这个 spec”，本会话开始实施。并行 [Spec119](119-call-observability.md) 与学习工作的代码、记录及推进状态保持，不借本次授权修改其他功能。

## 1. 目标与范围

用户场景为 Windows、本地应用、Auto 审批，需使用代理和 MCP。两项最小目标：

1. 手动配置 HTTP(S) 代理、原始用户名和密码，并让指定主机直接连接，不必依赖终端环境变量表达整套 Web/MCP 网络策略。
2. 在用户级配置中指定新根会话的初始工具列表，包括 PowerShell 和已登记 MCP 工具；后续仍可通过原 CLI/Web 工具选择入口调整。

沿用 Python、Pydantic、现有 HTTPX/HTTPX2 客户端、标准库 URL 编码及主机匹配能力，不新增依赖、配置中心、代理服务、工具注册平台或另一套运行循环。

范围保持：

- 网络设置继续仅管理 `web_search`、`web_fetch` 和远程 HTTP MCP。
- 主模型、摘要、Auto 审查模型客户端，以及 Shell/AITest/本地 MCP 程序自身的联网配置不自动纳入；若要统一模型代理，另行确认范围，不能以本 Spec 已实现全应用代理。
- 不新增代理设置页面、PAC/SOCKS、系统代理修改、运行中网络重载或凭证保险箱。
- 默认活动工具不改变工具登记、权限模式、审批、Deny、子角色名单或历史恢复合同；不自动启用所有新发现工具。
- 不改公开 Python 构建 API 的签名和既有默认行为，不升级依赖，不修改真实 `settings.toml`、`models.json`、`.env` 或用户数据库。

## 2. 实施前基线与数据流

以下记录开始实施本 Spec 前的源码事实，保留用于对照，不代表实施后的当前能力；新增接线与实际验证见第11节。

| 位置 | 当前职责与缺口 |
| --- | --- |
| `tools/web_settings.py:NetworkSettings` | 只有 `mode` 和 `proxy`；严格拒绝新的用户名、密码和 `no_proxy` 字段 |
| `tools/web_network.py:selected_proxy` | `direct` 返回直连；`proxy` 直接返回配置 URL；只有 `auto` 读取系统/环境代理与其中的 `NO_PROXY` |
| `tools/web_network.py:open_client` | Web 工具使用上述选择结果构造现有 HTTPX 客户端，继续验证 TLS |
| `mcp_client/connections.py:_http_transport` | 远程 MCP 共用 `selected_proxy`，通过 HTTPX2 连接，不另建代理规则 |
| `settings.py:AppSettings/load_settings` | 没有默认活动工具字段；未知顶层配置会失败；非法网络设置启动失败，不回退直连 |
| `application/cli.py:main`、`web/app.py:create_web_app` | 从内置工具中排除 Bash/PowerShell，直接作为初始列表传给应用；无工作区的 Web 使用空列表 |
| `application/async_application.py`、`async_persistent.py` | 已有 `initial_active_tools`；MCP 启用时在发现、登记之后核验名称，再创建 SessionService |
| `application/session_controls.py:create_workspace_session` | 从应用冻结的初始列表创建根会话，并沿已有必要保存链保存选择 |
| `application/agent_session.py:set_active_tools` | 用户调整选择后先保存，再更新会话状态 |
| `application/session_persistence.py:_aprepare_saved_configuration/_install_session` | 恢复数据库保存的工具选择，不重新套用新会话默认值 |

当前网络链：

```text
settings.toml → load_settings → NetworkSettings
  → Web 工具 open_client / MCP _http_transport
  → selected_proxy(目标 URL, 设置) → HTTPX / HTTPX2 → 目标或代理
```

当前工具链：

```text
CLI/Web 内置初始列表 → initial_active_tools
  → 登记内置/MCP工具 → 注册表验证 → SessionService
  → 新会话 active_tool_names → 必要保存 → 每次模型请求的工具选择
```

网络选择只描述发送路径，不授予目标访问权限；工具选择只描述模型可见工具，不构成执行授权。

## 3. 配置接口

下列地址、账号、密码和 MCP 工具名均为格式示意，不代表存在的服务或真实凭证。

```toml
# 顶层字段，必须放在各表之前。
default_active_tools = [
  "read", "ls", "find", "grep", "write", "edit",
  "powershell", "web_search", "web_fetch",
]

[network]
mode = "proxy"
proxy = "http://proxy.example.com:8080"
proxy_username = "alice"
proxy_password = 'p@ss#word'
no_proxy = [
  "localhost", "127.0.0.1", "::1",
  ".corp.example", "192.168.1.20",
]
```

| 字段 | 类型、缺省与约束 | 功能 |
| --- | --- | --- |
| `default_active_tools` | 可省略的有序字符串列表；缺省内部为 `None`，空列表合法 | CLI/Web 新根会话的默认选择 |
| `network.mode` | 保留 `auto/direct/proxy`；默认 `auto` | 代理来源与路由模式 |
| `network.proxy` | 保留现有可选 URL；`proxy` 模式必须提供 | HTTP(S) 代理地址 |
| `network.proxy_username` | 可选字符串；与有效代理 URL、密码同时提供，用户名非空 | 手动代理认证用户名 |
| `network.proxy_password` | 可选字符串；显式空密码允许，省略不等于空密码 | 手动代理认证密码 |
| `network.no_proxy` | 字符串列表，默认 `[]` | 本配置明确要求直连的主机范围 |

用户名、密码按原文填写；不要求用户做百分号编码。普通 TOML 字符串不新增 `${ENV}` 展开；MCP 原有 `env/headers_env` 引用机制保持独立。

## 4. 120-01：代理认证与直连合同

### 4.1 选择顺序

| 模式 | 顺序 |
| --- | --- |
| `direct` | 全部直接连接，不读取代理或认证信息 |
| `proxy` | 先匹配配置 `no_proxy`；命中直连，其他目标使用配置代理及其认证 |
| `auto` | 先匹配配置 `no_proxy`；未命中再沿用现有系统/环境代理及 `NO_PROXY` 选择 |

`auto` 不拿配置中的账号密码覆盖系统/环境代理凭据；配置的 `proxy` 和分离认证只在 `proxy` 模式使用。`proxy` 模式的例外来自本配置 `no_proxy`，不额外合并环境 `NO_PROXY`，保证手动设置可预测。未提供新字段时保持原行为。

已决定使用代理后，连接失败维持原失败路径，不自动直连。直连目标失败也不自动改走代理。工具原有搜索供应商回退、停止、总期限、TLS 和结果错误合同保持。

### 4.2 直连名单语法

第一版支持主机名、域名后缀和 IPv4/IPv6 字面地址。域名遵循当前标准库 NO_PROXY 的点边界匹配：`corp.example` 与 `.corp.example` 都匹配该域自身及其子域，不匹配 `notcorp.example` 或 `corp.example.other.com`。

- IP 按地址匹配；IPv6 配置写 `::1`，不包含 URL 的方括号。
- 域名匹配不区分大小写；实现时明确处理 URL 主机规范化，不能用无点边界的普通字符串后缀判断。
- 新名单条目不带协议、路径、账号、端口、CIDR 或通配表达式；不以正则匹配 URL。非法或空白条目启动失败，不静默忽略。
- 同一主机的不同目标端口共用规则。匹配按请求 URL 的主机完成，不做额外 DNS 查询以推断 IP 网段。
- `auto` 模式原有环境 `NO_PROXY` 的语法继续由现有标准库处理，不能因新字段收窄旧环境行为。
- `web_fetch` 使用原始目标主机选择路线，每次重定向重新判断；保留原 DNS 审查、地址绑定、TLS 原域名验证与权限检查。

| 示例目标 | 对应示例配置的结果 |
| --- | --- |
| `https://www.python.org` | 配置代理 |
| `https://corp.example`、`https://api.corp.example` | 直连 |
| `https://corp.example.other.com` | 配置代理 |
| `http://192.168.1.20:8080`、`http://localhost:3000` | 直连 |

### 4.3 认证、校验与保密

保留旧 `proxy = "http://user:password@host:port"` 语法。若 URL 已含 userinfo，同时提供新的用户名或密码字段，启动时报配置冲突；不隐式覆盖，也不重复编码。

只提供用户名或只提供密码、字段类型错误、NUL/CR/LF、代理 URL 或名单非法，均沿网络设置失败路径终止启动。错误信息只包含字段/类别，不回显输入值。密码、用户名及派生认证 URL 不进入 repr、日志、诊断事件、工具反馈、配置转储或 HTTP 错误展示。

复用现有 `selected_proxy(value, settings)` 接点与 `str | None` 结果：直连返回 `None`，代理返回仅供客户端消费的内部 URL；分离凭据使用标准库安全编码后装配，原始设置不改写。保留签名，不通过全局环境变量传递或切换，不在代理选择器里发网络请求。该改动有 Web 和 MCP 两个真实消费者，不另建网络策略注册层。

网络设置启动时固定，修改后重启。MCP 的刷新只重读 MCP 段，不顺便重载网络或给现有连接更换代理。该阶段只接普通 HTTP 代理用户名密码认证，不承诺企业 NTLM/Kerberos 登录。

## 5. 120-02：默认活动工具合同

`default_active_tools` 只由 CLI/Web 启动入口消费，并传入已有 `initial_active_tools`，不新增工具启用状态库或前端页面。Python SDK 调用者继续用已有参数控制选择，不因传入 AppSettings 自动改变 SDK 的历史缺省。

| 情况 | 行为 |
| --- | --- |
| 字段省略 | CLI/Web 保留当前内置集合，排除 Bash/PowerShell；MCP 不自动加入 |
| 显式列表 | 按该列表初始化新根会话，保留顺序 |
| 显式 `[]` | 新根会话零活动工具，不能被当作未配置 |
| 类型错误、空名、重复名 | 设置校验失败，不静默去重、修正或恢复全部工具 |
| 未登记名称 | 在内置及启用 MCP 发现/登记完成后校验失败，不替换成旧默认 |
| 无工作区 Web | 继续使用空集合，不因新默认字段开放工具；不扩大原无工作区行为 |
| 恢复已有会话 | 使用数据库中保存的选择，默认值不覆盖它 |
| 用户在 CLI/Web 调整工具 | 沿原选择、必要保存和后续请求生效链处理 |
| 子 Agent | 使用角色显式 `tool_names`，不继承根默认列表 |

MCP 使用完整登记名称，例如 `mcp__company__search_docs`。对应服务必须启用，远端工具必须进入 `allowed_tools`，且发现成功。默认列表不启动未配置的服务、不扩大允许名单、不授予审批许可。

字段在应用启动时读取；同一进程新建会话使用冻结默认值。运行中 MCP 新增或扩名单不会自动勾选新工具。MCP 停用后的可用性继续沿原过滤/审批合同处理，不因默认列表重新启用服务，也不清除已有选择；重启时列表引用的名称若未登记，仍明确失败。

本字段不是全局工具禁用策略：从默认列表省略的已登记工具，用户仍可通过现有菜单选中。若要阻断执行，继续使用显式 Deny。默认启用 PowerShell 后，调用仍经过 Auto/人工审批和执行处校验。

## 6. 正常与失败调用链

### 6.1 网络

正常：用户文件生产代理配置 → `load_settings` 校验并冻结 `NetworkSettings` → Web 工具或 MCP 传入真实目标 URL → `selected_proxy` 匹配主机并产生 `None/内部认证 URL` → 原客户端连接。只有客户端拥有网络副作用；匹配与编码不执行 IO，不改变权限判断。

失败一：用户同时配置 URL 凭据及分离凭据 → 网络校验失败 → 启动入口返回失败；不连接代理、不直连目标，日志只记录安全类别。

失败二：目标未命中直连名单，代理认证或连接失败 → 原网络错误/工具错误边界处理 → 模型得到有界错误，或启动 MCP 发现失败；不重放有副作用调用、不偷偷直连、不展示代理密码。MCP 运行中配置刷新失败仍保留原有效配置的既有合同。

### 6.2 工具默认值

正常：用户文件生产有序名称列表 → 设置验证类型/重复 → CLI/Web 传入 `initial_active_tools` → 原构建器完成 MCP 登记后用注册表核验 → `SessionService` 冻结默认 → 创建新会话，保存 `active_tool_names` → 模型请求取得当前活动工具。选择保存属于必要操作，进度展示不参与决定。

失败一：名称拼错或引用未登记 MCP 工具 → 原注册表拒绝 → 应用装配失败并按现有资源拥有链关闭已取得资源；不创建可运行的新会话，不回退为全部启用。

失败二：新会话或手工工具选择保存失败 → 沿原必要保存异常与状态合同传播；不声称选择已生效，不更新成功视图。恢复旧会话时读取旧列表，不把新配置当作恢复数据。

## 7. 影响文件与职责

实现预计超过 8 个文件，主要是以下现有接点和对应测试/文档；优先扩展现文件，若触及 500 行边界再说明拆分必要性。

| 文件 | 改动职责 |
| --- | --- |
| `naiyue_test_agent/tools/web_settings.py` | 增加分离认证与名单字段、严格校验、敏感显示保护 |
| `naiyue_test_agent/tools/web_network.py` | 在共享选择入口接名单和认证，保留环境模式与原客户端合同 |
| `naiyue_test_agent/mcp_client/resources.py` | 网络敏感字段不参与公共 model_dump 后，可信内部复制与连接身份仍显式保留完整网络设置，避免凭据丢失或授权身份漏算 |
| `naiyue_test_agent/settings.py` | 增加可省略的 `default_active_tools`；纳入顶层白名单及独立校验错误，不误报为子角色配置错误 |
| `naiyue_test_agent/application/cli.py` | 读取默认工具字段并复用原构建参数 |
| `naiyue_test_agent/web/app.py` | 同上，保留无工作区空集合 |
| `tests/test_web_tool_settings.py`、`tests/test_web_network.py` | 字段、路由、认证、保密和环境兼容 |
| `tests/test_mcp_transport_policy.py` | 证明 MCP 实际消费同一代理选择与认证；不只测设置对象 |
| `tests/test_tool_settings_entries.py`、`tests/test_web_session_controls.py` | CLI/Web 初始选择、空集合、错误名及旧会话恢复 |
| `scripts/release_smoke.py` | 沿原源码外安装worker读取临时配置并断言默认工具/认证/直连，防止发行包漏接线；不改变测试依赖或真实用户配置 |
| `docs/examples/settings.example.toml` | 实现后再加入真实可用字段和说明，当前样例保持可被当前加载器读取 |
| `docs/mcp-client.md`、`docs/aitest-web-tools.md` | 实现后说明网络范围、生效时点与默认选择 |
| 本 Spec、`docs/handoff.md`、`docs/future-work.md` | 状态、证据与真实待办；不改主线排序 |

`mcp_client/connections.py`、应用构建器、注册表、会话存储和前端原则上复用，不为外观一致新增包装。MCP资源的内部网络复制与身份已发现是实际消费者，按上表一并接线；不输出内部身份材料。若实际接线需要改动其他文件，先更新本表及原因；不先改运行时再补说明。

## 8. 测试与验收计划

两片分别按正确红灯 → 最小实现 → 定向回归推进。这里只记录待执行检查，不把既有探针或文档解析当成功能完成。

| 类别 | 必须覆盖 |
| --- | --- |
| 字段与兼容 | 缺省、原带认证 URL、分离认证、显式空密码、缺半组、冲突、未知字段/类型；非法网络不回退直连 |
| 路由 | 三种模式，配置名单与环境名单的优先级；目标域自身/子域/伪后缀；IPv4/IPv6、大小写、不同端口；非法名单 |
| 凭据 | `@/#/:/%` 等特殊字符正确编码一次；用户配置保持原值；repr、验证异常、日志、工具错误及诊断不泄露 |
| 真实接线 | WebSearch/WebFetch/HTTP MCP 客户端分别观测选中路线；本机代理夹具验证认证与直连，代理失败不增加直连请求 |
| 既有网络保护 | WebFetch 重定向逐跳选择，DNS/绑定地址/TLS 原域名保持；停止、超时、压缩响应拒绝与 MCP 错误合同无回归 |
| 默认工具 | CLI 和 Web 的未配置、列表、显式空列表；Windows PowerShell；未知/重复名；无工作区 Web |
| MCP 默认 | 发现后按完整名验证；未启用、未允许或拼错失败；新发现工具不自动加入；停用仍过滤 |
| 会话与权限 | 新会话保存后重开；旧会话手工选择不被新默认覆盖；必要保存失败不伪报成功；选中 Shell/MCP 仍受 Auto/Deny |
| 隔离 | 两个应用持有各自设置，不修改全局环境；SDK 原显式参数、子角色名单和 MCP 刷新范围保持 |

拟执行命令（实施后记录实际结果）：

```bash
python -m pytest -q tests/test_web_tool_settings.py tests/test_web_network.py tests/test_mcp_transport_policy.py tests/test_web_search_tool.py tests/test_web_fetch_tool.py
python -m pytest -q tests/test_tool_settings_entries.py tests/test_web_session_controls.py tests/test_mcp_agent_tools.py tests/test_mcp_runtime_configuration.py
python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

按现有[发行指南](../releasing.md)构建匹配平台的新候选目录，并执行源码外干净安装验证；不得覆盖旧产物。Windows 路径/解释器和本机代理行为需 Windows 原机或 CI 证据，macOS 静态/夹具验证不代替 Windows。真实公司代理、实际 MCP 和供应商网络效果单独记录；不请求用户提供密码作为写 Spec 或确定性测试前提。

## 9. 分片、回退与当前记录结果

- **120-01：网络字段与共享选择接线。** 字段、原认证 URL 兼容、直连名单、Web/MCP 消费及错误保密一起验收，不只创建未消费字段。
- **120-02：CLI/Web 默认活动工具。** 字段、初始参数、MCP 名称验证、会话持久化/恢复与权限反例一起验收，不替换原选择系统。

两片现已按后续授权完成本机验收。不修改运行依赖声明和数据库 schema；回退到旧软件前须从私有配置移除新增字段，旧加载器会拒绝它们，不能承诺旧版可直接读新配置。已保存会话不自动清空或迁移，所有回退/真实配置操作仍需另行明确范围。

初次记录阶段仅新增 Spec 和文档入口，未实现产品；随后的实现阶段已执行下节列出的测试、构建与安装。两阶段证据不可混用。

## 10. 参考与现有合同

- [当前可用设置样例](../examples/settings.example.toml)：当前行为的入口，不提前填入本 Spec 的新字段。
- [MCP 客户端说明](../mcp-client.md)、[Spec117-09](117-09-mcp-runtime-configuration.md)：启停、刷新、原名单与恢复边界。
- [Spec094](094-composer-tools-and-permissions.md)：原活动工具选择、保存及后续模型请求生效合同。
- [HTTPX 代理](https://www.python-httpx.org/advanced/proxies/)：复用已有客户端认证和代理连接能力。
- [Python 3.12 urllib.request](https://docs.python.org/3.12/library/urllib.request.html)、[urllib.parse](https://docs.python.org/3.12/library/urllib.parse.html)：复用主机匹配和凭据编码，不引入通用路由平台。

## 11. 实际实现与验收（2026-10-20）

### 11.1 接线结果

- `NetworkSettings` 已接分离认证和冻结的直连名单；敏感字段不参与 repr/公共 model_dump，验证错误隐藏输入。`selected_proxy` 先区分目标与名单中的 IP/域名：IP按规范化地址相等，只有域名进入标准库点边界匹配；再按原模式选择代理并编码认证一次。旧 URL 认证保持，冲突/非法设置启动失败。
- Web 工具和 HTTP MCP 共用原选择入口。MCP 资源内部通过可信字段复制保留代理，并把完整网络材料纳入原 HMAC 连接身份；不打印这些材料。没有新增网络管理层或进程环境修改。
- CLI/Web 将顶层默认值直接交给原 `initial_active_tools`，明确区分省略与空集合。原注册表在 MCP 发现后校验，原 SessionService/保存链负责新会话与恢复；无工作区 Web、SDK 显式参数、子角色及权限链保持。
- 安装 worker 读取临时配置并验证已安装包的认证/直连选择，以及真实 Web 新会话的三个默认工具；报告增加 `configuration=passed`。只是扩展原安装验收，不创建替代安装流程。

L1字段存在、L2严格校验/真实路由、L3客户端/入口/持久化消费均有下列测试与源码外安装证据。新生产源码仍全部在原文件中，没有新增无消费者抽象。

### 11.2 TDD、复核与环境诊断

新增16个测试函数、参数化合计64个用例。网络首轮正确红灯为27 failed/18 passed；默认工具与MCP复制首轮17 failed/43 passed。输入非法用例中原有extra禁止已能拒绝的部分不冒充新行为红灯。

独立安全/架构只读复核没有未解决发现。架构复核另指出IP条目会被标准库按域名后缀使用，已先用 `foo.127.0.0.1` 复现1 failed/3 passed，再在源代码区分地址/域名；补审确认符合合同。测试未放宽或跳过。

首轮全量32 failed/3488 passed，失败均涉及源码PATH缺少fd。已用既有 `scripts.native_assets.prepare_native` 从固定资产准备rg15.2.0/fd10.5.0，仅为测试子进程设置PATH；未改系统环境、配置或测试基础设施。原32项随后32 passed，最终全量结果如下。首轮失败日志保留，不把环境未齐的运行记为成功。

### 11.3 命令与实际结果

证据目录：`build/spec120-8ndt27vu/`。以下命令均在本会话实际执行，Python为项目`.venv`。

```bash
PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec120-8ndt27vu/wheel
.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec120-8ndt27vu/wheel --output build/spec120-8ndt27vu/install-config.json
.venv/bin/python -m twine check build/spec120-8ndt27vu/wheel/*.whl
.venv/bin/python -m pip check
git diff --check
```

| 验证 | 实际结果/证据 |
| --- | --- |
| 最终全后端 | **3524 passed, 1 warning in245.80s**，`backend-final.log`；warning为既有研究代码的LangChain MCP beta提示 |
| 定向网络/配置/CLI/Web/MCP | **128 passed in14.62s**，`focused-final.log` |
| 发行检查 | **47 passed in0.40s**，`release-checks.log` |
| 前端 | **46文件367 passed**，`frontend-tests.log`；TS/Vite构建成功，`frontend-build.log` |
| 生产源码与归档 | **159份**Python AST、500行上限及wheel内字节逐文件一致，`archive-current.json` |
| 干净安装 | **全部通过**，`install-config.json`：实际site-packages、配置、原生工具、前端/API、审批、SQLite重开、CLI及MCP主链 |
| MCP安装链 | 8次合成业务调用，进程全部退出，0供应商请求；无用户MCP/模型服务调用 |
| 包元数据/依赖 | Twine PASSED、pip check无破损；为执行已声明的检查安装了dev工具twine6.2.0，未修改运行依赖版本声明 |

候选仍沿用源码版本0.1.1，仅供验收，未作为新发行上传。wheel SHA-256：`5b8b8cb87b7a11c5fed9667d64290f803507afa30917d1c99763c6015ed3d0dc`。

以上证明本机、合成HTTP代理、官方MCP SDK夹具和源外安装链；不代替Windows原机、企业代理认证、真实MCP/供应商或业务效果。真实配置与用户库未改，模型统一代理仍未纳入。Spec120无遗留实现项，后续真实环境验证或发布分别确定范围；Spec119和学习工作完整保留。
