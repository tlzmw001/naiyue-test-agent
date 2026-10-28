# Spec135：Git 输入快照批量对象写入与校验

状态：2026-10-28 已实现并通过完整本机回归与合成基准，待 Windows 原机基准。本片不发布或修改真实配置。

## 当前主链与瓶颈

`SnapshotBuilder.prepare` 用安全文件句柄读取选定工作树字节、检查路径/内容/预算，随后每份文件运行一次 `git hash-object -w --stdin`。建树后 `validate_tree` 对每个 blob 分别运行 `cat-file -s` 与 `cat-file blob`，检查内容、链接和过滤器，再生成快照引用与 bundle。671 文件时这三处约 2013 次 Git 子进程；截图报告 Windows 端完整 prepare 388 秒、其中 671 次 hash-object 为 161 秒。截图中的一次 fast-import 0.30 秒仅是写入对照，不是完整 prepare 目标。

## 目标合同

- 写入侧把已从现有安全句柄读取、完成 `check_content` 与输入预算检查的字节以 Git 官方 `fast-import` blob 流写入**应用拥有的对象库**，保留每个路径的 mode/OID 配对。不得让 Git 按路径重读源文件；不得修改用户仓库、用户 index、用户 refs 或执行 hook/filter。流需要有界暂存，进程失败、输出数量/OID 不匹配和中断均拒绝 ready。
- 校验侧先以 `cat-file --batch-check` 取得所有 blob 的类型和大小并执行原逐文件/总量预算，再以 `cat-file --batch` 按受限输出组读取二进制内容，逐项检查 OID、类型、大小、`check_content` 与链接。保留后续 read-tree、filter 检查、快照及 bundle 合同。GitSource 与 LocalSource 共用校验。
- 不以此更改超时、文件数量、敏感内容、LFS、链接、路径穿越、源变化、权限或环境协议。官方批量接口只是减少进程数；不承诺固定秒数。

## 正常与失败路径

正常：原文件句柄 → 受检字节 → 有界 fast-import 流 → OID 对应原路径 → 独立 index/tree/commit → 批量读取对象内容 → 既有安全验证 → bundle。失败：输入在读取后改变，原最终路径复查仍拒绝；批量输出缺失/乱序/大小不符或 Git 异常则不保存 ready/ref/bundle；敏感内容仍在写对象前拒绝（远端来源在校验阶段拒绝）。

## 影响与验证

预计涉及 `execution/git_snapshot.py`、`execution/git_repository.py` 及 Git 输入相关测试。若共享 envd 源码变化，后续构建须按现有 `release/envd.json` 哈希门禁独立处理；本片不动已发布镜像。先写失败测试验证与基线相同的 tree、manifest、bundle、安全拒绝和 Git 子进程数量，再做最小实现。Windows 同机基准按阶段记录总耗时、Git 启动次数和输出哈希；本机基准不冒充 Windows 验收。

## 当前验证

批量命令数、分组上限、输出缺项/乱序、源文件读取后变化及原 Git 边界回归先红后绿；合并定向回归 127 passed、完整后端 5713 passed。macOS `/private/tmp` 里创建 671 个真实跟踪文件，完整 `SnapshotBuilder.prepare` 用时 0.312 秒，总计 24 次 Git 命令，其中 `fast-import`、`cat-file --batch-check`、`cat-file --batch` 各一次。新 envd 0.5.3 镜像的真实 Docker 输入/结果链路分别 8/8 通过。上述时间受本机硬件、Git 和文件大小影响，不能推算 Windows 公司原机耗时。
