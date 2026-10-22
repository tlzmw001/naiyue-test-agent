# Spec125-06：后台任务组合验收、性能和统一辅助侧栏

2026-10-21用户授权实施06，并要求右侧统一图标及功能页签；补充确认纳入上下文，同时保留输入框上下文图标直达。承接125主Spec及文件地图，不发布、不改真实配置/用户库，不做后台子Agent。

## 当前链路与修改范围

运行链：Shell Tool → 已有BackgroundProcessManager → HostProcessRunner/ProcessCapture；新用户run → 终态快照 → SystemMessage → task_output。05提供只读HTTP/CLI管理，前端三套独立侧栏框架由App分别开关。本片不新增任务执行或通知拥有者。

前端把三个重复的外框/关闭入口迁到ActivitySidebar，顶部只留一个带可访问标签的图标。固定三个实际页签：子智能体、后台任务、上下文；以后新增当前功能时可在相同定义和内容区域加入，不建设动态插件注册表。App持有打开/页签状态，保持工作区后台hook；子Agent详情绑定session/revision，切会话清除旧选择。上下文旧图标和/model等快捷入口改为选择对应区域；关闭/再次点击/Esc收起并返回实际触发按钮。页签支持方向键/Home/End。各内容组件移除旧外框和关闭props，测试与实际调用方一起迁移，不保留只服务旧测试的分支。

视觉沿现有CSS Modules/theme tokens/system字体，440px右侧，375px覆盖；图标用现有Icon组件扩展几何侧栏图标，活动点/标题表达运行及未知状态，数量在任务页签内显示。无数据展示空态，读取失败与0分开。切换页签保留已挂载内容选择；后台输出只在对应页签可见时读取，元数据仍按05可见页面规则查询。子Agent消息按钮直接打开子Agent页签；上下文图标直接打开上下文页签。

## 验收脚本与消费者

- tests/test_background_task_integration.py：沿真实图/持久应用，服务启动→第二轮工具发健康请求→task_stop，后台结果→终态系统状态→task_output；检查一次启动、消息/事件配对、退出/字节及下一轮不重放；应用重开注册表空、原归档可读。
- scripts/release_background_smoke.py：供源码测试、已安装release_smoke实际调用，固定离线模型、真实Shell进程、临时库/文件；包含工具发现、后台回执/查看/停止、关闭及正式重开，不恢复后台进程。失败断言在返回前显式执行，不能被观察隔离吞掉。Windows选择PowerShell，其余Bash，原机证据独立报告。
- scripts/release_smoke.py接线新增验收；源外worker使用绝对脚本路径运行时，新增同目录脚本可被import，不能依赖源码包路径。配套test_release_background_smoke.py验证正常报告和故障断言真实传播。
- scripts/benchmark_background_tasks.py：临时工作区/应用/固定模型和实际子进程，记录无任务、静默任务、受控输出、HTTP管理读取下聊天轮次、元数据/尾部、停止/关闭耗时；P50/P95/最大值/样本数与环境写JSON。另测已有结束记录增加后的元数据成本及洪峰触限。参数由CLI提供，默认值是可更改的基准设置，不改产品配置。无供应商请求；不以单机样本承诺普遍性能。

基准每个负载用新会话等长轮次/相同固定模型，先预热；安静/输出负载用同样受管进程，采样后台输出/读取与主任务并行。记录总量/上限与收尾事实，避免把后台已停的洪峰当作持续输出负载。基准不向测试写死机器耗时门槛，算法/边界用确定性断言验证。

## 正常与失败链

正常：图标 → ActivitySidebar所选页签 → 既有内容和hook → 原HTTP/app/manager；停止完成后旧模型run不被唤醒。上下文快捷键 → 同一容器的上下文页签，不另开第二面板。

失败：子Agent/上下文请求在切会话时由既有scope隔离；后台输出在切页签后取消，迟到响应不能覆盖新选择；停止未确认仍保留lost及提示。基准/安装验收发现未回收进程或归档不一致必须失败，finally仅负责必要收尾，不能把结果改成成功。

## 验证要求

UI先补统一入口/三页签/上下文直达/键盘收起/切会话范围/旧入口迁移的失败测试，再最小实现；脚本先补缺失入口红灯，再完成实际行为断言。运行专项和全后端/全前端/TS/Vite/结构编译依赖检查；四目标标准构建/Twine/同源码核对，Mac ARM64源外安装，其余平台只报告构建证据。真实浏览器桌面/375px浅深色与真实临时数据检查，输出耗时原始JSON和环境；阶段结束更新handoff/future-work和当前文档，用户理解/业务效果不由这些测试推定。

## 实际接线与验收（2026-10-21）

本片已完成。ActivitySidebar真实容纳三个页签；App仅一个辅助图标，ContextTrigger及子Agent消息跳转选择同一容器。三个旧内容组件的aside/宽度/关闭props/旧按钮样式已移除，调用与测试同步迁移为内容region；上下文当前估算/最近请求/用量/诊断、子Agent正文/审批跳转/停止，以及后台输出的原hook均保留实际消费者。没有保留只为旧测试存在的独立框架或兼容props。图标再次点击/按钮/Esc收起，焦点返回最后入口；上下文快捷入口在容器已打开时也可Esc收起。

新增8项后端、3项前端，原有迁移断言保持。入口/参数/UI与路径的实际红灯分别验证；额外增加的切页签暂停输出接线回归直接通过，不把它称为TDD红灯。最后一项路径回归在原脚本确定复现HTTP task_not_found后，修正基准传入路径为resolve()，Web隔离检查未放宽。源码/安装共享脚本覆盖显式与自动后台的正式图：服务跨轮健康请求、task_stop回填与实际PID退出、结果完成后的下一轮SystemMessage和task_output、正式重开无旧活动记录且原日志字节不变；故意注入错误的进程检查器会使验收失败。安装器发现宿主Shell后传给临时worker的fixture设置，保留原PATH仅安装环境及包内fd/rg门禁；没有修改用户配置或产品默认Shell发现方式。

验证命令与结果：

- PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest -q --tb=short：**4192 passed, 1原有LangChain Beta warning in310.25s**。此前为补基准路径回归而主动中断的一轮日志保留，不算通过。
- npm --prefix frontend test -- --run：**50文件409 passed**；npm --prefix frontend run build：TS/Vite通过、330模块，保留分包体积警告，0错误。
- 179产品Python、41脚本AST/编译，122前端源及上述Python文件500行，pip check/diff检查通过。
- 标准build_release四目标macos-arm64/linux-x86_64/linux-arm64/windows-x86_64全部构建/Twine通过；每包179个产品源文件和4个前端产物与当前目录逐字节一致。Mac ARM64源外安装通过，background_tasks报告服务跨轮/状态正文/空注册表重开/归档保持/收尾全部passed；其余仅构建，未声称Windows/PowerShell或Linux原机运行。
- 内置浏览器用临时库/真实受管服务/正式图产生的子Agent记录，检查三页签、上下文直达及Esc焦点、后台停止1→0；1280px/375px浅深色截图，窄屏scrollWidth=375，浏览器警告/错误为空。页面和服务已关闭，两个后台任务cleanup_confirmed=true，固定模型3次、真实供应商0次。

证据：build/spec125-06-ybcllk_s/{backend.log,frontend.log,frontend-build.log,verification.json,dist-final/,twine.log,install-final.json,benchmark-isolated.json,ui-result.json}和ui-*.png。dist/为最后Esc修正前的中间产物；当前结果以dist-final为准，未发布。

## 侧栏顶部调整（2026-10-22）

按用户补充要求，移除“会话辅助”独立标题行；三个页签直接处于侧栏最上方，与收起按钮同一行。扩展既有App键盘页签用例，先确认旧标题导致红灯，再调整ActivitySidebar结构/CSS；保留原页签、内容、上下文直达和焦点行为。

本次验证：npm --prefix frontend test -- --run src/App.test.tsx为11 passed；npm --prefix frontend test -- --run为50文件409 passed；npm --prefix frontend run build为TS/Vite通过、330模块、0错误，保留原体积警告；git diff --check通过。真实浏览器1280px/375px检查顶部布局、任务切换、Esc收起/返回入口焦点；窄屏scrollWidth=375，控制台warn/error为空。证据build/spec125-06-top-tabs-YjHHAkvI/{frontend-red.log,frontend.log,frontend-build.log,desktop.png,mobile.png,ui-layout.json,ui-result.json}；临时页/服务已关闭，两任务cleanup_confirmed=true，固定模型3次、供应商0次。本轮未改后端、未重跑后端测试或重建wheel；前节dist-final为本次布局调整前的候选。

## 性能结果与证据边界

命令：.venv/bin/python -m scripts.benchmark_background_tasks --output build/spec125-06-ybcllk_s/benchmark-isolated.json（其余采用脚本可调默认参数）。环境macOS26.4 ARM64/Python3.12.14，每组3次×40轮，每次预热2轮；固定异步模型、真实持久化run、后台受管Python进程，管理查询经鉴权ASGI HTTP。各组相同轮数/内容，普通刷新3秒，压测刷新50ms，输出目标64KiB/s。单轮耗时不含轮间50ms间隔、真实供应商延迟或浏览器绘制。

| 场景 | 单轮P50 ms | 单轮P95 ms | 最大ms |
| --- | ---: | ---: | ---: |
| 无后台基线 | 43.35 | 90.22 | 101.12 |
| 一个静默任务 | 42.78 | 89.18 | 99.40 |
| 持续输出，面板未读尾部 | 43.46 | 91.61 | 102.14 |
| 持续输出，正常3秒读取选中尾部 | 43.05 | 92.67 | 102.91 |
| 持续输出，50ms管理读取压测 | 44.20 | 99.33 | 107.28 |

正常读取场景相对基线P95差2.44ms；单机这组数据未见明显退化，不能证明零影响或统计显著性。普通列表/尾部每组仅6次，P95分别最高1.90/1.36ms；高频压测230次，尾部P95 1.60ms/最大41.58ms，压测周期不是产品默认周期。主动停止每组3次，所有组最大46.57ms；带活动任务关闭应用3次P50 42.25ms/最大42.30ms，均实际回收进程；小样本P95等于最大值，不把它写成稳定上界。

额外仅元数据扩展测试使用合成终态注册记录，不冒称同时运行1000个进程：0/100/1000条各40次，列表P95 0.17/0.78/2.73ms，1000条最大3.33ms。当前列表随本应用记录数扫描内存，不据此承诺任意历史规模固定耗时。真实洪峰写满64MiB后output_limit并确认收尾，2.59秒；只验证额度与停止，不把该已结束洪峰当作持续输出组。

第一轮benchmark.json同时有本轮测试/构建负载，output_open P95 228.11ms/最大625.45ms。完成回归/构建并关闭浏览器服务后独立复测，以上为benchmark-isolated.json；保留两份原始数据。复测没有重现该尾部幅度，但尚不能断言唯一成因或其他机器同样表现。未为测量结果预设硬通过耗时或修改产品策略。

真实配置/用户库/依赖/版本保持，保留并行学习/调研文件，未提交/push/发布。Spec125六片本机实现和验收已收束；Windows/Linux原机、真实模型业务效果仍独立待验，下一候选沿future-work为“已结束子Agent实例续跑”，先讨论，不自动实施。
