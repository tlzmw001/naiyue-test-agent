# 104-03：跨会话组合验收与限流故障隔离

日期：2026-10-18。状态：**已实施并验收**。依赖[104-01](104-01-application-concurrency.md)、[104-02](104-02-web-session-concurrency.md)。本片不包含供应商请求、发布或新产品重试。

## 1. 这一片补什么证据

104-01验证应用/持久化合同，104-02验证Web接线与范围订阅。104-03把这些能力放入同一组可重复场景，另用真实TCP/SSE和服务进程重开补传输/生命周期证据；不重做AgentSession/ActiveRun、权限或事件系统。已有底层用例继续复用，不把逐个通过当成组合验收已通过。

## 2. 拟新增与修改文件

| 文件 | 拟做内容 |
|---|---|
| tests/test_cross_session_integration.py | 真实Web/共享图/临时SQLite的多根、根子审批、控制、必要保存组合；按sid事件屏障，工具副作用账本证明不重复 |
| tests/test_concurrent_rate_limits.py | 沿现有SDK/框架错误分类注入429/ModelRateLimitError，覆盖根、子与Auto的既定不同结果，失败不影响健康会话，不增加重试策略 |
| scripts/probe_web_concurrency.py | 参照既有probe_web/probe_web_history，新增本片离线worker/catalog/run_probe；复用_launch/_stop/_until，临时服务端口0、独立进程、合成模型、真实SQLite及HTTP/SSE |
| tests/test_web_tcp_probe.py | 调用本片有界离线探针，检查结构化结果和退出码；不启动外部供应商 |
| 当前Spec/地图/交接 | 记录组合事实、调用数、必要缺陷与限制；104三片及106 R1收尾，P1只保留基线 |

原则上不新增生产类/字段/配置或数据库表。只在组合用例实际暴露104闭环缺陷时，先红灯、更新本Spec，再做对应生产修复；超过合同范围先讨论。测试文件超过500行按场景分文件，不修改全局conftest。

## 3. 场景与断言

1. A/B根并发，A依次根待批→恢复→子待批，B持续运行；A子审批在原父任务继续。A暂停保存后让C占空位，再批准A可明确拒绝；停止/Steer/Follow-up和草稿归属保持。已批准的真实Write/Read副作用次数以账本核对，恢复和重连不重放。
2. A必要保存被事件挡住时仍占额度，B可完成保存；满额C无模型/工具/checkpoint写。A保存失败后只A受阻，B状态/归档正确。关闭等到根/子/保存及所有Web producer结算，异常身份保持。
3. A/B共享目标写入：在现有目标占用边界用事件固定交错，另一方得到busy ToolMessage，原持有者可提交；不同目标可完成。继续使用既有锁/匹配/提交逻辑，不增加排队、worktree或自动合并。
4. [106 R1](106-concurrency-events-rate-limits-and-observation.md#42-104-03离线验收)：根429是失败终局，子429按既有报告合同返回父模型，Auto审查429回Ask而非Allow；各场景另有健康B完成。模型/SDK等待中的停止和保存顺序复用现有取消合同，不把限流失败伪装成普通成功工具消息。注入调用是离线逻辑调用，不宣称已测SDK真实HTTP尝试或网关RPM/TPM。
5. 实际TCP：A/B SSE同时有各自delta；断开A的显示连接不停止A，B继续。按run控制A、切回读取A；重复请求只返回原回执。成功保存后关闭第一个服务，再用同库启动第二个服务核对两个会话/档案/子记录及恢复门禁，读取不调用旧模型/工具。
6. 结束根审批跨重开与运行中旧子审批分别沿原092合同；不声称任意子节点原样续跑。离线硬退出场景优先复用既有恢复探针，必要时仅加组合断言，不能为了测试自动重放未知副作用。

拟有界探针的入口链：run_probe → _launch(worker_module=本片模块) → _worker建立临时应用 → 同时POST两sid/分别消费SSE → 控制/审批/等待 → 关闭 → 第二进程重开 → 只读校验/显式新输入。报告包括身份与顺序、离线逻辑调用/工具次数、恢复状态和失败原因，不含正文/凭证；默认0次真实供应商请求，模型调用只按固定场景且显式计数，无开放循环。

## 4. 验收与收尾

本轮固定实施合同：组合用例把根Write审批、子Read审批、满额拒绝、定向停止及必要保存屏障接在同一次Web/SQLite生命周期中，重开后只读根/子档案，核对写入提交次数。另用真实图验证同目标busy及不同目标可提交。已有092/105/104的崩溃恢复、关闭/保存故障与异常身份用例作为回归集合复用，不另造恢复机制。

限流注入覆盖官方SDK的429及框架ModelRateLimitError：根保留原异常，子反馈工具失败，Auto快审/详审回Ask，每例有独立健康会话；取消等待仍走现有停止链。注入统计是逻辑调用，不假装测试了SDK内部重试。

TCP探针预算固定为7次离线模型调用、1次真实Write、1次真实Read、0次供应商请求。首进程A/B先分别输出delta并等待控制，A被停止、B继续进入根Write审批，批准后进入子Read审批，最后完成；第2进程只读两会话和子记录并显式给B发一次新输入。停止A保留Steering/Follow-up，不执行这些保留项；重复POST与重连不增加调用。探针用临时文件事件驱动放行B，测试期限只保护探针，不成为产品超时。

先写确定性组合/限流测试；现有行为若通过，标为补证，不伪造红灯。实际TCP探针采用进程就绪/事件同步及整体测试保护期限，期限只限探针，不成为产品执行超时。遇必要缺陷另补稳定红灯，不能以放宽断言或删原检查收束。

专项及TCP后跑完整后端、前端、构建、分层/500行/链接/diff检查。通过只能证明本机离线组合与传输，不替代其他供应商、实网限流、Windows/Linux原机或业务质量验收。

104收尾后按既定顺序讨论M6→持久调用用量→持久诊断/交付缓存预算；checkpoint修剪、项目侧模型/工具安全重试继续延后。

## 5. 实施中复现的必要修复

新限流组合测试复现：SubagentModelFailureMiddleware已识别模型限流并原样上抛；ainvoke_subagent_step生成具体ToolException时未附带分类，子RunObservation读不到该次run的描述，结束事件降为unknown。模型调用失败事件及父工具提示已有正确分类，因此仅修转换点：复用attach_failure_description，给新ToolException附带model来源、既定category/reason、ToolException类型及当前child run_id，保留原异常cause。无需新类、槽、重试或父任务控制。回归同时断言根异常身份、子结束分类、父error工具消息及B继续。官方ChatOpenAI把SDK异常转换为OpenAIRateLimitError后才跨项目模型边界；离线注入沿该当前类型，不把跳过适配器的原始SDK异常当真实链路。

## 6. 实际结果与104收尾

新增2份组合/限流测试、1份TCP脚本及原TCP测试中的1个入口，共12项新增验收。真实图、工具和临时SQLite没有替换为假Agent；无conftest或全局测试设施修改。新组合行为通过属于补证；子模型结束事件分类丢失由回归用例复现后修复，不能将早期测试构造/事件字段错误记为产品红灯。

- 主组合：B运行期间，A根Write待批保存后释放；C占空位使A批准被拒且未消费决定；C停止保留自己的Steer/Follow-up；A重新批准后提交一次外部文件，进入子Read待批并在原父job继续。A必要保存被屏障挡住时仍busy，B可完成保存。收尾后重开3会话及子档案，只读不执行模型/工具，原文件提交账本仍为一次。
- 文件组合：A持有shared.txt提交边界，B收到busy error ToolMessage且未覆盖；C可完成other.txt；放行A后原拥有者完成。3会话各两次离线调用。
- 限流组合：OpenAIRateLimitError/ModelRateLimitError各覆盖根、子、Auto快审/详审；根原异常身份保持，子error消息及结束事件保留分类，Auto回Ask、批准后执行且不重审。另有等待中停止、B限流、C正常完成。每例核对独立持久化/活动归属、次数和日志不含合成私密标记，无项目重试。
- TCP：两个独立Uvicorn进程先后打开同一临时库；A/B均在结束前输出各自delta，断连不取消，A控制不影响B；B根Write审批的新run与子Read审批的原run分别正确，重复请求不增加调用。第2进程浏览两会话/子档案不调用模型，显式给B新输入才调用；固定7次离线模型、1次真实Write提交、1次真实Read、0次供应商请求。macOS临时路径账本使用真实目标路径匹配，不把路径别名造成漏计当成产品问题。
- 保存故障/异常身份/关闭全量等待、根审批跨重开、旧子任务中断及真实硬退出沿既有104/105/092/101回归复用；没有新增任意子审批跨重启续跑能力。

验证命令和本轮实际结果：

| 命令 | 实际结果 |
|---|---|
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q tests/test_cross_session_integration.py tests/test_concurrent_rate_limits.py tests/test_web_tcp_probe.py` | 15 passed in13.00s，/tmp/naiyue-10403-focused.log |
| `PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` | 2524 passed in110.02s，/tmp/naiyue-10403-backend.log |
| frontend目录 `npm test -- --run` | 27文件189 passed，/tmp/naiyue-10403-frontend.log |
| frontend目录 `npm run build` | TypeScript/Vite210模块成功，/tmp/naiyue-10403-build.log |
| compileall、pip check、AST/500行/分层 | 406份Python、67份前端源文件通过，core反向导入application为0，No broken requirements found |

架构/安全复核分别独立复跑12项新增验收，12 passed in4.38s/4.17s，无确认遗留。原始脏树保留，未改依赖/真实配置/用户库，未提交或push；新生产改动仅subagents.py转换点。500行/链接/diff检查通过。本机离线证据不代表Windows/Linux原机、真实SDK内部重试/429或其他供应商支持；104-02既有SSE reader清理告警仍是独立限制，本片未触碰UI。

104三片和106 E1/R1离线部分完成；P1只有基线。下一步讨论[Spec107/M6草案](107-model-request-prompt-cache.md)，再按已确认顺序讨论持久调用用量、持久诊断/交付缓存预算。checkpoint修剪及项目侧模型/工具安全重试继续延期。
