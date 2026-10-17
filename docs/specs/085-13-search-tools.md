# Spec085-13：Grep / Find 流式搜索（已实施）

接续已实施[085-12](085-12-process-lifecycle.md)。用户已授权本片实现，验证后讲085-14。本片落实总Spec第10节已确认的搜索功能和递归权限，复用进程生命周期；不重做审批、不实现双Shell或沙箱。

## 现状、目标和参照

当前Grep：`require_file_target→_grep_files→find._discover_paths(rg --files整份capture_output)→Python glob→candidate_access→_check_text整文件预读→逐文件_search_file(subprocess.run/整份JSON输出)→格式化`。另有独立regex预检进程。候选2000个、单文件8MiB、总扫描32MiB限制导致大仓库提前失败；总20秒deadline建立得偏晚，发现与预检不共用；异步图仍依靠同步工具工作线程。

当前Find：同一_discover_paths收全部文件名，Python glob过滤，仅有文件；过滤后整份排序/再限条数。存在8MiB发现输出上限，未使用fd原生glob/smart case，也不能发现目录。

已确认目标：Grep按获准候选分组交rg，原生glob/context/ignoreCase/literal，删除整文件预检与三种扫描上限；Find改fd原生glob/smart case、文件和目录、权限过滤后计数。缺程序给具体错误，不下载/安装或换另一套语义。结果和执行期限有界，跳过范围、超时与不完整都明确。

设计参照为本地Pi a69bef789：grep.ts用rg --json与原生--glob；find.ts用fd --glob/--hidden、非Git目录--no-require-git、路径模式--full-path及Windows分隔符适配。Pi原生--max-results发生在我们需要的候选权限过滤之前，因此不能照抄；我们必须先过滤再计返回名额。Pi的读取整行/拼接stderr不能作为本项目有界内存证据。AgentX现有RAG检索不是本地文件工具的同等接口，不把它当该实现的框架限制依据。实现沿该已确认设计，没有新依赖/安装/架构选型。

## 拟修改的文件和入口

| 文件 | 方法与职责 |
|---|---|
| tools/settings.py、tools/__init__.py | 新GrepSettings/FindSettings，挂BuiltinToolSettings；工厂同Read等生成严格公开/注入schema，配置缺失默认/额度非法回退，权限错误仍启动失败。 |
| tools/grep.py | 工厂接settings/process_settings；同步/异步入口共用`_grep_files`协调算法；`_discover_candidates`使用rg --files原生glob；`_search_group`按有界argv分组执行rg --json/context；候选先candidate_access，再将真实获准路径交内容搜索，结果返回前复核。删除_check_text、逐文件_search_file、独立regex预检及2000/8MiB/32MiB扫描上限；不保留从find私有函数借另一套glob匹配。 |
| tools/find.py | 工厂接settings/process_settings；`_find_files`以fd --print0流式处理文件/目录，原生glob/smart case；每项先candidate_access(list)，通过才占返回额度。删除旧rg发现、Python glob与整份收集。完整有限结果排序；已截断则只对已收集获准子集排序，明确不能声称全局最前N个。 |
| tools/paths.py | 在原verify_file_target比较已有kind，防止批准文件后同名目录扩大搜索范围；不新增状态字段/审批节点。 |
| 新tools/search_protocol.py | 只放当前三个流消费者需要的有界记录拼接：rg候选NUL、fd NUL、rg JSONL。跨chunk保留分隔符/编码状态，诊断尾部有界；不创建通用工具服务/文件Operations。 |
| tools/processes.py（必要薄桥调整） | 生命周期、清理与控制算法不复制；若搜索协调需拥有多个子进程，提取现有同步桥的“运行一个async调用”部分供Bash/Grep/Find共用，不加常驻线程/共享服务，删除被替代的无消费者薄转发。异步入口直接await。 |
| tools/builtin.py、两份配置样例与相应调用测试 | 接同一启动快照、添加字段注释；CLI/Web继续消费同一个内置集合，不改真实用户配置。 |

进程全部传argv且不经Shell，`eof_policy='complete'`、stdout/stderr分流、原RunControl。每次子进程的timeout取整次工具deadline的剩余时间；发现/过滤/分组搜索/结果采集共用该deadline，不能每组再获得20秒。单个进程退出码由搜索工具解释，runner只报告事实。

实施前核对：目录发现消费者会等待当前获准组的搜索，正常父退出/EOF不等于消费者完成。`HostProcessRunner._watch`须在正常路径继续等待consumer并计入执行deadline；不能父退出就提前进入5秒清理期。清理/停止算法保持，补真实退出但consumer仍处理的回归测试。同步薄桥迁移为`run_async_sync(async_call)`，Bash与两种搜索均为实际消费者，旧薄转发随消费者迁移删除。

协议额度实施细化：Find增加max_record_bytes=64KiB和max_diagnostic_bytes=8KiB；Grep的max_record_bytes=1MiB也约束候选NUL记录。不能用模型输入max_path_chars推导绝对候选路径缓冲，否则配置短输入路径额度会错误拒绝正常发现结果。所有额度分别注释，未改真实设置。

取消探针补充：Grep的发现consumer正在await一组rg时，外部取消发现runner会先等待consumer收尾；只传根RunControl无法让内层rg知道Task.cancel，不能直接cancel consumer而丢掉受管IO。新增调用内`_SearchControl`，通过现有control参数同时观察原RunControl及本次Grep task.cancelling()；内层搜索据此停止并收尾，外层保持原CancelledError。无新ContextVar/服务/公共接口，Find单进程不用此代理。先以真实受管合成程序复现，再验证进程回收与原取消参数。

运行器在stream_records拥有的独立async task中执行并被直接await；正常wait_for轮询的临时cancel/uncancel不作用到搜索owner。否则owner.cancelling()会在普通轮询中短暂为真，错误中止大批搜索。已有真实2051文件回归复现，另以无时间竞争的内部cancel/uncancel探针验证隔离；外部取消仍传递到受管任务，不能留下后台执行。

候选权限判断继续复用candidate_access，不修改其公开返回合同；搜索协调器另保存prepare_file_subject/verify_file_target的目标快照，在送入rg和输出前核对，并将原逻辑路径/真实路径都重新检查Deny。元数据IO不预读正文，链接仍不无条件follow；宿主路径校验不能宣称消除敌对并发换链的TOCTOU。

目标复核补充：已有subject保存kind，但verify_file_target未比较该事实，获准单个search文件在等待期间变成同名目录时可能扩大成递归搜索。本片先复现为正式图回归，再在paths.verify_file_target同一个复核点加入kind比较；无新字段/API/审批算法，也不改变软链接真实目标合同。

不存在的目标不能伪称file：kind在准备时明确为missing，前后均存在时才比较file/directory；不存在目标仍由原操作存在性检查处理。这样保留原串行write创建父目录后ls/grep该目录的能力。kind仍是同一JSON字符串字段，无枚举schema/DB映射变更，权限/Auto/保存消费原subject；已补该正式三工具链的回归，不以kind比较破坏串行创建场景。

## 正常例子与数据流

模型发`grep(pattern="TODO", path="src", glob="*.py", context=1)`；该调用是示意数据：

1. 既有权限管线形成src真实目标的search subject；无规则时普通工作区Allow，外部目录Ask。整批审批时任何搜索进程都尚未启动。
2. 执行许可通过，require_file_target固定起点。Grep创建一次deadline，启动rg --files/--hidden/--null并传原生--glob；未开启无条件--follow。
3. 候选示例`src/a.py`、`src/.private/b.py`、`src/secret.py`。逐项candidate_access，前者获准；后两者若未授权/命中Deny，仅登记受限跳过范围，不能先读正文再过滤。外部目录整体批准也不自动放开所有隐藏/私密后代。
4. 仅将已获准的真实路径累计到一个有界argv组；组满就用同一个runner调用rg --json/-C1（发现消费者等待时由原队列回压）。进程参数大小预算只控制每组，不重新引入总候选数上限。
5. JSON match/context逐条解析为path/line_number/text，最多保留配置的行片段和最终50KiB/匹配数结果；输出前复核目标仍获准，链接变化不能机械采用旧路径。普通二进制/编码行为由rg协议报告并明确说明，不能声称所有文件已逐字搜索。
6. 发现完成后处理剩余一组，确认结构化EOF和所有子进程收尾，返回匹配与跳过范围；ToolNode配原call_id给模型。模型不直接看到诊断内部缓存，也不会收到每文件审批。

Find类似：`fd(pattern,path)→NUL候选→candidate_access(list)→条目/字节额度→排序有限结果→具体提示`。Find只列名，不读正文；目录显示后缀。未授权目标不占用户可见名额，Deny优先于目录Allow。目录链接不无条件递归，外部真实目标、循环和发现期间消失需有行为测试。

## 失败、限额与需要核对的实现边界

- rg退出0/1分别是匹配/无匹配；退出2或启动/解析失败给具体工具错误。fd按其退出语义处理；stderr不等同业务失败。未知JSON记录类型可以按协议忽略，坏JSON、截断记录不能当完整成功。
- 已有结果但超时/被限额停止：保留结果，明确不完整及未完成范围；无结果但未完成：具体ToolException，不能写No matches。主动达到结果额度的结束是工具局部停止，只清理当前搜索进程，不能设置整个run的停止信号。
- 用户停止/外部取消/CleanupError沿12合同。源文件消失/不可读、无效regex/glob、程序缺失有具体原因和同ID反馈，不记录正文/命令参数。
- 删除扫描上限不等于允许无限协议缓冲。本轮本机探针已确认：rg --json即使带--max-columns=2000/--max-columns-preview，100006字符的源行仍生成100934字节输出；这些参数不能保证JSON记录有界。
- **已接可配置协议预算**：Grep的max_record_bytes=1MiB、max_diagnostic_bytes=8KiB、max_argument_bytes=16KiB/组。前两项约束单条JSON/诊断缓存，后者约束每次argv，不是文件或全局扫描上限。普通记录按max_line_chars裁片；超过单记录预算则跳过该记录并明确不完整，有其他结果就附警告，没有可用结果就报具体错误。避免为读取一条极端源行构建通用流式JSON解析器。Find另有64KiB NUL记录和8KiB诊断预算；当前/未来样例已注释，真实用户配置未改。
- Find按NUL名记录读取，文件名中的换行/非UTF-8字节不能破坏配对；路径展示安全转义。组参数按平台实际编码预算，超长单个路径明确报错，不偷偷裁路径。

## 配置、验证和迁移

沿未来样例接Grep默认100/最多1000匹配、context0/最多10、max_line_chars2000、正文50KiB、整次20秒及pattern/path/glob字符预算；Find默认1000/最多2000条、正文50KiB、整次15秒及pattern/path预算。新增协议预算也须注释语义，不与输出/队列/归档混淆。

TDD从真实rg/fd可用的本机定向用例与缺程序的确定性故障开始，不自动安装。覆盖原生glob/smart case/上下文、文件+目录、忽略规则/嵌套Git、外部批准后的隐藏/私密/Deny过滤且正文零读取、文件名分片/NUL/乱码、空/非法/过大记录、真实>旧8MiB文本和>旧2000候选、期限/结果上限停止、部分结果、停止/取消/清理故障。批准前零启动、同ID、串并行和保存合同不回归。

新生产模块和字段全链接线；迁移旧rg发现、预检、硬上限和相应测试，不为旧断言留兼容入口。最后全量后端/前端/build与≤500行/AST/配置/依赖/diff检查。Windows行为仍需15/WIN1实机验收，不能由本机argv单元断言替代。

## 实际接线与迁移

新增生产模块只有search_protocol.py，包含RecordBuffer、DiagnosticTail、SkippedTargets、SearchStopped及stream_records。三个实际流消费者是rg候选NUL、fd名字NUL、rg JSONL；候选权限和业务反馈仍在各工具。_GrepSearch持本次协调状态，_SearchControl只处理该调用的根停止/外部取消传递；不落checkpoint/DB、不进入模型参数、不新增ContextVar或App服务。

Grep实际链：`require_file_target→_grep_files→_GrepSearch.run→_discover_candidates→stream_records→HostProcessRunner→RecordBuffer→_authorize`，只准备元数据/授权。组满后`_search_group→_recheck→rg --json→_on_event→有界rows`，结束前再复核目标并生成字符串。Find实际链：`require_file_target→_find_files→fd --print0→RecordBuffer→candidate_access/verify_file_target→过滤后计名额→有限子集排序/反馈`。两者StructuredTool都接sync和native coroutine，Bash同步入口同时迁移到run_async_sync；CLI/Web继续共用create_builtin_tools(settings)。

所有rg调用带--no-config，环境RIPGREP_CONFIG_PATH不能偷偷改变受审参数/增加本片未准备的执行方式。两种搜索不经Shell，stdout与stderr分流，使用complete EOF策略和相同执行环境事实。未匹配文件的regex校验复用同一_search_group对空stdin执行，不保留独立预检链。

移除find的旧rg发现/Python glob，Grep的_check_text/_search_file/单独_validate_regex和2000文件/8MiB单文件/32MiB扫描阈值；run_process_sync薄转发及所有真实消费者迁移后删除。生产、测试、scripts已查无这些旧入口/常量消费者。旧归档和Bash输出算法仍有真实消费者，留14迁移。

旧行为测试明确迁移：UTF-8整文件拒绝改为rg的base64匹配字节/二进制截断事实，转义控制字符并说明有损或未完整搜索，不把正文写日志；旧大文件拒绝改为真实大文本能搜索且Python不预读。Find缺rg改为缺fd错误；截断结果按已批准有限子集排序，不再断言全局最前N项。保留配对、权限、控制、异常身份与保存阻断断言，无conftest修改/skip。

## 验证结果（2026-10-16）

新增五份测试共38例（test_search_protocol/streaming/runtime/settings/stop），另在test_process_settlement加正常EOF后consumer仍处执行期的真实回归。首次红灯为新模块/设置缺失；消费者提前清理、大批搜索误判取消、嵌套组取消未传递、诊断中路径含regex/glob词误判原因，以及获准文件变目录扩大范围，均有先失败再修的回归。类型复核同时以真实write→ls→grep回归保留前序创建缺失目录的能力。

- 原生rg真实搜索超过8MiB文本、2051候选并以多个获准路径一组执行；原生glob/上下文/大小写/字面量/特殊模式、长行片段/超大JSON记录后继续匹配。无Python正文预读/旧扫描上限。
- 原生fd 10.5.0：文件与目录、smart case、路径glob、Git ignore/目录链接不递归、NUL跨块及真实换行文件名。APFS拒绝非法UTF-8文件名，该部分改用显式假协议字节与元数据验证，未声称真实创建该文件成功。
- 正式sync/async图：外部目录整批批准前零搜索进程；隐藏/私密/Deny目标不进入rg正文argv，过滤后再占Find返回名额；同ID回填、默认串并行/必要保存保护不回归。native runner在图loop线程执行。
- 完成EOF/坏或截断记录、协议/输出/argv/诊断/跳过范围预算、超时有/无结果、局部达限后的真实父进程回收；调用内信号不停止整个run。真实受管合成程序验证活动停止/取消，不当成真实rg性能证据；原取消参数保留、已收到部分结果可保留且无下次模型。
- 配置loader→create_builtin_tools→严格公开schema/描述与实际runner设置接通，非法额度回退不丢合法Deny；配置与未来TOML校验通过。保留Bash及12生命周期测试。

本机已有fd位于~/.pi/agent/bin，但不在默认PATH中；只在以下测试命令临时前置，没有安装/下载或修改用户环境文件：

```sh
PATH="/Users/zmw/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q
# 1553 passed in 45.70s
```

定向11文件124 passed in6.62s（/tmp/naiyue-08513-focused-final.log）；kind/原权限与Read/Write定向83 passed，最终全量日志/tmp/naiyue-08513-full.log。前端npm test -- --run为9文件27 passed，npm run build为TypeScript/Vite186模块成功；compileall/pip check通过，263个Python AST/≤500行、当前loader/内置注册和未来TOML/diff检查通过。

证据为macOS真实rg/fd/文件、假模型正式图和显式故障注入；没有Windows/Linux实机、远端供应商模型或沙箱验证。本片没有新增依赖、改真实设置/环境/模型配置、提交或push。下一步[085-14双Shell审阅稿](085-14-dual-shell.md)，不自动实施。
