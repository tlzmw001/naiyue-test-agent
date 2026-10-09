# Pi Bash 完整输出保存：源码观察与待决策点

状态：2026-10-02 的只读调研快照；后续用户选择在工作区内保存并显式手动清理，已按 [Spec 045](../specs/045-workspace-bash-output-artifacts.md)实施。以下“当前状态与待讨论问题”保留当时的决策前背景，不代表最新实现。参考的本机 Pi 提交为 `a69bef789`；AgentX 本机提交为 `3fcfa0e`。本记录区分源码事实、项目推断和未验证项。

## Pi 的实际链路

1. `packages/coding-agent/src/core/tools/bash.ts` 创建 `OutputAccumulator({tempFilePrefix: "pi-bash"})`，本地执行器把 stdout 和 stderr 的数据都交给同一个 `onData`。其工具描述承诺返回最后 2000 行或 50 KiB，截断时把完整输出保存为临时文件。
2. `output-accumulator.ts` 增量解码 UTF-8、维护有界文本尾部；当总字节或行数超过阈值时，`ensureTempFile()` 在系统临时目录生成随机名称，调用 `createWriteStream`，把先前缓冲的原始块写入后继续写新块。完成时等待流关闭，`snapshot` 返回尾部、截断统计和 `fullOutputPath`。`bash.ts` 把路径写进模型可见的文本和结果 `details`；Pi 的 `read` 参数说明接受相对或绝对文件路径。这里是源码可见的连接，未做真实模型回读实验。
3. Pi 的 `bash.ts` 在成功、非零退出、超时或中止后都先取得输出快照；超时与中止把已有文本附到错误里。Agent loop 把单个工具错误转成同调用 ID 的错误工具结果。我们目前对副作用未知维持人工核对终局，这是明确的不同取舍，不为了结果外观一致而改动。
4. 所检查的 `OutputAccumulator` 路径没有显式传文件权限模式或文件总量上限；本次追踪的创建/返回链也没有展示删除时机。不能据此推断 Pi 整个产品没有别处的清理机制，更不能把它的临时文件策略直接当作本项目的安全默认值。

## 本项目当前状态与待讨论问题

本项目 [Spec 043](../specs/043-bash-argument-and-output-feedback.md)只在内存保留 stdout/stderr 各末尾 4096 原始字节，不保存全文；[Spec 044](../specs/044-bash-unknown-outcome-diagnostics.md)让未知副作用异常把已捕获尾部给本地用户人工核对，仍阻断该会话。当前 `read` 对工作区外文件要求另一次审批，因此不能只把系统临时目录的绝对路径交给模型，就宣称完整输出已可安全回读。AgentX 的 `WebTerminalService` 连接 Docker 容器的交互终端，未发现与 Pi `OutputAccumulator` 同形状的模型 Bash 完整日志产物；不能据此迁移相同 API。

下一次方案讨论需明确：

- 产物放在工作区内还是应用私有目录？模型、当前本地用户和将来 SaaS 用户分别凭什么读？是否需要专用的“按调用 ID 回读片段”接口，而不是暴露裸路径？
- 每次命令/每个会话的磁盘上限、达到上限后的行为、UTF-8/二进制、stdout/stderr 的顺序与元数据如何定义？
- 完成、超时、中止、崩溃和重启后，何时关闭、保留及删除产物？CLI/模型分别得到哪种有界提示？日志可能含敏感数据，文件权限和事件脱敏如何验证？

以上是待决策点，不是已定架构；下一切片须先写有明确容量、权限、生命周期和回读验收的 Spec，再按 TDD 实现。
