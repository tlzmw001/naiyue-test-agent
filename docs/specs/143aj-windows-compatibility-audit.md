# Spec143AJ：S11 Windows 兼容复核

用户在 S11 EBADF 修复后要求再次排查 Windows 兼容性。当前 R1 本机通过而 Windows 原生待复验；本片只修复已复现的兼容缺陷，不增加新功能、兼容旧 sandbox 或替用户部署。

## 当前链与失败边界

1. 本机选择/捕获：`cli.cloud_inputs.execute(select)` → `select_git` → SourcePlan；`capture` → input/receipt/source JSON → `input_upload.upload`/`freeze_result`。写端依赖 locale，读端按 UTF-8 解析；中文路径在 GBK 下写出非 UTF-8，CP1252/emoji 可直接编码失败。明确 UTF-8，无 schema/API 改动。
2. 取回/验收日志：`apply_changes`/`export_tree` → `cloud_runner.state.write_json`；Windows `scripts.cloud_workflow.journal.Journal` → `cloud_install.store.write`。两者 ensure_ascii=False 但默认编码；保存失败或重启读回失败会阻断原 ID 续接。只明确 UTF-8，保持原子替换、fsync、错误传播。
3. 结果忽略规则：`freeze_tree` → `ignored_paths` → 写临时 Git ignore → `git check-ignore`。中文规则用 locale 写出后与 UTF-8 路径无法匹配，导致应忽略文件进入结果；以 UTF-8、无换行转换重建规则，不改规则内容或 Git 参数。
4. 中断导出：`export_tree` → `_resume_export` → 读取现有前缀 → 追加固定输出。路径 lstat 与句柄 fstat 的 ctime 在 Windows 可含不同语义；跨接口比较全部 identity 会拒绝未改变的文件。初次只比较设备/inode/size，分别核对路径自身、句柄自身的完整身份。真实内容冲突、路径更换及读取中变化仍拒绝，原字节不覆盖。
5. 路径验证：`Selection/input_path` 与取回 `distinct_paths` 共用便携名称规则，遗漏 COM/LPT 后跟上标 ¹²³ 的设备名。按 Microsoft 文件命名规则在 IO 前拒绝，扩展已有保留名集合，不改变普通 Unicode 名称。
6. 云端 CLI JSON 重定向：`cli.cloud.main` 执行成功后用 ensure_ascii=False 输出，在 GBK/CP1252 pipe 中中文/emoji 可使回执输出失败并被误报成命令失败。三种 Windows 本机命令分派使用 ASCII 转义 JSON，解析后的值保持一致，不全局更改控制台编码。`release prepare` 为 Linux 运维入口，保持既有输出。

时间比较同类核查还发现 `delivery_review.observed_entry`（正式代码取回预检）把 source_handle 提供的句柄身份丢弃后与 lstat 比较。改为使用该句柄自己的初始样本；source_handle 仍在退出时检查完整句柄与路径前后身份。

## 实施切片

以上六项独立修复依次验证；每项最多两个生产文件和一份回归文件，不改公共函数签名、依赖、配置或测试基础设施。影响：cloud_files/capture.py、cli/cloud_inputs.py；cloud_runner/state.py、cloud_install/store.py；cloud_runner/result_git.py；cloud_files/delivery_apply.py、delivery_review.py；contracts/cloud_inputs.py；cli/cloud.py。新增 tests/cloud/test_windows_cloud_compatibility.py 覆盖全部入口。

测试局部 fixture 在未明确 encoding 时使用 GBK/CP1252，明确编码和二进制 IO 仍走真实实现；不改变系统 locale。stat 探针只偏移目标文件句柄 ctime，并分别注入 dev/inode/size/mtime/ctime 的后续变化；不是 Win32 原生运行证据。

## 验证与范围

先确认失败测试原因，再逐项最小修复。保留中文/emoji roundtrip、真实 Git ignore、CLI selection、日志重启、原前缀恢复及真实变更拒绝断言。已有 Windows 路径/权限、启动无窗口、共享句柄、进程停止、fsync 回归和云端结果相关测试重新执行；完成后全量 backend/frontend、标准构建与同源制品检查。

同类扫描额外核对文本 IO、文件/目录句柄、路径名/大小写、进程 argv/环境/停止和 Windows/Linux 实际入口边界。Linux 专用 Coordinator/Updater 的 POSIX 调用不能误判成 Windows 本机缺陷；Windows --remote 在本机不会执行 Docker/本地维护，CLI 已拒绝 --settings 本地更新。

无原生 Windows 环境，NTFS ACL、junction/链接权限、杀毒软件占用、Win11 Terminal 和真实 SSH/公司服务仍需用户最终验收。不使用本机模拟关闭该门禁。新候选若生成使用新名称，保留原 S11/R1 包；上传/发布/部署不包含在本片。

依据：[Microsoft 文件命名规则](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)、[Python 3.12 文件接口](https://docs.python.org/3.12/library/os.html)，结合当前存储层 Windows 时间语义回归测试。

## 本机验收结果（2026-11-03）

六项修复接入完成，新增46项回归、后端6336/前端594及标准构建通过。Windows专项239、真实Git与GBK/CP1252冷暖四组合、四平台wheel/Twine、Mac源外新venv产品烟测通过。见[复核报告](../../build/windows-compat-audit-20261103/audit.md)、[机器结果](../../build/windows-compat-audit-20261103/verification.json)。新S11 R2候选s11-891180569c067022，本地准备、未上传/部署；Windows原生仍待用户按[复验步骤](../../build/windows-compat-audit-20261103/windows-retest.md)执行。
