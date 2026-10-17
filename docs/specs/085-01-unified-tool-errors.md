# Spec085-01：工具错误统一反馈

版本边界：下文保留该片实施时点；Bash旧Unknown/私有runner/线程及名字保护已在[085-12](085-12-process-lifecycle.md)迁移删除。当前下一步为[085-13搜索](085-13-search-tools.md)审阅，不能据本片历史下一步判断现状。

日期：2026-10-16。状态：**用户审阅后授权实施，已完成本片本机验收。** 属于[总Spec085第15节](085-tool-system-rebuild.md#15-实施顺序与验收入口)第1片的第一小步；旧Spec084保留历史证据。下文为本片实现合同，实际结果见第8节；后续小片仍须先展示再实现。

## 1. 本步及后续顺序

本步只调整错误反馈归属：工具识别原因并raise ToolException，官方ToolErrorMiddleware生成错误ToolMessage；普通文件/自定义叶子工具的意外异常也转消息。同步删除WriteOutcomeUnknown及被替代的内部消费者。

总Spec第1片内部细分：085-01错误反馈→085-02工具/执行目录与依赖迁移→085-03 ToolSpec/Registry/settings及启动接线。之后仍按总表第2至7片：动态工具→权限/审批→文件能力→图片→双Shell→终检/Windows复盘。后续小Spec轮到时再细化，不建占位产品。

相对前六章：沿用create_agent、原BaseTool、wrap_tool_call、ToolMessage配对、批次及IO settle；新增一个同步错误回调及官方middleware接线。本步不改变参数、权限判断、HITL时机、调度算法、数据库或配置。当前审批metadata仍有生产消费者，等权限片替代后删除；工具目录下一步才搬迁，不同时改位置和行为。

## 2. 改前改后调用链

当前：具体异常→工具转ToolException→各BaseTool.handle_tool_error→各_safe_*回调/白名单→ToolMessage；意外异常有的被工具兜底，有的上抛并终止批次。

目标进入顺序，返回反向：

```text
ToolNode
 → ToolBatchMiddleware.wrap_tool_call / awrap_tool_call
 → observe_tool_dispatch / aobserve_tool_dispatch
 → 当前串并行调度
 → _record_tool_call（guard、日志、异步await_settled）
 → 官方ToolErrorMiddleware【新增】
 → BaseTool校验/执行
 → read/_read、write/_write等现有IO
```

ToolError在调度内侧，普通异常先转成结果再返回计划，不能先让plan.failed=True再在外层补消息；在guard内侧，截断/不匹配仍走未执行路径。ToolError没有after_model，原HITL→批次准备顺序不变。

## 3. 新回调和装配怎么写

新增`agent_runtime/tool_execution/feedback.py::tool_error_feedback(error, request)`。以下是拟实现的决策核心，非当前产品：

```python
def tool_error_feedback(error, request):
    if isinstance(error, (
        BashOutcomeUnknown, ToolBatchSchedulingError,
        ModelResponseRejected, ContextCompactionError,
    )):
        return None
    if isinstance(error, ToolException):
        content = str(error)
    elif request.tool_call['name'] == 'bash':
        return None
    else:
        content = (
            f'Tool execution failed ({safe_error_type(error)}). '
            'No normal result is available. '
            'Use the available context to decide the next step.'
        )
    return content
```

实际函数在返回转换内容前记录一次tool_error_converted：复用event_label/safe_error_type，附工具名、tool_call_id、可用session/run ID、异常及直接cause安全类型；不记录参数、正文、原始异常消息或堆栈，不重发工具开始/完成事件。诊断投递的普通Exception仅记录tool_error_diagnostics_failed，不覆盖已准备的反馈；此局部观察隔离不捕获控制信号。上段只展示决策，不是省略诊断要求。

ToolException正文是作者主动提供的公开提示，不扫描白名单。普通异常只反馈安全类型及固定说明；不自动重试。GraphBubbleUp由官方先行raise，UserStop/CancelledError保持控制；调度、模型门禁、上下文和必要保存故障仍归原层。

**Bash阶段边界**：旧runner混合了普通超时、输出不完整和回收异常，本步只把其明确ToolException接入公共转换；BashOutcomeUnknown及其他意外Bash异常仍上抛。名称分支只保护尚在使用的旧进程正文，在双Shell片随旧runner删除；不设置ToolError静态tools名单，不加metadata开关。最终双Shell按085区分ProcessOutcome/ProcessCleanupError后统一接入。

修改`agent_runtime.__init__._build_runtime_agent`：

```python
for item in tools:
    if item.handle_tool_error:
        raise ValueError(
            'Use ToolException for public feedback; '
            'handle_tool_error must be disabled.'
        )

# 原middleware列表中，在_record_tool_call之后加入：
ToolErrorMiddleware(on_error=tool_error_feedback)
```

不复制工具、不覆盖调用方字段、不缓存旧回调。同步/异步共用此同步回调。空tools也固定接线，需验证无工具正常回答及未知名称不可执行；包装hook可能创建ToolNode，不沿用旧084“空工具不装”的候选。动态登记在后片接入，此处不使用会遗漏新名字的启动过滤名单。

## 4. 工具正文及具体文件改动

下表tools仍指当前agent_runtime/tools；本步不搬目录。

| 文件/方法 | 修改 |
|---|---|
| read._read、ls._list_directory、find._find_files、grep._grep_files | 保留Unicode/IO/路径/glob等具体原因→ToolException；删顶层普通except Exception兜底，意外异常交公共层；Grep逐文件处理等业务分支保留 |
| 六个文件工具工厂/格式器 | 删SAFE_*_ERRORS、_safe_*_error及handle_tool_error；_set_safe_*只剩参数提示时直接赋handle_validation_error，不留无用途转发；仍使用的提示常量保留 |
| write._replace_temp | os.replace抛OSError时改为raise ToolException('File write outcome is unknown; inspect the file before another write.') from error；原锁、快照、原子替换、临时文件finally与提交阶段日志保留 |
| write/edit的create_session_*_tool | 删WriteOutcomeUnknown捕获/重复日志，直接调原_write/_edit；Edit继续复用_replace_temp |
| bash.create_session_bash_tool | 删handle_tool_error=lambda，公共层转换其具体ToolException；参数提示、审批、旧进程及Unknown行为保留 |
| agent_runtime/errors.py::RuntimeErrorPolicy.describe | 删WriteOutcomeUnknown导入/保护分类，保留仍有消费者的BashOutcomeUnknown |
| call_observation.py::observe_tool_dispatch/aobserve_tool_dispatch | 删WriteOutcomeUnknown特判，新写入失败为error_result；保留Bash outcome_unknown、取消/控制/未执行 |
| SessionService._run_steps/_resume_steps | 删WriteOutcomeUnknown显式manual_review_required条件及write_event残留选择；保留partial_tools、外部取消、Bash Unknown保护 |
| application/cli.py::_run_and_print/_resume_and_print | 删WriteOutcomeUnknown导入/catch成员；写入失败走现有工具消息；Bash部分输出/Unknown路径保留 |

参数校验继续由handle_validation_error/ToolNode负责，不强塞入on_error；动态工具runtime注入字段过滤的专门回归属于动态工具片，不能冒称本步已解决。

直接调用BaseTool.invoke/arun时，执行错误现在raise ToolException；统一错误ToolMessage属于Agent入口合同，不保留两条转换路径迁就旧测试。成功返回、工具对象身份和schema保持。固定workspace工厂仅在真实消费者迁移后删除，不在本步扩大长期API承诺。

三种事实分清：ToolMessage.status='error'是工具失败；领域tool_call_finished.outcome='error_result'是已返回错误结果；run可completed，因为模型已处理它。原内部tool_failed日志可以保留，不统一改名；逃出运行边界的错误才触发run_failed。事件字段、前端类型、DB schema不变；框架原生trace从内部handled返回变为抛错后middleware恢复，按此边界验收。

## 5. 三个模拟分支及控制反例

成功：read#r1返回正文→BaseTool构造success→ToolError原样返回→调度前进→观察success→模型继续。

具体失败：非法UTF-8→_read仍raise ToolException(INVALID_ENCODING) from error→公共回调返回原提示→官方生成同name='read'/tool_call_id='r1'/status='error'消息→计划正常结束本项→模型收到错误。原提示逐字保留。

意外失败：工具正文抛KeyError→公共模板→同ID error→模型继续。串行A/B/C中B失败，C仍按原顺序执行；并行批仍等已开始IO收尾，不新增依赖判断。

写提交未确认：os.replace失败→具体ToolException→公共error→模型继续，无自动重试。目标可能已改或未改；分别注入替换前失败/实际替换后抛错，验收真实文件状态。不仅因此锁会话；若随后根必要保存失败，原SessionPersistence仍终止并保护会话。

反例：HITL暂停、停止、外部取消、真实调度/模型/上下文/保存故障保持原控制及异常身份、必要IO settle、受限状态；不能从工具体伪抛一种错误来代替真实所属层故障验证。

## 6. 范围、TDD与验收

新增2生产文件：agent_runtime/tool_execution/__init__.py（导出真实回调）、feedback.py。修改12生产文件：runtime/__init__、call_observation、errors；read/ls/find/grep/write/edit/bash；application/session_service、cli。共14文件，均服务同一错误链；无新依赖、服务、凭证、配置或数据迁移。

新增tests/test_tool_error_feedback.py。至少read/ls/find/grep四组call_*辅助现在直接invoke；新增tests/tool_feedback_helpers.py::invoke_tool_feedback，复用已有ToolCapableFake及真实build_runtime_agent，显式传必要context。修改测试辅助是因为消息转换移到了Agent；不得复制转换实现或mock掉框架。内部IO测试仍可直接验证raise。

已有六文件工具、application_write、host_bash、批次、工具/turn事件、classifier/layered_failure、CLI显示等测试迁移旧断言。将“普通RuntimeError终止”改为获批新行为时，补真正机制故障的原异常断言；保留参数/权限/路径/正文/顺序/ID/保存/观察，不放宽、删失败用例或skip。

| 输入 | 必须证据 |
|---|---|
| 正常/具体失败/自定义ToolException | 原对象、schema、具体提示、ID/name不变；主动公开文本不再被旧白名单替换 |
| 普通RuntimeError/KeyError，同步/异步 | 不泄原异常正文；后继串行项及模型继续，计划不标failed |
| write/edit提交前/后失败 | error配对、真实文件状态、cause及阶段诊断；下一会话请求可进入，无自动重放 |
| 审批/停止/取消/真实机制故障 | 原身份/控制及必要IO settle，原来必须保护的状态仍保护 |
| Bash明确失败/旧Unknown/意外故障 | 具体提示公共转换，其余原样上抛；进程/部分输出保护保持 |
| tools=[]/未知名/旧handle_tool_error配置 | 无工具正常；未知名不执行；旧配置装配报错且对象没被修改 |
| 日志/观察 | 每调用开始结束配对；error_result与run终局正确；观察故障不改业务，测试末显式核对检查器 |

实施顺序：先新行为测试并观察预期红灯→接回调/装配→迁工具→清WriteUnknown消费者/旧测试→定向及全量回归；实际红绿结果见第8节。

```sh
.venv/bin/python -m pytest -q tests/test_tool_error_feedback.py tests/test_async_tool_batch.py tests/test_tool_batch_scheduling.py tests/test_application_write.py tests/test_host_bash.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q naiyue_test_agent scripts tests
.venv/bin/python -m pip check
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

终检rg生产/scripts/tests：WriteOutcomeUnknown、六组SAFE_*及旧格式器无消费者；BashUnknown和参数校验保留。生产文件≤500行，README/handoff/future-work同步当前入口，历史lesson只注明替代边界，不改用户掌握记录。无真实模型请求、配置/.env/数据库改动或提交/push。

## 7. 依据与当前证据

当前产品db87428及未提交课堂/设计文档；已沿真实装配、工具、观察、SessionService/CLI追链。本机LangChain1.4.2/tool_error.py支持回调None重抛、同步回调供异步、GraphBubbleUp传播；无需升级。

Pi a69bef789的executePreparedToolCall在工具外捕获并生成isError结果；AgentX 3fcfa0e的AbstractBuiltInToolProvider.executeTools在doExecute外处理具体/普通异常。仅参照公共边界，不据此证明全部故障能安全继续，不照搬原始异常正文。

[官方Tool error说明](https://docs.langchain.com/oss/python/langchain/middleware/built-in#tool-error)与本机源码一致：返回内容生成error、返回None传播、不自动重试。实现证据限下面的确定性本机验收，无真实模型或Windows运行证据。

## 8. 实施结果与增量复盘

- 基线：本轮改前全量1070 passed in 35.03s。首轮新行为测试在校正空工具假模型输入后为9 failed/8 passed：异常未转换、串行后项终止及旧handle配置未拒绝；接线后17 passed。另补Write/Edit替换前/后同步/异步八组；诊断故障两组先2 failed，再修局部投递边界转绿。新文件共27测试，不把后补测试冒称全都先经历红灯。
- 最终：`.venv/bin/python -m pytest -q --tb=short`为1097 passed in 35.08s；新文件27 passed in 0.79s；前端9文件27 passed。compileall退出0，pip check为No broken requirements found，TypeScript/Vite构建退出0。全量结果保留全部原测试数量，合同变化按本Spec迁移。
- 测试迁移：四种文件消息辅助经真实Agent；普通错误断言改成error/继续。仍验证原异常/恢复视图/兄弟IO收尾的测试，改在ToolError外侧真实运行dispatch注入故障，保留原身份与状态断言。旧类型保护单测使用仍在生产消费的BashOutcomeUnknown；未保留废弃Write类型。
- 当前主链只新增公共回调与官方wrapper；guard、调度、after_model审批、IO settle均在原位置。Write提交前后真实临时文件与cause已断言；普通工具已开始后用户停止时，保留其真实error结果且不再调用模型，不能改写成成功或未执行。
- 旧WriteOutcomeUnknown、六组白名单/格式器在生产/scripts/tests已无消费者；原BaseTool未复制/改写。Bash Unknown/意外故障、固定workspace工厂、审批metadata仍有实际消费者，按后片迁移，不是新增兼容壳。
- 只修改本片14生产文件及必要测试/文档。没有改依赖、真实配置/数据库、发模型请求或提交/push；下一步只展示085-02目录/依赖迁移方案，尚未实施。
