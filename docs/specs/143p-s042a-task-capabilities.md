# Spec143P：S04.2a 任务能力、外部网络与项目依赖

状态：2026-11-01用户确认后实施。B首版取消普通容器工具逐次审批；关闭网络指关闭外部访问，保留控制、必要保存和S05受限模型通道。S04.2b继续根子Agent、后台收束、停止与显式恢复；本片不提前声称其完成。

## 1. 决策与边界

- 现有本机模式和A消费者仍使用自己的权限；只修改B默认策略。B固定注册的文件/Shell/AITest工具在任务信任域内自动执行，仍经过工具准备、任务/路径校验、显式deny规则和必要保存；NoAsk令未获准操作拒绝，不建设远端逐工具审批API/UI。配置拒绝B的Default/Auto/BypassApprovals模式，避免生成无法回答的审批或跳过其他显式边界。
- Coordinator不运行任务Shell，不取得Docker socket，不读取任务库；任务不取得所有者/后端/真实模型凭证。结果应用、配置扩权、发布/部署仍是独立所有者操作。本容器同UID代码可以影响Runner/state，审批也不构成对任意Shell的独立安全边界。
- 参考：ZCode a60ba6a的05-permissions及PermissionService在yolo下允许普通工具、保留工具级交互；DeepSeek ce7073d的approveEscalation只对严格扩大模式请求批准，never拒绝ask；OpenAI官方sandboxing区分技术边界和审批。它们不证明本项目已通过实机门禁，也不要求照搬远端主机模式的审批。此前143a/f/g的完整B审批计划由本合同替代，143i/m等历史验证仍保留其时点。

## 2. 网络部署合同

首版复用开发机已配置的专用bridge及宿主策略，同一Coordinator/后端实例采用同一部署策略；不新增逐任务网络选择器、任意目的地代理或在线切换防火墙API。

| 部署模式 | 必要内部通道 | 依赖下载/Windows业务隧道/其他外部访问 |
| --- | --- | --- |
| 受控外部网络 | 允许当前任务接口及受限模型入口 | 按开发机既定网络策略允许 |
| 关闭外部网络 | 同上 | 全部拒绝，包括公共包源、业务中转、其他IPv4/IPv6外部路径 |

执行策略在容器外部，不用环境变量或工具deny冒充网络隔离。后端命名网络下不传不受支持的OpenSandbox networkPolicy；不用Docker network=none切断Runner。管理员配置宿主INPUT/转发/出站及代理，保留已建立连接返回流量，验证非授权宿主服务/管理面/其他任务拒绝。代理只接受策略允许的目标，禁外网时不能留下可转发外网的代理或Windows中转入口。必要模型入口仅S05固定上游/协议，不得成为通用CONNECT代理。

服务配置只把显式tool_environment传到任务bootstrap，再传工具子进程；不自动复制Coordinator进程环境，不将代理变量注入Runner解释器启动。端口/地址/包源由部署配置给出，不硬编码。当前没有自动修改宿主防火墙能力；联网/禁网证明必须用实际容器验证，不因配置标签或S00旧样本自动通过。

## 3. 项目依赖和调用链

镜像继续固定只读Runner环境与预装Kit/pytest的测试环境。项目可由Agent显式在工作区创建.venv并pip安装，不自动扫描requirements、安装包或运行setup脚本。联网/代理不可用、包构建失败等作为普通工具失败返回；不自动提权apt或修改/升级Runner依赖。

所有B工具在每次执行时使用同一环境规则：工作区.venv/bin优先，然后显式工具配置路径，再沿镜像PATH。Shell中的python/pip与AITest使用同一查找规则；AITest通过所选python -I -m aitest_kit.cli调用，不再用全局aitest脚本的固定shebang。配置禁止覆盖PATH/PYTHONHOME/PYTHONPATH/VIRTUAL_ENV等解释器身份字段；普通代理/包源/测试服务变量只进入工具环境，BASH_ENV等原防护仍保留。父进程环境不变。

新建venv默认没有Kit/pytest，用户或Agent需按模板对应版本与项目依赖显式安装；缺Kit返回实际模块导入错误，不暗中改用镜像Kit；.venv存在但Python缺失/不可执行时明确project_python_unavailable，Shell仍可修复。-I阻止工作区同名aitest_kit覆盖所选环境安装包。工作区保持时复用.venv；新任务独立准备。交付代码/requirements或lock，不将Linux虚拟环境搬到Windows；当前输入排除策略和产物选择按143b，不擅自修改。

正常链：CloudSettings(runner_permissions/tool_environment) → RunnerControl.prepare私有bootstrap → RunnerBootstrap → open_runner → runner_tools(root,environment) → 现有工具/HostProcessSystem → 每次按PATH选Python → pip/pytest/Kit输出 → 现有必要保存/历史ACK。AITest工厂接一个仅B传入的project_environment路径，在调用时拒绝损坏/缺失的项目Python，再直接传现有settings给HostProcesses；普通本机调用不传此参数，保持原行为。无新runtime、回调注册或通用解释器resolver。

失败链：显式deny或未授权工具 → 原权限准备/执行拒绝，无人审批；pip下载/安装或Kit导入失败 → 原工具错误回填 → 模型可分析失败，不能伪造安装成功；必要保存失败仍unknown/阻止回收。创建venv后的后续工具调用动态解析PATH，不将启动时Python选择缓存到后续执行。

## 4. 修改与验证

修改：cloud settings/bootstrap字段与生产映射；B runner工具装配与现有AITest工厂的可选项目环境路径；当前143/a/c/d/e/f/g合同；新增定向测试和部署使用说明。不修改真实配置、环境、现有用户数据、A权限算法，不添加/升级依赖，不提交/部署。

先红后绿覆盖：B默认write/bash连续执行无审批且原ID不重放；显式deny无副作用；B不接受交互/全绕过模式；工具环境bootstrap往返、非法名称/解释器变量拒绝；Shell创建venv后下一次AITest使用相同Python、缺Kit明确失败、父环境和Runner解释器不变；真实离线wheel安装/import及现有Kit运行。使用现有确定性模型/进程，不改conftest或旧断言。

网络两模式在最终Linux部署按上述正负矩阵单独验证；本机venv/进程测试不替代公司代理、包源、真实RPC和模型能力。发布前沿现有CI分片、前端构建、标准wheel构建及diff检查；本片不自动跨入S04.2b/S05。

## 5. 本机实现与验收（2026-11-01）

已接线：CloudSettings/RunnerBootstrap默认NoAsk＋allow(*)，显式deny仍优先，拒绝交互和Bypass模式；tool_environment从私有bootstrap到原工具环境，不改父环境；Bash与AITest动态使用项目Python；现有AITest工厂增加仅B使用的可选project_environment路径，项目Python坏则拒绝、不回退，-I阻止同名模块遮蔽。普通本机消费者不传此参数，原配置/审批继续运行。当前143/a/c/d/e/f/g的旧B审批承诺已替换，未删除仍有消费者的本机权限/审批代码，无新增runtime、resolver或执行包装。

| 验证 | 实际结果 |
| --- | --- |
| TDD | 初轮8项预期失败；Kit同名遮蔽1项、损坏Python2项分别先红后绿；新增合计19项 |
| 云端/Kit/存储方向/会话集成专项 | 319 passed |
| 完整后端两分片 | 3081＋3373＝6454 passed；JUnit身份核对原6435全部保留、新增19，无失败/skip |
| 前端 | 69文件568 passed；TypeScript/Vite构建通过，仅既有chunk-size提示 |
| 标准发行候选 | 四目标wheel/Twine通过，同源450 Python文件、application SHA一致；五个本轮生产文件逐字节等于当前源码；Mac源码目录外安装通过 |
| 真实本机进程 | Kit0.6.0＋grpc1.85.1实际安装；B的Kit工具成功、同一项目Python的pytest 1 passed、同名模块未执行、必要历史持久化 |
| 其他 | pip check无冲突、compileall、git diff --check、局部文档链接/结构检查通过；安全/架构复核问题关闭 |

证据：[verification.json](../../build/spec143-s042a-20261101/verification.json)、[实际Kit进程](../../build/spec143-s042a-20261101/real-kit-probe.json)。完整命令在verification；复验专项使用已按标准准备的native-assets/bin加入PATH：

```sh
PATH="$PWD/build/spec143-s042a-20261101/test-assets/bin:$PATH" .venv/bin/python -m pytest tests/cloud tests/test_aitest_kit_tool.py tests/test_storage_dependency_direction.py tests/test_session_lifecycle_integration.py -q
```

首轮完整回归缺fd，按标准准备本轮Mac native-assets后原两项失败用例通过，再完整重跑；没有改断言/skip。真实Kit探针是Mac子进程/确定性模型，四目标构建也不等于Windows/Linux原机运行。本片未启动Docker/修改防火墙/公司部署，外网两模式、公司代理/真实RPC和S05模型仍待最终容器复验；0供应商/业务请求。真实配置、依赖版本、A数据、Spec142、Docker资源均未改，未提交/发布。

唯一下一片S04.2b：接现有子Agent到固定任务装配、幂等停止本轮/根子及后台收束、必要保存/历史确认、显式恢复unknown且不重放未知外部副作用；先形成详细Spec，再按正常/故障确定性验证推进。S05/S06和其他切片不提前算完成。
