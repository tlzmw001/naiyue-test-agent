# R1-13B 审查后的受控交付

状态：2026-10-25已按用户收敛后的范围完成本机验收。仅支持合入当前本地分支、创建本机新分支、丢弃任务引用；远端推送实现已移除，PR取消，没有待确认项。

## 1. 当前链路、目标与范围

13A 的 ResultReview→SessionAPI.result_call 从保存的会话setup解析 project/session/binding/input；ResultService 管一次捕获和本机必要保存，ResultImporter 验证并导入 refs/naiyue/tasks/<result_id>。原 SandboxOperations 记录的是跨RPC操作，不适合塞入本机Git交付动作，否则应用关闭会错误地向envd查询/停止它们。

本片仅提供建立本机分支、干净线性合入、丢弃应用引用。收到结果后的远端推送、PR和其他发布操作由用户自行处理，应用不发现推送目标、不提供推送或PR入口。验收使用临时本机仓库与隔离Docker资源，不在开发仓库或用户真实项目执行交付，不提交或发布当前开发分支。

复用现有GitRepository、ResultImporter、认证作用域HTTP、owner.operation、run_blocking、ScopeTasks、RunAdmission和短事务Database。新增交付仓储与本机Git执行模块，不增加Agent框架、线程池或依赖，不做脏工作区应用、分支checkout、冲突自动解决、强制覆盖远端、远端SSH14或R2。

## 2. 身份、持久事实与接口

每个交付请求包含 operation_id(32位hex)、result_id、action 与该动作明确输入。后端从原会话解析binding/project，结果必须已ready、未discarded；核对receipt及缓存对象、任务ref和项目当前revision/位置。调用方不能给任意本机路径/refspec或可执行命令。

注册表 schema5→6增加 git_deliveries（operation_id PK、project_id、binding_id、result_id、state、record_json）。JSON保存不可变请求指纹、动作、项目revision/来源、预期Git状态与计划、实际结果/错误码及时间。不进入envd journal，不被SandboxOperationService.close当作进程。仓储SQL全在storage；项目归属查询必须带project/binding/result。

状态：preparing（尚未进入副作用）→executing（意图与预期状态已持久化）→completed/failed/unknown。必要保存失败不报告成功。相同operation_id参数不变；参数/结果/项目冲突拒绝。新请求遇到本项目未确认交付，先要求核对原操作，防止另起ID绕过未知状态。

executing/unknown恢复只查询实际本机Git状态：事实符合计划可补齐completed，不符合则保留unknown及人工核对提示；不会自动再执行副作用。preparing在明确无副作用事实下可完成预检；任何新副作用前先保存executing。

人工核对出口：用户先在本机检查部分提交/文件状态，再单独确认 acknowledge（HTTP还要求confirmed=true）。仅将原记录标为acknowledged并解除本项目未知操作门，保留原计划和不确定结果；不表示原动作成功，不执行Git、不撤销、不自动continue。新交付仍从头检查全部门槛。

拟新增领域入口 EnvironmentAPI.delivery_options(binding,result_id)、deliver(binding,result_id,command)、delivery_operations(binding,result_id)；SessionAPI从会话读取归属后转入。HTTP GET sessions/{sid}/results/{id}/delivery；POST同路径；GET同路径/operations；POST同路径/acknowledge（operation_id、confirmed=true）。严格额外字段、ID、分支名、动作、预算验证，沿现有认证/Origin/实例/幂等边界。POST返小回执，详情走GET，避免文件清单超过命令frame预算。

## 3. 建分支

- 用户明确指定branch，验证完整refs/heads/<branch>；拒绝HEAD、保留/非法名称、控制字符和参数注入。
- Local在原项目Git库，GitSource在该结果应用裸库创建分支。只能指向验证过的output_commit；不checkout、不改HEAD/index/工作文件。
- 新请求要求分支不存在，拒绝symbolic ref；用update-ref --no-deref＋期望零值CAS。已有用户分支不能覆盖，即使恰好同commit也不将它冒认为本次新建。
- 已保存executing后发生保存失败，原请求可核对同一分支/commit补齐完成。无法确认则unknown，不重建、不回滚用户分支。
- UI说明结果分支可能包含输入中的用户原有WIP，分支不等于Agent独立贡献；审查基准仍input→output。

## 4. 干净线性合入

仅Local提供。执行前重核：项目仍原位置/revision；HEAD等于original_head且为非symbolic目标异常的正常本地分支；index/worktree干净、无未完成merge/rebase/cherry-pick等操作；无会被覆盖的未跟踪或忽略文件；input_snapshot树等于original_head树。任务从input开始单父线性展开，无merge、异常根/缺对象或过长历史。大小写冲突和不支持特性明确拒绝。

准备、每一步执行及只读核对在任何status之前先拒绝用户info attributes，并用不会执行filter的check-attr检查当前工作树对所有index路径和待检出路径的有效属性（包含未跟踪/已修改的.gitattributes）。可信目标树另用独立索引验证属性。关闭hooks、GPG签名、编辑器、外部diff/textconv和不支持的过滤器，防止status/checkout触发自定义驱动/外逃路径。GitSource、脏源、输入WIP、历史异常时仍可审查/建分支，不提供强行合入。

将每个任务提交及其预期前/后HEAD保存计划，按顺序cherry-pick任务增量，不把合成输入提交带入正式历史。HEAD等于所挑提交的父节点时使用Git --ff，保留精确任务提交，便于中断对账。每一步前重核HEAD、分支、干净状态，完成后重核实际HEAD/index/worktree。只在所有步骤与必要保存成功后completed。

崩溃/取消/保存失败后用计划中的确切提交核对已完成前缀。部分合入、外部改动、未完成Git状态均明确unknown并保留现场，不自动continue/abort/reset。应用级预留只约束本应用；用户在其他终端/编辑器手工并发操作仍需避免，不能把预检或内部锁宣传为文件系统事务。

## 5. 丢弃引用

用户明确确认后，仅CAS删除应用任务ref（期望output），不删用户分支、不动HEAD/index/工作文件、不销毁沙箱，不立即GC对象/删除审查缓存。GitSource在应用裸库处理。引用变化/symbolic拒绝；原意图保存后已经消失可对账。完成事实与结果discarded标志同一注册表事务保存，阻止未来交付；历史diff保留并显示已丢弃。

## 6. 仅本机交付的边界

DeliveryCommand只接受branch、merge、discard；branch只用于创建本机分支。不再接受remote_name/remote_url等字段，push/PR和携带远端字段的旧请求返回验证错误，不进入意图预留或Git执行。

删除GitRemote和仅供推送使用的认证配置展开模块，GitRepository恢复原有输入读取/认证入口，不移除GitSource准备输入仍需使用的fetch/认证能力。交付options只返回discarded、三个actions和operations，不读取远端配置或认证信息；UI不再出现推送、远端选择或PR提示。旧的不可变交付事实不重写、不清库；新入口不执行旧push请求。

## 7. 准入、锁与关闭

RunAdmission增加项目交付预留标记，复用原admission.lock：短锁下检查本项目各SessionService actor和环境管理状态，借用既有background_tasks.deletion_guard检查/预留已加载会话的后台任务，释放短锁才执行Git。新run在prepare和正式_admit均复核；控制/停止保留。不同项目不受影响，不自动停止用户任务。

EnvironmentAPI复用HostManager的ScopeTasks，以('delivery',project_id)串行同项目交付，不阻塞其他项目或本项目只读历史。原SOP2(result_id)保护result metadata与discard/capture的交错；整个IO与必要保存加入既有SOP1接受计数。顺序：项目async gate→admission短预留→worker SOP2→短DB。不从SOP2反取DKR2，不持DB/admission线程锁等待Git或其他线程。同步更新lock-hierarchy并测反向路径/取消/关闭。

项目交付先在短host gate内设置准入预留，释放host gate后才做IO。rename/trust/relocate/remove/close在同host gate下核对该预留，因而公开EnvironmentAPI的冷项目入口也不能与项目变更交错；SessionAPI owner.operation继续覆盖热宿主。修改revision在副作用前后核对。宿主成功排空运行/accepted operations/进程后显式注销准入，失败仍阻断，不依赖GC。关闭封口后不开始新副作用，已启动工作由run_blocking/await_settled排空，未知记录保留供核对。

## 8. UI与配置

扩展既有ResultReview，沿用现有CSS Modules/字体/颜色/焦点围栏，不改应用壳。先展示交付可用条件/拒绝原因，再填分支/确认合入或丢弃；动作明确展示目标与固定commit，未知请求保留ID并提供“核对原交付”。交付与取回不同，取回不自动交付。持久操作列表可在重开窗口后继续核对；已discarded结果仍可审查。

所有执行参数走现有Git预算或新增最小DeliverySettings（提交数/记录数）；不修改用户真实配置/.env，不升级依赖。旧schema迁移、配置默认加载、序列化、调用方和测试同步。

## 9. 验收与可读性

先红后绿：临时真实Git的脏/部分暂存建分支不变、已有/symbolic引用拒绝、合入门槛矩阵、线性顺序、无重复提交、merge历史拒绝、未跟踪/忽略覆盖、用户hooks/filters/GPG不执行；发布后保存失败与部分合入未知、项目新run/后台任务竞争、同项目串行/跨项目并行、跨scope拒绝、schema5迁移。增加确定性拒绝测试：push/PR或携带远端字段的请求在HTTP与领域边界拒绝；交付options和三个动作不调用远端配置查询/认证。保存失败后的重启对账继续用本机分支验证。

前端测确认/不可用/已丢弃/失败/原ID/重开/窄屏/键盘；完整后端、前端、标准Mac/Linux ARM64 wheel、Twine/源外安装/包成员一致性，真实本机Docker捕获→本机交付，并回归13A与12B。证据build/r1-13b/verification.json。

参考已核对：Pi当前git-merge-and-resolve扩展在agent_end检查clean、fetch/merge，冲突留工作区交给后续消息；不是本片所需的显式交付/持久操作对账。AgentX GitHubService用JGit复制/commit/push及配置凭据，同样不是本项目的当前工作区保护合同。本片沿现有Python工具与确定性边界实现，不迁移它们的自动合并或凭据写法，也不改变LangChain图生命周期。

一手依据：[cherry-pick](https://git-scm.com/docs/git-cherry-pick)、[update-ref](https://git-scm.com/docs/git-update-ref)。

## 10. 范围收敛前的接线与验收记录

版本边界：本节记录此前包含推送的13B验证，不能作为当前功能范围或当前验证结果；2026-10-25用户已取消推送和PR，当前范围见第1—9节，收敛后的验收见第11节。

正常：ResultReview→ResultDelivery显示固定output、可用条件与用户选择→WebApi显式POST→原认证/Origin/实例/请求ID边界→SessionAPI.result_call从已存setup核对归属→EnvironmentAPI项目交付预留→DeliveryService校验receipt/cache/项目revision并持久preparing→GitDelivery或GitRemote生成计划→必要executing保存→副作用前revision复核→固定Git动作→实际ref/HEAD/工作树/远端核对→必要completed保存→UI小回执与操作记录。discard完成与结果标志在同一事务。

失败：门槛不满足时failed且不开始副作用；执行或必要完成保存未确认时unknown。相同operation_id进入reconcile，只读事实核对成功才补completed，部分合入/外部改动保持unknown，无自动continue/abort/reset。前端断线保留原command/ID，重开从已存记录恢复。人工ack单独确认，不表示成功或撤销。沙箱后台任务不写Local工作树，不额外依赖Docker查询来交付已固定结果；销毁沙箱后仍可交付。

复核修复：status之前的有效filter检查（info和未跟踪.gitattributes及只匹配未来检出路径的反例）、已关闭宿主GC残留、冷项目交付与重新定位竞争、本机URL insteadOf/pushInsteadOf及二次重写。保留认证助手与include假凭据的独立探针通过，没有真实认证写入。SessionService新增管理门后保持498行，注销职责由已有RunAdmission持有；不增加第二套锁/执行器。旧注册表v1/v3/v4/v5迁移夹具同步移除未来表并期待v6，保留原有行数据/身份/操作断言；原始失败日志保留，没有放宽或skip测试。

| 命令/证据 | 实际结果 |
|---|---|
| `.venv/bin/python build/r1-13b/run_full.py`、`.venv/bin/python build/r1-13b/rerun_group.py` | 第一组2711通过；第二组首次发现v3造库夹具问题，修正后整组2694通过。原失败日志保留 |
| `.venv/bin/python -m pytest tests/test_git_delivery_filters.py::test_preflight_rejects_filter_matching_only_a_future_checkout_path -q --junitxml=build/r1-13b/future-filter-final.xml` | 新增反例1通过；三个JUnit的用例集合精确覆盖最终collect的5406项 |
| `.venv/bin/python -m pytest tests/test_git_delivery*.py tests/test_git_auth_config.py tests/test_delivery_settings.py tests/test_multi_project_lifetime.py tests/test_scoped_host_opening.py -q` | 冻结前最后相关专项68通过；生命周期收尾专项27通过 |
| `npm --prefix frontend test -- --run` | 59文件、473项通过；最终相关两个组件10项通过 |
| `npm --prefix frontend run build` | TS/Vite退出0，已有大chunk提示保留 |
| `python -m scripts.build_release --target {macos-arm64,linux-arm64} --output build/r1-13b/wheels-verified-*` | 分别执行并通过；Twine通过，343 Python/4 Web/README与源字节一致 |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13b/wheels-verified-macos --output build/r1-13b/install-verified-macos.json` | Mac源码外全新环境安装与正式工具/API探针通过 |
| `python -m scripts.verify_sandbox_delivery --context colima-nyt-r1-10 --image naiyue-envd:r1-13b-verified --output build/r1-13b/docker-delivery-verified.json` | 真Docker捕获后销毁，仍可建分支、合入、推送临时bare远端、丢弃及重启审查，0供应商请求 |
| 同镜像分别执行 `scripts.verify_sandbox_results`、`scripts.verify_sandbox_sessions` | 原13A部分暂存/三锚点/离线审查与12B会话/子实例/审批/任务/失效不回退回归通过 |
| `.venv/bin/python build/r1-13b/verify_results.py` | 精确5406用例集合覆盖、495冻结输入无漂移、包及Docker证据汇总通过 |

Browser用临时项目、真实认证服务和隔离envd dispatcher transport核对正式建分支入口（不checkout）、重开已有操作、固定远端/提交确认、桌面/375px长路径/两列按钮及Escape归还焦点；与真实Docker证据分开。最终截图delivery-desktop-final.jpg、delivery-mobile-final.jpg。测试页面/容器/卷已清理，默认Docker context保持default，专用VM与验收镜像保留。真实配置/.env/用户库/依赖/版本和主目录并行工作未改；未提交/push/合并/发布。

上一阶段未实现PR，当时的供应商待确认项已由用户取消，不是当前待办；没有安装/登录或创建真实PR。未验证公司远端SSH、真实私有Git认证服务器、GitLab及R2；临时file远端与假凭据探针不能替代这些证据。CLI尚无新的交付命令，当前正式消费者为领域API与认证Web。大型历史受max_commits及现有Git数据预算限制，非线性历史/过滤器/脏工作树不强行合入，结果分支可能包含输入时已有WIP。

## 11. 本机三动作收敛实施与验收

修改原因：用户明确远端操作由用户自行决定，应用只负责接收结果后的合入当前本地分支、建立本机新分支或丢弃。删除专用远端执行器和认证临时配置模块，迁移原远端保存失败测试为本机分支重启核对，原失败语义/ID/引用/顺序断言保留；不是让旧失败用例跳过。保留现有本机过滤器、锁、项目位置、必要保存与unknown保护。

正常链：ResultDelivery三个动作→原认证HTTP→SessionAPI保存绑定核对→DeliveryService本机Git计划→先存executing→GitDelivery→核对本机ref/HEAD/工作树→必要完成保存。失败链：远端action/字段在严格合同验证时拒绝，Git及交付journal不产生变化；本机动作仍按原ID只读对账，部分合入不自动继续/回滚。

影响：contracts/git_delivery；application/environments/delivery；execution/git_repository及删除git_remote/git_auth_config；前端deliveryTypes/ResultDelivery/ResultReview/api；原推送专用测试替换为拒绝/无远端调用测试；verify_sandbox_delivery；当前README/research/Spec/handoff/project-plan。

本轮最终验证记录保存到build/r1-13b-local-only/verification.json；保留build/r1-13b原始历史证据，不能将历史推送能力误认为当前范围。


本轮实际命令与结果：

| 验证 | 结果 |
|---|---|
| `.venv/bin/python -m pytest tests/test_git_delivery_local_only.py tests/test_git_delivery_http.py -q`（先红） | 原实现3失败/3通过，确认push仍被接受、options及UI仍含推送；原日志red-backend.txt及red-frontend.txt保留 |
| `.venv/bin/python -m pytest tests/test_git_delivery_local_only.py tests/test_git_delivery_http.py tests/test_git_delivery_service.py -q` | 10通过，含严格拒绝远端action/字段、三个动作无远端配置/认证调用、保存失败后本机分支跨重启核对 |
| `.venv/bin/python build/r1-13b-local-only/run_full.py` | 两组2717＋2685＝5402通过，最终collect集合与两个JUnit逐项匹配；无skip、无放宽断言 |
| `npm --prefix frontend test -- --run` | 59文件、474项通过，含仅三个本机动作的UI反例 |
| `npm --prefix frontend run build` | TS/Vite退出0；原大chunk提示保留 |
| `python -m scripts.build_release --target {macos-arm64,linux-arm64} --output build/r1-13b-local-only/wheels-*` | 分别构建成功，Twine通过；341 Python/4 Web/README与源逐字节一致，包中无git_remote/git_auth_config模块 |
| `python -m scripts.verify_wheel_install --wheel-dir build/r1-13b-local-only/wheels-macos --output build/r1-13b-local-only/install-macos.json` | Mac源码外全新安装与正式工具/API探针通过 |
| `python -m scripts.verify_sandbox_delivery --context colima-nyt-r1-10 --image naiyue-envd:r1-13b-local-only --output build/r1-13b-local-only/docker-delivery.json` | 最终标准wheel镜像真实Docker通过捕获、销毁后建本机分支/合入/丢弃、保留用户分支及重启审查；交付记录正好三条，0供应商请求 |
| `.venv/bin/python build/r1-13b-local-only/verify_results.py`、`git diff --check` | 完整用例集合、冻结输入、包与Docker证据核对通过；没有范围外修改 |

移除项：GitRemote、固定远端认证临时配置模块及GitRepository.fixed_remote参数、远端合同字段、目标发现/执行分支、UI按钮/远端选择与错误文案、推送专用测试。推送保存失败用例已迁移为本机分支跨重启核对，丢回执/原ID/固定引用断言保留；新增拒绝远端请求及无远端查询测试。保留项：GitSource输入fetch/认证、原本机过滤器与引用保护、项目/会话准入、必要保存、unknown只读核对和人工确认。schema6及已有事实不重写，用户真实配置/库不修改。

Browser在临时项目、真实认证服务与隔离envd dispatcher transport上实际走完建分支→合入→丢弃，显示三条已完成记录，丢弃后仍可看已保存diff。桌面/375px截图为local-actions-desktop.jpg、local-actions-mobile.jpg；仅作为该临时UI验收，与真实Docker证据分开。临时页面/服务、测试容器/卷已清理，默认Docker context仍为default。未提交/push/发布，14 SSH与R2保持后续单独讨论。
