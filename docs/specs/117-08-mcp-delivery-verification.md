# MCP-08：交付验收与跨平台安装门禁

状态：2026-10-20，用户已确认取消Intel macOS，发行范围改为四平台；本机门禁
已完成，Linux双架构/Windows原机CI仍待验，不声明全部平台交付通过。00—07/09已完成本机验证，本片不提交/push/tag/发布，
不改真实配置/用户数据库/依赖/并行Spec118及课堂。当前工作树全部未提交成果保留。

## 用户确认：四平台发行范围（2026-10-20）

用户确认“那不用支持了，这四个机型够了”。当前支持目标为macos-arm64、
linux-x86_64、linux-arm64、windows-x86_64；macos-x86_64退出后续发行范围。
保留当前SDK/cryptography等依赖版本，不以降级恢复Intel支持。历史已发布文件及
先前五包验收证据不删除、不覆盖；本次只生成新目录的四包候选，不提交或发布。

当前调用链：native-tools.json的targets → build_release/native_assets的命令行选择、
CI targets输出 → 四平台wheels/安装 → complete的inspect_release → Publish复用同一
matrix及发布后安装。工具资产名单与targets同步移除Intel；YAML已消费配置无需复制
四份名单。完整发行校验必须接受恰好四个不同目标、拒绝缺包/重复包/额外旧Intel包，
保留版本一致及应用源码指纹一致检查。单机运行成功并不代替其他三个目标的原机CI。

修改范围：release/native-tools.json、scripts/release_artifacts.py、对应发行测试；
README、releasing、project-plan顶部、MCP说明/总Spec/future-work/handoff同步当前范围。
先迁移受支持目标的精确断言并补旧Intel包拒绝测试，观察红灯后改实现；按原流程
重建四包、完整归档/Twine/源码一致性检查、macARM严格安装，运行发行专项及全后端。

以下为调整前五平台的历史验收记录；其中Intel阻碍和待用户选择已经由上述决定关闭，
旧命令/报告保留当时事实。当前四平台结果在文末追加，不回写旧产物或日志。

## 当前链、缺口与范围

正式入口已接nyt/nyt chat/Web、mcp list、根子调用、配置刷新和TTL；后端全量3313、
前端366及macARM安装/专项探针已有证据。CI原有Linux全量测试、五目标wheel构建/
verify_wheel_install和完整发行包一致性检查，但标准release_smoke只验证内置工具，
MCP专项探针还在忽略的build目录，不能成为五平台持续门禁。

本片将MCP最小完整验收接入原安装worker：复用原OfflineModel、官方SDK合成fixture，
新增scripts/release_mcp_smoke.py组织安装后的禁用路径、只读list、运行中新增/选用/
审批/同会话复用、显式子角色、普通失败/未知结果、停用旧待审、新启用/TTL及冷历史。
不建第二套Agent运行时/连接管理器，不新增对外产品接口或依赖。验证辅助脚本可读
checkout中的现有纯SDK fixture；被测naiyue包仍由原worker显式断言来自新venv。

正常链：verify_wheel_install建立干净HOME/venv、安装指定hash wheel → release_smoke
原Web/内置验证 → MCP helper复用离线模型类与真实SDK进程 → 返回结构化结果 → 原
worker输出合并install报告。CI五平台已有相同命令，因此自动纳入门禁，不需改发布流程。

失败链：任一配对/计数/恢复/关闭断言或必要IO失败 → worker非零 → 安装脚本失败 →
CI阻断。helper的finally关闭App/所有夹具，不能把断言作为可降级观察吞掉。过程无
真实模型/第三方业务请求，诊断/报告不输出URL、凭证、第三方正文或配置原文。

修改面：原release_smoke.py、新release_mcp_smoke.py、对应test_release_mcp_smoke.py，
必要的当前文档/Spec/handoff；无产品代码预计变更。新增helper因为独立验收场景与
报告职责，避免把全部MCP逻辑塞进原Web场景；传入已有模型类，避免复制模型实现。
Windows进程存活检查只用stdlib并按实际结果断言；不把POSIX的os.kill(pid,0)直接
用于Windows，以免其信号语义不同。若检查不可用明确失败，不假报清理完成。

## 五平台证据边界

当前只有Darwin ARM64，没有Docker/Podman/其他平台执行器。采用原build_release对
五目标验包、完整release源指纹/白名单/许可证/原生资源SHA与格式检查，并逐目标
验证Python3.12按目标标签可解析的二进制依赖。macARM实际干净安装并运行增强worker。
Windows、Linux双架构、macIntel未运行，只标“构建/依赖通过、原机运行待CI”；已有
旧版本CI不代表这批工作树通过。不为远程验收擅自push/发布。五平台运行是剩余证据，
本片不能声称全部平台验收完成。

## 验证顺序

先失败测试确认helper缺失/验收未接入，再最小实现；故障注入要求检查器失败能使
helper失败且仍清理。随后发布专项、全后端/前端/构建、compile/pip/AST/500行/diff，
原stage_frontend一次、五wheel构建/inspect_release/twine、各平台二进制依赖解析、
macARM原verify_wheel_install增强安装。无业务/供应商效果声明，真实服务另计。

收尾提供每平台独立状态、当前正常与失败主链、全部入口/配置/限制、精确命令和
结果；同步使用说明/总Spec/future-work/handoff。08保留其余四平台原机门禁待授权
提交后CI，不把未运行记通过，也不自动开启OAuth/resources/prompts等后续能力。

## 验收发现后的范围补充（实施前确认既有合同）

本机跨平台解析已复现macIntel缺cryptography==50.0.2二进制包。上游49.0.0明确移除
x86_64 macOS支持。其余四目标按目标标签各73份wheel可解析，但仍不代替运行。保留原五目标和
依赖，不为过门禁降级/移除平台；是否保留Intel已向用户询问，等待独立决定。

原verify_wheel_install只限制本包二进制，依赖可能在CI因预装编译器回退源码编译。
这不能验收已确认的纯pip免编译目标。增加该脚本一行修改：--only-binary=:all:，使
缺依赖wheel时立即失败；用“源分发可用、二进制缺失”的模拟pip行为先观察假通过
红灯，再实施最小修改。无需修改平台配置或产品依赖，这是原交付合同的门禁补齐。

## 当前验收结果与正式入口

新增helper173行，原worker148行；verify_wheel_install只加强一处二进制安装限制。
包内157份生产Python完全保持07快照，没有新增SDK封装/产品功能或真实配置变更。
原CI与published-install均checkout同一提交并调用verify_wheel_install，新增MCP
场景是必经路径，不增加可跳过flag；fixture在checkout，naiyue被测包来自site-packages。

| 入口 | 当前实际链及边界 |
| --- | --- |
| nyt | cli.main → Web lifespan → persistent factory → 注册/共享图；标准安装验证Web/API与内置工具 |
| nyt chat | cli.main → application.cli / AsyncCLIApplication → 同一App loop；原工具/审批/保存保持，09命令已有真实桥测试 |
| nyt mcp list | mcp_client.cli → load_settings → discover_server → finally关闭；安装探针检查2工具、0tools/call |
| Web刷新工具与模式 | 原按钮 → MCP POST → arefresh_mcp → 准备/发布 → 原GET目录；源测试验证认证、失败保留、顺序、无自动勾选 |
| 根/子MCP | ToolSpec → 原JSON Schema/权限/精确许可 → SessionService.borrow_mcp → 单次SDK RPC → ToolMessage；安装检查根1/2、子1/2且不同PID |
| 恢复与TTL | 原SQLite历史读取不执行模型/工具；07占用/借用声明 → 单调deadline → 原_retire；安装验证旧待审停用为not_executed及TTL新进程1/1 |

失败消息仍区分tool_error、not_executed、outcome_unknown；安装探针显式检查三者、
计数和后续模型继续。该验证不是全协议/真实服务/供应商质量或权限隔离的生产证明。

TDD与检查器：首轮2项缺模块红灯；helper接入后检查器的CLI模块入口和历史消息形状
两处按真实API修正（History返回BaseMessage，非Web字典），产品代码不改。2项专项
加既有release测试47 passed in6.86s。发现依赖源码回退后补一项“无二进制但源码可
编译”的模拟pip：旧门禁假通过，看到DID NOT RAISE红灯；只将only-binary改:all:
后48 passed in6.93s。故障注入确认清理断言会终止helper，finally仍关闭实际进程。
没有改conftest/共享helpers或放宽断言、skip。

前一全量快照3315 passed in229.84s；二进制门禁追加后另跑最终全量，最终结果另见
下方记录。前端46文件366 passed in4.35s，TS/Vite成功（158ms，保留既有chunk提示）。
159份产品/worker源码AST/500行及compile、pip check/diff通过；增加installer一份
同样检查，不改产品依赖。Twine固定使用项目dev已有6.2.0，在忽略目录临时tools venv
运行；未修改应用venv或全局环境。

五目标均由原build_release与同一stage_frontend构建，每包287文件；inspect_release
检查五目标、版本/白名单/原生格式及SHA/许可证/相同应用指纹，通过。157份生产
Python逐文件与每个wheel一致。共同application_sha256：
`18acb84a3c53c3df01959cb92a251714b47692e6375124d8ad2576829dcd50a2`。
候选沿用当前源码0.1.1，仅用于验收，不是新的PyPI发行物。

| 目标 | 结构/源码/原生资源与Twine | Python3.12目标标签二进制解析 | 原机执行 |
| --- | --- | --- | --- |
| macos-arm64 | 通过 | 通过，73份wheel含本包 | 本机增强安装通过；全部依赖wheel限制另复验 |
| macos-x86_64 | 通过 | 失败：cryptography==50.0.2无对应wheel | 未运行；先解决依赖/支持范围 |
| linux-x86_64 | 通过 | 通过，按CI Ubuntu22.04/glibc2.35 | 未运行，待CI |
| linux-arm64 | 通过 | 通过，按CI Ubuntu24.04/glibc2.39 | 未运行，待CI |
| windows-x86_64 | 通过 | 通过，CPython3.12/win_amd64 | 未运行，待CI |

二进制解析不冒充安装/运行：另核对本机pip26.2.1的match_markers源码，
--platform调整wheel标签，依赖环境marker仍用宿主环境（只覆盖extra），不能据此
承诺目标系统条件依赖完整可用；该项明确限为标签解析证据，完整环境交给原机CI的
严格安装。Linux结果也不承诺第三方包支持所有glibc版本。原CI的
source pytest只在Linux执行，五平台install job将执行新增MCP门禁；本轮未推送工作树，
没有借旧提交CI或旧PyPI的通过结果替代本轮证据。

阻碍证据：PyPI50.0.2文件元数据与官方[49.0.0变更](https://cryptography.io/en/50.0.2/changelog/#v49-0-0)
确认Intel支持移除；[50.0.0变更](https://cryptography.io/en/50.0.2/changelog/#v50-0-0)
另有安全修复，因此没有为了门禁直接降到最后有universal2包的48.0.1。该旧版本对
本项目的影响尚未独立评估。本轮只记录缺二进制事实，不断言项目存在对应漏洞，
不擅自改变00已确定版本或发行范围。用户已收到Intel是否继续支持的问题。


## 最终命令、结果与待决项

```text
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
3316 passed in 226.00s (0:03:45)

npm --prefix frontend test -- --run
46 files / 366 passed
npm --prefix frontend run build
TypeScript / Vite passed

.venv/bin/python -m pytest tests/test_release_mcp_smoke.py tests/test_release_artifacts.py tests/test_release_workflows.py -q --tb=short
48 passed in 6.93s

for target in macos-arm64 macos-x86_64 linux-x86_64 linux-arm64 windows-x86_64; do
  .venv/bin/python -m scripts.build_release --target "$target" --frontend-assets build/mcp08-20261020/frontend-assets --output build/mcp08-20261020/wheels
done
五次exit 0，5个不同目标、每包287文件
.venv/bin/python -m scripts.release_artifacts --complete-release build/mcp08-20261020/wheels/*.whl
exit 0，五目标共同应用指纹/格式/白名单/资源校验通过
build/mcp08-20261020/tools/bin/python -m twine check build/mcp08-20261020/wheels/*.whl
5 PASSED，exit 0

for target in macos-arm64 macos-x86_64 linux-x86_64 linux-arm64 windows-x86_64; do
  .venv/bin/python build/mcp08-20261020/resolve_dependencies.py "$target"
done
macARM/Linux双架构/Windows exit 0；macIntel exit 1（cryptography 50.0.2无binary）

.venv/bin/python -m scripts.verify_wheel_install --wheel build/mcp08-20261020/wheels/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl --output build/mcp08-20261020/install-strict-macos-arm64.json
exit 0；全依赖wheel限制、原工具/Web/CLI/审批/SQLite与新增install.mcp均通过

compileall / pip check / 160份源码AST-500行 / git diff --check
通过；No broken requirements found.
```

真实增强安装结果：禁用零MCP IO；list零tools/call；运行中新增默认不勾选；根调用
1/2、子1/2且不同PID；已知失败tool_error/断连outcome_unknown后模型继续；停用旧
待审not_executed；审批占用保护/TTL回收后新进程1/1；冷历史无模型或工具重放。
合成tools/call共8次，供应商0，所有fixture进程退出；没有新增真实外部服务。

报告：[最终矩阵](../../build/mcp08-20261020/verification-final.json)、
[严格安装](../../build/mcp08-20261020/install-strict-macos-arm64.json)、
[全量](../../build/mcp08-20261020/pytest-strict-final.log)、
[五包检查](../../build/mcp08-20261020/archive-final.jsonl)、
[Twine](../../build/mcp08-20261020/twine-final.log)。每目标build/dependency日志与
命令参数JSON在同目录，verify脚本SHA也已保存；之前install/pytest结果是加强门禁
前快照，当前以上述strict-final为准。

本片新增3项后端测试，未改生产运行代码/依赖/平台配置/前端；CI YAML不必改动，
现有安装命令已必经增强worker。当前未提交/push/tag/上传；四个平台原机尚无本轮
CI证据，Intel先受已确认的二进制依赖阻碍，不能把08或五平台宣布全部完成。
唯一待决：用户是否继续要求Intel macOS支持；选择后单独处理兼容或发行范围，再
在获授权的提交上取得对应原机CI证据。没有主动开启其他MCP能力或原业务跟进。


## 四平台范围调整后的实际结果（2026-10-20）

先迁移CI矩阵精确断言、补目标/工具资产一致和旧Intel额外包拒绝断言，原配置/
写死五包数量触发3 failed、5 passed的预期红灯；移除目标及两工具对应资产，
inspect_release按配置目标数和集合校验后，发行专项50 passed in7.38s。
新增2项测试；原缺包/重复/版本/源码指纹检查保留，未改测试基础设施。

```text
.venv/bin/python -m pytest tests/test_release_workflows.py tests/test_release_artifacts.py tests/test_release_mcp_smoke.py -q --tb=short
50 passed in 7.38s
PATH="$PWD/build/release-native-0ff7df8/bin:$PATH" .venv/bin/python -m pytest tests -q --tb=short
3318 passed in 229.25s (0:03:49)
npm --prefix frontend test -- --run
46 files / 366 passed
npm --prefix frontend run build
TypeScript / Vite passed, 141ms（既有chunk提示保留）

for target in macos-arm64 linux-x86_64 linux-arm64 windows-x86_64; do
  .venv/bin/python -m scripts.build_release --target "$target" --frontend-assets build/mcp08-four-platform-20261020/frontend-assets --output build/mcp08-four-platform-20261020/wheels
done
四次exit 0，各287文件；157份Python与当前源码逐字节一致，前端重建与共用快照一致
.venv/bin/python -m scripts.release_artifacts --complete-release build/mcp08-four-platform-20261020/wheels/*.whl
exit 0，四个不同目标、版本/应用指纹/原生资源/白名单全部通过
build/mcp08-20261020/tools/bin/python -m twine check build/mcp08-four-platform-20261020/wheels/*.whl
4 PASSED，exit 0

.venv/bin/python -m scripts.verify_wheel_install --wheel build/mcp08-four-platform-20261020/wheels/naiyue_test_agent-0.1.1-py3-none-macosx_11_0_arm64.whl --output build/mcp08-four-platform-20261020/install-macos-arm64.json
exit 0，严格全依赖wheel安装及原工具/Web/CLI/SQLite/MCP全部通过
compileall / AST-500行 / pip check / git diff --check
通过，No broken requirements found.
```

四目标另用本目录resolve_dependencies.py对新候选执行原pip download二进制标签
检查，均exit 0，每目标73份wheel含本包。仍保留上文宿主marker限制，不当作其他
操作系统的完整安装/运行证据。macARM干净安装的MCP报告：8次合成工具调用、
0供应商；禁用/list/运行时新增/根子复用/错误未知/停用待审/TTL/冷历史与清理全部
通过。Mac ARM64证据来自本机，不声明M5 Pro机型专门测试。

当前结果：[四平台矩阵](../../build/mcp08-four-platform-20261020/verification-final.json)、
[完整归档](../../build/mcp08-four-platform-20261020/archive.jsonl)、
[严格安装](../../build/mcp08-four-platform-20261020/install-macos-arm64.json)、
[全后端](../../build/mcp08-four-platform-20261020/pytest.log)。旧五包和旧日志保留，不覆盖。

范围调整与本机验收已完成，Intel二进制依赖不再是当前目标的阻碍。依赖版本、产品
运行代码、真实配置/用户库、并行Spec118/学习记录保持；README/发行指南/项目计划
和交接已同步。没有提交、push、标签或上传。剩余证据仅为这批提交的Linux双架构
与Windows原机CI；尚未获得对应运行结果，08不能宣布全部平台交付通过。
