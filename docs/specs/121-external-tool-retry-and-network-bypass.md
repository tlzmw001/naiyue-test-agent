# Spec121：外部工具重试与直连名单扩展

状态：2026-10-21，00—06全部完成本机验收，尚未提交/发布。用户授权02—06连续实施，第7节为合同、第8—9节为当前主链与结果；第6节保留01阶段快照。

## 1. 用户目标与范围

用户确认把`no_proxy`的CIDR和通配符支持作为临时新增切片，合并到当前外部工具可靠性工作中。先完成名单扩展，再逐片推进Web与MCP重试。

- 已确认保留`timeout`的总时限含义；一次逻辑工具调用中的连接、请求、重试和退避不能重新获得完整总时限。
- `web_search`优先尝试尚未调用的供应商，之后再重试临时失败的供应商；追加重试额度在一次搜索中共享。
- MCP取消单次代理覆盖、临时连接和`arguments/client`入参包装。继续使用现有`network.mode=proxy`、代理鉴权和`no_proxy`，保留会话连接及原工具Schema。
- 不增加Agent任务级时间/次数硬预算，不修改真实设置、环境、用户数据库、模型供应商网络或Shell/stdio服务进程的联网配置。

## 2. 开片前调用链

```text
settings.toml [network]
  → settings.load_settings / NetworkSettings校验
  → Web工具工厂持有网络设置 / MCPSessionResources持有可信副本
  → web_network.selected_proxy(目标URL, 网络设置)
  → Web HTTPX客户端 / MCP HTTPX2传输
  → 目标主机直连，或使用配置/环境代理
```

开片前`NetworkSettings.no_proxy`是规范化字符串元组，只支持精确IP及域名自身/子域。`selected_proxy`在proxy/auto之前检查配置名单；proxy模式不额外合并环境`NO_PROXY`。auto之后仍调用标准库处理系统/环境名单。

名单匹配只依据URL中的主机，不查询DNS；域名解析成内网IP并不会使其匹配IP名单。网络选择与`web_fetch`原有目标解析、私网审批、地址固定和逐跳检查是不同职责，不能互相替代。

## 3. 121-01：CIDR与有限通配符

### 3.1 对外合同

保持`NetworkSettings`字段、`no_proxy`字符串列表格式以及`selected_proxy`函数签名。扩展名单语法：

| 写法 | 含义与规范化 |
| --- | --- |
| `10.0.0.0/8` | IPv4网段，含首末地址；只匹配IPv4字面主机 |
| `fd00::/8` | IPv6网段；只匹配IPv6字面主机 |
| `10.1.2.3/8` | 允许主机位，规范化为`10.0.0.0/8` |
| `10.*`、`10.20.*`、`10.20.30.*` | IPv4从某个整段开始到结尾的通配，分别规范化为/8、/16、/24 |
| `10.*.*.*`、`10.20.*.*` | 接受等价的尾部多段星号，规范化为相同CIDR |
| `*.corp.example` | 仅匹配其子域，包括多级子域；不匹配`corp.example`本身，不匹配伪后缀 |
| `*` | 显式要求所有目标直连；不会因代理失败自动产生 |
| 原主机、域名、精确IPv4/IPv6 | 保留原有语义；`corp.example`及`.corp.example`仍匹配自身及子域 |

CIDR只接受十进制前缀长度，不接受点分子网掩码、端口、路径或IPv6 zone ID。IPv4通配必须是整段且全部位于尾部，不支持`10.2*`、`10.*.2.*`。域名只支持开头的`*.`，不支持任意位置glob、`?`或字符集。域名保持IDNA/大小写/末尾点规范化。IPv6范围使用CIDR，不用星号。

非法条目继续在加载设置时失败，保留安全错误日志与输入隐藏；不静默忽略、字符串前缀匹配或猜测缺省网段。CIDR和通配符仅扩展本项目配置名单，不改变环境`NO_PROXY`的标准库语义。

### 3.2 正常与失败路径

正常：用户配置`mode=proxy`、`no_proxy=["10.*"]` → 校验规范化为`10.0.0.0/8` → Web/MCP构造客户端时选择`http://10.20.30.40:8000` → IP落在网段 → 返回None → 原HTTP客户端直连；其他IP继续使用代理。不会给目标发送代理鉴权。

失败：用户填写`10.999.*`或`10.0.0.0/99` → `NetworkSettings`拒绝 → `load_settings`按现有网络设置错误路径记录安全日志并终止装配 → 没有HTTP客户端或MCP连接被创建。代理不可用时仍按原错误返回，不新增直连回退。

### 3.3 修改面

| 文件 | 修改 |
| --- | --- |
| `tools/web_settings.py` | 原名单校验增加CIDR及有限通配符规范化 |
| `tools/web_network.py` | 原选择循环增加网段、子域通配与显式全量匹配 |
| `tests/test_network_bypass_patterns.py` | 新语法、边界、模式、实HTTP及MCP接线验证 |
| `tests/test_web_tool_settings.py` | 将原明确拒绝CIDR/子域星号的两项迁移到新合法合同，保留其余非法断言 |
| 设置示例、MCP文档 | 同步语法、匹配范围、环境名单边界 |
| 本Spec、future-work、handoff | 分片与验证状态 |

生产代码只扩展两个既有模块，不新建路由服务、注册层、缓存或依赖。预计包含文档超过8个文件。无API端点、DB字段/迁移或序列化类型变化；MCP身份仍消费规范化后的原网络设置。

### 3.4 验证

先增加失败测试，确认旧实现拒绝新语法；再改校验/匹配，迁移原合同测试，不放宽非法输入或凭证隐藏断言。

- IPv4/IPv6 CIDR内外、首末地址、/0及主机前缀、族不匹配、主机位规范化。
- IPv4尾部星号、域名/IDNA子域星号、显式`*`，及非法星号/前缀/URL形态。
- proxy/auto名单优先级、direct行为、原代理认证与环境选择回归。
- 不按域名解析结果匹配IP，IP名单不匹配伪域名；既有域名自身/子域语义保持。
- 真正HTTP客户端直连/代理与代理认证分离；MCP仍调用同一选择入口。
- 设置加载、公开转储、可信复制/身份及日志不暴露代理凭据。
- 专项pytest、全后端、前端测试/构建、标准本机构建、源码/包一致性及diff检查。

## 4. 后续重试合同记录

本节记录已讨论方向，尚未接入生产代码。后续02开片再固定配置结构、默认值和具体适配器接口。

### 4.1 共用部分

配置适用工具、最大追加重试次数、单次尝试上限、指数退避及抖动。保持一次逻辑工具调用的总截止时间，实际尝试上限取单次上限与剩余总时间较小者。退避消耗总时间；到期或停止后不再发送请求，先取消并收尾当前尝试。

重试位于已授权工具内部外部IO处，不重新消耗一次性审批，不重放整个模型/工具循环。普通最终失败仍回填原工具错误，机制/控制/必要保存错误不按普通网络失败吞掉。观察失败不能决定重试。

### 4.2 Web搜索

先按配置顺序让有效供应商各初试一次，成功即返回。缺密钥不发请求。临时失败记录该供应商下次可重试时间；没有结果且无新供应商后，才在共享追加额度和总时限内重试候选。

每家初试不消耗追加额度，故两家供应商加`max_retries=2`最多四次供应商请求；`max_retries=0`仍保留初次供应商回退。单次上限和初轮剩余时间分配共同避免首供应商占完总时间。空结果不重试该供应商；供应商认证/契约错误可切下一家但不重复；明确共用代理配置/鉴权错误不靠换供应商修复。

退避从该供应商失败时计算，调用其他供应商经过的时间计入等待。遵守有效`Retry-After`下界，不为等待一家阻塞另一家初试，不截短服务端等待要求。总时限到期返回超时，不把未完成搜索包装为全部无结果。

开片前`web_search.timeout`按每家供应商计时；本片改成整次搜索总时限，属于明确行为调整，工具Schema描述、设置说明与原测试同步迁移。

### 4.3 MCP

保留原工具Schema、审批、会话连接及网络选择。尚未进入`tools/call`的可信临时故障可以考虑重试；提交后结果未知默认不自动重放。若要允许某个工具提交后重试，必须有本地明确只读/幂等或业务去重合同；不凭工具名、模型推断或远端hint放行。停止、权限拒绝、目录变化及普通业务错误不重试。

## 5. 合并分片顺序

| 切片 | 内容 | 状态 |
| --- | --- | --- |
| 121-00 | 合并范围、当前链与分片Spec | 已完成 |
| 121-01 | no_proxy CIDR与有限通配符 | 已实现并完成本机验证，见第6节 |
| 121-02 | 固定重试配置/默认值、共用截止时间/额度/退避机制 | 已完成，见第7—9节 |
| 121-03 | web_fetch接入，保留目标/逐跳权限与地址检查 | 已完成 |
| 121-04 | web_search供应商初试、共享重试与总时限协调 | 已完成 |
| 121-05 | MCP分阶段安全重试，保持原连接和入参 | 已完成 |
| 121-06 | 组合故障、观测口径及标准安装验收 | 已完成 |

每个切片先红灯、最小实现、专项/全量及对应构建验证，再记录实际结果。正常、失败、取消、清理与日志都需断言，不把单次本地成功当作真实代理、供应商或业务效果。

## 6. 121-01实际结果与下一片

2026-10-21完成名单扩展。生产代码仅修改原`NetworkSettings`校验与`selected_proxy`循环，无新模块、依赖、公开签名或Schema字段变化。IPv4尾部星号规范化为CIDR，IPv6网段、子域星号与显式全量星号均经原Web/MCP网络入口消费。旧IP精确匹配、域名自身/子域、代理鉴权、环境选择及失败不直连保持。

新测试文件104项覆盖格式、首末地址、族隔离、伪后缀、IDNA、总星号、无DNS、加载/日志隐私、2次本地真实HTTP及7项MCP传输构造接线。旧测试中明确拒绝`*.corp.example`、`10.0.0.0/8`的两项随新合同迁移到合法输入覆盖，其余非法输入断言保持；未修改测试基础设施。

首次红灯为73 failed / 23 passed，确认旧校验拒绝新语法。首轮实现后边界补测发现`*..corp.example`被错误规范化，新增回归确认1 failed / 103 passed，再修原校验使其拒绝；无放宽断言或跳过测试。

| 验证命令/范围 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_network_bypass_patterns.py tests/test_web_tool_settings.py tests/test_web_network.py tests/test_web_search_tool.py tests/test_web_fetch_tool.py tests/test_mcp_transport_policy.py tests/test_mcp_resource_transports.py --tb=short` | 190 passed in 5.64s |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | 3670 passed in 230.73s；仅测试进程复用既有固定原生fd/rg PATH，与CI方式一致 |
| `npm test -- --run`（frontend） | 48文件，385 passed |
| `npm run build`（frontend） | TypeScript/Vite通过，保留原有包体积提示 |
| `.venv/bin/python -m compileall -q naiyue_test_agent`、AST/行数检查、`pip check` | 161份生产源码通过，均不超过500行，无依赖问题 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec121-01-vfQEyG/wheel` | 标准wheel构建成功，291文件，3,624,002字节 |
| wheel源码字节比对、`twine check` | 161份Python与当前源码逐份一致，Twine PASSED |
| `python -I`从wheel优先导入路由探针 | 确认导入来自wheel，6个CIDR/通配内外路由断言通过，0网络请求；不是独立venv安装验收 |
| 安全专项复核 | 无高置信度新安全问题；独立专项132 passed |
| `git diff --check` | 通过 |

日志与包保存在`build/spec121-01-vfQEyG/`。没有改真实配置、环境文件、用户数据库、模型/Shell联网或MCP会话/入参；现有学习文件与共享handoff内容保留。没有提交、push、标签或发布；公开PyPI 0.2.0尚不含本片。真实代理、其他原机及供应商业务效果未在本轮验证。

唯一下一片121-02：先说明并确认重试配置的适用工具、默认值及总截止时间/单次上限/追加额度/退避与停止合同，再实现共用机制。03—06仍未实现；MCP单次网络覆盖不恢复排期。

## 7. 02—06连续实施合同（后续授权覆盖第6节下一步）

新增顶层`[tool_retry]`，非法配置按安全错误终止启动，不随普通显示预算静默回退。默认tools为`["web_fetch", "web_search", "mcp__*"]`，max_retries=2，attempt_timeout_seconds=30，initial_delay_seconds=1，backoff_factor=2，max_delay_seconds=10；Full Jitter从0到指数退避上界取值。tools可设为空或指定两个Web名称、完整MCP名、服务级`mcp__服务__*`、全部`mcp__*`；其他工具不因写进名单而获得自动重放。replay_safe_tools默认空，仅接受完整MCP工具名，显式确认其业务只读/幂等/去重合同；不采用远端hint。

保持Web工具入参及MCP原Schema。Web现有timeout继续是总时限（搜索明确收紧为整次搜索），0关闭总时限但不关闭已选策略的单次尝试上限；MCP使用原call_timeout_seconds作为总时限，0仍不设总截止。审批等待不计入，获准后的资源等待/建连/目录、全部尝试/退避均计入。名单未选择的工具不加单次上限或追加重试；搜索的整次总截止语义统一。

共用tools/retry.py负责截止、受控等待、追加额度、逐候选退避与安全临时错误分类；抽取原web_network.await_request的真实资源所有权逻辑并迁移全部生产/测试消费者，不保留废弃转发。三个适配器共享此职责，供应商选择和MCP提交阶段仍归各自适配器。

Web仅重试可信临时网络/超时和HTTP 429/500/502/503/504；404、认证、预算、解析、TLS证书/代理配置及权限错误不重试。有效Retry-After为等待下界，超出剩余总时间则不再选该候选。抓取重试仍从原审批地址快照出发，每次重走重定向校验，不放宽DNS/私网/逐跳检查；单次尝试包含原地址回退和重定向链。

搜索初轮每次上限为min(单次上限, 剩余总时间/未初试有效供应商数)；初轮不额外退避。之后重试选择最早可用候选，同步保留配置顺序作为平局顺序；每家退避从失败时起算，调用另一家经过的时间计入。两家初试+共享2次追加最多4次请求。空结果只标记完成，不重试该家；部分空结果与供应商失败分开描述。总截止到期始终为超时，不伪报全部无结果。

MCP连接Owner仅给安全本地资源错误增加可信临时分类，不暴露远端文本/凭据。工具内部在一次许可下重试借用、目录及调用；每次重检配置/目录/停用，失败连接按原manager退休后再借用。已提交只对replay_safe_tools允许自动重复，且仍只处理临时传输故障；isError/业务错误不重试。跨尝试保留ever_submitted，后续预检失败不能把此前结果未知改成未执行。

复核补充：MCP受控HTTP传输的响应超预算/压缩拒绝原本同为ReadError。connections.py为这两项增加明确的本地ReadError子类型；Owner与工具执行复用MCP错误分类，包含异常组时也必须全部是临时故障才能重试，不按消息字符串猜测。既有ReadError捕获仍有效，响应边界不放宽。

失败后按原工具错误结果回填，控制/审批/机制/必要资源合同保持。进度与安全日志记录tool/provider、追加尝试序号及等待，不含query、URL、鉴权或正文；沿现有tool_call_id记录一次逻辑调用，不增加事件种类/DB表或把内部尝试计为多次模型工具调用。新配置通过原CLI/Web启动和MCP刷新创建的工具消费；设置本身不热更新。

验证范围：共用时钟/额度/退避确定性测试、Web状态/供应商顺序/Retry-After/总截止/停止与清理、MCP未提交可重试/未知不重放/显式安全重放及一次许可、真实应用事件配对与下一轮模型继续；全量后端、前端/构建、标准wheel与源外干净安装。新增机制预计超过8文件，不新增依赖，不改真实配置/用户库/版本，不自动提交或发布。

## 8. 02—06实际接线与可追踪主链（2026-10-21）

### 8.1 配置生产者与执行消费者

`settings.load_settings → AppSettings.tool_retry`产生不可变`ToolRetrySettings`。非法名单、负次数、非有限时间等使启动失败，日志只有安全类别。CLI/Web的原`create_builtin_tools`把同一策略传给两个Web闭包；两个异步应用工厂把策略传给`MCPSessionResources`。MCP初始注册与运行中刷新创建的ToolSpec均读取该资源对象，未新增另一套连接管理。此段设置本身不是热更新字段。

`RetryBudget`只负责一次逻辑调用的单调时钟截止、追加额度、每候选最早重试时间，以及取消/停止后的本地任务收尾。传入的`tool_name`来自已注册工具，`total_seconds`来自原工具设置/模型timeout。返回`attempt()`的业务结果不替供应商或MCP作选择，`claim_retry()`只在等待和停止检查通过后消费一次追加额度。原`web_network.await_request`的真实任务所有权逻辑已迁到`retry.await_attempt`，生产和测试无旧入口消费者。

### 8.2 三条正常/失败路径

| 场景 | 当前真实调用与数据合同 | 验证入口 |
| --- | --- | --- |
| 抓取503后成功 | 权限层产生含原URL、DNS地址列表及args的subject → `fetch`消费一次permit → `RetryBudget.attempt(request_chain)`。第一次503返回状态/头和空结果标志，按`Retry-After`及退避调度；第二次仍从原subject出发，Host/SNI保持原域名，返回有界正文 | `tests/test_web_retry.py::test_fetch_retries_transient_status_with_identical_reviewed_target` |
| 抓取重试遇私网重定向 | 第一次503后重试，302的新URL重新解析 → `_redirect_allowed`重检Deny/范围；缺新目标授权时抛ToolException，原反馈边界产生同ID错误，不连接新私网地址 | `tests/test_web_retry.py::test_fetch_retry_rechecks_redirect_permission_and_never_follows_private_target` |
| 搜索回退后重试成功 | `search`冻结本次可用Key与供应商顺序，Key只进HTTP请求；Baidu503→Tavily503→Baidu200，初试各一次不消费追加额度，第三次消费共享额度1；返回供应商/有限attempt类别与结果 | `tests/test_web_retry.py::test_search_tries_fallback_before_retrying_failed_provider` |
| 搜索到总截止 | 一个budget贯穿所有供应商与退避；`remaining()`到0抛TimeoutError → ToolException超时反馈。已完成空搜索不能覆盖该超时，也不重置下一家时限 | `tests/test_web_retry.py::test_total_timeout_is_not_reset_when_switching_providers` |
| MCP预检恢复 | 人工批准一次 → `execute`核对permit/args/配置和目录身份 → `budget.attempt`借用会话连接、检查目录。第一次目录临时超时在borrow退出时退休失败连接；原许可内第二次借用/检查成功后只发一次低层`session.call_tool`。仍只有一对逻辑工具开始/结束事件，重试等待使用同ID进度 | `tests/test_mcp_retry.py::test_real_sdk_preflight_retry_uses_one_approved_call_and_one_business_request` |
| MCP提交后未知 | `ever_submitted=True`后临时断连，默认不重放，`error_result`产出`status=error/outcome=outcome_unknown`。精确安全名单才可用追加额度；每次deepcopy原args，服务端修改不能污染下一次。若之后停用/预检失败，artifact及正文仍保留“可能已执行” | `tests/test_mcp_retry.py::test_mcp_retries_only_typed_failures_with_explicit_replay_safety` |

MCP返回`is_error`属于已收到的业务结果，直接交原`format_result`；普通协议错误不凭文本猜测临时性。错误结果仍回填给下一模型调用；图控制、权限机制和外部取消不转换为普通工具错误。重试不重放Agent图、整批工具或模型回答。必要资源清理可以超过请求截止，不能由超时承诺远端副作用已取消。

### 8.3 复核闭环与验证口径

- 共用/Web/MCP均先见预期红灯再实现；证据见本轮目录的`02-red.log`、`web-red.log`、`mcp-red.log`。
- 架构复核发现新增重试进度可把GraphBubbleUp/GraphInterrupt当普通MCP失败：`control-red.log`四红后，内外异常边界显式传播，回归检查原对象与无后续尝试。
- 对抗检查补极值退避数值上界、曾提交后再停用的错误文案及响应策略错误分类，分别有`backoff-red.log`、`unknown-red.log`、`response-policy-red.log`及转绿结果。MCP本地ReadError子类型保留旧捕获合同，普通传输断连与预算/压缩拒绝不混淆。
- CLI既有测试的工具工厂替身同步新增关键字`retry_settings`，原因是实际启动必须传入该设置；未放宽原子Agent/审批/退出断言，也未改conftest。首轮全量的6项TypeError保留日志，迁移后6项定向通过。
- 安全专项无高置信度发现；架构发现已复核关闭。此结论不是任意供应商或所有操作系统原机证明。

最终全量与安装结果见第9节，本轮目录为`build/spec121-rest-52krHQ/`。不修改真实配置、用户数据库、依赖或版本；学习文档保持其他会话成果，不把助手验证记作用户掌握。


## 9. 最终验收与边界

| 验证命令/检查 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_mcp_retry.py tests/test_mcp_retry_control.py tests/test_tool_retry_policy.py tests/test_web_retry.py --tb=short` | 64 passed in2.94s；含一次批准下真实SDK预检重试、唯一开始/结束事件及同ID进度 |
| `PATH="$PWD/build/spec120-8ndt27vu/test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short` | **3734 passed in229.23s**；`backend-verified.log`。PATH只对测试子进程增加现有原生资源，不修改环境文件 |
| `npm --prefix frontend test -- --run` | **48文件385 passed**；`frontend-tests.log` |
| `npm --prefix frontend run build` | TypeScript/Vite成功，保留既有chunk大小提示；`frontend-build.log` |
| AST/单文件500行、compileall、pip check、`git diff --check` | 163份生产Python通过，无破损依赖；新模块实际被设置/工厂/执行链消费 |
| `.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/spec121-rest-52krHQ/wheel-final` | 标准构建成功：293文件、3,628,595字节；163份源码逐字节与包内一致，Twine PASSED |
| `.venv/bin/python -m scripts.verify_wheel_install --wheel-dir build/spec121-rest-52krHQ/wheel-final --output build/spec121-rest-52krHQ/install-final.json` | 源码目录外独立venv、二进制依赖安装/pip check、CLI/配置/原生Find-Grep/Web/API/审批/SQLite重开全部passed；MCP新增/根子复用/错误未知/停用/TTL/冷恢复/全部进程退出通过，8次合成业务工具，0真实供应商 |
| 安全/架构专项与对抗复核 | 图控制发现已关闭；永久响应策略拒绝不会当临时断连重试，无未关闭高置信度发现 |

02—06新增64项，原01的104项及所有既有行为回归保持。首轮6项CLI替身参数错误和各红灯记录保留供查证，最终绿色结果以`backend-verified.log`为准；最终包与安装报告的SHA-256一致。前端无新交互，继续消费原工具进度，不添加无必要提示。

默认策略由原用户设置加载；配置说明在`docs/examples/settings.example.toml`、`docs/aitest-web-tools.md`和`docs/mcp-client.md`。没有新事件种类、数据表、运行依赖、真实模型/搜索业务调用或任务硬预算。普通工具耗尽可让模型继续，但不保证模型任务成功、远端停止或任意服务幂等；必要资源清理可超过截止。MCP单次代理覆盖保持取消，网络修改及tool_retry配置需要重启，MCP工具新增/启停仍走原刷新。

Spec121没有剩余实现分片。未提交、push、打标签或发布，PyPI 0.2.0不包含这些新改动；未作其他平台原机、真实代理/供应商和业务效果验证。按原排序下一项是项目侧模型重试治理，先讨论SDK/项目唯一所有者、流式部分输出及取消边界，不由本次授权自动实施。
