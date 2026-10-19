# Spec116：AITest CLI 与网页搜索、读取

状态：已实施并完成本地确定性验收，未提交/发布。仅后端、配置样例和说明；保留另一会话全部未提交成果。

## 已确认行为

- 新工具 `aitest_kit(args: list[str], timeout?: int)` 直接启动配置的程序及前缀参数，不经 Shell。
  默认程序 `aitest`；会话工作区为 cwd，CLI 参数原样交给 aitest。doctor/report 和帮助默认 Allow，其余 Ask；Deny 优先。
  不把 doctor/report 宣称为只读。工具串行、may_mutate。优先读取当前项目 `.claude/skills` 的匹配技能，工作流不硬编码在运行时。
  每次新进程，继承环境并支持覆盖/移除。无默认执行期限，0/null 同样不限；复用现有进程树收尾、停止、进度及合并输出归档，2000 行/64 KiB。
- `web_search(query, max_results?, timeout?)` 一个公共工具。默认百度→Tavily，顺序可配置。
  每家最多一次，失败（网络/服务/鉴权/额度/响应结构）或空结果才回退；有效结果不因质量判断自动回退。
  每家总等待默认30秒，可配置；调用传0不限。停止/取消、本地Deny、非法参数不回退。
  Tavily固定Basic、关闭auto_parameters/answer/raw_content，不自动升级。两家失败为ToolException；正常空结果与失败分别报告。
  返回实际供应商与有限失败类别，不返回供应商错误正文、Key或代理凭证。
- `web_fetch(url, timeout?)` 只做HTTP(S) GET，提取HTML可见正文（保留链接/标题）或返回文本/JSON；不执行JS、不使用浏览器Cookie。
  公共目标默认Allow，私有/回环/内网目标Ask；显式规则与模式沿用既有管线。DNS地址加入审批对象，连接绑定审查IP并保留Host/TLS SNI。
  重定向逐跳检查Deny及公共/私有目标；新私有目标未获授权时反馈新URL供重新调用审批，不能在工具执行中再插入interrupt。
  输入/下载/返回大小及重定向数可配置；不宣称浏览器/PDF/OCR能力。输出为不可信网页数据。
- Web共用 `[network]`：auto读取环境/系统代理及NO_PROXY，direct忽略代理，proxy强制使用显式HTTP(S)代理。
  不偷偷从代理降级直连；TLS验证保持开启。HTTP(S)代理不新增依赖，SOCKS不在当前依赖支持范围。
  搜索供应商端点及Key环境变量名在配置中；不修改真实settings/.env，不把Key写进样例。

## 当前链路与改后链路

当前：CLI/Web `load_settings → create_builtin_tools → prepare_tool_registry → 会话活动集合 → ToolSetMiddleware → 参数校验/权限准备 → 批量审批 → ExecutionAuthorizationMiddleware → 工具 → ToolError`。

改后在同一集合登记三个ToolSpec，公共schema不含runtime。旧会话仍按持久活动集合选择，新会话默认启用新增工具；UI已有启停功能不新增前端。

正常例：`web_search(query="pytest fixture") → 权限Allow → 百度429 → Tavily Basic → 统一结果/供应商/回退原因 → ToolMessage → 下一次模型调用`。
失败例：两家返回鉴权/连接错误 → 有限日志记录供应商及类别 → ToolException → 现有错误消息 → 模型继续。取消时等待本地请求关闭，不启动备用调用。

AITest例：`args=["doctor"] → Allow → [executable,*prefix_args,"doctor"] → HostProcessRunner → 进度/尾部/归档 → ToolMessage`。
`args=["run",...] → Ask → 全批暂停 → 批准 → 单次执行许可 → 同一进程链`；非零退出返回含输出的具体错误消息，不自动重放。

## 文件与职责

- 新增tools/aitest_kit.py：schema、最小命令类别判断、argv、提示词；不维护子命令清单或测试业务策略。
- 新增tools/process_execution.py：从shell.py提取已有输出/停止/清理执行主体，供两种Shell及AITest复用；Shell schema/权限/行为保持。
- 新增tools/web_settings.py：网络、供应商、搜索/读取预算；由现有AppSettings/BuiltinToolSettings聚合。
- 新增tools/web_network.py：有限下载、代理选择、取消与等待、DNS/URL校验；新增tools/web_fetch.py负责审查/重定向与文本提取。
- 新增tools/web_search.py：两个API的请求/结果适配及固定回退；没有新SDK/服务。
- 修改tools/settings.py、builtin.py、__init__.py、settings.py、CLI/Web装配：配置与注册接线。
- 必要时调整技能目录提示，明确AITest任务的claude来源偏好，不全局改其他任务默认来源。
- 更新配置样例、README/工具说明/文件地图及handoff；与并行文档变更做增量合并。
- 新增确定性配置/argv/审批/网络/回退/取消/真实本地HTTP及进程测试，更新精确登记名称的旧断言。

## 验收

先新增失败测试，再实现。使用httpx MockTransport、合成Key、临时工作区和本地HTTP服务；禁止调用真实付费API。
覆盖：首家成功/失败/空、两家失败/空、Key缺失/非法响应、单家30秒及0、停止不回退、下载预算、代理NO_PROXY/强制代理、DNS绑定、重定向私有目标/Deny、AITest默认审批与非零输出、注册/配置真实链路。
最后后端全量、前端既有测试/构建、compileall/pip check/diff检查；安全与架构独立复核。当前机器没有aitest-kit依赖和两家Key，真实AITest/供应商业务验收须明确列为未验证，不伪造完成。

## 实施中补证与收敛

- 首批新工具测试9红1绿：缺模块/配置入口；实际注册集成另有1红4绿，登记后转绿。
- 独立安全/架构复核发现帮助token可能被Click当作选项值或pytest透传，已用严格帮助形状处理；doctor/report的Allow保持。
- 压缩炸弹探针证明HTTPX会先自动解压再分块，原预算后置不足；改为identity请求，并在读取正文前拒绝压缩响应。回归验证不消费响应流。
- 当前httpcore的HTTP CONNECT隧道不消费sni_hostname扩展；复用HTTPX并提供绑定原域名的标准SSLContext，TLS验证不关闭。
  本地临时证书/HTTPS服务器/HTTP CONNECT代理探针同时验证原SNI、审查IP及校验开启；未请求外网。HTTPS代理独立上下文已接线，未单独搭建HTTPS代理实测。
- 双栈第一地址失败只在已审查集合内切换连接；Windows从有效环境大小写不敏感取PATH，移除PATH不再偷用父PATH。
- 上述五项回归先确认5红9绿再修复。Shell共享ProcessToolSettings只放实际共用字段，AITest不暴露无消费者的Shell探测设置。
- 现有工具名单/执行模式/副作用向量的精确断言同步扩展；旧CLI factory桩显式接收network_settings，未保留旧分支或减少断言。
- 配置样例严格AppSettings.model_validate后与load_settings结果相等；源码开发测试使用仓库已有build/release-native-0ff7df8/bin的fd/rg临时PATH，不修改用户环境或安装新依赖。

## 最终验收（2026-10-19）

- 新增定向27 passed in1.26s；覆盖配置、argv/默认权限/退出反馈、搜索回退/停止/期限、DNS绑定/重定向/Deny、代理/TLS/压缩预算及真实本地HTTP审批。
- `PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q`：**3004 passed in121.66s**。
  日志 `/tmp/naiyue-spec116-backend-green.log`。首轮全量旧消费者名称/桩失败已完整迁移后重新全量通过。
- `cd frontend && npm test -- --run`：**44文件347 passed**；`npm run build`：TypeScript/Vite成功，存在原有chunk大小提示。
  日志 `/tmp/naiyue-spec116-frontend-tests.log`、`/tmp/naiyue-spec116-frontend-build.log`；本任务未编辑前端源文件。
- `python -m compileall -q naiyue_test_agent`、144份生产Python AST/500行约束、`python -m pip check`、`git diff --check`通过。
- 独立安全复核23项通过，原gzip/帮助绕过探针关闭；独立架构扩展141项通过。
  本地TLS探针 `/tmp/naiyue-spec116-tls-probe.py`：direct HTTPS及HTTP CONNECT均PASS，保留原SNI、审查IP与证书校验。
- 注册主链实测12工具，新工具全部native async、模型schema不含runtime；sample严格解析无fallback，CLI/Web显式传递网络配置。
- **证据边界**：未请求真实百度/Tavily搜索API；AITest通过本机Python合成CLI及下述同名可执行程序验证通用执行机制，不代表真实AITest工作流验收。
  Windows仅有路径/环境合成回归，没有本机Windows实跑。HTTP(S)代理配置已接，真实TLS探针覆盖HTTP CONNECT，未单测HTTPS代理服务。
- 下一步：用户配置测试环境AITest程序及两家Key后，做少量真实搜索/网页/AITest验收；提交及发布另行授权。无真实配置、数据库、原AIAutoTest项目或前端源文件改动。

## 同名CLI补充验证（2026-10-19）

- 上一轮的合成CLI显式使用Python路径，未覆盖默认`aitest`名称发现；本次补临时`bin/aitest`可执行脚本，保留`executable="aitest"`、空`prefix_args`，仅在工具子进程环境的PATH前置临时目录。不安装真实AITest，不修改用户PATH或配置。
- 调用链：异步应用→确定性模型工具调用→权限准备/批量审批→`create_aitest_kit_tool.execute`→真实`shutil.which`→真实`HostProcessRunner`→临时同名CLI→ToolMessage→模型继续。未替换程序查找或进程执行器。
- `--help`、`doctor`、`report`无需审批；`report`主动以7退出并写stderr，错误反馈后模型继续。`run`暂停时磁盘调用记录保持3条，批准后变为4条；下一次`run`被拒绝，记录仍为4条。检查实际argv、工作目录和程序路径，包含空格、中文与shell符号的参数逐项相等。
- 命令：`PYTHONPATH="$PWD:$PWD/tests" .venv/bin/python -m pytest -q -s /tmp/naiyue_spec116_same_name_cli_test.py`，**1 passed in1.74s**，实际启动4个子进程。此探针为本机POSIX验证，不代表Windows实机或真实AITest业务验收；本次没有修改生产代码。
