# Spec 017：最小本地 CLI 接线

状态：2026-09-30，已按本 Spec 实施。前置：[Spec 016](016-local-application-composition.md) 的 `build_local_application` 与进程内会话服务。本切片是应用入口诊断，不是通用测试 Agent 或 Pi 式运行中控制的完成声明。

## 当前数据流与问题

当前只有脚本各自装配或直接调用模型做受限探针；`LocalApplication` 已配对目录、会话服务、Agent 和内存 checkpointer，但没有用户可连续输入的入口。新 CLI 只调用这一个应用对象，不再自行装配 Agent、访问内部 `thread_id` 或重写循环。

## 输入、输出与失败边界

- 命令：`.venv/bin/python -m naiyue_test_agent.application.cli --config <模型配置路径>`；配置路径必须显式提供。启动时加载一次目录并用配置的默认模型创建一个进程内会话，显示当前模型 ID，不输出凭证、内部 thread ID 或原始配置。
- 普通非空输入作为一条新用户消息调用 `app.run(session_id, [('user', text)])`；只打印本轮最后一条 `AIMessage` 的文本。消息历史由 checkpointer 读取，不把旧历史再次手动拼进输入。空白行不请求模型。
- `:model <model_id>` 调用 `app.select_model`；成功显示已选模型 ID，下一次模型调用才使用它。缺少/未知模型 ID 给出安全提示，不改变已有选型。`:quit` 或输入 EOF 在两轮之间正常退出。其他冒号命令给出“未知命令”，不送给模型。
- 配置加载失败、运行失败或最终结果缺少可展示的模型文本时返回/显示安全失败，不打印异常正文、提示词、工具参数、密钥或原始响应。运行失败由当前运行层日志记录脱敏事件；CLI 自身只记录异常类型。一次模型失败后 CLI 保留会话，允许下一条输入继续尝试；初始化失败直接退出非零。
- 仍为同步 `invoke`：模型正在调用期间 CLI 不读取新输入；`:quit` 不是取消活动模型请求。不增加通用文件工具、业务提示词、RAG、Web、运行中 steering/follow-up、真实停止/取消或持久化。工具列表为空、系统提示词为空仅表示此次诊断没有测试策略，不作为未来业务默认策略。

## 文件与验证

- 新增 `naiyue_test_agent/application/cli.py`：本地终端适配，调用现有 `build_local_application`；不修改该函数或下层公开 API。
- 新增 `tests/test_application_cli.py`：先写失败测试并确认失败原因是 CLI 缺失。假目录/假模型通过真实 `LocalApplication` 和 `create_agent` 走两轮输入与 A→B 切换；另测未知模型、空白输入、初始化失败和模型失败正文脱敏。CLI 输入输出用内存流控制，测试不调用网络或读取 `.env`。
- 更新 README、路线图、后续清单、交接与学习记录，注明 CLI 同步边界以及下一阶段回到运行层补 Pi loop 控制语义。

验收：定向与全量 pytest、`compileall`、`pip check` 通过；组件 L1/L2/L3 要证明 CLI → 应用组合入口 → SessionService → 运行层 → `create_agent`。完成确定性验证后，使用现有 `.env/models.json` 进行一次只含合成文本、无工具的真实模型入口验证；不编辑配置文件，记录命令、结果、供应商证据边界和费用/次数范围。所有新增文件不超过 500 行。

## 实施与实际结果

新增 `application/cli.py` 和 `tests/test_application_cli.py`。TDD 红灯命令 `.venv/bin/python -m pytest -q tests/test_application_cli.py` 在收集阶段报 `ImportError: cannot import name 'cli'`，退出码 2。最小实现后的首轮定向测试有 3 例通过、1 例因测试预期遗漏当前 LangChain 注入的空 `SystemMessage` 而失败；随后把断言修正为包含该真实输入，并未改变运行逻辑或放宽历史顺序检查。最终定向测试 `6 passed in 0.54s`；全量与构建结果见交接记录。

使用已有私有配置进行真实入口验证，未修改 `.env`：

```bash
printf 'Reply with only ALPHA.\nReply with only BETA.\n:quit\n' | LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m naiyue_test_agent.application.cli --config .env/models.json
```

退出码 0；实际输出依次为 `当前模型：heiyu-gpt-5.5`、`ALPHA`、`BETA`，共两次无工具模型调用。CLI 真实入口证据只说明该网关在此配置下完成两轮请求；不能独立证明网关后端模型身份、历史理解质量、取消/steering 或测试业务能力。完整提示词已由该命令中的合成文字给出；凭证未写入日志或文档。
