# Spec115：名称清单与文件内容权限分离

日期：2026-10-18—19。状态：已实现、验证并随 `v0.1.1` 发布。下文保留实现阶段的验证快照，最终发行结果见[当前交接](../handoff.md)。

## 目标与当前链路

用户确认：ls 这类只获取清单的工具只需按工作区判断默认授权，不再按隐藏/私有名称过滤；读取、搜索正文与写入另行检查。当前 ls/find 均声明 FilePermission(operation='list')，经整次调用授权后，candidate_access 又把点开头及 .pem/.key 子项过滤，造成清单与实际读取能力混淆。

范围为 ls 和 fd 驱动的 find。grep 即使采用文件名输出，也要先读取正文匹配，仍属 search，不在本次放宽范围。显式 deny、请求路径/真实路径、符号链接、宿主权限、执行许可绑定和输出/停止预算继续生效。

## 行为与接口

- 默认目标授权：list 的请求和真实路径均在工作区内时允许，包括隐藏目录；工作区外仍 ask，Auto/人工/会话授权沿原入口处理。
- 获准目录的普通、隐藏、私有名称均可返回；子项仍须在获准目录真实范围内，不能跟随外链扩大范围。名称可见不代表获得 read/write/search 权限。
- 显式 deny 仍优先于 allow；只移除 list 的默认隐藏/私有判断，不全局修改 hidden_or_private。
- find 保留原生 ignore/glob、条数与字节上限，不因取消权限过滤改变 .gitignore 的发现语义。
- list 的越界跳过原因使用 unapproved_external_target，避免继续以 private 暗示隐藏名受限；search 的原原因保持。
- 不改公开 Python API 签名、配置、schema、依赖或应用审批生命周期。

## 正常与失败例子

正常：模型 ls('.') → FilePermission.prepare/default_decision → 工作区内 allow → 执行许可 → require_file_target → scandir → candidate_access(list) → 含 .agents/.gitignore/.env 名称的清单。之后模型 read('.env') → 独立 read 决策仍 ask。

外部：find(path=外部目录) → ask → 获准后 fd 候选 → 内部隐藏名称可见，外链跳过；无授权时工具不执行。显式 deny 或 IO/路径变化仍按原合同过滤/报告，不伪造完整成功。

## 影响与验收

- tools/permissions.py：只调整 list 的默认决策与候选范围判断。
- tools/ls.py、find.py：描述清单合同。
- 既有 ls/find/权限测试迁移到新合同；保留 deny、别名、取消、限额、错误断言，不减少内容操作检查。
- 新增工作区内隐藏目标、外部授权、清单后 read/grep/write 仍受限的确定性回归；先红灯再实现。
- docs/web-tools.md 同步当前工具语义；handoff 完整切片收尾更新一次。
- 最终全量后端、前端和构建；不提交、push 或发布。

## 实际结果（2026-10-19）

- 修改只作用于 FilePermission.default_decision 的 list 分支及 candidate_access(list)：工作区内隐藏目标允许，获准父目录内的隐藏/私有名称允许；search/read/write 不受这两个分支影响。
- 新增 11 项测试覆盖工作区内外、直接隐藏目标、外部批准、清单后内容仍须审批；原 deny/别名/结果额度/CLI/停止断言保留。Find 额度测试将已获准的隐藏候选改为未获准外链，继续证明过滤在结果计数前，未削减该合同。
- 初跑 14 failed / 5 passed，其中 12 项对应旧名称过滤合同，另 2 项是新测试误把 read/write 目标设为目录；修正测试夹具为私有文件，生产代码没有为测试允许目录读写。工具/权限定向最终 56 passed；搜索/CLI 消费者 19 passed。
- 全量首轮 2961 passed / 2 failed 为进程已收集的 CLI/Find 旧断言；迁移断言后重跑 `.venv/bin/python -m pytest -q`，2963 passed in 118.59s。命令级 PATH 使用已有 `build/release-native-0ff7df8/bin`，未改环境文件。日志 `build/latest-listing-backend-final.log`。
- 前端 42 文件 312 passed，TS/Vite 526 模块构建成功；pip check、compileall、diff 检查通过。正文自动加载属于 Spec114，不是名称授权副作用。
- 无供应商请求、配置/依赖/schema/真实用户库变更；未提交、push 或发布。
