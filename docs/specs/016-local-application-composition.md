# Spec 016：进程内应用组合入口

状态：2026-09-30；已按本 Spec 实施。前置：[Spec 014](014-in-memory-session-service.md) 的 `SessionService`、[Spec 015](015-synchronous-tool-events.md) 的同步运行事件和 [模型配置 Spec 004](004-local-model-config-format.md)。

## 当前数据流与问题

目前只有测试代码手工装配 `ModelCatalog → SessionService → build_runtime_agent(checkpointer=InMemorySaver())`。运行层的 `build_configured_runtime_agent` 会自行加载一份模型目录；若未来应用入口又为 `SessionService` 加载另一份，切模型时可能由两份不同快照决定行为。当前 `SessionService.run(agent, ...)` 还允许调用方传入任意 Agent，项目没有一条可复用的应用组合入口保证同一服务、同一模型目录和同一个 checkpointer 被配对。

## 本切片契约

- 新增 `application.build_local_application(config_path, *, tools, system_prompt, response_format)`。调用时只执行一次 `load_model_catalog(config_path)`，不提供隐式配置路径；从同一 `ModelCatalog` 构造 `SessionService`，创建一个 `InMemorySaver`，使用现有 `build_runtime_agent` 装配 Agent。选模回调为该服务的 `resolve_model`，默认模型对象来自同一目录，运行事件由已有 middleware 记录。函数不发起模型网络请求，不读取别的配置，不改 `.env`。
- 返回一个最小 `LocalApplication` 对象，持有配对好的会话服务与 Agent；对外提供 `default_model_id`、`create_session(model_id)`、`select_model(session_id, model_id)` 和 `run(session_id, messages)`。`run` 内部固定调用所持有的 `SessionService.run(agent, ...)`，入口调用方不再传任意 Agent。`SessionService` 与运行层已有公开签名保持不变。
- `tools`、`system_prompt`、`response_format` 仍由未来 `test_agent` 策略或入口提供；本切片不添加通用文件工具、系统提示词、CLI/Web、日志消费者或身份授权。无持久化：进程退出后会话元数据与 `InMemorySaver` 都消失。外部输入的会话授权在未来 Web 入口另行实现，当前对象只用于可信进程内调用。
- 配置读取/校验失败原样传播，不能返回部分装配对象或静默退回硬编码模型。应用组合中不新增第二套模型选择规则、重试或错误转换。

## 影响文件与验证

- `naiyue_test_agent/application/__init__.py`：在现有应用包内增加最小组合函数和对象，依赖现有 `model_access`、`agent_runtime` 与 LangGraph 的内存 checkpointer；下层不导入应用层。
- 新增 `tests/test_application_composition.py`：先写失败测试。用一次计数的假目录加载和真实 `create_agent` + 假模型确认仅加载一次；同一应用同会话两轮消息看到历史，A→B 选模，另一会话隔离；检查无 `thread_id` 对外暴露、失败配置不继续装配、run/model/tool 现有事件路径可用。避免真实模型或凭证。
- `README.md`、`docs/implementation-roadmap.md`、`docs/future-work.md`、`docs/handoff.md`：记录当前接线证据、限制和下一步。

验收：新测试先因缺少组合入口红灯，再做最小实现；定向和全量 pytest、`compileall`、`pip check` 通过，检查所有修改文件低于 500 行。L3 要证明经**新组合入口**调用，而非继续手工拼装的旧测试；不把假模型结果当成真实供应商或业务质量证据。

## 实施与证据

`application.build_local_application` 加载一份目录，构造服务与一个内存 checkpointer，调用既有 `build_runtime_agent`；`LocalApplication.run` 固定使用这组服务和 Agent。运行层没有反向导入应用层。`record_selection` 在此入口没有第二份会话写入逻辑；已有 middleware 在带 `run_id` 的调用上记录 `model_selected`。

TDD 红灯：`.venv/bin/python -m pytest -q tests/test_application_composition.py` 输出 `2 failed`，两例均因组合函数缺失而失败。实现后同一命令输出 `2 passed in 0.61s`；测试还检查模型事件 A、A、B、A，工具调用 ID 配对和日志不含合成敏感正文。全量及构建检查结果见交接记录。本切片没有真实供应商调用、持久恢复或 CLI/Web 接线。
