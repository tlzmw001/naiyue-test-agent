# Spec085-15：工具迁移终检与 Windows 兼容性复盘

日期：2026-10-16。范围：[085-15](../specs/085-15-final-audit-windows.md)，当前工作区的085-01—14实现；不修改真实配置、依赖、数据库或用户文件，不实施沙箱/SubAgent。

**结论：本机工具主链接线和迁移回归通过；当前仓库不能直接作为已支持原生Windows的版本交付。** 已确认启动/私密检查/覆盖编辑阻点，并发现Windows路径命名空间的Deny匹配缺口。以下平台证据是源码＋官方约束＋macOS隔离探针，不是Windows原机成功/攻击验证。WIN1继续开放。

后续决策（同日）：用户先取消模型配置权限限制，再取消会话文件私密权限门槛。load_model_catalog和SessionStorage均已移除组/其他用户模式位拒绝；后者内部检查改名为_check_store_file，保留普通文件/链接检查、实际IO失败、schema及同库实例独占。W2中模型/会话被拒的探针属于修改前快照，不新增这两类文件的ACL检查。归档目录检查仍待独立处理，数据库锁本轮不变。


Spec087后续实现（同日）：SessionStorage已移除顶层fcntl和无条件O_NOFOLLOW/flock，改用根级storage_files.py；POSIX按需fcntl，Windows用CreateFileW普通/reparse句柄检查、非继承CRT描述符、msvcrt固定范围锁。W1及会话库API阻点已在代码处理；macOS真实双进程和Windows分支mock有验收，Windows/Linux原机尚未验证。以下W1源码行号/探针是修改前证据；W2归档私密位、W3写入fchmod和W4路径命名空间仍待处理。当前新主链见[Spec087](../specs/087-workspace-session-storage.md)，不据此宣布WIN1完成。


Spec088/089后续实现（同日）：已接Windows ReplaceFileW内容提交及原DACL保留选项，跳过Windows无意义的fchmod，取消归档私密模式位门槛并保留普通/reparse/标记/快照检查；统一普通/扩展盘符和UNC比较，覆盖规则、默认、session_allow、递归过滤和写入占用。不支持设备/ADS/歧义表示在目标IO前反馈，配置在启动校验拒绝。当前W2/W3/W4对应代码缺口已处理，以下描述保留原始探针事实；真实DACL/长路径/UNC/junction和安装/双Shell/停止/保存恢复仍未在Windows/Linux原机验证。新增73个本机路径/平台分支/正式图用例，完整结果见handoff。WIN1继续开放，当前唯一平台下一步为原机端到端验收，不再列这些代码分支为未实施。

## 1. 环境与证据方法

- 实机：macOS、Python3.12.14；LangChain1.4.2、LangGraph1.2.12、langchain-core1.6.6、Uvicorn0.54.0、Pillow12.3.0、aiosqlite0.22.1、checkpoint-sqlite3.1.1。本机Bash3.2.57，rg15.2.0/fd10.5.0为此前已安装；没有pwsh、Windows或Linux测试机证据。
- `/tmp/naiyue-08515-portability-probes.py`在临时目录/独立子进程中移除特定Unix API、替换stat形状、注入导入失败；不把整个sys.platform改为Windows来伪装操作系统。只有Uvicorn工厂的一个分支测试改platform并用哨兵代替Proactor类，**没有实际构造Windows事件循环**。
- `PYTHONPATH=. .venv/bin/python /tmp/naiyue-08515-portability-probes.py`结果保存在`/tmp/naiyue-08515-portability-probes.log`，13条受限事实。临时文件不是仓库交付依赖；下文有可重复的最小接口输入和Windows原机复现命令。探针不读真实凭据、不调用远端模型。
- 当前工作树本就包含前14片及课堂的未提交改动；本片保留原状态，不切分支、不清理用户工作、不提交/push。

## 2. 当前工具链的验收

### 正常与审批恢复

以启用powershell后调用`{'name':'powershell','id':'ps-1','args':{'command':'python -m pytest -q','timeout':60}}`为说明数据；这不是Windows真实运行记录。

1. `web.app.create_web_app`的lifespan加载ToolSettings并调用`create_builtin_tools`；八个原始ToolSpec交给`application.prepare_tool_registry`，初始活动六文件，Shell独立启用。CLI也复用同一设置/集合。共享注册表、会话活动名称、当前模型请求快照分别有真实消费者，不复制BaseTool。find/grep/bash/powershell有原生coroutine；read/ls/write/edit仍由StructuredTool的线程回退执行同步文件IO，外层await_settled确认已开始操作收尾。本轮结构探针曾错误假定八项都有coroutine，按075既有合同核对后改为分别验证两种入口，没有据此修改生产逻辑或放宽已有测试。
2. `ToolSetMiddleware.prepare_tool_view/model_inputs`固定本次原始定义，模型schema/提示词/预算用同一集合。主响应后`PermissionMiddleware.prepare_local_batch/_prepare`校验公开参数、准备subject和本批mode/session_allow，Deny优先，再Allow/默认Ask和模式。`ExecutionAuthorizationMiddleware`稍后消费保存结果，不再匹配一遍规则。
3. `_build_runtime_agent`中after_model hook按框架逆序调度，因此权限准备在`BatchApprovalMiddleware.after_model`之前，批次调度计划在审批之后建立；已核对本机langchain/agents/factory.py:1787的after_model逆序连边。Ask→固定图节点内的局部官方HITL→整批暂停，未批准时用户命令零执行；固定版本探测属于启动装配。恢复重入HITL节点，使用已保存权限事实，不重跑Auto；之后`ToolBatchMiddleware`建立当前上下文计划。
4. `ExecutionAuthorizationMiddleware._authorize`检查call_id/名字/原始args，Ask一次性消费匹配grant，随后创建本次context permit。`build_shell_tool.execute`显式`require_tool_permit`，适配器只构建可信启动参数/环境，`HostProcessRunner.run`拥有子进程和消费者。
5. `OutputAccumulator`产生尾部，`ShellOutputArchive`记录前缀/原始/条件文本视图；`format_outcome`返回正文或抛具体ToolException。官方ToolError在外围生成相同call_id的error ToolMessage，普通非零结果不使运行失败；观察层产生配对事件，必要checkpoint/metadata保存后才发布完成终局。

文件链沿同一许可，`require_file_target→verify_file_target`在IO前复核真实目标。Write/Edit在创建父目录、准备临时文件及提交前的再次复核保护各自阶段的目标身份；这些是执行检查，不是重复Auto审查。软链接、目录身份、扫描候选过滤、图片消息与artifact仍由原模块负责，无新Operation类或通用OS平台。

### 失败与控制

`HostProcessRunner.run→ProcessCleanupError→tool_error_feedback返回None→原异常上抛→SessionService标记manual_review_required→必要保存/失败终局`。资源未确认不能当普通错误继续；原始取消/停止、GraphInterrupt、调度/许可故障也不走普通错误转换。ProcessCleanupError被诊断为tool_outcome_unknown仍有事件/状态消费者，不因字段名字包含unknown就删除。

四条新增真实应用测试覆盖sync/async×普通运行/审批恢复，检查异常同一对象、busy释放、pending清空、失败终局与核对标志、诊断无私密正文。正常、拒绝、动态集合、Auto恢复不重审、记住批准、存储恢复及并发/顺序使用已有测试验收。

### 清理与保留清单

| 项目 | 当前结论与消费者 |
|---|---|
| runtime下旧tools/tool_approval/file_permissions/tool_batch模块、旧grant别名/metadata、ToolFeedbackError、Write/BashOutcomeUnknown、逐工具handle_tool_error | 生产、scripts和tests搜索未发现旧执行消费者；注册层仍拒绝单工具错误处理，属于当前合同。 |
| BashOutputArchive/preview_bash_outputs/clear_bash_outputs、旧Shell环境额度开关、run_process_sync、Popen/communicate/双采集线程 | 生产链无残留；公共Shell/输出及run_async_sync承担真实同步消费者。 |
| 旧host-bash/host_bash文本 | 只剩拒绝旧CLI参数/旧Web字段的回归测试及test_host_bash文件名/导入；不是兼容入口，保留拒绝断言。 |
| bash_event诊断分支 | 本片唯一生产清理：SessionService两处session_manual_review_required统一为session_event，删除仅为旧Shell标签选择的条件；不改异常/权限/保存。四例先红灯（sync两例缺session_event）后转绿。 |
| 同步工厂/运行入口、run_async_sync、sync/async批次算法、框架hook | App公开同步调用、脚本/同步图及正式异步图确有消费者；不以“同步”或静态外部调用少为由删除。 |
| 旧归档stdout/stderr格式、.naiyue-bash-output-v1标记 | output._snapshot/所有权检查与Read/CLI精确清理仍消费既有文件。继续支持数据，无自动迁移/删除。 |
| grant、执行permit、文件身份复核、数据库独占、会话文件类型/链接检查、日志所有权 | 各自职责仍需保留，新Allow/Deny不替代OS访问或存储一致性；模型配置及会话文件的私密模式位门槛已按后续决定取消。 |

以旧符号搜索、相关顶层定义引用及真实hook调用点作为检查入口；不是对整个项目所有动态调用的形式化证明。没有为假设外部用户新增转发/缓存/服务。

## 3. Windows问题与证据

### W1：CLI/Web在进入main前被fcntl阻断（启动阻断）

- 代码：`application/cli.py→cli_async.py→async_persistent.py→session_storage.py:3`；Web的`web.__main__→web.app→async_persistent.py`也加载同一存储。即使CLI指定`--no-session`，模块导入仍会到达这里。
- [Python3.12 fcntl文档](https://docs.python.org/3.12/library/fcntl.html)限定Unix。隔离导入探针让fcntl不可用后，两个真实入口均得到`ModuleNotFoundError:fcntl`，未开始读模型或调用工具。
- Windows复现：`python -m naiyue_test_agent.application.cli --help`、`python -m naiyue_test_agent.web --help`。这里是预期原机验证入口，尚未在Windows执行。
- 只把import挪进构造函数不能完成修复：`SessionStorage.__init__`随后还无条件依赖O_NOFOLLOW和flock；移除O_NOFOLLOW的本机探针确认AttributeError。本片不靠删锁绕过它。
- 后续文件：session_storage.py、persistent.py/async_persistent.py及跨进程锁测试。保留同库进程独占、句柄关闭、链接/非普通文件保护及故障可见；平台锁实现另定。

### W2：私密检查把Windows模式位当POSIX权限（模型/会话检查已取消，归档待处理）

- 原复盘代码：`model_access.load_model_catalog`和`SessionStorage._check_private`的`S_IMODE&0o077`（均已移除；后者当前为_check_store_file）；`tools.output._directory`的`mode&0o077`仍在。
- [CPython3.12.14 attributes_to_mode](https://github.com/python/cpython/blob/v3.12.14/Python/fileutils.c#L1003)把Windows常规文件映射为0444/0666，目录再加0111；这不等于ACL中其他用户能否访问。注入对应stat形状后：模型读取在正文前被拒绝、存储抛SessionPersistenceError、归档目录被拒绝。归档降级不使原Shell命令失败，但会丢失完整回读能力。
- 不应把“Windows chmod只改只读属性”推广成“mkdir完全不能设ACL”：[Python3.12 os文档](https://docs.python.org/3.12/library/os.html#os.mkdir)说明3.12.4起mkdir(mode=0o700)有Windows专门处理；已有st_mode检查仍无法验证其ACL。chmod也不能据此替代已有文件的ACL检查。
- 模型JSON及会话库/lock/sidecar不再列入私密权限修复，不新增ACL校验或开关。会话新建模式不变，既有权限不自动调整；fcntl/O_NOFOLLOW与同库独占另行兼容。归档目录/文件的剩余检查继续按实际消费者单独讨论。

### W3：覆盖已有文件及Edit依赖fchmod（功能阻断）

- 代码：`tools.write._write:162`仅替换已有文件时调用fchmod；`tools.edit._copy_to_temp:172`总会调用。
- [Python3.12 fchmod](https://docs.python.org/3.12/library/os.html#os.fchmod)限定Unix。隔离探针移除该API，真实新建Write成功，已有文件覆盖和Edit均AttributeError；原文件保持before，临时文件清理为0。没有把“所有Write均不可用”作为结论，也没有真实Windows原子替换成功证据。
- 正式图会将此普通异常反馈模型；不能因此认定编辑能力已支持。后续需分别处理Windows的只读属性/ACL保留、临时文件继承、已占用文件提交失败与现有提交未确认合同；不能仅`hasattr(fchmod)`后静默丢弃原属性。

### W4：扩展命名空间未纳入Deny目标等价比较（授权风险）

最小匹配输入：规则`tool=read,path=C:/work/private.txt`；subject的平台为windows、requested与resolved使用同一目标表示，其余字段相同。

```text
requested/resolved=C:\work\private.txt        → deny_matches=true
requested/resolved=\\?\C:\work\private.txt    → deny_matches=false
```

`permission_rules._component_match`只统一斜杠和casefold，未统一前缀。`prepare_file_subject→Path.resolve`也不能直接证明已去前缀：[CPython3.12.14 ntpath.realpath](https://github.com/python/cpython/blob/v3.12.14/Lib/ntpath.py#L611)在原输入已有前缀时保留它；[Windows命名空间文档](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file#win32-file-namespaces)确认该前缀有实际文件API语义。

这是规则函数真实运行＋CPython/Windows源码合同，**没有在Windows实际读取被禁文件**。修复不能只往规则字符串上replace一个前缀：还要保证真实目标、请求目标、规则、祖先边界、UNC/junction/设备命名空间的语义一致。尤其不能无条件剥前缀后改变本来合法但含特殊字符路径的解释。按独立路径授权小Spec决定支持/明确拒绝的表示，保留Deny优先，再做原机回归。本片不改现有授权范围。

### 已排除与仍需验证

- **当前Web默认循环不是已确认阻点。** `web.__main__.main`明确workers=1且无reload；本机安装Uvicorn0.54.0的`Server.run→Config.get_loop_factory→auto_loop_factory→asyncio_loop_factory`在Windows且use_subprocess=False时返回Proactor。哨兵探针验证分支选择；[Python平台文档](https://docs.python.org/3.12/library/asyncio-platforms.html#subprocess-support-on-windows)确认Proactor支持subprocess。尚未原机构造/运行。多worker、reload、自定义loop不在当前入口合同里，不能外推。
- CLI的`AsyncCliApplication._serve`用默认asyncio.Runner；进程工具使用public subprocess_exec、Windows创建标志/taskkill分支，已有假transport测试。原机需补真实退出、持管道、取消/停止、taskkill失败和子树边界；不额外承诺逃逸子进程的回收。
- PowerShell已有5.1/7参数/编码分支与UTF-16LE EncodedCommand，缺真实解释器证据。须实际验证原生输出编码、CLIXML/错误呈现、非终止错误/退出码、显式-Encoding与7.4+重定向。Git Bash还需原生程序/含空格路径与PATH转换验证。
- `Path.resolve`、文件锚点/类型快照、大小写规则分支已经存在。盘符/UNC/长路径/设备名/ADS/junction、占用文件、网络文件系统及NTFS细节仍需平台验收，不能只改sys.platform后宣布已覆盖。
- requirements及Web依赖没有安装验证到Windows/Linux；原生wheel/目标架构、rg/fd/pwsh或Git Bash安装及PATH、Windows命令说明均待补。现有macOS venv和前端构建不证明原机可安装。

## 4. 另一项已存在的运行限制

后续状态：下段为修复前证据；[090A](../specs/090-tool-wait-stop-and-author-contract.md)已在2026-10-16删除sync/async正常排队期限和旧环境读取，并补同步停止guard。Windows原机验收仍未完成；Shell/搜索可选执行期限另归090C，不混同于这项排队修复。

`ToolBatchMiddleware`仍从NAIYUE_TOOL_BATCH_WAIT_SECONDS读默认60秒；_SequentialPlan/AsyncBatchPlan等待前序串行工具超过它就报ToolBatchSchedulingError。Shell允许最长300秒，因此“前序合法长命令＋后序调用”同样可能受影响，并非只有未来子Agent。隔离探针把同一等待预算设为0.01秒，用事件持续保持前序任务运行，后序未进入执行就得到ToolBatchSchedulingError且batch_failed=true；不依赖“睡一会儿后碰巧看到某状态”，也不是实跑60秒。它没有被本片当死代码删除：[Spec086第11节](../specs/086-subagent-foreground.md)已记录移除正常排队期限的目标。应在独立调度切片实现并保留机制故障/停止/命令自身期限，不在本片静默更改调度策略。

## 5. 参考项目与修复顺序

源码版本：Pi a69bef789、AgentX3fcfa0e（本地HEAD，无pull）。Pi的`AuthStorage→FileAuthStorageBackend.withLock/withLockAsync→proper-lockfile`围绕auth.json读写持锁，写文件创建mode0600，未找到本项目这种读前强制S_IMODE私密检查；这条链不能证明Pi核验Windows ACL，也不能直接复用成SQLite全生命周期锁。AgentX的`AgentSessionAppService→SessionDomainService→SessionRepository(MyBatis)`走服务端数据库，用户范围由userId查询条件决定，不是本地SQLite文件独占锁，不能用它取消我们已有保护。

建议按真实消费者分片，先讨论Windows安全文件/锁，再改文件属性与路径授权，最后补原机端到端；模块公开函数签名尽量保持，平台实现从现有调用点接入。没有证据需要重建ToolSpec/注册表/权限管线或引入通用OS服务。具体锁/ACL后端与权限保留合同待下一轮决定，不在此次复盘中安装库或拼出未经验证的WinAPI封装。

## 6. 验收矩阵与结果

| 能力 | macOS本轮 | Linux原机 | Windows PowerShell5.1 | Windows PowerShell7 |
|---|---|---|---|---|
| 主工具/权限/审批/动态集合及保存恢复 | 确定性正式图/本地IO回归 | 未验证 | 087/088已接，未原机验证 | 087/088已接，未原机验证 |
| 文件/搜索/图片/Edit | 真实文件、原有rg/fd/Pillow回归 | 未验证 | 088/089已接，未原机验证 | 088/089已接，未原机验证 |
| Shell输出/超时/停止/取消 | 真实Bash/进程＋故障注入 | 未验证 | 参数/模拟协议证据，未原机验证 | 参数/模拟协议证据，未原机验证 |
| 前端 | 测试/build通过 | 未验证 | 未原机验证 | 未原机验证 |
| 会话/归档访问保护、进程锁/文件占用/安装 | 既有POSIX保护回归 | 未验证 | 需专门修复与实测 | 需专门修复与实测 |

验证命令与实际结果：

```text
PATH="/Users/zmw/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q
1597 passed in 48.17s
```

- `/tmp/naiyue-08515-full.log`：本轮全量1597；新增四例诊断回归，未删除/skip既有用例。相关15文件定向168 passed in7.82s，日志`/tmp/naiyue-08515-focused.log`。
- `frontend`下`npm test -- --run`：9文件27 passed；`npm run build`：TypeScript/Vite186模块成功。日志`/tmp/naiyue-08515-frontend.log`与`/tmp/naiyue-08515-build.log`。
- `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`无错误；`pip check`为No broken requirements found；272个Python文件AST/≤500行通过。
- 当前注释样例经严格ToolSettings与实际loader/八原始工具注册校验；public schema无runtime，串并行/副作用和四原生async＋四文件线程回退逐项确认。未来样例TOML解析及`git diff --check`通过。
- 旧模块/符号/配置消费者搜索完成；剩余旧名字是拒绝旧参数/旧标签断言、现有归档格式或历史记录，见清单。仅一个生产文件发生本片修改，删除两个旧标签选择分支；无新生产模块/依赖、真实配置/存储迁移。

WIN1不因本机全绿而标完成；本片完成的是工具迁移终检与阻点/后续修复范围复盘。Windows安全文件/锁、文件属性/路径授权、真实三系统验收与长命令排队限制仍是明确待办，不能写成“直接拿到Windows即可运行”。

### 后续配置权限变更的验证（2026-10-16）

模型配置权限门槛取消的十例回归先8 failed/2 passed，移除模式位判断后模型相关三文件88 passed in3.93s。全量命令同上，最新结果**1607 passed in48.71s**，日志`/tmp/naiyue-config-permissions-full.log`；前端9文件27 passed、TypeScript/Vite186模块及compileall/pip check通过，273个Python文件AST/≤500行通过。此结果覆盖正常读取、内容校验及OS读取失败，未修改真实配置/权限，不是Windows原机验证。

会话文件权限门槛取消的本机验证（2026-10-16）：新行为先3 failed/2 passed；持久四文件41 passed in4.56s，全量1610 passed in49.15s（/tmp/naiyue-storage-permissions-full.log），前端9文件27通过与构建通过，compileall/pip check、273个Python AST/≤500行及diff检查通过。未修改真实数据库或权限；Windows锁兼容仍未实现。存储位置与多进程共享仍在讨论，不能把分库示例当成已批准迁移。
