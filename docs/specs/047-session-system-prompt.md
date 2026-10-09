# Spec 047：基础系统提示词与会话工作区指令

状态：2026-10-03，已按 TDD 实施并完成本机确定性验证。

## 当前数据流与问题

CLI 用 `system_prompt=''` 装配一个共享 `create_agent`。`SessionService` 为每个会话保存可信工作区，并在每次 run 中选择模型，但模型请求没有该工作区的项目指令。同一个 Agent 服务不同工作区时，不能在创建 Agent 时把某个工作区的文件固定成所有会话的系统提示词。

Pi `a69bef789` 的 `AgentSession._rebuildSystemPrompt → buildSystemPrompt` 组合基础文字、实际工具、项目指令、Skill 索引和工作目录；同一目录的文件选择顺序把 `AGENTS.md` 排在 `CLAUDE.md` 前。本项目借鉴分层装配，不复制 Pi 文档清单或宣称尚未接通的 Skill 读取能力。

## 本切片契约

- CLI 把用户定稿的三段文字作为基础系统提示词传给现有 `build_local_application(..., system_prompt=...)`，不修改该公开签名。其他调用者传入的自定义提示词原样保留；项目指令按会话动态追加。
- 应用层只在会话工作区根目录按 `AGENTS.md`、`CLAUDE.md` 的顺序选择第一个存在的文件；两者都没有或会话没有工作区时，不附加项目指令。即使优先文件为空、格式错误或读取失败，也不悄悄回退到次级文件。
- 每次普通 run 和审批恢复 run 开始时各读取一次，并在该 run 的所有模型调用期间冻结快照；同一共享 Agent 的不同会话不能看到对方的项目指令。文件内容不放进 checkpoint、日志或工具参数。
- 文件按严格 UTF-8 读取，允许 UTF-8 BOM；正文上限 64 KiB。解析后的目标必须仍是当前工作区内的普通非隐藏文件；不可读、超限、越界或解码失败均在模型调用前终止本次 run，记录不含正文的错误类型，并释放会话 busy 状态。提示词文件不是权限授予；工具现有审批与路径校验保持不变。
- 运行层只接收应用层提供的本 run 项目指令，附到当前 `ModelRequest.system_message`；没有项目指令时保持原请求。Steering/Follow-up 在同一 run 内看到同一快照。运行层不查找文件名、不读取工作区。
- Skill 发现/懒加载和额外工具概览不在本切片。当前受限 `read` 不能通读隐藏 Skill 目录，未有安全加载入口前不能把不可读取的 Skill 列入模型提示词。上下文 token 观测和压缩另片处理。

## 影响文件与验证

- 新增 `application/project_instructions.py` 保存用户确认的基础文字及有界文件加载；新增 `agent_runtime/project_prompt.py` 只负责按会话装配模型请求。
- 修改 `application/__init__.py` 在普通/审批恢复 run 冻结并清除快照，注入运行层所需的解析回调；修改 `agent_runtime/__init__.py` 的私有装配路径；修改 `application/cli.py` 传入基础文字。公开函数签名不变。
- 先写确定性失败测试：CLI 无工作区仍收到基础文字；AGENTS/CLAUDE 优先级、无文件、不同工作区隔离；同一 run 文件改动后第二次模型调用仍见初始快照、下一 run 可见新内容；审批恢复重新读取；越界、超限、解码失败不调用模型且 busy 释放；日志不含正文。测试经真实 `create_agent` 与假模型观察请求输入，不依赖网络。
- 运行定向和全量 pytest、compileall、pip check；验证代码文件低于 500 行。真实供应商如何服从项目指令、Skill 加载、生产级防路径竞态与业务效果不由此切片证明。

## 实施与证据

- 基线 `.venv/bin/python -m pytest -q` 为 `418 passed in 15.01s`。新增测试先因 `application.project_instructions` 缺失在收集阶段退出 2。最小实现后，新测试 7 例通过；两处旧 CLI 测试仍预期空系统提示词，按本次明确变更更新为用户定稿文字。最终定向 `23 passed in 1.05s`，全量 `425 passed in 14.69s`。
- `.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` 退出 0；`.venv/bin/python -m pip check` 输出 `No broken requirements found.`；`git diff --check` 退出 0，但当前仓库文件未跟踪，该命令不覆盖未跟踪内容。生产 Python 文件最大为 `application/__init__.py` 的 467 行，均低于 500 行。独立脚本核对 `BASE_SYSTEM_PROMPT` 与用户三段原文逐字相等，输出 `approved_prompt_exact_match=True`。
- 假模型经真实 `create_agent` 记录模型输入，验证无工作区 CLI 有基础文字、双工作区隔离和 AGENTS 优先、同一 run 两次模型调用使用冻结快照、下一 run 重新加载、审批恢复下一 run 重新加载、失败文件不调用模型且会话 busy 释放。日志断言无合成私有正文。无真实供应商提示词效果或 Skill 懒加载验证；64 KiB 上限仍不等于模型剩余上下文预算，目标解析与打开间仍有竞态。
