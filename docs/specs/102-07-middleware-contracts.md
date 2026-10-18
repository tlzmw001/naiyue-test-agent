# Spec102-07：middleware顺序说明与生产链验收

日期：2026-10-18。状态：已实施并通过完整本机验收；第1—3节保留实施合同，第4—5节为实际证据。承接[主Spec第11节](102-session-state-lifecycle-refactor.md#11-已确认jmiddleware顺序注释与行为验收)及[102-06第5节准备方案](102-06-web-setting-errors.md#5-下一片102-07准备方案供审阅尚未实施)。

## 1. 当前链与范围

现有agent_runtime._build_runtime_agent注册共同middleware列表；build_runtime_agent低层入口不启用交互控制/turn，_assemble_interactive_runtime根入口启用停止/turn并增加根Steering/Follow-up；build_subagent_runtime沿用共同列表，使用独立角色工具/提示词/模型、压缩/溢出恢复、可选模型预算及子模型失败识别。图创建与每次图运行是不同阶段，不以注册顺序直接猜全部执行顺序。

本片保留生产列表及所有行为，仅在agent_runtime/__init__.py的三个装配位置及agent_runtime/subagents.py的build_subagent_runtime补简短说明。不新增生产类/方法/字段/模块，不改公开API、依赖、配置、DB、模型/工具调用、权限或恢复协议。实际行为若违约，先复现并报告，不为通过验收静默重排或扩大范围。

主Spec接近500行，本片合同和结果独立存放。tests/test_agent_middleware_order.py现有245行、只验证A/B框架规则，保留；生产场景还需权限、审批、工具、turn和模型输入，多于剩余空间，因此新增tests/test_runtime_middleware_contracts.py，并复用现有测试提供的假模型/应用入口及资源清理。仅该文件局部追踪fixture在调用原方法前后记录事实并保留hook_config元数据，不修改conftest或公共helper，不替代真实流水线，不在可降级观察回调内做断言。

## 2. 关键合同与具体数据

当前安装LangChain 1.4.2 / LangGraph 1.2.12。本机factory及现有框架运行测试确认：before节点按注册正向、after节点反向；首wrap在外层，返回反向。jump、中断和异常允许跳过后续节点，不把注释写成每次必经清单。

| 环节 | 实际生产者/消费者与验收 |
|---|---|
| 模型输入 | select_model选当前模型；ToolSet装配当前工具/提示词；ModelInput转换模型输入；observe_model_request接到上述结果。核对选模、工具、prompt、消息与实际模型收到的一致，不用只比类名列表 |
| 权限与暂停 | PermissionMiddleware.aafter_model输出tool_execution，含batch_id/mode/calls；例如calls=[{id:'ask', decision:'ask'}, {id:'ordinary', decision:'allow'}]。BatchApprovalMiddleware收到该结果后中断，同批两项工具体均未执行 |
| 恢复与计划 | 批准恢复沿保存决定走审批节点，原权限/Auto不重审；ToolBatch.aafter_model随后建立原批计划。拒绝由官方审批写配对ToolMessage，未执行的工具不能产生真实效果 |
| 工具包装 | ToolSet选择原批tool → ToolBatch调度 → 调用观察 → ToolError → ExecutionAuthorization._authorize → 工具体。工具检查_execution_permit；ask消费一次grant，原错误反馈保留消息/调用ID配对 |
| turn | 其余after_model检查后TurnBoundary.mark_ready只设置检查通过。TurnObserver.try_finish仍需AI已可见、当前调用结果配对齐全；不得以AI展示或ready标记直接判完成 |
| 停止与失败 | StopBoundary可先于权限检查jump_to=end；ToolException转为错误ToolMessage继续模型，PermissionMechanismError等机制故障保留原异常身份。子图仍走既有父子审批路由 |

正常例子：arun提出ask＋ordinary → 权限准备 → 固定批审批暂停，无工具效果、无批次执行计划完成/turn ready → approve恢复（不重审） → 建计划/mark_ready → 调度/核对permit/工具体 → 配对结果 → 工具turn完成 → 最终文本turn。拒绝例子：reject写ask错误结果、ask工具体不进入，其余合法项照原批计划执行，模型收到配对反馈继续。应用保存/释放busy后才发run完成，仍属应用层。

## 3. 测试与实施顺序

先记录原框架、权限/Auto/错误/turn/停止/子审批相关基线。新增生产组合测试，用包装原方法的局部追踪记录真实顺序/数据，所有断言在测试主体执行：暂停前不执行整批、恢复准备次数、批次与ready先后、执行permit、错误回填及主异常身份；另验证最终请求所用模型/工具/prompt。复用已有未配对结果、停止、Auto不重审、子批准/拒绝和必要保存测试，不复制整套已有场景。

本片没有新增运行行为；新增行为断言若当前实现即通过，记录原行为成立，不把后补验收或注释称为TDD红灯。不为制造红灯先破坏生产顺序。补装配说明后重跑专项、完整后端/前端/构建、compileall/pip check/diff/AST/500行/新增链接检查，独立只读复核只覆盖本片增量。假模型/临时文件/库，不发送供应商请求、不修改真实配置/用户库、不提交push。

完成时同步主Spec当前状态与索引、README、handoff、路线/文件地图、future-work。Spec102已确认的7片收尾；原问题第6/9项已随101完成，第7跨会话并发仍待独立决定，不标作本片完成。103剩余UI、usage/诊断/缓存/checkpoint修剪、PyPI与Windows/Linux原机仍独立。

## 4. 生产链证据与已执行专项

相关基线85 passed in2.29s（/tmp/naiyue-10207-baseline.log）。新增8项生产合同在加注释之前已8 passed in0.82s（/tmp/naiyue-10207-contracts-before-comments.log），表明原生产行为已满足，不宣称有功能修复或TDD红灯。最初错误探针的runtime注入schema不符合当前注册合同，已改为调用既有build_tool_schemas提供public/execution继承模型；没有放宽注册校验、修改公共helper或旧断言。

| 新用例 | 真实调用及检查结果 |
|---|---|
| 根approve/reject（2项） | Permission完成后进入固定审批节点；暂停时同批效果为空，无plan/ready/工具包装；恢复后只有approval重入，原批Permission一次。plan完成先于ready，ready时turn仍active；实际执行依次进入ToolSet/调度/观察/ToolError/授权，permit绑定原batch/call。拒绝项不进工具体，配对结果及turn完成事件先于下一模型 |
| 工具反馈/机制故障（2项） | 工具体实际require_tool_permit，通过后才抛原异常；ToolException由真实ToolError回填后下一模型继续，PermissionMechanismError穿过所有包装仍为原对象，失败turn不冒充完成 |
| 最终请求（1项） | 会话从A切B、工具从unused切probe，真实项目约束加入prompt；B不支持图片，ModelInput先替换请求中的像素块，观察器记录与B真正收到的输入一致，checkpoint结果仍保留canonical图片块。原A无调用、日志无像素，无观察降级 |
| 停止跳转（1项） | 模型回复验收后发真实request_stop，StopBoundary直接结束；Permission/审批/plan/工具包装均不进入，原调用生成配对user_stop消息，工具无效果、无悬挂审批 |
| 子approve/reject（2项） | 真实subagent委派及子独立路径下重复确认权限→审批→plan→ready、许可与工具包装顺序；子整批等待，批准/拒绝反馈配对，根继续且父子均只调用模型两次，无新恢复协议 |

两个生产文件只增加注释，AST对比与分片基线完全一致。注释区分standalone/root/child、框架节点与wrap嵌套、可跳过后续hook、ready与完成；未重排列表或新增生产入口。修改后初次专项110 passed in2.41s（/tmp/naiyue-10207-focused.log），覆盖原框架顺序、Auto恢复不重审、未配对不完成、原保存/失败/停止与子审批边界。

全量首轮6 failed/2409 passed in102.13s（/tmp/naiyue-10207-backend.log），均为新探针漏记observation.enter/exit。按调试协议用test_async_failures＋新合同文件复现6 failed/3 passed（/tmp/naiyue-10207-order-repro-observer.log）：旧故障注入在_record_tool_call实例上恢复绑定方法，覆盖了新探针对类方法的追踪，真实观察日志和执行仍存在。本片只修局部探针：对共享观察器包装实际装配实例的当前方法；其余按类方法包装，原参数原样委托一次。不修改旧测试/公共helper、产品观察代码、断言或排序；修正后需重跑该顺序及全量。

架构/安全独立只读复核无本片确证发现：架构独立8 passed in0.80s；安全的生产合同/权限/子恢复/ToolError专项46 passed in1.61s。391份Python AST/单文件500行、compileall/pip check/diff检查通过。最终全量结果见第5节。

## 5. 全量验收与Spec102收尾（2026-10-18）

追踪接线修正后，原顺序复现9 passed in1.80s（/tmp/naiyue-10207-order-fixed.log），最终专项111 passed in3.41s（/tmp/naiyue-10207-focused-final.log）。安全独立复核再次按该顺序验证9 passed in1.81s，架构确认类/实例绑定均委托原方法一次，未放宽任何顺序/效果/配对/异常身份断言。首轮失败日志保留，不把测试接线缺陷描述为生产缺陷。

最终完整后端 **2415 passed in100.02s**（/tmp/naiyue-10207-backend-final.log）；前端 **26文件186 passed**（/tmp/naiyue-10207-frontend.log）；TypeScript/Vite **210模块成功**（/tmp/naiyue-10207-build.log）。实际命令：

```bash
PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m compileall -q naiyue_test_agent tests scripts docs/examples
.venv/bin/python -m pip check
git diff --check
```

391份Python AST/单文件500行及两处生产AST不变检查通过；当前__init__.py204行、subagents.py165行、新测试331行，主Spec保持500行。compileall、pip check、diff及新增文档链接检查通过。只读复核无本片确证遗留发现。无新生产模块/类/方法/配置/依赖/数据结构，原编译图、调用/错误/权限/审批/停止/保存合同和实际消费者保持；没有创建平行旧入口或退役代码。

已同步README、主Spec当前表格/第27节、102-06历史下一片说明、handoff、计划/路线与085/086地图。当前已确认的102-01—07全部收尾，主Spec第6/9项已随101完成；原问题第7跨会话并发仍待独立决策。本片不代表Windows/Linux原机、真实供应商或业务效果新增验收，不新增用户掌握声明。

保留原100/101/102前六片/103脏树，未提交push；未修改用户配置/用户库、未发供应商请求。下一步回到Spec103未完成项，先明确一个切片并讲方案，尚未授权自动实施其余UI。usage/诊断/缓存/checkpoint修剪、PyPI交付及平台原机验证仍分别独立。
