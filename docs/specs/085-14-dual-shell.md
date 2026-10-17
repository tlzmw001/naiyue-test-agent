# Spec085-14：双Shell、环境与有界输出（已实施，2026-10-16）

接续085-13，落实总Spec第13节已讨论的Shell产品行为。生命周期复用现有HostProcessRunner；权限继续复用ToolSpec/精确命令规则/四模式/整批审批。本片不实现沙箱、持久终端、后台进程平台或工具管理UI。

## 当前入口与本片目标

当前Bash：`create_session_bash_tool→bash/abash→_prepare→HostProcessRunner→_feedback`，同步通过run_async_sync复用。仍从NAIYUE_BASH_SHELL读取解释器，使用固定PATH/HOME/TMPDIR/LANG；双流各4096B尾部，BashOutputArchive的16/128MiB来自环境变量。12负责清理，13只迁移同步桥和正常consumer等待；这些Shell产品层策略尚未改变。

目标：Bash与PowerShell两个工具名字、各自command/timeout与语言，当前用户本机文件/程序访问能力；默认继承父环境并允许显式覆盖/移除。每次新非交互进程、cwd为会话工作区、无TTY/stdin关闭。Shell输出合并到一个管道，返回最多2000行且64KiB的末尾正文，必要时保留原始归档和Read可读文本视图。超时、停止、取消与真正清理故障沿12/13合同。

## 类、函数与文件

| 文件 | 拟新增/修改及实际消费者 |
|---|---|
| tools/settings.py、tools/__init__.py | EnvironmentSettings；ShellSettings共用字段；BashSettings/PowerShellSettings各自默认和PowerShell execution_policy；ToolOutputSettings及ToolSettings.tool_output。严格schema与描述由同一启动快照生成。非法额度/环境结构按当前tools配置诊断回退，权限读/格式失败仍启动失败；不修改真实配置。 |
| 新tools/shell.py | ShellAdapter.resolve_executable/probe_version/build_request/format_outcome；build_environment；共用sync/async执行入口。两种Shell均真实调用，不建通用后端注册平台。工厂闭包只持不可变设置/解释器事实，workspace/control/permit从每次runtime传入。 |
| tools/bash.py | 保留工厂与Bash输入；适配器生成Bash argv，将原_prepare/_feedback/尾部逻辑迁移到shell/output；不保留两套执行算法。 |
| 新tools/powershell.py | PowerShell工厂、公开/注入schema和PowerShellAdapter；与Bash同为sequential/may_mutate、默认Ask。只允许原生PowerShell语法，不把Bash命令翻译过去。 |
| tools/output.py | OutputAccumulator负责增量解码/文本规范化/尾部行字节范围；ShellOutputArchive负责现有所有权目录、限额、原始和条件文本视图。保留ClearPlan/精确预览再清理合同，迁移实际CLI消费者；废弃Bash专属类/函数只在所有消费者迁移后删除。 |
| tools/builtin.py、application/cli.py、web/app.py | 登记八工具，活动集合继续独立选择；默认文件工具仍为六个，不能继续用“除bash外全启用”而意外启用PowerShell。CLI输出清理迁移到共用命名/设置；不新增专用开关。 |
| 两份注释配置、受影响脚本/测试/当前说明 | 把NAIYUE_BASH_SHELL和归档额度环境开关迁移到明确设置，删除无消费者旧分支；保留用户已有归档文件可回读/清理。 |

每个生产文件≤500行。实现细化：新增tools/output_text.py承载OutputAccumulator（解码、终端控制规范化与尾部范围）；tools/output.py仅承载ShellOutputArchive/所有权/额度/清理。两者由公共Shell入口直接调用；归档结束时流式重放已保存raw生成必要文本视图，避免为迟到的ANSI/编码问题常驻保留完整正文。两份文件都有当前消费者。

## 解释器、环境与可信启动

`resolve_executable(settings, platform)`优先显式配置，配置不可用不静默改用另一程序；自动发现按宿主平台处理。Windows的Bash只选择原生Git Bash可执行文件，PowerShell优先pwsh再系统powershell.exe；macOS/Linux的Bash按本机路径发现，PowerShell需要已安装pwsh。没有安装/下载或WSL/容器回退。

拟沿Grep/Find的装配方式登记两个Shell定义；缺解释器时提供明确不可用事实，真正调用返回具体ToolException，不妨碍用户使用其余工具。这里将“启动报错”明确为启动该工具进程时报错，不新增全局可用性服务或改变注册/活动API；后续UI可展示相同事实。用户已授权按此落点实施。

为提示真实版本，`probe_version`仅对找到的解释器运行固定元数据命令，使用同一runner，不执行用户脚本、不开profile。已新增每Shell可配置probe_timeout_seconds，默认5秒；输出受既有Shell字节预算约束。工厂保存实际解析出的版本或unknown，不按文件名猜版本、不泄漏完整环境；缺失/探测失败安全诊断。首次装配与后续代码注册均可调用，不新增App接口/常驻服务。

固定版本探测属于可信装配，可能在尚无用户调用时启动短进程；“批准前零启动”的验收专指用户提出的command，不把元数据探测冒充已批准工具执行。该装配行为及缺解释器处理已按用户本轮授权接线。

`build_environment`每次复制os.environ，依次values覆盖、path_prepend按平台pathsep加入PATH、unset_env移除；Windows按环境名不区分大小写处理，避免PATH/Path两个副本。显式unset最后执行，优先于前两步；不修改共享环境、不自动加载项目.env。保留业务测试凭据正常继承，不使用TOKEN/KEY等模糊黑名单。

已采用的工程默认：Bash unset_env=[BASH_ENV, NAIYUE_WEB_TOKEN]，PowerShell仅NAIYUE_WEB_TOKEN；用户可明确调整。Bash用非登录/非交互及不主动source rc/profile；PowerShell用NoLogo/NoProfile/NonInteractive/EncodedCommand，脚本按UTF-16LE Base64编码。默认execution_policy=inherit；显式bypass仅传给该子进程，不调用Set-ExecutionPolicy修改系统/用户策略。

## 正常调用：具体数据

例：Windows会话已启用powershell，主模型提出以下调用，参数仅为示意：

```python
{'name': 'powershell', 'id': 'ps-1',
 'args': {'command': 'python -m pytest -q', 'timeout': 60}}
```

1. 原权限节点审查powershell完整原始command。命中Deny拒绝，精确Allow可免默认Ask；其余按当前模式。Bash的同字符串Allow不自动用于PowerShell，工具名和语言不同。
2. 整批批准后ExecutionAuthorization发本次permit。工具显式验证，公共Shell入口从runtime取得workspace_root/control，检查停止。人工等待不占执行timeout。
3. PowerShellAdapter基于已选解释器生成可信UTF-8初始化前缀、必要5.1文本默认编码设置及用户脚本；原用户command仍是授权绑定对象。显式Encoding优先，7.4+原生二进制重定向不机械套文本改写；不追加exit LASTEXITCODE或设置ErrorActionPreference=Stop。
4. build_environment生成独立环境；build_request产生`argv/cwd/env/timeout_seconds/merge_output=True/eof_policy='shell'/execution_environment`，交同一个runner。Bash路径只更换argv/初始化，不复制进程算法。
5. OutputAccumulator连续消费原始字节，更新合并文本尾部/实际行字节范围；超预算时将已缓冲前缀与后续流交Archive，不把仅剩的尾部冒充完整日志。
6. runner确认exit/EOF/consumer/本端关闭，format_outcome生成exit_code/完整性/有损和裁片原因/尾部/归档路径；ToolNode配原ps-1返回。成功或普通ToolException回填后主模型继续。

同一command内cd或设置变量只作用于该新进程，下次仍从会话workspace和新环境开始。进程的文件效果保留，不声称回滚。stderr含Error不等于失败，Shell退出码是Shell自身事实。

## 输出、归档与失败

- 原生stdout/stderr在启动时合并一根管道。保留接收顺序，不能承诺逆转程序自身缓冲或业务绝对时间顺序；rg/fd继续分流结构化stdout与诊断stderr。
- 文本增量解码，CRLF/独立CR规范化、ANSI处理、其余控制字符可读转义；跨块解码/控制状态有限，不先拼完整输出。无效编码替换须标有损，原始字节独立保存。
- 尾部同时满足2000行与64KiB。优先完整末尾行；最后单行仍过长则保留安全字符边界的行尾片段，说明实际行范围/部分行、行数或字节裁片原因。采集不完整只能报告观察到的总量。
- 普通有效UTF-8无需额外规范化时只存一份；有损/控制转换时提供原始与UTF-8文本视图，模型得到可供Read使用的路径与offset示例。短有效文本不另存；短有损输出可触发原始归档。
- 每调用实际归档16MiB、同工作区跨Shell/会话128MiB；原始与文本视图都计落盘字节。磁盘/配额失败继续排管道并保存最新尾部，说明保存前缀、尾部和中间可能缺失，不标Full output。
- 新文件可用随机ID-raw/text.log命名；旧ID-stdout/stderr.log和现有所有权标记继续可识别。清理只接受精确已展示且未变化的受管文件集合，不自动重命名/迁移/删除旧产物。不宣称跨进程quota事务或审计防篡改。
- 普通非零/确认收尾的超时给具体ToolException和保留输出，不自动重试/换Shell；用户停止配对当前结果并结束本轮，排队项不启动；外部取消收尾后保留原信号。ProcessCleanupError仍上抛，归档不与未结束writer并发finish。

## 配置、验收及下一步

沿未来样例接tools.bash/tools.powershell各自command长度、默认/最大timeout、输出行字节、每调用归档、output_encoding、environment及可选executable；PowerShell另有execution_policy。根tool_output.max_workspace_archive_bytes接入同一个工作区额度。新增probe_timeout_seconds须同步样例注释。结构校验、模型schema/描述、执行检查都从同一设置生成，不保留环境变量额度双来源。

TDD覆盖Bash真实合并/尾部/超长行/编码与ANSI跨块、归档前缀/文本视图/双份计额/配额磁盘错误/旧日志预览清理；继承/覆盖/移除/PATH及Windows大小写；每调用新进程/不延续cd/env、隐藏启动项默认处理；双工具精确规则、活动集合/默认六文件、批准前零启动、非零/超时/停止/取消/Cleanup身份与必要保存。PowerShell argv、EncodedCommand和5.1/7分支以无执行策略变更的单元断言验证；实际解释器存在才记真实执行证据，不自动安装或将假transport当Windows验收。

最后全量后端/前端/build、配置、依赖、AST/500行和迁移残留检查。原文件/图片/搜索能力不回归。Shell工具具备平台适配不代表全项目Windows可运行；session_storage/fcntl、权限位/fchmod、模型凭证与日志等仍留085-15/WIN1全项目复盘，OS隔离沙箱另立Spec。

## 实施与验证（2026-10-16）

- 新生产模块仅shell.py、powershell.py、output_text.py；实际入口为create_builtin_tools→两个工厂→build_shell_tool闭包→原许可/runner→OutputAccumulator/ShellOutputArchive→format_outcome。没有复制BaseTool、ContextVar、沙箱或后台平台。CLI/Web登记八项但初始活动仍六文件；PowerShell审批同样转义展示完整命令/工作区。
- 设置/严格schema/描述/执行共用快照；部分environment配置保留对应Shell默认移除列表，显式unset_env=[]可以清空。父环境每次新取，真实用户.env/模型/工具配置及依赖未改。
- 旧Bash固定PATH/HOME/TMPDIR/LANG、NAIYUE_BASH_SHELL与两个归档额度环境变量、双流4KiB算法及BashOutputArchive/专属preview/clear函数已从生产/脚本/测试消费者移除。旧UUID-stdout/stderr.log和所有权标记为现有产物合同，继续读取/精确预览清理。
- 新五份测试40例。先确认设置/输出接口红灯，再接实现；后续针对归档收尾取消、部分环境默认、Windows相对启动路径、PowerShell终端转义及额度截断半个UTF-8字符分别红灯→修复→回归。其他适配器分支以单元断言及真实Bash图回归补证，不把后补断言一概称作TDD。未删除/skip失败测试，未改conftest。
- 测试helper变更原因：tool_feedback_helpers新增可选permission_mode以让Shell案例经过真实图并明确采用BypassApprovals，既有默认NoAsk保持；test_host_bash.make_app显式模拟激活测试venv的父PATH，替代旧生产代码强行插入venv的前提。旧输出测试改合流/新限额，跨命令额度测试显式使用小额度，保留权限/配对/控制/异常身份/必要保存和所有权断言。
- 归档finish也有独立受管task：取消等待已经开始的磁盘IO后保留原信号；consumer未结束的Cleanup故障保留原异常并延后finish。原始日志按真实字节，文本视图额度截断保留完整UTF-8字符，写读故障不宣称可完整回读。阻塞Python磁盘线程无法被强杀；仍沿12的已知限制，不承诺磁盘故障时严格结束上界。

验证命令与实际结果：

```text
PATH="/Users/zmw/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q
1593 passed in 48.15s
```

全量日志：/tmp/naiyue-08514-full.log。14文件定向161 passed in 11.32s，日志/tmp/naiyue-08514-focused-final.log。前端`npm test -- --run`为9文件27 passed，`npm run build`为TypeScript/Vite186模块成功（/tmp/naiyue-08514-frontend.log、/tmp/naiyue-08514-build.log）。`compileall -q naiyue_test_agent tests scripts`、`pip check`（No broken requirements found.）、271个Python AST/≤500行、当前样例严格loader/真实八工具注册、未来样例TOML及`git diff --check`通过。旧Shell环境开关/类/清理函数/同步薄转发在生产、脚本、测试中搜索无残留；所有权标记和stdout/stderr旧日志格式为有意保留的产物消费者，不是旧执行分支。

本机Bash固定探测结果3.2.57；真实Bash/pytest/文件IO和假模型正式图已验。fd是此前已有安装，本轮仅在测试命令前置PATH；没有安装/下载/依赖升级或提交/push。Windows/Linux实机、真实PowerShell和远端模型均未验证，本机缺pwsh，不安装来补结果。下一片[085-15终检/WIN1](085-15-final-audit-windows.md)仅供审阅，本轮没有实施。
