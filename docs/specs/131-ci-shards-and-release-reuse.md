# Spec131：完整测试分片与同提交发行产物复用

## 目标与现状

用户批准将 0.5.0 候选、近期产品修改和教程整合到 main，并实施 CI 提速。本轮只整合代码，不创建标签或触发 PyPI 发布。现状：CI 后端单任务运行完整 tests，最近成功运行耗时 1122.61 秒；前端、四目标构建和源码外安装已并行。标签 Publish 再调用同一 CI，重复测试与构建。

## 正常与失败调用链

1. PR/main → CI.targets 校验目标 → 两个 test 分片、frontend → 四目标 wheels → complete 汇总完整性。分片按排序后的测试文件交替分配，同文件所有参数用例留在一片；两片并集覆盖 tests 下全部 pytest 默认命名文件，交集为空。每片 pytest -x，失败立即返回非零；不减少成功路径的覆盖。JUnit 无论成功失败均上传，用于后续评估负载，不自动修改分片。
2. complete → 既有 inspect_release 检查四目标、版本、相同应用内容 → 生成清单（仓库、完整提交 SHA、运行 ID、版本、每个 wheel 的目标/文件名/SHA256）→ 上传 release-candidate，含 manifest.json 和 wheels/。
3. 标签 → targets.check_tag → resolve 查询本仓库 CI 工作流的成功 main push。只接受 SHA 完全相同、head_repository 相同、工作流路径正确且 release-candidate 未过期的运行。没有可用候选，执行完整 CI；API 查询失败显式失败并保留诊断。
4. 有候选时跳过 test/frontend/wheels；complete 下载指定 run 的 release-candidate，检查清单身份、运行 ID、版本、精确文件集合、每个文件哈希，再调用 inspect_release。错误立即阻断发布，不静默采纳不同提交或不同来源的产物。校验后上传当前发布运行的同名产物。
5. Publish 仍等待 reusable CI 成功，使用 OIDC/PyPI environment 发布验收后的 wheels/；随后四平台各选本平台 wheel，复用既有 verify_wheel_install 的公共 PyPI 下载、哈希与源码外安装检查。

## 修改面与接口

- scripts/ci_shards.py：test_files(directory, index, count)；CLI --index/--count/--output。使用标准库，执行现有 pytest，无新依赖。
- scripts/ci_release.py：候选选择、manifest 创建/核验和发布后目标选择；CLI resolve/create/verify/install。GitHub 查询使用 runner 自带 gh，token 仅通过环境传入；候选只来自 main push，不信任 fork/PR 产物。
- .github/workflows/ci.yml：matrix shard [0, 1]，每片保留 40 分钟故障预算，正常完成时间由实际负载决定；增加 JUnit、候选产物及标签复用门禁。只读 actions 权限。
- .github/workflows/publish.yml：消费候选目录，保留 OIDC 和四平台公开安装。
- tests/test_ci_shards.py、tests/test_ci_release.py、tests/test_release_workflows.py：行为测试与工作流接线测试。迁移旧的单任务/下载路径断言，保留完整覆盖、完整产物门禁和凭证边界。
- README、docs/releases/0.5.0.md、docs/releasing.md、handoff：同步整合后实际能力、维护操作与验收结果；教程保留历史版本边界。Spec128 正式稿保留，新纳入的旧草稿放 archive/spec128-local-drafts/。

## 验收与范围

先确认新测试因缺少实现失败，再实现。验证两片完整且无重叠、非法参数拒绝、命令失败传播、候选身份/事件/工作流/提交/过期拒绝、清单身份/版本/哈希/多余文件拒绝，以及完整工作流条件与发布后检查。运行全部后端、全部前端与构建、pip check、标准 wheel/源码外安装、diff 检查；远端 PR 与 main CI 分别核验，不以本机结果替代远端耗时或实际发布验证。

不改依赖、真实模型/用户配置、用户数据库，不删除本地研究资料，不自动取消发布任务，不建立新 worktree，不修改已发布标签。复用只能减少同提交的重复发行准备；新提交仍须完整测试。

## 参考与复核

采用 GitHub 官方的[矩阵任务](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)、[跨任务/运行产物下载](https://docs.github.com/en/actions/tutorials/store-and-share-data)与[可复用工作流输出](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)合同；pytest 使用既有 `-x` 失败退出机制。两项独立只读复核未发现阻塞，实际 pytest 收集核验为 5602 项，3122/2480，交集/遗漏/多出均 0；这些数量不是远端性能承诺。

## 本机实施验收

新测试先因缺少两个 scripts 模块收集失败，最小实现后专项通过。完整命令为 `python -m scripts.ci_shards --index 0 --count 2 --output build/main-integration/backend-0.xml` 与 index 1 并行；分别 3122 passed/347.61s、2480 passed/274.59s，共 5602。前端 68 文件/550 项与 TS/Vite、标准 Mac ARM64 wheel/Twine/源码外全新安装、compileall、pip check 与 diff 检查通过，独立安全专项 78 项通过。对旧 main 的真实 GitHub 查询返回无同提交候选，明确输出完整回退。真实远端耗时、main 候选选择及下载核验以 [PR#1](https://github.com/tlzmw001/naiyue-test-agent/pull/1) / Actions 为准；本轮不创建标签、不执行 PyPI 发布，不宣称标签复用已在实网发布中验收。
