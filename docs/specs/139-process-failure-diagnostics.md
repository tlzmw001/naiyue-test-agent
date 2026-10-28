# Spec139：进程与沙箱失败诊断

日期：2026-10-28。用户已批准审查中的日志补齐，要求区分日志、持久诊断与模型工具反馈。实施中；不发布、不改变用户配置。

## 1. 当前链路与信息归属

正常：Agent 工具 → Host/Docker ProcessPort → native runner/envd operation → 有界输出与退出状态 → format_process_outcome → ToolMessage → 模型下一轮。日志不是任务输出，不能把 stdout/stderr 从工具结果移走。

失败：DockerCLI/GitRepository 收集进程结果 → 固定 SandboxError/GitInputError → 应用 operation/lifecycle 保存 unknown/stale → 工具反馈或运行失败事件。现有文本日志只有类型/退出码；部分 extra 字段被默认 Formatter 省略。envd worker 执行后保存终态失败，再保存失败状态也可能失败；本片只补独立诊断并保留异常/收尾语义。

| 信息 | 去向与合同 |
|---|---|
| 用户命令的输出、非零退出、超时、工具参数/文件错误 | 原工具结果，包括错误 ToolMessage；让模型获得实际结果和已有恢复提示，不新增重试 |
| 进程身份、系统错误码、传输类别、失败阶段、输出字节数 | 安全日志；不把内部 stderr、凭证、环境、绝对路径、异常正文注入模型 |
| run/tool 的状态、outcome、固定 reason、耗时、关联 ID | 原运行事件和既有持久诊断，不增加平行事件库或 DB schema |
| 副作用未知、清理未确认、必要保存失败 | 保持原 unknown、原 ID 查询和受保护异常合同；不因补日志允许模型重放 |
| 记录日志自身失败 | 不覆盖业务返回、原异常、取消和停止语义 |

参考：本机 Pi a69bef789 的 bash.ts 将输出交工具消费者，agent-loop.ts 将可处理工具异常转换为 isError 结果。AgentX 3fcfa0e 的 TaskExecutionHandler.executeSubTask 将失败保存为任务状态并发前端消息；这不是本项目工具错误回填实现，不能直接照搬。Claude 官方 https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls 支持 tool_result/is_error；本机 LangChain 1.4.2 的 ToolErrorMiddleware 配合本项目 tool_error_feedback 决定是否生成错误 ToolMessage，ProcessCleanupError 仍抛给运行层。

## 2. 最小改动

- 复用现有 logger、错误事件与状态记录。新增小型标准库诊断模块，因为数个独立边界重复需要安全 OS/SQLite/异常组元数据；该模块只投影事实，不选择恢复策略、不持有运行上下文。
- Docker/Git：固定命令类别、transport、termination/exit、耗时、超时、stderr 字节数和有限已知类别。stderr 只在有界内存中分类，不记录正文；未知保留 unclassified，check=False 的预期 Git 探针不升级为错误。
- 应用环境 operations/results/delivery/service/lifecycle：在原失败日志补已有 ID、阶段、固定 code 与系统错误码；watcher 结束明确标识。避免轮询成功日志。
- envd operations：执行与终态保存阶段分开；错误状态二次保存失败单独日志后原样 raise，不更改 journal/锁/活动项清理。
- Host process/shell/background/service launcher：补 OS 错误码、现有 ID、taskkill 返回码；不修改工具反馈正文或异常分类。
- MCP owner/discovery/drain：受限叶子异常类型和系统码、阶段；不输出 SDK/stderr 原文。
- service/web 默认 Formatter 输出经过字段白名单筛选的 application_event/session_event/tool_error，带时间戳。保留默认 WARNING，普通成功日志级别不提升。诊断持久化协议不变。

## 3. 文件范围与接口

生产：diagnostics.py；execution/{docker_cli,git_repository,docker_environment}.py；application/environments/{operations,results,delivery,service,lifecycle}.py；envd/operations.py；tools/{processes,process_execution}.py；application/sessions/background_tasks.py；service/{launcher,__main__}.py；web/__main__.py；mcp_client/{owner,discovery,connections}.py。

测试：对应失败日志回归、格式化输出/脱敏、工具反馈与受保护异常回归。原输入回收补丁保持。

无公开 API 签名、DB、事件 wire schema、依赖、锁顺序或重试规则变化。diagnostics.py 进入 envd 源清单。用户确认以“不需要更换远端镜像”为边界：本地 envd 构建版本记为0.5.4并更新源码哈希，远端继续使用已发布0.5.3/AITest Kit0.6.0镜像；不部署、不替换。控制端诊断随控制端生效，容器内新增日志不会在旧镜像中出现。hello只验证协议和绑定、不绑定包版本，新增旧版握手兼容测试；这不是公司原机实测。

## 4. 验证

先红后绿：不同255/128原因可区分但敏感文本不出现；Git超时可观测且预期探针无噪音；任务/结果关联和阶段；worker两次保存失败保留异常与done状态；taskkill码/MCP异常组/OS码；真实Formatter保留安全extra丢弃正文；模型仍收到命令错误输出，ProcessCleanupError仍停止模型。

最终运行受影响测试、完整后端、前端测试/构建、标准控制端与envd wheel构建/检查、compileall/pip check/diff check。内存故障注入不冒充Windows/公司SSH验收。本轮不改变输入回收行为、不加入手动清理入口、不发布。

## 5. 实施与本机验收

已接线：安全诊断投影/发送、默认文本Formatter、Docker/Git stderr分类与预算计数、任务和生命周期身份/阶段、MCP叶异常、envd二次保存事件。字段与发送保护均仅隔离普通Exception，原BaseException控制信号保持；旧工具反馈、受保护异常、状态保存与查询路径没有迁移。

新增26项：各诊断缺口先复现后转绿；既有模型反馈和旧envd握手作为行为保持验收。新增异常组属性探针最初使pytest自身的异常渲染失败，测试改为在测试体捕获并断言身份后正常复现投影缺口，未改测试基础设施或放宽业务断言。执行源新增一份纯标准库诊断模块，原构建测试58源精确断言同步为59并显式检查diagnostics.py入包；仍逐字节核对全部源和45依赖、拒绝控制端模块。

后端最终收集5787项，与2826＋2920两组及55项门禁的通过节点逐一比对无缺失/失败/skip；最后Git stderr预算计数另以164项全Git相关回归覆盖。前端554和TS/Vite通过。标准build_release macos-arm64、build_envd linux-x86_64成功，两个wheel与当前Python源逐字节一致，Twine/源外安装/compileall/pip/diff/行数检查通过。构建时一次误把frontend/dist传给预打包资源参数被门禁拒绝，纠正为标准无该参数构建成功，没有绕过或修改构建器。证据见build/process-diagnostics-20261028/verification.json及backend-coverage.json。

局限：envd二次保存仍可能使终态未确认，本片只补日志；输入回收Windows PermissionError未修；远端原镜像不包含容器内新增日志。现有0.5.3协议源码与本分支相同，旧版握手验收通过，无公司SSH/真实供应商新请求。未提交、发布、替换镜像。

2026-10-28 发布0.5.5前复核补充：原输入回收诊断直接调用logger，日志sink失败会替换原PermissionError。新增故障注入回归先确认红灯，再改用本片已有safe_log；只隔离日志失败，不改删除动作、异常身份、次数或重试策略。后续发布证据见handoff与0.5.5发行说明。
