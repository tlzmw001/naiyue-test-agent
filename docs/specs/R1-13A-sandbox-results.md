# R1-13A 沙箱结果取回、验证和差异查看

状态：已完成本机验收（2026-10-25）。依据 research 多项目与远端执行方案 7.2，以及用户批准的 13A/13B 分拆。

## 范围与当前链路

12B 已把会话固定到 project/session/environment/input，envd operation journal 管一次性副作用，控制器 sandbox_operations 管回执和恢复。InputService 的独立对象库和临时索引已经保护源 HEAD、暂存区和工作区。此次扩展这些实际入口，不另造任务执行框架。

13A 提供：显式取回当前结果、验证并保存 bundle、导入应用任务引用、输入到输出的文件列表和逐文件 diff、重复请求核对。13B 才提供建本地分支、干净合入、丢弃、push/PR。此次不 checkout、不 stash/reset、不写工作文件、不调用模型。

## 合同

- 三个锚点继续分别记录 original_head、input_snapshot、output_commit。比较固定为 input_snapshot → output_commit。
- result_id 为 32 位小写十六进制，同一次取回的 operation_id 就是 result_id。HTTP 明确带 result_id，断线后继续原 ID；不同 selection/绑定不能复用。
- 输出使用沙箱当前 HEAD 及当前已跟踪文件的字节，剩余修改通过应用独立索引形成合成提交，父提交为沙箱 HEAD；不修改沙箱 HEAD/index。沙箱 HEAD 必须以 input_snapshot 为祖先。
- 新增未跟踪文件必须在请求中显式给出 include_untracked，不从 Shell 输出或模型声明猜测。用户已确认沿用 12A：已跟踪文件纳入当前改动，未跟踪文件默认不勾选，由用户明确选择；后端始终精确采用请求清单。忽略项不传，当前树中已知凭据、外逃符号链接、LFS/submodule、超限输入明确拒绝，与 12A 相同。历史敏感内容不在扫描承诺内。
- 回执包含 result_id/project_id/session_id/binding_id/input_id/original_head/input_snapshot/output_commit/bundle_size/bundle_sha256；不得接受自由 ref/path。唯一允许导出引用 refs/naiyue/tasks/<result_id>。
- 完整 bundle 第一版不做增量优化，受现有 git max_bundle/max_cache/max_files/max_file/max_input/timeout 预算约束。快照/对象/网络操作沿用有界 GitRepository 和安全文件打开。
- envd 新 kind git_result 复用持久 operation journal，提交前要求安装完成、无活跃进程/文件操作/读者、无未确认进程；取回期间拒绝新的执行和文件读取。并发取回同样拒绝。失败不会换目录，不会重放未知 capture。options 是只读清单，取回时重新校验。
- 控制器在应用目录 git-results/<result_id> 续传 bundle；分块严格核对 offset/length/base64/hash。只重试读取/导入，绝不重新执行同 ID 的捕获。completed capture 仍需所有验证和必要保存成功才是 ready。
- 先在私有空裸库 bundle verify、严格单 ref 校验、fetch/fsck，核对回执和可信输入、输出对象、祖先关系，并 validate_tree。所有文件读取和 diff 均从已验证对象库取，不信远端路径/HTML/patch。
- 最后导入 Local 项目的 Git 对象并 CAS 创建 refs/naiyue/tasks/<id>；GitSource 保存在应用裸库。关闭 hooks、replace、外部 diff/textconv/filter，固定 ref，拒绝布局改变和项目 revision 改变。已有相同 ref/commit 可对账，其他值冲突；Git 和 SQLite 无共同事务，metadata.delivery 分阶段记录，不自动回滚用户数据。
- Local 导入用私有已验证对象库生成有界完整 pack，用户仓库仅 index-pack --stdin --strict；不从用户仓库 fetch，防止本地 url.insteadOf、sshCommand、alternateRefsCommand 被取回动作触发。引用显式拒绝 symbolic ref，update-ref --no-deref + 期望空值 CAS 防止解引用到用户分支。
- ready、导入位置、三锚点、文件清单、排除项保存在现有 sandbox_operations.metadata.delivery；无新增 DB schema。bundle/私有对象归应用 data_dir，销毁沙箱后仍可审查已取回结果。用户删改缓存明确报错，不伪造空结果。
- Diff 按文件请求，Git --no-ext-diff --no-textconv 和 literal pathspec，raw/numstat 用 NUL 分隔；展示增删改、重命名、二进制、mode/symlink 和大小写冲突提示。超过预览预算明确拒绝预览，文件清单与结果仍保留，不能静默截断。无变化显示真实空清单。

## 接口与消费者

- EnvironmentAPI.result_options(binding)、capture_result(binding,result_id,include_untracked)、results(binding)、result(binding,id)、result_diff(binding,id,path)。关闭门和 owner.operation 必须覆盖完整 IO/保存。
- SessionAPI 用现有 history_lock/manage_environment 保护 capture，与 run/环境销毁互斥；从存储取 project/session/setup，调用方不能指定其他绑定。列表和已保存 diff 不依赖活着的容器。
- HTTP 经现有认证/作用域服务：GET sessions/{sid}/results/options；POST sessions/{sid}/results；GET sessions/{sid}/results；GET sessions/{sid}/results/{id}；GET sessions/{sid}/results/{id}/diff?path=。输入严格验证，不新增无认证入口。
- Web 执行位置菜单进入结果审查，复用现有 CSS Modules、颜色/字体/焦点围栏。确认清单 → 取回状态 → 文件列表 → 单文件补丁。固定显示输入/输出短 hash 和“未写回工作文件”；未知请求保留 ID 并提供核对原请求。历史结果在容器销毁后仍可打开。
- 本片不增加 CLI 合入命令；领域 API 和认证 HTTP 可供 CLI 后续消费，现有 CLI 的沙箱运行保持。

## 正常与失败路径

正常：Web result_id → SessionAPI 读会话/互斥 → ResultService 持久 reserve → envd journal git_result → SnapshotBuilder 独立快照 → 回执 → 有界 result_chunk → 私有验证 → Local 固定 ref CAS/或 GitSource 镜像 → 必要 delivery ready 保存 → file list/diff。

断线：保留 operation_id → 原 operation_status → completed 时续读原不可变 bundle，unknown 时明确拒绝重新捕获。恶意 ref/hash/绑定/非祖先：拒绝验证，不发布 Local ref。发布后 DB 失败：下次核对相同 ref 后完成保存，不再次捕获，不改工作区。运行中/后台进程活跃：拒绝捕获，原进程继续。沙箱已销毁：已 ready 结果可以查阅，新取回失败。

## 影响文件与锁

contracts/git_results.py；execution/git_results.py（受控导入/diff）；envd/git_results.py；envd/operations.py；application/environments/results.py/api.py/operations.py；sessions/result_api.py/api.py；web/result_routes.py/routes.py；前端 resultTypes/api/ResultReview/ExecutionSelector；测试、脚本和交接。每文件不超过 500 行。

复用现有 DKR2 environment → SOP2 operation 顺序，结果的同 ID 操作复用 SOP2，不另加全局锁。envd EOP1 只预约，结果 Git IO 在 worker 中，journal/结果本地状态短锁遵循现有方向。不可在管理字典锁里等待 Git/RPC。同步进程/存储通过 run_blocking。同步更新 lock-hierarchy 的直接和间接调用。

## 验收

先红后绿：真实 Git 的部分暂存/WIP 与输入基准、未提交修改、增删重命名/二进制/mode、无变化、固定引用；绑定/ref/hash/路径/祖先攻击拒绝；不会执行外部 diff/用户 hooks；未知 capture 不重放；断线续读/发布后保存失败对账；并发捕获/运行/销毁互斥；销毁后离线查看；HTTP 跨项目拒绝；Web 失败/空/键盘/窄屏。

专项与全后端/前端、标准 Mac/Linux ARM64 wheel、Twine/离源码安装和包内容一致性；真实本机 Docker 捕获/取回/diff，并核对源 HEAD/index/文件保持、容器清理和默认 context 保持。记录命令/结果到 build/r1-13a/verification.json。测试通过不代表代码安全、真实模型质量或远端公司环境验收。

Git 一手依据：[bundle verify](https://git-scm.com/docs/git-bundle)、[diff 禁用外部程序](https://git-scm.com/docs/git-diff)、[update-ref CAS](https://git-scm.com/docs/git-update-ref)。

## 当前接线与验收结果

- 正常链已接：ExecutionSelector→ResultReview→WebApi.captureResult→认证作用域router→SessionAPI.result_call→保存setup及管理预留→EnvironmentAPI→ResultService→git_result journal→ResultExporter/SnapshotBuilder→有界原bundle读取→ResultImporter.verify→私有pack→Local index-pack+no-deref CAS→必要delivery ready保存。POST仅返回小receipt，Web随后GET完整详情，避免大文件清单撑破HTTP命令回执预算。
- 失败链已接：捕获/输入不匹配、活跃操作、未知进程拒绝；capture断线只查询原ID；unknown不重放，关闭仅保留其不确定事实，不误作后台进程停止。下载断线按已落盘偏移续读，校验不通过不发布任务ref。发布后必要保存失败，下次核对原ref完成保存。原图/工具机制异常合同保持。
- 三类复核问题先红后绿：Docker真实解码遗漏git_result_*有限码；Local fetch触发url/ssh/alternate命令与symbolic ref解引用；unknown capture的关闭误判。UI另有busy时summary焦点越界回归，已扩展原焦点围栏识别可见summary/tabindex，不增加并行实现。
- schema与依赖版本不变；无CLI交付命令，无分支/checkout/merge/cherry-pick/push/PR实现。历史输入继续由12A负责，输出只复用已验证的快照算法和有界Git执行器。没有新增互斥锁/执行器/配置项。

实际命令与结果：

| 验证 | 结果 |
|---|---|
| `.venv/bin/python build/r1-13a/run_full.py` | 2组2678＋2677＝5355通过；该轮收集后新增的unknown关闭/GitSource两项，由下方专项补齐；当前收集5357 |
| `PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests/test_git_results.py tests/test_git_result_service.py tests/test_git_result_boundaries.py tests/test_git_result_http.py tests/test_git_result_rpc.py tests/test_envd_git_inputs.py tests/test_envd_operations.py tests/test_sandbox_sessions.py tests/test_sandbox_session_http.py tests/test_sandbox_operation_recovery.py -q` | 61 passed / 46.95s，覆盖收尾改动与两项补充 |
| `npm --prefix frontend test -- --run` | 58文件、468项通过；未放宽token/行为断言 |
| `npm --prefix frontend run build` | TS/Vite退出0；现有大chunk提示保留，未改构建阈值 |
| `python -m scripts.build_release --target {macos-arm64,linux-arm64} --output build/r1-13a/wheels-final-*` | 分别执行并通过；Twine通过，336 Python/4 Web/README与源码字节一致 |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13a/wheels-final-macos --output build/r1-13a/install-final-macos.json` | Mac源码外安装/工具/API验证通过 |
| `python -m scripts.verify_sandbox_results --context colima-nyt-r1-10 --image naiyue-envd:r1-13a-final --output build/r1-13a/docker-results-final.json` | 活跃进程拒绝、部分暂存保持、三锚点、rename/binary/mode、原ID、销毁/重启后审查均通过，0供应商请求 |
| `python -m scripts.verify_sandbox_sessions --context colima-nyt-r1-10 --image naiyue-envd:r1-13a-final --output build/r1-13a/docker-sessions-final.json` | 原12B真实会话/并行/审批/子实例/任务/失效边界通过，0供应商请求 |

实际Browser检查使用临时项目+真实认证服务/正式envd dispatcher的隔离transport，不连接供应商：菜单打开、明确选择未跟踪文件、POST取回→GET审查、两条真实取回记录、diff、桌面/375px长文件名、Tab/summary及Escape返回原入口。busy焦点分支使用事件控制的确定性测试，不宣称由瞬时浏览器状态证明。截图在build/r1-13a/result-desktop.jpg与result-mobile.jpg。架构/安全复核已关闭；真实Docker和浏览器证据范围分别记录，不能互相替代。

结果缓存位置：控制端data_dir/workspaces/<project_id>/git-results/<result_id>；envd临时捕获在/state/git-results/<result_id>。Local仅额外写Git对象和应用任务ref，文件不自动写回；GitSource保存在控制端结果裸库。当前取回记录只列最近32项，单文件diff受现有Git命令输出预算限制，超限明确拒绝预览；不承诺检查Git历史敏感内容或证明返回代码安全。实际公司远端、R2和13B交付仍未验收。
