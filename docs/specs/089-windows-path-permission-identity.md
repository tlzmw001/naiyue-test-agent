# Spec089：Windows路径授权比较

状态：2026-10-16，用户已批准，已实施接线；本机验证与原机边界见末尾。依赖现有Permission流水线，不改Allow/Deny配置结构、模式、批次审批、Auto或一次性许可协议。

## 问题和边界

真实规则探针：Deny C:/work/private.txt匹配普通路径，但不匹配\\?\C:\work\private.txt。_component_match目前只统一分隔符和casefold；默认策略/会话记住/递归范围/Write占用键也必须采用同一比较语义。此为本机规则函数证据，不是Windows真实禁读绕过。

支持Windows普通盘符绝对路径、普通UNC、合法扩展盘符/扩展UNC，以及工具相对路径。拒绝设备命名空间/卷GUID/ADS/保留设备名、尾点尾空格、扩展路径内.或..及依赖每驱动器cwd的路径；它们是普通文件工具能力/输入错误，不是隐私Deny。规则内这些不支持表示在启动schema校验时报错；文件目标在第一次探测IO前校验，解析出的真实目标也复核。不通过字符串无条件剥前缀改变IO路径。

## 生产修改（路径部分3文件，Write占用键复用088文件）

- tools/paths.py：新增permission_path(value,*,windows=None,pattern=False)→PurePath，Windows分解并校验前缀/anchor/组件，比较表示统一大小写和分隔符，普通与合法扩展表示等价；POSIX使用PurePosixPath。真实IO仍用原Path。prepare_file_subject在原始/别名候选及resolved校验Windows文件表示，包含junction严格解析；verify_file_target/hidden_or_private共用比较。无新增审查对象字段。
- tools/permission_rules.py：path_pattern在现有语法约束前识别Windows表示并校验（扩展前缀的?是语法，不当通配符）；_component_match调用同一比较方法，其余glob按组件运行。Deny仍匹配requested和resolved，Allow仅resolved。
- tools/permissions.py：默认工作区/资源范围、session_allow工作区及真实目标比较、candidate_access父范围都用permission_path；不改变隐藏/私密默认Ask与Deny优先。外部目录批准后普通子目标继承，隐藏/私密未授权仍跳过并反馈。
- tools/write.py：_reserve_target对Windows真实目标使用该比较表示，再沿既有unicode/case占用键；不同合法前缀不得获得两把锁。

正常例：Read \\?\C:\work\private.txt→subject保留原生requested/resolved→规则比较键C:/work/private.txt→Deny命中→原拒绝反馈，零读取。无Deny的合法普通目标仍按Allow/保守默认→模式→必要审批→执行前复核→IO；恢复不重跑Auto。
失败例：设备/ADS等在准备时ToolException回模型；非法规则启动前报ValueError；批准后链接/junction改变沿原目标变化错误且零副作用，不把无法解析当成允许。组件比较确保C:/work2不属于C:/work。

## 验证

先红灯覆盖两方向盘符/UNC/扩展规则等价、大小写/分隔符/glob/目录边界、默认工作区和hidden/resource判断、session_allow、递归Deny/未授权隐私过滤、Write同目标占用；不支持表示在IO前拒绝，规则schema拒绝设备/ADS/歧义名。保留原符号链接/TOCTOU/审批恢复/Auto只审一次回归。macOS可运行纯Windows路径语义测试与局部mock，不伪装Windows文件系统；实际长路径/UNC/junction/ACL放到WIN1原机验收。

与088合计6个现有生产文件，测试另列；无新依赖/服务/配置，不重建create_agent或ToolSpec。完整验收含全量pytest、前端测试/build、compileall/pip check、AST/500行/diff；同步handoff、project-plan、future-work和Windows复盘，未授权提交/push。

参考：[Windows命名空间与设备名](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)。Pi和AgentX没有本项目同一套Allow/Deny比较链，不能据其正常IO推断本项目规则正确。

## 实施记录

- 现有paths.py新增唯一permission_path比较入口，标准库PurePath/ntpath；请求/真实路径保留原生字符串，无新State字段或通用平台对象。规则匹配、默认/资源范围、session_allow、递归候选、执行复核和写目标占用均已接线。
- 首批42测试34失败/8通过后转绿；额外四个先红边界为设备名内空格、UNC流、UNC控制字符和普通路径父组件范围，已补齐。不支持路径在native分支第一次目标lstat前报ToolException；配置同类表示启动schema拒绝。隐藏/隐私Ask、Deny优先、恢复不重跑Auto的原合同保持。
- 新增六种文件工具×sync/async的真实图集成：合成Windows subject遇普通路径Deny，即使BypassApprovals和目录Allow也为config_deny，checkpoint保留原生requested/resolved，工具执行零进入，返回同ID error，not_executed事件和后续模型回答完整。权限事实从已保存批次checkpoint核验；最终结果的批次calls按现有生命周期清空，不为测试保留旧状态。
- 四份新增测试合计73例本机通过；不把PureWindowsPath、环境/句柄mock当成Windows文件系统证明。实际UNC/长路径/junction/文件占用与ACL保留进入WIN1原机验收。最终全量/构建结果见handoff。

最终验证（两片合并）：全量`PATH="$HOME/.pi/agent/bin:$PATH" .venv/bin/python -m pytest -q` → **1707 passed in50.90s**；新增四文件73 passed in1.03s；前端9文件27测试与TypeScript/Vite186模块构建通过。compileall/pip check、283文件AST/105生产文件≤500行及diff检查通过。未改真实配置/数据库/依赖，未提交/push，无真实Windows/Linux或远端模型请求。
