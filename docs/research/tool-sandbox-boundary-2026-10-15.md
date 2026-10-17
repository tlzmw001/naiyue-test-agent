# 工具收口复核：Operations与后续沙箱边界

> 2026-10-15整合：当前工具实施边界见[Spec085](../specs/085-tool-system-rebuild.md)。早期完整文件/进程Operations只是候选，已收口为共享进程入口先行、原生文件IO保留；不以完整沙箱设计阻塞工具建设，不实现VM/OS隔离后端。原生Windows应用/PowerShell需求保留，工具完成后另做WIN1全项目兼容性复盘。

日期：2026-10-15。状态：只读调研与设计建议，未授权实现。来源为用户最初粘贴的方案、当前工具/权限/Shell设计，以及本轮核对的上游源码与官方文档。不因用户要求展望沙箱就安装运行时、启动VM、修改实际配置或提前实现沙箱。

## 1. 版本与证据

- Pi本机参考HEAD为a69bef789bc95abf0acee16f7b4660b70b650bb9。本轮通过git ls-remote和GitHub commits API核对上游main为5ab7fe74c400097b386662205cd93900695b8a71，提交时间2026-10-15T14:49:50Z；examples/extensions/gondolin/index.ts和sandbox/index.ts与本机文件逐字节一致。仅把公开源码复制到临时研究目录，没有更新参考仓库checkout。
- Gondolin上游main为0705083ebf7108aa00f5acc0b8ab37c1c145d365，提交时间2026-10-04T22:55:25Z。Pi本机示例package.json固定依赖@earendil-works/gondolin 0.12.0；Gondolin主线文档/SDK行为不直接冒充该固定依赖已运行验证。
- 核对入口：[Pi Gondolin示例](https://github.com/earendil-works/pi/blob/5ab7fe74c400097b386662205cd93900695b8a71/packages/coding-agent/examples/extensions/gondolin/index.ts)、[Pi OS沙箱示例](https://github.com/earendil-works/pi/blob/5ab7fe74c400097b386662205cd93900695b8a71/packages/coding-agent/examples/extensions/sandbox/index.ts)、[Pi安全说明](https://github.com/earendil-works/pi/blob/5ab7fe74c400097b386662205cd93900695b8a71/packages/coding-agent/docs/security.md)。没有运行Pi、Gondolin或实际隔离样本。

## 2. 之前标为后续的事项：影响判断

| 项目与原记录 | 是否影响当前工具结构 | 收口建议/边界 |
|---|---|---|
| Read超长单行的行内续读，第11节 | 若要求完整续读同一行，会影响参数接口；只返回前缀则不必 | 建议首版按已讨论方向返回有界前缀并明确该行剩余内容未返回，不声称仅凭行offset可续读；是否新增字节/字符游标仍需明确，不自动加入。 |
| Read图片格式、输入/缩放额度及处理依赖，第11—12节 | 影响图片分支、依赖和预算，不推翻统一多模态/协议适配链 | 总Spec前定支持格式、缩放和实际配置，不能把Pi默认或Pillow当作本项目已获批准；前端图片卡片仍后排。 |
| 符号链接，第18.4节 | 影响路径权限、读写目标、原子替换及未来VM挂载，需在实现前明确 | 不能只补Operations就跳过。当前不支持与永久权限Deny不同；若支持，要按真实目标授权并保留链接、提交前复核目标一致。是否启用仍待用户确定。 |
| Edit归一化缓冲、混合换行、diff/patch/预览，第18.4节 | 影响算法、额度和返回/投影，但不改变主循环 | 这些是总Spec必须写全的合同。预览未获读取许可时只展示参数，不能提前读原文；执行复核文件状态不重跑Auto。原文不设1MiB大小限制。 |
| 动态登记/活动集合与替换/移除，第5—7节 | 现有App共享定义＋会话活动集合方向可保留；首期是否replace/unregister须界定 | 不为假设的热替换加版本平台；已进入模型请求/审批的工具和执行位置不能中途换。自定义工具登记不自动获得沙箱隔离。 |
| Auto配置、固定HITL、旧错误/调度消费者 | 不需重选架构，但实施前必须落具体配置/签名与迁移清单 | 复用模型目录；旧Bash排除及人工grant门要同步迁移；交接仍列出的同步拒绝串行项问题须在实施中复现/修复，不能把调研当已修复。 |
| 运行中输出UI、工具卡、Shift+Tab、活动工具管理界面 | 属于已排后的前端工作 | 后端必要事件/返回及会话状态合同保留，不提前做UI，不让额外进度投递改变执行结果。 |
| 项目插件加载/工作区信任、跨重启审批、多用户、依赖DAG/后台任务 | 本轮不引入 | 可信代码注册、既有批次调度和宿主运行先按已确认范围实现；这些能力有独立授权/持久化/隔离需求。 |
| Operations与执行环境，第14/17/18/19节的早期“暂不加” | 现在有具体后续沙箱场景，需要重新讨论IO替换边界 | 建议明确窄的文件IO/进程IO接点，当前只接宿主适配；不提前实现VM/容器平台。本节是新建议，尚未替换成已批准接口。 |

## 3. 原方案的Operations具体是什么

用户粘贴稿说的是ReadOperations/BashOperations等可替换IO依赖，不是给模型调用加一个operation字段，也不是给每次调用再包一层审批对象。它改变工具如何取得文件/启动程序，不改变模型看到的read/path或bash/command。

Pi链：createReadToolDefinition(cwd,{operations})捕获ops→execute解析路径→ops.access/readFile→统一文字/图片/截断；Bash的ops.exec接command/cwd/onData/signal/timeout/env。默认ops使用本地文件/子进程；Gondolin注入vm.fs/vm.exec。Operations本身不授权、不隔离、不说明网络策略已生效。

示意（候选职责，不是当前API）：

```text
模型 read(path) / bash(command)
  → 本批工具视图、参数与权限准备
  → 整批审批/原串并行调度
  → 原工具的业务逻辑
      → 当前会话的文件IO或进程IO
          宿主适配：Python文件/子进程
          后续适配：VM文件接口/VM进程
  → 原有截断、归档、ToolException/ToolError
  → 同调用ID的ToolMessage
```

建议只提取真实接触外部资源的部分，保留工具原因识别与反馈、分页/匹配/diff等逻辑。文件能力须支持已有分块读取、元信息、目录枚举、临时写入/提交；进程能力覆盖Shell，也覆盖Grep的rg及Find的fd，不能文件换成VM后仍在宿主spawn搜索。Shell合并输出与rg/fd的结构化stdout/诊断stderr是各工具消费者合同，不因共用进程IO就强行合并所有输出。

不直接照抄Pi的整文件readFile/writeFile接口：它不足以证明本项目大文件Edit的有界内存、原子替换/变化检测合同仍成立。Pi Gondolin示例的Grep还有专门的guest遍历/整文件读取/正则实现，并非只换ops后原生rg行为自动不变。本项目已确认的原生rg/fd方向不能被这个示例悄悄替换；缺少相应工具或提交能力的后端须明确不支持，不能静默退化。

ToolSpec/注册表继续归App共享；具体环境/资源按会话和当前批次从已有ToolRuntime/context传递，不能把某个会话VM写进共享BaseTool可变字段，不复制所有工具。自定义工具只有需要替换IO时才使用这些依赖，不强制继承一个新BaseTool；可信自定义Python任意IO仍可能在宿主，不能仅凭登记宣称已经沙箱化。精确类名/方法签名和宿主消费者在总Spec按实际代码列出，不先建通用run(action_dict)或多层执行器。

## 4. Pi的两种沙箱示例不能混为一谈

### 4.1 OS沙箱示例

examples/extensions/sandbox/index.ts注入BashOperations→SandboxManager.wrapWithSandbox→启动包装命令；依赖@anthropic-ai/sandbox-runtime。示例只对darwin/linux初始化，read/write/edit仍是宿主工具。sandboxEnabled/Initialized为false时会执行localBash；初始化失败会通知错误，但后续工具仍有宿主路径。这是源码事实，不作为本项目沙箱必需模式的默认。

[Claude官方沙箱](https://code.claude.com/docs/en/sandboxing)当前也把本机Shell命令及其子进程放进OS边界：macOS Seatbelt、Linux/WSL2 bubblewrap；原生Windows不在该边界内，文件工具/MCP/hooks另行运行。其可配置降级与退出选项说明实际执行位置必须明确，不能把“产品支持沙箱”理解为所有入口均受隔离。

### 4.2 Gondolin微型VM示例

Pi示例的实际链：session_start/ensureVm→VM.create，RealFSProvider将宿主工作目录挂到guest /workspace→注册覆盖read/write/edit/ls/find/grep/bash；文件ops调用vm.fs，Bash ops调用vm.exec并流式回传；session_shutdown关闭VM。当前示例会寻找guest Bash，找不到回退/bin/sh，并以-lc执行；不能把该回退及登录Shell语义当作本项目已确认Bash合同。user_bash也有同一VM路由。

[Gondolin架构](https://earendil-works.github.io/gondolin/architecture/)：宿主TypeScript/Node控制库与CLI→QEMU（默认）或实验性libkrun→Linux guest；sandboxd负责创建进程，sandboxfs经FUSE/RPC把挂载文件访问交回宿主VFS provider，网络在宿主代理/策略层处理。它不是Python虚拟环境，也不是只加一个目录校验。

[Gondolin说明](https://github.com/earendil-works/gondolin/blob/0705083ebf7108aa00f5acc0b8ab37c1c145d365/README.md)列macOS/Linux支持且仍有实验性边界；不能承诺原生Windows宿主/Windows guest已覆盖。已核对的控制接口是TypeScript/Node，本项目若选用还需验证Python如何调用控制面，不能直接把TS import写进Python。此处不提前确定引入第二运行时。

Pi例子挂载的/workspace写入会回到宿主，VM销毁不自动回滚这些文件；主Agent/扩展代码仍在宿主。Bash传入env只由sanitizeEnv保留字符串值，不是移除秘密；不能把SDK可选的HTTP secret-placeholder能力说成该示例已自动保护所有继承环境。[安全设计](https://earendil-works.github.io/gondolin/security/)描述的网络/凭证保证依赖实际配置；[VFS provider](https://earendil-works.github.io/gondolin/vfs/)支持只读/影子层等组合，但示例的普通RealFS挂载不等于已应用全部策略。

## 5. 会影响现有设计的五个边界

1. **执行目标与路径命名空间。** 宿主src/a.py和guest /workspace/src/a.py需要当前后端解析及映射。权限准备保存的目标/工作区/命令必须与实际执行环境一致，不能先用宿主Path.resolve批准再去VM执行另一目标；等待审批时不换环境。提示词提供真实执行OS/cwd/可用解释器，不能仅用App所在OS。
2. **原生PowerShell与Linux guest。** 宿主Windows PowerShell5.1不能由Linux微型VM提供。若guest装pwsh，它仍是Linux PowerShell7；活动工具集合须按环境实际能力准备。宿主Bash/PowerShell支持不等于同一沙箱覆盖，不能在VM失败时自动换宿主执行。
3. **环境与依赖。** 已确认的默认继承用于当前宿主执行，不自动承诺把全部宿主PATH/HOME/凭证照搬进未来VM。环境准备应在目标适配边界完成；guest使用自己的解释器/环境，再显式配置测试变量。宿主macOS/Windows的.venv不能直接当Linux依赖，需guest安装/镜像准备。该后续配置不能反过来改变当前宿主继承合同。
4. **日志和保存。** 返回模型的日志路径需能被同会话Read访问；采用逻辑工作区路径并由后端映射，或明确的受管产物位置，不只返回guest不可见的宿主绝对路径。原始输出/UTF-8视图/额度及必要保存消费者必须一起核对；不先建外部对象存储。
5. **权限策略与隔离策略。** 工具Deny/Allow决定能否调用；内核/VFS/网络边界限制运行中能访问什么。tool=read的Deny不是天然的“所有进程禁止读此文件”，不能直接把所有工具规则编译成全局限制。可以复用规范化资源和配置来源，但适用范围/编译能力须明确；BypassApprovals不自动关闭已要求的隔离，沙箱可用也不自动跳过显式Deny。

## 6. 对原粘贴方案的两处修正

- “换Operations后hook/授权/审计都不需要改”过于绝对：循环/优先级/框架HITL可以保留，但执行位置、路径、能力、授权绑定、日志来源和事件展示仍有消费者需要同步。
- “沙箱不可用后退回逐条人工审批仍算fail-closed”不成立：审批宿主执行不等于保持原隔离边界。若本次要求沙箱而初始化失败，应不执行并报告；如以后支持用户明确改选宿主，要按新的执行位置重新审查，不静默重放可能已有副作用的命令。Pi示例的宿主回退和Claude可配置行为不直接成为我们的安全合同。

## 7. 后续沙箱路线建议，尚未作正式选型

本轮先确定可替换IO、目标/路径/能力/日志合同，实施时仅接宿主适配；不安装Gondolin、不编VM控制服务、不预建多后端注册平台。第一个沙箱切片只验收“在指定边界内执行一个真实pytest任务，并能停止和读回日志”，先选一种隔离方案验证，不同时建设Seatbelt/Bubblewrap/容器/VM全套。

候选取舍：若先解决本机macOS/Linux Shell命令约束，可优先验证OS沙箱；若目标是未知测试代码的独立Linux运行环境，优先比较容器或Gondolin微型VM，后者有需额外控制面/镜像的成本。当前证据不足以直接选定Gondolin，原生Windows PowerShell隔离也不能被Linux方案代替。具体选型须由目标工作负载、平台和实际探针决定，不把“微型”当性能或成熟度证明。

验证清单：正常pytest/读写/rg/fd、同一执行位置的日志回读、工作区外和声明禁止目标、符号链接绕过、网络允许/拒绝、宿主测试服务映射、超时/停止/沙箱启动失败。HTTP/RPC测试不能只确认能联网：guest localhost不是宿主测试服务，Gondolin网络不是通用NAT，内网/数据库/原生RPC通道要按实际协议验证。测试通过前不报告沙箱保证。

当前结论：已有ToolSpec/会话活动集合/统一ToolError/固定审批/批次调度仍可保留。新增需要用户确认的方向是窄IO替换接点及执行环境合同；符号链接等原未定行为在最终Spec前继续收口。所有实施、依赖及配置变更仍等待用户统一授权。

## 8. Windows原生要求与Codex复用调研

用户进一步确认：需要操作Windows电脑上的文件、运行本机程序及已有开发环境；沙箱内也要支持原生Windows程序/PowerShell。Linux容器/VM能映射代码，不等于能直接使用Windows解释器、依赖和程序，不能作为覆盖该要求的唯一方案。本节为源码事实与候选建议，尚未决定沙箱产品或授权实现。

### 8.1 版本与Windows调用链

2026-10-15通过GitHub API核对Codex main为f0c5077c34ec7a76b4ad9134835925e6ae3d72ec，提交时间2026-10-15T15:12:58Z。只将选定公开源码复制到临时研究目录；未安装/运行Codex CLI、未启动沙箱。微软MXC按该Codex工作区锁定的6cd3d58f05d3447e67109cfb75e042803b843ca4核对，不把另一版本README当同版执行证据。

- 官方当前列三种Windows实现：MXC为兼容设备上的新路线，elevated/unelevated为旧路线。MXC需要相应Windows 11能力，Codex实际通过创建/关闭PSEC探测，不仅检查版本号；拒绝路径还需对应原生能力。Windows 10或不支持的Windows 11不能宣称MXC覆盖。
- CLI链：cli/src/main.rs按宿主平台分派→debug_sandbox.rs::run_command_under_windows_sandbox→run_command_under_sandbox，加载权限配置/环境并选择后端。该入口不启动模型循环；会加载Codex自己的配置，不是一个无配置影响的裸spawn。
- MXC链：SandboxManager::transform→codex_mxc_sandbox::create_command_args，携带权限profile、policy cwd、网络上下文和exact argv→当前Codex可执行文件的内部helper→windows::run→policy::build_request→native::launch→Microsoft BaseContainerRunner::spawn/wait。helper清理启动传输环境，stdio继承外层管道。
- 微软runner使用PSEC/CreateProcessSecurityEnvironment及带安全环境属性的CreateProcessW；创建挂起进程、放入Job后恢复，用Job处理进程树生命周期。Codex该路径探测并要求PSEC，不进入微软SDK通用AppContainer分派，不创建用户、不修改宿主ACL、不请求提权。Job的进程清理与PSEC的访问限制是不同职责。
- elevated旧路线：准备/维护低权限沙箱用户、权限根/ACL、网络规则和运行辅助程序，受限token启动原生程序；首次准备需要管理员批准，实际命令不以管理员运行。unelevated使用当前用户派生的受限token，网络和拒绝读取能力更弱，不能视作同等替代。
- MXC源码明确记录：前台进程结束会清理剩余后代；托管网络要求允许宿主loopback双向访问，不支持client-only loopback限制。它们都需要用真实测试服务/PowerShell验证，不能因官方产品采用就宣称本项目已适配。

### 8.2 可复用入口与边界

| 入口 | 已核对的事实 | 本项目成本与候选判断 |
|---|---|---|
| codex sandbox公开CLI | 三平台都有独立命令入口，指定argv、cwd、权限profile；输出继承stdio并返回退出状态，不需模型循环 | 优先做后续可行性验证。依赖完整CLI及配套helper和固定版本；需控制配置来源/环境、策略翻译、实时输出/停止、错误分类，不能仅调用一次就算接入完成 |
| Codex内部Rust crates | 有codex-sandboxing、codex-mxc-sandbox及Windows旧实现；直接依赖codex-protocol、network-proxy、路径/PTY等工作区组件 | 不是Python可import库。可做独立Rust辅助程序，但要维护工作区依赖、三平台编译/打包/协议和更新；不能仅复制MXC的5个非测试源文件就认为独立化完成 |
| Microsoft MXC SDK/原生程序 | 有Rust、Node、.NET入口和原生执行程序，未发现官方Python绑定；三平台均有后端 | 可作为替代研究方向。该锁定版本README明确为早期预览，提示策略存在已知过宽行为、不应把所有MXC profile当安全边界；Codex特定的PSEC/策略转换不能被等同为SDK全部默认行为 |
| codex exec-server | 独立进程/文件RPC，有输出与退出通知，源码支持附带sandbox上下文；省略时可无沙箱 | 是另一个更重的候选，不把它误当只连上就自动隔离；本轮不引入长驻执行服务和其连接/认证/恢复协议 |

CLI也会处理自己的环境和默认保护目录：例如工作区写入profile可保护.git/.agents/.codex等。要验证能否通过显式细粒度策略表达本项目已授权的Skill修改，不能直接复制默认profile后悄悄扩大本项目Deny；不使用全盘放开来掩盖策略差异。独立CLI无模型调用不等于完全不读配置，包含托管配置时还可触发其配置加载路径。

### 8.3 macOS/Linux与复杂度判断

- macOS：同一CLI入口→Seatbelt策略生成→/usr/bin/sandbox-exec→原生程序及子进程。可保留宿主路径/解释器，但读写根、系统库/服务、符号链接及网络策略都需编译和验证。
- Linux：同一CLI入口→codex-linux-sandbox→bubblewrap文件系统视图/namespace→seccomp与no_new_privs→原生程序；当前不是只有Landlock。受内核/user namespace及容器宿主限制，不能保证所有Linux机器开箱可用。
- 复杂度是设计估计：接公开CLI为中等的集成任务；抽取Rust实现并发布自有helper为高；独立重写三平台隔离为很高。Windows旧路线的用户/ACL/防火墙/安装与回收、MXC版本能力差异、macOS策略与Linux namespace/seccomp都需维护。没有跨平台探针前不给精确工期，也不用源码文件数量冒充本项目实现量。
- 候选最小链：现有权限/整批审批→原调度→已批准的命令与资源范围→统一进程启动接点→Codex平台沙箱→解释器→输出/归档/统一ToolError。不把BypassApprovals解释成关闭沙箱，不把工具名限定的Deny直接编译成全局资源禁读，也不因后端不可用静默换宿主。
- 本机OS进程隔离优先支持的是进程接点；Bash/PowerShell及rg/fd应共用相同执行位置。Python主进程里的Read/Write/Edit不会因子进程沙箱而自动隔离；若目标只隔离命令，它们继续应用层权限。若要求文件工具同受OS边界约束，需另定隔离helper/worker及文件IO合同。不能仅凭“以后做沙箱”就提前确认完整FileOperations抽象。

后续验证建议（需另获实施授权）：固定版本/配置来源；Windows上原生powershell.exe、pwsh.exe和Git Bash；三平台真实项目测试、外部目录逐次授权、目录/文件链接、禁止目标、网络/本机测试服务、连续输出与归档、超时/停止及后代清理、启动失败不执行、Skill显式授权写入、受支持/不受支持策略的明确失败。当前只有静态证据，没有三平台运行或安全保证。

依据：[官方Windows沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)、[公开CLI](https://learn.chatgpt.com/docs/cli/reference)、[权限实施说明](https://learn.chatgpt.com/docs/permissions)；[固定版本CLI调用链](https://github.com/openai/codex/blob/f0c5077c34ec7a76b4ad9134835925e6ae3d72ec/codex-rs/cli/src/debug_sandbox.rs)、[MXC接法](https://github.com/openai/codex/blob/f0c5077c34ec7a76b4ad9134835925e6ae3d72ec/codex-rs/mxc-sandbox/README.md)、[微软MXC版本与限制](https://github.com/microsoft/mxc/blob/6cd3d58f05d3447e67109cfb75e042803b843ca4/README.md)、[微软原生runner](https://github.com/microsoft/mxc/blob/6cd3d58f05d3447e67109cfb75e042803b843ca4/src/backends/appcontainer/common/src/base_container_runner.rs)。

## 9. DeepSeek Harness的进程沙箱与文件工具边界

用户询问官方DeepSeek Harness。2026-10-15核对deepseek-ai/deepseek-harness master为86a402200d95fecef4529d969836f1fe13c6cb0d（2026-10-15T12:22:44Z），只读公开源码；相关包声明版本0.2.1-alpha.4，不等同已验证npm发布产物。未安装依赖、运行DSH、修改本机ACL或启动进程沙箱。

- 目标是共享宿主内核/文件系统的进程限制。read-only/workspace-write/danger-full-access是文件效果模式，后者绕过沙箱；网络和进程可见性不属于这套模式的承诺。它们不能直接对应本项目Default/NoAsk/Auto/BypassApprovals审批模式。
- 实际链：SandboxPwshExecutor.resolve/execute取得本次SandboxExecutionPolicy→ctx.sandboxPolicy按会话/显式授权模式解析workspaceRoot→ctx.sandbox.confine→LocalSandboxProvider规范化根并选择runner→返回替换argv、enforcement和诊断分类规则→executeArgv/子进程提供方启动→装饰输出与退出结果。confine本身不是另一套模型循环，也不直接运行所有工具。
- Linux链优先探测bwrap，失败再探测包内分发的landlock-run；macOS用Seatbelt；Windows用windows-acl，单候选按执行时故障报告而非先探测。不能概括为所有平台都先执行探针。无可用运行器或准备失败则SANDBOX_UNAVAILABLE，不静默变成无约束执行。
- Linux bwrap profile将/只读映射，workspace-write再绑定可写工作区并创建/tmp tmpfs；还有PID namespace配置，但模式不承诺一般进程或网络隔离。Landlock profile允许读取/、允许写/dev/null，workspace-write再开放工作区和/tmp，旧ABI可报告partial。macOS策略allow default后deny file-write*，再开放/dev/null及可写根；同样没有把网络访问收紧。
- Windows通过Node/koffi绑定Win32 API：AclSandbox.init创建WRITE_RESTRICTED受限token并降低到Low完整性；工作区/私有临时目录获得各自能力SID的允许ACE、父目录删除权限的限制ACE及Low标签；runner以该token启动原生程序，配合Job控制进程树，不新建专用用户。既有用户的读取能力保留，写权限受额外检查与完整性限制。PowerShell/Bash均可消费此后端。
- Windows明确报告partial：硬链接共享文件对象和安全描述符，工作区内链接被授予权限可能影响外部别名；另有带其他AppContainer包SID的对象不可读等兼容缺口。读取/联网不受这套写限制拦截，因此不能单独实现本项目若要求的任意Shell子进程全局禁读/禁网。
- 宿主副作用真实存在：工作区ACE、删除限制及Low标签作为复用缓存长期保留；私有temp按会话/工作区随机分配独立SID并尝试回收。Low标签保留可改变同用户其他Low完整性程序对该树的访问。大目录首次继承传播可昂贵；不能说工具退出就恢复全部权限。
- PowerShell有功能差异：read-only下无法完成临时目录启动探测时可能进入ConstrainedLanguage；workspace-write的标准私有temp路径通常保留FullLanguage，宿主策略仍可限制。不能把“可启动PowerShell”当所有测试代码行为相同。
- 文件工具另用SandboxedFileSystem扩展LocalFileSystem：writeText/editText前按同一调用策略重新解析真实目标并检查包含关系，复用本地原子写/匹配；读取不受此层限制。这是可信工具代码的应用边界，源码明确接受缩小后的路径竞态，不冒充内核隔离。
- 包有公开AclSandbox API和runner入口，可供Harness外Node程序使用；本项目Python仍需辅助进程接入或移植，不必迁移Cordis/Agent循环。移植也必须保留ACL/标签/资源回收/架构ABI与partial边界，不能只抄CreateRestrictedToken调用。
- DSH历史决策对早期MXC的否决理由属于其调研版本；不能把它直接用来推翻第8节核对的Codex新PSEC实现。当前可借鉴的是窄进程启动接点、每调用策略及真实报告后端能力，未决定采用该Windows后端。

依据：[子系统合同](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/docs/subsystems/sandbox.md)、[平台profile源码](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/sandbox/sandbox-local/src/profiles.ts)、[Windows公开API和限制](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/sandbox/sandbox-windows-acl/README.md)、[Windows启动器](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/sandbox/sandbox-windows-acl/src/runner.ts)、[PowerShell接线](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/shell/pwsh-sandbox/src/index.ts)、[文件工具检查](https://github.com/deepseek-ai/deepseek-harness/blob/86a402200d95fecef4529d969836f1fe13c6cb0d/packages/fs/fs-sandbox/src/index.ts)。

## 10. 倾向本机进程隔离后，对现有工具设计的影响

用户目前倾向Claude/DeepSeek式的本机进程隔离；这是执行形态偏好，尚未决定具体后端、读/写/网络限制范围，未授权实现。保留原生Windows/PowerShell需求；Claude当前原生Windows无同等沙箱，DSH Windows为partial写约束，不能因采用同一路线就宣称三平台同等能力。

- **进程入口收口，有当前消费者。** Bash当前_run_host_command直接Popen，Grep的_search_file/_validate_regex及Find的_discover_paths直接subprocess.run；将已计划的异步进程核心扩展为共享启动接点，供双Shell和rg/fd使用。保留argv、cwd、env、总截止时间/停止、stdout/stderr消费方式及本次已确定的沙箱资源策略。进程适配可先宿主、后接OS后端，不新建无消费者的后端平台；校验用的外部程序也不能遗漏。
- **文件工具不自动移入隔离进程。** Read/Write/Edit/Ls继续可信Python实现和共享权限管线；图片、分页、分块匹配/原子提交、diff及目录展示保留。真实路径/符号链接与外部路径授权仍需收口，但不是进程沙箱带来的新算法。当前无远程文件命名空间需求，不因沙箱偏好就建立完整FileOperations接口。若要求文件工具本身也受内核边界，另定worker/helper范围。
- **调用许可与执行边界交接。** 工具Deny/Allow/模式决定可否调用，OS策略决定运行中可访问资源；在原有本批已保存准备结果中携带确定的执行位置/工作区/资源范围，执行消费该结果，不再跑一次Auto，也不让审批期间切换执行环境。显式Allow、人工批准和BypassApprovals不自动关闭沙箱。
- **外部目录扩权是需补定的合同。** command/timeout可继续服务固定边界执行；若要由模型提出本次额外路径授权，需显式申请入口并让原审批管线审查，不能在不加接口/不变配置的情况下声称已支持，也不从任意Shell字符串或脚本推断所有IO。候选倾向本次范围扩权，具体字段、是否支持本次无隔离执行均未决定；命令精确Allow与文件资源Allow不能混用。
- **读/网络限制不能从工具规则自动推导。** tool=read的Deny不天然成为所有子进程禁读；隐藏/隐私默认Ask也不天然成为脚本逐文件暂停点。需要先确定哪些是全局资源限制，再验证后端能执行。DSH写约束不能冒充禁读/禁网；HTTP/RPC及localhost测试服务、依赖下载/缓存访问需按实际能力定边界，当前不预设网络拒绝或新增配置默认值。
- **收尾与结果分层。** 已定新Shell/无TTY、输出合并2000行/64KiB、归档/Read回读和额度保留。停止对象覆盖运行器、实际Shell/子进程及后端临时资源，不能只等待包装进程退出；后端清理语义另实测。准备/运行器故障与普通命令失败要能区分，不仅凭合并日志中的Permission denied等文本断言原因。普通受限操作反馈具体ToolException，配置/初始化/真正机制故障及控制信号保留各自层职责；不静默退回宿主或自动重试有副作用命令。
- **环境与日志。** 继承父环境＋显式覆盖/移除继续作为输入合同。后端如必须使用私有TEMP/TMP或启动传输变量，应由适配层显式管理/说明，不恢复业务环境白名单。归档由受信宿主采集器保存，日志无需为采集另开放子进程写权限；本机路径仍可由Read访问，不引入远程日志映射。不同后端的额外环境变更/ACL副作用不能被隐藏。
- **装配与自定义工具。** 启动前校验可判定的后端/平台/配置问题；ToolSpec、共享注册表、会话活动集合及原调度不变。可信自定义Python直接文件/网络IO不因注册而受子进程沙箱约束；需要隔离的外部命令使用同一进程入口，不强制新增BaseTool继承体系。

正常示意：bash执行工作区pytest→本批准备/需要则整批审批→原串行调度→共享进程入口将同一命令置于OS边界→输出/归档/结果→同调用ID ToolMessage。失败示意：pytest试写未允许的外部缓存目录→OS拒绝该操作→按实际进程结果反馈；模型可改用允许的临时目录，若需扩大范围则另提出明确申请，不能自动去掉隔离重跑。沙箱未启动成功的情况单独处理，不把它报告为测试业务失败。

当前建议仅收口进程接点与权限交接，不推翻工具功能或主循环。下一讨论点是进程沙箱应承诺哪些资源限制，再确定外部范围申请与后端接口；不因用户倾向某条路线提前将候选API写入真实配置。
