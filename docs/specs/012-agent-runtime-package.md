# Spec 012：Agent 运行层命名与包归类

状态：2026-09-30；结构变更已完成并验证。

## 当前数据流与原因

`agent_runtime.py` 已包含 `RunContext`、按会话选模的 middleware、`build_runtime_agent` 与配置驱动装配函数。`test_agent/discount_case_design.py`、脚本和测试从 `naiyue_test_agent.agent_runtime` 导入这些名字。`create_agent` / LangGraph 拥有模型—工具循环；本项目文件负责框架装配和模型调用边界，并未自写循环。下一阶段的消息、停止、上下文和事件能力仍需按独立切片设计，单文件目前 119 行，尚无需预建空实现模块。

## 本切片契约

- 层名和 Python 包名固定为 `agent_runtime`。避免使用暗示自写运行引擎的 `agent_core`；四层依赖方向仍为 `application → test_agent → agent_runtime → model_access`。
- 将 `naiyue_test_agent/agent_runtime.py` 原样移至 `naiyue_test_agent/agent_runtime/__init__.py`。保留现有 `naiyue_test_agent.agent_runtime` 导入路径、公开函数签名、运行结果、异常和日志字段；不拆分只使用一次的代码，不新增占位模块。
- 只做包归类与文档同步，不实现 steering、follow-up、取消、压缩、事件总线或持久化。它们的职责与优先顺序在架构记录中讨论，进入每项功能时再定接口和验收。

## 影响文件与验证

- 源文件仅做上述移动；已有 `tests/test_agent_runtime.py`、`tests/test_model_access.py`、`tests/test_discount_case_design.py` 是公开导入与行为的回归保护，不改测试断言。
- 同步 `docs/implementation-roadmap.md`、`docs/agent-runtime-architecture-draft.md`、`docs/handoff.md` 中的现状和边界。
- 变更前后运行全量 `.venv/bin/python -m pytest -q`；另运行 `compileall`、`pip check` 和公开导入检查。纯文件移动没有新增行为，不能把已有测试的绿灯伪称为 TDD 红灯→绿灯。

## 实际验证

- 移动前 `.venv/bin/python -m pytest -q`：`84 passed in 3.74s`。
- 原样移动 `agent_runtime.py` 到 `agent_runtime/__init__.py` 后，全量 `.venv/bin/python -m pytest -q`：`84 passed in 3.63s`；`.venv/bin/python -m compileall -q naiyue_test_agent scripts tests` 退出码 0；`.venv/bin/python -m pip check`：`No broken requirements found.`。
- 公开导入探针能从 `naiyue_test_agent.agent_runtime` 导入 `RunContext`、两个装配函数与 middleware 工厂，实际模块位置为 `naiyue_test_agent/agent_runtime/__init__.py`。无运行逻辑、测试断言或依赖版本变化；本切片没有 TDD 红灯，不能宣称新增能力已验证。
