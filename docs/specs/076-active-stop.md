# Spec 076：统一活动停止及 CLI 异步接线

状态：2026-10-08，用户确认实现，已接线；先 Spec、失败测试、实现及回归，最终结果见交接。超过八个文件的跨模块切片。

## 契约与数据流

当前 CLI 输入线程与 run worker 分离，但 worker 调同步应用；after-turn 标志只在轮次边界消费。异步应用已有模型/摘要/工具/保存链路，外部 Task.cancel 仍是 failed/cancelled，线程 IO 必须 settle。改为：CLI 输入 → 应用 request_stop(session_id, run_id 可选) → run 私有控制 → 模型/摘要取消或工具停止/排空 → 图安全结束 → checkpoint 确认 → 元数据提交 → run_finished(active) → 释放会话。

- 对外只保留 request_stop，不留 request_stop_after_turn 别名；旧调用点、测试及事件 stop_mode/boundary 同步迁移为 active。同步库仍可调用，但同步网络只能协作边界停止；正式 CLI 必须调用异步应用，不将同步 SDK 称为活动网络取消。
- run 控制属于 runtime，通过已有 contextvars/可信 context 注入，应用创建/绑定/销毁；供应商模型不持有跨 run 信号。选择后识别BaseChatModel默认线程回退并settle；原生async实现由实际选择模型决定，不以默认模型判断其他会话。私有 Thread Event 用于 Bash，当前 model-handler 子任务注册用于模型、摘要、重试等待；只转换本控制发起的取消，外部取消/真实异常保持原传播。
- 模型 wrapper 外层覆盖选择、prompt、强制摘要与恢复；主动摘要在 before_agent 中独立注册同一控制并跳转 end。用户取消后返回空 ModelResponse+停止状态；最先 after_model hook jump end，跳过审批与调度；HITL准备期间停止则等review线程settle，仅在用户信号存在时将该hook的GraphInterrupt转换为未执行结果与end，不消费授权，必要时补齐本轮已确认 tool_call 的未执行 ToolMessage。不伪造 AIMessage。before_model 即使首次调用也允许停止，不发送排队消息。
- 工具调度获取串行位置/并发名额后再检查信号，未开始调用返回有理由的错误 ToolMessage；已经开始的任意工具默认 settle，不强杀 Python 线程或假定 async 工具安全可取消。Bash 在工具主体读取可信信号，启动前检查、等待中终止进程组并收集输出；已启动 Bash 的副作用不确定仍 BashOutcomeUnknown/failed/人工核对，不自动回滚/重试。
- 用户停止正常收尾为 stopped/active；工具、保存等真实失败仍 failed，unknown 不覆盖。外部 Task.cancel 保持 failed/cancelled。停止时关闭 steering/follow 准入，原队列取出保留给调用方，CLI 显示待发送内容；停止收尾期间新提交内容留在 CLI，不自动启动。提供 :pending 展示和 :send 显式重新提交，避免没有行编辑器时只能抄录。队列不落 DB/日志正文。
- CLI 复用输入/run worker，不重写命令系统：薄同步桥用专属事件循环运行 AsyncApplication/AsyncSqliteSaver；控制通过线程安全提交进入所属 loop；worker 等 Future 时输入线程保持响应。退出 drain 已有 worker 后关闭应用/loop。持久预检库必须先关闭，再由异步上下文重新持有锁并复核恢复，不能双持锁。不新增 UI、字符流打印或多用户/持久多 run 并发。
- 事件继续使用既有类型，不增加事件数量。model/turn/context 观察收口用户取消；控制 applied 边界 active；run_finished 在保存之后。工具未执行事件 reason=user_stop。保持 DB schema1，ready 必须 next 为空、无 task error/interrupt、工具结果配对。

## 参照与实现范围

Pi a69bef789：Agent activeRun 锁、abort signal、await listeners/finishRun/idle；write 等文件操作 settle 后检查取消；TUI Esc 恢复排队消息到编辑器。Pi 没有本项目 checkpoint+SQLite 安全恢复阻断，不能说该检查照搬 Pi。AgentX3fcfa0e interruptSession 关闭 SSE，当前路径未证明底层取消。官方 RunControl drain 只在图 superstep 边界停止，不满足活动请求取消；继续 create_agent/middleware，非手写图。当前版本 empty ModelResponse+after_model jump end 离线探针已得到仅 HumanMessage、next=()、无 task error，须验证整条装配。

新增 runtime/run_control.py、runtime/active_stop.py、application/cli_async.py；修改装配/context、会话控制/观察/异步桥、批次执行/Bash、事件校验/显示、CLI。测试及旧 probe 名称同步迁移；不修改 .env、配置、依赖或关键知识点。同步库公开停止改名是本次用户确认的接口迁移，其余签名保持。

## 验证

- 红灯：等待模型时取消/无假回答/next 空/同会话续聊；摘要和重试等待取消不继续；原始取消与真实故障不被用户信号覆盖。
- 工具：正在写入保持 busy、串行后续/并发等待调用未执行且 ToolMessage 配对；Bash 真实子进程停止/输出/unknown 阻断；停止前/审批边界/跨会话/stale run/幂等/队列保留。
- 持久：stopped 后 safe checkpoint/ready/新进程恢复；保存失败仍 failed/旧指针；保存期间新 run 拒绝。
- CLI：真实 async model hook、停止等待中仍接收输入、保留无自动发送、明确重发、审批/模型切换/持久恢复原回归；非TTY请求顺序维持。
- 命令：`.venv/bin/python -m pytest -q`，`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests`，`.venv/bin/python -m pip check`，`git diff --check`；新增有界真实模型停止 probe 使用现有默认配置，不输出正文/密钥，不验证服务端计算或计费停止。

失败假设：SDK/第三方 async 工具可能不响应取消。默认工具 settle，故不能承诺任意工具固定秒数停止；模型真实探针有父截止，失败显式报告，不升级依赖或用 AttributeError 猜取消。必要的保存收尾不能提前终局。

验证边界：真实网关一次模型增量后取消通过；两个真实独立Python进程的停止/恢复使用离线native模型。SDK mock测试包住真实anyio.sleep，只用Event观察进入30秒backoff后取消，未用短sleep时间断言。历史两条验收按明确的新契约改为首次模型未调用、停止先于审批并回填错误；不是放宽旧断言。

最终验收：全量905 passed in 28.11s；compileall/pip check/diff空白检查与Python500行检查通过。新增23例包含停止配对、线程回退、审批竞态、SDK真实backoff的mock取消、跨进程恢复、CLI重发；不把追加验证全称作TDD。真实默认网关probe一次逻辑调用停止通过（详情/时间边界见handoff219）。
