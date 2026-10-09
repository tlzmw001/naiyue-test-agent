# Spec 025：CLI 运行中提交消息与完整模型文本

状态：2026-10-01；已按 TDD 实施。范围只涉及本地单进程 CLI，不引入异步、逐 token 流、通用工具或 Web。

## 当前数据流与问题

`CLI → LocalApplication.run → SessionService.run → invoke_agent_run → agent.invoke` 是同步调用。`SessionService` 已有同锁保护的 Steering 和 Follow-up 队列；运行层已在 `before_model`/`after_agent` 消费它们。CLI 当前在主线程等待 `app.run`，期间不能读取终端输入，完成后只显示 `result['messages'][-1]`，因此既无法从终端提交运行中消息，也会漏掉同一 run 的较早模型回答。LangGraph 结果还含历史消息，不能简单打印所有 `AIMessage`。

Pi 当前终端入口在运行中把普通提交交给 Steering，单独的 Follow-up 操作进入另一队列；本项目用 `:follow <文本>` 表达后者。AgentX 当前对话入口没有与这两种 CLI 队列一一对应的公开契约，不据此推定其实现。

## 本切片契约

- 交互式终端（TTY）中，一条普通消息启动运行后，模型调用在工作线程执行，CLI 主线程继续接收输入。运行中普通非空输入调用现有 `app.steer`；`:follow <文本>` 调用现有 `app.follow_up`。两者必须明确提示“已排队”，不把排队当作模型已消费。空文本不入队；`:follow` 缺文本显示用法。空闲时普通文本和 `:follow <文本>` 都启动新运行。
- 运行接收窗口已关闭的竞争下，CLI 不丢已输入文本：等待当前运行结束后把它作为下一次普通输入运行。Steering 与 Follow-up 的本次交付以应用层是否接受为准，不由工作线程 `is_alive()` 单独决定。
- `:model` 保持现有选模能力；运行中的新模型选择影响下一次模型调用，CLI 明确提示该语义。`:quit`/EOF 在活动运行时等待该运行结束后退出，不把退出伪装成活动取消。
- 不接逐 token 流。每次运行成功后，按状态中的出现顺序显示**本次运行新增的每条非空 `AIMessage.text`**；工具调用消息若有文本也显示该文本，工具调用 JSON 和 `ToolMessage` 不冒充回答。使用本次输入的唯一 `HumanMessage.id` 在结果消息中定位边界，避免重放历史和在先前失败后显示旧的部分结果。没有文本显示现有空回答提示。运行失败只显示安全的异常类型，不展示可能含敏感数据的部分结果或异常正文。
- 非 TTY 的管道/脚本输入保持逐轮同步执行，以保证批量输入不会因读取速度被误判成 Steering。两条路径共用运行输出规则。会话、checkpoint、运行层和模型接入层接口不变。
- 这是功能性行式终端，不保证工作线程输出时重绘用户正在编辑的输入行；漂亮的 TUI/逐 token 显示留待独立切片。

## 影响文件与验证

- 修改 `naiyue_test_agent/application/cli.py`：终端输入调度、现有队列方法调用、每次运行的新增模型文本展示；不更改下层公开 API。
- 扩展 `tests/test_application_cli.py`：先写失败测试，以 `threading.Event` 阻塞假模型，证明模型运行中普通消息和 `:follow` 均可提交，并验证输入顺序/输出包含每条新模型文本且不重放上一轮；覆盖关窗竞争、空命令、安全失败和非 TTY 顺序行为。测试不依赖短 sleep 或真实网络。
- 更新 README、`docs/handoff.md`、`docs/future-work.md` 中与 CLI 能力相矛盾的现状；若总体设计的入口决策需要改变，再更新 `docs/project-plan.md`。

验收：新测试先因行为缺失失败；实现后定向与全量 pytest、`compileall`、`pip check` 全部通过。L1/L2/L3 需证明真实 CLI 入口调用 `app.steer`/`app.follow_up` 并在同一 run 的真实 `create_agent` 假模型结果中展示所有新增文本。真实网关中途输入需手工交互验证；若无法做，不把确定性线程测试表述为真实网络证据。

## 实施与实际结果

- 红灯：新增测试第一次运行暴露测试自身对尚未导入的 `cli.sys` 的错误假设，先修正测试装置；再次运行 `7 passed, 2 failed in 6.63s`，实际失败为同步 CLI 无法在阻塞调用期间接收消息、`:follow` 未识别。实现后定向 `10 passed in 0.57s`，全量 `175 passed in 3.75s`；`compileall` 退出 0，`pip check` 为 `No broken requirements found.`。
- L1/L2/L3：TTY 主线程读输入、工作线程执行旧同步 `app.run`；普通文本/`:follow` 分别接入已存在的应用队列。确定性测试让第一轮模型等待事件，确认运行中两类消息均被应用接收、同一次图运行输出三条模型文本且各一次；另测窗口关闭竞争下输入变成下一轮，非 TTY 旧行为仍通过。
- 真实网关交互：在 PTY 运行既有 `--config .env/models.json` 入口，连续输入 `Reply with exactly FIRST.`、`Now reply with exactly SECOND.`、`:follow Reply with exactly THIRD.`；终端先显示 `Steering 已排队` 与 `Follow-up 已排队`，运行结束显示 `SECOND` 与 `THIRD`，随后 `:quit` 退出 0。Steering 在第一次模型调用前已入队，因此没有独立的 `FIRST` 模型响应；这次真实运行验证输入/输出接线，不证明真实工具执行、所有网络时序或回答质量。未修改私有配置、未显示密钥。
- 限制：终端输出不重绘正在编辑的输入行；工作线程不支持活动取消；队列仍仅存在于进程内。`已排队` 不等于模型已消费，失败时本次部分回答不会当作完整成功展示。
