# Shell工具设计：输出、运行设置、子进程与跨平台

> 2026-10-16版本边界：双Shell已按[085-14](../specs/085-14-dual-shell.md)实施；缺解释器时保留定义并在实际调用时报错，版本以固定有界探测取得，下文候选“未公布不可用工具”等旧表述不再是当前行为。Windows实机仍待15/WIN1。

> 2026-10-15整合：函数/文件/完整收尾合同以[Spec085](../specs/085-tool-system-rebuild.md)及[配置样例](../examples/tool-runtime.example.toml)为准。本稿保留11—12节候选和探针；共享进程入口纳入本次，实际沙箱后做。总Spec中的少量默认环境移除、100ms/5s等属于工程默认建议；PowerShell默认策略建议改为inherit，可显式选择进程级bypass，不能把旧候选默认Bypass视为已确认。无产品实现。

日期：2026-10-15。状态：设计讨论，未实现。延续[工具设计第19节](tool-registration-design-2026-10-15.md#19-bash设计起点现状pi与候选主链待讨论)。用户确认Windows同时支持Bash和原生PowerShell、2000行/64KiB尾部、各工具额度按工具分组并注释的配置方案、每次调用新建Shell，以及默认继承父进程环境并支持显式覆盖/移除；其他部分沿已讨论建议方向推进。撤回“平台基础项＋业务变量白名单”的默认候选。已定方向、仍需讨论的行为和实施验证分别见第10节，不能将文档认可当作产品已接线。

## 1. 双Shell与参考边界

- 建议保留bash并新增powershell两个模型工具，均为command/timeout，各自固定语言，共用子进程生命周期、输出、归档和错误反馈。复用ToolSpec、会话活动集合、权限/HITL与sequential，不新增模型可随意改解释器的shell参数，不让同一bash工具静默改用PowerShell执行。
- Windows可同时注册已安装的Git Bash与PowerShell；PowerShell优先pwsh 7+，回退系统powershell.exe 5.1并明确实际版本。macOS/Linux默认Bash，若显式启用且已安装pwsh也可使用PowerShell；不自动安装。明确启用但解释器不可用时启动报错，不把不可用工具公布给模型。工具提示提供真实OS、Shell类型/版本、会话cwd及必要路径习惯，不输出完整环境。
- Pi a69bef789的默认Bash通过Git Bash等适配Windows，但未据本轮源码声称它有独立原生PowerShell工具。Claude Code当前官方工具表有Bash和PowerShell两个独立名称；Windows有Git Bash时可共存，无Git Bash时可用PowerShell，原生PowerShell优先7+再回退5.1；默认启用情况与平台/账号渠道有关。[Claude工具](https://code.claude.com/docs/en/tools-reference#powershell-tool)、[Windows安装](https://code.claude.com/docs/en/setup#install-on-native-windows)、[环境开关](https://code.claude.com/docs/en/env-vars)。这是官方文档证据，没有检查用户安装的Claude版本或其闭源内部实现。
- 双Shell权限必须按真实工具与语言检查，不能把Bash字符串规则自动视为PowerShell等价规则。Claude对Bash Deny与PowerShell另有联动关闭规则，不直接照搬为本项目合同。两者默认Ask并接共享Deny/Allow/四模式的方向沿用；首版命令匹配和跨Shell范围仍需第10节明确。

## 2. stdout/stderr组织与尾部

- 上轮“保留双流”候选已被后续合并方向替代：当前模型执行/排查测试日志场景默认合并，启动时stdout=PIPE、stderr=STDOUT，读一个管道，模型显示及归档都用这条流。合并会失去原始stdout/stderr标签；得到的是同一管道接收到的顺序，不能逆转程序自身缓冲或承诺业务事件的绝对时间顺序。
- 独立保留两流适合stdout作为JSON数据、stderr作为诊断的消费者，但重建交错顺序需要额外机制。本轮没有该结构化管道消费者，不同时建设双流+合并第三套日志。Pi是两管道onData回调汇入同一OutputAccumulator；这里建议直接OS层合并，行为目标相近，不能说两者实现相同。
- 用户确认尾部正文与本项目Read对齐：最多2000行且64KiB UTF-8文本，任一先到即截断。这里覆盖上轮沿Pi的50KiB候选；Pi本身仍为2000行/50KiB，不能误记为Pi也用64KiB。额度作为运行设置而非模型每次参数；消息头/状态/路径另有小而有限的开销，不能把正文额度声称整个消息严格64KiB。
- 优先保留末尾完整行，字节边界不拆UTF-8；若最后单行本身超限，返回该行末尾可容纳部分，明确“第N行尾部片段”。记录输出总行数、总文本字节及实际返回行范围/部分行；提示区分行数/字节原因，不能只说truncated=true，也不能在采集不完整时捏造完整总量。
- 总量统计针对解码并用于展示/回读的文本，不混用原始字节数。CRLF按一行处理；单独CR进度覆盖和ANSI控制序列需转为可读表示，不让Read因控制字节拒绝文本回读，具体转换在Spec固定。命令非零、超时、停止沿同样的尾部/归档表示，执行状态独立说明。

## 3. 保存与Read回读

- 保留工作区naiyue-agent-output下的随机文件、所有权确认和手动清理；短有效文本已完整进入工具消息时不另落盘。首次超过返回预算时将已缓冲前缀及后续内容流式落盘，不能只保存截断后的尾部；若短输出发生解码替换、需要保留原始诊断，也可触发归档并说明。第一版不加TTL、外部对象存储或独立日志工具。
- 建议保留原始字节归档；普通有效UTF-8且无需文本适配时一份日志即可。非UTF-8或含Read不能接受的控制字节时，额外提供增量转码/转义后的UTF-8文本视图给Read。视图是可读表示，不冒称原始字节；发生解码替换须明确标记有损。raw文件保存了什么与文本视图是否完整分别说明。这比只存UTF-8多一个条件视图，但服务于当前Read仅UTF-8和Windows编码差异两个真实消费者约束，不建设通用文件转换平台。
- 仍建议单次实际归档总量16MiB、工作区128MiB，原始文件和条件视图均计实际落盘字节；这些是有界默认，不保证每条命令都有完整日志。配额/目录/磁盘失败后继续读管道、保留最新尾部，停止增加归档；说明保存的是前部片段、尾部在消息中、中间可能缺失，不标Full output。
- 模型结果给Read可用的文本路径、完整性、保存/返回范围及必要offset示例；offset为1起始。返回文件路径不自动附加全文或再次调用模型。Read继续走已有权限管线，日志默认是工作区普通文件；显式Deny仍生效，不能用“工具生成”绕过。清理后文件不存在按普通Read错误反馈，checkpoint路径引用不是日志永久存储。
- 旧stdout/stderr命名和所有权标记有真实已保存文件消费者，实施须同步新旧文件的回读/清理识别，不擅自删除旧日志。Windows文件需先关闭再清理；不能把POSIX的0700/0600及st_mode检查原样当作Windows权限保证。

## 4. “可信配置”的具体含义

改称“应用运行设置”以免误解：指由用户/应用启动者提供、App读取并校验后交给工具的设置，区别于模型提出的command/timeout。它不是额外安全类、不是每次审批，也不保证用户写的配置天然正确；非法配置启动时报错，权限Deny/工具能力限制仍照常生效。

需要表达的设置包括启用哪些Shell、解释器路径/版本偏好、默认/最大超时、尾部行/字节预算、归档额度、PATH附加项、显式环境覆盖/移除、输出编码。SDK通过装配参数传入；CLI/Web复用同一个加载/校验入口。所有工具的相关额度统一按工具分组、逐键注释，文件见[tool-runtime.example.toml](../examples/tool-runtime.example.toml)及第9节。这是文档目录内的可解析设计样例，尚无产品加载器；不修改现有模型专用JSON，不要求每个工具各有一个配置文件，不新增model传env/cwd权限。

来源：应用默认值+用户明确选择的运行设置。普通项目文件、AGENTS.md/Skill或模型产物不自动改解释器/环境；运行设置首版启动时固定，修改后重启App生效，不在每条命令偷偷重读，也不新增显式热重载入口。模型仍仅提出command和受上限约束的timeout。

## 5. 环境与一次一进程

- 每次工具调用启动一个非交互Shell，以当前会话工作区为cwd；一次command内可包含多条命令、cd和环境变量修改。下一次调用重新从会话cwd及父进程继承环境加显式设置开始，前次cd/export/alias不保留；已经写入的文件/已发生外部效果保留。不是交互终端，不支持等待键盘回答，不把命令中的&当作受管理后台task_id。
- 不默认加载Bash rc/profile或PowerShell profile的方向保留。Bash非交互仍可能读取BASH_ENV，本机探针已确认--noprofile/--norc不阻止它；继承环境后这项与启动合同的协调仍须明确，不能借此恢复全面业务白名单。PowerShell用NoProfile/NonInteractive等固定启动语义，命令传输考虑EncodedCommand的复杂引号/Unicode支持，界面和权限始终审查原始命令；传输编码不等于安全过滤，不修改机器执行策略。
- 用户已确认默认继承父进程环境，支持显式覆盖和移除，不默认使用pass_env白名单、不新增严格环境模式。环境取自实际启动Agent服务的进程，不等于任意终端窗口，也不自动读取项目.env。继承PATH后按显式目录增补，正确使用os.pathsep和Windows大小写规则；不再额外把Agent自身venv插到项目解释器前。若启动环境本来已激活Agent的venv，继承PATH仍可能让该venv优先，需要用户明确选择项目解释器或配置PATH，不能承诺自动恢复激活前的PATH。
- HOME/USERPROFILE、TEMP/TMP/TMPDIR及locale正常继承或显式覆盖，不再改成工作区或硬编码en_US.UTF-8。环境已有的凭证也会继承，不能继续宣称“模型API凭证必不下传”；当前模型配置对象本身不会因环境继承自动传给Shell。业务变量无须逐个登记；不在普通日志或提示词打印环境值。

## 6. 平台与Shell分别适配

| 职责 | macOS/Linux | Windows |
|---|---|---|
| Bash | 明确配置或发现bash，不因系统默认zsh就改变工具语言 | 发现已安装Git Bash，路径/版本写入工具事实，不默认跨到WSL |
| PowerShell | 显式启用且安装pwsh 7+时可用 | pwsh优先，powershell.exe 5.1回退，准确标识版本 |
| 启动/工作目录 | 独立会话/进程组，系统路径传cwd | 原生子进程/进程组标志，Windows路径传cwd；不同Shell的路径表示分别说明 |
| 停止 | 终止受管理进程组并等待收尾 | 沿Pi方向评估taskkill /PID /T /F并等待结果/进程收尾，不能只杀Shell父进程或照搬killpg |
| 异步 | 原生asyncio子进程 | 必须在支持子进程的事件循环上运行，标准Proactor支持，Selector不支持；Web/CLI实际入口都需验证 |
| 环境/目录 | PATH分隔符和系统locale；POSIX权限 | 保留SystemRoot等必要项、Path大小写/分号、用户/临时目录；权限不能照搬POSIX模式位 |

PowerShell 5.1/7的编码和语法不完全相同；使用适配的文本输出/编码初始化，外部原生命令仍可能输出其他编码，允许声明编码、不自动猜测。PowerShell有多条逻辑输出流，第一版采集命令行呈现的文本，不承诺返回.NET对象。退出码是Shell进程退出码，不代表脚本每一步；不凭stderr非空断言失败，也不静默重写ErrorActionPreference。PowerShell内部非终止错误、原生命令退出码保留和UTF-8初始化需用5.1/7真实样本确定，不能只改可执行文件名就宣称等价。

跨平台保证目标是工具启动、权限配对、输出和停止合同一致，不保证同一段脚本及工具参数在所有系统通用。提示词提供OS/Shell事实，模型据此生成命令；不写自动把Bash转换为PowerShell的翻译器，不在运行失败后偷偷换Shell重放。

## 7. 依据与验证边界

- [Python subprocess](https://docs.python.org/3.12/library/subprocess.html)：stderr=STDOUT、env替换与Windows SystemRoot；[asyncio平台](https://docs.python.org/3.12/library/asyncio-platforms.html)：Windows子进程事件循环支持；[taskkill](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)：/T进程树与/F。
- [PowerShell7启动](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh?view=powershell-7.5)、[5.1启动](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)、[编码](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5)、[逻辑流](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_redirection?view=powershell-7.5)；[MSYS2路径](https://www.msys2.org/docs/filesystem-paths/)。
- 本机macOS临时子进程探针：依次write stdout A、stderr E、stdout B，分开捕获为A/B与E，合并管道为A/E/B；这是固定样本，不证明程序所有业务事件的时间顺序。BASH_ENV临时脚本在--noprofile/--norc下仍执行；第二次Shell调用不继承前次cd/export，但前次写入文件仍存在。
- 本机有Bash、无pwsh，没有Windows/PowerShell实机或CI证据。本轮没有重跑上轮37项产品测试，不把文档/标准库探针称为跨平台完成。后续需三系统真实进程样本，覆盖中文/空格路径、合并长输出、超长行、非UTF-8、5.1/7退出码、超时/停止子树、归档配额/清理以及实际Web事件循环，Mock只做补充。未改产品/测试/配置/依赖。

## 8. 新Shell进程与环境的依据复核

本轮重新核对Pi a69bef789的createLocalBashOperations.exec→spawn，以及getShellEnv。每次exec都创建子进程，cwd由调用方传入；getShellEnv展开process.env并补自己的bin目录到PATH，不使用本项目候选的基础环境选择策略。源码位置：[bash.ts](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/core/tools/bash.ts:90)、[shell.ts](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/utils/shell.ts:122)。本轮为源码核验，没有运行Pi服务。

[Claude Code官方工具文档](https://code.claude.com/docs/en/tools-reference#bash-tool-behavior)明确每条Bash命令用独立进程，前次export不延续；主会话可以另行延续授权目录内的cd，并在会话启动时读取用户启动文件、把alias/function/options应用到后续命令。持久化环境另有CLAUDE_ENV_FILE/Hook机制。因此“新进程”不等于“产品绝对不能持有cwd/环境”，也不能将我们的固定cwd、默认不加载profile表述为与Claude完全一致。[环境文档](https://code.claude.com/docs/en/env-vars)及[设置文档](https://code.claude.com/docs/en/settings)证明环境变量可配置，不证明Claude采用只选基础变量的环境默认。

AgentX的WebTerminalService.createTerminalSession→TerminalSession.start→Docker execCreateCmd/execStartCmd则持有TTY、stdin与WebSocket连接，是交互终端消费者。此链不能当作模型Bash工具的执行合同，也不据此引入本项目尚无需求的常驻Shell。

结论：Pi源码和Claude官方文档都支持“一次工具调用创建一个新Shell进程”作为成熟产品采用的方案；没有总体统计，不声称行业多数或唯一正确方式。用户确认本项目固定会话cwd，一条command内可cd、激活环境、多步执行，文件效果延续。收益是调用不依赖上次残留的export/目录，适合当前批次审批与停止管理；代价是每次启动成本，跨调用的目录/激活需要写清楚。暂不增加持久cwd捕获、环境快照、终端会话或后台任务平台。

环境依据复核后的纠正：[Python subprocess文档](https://docs.python.org/3.12/library/subprocess.html)支持默认继承和env映射替换；Pi继承父环境。此前“App持有模型密钥”不足以证明需要默认白名单：当前load_model_catalog从JSON读取provider.api_key，再通过配置对象传给模型客户端，不会自动放入环境。Web启动确有NAIYUE_WEB_TOKEN环境入口，若该变量存在则完整继承会下传；这是代码路径，不是读取或验证实际凭证值。严格筛选能减少无关凭证下传和宿主变量干扰，但不是磁盘/网络隔离；它会增加测试地址、代理、证书和项目变量的重复登记，当前本地测试目标下默认继承更合适。用户已确认此调整。

当前环境合同与样例：创建子进程时取父进程环境副本→应用environment.values的字符串覆盖/新增→按系统规则合成path_prepend→移除unset_env列出的名称→交给子进程。操作仅影响子进程，不修改共享os.environ或其他会话。Windows名称大小写不敏感，POSIX区分；values与unset_env冲突、PATH增补与移除PATH冲突，均在准备阶段报配置错误；移除本来不存在的变量不报错。没有覆盖/移除/目录增补时沿默认继承，不自动猜测敏感名称或逐个登记业务变量。模型仍不能传任意env；配置文件只写非敏感固定值，已有测试凭证可继承，可信应用也可注入。BASH_ENV及App私有变量的少量默认处理尚须明确，不在空unset_env样例背后暗加一套过滤。

## 9. 各工具额度与带注释的配置草案

用户已确认按工具分组的一份工具运行TOML方案，不只把Bash的几项常量搬出去。采用[标准注释语法](https://toml.io/en/v1.0.0#comment)，本项目Python3.12可用标准库tomllib读取；现有模型JSON不迁移、不为注释引入额外解析依赖。完整样例放入docs/examples而非实际运行配置目录，仍不是当前已接线的公开API；本轮同步去除旧环境白名单字段，改为显式覆盖/移除。

| 工具 | 保留/本轮确认的默认与上限 | 拟用的主要键 |
|---|---|---|
| read | 默认2000行、最大2000行、64KiB文本 | default_limit、max_limit、max_output_bytes |
| grep | 默认100/最大1000个匹配、前后默认0/最大各10行、单条文本2000字符、50KiB正文、20秒整次期限 | default_limit、max_limit、default_context_lines、max_context_lines、max_line_chars、max_output_bytes、timeout_seconds |
| find | 默认1000/最大2000条、50KiB正文、15秒整次期限 | default_limit、max_limit、max_output_bytes、timeout_seconds |
| ls | 默认500/最大2000条、50KiB正文 | default_limit、max_limit、max_output_bytes |
| write | content的UTF-8输入默认上限1MiB | max_input_bytes |
| edit | 全部oldText/newText的UTF-8输入总和默认上限1MiB | max_input_bytes，与write分开配置 |
| bash/powershell | 默认60/最大300秒、2000行/64KiB尾部、每调用16MiB实际归档、command最多8192字符 | default_timeout_seconds、max_timeout_seconds、max_output_lines、max_output_bytes、max_archive_bytes、max_command_chars |
| 共享日志存储 | 每工作区128MiB，跨Shell/会话共用，包含受管既有归档 | tool_output.max_workspace_archive_bytes |

此外将已有path/pattern/glob的4096字符参数上限按所属工具表达为max_path_chars、max_pattern_chars、max_glob_chars；允许应用配置调整不保证操作系统也支持任意长路径/命令。64KiB按65536字节，50KiB按51200字节；不将不同预算混写成一个含糊max_size，也不把匹配数/条目数叫行数。用户本轮对齐的是Shell与Read的64KiB，Grep/Find/Ls暂保留原50KiB，未擅自全工具改值。

不复活已确认移除的Grep候选文件2000/单文件8MiB/扫描总量32MiB，以及Find候选发现8MiB上限。Edit内部64KiB分块、管道读取块和收尾等待属于实现参数，不因数值也是64KiB就变成用户可处理文件的上限。图片的输入/缩放预算、模糊匹配缓冲和diff/patch/预览额度尚未定数值，须在相关分支落Spec时一起配置并注释；不填假额度、不把大文件原文限制为1MiB。

加载/生效拟沿既有装配边界：读取显式运行设置→结合本次要登记的工具检查名称及键、类型/正数/默认≤上限等约束→准备各工具参数模型、描述和执行依赖→登记真实ToolSpec→创建Agent。额度、输入schema和执行代码必须消费同一份准备结果，不能配置放宽后仍被旧Field(le=2000)拒绝，或描述仍硬写300秒。配置失败结束装配，模型调用本身超出已公布参数范围仍走既定参数错误反馈；触及输出/归档预算则返回明确的不完整信息。修改运行设置首版重启App生效，不跟随每条命令热读；这与会话活动工具/权限模式下一批切换不是同一种配置生命周期。

动态登记自定义工具仍由可信应用代码提供和校验所需参数后构造工具，再登记ToolSpec；同类业务额度可放tools.<工具名>，具体键由该工具声明/校验。加载设置不会自动import配置字符串指定的模块；不为统一内置额度写法强制新增ToolSpec配置框架。运行中登记同样先准备、校验再发布，失败不改旧集合；默认输入/权限/执行/错误合同不变。现有NAIYUE_WRITE_MAX_BYTES等内部额度入口在实施时迁移到此准备链并清理消费者，不平行保留两套来源制造不清楚的优先级；当前不改实际配置。

本轮验收边界：只检查样例可被本机Python3.12的tomllib解析、默认/上限关系、逐键注释、旧白名单字段移除和文档差异。没有运行新工具实现、重跑产品测试或验证Windows。配置及环境主体已确认，不列回未决项；Shell剩余讨论见下一节，图片/Edit额度另归各自工具，不混进Bash清单。

## 10. Bash一节收口清单：已定方向、待讨论行为与验证

### 10.1 已确定，不再重复开题

- Windows双Shell；每个工具固定语言、一次调用新进程、会话工作区cwd；不建设交互/持久Shell/后台任务平台，不在失败后换解释器重放。
- 共享Deny/Allow/四模式、默认Ask、整批审批后再执行、保留sequential；不能继续要求所有放行结果都另带人工批准grant。
- 命令条件首版只支持完整command精确匹配，其余交既定模式；不新增命令前缀/glob/正则、拆分复合命令或语义等价分析。同意`pytest -q`不覆盖改参或`pytest -q; 其他命令`，Bash和PowerShell按真实工具名分别匹配；会话“不再询问”沿同一精确命令及当前工作区范围，不扩为永久全局允许。
- 合并stdout/stderr、2000行/64KiB尾部、按需归档/Read回读、原始字节与必要UTF-8视图、单调用16MiB及工作区共享128MiB默认，额度可配置并逐键注释。
- 默认继承父环境，显式覆盖/移除；不要求pass_env、不按TOKEN/KEY猜测过滤、不人为伪造HOME或硬编码locale，配置先校验再创建Agent。
- 第19.1节的异常/控制方向沿已认可建议推进：非零/已收尾超时/采集失败转具体反馈；归档失败可降级；用户停止结束本轮；真正取消与无法确认受管进程收尾仍上抛。无自动重试、无通用副作用核对平台。

### 10.2 仍需讨论的行为，按建议顺序

| 顺序 | 未明确的合同 | 需要用具体例子决定的内容 |
|---|---|---|
| 1 | 命令级Allow/Deny | 用户已确认完整命令精确匹配，其余交模式；已移入10.1，不再列为未决方案。精确匹配不保证脚本内部IO范围或禁止同一效果的所有表达方式。 |
| 2 | 子进程/管道结束与停止收尾 | Shell父进程退出0但后台子进程仍持有stdout，何时算一次调用结束、排空等待多长、是否继续终止受管子进程、确实收尾失败怎样传播和呈现？不支持后台任务管理已确定，但不等于这些情况自动不存在。 |
| 3 | PowerShell错误与退出码 | 非终止Write-Error后继续成功输出、原生命令返回非零、最后一条命令成功/显式exit不同组合怎样形成最终反馈？已有“不凭stderr非空断言失败、不静默改ErrorActionPreference”方向，不再重开，须明确适配器实际保留什么。 |
| 4 | 编码与文本显示的确切规则 | PowerShell5.1/7文本初始化及外部程序编码怎样配合output_encoding；解码替换、ANSI和单独CR进度如何变为Read可回读文本，显示字节/行号按何种表示计数。原始日志/必要文本视图方向不变。 |

环境主体不再开题；只剩一项小的启动合同需要随第2/4项收口：不默认加载profile的前提下，BASH_ENV等隐式启动项怎么处理，以及NAIYUE_WEB_TOKEN这类App私有变量是否给默认移除项、能否被显式覆盖。两者不能被一句“继承父环境”或“全部敏感变量过滤”含糊带过。当前样例unset_env为空，没有偷偷批准默认过滤清单。

### 10.3 交给总Spec和验证，不逐项要求用户重新决定

- 接一个原生异步子进程核心，现有真实同步消费者走同一核心的薄适配；具体类/方法/模块和调用方在总Spec列清，不保留两套进程算法，也不新建无消费者的执行后端平台。
- 把已存本批权限结果交给两Shell，恢复不重跑Auto；清理旧人工grant硬门。第19.1方向纳入最终总Spec时同步Spec084旧“Bash暂排除”范围、handle_tool_error、应用/CLI/观察/错误分类及旧会话门禁消费者，不把旧文档候选留成并行合同。
- 归档结构/所有权/手动清理需要识别已有受管文件；新旧日志实际占用及并发写入共用工作区额度。跨进程计数和清理竞争是验证项，不能仅靠同进程Mock声称配额已可靠实施。
- 临时管道、启动后准备失败、输出任务异常、归档降级、必要保存故障及控制配对均须接线和故障验证；不能用“最终消息能返回”替代资源收尾及原异常合同。
- macOS/Linux/Windows真实样本覆盖解释器发现、中文/空格路径、长输出/超长行、编码、5.1/7退出码、超时/停止、配额/清理及Web事件循环。当前没有Windows/PowerShell实机证据，写配置不等于完成适配。

第1项已由用户确认；第2—4项及隐式启动项的本轮建议见第11节。下节仅新增候选和依据，没有将用户对第1项的认可扩大成对其他候选的批准，配置样例暂不加入未确认的新默认。

## 11. 第2—4项及启动项：参考复核后的候选建议

### 11.1 进程退出、输出与收尾

Pi源码事实：createLocalBashOperations.exec→waitForChildProcess；后者区分exit与close/end。父进程退出后等EOF，或等输出静默100ms；每次新数据重置静默计时，达到静默边界会destroy读取流并按父退出码返回，不证明所有后台后代退出。timeout/abort走killProcessTree，POSIX尝试进程组再父PID，Windows异步启动taskkill；没有逐一确认任意后代回收的完整机制。[child-process.ts](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/utils/child-process.ts:35)、[shell.ts](/Users/zmw/naiyue-personal-workspace/pi/packages/coding-agent/src/utils/shell.ts:200)。

Claude官方公开后台任务、任务查询/停止等产品机制，但本轮文档没有给出同样的父进程退出后EOF静默算法，不能据此声称其内部与Pi一致。[工具参考](https://code.claude.com/docs/en/tools-reference#background-commands)。AgentX已核对的WebSocket/Docker终端是持久交互消费者，不是本项目首版Shell合同。

候选沿Pi的小机制，不默认追查并证明所有后代结束：正常读取到父进程退出，继续排空至EOF或100ms静默；持续输出仍受本次调用剩余期限约束，不允许一直重置成无上限。EOF前按策略结束采集时关闭本端读取资源，明确capture不完整/可能有后代继续，不把原退出0改称未执行，也不因此默认锁会话或抛机制异常。区别于真正的读取IO异常：后者仍沿19.1给具体错误消息。命令已退出后耗尽排空期限，应写“输出采集达到时限”，不能伪称父命令执行超时。

超时/用户停止且父进程仍在时，停止受管进程组/树、等待本次受管Shell回收并关闭采集；清理等待建议先沿当前5秒的量级。正常父退出不新增“自动杀掉所有后代”的合同，也不把`&`视为受管后台任务。只有受管父进程/本端资源确实不能在收尾期限内结束，才按机制故障上抛；不把所有未知后代等同此故障。100ms静默及收尾等待拟在认可后放入工具运行设置；测试用事件同步，不依赖紧时间窗。

本机Python3.12临时asyncio探针：子进程再启动一个持有输出管道的后代后退出，观察到`parent_returncode=0; wait_finished=False; output_eof=False`；清理后两个任务都结束。说明不能仅以await process.wait()/管道EOF视作独立的父进程退出事件，需要分别观察公开returncode与读取任务。此探针不是产品实现，未留下后台进程；无Windows验证。

### 11.2 PowerShell错误与退出码

Pi Bash按非零码抛错误并保留输出，没有从文本推断每一步成功。Claude官方对rg/grep/git diff等常见命令的部分非零码另作正常结果处理，PowerShell还包括robocopy等例外；这是更完整的产品适配，不是Shell或本项目框架自动具备的行为。[Claude工具行为](https://code.claude.com/docs/en/tools-reference#shell-selection-in-settings-hooks-and-skills)。

候选首版尊重Shell进程结果：0反馈该Shell正常退出，非零反馈具体错误；保留输出，模型判断业务含义，不制作命令名/退出码白名单，不凭stderr或“Error”字样判失败，不统一设置ErrorActionPreference=Stop。`Write-Error 'x'; Write-Output 'done'`这类前面有非终止错误、最后成功的脚本可退出0，工具不能宣称每一步都成功；与Bash中先失败后成功同样需要区分。

[PowerShell官方启动语义](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pwsh?view=powershell-7.5#-command---c)说明-Command根据最后状态给0/1，特定原生命令码可能折为1；需要精确保留时可显式`exit $LASTEXITCODE`。不在所有脚本末尾自动追加这句，避免把之前已处理的原生失败误变成最终结果；显式exit及终止错误按解释器规则保留。5.1/7须分别实测，不能仅据7的资料宣称都通过。

### 11.3 编码、文本视图与控制字符

Pi OutputAccumulator使用流式UTF-8解码器并分别计原始/解码后字节，截断时按需落原始字节文件；本轮不能把utils里的sanitizeBinaryOutput当作Bash执行主链已调用的净化器，实际Bash/Accumulator没有该调用。Claude官方声明PowerShell支持UTF-8原生程序输入、5.1文本重定向UTF-8、移除错误输出ANSI；不能据此推导其所有字节/控制处理算法。

候选采用UTF-8增量解码及统一可读文本视图；PowerShell在本次进程内设置控制台文本和向原生程序传文本的UTF-8初始化，5.1的Out-File/文本重定向默认UTF-8，用户显式Encoding优先。用Out-File具体默认参数，不全局设置所有cmdlet的Encoding；5.1的UTF-8 BOM与7的无BOM差异要保留事实，不能承诺逐字节相同。PowerShell7.4+原生命令直接重定向可保留二进制流，不重写成统一文本。[编码](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5#changing-the-default-encoding)、[原生重定向](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_redirection?view=powershell-7.5#redirecting-output-from-native-commands)。

外部程序未必服从UTF-8约定；继续允许显式output_encoding，不猜编码。非法字节用替换字符保持反馈、标记有损并保留已收到的原始归档，不把转码后的视图冒称无损原文。模型正文与Read视图使用同一文本表示和行号，2000行/64KiB计其UTF-8正文，原始文件仍计真实落盘。

文本表示候选：删除ANSI控制序列但保留文字，CRLF规范为LF，独立CR也转LF，其他不可见控制字符给可读转义、保留制表符/换行。`10%\r20%\rDone\n`显示为三行，不模拟终端覆盖整屏；这会保留进度历史，但已有尾部和归档额度限制。转换需增量处理跨块转义/CRLF/多字节边界；不建设终端仿真器，不用一个未经验证的局部正则承诺完整控制协议。

### 11.4 隐式启动、私有环境与执行策略

Pi默认Bash-c且环境展开process.env，当前链没有去掉BASH_ENV；本机Bash手册与先前临时探针说明非交互Bash仍可按BASH_ENV执行文件。Claude Bash在会话启动时读取用户启动文件、复用部分环境/函数等；其PowerShell官方说明不加载profile。因此不能声称“它们都使用完全干净且不读配置的Shell”。

候选：Bash默认非登录/非交互、不主动source用户rc/profile，并在可见的默认unset_env里明确列BASH_ENV；如用户确需该启动脚本，先从移除列表撤掉，再按需显式准备环境。PowerShell使用NoProfile/NonInteractive/文本输出及安全传输命令的EncodedCommand；该编码不是权限或安全检查。Agent自己使用、工具不需要的NAIYUE_WEB_TOKEN也建议列默认unset_env。都保持小而具体的配置项，允许用户显式调整，不删除所有TOKEN/KEY、PYTHONPATH、代理或证书变量，也不引入业务白名单。当前配置样例仍为空列表，等用户确认这些默认后再同步。

继承SHELLOPTS等Shell行为变量/导出函数与主动source启动文件不是同一件事；本轮不扩大成清空所有会影响程序的环境，也不承诺完全净化用户Shell语义。需要固定某个行为时分别给理由；用户显式source/点加载项目脚本仍是实际command的一部分。

Claude官方还说明Windows PowerShell默认使用进程级ExecutionPolicy Bypass，企业MachinePolicy/UserPolicy仍有效，并提供尊重现有策略的设置。候选沿此易用性：仅该子进程选择Bypass以运行用户测试脚本，支持运行设置改为尊重现有有效策略，不调用Set-ExecutionPolicy修改用户/机器长期策略；这是本轮需用户认可的额外启动选择，不属于已确认环境继承自动授予的结论。[Claude PowerShell启动](https://code.claude.com/docs/en/tools-reference#powershell-tool)。

本节状态：只确认第1项精确命令；11.1—11.4是本轮有来源的候选。未改产品/测试/运行配置或安装解释器；本机没有pwsh。配置样例未提前加入候选收尾数值、默认移除项或执行策略字段。

后续收尾复核见[Operations与沙箱边界](tool-sandbox-boundary-2026-10-15.md)：本节宿主执行的进程/环境方向不因沙箱展望自动推翻。需要新明确的是执行后端与当前OS/cwd/环境、权限对象和日志位置的绑定；Linux VM不能提供原生Windows PowerShell，未来沙箱环境也不能自动照搬宿主继承环境。用户当前要求先检查、展望，不实施隔离后端。

用户随后倾向本机进程隔离，影响收口见[沙箱记录第10节](tool-sandbox-boundary-2026-10-15.md#10-倾向本机进程隔离后对现有工具设计的影响)：共享进程入口承接双Shell及rg/fd，已定输出/归档/环境/批次策略保留；批准命令不自动扩大OS资源范围。外部目录的显式申请入口、读/网络限制和具体后端尚未决定，command/timeout暂不擅自加字段；仍不实现。

## 12. 启动与收尾的通用机制复核，候选合同

用户进一步要求说明进程方案是否通用准确。本节收束可复用机制与实际保证，不新增沙箱前置要求；未授权产品实现，也未把Windows候选当已验证。

- 进入实际执行后建立单调时钟总截止时间，人工审批等待不计命令执行时长；准备解释器/argv、cwd、显式环境和输出消费方式。启动前检查停止，启动期间发生取消也要接管可能已创建的进程并收尾。Shell显式启动所选解释器，rg/fd直接argv执行，不额外套shell。
- 使用asyncio子进程能力持续采集，避免先wait再读导致管道堵塞，也避免用communicate将无限输出收进内存。Bash/PowerShell合并流；rg/fd的结构化stdout与诊断stderr分开并发消费，不因公共入口而改输出协议。归档/尾部缓存有界，磁盘保存失败继续消费管道并按既定合同降级。
- 退出、输出关闭、资源收尾是独立事实。Python公开SubprocessProtocol提供process_exited/pipe_connection_lost等事件；SubprocessTransport提供get_returncode/get_pipe_transport及管道close。执行层可用这组公开API接出独立事件和有界关闭，不以一个高层wait/communicate代表全部完成，不依赖私有_transport。
- 本机Python3.12一次性探针：父进程启动持有stdout的短时后代后退出，公开协议得到parent_exit_code: 0、pipe_closed_at_parent_exit: False；显式关闭管道后public_pipe_close_completed: True。finally终止本次独立进程组并关闭transport；这是离线机制证据，未改产品/测试文件，不是Windows验证。
- Shell正常退出按11.1的Pi候选：EOF则完成；否则以最后收到输出为基准等短静默，候选100ms，持续输出仍受剩余总期限约束。主动关闭本端采集须标记不完整；退出0可与采集不完整同时成立。100ms是启发式，不证明未来不会再输出，也不证明所有后代退出；正常退出仍不新增自动杀所有后代的合同。
- 该Shell静默策略不能直接套给所有消费者。rg/fd正常结果优先要求进程退出和输出结束；未完整取得输出时沿搜索不完整/错误合同反馈，不悄悄按完整结果解析，也不把缺失输出解释成无匹配。
- 超时/用户停止在工作进程仍运行时，终止受管进程组/树，继续有界读取和回收，再完成归档。已退出但管道未结束时是排空/采集达到时限，不伪报命令仍在执行。真正任务取消做有界清理后继续传播。收尾只给一份单独的有界预算（当前候选沿5秒量级），不是每个小步骤重发5秒；具体数值确认后走配置。
- macOS/Linux新建session/进程组，取消/超时向组发送终止信号并回收直接子进程。主动setsid等方式脱离的进程不在该宿主管理保证内；本次不建设任意后代发现平台。
- Windows原生asyncio须验证实际Web/CLI事件循环支持，标准Proactor支持子进程。按已有候选先沿Pi用taskkill /T /F清理并等待工具结果、确认受管父进程回收及本端资源关闭；不能把taskkill返回成功当任意后代已终止，尤其父先退出/后代脱离。JobObject是更强的后续进程生命周期手段，并非沙箱本身；若将严格树控制列为首版硬承诺，需单独核对并实现Job启动/句柄/嵌套等合同，不能只在已有asyncio spawn后补一句assign。当前未新增该硬承诺。
- 收尾故障必须记录：受管父或本端必要资源不能在期限内回收时按运行机制故障处理，不用成功结果掩盖；不因无法证明所有未知后代状态就默认锁会话。必要终止/回收、可降级归档和普通观察分开。
- 测试按事件同步覆盖：大量连续输出/双流、父退管道留存、EOF与退出顺序、超时/用户停止/任务取消、启动阶段取消、读失败、归档失败、清理失败、并发停止隔离。现有正常命令测试不证明这些边界；macOS/Linux/Windows真实样本另验证。

依据：[Python异步子进程](https://docs.python.org/3.12/library/asyncio-subprocess.html)、[公开进程协议/transport](https://docs.python.org/3.12/library/asyncio-protocol.html)、[Windows Job](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)；Pi版本/函数入口见11.1。本轮只追加设计与本机一次性探针，没有实施公共执行器或启动Windows/沙箱。
