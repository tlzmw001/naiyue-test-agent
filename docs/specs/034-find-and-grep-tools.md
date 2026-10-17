# Spec 034：`find` 与 `grep` 两种只读搜索工具

版本边界：本片保存原实施时点；[085-13](085-13-search-tools.md)已接Grep/Find流式rg/fd并移除旧搜索入口，Bash同步薄桥迁移为run_async_sync，正常consumer等待与取消协调以13为准。当前下一步是[085-14双Shell](085-14-dual-shell.md)审阅。

状态：2026-10-02，已完成本地实现、接线、确定性回归与一次真实模型工具链路探针。用户要求一个较大切片同时完成两种工具，并确认遵守 `.gitignore`、以本机已有 `rg` 为显式运行前提；不宣称两个工具已达到 Pi 的全部行为。

## 当前链路与参考事实

当前 CLI 带 `--workspace` 时注册 `read_file` 与 `ls`。可信工作区由应用会话按 run 注入；Read 校验文件正文权限，`ls` 校验目录列名权限。工作区外目标由条件 HITL 中断，同一 checkpoint 恢复时按调用 ID 和规范化目标一次性授权。工具级参数/执行失败回填安全且可行动的 `ToolMessage(status='error')`，模型可在下一 turn 调整。

Pi `a69bef789` 的 `packages/coding-agent/src/core/tools/find.ts` 使用 `fd`，按 glob 找文件，默认 1000 条，上限受 50 KiB 输出预算约束；`grep.ts` 使用 `rg --json`，支持正则/字面量、大小写、文件 glob、前后文与默认 100 条匹配，结果含相对文件路径和行号，输出受 50 KiB 限制。两者均使隐藏路径进入发现范围，但遵守 `.gitignore`；`find` 与 `grep` 是独立工具定义，运行时由工具集合注册。本机未安装 `fd`，已安装 `rg 15.2.0`；本次统一用 `rg` 进行可复现的文件发现和内容搜索，不下载二进制、不增加 Python 依赖。AgentX `3fcfa0e` 的 `BuiltInToolRegistry → AbstractBuiltInToolProvider.createTools → executeTools` 提供工具注册、参数解析和错误包装的工程参照；已核对的内置工具没有本项目可直接复用的 Python 文件搜索与工作区审批实现。

## 行为契约

`find(pattern, path='.', limit=1000)`：`pattern` 为非空 glob，`path` 必须是目录，`limit` 为正整数且最多 2000。相对路径以可信工作区解析；`..` 拒绝。工作区内直接发现名称，工作区外目录按 `ls` 的逐目标审批；只返回相对搜索目录的文件名，包含隐藏名称但遵守 `.gitignore`，不读取文件正文。无命中返回 `No files found matching pattern`。结果最多 `limit` 条、正文最多 50 KiB，并明确提示截断。缺少 `rg`、无效 glob、路径/审批/I/O 故障各返回安全工具错误。文件枚举先让 `rg --files` 应用忽略规则，再在 Python 对名字匹配 glob：本机探针发现直接传 `rg --glob '*.md'` 会使被 `.gitignore` 排除的同后缀文件重新出现，不能采用。

`grep(pattern, path='.', glob=None, ignoreCase=False, literal=False, context=0, limit=100)`：`pattern` 为非空字符串；正则为默认，`literal=True` 用固定字符串；`context` 是 0–10，`limit` 是 1–1000。`path` 可为工作区内目录或 Read 可读的单文件。目录搜索按 `.gitignore` 发现候选文件，**逐文件用 Read 的 `classify_read_target` 再过滤**：`.env`、其他敏感隐藏项、`.pem/.key`、越界符号链接等文件内容不可搜索；普通 `.gitignore`、`.github/` 内允许文件可以搜索。工作区外普通单文件可沿用 Read 的逐目标审批；工作区外目录不提供一次性批量正文批准，本片返回安全错误并指引改为具体文件。结果为 `相对路径:行号: 内容`，前后文用 `相对路径-行号- 内容`，匹配数默认 100、输出正文最多 50 KiB、单行显示最多 2000 字符；无命中返回 `No matches found`。非法正则提示修正模式或使用 `literal=True`，不回显模式或底层 stderr。

两者 schema 严格且模型看不到 `ToolRuntime`。文件名和匹配文本的控制字符要转义以避免伪造工具结果行；不回显未经处理的路径、原始异常、模型参数到错误消息或日志。`rg` 以 argv 列表调用，`shell=False`、有超时，路径与表达式都放在 `--` 后或明确选项值中。Read/ls 的公开签名保持；`resume_agent_run_in_workspace` 的既有 `approved_reads` 参数名兼容沿用，内部单次 grant 可授权这两个新只读动作。没有 `rg` 时两个工具都回填可行动错误，Agent 不因工具局部失败退出。LangGraph 中断/停止不可被普通异常处理吞掉。

本次 `grep` 为同步受控搜索：每个候选文件先按 Read 权限与 UTF-8/非文本规则检查，单文件上限 8 MiB、单次已检查正文上限 32 MiB、候选文件上限 2000、整体约 20 秒；超出预算返回安全错误。目录中跳过不可读/过大文件时，结果明确附跳过数量，不把局部结果默称完整。`rg --max-columns-preview` 与工具输出格式将展示长行截到约 2000 字符，并提示用 `read_file` 查看原文；正则由 `rg` 解析，即使当前没有候选文件也先验证语法。`find` 的 `rg --files` 输出最大按 8 MiB 校验，但当前 `subprocess.run` 会先接收输出再检查，这还不是对极大目录进程内存的严格上限。

## 修改范围和接线

- 新建 `agent_runtime/tools/find.py` 与 `grep.py`，各有固定工作区版与会话版工具、schema、路径分类/审批回调、固定安全错误；复用现有可信根、Read 文件许可和一次性 grant。不增加环境/配置文件、不升级依赖。
- `agent_runtime/tools/__init__.py` 导出工厂；`agent_runtime/__init__.py` 在同一 HITL 配置注册会话版 `find`/`grep`；`application/__init__.py` 将两种新动作纳入待审批调用与目标核对；CLI 显式工作区注册四个工具，不改变无工作区模式。
- `tests/` 从工具直接调用、假模型运行与 CLI 模拟三层验证。同步 docs/handoff.md、future-work.md、project-plan.md 与 README 导航。

## 验收与证据边界

红灯测试覆盖 glob/路径/大小写/正则/字面量/前后文、`.gitignore`、隐藏名称可见与隐藏正文不可搜、非法参数/正则、无命中、条数/字节限制、缺少 `rg`、目录与单文件审批、拒绝/目标变更、Read/ls/find/grep 混合待审批、同 ID 工具错误进入下一模型 turn、CLI 注册。再跑定向与全量 pytest、compileall 和 pip check。真实模型主动选工具、超大仓库的搜索开销、跨重启恢复、TOCTOU 与更复杂的 `.gitignore` 边界单独标记未验证；不把测试全绿等同于生产成熟。

## 已执行验证

- 红灯：三个新增测试模块因新工具不存在而出现 3 个预期导入错误。实现后的检查又发现无候选文件时非法正则被误报无匹配、跳过非文本文件未提示、Python 3.12 `PurePath.match` 对多层 `**` 与常见 globstar 语义不一致、无效 glob 提示不准确；分别补失败测试并改生产代码。
- `.venv/bin/python -m pytest -q` → `296 passed in 4.42s`；`.venv/bin/python -m compileall -q naiyue_test_agent tests scripts` → 退出 0；`.venv/bin/python -m pip check` → `No broken requirements found.`。工具、假模型运行、四工具同批 HITL 恢复及 CLI 注册均在测试中实际接线。
- 真实模型探针：使用本地已配置的 `heiyu-gpt-5.5`，仅向模型提供会话版 `find` 与 `grep`，要求在当前仓库定位 `README.md` 并搜索标题。观测 `status=PASS`，工具顺序 `find,grep`，两条 `ToolMessage.status=success`，未请求审批，末条消息为 AI。探针只输出工具名和状态，不输出配置/凭证；这证明该请求的真实模型工具选择和回填，未评分最终答案质量，也不是稳定成功率或其他供应商证据。
- 剩余边界：`find` 只发现文件名，尚不输出目录项；与 Pi 的 `fd` 引擎和所有 glob 边界并非完全等价。`rg` 为外部运行前提，未在用户独立终端或打包环境验证其可获得性。目录扫描/`rg --files` 大输出的内存峰值、复杂忽略规则、并发路径替换与重启后审批均未做专项验收。
