# 130-05 集成、构建和产品验证

范围：前四片配置与真实接线、正常与失败路径、所有既有测试回归、前端build、wheel/twine、仓库外新环境安装、浏览器实际图片剪贴板及拖入/选图、深浅主题与375px溢出。只使用离线模型和临时数据库，不读取或修改真实用户数据，不发付费供应商请求，不发布产物。

已观察红灯：01缺模块12 failed；02历史images缺失；03仅图control422；04缺useComposerImages模块。已有专项绿灯：57 passed后端，535 passed前端，TypeScript/Vite build通过（已有bundle大小warning保留）。完整结果在收尾追加。

## 最终结果（2026-10-26）

五片已完成。本轮最终产品测试目录为`tests/`，535个测试文件、5532个用例。两批全量起跑时5528项：**2826 passed in381.51s + 2702 passed in236.63s**；起跑后补入4项非默认原始/编码/待处理字节预算用例，配置专项**28 passed in1.95s**包含这4项。最终图片主验收**32 passed in3.71s**；控制锁/长正文等收尾专项**41 passed in2.76s**，新建/恢复装配专项52通过。均保留原断言，未skip或放宽；研究副本的第三方测试不混入产品门禁。

可复验主命令（在本worktree，`fd`复用已有构建资产）：

```sh
PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest -q tests/test_composer_image_inputs.py tests/test_web_image_inputs.py tests/test_image_control_queue.py tests/test_web_image_limits.py
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

结果：图片主验收32项通过；前端**68文件543项通过**（含仅图清空后发送模式重置），TypeScript/Vite构建362模块、0错误。保留既有主bundle大于500kB的warning，不更改依赖或构建阈值。compileall、pip check、diff检查通过；全部68个变更源码/测试文件≤500行。

标准macOS ARM64 wheel已在`build/spec130-20261026/accepted-candidate/`生成，484个归档成员；Twine通过，源码外全新venv安装/PATH排除系统rg/fd的完整smoke通过。安装覆盖CLI/Web/原生工具、存储、后台任务、子实例、记忆/MCP及新增图片场景：80×40实际规范化为20×10、保存后重开、source_revision拒绝旧版本、删除后不可读，0供应商请求。最终报告为`build/spec130-20261026/accepted-install-smoke.json`，与354个Python源码及4个Web资源逐字节核对；核对结果汇总在`build/spec130-20261026/verification.json`。

浏览器使用独立临时服务/数据库及离线Vision/Text模型：Cmd+V的JPEG截图和PNG、原生多文件选择、仅图/图文、移除/预览、未知/文字模型拒绝新图且保留、切换回视觉模型后发送、文字模型继续聊天及历史图片仍可查看、认证Blob预览/下载均已验证。下载PNG与处理后数据相同。1280桌面和375×812深浅主题验证，窄屏document.scrollWidth=innerWidth=375，长中文文件名无横向溢出。截图在`build/spec130-20261026/screenshots/`；拖入覆盖为DOM行为测试，未声称macOS原生文件拖拽、Windows/Linux原机或真实供应商视觉理解已验证。

红灯及修正证据：缺失入口/元数据/仅图control/未知请求身份/恢复竞态/停止等待worker/大正文回执/缺GIF标记/多余旧图hash/图片提交停止按钮/仅图模式重置分别先失败后修正；全量发现的冷会话404和模型层反向依赖已修正。首次缺fd使用已有构建PATH解决，未改产品Find实现；测试流式请求使用独立无JSON装饰hook的ASGI客户端，未修改conftest或公共测试配置。曾从根目录递归收集研究clone产生第三方缺依赖/重名错误，最终显式收集产品tests目录，未为此安装或修改第三方研究项目。

限制按D1—D5保持：未发草稿不跨刷新/重启；未消费队列仅本次应用生命周期；只保存模型实际收到的处理后版本；能力声明不等于网关视觉质量证明。不改真实配置、用户库、依赖或版本；无提交/push/发布，无新增用户独立掌握证据。临时验证页关闭，视口/主题恢复，测试服务经SIGINT关闭且临时目录已回收（夹具进程退出130为主动中断，不作为构建或产品测试失败）。

历史图按需加载及对象URL释放/迟到读取abort新增2项，最终前端68文件543项全部通过。

清理补充：本轮最初失败的Playwright连接工具曾打开Chrome扩展连接页；浏览器URL策略阻止代理观察/关闭该协议页面，未绕过策略，该连接页如仍打开需用户手动关闭。上述应用验证页和两个临时服务已关闭，临时数据库目录已回收。

## 提交前最终复验（2026-10-26）

用户随后授权将Spec130提交并push到`codex/project-execution-root`。上文“不提交/push”是实现验收时点；本次只交付该功能代码、测试与文档，`.venv`、`research/`和构建产物不进入提交，不合并main、不打标签或发布。

对相对`581f833`的完整差异进行了架构、安全及异常组合复核。复现并修正“停止保留文字＋图片＋未知请求成功重试”后残留已发文字：冻结请求分别持有合并正文与原始草稿，回执按原始草稿清理，后来编辑的新草稿保留。原请求中的待发送文字在冻结期间只读，新文字仍可编辑。新增回归先红后绿，专项8项通过；最终前端全量**68文件546项通过**，TS/Vite构建362模块、0错误，原bundle大小warning保持。

最终候选在`build/spec130-20261026/ship-final/`，484个成员，SHA-256为`fe223b28916ad14f6f4b561f9693864d5be0fe60f5060d46abc2cf1ba6465a6e`；354个Python源码及4个Web资源与当前源码逐字节相同。Twine、源码外全新环境安装及完整离线smoke通过，含图片20×10规范化、历史重开、版本拒绝及删除不可读，0供应商请求。安装报告为`ship-final-install-smoke.json`，复验日志使用`ship-*`前缀；此前`accepted-candidate`保留为最初验收证据，不代表后续修正后的前端字节。11项配置示例与中央默认一致，68个变更源码/测试均不超过500行，compileall、pip check和diff检查通过。最终Git交付以分支记录为准。

提交前后端全量命令：`PATH="$PWD/build/r1-01-test-assets/bin:$PATH" .venv/bin/python -m pytest tests -q`，结果 **5532 passed in 588.70s (0:09:48)**，包含全部535个产品测试文件，无失败或跳过。前端命令`npm --prefix frontend test -- --run`结果546通过；`npm --prefix frontend run build`退出0。原分组结果保留为此前验收时点，提交使用本次完整全量结果。
