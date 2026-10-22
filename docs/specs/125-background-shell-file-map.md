# Spec125：实施文件、接线与迁移地图

2026-10-21，属于[主Spec](125-background-shell-tasks.md)。125-01—06已完成本机实现与验收（主Spec第12—17节），[05合同](125-05-background-management.md)已接CLI/Web管理，[06结果](125-06-background-acceptance.md)含组合/性能及统一辅助侧栏。按六片实施；无新增服务、依赖、真实配置编辑或DB schema变更。

## 1. 125-01：执行期限与删除工作区归档总额度

| 文件 | 具体改动/实际消费者 |
| --- | --- |
| `naiyue_test_agent/shell_task_settings.py`（新增） | ShellTaskSettings首先只加入max_execution_seconds=0；后续片在有真实消费者时加入日志/等待字段，避免先声明未接线能力 |
| `naiyue_test_agent/settings.py` | AppSettings加载shell_tasks；非法上限不能回退成无限；清理tool_output字段/默认对象，识别公开旧总量键并告警忽略 |
| `naiyue_test_agent/tools/shell.py` | 一处计算模型timeout与配置上限的有效值，再构造ProcessRequest；保留permit、解释器、cwd/env与同步桥 |
| `naiyue_test_agent/tools/shell.py::ShellInput` | Shell自有timeout字段描述配置上限；共享schemas.py保持，Find/Grep/AITest仍是原不限时语义 |
| `naiyue_test_agent/tools/output.py` | 删除_workspace_limit及写入时全目录求和；只按单次剩余额度写；所有权/安全打开/手动清理快照保留 |
| `naiyue_test_agent/tools/settings.py`、`tools/__init__.py` | 删除仅承载总额度的ToolOutputSettings及导出，不留空壳；普通Shell/aitest单次字段保持 |
| `naiyue_test_agent/tools/{bash,powershell,builtin,aitest_kit,process_execution}.py` | 迁移output_settings无用途参数；shell_tasks显式传给双Shell而非搜索/aitest |
| `naiyue_test_agent/mcp_client/{resources,tools,results,refresh}.py` | 移除总额度对象传递；server.max_response_bytes/max_result_bytes继续约束单次MCP结果与归档；历史workspace_limit原因仍可投影 |
| `naiyue_test_agent/application/{async_application,async_persistent,cli}.py`、`web/app.py` | 生产装配读取真实shell_tasks并传入双Shell；删除所有总额度空对象接线 |
| `scripts/provider_acceptance_cases.py`及搜索命中的脚本/测试 | 迁移原内部参数；保留实际工具、授权和结果断言，不为旧测试保留死参数 |

验证：新增test_shell_task_settings.py；迁移test_shell_settings/test_shell_output/test_host_bash等总额度断言为不同调用独立容量断言，保留UTF-8/归档失败/权限/停止行为；MCP结果和aitest专项回归。

实际代码结构变更时同步两份示例TOML、README/工具说明；只改仓库说明，不编辑用户settings.toml。公开旧键只在配置输入边界给迁移提示，不能隐藏执行限额或忽略其他未知键。

## 2. 125-02：显式后台、模型管理工具及应用关闭

| 文件 | 具体改动/实际消费者 |
| --- | --- |
| `naiyue_test_agent/application/background_tasks.py`（新增） | BackgroundProcessManager：跟踪操作/后台记录、独立worker、状态/去重/归属、list/output/stop、删除和输出清理准入、关闭收尾；超500行时按真实输出/控制职责拆，不提前建通用平台 |
| `naiyue_test_agent/tools/background_tasks.py`（新增） | 窄ProcessTaskPort协议和task_list/task_output/task_stop三个ToolSpec；root/permit/scope检查，按既有StructuredTool模式注册 |
| `naiyue_test_agent/tools/process_execution.py` | 把现有共享输出收集/归档收尾收敛为可持有的ProcessCapture；前台包装和manager共同使用；同进程任务不复制整个runtime |
| `naiyue_test_agent/tools/processes.py` | 内部启动确认信号；唯一run/spawn/watch/settle/terminate继续服务当前调用方，不复制POSIX/Windows清理 |
| `naiyue_test_agent/tools/output.py` | 显式后台强制归档、查询当前配额事实、只控制单任务容量；输出/清理状态线程安全 |
| `naiyue_test_agent/shell_task_settings.py`、`tools/shell.py` | 此片加入64MiB后台日志字段并消费；background严格bool默认false，主/子入口检查，已授权请求一次交接 |
| `naiyue_test_agent/agent_runtime/{run_services,invocation_context}.py` | 复用原作用域投影一个窄任务端口，不新增ContextVar；启动worker时不继承它 |
| `naiyue_test_agent/application/{session_service,session_controls,agent_session,run_events,session_execution}.py` | Service构造manager，root执行bind时传端口；子可借同一窄端口但业务拒绝后台，不给其任意App对象 |
| `naiyue_test_agent/application/{async_application,async_persistent,session_persistence}.py` | 新管理方法、关闭门禁与收尾顺序；会话删除检查在真实业务删除路径内，不能只在Web按钮禁止 |
| `naiyue_test_agent/application/cli.py` | 原:outputs clear经cli_async桥调用应用preview/clear及共享活动writer准入；该文件当前497行，新后台交互解析留到05独立模块 |
| `naiyue_test_agent/tools/builtin.py`及默认工具装配 | 登记三工具，保留Bash/PowerShell默认opt-in；角色实际工具集合校验及控制工具可见性同步 |

测试新增test_background_tasks.py、test_background_task_lifecycle.py、test_background_tools.py；真实服务握手后确认同会话第二轮可用，使用已有权限/图助手而非复制框架。拒绝/跨会话/停止/异常/启动在途/关闭/保存失败/清理准入均有断言。

已有Web删除错误映射/API说明及前端提示同步session_background_tasks_active；现有记住批准规则按background标志隔离，前台授权不自动变为后台授权。

此片不为后台另造Graph，不保存整个run服务或许可，不新建SQLite表。只运行task_stop不能直接标finished；必须核对实际runner/管道/consumer。

## 3. 125-03：前台自动转换

| 文件 | 具体改动/实际消费者 |
| --- | --- |
| `naiyue_test_agent/shell_task_settings.py` | 此片加入foreground_wait_seconds=120并接线 |
| `naiyue_test_agent/tools/shell.py` | root前台提交给同一manager，工具等待完成/F/原停止；child仍用前台路径；不使用wait_for取消进程worker |
| `naiyue_test_agent/application/background_tasks.py` | 前台候选操作跟踪、原子转换、停止与自然退出优先、后台回执；未转换操作不展示为后台任务 |
| `naiyue_test_agent/tools/{process_execution,output}.py` | 转换激活归档/变更单次模式额度，累计量不重置；工具等待方拥有ToolProgress，转换后不再向旧run发送；旧截断事实不清除 |
| `naiyue_test_agent/tools/{processes,process_protocol}.py` | 同一执行计时原点回调和自然退出回调；不复制spawn/watch/settle算法 |
| 双Shell工具描述/审批展示消费者 | 明确有限前台等待与总执行期限，不能把转后台称为命令成功；审批展示自动转换规则 |

新增test_background_promotion.py，扩展test_process_execution_timeouts/test_shell_lifecycle：可控时钟测期限，实际进程记录启动次数/PID一致，交接前后stop/cancel竞争、前台额度缺口、子Agent永不转换、一次ToolMessage与writer终止断言。

02完成后的03实施顺序：

1. ShellTaskSettings加入foreground_wait_seconds=120并接CLI/Web真实装配。前台等待阈值和执行期限共用runner开始执行的单调时钟原点；现有ProcessRequest.on_started仅表示spawn已确认，03需补计时原点信号，不能把等待spawn的时间漏掉。
2. 主Agent前台入口从启动起交给同一manager跟踪候选操作，等待方负责原run停止和ToolProgress。正常结束仍按原前台结果返回；候选操作尚未转换时不出现在后台列表。子Agent/AITest继续用现有前台执行包装。
3. 到F时先仲裁自然退出、原run停止和执行期限；T<=F不会转换，T>F或无限且仍在运行才交接。等待不能取消worker；spawn未确认时允许交接为starting，不能伪造running。显式background=true继续走02路径，不启用F。
4. 转换沿同一个ProcessCapture激活归档并调整单任务额度，累计量和runner原deadline均不重置。旧前台额度已丢弃输出时，历史缺口与当前能否继续写分开表示，不能因保留旧不完整原因而立即误停；后台额度较小时按已写量决定停止，不裁剪文件。
5. 用可控clock验证T<F/T=F/T>F/无限、停止/自然退出竞争；用真实进程验证只spawn一次、PID/字节累计连续、交接后原run不再收到进度且停止不串任务。03仍不接04通知或05界面。

## 4. 125-04：下一次用户运行的通知与预算

| 文件 | 具体改动/实际消费者 |
| --- | --- |
| `naiyue_test_agent/shell_task_settings.py`、应用构建器 | max_notice_items=20/max_notice_bytes=8192，严格正整数，传入同一manager；真实设置不改 |
| `naiyue_test_agent/application/background_tasks.py` | 每会话终态待提示、不可变快照与版本回执；按shell_tasks.max_notice_items/max_notice_bytes有界，默认20项/8KiB，不读日志 |
| `naiyue_test_agent/application/{session_execution,session_state,agent_session,run_events,session_context}.py` | 新用户root运行准备快照；审批/压缩/预览不消费；主/子prompt部分显式分离 |
| `naiyue_test_agent/tools/background_tasks.py` | 不可变BackgroundNoticeSnapshot和同步只读快照/版本确认窄协议；不读输出正文 |
| `naiyue_test_agent/agent_runtime/{run_services,invocation_context}.py` | 复用原RunServices携带本轮快照，只投影匹配session/workspace/run的root作用域；不新增ContextVar |
| `naiyue_test_agent/agent_runtime/session_port.py`、`tool_execution/toolset.py` | prompt parts读取携带agent_path，不把root通知传入子Agent |
| `naiyue_test_agent/agent_runtime/{context_usage,request_preparation,call_observation}.py` | background_tasks动态SystemMessage段加入预算/压缩与parts；有效响应接受后通过窄端口确认快照；失败保留，不让可选回执覆盖主结果 |
| `frontend/src/components/ContextPanel.tsx` | 新prompt部分中文标签，与实际估算一致 |

新增test_background_notices.py、test_background_notice_delivery.py、test_background_notice_boundaries.py，扩展run_services及context-inspector前端测试；迁移两个测试/一个现用probe的resolve_prompt_parts回调至agent_path合同，保留原断言：无用户输入时不增加调用数；下一轮可查输出；请求失败/拒绝响应/用户停止保留通知，成功仅确认实际包含的版本；超批/超预算剩余不丢；context预览无副作用；child/审批恢复/手动压缩不误消费。

## 5. 125-05：CLI/Web管理与独立状态显示

| 文件 | 具体改动/实际消费者 |
| --- | --- |
| `naiyue_test_agent/application/cli_background.py`（新增） | 只负责三条交互命令解析与展示；已有cli.py调用，避免497行原文件继续膨胀 |
| `naiyue_test_agent/application/cli_async.py`、`cli.py` | 桥接app的list/output/stop，运行中仍可操作；EOF/quit沿应用关闭清理 |
| `naiyue_test_agent/web/background_tasks.py`（新增） | 任务路由/安全投影，挂到原app鉴权；和service共用application，不复制执行 |
| `naiyue_test_agent/web/app.py`及新增任务路由 | app装配；任务专属错误映射/帧预算集中到background_tasks.py，复用service的application/settings及原WebError，无须扩展445行的service或通用errors |
| `frontend/src/{api,types,uiSettings}.ts` | 新任务类型/API/版本查询与3秒只读轮询参数；无任意文件路径API |
| `frontend/src/useBackgroundTasks.ts`（新增） | 管应用总览/选中输出/失败/停止/AbortController，页面不可见或卸载时停止轮询 |
| `frontend/src/components/BackgroundTasks.tsx`及`.module.css`（新增） | 有限列表/所属会话/状态/尾部/停止，独立于聊天run；空白/失败/进行中分开，正文按文本渲染 |
| `frontend/src/App.tsx` | 全局后台任务入口及活动数量、完成提示；切会话不销毁后台拥有者，不重设聊天滚动 |

新增test_web_background_tasks.py、test_cli_background_tasks.py及frontend/src/background-tasks.test.tsx：沿原鉴权/作用域；列表分页、终态重复stop、权限/输出缺失/cleanup故障；idle/busy/切会话/禁用Shell仍能用户停止；轮询与完成提示不创建run、不使用失效SSE、不吞错误。

## 6. 125-06：组合、性能与发行入口

当前已实现，按用户补充并入ActivitySidebar统一图标/三个页签及上下文图标直达；App及三个内容组件/样式/调用测试同步迁移，删除旧框架，不增加独立执行拥有者。源码验收与源外worker共用scripts/release_background_smoke.py，tests/test_background_task_integration.py和test_background_benchmark.py实际消费，明确主异常与检查器失败。

- `scripts/benchmark_background_tasks.py`（新增）：只用临时工作区/库/固定模型、独立本机子进程，记录基线/后台负载/输出洪峰/停止关闭数据；不写真实配置或用户库。
- `tests/test_background_task_integration.py`（新增）：正式图的服务启动→健康检查→下一轮请求→停止，以及后台测试→下一轮状态提示→task_output。
- 扩展已有发布安装验收的工具发现/后台回执/停止/恢复边界：应用重启不恢复任务，旧日志仍可读；不把内存注册表当checkpoint持久任务。
- 四目标沿现有CI/构建流程，Mac本机与Windows/Linux实际运行分列；不升级依赖、不改版本、不发布。
- 更新README、相关工具说明、两份示例配置、future-work和handoff，仅记录本片真实结果；不改关键知识点或学习掌握记录。

## 7. 迁移清理与验收清单

- 对每个新增字段追生产者、实际消费者、返回投影、测试和当前文档；新配置在哪片进入，就在哪片接入真实行为。
- 删除工作区总归档额度的运行逻辑/Settings空壳/装配传递/旧断言；历史已保存结果的workspace_limit投影仍有读历史消费者，故保留。
- 保留ProcessRunner同步桥给解释器探测及仍有消费者的同步工具；本片不扩成全项目async-only清理。
- 不保留前台/后台两套spawn/kill/文本规范化算法；保留不同拥有者的等待、控制、通知职责。
- 测试不依赖紧sleep；普通观察/通知故障不覆盖执行异常，必要输出/进程清理失败有显式事实。
- 最脆弱前提是当前受管进程组能覆盖常规服务子进程；任意daemon/setsid脱离和应用被强杀不在保证内，不能据本片宣称完整沙箱或崩溃后无残留。
- 每片给L1文件存在、L2真实实现、L3启动/运行接线证据；产品功能未接入时不以结构/文档测试称完成。

地图初稿时尚未开始产品修改；当前以顶部及主Spec实际结果为准，六片已完成本机验收，其他原机/真实模型效果独立待验。按用户指令继续，不自动扩大范围。
