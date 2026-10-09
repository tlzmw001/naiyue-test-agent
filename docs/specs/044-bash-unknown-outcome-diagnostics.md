# Spec 044：Bash 未知副作用时保留有界人工诊断

状态：2026-10-02，已按 TDD 实施并通过本机验证。范围来自用户确认：先做超时等未知副作用时的部分输出诊断，并补模型可见的 Bash 参数/输出说明。完整日志保存与回读先按 Pi 源码讨论方案，不在本片实现；运行中展示和活动取消留待工具整体回溯。

## 当前链路与问题

审批后的 `bash` 经 `ToolNode` 调用 `_run_host_command`，两个读取线程把 stdout/stderr 各自末尾 4096 原始字节保存在内存。正常/非零退出由工具结果回填；超时先杀进程组，再直接抛 `BashOutcomeUnknown`。会话服务收到异常后置 `manual_review_required=True`，CLI 只显示固定提示。已捕获的 pytest 诊断随栈帧消失，用户难以人工核对。工具的模型可见 JSON schema 目前也没有说明默认 60 秒超时与仅回填末尾 4096 字节。

## 本片契约

1. `BashOutcomeUnknown` 保持原有异常类型与普通单字符串构造方式，附加只读用途的 `partial_stdout`、`partial_stderr` 与 `output_truncated`，默认均为空/假。异常的 `str(error)`、日志事件和用户固定失败说明不拼入输出正文，避免意外把命令输出写入日志。
2. 在已启动命令的超时、输出读取失败或输出流未关闭路径，先终止/等待现有进程组处理，再从现有有界缓冲区快照构造异常。诊断最多为每个流末尾 4096 原始字节解码后的内容；用现有 UTF-8 边界处理。它可能不完整，不能当作命令成功、失败或未产生副作用的证据。
3. CLI 仅在捕获该异常时，把非空的部分输出作为“已捕获、可能不完整”的人工诊断显示；用 JSON 字符串转义控制字符，避免命令输出改变终端显示。没有部分输出时保留现有固定提示。`SessionService` 仍标记人工核对并拒绝同一会话自动重跑，不能把异常转为普通 `ToolMessage` 让模型自行继续。
4. 模型可见 Bash schema 说明：需人工审批、cwd 是工作区而非隔离、默认 60 秒/最大 300 秒、stdout/stderr 各自只回填末尾 4096 字节且不保存全文。保持 `command`/`timeout` 字段形状和工具公开名称不变。完整日志产物契约留待下一阶段。

## 影响文件与验证

- `agent_runtime/tools/bash.py`：异常携带有界快照，三个未知结果分支接线；更新 Bash 工具描述与参数说明。
- `application/cli.py`：两个入口的未知结果捕获都安全展示非空部分诊断，不记入日志。
- `tests/test_host_bash.py`：先写可复现的失败测试：命令输出标识后超时，断言异常保留诊断、同会话阻断和 CLI 展示；控制字符不能原样进入终端；模型工具 schema 包含预算说明。再做最小实现与回归。
- `README.md`、`docs/future-work.md`、`docs/handoff.md`：同步已验证范围、已知限制与下一步；`docs/关键知识点.md` 不修改。

验证：`.venv/bin/python -m pytest -q tests/test_host_bash.py`、`.venv/bin/python -m pytest -q`、`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts`、`.venv/bin/python -m pip check`。不调用真实模型，不修改 `.env` 或依赖；只用临时目录内已知安全的命令。L1/L2/L3 分别检查代码和文档存在、异常确有有界数据、CLI 与会话阻断接线均有测试证据。

## 实际验证

- 先加 3 个目标测试，定向红灯为 `3 failed, 17 passed in 4.87s`，失败分别是异常无部分输出字段、CLI 不展示、模型 schema 缺说明。最小实现后定向 `20 passed in 4.68s`；再补 stdout/stderr 双流预算样本，最终定向 `21 passed in 5.77s`。
- 全量 `.venv/bin/python -m pytest -q` 为 `399 passed in 12.17s`；`compileall` 退出 0；`pip check` 输出 `No broken requirements found.`。测试使用临时命令和假模型，验证会话仍阻断、异常/日志不含输出正文、CLI 控制字符已转义、每流部分诊断不超过 4096 原始字节对应的尾部。未执行真实模型请求，不能推断供应商端行为。
- L1/L2/L3：`bash.py` 的异常字段及三条未知结果路径、CLI 两个异常入口和模型工具描述均存在且已接线；真实临时命令让超时异常携带输出并在 CLI 显示。由于诊断只保留尾部，不能证明命令的全部执行过程或副作用；完整日志另设计。
