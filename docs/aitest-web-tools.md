# AITest 与 Web 工具配置

Spec116新增 `aitest_kit`、`web_search`、`web_fetch`，复用既有CLI/Web工具启停和权限模式，已随 [PyPI 0.1.1](https://pypi.org/project/naiyue-test-agent/0.1.1/) 发布。
配置在用户目录的 `~/.naiyue-test-agent/settings.toml`（Windows为 `%USERPROFILE%\.naiyue-test-agent\settings.toml`）；
完整带注释字段见[样例](examples/settings.example.toml)。文件不自动改写，改动后重启；已保存会话的活动工具集合不会自动扩展，可在工具菜单启用。

## AITest

`aitest_kit`模型参数为 `{"args":["doctor"],"timeout":0}`；timeout省略/null/0均不限时，正整数为单次执行秒数。
工具只执行配置的程序及前缀，不拼Shell字符串；模型用 `args=["--help"]` 或 `["run","--help"]` 查询CLI。
会话工作区是cwd，模型传入的 `--workspace` 仍由AITest CLI解释，不是工具边界或沙箱。

`doctor`/`report`默认Allow；严格帮助形状也Allow；其余默认Ask，显式Deny仍优先。
`--help`作为选项值或pytest透传参数不获得默认Allow。
这些是审批默认值，不宣称doctor/report没有写入、导入或外部效果。

已有测试环境可直接使用其中的Python，避免在Agent环境重复安装aitest-kit：

```toml
[tools.aitest_kit]
executable = '/absolute/path/to/test-venv/bin/python'
prefix_args = ["-m", "aitest_kit.cli"]
```

Windows可将executable指向测试venv的 `Scripts\python.exe`。或者保留默认 `aitest`，在environment.path_prepend中配置其Scripts/bin目录。
环境默认继承父进程，支持显式覆盖和最后移除；Web访问令牌默认不传给子进程。
所有操作继承宿主用户实际权限，尚无沙箱。每次新进程，输出/进度/停止/归档与Shell共享。 Spec125-01移除共享归档器的工作区累计额度，AITest仍保留每调用`max_archive_bytes`，raw/text合计；目录固定为`<workspace_root>/naiyue-agent-output/`，不自动删除。AITest的timeout不采用`shell_tasks`执行上限。

AITest任务优先读取项目 `.claude/skills` 的匹配SKILL.md；详细步骤、knowledge_refs、失败分类由技能/项目资料提供。
没有硬编码子命令工作流，生成pytest始终是输出；不要手改来掩盖用例/生成问题。

## 搜索

配置两家Key对应的环境变量，值由启动服务的终端继承，不放进仓库。默认变量名：

- `BAIDU_SEARCH_API_KEY`：百度千帆AppBuilder搜索API Key。
- `TAVILY_API_KEY`：Tavily API Key。

可以在供应商配置中改 `api_key_env` 名称及HTTPS端点，不需要更换模型供应商。
工具不自动注册服务、购买额度或切付费计划；账户已开通的计费方式仍由供应商账户管理，代码无法将一个已收费账户变成免费账户。

`web_search`参数：query必填、max_results可选、timeout可选。
默认百度优先，失败或空结果后先尝试Tavily；两家初试后，只对临时失败项使用共享追加重试额度。缺Key的供应商跳过；认证/格式/预算错误不重试该家，代理配置或407鉴权错误立即结束。
Tavily固定Basic，auto_parameters/answer/raw_content关闭；成功结果不会自动请求第二家。
整次搜索总时限默认30秒，包括所有供应商、重试和退避；调用timeout覆盖，0取消总时限，但已启用策略的单次尝试上限仍适用。初试按剩余总时间与未初试有效供应商数分配上限，给备用供应商留出机会。停止/取消会先结束本地请求，不再回退或重试。
所有已成功搜索为空时报告空；有失败时逐家说明，不能把失败伪装成无结果；两家均失败走既有工具错误消息，Agent可继续。

返回供应商、回退类别、标题、URL和片段。供应商错误正文/Key/代理凭证不进入日志或模型反馈。
检索结果不是验证结论；模型按需要调用web_fetch读取原文并引用来源。

## 网页与网络

`web_fetch(url, timeout?)`读取HTML可见正文（含链接）以及文本/JSON；不渲染JS、不使用浏览器Cookie，不做PDF/OCR。
公共目标默认Allow，私有地址/回环/内网目标Ask；DNS地址进入审查对象，实际连接绑定该地址，TLS继续校验原域名。
同次DNS返回的其他已审查地址仅在连接建立失败时尝试，仍受同一总期限限制。
重定向逐跳检查Deny和目标范围；新私有目标没有授权时返回新URL供模型重新调用审批。
网址规则可用现有 `[permissions]` 的精确args匹配，例如 `tool="web_fetch", args={url="http://127.0.0.1:8080/docs"}`；
路径glob仍只用于文件工具，不把URL伪装成文件路径。

`[network] mode="auto"`遵循系统/环境HTTP(S)代理及NO_PROXY；没有代理则直连。
`mode="direct"`忽略代理；`mode="proxy"`必须有显式proxy，失败不会自动直连。
当前依赖支持HTTP(S)代理，不承诺SOCKS。TLS验证开启，支持SSL_CERT_FILE/SSL_CERT_DIR；HTTPS经CONNECT代理也保留原域名校验。
本设置用于Web工具和远程HTTP MCP，不改变模型客户端、AITest内部HTTP测试或Shell/stdio服务程序的网络设置。

同一网络策略也用于远程HTTP MCP。可配置 `proxy_username`、`proxy_password`，直接填写
原始值，无需手工URL编码；两项需同时提供，不能和proxy URL内的认证混用。`no_proxy`
列表先检查主机直连例外，之后再按proxy/auto选择代理；域名匹配自身及点边界子域，
IP按地址匹配；当前源码Spec121还支持IPv4/IPv6 CIDR、IPv4尾段通配（如`10.*`）、仅子域通配`*.corp.example`及显式全部直连`*`，不接受协议、端口或路径。匹配URL主机，不查询DNS；网络设置修改后重启。

顶层 `default_active_tools` 可以设置CLI/Web新根会话初始工具，例如
`["read", "grep", "powershell", "aitest_kit", "web_search", "web_fetch"]`。
省略维持原默认，显式`[]`表示零工具；旧会话保留已保存选择。工具被选中不免除审批，
且此字段不影响AITest执行程序、子角色名单或SDK显式initial_active_tools。

下载、输出及重定向预算可配。首版请求identity编码，并在读正文前拒绝压缩响应，避免自动解压突破预算。
网页正文作为不可信数据提供给模型，不授予工具执行权限。网络服务实际可用性、免费额度及搜索质量需要真实账户/网络另行验收。

## 外部工具重试（Spec121，当前源码）

配置见样例的`[tool_retry]`：默认覆盖`web_fetch`、`web_search`、全部MCP，最多追加2次，单次尝试30秒，指数退避上界1→2→…→10秒，并在0到上界间随机抖动。有效`Retry-After`是等待下界，超过剩余总时间便不再重试该候选；不会提前重试，也不阻塞尚未初试的备用搜索供应商。两个供应商加2次共享追加额度，最多4次供应商请求。

Web只重试临时传输故障、超时和HTTP 429/500/502/503/504；永久错误、TLS/代理配置、解析/预算及权限错误不重试。抓取仍复用原审查地址快照，每次重新检查重定向权限。普通耗尽/超时仍产生配对工具错误消息供Agent继续处理；停止、图中断与机制故障保持原传播合同。

`tools=[]`关闭策略；支持两个Web名称、完整MCP名、`mcp__服务__*`或`mcp__*`。原工具总时限始终有效。MCP的提交阶段和精确安全名单见[MCP说明](mcp-client.md#外部调用重试spec121当前源码)。配置段本身修改后重启，原MCP新增/启停仍可通过“刷新工具与模式”应用。

内部尝试沿用一次授权、一个逻辑工具调用ID及已有进度/日志；不会重复计为多次模型工具调用，也没有新增任务级硬预算。Spec121尚未发布，不能据此推断公开0.2.0已具备上述行为。
