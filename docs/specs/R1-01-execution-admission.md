# R1-01：执行身份准入与错误分类

日期：2026-10-24。用户已批准本片实现；基线为 `codex/project-execution-root` 的 `d717d14`。状态：已完成本机验收，未提交/合并/发布。

## 1. 目标与边界

按已确认的《多项目与远端执行架构方案》第 9 节实现：执行身份必须显式提供，路径检查失败按真实原因报告。本片不实施项目注册、Docker/envd、网络配置、存储搬迁、全面路径改名或 worktree 管理，不改数据库 schema、依赖和用户配置。

CLI 命令、HTTP 路由和工具参数保持。`execution_root_unavailable` 新增 `unreadable` 原因。后台管理器和 ProcessTaskPort 是内部入口，本片将 `execution_root` 改为必填关键字；现有生产、脚本与测试消费者同步迁移，不保留推断 Local 的兼容分支。

## 2. 当前链与目标链

当前：Shell → task_context 从 RunScope.identity 取身份 → submit / foreground → _register → require_execution_directory → validate_execution_root → ProcessCapture / 任务登记 / worker。生产 Shell 已传身份，但管理器允许缺省并临时构造 Local；操作记录仍可能保存 None。目录检查先 stat，随后 is_dir 再次 stat；前者的 OSError 被错报 wrong_project，后者可能直接漏出。

目标正常：会话装配明确 Local → RunScope 冻结身份 → 工具许可检查 → task_context 传递 → _register 核对身份类型与三个路径 → 目录及必要项目检查 → 归档检查 → 登记并创建 worker。ProcessRequest 深拷贝、任务去重、停止、输出、归档和前后台转换保留。

目标失败：缺身份 / 范围不匹配 / 目录不可用 → 在 ProcessCapture、任务登记和 worker 之前失败 → 工具反馈或会话不可运行状态；不启动进程、不改用主目录。历史读取及已有任务 list/output/stop/aclose 不新增目录门槛。

## 3. 数据与接口合同

- `submit(..., execution_root: ExecutionRoot, project_id=None)` 与 `_register`、ProcessTaskPort 同步；foreground 进入同一准入链。省略必填参数产生内部契约 TypeError。
- 显式 None、错误类型、`execution_root.path != workspace_root` 或 `request.cwd != workspace_root`：复用 `Background task scope is unavailable.` 的 ToolException。
- `_Operation` 保存实际验过的非空 ExecutionRoot。项目 UUID 仍可为空：明确 Local 的临时会话只检查目录，不创建身份文件、不开放项目 history/memory。
- `validate_execution_root(None)` 保留无目录聊天语义；工具和进程边界自行拒绝缺少执行范围。
- 根路径只 stat 一次，用 `stat.S_ISDIR(info.st_mode)` 判定；不承诺消除检查后文件系统竞争。

| 事实 | reason |
| --- | --- |
| 根不存在 | missing |
| 根或其上级不是目录 | not_directory |
| 根检查、身份读取的权限/其他 OS IO 错误 | unreadable |
| marker 缺失、内容无效、UUID 不匹配 | wrong_project |
| 遗留 worktree 未登记 | not_registered |

IO 失败保留受控日志，只记事件及异常类型，不写原始异常消息/路径/身份内容。HTTP 继续投影 code/reason，CLI 保留原因与历史可读提示；Web union 和中文提示同步新增 unreadable。

## 4. 影响清单与旧消费者

- `execution_roots.py`：单次 stat 和错误分类。
- `application/sessions/background_tasks.py`、`tools/background_tasks.py`：内部必填身份、类型/路径检查、操作登记类型。
- 审计 `tools/permissions.py`、`shell.py`、`process_execution.py`、`aitest_kit.py`：已接入的检查与传播不重复添加。
- `frontend/src/types.ts`、`App.tsx`、`App.test.tsx`：原因和展示。
- `tests/test_execution_admission.py`：独立准入反例与分类，避免继续扩大既有合同测试文件。
- 既有 execution_root 合同/文件/Web/archives、background 系列、CLI 展示测试：保留原行为断言，增加 unreadable 覆盖。
- `scripts/benchmark_background_tasks.py` 及直接调用管理器的测试 helper：显式提供原场景已有的执行身份。helper 修改只用于接口迁移，不改变时间窗口和业务断言。

删除项：管理器临时构造 Local、scope 错报 wrong_project、根的第二次 stat。保留项：已知 Local 的会话装配、project_id=None、无目录聊天、历史类型、已运行任务查询/停止。

## 5. TDD 与验收

1. 先跑红灯：省略/None/错误类型/路径错配不登记不派发；stat/marker IO 错误为 unreadable；检查根只读取一次；UI 显示原因。
2. 最小实现，逐个迁移现有消费者。标明红灯来自原缺陷，不把旧测试缺参数误认为目录失效验证。
3. 正常与拒绝分支：正确 UUID、明确临时 Local、无目录聊天；目录失效后历史可读/新 run 拒绝、子 Agent 记录保留/续跑拒绝、前后台及文件工具不回退；已有任务输出和停止保留。
4. 权限失败用 monkeypatch 注入，不依赖 chmod/root 权限；进程/worker 用事件或状态同步，检查器在测试末断言计数，不能被后台异常隔离吞掉。
5. 专项与全量后端、全量前端、TypeScript/Vite、标准 macOS ARM64 wheel 构建、diff/文件行数/消费者扫描。原生 rg/fd 走既有 native-assets 流程，不改依赖/环境配置。

实际验证命令（先由 native-assets 准备 rg/fd 并将 build/r1-01-test-assets/bin 加入当前命令 PATH）：

```sh
.venv/bin/python -m pytest tests/test_execution_admission.py tests/test_execution_root_contract.py tests/test_execution_root_files.py tests/test_execution_root_web.py tests/test_execution_root_archives.py tests/test_public_history_recovery.py tests/test_background_tasks.py tests/test_background_tools.py tests/test_background_task_integration.py tests/test_background_promotion.py tests/test_background_promotion_lifecycle.py -q --tb=short
npm --prefix frontend test -- --run
npm --prefix frontend run build
.venv/bin/python -m pytest tests -q --tb=short
.venv/bin/python -m scripts.build_release --target macos-arm64 --output build/r1-01-dist
```

本片收尾后只讲 R1-02 存储依赖收束，批准前不搬文件。没有真实供应商、Docker、SSH 或其他平台执行证据时不声称对应能力通过。


## 6. 当前实现、结果与证据边界

- `submit`、ProcessTaskPort、`_register` 同步要求显式 ExecutionRoot；`_Operation` 不再声明可空执行身份。foreground 经同一个 `_register` 拒绝缺省身份。冻结请求、登记去重、任务清理保持原链。
- 校验根只调用一次 stat；用返回类型位判断目录。根与 marker 的 OS IO 错误为 unreadable，marker 丢失/损坏/UUID 不符仍是 wrong_project。日志只保留事件/异常类型。
- `permissions`、Shell、AITest 与 process_execution 已接入统一检查或许可检查，无需新增平行校验。HTTP/CLI 原有投影已传递新 reason，仅新增其反例；前端 union/文案同步修改。
- 旧直接调用迁移覆盖后台任务测试、前后台转换/生命周期测试、缺目录合同测试和 benchmark 脚本。脚本读取真实 session snapshot 的 project_id/execution_root；测试 helper 显式声明其已有 Local 场景。新增 deny_execution_root helper 供文件/Web/公开CLI三个路径复用相同的根级 IO 注入，不修改 conftest 或测试断言基础设施。
- 没有新增模型/网络能力、数据库迁移或配置，已有应用输出归档、进程组停止和关闭行为未改。第二片未实施。

| 验证 | 本轮实际结果 |
| --- | --- |
| 新增准入红灯 | 13 failed / 13 passed；缺身份未拒绝、错范围错报、IO 分类、二次 stat 各有真实失败 |
| UI 红灯 | unreadable 原提示没有中文原因；原 missing 用例通过 |
| 准入/文件/历史/CLI-Web/后台与转换专项 | 133 passed in 15.73s |
| 全后端 | 4850 passed in 344.58s（原 4808，本片新增 42 个参数化场景） |
| 全前端 | 50 文件，424 passed（原 423） |
| TypeScript/Vite | exit 0；保留既有大包提示，无错误 |
| 标准 macOS ARM64 wheel + Twine | exit 0 / PASSED；3 份变更 Python 产品源码与 wheel 逐字节一致 |
| 静态与接线 | 所有带 workspace_root 的直接进程入口显式传 execution_root；变更 Python AST、源文件 500 行、diff/new-file 空白检查通过 |

日志：`build/r1-01/backend.log`、`frontend.log`、`frontend-build.log`、`wheel-build.log`；源码核对 `verified-source.json`，汇总 `verification.json`。验证只覆盖本机和确定性替身/真实本地进程、SQLite；没有调用模型供应商、Docker、SSH，也未验证其他平台。

可追踪调试入口：`tests/test_execution_admission.py` 的前后台参数化测试 → BackgroundProcessManager.submit/foreground → _register → require_execution_directory → validate_execution_root。正常分支见既有 test_background_tasks 与 test_execution_root_archives：有明确 Local 的请求获得 task_id，目录移走后输出仍进入外部 archive_root，stop 确认清理；失败分支断言 capture、登记表、worker/runner 均未产生副作用。公开恢复见 test_public_history_recovery：历史可读，identity 未改，模型调用计数保持 1。
