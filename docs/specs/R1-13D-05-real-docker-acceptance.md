# R1-13D-05：真实双镜像与资源生命周期验收

2026-10-26用户“继续吧”授权承接04实施本片。唯一开发树为project-execution-root，基线ce617d0；01—04未提交工作保留，不提交/push/发布，不进入SSH14。先核对现有构建/验证脚本和实际Docker，再验收；模型使用离线固定响应，实际图/工具/RPC/Git/数据库/Docker不替换。

## 目标与边界

确认前四片在真实Linux容器上组合成立：两套兼容envd且镜像ID不同的模板、独立项目/会话绑定、输入和工具执行、模板及生命周期策略热更新、原请求/原容器恢复、额度/空闲/退出停止、明确销毁及故障后无本机回退。验收不是模型效果评测，也不证明公司远端部署或SSH能力。

使用已存在的专用Colima profile nyt-r1-10；初始为Stopped，显式启动但不激活/保存配置，不修改默认Docker context。保存daemon/context、镜像、容器和卷初始清单。标准scripts.build_release构建Linux ARM64 wheel；复用scripts.build_sandbox_image构建Python环境，再基于该确切镜像加Node/npm形成第二套准备镜像。镜像使用本片独有标签，构建输入在build/r1-13d-05，不覆盖既有镜像。依赖和真实应用配置不修改，临时应用settings/data_dir/source全部隔离。

每个容器/卷通过正式环境服务创建并带项目/会话/绑定label；只对本次登记资源操作。故障注入也只能针对已核实归属的原ID；禁止全局prune、按名称前缀批删、停止daemon来模拟断线或修改Docker网络/宿主配置。应用确认销毁原记录；最终按label及初始清单检查无新增容器/卷泄漏、既有资源未受影响。临时镜像保留为可复查产物；如果失败清理也必须报告，不吞错。

## 当前调用链和实现范围

已存在verify_sandbox_sessions/inputs/results/delivery/processes等探针，分别覆盖单镜像链，优先复用seed/git、标准构建与正式Application API；新增verify_sandbox_profiles只负责本次双模板组合场景，不复制产品准入/额度/生命周期算法。旧会话探针重开时须显式启动04已停止的原容器，保留原审批、子Agent及文件断言。

正常链：临时配置→load_sandbox_configuration→open_service_application→ProjectAPI登记→SessionAPI.create_sandbox(profile_id/revision/request_id)先保存选择→原EnvironmentRegistry预留/真实Docker创建和输入安装→RunAPI/create_agent工具循环→真实envd→必要保存；状态/管理/结果审查继续用当前API。刷新从临时固定文件加载，旧会话image_id/container_id不变，新会话使用新模板。退出先排空后台进程，再真实docker stop，重新打开只能显式start原容器。

失败链：达到额度拒绝创建、保留原sid；释放后继续原准备。旧revision拒绝且不增加会话/资源。模拟丢回执只在真实执行已返回后丢弃一次响应，核对原operation_id并验证副作用一次；不重放。缺失/停止/身份错误的原环境明确拒绝，不创建替代或执行本机文件。未知状态在recover前仍占额度。销毁必须业务层确认，原容器和两个卷缺失后才能释放保留额度。

## 验收矩阵

1. 两个模板必须指向不同镜像ID；正式Shell验证Python/Node运行时、同名文件各自隔离、本机项目未写入；检查镜像/用户/挂载/网络/资源事实，复用provider校验。
2. 选定隐藏Skill输入仅进入对应快照；两项目共享运行额度。原创建请求重试不多建；热刷新改默认/移除模板/无效配置保留旧版，已有环境不换绑；新请求旧revision拒绝。
3. 停止释放running、保留retained和文件；启动原容器不增加retained。降额不杀已有任务；第三个会话额度不足保留准备状态，释放后原sid继续。明确销毁才释放retained。
4. 空闲使用注入now推进确定性检查，不等真实30分钟；活动run/后台/审批/unknown和输出未结算受保护；后台自然完成后才开始空闲，重复只读不续期。
5. 正常关闭确认进程结束、容器停止、卷保留；重开显式启动原ID并读取原文件；销毁后历史/已取回结果仍可查。结束确认资源清理。
6. 真实RPC丢回执、原容器缺失/停止等反例验证明确错误、原ID核对和无本机回退。Docker不可达通过仅该临时应用使用的不可达context测试，不改变/停止真实daemon。

## 文件与验证

新Spec、scripts/verify_sandbox_profiles.py、对应脚本安全/参数测试；真实复跑已确认旧verify_sandbox_sessions、verify_sandbox_inputs、verify_sandbox_processes在重开时仍假设容器running，须迁移为显式启动原容器，保留原验证。processes模拟envd意外重启改为身份核对后的外部停止，因为正常管理stop现在正确拒绝未结束后台任务。README/handoff/project-plan同步实际证据。仅在真实失败复现并定位后修产品，先更新Spec、写确定性失败测试，再最小修复。新验收脚本不是产品新功能，不能将已有功能直接通过称为TDD红灯。

验收收紧：清理工厂只使用最初保存的有效配置，不重新读取故障注入文件；失败保留临时登记目录并输出恢复位置。运行时命令用&&串联并验证带标记的具体版本，禁止以工具包装中的单字符证明Node/npm存在。后台停止后等待实际输出采集task结算，再用注入now判定空闲，避免把读使用权保护造成的一次扫描跳过误判为停止失败。

旧verify_sandbox_environment同样已实跑在重开files处报env_stopped，按同一合同迁移显式启动原ID。审批输入使用受默认策略保护的隐藏路径，普通项目内文件原本允许，不把无审批误判成沙箱缺陷。

先补脚本安全校验失败测试（相同镜像ID拒绝、清理仅本片登记项目、清理错误可见/原异常保留）；再实现探针。标准构建、实际两镜像验收、相关既有真实探针、全量Python/前端/构建及源码外安装按当前变更影响核对；不为文档重复跑全量。记录精确命令/版本/镜像ID/实际结果和残留资源，0供应商请求与真实Docker证据单列。完成后才更新顶部交接为已完成。

## 实际结果与复查入口

本片没有修改naiyue_test_agent产品逻辑、前端源码、公开接口、依赖/版本、真实配置或用户数据库。新增组合验收脚本（复用seed/git及标准构建）、8项脚本安全测试，迁移4个旧Docker探针；更新当前说明。安全专项最初缺脚本模块，后续清理工厂/失败目录/版本校验回归失败后补实现转绿，不将既有产品功能跑通声称为产品TDD。

运行环境：现有Colima nyt-r1-10，Linux aarch64、4 CPU/6GiB/40GiB；Docker默认context始终default。本次开始前和清理后的容器、卷均为0。完成后专用VM恢复原Stopped状态，不删除VM、既有镜像或修改其保存配置；两套新增验收镜像保留供复查。这些镜像是本机测试产物，不规定公司开发环境的语言版本。

| 模板 | 镜像标签 | 实际镜像ID | 工具读到的运行时 |
| --- | --- | --- | --- |
| Python | naiyue-envd:r1-13d-05-python-20261026 | sha256:9f46d7aea1dc6b1fa8f9c010cce6c41cd0d156da441cb382bb7d6ae285c6da37 | Python 3.12.15 |
| Node | naiyue-envd:r1-13d-05-node-20261026 | sha256:f8ed91c9d2d92038db3368215a3f6950b57fc99566859fb370795c95b3d95c69 | Node v18.20.4 / npm 9.2.0 |

Python镜像来自本轮标准Linux ARM64 wheel；Node从该镜像添加语言工具。BuildKit首轮把FROM中的裸sha256镜像ID当成远端仓库而拒绝；改为本片独有本机标签、禁用pull，并在构建前后核对标签对应的确切ID后成功。未改产品Dockerfile或builder来绕过构建。最终Mac/Linux wheel的应用内容hash均为c2db16c40d014d662ded95130d571c2ca0f6453c2b40e38ee0fd03f1d89213ce，与验收镜像源wheel相同；最终README元数据单独重新构建，不将镜像内旧说明当作最终说明。

组合探针14项全部通过：两镜像/文件/隐藏Skill隔离；活动run拒绝停止和销毁；跨项目运行额度保留原会话；模板热刷新/降额不换绑不杀任务；真实后台保护与空闲停止保留文件；待审批保护；unknown占额到核对；原准备继续/保留额度/销毁/结果保存；退出停止原容器并保留双卷；重开显式start同容器及任务归档；真实RPC丢回执只查原操作；停止/缺失原环境不回退本机；不可达context明确错误且不改默认；全部自建容器和双卷缺失确认。共4个环境绑定，项目本机HEAD/index/工作内容保持。

额外复跑6个既有真实探针，全部exit0：文件/网络none与external/资源/权限过滤、Git输入、会话与审批/子Agent续跑、进程和有界输出/丢回执/控制端异常退出、结果快照、销毁后本机交付（建分支/合入/丢弃）。重开先start原容器；envd异常重启采用核实原ID后的外部停止，等待观察者结算再recover/start，正常管理stop仍拒绝未结算任务。新组合脚本重开时补SessionAPI.prepare再调用RunAPI，与CLI/Web的恢复装配前提一致；没有将测试遗漏修进产品。

精确命令（在本工作树，以下PY代表带内置fd/rg路径的.venv/bin/python）：

```bash
colima start nyt-r1-10 --activate=false --save-config=false
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python -m scripts.verify_sandbox_profiles \
  --context colima-nyt-r1-10 \
  --python-image naiyue-envd:r1-13d-05-python-20261026 \
  --node-image naiyue-envd:r1-13d-05-node-20261026 \
  --output build/r1-13d-05/docker-profiles.json

# 其余6个实际命令同样显式指定本机context/image/output：
# scripts.verify_sandbox_environment / verify_sandbox_inputs / verify_sandbox_sessions
# scripts.verify_sandbox_processes / verify_sandbox_results / verify_sandbox_delivery
# 报告为build/r1-13d-05/docker-{environment,inputs,sessions,processes,results,delivery}.json。

.venv/bin/python -m pytest -q tests/test_sandbox_profile_acceptance.py
# 8 passed
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python build/r1-13d-05/run_shard.py 0
# 2749 passed in 362.36s
env PATH="$PWD/build/pre-profiles-push/test-assets/bin:$PATH" .venv/bin/python build/r1-13d-05/run_shard.py 1
# 2748 passed in 331.27s
# collect限定tests目录：5497项/529文件；分组无重叠且覆盖全部收集项。
cd frontend && npm test -- --run
# 533 passed / 64 files
cd frontend && npm run build
# TypeScript + Vite通过，0错误，保留原有large chunk提示。
```

Mac ARM64与Linux ARM64均走scripts.build_release，Twine通过；Mac源码外全新虚拟环境安装走scripts.verify_wheel_install，具体返回见build/r1-13d-05/install.json。345个Python/4个Web资源及README在两套最终wheel内逐字节核对；新脚本compileall与git diff --check通过。

真实浏览器验收额外使用真实TCP认证服务+同一真实Docker context/两镜像，唯一替代是模型目录的离线固定响应：选择Node后0环境；首次发送后创建1个真实Node环境，Shell显示exit0及Node/npm版本；管理停止→原绑定启动，取回差异显示app.txt从working input变为node UI acceptance，本机源文件仍是working input。桌面1280×900/375px检查、控制台0 error/warn，临时页面/服务已关闭，自建资源经应用明确销毁。没有推送远端、未将当前脏工作树合入本机。截图：[执行](../../build/r1-13d-05/real-docker-node-desktop.jpg)、[管理](../../build/r1-13d-05/real-docker-management.jpg)、[差异](../../build/r1-13d-05/real-docker-result-desktop.jpg)、[窄屏](../../build/r1-13d-05/real-docker-result-mobile.jpg)。

所有模型场景使用固定离线模型，0供应商模型请求，不证明模型业务效果。Docker/apt/pip构建访问与供应商请求分开计；公司网络策略、公司镜像/部署和SSH14未验收。失败日志和保留的故障登记目录用于查证，后续成功不覆盖失败事实；自动清理只作用于本片登记资源。资源前后清单、镜像/构建/安装报告、浏览器事实及汇总见build/r1-13d-05/verification.json。

01—05完成且保持未提交/push/发布。唯一下一步为用户使用验收；SSH14仍按先前要求暂缓，不自动开启下一开发片。
