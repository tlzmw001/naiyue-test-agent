# Spec102-06：Web设置操作的局部异常处理

日期：2026-10-18。状态：已实施并通过本机验收，第1—4节保留实施合同，第6节记录结果；第5节保留当时的下一片准备，102-07现已实施，见该片结果。承接[主Spec第26节](102-session-state-lifecycle-refactor.md#26-102-06设置异常映射已实施)。

## 1. 当前链与范围

WebService.select_thinking、select_tools、select_permission_mode各自先调用_session_status做轻量身份/工作区检查，拒绝recovery，再await应用写操作，映射异常，最后返回成功值。三处只有await及异常映射属于同一职责；本片仅合并这部分，不修改应用API、审批/工具生效时点、存储、配置或依赖。

生产代码只改web/service.py，在WebService增加async _apply_setting(operation, *, invalid_code, invalid_event, failure_event)。operation是外层已经确定的应用协程；通过准入后才创建，helper只await一次。不增加生产类/文件、回调、装饰器、任务包装、重试或按操作名解析的注册表。

## 2. 方法与错误合同

| 外层方法/原应用方法 | ValueError的422码 | warning标签 | 其他Exception的error标签 | 成功值 |
|---|---|---|---|---|
| select_thinking / aselect_thinking | thinking_unavailable | web_thinking_selection_invalid | web_thinking_selection_failed | 原model_id/thinking_level |
| select_tools / aset_active_tools | invalid_tool_selection | web_tool_selection_invalid | web_tool_selection_failed | 应用get_active_tools确认值的list |
| select_permission_mode / aset_permission_mode | permission_mode_unavailable | web_permission_mode_unavailable | web_permission_mode_failed | 应用get_permission_mode确认值 |

捕获顺序保持：ValueError先于Exception；ModelConfigError继承ValueError，因此仍按当前操作给422，不改变为model_unavailable。其他Exception只记录指定标签及异常类型，再经原public_error映射；已是WebError时保持对象身份。不输出私有参数、异常正文或traceback。

CancelledError及其他BaseException保持原对象自然上抛，无新shield。前置_session_status、recovery门禁及成功getter均在helper捕获范围外；getter失败不能被记成应用setter失败。select_model原来没有同样的ValueError/日志合同，保持独立。web/settings.py配置对象不在本片范围内。

## 3. 正常与失败调用链

正常：鉴权与请求schema → PATCH tools(active_tools=['read','grep']) → select_tools → _session_status及recovery检查 → 创建aset_active_tools协程 → _apply_setting内await → 原注册表校验/必要保存/状态更新 → 外层get_active_tools → {'active_tools':['read','grep']}。原暂停审批及下一批生效规则保持。

失败：未知工具由原setter抛ValueError → 指定warning → 422 invalid_tool_selection，不回显名称。保存异常 → 指定error标签/类型 → 原public_error的安全码。准入失败不创建setter协程；成功getter失败原样上抛，不误归类。取消不转Web错误，不重试已经开始的写入。

## 4. 测试与实施顺序

复用现有Web控件、推理设置、身份/工作区/恢复、审批保持和持久化测试。新增tests/test_web_setting_errors.py覆盖三项实际接线、参数及一次await、成功确认值、ValueError/ModelConfigError、安全公开映射/日志、WebError身份、取消身份、准入与成功getter边界。新文件是该三项合同的测试，不改conftest或测试基础设施。

先跑现有基线，再写新测试并实际记录红灯；保留旧行为测试结果，缺少helper及三个入口未接线是本片新增结构要求。按合同最小实现后跑专项、完整后端/前端/构建、compileall/pip check/diff与500行检查，独立只读复核仅覆盖本片增量。使用假模型/临时库，不发供应商请求，不修改用户配置/用户库，不提交push。

完成后更新本片结果、主Spec当前状态、交接及当前文件地图；下一片102-07只讲准备方案，不实施。

## 5. 下一片102-07准备方案（供审阅，尚未实施）

本节保留102-06收尾时点；现已按[102-07合同与结果](102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)完成，不再是当前待实施项。

102-07收尾已确认的middleware顺序注释与生产链组合验收，不改变排序，不新增生产类/协议/状态/调度器。当前安装LangChain 1.4.2、LangGraph 1.2.12；本机factory的链构造确认before正向、after反向、首wrapper在外层，现有test_agent_middleware_order已有真实框架运行验证。仅比较类名列表不足以证明生产行为。

准备修改agent_runtime/__init__.py的_build_runtime_agent：在原middleware列表旁写简短约束表；修改agent_runtime/subagents.py的build_subagent_runtime：注明子图沿用共同列表、没有根Steering/Follow-up注入、另有模型预算及模型失败识别。root交互装配、低层build_runtime_agent及child条件项分别注明，不给它们假定相同的hook集合。

测试优先在既有test_agent_middleware_order.py补生产链断言，保留原框架顺序测试；若接近500行则将生产组合用例放独立测试文件并注明职责。复用权限/Auto/批次/turn/停止/子审批现有用例，只有缺少的关键先后与数据证据才新增测试；不建立一套替代生产middleware的模拟流水线，也不修改conftest来伪造轨迹。

正常例子：用户要求读工作区外文件 → 当前模型/工具/prompt与图片请求准备完成 → 请求观察看到最终输入 → 模型提出Read → after_model按反向先准备权限、再走固定批审批节点；暂停时工具体未进入。批准恢复沿保存决定继续 → 批次计划及其余after_model检查 → TurnBoundary.mark_ready只标记检查完成 → ToolSet选原批工具 → 批次调度 → 调用观察 → ToolError边界 → ExecutionAuthorization核对并消费原grant、给permit → 真实工具体 → 配对ToolMessage齐全后TurnObserver.try_finish才完成该工具turn → 下一模型及最终文本turn；应用仍负责必要保存后才报告run完成。注册列表中权限位于审批后面，实际after_model顺序相反。

失败/分支验证：拒绝由官方审批生成配对错误消息且工具未执行；普通ToolException由ToolError回填并允许下一模型，机制异常保留原身份；停止可jump_to=end，不能要求后续每个hook仍出现；未配对工具结果不能完成turn。子图的批准/拒绝仍通过原父子审批路由，不改恢复协议。Auto已准备批次恢复不重审、请求观察使用当前选模/工具/prompt、turn不因仅展示AI消息提前完成，均须有行为断言而非注释声明。

验证以现有用例为基线，先为确实缺少的生产约束补红灯断言，再做注释和必要测试接线；若原行为已通过，则如实记录原行为证据，不制造失败。发现真实违约先最小复现并说明，不能借最后验收片重排列表或改权限合同。最后跑完整后端/前端/构建与架构/安全只读复核，同步主Spec及文件地图，列清102已确认范围完成和仍待决定的跨会话并发。以上仅为下一片准备，本轮不实施。

## 6. 实际结果（2026-10-18）

只修改1个生产文件web/service.py，新增1份测试文件；无新生产类/模块/依赖。_apply_setting由三个真实入口调用，原准入/恢复门禁保留，两个成功getter仍在捕获外，错误码/日志/取消身份均保持；select_model与web/settings.py不改。没有保留三段并行旧处理，也未扩大捕获/重试范围。正常和失败主链见第3节，实际HTTP调用、临时SQLite及异步取消测试已接到相应入口。

既有相关基线11 passed in2.08s。新增62项：正式红灯3 failed/59 passed in1.04s（/tmp/naiyue-10206-red.log），三处失败均为尚未新增_apply_setting；59项确认旧行为合同已成立。最初HTTP测试把既有error.code误写成平铺error，核对app.exception_handler后纠正新断言，再记录正式红灯；没有修改产品wire、旧测试、conftest或helper来制造通过。实施后专项83 passed in2.31s（/tmp/naiyue-10206-focused.log）。取消测试使用Event同步，实际task.cancel进入原setter的finally，原CancelledError对象上抛，成功getter未调用。

完整后端 **2407 passed in102.11s**（/tmp/naiyue-10206-backend.log）；前端 **26文件186 passed**（/tmp/naiyue-10206-frontend.log）；TypeScript/Vite **210模块成功**（/tmp/naiyue-10206-build.log）。命令：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

390份Python AST/单文件500行检查通过；service.py327行，新测试218行。compileall、pip check、diff通过，README/交接/当前路线与文件地图同步。架构/安全独立只读复核未发现本片确证问题，安全另跑设置/边界/恢复专项73 passed in2.39s。保留原脏树，未提交push；无用户配置/用户库或真实供应商请求，未新增Windows/Linux原机、浏览器人工验收或用户掌握证据。

102-06收尾时7片完成6片；102-07现已完成，当前7片均收尾，见[最新结果](102-07-middleware-contracts.md#5-全量验收与spec102收尾2026-10-18)。跨会话并发仍待独立决定，不随本片或顺序验收自动开启。
